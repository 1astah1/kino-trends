"""Аналитика: сводка, ряды, разрезы, воронка, когорты, стоимость, карточка ролика, CSV
(docs/ARCHITECTURE.md §5.2 «Аналитика», §6.6, §6.7, §7.3; контракт M4-0).

Реализация — M4-B (`analytics/{formulas,stats,rollups}.py`, `repos/analytics.py`,
`services/analytics.py`, `api/routers/analytics.py`), тег `analytics`, все GET — viewer+:

* `GET /analytics/summary` `AnalyticsQuery` → `AnalyticsSummary` (итоги, прошлый период той же длины,
  уровни атрибуции, `data_freshness`);
* `GET /analytics/timeseries` `TimeseriesQuery` → `AnalyticsTimeseries` (хвост групп сворачивается
  в `other`);
* `GET /analytics/breakdown` `BreakdownQuery` → `AnalyticsBreakdown`
  (`dimension=video|channel|niche|product|campaign|variant|track|platform`; `video` — лидерборд роликов);
* `GET /analytics/funnel` `AnalyticsQuery` → `AnalyticsFunnel` (две ветки: домен и бот);
* `GET /analytics/cohorts` `CohortsQuery` → `AnalyticsCohorts` (тепловая карта: неделя публикации × возраст);
* `GET /analytics/costs` `CostsQuery` → `AnalyticsCosts` (честная цена опубликованного ролика);
* `GET /analytics/freshness` → `DataFreshness`;
* `GET /analytics/videos/{publication_id}` `VideoAnalyticsQuery` → `VideoAnalytics` · 404
  publication_not_found;
* `GET /analytics/export` `ExportQuery` → `text/csv` (UTF-8 с BOM, `encode_csv`), имя файла
  `csv_filename`; 422, если отчёту не хватает параметра (experiment — ровно один `campaign_id`,
  video — `publication_id`).

Определения (§6.6, §6.7; формулы — `analytics/formulas.py`, M4-B):

* **Режимы.** `activity` — всё, что случилось в периоде (просмотры — `views_delta` по бизнес-дням,
  клики и конверсии — по времени события, расход — по времени вызова). `cohort` — ролики,
  опубликованные в периоде, и всё, что они набрали к возрасту `cohort_age` (`lifetime` — к сегодняшнему
  дню); ролики моложе `cohort_age` считаются незрелыми (`CohortInfo.immature`).
* **Бизнес-день** — `business_timezone` (Europe/Moscow) через БД: `(ts AT TIME ZONE tz)::date`.
* **Просмотры.** `basis=public` — публичные счётчики (с 24.08.2026 завышены на 20–30%, опорная линия
  `YT_PUBLIC_VIEWS_CHANGE_DATE`); `engaged` — engagedViews из YouTube Analytics (OAuth, M4-D, не в этом
  прогоне): пока их нет, берутся публичные и `basis_fallback=true`.
* **Клики** `clicks` = входы в редиректор (`landing` + `direct`), без ботов. **Посетители**
  `visitors = unique_visitors + direct_bot_starts` (старты бота без `_l` — не через лендинг).
  **Пользователи** `users` = уникальные `user_ref_hash` среди `bot_start`.
* **Атрибуция** (`AttributionLevel`): `exact` — код пакета или алиас `yt_`/`vk_` → ролик; `channel` —
  ссылка канала; `direct` — голый домен, неизвестный код, событие без тега. В разрез роликов
  попадает только `exact`; `channel` и `direct` видны в `attribution` сводки и воронки.
* **Расход.** `spend_usd` — `generation_events` роликов в разрезе (ASR и LLM источника размазываются
  по отрендеренным клипам, бриф — по роликам брифа); без ролика — `overhead_usd`, в итог — только при
  `include_overhead=true`. `cost_per_published_video_usd` = расход **всех** роликов периода, включая
  отклонённые и упавшие, / число опубликованных (ТЗ §8). Рубли — по `fx_usd_rub`.
* **Доли.** `ctr_per_1k_views = visitors / views × 1000`; `cost_per_* = spend_usd / знаменатель`;
  знаменатель 0 → `null`, а не 0 и не бесконечность.
"""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import Query
from pydantic import BaseModel, Field, model_validator

from autoshorts.api.schemas.common import ApiModel, Currency, Slug
from autoshorts.api.schemas.jobs import BannerVariantRef, ChannelRef
from autoshorts.api.schemas.metrics import CollectorStatus, MetricsSnapshot
from autoshorts.api.schemas.publications import AgeVerdict, Publication
from autoshorts.api.schemas.quota import QuotaDay
from autoshorts.api.schemas.tracking import TrafficGranularity
from autoshorts.domain.enums import (
    AnalyticsBasis,
    AnalyticsMode,
    ConversionEventType,
    JobKind,
    Platform,
    PublicationStatus,
)

