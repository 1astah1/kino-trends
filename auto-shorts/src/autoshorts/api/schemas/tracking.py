"""Трекинг-ссылки (docs/ARCHITECTURE.md §5.2 «…трекинг», §6.6 «Атрибуция и конверсии»).

Реализация — M3-B (`api/routers/tracking_links.py`, `domain/tags.py`, `services/tracking.py`),
тег `tracking`:

* `GET /tracking-links` `?product_id&level&channel_id&campaign_id&is_active&q&limit&offset`
  → `TrackingLinkPage`;
* `POST /tracking-links` `TrackingLinkCreate` → 201 `TrackingLink` · 409 tracking_code_taken;
* `GET /tracking-links/{link_id}` → `TrackingLink`;
* `PATCH /tracking-links/{link_id}` `TrackingLinkPatch` → `TrackingLink`;
* `GET /tracking-links/{link_id}/stats` `?date_from&date_to&granularity` → `TrackingLinkStats`;
* `POST /channels/{channel_id}/tracking-link` `ChannelTrackingLinkCreate` → `TrackingLink`
  (кнопка «Подписаться» / шапка канала; повтор отдаёт существующую активную ссылку).

Коды (§6.6): пакетный — 5 символов Crockford base32 в нижнем регистре без `0/o/1/l/i/u`
(генерирует `package_build`); алиас площадки `yt_<id>` / `vk_<owner>_<id>` (регистрация публикации);
канальные, кампанийные и ручные. Все — алфавит start-параметра Telegram `^[A-Za-z0-9_-]{2,64}$`
(payload бота `/start`/`startapp` ≤ 64), кириллица невозможна по построению. Суффикс `_l` у
start-параметра означает «пришёл с лендинга» и в коде ссылки не хранится.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from autoshorts.api.schemas.common import ApiModel, Currency, PageMeta, RequestModel, Slug
from autoshorts.domain.enums import Platform, TrackingLevel

__all__ = [
    "LANDING_SUFFIX",
    "PACKAGE_CODE_ALPHABET",
    "PACKAGE_CODE_LENGTH",
    "TRACKING_CODE_PATTERN",
    "AttributionLevel",
    "ChannelTrackingLinkCreate",
    "ManualTrackingLevel",
    "TrackingLink",
    "TrackingLinkCreate",
    "TrackingLinkPage",
    "TrackingLinkPatch",
    "TrackingLinkStats",
    "TrafficGranularity",
    "TrafficPoint",
    "TrafficTotals",
]

TRACKING_CODE_PATTERN = r"^[A-Za-z0-9_-]{2,64}$"
"""Код ссылки; Telegram start/startapp payload — `[A-Za-z0-9_-]{1,64}`, у нас от 2 символов."""
PACKAGE_CODE_ALPHABET = "23456789abcdefghjkmnpqrstvwxyz"
"""Crockford base32 в нижнем регистре без 0/o/1/l/i/u (§6.6)."""
PACKAGE_CODE_LENGTH = 5
LANDING_SUFFIX = "_l"
"""`<code>_l` в start-параметре: старт бота с лендинга (`via_landing=true`), без двойного счёта."""


class ManualTrackingLevel(StrEnum):
    """Уровни, которые оператор создаёт руками (package/publication создаёт система)."""

    MANUAL = "manual"
    CHANNEL = "channel"
    CAMPAIGN = "campaign"


class AttributionLevel(StrEnum):
    """Уровень атрибуции в отчётах (§6.6): код пакета/алиас → ролик; канал; голый домен и неизвестное."""

    EXACT = "exact"
    CHANNEL = "channel"
    DIRECT = "direct"


class TrackingLinkCreate(RequestModel):
    product_id: Slug
    level: ManualTrackingLevel
    channel_id: Slug | None = Field(default=None, description="Обязателен для level=channel")
    campaign_id: int | None = Field(default=None, description="Обязателен для level=campaign")
    code: str | None = Field(
        default=None, pattern=TRACKING_CODE_PATTERN, description="Пусто — сгенерировать; занят → 409"
    )
    label: str | None = Field(default=None, max_length=200)
    target_override_url: str | None = Field(default=None, pattern=r"^https://\S{3,2000}$")


class TrackingLinkPatch(RequestModel):
    label: str | None = Field(default=None, max_length=200)
    is_active: bool | None = None
    target_override_url: str | None = Field(default=None, pattern=r"^https://\S{3,2000}$")


class ChannelTrackingLinkCreate(RequestModel):
    product_id: Slug | None = Field(default=None, description="Пусто — продукт канала по умолчанию")
    label: str | None = Field(default=None, max_length=200)


class TrackingLink(ApiModel):
    id: int
    code: str
    product_id: str
    level: TrackingLevel
    job_id: int | None
    publication_id: int | None
    channel_id: str | None
    campaign_id: int | None
    banner_variant_id: int | None
    platform: Platform | None
    label: str | None
    target_override_url: str | None
    is_active: bool
    redirect_url: str = Field(description="https://<redirect_host>/<code>")
    telegram_url: str | None = Field(description="tg://resolve?domain=<bot>&start=<code>")
    telegram_https_url: str | None = Field(description="https://telegram.me/<bot>?start=<code>")
    tma_url: str | None = Field(description="https://t.me/<bot>/<app>?startapp=<code> для TMA")
    clicks_7d: int | None = Field(default=None, description="Только в списке")
    created_by: int | None
    created_at: datetime
    updated_at: datetime


class TrackingLinkPage(PageMeta):
    items: list[TrackingLink]


class TrafficGranularity(StrEnum):
    HOUR = "hour"
    DAY = "day"


class TrafficTotals(ApiModel):
    clicks: int
    unique_visitors: int
    bot_clicks: int = Field(description="Боты по UA и HEAD (в статистику не идут)")
    to_telegram: int
    to_site: int
    bot_starts: int
    signups: int
    first_payments: int
    revenue_minor: int
    currency: Currency | None


class TrafficPoint(ApiModel):
    t: datetime
    clicks: int
    unique_visitors: int
    to_telegram: int
    to_site: int
    bot_starts: int
    signups: int
    first_payments: int
    revenue_minor: int


class TrackingLinkStats(ApiModel):
    link_id: int
    granularity: TrafficGranularity
    totals: TrafficTotals
    series: list[TrafficPoint]
