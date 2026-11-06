"""Источники нарезки: приём с правами, карточка, права, медиа студии (§5.2, §6.3.1). Владелец — M5-A.

Контракт — `api/schemas/{sources,transcripts}.py`, логика — `services/sources.py`, задачи конвейера —
`tasks/sources.py`. Движок: `clipping/source_ingest.py` (`validate_rights`, `prepare_source`,
`transcribe_prepared`), `pipeline/{proxy,peaks,storyboard}.py`, `providers/asr/*` — тяжёлый ASR
в дочернем процессе, как в `scripts/clip_local.py`.

Медиа отдаются `api.media.media_response` (Range в dev, X-Accel-Redirect в prod). Транскрипт
JSON отдаётся без повторной проверки pydantic: слов в часе источника — десятки тысяч.

Кандидаты и повторный отбор по источнику (`/sources/{source_id}/candidates`, `/select`, `/clips`)
— `api/routers/clip_candidates.py` (M5-B, тег `clipping`).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from autoshorts.api.deps import OperatorDep, ViewerDep, audit_actor
from autoshorts.api.media import media_response
from autoshorts.api.schemas.common import (
    SLUG_PATTERN,
    LimitQuery,
    OffsetQuery,
    SortQuery,
    TaskAccepted,
    binary_responses,
)
from autoshorts.api.schemas.sources import (
    Source,
    SourceFromUpload,
    SourceFromUrl,
    SourcePage,
    SourcePatch,
    SourcePeaks,
    SourceReprocess,
    SourceRights,
    SourceRightsIn,
    SourceScenes,
    SourceStoryboard,
)
from autoshorts.api.schemas.transcripts import SourceTranscript, TranscriptFormat
from autoshorts.domain.enums import SourceOrigin, SourceStatus
from autoshorts.services import sources as svc

router = APIRouter(tags=["sources"])

SlugQuery = Annotated[str | None, Query(pattern=SLUG_PATTERN)]


@router.post(
    "/sources/from-upload",
    name="create_from_upload",
    status_code=202,
    response_model=Source,
    summary="Источник из tus-загрузки (права обязательны; 409 upload_not_complete)",
)
def create_from_upload(body: SourceFromUpload, principal: OperatorDep, request: Request):
    return svc.create_from_upload(body, principal, audit_actor(request, principal))


@router.post(
    "/sources/from-url",
    name="create_from_url",
    status_code=202,
    response_model=Source,
    summary="Источник по ссылке: своё или лицензированное (права обязательны)",
)
def create_from_url(body: SourceFromUrl, principal: OperatorDep, request: Request):
    return svc.create_from_url(body, principal, audit_actor(request, principal))


@router.get("/sources", name="list", response_model=SourcePage, summary="Источники")
def list_sources(
    principal: ViewerDep,
    status: list[SourceStatus] | None = Query(default=None),
    origin: SourceOrigin | None = None,
    niche_id: SlugQuery = None,
    q: str | None = Query(default=None, max_length=200, description="Поиск по названию"),
    sort: SortQuery = "-created_at",
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
):
    return svc.list_sources(
        statuses=[str(s) for s in status] if status else None,
        origin=str(origin) if origin else None,
        niche_id=niche_id,
        q=q,
        sort=sort,
        limit=limit,
        offset=offset,
    )


@router.get("/sources/{source_id}", name="get", response_model=Source, summary="Источник и прогресс")
def get_source(source_id: int, principal: ViewerDep):
    return svc.get_source(source_id)


@router.patch("/sources/{source_id}", name="update", response_model=Source, summary="Правка источника")
def update_source(source_id: int, body: SourcePatch, principal: OperatorDep, request: Request):
    return svc.update_source(source_id, body.model_dump(exclude_unset=True), audit_actor(request, principal))


@router.delete(
    "/sources/{source_id}",
    name="delete",
    status_code=204,
    response_class=Response,
    summary="Удалить источник (?purge_files=true — и файлы)",
)
def delete_source(source_id: int, principal: OperatorDep, request: Request, purge_files: bool = False):
    svc.delete_source(source_id, purge_files=purge_files, actor=audit_actor(request, principal))
    return Response(status_code=204)


@router.get(
    "/sources/{source_id}/rights", name="rights_get", response_model=SourceRights, summary="Права на источник"
)
def get_rights(source_id: int, principal: ViewerDep):
    return svc.get_rights(source_id)


@router.put(
    "/sources/{source_id}/rights",
    name="rights_put",
    response_model=SourceRights,
    summary="Заменить запись прав (аудит; CC BY-SA/NC/ND — 422)",
)
def put_rights(source_id: int, body: SourceRightsIn, principal: OperatorDep, request: Request):
    return svc.put_rights(source_id, body, principal, audit_actor(request, principal))


@router.post(
    "/sources/{source_id}/reprocess",
    name="reprocess",
    status_code=202,
    response_model=TaskAccepted,
    summary="Перезапустить шаги: prepare, transcribe, select, scenes",
)
def reprocess_source(source_id: int, body: SourceReprocess, principal: OperatorDep, request: Request):
    task_id = svc.reprocess(
        source_id, [str(s) for s in body.steps], body.asr_provider, audit_actor(request, principal)
    )
    return {"task_id": task_id}


@router.get(
    "/sources/{source_id}/transcript",
    name="transcript",
    response_model=SourceTranscript,
    responses={200: {"content": {"text/plain": {"schema": {"type": "string"}}}}},
    summary="Транскрипт: json (слова и предложения) или srt/vtt",
)
def get_transcript(
    source_id: int,
    principal: ViewerDep,
    format: TranscriptFormat = TranscriptFormat.JSON,
):
    if format == TranscriptFormat.JSON:
        return JSONResponse(svc.transcript_json(source_id))
    return PlainTextResponse(
        svc.transcript_text(source_id, str(format)), media_type="text/plain; charset=utf-8"
    )


@router.get(
    "/sources/{source_id}/media/proxy",
    name="media_proxy",
    response_class=Response,
    responses=binary_responses("video/mp4", description="Прокси 540p, GOP 1 с (Range)"),
    summary="Прокси 540p для студии",
)
def media_proxy(source_id: int, principal: ViewerDep):
    rel, mime, name = svc.media_file(source_id, "proxy")
    return media_response(rel, filename=name, media_type=mime)


@router.get(
    "/sources/{source_id}/media/audio",
    name="media_audio",
    response_class=Response,
    responses=binary_responses("audio/mp4", description="Звук источника m4a (Range)"),
    summary="Звук для прослушивания кандидата",
)
def media_audio(source_id: int, principal: ViewerDep):
    rel, mime, name = svc.media_file(source_id, "audio")
    return media_response(rel, filename=name, media_type=mime)


@router.get(
    "/sources/{source_id}/peaks",
    name="peaks",
    response_model=SourcePeaks,
    summary="Волна (формат wavesurfer; файл отдаётся как есть)",
)
def get_peaks(source_id: int, principal: ViewerDep):
    rel, mime, name = svc.media_file(source_id, "peaks")
    return media_response(rel, filename=name, media_type=mime)


@router.get(
    "/sources/{source_id}/storyboard",
    name="storyboard",
    response_model=SourceStoryboard,
    summary="Раскадровка: сетка и листы",
)
def get_storyboard(source_id: int, principal: ViewerDep):
    return svc.storyboard(source_id)


@router.get(
    "/sources/{source_id}/storyboard/{sheet_idx}",
    name="storyboard_sheet",
    response_class=Response,
    responses=binary_responses("image/jpeg", description="Лист спрайта"),
    summary="Лист раскадровки",
)
def get_storyboard_sheet(source_id: int, sheet_idx: int, principal: ViewerDep):
    rel = svc.storyboard_sheet(source_id, sheet_idx)
    return media_response(
        rel, filename=f"source_{source_id}_storyboard_{sheet_idx}.jpg", media_type="image/jpeg"
    )


@router.get(
    "/sources/{source_id}/scenes",
    name="scenes",
    response_model=SourceScenes,
    summary="Склейки в отрезке (что посчитано)",
)
def get_scenes(
    source_id: int,
    principal: ViewerDep,
    start_sec: float | None = Query(default=None, ge=0),
    end_sec: float | None = Query(default=None, gt=0),
):
    return svc.scenes(source_id, start_sec, end_sec)