__all__ = [
    "ANALYTICS_DEFAULT_DAYS",
    "ANALYTICS_MAX_DAYS",
    "CSV_BOM",
    "CSV_MEDIA_TYPE",
    "DIMENSION_LABELS",
    "METRIC_LABELS",
    "NONE_KEY",
    "OTHER_KEY",
    "TIMESERIES_TOP_DEFAULT",
    "TIMESERIES_TOP_MAX",
    "YT_PUBLIC_VIEWS_CHANGE_DATE",
    "AgePoint",
    "AgeViewsPoint",
    "AnalyticsBreakdown",
    "AnalyticsCohorts",
    "AnalyticsCosts",
    "AnalyticsDimension",
    "AnalyticsFilters",
    "AnalyticsFunnel",
    "AnalyticsGranularity",
    "AnalyticsGroupBy",
    "AnalyticsMetric",
    "AnalyticsQuery",
    "AnalyticsReport",
    "AnalyticsSummary",
    "AnalyticsTimeseries",
    "AnalyticsTotals",
    "AttributionSplit",
    "AttributionTotals",
    "BreakdownQuery",
    "BreakdownRow",
    "BreakdownVideo",
    "ChartAnnotation",
    "CohortAge",
    "CohortAggregate",
    "CohortCounts",
    "CohortInfo",
    "CohortMetric",
    "CohortRow",
    "CohortValues",
    "CohortsQuery",
    "ConversionCount",
    "CostGroupBy",
    "CostGroupRow",
    "CostItem",
    "CostTotals",
    "CostsQuery",
    "CsvDialect",
    "DataFreshness",
    "DateRange",
    "ExportQuery",
    "FreshnessWarning",
    "FreshnessWarningCode",
    "FunnelBranches",
    "FunnelCosts",
    "FunnelStep",
    "FunnelStepCode",
    "JobOutcome",
    "LadderStep",
    "LadderStepStatus",
    "MetricSeries",
    "MetricUnit",
    "TimeseriesPoint",
    "TimeseriesQuery",
    "TimeseriesSeries",
    "VideoAnalytics",
    "VideoAnalyticsQuery",
    "VideoConversionEvent",
    "VideoCosts",
    "VideoTrafficPoint",
    "content_disposition",
    "csv_filename",
    "encode_csv",
    "resolve_range",
]

# ---------------------------------------------------------------- константы

ANALYTICS_DEFAULT_DAYS = 28
"""Период по умолчанию (пусто в `date_from`/`date_to`): последние 28 бизнес-дней, включая сегодня."""
ANALYTICS_MAX_DAYS = 400
YT_PUBLIC_VIEWS_CHANGE_DATE = date(2026, 8, 24)
"""С этого дня публичные просмотры YouTube Shorts завышены на 20–30% — опорная линия на всех рядах."""
TIMESERIES_TOP_DEFAULT = 8
TIMESERIES_TOP_MAX = 20
OTHER_KEY = "other"
"""Ключ свёрнутого хвоста групп (ряды и разрезы)."""
NONE_KEY = "none"
"""Ключ группы без значения измерения (ролик без кампании, без варианта …)."""
BREAKDOWN_LIMIT_MAX = 500

# ---------------------------------------------------------------- перечисления


class AnalyticsDimension(StrEnum):
    """Измерение разреза; `track` — трек ролика (`jobs.kind`: generate | clip), `variant` — баннер A/B."""

    VIDEO = "video"
    CHANNEL = "channel"
    NICHE = "niche"
    PRODUCT = "product"
    CAMPAIGN = "campaign"
    VARIANT = "variant"
    TRACK = "track"
    PLATFORM = "platform"


class AnalyticsGroupBy(StrEnum):
    """Группировка рядов: `none` — один ряд, иначе как `AnalyticsDimension`."""

    NONE = "none"
    VIDEO = "video"
    CHANNEL = "channel"
    NICHE = "niche"
    PRODUCT = "product"
    CAMPAIGN = "campaign"
    VARIANT = "variant"
    TRACK = "track"
    PLATFORM = "platform"


class AnalyticsGranularity(StrEnum):
    DAY = "day"
    WEEK = "week"  # точка — понедельник недели (ISO), бизнес-дни


class CohortAge(StrEnum):
    """Возраст ролика, к которому считается когорта (`mode=cohort`)."""

    H24 = "h24"
    H72 = "h72"
    D7 = "d7"
    D14 = "d14"
    D30 = "d30"
    LIFETIME = "lifetime"


COHORT_AGE_HOURS: dict[CohortAge, int | None] = {
    CohortAge.H24: 24,
    CohortAge.H72: 72,
    CohortAge.D7: 168,
    CohortAge.D14: 336,
    CohortAge.D30: 720,
    CohortAge.LIFETIME: None,
}


class AgePoint(StrEnum):
    """Точки кривой «просмотры от возраста» в карточке ролика."""

    H1 = "h1"
    H2 = "h2"
    H4 = "h4"
    H8 = "h8"
    H12 = "h12"
    H24 = "h24"
    H48 = "h48"
    H72 = "h72"
    D7 = "d7"
    D14 = "d14"
    D30 = "d30"


AGE_POINT_HOURS: dict[AgePoint, int] = {
    AgePoint.H1: 1,
    AgePoint.H2: 2,
    AgePoint.H4: 4,
    AgePoint.H8: 8,
    AgePoint.H12: 12,
    AgePoint.H24: 24,
    AgePoint.H48: 48,
    AgePoint.H72: 72,
    AgePoint.D7: 168,
    AgePoint.D14: 336,
    AgePoint.D30: 720,
}


class MetricUnit(StrEnum):
    COUNT = "count"
    USD = "usd"
    RUB = "rub"
    RUB_MINOR = "rub_minor"  # копейки: revenue_minor
    RATIO = "ratio"  # 0..1
    PER_1K = "per_1k"


