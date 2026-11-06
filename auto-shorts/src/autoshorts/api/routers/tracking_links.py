"""Трекинг-ссылки: ручные, канальные, кампанийные; статистика (§5.2, §6.6). Владелец — M3-B.

Контракт — `api/schemas/tracking.py`. Заглушки контракта M5M3-0 отвечают 501; M3-B заменяет тела, не
меняя пути, имена и схемы. Коды — `domain/tags.py` (Crockford, алиасы, `_l`), алфавит start-параметра
Telegram `^[A-Za-z0-9_-]{2,64}$`. Пакетные и публикационные ссылки создаёт система (package_build,
регистрация публикации), здесь — только manual | channel | campaign.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import JSONResponse

from autoshorts.api.deps import OperatorDep, ViewerDep, audit_actor
from autoshorts.api.schemas.common import SLUG_PATTERN, DateFromQuery, DateToQuery, LimitQuery, OffsetQuery
from autoshorts.api.schemas.tracking import (
    ChannelTrackingLinkCreate,
    TrackingLink,
    TrackingLinkCreate,
    TrackingLinkPage,
    TrackingLinkPatch,
    TrackingLinkStats,
    TrafficGranularity,
)
from autoshorts.domain.enums import TrackingLevel

router = APIRouter(tags=["tracking"])

SlugQuery = Annotated[str | None, Query(pattern=SLUG_PATTERN)]
ChannelIdPath = Annotated[str, Path(pattern=SLUG_PATTERN)]


def _connection() -> Any:
    from autoshorts.db.pool import connection

    return connection()


def _svc() -> Any:
    from autoshorts.services import tracking

    return tracking


def _out(conn: Any, row: dict[str, Any]) -> dict[str, Any]:
    return TrackingLink.model_validate(_svc().link_view(conn, row)).model_dump(mode="json")


@router.get("/tracking-links", name="links_list", response_model=TrackingLinkPage, summary="Трекинг-ссылки")
def list_links(
    principal: ViewerDep,
    product_id: SlugQuery = None,
    level: list[TrackingLevel] | None = Query(default=None),
    channel_id: SlugQuery = None,
    campaign_id: int | None = None,
    is_active: bool | None = None,
    q: str | None = Query(default=None, max_length=100, description="Поиск по коду и подписи"),
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
):
    with _connection() as conn:
        return _svc().list_links(
            conn,
            product_id=product_id,
            levels=[x.value for x in level] if level else None,
            channel_id=channel_id,
            campaign_id=campaign_id,
            is_active=is_active,
            q=q,
            limit=limit,
            offset=offset,
        )


@router.post(
    "/tracking-links",
    name="links_create",
    status_code=201,
    response_model=TrackingLink,
    summary="Создать ссылку (manual | channel | campaign; 409 tracking_code_taken)",
)
def create_link(body: TrackingLinkCreate, principal: OperatorDep, request: Request):
    with _connection() as conn:
        row = _svc().create_link(conn, body, audit_actor(request, principal))
        out = _out(conn, row)
    return JSONResponse(out, status_code=201)


@router.get("/tracking-links/{link_id}", name="links_get", response_model=TrackingLink, summary="Ссылка")
def get_link(link_id: int, principal: ViewerDep):
    with _connection() as conn:
        return _out(conn, _svc().require_link(conn, link_id))


@router.patch(
    "/tracking-links/{link_id}", name="links_update", response_model=TrackingLink, summary="Правка ссылки"
)
def update_link(link_id: int, body: TrackingLinkPatch, principal: OperatorDep, request: Request):
    with _connection() as conn:
        row = _svc().update_link(
            conn, link_id, body.model_dump(exclude_unset=True), audit_actor(request, principal)
        )
        return _out(conn, row)


@router.get(
    "/tracking-links/{link_id}/stats",
    name="links_stats",
    response_model=TrackingLinkStats,
    summary="Клики и конверсии ссылки",
)
def link_stats(
    link_id: int,
    principal: ViewerDep,
    date_from: DateFromQuery = None,
    date_to: DateToQuery = None,
    granularity: TrafficGranularity = TrafficGranularity.DAY,
):
    with _connection() as conn:
        return _svc().link_stats(
            conn, link_id, date_from=date_from, date_to=date_to, granularity=granularity.value
        )


@router.post(
    "/channels/{channel_id}/tracking-link",
    name="channel_link_create",
    response_model=TrackingLink,
    summary="Ссылка канала (шапка, «Подписаться»); повтор — существующая",
)
def create_channel_link(
    channel_id: ChannelIdPath, body: ChannelTrackingLinkCreate, principal: OperatorDep, request: Request
):
    with _connection() as conn:
        row = _svc().channel_link(
            conn, channel_id, body.product_id, body.label, audit_actor(request, principal)
        )
        return _out(conn, row)
