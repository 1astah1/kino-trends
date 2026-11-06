"""Формулы аналитики (docs/ARCHITECTURE.md §6.6, §6.7; определения — докстринг `api/schemas/analytics.py`).

Слагаемые (`Components`) — аддитивные счётчики одной группы (ролик, канал, день …): их можно складывать
между группами и днями. Доли и цены (`AnalyticsTotals`) считаются из слагаемых **после** сложения, а не
усреднением долей, — поэтому «хвост» рядов (`other`) и итоги всегда согласованы с формулами.

* `clicks = landing_views + direct` — входы в редиректор (без ботов);
* `visitors = unique_visitors + direct_bot_starts` — без двойного счёта: старт бота с лендинга приходит
  как `<код>_l` и в `direct_bot_starts` не попадает;
* `users` — уникальные `user_ref_hash` среди `bot_start` (у события без `user_ref` — само событие);
* `spend_usd` — расход роликов группы (включая брак); накладные (`overhead_usd`) входят только при
  `include_overhead`; `spend_rub = spend_usd × fx_usd_rub`;
* `cost_per_published_video_usd = spend_usd / videos_published` — честная цена (ТЗ §8): в числителе и
  отклонённые, и упавшие ролики; `cost_per_video_usd = spend_usd / videos_produced`;
* `ctr_per_1k_views = visitors / views × 1000`; `cr_visitor_to_start = users / visitors`;
  `cr_start_to_payment = first_payments / users`; `romi = (выручка₽ − расход₽) / расход₽`;
* знаменатель 0 → `None` (не 0 и не бесконечность).

`revenue_minor` — чистая выручка в копейках: оплаты (`first_payment`, `payment`) минус возвраты.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

__all__ = [
    "COMPONENT_KEYS",
    "FLOAT_COMPONENTS",
    "Components",
    "add_components",
    "compute_totals",
    "cost_per",
    "empty_components",
    "funnel_branches",
    "kpi_value",
    "median",
    "percentile",
    "per_1k",
    "ratio",
    "sum_components",
]

Components = dict[str, float]

COMPONENT_KEYS: tuple[str, ...] = (
    "videos_produced",
    "videos_published",
    "videos_rejected",
    "videos_failed",
    "views",
    "engaged_views",
    "likes",
    "comments",
    "landing_views",
    "unique_visitors",
    "to_telegram",
    "to_site",
    "direct",
    "direct_bot_starts",
    "bot_starts",
    "users",
    "tma_opens",
    "signups",
    "first_payments",
    "payments",
    "revenue_minor",
    "spend_usd",
    "overhead_usd",
    "rejected_spend_usd",
)
FLOAT_COMPONENTS = frozenset({"spend_usd", "overhead_usd", "rejected_spend_usd"})

MONEY_DIGITS = 6
RATIO_DIGITS = 6


def empty_components() -> Components:
    return dict.fromkeys(COMPONENT_KEYS, 0)


def add_components(target: Components, other: Mapping[str, Any]) -> Components:
    """Прибавить `other` к `target` (на месте); неизвестные ключи игнорируются, `None` — ноль."""
    for key in COMPONENT_KEYS:
        v = other.get(key)
        if v:
            target[key] = target.get(key, 0) + (float(v) if key in FLOAT_COMPONENTS else int(v))
    return target


def sum_components(items: Iterable[Mapping[str, Any]]) -> Components:
    out = empty_components()
    for item in items:
        add_components(out, item)
    return out


def ratio(num: float | int | None, den: float | int | None, *, digits: int = RATIO_DIGITS) -> float | None:
    if num is None or not den:
        return None
    value = float(num) / float(den)
    return round(value, digits) if math.isfinite(value) else None


def per_1k(num: float | int | None, den: float | int | None) -> float | None:
    if num is None or not den:
        return None
    return round(float(num) / float(den) * 1000.0, 4)


def cost_per(spend: float | None, n: float | int | None) -> float | None:
    if spend is None or not n:
        return None
    return round(float(spend) / float(n), MONEY_DIGITS)


def compute_totals(
    c: Mapping[str, Any],
    *,
    fx_usd_rub: float,
    include_overhead: bool = False,
    engaged_available: bool = False,
    currency: str | None = None,
) -> dict[str, Any]:
    """`AnalyticsTotals` из слагаемых (все поля контракта, доли — `None` при нулевом знаменателе)."""

    def n(key: str) -> int:
        return int(c.get(key) or 0)

    def f(key: str) -> float:
        return float(c.get(key) or 0.0)

    views = n("views")
    visitors = n("unique_visitors") + n("direct_bot_starts")
    users = n("users")
    bot_starts = n("bot_starts")
    first_payments = n("first_payments")
    overhead = f("overhead_usd")
    spend = f("spend_usd") + (overhead if include_overhead else 0.0)
    spend_rub = spend * float(fx_usd_rub)
    revenue_minor = n("revenue_minor")
    revenue_rub = revenue_minor / 100.0
    romi = round((revenue_rub - spend_rub) / spend_rub, RATIO_DIGITS) if spend_rub > 0 else None
    return {
        "videos_produced": n("videos_produced"),
        "videos_published": n("videos_published"),
        "videos_rejected": n("videos_rejected"),
        "videos_failed": n("videos_failed"),
        "views": views,
        "engaged_views": n("engaged_views") if engaged_available else None,
        "likes": n("likes"),
        "comments": n("comments"),
        "clicks": n("landing_views") + n("direct"),
        "landing_views": n("landing_views"),
        "unique_visitors": n("unique_visitors"),
        "to_telegram": n("to_telegram"),
        "to_site": n("to_site"),
        "direct_bot_starts": n("direct_bot_starts"),
        "visitors": visitors,
        "bot_starts": bot_starts,
        "users": users,
        "tma_opens": n("tma_opens"),
        "signups": n("signups"),
        "first_payments": first_payments,
        "payments": n("payments"),
        "revenue_minor": revenue_minor,
        "currency": currency if revenue_minor else None,
        "spend_usd": round(spend, MONEY_DIGITS),
        "spend_rub": round(spend_rub, 2),
        "overhead_usd": round(overhead, MONEY_DIGITS),
        "rejected_spend_usd": round(f("rejected_spend_usd"), MONEY_DIGITS),
        "ctr_per_1k_views": per_1k(visitors, views),
        "cr_visitor_to_start": ratio(users, visitors),
        "cr_start_to_payment": ratio(first_payments, users),
        "cost_per_visitor_usd": cost_per(spend, visitors),
        "cost_per_user_usd": cost_per(spend, users),
        "cost_per_bot_start_usd": cost_per(spend, bot_starts),
        "cost_per_first_payment_usd": cost_per(spend, first_payments),
        "cost_per_published_video_usd": cost_per(spend, n("videos_published")),
        "cost_per_video_usd": cost_per(spend, n("videos_produced")),
        "romi": romi,
    }


def kpi_value(value: float | int | None, previous: float | int | None) -> dict[str, float | None]:
    """`KpiValue`: значение, прошлый период, дельта и дельта в процентах (прошлое 0 → `None`)."""
    v = float(value) if value is not None else None
    p = float(previous) if previous is not None else None
    delta = round(v - p, MONEY_DIGITS) if v is not None and p is not None else None
    pct = round(delta / p * 100.0, 2) if delta is not None and p else None
    return {"value": v, "previous": p, "delta": delta, "delta_pct": pct}


def percentile(values: Sequence[float | int], q: float) -> float | None:
    """Перцентиль с линейной интерполяцией (как `numpy.percentile(..., method='linear')`), q в 0..100."""
    data = sorted(float(v) for v in values if v is not None)
    if not data:
        return None
    if len(data) == 1:
        return data[0]
    pos = (len(data) - 1) * (q / 100.0)
    lo = math.floor(pos)
    hi = min(lo + 1, len(data) - 1)
    return data[lo] + (data[hi] - data[lo]) * (pos - lo)


def median(values: Sequence[float | int]) -> float | None:
    return percentile(values, 50.0)


def _step(
    code: str, value: float | None, prev: float | None, views: int, spend: float
) -> dict[str, float | str | None]:
    return {
        "code": code,
        "value": value,
        "rate_from_prev": ratio(value, prev) if prev is not None else None,
        "per_1k_views": per_1k(value, views),
        "cost_per_usd": cost_per(spend, value) if value else None,
    }


def funnel_branches(totals: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Две ветки воронки (`FunnelBranches`).

    Домен: просмотры → показы лендинга → уникальные посетители → на сайт → регистрации → первые оплаты.
    Бот: просмотры → в Telegram (с лендинга) и старты напрямую (две параллельные входные ступени, доля
    каждой — от просмотров) → старты → пользователи → первые оплаты; доля стартов — от суммы входов.
    """
    views = int(totals.get("views") or 0)
    spend = float(totals.get("spend_usd") or 0.0)

    def val(key: str) -> float:
        return float(totals.get(key) or 0)

    domain = [
        _step("views", float(views), None, views, spend),
        _step("landing_views", val("landing_views"), float(views), views, spend),
        _step("unique_visitors", val("unique_visitors"), val("landing_views"), views, spend),
        _step("to_site", val("to_site"), val("unique_visitors"), views, spend),
        _step("signups", val("signups"), val("to_site"), views, spend),
        _step("first_payments", val("first_payments"), val("signups"), views, spend),
    ]
    entry = val("to_telegram") + val("direct_bot_starts")
    bot = [
        _step("views", float(views), None, views, spend),
        _step("to_telegram", val("to_telegram"), float(views), views, spend),
        _step("direct_bot_starts", val("direct_bot_starts"), float(views), views, spend),
        _step("bot_starts", val("bot_starts"), entry, views, spend),
        _step("users", val("users"), val("bot_starts"), views, spend),
        _step("first_payments", val("first_payments"), val("users"), views, spend),
    ]
    return {"domain": domain, "bot": bot}