class AnalyticsMetric(StrEnum):
    """Числовые поля `AnalyticsTotals` — всё, что можно вывести рядом, отсортировать и выгрузить."""

    VIDEOS_PRODUCED = "videos_produced"
    VIDEOS_PUBLISHED = "videos_published"
    VIDEOS_REJECTED = "videos_rejected"
    VIDEOS_FAILED = "videos_failed"
    VIEWS = "views"
    ENGAGED_VIEWS = "engaged_views"
    LIKES = "likes"
    COMMENTS = "comments"
    CLICKS = "clicks"
    LANDING_VIEWS = "landing_views"
    UNIQUE_VISITORS = "unique_visitors"
    TO_TELEGRAM = "to_telegram"
    TO_SITE = "to_site"
    DIRECT_BOT_STARTS = "direct_bot_starts"
    VISITORS = "visitors"
    BOT_STARTS = "bot_starts"
    USERS = "users"
    TMA_OPENS = "tma_opens"
    SIGNUPS = "signups"
    FIRST_PAYMENTS = "first_payments"
    PAYMENTS = "payments"
    REVENUE_MINOR = "revenue_minor"
    SPEND_USD = "spend_usd"
    SPEND_RUB = "spend_rub"
    OVERHEAD_USD = "overhead_usd"
    REJECTED_SPEND_USD = "rejected_spend_usd"
    CTR_PER_1K_VIEWS = "ctr_per_1k_views"
    CR_VISITOR_TO_START = "cr_visitor_to_start"
    CR_START_TO_PAYMENT = "cr_start_to_payment"
    COST_PER_VISITOR_USD = "cost_per_visitor_usd"
    COST_PER_USER_USD = "cost_per_user_usd"
    COST_PER_BOT_START_USD = "cost_per_bot_start_usd"
    COST_PER_FIRST_PAYMENT_USD = "cost_per_first_payment_usd"
    COST_PER_PUBLISHED_VIDEO_USD = "cost_per_published_video_usd"
    COST_PER_VIDEO_USD = "cost_per_video_usd"
    ROMI = "romi"


METRIC_LABELS: dict[AnalyticsMetric, tuple[str, MetricUnit]] = {
    AnalyticsMetric.VIDEOS_PRODUCED: ("Сделано роликов", MetricUnit.COUNT),
    AnalyticsMetric.VIDEOS_PUBLISHED: ("Опубликовано", MetricUnit.COUNT),
    AnalyticsMetric.VIDEOS_REJECTED: ("Отклонено", MetricUnit.COUNT),
    AnalyticsMetric.VIDEOS_FAILED: ("Сбоев", MetricUnit.COUNT),
    AnalyticsMetric.VIEWS: ("Просмотры", MetricUnit.COUNT),
    AnalyticsMetric.ENGAGED_VIEWS: ("Вовлечённые просмотры", MetricUnit.COUNT),
    AnalyticsMetric.LIKES: ("Лайки", MetricUnit.COUNT),
    AnalyticsMetric.COMMENTS: ("Комментарии", MetricUnit.COUNT),
    AnalyticsMetric.CLICKS: ("Переходы по ссылке", MetricUnit.COUNT),
    AnalyticsMetric.LANDING_VIEWS: ("Показы лендинга", MetricUnit.COUNT),
    AnalyticsMetric.UNIQUE_VISITORS: ("Уникальные посетители лендинга", MetricUnit.COUNT),
    AnalyticsMetric.TO_TELEGRAM: ("В Telegram", MetricUnit.COUNT),
    AnalyticsMetric.TO_SITE: ("На сайт", MetricUnit.COUNT),
    AnalyticsMetric.DIRECT_BOT_STARTS: ("Старты бота напрямую", MetricUnit.COUNT),
    AnalyticsMetric.VISITORS: ("Посетители", MetricUnit.COUNT),
    AnalyticsMetric.BOT_STARTS: ("Старты бота", MetricUnit.COUNT),
    AnalyticsMetric.USERS: ("Пользователи", MetricUnit.COUNT),
    AnalyticsMetric.TMA_OPENS: ("Открытия Mini App", MetricUnit.COUNT),
    AnalyticsMetric.SIGNUPS: ("Регистрации", MetricUnit.COUNT),
    AnalyticsMetric.FIRST_PAYMENTS: ("Первые оплаты", MetricUnit.COUNT),
    AnalyticsMetric.PAYMENTS: ("Оплаты", MetricUnit.COUNT),
    AnalyticsMetric.REVENUE_MINOR: ("Выручка, коп.", MetricUnit.RUB_MINOR),
    AnalyticsMetric.SPEND_USD: ("Расход, $", MetricUnit.USD),
    AnalyticsMetric.SPEND_RUB: ("Расход, ₽", MetricUnit.RUB),
    AnalyticsMetric.OVERHEAD_USD: ("Накладные, $", MetricUnit.USD),
    AnalyticsMetric.REJECTED_SPEND_USD: ("Расход на брак, $", MetricUnit.USD),
    AnalyticsMetric.CTR_PER_1K_VIEWS: ("Посетители на 1000 просмотров", MetricUnit.PER_1K),
    AnalyticsMetric.CR_VISITOR_TO_START: ("Конверсия посетитель → старт", MetricUnit.RATIO),
    AnalyticsMetric.CR_START_TO_PAYMENT: ("Конверсия старт → оплата", MetricUnit.RATIO),
    AnalyticsMetric.COST_PER_VISITOR_USD: ("Цена посетителя, $", MetricUnit.USD),
    AnalyticsMetric.COST_PER_USER_USD: ("Цена пользователя, $", MetricUnit.USD),
    AnalyticsMetric.COST_PER_BOT_START_USD: ("Цена старта бота, $", MetricUnit.USD),
    AnalyticsMetric.COST_PER_FIRST_PAYMENT_USD: ("Цена первой оплаты, $", MetricUnit.USD),
    AnalyticsMetric.COST_PER_PUBLISHED_VIDEO_USD: ("Цена опубликованного ролика, $", MetricUnit.USD),
    AnalyticsMetric.COST_PER_VIDEO_USD: ("Цена сделанного ролика, $", MetricUnit.USD),
    AnalyticsMetric.ROMI: ("ROMI", MetricUnit.RATIO),
}
"""Подписи и единицы: заголовки CSV (`dialect=excel_ru`) и сверка с `web/lib/metric-definitions.ts`."""

