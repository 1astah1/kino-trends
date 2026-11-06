"""A/B баннеров между роликами (docs/ARCHITECTURE.md §6.7 «A/B», §7.3 «Продукты → A/B»; контракт M4-0).

Реализация — M4-B (`analytics/stats.py`, `api/routers/experiments.py`), тег `experiments`:

* `GET /campaigns/{campaign_id}/experiment` `?platform` → `Experiment` (viewer+) · 404 campaign_not_found.

Правила (один ролик с двумя баннерами **не публикуется** — это дубль; A/B идёт между разными роликами):

* в расчёт идут только зрелые ролики — старше `ab_min_age_hours` (72 ч) на момент расчёта;
* тесты: Манн–Уитни (scipy, двусторонний) по `ln(views_72h)` и по CTR на ролик
  (`visitors / views_72h × 1000`), попарно между вариантами;
* pooled CTR варианта — `Σ visitors / Σ views × 1000` с доверительным интервалом Пуассон–Гамма (95%);
* **вердикт** (`verdict`) только при `ready=true`: в каждом варианте не меньше `ab_min_videos_per_variant`
  (20) зрелых роликов и не меньше `AB_MIN_VISITORS_TOTAL` (50) посетителей всего — `experiment_readiness`.
  До этого p-value показываются, но UI пишет «рано судить: нужно ещё N».
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, Field

from autoshorts.api.schemas.common import ApiModel
from autoshorts.domain.enums import CampaignStatus, Platform, VariantStatus

__all__ = [
    "AB_ALPHA",
    "AB_CI_LEVEL",
    "AB_MIN_VISITORS_TOTAL",
    "Experiment",
    "ExperimentNotReadyReason",
    "ExperimentPoint",
    "ExperimentQuery",
    "ExperimentTest",
    "ExperimentTestMetric",
    "ExperimentVariant",
    "ExperimentVerdict",
    "StatTestMethod",
    "experiment_readiness",
]

AB_MIN_VISITORS_TOTAL = 50
AB_ALPHA = 0.05
AB_CI_LEVEL = 0.95


class ExperimentNotReadyReason(StrEnum):
    NO_VARIANTS = "no_variants"
    SINGLE_VARIANT = "single_variant"
    NOT_ENOUGH_MATURE_VIDEOS = "not_enough_mature_videos"
    NOT_ENOUGH_VISITORS = "not_enough_visitors"


class ExperimentTestMetric(StrEnum):
    LN_VIEWS_72H = "ln_views_72h"
    CTR_PER_1K = "ctr_per_1k"


class StatTestMethod(StrEnum):
    MANN_WHITNEY_U = "mann_whitney_u"


def experiment_readiness(
    mature_by_variant: Mapping[str, int],
    visitors_total: int,
    *,
    min_videos_per_variant: int,
    min_visitors_total: int = AB_MIN_VISITORS_TOTAL,
) -> tuple[bool, ExperimentNotReadyReason | None, int]:
    """Готов ли вердикт: `(ready, reason, videos_needed)`.

    `mature_by_variant` — число зрелых роликов по вариантам, участвующим в тесте (активные и на паузе;
    архивные не участвуют). `videos_needed` — сколько зрелых роликов ещё не хватает в сумме по
    вариантам до `min_videos_per_variant` в каждом.
    """
    if not mature_by_variant:
        return False, ExperimentNotReadyReason.NO_VARIANTS, 0
    needed = sum(max(min_videos_per_variant - n, 0) for n in mature_by_variant.values())
    if len(mature_by_variant) < 2:
        return False, ExperimentNotReadyReason.SINGLE_VARIANT, needed
    if needed > 0:
        return False, ExperimentNotReadyReason.NOT_ENOUGH_MATURE_VIDEOS, needed
    if visitors_total < min_visitors_total:
        return False, ExperimentNotReadyReason.NOT_ENOUGH_VISITORS, 0
    return True, None, 0


class ExperimentQuery(BaseModel):
    platform: list[Platform] = Field(
        default_factory=list, description="Пусто — все площадки (смешивать YouTube и VK не рекомендуется)"
    )


ExperimentQueryParams = Annotated[ExperimentQuery, Query()]


class ExperimentPoint(ApiModel):
    """Один ролик варианта — точка strip-plot."""

    publication_id: int
    video_uid: str
    channel_id: str
    platform: Platform
    published_at: datetime | None
    age_hours: float | None
    mature: bool
    views_72h: int | None
    ln_views_72h: float | None
    visitors: int
    ctr_per_1k: float | None


class ExperimentVariant(ApiModel):
    id: int
    code: str
    name: str
    status: VariantStatus
    weight: int
    videos: int = Field(description="Опубликовано роликов с этим вариантом")
    videos_mature: int
    needs_more: int = Field(description="Сколько зрелых роликов не хватает до минимума")
    views_72h_median: float | None
    views_72h_p25: float | None
    views_72h_p75: float | None
    visitors: int
    ctr_per_1k_pooled: float | None
    ctr_ci: Annotated[list[float], Field(min_length=2, max_length=2)] | None = Field(
        description="[нижняя, верхняя] граница 95% (Пуассон–Гамма)"
    )
    bot_starts: int
    users: int
    first_payments: int
    revenue_minor: int
    spend_usd: float
    cost_per_visitor_usd: float | None
    cost_per_bot_start_usd: float | None
    points: list[ExperimentPoint]


class ExperimentTest(ApiModel):
    metric: ExperimentTestMetric
    method: StatTestMethod
    a: str = Field(description="Код варианта A")
    b: str = Field(description="Код варианта B")
    a_variant_id: int
    b_variant_id: int
    u: float
    p_value: float
    n_a: int
    n_b: int
    significant: bool = Field(description="p_value < alpha (без поправки на множественность)")
    better: str | None = Field(description="Код варианта с большей медианой, если значимо")


class ExperimentVerdict(ApiModel):
    winner_variant_id: int | None = Field(description="null — значимой разницы нет")
    winner_code: str | None
    metric: ExperimentTestMetric
    p_value: float
    summary: str = Field(description="Вывод для человека, по-русски")


class Experiment(ApiModel):
    campaign_id: int
    campaign_name: str
    product_id: str
    campaign_status: CampaignStatus
    ready: bool
    reason: ExperimentNotReadyReason | None
    min_videos_per_variant: int = Field(description="ab_min_videos_per_variant (20)")
    min_age_hours: int = Field(description="ab_min_age_hours (72)")
    min_visitors_total: int
    videos_needed: int
    visitors_total: int
    alpha: float
    computed_at: datetime
    variants: list[ExperimentVariant]
    tests: list[ExperimentTest]
    verdict: ExperimentVerdict | None = Field(description="Только при ready=true")
