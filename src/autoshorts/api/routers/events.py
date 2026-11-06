"""SSE-поток событий (§5.1), viewer+ (cookie-сессия; EventSource с withCredentials). Владелец — M1-B.

Схема кадра — `SseEvent` (domain/events.py); источник — `LISTEN as_events` (один на процесс,
`api/sse.py`). `ping` сразу после подключения и раз в 15 с тишины.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from autoshorts.api.deps import ViewerDep
from autoshorts.api.routers import EventStreamResponse
from autoshorts.api.schemas.common import json_error_responses
from autoshorts.api.schemas.system import SseEvent
from autoshorts.api.sse import sse_response
from autoshorts.domain.events import SSE_PING_INTERVAL_SEC

router = APIRouter(tags=["system"])


@router.get(
    "/events/stream",
    name="events_stream",
    response_model=SseEvent,
    response_class=EventStreamResponse,
    responses=json_error_responses(),
    openapi_extra={"x-sse-event-schema": {"$ref": "#/components/schemas/SseEvent"}},
    response_description="Кадры SSE; форма одного кадра — схема SseEvent (x-sse-event-schema)",
    summary="Поток событий (text/event-stream), ping раз в 15 с",
)
async def events_stream(principal: ViewerDep, request: Request):
    ping = getattr(request.app.state, "sse_ping_interval", None) or SSE_PING_INTERVAL_SEC
    return sse_response(request.app.state.events, ping_interval=ping)