DIMENSION_LABELS: dict[AnalyticsDimension, str] = {
    AnalyticsDimension.VIDEO: "Ролик",
    AnalyticsDimension.CHANNEL: "Канал",
    AnalyticsDimension.NICHE: "Ниша",
    AnalyticsDimension.PRODUCT: "Продукт",
    AnalyticsDimension.CAMPAIGN: "Кампания",
    AnalyticsDimension.VARIANT: "Вариант баннера",
    AnalyticsDimension.TRACK: "Трек",
    AnalyticsDimension.PLATFORM: "Площадка",
}


class FunnelStepCode(StrEnum):
    VIEWS = "views"
    CLICKS = "clicks"
    LANDING_VIEWS = "landing_views"
    UNIQUE_VISITORS = "unique_visitors"
    TO_TELEGRAM = "to_telegram"
    TO_SITE = "to_site"
    DIRECT_BOT_STARTS = "direct_bot_starts"
    VISITORS = "visitors"
    BOT_STARTS = "bot_starts"
    USERS = "users"
    SIGNUPS = "signups"
    FIRST_PAYMENTS = "first_payments"


class CostGroupBy(StrEnum):
    PROVIDER = "provider"
    STEP = "step"  # generation_events.step_name
    TRACK = "track"
    OUTCOME = "outcome"  # чем кончился ролик: JobOutcome
    CHANNEL = "channel"
    NICHE = "niche"
    PRODUCT = "product"


class JobOutcome(StrEnum):
    """Исход ролика для разреза стоимости (честная цена: брак тоже стоит денег)."""

    PUBLISHED = "published"
    APPROVED = "approved"  # одобрен, ещё не залит
    IN_PROGRESS = "in_progress"  # в работе или ждёт приёмки
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"
    OVERHEAD = "overhead"  # расход без ролика: трендоскаут, ASR/LLM источника без клипов


class CohortMetric(StrEnum):
    VIEWS = "views"
    VISITORS = "visitors"
    BOT_STARTS = "bot_starts"
    USERS = "users"
    FIRST_PAYMENTS = "first_payments"
    REVENUE_MINOR = "revenue_minor"
    CTR_PER_1K_VIEWS = "ctr_per_1k_views"


class CohortAggregate(StrEnum):
    MEDIAN = "median"  # медиана по роликам когорты (по умолчанию: не среднее, §7.3)
    SUM = "sum"
    MEAN = "mean"


class FreshnessWarningCode(StrEnum):
    COLLECTOR_DISABLED = "collector_disabled"
    COLLECTOR_FAILING = "collector_failing"
    QUOTA_EXHAUSTED = "quota_exhausted"
    METRICS_OVERDUE = "metrics_overdue"
    MANUAL_INPUT_DUE = "manual_input_due"
    ROLLUP_STALE = "rollup_stale"
    ENGAGED_VIEWS_UNAVAILABLE = "engaged_views_unavailable"
    PUBLIC_VIEWS_INFLATED = "public_views_inflated"


class ChartAnnotationCode(StrEnum):
    YT_PUBLIC_VIEWS_CHANGE = "yt_public_views_change"


class LadderStepStatus(StrEnum):
    DONE = "done"  # снимок есть
    DUE = "due"  # время пришло, снимка ещё нет
    MISSED = "missed"  # пропущен (коллектор лежал) — точка будет интерполирована
    FUTURE = "future"


class AnalyticsReport(StrEnum):
    """`GET /analytics/export?report=…`."""

    SUMMARY = "summary"
    TIMESERIES = "timeseries"
    BREAKDOWN = "breakdown"
    FUNNEL = "funnel"
    COHORTS = "cohorts"
    COSTS = "costs"
    EXPERIMENT = "experiment"  # ровно один campaign_id
    VIDEO = "video"  # снимки одного ролика: publication_id


class CsvDialect(StrEnum):
    """`excel_ru` — для Excel с русской локалью: `;`, десятичная запятая, русские заголовки, время МСК.
    `rfc4180` — для машин: `,`, точка, заголовки-ключи латиницей, время ISO 8601 UTC с `Z`.
    Оба — UTF-8 с BOM и переводом строки CRLF."""

    EXCEL_RU = "excel_ru"
    RFC4180 = "rfc4180"


# ---------------------------------------------------------------- фильтры (query-модели FastAPI)


def resolve_range(
    date_from: date | None, date_to: date | None, today: date, *, default_days: int = ANALYTICS_DEFAULT_DAYS
) -> tuple[date, date]:
    """Период запроса: пусто — последние `default_days` бизнес-дней по `today` включительно;
    только одна граница — вторая достраивается на `default_days`."""
    if date_from is None and date_to is None:
        return today - timedelta(days=default_days - 1), today
    if date_from is None:
        assert date_to is not None
        return date_to - timedelta(days=default_days - 1), date_to
    if date_to is None:
        return date_from, max(date_from, min(today, date_from + timedelta(days=default_days - 1)))
    return date_from, date_to


