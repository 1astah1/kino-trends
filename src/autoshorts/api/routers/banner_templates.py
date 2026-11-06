"""Шаблоны баннера (M2-A): пресеты варианта — CRUD, YAML формы config/banner.yaml, проверка, PNG-кадр.

Решение владельца 2026-09-26: `layout` шаблона — это `BannerVariantDraft` (слои с креативом,
тайминг, маркировка). Проверка и кадр принимают общие `BannerValidateRequest` /
`BannerPreviewRequest`: `draft` (черновик) или `variant_id`, иначе — `layout` самого шаблона.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile

from autoshorts.api.deps import OperatorDep, ViewerDep, audit_actor
from autoshorts.api.media import ascii_filename
from autoshorts.api.routers import PngResponse, YamlResponse
from autoshorts.api.schemas.banners import (
    BannerPreviewRequest,
    BannerTemplate,
    BannerTemplateCreate,
    BannerTemplatePage,
    BannerTemplatePatch,
    BannerValidateRequest,
    BannerValidateResponse,
)
from autoshorts.api.schemas.common import LimitQuery, OffsetQuery, binary_responses, text_responses
from autoshorts.domain.enums import BannerTemplateStatus
from autoshorts.services import banners as banners_service
from autoshorts.services.products import field_error

router = APIRouter(tags=["banners"])


def _read_text(file: UploadFile) -> str:
    raw = file.file.read(banners_service.MAX_YAML_BYTES + 1)
    if len(raw) > banners_service.MAX_YAML_BYTES:
        raise field_error("file", "YAML баннера больше 256 КБ", "too_large")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise field_error("file", "YAML должен быть в UTF-8", "encoding") from None


@router.get("/banner-templates", name="list", response_model=BannerTemplatePage, summary="Шаблоны баннера")
def list_templates(
    principal: ViewerDep,
    status: BannerTemplateStatus | None = None,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
):
    return banners_service.list_templates(status=status, limit=limit, offset=offset)


@router.post(
    "/banner-templates",
    name="create",
    status_code=201,
    response_model=BannerTemplate,
    summary="Создать шаблон",
)
def create_template(body: BannerTemplateCreate, principal: OperatorDep, request: Request):
    return banners_service.create_template(body.name, body.layout, audit_actor(request, principal))


@router.post(
    "/banner-templates/import-yaml",
    name="import_yaml",
    status_code=201,
    response_model=BannerTemplate,
    summary="Импорт формы config/banner.yaml",
)
def import_template_yaml(
    file: Annotated[UploadFile, File()],
    name: Annotated[str, Form(min_length=1)],
    principal: OperatorDep,
    request: Request,
):
    return banners_service.import_template_yaml(
        _read_text(file), name=name, actor=audit_actor(request, principal)
    )


@router.get("/banner-templates/{template_id}", name="get", response_model=BannerTemplate, summary="Шаблон")
def get_template(template_id: int, principal: ViewerDep):
    return banners_service.get_template(template_id)


@router.patch(
    "/banner-templates/{template_id}",
    name="update",
    response_model=BannerTemplate,
    summary="Правка шаблона (409 version_conflict)",
)
def update_template(template_id: int, body: BannerTemplatePatch, principal: OperatorDep, request: Request):
    return banners_service.update_template(
        template_id,
        name=body.name,
        status=str(body.status) if body.status else None,
        draft=body.layout,
        expected_version=body.expected_version,
        actor=audit_actor(request, principal),
    )


@router.post(
    "/banner-templates/{template_id}/duplicate",
    name="duplicate",
    status_code=201,
    response_model=BannerTemplate,
    summary="Копия шаблона",
)
def duplicate_template(template_id: int, principal: OperatorDep, request: Request):
    return banners_service.duplicate_template(template_id, audit_actor(request, principal))


@router.get(
    "/banner-templates/{template_id}/export-yaml",
    name="export_yaml",
    response_class=YamlResponse,
    responses=text_responses("text/yaml", "YAML формы config/banner.yaml"),
    summary="Экспорт в YAML",
)
def export_template_yaml(template_id: int, principal: ViewerDep):
    text, name = banners_service.export_template_yaml(template_id)
    filename = ascii_filename(name, default=f"banner_{template_id}") + ".yaml"
    return YamlResponse(text, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post(
    "/banner-templates/{template_id}/validate",
    name="validate",
    response_model=BannerValidateResponse,
    summary="Серверная валидация (источник истины)",
)
def validate_template(template_id: int, body: BannerValidateRequest, principal: OperatorDep):
    return banners_service.validate_request(body, template_id=template_id)


@router.post(
    "/banner-templates/{template_id}/preview",
    name="preview",
    response_class=PngResponse,
    responses=binary_responses("image/png", description="Кадр 540×960 тем же кодом, что в рендере"),
    summary="PNG-кадр баннера",
)
def preview_template(template_id: int, body: BannerPreviewRequest, principal: OperatorDep):
    png, t_sec = banners_service.preview_request(body, template_id=template_id)
    return PngResponse(png, headers={"X-Preview-T-Sec": f"{t_sec:.3f}", "Cache-Control": "no-store"})
