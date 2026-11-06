"""Бюджет дня и журнал вызовов провайдеров (docs/ARCHITECTURE.md §5.2, §6.1 «Бюджет»).

M4-0 (аддитивно): постоянные резервы бюджета — таблица `budget_reservations` и представление
`v_budget_orphan_reservations` (миграция 0016). Их значения — `BudgetReservationStatus`,
`BudgetReleaseReason`, `BudgetOrphanReason`; правила закрытия резерва — в комментарии миграции.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import Field

from autoshorts.api.schemas.common import ApiModel, JournalMeta, RequestModel

BUDGET_TASKLESS_RESERVATION_TTL_SEC = 3600
"""Резерв без задачи (синхронный вызов API) старше часа считается сиротой (`no_task_stale`)."""


class BudgetReservationStatus(StrEnum):
    """`budget_reservations.status` (0016)."""

    OPEN = "open"
    SETTLED = "settled"  # вызов состоялся: резерв → расход (actual_usd)
    RELEASED = "released"  # снят без расхода (release_reason)


class BudgetReleaseReason(StrEnum):
    """`budget_reservations.release_reason` (0016)."""

    CALL_FAILED = "call_failed"  # платный вызов не состоялся и денег не стоил (gate.release)
    TASK_ENDED = "task_ended"  # гейт задачи снял незакрытое после обработчика (release_open)
    WORKER_LOST = "worker_lost"  # reaper: хозяин резерва пропал (v_budget_orphan_reservations)
    EXPIRED = "expired"  # уборка: открытый резерв прошлых дней
    MANUAL = "manual"  # администратор


class BudgetOrphanReason(StrEnum):
    """`v_budget_orphan_reservations.orphan_reason` (0016)."""

    TASK_NOT_RUNNING = "task_not_running"
    TASK_REPICKED = "task_repicked"
    NO_TASK_STALE = "no_task_stale"


class Budget(ApiModel):
    day: date = Field(description="Бизнес-день в business_timezone")
    daily_limit_usd: float
    spent_today_usd: float
    reserved_usd: float
    paused: bool
    pause_reason: str | None
    alert_threshold_pct: int


class BudgetPatch(RequestModel):
    daily_limit_usd: float | None = Field(default=None, ge=0)
    alert_threshold_pct: int | None = Field(default=None, ge=1, le=100)


class BudgetPause(RequestModel):
    reason: str = Field(min_length=1, max_length=500)


class GenerationEvent(ApiModel):
    id: int
    job_id: int | None
    task_id: int | None
    source_id: int | None
    trend_brief_id: int | None
    step_name: str
    provider: str
    model: str | None
    units: float | None
    unit_price_usd: float | None
    cost_usd: float
    latency_ms: int | None
    http_status: int | None
    status: str = Field(description="ok | error")
    error: str | None
    cpu_ms: int | None
    created_at: datetime


class GenerationEventJournal(JournalMeta):
    items: list[GenerationEvent]