def previous_range(date_from: date, date_to: date) -> tuple[date, date]:
    """Прошлый период той же длины, вплотную перед текущим (для дельт KPI)."""
    days = (date_to - date_from).days + 1
    return date_from - timedelta(days=days), date_from - timedelta(days=1)


class AnalyticsFilters(BaseModel):
    """Общие фильтры аналитики (§5.2). Мультифильтры — повтором: `?channel_id=a&channel_id=b`."""

    date_from: date | None = Field(default=None, description="YYYY-MM-DD включительно, business_timezone")
    date_to: date | None = Field(default=None, description="YYYY-MM-DD включительно")
    platform: list[Platform] = Field(default_factory=list)
    channel_id: list[Slug] = Field(default_factory=list)
    niche_id: list[Slug] = Field(default_factory=list)
    product_id: list[Slug] = Field(default_factory=list)
    campaign_id: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list)
    banner_variant_id: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list)
    track: list[JobKind] = Field(default_factory=list, description="Трек: generate | clip")
    mode: AnalyticsMode = Field(default=AnalyticsMode.ACTIVITY, description="activity | cohort")
    basis: AnalyticsBasis = Field(
        default=AnalyticsBasis.PUBLIC, description="public | engaged (engaged — только с OAuth, M4-D)"
    )
    cohort_age: CohortAge = Field(default=CohortAge.LIFETIME, description="Только для mode=cohort")
    include_overhead: bool = Field(default=False, description="Накладные (без ролика) в spend_usd")

    @model_validator(mode="after")
    def _range(self) -> AnalyticsFilters:
        if self.date_from and self.date_to:
            if self.date_from > self.date_to:
                raise ValueError("date_from позже date_to")
            if (self.date_to - self.date_from).days + 1 > ANALYTICS_MAX_DAYS:
                raise ValueError(f"период длиннее {ANALYTICS_MAX_DAYS} дней")
        return self


class AnalyticsQuery(AnalyticsFilters):
    """`/analytics/summary`, `/analytics/funnel`."""


class TimeseriesQuery(AnalyticsFilters):
    metric: AnalyticsMetric = AnalyticsMetric.VIEWS
    granularity: AnalyticsGranularity = AnalyticsGranularity.DAY
    group_by: AnalyticsGroupBy = AnalyticsGroupBy.NONE
    top: int = Field(default=TIMESERIES_TOP_DEFAULT, ge=1, le=TIMESERIES_TOP_MAX)


class BreakdownQuery(AnalyticsFilters):
    dimension: AnalyticsDimension = AnalyticsDimension.CHANNEL
    sort: str = Field(
        default="-views",
        pattern=r"^-?[a-z0-9_]+$",
        description="Метрика AnalyticsMetric или n_videos, views_median, views_72h_median, label, "
        "published_at (только video); `-` — по убыванию",
    )
    limit: int = Field(default=50, ge=1, le=BREAKDOWN_LIMIT_MAX)
    offset: int = Field(default=0, ge=0)


class CohortsQuery(AnalyticsFilters):
    metric: CohortMetric = CohortMetric.VIEWS
    granularity: AnalyticsGranularity = Field(
        default=AnalyticsGranularity.WEEK, description="Когорта — день или неделя публикации"
    )
    aggregate: CohortAggregate = CohortAggregate.MEDIAN


class CostsQuery(AnalyticsFilters):
    granularity: AnalyticsGranularity = AnalyticsGranularity.DAY
    group_by: CostGroupBy = CostGroupBy.PROVIDER
    top: int = Field(default=TIMESERIES_TOP_DEFAULT, ge=1, le=TIMESERIES_TOP_MAX)


class VideoAnalyticsQuery(BaseModel):
    basis: AnalyticsBasis = AnalyticsBasis.PUBLIC
    traffic_granularity: TrafficGranularity | None = Field(
        default=None, description="Пусто — hour для роликов младше 7 дней, иначе day"
    )


class ExportQuery(AnalyticsFilters):
    """Параметры отчёта — как у одноимённого эндпоинта; лишние игнорируются."""

    report: AnalyticsReport = AnalyticsReport.BREAKDOWN
    dialect: CsvDialect = CsvDialect.EXCEL_RU
    dimension: AnalyticsDimension = AnalyticsDimension.VIDEO
    metric: AnalyticsMetric = AnalyticsMetric.VIEWS
    granularity: AnalyticsGranularity = AnalyticsGranularity.DAY
    group_by: AnalyticsGroupBy = AnalyticsGroupBy.NONE
    cost_group_by: CostGroupBy = CostGroupBy.PROVIDER
    cohort_metric: CohortMetric = CohortMetric.VIEWS
    top: int = Field(default=TIMESERIES_TOP_DEFAULT, ge=1, le=TIMESERIES_TOP_MAX)
    sort: str = Field(default="-views", pattern=r"^-?[a-z0-9_]+$")
    publication_id: int | None = Field(default=None, ge=1, description="report=video")


AnalyticsQueryParams = Annotated[AnalyticsQuery, Query()]
TimeseriesQueryParams = Annotated[TimeseriesQuery, Query()]
BreakdownQueryParams = Annotated[BreakdownQuery, Query()]
CohortsQueryParams = Annotated[CohortsQuery, Query()]
CostsQueryParams = Annotated[CostsQuery, Query()]
VideoAnalyticsQueryParams = Annotated[VideoAnalyticsQuery, Query()]
ExportQueryParams = Annotated[ExportQuery, Query()]

