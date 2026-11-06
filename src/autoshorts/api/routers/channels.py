"""Каналы, здоровье, инциденты (M2-A).

`/channels/{channel_id}/tracking-link` — M3-B, `/channels/{channel_id}/oauth/*` — M4-D.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request

from autoshorts.api.deps import OperatorDep, ViewerDep, audit_actor
from autoshorts.api.schemas.channels import (
    Channel,
    ChannelCreate,
    ChannelHealth,
    ChannelIncident,
    ChannelIncidentCreate,
    ChannelIncidentList,
    ChannelPage,
    ChannelPatch,
)
from autoshorts.api.schemas.common import SLUG_PATTERN, LimitQuery, OffsetQuery
from autoshorts.domain.enums import ChannelStatus, Platform
from autoshorts.services import channels as channels_service

router = APIRouter(tags=["channels"])

ChannelIdPath = Annotated[str, Path(pattern=SLUG_PATTERN)]


@router.get("/channels", name="list", response_model=ChannelPage, summary="Каналы")
def list_channels(
    principal: ViewerDep,
    platform: Platform | None = None,
    status: list[ChannelStatus] | None = Query(default=None),
    niche_id: str | None = Query(default=None, pattern=SLUG_PATTERN),
    cluster_id: str | None = Query(default=None, pattern=SLUG_PATTERN),
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
):
    return channels_service.list_channels(
        platform=platform,
        statuses=[str(s) for s in status] if status else None,
        niche_id=niche_id,
        cluster_id=cluster_id,
        limit=limit,
        offset=offset,
    )


@router.post("/channels", name="create", status_code=201, response_model=Channel, summary="Создать канал")
def create_channel(body: ChannelCreate, principal: OperatorDep, request: Request):
    fields = body.model_dump(mode="json")
    fields["profile"] = body.profile.model_dump(mode="json", exclude_none=True)
    fields["publishing_plan"] = body.publishing_plan.model_dump(mode="json", exclude_none=True)
    return channels_service.create_channel(fields, audit_actor(request, principal))


@router.get("/channels/{channel_id}", name="get", response_model=Channel, summary="Канал")
def get_channel(channel_id: ChannelIdPath, principal: ViewerDep):
    return channels_service.get_channel(channel_id)


@router.patch("/channels/{channel_id}", name="update", response_model=Channel, summary="Правка канала")
def update_channel(channel_id: ChannelIdPath, body: ChannelPatch, principal: OperatorDep, request: Request):
    changes = {}
    for name in body.model_fields_set:
        value = getattr(body, name)
        if name in ("profile", "publishing_plan") and value is not None:
            value = value.model_dump(mode="json", exclude_unset=True)
        elif name == "status" and value is not None:
            value = str(value)
        changes[name] = value
    return channels_service.update_channel(channel_id, changes, audit_actor(request, principal))


@router.get(
    "/channels/{channel_id}/health", name="health", response_model=ChannelHealth, summary="Здоровье канала"
)
def channel_health(channel_id: ChannelIdPath, principal: ViewerDep):
    return channels_service.channel_health(channel_id)


@router.get(
    "/channels/{channel_id}/incidents",
    name="incidents_list",
    response_model=ChannelIncidentList,
    summary="Журнал инцидентов",
)
def list_incidents(channel_id: ChannelIdPath, principal: ViewerDep):
    return channels_service.list_incidents(channel_id)


@router.post(
    "/channels/{channel_id}/incidents",
    name="incidents_create",
    status_code=201,
    response_model=ChannelIncident,
    summary="Записать инцидент",
)
def create_incident(
    channel_id: ChannelIdPath, body: ChannelIncidentCreate, principal: OperatorDep, request: Request
):
    return channels_service.create_incident(
        channel_id, body.model_dump(mode="python"), audit_actor(request, principal)
    )
