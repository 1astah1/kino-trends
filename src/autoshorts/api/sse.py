"""SSE `GET /api/v1/events/stream` (docs/ARCHITECTURE.md §5.1 «SSE»).

Один `LISTEN as_events` на процесс API (`EventBroadcaster`), от него — очередь на каждого
подписчика. Кадр: `event: <тип>\\ndata: <json>\\n\\n`; `ping` сразу после подключения и
раз в 15 с тишины. Полезная нагрузка NOTIFY проверяется схемой события (`domain.events`),
битые уведомления пропускаются с предупреждением.

Если соединение LISTEN оборвалось, все открытые потоки закрываются: браузер переподключится
(EventSource) и перечитает состояние, а не останется с дырой в событиях.

Заголовки ответа: `Cache-Control: no-cache`, `X-Accel-Buffering: no` (nginx не буферизует).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any

from starlette.responses import StreamingResponse

from autoshorts.domain.events import (
    EVENTS_CHANNEL,
    SSE_PING_INTERVAL_SEC,
    EventType,
    decode_notify,
    format_sse,
)

log = logging.getLogger(__name__)

QUEUE_MAX = 256
RECONNECT_DELAYS_SEC = (0.5, 1.0, 2.0, 5.0, 10.0)
LISTEN_POLL_SEC = 5.0
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
_CLOSE = object()


class Subscription:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=QUEUE_MAX)
        self.dropped = 0

    def put(self, item: Any) -> None:
        """Не блокирует: медленный клиент теряет самые старые кадры, а не тормозит остальных."""
        while True:
            try:
                self.queue.put_nowait(item)
                return
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueEmpty):
                    self.queue.get_nowait()
                    self.dropped += 1


class EventBroadcaster:
    """LISTEN `as_events` → кадры SSE всем подписчикам процесса."""

    def __init__(self, conninfo: str | None = None, channel: str = EVENTS_CHANNEL) -> None:
        self.conninfo = conninfo
        self.channel = channel
        self._subs: set[Subscription] = set()
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self.connected: asyncio.Event | None = None  # создаётся в цикле событий при первом subscribe
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def subscribers(self) -> int:
        return len(self._subs)

    def _ensure_task(self) -> None:
        loop = asyncio.get_running_loop()
        if self._task is not None and not self._task.done() and self._loop is loop:
            return
        self._loop = loop
        self.connected = asyncio.Event()
        self._task = loop.create_task(self._run(), name="autoshorts-sse-listener")

    async def subscribe(self) -> Subscription:
        if self._closed:
            raise RuntimeError("broadcaster closed")
        sub = Subscription()
        self._subs.add(sub)
        self._ensure_task()
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        self._subs.discard(sub)

    def publish_frame(self, frame: str) -> None:
        for sub in list(self._subs):
            sub.put(frame)

    def _close_all(self) -> None:
        for sub in list(self._subs):
            sub.put(_CLOSE)

    async def _run(self) -> None:
        from autoshorts.db.notify import AsyncListener

        attempt = 0
        listener = AsyncListener([self.channel], self.conninfo, application_name="autoshorts-api-sse")
        try:
            while not self._closed:
                try:
                    await listener.connect()
                    attempt = 0
                    if self.connected is not None:
                        self.connected.set()
                    while not self._closed:
                        for note in await listener.wait(LISTEN_POLL_SEC):
                            self._dispatch(note.payload)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - любой сбой LISTEN: переподключиться
                    if self.connected is not None:
                        self.connected.clear()
                    delay = RECONNECT_DELAYS_SEC[min(attempt, len(RECONNECT_DELAYS_SEC) - 1)]
                    attempt += 1
                    log.warning("sse listener lost (%s), reconnect in %.1fs", exc, delay)
                    self._close_all()
                    await listener.close()
                    await asyncio.sleep(delay)
        finally:
            with contextlib.suppress(Exception):
                await listener.close()

    def _dispatch(self, payload: str) -> None:
        try:
            event, data = decode_notify(payload)
        except Exception as exc:  # noqa: BLE001 - чужой/битый NOTIFY не ломает поток
            log.warning("bad as_events payload skipped: %s", str(exc)[:200])
            return
        self.publish_frame(format_sse(event, data))

    async def close(self) -> None:
        self._closed = True
        self._close_all()
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


async def event_frames(
    broadcaster: EventBroadcaster, *, ping_interval: float = SSE_PING_INTERVAL_SEC
) -> AsyncIterator[str]:
    """Кадры одного клиента: ping сразу, события, ping после `ping_interval` тишины."""
    sub = await broadcaster.subscribe()
    try:
        yield "retry: 5000\n\n"
        yield format_sse(EventType.PING, {})
        while True:
            try:
                item = await asyncio.wait_for(sub.queue.get(), timeout=ping_interval)
            except TimeoutError:
                item = format_sse(EventType.PING, {})
            if item is _CLOSE:
                return
            yield item
    finally:
        broadcaster.unsubscribe(sub)


def sse_response(
    broadcaster: EventBroadcaster, *, ping_interval: float = SSE_PING_INTERVAL_SEC
) -> StreamingResponse:
    return StreamingResponse(
        event_frames(broadcaster, ping_interval=ping_interval),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
