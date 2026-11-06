"""Публикации: разбор ссылки, регистрация, реестр, метрики (§5.2 «…публикации…», §5.4 Publication, §6.5).

Реализация — M3-A (`api/routers/publications.py`, `domain/url_parse.py`, `services/publications.py`),
тег `publications`. Площадки: YouTube Shorts — основная, VK Клипы — вторая.

* `POST /publications/parse-url` `ParseUrlRequest` → `ParsedUrl` · 422 unsupported_url
  (кириллица, мусор, чужие домены);
* `POST /publications` `PublicationCreate` → 201 `PublicationCreated` · 409 publication_exists
  | same_platform_crosspost_forbidden · 422 platform_mismatch (площадка ссылки ≠ площадка канала).
  Одна транзакция: публикация, алиас `yt_<id>` / `vk_<owner>_<id>`, пакет → published,
  ролик → published, мастер в `masters/`; `Idempotency-Key` поддерживается;
* `GET /publications` `?platform&channel_id&niche_id&product_id&campaign_id&banner_variant_id&kind&status
  &verify_status&date_from&date_to&sort=-latest_views&limit&offset` → `PublicationPage`;
* `GET /publications/{publication_id}` → `Publication`;
* `PATCH /publications/{publication_id}` `PublicationPatch` → `Publication`;
* `DELETE /publications/{publication_id}` (admin) → 204: мягкое удаление ошибочной регистрации,
  ролик → approved, пакет → ready;
* `POST /publications/{publication_id}/reverify`, `/refresh-metrics` → 202 `TaskAccepted`;
* `GET /publications/{publication_id}/metrics` `?granularity=raw|day` → `PublicationMetrics`;
* `GET /publications/{publication_id}/retention` → `PublicationRetention`;
* `GET /publications/{publication_id}/traffic` `?granularity=hour|day` → `PublicationTrafficSeries`.

M4-0 (аддитивно, поля с умолчаниями — реализация M3-A может их пока не заполнять):
`PublicationLatest.source` (кто снял последний снимок), `PublicationAgeViews.d14/d30`,
`Publication.freshness` (`schemas.metrics.PublicationFreshness`: коллектор, опрос, ручной ввод).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from autoshorts.api.schemas.clipping import OriginalityVerdict
from autoshorts.api.schemas.common import ApiModel, Currency, PageMeta, RequestModel
from autoshorts.api.schemas.jobs import BannerVariantRef, ChannelRef
from autoshorts.api.schemas.metrics import PublicationFreshness
from autoshorts.api.schemas.tracking import TrackingLink, TrafficGranularity, TrafficPoint
from autoshorts.domain.enums import (
    JobKind,
    MetricsSource,
    Platform,
    PlatformKind,
    PublicationStatus,
    RetentionFailureClass,
    VerifyStatus,
)

__all__ = [
    "AgeVerdict",
    "MetricsGranularity",
    "MetricsPoint",
    "ParseUrlRequest",
    "ParsedUrl",
    "Publication",
    "PublicationAgeViews",
    "PublicationCreate",
    "PublicationCreated",
    "PublicationLatest",
    "PublicationMetrics",
    "PublicationPage",
    "PublicationPatch",
    "PublicationRetention",
    "PublicationTracking",
    "PublicationTraffic",
    "PublicationTrafficSeries",
    "PublicationUnitEconomics",
    "RetentionPoint",
    "TrafficGranularity",
    "TrafficPoint",
]

URL_MAX = 2000


class ParseUrlRequest(RequestModel):
    url: str = Field(min_length=8, max_length=URL_MAX)


class ParsedUrl(ApiModel):
    """Результат `domain/url_parse.py` (фронт повторяет его в `web/lib/platform-url.ts`)."""

    platform: Platform
    platform_video_id: str
    platform_kind: PlatformKind
    canonical_url: str
    alias_code: str = Field(description="yt_<id> | vk_<owner>_<id>, алфавит start-параметра Telegram")
    owner_ref: str | None = Field(default=None, description="VK: владелец клипа (-<group_id> или <user_id>)")


class PublicationCreate(RequestModel):
    package_id: int
    url: str = Field(min_length=8, max_length=URL_MAX)
    published_at: datetime | None = Field(default=None, description="Пусто — сейчас")


class PublicationPatch(RequestModel):
    status: PublicationStatus | None = None
    status_reason: str | None = Field(default=None, max_length=500)
    has_claim: bool | None = None
    published_at: datetime | None = None


class PublicationTracking(ApiModel):
    codes: list[str] = Field(description="Код пакета и алиас площадки")


class PublicationLatest(ApiModel):
    captured_at: datetime | None
    views: int | None
    likes: int | None
    comments: int | None
    engaged_views: int | None
    retention_3s: float | None
    avg_pct_viewed: float | None
    source: MetricsSource | None = Field(default=None, description="Источник последнего снимка (M4)")


class PublicationAgeViews(ApiModel):
    """Просмотры на ровный возраст: линейная интерполяция между снимками, без экстраполяции (§6.5)."""

    h24: int | None
    h72: int | None
    d7: int | None
    d14: int | None = Field(default=None, description="M4: для когорт d14")
    d30: int | None = Field(default=None, description="M4: для когорт d30")


class AgeVerdict(StrEnum):
    """Какой срез по возрасту уже готов для вердикта."""

    H24 = "h24"
    H72 = "h72"
    D7 = "d7"


class PublicationTraffic(ApiModel):
    landing_views: int
    unique_visitors: int
    to_telegram: int
    to_site: int
    bot_starts: int
    signups: int
    first_payments: int
    revenue_minor: int
    currency: Currency | None


class PublicationUnitEconomics(ApiModel):
    job_cost_usd: float
    ctr_per_1k_views: float | None
    cost_per_visitor_usd: float | None


class Publication(ApiModel):
    id: int
    job_id: int
    package_id: int | None
    video_uid: str
    kind: JobKind
    platform: Platform
    platform_video_id: str | None
    platform_kind: PlatformKind | None
    url: str | None
    canonical_url: str | None
    status: PublicationStatus
    status_reason: str | None
    verify_status: VerifyStatus
    verify_detail: str | None
    verified_at: datetime | None
    has_claim: bool
    channel: ChannelRef
    niche_id: str | None
    product_id: str | None
    campaign_id: int | None
    banner_variant: BannerVariantRef | None
    tracking: PublicationTracking
    latest: PublicationLatest
    age_views: PublicationAgeViews
    verdict: AgeVerdict | None
    traffic: PublicationTraffic
    unit_economics: PublicationUnitEconomics
    originality_verdict: OriginalityVerdict | None = Field(
        description="Нарезка: итог отчёта на момент заливки"
    )
    next_poll_at: datetime | None
    published_at: datetime | None
    created_at: datetime
    freshness: PublicationFreshness | None = Field(
        default=None, description="M4: свежесть метрик, коллектор и ручной ввод"
    )


class PublicationPage(PageMeta):
    items: list[Publication]


class PublicationCreated(ApiModel):
    publication: Publication
    tracking_links: list[TrackingLink] = Field(description="Код пакета (package) и алиас (publication)")


class MetricsGranularity(StrEnum):
    RAW = "raw"
    DAY = "day"


class MetricsPoint(ApiModel):
    t: datetime
    age_hours: float
    views: int | None
    likes: int | None
    comments: int | None
    engaged_views: int | None
    source: MetricsSource


class PublicationMetrics(ApiModel):
    publication_id: int
    granularity: MetricsGranularity
    points: list[MetricsPoint]


class RetentionPoint(ApiModel):
    ratio: float = Field(description="Доля длительности ролика, 0..1")
    audience_watch_ratio: float
    relative: float | None = Field(description="relativeRetentionPerformance")


class PublicationRetention(ApiModel):
    captured_at: datetime | None
    duration_sec: float | None
    retention_3s: float | None
    failure_class: RetentionFailureClass | None
    points: list[RetentionPoint]


class PublicationTrafficSeries(ApiModel):
    publication_id: int
    granularity: TrafficGranularity
    points: list[TrafficPoint]
