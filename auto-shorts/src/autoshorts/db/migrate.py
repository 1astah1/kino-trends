"""Раннер SQL-миграций (docs/ARCHITECTURE.md §4.1). CLI: `autoshorts-admin migrate [--dry-run] [--no-backup]`.

Порядок работы `migrate()`:

1. Сессионный `pg_advisory_lock(771000)` — второй раннер ждёт (до `lock_timeout`) и увидит,
   что всё уже применено.
2. План только на чтение: какие версии применены, какие файлы ждут, совпадают ли sha256.
   Отказ работать, если: файл применённой версии изменился (checksum), в БД есть версия без
   файла, ждущий файл старше последней применённой версии, имена или предохранители файлов
   кривые, legacy-схема не совпадает с baseline.
3. **Принятие baseline:** `schema_migrations` пуста (или её нет), а `jobs` существует — это
   legacy-база, созданная `scripts/init_db.sql`. Колонки 10 таблиц baseline сверяются через
   `information_schema`, версия 1 записывается с `adopted=true`, файл 0001 не выполняется.
4. Бэкап `pg_dump -Fc -f $BACKUP_DIR/pre_migrate_<ts>.dump` перед первым неприменённым файлом
   (если в базе есть таблицы и не передан `backup=False` / `--no-backup`). Дамп не удался —
   ни один файл не применяется.
5. Идемпотентный пролог `migrations/_bootstrap.sql` (таблица `schema_migrations` и функция
   `as_migration_guard`), затем каждый файл одной транзакцией + строка в `schema_migrations`
   (name = имя файла без .sql, checksum = sha256 байтов файла, duration_ms).

Проверка при старте процессов (api, public, воркеры): `assert_schema_current()` — если
`max(version)` отстаёт от последнего файла, бросает `MigrationsPending` (503 `migrations_pending`).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from autoshorts.core.errors import MigrationsPending
from autoshorts.core.timeutil import utcnow

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
BOOTSTRAP_FILENAME = "_bootstrap.sql"
VERSION_GLOB = "[0-9][0-9][0-9][0-9]_*.sql"
FILENAME_RE = re.compile(r"(?P<version>\d{4})_(?P<slug>[a-z0-9][a-z0-9_]*)\.sql")
GUARD_RE = re.compile(r"\s*SELECT\s+as_migration_guard\(\s*(?P<version>\d+)\s*\)\s*;", re.IGNORECASE)
ADVISORY_LOCK_KEY = 771000
BASELINE_VERSION = 1
DEFAULT_LOCK_TIMEOUT_SEC = 60.0
PG_DUMP_TIMEOUT_SEC = 1800
# Перед этими версиями старые процессы (queue/worker.py, бот, дашборд) должны быть остановлены (§4.1).
STOP_LEGACY_BEFORE = frozenset({3, 4})

# Колонки baseline (= scripts/init_db.sql = 0001). Сверка при принятии legacy-базы.
# Тест tests/migrations/test_runner.py::test_baseline_columns_match_0001 держит словарь в согласии с файлом.
BASELINE_COLUMNS: dict[str, frozenset[str]] = {
    "channels": frozenset({"id", "niche", "platform", "cluster", "created_at", "notes"}),
    "jobs": frozenset(
        {
            "id", "video_uid", "channel_id", "niche", "template_version", "status", "stage_payload",
            "reject_reason", "attempt_count", "review_sent_at", "locked_by", "locked_at",
            "created_at", "updated_at",
        }
    ),
    "generation_events": frozenset(
        {
            "id", "job_id", "step_name", "provider", "model", "units", "unit_price_usd", "cost_usd",
            "latency_ms", "http_status", "created_at",
        }
    ),
    "budget_state": frozenset({"id", "daily_limit_usd", "spent_today_usd", "day", "paused"}),
    "publications": frozenset(
        {
            "id", "job_id", "channel_id", "platform", "platform_video_id", "master_file_path", "title",
            "description", "hashtags", "published_at", "attribution_tag", "created_at",
        }
    ),
    "attribution_events": frozenset({"id", "tag", "source", "route", "ip_hash", "user_agent", "created_at"}),
    "metrics_snapshots": frozenset(
        {
            "id", "publication_id", "captured_at", "age_hours", "views", "engaged_views", "retention_3s",
            "avg_pct_viewed", "likes", "comments",
        }
    ),
    "competitor_channels": frozenset({"id", "platform", "channel_ref", "pool", "active"}),
    "competitor_video_snapshots": frozenset(
        {"id", "competitor_id", "external_video_id", "captured_at", "age_hours", "views", "outlier_score"}
    ),
    "trend_briefs": frozenset(
        {
            "id", "external_video_id", "hook_type", "hook_duration_sec", "cuts_per_sec_first5",
            "total_duration_bucket", "text_on_screen_density", "payoff_timestamp_ratio", "loop_seam",
            "cta_position", "outlier_score", "reviewed", "accepted", "created_at",
        }
    ),
}  # fmt: skip


# ---------------------------------------------------------------- ошибки


class MigrationError(RuntimeError):
    """Раннер отказывается работать или миграция упала."""


class MigrationFilesError(MigrationError):
    """Кривые файлы: имя, дубль номера, предохранитель не совпадает с номером."""


class ChecksumMismatch(MigrationError):
    """Файл уже применённой миграции изменился (слитый файл неизменяем, §10.1 п. 3)."""


class UnknownAppliedMigration(MigrationError):
    """В БД есть версия, файла которой нет: код старше базы."""


class OutOfOrderMigration(MigrationError):
    """Неприменённый файл старше последней применённой версии."""


class BaselineMismatch(MigrationError):
    """Legacy-база не похожа на baseline 0001: принимать её нельзя."""


class BackupFailed(MigrationError):
    """pg_dump перед миграцией не удался — миграции не применяются."""


class MigrationLockTimeout(MigrationError):
    """Другой раннер держит advisory lock дольше lock_timeout."""


class MigrationFailed(MigrationError):
    """SQL файла упал; транзакция файла откатилась, более ранние файлы остались применёнными."""

    def __init__(self, migration: Migration, cause: BaseException) -> None:
        super().__init__(f"{migration.name}: {cause}")
        self.migration = migration
        self.cause = cause


# ---------------------------------------------------------------- модель


@dataclass(frozen=True)
class Migration:
    version: int
    name: str  # имя файла без .sql, пишется в schema_migrations.name
    path: Path
    checksum: str  # sha256 hex байтов файла
    data: bytes = field(repr=False, compare=False)

    @property
    def sql(self) -> str:
        return self.data.decode("utf-8")


@dataclass(frozen=True)
class AppliedMigration:
    version: int
    name: str
    checksum: str
    applied_at: datetime | None
    duration_ms: int | None
    adopted: bool


@dataclass
class MigrationPlan:
    migrations: list[Migration]
    applied: dict[int, AppliedMigration]
    schema_table_exists: bool
    adopt_baseline: bool
    pending: list[Migration]
    problems: list[MigrationError]
    has_user_tables: bool = False  # есть что бэкапить (кроме schema_migrations)

    @property
    def latest_version(self) -> int:
        return max((m.version for m in self.migrations), default=0)

    @property
    def current_version(self) -> int | None:
        if self.adopt_baseline:
            return BASELINE_VERSION
        return max(self.applied, default=None)

    @property
    def up_to_date(self) -> bool:
        return not self.pending and not self.adopt_baseline and not self.problems

    def raise_for_problems(self) -> None:
        if not self.problems:
            return
        first = self.problems[0]
        if len(self.problems) == 1:
            raise first
        text = "; ".join(str(p) for p in self.problems)
        raise type(first)(text)


@dataclass
class MigrateResult:
    plan: MigrationPlan
    dry_run: bool
    applied: list[Migration] = field(default_factory=list)
    adopted_baseline: bool = False
    backup_path: Path | None = None
    durations_ms: dict[int, int] = field(default_factory=dict)

    @property
    def current_version(self) -> int | None:
        if self.dry_run:
            return self.plan.current_version
        if self.applied:
            return self.applied[-1].version
        return self.plan.current_version


@dataclass(frozen=True)
class SchemaState:
    current: int | None
    latest: int
    pending: tuple[int, ...]

    @property
    def ok(self) -> bool:
        return not self.pending


# ---------------------------------------------------------------- файлы


def discover_migrations(migrations_dir: Path | None = None) -> list[Migration]:
    """Файлы версий `NNNN_<name>.sql` по возрастанию. `_bootstrap.sql` и прочее — не версии."""
    directory = Path(migrations_dir or MIGRATIONS_DIR)
    if not directory.is_dir():
        raise MigrationFilesError(f"нет каталога миграций: {directory}")
    found: dict[int, Migration] = {}
    for path in sorted(directory.glob(VERSION_GLOB)):
        m = FILENAME_RE.fullmatch(path.name)
        if not m:
            raise MigrationFilesError(f"{path.name}: имя должно быть NNNN_<латиница_в_нижнем_регистре>.sql")
        version = int(m.group("version"))
        if version <= 0:
            raise MigrationFilesError(f"{path.name}: номер версии должен быть больше нуля")
        if version in found:
            raise MigrationFilesError(
                f"две миграции с номером {version}: {found[version].path.name}, {path.name}"
            )
        data = path.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MigrationFilesError(f"{path.name}: файл не в UTF-8 ({exc})") from exc
        first_stmt = next(
            (ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("--")), ""
        )
        g = GUARD_RE.match(first_stmt)
        if not g or int(g.group("version")) != version:
            raise MigrationFilesError(
                f"{path.name}: первая строка должна быть `SELECT as_migration_guard({version});`"
            )
        found[version] = Migration(
            version=version,
            name=path.stem,
            path=path,
            checksum=hashlib.sha256(data).hexdigest(),
            data=data,
        )
    if not found:
        raise MigrationFilesError(f"в {directory} нет файлов миграций")
    return [found[v] for v in sorted(found)]


def bootstrap_sql(migrations_dir: Path | None = None) -> str:
    return (Path(migrations_dir or MIGRATIONS_DIR) / BOOTSTRAP_FILENAME).read_text(encoding="utf-8")


@lru_cache(maxsize=8)
def _file_versions_cached(directory: str) -> tuple[int, ...]:
    return tuple(m.version for m in discover_migrations(Path(directory)))


def file_versions(migrations_dir: Path | None = None) -> tuple[int, ...]:
    """Номера файлов миграций (кешируется: слитые файлы неизменны на время жизни процесса)."""
    return _file_versions_cached(str(Path(migrations_dir or MIGRATIONS_DIR)))


def latest_version(migrations_dir: Path | None = None) -> int:
    """Номер последнего файла миграции."""
    return file_versions(migrations_dir)[-1]


# ---------------------------------------------------------------- чтение состояния БД


def _regclass_exists(conn: psycopg.Connection[Any], name: str) -> bool:
    row = conn.execute("SELECT to_regclass(%(n)s) IS NOT NULL AS ok", {"n": f"public.{name}"}).fetchone()
    return bool(_first(row))


def _first(row: Any) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


def read_applied(conn: psycopg.Connection[Any]) -> dict[int, AppliedMigration]:
    """Строки `schema_migrations` (пусто, если таблицы нет)."""
    if not _regclass_exists(conn, "schema_migrations"):
        return {}
    cur = conn.execute(
        "SELECT version, name, checksum, applied_at, duration_ms, adopted"
        " FROM schema_migrations ORDER BY version"
    )
    out: dict[int, AppliedMigration] = {}
    for row in cur.fetchall():
        vals = list(row.values()) if isinstance(row, dict) else list(row)
        out[int(vals[0])] = AppliedMigration(int(vals[0]), vals[1], vals[2], vals[3], vals[4], bool(vals[5]))
    return out


def baseline_problems(conn: psycopg.Connection[Any]) -> list[str]:
    """Чем legacy-схема отличается от baseline: пропавшие таблицы и колонки (лишние допустимы)."""
    rows = conn.execute(
        """
        SELECT table_name, column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = ANY(%(tables)s)
        """,
        {"tables": list(BASELINE_COLUMNS)},
    ).fetchall()
    have: dict[str, set[str]] = {}
    for row in rows:
        t, c = (row["table_name"], row["column_name"]) if isinstance(row, dict) else (row[0], row[1])
        have.setdefault(t, set()).add(c)
    problems: list[str] = []
    for table, expected in BASELINE_COLUMNS.items():
        if table not in have:
            problems.append(f"нет таблицы {table}")
            continue
        missing = sorted(expected - have[table])
        if missing:
            problems.append(f"в {table} нет колонок {', '.join(missing)}")
    return problems


def compute_plan(conn: psycopg.Connection[Any], migrations: Sequence[Migration]) -> MigrationPlan:
    """План без записи в БД: что применено, что ждёт, есть ли причины отказаться."""
    migrations = list(migrations)
    by_version = {m.version: m for m in migrations}
    schema_table_exists = _regclass_exists(conn, "schema_migrations")
    applied = read_applied(conn) if schema_table_exists else {}
    problems: list[MigrationError] = []

    adopt = False
    if not applied and _regclass_exists(conn, "jobs"):
        if BASELINE_VERSION not in by_version:
            problems.append(MigrationFilesError("нет файла baseline 0001 — принять legacy-базу нечем"))
        else:
            diff = baseline_problems(conn)
            if diff:
                problems.append(
                    BaselineMismatch(
                        "legacy-база не совпадает с baseline 0001 (scripts/init_db.sql): "
                        + "; ".join(diff)
                        + ". Приведите схему к init_db.sql вручную и повторите."
                    )
                )
            else:
                adopt = True

    for version, row in sorted(applied.items()):
        m = by_version.get(version)
        if m is None:
            problems.append(
                UnknownAppliedMigration(
                    f"в БД применена версия {version} ({row.name}), а файла нет — код старше базы"
                )
            )
        elif row.checksum != m.checksum:
            problems.append(
                ChecksumMismatch(
                    f"{m.name}: файл изменён после применения (sha256 в БД {row.checksum[:12]}…, "
                    f"у файла {m.checksum[:12]}…). Слитая миграция неизменяема — верните файл"
                )
            )

    done = set(applied) | ({BASELINE_VERSION} if adopt else set())
    pending = [m for m in migrations if m.version not in done]
    top = max(done, default=0)
    for m in pending:
        if m.version < top:
            problems.append(
                OutOfOrderMigration(f"{m.name} не применена, хотя применена более поздняя версия {top}")
            )

    return MigrationPlan(
        migrations=migrations,
        applied=applied,
        schema_table_exists=schema_table_exists,
        adopt_baseline=adopt,
        pending=pending,
        problems=problems,
        has_user_tables=_has_user_tables(conn),
    )


def schema_state(conn: psycopg.Connection[Any], migrations_dir: Path | None = None) -> SchemaState:
    """Дёшево (для /health/ready): какие версии из файлов ещё не применены, без проверки checksum."""
    versions = file_versions(migrations_dir)
    applied = read_applied(conn)
    pending = tuple(v for v in versions if v not in applied)
    return SchemaState(current=max(applied, default=None), latest=versions[-1], pending=pending)


def assert_schema_current(
    conn: psycopg.Connection[Any] | None = None,
    *,
    conninfo: str | None = None,
    migrations_dir: Path | None = None,
) -> SchemaState:
    """Проверка при старте api/public/воркеров (§4.1). Отстаёт — `MigrationsPending`."""
    if conn is None:
        from autoshorts.core.config import get_env

        with psycopg.connect(conninfo or get_env().database_url, autocommit=True, connect_timeout=5) as own:
            return assert_schema_current(own, migrations_dir=migrations_dir)
    state = schema_state(conn, migrations_dir)
    if not state.ok:
        raise MigrationsPending(
            f"Схема БД на версии {state.current}, файлы до {state.latest}: "
            "выполните autoshorts-admin migrate",
            details={
                "current_version": state.current,
                "latest_version": state.latest,
                "pending": list(state.pending),
            },
        )
    return state


# ---------------------------------------------------------------- блокировка и бэкап


def _acquire_lock(conn: psycopg.Connection[Any], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    announced = False
    while True:
        row = conn.execute("SELECT pg_try_advisory_lock(%(k)s)", {"k": ADVISORY_LOCK_KEY}).fetchone()
        if _first(row):
            return
        if time.monotonic() >= deadline:
            raise MigrationLockTimeout(
                f"advisory lock {ADVISORY_LOCK_KEY} занят другим раннером миграций дольше {timeout:.0f} с"
            )
        if not announced:
            log.warning("ждём, пока другой раннер миграций отпустит блокировку %s", ADVISORY_LOCK_KEY)
            announced = True
        time.sleep(0.25)


def _release_lock(conn: psycopg.Connection[Any]) -> None:
    if conn.closed:
        return
    try:
        conn.execute("SELECT pg_advisory_unlock(%(k)s)", {"k": ADVISORY_LOCK_KEY})
    except psycopg.Error as exc:  # соединение всё равно закрывается — lock уйдёт вместе с сессией
        log.warning("не удалось отпустить advisory lock: %s", exc)


def _has_user_tables(conn: psycopg.Connection[Any]) -> bool:
    row = conn.execute(
        """
        SELECT EXISTS (
          SELECT 1 FROM pg_tables
          WHERE schemaname NOT IN ('pg_catalog', 'information_schema') AND tablename <> 'schema_migrations'
        )
        """
    ).fetchone()
    return bool(_first(row))


def backup_database(
    conninfo: str,
    backup_dir: Path,
    *,
    pg_dump_bin: str = "pg_dump",
    timeout: float = PG_DUMP_TIMEOUT_SEC,
    prefix: str = "pre_migrate",
) -> Path:
    """`pg_dump -Fc` в `<backup_dir>/<prefix>_<UTC ts>.dump` (права 600). Пароль — через PGPASSWORD,
    не в argv. Любая неудача — `BackupFailed`, недописанный файл удаляется."""
    exe = shutil.which(pg_dump_bin)
    if exe is None:
        raise BackupFailed(f"не найден {pg_dump_bin}: без бэкапа миграции не применяются (или --no-backup)")
    try:
        backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise BackupFailed(f"не создать каталог бэкапов {backup_dir}: {exc}") from exc

    params = conninfo_to_dict(conninfo)
    password = params.pop("password", None)
    env = dict(os.environ)
    if password:
        env["PGPASSWORD"] = str(password)
    dsn = make_conninfo(**params)

    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    target = backup_dir / f"{prefix}_{stamp}.dump"
    n = 1
    while target.exists():
        target = backup_dir / f"{prefix}_{stamp}_{n}.dump"
        n += 1
    part = target.with_name(target.name + ".part")
    try:
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    except OSError as exc:
        raise BackupFailed(f"не создать файл бэкапа {part}: {exc}") from exc

    cmd = [exe, "--format=custom", f"--file={part}", f"--dbname={dsn}"]
    nice = shutil.which("nice")
    if nice:
        cmd = [nice, "-n", "10", *cmd]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        part.unlink(missing_ok=True)
        raise BackupFailed(f"pg_dump не отработал: {exc}") from exc
    if proc.returncode != 0 or part.stat().st_size == 0:
        part.unlink(missing_ok=True)
        err = (proc.stderr or "").strip().splitlines()
        raise BackupFailed(
            f"pg_dump завершился с кодом {proc.returncode}: {err[-1] if err else 'пустой дамп'}"
        )
    part.rename(target)
    log.info(
        "бэкап перед миграцией: %s (%d КБ, %.1f с)",
        target,
        target.stat().st_size // 1024,
        time.monotonic() - t0,
    )
    return target


def _warn_other_sessions(conn: psycopg.Connection[Any], plan: MigrationPlan) -> None:
    if not STOP_LEGACY_BEFORE & {m.version for m in plan.pending}:
        return
    row = conn.execute(
        """
        SELECT count(*) AS n, string_agg(DISTINCT coalesce(nullif(application_name, ''), '?'), ', ') AS apps
        FROM pg_stat_activity
        WHERE datname = current_database() AND pid <> pg_backend_pid() AND backend_type = 'client backend'
        """
    ).fetchone()
    n = row["n"] if isinstance(row, dict) else row[0]
    apps = row["apps"] if isinstance(row, dict) else row[1]
    if n:
        log.warning(
            "к базе подключено ещё %s сессий (%s). Перед 0003/0004 остановите старые процессы: "
            "queue/worker.py, бот, дашборд (§4.1)",
            n,
            apps,
        )


# ---------------------------------------------------------------- применение


def _bootstrap(conn: psycopg.Connection[Any], migrations_dir: Path | None) -> None:
    with conn.transaction():
        conn.execute(bootstrap_sql(migrations_dir))


def _adopt_baseline(conn: psycopg.Connection[Any], baseline: Migration) -> None:
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO schema_migrations (version, name, checksum, duration_ms, adopted)
            VALUES (%(v)s, %(n)s, %(c)s, 0, true)
            """,
            {"v": baseline.version, "n": baseline.name, "c": baseline.checksum},
        )
    log.info("legacy-база принята как baseline: версия %s записана с adopted=true", baseline.version)


