"""События реального времени (docs/ARCHITECTURE.md §5.1, таблица SSE).

Путь события: сервис делает `NOTIFY as_events, '<json>'` в той же транзакции, что и изменение,
API слушает `LISTEN as_events` и отдаёт `GET /api/v1/events/stream` (text/event-stream):

    event: job_status
    data: {"job_id": 1234, "status": "pending_review", "stage": "qc"}

Имена событий — snake_case. Полезная нагрузка NOTIFY ≤ 8000 байт (`encode_notify`).
Вставка задачи будит воркеры отдельным каналом: `NOTIFY as_tasks, '<heavy|light>'`.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from autoshorts.domain.enums import (
    AlertSeverity,
    JobStage,
    JobStatus,
    ResourceClass,
    SourceStatus,
)

EVENTS_CHANNEL = "as_events"
TASKS_CHANNEL = "as_tasks"
MAX_NOTIFY_PAYLOAD_BYTES = 8000
SSE_PING_INTERVAL_SEC = 15


class EventType(StrEnum):
    JOB_STATUS = "job_status"
    JOB_PROGRESS = "job_progress"
    TASK_PROGRESS = "task_progress"
    REVIEW_QUEUE = "review_queue"
    PACKAGES = "packages"
    ALERT = "alert"
    WORKER = "worker"
    BUDGET = "budget"
    SOURCE_STATUS = "source_status"
    PUBLICATION_METRICS = "publication_metrics"
    PING = "ping"


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobStatusEvent(_Event):
    job_id: int
    status: JobStatus
    stage: JobStage | None = None


class JobProgressEvent(_Event):
    job_id: int
    task_id: int
    stage: JobStage | None = None
    pct: float


class TaskProgressEvent(_Event):
    task_id: int
    kind: str
    pct: float
    source_id: int | None = None


class ReviewQueueEvent(_Event):
    count: int


class PackagesEvent(_Event):
    ready: int
    downloaded: int


class AlertEvent(_Event):
    alert_id: int
    code: str
    severity: AlertSeverity


class WorkerEvent(_Event):
    worker_id: str
    resource_class: ResourceClass
    state: str  # idle | busy | deferred:<reason> | stopping
    task_id: int | None = None


class BudgetEvent(_Event):
    spent_usd: float
    reserved_usd: float
    limit_usd: float
    paused: bool


class SourceStatusEvent(_Event):
    source_id: int
    status: SourceStatus
    pct: float | None = None


class PublicationMetricsEvent(_Event):
    publication_id: int


class PingEvent(_Event):
    pass


EVENT_PAYLOADS: dict[EventType, type[BaseModel]] = {
    EventType.JOB_STATUS: JobStatusEvent,
    EventType.JOB_PROGRESS: JobProgressEvent,
    EventType.TASK_PROGRESS: TaskProgressEvent,
    EventType.REVIEW_QUEUE: ReviewQueueEvent,
    EventType.PACKAGES: PackagesEvent,
    EventType.ALERT: AlertEvent,
    EventType.WORKER: WorkerEvent,
    EventType.BUDGET: BudgetEvent,
    EventType.SOURCE_STATUS: SourceStatusEvent,
    EventType.PUBLICATION_METRICS: PublicationMetricsEvent,
    EventType.PING: PingEvent,
}


def encode_notify(event: EventType | str, data: BaseModel | dict[str, Any]) -> str:
    """JSON для `NOTIFY as_events`: `{"event": "<type>", "data": {...}}`.

    Данные валидируются схемой события. Больше 8000 байт — ValueError (в NOTIFY кладут
    идентификаторы, а не документы).
    """
    etype = EventType(event)
    model = EVENT_PAYLOADS[etype]
    payload = data if isinstance(data, model) else model.model_validate(data)
    text = json.dumps(
        {"event": etype.value, "data": payload.model_dump(mode="json", exclude_none=True)},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(text.encode("utf-8")) > MAX_NOTIFY_PAYLOAD_BYTES:
        raise ValueError(f"NOTIFY payload for {etype.value} exceeds {MAX_NOTIFY_PAYLOAD_BYTES} bytes")
    return text


def decode_notify(text: str) -> tuple[EventType, BaseModel]:
    """Обратная операция для слушателя в API."""
    raw = json.loads(text)
    etype = EventType(raw["event"])
    return etype, EVENT_PAYLOADS[etype].model_validate(raw.get("data", {}))


def format_sse(event: EventType | str, data: BaseModel | dict[str, Any]) -> str:
    """Кадр SSE: `event: <type>\\ndata: <json>\\n\\n`."""
    etype = EventType(event)
    body = data.model_dump(mode="json", exclude_none=True) if isinstance(data, BaseModel) else data
    return f"event: {etype.value}\ndata: {json.dumps(body, ensure_ascii=False, separators=(',', ':'))}\n\n"
