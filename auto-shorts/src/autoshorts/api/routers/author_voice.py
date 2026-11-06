"""Авторский слой clip-job: сценарий, голос автора, отчёт об оригинальности (§6.3.6–§6.3.9). Владелец — M5-B.

Контракт — `api/schemas/clipping.py`, логика — `services/clipping.py`. Движок:
`clipping/author/{script,voice,share}.py`, `clipping/edl.py` (раскладка без рендера для оценки),
`clipping/originality.py`.

* Сценарий: `GET/PUT /jobs/{job_id}/author-script`, новый черновик — `POST …/author-script/draft`
  (задача стадии `author_script`: LLM через бюджет, без ключа — шаблон с пометкой).
* Голос: запись оператора multipart `audio` + `segment` (≤ 20 МБ, webm/ogg/wav, тип по сигнатуре),
  файл — artifact `author_voice` (idx: cold_open 0, insert_0 1, insert_1 2, outro 3), тишина по краям
  и тайминги (GigaAM) — в задаче. UploadGuard: для этого пути нужен лимит 21 МБ (см. api/app.py).
* Оригинальность: оценка до рендера (`/originality/estimate`) и отчёт финального рендера (`/originality`).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Request, Response, UploadFile

from autoshorts.api.deps import OperatorDep, ViewerDep, audit_actor
from autoshorts.api.schemas.clipping import (
    AuthorScriptDraftRequest,
    AuthorScriptPut,
    AuthorVoiceAccepted,
    JobAuthorScript,
    JobAuthorVoice,
    OriginalityEstimateRequest,
    OriginalityReportOut,
)
from autoshorts.api.schemas.common import TaskAccepted, binary_responses
from autoshorts.domain.enums import AuthorVoiceSegment

router = APIRouter(tags=["clipping"])


def _connection():
    from autoshorts.db.pool import connection

    return connection()


def _caller(request: Request, principal: Any) -> Any:
    from autoshorts.services.jobs import Caller

    return Caller.from_principal(principal, audit_actor(request, principal))


# ---------------------------------------------------------------- сценарий


@router.get(
    "/jobs/{job_id}/author-script",
    name="author_script_get",
    response_model=JobAuthorScript,
    summary="Авторский текст: черновик LLM / шаблон / текст оператора",
)
def get_author_script(job_id: int, principal: ViewerDep):
    from autoshorts.services import clipping as svc

    with _connection() as conn:
        return svc.author_script_view(conn, job_id)


@router.put(
    "/jobs/{job_id}/author-script",
    name="author_script_put",
    response_model=JobAuthorScript,
    summary="Текст оператора (409 version_conflict | job_state_conflict)",
)
def put_author_script(job_id: int, body: AuthorScriptPut, principal: OperatorDep, request: Request):
    from autoshorts.services import clipping as svc

    with _connection() as conn:
        return svc.put_author_script(conn, job_id, body, _caller(request, principal))


@router.post(
    "/jobs/{job_id}/author-script/draft",
    name="author_script_draft",
    status_code=202,
    response_model=TaskAccepted,
    summary="Новый черновик авторского текста (LLM платно; без ключа — шаблон)",
)
def draft_author_script(
    job_id: int, body: AuthorScriptDraftRequest, principal: OperatorDep, request: Request
):
    from autoshorts.services import clipping as svc

    with _connection() as conn:
        return {"task_id": svc.request_author_draft(conn, job_id, body, _caller(request, principal))}


# ---------------------------------------------------------------- голос


@router.get(
    "/jobs/{job_id}/author-voice",
    name="author_voice_get",
    response_model=JobAuthorVoice,
    summary="Голос автора по репликам",
)
def get_author_voice(job_id: int, principal: ViewerDep):
    from autoshorts.services import clipping as svc

    with _connection() as conn:
        return svc.author_voice_view(conn, job_id)


@router.post(
    "/jobs/{job_id}/author-voice",
    name="author_voice_upload",
    status_code=202,
    response_model=AuthorVoiceAccepted,
    summary="Запись голоса оператора на реплику (multipart ≤ 20 МБ; 413, 415)",
)
def upload_author_voice(
    job_id: int,
    audio: Annotated[UploadFile, File(description="webm/ogg (opus) из MediaRecorder или wav")],
    segment: Annotated[AuthorVoiceSegment, Form(description="cold_open | insert_0 | insert_1 | outro")],
    principal: OperatorDep,
    request: Request,
):
    from autoshorts.services import clipping as svc

    try:
        return svc.upload_author_voice(
            job_id, segment.value, audio.file, audio.content_type, _caller(request, principal)
        )
    finally:
        audio.file.close()


@router.get(
    "/jobs/{job_id}/author-voice/{segment}/audio",
    name="author_voice_audio",
    response_class=Response,
    responses=binary_responses("audio/webm", "audio/ogg", "audio/wav", "audio/mpeg", description="Реплика"),
    summary="Прослушать реплику (запись или TTS)",
)
def get_author_voice_audio(job_id: int, segment: AuthorVoiceSegment, principal: ViewerDep):
    from autoshorts.api.media import media_response
    from autoshorts.services import clipping as svc

    with _connection() as conn:
        f = svc.author_voice_file(conn, job_id, segment.value)
    return media_response(f.relpath, filename=f.filename, media_type=f.media_type)


@router.delete(
    "/jobs/{job_id}/author-voice/{segment}",
    name="author_voice_delete",
    status_code=204,
    response_class=Response,
    summary="Удалить запись реплики (озвучится TTS)",
)
def delete_author_voice(job_id: int, segment: AuthorVoiceSegment, principal: OperatorDep, request: Request):
    from autoshorts.services import clipping as svc

    with _connection() as conn:
        svc.delete_author_voice(conn, job_id, segment.value, _caller(request, principal))
    return Response(status_code=204)


# ---------------------------------------------------------------- оригинальность


@router.post(
    "/jobs/{job_id}/originality/estimate",
    name="originality_estimate",
    response_model=OriginalityReportOut,
    summary="Оценка оригинальности до рендера (дёшево, без ffmpeg)",
)
def estimate_originality(
    job_id: int, body: OriginalityEstimateRequest, principal: OperatorDep, request: Request
):
    from autoshorts.services import clipping as svc

    with _connection() as conn:
        return svc.estimate_job_originality(conn, job_id, body)


@router.get(
    "/jobs/{job_id}/originality",
    name="originality_get",
    response_model=OriginalityReportOut,
    summary="Отчёт об оригинальности финального рендера (404 originality_not_found)",
)
def get_originality(job_id: int, principal: ViewerDep):
    from autoshorts.services import clipping as svc

    with _connection() as conn:
        return svc.get_originality(conn, job_id)