# ---------------------------------------------------------------- общие блоки ответов


class DateRange(ApiModel):
    date_from: date
    date_to: date
    days: int
    timezone: str = Field(description="business_timezone, в котором считаются дни")


class ChartAnnotation(ApiModel):
    """Опорная линия на временных графиках."""

    date: date
    code: ChartAnnotationCode
    label: str


class FreshnessWarning(ApiModel):
    code: FreshnessWarningCode
    message: str = Field(description="Для человека, по-русски")


class DataFreshness(ApiModel):
    """Плашка свежести (§7.3 «Аналитика»): насколько цифрам можно верить прямо сейчас."""

    computed_at: datetime
    metrics_polled_at: datetime | None = Field(description="Последний успешный опрос любой публикации")
    metrics_oldest_due_at: datetime | None = Field(description="Самый старый просроченный next_poll_at")
    public_counts_age_min: int | None = Field(
        description="Медианный возраст последнего снимка живых публикаций, минут"
    )
    publications_stale: int
    publications_manual_due: int
    rollup_at: datetime | None = Field(description="Последний успешный stats_rollup")
    clicks_last_at: datetime | None
    conversions_last_at: datetime | None
    analytics_pulled_at: datetime | None = Field(
        description="YouTube Analytics (OAuth, M4-D) — в этом прогоне всегда null"
    )
    collectors: list[CollectorStatus]
    quota: list[QuotaDay] = Field(description="Текущие сутки квоты")
    stale: bool = Field(description="Есть предупреждения, из-за которых цифры неполные")
    warnings: list[FreshnessWarning]


class AnalyticsTotals(ApiModel):
    """Итоги по набору роликов и периоду. Доли и цены — `null`, если знаменатель 0."""

    # производство
    videos_produced: int = Field(description="Роликов дошло до приёмки (включая брак)")
    videos_published: int
    videos_rejected: int
    videos_failed: int
    # просмотры
    views: int
    engaged_views: int | None = Field(description="Только при OAuth (M4-D)")
    likes: int
    comments: int
    # трафик (без ботов)
    clicks: int = Field(description="Входы в редиректор: landing + direct")
    landing_views: int
    unique_visitors: int
    to_telegram: int
    to_site: int
    direct_bot_starts: int = Field(description="Старты бота без _l (не через лендинг)")
    visitors: int = Field(description="unique_visitors + direct_bot_starts")
    # конверсии
    bot_starts: int
    users: int = Field(description="Уникальные пользователи бота (user_ref_hash у bot_start)")
    tma_opens: int
    signups: int
    first_payments: int
    payments: int
    revenue_minor: int
    currency: Currency | None = Field(description="Валюта выручки (RUB)")
    # деньги
    spend_usd: float
    spend_rub: float
    overhead_usd: float = Field(description="Расход без ролика; в spend_usd — только при include_overhead")
    rejected_spend_usd: float = Field(description="Расход на отклонённые, упавшие и отменённые")
    # доли и цены
    ctr_per_1k_views: float | None
    cr_visitor_to_start: float | None
    cr_start_to_payment: float | None
    cost_per_visitor_usd: float | None
    cost_per_user_usd: float | None
    cost_per_bot_start_usd: float | None
    cost_per_first_payment_usd: float | None
    cost_per_published_video_usd: float | None = Field(
        description="Расход всех роликов периода (включая брак) / опубликованные"
    )
    cost_per_video_usd: float | None = Field(description="Расход / сделанные ролики")
    romi: float | None = Field(description="(выручка₽ − расход₽) / расход₽")


class AttributionTotals(ApiModel):
    visitors: int
    bot_starts: int
    users: int
    signups: int
    first_payments: int
    revenue_minor: int


class AttributionSplit(ApiModel):
    """Уровни атрибуции (§6.6): в разрезы роликов попадает только exact."""

    exact: AttributionTotals
    channel: AttributionTotals
    direct: AttributionTotals


class CohortInfo(ApiModel):
    age: CohortAge
    videos: int = Field(description="Роликов опубликовано в периоде")
    videos_mature: int = Field(description="Из них достигли возраста age")
    immature: int


class TimeseriesPoint(ApiModel):
    t: date = Field(description="День или понедельник недели (business_timezone)")
    v: float | None


class TimeseriesSeries(ApiModel):
    key: str = Field(description="Значение группы; 'other' — свёрнутый хвост, 'none' — без значения")
    label: str
    total: float | None
    points: list[TimeseriesPoint]


class MetricSeries(ApiModel):
    metric: AnalyticsMetric
    points: list[TimeseriesPoint]


class FunnelStep(ApiModel):
    code: FunnelStepCode
    value: float | None
    rate_from_prev: float | None = Field(description="Доля от предыдущего шага ветки, 0..1")
    per_1k_views: float | None
    cost_per_usd: float | None = Field(description="spend_usd / value")


class FunnelBranches(ApiModel):
    """Ветка домена: просмотры → лендинг → посетители → на сайт → регистрации → оплаты.
    Ветка бота: просмотры → в Telegram + старты напрямую → старты → пользователи → оплаты."""

    domain: list[FunnelStep]
    bot: list[FunnelStep]


class FunnelCosts(ApiModel):
    spend_usd: float
    cost_per_visitor_usd: float | None
    cost_per_user_usd: float | None
    cost_per_bot_start_usd: float | None
    cost_per_first_payment_usd: float | None


