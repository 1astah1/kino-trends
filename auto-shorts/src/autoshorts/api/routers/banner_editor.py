"""Редактор баннера (M2-A): креативы владельца, safe zones, шрифты, проверка и PNG-кадр варианта.

Решение владельца от 2026-09-26 (docs/OWNER-DECISIONS.md п. 1): баннер — загруженный креатив.
Форма — `api/schemas/banners.py`, логика — `services/{banner_creatives,banners,banner_preview,campaigns}.py`.

- `POST /banner-creatives`: multipart `file` ≤ 50 МБ; статика — сразу `ready`, анимация — `processing`
  + `task_id` (тяжёлая задача `banner_creative_normalize`). Отказ: 413 / 415 / 422 `creative_rejected`,
  в `details.asset_error` — `CreativeRejectCode`.
- `DELETE /banner-creatives/{creative_sha256}`: мягкое удаление; 409 `creative_in_use` + variant_ids.
- `POST /banner-variants/validate`: `banner_validate.validate_variant` по профилям safe zones.
- `POST /banner-variants/preview`: PNG тем же кодом, что рендер (`banner_overlay`), `X-Preview-T-Sec`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Path, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse

from autoshorts.api.deps import OperatorDep, ViewerDep, audit_actor
from autoshorts.api.media import media_response
from autoshorts.api.routers import PngResponse
from autoshorts.api.schemas.banners import (
    FONT_NAME_PATTERN,
    SHA256_PATTERN,
    BannerCreative,
    BannerCreativePage,
    BannerCreativePatch,
    BannerCreativeUploadResult,
    BannerFontList,
    BannerPreviewRequest,
    BannerSafeZoneCatalog,
    BannerValidateRequest,
    BannerValidateResponse,
    CreativeMediaKind,
    CreativeStatus,
)
from autoshorts.api.schemas.campaigns import BannerVariant, BannerVariantDuplicate
from autoshorts.api.schemas.common import (
    SLUG_PATTERN,
    LimitQuery,
    OffsetQuery,
    SortQuery,
    binary_responses,
    json_error_responses,
)
from autoshorts.domain.banner_schema import CreativeKind
from autoshorts.services import banner_creatives as creatives_service
from autoshorts.services import banners as banners_service
from autoshorts.services import campaigns as campaigns_service

router = APIRouter(tags=["banners"])

CreativeSha = Annotated[
    str, Path(pattern=SHA256_PATTERN, description="sha256 исходного файла креатива, 64 hex")
]
FontName = Annotated[str, Path(pattern=FONT_NAME_PATTERN, description="Имя файла шрифта из config/fonts")]
IMMUTABLE_CACHE = "private, max-age=604800, immutable"


def _png_with_t_header() -> dict[int | str, dict[str, Any]]:
    responses = binary_responses("image/png", description="Кадр PNG тем же кодом, что финальный рендер")
    responses[200]["headers"] = {
        "X-Preview-T-Sec": {
            "description": "Фактическое время кадра, с (для moment=impulse|final_repeat)",
            "schema": {"type": "number"},
        }
    }
    return responses


# ---------------------------------------------------------------- креативы


@router.get(
    "/banner-creatives",
    name="creatives_list",
    response_model=BannerCreativePage,
    summary="Библиотека креативов (без удалённых)",
)
def list_creatives(
    principal: ViewerDep,
    kind: CreativeKind | None = None,
    status: list[CreativeStatus] | None = Query(default=None),
    product_id: str | None = Query(default=None, pattern=SLUG_PATTERN),
    q: str | None = Query(default=None, max_length=200, description="Поиск по имени"),
    sort: SortQuery = None,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
):
    return creatives_service.list_creatives(
        kind=str(kind) if kind else None,
        statuses=[str(s) for s in status] if status else None,
        product_id=product_id,
        q=q,
        sort=sort,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/banner-creatives",
    name="creatives_upload",
    status_code=201,
    response_model=BannerCreativeUploadResult,
    summary="Загрузить креатив (multipart, ≤ 50 МБ; 413/415/422 c details.asset_error)",
)
def upload_creative(
    file: Annotated[
        UploadFile,
        File(description="PNG/JPG/WebP, GIF, APNG, WebM (альфа), MP4/MOV; тип — по сигнатуре"),
    ],
    principal: OperatorDep,
    request: Request,
    name: Annotated[str | None, Form(min_length=1, max_length=200)] = None,
    product_id: Annotated[str | None, Form(pattern=SLUG_PATTERN)] = None,
):
    return creatives_service.upload_creative(
        file.file,
        filename=file.filename,
        name=name,
        product_id=product_id,
        actor=audit_actor(request, principal),
        declared_size=file.size,
    )


@router.get(
    "/banner-creatives/{creative_sha256}",
    name="creatives_get",
    response_model=BannerCreative,
    summary="Креатив",
)
def get_creative(creative_sha256: CreativeSha, principal: ViewerDep):
    return creatives_service.get_creative(creative_sha256)


@router.patch(
    "/banner-creatives/{creative_sha256}",
    name="creatives_update",
    response_model=BannerCreative,
    summary="Переименовать, заметка, продукт",
)
def update_creative(
    creative_sha256: CreativeSha, body: BannerCreativePatch, principal: OperatorDep, request: Request
):
    changes = {name: getattr(body, name) for name in body.model_fields_set}
    return creatives_service.update_creative(creative_sha256, changes, audit_actor(request, principal))


@router.delete(
    "/banner-creatives/{creative_sha256}",
    name="creatives_delete",
    status_code=204,
    response_class=Response,
    responses=json_error_responses(),
    summary="Удалить креатив (409 creative_in_use, пока на него ссылаются варианты)",
)
def delete_creative(creative_sha256: CreativeSha, principal: OperatorDep, request: Request):
    creatives_service.delete_creative(creative_sha256, audit_actor(request, principal))
    return Response(status_code=204)


@router.get(
    "/banner-creatives/{creative_sha256}/media/{media_kind}",
    name="creatives_media",
    response_class=Response,
    responses=binary_responses(
        "image/png", "video/webm", description="Нормализованный файл, миниатюра, 1-й кадр"
    ),
    summary="Медиа креатива (Range)",
)
def creative_media(creative_sha256: CreativeSha, media_kind: CreativeMediaKind, principal: ViewerDep):
    rel, mime, filename = creatives_service.creative_media(creative_sha256, str(media_kind))
    return media_response(rel, filename=filename, media_type=mime, cache_control=IMMUTABLE_CACHE)


# ---------------------------------------------------------------- справочники редактора


@router.get(
    "/banner-safe-zones",
    name="safe_zones",
    response_model=BannerSafeZoneCatalog,
    summary="Профили safe zones площадок (config/safe_zones.json)",
)
def safe_zones(principal: ViewerDep):
    return banners_service.safe_zone_catalog()


@router.get(
    "/banner-fonts", name="fonts_list", response_model=BannerFontList, summary="Шрифты служебного текста"
)
def list_fonts(principal: ViewerDep):
    return {"items": banners_service.list_fonts()}


@router.get(
    "/banner-fonts/{font_name}",
    name="fonts_file",
    response_class=Response,
    responses=binary_responses("font/ttf", "font/otf", description="Файл шрифта"),
    summary="Файл шрифта для превью редактора",
)
def font_file(font_name: FontName, principal: ViewerDep):
    path = banners_service.font_path(font_name)
    mime = "font/otf" if font_name.lower().endswith(".otf") else "font/ttf"
    return FileResponse(path, media_type=mime, headers={"Cache-Control": IMMUTABLE_CACHE})


# ---------------------------------------------------------------- проверка и кадр варианта


@router.post(
    "/banner-variants/validate",
    name="variant_validate",
    response_model=BannerValidateResponse,
    summary="Проверка варианта или черновика (источник истины)",
)
def validate_variant(body: BannerValidateRequest, principal: OperatorDep):
    return banners_service.validate_request(body)


@router.post(
    "/banner-variants/preview",
    name="variant_preview",
    response_class=PngResponse,
    responses=_png_with_t_header(),
    summary="«Кадр из рендера»: PNG варианта или черновика",
)
def preview_variant(body: BannerPreviewRequest, principal: OperatorDep):
    png, t_sec = banners_service.preview_request(body)
    return PngResponse(png, headers={"X-Preview-T-Sec": f"{t_sec:.3f}", "Cache-Control": "no-store"})


@router.get(
    "/banner-variants/{variant_id}",
    name="variant_get",
    response_model=BannerVariant,
    summary="Вариант целиком (для редактора)",
)
def get_variant(variant_id: int, principal: ViewerDep):
    return campaigns_service.get_variant(variant_id)


@router.post(
    "/banner-variants/{variant_id}/duplicate",
    name="variant_duplicate",
    status_code=201,
    response_model=BannerVariant,
    summary="Копия варианта для A/B (новый code)",
)
def duplicate_variant(
    variant_id: int, body: BannerVariantDuplicate, principal: OperatorDep, request: Request
):
    return campaigns_service.duplicate_variant(
        variant_id,
        code=body.code,
        name=body.name,
        campaign_id=body.campaign_id,
        actor=audit_actor(request, principal),
    )
