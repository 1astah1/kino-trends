"""Общие фикстуры тестов (M1-A): тестовая PostgreSQL с TRUNCATE-изоляцией и окружение процесса.

База — `TEST_DATABASE_URL` (по умолчанию postgresql://autoshorts:autoshorts@127.0.0.1:5432/autoshorts_test).
Имя базы обязано начинаться с `autoshorts_test`: dev-база `autoshorts` с legacy-данными
тестами не трогается никогда. Нет PostgreSQL — DB-тесты пропускаются (skip), остальные идут.

Как это устроено:
* При первом запросе DB-фикстуры в сессии база доводится `db.migrate` до последней версии.
  Если набор миграций поменялся (другой sha256 файлов) или база в неизвестном состоянии,
  она пересоздаётся с нуля, и сразу после миграций снимается «эталон сидов» — строки, которые
  вставили сами миграции (budget_state, products, schedules, provider_configs …). Эталон
  хранится в той же базе в схеме `as_test`, поэтому следующая сессия не мигрирует заново.
* Перед каждым тестом с `clean_db`/`db_conn`/`app_env` все таблицы `public` (кроме
  `schema_migrations`) очищаются одним `TRUNCATE … RESTART IDENTITY CASCADE`, и эталон сидов
  заливается обратно `COPY`. Тест видит базу «как после migrate».
* `AUTOSHORTS_TEST_DB_ISOLATE=1` — своя база `autoshorts_test_<pid>` на сессию (удаляется в
  конце): для параллельных прогонов нескольких потоков на одной машине.

Фикстуры:
    pg_server_url   (session)  URL сервера (база postgres) — для CREATE/DROP DATABASE
    db_url          (session)  URL подготовленной тестовой базы (без очистки)
    clean_db        (function) очистить базу до сидов и вернуть её URL
    db_conn         (function) psycopg-соединение autocommit + dict_row к чистой базе, TimeZone=UTC
    env_settings    (function) окружение процесса для тестов (без БД): tmp-каталоги, ключи, APP_ENV=test
    app_env         (function) env_settings + DATABASE_URL на чистую тестовую базу
    db_pool         (function) пул `autoshorts.db.pool` на тестовой базе (закрывается после теста)
    fast_password_hasher (function) дешёвые параметры argon2id на время теста
    make_database   (function) фабрика отдельных баз `autoshorts_test_<suffix>`, удаляются после теста
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pytest

DEFAULT_TEST_DATABASE_URL = "postgresql://autoshorts:autoshorts@127.0.0.1:5432/autoshorts_test"
SAFE_DB_PREFIX = "autoshorts_test"
DB_FIXTURES = frozenset(
    {"db_url", "clean_db", "db_conn", "app_env", "db_pool", "make_database", "pg_server_url"}
)
TEST_REDIRECT_SALT = "test-redirect-salt-0123456789abcdef"

# Страховка: код, который читает DATABASE_URL сам (legacy config, core.config), в тестах
# смотрит на тестовую базу, а не на dev-базу из .env. Переменные процесса главнее .env.
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
os.environ.setdefault("APP_ENV", "test")


# ---------------------------------------------------------------- адреса и защита


def test_database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


def url_with_database(url: str, dbname: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{dbname}"))


def database_of(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def assert_safe_database(dbname: str) -> None:
    if not dbname.startswith(SAFE_DB_PREFIX):
        raise pytest.UsageError(
            f"Тестовая база {dbname!r} не начинается с {SAFE_DB_PREFIX!r}: тесты очищают базу TRUNCATE, "
            "dev-базу autoshorts трогать нельзя. Исправьте TEST_DATABASE_URL."
        )


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _admin_connect(server_url: str) -> Any:
    import psycopg

    return psycopg.connect(server_url, autocommit=True, connect_timeout=5)


DROP_ATTEMPTS = 8


def drop_database_robust(
    server_url: str,
    dbname: str,
    *,
    attempts: int = DROP_ATTEMPTS,
    prefixes: tuple[str, ...] = (SAFE_DB_PREFIX,),
) -> None:
    """`DROP DATABASE IF EXISTS` без флаки на гонках при разборе фикстур.

    Роль тестов — не суперпользователь, поэтому `WITH (FORCE)` иногда падает сам:
    `permission denied to terminate process` (в базе как раз работает autovacuum) или
    `database … is being accessed by other users` (сессия закрывающегося пула, воркера-подпроцесса
    или psql ещё не ушла, пока сервер ждал её 5 с). Поэтому на каждой попытке: закрыть базе новые
    подключения (владелец может), снять свои живые сессии, `DROP … WITH (FORCE)`, а если он отказал
    по правам — обычный `DROP` (он сам гасит autovacuum). Не вышло — пауза с ростом и повтор.
    """
    import time

    import psycopg

    if not dbname.startswith(prefixes):
        assert_safe_database(dbname)  # бросит UsageError: чужие базы тесты не удаляют
    delay = 0.2
    last: Exception | None = None
    for _ in range(max(1, attempts)):
        try:
            with _admin_connect(server_url) as conn:
                if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone():
                    return
                try:
                    conn.execute(f'ALTER DATABASE "{dbname}" WITH ALLOW_CONNECTIONS false')
                except psycopg.Error:
                    pass  # не владелец — не страшно, FORCE ниже всё равно снимет сессии
                pids = conn.execute(
                    "SELECT pid FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()"
                    " AND usename = current_user",
                    (dbname,),
                ).fetchall()
                for (pid,) in pids:
                    try:
                        conn.execute("SELECT pg_terminate_backend(%s, 3000)", (pid,))
                    except psycopg.Error:
                        pass
                try:
                    conn.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
                    return
                except (psycopg.errors.InsufficientPrivilege, psycopg.errors.ObjectInUse) as exc:
                    last = exc
                conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
                return
        except (psycopg.errors.InsufficientPrivilege, psycopg.errors.ObjectInUse) as exc:
            last = exc
        except psycopg.OperationalError as exc:  # сервер на миг не принял подключение
            last = exc
        time.sleep(delay)
        delay = min(delay * 2, 3.0)
    assert last is not None
    raise last


def _drop_database(server_url: str, dbname: str) -> None:
    assert_safe_database(dbname)
    drop_database_robust(server_url, dbname)


def create_database_robust(server_url: str, dbname: str, *, attempts: int = DROP_ATTEMPTS) -> None:
    """`CREATE DATABASE`, если её нет. Повтор на `source database "template1" is being accessed by
    other users`: шаблон на миг занят чужой сессией (соседний прогон, psql)."""
    import time

    import psycopg

    delay = 0.2
    for attempt in range(max(1, attempts)):
        with _admin_connect(server_url) as conn:
            if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone():
                return
            try:
                conn.execute(f'CREATE DATABASE "{dbname}"')
                return
            except psycopg.errors.ObjectInUse:
                if attempt + 1 >= attempts:
                    raise
        time.sleep(delay)
        delay = min(delay * 2, 3.0)


def _create_database(server_url: str, dbname: str, *, recreate: bool = False) -> None:
    assert_safe_database(dbname)
    if recreate:
        drop_database_robust(server_url, dbname)
    create_database_robust(server_url, dbname)


# ---------------------------------------------------------------- эталон сидов и очистка


SNAPSHOT_FORMAT = "seed-v2"  # смена формата эталона пересоздаёт тестовую базу


@dataclass
class SeedSnapshot:
    fingerprint: str
    tables: list[str]  # все таблицы public, кроме schema_migrations
    order: list[str]  # те же таблицы, родитель раньше ребёнка (FK)
    seeds: dict[str, bytes] = field(default_factory=dict)  # таблица -> COPY text эталона
    checksums: dict[str, str] = field(default_factory=dict)  # таблица -> md5 содержимого эталона
    sequences: dict[str, tuple[str, str]] = field(
        default_factory=dict
    )  # последовательность -> (таблица, колонка)


def migrations_fingerprint() -> str:
    from autoshorts.db.migrate import discover_migrations

    h = hashlib.sha256(SNAPSHOT_FORMAT.encode())
    for m in discover_migrations():
        h.update(f"{m.version}:{m.checksum}\n".encode())
    return h.hexdigest()


def _q(conn: Any) -> Any:
    """Курсор с кортежами: помощники работают и с dict_row-соединением теста."""
    from psycopg.rows import tuple_row

    return conn.cursor(row_factory=tuple_row)


def _public_tables(conn: Any) -> list[str]:
    rows = (
        _q(conn)
        .execute(
            "SELECT tablename FROM pg_tables"
            " WHERE schemaname = 'public' AND tablename <> 'schema_migrations' ORDER BY tablename"
        )
        .fetchall()
    )
    return [r[0] for r in rows]


def _fk_order(conn: Any, tables: list[str]) -> list[str]:
    """Топологический порядок: родитель раньше ребёнка. Циклы FK разрываются по алфавиту."""
    rows = (
        _q(conn)
        .execute(
            """
        SELECT c.conrelid::regclass::text AS child, c.confrelid::regclass::text AS parent
        FROM pg_constraint c
        WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace
        """
        )
        .fetchall()
    )
    deps: dict[str, set[str]] = {t: set() for t in tables}
    for child, parent in rows:
        child, parent = child.removeprefix("public."), parent.removeprefix("public.")
        if child in deps and parent in deps and child != parent:
            deps[child].add(parent)
    ordered: list[str] = []
    while deps:
        ready = sorted(t for t, d in deps.items() if not d) or [sorted(deps)[0]]
        for t in ready:
            ordered.append(t)
            deps.pop(t)
        for d in deps.values():
            d.difference_update(ready)
    return ordered


def _sequences(conn: Any, tables: list[str]) -> dict[str, tuple[str, str]]:
    rows = (
        _q(conn)
        .execute(
            """
        SELECT seq, table_name, column_name FROM (
          SELECT c.table_name, c.column_name,
                 pg_get_serial_sequence(format('public.%%I', c.table_name), c.column_name) AS seq
          FROM information_schema.columns c
          WHERE c.table_schema = 'public' AND c.table_name = ANY(%s)
        ) s
        WHERE seq IS NOT NULL
        """,
            (tables,),
        )
        .fetchall()
    )
    return {seq: (table, column) for seq, table, column in rows}


def _checksum_sql(table: str) -> str:
    return f"SELECT md5(coalesce(string_agg(t::text, E'\\n' ORDER BY t::text), '')) FROM public.\"{table}\" t"


def _table_checksums(conn: Any, tables: list[str]) -> dict[str, str]:
    if not tables:
        return {}
    sql = "SELECT " + ", ".join(f"({_checksum_sql(t)})" for t in tables)
    return dict(zip(tables, _q(conn).execute(sql).fetchone(), strict=True))


def _nonempty_tables(conn: Any, tables: list[str]) -> set[str]:
    if not tables:
        return set()
    sql = "SELECT " + ", ".join(f'EXISTS (SELECT 1 FROM public."{t}")' for t in tables)
    return {t for t, has in zip(tables, _q(conn).execute(sql).fetchone(), strict=True) if has}


def _capture_seeds(conn: Any) -> SeedSnapshot:
    tables = _public_tables(conn)
    snap = SeedSnapshot(
        fingerprint=migrations_fingerprint(),
        tables=tables,
        order=_fk_order(conn, tables),
        sequences=_sequences(conn, tables),
    )
    for table in snap.order:
        if table not in _nonempty_tables(conn, [table]):
            continue
        buf = bytearray()
        with _q(conn) as cur, cur.copy(f'COPY public."{table}" TO STDOUT') as copy:
            for chunk in copy:
                buf += bytes(chunk)
        snap.seeds[table] = bytes(buf)
    snap.checksums = _table_checksums(conn, list(snap.seeds))
    return snap


def _store_snapshot(conn: Any, snap: SeedSnapshot) -> None:
    _q(conn).execute("CREATE SCHEMA IF NOT EXISTS as_test")
    _q(conn).execute("DROP TABLE IF EXISTS as_test.seed, as_test.meta")
    _q(conn).execute("CREATE TABLE as_test.meta (fingerprint TEXT NOT NULL)")
    _q(conn).execute(
        "CREATE TABLE as_test.seed (table_name TEXT PRIMARY KEY, checksum TEXT NOT NULL, data BYTEA NOT NULL)"
    )
    _q(conn).execute("INSERT INTO as_test.meta VALUES (%s)", (snap.fingerprint,))
    for table, data in snap.seeds.items():
        _q(conn).execute("INSERT INTO as_test.seed VALUES (%s, %s, %s)", (table, snap.checksums[table], data))


def _load_snapshot(conn: Any) -> SeedSnapshot | None:
    if not _q(conn).execute("SELECT to_regclass('as_test.meta') IS NOT NULL").fetchone()[0]:
        return None
    row = _q(conn).execute("SELECT fingerprint FROM as_test.meta").fetchone()
    if row is None or row[0] != migrations_fingerprint():
        return None
    tables = _public_tables(conn)
    snap = SeedSnapshot(
        fingerprint=row[0], tables=tables, order=_fk_order(conn, tables), sequences=_sequences(conn, tables)
    )
    for table, checksum, data in (
        _q(conn).execute("SELECT table_name, checksum, data FROM as_test.seed").fetchall()
    ):
        snap.seeds[table] = bytes(data)
        snap.checksums[table] = checksum
    return snap


def _restore_seeds(conn: Any, snap: SeedSnapshot, tables: set[str]) -> None:
    with _q(conn) as cur:
        for table in snap.order:
            if table in tables and table in snap.seeds:
                with cur.copy(f'COPY public."{table}" FROM STDIN') as copy:
                    copy.write(snap.seeds[table])


def reset_database(conn: Any, snap: SeedSnapshot) -> None:
    """Вернуть базу к состоянию «сразу после migrate», трогая только изменённое.

    Непустые таблицы без сидов и таблицы сидов с изменённым содержимым чистятся DELETE
    (ребёнок раньше родителя), сиды заливаются обратно COPY, использованные последовательности
    сбрасываются. Если DELETE упирается в цикл FK — полный TRUNCATE … CASCADE всех таблиц.
    """
    import psycopg

    with conn.transaction():
        _q(conn).execute("SET LOCAL lock_timeout = '15s'")
        nonempty = _nonempty_tables(conn, snap.tables)
        current = _table_checksums(conn, list(snap.seeds))
        changed_seeds = {t for t, c in current.items() if c != snap.checksums.get(t)}
        dirty = (nonempty - set(snap.seeds)) | changed_seeds
        if dirty:
            try:
                with conn.transaction():
                    for table in reversed(snap.order):
                        if table in dirty:
                            _q(conn).execute(f'DELETE FROM public."{table}"')
                restore = changed_seeds
            except psycopg.errors.ForeignKeyViolation:
                _q(conn).execute(
                    "TRUNCATE "
                    + ", ".join(f'public."{t}"' for t in snap.tables)
                    + " RESTART IDENTITY CASCADE"
                )
                restore = set(snap.seeds)
            _restore_seeds(conn, snap, restore)

        used = (
            _q(conn)
            .execute(
                "SELECT format('%I.%I', schemaname, sequencename) FROM pg_sequences "
                "WHERE schemaname = 'public' AND last_value IS NOT NULL"
            )
            .fetchall()
        )
        for (seq,) in used:
            owner = snap.sequences.get(seq)
            if owner is None:
                continue
            table, column = owner
            if table in snap.seeds:
                _q(conn).execute(
                    f'SELECT setval(%s, COALESCE(max("{column}"), 1), max("{column}") IS NOT NULL) '
                    f'FROM public."{table}"',
                    (seq,),
                )
            else:
                _q(conn).execute("SELECT setval(%s, 1, false)", (seq,))


def _prepare_database(server_url: str, url: str) -> SeedSnapshot:
    """Довести базу до последней миграции и получить эталон сидов (пересоздав базу при нужде)."""
    import psycopg

    from autoshorts.db.migrate import MigrationError, migrate, schema_state

    dbname = database_of(url)
    fingerprint = migrations_fingerprint()
    _create_database(server_url, dbname)
    try:
        with psycopg.connect(url, autocommit=True) as conn:
            snap = _load_snapshot(conn)
            if snap is not None and snap.fingerprint == fingerprint and schema_state(conn).ok:
                return snap
    except (psycopg.Error, MigrationError):
        pass
    # набор миграций поменялся или база в неизвестном состоянии — с нуля
    _create_database(server_url, dbname, recreate=True)
    migrate(url, backup=False, lock_timeout=30)
    with psycopg.connect(url, autocommit=True) as conn:
        snap = _capture_seeds(conn)
        _store_snapshot(conn, snap)
    return snap


# ---------------------------------------------------------------- фикстуры


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Тесты с DB-фикстурами получают маркер `db` (для `-m "not db"`)."""
    marker = pytest.mark.db
    for item in items:
        if DB_FIXTURES.intersection(getattr(item, "fixturenames", ())):
            item.add_marker(marker)


