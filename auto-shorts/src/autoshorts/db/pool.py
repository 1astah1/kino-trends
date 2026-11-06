"""Пул соединений PostgreSQL на psycopg-pool (docs/ARCHITECTURE.md §0: psycopg 3 без ORM).

Один пул на процесс. Соединения: `row_factory=dict_row`, сессия в `TimeZone=UTC` (все
`timestamptz` приходят aware-UTC), `application_name` процесса, проверка живости при выдаче.

    from autoshorts.db.pool import init_pool, connection, close_pool
    init_pool(application_name="autoshorts-api")   # при старте (иначе создастся лениво)
    with connection() as conn:                     # транзакция: commit при успехе, rollback при ошибке
        conn.execute("UPDATE ...", {...})
    close_pool()                                   # при остановке

Репозитории (`repos/*`) принимают `conn` и сами соединения не открывают. Для LISTEN и раннера
миграций нужны отдельные долгоживущие соединения — `connect()` (не из пула).
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Iterator
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)

DEFAULT_MIN_SIZE = 1
DEFAULT_MAX_SIZE = 5
DEFAULT_TIMEOUT_SEC = 30.0  # сколько ждать свободное соединение
DEFAULT_OPEN_TIMEOUT_SEC = 10.0
DEFAULT_CONNECT_TIMEOUT_SEC = 5

Connection = psycopg.Connection[dict[str, Any]]

_lock = threading.Lock()
_pool: ConnectionPool | None = None


def _conninfo(conninfo: str | None) -> str:
    if conninfo:
        return conninfo
    from autoshorts.core.config import get_env

    return get_env().database_url


def _configure(conn: psycopg.Connection[Any]) -> None:
    """Колбэк пула для нового соединения: UTC-сессия. Соединение должно остаться idle."""
    conn.execute("SET TIME ZONE 'UTC'")
    conn.commit()


def init_pool(
    conninfo: str | None = None,
    *,
    min_size: int = DEFAULT_MIN_SIZE,
    max_size: int = DEFAULT_MAX_SIZE,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    open_timeout: float = DEFAULT_OPEN_TIMEOUT_SEC,
    application_name: str = "autoshorts",
    wait: bool = True,
) -> ConnectionPool:
    """Создать (или пересоздать) пул процесса. `conninfo` по умолчанию — `DATABASE_URL`.

    `wait=True` — дождаться `min_size` соединений, иначе `psycopg_pool.PoolTimeout` (БД недоступна).
    """
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None
        pool = ConnectionPool(
            _conninfo(conninfo),
            min_size=min_size,
            max_size=max(max_size, min_size),
            timeout=timeout,
            kwargs={
                "row_factory": dict_row,
                "application_name": application_name,
                "connect_timeout": DEFAULT_CONNECT_TIMEOUT_SEC,
            },
            configure=_configure,
            check=ConnectionPool.check_connection,
            name=application_name,
            open=False,
        )
        pool.open(wait=wait, timeout=open_timeout)
        _pool = pool
        return pool


def get_pool() -> ConnectionPool:
    """Пул процесса; если `init_pool` не вызывался — создаётся с параметрами по умолчанию."""
    pool = _pool
    if pool is not None:
        return pool
    return init_pool()


def close_pool() -> None:
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None


@contextlib.contextmanager
def connection(*, timeout: float | None = None) -> Iterator[Connection]:
    """Соединение из пула на одну транзакцию: commit при выходе, rollback при исключении."""
    with get_pool().connection(timeout=timeout) as conn:
        yield conn


@contextlib.contextmanager
def transaction(*, timeout: float | None = None) -> Iterator[Connection]:
    """Синоним `connection()` — подчёркивает границу транзакции в сервисах."""
    with connection(timeout=timeout) as conn:
        yield conn


def connect(
    conninfo: str | None = None,
    *,
    autocommit: bool = False,
    application_name: str = "autoshorts",
    utc: bool = True,
) -> Connection:
    """Отдельное соединение вне пула (LISTEN, раннер миграций, скрипты). Закрывать самому."""
    conn = psycopg.connect(
        _conninfo(conninfo),
        autocommit=autocommit,
        row_factory=dict_row,
        application_name=application_name,
        connect_timeout=DEFAULT_CONNECT_TIMEOUT_SEC,
    )
    if utc:
        conn.execute("SET TIME ZONE 'UTC'")
        if not autocommit:
            conn.commit()
    return conn


def ping(*, timeout: float = 5.0) -> bool:
    """Жива ли БД через пул (для /health/ready)."""
    try:
        with connection(timeout=timeout) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception as exc:  # noqa: BLE001 - любой сбой означает «не готово»
        log.warning("db ping failed: %s", exc)
        return False
