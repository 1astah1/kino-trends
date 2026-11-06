"""LISTEN/NOTIFY: события для SSE и пробуждение воркеров (docs/ARCHITECTURE.md §5.1, §6.1).

Отправка — в той же транзакции, что и изменение (NOTIFY доставляется после COMMIT и
пропадает при ROLLBACK):

    with connection() as conn:
        conn.execute("UPDATE jobs SET status = ... WHERE id = %(id)s", {...})
        publish_event(conn, EventType.JOB_STATUS, {"job_id": 1, "status": "processing", "stage": "voice"})

Приём — отдельное долгоживущее autocommit-соединение вне пула:

    with Listener([EVENTS_CHANNEL]) as listener:
        for note in listener.wait(timeout=15):      # [] по таймауту
            event, data = decode_notify(note.payload)

`Listener` сам переподключается после обрыва (в выдаче появляется `Reconnected`, чтобы
потребитель мог перечитать состояние, пропущенное за время обрыва). Асинхронный вариант
для SSE-эндпоинта — `AsyncListener`.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import sql
from pydantic import BaseModel

from autoshorts.domain.events import EVENTS_CHANNEL, TASKS_CHANNEL, EventType, encode_notify

log = logging.getLogger(__name__)

CHANNEL_RE = re.compile(r"[a-z_][a-z0-9_]{0,62}")
RECONNECT_DELAYS_SEC = (0.5, 1.0, 2.0, 5.0, 10.0)


@dataclass(frozen=True)
class Notification:
    channel: str
    payload: str
    pid: int


@dataclass(frozen=True)
class Reconnected:
    """Маркер: соединение слушателя восстановлено, уведомления за время обрыва потеряны."""

    channels: tuple[str, ...]


def _check_channel(channel: str) -> str:
    if not CHANNEL_RE.fullmatch(channel):
        raise ValueError(f"invalid LISTEN/NOTIFY channel name: {channel!r}")
    return channel


# ---------------------------------------------------------------- отправка


def notify(conn: psycopg.Connection[Any], channel: str, payload: str = "") -> None:
    """`pg_notify(channel, payload)` в текущей транзакции `conn`."""
    conn.execute(
        "SELECT pg_notify(%(channel)s, %(payload)s)", {"channel": _check_channel(channel), "payload": payload}
    )


def publish_event(
    conn: psycopg.Connection[Any], event: EventType | str, data: BaseModel | dict[str, Any]
) -> str:
    """Событие SSE в канал `as_events` (схема данных проверяется, ≤ 8000 байт). Возвращает payload."""
    payload = encode_notify(event, data)
    notify(conn, EVENTS_CHANNEL, payload)
    return payload


def wake_workers(conn: psycopg.Connection[Any], resource_class: str) -> None:
    """Разбудить воркеры класса `heavy|light` после вставки задачи (канал `as_tasks`)."""
    notify(conn, TASKS_CHANNEL, resource_class)


# ---------------------------------------------------------------- приём (sync)


class Listener:
    """LISTEN на одном выделенном autocommit-соединении с автоматическим переподключением."""

    def __init__(
        self,
        channels: Iterable[str],
        conninfo: str | None = None,
        *,
        application_name: str = "autoshorts-listener",
    ) -> None:
        self.channels = tuple(_check_channel(c) for c in channels)
        if not self.channels:
            raise ValueError("Listener needs at least one channel")
        self._conninfo = conninfo
        self._application_name = application_name
        self._conn: psycopg.Connection[Any] | None = None
        self._pending_reconnect_marker = False

    # контекстный менеджер
    def __enter__(self) -> Listener:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _resolve_conninfo(self) -> str:
        if self._conninfo:
            return self._conninfo
        from autoshorts.core.config import get_env

        return get_env().database_url

    def connect(self) -> None:
        self.close()
        conn = psycopg.connect(
            self._resolve_conninfo(),
            autocommit=True,
            application_name=self._application_name,
            connect_timeout=5,
        )
        for channel in self.channels:
            conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(channel)))
        self._conn = conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    @property
    def connected(self) -> bool:
        return self._conn is not None and not self._conn.closed

    def _reconnect(self, deadline: float) -> bool:
        for delay in RECONNECT_DELAYS_SEC:
            try:
                self.connect()
                self._pending_reconnect_marker = True
                log.info("listener reconnected", extra={"channels": ",".join(self.channels)})
                return True
            except psycopg.OperationalError as exc:
                log.warning("listener reconnect failed: %s", exc)
                if time.monotonic() + delay > deadline:
                    return False
                time.sleep(delay)
        return False

    def wait(self, timeout: float) -> list[Notification | Reconnected]:
        """Ждать до `timeout` секунд первое уведомление и забрать всё накопившееся.

        Возвращает список (пустой по таймауту). При обрыве соединения пытается переподключиться
        в пределах того же таймаута и кладёт в выдачу `Reconnected`.
        """
        deadline = time.monotonic() + max(timeout, 0.0)
        out: list[Notification | Reconnected] = []
        if not self.connected and not self._reconnect(deadline):
            return out
        if self._pending_reconnect_marker:
            out.append(Reconnected(self.channels))
            self._pending_reconnect_marker = False
        assert self._conn is not None
        try:
            remaining = max(deadline - time.monotonic(), 0.0)
            for n in self._conn.notifies(timeout=remaining, stop_after=1):
                out.append(Notification(n.channel, n.payload, n.pid))
            if any(isinstance(x, Notification) for x in out):
                for n in self._conn.notifies(timeout=0.0):
                    out.append(Notification(n.channel, n.payload, n.pid))
        except psycopg.OperationalError as exc:
            log.warning("listener connection lost: %s", exc)
            self.close()
            if self._reconnect(deadline):
                out.append(Reconnected(self.channels))
                self._pending_reconnect_marker = False
        return out


# ---------------------------------------------------------------- приём (async, для SSE)


class AsyncListener:
    """Асинхронный LISTEN (psycopg AsyncConnection). Переподключение — на стороне потребителя:
    при `psycopg.OperationalError` из `wait()` вызовите `connect()` снова и перечитайте состояние.
    """

    def __init__(
        self,
        channels: Iterable[str],
        conninfo: str | None = None,
        *,
        application_name: str = "autoshorts-sse",
    ) -> None:
        self.channels = tuple(_check_channel(c) for c in channels)
        if not self.channels:
            raise ValueError("AsyncListener needs at least one channel")
        self._conninfo = conninfo
        self._application_name = application_name
        self._conn: psycopg.AsyncConnection[Any] | None = None

    async def __aenter__(self) -> AsyncListener:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        await self.close()
        conninfo = self._conninfo
        if not conninfo:
            from autoshorts.core.config import get_env

            conninfo = get_env().database_url
        conn = await psycopg.AsyncConnection.connect(
            conninfo, autocommit=True, application_name=self._application_name, connect_timeout=5
        )
        for channel in self.channels:
            await conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(channel)))
        self._conn = conn

    async def close(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.close()
            finally:
                self._conn = None

    async def wait(self, timeout: float) -> list[Notification]:
        """До `timeout` секунд ждать первое уведомление, затем забрать накопившиеся."""
        if self._conn is None:
            await self.connect()
        assert self._conn is not None
        out: list[Notification] = []
        async for n in self._conn.notifies(timeout=max(timeout, 0.0), stop_after=1):
            out.append(Notification(n.channel, n.payload, n.pid))
        if out:
            async for n in self._conn.notifies(timeout=0.0):
                out.append(Notification(n.channel, n.payload, n.pid))
        return out
