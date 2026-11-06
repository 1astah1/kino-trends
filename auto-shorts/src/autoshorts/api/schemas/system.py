"""Система: здоровье, статус воркеров и ресурсов, алерты, аудит, обслуживание, SSE
(docs/ARCHITECTURE.md §5.1, §5.2 «Система, мета, события», «обслуживание»)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, RootModel

from autoshorts.api.schemas.budget import Budget
from autoshorts.api.schemas.common import ApiModel, JournalMeta
from autoshorts.api.schemas.tasks import Worker
from autoshorts.domain.enums import ActorKind, AlertSeverity, AlertStatus, JobStage
from autoshorts.domain.events import (
    AlertEvent,
    BudgetEvent,
    JobProgressEvent,
    JobStatusEvent,
    PackagesEvent,
    PingEvent,
    PublicationMetricsEvent,
    ReviewQueueEvent,
    SourceStatusEvent,
    TaskProgressEvent,
    WorkerEvent,
)

# ---------------------------------------------------------------- здоровье


class HealthResponse(ApiModel):
    status: Literal["ok"]


class ReadyCheck(ApiModel):
    ok: bool
    detail: str | None = None


class ReadyChecks(ApiModel):
    db: ReadyCheck
    migrations: ReadyCheck
    media_writable: ReadyCheck
    disk: ReadyCheck


class ReadyResponse(ApiModel):
    """200 — всё ок; 503 с тем же телом и `status=not_ready` (например, `migrations_pending`)."""

    status: Literal["ok", "not_ready"]
    checks: ReadyChecks


# ---------------------------------------------------------------- статус системы


class HeavySlot(ApiModel):
    owner: str | None = Field(description="id воркера, держащего тяжёлый слот")
    task_id: int | None
    stage: JobStage | None
    pct: float | None


class DiskThresholds(ApiModel):
    soft_gb: float
    hard_gb: float
    emergency_gb: float


class DiskStatus(ApiModel):
    free_gb: float
    thresholds: DiskThresholds


class PsiStatus(ApiModel):
    cpu10: float | None
    mem10: float | None


class QueueCounters(ApiModel):
    queued: int
    running: int
    deferred: int


class QueueStatus(ApiModel):
    heavy: QueueCounters
    light: QueueCounters


class Versions(ApiModel):
    app: str
    db_schema: int | None


class SystemStatus(ApiModel):
    workers: list[Worker]
    heavy_slot: HeavySlot
    disk: DiskStatus
    load1: float
    psi: PsiStatus
    mem_available_mb: int
    queue: QueueStatus
    budget: Budget
    versions: Versions


# ---------------------------------------------------------------- алерты


class Alert(ApiModel):
    id: int
    code: str = Field(description="См. AlertCode: disk_low, worker_down, budget_80, …")
    severity: AlertSeverity
    status: AlertStatus
    title: str
    details: dict[str, Any]
    opened_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None
    acked_by: int | None


class AlertList(ApiModel):
    items: list[Alert]


# ---------------------------------------------------------------- аудит


class AuditEntry(ApiModel):
    id: int
    occurred_at: datetime
    actor_kind: ActorKind
    actor_user_id: int | None
    action: str = Field(description="job.review, secret.set, product.update, …")
    entity_type: str
    entity_id: str | None
    summary: str | None
    diff: dict[str, Any] | None = Field(description="Значения секретов вырезаны")
    request_id: str | None


class AuditJournal(JournalMeta):
    items: list[AuditEntry]


# ---------------------------------------------------------------- обслуживание


class CleanupPreviewItem(ApiModel):
    category: str = Field(
        description="work_artifacts, rejected_media, proxies, originals, tus_parts, task_logs, tmp"
    )
    files: int
    bytes: int


class CleanupPreview(ApiModel):
    items: list[CleanupPreviewItem]


# ---------------------------------------------------------------- SSE (§5.1)


class SseJobStatus(ApiModel):
    event: Literal["job_status"]
    data: JobStatusEvent


class SseJobProgress(ApiModel):
    event: Literal["job_progress"]
    data: JobProgressEvent


class SseTaskProgress(ApiModel):
    event: Literal["task_progress"]
    data: TaskProgressEvent


class SseReviewQueue(ApiModel):
    event: Literal["review_queue"]
    data: ReviewQueueEvent


class SsePackages(ApiModel):
    event: Literal["packages"]
    data: PackagesEvent


class SseAlert(ApiModel):
    event: Literal["alert"]
    data: AlertEvent


class SseWorker(ApiModel):
    event: Literal["worker"]
    data: WorkerEvent


class SseBudget(ApiModel):
    event: Literal["budget"]
    data: BudgetEvent


class SseSourceStatus(ApiModel):
    event: Literal["source_status"]
    data: SourceStatusEvent


class SsePublicationMetrics(ApiModel):
    event: Literal["publication_metrics"]
    data: PublicationMetricsEvent


class SsePing(ApiModel):
    event: Literal["ping"]
    data: PingEvent


class SseEvent(
    RootModel[
        Annotated[
            SseJobStatus
            | SseJobProgress
            | SseTaskProgress
            | SseReviewQueue
            | SsePackages
            | SseAlert
            | SseWorker
            | SseBudget
            | SseSourceStatus
            | SsePublicationMetrics
            | SsePing,
            Field(discriminator="event"),
        ]
    ]
):
    """Один кадр `text/event-stream`: `event:` = поле `event`, `data:` = JSON поля `data`."""
