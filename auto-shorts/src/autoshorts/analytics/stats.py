"""Статистика A/B и разрезов (docs/ARCHITECTURE.md §6.7 «A/B»; контракт `api/schemas/experiments.py`).

Один ролик с двумя баннерами не публикуется (это дубль) — A/B идёт **между разными роликами**:

* в тест идут только зрелые ролики — старше `ab_min_age_hours` (72 ч);
* горизонт одинаковый для всех роликов: просмотры `views_72h` и посетители за первые 72 ч
  (`visitors_72h`), иначе старые ролики выигрывали бы у молодых просто временем;
* Манн–Уитни (двусторонний, scipy) по `ln(1 + views_72h)` (единица — чтобы ролик с нулём просмотров
  не выпадал; монотонное преобразование на ранговый тест не влияет, но так значение в точке читаемо) и по
  CTR ролика `visitors_72h / views_72h × 1000`, попарно между вариантами;
* pooled CTR варианта — `Σ visitors_72h / Σ views_72h × 1000` с 95% интервалом Пуассон–Гамма;
* вердикт — только при `ready=true` (`experiment_readiness`: ≥ 20 зрелых роликов в каждом варианте и
  ≥ 50 посетителей всего). Решает CTR (баннер влияет на переходы): победитель — вариант с лучшим pooled
  CTR, если он значимо (p < alpha) лучше **каждого** другого варианта по CTR роликов; иначе «значимой
  разницы нет». Поправки на множественность нет — это сказано в тексте вердикта при трёх и более вариантах.

Бизнес-счётчики варианта (старты, пользователи, оплаты, выручка, расход) — за всю жизнь всех его роликов.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from typing import Any

from autoshorts.analytics.formulas import cost_per, median, per_1k, percentile
from autoshorts.api.schemas.experiments import (
    AB_ALPHA,
    AB_CI_LEVEL,
    AB_MIN_VISITORS_TOTAL,
    experiment_readiness,
)

__all__ = [
    "ExperimentInput",
    "VariantInput",
    "build_experiment",
    "group_vs_rest",
    "ln_views",
    "mann_whitney",
    "poisson_gamma_ci",
]


def ln_views(views: float | int | None) -> float | None:
    if views is None or views < 0:
        return None
    return math.log1p(float(views))


def mann_whitney(a: Sequence[float], b: Sequence[float]) -> tuple[float, float] | None:
    """Двусторонний U-тест Манна–Уитни: `(U для a, p)`. Пустая выборка — `None`; все значения
    одинаковы — `(n_a·n_b/2, 1.0)` (scipy на таком входе даёт nan)."""
    xs = [float(v) for v in a if v is not None and math.isfinite(float(v))]
    ys = [float(v) for v in b if v is not None and math.isfinite(float(v))]
    if not xs or not ys:
        return None
    if len(set(xs) | set(ys)) == 1:
        return len(xs) * len(ys) / 2.0, 1.0
    from scipy.stats import mannwhitneyu

    res = mannwhitneyu(xs, ys, alternative="two-sided", method="auto")
    u, p = float(res.statistic), float(res.pvalue)
    if not math.isfinite(p):
        return u, 1.0
    return u, min(max(p, 0.0), 1.0)


def poisson_gamma_ci(
    count: float | int, exposure: float | int, *, scale: float = 1000.0, level: float = AB_CI_LEVEL
) -> list[float] | None:
    """Интервал для частоты `count / exposure × scale` (точный Пуассон через гамма-квантили):
    нижняя `Γ⁻¹(α/2; k)`, верхняя `Γ⁻¹(1−α/2; k+1)`; при `k = 0` нижняя 0. Нет экспозиции — `None`."""
    if not exposure or exposure <= 0:
        return None
    from scipy.stats import gamma

    k = max(float(count), 0.0)
    alpha = 1.0 - level
    lo = 0.0 if k == 0 else float(gamma.ppf(alpha / 2.0, k))
    hi = float(gamma.ppf(1.0 - alpha / 2.0, k + 1.0))
    factor = scale / float(exposure)
    return [round(lo * factor, 4), round(hi * factor, 4)]


def group_vs_rest(values_by_key: Mapping[str, Sequence[float]], *, min_n: int = 2) -> dict[str, float | None]:
    """p-value Манна–Уитни «группа против всех остальных» для каждой группы (разрезы, §7.3)."""
    out: dict[str, float | None] = {}
    for key, vals in values_by_key.items():
        rest = [v for k, other in values_by_key.items() if k != key for v in other]
        if len(vals) < min_n or len(rest) < min_n:
            out[key] = None
            continue
        res = mann_whitney(vals, rest)
        out[key] = round(res[1], 6) if res else None
    return out


# ---------------------------------------------------------------- эксперимент


@dataclass
class VariantInput:
    """Вариант и его ролики (точки) для `build_experiment`."""

    id: int
    code: str
    name: str
    status: str
    weight: int
    points: list[dict[str, Any]] = field(default_factory=list)
    """`publication_id, video_uid, channel_id, platform, published_at, age_hours, views_72h, visitors_72h`."""
    bot_starts: int = 0
    users: int = 0
    first_payments: int = 0
    revenue_minor: int = 0
    spend_usd: float = 0.0
    visitors_lifetime: int = 0


@dataclass
class ExperimentInput:
    campaign_id: int
    campaign_name: str
    product_id: str
    campaign_status: str
    variants: list[VariantInput]
    min_videos_per_variant: int
    min_age_hours: int
    computed_at: datetime
    alpha: float = AB_ALPHA
    min_visitors_total: int = AB_MIN_VISITORS_TOTAL


def _point(p: Mapping[str, Any], min_age_hours: int) -> dict[str, Any]:
    age = p.get("age_hours")
    mature = age is not None and float(age) >= min_age_hours
    views = p.get("views_72h")
    visitors = int(p.get("visitors_72h") or 0)
    return {
        "publication_id": int(p["publication_id"]),
        "video_uid": str(p["video_uid"]),
        "channel_id": str(p["channel_id"]),
        "platform": p["platform"],
        "published_at": p.get("published_at"),
        "age_hours": round(float(age), 2) if age is not None else None,
        "mature": mature,
        "views_72h": int(views) if views is not None else None,
        "ln_views_72h": round(ln_views(views), 6) if views is not None else None,
        "visitors": visitors if mature else 0,
        "ctr_per_1k": per_1k(visitors, views) if mature and views else None,
    }


def _variant(v: VariantInput, min_age_hours: int, min_videos: int) -> dict[str, Any]:
    points = [_point(p, min_age_hours) for p in v.points]
    mature = [p for p in points if p["mature"]]
    views72 = [p["views_72h"] for p in mature if p["views_72h"] is not None]
    visitors = sum(p["visitors"] for p in mature)
    exposure = sum(views72)
    return {
        "id": v.id,
        "code": v.code,
        "name": v.name,
        "status": v.status,
        "weight": v.weight,
        "videos": len(points),
        "videos_mature": len(mature),
        "needs_more": max(min_videos - len(mature), 0),
        "views_72h_median": median(views72),
        "views_72h_p25": percentile(views72, 25),
        "views_72h_p75": percentile(views72, 75),
        "visitors": visitors,
        "ctr_per_1k_pooled": per_1k(visitors, exposure),
        "ctr_ci": poisson_gamma_ci(visitors, exposure) if exposure else None,
        "bot_starts": v.bot_starts,
        "users": v.users,
        "first_payments": v.first_payments,
        "revenue_minor": v.revenue_minor,
        "spend_usd": round(v.spend_usd, 6),
        "cost_per_visitor_usd": cost_per(v.spend_usd, v.visitors_lifetime),
        "cost_per_bot_start_usd": cost_per(v.spend_usd, v.bot_starts),
        "points": points,
    }


def _tests(variants: list[dict[str, Any]], alpha: float) -> list[dict[str, Any]]:
    tests: list[dict[str, Any]] = []
    for a, b in combinations(variants, 2):
        for metric, key in (("ln_views_72h", "ln_views_72h"), ("ctr_per_1k", "ctr_per_1k")):
            xa = [p[key] for p in a["points"] if p["mature"] and p[key] is not None]
            xb = [p[key] for p in b["points"] if p["mature"] and p[key] is not None]
            res = mann_whitney(xa, xb)
            if res is None:
                continue
            u, p_value = res
            significant = p_value < alpha
            better = None
            if significant:
                ma, mb = median(xa), median(xb)
                if ma is not None and mb is not None and ma != mb:
                    better = a["code"] if ma > mb else b["code"]
            tests.append(
                {
                    "metric": metric,
                    "method": "mann_whitney_u",
                    "a": a["code"],
                    "b": b["code"],
                    "a_variant_id": a["id"],
                    "b_variant_id": b["id"],
                    "u": round(u, 4),
                    "p_value": round(p_value, 6),
                    "n_a": len(xa),
                    "n_b": len(xb),
                    "significant": significant,
                    "better": better,
                }
            )
    return tests


def _verdict(variants: list[dict[str, Any]], tests: list[dict[str, Any]], alpha: float) -> dict[str, Any]:
    ctr_tests = [t for t in tests if t["metric"] == "ctr_per_1k"]
    ranked = sorted(
        (v for v in variants if v["ctr_per_1k_pooled"] is not None),
        key=lambda v: v["ctr_per_1k_pooled"],
        reverse=True,
    )
    many = len(variants) > 2
    note = " Поправки на множественность сравнений нет — перепроверьте на новых роликах." if many else ""
    if not ranked or not ctr_tests:
        p_min = min((t["p_value"] for t in ctr_tests), default=1.0)
        return {
            "winner_variant_id": None,
            "winner_code": None,
            "metric": "ctr_per_1k",
            "p_value": p_min,
            "summary": "Значимой разницы в переходах на 1000 просмотров нет." + note,
        }
    best = ranked[0]
    mine = [t for t in ctr_tests if best["id"] in (t["a_variant_id"], t["b_variant_id"])]
    wins = [t for t in mine if t["significant"] and t["better"] == best["code"]]
    others = len(variants) - 1
    if mine and len(wins) == others:
        p_worst = max(t["p_value"] for t in wins)
        return {
            "winner_variant_id": best["id"],
            "winner_code": best["code"],
            "metric": "ctr_per_1k",
            "p_value": p_worst,
            "summary": (
                f"Вариант {best['code']} значимо лучше по переходам на 1000 просмотров: "
                f"{best['ctr_per_1k_pooled']:.2f} (p = {p_worst:.3f}).{note}"
            ),
        }
    p_min = min((t["p_value"] for t in mine), default=1.0)
    views_sig = [t for t in tests if t["metric"] == "ln_views_72h" and t["significant"]]
    extra = (
        f" По просмотрам за 72 ч разница есть (лучше {views_sig[0]['better']}), но баннер на неё не влияет."
        if views_sig and views_sig[0]["better"]
        else ""
    )
    return {
        "winner_variant_id": None,
        "winner_code": None,
        "metric": "ctr_per_1k",
        "p_value": p_min,
        "summary": "Значимой разницы в переходах на 1000 просмотров нет." + extra + note,
    }


def build_experiment(inp: ExperimentInput) -> dict[str, Any]:
    """`Experiment` (контракт) из вариантов и их роликов. Вердикт — только при готовности."""
    variants = [_variant(v, inp.min_age_hours, inp.min_videos_per_variant) for v in inp.variants]
    visitors_total = sum(v["visitors"] for v in variants)
    ready, reason, needed = experiment_readiness(
        {v["code"]: v["videos_mature"] for v in variants},
        visitors_total,
        min_videos_per_variant=inp.min_videos_per_variant,
        min_visitors_total=inp.min_visitors_total,
    )
    tests = _tests(variants, inp.alpha)
    return {
        "campaign_id": inp.campaign_id,
        "campaign_name": inp.campaign_name,
        "product_id": inp.product_id,
        "campaign_status": inp.campaign_status,
        "ready": ready,
        "reason": reason.value if reason else None,
        "min_videos_per_variant": inp.min_videos_per_variant,
        "min_age_hours": inp.min_age_hours,
        "min_visitors_total": inp.min_visitors_total,
        "videos_needed": needed,
        "visitors_total": visitors_total,
        "alpha": inp.alpha,
        "computed_at": inp.computed_at,
        "variants": variants,
        "tests": tests,
        "verdict": _verdict(variants, tests, inp.alpha) if ready else None,
    }
