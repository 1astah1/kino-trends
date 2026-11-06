"""Статистика A/B (M4-B, `analytics/stats.py`): меньше 20 зрелых роликов или моложе 72 ч — вердикта
нет; на синтетике с известным сдвигом p < 0,05 и победитель верный."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from autoshorts.analytics.stats import (
    ExperimentInput,
    VariantInput,
    build_experiment,
    group_vs_rest,
    ln_views,
    mann_whitney,
    poisson_gamma_ci,
)
from autoshorts.api.schemas.experiments import Experiment

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def points(
    rng: random.Random, n: int, *, mu: float, ctr_per_1k: float, age_hours: float = 120.0, start_id: int = 1
) -> list[dict[str, Any]]:
    out = []
    for i in range(n):
        views = int(math.exp(rng.gauss(mu, 0.4)))
        visitors = sum(1 for _ in range(views) if rng.random() < ctr_per_1k / 1000.0)
        out.append(
            {
                "publication_id": start_id + i,
                "video_uid": f"v{start_id + i:05d}",
                "channel_id": "taro_main",
                "platform": "youtube",
                "published_at": NOW - timedelta(hours=age_hours),
                "age_hours": age_hours,
                "views_72h": views,
                "visitors_72h": visitors,
            }
        )
    return out


def experiment(variants: list[VariantInput], **kw: Any) -> dict[str, Any]:
    out = build_experiment(
        ExperimentInput(
            campaign_id=7,
            campaign_name="Осень",
            product_id="umka",
            campaign_status="active",
            variants=variants,
            min_videos_per_variant=kw.pop("min_videos", 20),
            min_age_hours=kw.pop("min_age", 72),
            computed_at=NOW,
            **kw,
        )
    )
    Experiment.model_validate(out)  # форма контракта
    return out


def variant(vid: int, code: str, pts: list[dict[str, Any]]) -> VariantInput:
    return VariantInput(id=vid, code=code, name=f"Вариант {code}", status="active", weight=50, points=pts)


def test_mann_whitney_basics() -> None:
    assert mann_whitney([], [1.0]) is None
    u, p = mann_whitney([1.0, 1.0], [1.0, 1.0, 1.0])  # type: ignore[misc]
    assert p == 1.0 and u == 3.0
    u, p = mann_whitney([1, 2, 3, 4, 5, 6, 7, 8], [11, 12, 13, 14, 15, 16, 17, 18])  # type: ignore[misc]
    assert p < 0.01 and u == 0.0


def test_poisson_gamma_ci_exact_values() -> None:
    lo, hi = poisson_gamma_ci(10, 10_000)  # 10 посетителей на 10 000 просмотров → 1 на 1000
    assert lo == pytest.approx(0.4795, abs=1e-3) and hi == pytest.approx(1.8390, abs=1e-3)
    zero = poisson_gamma_ci(0, 5_000)
    assert zero is not None and zero[0] == 0.0 and zero[1] == pytest.approx(0.7378, abs=1e-3)
    assert poisson_gamma_ci(3, 0) is None


def test_ln_views() -> None:
    assert ln_views(0) == 0.0 and ln_views(None) is None and ln_views(-1) is None
    assert ln_views(math.e - 1) == pytest.approx(1.0)


def test_not_ready_below_20_mature_videos_even_with_strong_shift() -> None:
    rng = random.Random(1)
    a = variant(1, "A", points(rng, 19, mu=7.0, ctr_per_1k=1.0))
    b = variant(2, "B", points(rng, 25, mu=9.0, ctr_per_1k=6.0, start_id=100))
    ex = experiment([a, b])
    assert ex["ready"] is False and ex["reason"] == "not_enough_mature_videos"
    assert ex["videos_needed"] == 1 and ex["verdict"] is None
    va = next(v for v in ex["variants"] if v["code"] == "A")
    assert va["videos_mature"] == 19 and va["needs_more"] == 1
    assert ex["tests"], "p-value показываются и до вердикта"


def test_not_ready_before_72_hours() -> None:
    rng = random.Random(2)
    a = variant(1, "A", points(rng, 30, mu=8.0, ctr_per_1k=2.0, age_hours=48))
    b = variant(2, "B", points(rng, 30, mu=8.0, ctr_per_1k=2.0, age_hours=71.9, start_id=100))
    ex = experiment([a, b])
    assert ex["ready"] is False and ex["reason"] == "not_enough_mature_videos"
    assert all(v["videos_mature"] == 0 and v["visitors"] == 0 for v in ex["variants"])
    assert all(not p["mature"] and p["ctr_per_1k"] is None for v in ex["variants"] for p in v["points"])


def test_not_ready_without_visitors_or_second_variant() -> None:
    rng = random.Random(3)
    a = variant(1, "A", points(rng, 20, mu=5.0, ctr_per_1k=0.0))
    b = variant(2, "B", points(rng, 20, mu=5.0, ctr_per_1k=0.0, start_id=100))
    ex = experiment([a, b])
    assert ex["ready"] is False and ex["reason"] == "not_enough_visitors"
    single = experiment([variant(1, "A", points(rng, 25, mu=8.0, ctr_per_1k=3.0))])
    assert single["reason"] == "single_variant" and single["tests"] == []
    assert experiment([])["reason"] == "no_variants"


def test_synthetic_shift_is_significant_and_winner_is_right() -> None:
    rng = random.Random(42)
    a = variant(1, "A", points(rng, 25, mu=8.0, ctr_per_1k=1.5))
    b = variant(2, "B", points(rng, 25, mu=8.8, ctr_per_1k=4.0, start_id=100))
    ex = experiment([a, b])
    assert ex["ready"] is True and ex["reason"] is None
    tests = {t["metric"]: t for t in ex["tests"]}
    assert tests["ln_views_72h"]["p_value"] < 0.05 and tests["ln_views_72h"]["better"] == "B"
    assert tests["ctr_per_1k"]["p_value"] < 0.05 and tests["ctr_per_1k"]["significant"]
    assert tests["ctr_per_1k"]["n_a"] == 25 and tests["ctr_per_1k"]["method"] == "mann_whitney_u"
    assert ex["verdict"]["winner_code"] == "B" and ex["verdict"]["winner_variant_id"] == 2
    assert ex["verdict"]["metric"] == "ctr_per_1k" and ex["verdict"]["p_value"] < 0.05
    vb = next(v for v in ex["variants"] if v["code"] == "B")
    lo, hi = vb["ctr_ci"]
    assert lo < vb["ctr_per_1k_pooled"] < hi


def test_no_shift_no_winner() -> None:
    rng = random.Random(7)
    a = variant(1, "A", points(rng, 25, mu=8.0, ctr_per_1k=3.0))
    b = variant(2, "B", points(rng, 25, mu=8.0, ctr_per_1k=3.0, start_id=100))
    ex = experiment([a, b])
    assert ex["ready"] is True
    assert ex["verdict"]["winner_variant_id"] is None
    assert "Значимой разницы" in ex["verdict"]["summary"]


def test_group_vs_rest() -> None:
    rng = random.Random(5)
    groups = {
        "a": [rng.gauss(10, 1) for _ in range(20)],
        "b": [rng.gauss(10, 1) for _ in range(20)],
        "c": [rng.gauss(14, 1) for _ in range(20)],
        "tiny": [10.0],
    }
    p = group_vs_rest(groups)
    assert p["c"] is not None and p["c"] < 0.001
    assert p["a"] is not None and p["a"] > p["c"]
    assert p["tiny"] is None
