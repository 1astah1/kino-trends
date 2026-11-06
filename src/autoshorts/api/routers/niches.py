"""Ниши (M2-A): версии конфига, валидация, YAML.

Инструменты ниши (`/test-script`, `/voice-sample`, `/text/check-forbidden`) — M6-B (`niche_tools.py`).
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Path, Request, UploadFile

from autoshorts.api.deps import OperatorDep, ViewerDep, audit_actor
from autoshorts.api.routers import YamlResponse
from autoshorts.api.schemas.common import SLUG_PATTERN, LimitQuery, OffsetQuery, text_responses
from autoshorts.api.schemas.niches import (
    Niche,
    NicheCreate,
    NichePage,
    NichePatch,
    NicheValidateRequest,
    NicheValidateResponse,
    NicheVersionConfig,
    NicheVersionList,
    NicheVersionPut,
)
from autoshorts.domain.enums import NicheStatus
from autoshorts.services import niches as niches_service
from autoshorts.services.products import field_error

router = APIRouter(tags=["niches"])

NicheIdPath = Annotated[str, Path(pattern=SLUG_PATTERN)]
MAX_YAML_BYTES = 256 * 1024


def _read_yaml(file: UploadFile) -> str:
    raw = file.file.read(MAX_YAML_BYTES + 1)
    if len(raw) > MAX_YAML_BYTES:
        raise field_error("file", "YAML ниши больше 256 КБ", "too_large")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise field_error("file", "YAML должен быть в UTF-8", "encoding") from None


@router.get("/niches", name="list", response_model=NichePage, summary="Ниши")
def list_niches(
    principal: ViewerDep, status: NicheStatus | None = None, limit: LimitQuery = 50, offset: OffsetQuery = 0
):
    return niches_service.list_niches(status=status, limit=limit, offset=offset)


@router.post(
    "/niches", name="create", status_code=201, response_model=Niche, summary="Создать нишу или клонировать"
)
def create_niche(body: NicheCreate, principal: OperatorDep, request: Request):
    return niches_service.create_niche(
        niche_id=body.id,
        label=body.label,
        config=body.config,
        clone_from=body.clone_from,
        actor=audit_actor(request, principal),
    )


@router.post(
    "/niches/import-yaml",
    name="import_yaml",
    status_code=201,
    response_model=Niche,
    summary="Импорт YAML ниши",
)
def import_niche_yaml(
    file: Annotated[UploadFile, File()],
    principal: OperatorDep,
    request: Request,
    mode: Annotated[Literal["create", "replace"], Form()] = "create",
):
    return niches_service.import_yaml(_read_yaml(file), mode=mode, actor=audit_actor(request, principal))


@router.get("/niches/{niche_id}", name="get", response_model=Niche, summary="Ниша с текущим конфигом")
def get_niche(niche_id: NicheIdPath, principal: ViewerDep):
    return niches_service.get_niche(niche_id)


@router.put(
    "/niches/{niche_id}",
    name="put_version",
    response_model=Niche,
    summary="Новая версия конфига (409 version_conflict, 422 — ошибки по путям)",
)
def put_niche_version(niche_id: NicheIdPath, body: NicheVersionPut, principal: OperatorDep, request: Request):
    return niches_service.put_version(
        niche_id, body.config, body.expected_version, audit_actor(request, principal)
    )


@router.patch("/niches/{niche_id}", name="update", response_model=Niche, summary="Метаданные ниши")
def update_niche(niche_id: NicheIdPath, body: NichePatch, principal: OperatorDep, request: Request):
    changes = {name: getattr(body, name) for name in body.model_fields_set}
    return niches_service.update_meta(niche_id, changes, audit_actor(request, principal))


@router.get(
    "/niches/{niche_id}/versions",
    name="versions_list",
    response_model=NicheVersionList,
    summary="История версий",
)
def list_niche_versions(niche_id: NicheIdPath, principal: ViewerDep):
    return niches_service.list_versions(niche_id)


@router.get(
    "/niches/{niche_id}/versions/{version}",
    name="versions_get",
    response_model=NicheVersionConfig,
    summary="Конфиг версии",
)
def get_niche_version(niche_id: NicheIdPath, version: int, principal: ViewerDep):
    return niches_service.get_version(niche_id, version)


@router.post(
    "/niches/{niche_id}/validate",
    name="validate",
    response_model=NicheValidateResponse,
    summary="Проверка конфига без сохранения",
)
def validate_niche(niche_id: NicheIdPath, body: NicheValidateRequest, principal: OperatorDep):
    return niches_service.validate_niche_config(niche_id, body.config)


@router.get(
    "/niches/{niche_id}/export-yaml",
    name="export_yaml",
    response_class=YamlResponse,
    responses=text_responses("text/yaml", "YAML ниши"),
    summary="Экспорт в YAML",
)
def export_niche_yaml(niche_id: NicheIdPath, principal: ViewerDep):
    text = niches_service.export_yaml(niche_id)
    return YamlResponse(text, headers={"Content-Disposition": f'attachment; filename="{niche_id}.yaml"'})
