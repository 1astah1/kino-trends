"""Раннер миграций `autoshorts.db.migrate` и CLI `autoshorts-admin migrate` (docs/ARCHITECTURE.md §4.1, M1).

Каждый тест работает в своей базе `autoshorts_test_runner_*` (создаётся и удаляется тестом);
`autoshorts_test` и dev-база `autoshorts` не трогаются.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import threading
from pathlib import Path

import psycopg
import pytest

from autoshorts.core.errors import MigrationsPending
from autoshorts.db import migrate as runner
from tests.migrations.conftest import ROOT, admin_cli, psql, rows, scalar

pytestmark = pytest.mark.db

GUARD = "SELECT as_migration_guard({v});\n"


def _synthetic(dirpath: Path, files: dict[str, str]) -> Path:
    """Каталог с `_bootstrap.sql` и заданными файлами (для общих свойств раннера, без схемы v1)."""
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / runner.BOOTSTRAP_FILENAME).write_bytes(
        (runner.MIGRATIONS_DIR / runner.BOOTSTRAP_FILENAME).read_bytes()
    )
    for name, body in files.items():
        (dirpath / name).write_text(body, encoding="utf-8")
    return dirpath


# ---------------------------------------------------------------- пустая база → последняя версия

LATEST = runner.latest_version()
FILE_COUNT = len(runner.discover_migrations())
PENDING_AFTER_BASELINE = tuple(v for v in runner.file_versions() if v > 1)


@pytest.fixture(scope="module")
def full_db(module_db) -> str:
    """База, прогнанная CLI с нуля (без --no-backup: в пустой базе бэкап не нужен)."""
    url = module_db("full")
    res = admin_cli("migrate", "--database-url", url)
    assert res.returncode == 0, res.stderr + res.stdout
    assert f"Готово: версия {LATEST}" in res.stdout
    return url


def test_cli_migrate_empty_db_reaches_latest_version(full_db: str) -> None:
    latest = runner.discover_migrations()[-1].version
    assert latest == LATEST
    assert scalar(full_db, "SELECT max(version) FROM schema_migrations") == latest
    assert scalar(full_db, "SELECT count(*) FROM schema_migrations") == FILE_COUNT
    assert scalar(full_db, "SELECT count(*) FROM schema_migrations WHERE adopted") == 0


def test_recorded_names_and_checksums_match_files(full_db: str) -> None:
    recorded = {
        v: (n, c) for v, n, c in rows(full_db, "SELECT version, name, checksum FROM schema_migrations")
    }
    for m in runner.discover_migrations():
        assert recorded[m.version] == (m.path.stem, hashlib.sha256(m.path.read_bytes()).hexdigest())


def test_second_run_is_noop(full_db: str) -> None:
    before = rows(full_db, "SELECT version, applied_at FROM schema_migrations ORDER BY 1")
    res = admin_cli("migrate", "--database-url", full_db)
    assert res.returncode == 0, res.stderr
    assert "Миграции не нужны" in res.stdout
    assert rows(full_db, "SELECT version, applied_at FROM schema_migrations ORDER BY 1") == before


def test_psql_reapply_blocked_by_guard(full_db: str) -> None:
    again = psql(full_db, "-f", str(runner.MIGRATIONS_DIR / "0004_queue.sql"))
    assert again.returncode != 0
    assert "migration 4 already applied" in again.stderr


def test_seeds_present_after_migrate(full_db: str) -> None:
    assert scalar(full_db, "SELECT count(*) FROM schedules") == 14
    assert scalar(full_db, "SELECT count(*) FROM provider_configs") == 15
    assert scalar(full_db, "SELECT count(*) FROM products WHERE id = 'umka'") == 1


def test_schema_state_ok_on_latest(full_db: str) -> None:
    with psycopg.connect(full_db, autocommit=True) as conn:
        state = runner.assert_schema_current(conn)
    assert state.ok and state.current == state.latest == LATEST


def test_dry_run_writes_nothing(module_db) -> None:
    url = module_db("dry")
    res = admin_cli("migrate", "--dry-run", "--database-url", url)
    assert res.returncode == 0, res.stderr
    assert res.stdout.count("будет применена") == FILE_COUNT
    assert scalar(url, "SELECT to_regclass('public.schema_migrations') IS NULL") is True
    assert scalar(url, "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'") == 0


# ---------------------------------------------------------------- checksum и порядок


def test_checksum_tamper_refused(module_db, migrations_copy) -> None:
    url = module_db("tamper")
    mdir = migrations_copy()
    runner.migrate(url, backup=False, migrations_dir=mdir)

    target = mdir / "0004_queue.sql"
    data = bytearray(target.read_bytes())
    # меняем один ASCII-байт внутри SQL-комментария: смысл SQL тот же, sha256 другой
    idx = [m.start() for m in re.finditer(rb"-- [A-Za-z]", bytes(data))][-1] + 3
    data[idx] = ord("X") if data[idx] != ord("X") else ord("Y")
    target.write_bytes(bytes(data))

    with pytest.raises(runner.ChecksumMismatch, match="0004_queue"):
        runner.migrate(url, backup=False, migrations_dir=mdir)

    res = admin_cli("migrate", "--no-backup", "--database-url", url, "--migrations-dir", str(mdir))
    assert res.returncode == 1
    assert "0004_queue" in res.stderr and "изменён" in res.stderr

    dry = admin_cli("migrate", "--dry-run", "--database-url", url, "--migrations-dir", str(mdir))
    assert dry.returncode == 1 and "ОТКАЗ" in dry.stderr
    assert scalar(url, "SELECT max(version) FROM schema_migrations") == LATEST


def test_new_file_applied_incrementally(module_db, migrations_copy) -> None:
    url = module_db("incr")
    mdir = migrations_copy()
    runner.migrate(url, backup=False, migrations_dir=mdir)
    extra = LATEST + 1
    (mdir / f"{extra:04d}_extra.sql").write_text(
        GUARD.format(v=extra) + "CREATE TABLE extra_t (id INT);\n", encoding="utf-8"
    )
    result = runner.migrate(url, backup=False, migrations_dir=mdir)
    assert [m.version for m in result.applied] == [extra]
    assert scalar(url, "SELECT max(version) FROM schema_migrations") == extra
    assert scalar(url, "SELECT to_regclass('public.extra_t') IS NOT NULL") is True


def test_failed_file_rolls_back_only_itself(module_db, tmp_path: Path) -> None:
    url = module_db("fail")
    mdir = _synthetic(
        tmp_path / "m",
        {
            "0001_a.sql": GUARD.format(v=1) + "CREATE TABLE t_a (id INT);\n",
            "0002_b.sql": GUARD.format(v=2) + "CREATE TABLE t_b (id INT);\nSELECT 1/0;\n",
        },
    )
    with pytest.raises(runner.MigrationFailed, match="0002_b") as err:
        runner.migrate(url, backup=False, migrations_dir=mdir)
    assert isinstance(err.value.cause, psycopg.errors.DivisionByZero)
    assert scalar(url, "SELECT max(version) FROM schema_migrations") == 1
    assert scalar(url, "SELECT to_regclass('public.t_b') IS NULL") is True

    (mdir / "0002_b.sql").write_text(GUARD.format(v=2) + "CREATE TABLE t_b (id INT);\n", encoding="utf-8")
    result = runner.migrate(url, backup=False, migrations_dir=mdir)
    assert [m.version for m in result.applied] == [2]


def test_out_of_order_and_unknown_versions_refused(module_db, tmp_path: Path) -> None:
    url = module_db("order")
    mdir = _synthetic(
        tmp_path / "m",
        {
            "0001_a.sql": GUARD.format(v=1) + "CREATE TABLE t_a (id INT);\n",
            "0003_c.sql": GUARD.format(v=3) + "CREATE TABLE t_c (id INT);\n",
        },
    )
    runner.migrate(url, backup=False, migrations_dir=mdir)
    (mdir / "0002_b.sql").write_text(GUARD.format(v=2) + "CREATE TABLE t_b (id INT);\n", encoding="utf-8")
    with pytest.raises(runner.OutOfOrderMigration, match="0002_b"):
        runner.migrate(url, backup=False, migrations_dir=mdir)

    (mdir / "0002_b.sql").unlink()
    (mdir / "0003_c.sql").unlink()
    with pytest.raises(runner.UnknownAppliedMigration, match="версия 3"):
        runner.migrate(url, backup=False, migrations_dir=mdir)


@pytest.mark.parametrize(
    ("name", "body", "match"),
    [
        ("0001_Bad.sql", GUARD.format(v=1), "имя"),
        ("0001_a.sql", "CREATE TABLE x (id INT);\n", "первая строка"),
        ("0001_a.sql", GUARD.format(v=2), "первая строка"),
    ],
)
def test_bad_files_refused(tmp_path: Path, name: str, body: str, match: str) -> None:
    mdir = _synthetic(tmp_path / "m", {name: body})
    with pytest.raises(runner.MigrationFilesError, match=match):
        runner.discover_migrations(mdir)


def test_non_utf8_file_refused(tmp_path: Path) -> None:
    mdir = _synthetic(tmp_path / "m", {})
    (mdir / "0001_a.sql").write_bytes(GUARD.format(v=1).encode() + b"-- \xd0\n")
    with pytest.raises(runner.MigrationFilesError, match="UTF-8"):
        runner.discover_migrations(mdir)


def test_duplicate_versions_refused(tmp_path: Path) -> None:
    mdir = _synthetic(tmp_path / "m", {"0001_a.sql": GUARD.format(v=1), "0001_b.sql": GUARD.format(v=1)})
    with pytest.raises(runner.MigrationFilesError, match="две миграции"):
        runner.discover_migrations(mdir)


def test_bootstrap_file_is_not_a_version() -> None:
    names = [m.path.name for m in runner.discover_migrations()]
    assert runner.BOOTSTRAP_FILENAME not in names
    assert names == sorted(names) and names[0] == "0001_baseline.sql"


# ---------------------------------------------------------------- блокировка


def test_advisory_lock_blocks_second_runner(module_db, tmp_path: Path) -> None:
    url = module_db("lock")
    mdir = _synthetic(tmp_path / "m", {"0001_a.sql": GUARD.format(v=1) + "CREATE TABLE t_a (id INT);\n"})
    with psycopg.connect(url, autocommit=True) as holder:
        holder.execute("SELECT pg_advisory_lock(%s)", (runner.ADVISORY_LOCK_KEY,))
        with pytest.raises(runner.MigrationLockTimeout):
            runner.migrate(url, backup=False, migrations_dir=mdir, lock_timeout=0.5)
        assert scalar(url, "SELECT to_regclass('public.t_a') IS NULL") is True
    assert runner.migrate(url, backup=False, migrations_dir=mdir).applied


def test_concurrent_runners_apply_each_file_once(module_db) -> None:
    url = module_db("race")
    results: list[runner.MigrateResult] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            results.append(runner.migrate(url, backup=False, lock_timeout=60))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    assert not errors, errors
    assert sorted(len(r.applied) for r in results) == [0, FILE_COUNT]
    assert scalar(url, "SELECT count(*) FROM schema_migrations") == FILE_COUNT


# ---------------------------------------------------------------- бэкап


@pytest.fixture
def baseline_only_db(module_db, migrations_copy):
    """База на версии 1 (таблицы есть) + каталог со всеми файлами: ждут 0002…последней."""

    def factory(suffix: str) -> str:
        url = module_db(suffix)
        runner.migrate(url, backup=False, migrations_dir=migrations_copy([1]))
        return url

    return factory


def test_backup_made_before_first_pending(baseline_only_db, tmp_path: Path) -> None:
    url = baseline_only_db("bkp")
    bdir = tmp_path / "backups"
    result = runner.migrate(url, backup=True, backup_dir=bdir)
    assert result.backup_path is not None and result.backup_path.parent == bdir
    assert result.backup_path.name.startswith("pre_migrate_") and result.backup_path.suffix == ".dump"
    assert result.backup_path.stat().st_mode & 0o777 == 0o600
    assert not list(bdir.glob("*.part"))
    listing = subprocess.run(
        ["pg_restore", "-l", str(result.backup_path)], capture_output=True, text=True, check=True
    ).stdout
    assert "TABLE public jobs" in listing and "schema_migrations" in listing
    assert scalar(url, "SELECT max(version) FROM schema_migrations") == LATEST


def test_failed_backup_applies_nothing(baseline_only_db, tmp_path: Path) -> None:
    url = baseline_only_db("bkpfail")
    with pytest.raises(runner.BackupFailed, match="кодом 1"):
        runner.migrate(url, backup=True, backup_dir=tmp_path / "b", pg_dump_bin="false")
    with pytest.raises(runner.BackupFailed, match="не найден"):
        runner.migrate(url, backup=True, backup_dir=tmp_path / "b", pg_dump_bin="pg_dump_does_not_exist")
    assert scalar(url, "SELECT max(version) FROM schema_migrations") == 1
    assert not list((tmp_path / "b").glob("*"))


def test_no_backup_flag(baseline_only_db, tmp_path: Path) -> None:
    url = baseline_only_db("nobkp")
    bdir = tmp_path / "b"
    res = admin_cli("migrate", "--no-backup", "--database-url", url, "--backup-dir", str(bdir))
    assert res.returncode == 0, res.stderr
    assert not bdir.exists() or not list(bdir.iterdir())
    assert scalar(url, "SELECT max(version) FROM schema_migrations") == LATEST


def test_schema_state_reports_pending(baseline_only_db) -> None:
    url = baseline_only_db("pending")
    with psycopg.connect(url, autocommit=True) as conn:
        state = runner.schema_state(conn)
        assert not state.ok and state.current == 1 and state.pending == PENDING_AFTER_BASELINE
        with pytest.raises(MigrationsPending) as err:
            runner.assert_schema_current(conn)
    assert err.value.status == 503 and err.value.code == "migrations_pending"
    assert err.value.details["pending"] == list(PENDING_AFTER_BASELINE)
    with pytest.raises(MigrationsPending):
        runner.assert_schema_current(conninfo=url)


# ---------------------------------------------------------------- принятие baseline


def test_baseline_columns_match_0001(baseline_only_db) -> None:
    url = baseline_only_db("cols")
    got: dict[str, set[str]] = {}
    for table, column in rows(
        url,
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name <> 'schema_migrations'",
    ):
        got.setdefault(table, set()).add(column)
    assert got == {t: set(c) for t, c in runner.BASELINE_COLUMNS.items()}


def test_adopts_database_created_by_init_db_sql(module_db) -> None:
    url = module_db("adopt")
    res = psql(url, "-f", str(ROOT / "scripts" / "init_db.sql"))
    assert res.returncode == 0, res.stderr
    result = runner.migrate(url, backup=False)
    assert result.adopted_baseline
    assert [m.version for m in result.applied] == list(PENDING_AFTER_BASELINE)
    assert rows(url, "SELECT version, adopted FROM schema_migrations ORDER BY 1") == [(1, True)] + [
        (v, False) for v in PENDING_AFTER_BASELINE
    ]
    checksum = scalar(url, "SELECT checksum FROM schema_migrations WHERE version = 1")
    assert checksum == runner.discover_migrations()[0].checksum


def test_baseline_mismatch_refused_without_writes(module_db) -> None:
    url = module_db("mismatch")
    assert psql(url, "-f", str(ROOT / "scripts" / "init_db.sql")).returncode == 0
    assert psql(url, "-c", "ALTER TABLE jobs DROP COLUMN review_sent_at").returncode == 0
    with pytest.raises(runner.BaselineMismatch, match="review_sent_at"):
        runner.migrate(url, backup=False)
    assert scalar(url, "SELECT to_regclass('public.schema_migrations') IS NULL") is True
    assert scalar(url, "SELECT to_regclass('public.users') IS NULL") is True
