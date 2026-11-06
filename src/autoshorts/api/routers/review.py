"""Приёмка: очередь, контекст экрана, решение, отмена решения. Владелец — M2-C.

Логика — `services/review.py` (те же функции вызывает Telegram-бот). Права: чтение — viewer,
решения — operator (`override_qc` — только admin, проверяет сервис).
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from autoshorts.api.deps import OperatorDep, ViewerDep, audit_actor
from autoshorts.api.schemas.common import SLUG_PATTERN
from autoshorts.api.schemas.jobs import Job
from autoshorts.api.schemas.review import ReviewContext, ReviewQueue, ReviewRequest
from autoshorts.services import jobs as jobs_service
from autoshorts.services import review as svc

router = APIRouter(tags=["review"])


def _connection():
    from autoshorts.db.pool import connection

    return connection()


@router.get("/review-queue", name="queue", response_model=ReviewQueue, summary="Очередь приёмки")
def review_queue(principal: ViewerDep, channel_id: str | None = Query(default=None, pattern=SLUG_PATTERN)):
    with _connection() as conn:
        return {"items": svc.review_queue(conn, channel_id=channel_id)}


@router.get(
    "/jobs/{job_id}/review-context",
    name="context",
    response_model=ReviewContext,
    summary="Всё для экрана приёмки",
)
def review_context(job_id: int, principal: ViewerDep):
    with _connection() as conn:
        return svc.review_context(conn, job_id)


@router.post(
    "/jobs/{job_id}/review",
    name="decide",
    response_model=Job,
    summary="Решение по ролику (409 job_state_conflict, если уже решено)",
)
def review_decide(job_id: int, body: ReviewRequest, principal: OperatorDep, request: Request):
    caller = jobs_service.Caller.from_principal(principal, audit_actor(request, principal))
    with _connection() as conn:
        job = svc.decide(conn, job_id, svc.DecisionInput.from_request(body), caller)
        return Job.model_validate(jobs_service.job_view(conn, job)).model_dump(mode="json")


@router.post(
    "/jobs/{job_id}/review/undo",
    name="undo",
    response_model=Job,
    summary="Отмена решения ≤ 10 мин (409, если пакет скачан или есть публикация)",
)
def review_undo(job_id: int, principal: OperatorDep, request: Request):
    caller = jobs_service.Caller.from_principal(principal, audit_actor(request, principal))
    with _connection() as conn:
        job = svc.undo(conn, job_id, caller)
        return Job.model_validate(jobs_service.job_view(conn, job)).model_dump(mode="json")
