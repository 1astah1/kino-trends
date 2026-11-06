"""`autoshorts.db.notify`: NOTIFY в транзакции, Listener, переподключение, AsyncListener."""

from __future__ import annotations

import asyncio
import time

import psycopg
import pytest

from autoshorts.db import notify as dbnotify
from autoshorts.domain.events import EVENTS_CHANNEL, TASKS_CHANNEL, EventType, decode_notify


def test_event_delivered_after_commit_only(clean_db: str) -> None:
    with dbnotify.Listener([EVENTS_CHANNEL], clean_db) as listener:
        with psycopg.connect(clean_db) as conn:
            dbnotify.publish_event(conn, EventType.REVIEW_QUEUE, {"count": 3})
            assert listener.wait(0.2) == []  # ещё не COMMIT
            conn.commit()
        got = listener.wait(2.0)
    assert len(got) == 1 and isinstance(got[0], dbnotify.Notification)
    assert got[0].channel == EVENTS_CHANNEL
    event, data = decode_notify(got[0].payload)
    assert event is EventType.REVIEW_QUEUE and data.count == 3


def test_rollback_drops_notification(clean_db: str) -> None:
    with dbnotify.Listener([EVENTS_CHANNEL], clean_db) as listener:
        with psycopg.connect(clean_db) as conn:
            dbnotify.publish_event(conn, EventType.REVIEW_QUEUE, {"count": 1})
            conn.rollback()
        assert listener.wait(0.3) == []


def test_wait_collects_burst_and_times_out(clean_db: str) -> None:
    with dbnotify.Listener([EVENTS_CHANNEL, TASKS_CHANNEL], clean_db) as listener:
        with psycopg.connect(clean_db, autocommit=True) as conn:
            dbnotify.wake_workers(conn, "heavy")
            dbnotify.wake_workers(conn, "light")
            dbnotify.publish_event(
                conn, "budget", {"spent_usd": 1, "reserved_usd": 0, "limit_usd": 15, "paused": False}
            )
        got: list = []
        deadline = time.monotonic() + 3.0
        while len(got) < 3 and time.monotonic() < deadline:
            got.extend(listener.wait(1.0))  # разные транзакции могут прийти разными пачками
        assert [(n.channel, n.payload) for n in got] == [
            (TASKS_CHANNEL, "heavy"),
            (TASKS_CHANNEL, "light"),
            (EVENTS_CHANNEL, got[2].payload),
        ]
        assert decode_notify(got[2].payload)[0] is EventType.BUDGET
        t0 = time.monotonic()
        assert listener.wait(0.3) == []
        assert 0.2 < time.monotonic() - t0 < 2.0


def test_invalid_channel_and_payload_rejected(clean_db: str) -> None:
    with pytest.raises(ValueError):
        dbnotify.Listener(["as-events; DROP"], clean_db)
    with psycopg.connect(clean_db, autocommit=True) as conn:
        with pytest.raises(ValueError):
            dbnotify.notify(conn, "Канал", "x")
        with pytest.raises(ValueError):
            dbnotify.publish_event(conn, EventType.REVIEW_QUEUE, {"count": "много"})


def test_listener_reconnects_after_backend_killed(clean_db: str) -> None:
    listener = dbnotify.Listener([EVENTS_CHANNEL], clean_db, application_name="as-test-listener")
    listener.connect()
    try:
        with psycopg.connect(clean_db, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE application_name = 'as-test-listener' AND pid <> pg_backend_pid()"
            )
        got = listener.wait(3.0)
        assert any(isinstance(x, dbnotify.Reconnected) for x in got)
        assert listener.connected
        with psycopg.connect(clean_db, autocommit=True) as conn:
            dbnotify.publish_event(conn, EventType.REVIEW_QUEUE, {"count": 7})
        after = [x for x in listener.wait(2.0) if isinstance(x, dbnotify.Notification)]
        assert len(after) == 1
    finally:
        listener.close()


def test_async_listener(clean_db: str) -> None:
    async def scenario() -> list[dbnotify.Notification]:
        async with dbnotify.AsyncListener([EVENTS_CHANNEL], clean_db) as listener:
            assert await listener.wait(0.1) == []
            with psycopg.connect(clean_db, autocommit=True) as conn:
                dbnotify.publish_event(
                    conn, EventType.ALERT, {"alert_id": 5, "code": "disk_low", "severity": "warning"}
                )
            return await listener.wait(2.0)

    got = asyncio.run(scenario())
    assert len(got) == 1
    event, data = decode_notify(got[0].payload)
    assert event is EventType.ALERT and data.alert_id == 5
