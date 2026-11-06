"""Кампании и их варианты (M2-A).

A/B-статистика `/campaigns/{campaign_id}/experiment` — M4-B (`experiments.py`).
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from autoshorts.api.deps import OperatorDep, ViewerDep, audit_actor
from autoshorts.api.schemas.campaigns import (
    BannerVariant,
    BannerVariantCreate,
    BannerVariantList,
    Campaign,
    CampaignCreate,
    CampaignPage,
    CampaignPatch,
    CampaignStatusChange,
)
from autoshorts.api.schemas.common import SLUG_PATTERN, LimitQuery, OffsetQuery
from autoshorts.domain.enums import CampaignStatus
from autoshorts.services import campaigns as campaigns_service

router = APIRouter(tags=["campaigns"])


@router.get("/campaigns", name="list", response_model=CampaignPage, summary="Кампании")
def list_campaigns(
    principal: ViewerDep,
    product_id: str | None = Query(default=None, pattern=SLUG_PATTERN),
    status: list[CampaignStatus] | None = Query(default=None),
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
):
    return campaigns_service.list_campaigns(
        product_id=product_id,
        statuses=[str(s) for s in status] if status else None,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/campaigns", name="create", status_code=201, response_model=Campaign, summary="Создать кампанию"
)
def create_campaign(body: CampaignCreate, principal: OperatorDep, request: Request):
    fields = body.model_dump(mode="python")
    for key in ("allocation",):
        fields[key] = str(fields[key])
    fields["targeting"] = body.targeting.model_dump(mode="json")
    return campaigns_service.create_campaign(fields, audit_actor(request, principal))


@router.get("/campaigns/{campaign_id}", name="get", response_model=Campaign, summary="Кампания")
def get_campaign(campaign_id: int, principal: ViewerDep):
    return campaigns_service.get_campaign(campaign_id)


@router.patch("/campaigns/{campaign_id}", name="update", response_model=Campaign, summary="Правка кампании")
def update_campaign(campaign_id: int, body: CampaignPatch, principal: OperatorDep, request: Request):
    changes = {}
    for name in body.model_fields_set:
        value = getattr(body, name)
        if name == "targeting" and value is not None:
            value = value.model_dump(mode="json")
        elif name == "allocation" and value is not None:
            value = str(value)
        changes[name] = value
    return campaigns_service.update_campaign(campaign_id, changes, audit_actor(request, principal))


@router.post(
    "/campaigns/{campaign_id}/status", name="set_status", response_model=Campaign, summary="Статус кампании"
)
def set_campaign_status(
    campaign_id: int, body: CampaignStatusChange, principal: OperatorDep, request: Request
):
    return campaigns_service.set_campaign_status(
        campaign_id, str(body.status), audit_actor(request, principal)
    )


@router.get(
    "/campaigns/{campaign_id}/variants",
    name="variants_list",
    response_model=BannerVariantList,
    summary="Варианты",
)
def list_variants(campaign_id: int, principal: ViewerDep):
    return campaigns_service.list_variants(campaign_id)


@router.post(
    "/campaigns/{campaign_id}/variants",
    name="variants_create",
    status_code=201,
    response_model=BannerVariant,
    summary="Создать вариант",
)
def create_variant(campaign_id: int, body: BannerVariantCreate, principal: OperatorDep, request: Request):
    return campaigns_service.create_variant(campaign_id, body, audit_actor(request, principal))
