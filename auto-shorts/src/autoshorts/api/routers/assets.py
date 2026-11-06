"""Ассеты (шрифты, логотипы, музыка) — M2-A."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from autoshorts.api.deps import OperatorDep, ViewerDep, audit_actor
from autoshorts.api.media import media_response
from autoshorts.api.schemas.assets import Asset, AssetCreate, AssetList
from autoshorts.api.schemas.common import binary_responses
from autoshorts.domain.enums import AssetKind
from autoshorts.services import assets as assets_service

router = APIRouter(tags=["assets"])


@router.get("/assets", name="list", response_model=AssetList, summary="Ассеты")
def list_assets(principal: ViewerDep, kind: AssetKind | None = None):
    return assets_service.list_assets(str(kind) if kind else None)


@router.post(
    "/assets",
    name="create",
    status_code=201,
    response_model=Asset,
    summary="Зарегистрировать ассет из загрузки (422 font_no_cyrillic)",
)
def create_asset(body: AssetCreate, principal: OperatorDep, request: Request):
    return assets_service.create_asset(
        upload_id=body.upload_id,
        kind=str(body.kind),
        name=body.name,
        license_note=body.license_note,
        actor=audit_actor(request, principal),
    )


@router.get(
    "/assets/{asset_id}/file",
    name="file",
    response_class=Response,
    responses=binary_responses(
        "application/octet-stream", "font/ttf", "image/png", description="Файл ассета"
    ),
    summary="Файл ассета (шрифты для превью баннера)",
)
def asset_file(asset_id: int, principal: ViewerDep):
    row = assets_service.asset_file(asset_id)
    return media_response(row["path"], filename=row["name"], media_type=row["mime"])


@router.delete(
    "/assets/{asset_id}", name="delete", status_code=204, response_class=Response, summary="Удалить"
)
def delete_asset(asset_id: int, principal: OperatorDep, request: Request):
    assets_service.delete_asset(asset_id, audit_actor(request, principal))
    return Response(status_code=204)
