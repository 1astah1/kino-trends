"""`autoshorts.db.pool`: пул psycopg-pool, транзакции, UTC, dict-строки."""

from __future__ import annotations

from datetime import UTC

import psycopg
import pytest

from autoshorts.db import pool as dbpool


def test_connection_commits_on_success(db_pool) -> None:
    with dbpool.connection() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('fx_usd_rub', '95.5')")
    with dbpool.connection() as conn:
        row = conn.execute("SELECT key, value FROM settings").fetchone()
    assert row == {"key": "fx_usd_rub", "value": 95.5}


def test_connection_rolls_back_on_error(db_pool) -> None:
    with pytest.raises(RuntimeError), dbpool.transaction() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('fx_usd_rub', '95.5')")
        raise RuntimeError("boom")
    with dbpool.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM settings").fetchone()["n"] == 0


def test_session_timezone_utc_and_aware_datetimes(db_pool) -> None:
    with dbpool.connection() as conn:
        tz = conn.execute("SHOW TimeZone").fetchone()["TimeZone"]
        now = conn.execute("SELECT now() AS now").fetchone()["now"]
    assert tz == "UTC"
    assert now.tzinfo is not None and now.utcoffset() == UTC.utcoffset(now)


def test_get_pool_lazily_uses_database_url(app_env) -> None:
    dbpool.close_pool()
    pool = dbpool.get_pool()
    try:
        assert pool is dbpool.get_pool()
        assert dbpool.ping()
        with dbpool.connection() as conn:
            assert (
                conn.execute("SELECT current_database() AS d").fetchone()["d"]
                == app_env.database_url.rsplit("/", 1)[-1]
            )
    finally:
        dbpool.close_pool()


def test_init_pool_replaces_previous(db_pool) -> None:
    second = dbpool.init_pool(db_pool.conninfo, min_size=1, max_size=2, application_name="autoshorts-tests-2")
    assert db_pool.closed and not second.closed
    with dbpool.connection() as conn:
        name = conn.execute("SHOW application_name").fetchone()["application_name"]
    assert name == "autoshorts-tests-2"


def test_connect_outside_pool(app_env) -> None:
    conn = dbpool.connect(autocommit=True, application_name="autoshorts-tests-direct")
    try:
        row = conn.execute("SHOW TimeZone").fetchone()
        assert row == {"TimeZone": "UTC"}
    finally:
        conn.close()


def test_ping_false_when_db_unreachable(env_settings) -> None:
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect("postgresql://autoshorts:x@127.0.0.1:1/none", connect_timeout=1)
    dbpool.init_pool("postgresql://autoshorts:x@127.0.0.1:1/none", wait=False, timeout=0.5)
    try:
        assert dbpool.ping(timeout=0.5) is False
    finally:
        dbpool.close_pool()