@pytest.fixture(scope="session")
def pg_server_url() -> str:
    """URL служебной базы postgres того же сервера. Нет сервера — skip."""
    import psycopg

    url = url_with_database(test_database_url(), "postgres")
    try:
        with _admin_connect(url):
            pass
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL недоступен: {str(exc).strip().splitlines()[0] if str(exc).strip() else exc}")
    return url


@pytest.fixture(scope="session")
def _db_state(pg_server_url: str) -> Iterator[tuple[str, SeedSnapshot]]:
    base = test_database_url()
    dbname = database_of(base)
    assert_safe_database(dbname)
    isolate = _truthy(os.environ.get("AUTOSHORTS_TEST_DB_ISOLATE"))
    if isolate:
        dbname = f"{dbname}_{os.getpid()}"
    url = url_with_database(base, dbname)
    snap = _prepare_database(pg_server_url, url)
    try:
        yield url, snap
    finally:
        if isolate:
            _drop_database(pg_server_url, dbname)


@pytest.fixture(scope="session")
def db_url(_db_state: tuple[str, SeedSnapshot]) -> str:
    """URL тестовой базы на последней миграции (без очистки — для read-only тестов)."""
    return _db_state[0]


@pytest.fixture
def clean_db(_db_state: tuple[str, SeedSnapshot]) -> str:
    """Очистить тестовую базу до состояния «сразу после migrate» и вернуть её URL."""
    import psycopg

    url, snap = _db_state
    with psycopg.connect(url, autocommit=True) as conn:
        reset_database(conn, snap)
    return url