def _apply(conn: psycopg.Connection[Any], m: Migration) -> int:
    t0 = time.monotonic()
    try:
        with conn.transaction():
            conn.execute(m.sql)  # без параметров: несколько операторов, % не интерпретируется
            duration_ms = int((time.monotonic() - t0) * 1000)
            conn.execute(
                """
                INSERT INTO schema_migrations (version, name, checksum, duration_ms)
                VALUES (%(v)s, %(n)s, %(c)s, %(d)s)
                """,
                {"v": m.version, "n": m.name, "c": m.checksum, "d": duration_ms},
            )
    except psycopg.Error as exc:
        raise MigrationFailed(m, exc) from exc
    log.info("применена миграция %s (%d мс)", m.name, duration_ms)
    return duration_ms


def migrate(
    conninfo: str | None = None,
    *,
    dry_run: bool = False,
    backup: bool = True,
    backup_dir: Path | None = None,
    migrations_dir: Path | None = None,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT_SEC,
    pg_dump_bin: str = "pg_dump",
) -> MigrateResult:
    """Применить неприменённые миграции (см. докстринг модуля).

    `conninfo`/`backup_dir` по умолчанию берутся из `DATABASE_URL`/`BACKUP_DIR`.
    `dry_run=True` ничего не пишет (даже пролог) и возвращает план с найденными проблемами;
    без `dry_run` проблема плана поднимается исключением-наследником `MigrationError`.
    """
    if conninfo is None or (backup and backup_dir is None):
        from autoshorts.core.config import get_env

        env = get_env()
        conninfo = conninfo or env.database_url
        backup_dir = backup_dir or env.backup_dir
    migrations = discover_migrations(migrations_dir)

    with psycopg.connect(
        conninfo, autocommit=True, application_name="autoshorts-migrate", connect_timeout=10
    ) as conn:
        _acquire_lock(conn, lock_timeout)
        try:
            plan = compute_plan(conn, migrations)
            result = MigrateResult(plan=plan, dry_run=dry_run)
            if dry_run:
                return result
            plan.raise_for_problems()
            if plan.up_to_date:
                log.info("миграции не нужны: база на версии %s", plan.current_version)
                return result

            if plan.pending and backup and plan.has_user_tables:
                assert backup_dir is not None
                result.backup_path = backup_database(conninfo, Path(backup_dir), pg_dump_bin=pg_dump_bin)
            _warn_other_sessions(conn, plan)

            _bootstrap(conn, migrations_dir)
            if plan.adopt_baseline:
                _adopt_baseline(conn, next(m for m in migrations if m.version == BASELINE_VERSION))
                result.adopted_baseline = True
            for m in plan.pending:
                result.durations_ms[m.version] = _apply(conn, m)
                result.applied.append(m)
            return result
        finally:
            _release_lock(conn)