# ---------------------------------------------------------------- ответы эндпоинтов


class AnalyticsSummary(ApiModel):
    range: DateRange
    previous_range: DateRange
    filters: AnalyticsFilters
    basis_fallback: bool = Field(description="Просили engaged, а данных нет: посчитано по public")
    totals: AnalyticsTotals
    previous: AnalyticsTotals
    attribution: AttributionSplit
    cohort: CohortInfo | None = Field(description="Только mode=cohort")
    data_freshness: DataFreshness
    annotations: list[ChartAnnotation]


class AnalyticsTimeseries(ApiModel):
    metric: AnalyticsMetric
    granularity: AnalyticsGranularity
    group_by: AnalyticsGroupBy
    top: int
    range: DateRange
    filters: AnalyticsFilters
    basis_fallback: bool
    series: list[TimeseriesSeries]
    annotations: list[ChartAnnotation]


class BreakdownVideo(ApiModel):
    """Колонки лидерборда роликов (`dimension=video`)."""

    publication_id: int
    job_id: int
    video_uid: str
    title: str | None
    url: str | None
    platform: Platform
    platform_video_id: str | None
    channel: ChannelRef
    niche_id: str | None
    product_id: str | None
    campaign_id: int | None
    banner_variant: BannerVariantRef | None
    track: JobKind
    status: PublicationStatus
    published_at: datetime | None
    age_hours: float | None
    latest_views: int | None
    views_24h: int | None
    views_72h: int | None
    views_7d: int | None
    verdict: AgeVerdict | None
    poster_url: str | None = Field(description="/api/v1/jobs/{job_id}/media/poster")
    stale: bool
    manual_due: bool


class BreakdownRow(ApiModel):
    key: str = Field(
        description="Значение измерения (для video — publication_id строкой); 'none' — без значения"
    )
    label: str
    n_videos: int
    n_mature: int = Field(description="Роликов старше ab_min_age_hours")
    metrics: AnalyticsTotals
    views_median: float | None = Field(description="Медиана просмотров на ролик в выбранном режиме")
    views_p25: float | None
    views_p75: float | None
    views_72h_median: float | None
    share_views: float | None
    share_spend: float | None
    needs_more_n: bool = Field(description="n_mature < ab_min_videos_per_variant: сравнивать рано")
    p_value: float | None = Field(description="Манн–Уитни ln(views_72h): группа против остальных")
    video: BreakdownVideo | None = Field(description="Только dimension=video")


class AnalyticsBreakdown(ApiModel):
    dimension: AnalyticsDimension
    range: DateRange
    filters: AnalyticsFilters
    basis_fallback: bool
    sort: str
    total_rows: int
    limit: int
    offset: int
    min_videos_for_stats: int = Field(description="ab_min_videos_per_variant")
    rows: list[BreakdownRow]
    totals: AnalyticsTotals


class AnalyticsFunnel(ApiModel):
    range: DateRange
    filters: AnalyticsFilters
    basis_fallback: bool
    views: int
    branches: FunnelBranches
    attribution: AttributionSplit
    costs: FunnelCosts


class CohortValues(ApiModel):
    h24: float | None
    h72: float | None
    d7: float | None
    d14: float | None
    d30: float | None


class CohortCounts(ApiModel):
    """Сколько роликов когорты уже достигли возраста (незрелые в значение не входят)."""

    h24: int
    h72: int
    d7: int
    d14: int
    d30: int


class CohortRow(ApiModel):
    cohort: date = Field(description="День или понедельник недели публикации")
    n_videos: int
    values: CohortValues
    mature: CohortCounts


class AnalyticsCohorts(ApiModel):
    metric: CohortMetric
    granularity: AnalyticsGranularity
    aggregate: CohortAggregate
    range: DateRange
    filters: AnalyticsFilters
    basis_fallback: bool
    rows: list[CohortRow]


class CostTotals(ApiModel):
    spend_usd: float
    spend_rub: float
    fx_usd_rub: float
    overhead_usd: float
    published_spend_usd: float
    rejected_spend_usd: float = Field(description="Отклонённые, упавшие и отменённые")
    in_progress_spend_usd: float
    videos_produced: int
    videos_published: int
    videos_rejected: int
    videos_failed: int
    cost_per_published_video_usd: float | None = Field(
        description="Расход всех роликов периода (включая брак) / опубликованные"
    )
    cost_per_video_usd: float | None


class CostGroupRow(ApiModel):
    key: str
    label: str
    spend_usd: float
    calls: int
    share: float | None


class AnalyticsCosts(ApiModel):
    range: DateRange
    filters: AnalyticsFilters
    granularity: AnalyticsGranularity
    group_by: CostGroupBy
    totals: CostTotals
    series: list[TimeseriesSeries] = Field(description="Расход по группам и дням/неделям, $")
    groups: list[CostGroupRow]
    annotations: list[ChartAnnotation]


# ---------------------------------------------------------------- карточка ролика


class LadderStep(ApiModel):
    step: int
    age_hours: float
    due_at: datetime | None
    captured_at: datetime | None
    status: LadderStepStatus


class AgeViewsPoint(ApiModel):
    """Просмотры на ровный возраст на фоне коридора p25–p75 канала (ролики канала за 90 дней)."""

    age: AgePoint
    age_hours: int
    views: float | None = Field(description="Линейная интерполяция между снимками; без экстраполяции")
    interpolated: bool
    reached: bool = Field(description="Ролик уже старше этого возраста")
    channel_p25: float | None
    channel_median: float | None
    channel_p75: float | None


