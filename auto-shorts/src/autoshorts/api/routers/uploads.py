"""tus 1.0.0: возобновляемая загрузка источников и документов прав (§5.2, §6.3.1). Владелец — M5-A.

Контракт — `api/schemas/uploads.py`, логика — `services/uploads_tus.py`. Тело PATCH читается потоком
из `request.stream()` (не в память), чанк ≤ 16 МБ, запись — в `MEDIA_ROOT/uploads/<upload_id>.part`
через `os.pwrite`. Ответы tus-эндпоинтов, в том числе ошибки, несут `Tus-Resumable: 1.0.0`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Path, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from autoshorts.api.deps import OperatorDep
from autoshorts.api.schemas.common import json_error_responses
from autoshorts.api.schemas.uploads import TUS_CONTENT_TYPE, TUS_VERSION, UPLOAD_ID_PATTERN, UploadInfo
from autoshorts.core.errors import AppError, PayloadTooLarge
from autoshorts.services import uploads_tus as tus

router = APIRouter(tags=["uploads"])

UploadIdPath = Annotated[str, Path(pattern=UPLOAD_ID_PATTERN, description="32 hex")]
TusResumable = Annotated[
    str | None, Header(alias="Tus-Resumable", description="Обязателен: 1.0.0 (иначе 412)")
]
UploadLength = Annotated[
    int | None, Header(alias="Upload-Length", ge=0, description="Полный размер файла, байт")
]
UploadMetadata = Annotated[
    str | None,
    Header(
        alias="Upload-Metadata",
        max_length=4096,
        description="`filename <b64>,filetype <b64>,purpose <b64>` (purpose: source | asset | rights_doc)",
    ),
]
UploadOffset = Annotated[int | None, Header(alias="Upload-Offset", ge=0, description="Смещение чанка")]


def _hdr(description: str) -> dict[str, Any]:
    return {"description": description, "schema": {"type": "string"}}


TUS_RESUMABLE_HDR = {"Tus-Resumable": _hdr("1.0.0")}


def _error(exc: AppError) -> JSONResponse:
    """Ошибка в едином формате (§5.1) плюс заголовки tus (клиенту нужен Tus-Resumable)."""
    headers = tus.base_headers()
    if isinstance(exc, tus.TusVersionUnsupported):
        headers["Tus-Version"] = TUS_VERSION
    return JSONResponse(status_code=exc.status, content=exc.to_payload(), headers=headers)


def _content_length(request: Request) -> int | None:
    raw = request.headers.get("content-length")
    return int(raw) if raw and raw.strip().isdigit() else None


def _no_body(status: int, description: str, headers: dict[str, Any]) -> dict[int | str, dict[str, Any]]:
    return {status: {"description": description, "headers": headers}, **json_error_responses()}


@router.options(
    "/uploads/tus",
    name="tus_options",
    status_code=204,
    response_class=Response,
    responses=_no_body(
        204,
        "Возможности сервера",
        {
            "Tus-Version": _hdr("1.0.0"),
            "Tus-Extension": _hdr("creation,termination,expiration"),
            "Tus-Max-Size": _hdr("upload_max_gb в байтах"),
            **TUS_RESUMABLE_HDR,
        },
    ),
    summary="tus: возможности",
)
def tus_options(principal: OperatorDep):
    return Response(status_code=204, headers=tus.options_headers())


@router.post(
    "/uploads/tus",
    name="tus_create",
    status_code=201,
    response_class=Response,
    responses=_no_body(
        201,
        "Сессия создана",
        {
            "Location": _hdr("/api/v1/uploads/tus/<upload_id>"),
            "Upload-Expires": _hdr("RFC 7231, 24 ч"),
            **TUS_RESUMABLE_HDR,
        },
    ),
    summary="tus: создать загрузку (413 больше upload_max_gb, 507 нет места)",
)
def tus_create(
    principal: OperatorDep,
    request: Request,
    tus_resumable: TusResumable = None,
    upload_length: UploadLength = None,
    upload_metadata: UploadMetadata = None,
):
    try:
        tus.check_resumable(tus_resumable)
        row = tus.create_upload(principal=principal, length=upload_length, metadata_header=upload_metadata)
    except AppError as exc:
        return _error(exc)
    return Response(status_code=201, headers=tus.create_headers(row))


@router.head(
    "/uploads/tus/{upload_id}",
    name="tus_head",
    status_code=200,
    response_class=Response,
    responses=_no_body(
        200,
        "Смещение",
        {
            "Upload-Offset": _hdr("Получено байт"),
            "Upload-Length": _hdr("Полный размер"),
            "Cache-Control": _hdr("no-store"),
            **TUS_RESUMABLE_HDR,
        },
    ),
    summary="tus: сколько уже получено (404, 410 upload_expired)",
)
def tus_head(upload_id: UploadIdPath, principal: OperatorDep, tus_resumable: TusResumable = None):
    try:
        tus.check_resumable(tus_resumable)
        headers = tus.head_upload(upload_id, principal)
    except AppError as exc:
        return _error(exc)
    return Response(status_code=200, headers=headers)


@router.patch(
    "/uploads/tus/{upload_id}",
    name="tus_patch",
    status_code=204,
    response_class=Response,
    responses=_no_body(204, "Чанк принят", {"Upload-Offset": _hdr("Новое смещение"), **TUS_RESUMABLE_HDR}),
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {TUS_CONTENT_TYPE: {"schema": {"type": "string", "format": "binary"}}},
        }
    },
    summary="tus: чанк ≤ 16 МБ (409 upload_offset_mismatch, 410, 413, 415)",
)
async def tus_patch(
    upload_id: UploadIdPath,
    principal: OperatorDep,
    request: Request,
    tus_resumable: TusResumable = None,
    upload_offset: UploadOffset = None,
):
    try:
        tus.check_resumable(tus_resumable)
        plan = await run_in_threadpool(
            tus.begin_patch,
            upload_id,
            principal,
            offset=upload_offset,
            content_type=request.headers.get("content-type"),
            content_length=_content_length(request),
        )
        if isinstance(plan, dict):  # загрузка уже готова, смещение верное — нечего принимать
            return Response(status_code=204, headers=tus.patch_headers(plan))
        outcome, row = await tus.receive_chunk(plan, request.stream())
        if outcome.too_large:
            raise PayloadTooLarge(
                "Чанк больше 16 МБ или выходит за Upload-Length",
                details={"limit_bytes": plan.max_bytes, "offset": int(row["received_bytes"])},
            )
    except AppError as exc:
        return _error(exc)
    return Response(status_code=204, headers=tus.patch_headers(row))


@router.delete(
    "/uploads/tus/{upload_id}",
    name="tus_terminate",
    status_code=204,
    response_class=Response,
    responses=_no_body(204, "Загрузка прервана, файл удалён", TUS_RESUMABLE_HDR),
    summary="tus: прервать загрузку",
)
def tus_terminate(upload_id: UploadIdPath, principal: OperatorDep, tus_resumable: TusResumable = None):
    try:
        tus.check_resumable(tus_resumable)
        tus.terminate_upload(upload_id, principal)
    except AppError as exc:
        return _error(exc)
    return Response(status_code=204, headers=tus.base_headers())


@router.get("/uploads/{upload_id}", name="get", response_model=UploadInfo, summary="Состояние загрузки")
def get_upload(upload_id: UploadIdPath, principal: OperatorDep):
    return tus.upload_info(upload_id, principal)
