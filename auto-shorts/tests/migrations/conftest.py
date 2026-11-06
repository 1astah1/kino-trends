"""Помощники тестов раннера миграций: копии каталога миграций, psql, запуск CLI, отдельные базы."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from autoshorts.db.migrate import BOOTSTRAP_FILENAME, MIGRATIONS_DIR
from tests.conftest import create_database_robust, drop_database_robust

ROOT = Path(__file__).resolve().parents[2]
LEGACY_DB_PREFIX = "autoshorts_legacy_check"
ALLOWED_DB_PREFIXES = ("autoshorts_test", LEGACY_DB_PREFIX)


def copy_migrations(target: Path, versions: Iterable[int] | None = None) -> Path:
    """Копия `db/migrations` (все версии или выбранные) + `_bootstrap.sql` в `target`."""
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(MIGRATIONS_DIR / BOOTSTRAP_FILENAME, target / BOOTSTRAP_FILENAME)
    wanted = set(versions) if versions is not None else None
    for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        if wanted is None or int(path.name[:4]) in wanted:
            shutil.copy2(path, target / path.name)
    return target


def psql(url: str, *args: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    if not shutil.which("psql"):
        pytest.skip("psql не установлен")
    return subprocess.run(
        ["psql", url, "-X", "-q", "-v", "ON_ERROR_STOP=1", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def scalar(url: str, query: str, params: Any = None) -> Any:
    with psycopg.connect(url, autocommit=True) as conn:
        row = conn.execute(query, params).fetchone()
        return None if row is None else row[0]


def rows(url: str, query: str, params: Any = None) -> list[tuple[Any, ...]]:
    with psycopg.connect(url, autocommit=True) as conn:
        return conn.execute(query, params).fetchall()


def admin_cli(
    *args: str, env: dict[str, str] | None = None, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    """`autoshorts-admin …` отдельным процессом (так же, как его вызовет оператор)."""
    script = Path(sys.executable).with_name("autoshorts-admin")
    cmd = (
        [str(script)]
        if script.exists()
        else [sys.executable, "-c", "import sys; from autoshorts.cli import main; sys.exit(main())"]
    )
    full_env = dict(os.environ)
    full_env.update(env or {})
    return subprocess.run(
        [*cmd, *args], capture_output=True, text=True, timeout=300, env=full_env, input=stdin
    )


def create_db(server_url: str, dbname: str) -> str:
    assert dbname.startswith(ALLOWED_DB_PREFIXES), dbname
    drop_database_robust(server_url, dbname, prefixes=ALLOWED_DB_PREFIXES)
    create_database_robust(server_url, dbname)
    return server_url.rsplit("/", 1)[0] + f"/{dbname}"


def drop_db(server_url: str, dbname: str) -> None:
    """Удалить базу; гонки с autovacuum и уходящими сессиями — повтор (`drop_database_robust`)."""
    assert dbname.startswith(ALLOWED_DB_PREFIXES), dbname
    drop_database_robust(server_url, dbname, prefixes=ALLOWED_DB_PREFIXES)


@pytest.fixture
def migrations_copy(tmp_path: Path) -> Callable[..., Path]:
    """Фабрика копий каталога миграций: `migrations_copy()` — все, `migrations_copy([1])` — только 0001."""
    counter = iter(range(1000))

    def factory(versions: Iterable[int] | None = None) -> Path:
        return copy_migrations(tmp_path / f"migrations_{next(counter)}", versions)

    return factory


@pytest.fixture(scope="module")
def module_db(pg_server_url: str, request: pytest.FixtureRequest) -> Iterator[Callable[[str], str]]:
    """Фабрика баз на время модуля: `module_db("full")` → `autoshorts_test_<module>_full`."""
    created: list[str] = []
    stem = request.module.__name__.rsplit(".", 1)[-1].removeprefix("test_")

    def factory(suffix: str) -> str:
        dbname = f"autoshorts_test_{stem}_{suffix}"[:63]
        created.append(dbname)
        return create_db(pg_server_url, dbname)

    try:
        yield factory
    finally:
        for name in created:
            drop_db(pg_server_url, name)