class VideoTrafficPoint(ApiModel):
    t: datetime
    clicks: int
    landing_views: int
    unique_visitors: int
    to_telegram: int
    to_site: int
    direct_bot_starts: int
    bot_starts: int
    signups: int
    first_payments: int
    revenue_minor: int


class ConversionCount(ApiModel):
    event_type: ConversionEventType
    count: int
    unique_users: int
    via_landing: int = Field(description="Из них через лендинг (_l)")
    amount_minor: int


class VideoConversionEvent(ApiModel):
    occurred_at: datetime
    event_type: ConversionEventType
    via_landing: bool
    amount_minor: int | None
    currency: Currency | None


class CostItem(ApiModel):
    step_name: str
    provider: str
    calls: int
    cost_usd: float


class VideoCosts(ApiModel):
    job_cost_usd: float = Field(description="generation_events самого ролика")
    source_share_usd: float = Field(description="Доля ASR/LLM источника (нарезка)")
    brief_share_usd: float = Field(description="Доля разбора брифа трендоскаута")
    total_usd: float
    total_rub: float
    items: list[CostItem]
    cost_per_visitor_usd: float | None
    cost_per_user_usd: float | None
    cost_per_bot_start_usd: float | None
    cost_per_first_payment_usd: float | None
    revenue_minor: int
    romi: float | None


class VideoAnalytics(ApiModel):
    publication: Publication
    totals: AnalyticsTotals = Field(description="За всю жизнь ролика")
    snapshots: list[MetricsSnapshot] = Field(description="Все снимки по времени")
    ladder: list[LadderStep]
    age_views: list[AgeViewsPoint]
    traffic_granularity: TrafficGranularity
    traffic: list[VideoTrafficPoint]
    conversions: list[ConversionCount]
    recent_conversions: list[VideoConversionEvent] = Field(description="Последние 20 событий")
    funnel: FunnelBranches
    costs: VideoCosts
    annotations: list[ChartAnnotation]


# ---------------------------------------------------------------- CSV

CSV_BOM = "﻿"
CSV_MEDIA_TYPE = "text/csv"
"""Starlette дописывает `; charset=utf-8`: заголовок ответа — `text/csv; charset=utf-8`."""
CSV_LINE_TERMINATOR = "\r\n"
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
"""Текст, начинающийся с этих символов, Excel исполняет как формулу — такие ячейки экранируются `'`."""
_DIALECT_SEPARATORS: dict[CsvDialect, tuple[str, str]] = {
    CsvDialect.EXCEL_RU: (";", ","),  # (разделитель полей, десятичный разделитель)
    CsvDialect.RFC4180: (",", "."),
}


def _csv_number(value: float | int | Decimal, decimal_sep: str) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    dec = value if isinstance(value, Decimal) else Decimal(repr(value))
    if not dec.is_finite():
        return ""
    text = format(dec, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("-0", ""):
        text = "0"
    return text.replace(".", decimal_sep)


def _csv_cell(value: Any, dialect: CsvDialect, tz: str) -> str:
    _, decimal_sep = _DIALECT_SEPARATORS[dialect]
    if value is None:
        return ""
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, bool | int | float | Decimal):
        return _csv_number(value, decimal_sep)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        if dialect is CsvDialect.EXCEL_RU:
            return value.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M:%S")
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    if text.startswith(CSV_FORMULA_PREFIXES):
        text = "'" + text
    return text


def encode_csv(
    header: Sequence[str],
    rows: Iterable[Sequence[Any]],
    dialect: CsvDialect = CsvDialect.EXCEL_RU,
    *,
    tz: str = "Europe/Moscow",
) -> bytes:
    """Эталонный CSV выгрузок аналитики: UTF-8 с BOM, CRLF, кавычки по необходимости.

    Ячейки: `None` → пусто; bool → 1/0; числа без экспоненты, десятичный разделитель по диалекту;
    дата — `YYYY-MM-DD`; время — локальное `business_timezone` (`excel_ru`) или ISO UTC с `Z`
    (`rfc4180`); текст, похожий на формулу (`= + - @`), получает префикс `'`.
    """
    delimiter, _ = _DIALECT_SEPARATORS[dialect]
    buf = io.StringIO()
    writer = csv.writer(
        buf, delimiter=delimiter, lineterminator=CSV_LINE_TERMINATOR, quoting=csv.QUOTE_MINIMAL
    )
    writer.writerow([_csv_cell(h, dialect, tz) for h in header])
    width = len(header)
    for row in rows:
        cells = [_csv_cell(v, dialect, tz) for v in row]
        if len(cells) != width:
            raise ValueError(f"строка CSV из {len(cells)} ячеек, а заголовок из {width}")
        writer.writerow(cells)
    return (CSV_BOM + buf.getvalue()).encode("utf-8")


def csv_filename(
    report: AnalyticsReport | str, date_from: date | None = None, date_to: date | None = None
) -> str:
    """Латинское имя файла выгрузки: `autoshorts_<report>_<from>_<to>.csv`."""
    parts = ["autoshorts", str(AnalyticsReport(report).value)]
    if date_from:
        parts.append(date_from.isoformat())
    if date_to:
        parts.append(date_to.isoformat())
    return "_".join(parts) + ".csv"


def content_disposition(filename: str) -> str:
    if not filename.isascii() or any(c in filename for c in '"\\/\r\n'):
        raise ValueError("имя файла выгрузки — только латиница без кавычек и слешей")
    return f'attachment; filename="{filename}"'
