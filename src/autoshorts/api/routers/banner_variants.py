"""Варианты баннера (A/B): правка и статус (M2-A). Карточка, копия, проверка и кадр — `banner_editor.py`."""

from __future__ import annotations

from fastapi import APIRouter, Request

from autoshorts.api.deps import OperatorDep, audit_actor
from autoshorts.api.schemas.campaigns import BannerVariant, BannerVariantPatch, VariantStatusChange
from autoshorts.services import campaigns as campaigns_service

router = APIRouter(tags=["campaigns"])


@router.patch(
    "/banner-variants/{variant_id}",
    name="variants_update",
    response_model=BannerVariant,
    summary="Правка варианта",
)
def update_variant(variant_id: int, body: BannerVariantPatch, principal: OperatorDep, request: Request):
    return campaigns_service.update_variant(variant_id, body, audit_actor(request, principal))


@router.post(
    "/banner-variants/{variant_id}/status",
    name="variants_set_status",
    response_model=BannerVariant,
    summary="Статус варианта",
)
def set_variant_status(variant_id: int, body: VariantStatusChange, principal: OperatorDep, request: Request):
    return campaigns_service.set_variant_status(variant_id, str(body.status), audit_actor(request, principal))
