"""Ролики: список, создание, управление, медиа, сцены, таймлайн. Владелец — M2-C.

Пакет ролика (`/jobs/{job_id}/package`) — M3-A, голос оператора (`/jobs/{job_id}/author-voice`) — M5-B.

Права (§5.1): чтение — viewer и выше, изменения — operator и выше (CSRF проверяет зависимость).
Логика — `services/jobs.py`; роутер открывает транзакцию, собирает `Caller` и отдаёт схемы.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from autoshorts.api.deps import OperatorDep, ViewerDep, audit_actor
from autoshorts.api.schemas.common import (
    SLUG_PATTERN,
    DateFromQuery,
    DateToQuery,
    LimitQuery,
    OffsetQuery,
    SortQuery,
    TaskAccepted,
    binary_responses,
)
from autoshorts.api.schemas.jobs import (
    Job,
    JobAdapt,
    JobBatchCreate,
    JobClone,
    JobCreate,
    JobHistory,
    JobList,
    JobPage,
    JobPatch,
    JobRetry,
    JobTaskList,
    SceneList,
    SceneRegenerate,
    SimilarJobList,
    StageCountList,
)
from autoshorts.core.errors import Conflict
from autoshorts.domain.enums import JobKind, JobStage, JobStatus, MediaKind
from autoshorts.services import jobs as svc

router = APIRouter(tags=["jobs"])

IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", max_length=128, description="Повтор с тем же ключом вернёт тот же ответ"),
]
SlugQuery = Annotated[str | None, Query(pattern=SLUG_PATTERN)]


def _caller(request: Request, principal: Any) -> svc.Caller:
    return svc.Caller.from_principal(principal, audit_actor(request, principal))


def _connection():
    from autoshorts.db.pool import connection

    return connection()


def _job_out(conn: Any, job: dict[str, Any], *, include_words: bool = False) -> dict[str, Any]:
    return Job.model_validate(svc.job_view(conn, job, include_words=include_words)).model_dump(mode="json")


def _idempotent(
    conn: Any, principal: Any, scope: str, key: str | None, *, status_code: int = 201
) -> JSONResponse | None:
    """Повтор с тем же `Idempotency-Key` — сохранённый ответ с тем же статусом."""
    if not key:
        return None
    from autoshorts.repos import jobs as jobs_repo

    stored = jobs_repo.idempotency_claim(conn, int(principal.user_id), f"{scope}:{key}")
    if stored is None:
        return None
    if not stored["response"]:
        raise Conflict("Запрос с этим ключом ещё выполняется", code="idempotency_in_progress")
    return JSONResponse(stored["response"], status_code=status_code)


def _remember(conn: Any, principal: Any, scope: str, key: str | None, response: dict[str, Any]) -> None:
    if key:
        from autoshorts.repos import jobs as jobs_repo

        jobs_repo.idempotency_store(conn, int(principal.user_id), f"{scope}:{key}", response)


@router.get("/jobs", name="list", response_model=JobPage, summary="Ролики")
def list_jobs(
    principal: ViewerDep,
    kind: JobKind | None = None,
    status: list[JobStatus] | None = Query(default=None),
    stage: list[JobStage] | None = Query(default=None),
    channel_id: SlugQuery = None,
    niche_id: SlugQuery = None,
    product_id: SlugQuery = None,
    campaign_id: int | None = None,
    banner_variant_id: int | None = None,
    source_id: int | None = None,
    q: str | None = Query(default=None, max_length=200, description="Поиск по video_uid, теме, коду"),
    date_from: DateFromQuery = None,
    date_to: DateToQuery = None,
    sort: SortQuery = "-created_at",
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
):
    with _connection() as conn:
        return svc.list_jobs(
            conn,
            kind=kind.value if kind else None,
            statuses=[s.value for s in status] if status else None,
            stages=[s.value for s in stage] if stage else None,
            channel_id=channel_id,
            niche_id=niche_id,
            product_id=product_id,
            campaign_id=campaign_id,
            banner_variant_id=banner_variant_id,
            source_id=source_id,
            q=q,
            date_from=date_from,
            date_to=date_to,
            sort=sort,
            limit=limit,
            offset=offset,
        )


@router.get("/jobs/stage-counts", name="stage_counts", response_model=StageCountList, summary="Лента стадий")
def stage_counts(principal: ViewerDep):
    with _connection() as conn:
        return {"items": svc.stage_counts(conn)}


@router.post(
    "/jobs",
    name="create",
    status_code=201,
    response_model=Job,
    summary="Создать ролик generate или clip (423 budget_paused, 409)",
)
def create_job(
    body: JobCreate, principal: OperatorDep, request: Request, idempotency_key: IdempotencyKey = None
):
    caller = _caller(request, principal)
    with _connection() as conn:
        replay = _idempotent(conn, principal, "jobs", idempotency_key)
        if replay is not None:
            return replay
        job = svc.create_job(conn, svc.spec_from_request(body), caller)
        out = _job_out(conn, job)
        _remember(conn, principal, "jobs", idempotency_key, out)
    return out


@router.post("/jobs/batch", name="create_batch", response_model=JobList, summary="Пачка генерации (≤ 20 тем)")
def create_jobs_batch(
    body: JobBatchCreate, principal: OperatorDep, request: Request, idempotency_key: IdempotencyKey = None
):
    caller = _caller(request, principal)
    base = svc.JobSpec(
        kind=JobKind.GENERATE.value,
        channel_id=body.channel_id,
        campaign_id=body.campaign_id,
        banner_variant_id=body.banner_variant_id,
        start=body.start,
    )
    with _connection() as conn:
        replay = _idempotent(conn, principal, "jobs_batch", idempotency_key, status_code=200)
        if replay is not None:
            return replay
        jobs = svc.create_batch(conn, base, body.topics, caller)
        out = {"items": [_job_out(conn, j) for j in jobs]}
        _remember(conn, principal, "jobs_batch", idempotency_key, out)
    return out


@router.get("/jobs/{job_id}", name="get", response_model=Job, summary="Ролик детально")
def get_job(
    job_id: int,
    principal: ViewerDep,
    include: str | None = Query(default=None, pattern=r"^[a-z_]+(,[a-z_]+)*$", description="words,script"),
):
    parts = set((include or "").split(","))
    with _connection() as conn:
        return _job_out(conn, svc.get_job(conn, job_id), include_words="words" in parts)


@router.patch(
    "/jobs/{job_id}", name="update", response_model=Job, summary="Правка до старта (draft|queued|rework)"
)
def update_job(job_id: int, body: JobPatch, principal: OperatorDep, request: Request):
    changes: dict[str, Any] = {}
    for name in body.model_fields_set:
        value = getattr(body, name)
        if name == "author_layer" and value is not None:
            value = value.model_dump(mode="json")
        changes[name] = value
    with _connection() as conn:
        return _job_out(conn, svc.patch_job(conn, job_id, changes, _caller(request, principal)))


@router.post("/jobs/{job_id}/start", name="start", response_model=Job, summary="Запустить")
def start_job(job_id: int, principal: OperatorDep, request: Request):
    with _connection() as conn:
        return _job_out(conn, svc.start_job(conn, job_id, _caller(request, principal)))


@router.post("/jobs/{job_id}/cancel", name="cancel", response_model=Job, summary="Отменить")
def cancel_job(job_id: int, principal: OperatorDep, request: Request):
    with _connection() as conn:
        return _job_out(conn, svc.cancel_job(conn, job_id, _caller(request, principal)))


@router.post("/jobs/{job_id}/retry", name="retry", response_model=Job, summary="Перезапуск со стадии")
def retry_job(job_id: int, body: JobRetry, principal: OperatorDep, request: Request):
    stage = body.from_stage.value if body.from_stage else None
    with _connection() as conn:
        return _job_out(conn, svc.retry_job(conn, job_id, stage, _caller(request, principal)))


@router.post(
    "/jobs/{job_id}/clone", name="clone", status_code=201, response_model=Job, summary="Клон с правками"
)
def clone_job(job_id: int, body: JobClone, principal: OperatorDep, request: Request):
    overrides: dict[str, Any] = {}
    for name in body.overrides.model_fields_set:
        value = getattr(body.overrides, name)
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json", exclude_none=name == "options")
        overrides[name] = value
    overrides.setdefault("start", body.overrides.start)
    with _connection() as conn:
        return _job_out(conn, svc.clone_job(conn, job_id, overrides, _caller(request, principal)))


@router.post(
    "/jobs/{job_id}/adapt",
    name="adapt",
    status_code=201,
    response_model=Job,
    summary="Перерендер под другую площадку (VK), дочерний job",
)
def adapt_job(job_id: int, body: JobAdapt, principal: OperatorDep, request: Request):
    with _connection() as conn:
        return _job_out(conn, svc.adapt_job(conn, job_id, body.channel_id, _caller(request, principal)))


@router.get(
    "/jobs/{job_id}/media/{media_kind}",
    name="media",
    response_class=Response,
    responses=binary_responses(
        "video/mp4", "image/jpeg", "image/png", "audio/mpeg", description="Медиа (Range)"
    ),
    summary="Медиа ролика (Range; ?download=1 — attachment)",
)
def job_media(job_id: int, media_kind: MediaKind, principal: ViewerDep, download: bool = False):
    from autoshorts.api.media import media_response

    with _connection() as conn:
        f = svc.media_file(conn, job_id, media_kind.value)
    return media_response(f.relpath, filename=f.filename, download=download, media_type=f.media_type)


@router.get("/jobs/{job_id}/scenes", name="scenes_list", response_model=SceneList, summary="Сцены")
def list_scenes(job_id: int, principal: ViewerDep):
    with _connection() as conn:
        return {"items": svc.scene_list(conn, svc.get_job(conn, job_id))}


@router.get(
    "/jobs/{job_id}/scenes/{scene_idx}/image",
    name="scene_image",
    response_class=Response,
    responses=binary_responses("image/png", description="Картинка сцены"),
    summary="Картинка сцены",
)
def scene_image(job_id: int, scene_idx: int, principal: ViewerDep):
    from autoshorts.api.media import media_response

    with _connection() as conn:
        f = svc.scene_image_file(conn, job_id, scene_idx)
    return media_response(f.relpath, filename=f.filename, media_type=f.media_type)


@router.post(
    "/jobs/{job_id}/scenes/{scene_idx}/regenerate",
    name="scene_regenerate",
    status_code=202,
    response_model=TaskAccepted,
    summary="Перегенерация сцены (платно; ролик уходит в processing с render)",
)
def regenerate_scene(
    job_id: int, scene_idx: int, body: SceneRegenerate, principal: OperatorDep, request: Request
):
    with _connection() as conn:
        svc.regenerate_scene(conn, job_id, scene_idx, body.prompt, _caller(request, principal))
        task_id = svc.latest_stage_task_id(conn, job_id, JobStage.VISUALS.value)
    return {"task_id": task_id}


@router.get("/jobs/{job_id}/tasks", name="tasks", response_model=JobTaskList, summary="Таймлайн стадий")
def job_tasks(job_id: int, principal: ViewerDep):
    with _connection() as conn:
        return {"items": svc.task_timeline(conn, job_id)}


@router.get("/jobs/{job_id}/history", name="history", response_model=JobHistory, summary="Переходы и решения")
def job_history(job_id: int, principal: ViewerDep):
    with _connection() as conn:
        return {"items": svc.history(conn, job_id)}


@router.get(
    "/jobs/{job_id}/similar", name="similar", response_model=SimilarJobList, summary="Похожие свои ролики"
)
def similar_jobs(job_id: int, principal: ViewerDep):
    with _connection() as conn:
        return {"items": svc.similar_jobs(conn, job_id)}
