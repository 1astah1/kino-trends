"""Формулы аналитики (M4-B, `analytics/formulas.py`): посетители без двойного счёта, честная цена
опубликованного ролика с браком, нулевые знаменатели, KPI и воронка."""

from __future__ import annotations

import numpy as np
import pytest

from autoshorts.analytics.formulas import (
    COMPONENT_KEYS,
    add_components,
    compute_totals,
    cost_per,
    empty_components,
    funnel_branches,
    kpi_value,
    median,
    per_1k,
    percentile,
    ratio,
    sum_components,
)
from autoshorts.api.schemas.analytics import AnalyticsMetric, AnalyticsTotals


def comps(**kw: float) -> dict[str, float]:
    c = empty_components()
    c.update(kw)
    return c


def test_totals_cover_the_contract_and_validate() -> None:
    t = compute_totals(comps(views=1000, spend_usd=1.0), fx_usd_rub=95.0)
    AnalyticsTotals.model_validate(t)
    assert set(t) == set(AnalyticsTotals.model_fields)
    assert {m.value for m in AnalyticsMetric} <= set(t)


def test_visitors_and_clicks_without_double_count() -> None:
    t = compute_totals(
        comps(
            landing_views=120, direct=30, unique_visitors=100, direct_bot_starts=15, bot_starts=60, users=50
        ),
        fx_usd_rub=95.0,
    )
    assert t["clicks"] == 150  # landing + direct
    assert t["visitors"] == 115  # уникальные посетители лендинга + старты без _l
    assert t["cr_visitor_to_start"] == pytest.approx(50 / 115, abs=1e-6)


def test_honest_cost_per_published_video_counts_rejects() -> None:
    # 10 роликов: 4 опубликованы, 6 в браке; расход всех 10 — 5 $, из них брак 3 $
    t = compute_totals(
        comps(
            videos_produced=10,
            videos_published=4,
            videos_rejected=6,
            spend_usd=5.0,
            rejected_spend_usd=3.0,
            unique_visitors=40,
            users=8,
            first_payments=2,
        ),
        fx_usd_rub=95.0,
    )
    assert t["cost_per_published_video_usd"] == pytest.approx(1.25)  # 5 / 4, не 2 / 4
    assert t["cost_per_video_usd"] == pytest.approx(0.5)
    assert t["rejected_spend_usd"] == pytest.approx(3.0)
    assert t["cost_per_visitor_usd"] == pytest.approx(0.125)
    assert t["cost_per_user_usd"] == pytest.approx(0.625)
    assert t["cost_per_first_payment_usd"] == pytest.approx(2.5)
    assert t["spend_rub"] == pytest.approx(475.0)


def test_overhead_only_with_flag() -> None:
    c = comps(spend_usd=2.0, overhead_usd=1.0, videos_published=2)
    assert compute_totals(c, fx_usd_rub=100)["spend_usd"] == 2.0
    with_oh = compute_totals(c, fx_usd_rub=100, include_overhead=True)
    assert with_oh["spend_usd"] == 3.0 and with_oh["overhead_usd"] == 1.0
    assert with_oh["cost_per_published_video_usd"] == 1.5


def test_zero_denominators_are_none_not_zero() -> None:
    t = compute_totals(empty_components(), fx_usd_rub=95.0)
    for key in (
        "ctr_per_1k_views",
        "cr_visitor_to_start",
        "cr_start_to_payment",
        "cost_per_visitor_usd",
        "cost_per_user_usd",
        "cost_per_bot_start_usd",
        "cost_per_first_payment_usd",
        "cost_per_published_video_usd",
        "cost_per_video_usd",
        "romi",
    ):
        assert t[key] is None, key
    assert t["engaged_views"] is None and t["currency"] is None
    assert ratio(1, 0) is None and per_1k(5, None) is None and cost_per(1.0, 0) is None


def test_ctr_romi_and_revenue() -> None:
    t = compute_totals(
        comps(views=20000, unique_visitors=40, spend_usd=1.0, revenue_minor=19000),
        fx_usd_rub=95.0,
        currency="RUB",
    )
    assert t["ctr_per_1k_views"] == pytest.approx(2.0)
    assert t["romi"] == pytest.approx((190.0 - 95.0) / 95.0, abs=1e-6)
    assert t["currency"] == "RUB"


def test_add_components_sums_and_ignores_unknown() -> None:
    a = comps(views=5, spend_usd=0.1)
    add_components(a, {"views": 3, "spend_usd": 0.2, "unknown": 7, "likes": None})
    assert a["views"] == 8 and a["spend_usd"] == pytest.approx(0.3) and "unknown" not in a
    s = sum_components([comps(users=1), comps(users=2)])
    assert s["users"] == 3 and set(s) == set(COMPONENT_KEYS)


def test_kpi_value() -> None:
    assert kpi_value(150, 100) == {"value": 150.0, "previous": 100.0, "delta": 50.0, "delta_pct": 50.0}
    assert kpi_value(5, 0)["delta_pct"] is None
    assert kpi_value(None, 3) == {"value": None, "previous": 3.0, "delta": None, "delta_pct": None}


def test_percentile_matches_numpy() -> None:
    data = [7, 1, 3, 10, 22, 5]
    for q in (0, 25, 50, 75, 90, 100):
        assert percentile(data, q) == pytest.approx(float(np.percentile(data, q)))
    assert median([]) is None and median([4]) == 4.0


def test_funnel_branches_codes_and_rates() -> None:
    t = compute_totals(
        comps(
            views=10000,
            landing_views=100,
            unique_visitors=80,
            to_site=20,
            signups=10,
            first_payments=2,
            to_telegram=40,
            direct_bot_starts=10,
            bot_starts=45,
            users=40,
            spend_usd=1.0,
        ),
        fx_usd_rub=95.0,
    )
    f = funnel_branches(t)
    assert [s["code"] for s in f["domain"]] == [
        "views",
        "landing_views",
        "unique_visitors",
        "to_site",
        "signups",
        "first_payments",
    ]
    assert [s["code"] for s in f["bot"]] == [
        "views",
        "to_telegram",
        "direct_bot_starts",
        "bot_starts",
        "users",
        "first_payments",
    ]
    bot = {s["code"]: s for s in f["bot"]}
    assert bot["views"]["rate_from_prev"] is None
    assert bot["bot_starts"]["rate_from_prev"] == pytest.approx(45 / 50)  # от суммы входов
    assert bot["users"]["per_1k_views"] == pytest.approx(4.0)
    assert bot["users"]["cost_per_usd"] == pytest.approx(0.025)
