"""Журнал кликов редиректора (docs/ARCHITECTURE.md §5.2 `GET /clicks`, §5.3, §6.6).

Реализация — M3-B (`api/routers/clicks.py`, `repos/clicks.py`), тег `tracking`:

* `GET /clicks` `?tracking_link_id&publication_id&product_id&channel_id&route&include_bots&before_id&limit`
  → `ClickJournal` (журнал: курсор `before_id`, по убыванию id).

Сырой IP не хранится: `visitor_key = hmac_sha256(REDIRECT_SALT, day|ip|ua)[:32]`. Боты (UA, HEAD)
пишутся с `is_bot=true` и по умолчанию в журнал не попадают.
"""

from __future__ import annotations

from datetime import datetime

from autoshorts.api.schemas.common import ApiModel, JournalMeta
from autoshorts.domain.enums import ClickRoute, Platform

__all__ = ["Click", "ClickJournal"]


class Click(ApiModel):
    id: int
    occurred_at: datetime
    code: str
    tracking_link_id: int | None
    route: ClickRoute
    product_id: str | None
    publication_id: int | None
    job_id: int | None
    channel_id: str | None
    campaign_id: int | None
    banner_variant_id: int | None
    platform: Platform | None
    visitor_key: str
    user_agent: str | None
    referer_host: str | None
    is_bot: bool


class ClickJournal(JournalMeta):
    items: list[Click]
