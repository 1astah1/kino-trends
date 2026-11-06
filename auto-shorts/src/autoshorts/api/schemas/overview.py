"""Обзор — «Пульт» оператора (docs/ARCHITECTURE.md §5.2 `/overview`, §7.3 «Обзор»; контракт M4-0).

Реализация — M4-B (`services/analytics.py`, `api/routers/overview.py`), тег `overview`:

* `GET /overview` `OverviewQuery` → `Overview` (viewer+).

Период по умолчанию — последние `OVERVIEW_DEFAULT_DAYS` бизнес-дней включая сегодня, дельты KPI — к
прошлому периоду той же длины (`analytics.previous_range`). Режим всегда `activity`. Определения
метрик — как в `schemas.analytics` (посетители, пользователи, честная цена опубликованного ролика).
Колонка «Требует тебя» (`todo`) и онбординг при пустой базе приходят тем же ответом, чтобы Обзор
открывался одним запросом; ряды для спарклайнов — дневные точки тех же KPI.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, Field, model_validator

from autoshorts.api.schemas.analytics import (
    ANALYTICS_MAX_DAYS,
    BreakdownRow,
    ChartAnnotation,
    DataFreshness,
    DateRange,
    FunnelBranches,
    MetricSeries,
)
from autoshorts.api.schemas.budget import Budget
from autoshorts.api.schemas.common import ApiModel, Slug
from autoshorts.api.schemas.system import Alert
from autoshorts.domain.enums import AnalyticsBasis, Platform

__all__ = [
    "OVERVIEW_ALERTS_LIMIT",
    "OVERVIEW_DEFAULT_DAYS",
    "OVERVIEW_TOP_VIDEOS",
    "KpiValue",
    "Overview",
    "OverviewFilters",
    "OverviewKpis",
    "OverviewOnboarding",
    "OverviewQuery",
    "OverviewTodo",
]

OVERVIEW_DEFAULT_DAYS = 7
OVERVIEW_TOP_VIDEOS = 10
OVERVIEW_ALERTS_LIMIT = 5


class OverviewFilters(BaseModel):
    date_from: date | None = Field(default=None, description="YYYY-MM-DD включительно, business_timezone")
    date_to: date | None = Field(default=None, description="YYYY-MM-DD включительно")
    platform: list[Platform] = Field(default_factory=list)
    channel_id: list[Slug] = Field(default_factory=list)
    product_id: list[Slug] = Field(default_factory=list)
    basis: AnalyticsBasis = AnalyticsBasis.PUBLIC

    @model_validator(mode="after")
    def _range(self) -> OverviewFilters:
        if self.date_from and self.date_to:
            if self.date_from > self.date_to:
                raise ValueError("date_from позже date_to")
            if (self.date_to - self.date_from).days + 1 > ANALYTICS_MAX_DAYS:
                raise ValueError(f"период длиннее {ANALYTICS_MAX_DAYS} дней")
        return self


class OverviewQuery(OverviewFilters):
    """Параметры `GET /overview` (query-модель FastAPI)."""


OverviewQueryParams = Annotated[OverviewQuery, Query()]


class KpiValue(ApiModel):
    value: float | None
    previous: float | None = Field(description="За прошлый период той же длины")
    delta: float | None = Field(description="value − previous")
    delta_pct: float | None = Field(description="delta / previous × 100; previous = 0 → null")


class OverviewKpis(ApiModel):
    """Имена полей — коды `AnalyticsMetric` (подписи и формулы — там же)."""

    views: KpiValue
    clicks: KpiValue
    visitors: KpiValue
    bot_starts: KpiValue
    users: KpiValue
    signups: KpiValue
    first_payments: KpiValue
    revenue_minor: KpiValue
    spend_usd: KpiValue
    videos_published: KpiValue
    ctr_per_1k_views: KpiValue
    cost_per_visitor_usd: KpiValue
    cost_per_user_usd: KpiValue
    cost_per_first_payment_usd: KpiValue
    cost_per_published_video_usd: KpiValue


class OverviewTodo(ApiModel):
    """«Требует тебя»: счётчики, по клику — переход на экран с фильтром."""

    pending_review: int
    qc_failed: int
    packages_ready: int
    packages_awaiting_url: int = Field(description="Скачаны, но ссылка на публикацию не вставлена")
    unverified_publications: int = Field(description="verify_status не ok (кроме pending моложе часа)")
    manual_metrics_due: int = Field(description="Публикации ждут ручного ввода метрик")
    verdicts_72h_ready: int = Field(description="Ролики, достигшие 72 ч за последние сутки")
    experiments_ready: int = Field(description="Кампании с готовым вердиктом A/B")


class OverviewOnboarding(ApiModel):
    """Чек-лист первого запуска: показывается, пока `done=false`."""

    products: int
    channels: int
    publications: int
    conversions: int = Field(description="Принятых (не тестовых) событий от продуктов")
    youtube_key_set: bool = Field(description="Задан ключ YouTube Data API (общий или куста)")
    webhook_secret_set: bool = Field(description="Хотя бы у одного продукта задан секрет вебхука")
    done: bool


class Overview(ApiModel):
    range: DateRange
    previous_range: DateRange
    filters: OverviewFilters
    basis_fallback: bool
    kpi: OverviewKpis
    series: list[MetricSeries] = Field(description="Дневные точки KPI для спарклайнов")
    funnel: FunnelBranches
    todo: OverviewTodo
    top_videos: list[BreakdownRow] = Field(
        description="Топ-10 роликов по просмотрам за период (dimension=video)"
    )
    alerts: list[Alert] = Field(description="Открытые, сначала критичные, не больше 5")
    alerts_open: int
    budget: Budget
    data_freshness: DataFreshness
    onboarding: OverviewOnboarding
    annotations: list[ChartAnnotation]
