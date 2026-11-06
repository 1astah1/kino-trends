"""Все SQL-миграции применяются по порядку на пустой базе, повтор файла падает на предохранителе.

Это проверка самих SQL-файлов через psql (так, как их гоняют глазами на копии, §4.1).
Тесты раннера `db/migrate.py` (принятие baseline, checksum, legacy-копия) — tests/migrations (M1-A).
База: отдельная `autoshorts_test_contract` в том же PostgreSQL, что TEST_DATABASE_URL
(по умолчанию postgresql://autoshorts:autoshorts@127.0.0.1:5432/autoshorts_test);
создаётся и удаляется самим тестом.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from tests.contract.conftest import contract_db_name

pytestmark = pytest.mark.db

MIGRATIONS = Path(__file__).resolve().parents[2] / "src" / "autoshorts" / "db" / "migrations"
DEFAULT_URL = "postgresql://autoshorts:autoshorts@127.0.0.1:5432/autoshorts_test"
DB_NAME = contract_db_name("autoshorts_test_contract")


def _url_for(db: str) -> str:
    base = urlsplit(os.environ.get("TEST_DATABASE_URL", DEFAULT_URL))
    return urlunsplit(base._replace(path=f"/{db}"))


def _psql(db: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["psql", _url_for(db), "-X", "-q", "-v", "ON_ERROR_STOP=1", *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.fixture(scope="module")
def fresh_db():
    if not shutil.which("psql"):
        pytest.skip("psql not installed")
    probe = _psql("postgres", "-Atc", "SELECT 1")
    if probe.returncode != 0:
        pytest.skip(f"PostgreSQL not reachable: {probe.stderr.strip()[:200]}")
    drop = f'DROP DATABASE IF EXISTS "{DB_NAME}"'
    assert _psql("postgres", "-c", drop).returncode == 0
    created = _psql("postgres", "-c", f'CREATE DATABASE "{DB_NAME}"')
    if created.returncode != 0:
        pytest.skip(f"cannot create {DB_NAME}: {created.stderr.strip()[:200]}")
    try:
        yield DB_NAME
    finally:
        _psql("postgres", "-c", drop)


def test_apply_all_in_order_then_guard_blocks_reapply(fresh_db: str) -> None:
    boot = _psql(fresh_db, "-f", str(MIGRATIONS / "_bootstrap.sql"))
    assert boot.returncode == 0, boot.stderr
    files = sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    for f in files:
        version = int(f.name[:4])
        checksum = hashlib.sha256(f.read_bytes()).hexdigest()
        record = (
            "INSERT INTO schema_migrations (version, name, checksum) "
            f"VALUES ({version}, '{f.stem}', '{checksum}')"
        )
        res = _psql(fresh_db, "-1", "-f", str(f), "-c", record)
        assert res.returncode == 0, f"{f.name}: {res.stderr}"

    top = _psql(fresh_db, "-Atc", "SELECT max(version), count(*) FROM schema_migrations")
    assert top.stdout.strip() == f"{int(files[-1].name[:4])}|{len(files)}"

    again = _psql(fresh_db, "-1", "-f", str(MIGRATIONS / "0004_queue.sql"))
    assert again.returncode != 0
    assert "migration 4 already applied" in again.stderr

    seeded = _psql(
        fresh_db,
        "-Atc",
        "SELECT (SELECT count(*) FROM schedules), (SELECT count(*) FROM provider_configs),"
        " (SELECT count(*) FROM products WHERE id = 'umka')",
    )
    assert seeded.stdout.strip() == "15|15|1"  # 0019: + autopublish_tick
