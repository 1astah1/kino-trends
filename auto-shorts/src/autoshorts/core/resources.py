"""Ресурсы машины и допуск тяжёлых задач (docs/ARCHITECTURE.md §0 «Ресурсы», §6.1, §8.1).

Что здесь есть:

* снимок ресурсов: MemAvailable, PSI (cpu/memory/io some avg10), load1, свободное место под
  `MEDIA_ROOT` (+ занятое `MEDIA_ROOT` для квоты `media_max_gb`, с кешем);
* политика допуска: пороги из настроек (`heavy_*`, `disk_*`, `media_max_gb`) с дефолтами профиля
  `RESOURCE_PROFILE` (`shared_dev` / `dedicated_small`);
* решение «можно ли брать тяжёлую задачу» — `evaluate_heavy_admission()` / `check_heavy_admission()`
  (второе бросает `ResourceDeferred(reason)`: попытка задачи не тратится);
* дисковый сторож 12/8/5 ГБ: `disk_level()`, `ensure_upload_space()` (507 для новых загрузок);
* `FileLock` — flock для «ровно одного тяжёлого процесса на хосте» (вторая половина — advisory
  lock 771001 в `workers/guards.py`);
* `heavy_thread_env()` — переменные потоков для тяжёлых подпроцессов (§8.1).

Модуль не зависит от БД и API; `/proc` читается напрямую (Linux). На системах без PSI значения
PSI равны None и в допуске не участвуют.
"""

from __future__ import annotations

import errno
import fcntl
import logging
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from autoshorts.core.errors import InsufficientStorage, ResourceDeferred
from autoshorts.domain.enums import DeferReason, ResourceProfile

if TYPE_CHECKING:
    from autoshorts.domain.settings_schema import RuntimeSettings

log = logging.getLogger(__name__)

MEMINFO_PATH = Path("/proc/meminfo")
PSI_ROOT = Path("/proc/pressure")
GB = 1024**3

# Через сколько секунд повторить попытку допуска, если ресурсов нет (§6.1: run_after сдвигается).
DEFAULT_DEFER_SEC: dict[str, float] = {
    DeferReason.MEM_LOW: 30.0,
    DeferReason.LOAD_HIGH: 30.0,
    DeferReason.DISK_LOW: 300.0,
}

# Причины, которые выставляет сам допуск (в отличие от budget_exhausted / quota_exhausted).
ADMISSION_DEFER_REASONS: frozenset[str] = frozenset(
    {DeferReason.MEM_LOW, DeferReason.LOAD_HIGH, DeferReason.DISK_LOW}
)


# ---------------------------------------------------------------- чтение /proc


