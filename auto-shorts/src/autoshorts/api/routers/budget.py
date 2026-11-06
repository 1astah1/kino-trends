"""Бюджет дня (чтение viewer+, изменения admin) и журнал вызовов провайдеров (viewer+).
Владелец — M1-B. Логика — `services/budget.py` (§6.1).
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from autoshorts.api.deps import AdminDep, ViewerDep, audit_actor
from autoshorts.api.pagination import journal
from autoshorts.api.schemas.budget import Budget, BudgetPatch, BudgetPause, GenerationEventJournal
from autoshorts.api.schemas.common import BeforeIdQuery, DateFromQuery, DateToQuery, LimitQuery
from autoshorts.services import budget as budget_service

router = APIRouter(tags=["budget"])


@router.get("/budget", name="get", response_model=Budget, summary="Бюджет текущего бизнес-дня")
def get_budget(principal: ViewerDep):
    return budget_service.get_budget()


@router.patch("/budget", name="update", response_model=Budget, summary="Лимиты (admin)")
def update_budget(body: BudgetPatch, principal: AdminDep, request: Request):
    return budget_service.update_budget(
        daily_limit_usd=body.daily_limit_usd,
        alert_threshold_pct=body.alert_threshold_pct,
        actor=audit_actor(request, principal),
    )


@router.post("/budget/pause", name="pause", response_model=Budget, summary="Ручная пауза платных шагов")
def pause_budget(body: BudgetPause, principal: AdminDep, request: Request):
    return budget_service.pause(body.reason, audit_actor(request, principal))


@router.post("/budget/resume", name="resume", response_model=Budget, summary="Снять паузу")
def resume_budget(principal: AdminDep, request: Request):
    return budget_service.resume(audit_actor(request, principal))


@router.get(
    "/generation-events",
    name="generation_events",
    response_model=GenerationEventJournal,
    summary="Журнал вызовов провайдеров",
)
def generation_events(
    principal: ViewerDep,
    job_id: int | None = None,
    source_id: int | None = None,
    provider: str | None = None,
    step_name: str | None = None,
    date_from: DateFromQuery = None,
    date_to: DateToQuery = None,
    before_id: BeforeIdQuery = None,
    limit: LimitQuery = 50,
):
    rows = budget_service.list_generation_events(
        job_id=job_id,
        source_id=source_id,
        provider=provider,
        step_name=step_name,
        date_from=date_from,
        date_to=date_to,
        before_id=before_id,
        limit=limit,
    )
    return journal(rows, limit=limit)
