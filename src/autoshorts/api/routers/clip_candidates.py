"""Кандидаты клипов, повторный отбор, clip-job из кандидата (§5.2, §6.3.3–§6.3.5). Владелец — M5-B.

Контракт — `api/schemas/clipping.py`, логика — `services/clipping.py`. Движок:
`clipping/{selection,scoring,snapping,heuristic_select,llm_select}.py` (отбор в задаче `source_select`,
light), правка границ — `snapping.snap_clip_to_words` синхронно. Создание clip-job — через
`services/jobs.create_job` (как `POST /jobs` с `kind=clip`), ответ — `Job`.
Права (§5.1): чтение — viewer, изменения — operator (CSRF проверяет зависимость).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from autoshorts.api.deps import OperatorDep, ViewerDep, audit_actor
from autoshorts.api.schemas.clipping import (
    ClipBatchCreate,
    ClipCandidate,
    ClipCandidateCreate,
    ClipCandidateList,
    ClipCandidatePatch,
    ClipJobOptions,
    OriginalityEstimateRequest,
    OriginalityReportOut,
    SourceSelectRequest,
)
from autoshorts.api.schemas.common import TaskAccepted
from autoshorts.api.schemas.jobs import Job, JobList
from autoshorts.domain.enums import CandidateStatus

router = APIRouter(tags=["clipping"])


def _connection():
    from autoshorts.db.pool import connection

    return connection()


def _caller(request: Request, principal: Any) -> Any:
    from autoshorts.services.jobs import Caller

    return Caller.from_principal(principal, audit_actor(request, principal))


def _job_out(conn: Any, job: dict[str, Any]) -> dict[str, Any]:
    from autoshorts.services import jobs as jobs_service

    return Job.model_validate(jobs_service.job_view(conn, job)).model_dump(mode="json")


@router.get(
    "/sources/{source_id}/candidates",
    name="candidates_list",
    response_model=ClipCandidateList,
    summary="Кандидаты источника (по final_score) и подпись режима отбора",
)
def list_candidates(
    source_id: int,
    principal: ViewerDep,
    status: list[CandidateStatus] | None = Query(default=None),
    include_superseded: bool = Query(default=False, description="Показать кандидатов прошлых прогонов"),
):
    from autoshorts.services import clipping as svc

    with _connection() as conn:
        return svc.list_candidates(
            conn,
            source_id,
            statuses=[s.value for s in status] if status else None,
            include_superseded=include_superseded,
        )


@router.post(
    "/sources/{source_id}/candidates",
    name="candidates_create",
    status_code=201,
    response_model=ClipCandidate,
    summary="Ручной кандидат (по словам или секундам, со снаппингом)",
)
def create_candidate(source_id: int, body: ClipCandidateCreate, principal: OperatorDep, request: Request):
    from autoshorts.services import clipping as svc

    with _connection() as conn:
        return svc.create_candidate(conn, source_id, body, _caller(request, principal))


@router.post(
    "/sources/{source_id}/select",
    name="select",
    status_code=202,
    response_model=TaskAccepted,
    summary="Повторный отбор моментов с параметрами (LLM или эвристика)",
)
def select_again(source_id: int, body: SourceSelectRequest, principal: OperatorDep, request: Request):
    from autoshorts.services import clipping as svc

    with _connection() as conn:
        return {"task_id": svc.request_selection(conn, source_id, body, _caller(request, principal))}


@router.post(
    "/sources/{source_id}/clips",
    name="clips_batch",
    response_model=JobList,
    summary="Пачка clip-job из отрезков и кандидатов (≤ 20)",
)
def create_clips_batch(source_id: int, body: ClipBatchCreate, principal: OperatorDep, request: Request):
    from autoshorts.services import clipping as svc

    with _connection() as conn:
        jobs = svc.create_clips_batch(conn, source_id, body, _caller(request, principal))
        return {"items": [_job_out(conn, j) for j in jobs]}


@router.get(
    "/clip-candidates/{candidate_id}", name="candidate_get", response_model=ClipCandidate, summary="Кандидат"
)
def get_candidate(candidate_id: int, principal: ViewerDep):
    from autoshorts.services import clipping as svc

    with _connection() as conn:
        return svc.get_candidate(conn, candidate_id)


@router.patch(
    "/clip-candidates/{candidate_id}",
    name="candidate_update",
    response_model=ClipCandidate,
    summary="Границы по словам (снаппинг), статус, тексты, флаги, заметки",
)
def update_candidate(candidate_id: int, body: ClipCandidatePatch, principal: OperatorDep, request: Request):
    from autoshorts.services import clipping as svc

    with _connection() as conn:
        return svc.patch_candidate(conn, candidate_id, body, _caller(request, principal))


@router.post(
    "/clip-candidates/{candidate_id}/jobs",
    name="candidate_create_job",
    status_code=201,
    response_model=Job,
    summary="В рендер: clip-job (409 clip_candidate_blocked | clip_candidate_in_use, 423)",
)
def create_job_from_candidate(
    candidate_id: int, body: ClipJobOptions, principal: OperatorDep, request: Request
):
    from autoshorts.services import clipping as svc

    with _connection() as conn:
        job = svc.create_job_from_candidate(conn, candidate_id, body, _caller(request, principal))
        return _job_out(conn, job)


@router.post(
    "/clip-candidates/{candidate_id}/originality-estimate",
    name="candidate_originality_estimate",
    response_model=OriginalityReportOut,
    summary="Оценка оригинальности до рендера (индикатор 70/30 студии)",
)
def estimate_candidate_originality(
    candidate_id: int, body: OriginalityEstimateRequest, principal: OperatorDep, request: Request
):
    from autoshorts.services import clipping as svc

    with _connection() as conn:
        return svc.estimate_candidate_originality(conn, candidate_id, body)