def read_mem_available_mb(path: Path = MEMINFO_PATH) -> int | None:
    """`MemAvailable` в МБ (None, если /proc/meminfo нет или поле не найдено)."""
    try:
        with open(path, encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024  # kB -> MB
    except (OSError, ValueError, IndexError):
        return None
    return None


@dataclass(frozen=True)
class Psi:
    """Строка `some` из /proc/pressure/<resource> (проценты времени со сваливанием в ожидание)."""

    some_avg10: float
    some_avg60: float
    some_avg300: float
    full_avg10: float | None = None


def parse_psi(text: str) -> Psi | None:
    some: dict[str, float] = {}
    full: dict[str, float] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        target = some if parts[0] == "some" else full if parts[0] == "full" else None
        if target is None:
            continue
        for item in parts[1:]:
            key, _, value = item.partition("=")
            try:
                target[key] = float(value)
            except ValueError:
                continue
    if "avg10" not in some:
        return None
    return Psi(
        some_avg10=some["avg10"],
        some_avg60=some.get("avg60", 0.0),
        some_avg300=some.get("avg300", 0.0),
        full_avg10=full.get("avg10"),
    )


def read_psi(resource: Literal["cpu", "memory", "io"], root: Path = PSI_ROOT) -> Psi | None:
    """PSI ресурса или None (ядро без CONFIG_PSI, контейнер без /proc/pressure)."""
    try:
        return parse_psi((root / resource).read_text(encoding="ascii"))
    except OSError:
        return None


def read_load1() -> float:
    try:
        return float(os.getloadavg()[0])
    except OSError:
        return 0.0


def _existing_ancestor(path: Path) -> Path:
    p = path
    while not p.exists():
        if p.parent == p:
            break
        p = p.parent
    return p


def disk_free_gb(path: Path) -> float:
    """Свободно на файловой системе, где лежит `path` (или его ближайший существующий предок), ГБ."""
    usage = shutil.disk_usage(_existing_ancestor(Path(path)))
    return usage.free / GB


def dir_size_bytes(path: Path) -> int:
    """Размер дерева каталога (без перехода по симлинкам). Нет каталога — 0."""
    total = 0
    stack = [Path(path)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


class MediaUsageCache:
    """Кеш размера MEDIA_ROOT: обход дерева дорогой, а для квоты хватает точности в минуты."""

    def __init__(self, ttl_sec: float = 300.0) -> None:
        self.ttl_sec = ttl_sec
        self._lock = threading.Lock()
        self._value: dict[Path, tuple[float, float]] = {}

    def get_gb(self, root: Path) -> float:
        root = Path(root)
        now = time.monotonic()
        with self._lock:
            cached = self._value.get(root)
            if cached is not None and now - cached[0] < self.ttl_sec:
                return cached[1]
        value = dir_size_bytes(root) / GB
        with self._lock:
            self._value[root] = (now, value)
        return value

    def invalidate(self) -> None:
        with self._lock:
            self._value.clear()


MEDIA_USAGE = MediaUsageCache()


@dataclass(frozen=True)
class ResourceSnapshot:
    mem_available_mb: int | None
    psi_cpu10: float | None
    psi_mem10: float | None
    psi_io10: float | None
    load1: float
    disk_free_gb: float | None
    media_used_gb: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def take_snapshot(media_root: Path | None = None, *, media_usage: bool = False) -> ResourceSnapshot:
    """Текущие ресурсы. `media_usage=True` — ещё и занятое MEDIA_ROOT (из кеша, для квоты)."""
    cpu = read_psi("cpu")
    mem = read_psi("memory")
    io = read_psi("io")
    free: float | None = None
    used: float | None = None
    if media_root is not None:
        try:
            free = disk_free_gb(Path(media_root))
        except OSError:
            free = None
        if media_usage:
            used = MEDIA_USAGE.get_gb(Path(media_root))
    return ResourceSnapshot(
        mem_available_mb=read_mem_available_mb(),
        psi_cpu10=cpu.some_avg10 if cpu else None,
        psi_mem10=mem.some_avg10 if mem else None,
        psi_io10=io.some_avg10 if io else None,
        load1=read_load1(),
        disk_free_gb=free,
        media_used_gb=used,
    )


# ---------------------------------------------------------------- политика допуска


@dataclass(frozen=True)
class AdmissionPolicy:
    """Пороги допуска. `None` у PSI — проверка выключена (профиль dedicated_small)."""

    min_mem_available_mb: int
    max_load1: float
    max_psi_mem10: float | None
    max_psi_cpu10: float | None
    disk_soft_min_gb: float
    disk_hard_min_gb: float
    disk_emergency_gb: float
    media_max_gb: float | None = None


# §8.1: дефолты профилей. Явно заданные в БД настройки (`settings`) главнее профиля.
PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
    ResourceProfile.SHARED_DEV: {
        "min_mem_available_mb": 1200,
        "max_load1": 3.0,
        "max_psi_mem10": 10.0,
        "max_psi_cpu10": 40.0,
    },
    ResourceProfile.DEDICATED_SMALL: {
        "min_mem_available_mb": 600,
        "max_load1": 2.5,
        "max_psi_mem10": None,
        "max_psi_cpu10": None,
    },
}

_SETTING_TO_POLICY = {
    "heavy_min_mem_available_mb": "min_mem_available_mb",
    "heavy_max_load1": "max_load1",
    "heavy_max_psi_mem10": "max_psi_mem10",
    "heavy_max_psi_cpu10": "max_psi_cpu10",
}


def env_resource_profile(default: ResourceProfile = ResourceProfile.SHARED_DEV) -> ResourceProfile:
    """Профиль из env `RESOURCE_PROFILE` (`core.config.get_env()`); кривой env — `default`."""
    try:
        from autoshorts.core.config import get_env

        return ResourceProfile(get_env().resource_profile)
    except Exception as exc:  # noqa: BLE001 - допуск не должен падать из-за окружения
        log.warning("RESOURCE_PROFILE unavailable, using %s: %s", default.value, exc)
        return default


def admission_policy(
    settings: RuntimeSettings | None = None, profile: ResourceProfile | str | None = None
) -> AdmissionPolicy:
    """Пороги из настроек с дефолтами профиля.

    Профиль: аргумент → настройка `resource_profile`, если она явно переопределена в БД →
    env `RESOURCE_PROFILE` (§8.1, §8.3; на VPS задаётся в `.env`) → `shared_dev`. Порог `heavy_*`
    из настроек применяется, только если он явно переопределён (есть в `settings.model_fields_set`),
    иначе берётся значение профиля: так `dedicated_small` получает свои 600 МБ / load1 2,5 без
    правки каждой настройки.
    """
    from autoshorts.domain.settings_schema import RuntimeSettings

    rs = settings if settings is not None else RuntimeSettings()
    if profile is not None:
        prof = ResourceProfile(profile)
    elif "resource_profile" in rs.model_fields_set:
        prof = ResourceProfile(rs.resource_profile)
    else:
        prof = env_resource_profile(default=ResourceProfile(rs.resource_profile))
    values = dict(PROFILE_DEFAULTS.get(prof, PROFILE_DEFAULTS[ResourceProfile.SHARED_DEV]))
    explicit = rs.model_fields_set
    for setting_key, policy_key in _SETTING_TO_POLICY.items():
        if setting_key in explicit:
            values[policy_key] = getattr(rs, setting_key)
    return AdmissionPolicy(
        min_mem_available_mb=int(values["min_mem_available_mb"]),
        max_load1=float(values["max_load1"]),
        max_psi_mem10=values["max_psi_mem10"],
        max_psi_cpu10=values["max_psi_cpu10"],
        disk_soft_min_gb=float(rs.disk_soft_min_gb),
        disk_hard_min_gb=float(rs.disk_hard_min_gb),
        disk_emergency_gb=float(rs.disk_emergency_gb),
        media_max_gb=rs.media_max_gb,
    )


class DiskLevel(StrEnum):
    OK = "ok"
    SOFT = "soft"  # < disk_soft_min_gb: новые загрузки не принимаются (507)
    HARD = "hard"  # < disk_hard_min_gb: пауза heavy + алерт disk_low
    EMERGENCY = "emergency"  # < disk_emergency_gb: аварийная уборка, текущая стадия падает чисто


def effective_free_gb(
    free_gb: float | None, policy: AdmissionPolicy, media_used_gb: float | None = None
) -> float | None:
    """Свободное место с учётом квоты `media_max_gb` (меньшее из «диск» и «квота − занято»)."""
    values = [v for v in (free_gb,) if v is not None]
    if policy.media_max_gb is not None and media_used_gb is not None:
        values.append(policy.media_max_gb - media_used_gb)
    return min(values) if values else None


def disk_level(
    free_gb: float | None, policy: AdmissionPolicy, media_used_gb: float | None = None
) -> DiskLevel:
    free = effective_free_gb(free_gb, policy, media_used_gb)
    if free is None:
        return DiskLevel.OK
    if free < policy.disk_emergency_gb:
        return DiskLevel.EMERGENCY
    if free < policy.disk_hard_min_gb:
        return DiskLevel.HARD
    if free < policy.disk_soft_min_gb:
        return DiskLevel.SOFT
    return DiskLevel.OK


@dataclass(frozen=True)
class AdmissionDecision:
    ok: bool
    reason: str | None = None  # значение DeferReason
    detail: str = ""
    retry_after_sec: float = 0.0
    disk: DiskLevel = DiskLevel.OK

    def raise_if_denied(self) -> None:
        if not self.ok:
            raise ResourceDeferred(
                self.reason or DeferReason.LOAD_HIGH,
                retry_after_sec=self.retry_after_sec,
                message=self.detail,
            )


def evaluate_heavy_admission(snapshot: ResourceSnapshot, policy: AdmissionPolicy) -> AdmissionDecision:
    """Можно ли начинать тяжёлую задачу. Порядок проверок: диск → память → нагрузка."""
    level = disk_level(snapshot.disk_free_gb, policy, snapshot.media_used_gb)
    if level in (DiskLevel.HARD, DiskLevel.EMERGENCY):
        free = effective_free_gb(snapshot.disk_free_gb, policy, snapshot.media_used_gb)
        return AdmissionDecision(
            False,
            DeferReason.DISK_LOW,
            f"свободно {free:.1f} ГБ < {policy.disk_hard_min_gb:g} ГБ",
            DEFAULT_DEFER_SEC[DeferReason.DISK_LOW],
            level,
        )
    mem = snapshot.mem_available_mb
    if mem is not None and mem < policy.min_mem_available_mb:
        return AdmissionDecision(
            False,
            DeferReason.MEM_LOW,
            f"MemAvailable {mem} МБ < {policy.min_mem_available_mb} МБ",
            DEFAULT_DEFER_SEC[DeferReason.MEM_LOW],
            level,
        )
    if (
        policy.max_psi_mem10 is not None
        and snapshot.psi_mem10 is not None
        and snapshot.psi_mem10 >= policy.max_psi_mem10
    ):
        return AdmissionDecision(
            False,
            DeferReason.MEM_LOW,
            f"PSI memory avg10 {snapshot.psi_mem10:.1f} ≥ {policy.max_psi_mem10:g}",
            DEFAULT_DEFER_SEC[DeferReason.MEM_LOW],
            level,
        )
    if snapshot.load1 >= policy.max_load1:
        return AdmissionDecision(
            False,
            DeferReason.LOAD_HIGH,
            f"load1 {snapshot.load1:.2f} ≥ {policy.max_load1:g}",
            DEFAULT_DEFER_SEC[DeferReason.LOAD_HIGH],
            level,
        )
    if (
        policy.max_psi_cpu10 is not None
        and snapshot.psi_cpu10 is not None
        and snapshot.psi_cpu10 >= policy.max_psi_cpu10
    ):
        return AdmissionDecision(
            False,
            DeferReason.LOAD_HIGH,
            f"PSI cpu avg10 {snapshot.psi_cpu10:.1f} ≥ {policy.max_psi_cpu10:g}",
            DEFAULT_DEFER_SEC[DeferReason.LOAD_HIGH],
            level,
        )
    return AdmissionDecision(True, disk=level)


def check_heavy_admission(snapshot: ResourceSnapshot, policy: AdmissionPolicy) -> AdmissionDecision:
    """Как `evaluate_heavy_admission`, но при отказе бросает `ResourceDeferred(reason)`."""
    decision = evaluate_heavy_admission(snapshot, policy)
    decision.raise_if_denied()
    return decision


def ensure_upload_space(
    media_root: Path,
    policy: AdmissionPolicy,
    incoming_bytes: int = 0,
    *,
    media_used_gb: float | None = None,
) -> float:
    """Проверка перед приёмом новой загрузки: `< disk_soft_min_gb` (после записи) — 507.

    Возвращает свободное место в ГБ. Бросает `InsufficientStorage`.
    """
    free = disk_free_gb(media_root)
    if media_used_gb is None and policy.media_max_gb is not None:
        media_used_gb = MEDIA_USAGE.get_gb(media_root)
    eff = effective_free_gb(free, policy, media_used_gb)
    after = (eff if eff is not None else free) - incoming_bytes / GB
    if after < policy.disk_soft_min_gb:
        raise InsufficientStorage(
            f"Мало места: после загрузки останется {after:.1f} ГБ, нужно не меньше "
            f"{policy.disk_soft_min_gb:g} ГБ",
            details={
                "free_gb": round(eff if eff is not None else free, 2),
                "min_gb": policy.disk_soft_min_gb,
            },
        )
    return eff if eff is not None else free


# ---------------------------------------------------------------- flock


HEAVY_LOCK_NAME = "autoshorts-heavy.lock"


def default_heavy_lock_path() -> Path:
    """`/run/lock/autoshorts-heavy.lock` (юнит heavy открывает /run/lock на запись), иначе
    `$XDG_RUNTIME_DIR`, иначе системный tmp. flock действует на хосте целиком."""
    candidates = [Path("/run/lock")]
    if os.environ.get("XDG_RUNTIME_DIR"):
        candidates.append(Path(os.environ["XDG_RUNTIME_DIR"]))
    for candidate in candidates:
        if candidate.is_dir() and os.access(candidate, os.W_OK):
            return candidate / HEAVY_LOCK_NAME
    return Path(tempfile.gettempdir()) / HEAVY_LOCK_NAME


class FileLock:
    """Неблокирующий `flock(LOCK_EX)` на файле. Держится, пока открыт дескриптор.

    Процесс, умерший с блокировкой, отпускает её автоматически (ядро закрывает fd).
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._fd: int | None = None
        self._mutex = threading.Lock()

    @property
    def held(self) -> bool:
        return self._fd is not None

    def try_acquire(self) -> bool:
        with self._mutex:
            if self._fd is not None:
                return True
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                os.close(fd)
                if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                    return False
                raise
            try:
                os.ftruncate(fd, 0)
                os.write(fd, f"{os.getpid()}\n".encode("ascii"))
            except OSError:
                pass
            self._fd = fd
            return True

    def release(self) -> None:
        with self._mutex:
            if self._fd is None:
                return
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None

    def __enter__(self) -> FileLock:
        if not self.try_acquire():
            raise BlockingIOError(f"lock {self.path} is held by another process")
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


# ---------------------------------------------------------------- потоки подпроцессов


def heavy_thread_env(threads: int = 2, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Env для тяжёлых подпроцессов (§8.1): OMP=threads, BLAS/MKL/NUMBA=1."""
    env = dict(os.environ if base is None else base)
    env.update(
        {
            "OMP_NUM_THREADS": str(threads),
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMBA_NUM_THREADS": "1",
        }
    )
    return env
