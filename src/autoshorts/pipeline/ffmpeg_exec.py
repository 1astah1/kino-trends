"""Единственная точка запуска тяжёлых подпроцессов (docs/ARCHITECTURE.md §5.5, §8.1).

    result = run_media(["ffmpeg", "-i", src, ..., out], timeout_sec=600, threads=2,
                       progress_cb=ctx.progress, duration_sec=21.4, cancel=ctx.cancel,
                       log_path=ctx.log_path)

Что делает:

* итоговая команда `["nice", "-n", str(nice), *cmd]`; для ffmpeg добавляются
  `-hide_banner -nostdin -nostats -filter_threads 1 -filter_complex_threads 1 -progress pipe:1`
  (глобальные опции сразу после бинаря) и `-threads N` перед выходным файлом, если своего
  `-threads` в команде нет;
* процесс стартует в своей сессии (`start_new_session=True`), поэтому таймаут, отмена
  (`cancel: threading.Event`) и остановка воркера убивают **всю группу** через `os.killpg`:
  SIGTERM, через `kill_grace_sec` — SIGKILL. После штатного выхода добиваются отставшие
  процессы группы;
* stderr (и stdout не-ffmpeg команд) дописывается в `log_path` и хвост 4 КБ — в результат;
  командная строка, лог и хвост проходят маскировку секретов (`core.logging.redact_text`):
  лог задачи и `last_error` видят viewer и выше;
* прогресс ffmpeg (`out_time_us`) переводится в долю 0..1 от `duration_sec` и отдаётся в
  `progress_cb` не чаще раза в `progress_interval_sec`;
* env тяжёлого подпроцесса: `OMP_NUM_THREADS=threads`, BLAS/MKL/NUMBA = 1.

Ошибки: таймаут — `MediaTimeout`, ненулевой код — `MediaError` (оба `RetryableError`: ffmpeg мог
быть убит OOM-киллером или systemd), отмена — `core.errors.Cancelled`. `check=False` возвращает
результат с ненулевым `returncode` вместо исключения.

Модуль не знает о БД: воркер при остановке зовёт `kill_all_active()`.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from autoshorts.core.errors import Cancelled, RetryableError
from autoshorts.core.logging import redact_text
from autoshorts.core.resources import heavy_thread_env

log = logging.getLogger(__name__)

STDERR_TAIL_BYTES = 4096
DEFAULT_KILL_GRACE_SEC = 5.0
FFMPEG_BINARIES = frozenset({"ffmpeg"})
FFMPEG_GLOBAL_OPTS: tuple[str, ...] = (
    "-hide_banner",
    "-nostdin",
    "-nostats",
    "-filter_threads",
    "1",
    "-filter_complex_threads",
    "1",
    "-progress",
    "pipe:1",
)

ProgressCallback = Callable[[float], None]


class MediaError(RetryableError):
    """Подпроцесс завершился с ненулевым кодом."""

    def __init__(self, message: str, *, returncode: int | None, stderr_tail: str, cmd: Sequence[str]) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr_tail = stderr_tail
        self.cmd = list(cmd)


class MediaTimeout(MediaError):
    """Подпроцесс не уложился в `timeout_sec` и был убит вместе с группой."""


@dataclass
class MediaResult:
    cmd: list[str]
    returncode: int
    duration_ms: int
    stderr_tail: str = ""
    out_time_sec: float | None = None
    pgid: int | None = None
    progress: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.returncode == 0


# ---------------------------------------------------------------- реестр активных групп


_active_lock = threading.Lock()
_active: dict[int, subprocess.Popen[bytes]] = {}


def active_process_groups() -> list[int]:
    with _active_lock:
        return list(_active)


def kill_process_group(pgid: int, *, grace_sec: float = DEFAULT_KILL_GRACE_SEC, proc: Any = None) -> None:
    """SIGTERM группе, ожидание `grace_sec`, затем SIGKILL. Отсутствие группы — не ошибка."""
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        log.warning("no permission to signal process group %s", pgid)
        return
    deadline = time.monotonic() + max(grace_sec, 0.0)
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None and not _group_alive(pgid):
            return
        if proc is None and not _group_alive(pgid):
            return
        time.sleep(0.05)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill_all_active(*, grace_sec: float = DEFAULT_KILL_GRACE_SEC) -> int:
    """Убить группы всех подпроцессов, запущенных через `run_media` в этом процессе (SIGTERM воркера)."""
    groups = active_process_groups()
    threads = [
        threading.Thread(
            target=kill_process_group, args=(pgid,), kwargs={"grace_sec": grace_sec}, daemon=True
        )
        for pgid in groups
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(grace_sec + 2.0)
    return len(groups)


# ---------------------------------------------------------------- команда


def is_ffmpeg(cmd: Sequence[str]) -> bool:
    return bool(cmd) and os.path.basename(str(cmd[0])) in FFMPEG_BINARIES


def build_command(cmd: Sequence[str], *, threads: int, nice: int = 10, progress: bool = True) -> list[str]:
    """Итоговый argv: `nice -n <nice>` + команда (+ опции ffmpeg). Команду не изменяет."""
    if not cmd:
        raise ValueError("empty command")
    argv = [str(a) for a in cmd]
    if is_ffmpeg(argv):
        head, rest = argv[0], argv[1:]
        globals_ = [o for o in FFMPEG_GLOBAL_OPTS if progress or o not in ("-progress", "pipe:1")]
        # не дублировать то, что вызывающий уже указал
        dedup: list[str] = []
        i = 0
        while i < len(globals_):
            opt = globals_[i]
            takes_value = opt in ("-filter_threads", "-filter_complex_threads", "-progress")
            if opt not in rest:
                dedup.append(opt)
                if takes_value:
                    dedup.append(globals_[i + 1])
            i += 2 if takes_value else 1
        if "-threads" not in rest and rest:
            rest = [*rest[:-1], "-threads", str(int(threads)), rest[-1]]
        argv = [head, *dedup, *rest]
    return ["nice", "-n", str(int(nice)), *argv]


# ---------------------------------------------------------------- разбор прогресса


def _parse_out_time(block: Mapping[str, str]) -> float | None:
    for key in ("out_time_us", "out_time_ms"):  # у ffmpeg out_time_ms тоже в микросекундах
        raw = block.get(key)
        if raw and raw.lstrip("-").isdigit():
            value = int(raw)
            return value / 1_000_000 if value >= 0 else None
    raw = block.get("out_time")
    if raw and ":" in raw:
        try:
            h, m, s = raw.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
        except ValueError:
            return None
    return None


def _redact_bytes(data: bytes) -> bytes:
    """Маскировка секретов в сыром выводе; не-UTF-8 байты переживают её без изменений."""
    return redact_text(data.decode("utf-8", errors="surrogateescape")).encode(
        "utf-8", errors="surrogateescape"
    )


class _Tail:
    """Кольцевой буфер последних байт вывода + дозапись в файл лога.

    В файл вывод пишется целыми строками после маскировки секретов (секрет не должен разорваться
    между кусками чтения); строка без перевода длиннее `limit` пишется как есть по достижении
    лимита, остаток — в `flush()`.
    """

    def __init__(self, log_file: IO[bytes] | None, limit: int = STDERR_TAIL_BYTES) -> None:
        self._buf: deque[bytes] = deque()
        self._size = 0
        self._limit = limit
        self._file = log_file
        self._pending = b""
        self._lock = threading.Lock()

    def _write(self, data: bytes) -> None:
        if self._file is None or not data:
            return
        try:
            self._file.write(_redact_bytes(data))
            self._file.flush()
        except (OSError, ValueError):
            self._file = None

    def feed(self, chunk: bytes) -> None:
        with self._lock:
            if self._file is not None:
                self._pending += chunk
                head, sep, rest = self._pending.rpartition(b"\n")
                if sep:
                    self._write(head + sep)
                    self._pending = rest
                if len(self._pending) > self._limit:
                    self._write(self._pending)
                    self._pending = b""
            self._buf.append(chunk)
            self._size += len(chunk)
            while self._size > self._limit and len(self._buf) > 1:
                self._size -= len(self._buf.popleft())

    def flush(self) -> None:
        """Дописать в файл неполную последнюю строку."""
        with self._lock:
            self._write(self._pending)
            self._pending = b""

    def text(self) -> str:
        with self._lock:
            data = b"".join(self._buf)[-self._limit :]
        return redact_text(data.decode("utf-8", errors="replace"))


# ---------------------------------------------------------------- запуск


def run_media(
    cmd: Sequence[str],
    *,
    timeout_sec: float,
    threads: int,
    nice: int = 10,
    progress_cb: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    log_path: Path | None = None,
    duration_sec: float | None = None,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    check: bool = True,
    kill_grace_sec: float = DEFAULT_KILL_GRACE_SEC,
    progress_interval_sec: float = 0.5,
    poll_interval_sec: float = 0.1,
) -> MediaResult:
    """Запустить тяжёлый подпроцесс с nice, лимитом потоков, прогрессом, таймаутом и отменой."""
    if cancel is not None and cancel.is_set():
        raise Cancelled("cancelled before start")
    argv = build_command(cmd, threads=threads, nice=nice)
    ffmpeg = is_ffmpeg(cmd)
    proc_env = heavy_thread_env(threads, env)

    log_file: IO[bytes] | None = None
    if log_path is not None:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "ab")  # noqa: SIM115 - закрывается в finally
        log_file.write(f"$ {redact_text(' '.join(argv))}\n".encode())
        log_file.flush()
    tail = _Tail(log_file)

    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
        env=proc_env,
        start_new_session=True,
        close_fds=True,
    )
    pgid = proc.pid  # лидер новой сессии: pgid == pid
    with _active_lock:
        _active[pgid] = proc

    progress_state: dict[str, str] = {}
    last_out_time: list[float | None] = [None]
    last_emit = [0.0]

    def _emit(fraction: float, force: bool = False) -> None:
        if progress_cb is None:
            return
        now = time.monotonic()
        if not force and now - last_emit[0] < progress_interval_sec:
            return
        last_emit[0] = now
        try:
            progress_cb(max(0.0, min(1.0, fraction)))
        except Exception:  # noqa: BLE001 - сбой колбэка не должен убивать чтение вывода
            log.exception("progress callback failed")

    def _read_progress(stream: IO[bytes]) -> None:
        block: dict[str, str] = {}
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", errors="replace").strip()
            key, sep, value = line.partition("=")
            if not sep:
                continue
            block[key] = value
            if key == "progress":
                progress_state.clear()
                progress_state.update(block)
                out_time = _parse_out_time(block)
                if out_time is not None:
                    last_out_time[0] = out_time
                    if duration_sec:
                        _emit(out_time / duration_sec, force=value == "end")
                block = {}

    def _read_plain(stream: IO[bytes]) -> None:
        for chunk in iter(lambda: stream.read1(8192) if hasattr(stream, "read1") else stream.read(8192), b""):
            tail.feed(chunk)

    assert proc.stdout is not None and proc.stderr is not None
    readers = [
        threading.Thread(target=_read_plain, args=(proc.stderr,), daemon=True, name=f"media-stderr-{pgid}"),
        threading.Thread(
            target=_read_progress if ffmpeg else _read_plain,
            args=(proc.stdout,),
            daemon=True,
            name=f"media-stdout-{pgid}",
        ),
    ]
    for t in readers:
        t.start()

    deadline = started + max(timeout_sec, 0.0)
    killed_for: str | None = None
    try:
        while True:
            try:
                proc.wait(timeout=poll_interval_sec)
                break
            except subprocess.TimeoutExpired:
                pass
            if cancel is not None and cancel.is_set():
                killed_for = "cancel"
            elif time.monotonic() >= deadline:
                killed_for = "timeout"
            if killed_for:
                log.warning("killing process group %s (%s): %s", pgid, killed_for, " ".join(argv[:6]))
                kill_process_group(pgid, grace_sec=kill_grace_sec, proc=proc)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=kill_grace_sec + 5.0)
                break
    except BaseException:
        # KeyboardInterrupt/SystemExit в потоке воркера — не оставлять сирот
        kill_process_group(pgid, grace_sec=kill_grace_sec, proc=proc)
        raise
    finally:
        # добить отставших членов группы (например, `sh -c "cmd &"`), если лидер уже вышел
        if _group_alive(pgid):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pgid, signal.SIGKILL)
        for t in readers:
            t.join(timeout=2.0)
        tail.flush()
        with _active_lock:
            _active.pop(pgid, None)
        if log_file is not None:
            with contextlib.suppress(OSError):
                log_file.write(f"# exit {proc.returncode} ({killed_for or 'done'})\n".encode())
                log_file.close()

    duration_ms = int((time.monotonic() - started) * 1000)
    result = MediaResult(
        cmd=argv,
        returncode=proc.returncode if proc.returncode is not None else -1,
        duration_ms=duration_ms,
        stderr_tail=tail.text(),
        out_time_sec=last_out_time[0],
        pgid=pgid,
        progress=dict(progress_state),
    )
    if killed_for == "cancel":
        raise Cancelled("media process cancelled")
    if killed_for == "timeout":
        raise MediaTimeout(
            f"timeout after {timeout_sec:g}s: {os.path.basename(str(cmd[0]))}",
            returncode=result.returncode,
            stderr_tail=result.stderr_tail,
            cmd=argv,
        )
    if result.returncode == 0 and progress_cb is not None and duration_sec:
        _emit(1.0, force=True)
    if check and result.returncode != 0:
        raise MediaError(
            f"{os.path.basename(str(cmd[0]))} exited with {result.returncode}",
            returncode=result.returncode,
            stderr_tail=result.stderr_tail,
            cmd=argv,
        )
    return result
