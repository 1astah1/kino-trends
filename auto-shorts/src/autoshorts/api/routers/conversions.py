"""Журнал конверсий (§5.2 `GET /conversions`, §6.6). Владелец — M3-B.

Контракт — `api/schemas/conversions.py`. Заглушка контракта M5M3-0 отвечает 501; M3-B заменяет тело.
Приём конверсий — не здесь: публичный `POST /ingest/v1/events` (хост редиректора, `public/*`, HMAC).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from autoshorts.api.deps import ViewerDep
from autoshorts.api.schemas.common import SLUG_PATTERN, BeforeIdQuery, LimitQuery
from autoshorts.api.schemas.conversions import ConversionJournal
from autoshorts.domain.enums import ConversionEventType

router = APIRouter(tags=["tracking"])

SlugQuery = Annotated[str | None, Query(pattern=SLUG_PATTERN)]


@router.get(
    "/conversions", name="conversions_list", response_model=ConversionJournal, summary="Журнал конверсий"
)
def list_conversions(
    principal: ViewerDep,
    product_id: SlugQuery = None,
    tracking_link_id: int | None = None,
    publication_id: int | None = None,
    event_type: list[ConversionEventType] | None = Query(default=None),
    include_test: bool = False,
    before_id: BeforeIdQuery = None,
    limit: LimitQuery = 50,
):
    from autoshorts.db.pool import connection
    from autoshorts.repos import conversions as conversions_repo
    from autoshorts.services.tracking import conversion_view, journal_page

    with connection() as conn:
        rows = conversions_repo.journal(
            conn,
            product_id=product_id,
            tracking_link_id=tracking_link_id,
            publication_id=publication_id,
            event_types=[t.value for t in event_type] if event_type else None,
            include_test=include_test,
            before_id=before_id,
            limit=limit,
        )
    return {"items": [conversion_view(r) for r in rows], "next_before_id": journal_page(rows, limit)}