@pytest.fixture
def db_conn(clean_db: str) -> Iterator[Any]:
    """Соединение с чистой тестовой базой: autocommit, dict_row, TimeZone=UTC."""
    import psycopg
    from psycopg.rows import dict_row

    conn = psycopg.connect(clean_db, autocommit=True, row_factory=dict_row)
    conn.execute("SET TIME ZONE 'UTC'")
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def env_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Any]:
    """Окружение процесса для тестов без БД: APP_ENV=test, каталоги в tmp_path, свежий ключ секретов."""
    from autoshorts.core.config import get_env, reload_env
    from autoshorts.core.security import generate_fernet_key

    for name in ("SESSION_COOKIE_SECURE", "AS_SECRETS_KEY_FILE", "LOG_FORMAT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setenv("MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("AS_SECRETS_KEY", generate_fernet_key())
    monkeypatch.setenv("REDIRECT_SALT", TEST_REDIRECT_SALT)
    env = reload_env()
    try:
        yield env
    finally:
        get_env.cache_clear()


@pytest.fixture
def app_env(env_settings: Any, clean_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """`env_settings` + DATABASE_URL на очищенную тестовую базу. Пул процесса закрывается после теста."""
    from autoshorts.core.config import get_env, reload_env
    from autoshorts.db.pool import close_pool

    monkeypatch.setenv("DATABASE_URL", clean_db)
    env = reload_env()
    try:
        yield env
    finally:
        close_pool()
        get_env.cache_clear()


@pytest.fixture
def db_pool(app_env: Any) -> Iterator[Any]:
    """Пул `autoshorts.db.pool` на тестовой базе (max 4 соединения)."""
    from autoshorts.db.pool import close_pool, init_pool

    pool = init_pool(app_env.database_url, min_size=1, max_size=4, application_name="autoshorts-tests")
    try:
        yield pool
    finally:
        close_pool()


@pytest.fixture
def fast_password_hasher() -> Iterator[None]:
    """argon2id с минимальными параметрами: тесты входа не тратят по 60 МБ и 50 мс на хеш."""
    from autoshorts.core.security import configure_password_hasher, restore_password_hasher

    previous = configure_password_hasher(time_cost=1, memory_cost=8, parallelism=1)
    try:
        yield
    finally:
        restore_password_hasher(previous)


@pytest.fixture
def make_database(pg_server_url: str) -> Iterator[Callable[[str], str]]:
    """Фабрика пустых баз `autoshorts_test_<suffix>`; все созданные удаляются после теста."""
    created: list[str] = []

    def factory(suffix: str) -> str:
        dbname = f"{SAFE_DB_PREFIX}_{suffix}"
        _create_database(pg_server_url, dbname, recreate=True)
        created.append(dbname)
        return url_with_database(test_database_url(), dbname)

    try:
        yield factory
    finally:
        for dbname in reversed(created):
            _drop_database(pg_server_url, dbname)
