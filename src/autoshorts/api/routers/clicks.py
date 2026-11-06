"""Журнал кликов редиректора (§5.2 `GET /clicks`, §6.6). Владелец — M3-B.

Контракт — `api/schemas/clicks.py`. Заглушка контракта M5M3-0 отвечает 501; M3-B заменяет тело.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from autoshorts.api.deps import ViewerDep
from autoshorts.api.schemas.clicks import ClickJournal
from autoshorts.api.schemas.common import SLUG_PATTERN, BeforeIdQuery, LimitQuery
from autoshorts.domain.enums import ClickRoute

router = APIRouter(tags=["tracking"])

SlugQuery = Annotated[str | None, Query(pattern=SLUG_PATTERN)]


@router.get("/clicks", name="clicks_list", response_model=ClickJournal, summary="Журнал кликов")
def list_clicks(
    principal: ViewerDep,
    tracking_link_id: int | None = None,
    publication_id: int | None = None,
    product_id: SlugQuery = None,
    channel_id: SlugQuery = None,
    route: list[ClickRoute] | None = Query(default=None),
    include_bots: bool = False,
    before_id: BeforeIdQuery = None,
    limit: LimitQuery = 50,
):
    from autoshorts.db.pool import connection
    from autoshorts.repos import clicks as clicks_repo
    from autoshorts.services.tracking import journal_page

    with connection() as conn:
        rows = clicks_repo.journal(
            conn,
            tracking_link_id=tracking_link_id,
            publication_id=publication_id,
            product_id=product_id,
            channel_id=channel_id,
            routes=[r.value for r in route] if route else None,
            include_bots=include_bots,
            before_id=before_id,
            limit=limit,
        )
    return {"items": rows, "next_before_id": journal_page(rows, limit)}
