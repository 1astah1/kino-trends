"""Аналитика M4-B: «Пульт» (Обзор), сводка, ряды, разрезы, воронка, когорты, стоимость, свежесть данных,
карточка ролика, A/B кампании и выгрузка CSV (docs/ARCHITECTURE.md §6.6, §6.7, §7.3).

Контракты и определения метрик — докстринги `api/schemas/analytics.py`, `api/schemas/overview.py`,
`api/schemas/experiments.py`; SQL — `repos/analytics.py`; формулы — `analytics/formulas.py`;
интерполяция снимков — `analytics/interp.py`; статистика A/B — `analytics/stats.py`.

    from autoshorts.services import analytics
    analytics.overview(conn, OverviewQuery())          # GET /overview
    analytics.summary(conn, AnalyticsQuery())          # GET /analytics/summary
    analytics.experiment(conn, campaign_id)            # GET /campaigns/{id}/experiment

Опрос YouTube Analytics (досмотры, удержание) — отдельный модуль `services/yt_analytics.py`.

Сборка цифр:

* **activity** — ролл-апы по бизнес-дням периода: просмотры `stats_daily_views`, трафик
  `stats_daily_traffic`/`stats_daily_unattributed`, пользователи — сырые `bot_start` (уникальность за
  период), расход и производство — `generation_events` и `jobs`. В итоги, ряды и разрезы идут уровни
  атрибуции `exact` и `channel` (разрез и ряды по роликам — только `exact`); прямой трафик продукта
  (`direct`) виден в блоке `attribution`.
* **cohort** — ролики, опубликованные в периоде: просмотры, лайки и комментарии на возраст `cohort_age`
  (интерполяция между соседними снимками, без экстраполяции; `lifetime` — последнее известное значение),
  клики и конверсии за первые `cohort_age` часов (только `exact`). Незрелые ролики (моложе `cohort_age`)
  в значения не входят. Расход и производство — всех роликов когорты. Блок `attribution` — всегда по
  времени события в периоде.
* **basis=engaged** — вовлечённые просмотры из снимков; пока их нет, считается по публичным и
  `basis_fallback=true` с предупреждением `engaged_views_unavailable`.

Поле, которое не из чего посчитать, отдаётся пустым (`None`/0 по контракту), без выдуманных чисел.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from autoshorts.analytics import formulas as F
from autoshorts.analytics.interp import bracket_value, build_series, round_count, value_at, value_at_age
from autoshorts.api.schemas.analytics import (
    AGE_POINT_HOURS,
    ANALYTICS_DEFAULT_DAYS,
    BREAKDOWN_LIMIT_MAX,
    COHORT_AGE_HOURS,
    DIMENSION_LABELS,
    METRIC_LABELS,
    NONE_KEY,
    OTHER_KEY,
    YT_PUBLIC_VIEWS_CHANGE_DATE,
    AnalyticsDimension,
    AnalyticsFilters,
    AnalyticsMetric,
    AnalyticsQuery,
    AnalyticsReport,
    BreakdownQuery,
    CohortAge,
    CohortAggregate,
    CohortMetric,
    CohortsQuery,
    CostsQuery,
    CsvDialect,
    FreshnessWarningCode,
    TimeseriesQuery,
    VideoAnalyticsQuery,
    csv_filename,
    encode_csv,
    previous_range,
    resolve_range,
)
from autoshorts.api.schemas.metrics import METRICS_OVERDUE_GRACE_MIN, YOUTUBE_API_KEY_SECRET_DEFAULT
from autoshorts.api.schemas.overview import (
    OVERVIEW_ALERTS_LIMIT,
    OVERVIEW_DEFAULT_DAYS,
    OVERVIEW_TOP_VIDEOS,
    OverviewFilters,
    OverviewKpis,
)
from autoshorts.core.errors import NotFound, ValidationFailed
from autoshorts.core.timeutil import date_range_utc
from autoshorts.domain.enums import LABELS, AnalyticsBasis, AnalyticsMode, JobKind, Platform
from autoshorts.repos import analytics as repo

log = logging.getLogger(__name__)

__all__ = [
    "breakdown",
    "cohorts",
    "costs",
    "experiment",
    "export_csv",
    "freshness",
    "funnel",
    "overview",
    "summary",
    "timeseries",
    "video",
]

Row = dict[str, Any]
Key = tuple[date | None, str | None]  # (корзина времени, ключ группы)

CURRENCY = "RUB"
LEVELS_TOTAL = ("exact", "channel")  # итоги, ряды, разрезы: без прямого трафика продукта
LEVELS_VIDEO = ("exact",)  # разрез и ряды по роликам
AB_HORIZON_HOURS = 72  # views_72h и посетители за первые 72 ч — одинаковый горизонт для всех роликов
ROLLUP_STALE_MIN = 60  # ролл-ап по расписанию раз в 15 минут; час без него — цифры отстают
LADDER_TOLERANCE = timedelta(minutes=5)  # коллектор берёт публикацию чуть раньше срока (DUE_EPSILON)
CHANNEL_CORRIDOR_DAYS = 90
RECENT_CONVERSIONS = 20
HOURLY_TRAFFIC_MAX_AGE_H = 168

ANNOTATION_LABEL = "YouTube: публичные просмотры Shorts с этого дня завышены на 20–30%"
NONE_LABELS: dict[str, str] = {
    "video": "Без ролика",
    "channel": "Без канала",
    "niche": "Без ниши",
    "product": "Без продукта",
    "campaign": "Без кампании",
    "variant": "Без варианта",
    "track": "Без трека",
    "platform": "Без площадки",
    "provider": "Без провайдера",
    "step": "Без шага",
    "outcome": "Без исхода",
}
OTHER_LABEL = "Остальные"
TOTAL_KEY = "total"
OUTCOME_LABELS: dict[str, str] = {
    "published": "Опубликован",
    "approved": "Одобрен, не залит",
    "in_progress": "В работе",
    "rejected": "Отклонён",
    "failed": "Сбой",
    "cancelled": "Отменён",
    "overhead": "Накладные (без ролика)",
}
COLLECTOR_NAMES: dict[str, str] = {
    "yt_data": "YouTube Data API",
    "ytdlp": "yt-dlp (VK)",
    "vk_api": "VK API",
    "manual": "Ручной ввод",
}
STALE_WARNINGS = frozenset(
    {
        FreshnessWarningCode.COLLECTOR_DISABLED,
        FreshnessWarningCode.COLLECTOR_FAILING,
        FreshnessWarningCode.QUOTA_EXHAUSTED,
        FreshnessWarningCode.METRICS_OVERDUE,
        FreshnessWarningCode.MANUAL_INPUT_DUE,
        FreshnessWarningCode.ROLLUP_STALE,
    }
)
"""Предупреждения, из-за которых цифры неполные (`DataFreshness.stale`)."""

KPI_METRICS: tuple[str, ...] = tuple(OverviewKpis.model_fields)
COHORT_AGES: tuple[tuple[str, int], ...] = tuple(
    (age.value, hours) for age, hours in COHORT_AGE_HOURS.items() if hours is not None
)
METRIC_KEYS = frozenset(m.value for m in AnalyticsMetric)
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


# ---------------------------------------------------------------- контекст запроса


@dataclass(frozen=True)
class _Ctx:
    tz: str
    today: date
    now: datetime
    d_from: date
    d_to: date
    since: datetime
    until: datetime
    scope: repo.Scope
    mode: str
    cohort_hours: int | None
    cohort_age: str
    basis: str
    include_overhead: bool
    fx: float
    settings: Any

    @property
    def cohort(self) -> bool:
        return self.mode == AnalyticsMode.COHORT.value

    def shifted(self) -> _Ctx:
        """Тот же запрос за прошлый период той же длины."""
        d_from, d_to = previous_range(self.d_from, self.d_to)
        since, until = date_range_utc(d_from, d_to, self.tz)
        return replace(self, d_from=d_from, d_to=d_to, since=since, until=until)


def _val(v: Any) -> str:
    return str(getattr(v, "value", v))


def _settings(conn: Any) -> Any:
    from autoshorts.services.settings import get_runtime_settings

    return get_runtime_settings(conn)


def _ctx(conn: Any, q: Any, *, default_days: int = ANALYTICS_DEFAULT_DAYS) -> _Ctx:
    settings = _settings(conn)
    tz = settings.business_timezone
    today = repo.business_today(conn, tz)
    d_from, d_to = resolve_range(
        getattr(q, "date_from", None), getattr(q, "date_to", None), today, default_days=default_days
    )
    since, until = date_range_utc(d_from, d_to, tz)
    assert since is not None and until is not None
    age = getattr(q, "cohort_age", None) or CohortAge.LIFETIME
    return _Ctx(
        tz=tz,
        today=today,
        now=repo.db_now(conn),
        d_from=d_from,
        d_to=d_to,
        since=since,
        until=until,
        scope=repo.Scope.of(q),
        mode=_val(getattr(q, "mode", None) or AnalyticsMode.ACTIVITY),
        cohort_hours=COHORT_AGE_HOURS[CohortAge(_val(age))],
        cohort_age=_val(age),
        basis=_val(getattr(q, "basis", None) or AnalyticsBasis.PUBLIC),
        include_overhead=bool(getattr(q, "include_overhead", False)),
        fx=float(settings.fx_usd_rub),
        settings=settings,
    )


def _range(ctx: _Ctx) -> Row:
    return {
        "date_from": ctx.d_from,
        "date_to": ctx.d_to,
        "days": (ctx.d_to - ctx.d_from).days + 1,
        "timezone": ctx.tz,
    }


def _filters(q: Any, model: type = AnalyticsFilters) -> Row:
    """Эхо фильтров запроса (только поля `model`, без параметров конкретного эндпоинта)."""
    return {name: getattr(q, name) for name in model.model_fields if hasattr(q, name)}


def _annotations(d_from: date, d_to: date) -> list[Row]:
    if d_from <= YT_PUBLIC_VIEWS_CHANGE_DATE <= d_to:
        return [
            {"date": YT_PUBLIC_VIEWS_CHANGE_DATE, "code": "yt_public_views_change", "label": ANNOTATION_LABEL}
        ]
    return []


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _buckets(d_from: date, d_to: date, granularity: str) -> list[date]:
    out: list[date] = []
    if granularity == "week":
        d, step = _monday(d_from), 7
    else:
        d, step = d_from, 1
    while d <= d_to:
        out.append(d)
        d += timedelta(days=step)
    return out


def _bucket_of(day: date | None, granularity: str | None) -> date | None:
    if granularity is None or day is None:
        return None
    return _monday(day) if granularity == "week" else day


def _f(v: Any) -> float | None:
    return float(v) if v is not None else None


def _hours_since(now: datetime, t: datetime | None) -> float | None:
    if t is None:
        return None
    return (now - t).total_seconds() / 3600.0


# ---------------------------------------------------------------- сбор слагаемых


@dataclass
class _Collected:
    comps: dict[Key, F.Components]
    engaged: bool = False  # хотя бы у одной строки есть вовлечённые просмотры
    cohort_info: Row | None = None
    pubs: list[Row] | None = None  # cohort: публикации когорты
    pub_values: dict[int, Row] | None = None  # cohort: значения зрелых роликов на возраст

    def get(self, key: Key = (None, None)) -> F.Components:
        return self.comps.get(key) or F.empty_components()


def _key(raw: Any, group: str | None) -> str | None:
    if group is None:
        return None
    return NONE_KEY if raw is None else str(raw)


def _put(out: dict[Key, F.Components], rows: Iterable[Row], group: str | None) -> None:
    for r in rows:
        F.add_components(out.setdefault((r["bucket"], _key(r["key"], group)), F.empty_components()), r)


def _spend_and_production(
    conn: Any, ctx: _Ctx, out: dict[Key, F.Components], group: str | None, bucket: str | None
) -> None:
    # накладные (без ролика) в группах — только если их просят в итог; в общих итогах — всегда
    # (поле overhead_usd), а в spend_usd их добавляет compute_totals по include_overhead
    with_overhead = group is None or ctx.include_overhead
    _put(
        out,
        repo.spend_rows(
            conn,
            scope=ctx.scope,
            tz=ctx.tz,
            since=ctx.since,
            until=ctx.until,
            cohort=ctx.cohort,
            group=group,
            bucket=bucket,
            include_overhead=with_overhead,
        ),
        group,
    )
    _put(
        out,
        repo.production_rows(
            conn,
            scope=ctx.scope,
            tz=ctx.tz,
            since=ctx.since,
            until=ctx.until,
            cohort=ctx.cohort,
            group=group,
            bucket=bucket,
        ),
        group,
    )


def _activity(
    conn: Any, ctx: _Ctx, *, group: str | None, bucket: str | None, levels: Sequence[str]
) -> _Collected:
    out: dict[Key, F.Components] = {}
    views = repo.views_rows(
        conn, scope=ctx.scope, d_from=ctx.d_from, d_to=ctx.d_to, group=group, bucket=bucket
    )
    _put(out, views, group)
    engaged = any(r.get("engaged_views") is not None for r in views)
    _put(
        out,
        repo.traffic_rows(
            conn, scope=ctx.scope, d_from=ctx.d_from, d_to=ctx.d_to, group=group, bucket=bucket, levels=levels
        ),
        group,
    )
    _put(
        out,
        repo.users_rows(
            conn,
            scope=ctx.scope,
            tz=ctx.tz,
            since=ctx.since,
            until=ctx.until,
            group=group,
            bucket=bucket,
            levels=levels,
        ),
        group,
    )
    _spend_and_production(conn, ctx, out, group, bucket)
    return _Collected(out, engaged=engaged)


def _views_at(
    conn: Any, pub_ids: Sequence[int], hours: Sequence[int | None]
) -> dict[tuple[int, int | None], Row]:
    """Просмотры, лайки и комментарии на возраст `h` (None — сейчас, последнее известное значение)."""
    out: dict[tuple[int, int | None], Row] = {}
    for r in repo.views_at_ages(conn, list(pub_ids), list(hours)):
        h = None if int(r["h"]) < 0 else int(r["h"])
        carry = h is None
        values: Row = {}
        for name, k0, k1 in (("views", "v0", "v1"), ("likes", "l0", "l1"), ("comments", "c0", "c1")):
            prev = (r["t0"], r[k0]) if r["t0"] is not None and r[k0] is not None else None
            nxt = (r["t1"], r[k1]) if r["t1"] is not None and r[k1] is not None else None
            v = bracket_value(r["published_at"], r["at"], prev, nxt, carry_forward=carry)
            values[name] = v
        out[(int(r["publication_id"]), h)] = values
    return out


def _pub_dim(p: Mapping[str, Any], group: str | None) -> str | None:
    if group is None:
        return None
    column = {
        "video": "publication_id",
        "channel": "channel_id",
        "niche": "niche_id",
        "product": "product_id",
        "campaign": "campaign_id",
        "variant": "banner_variant_id",
        "track": "track",
        "platform": "platform",
    }[group]
    return _key(p.get(column), group)


def _cohort(conn: Any, ctx: _Ctx, *, group: str | None, bucket: str | None) -> _Collected:
    out: dict[Key, F.Components] = {}
    hours = ctx.cohort_hours
    pubs = repo.cohort_publications(conn, scope=ctx.scope, tz=ctx.tz, since=ctx.since, until=ctx.until)
    mature = [p for p in pubs if hours is None or float(p["age_hours"] or 0) >= hours]
    ids = [int(p["publication_id"]) for p in mature]
    at = _views_at(conn, ids, [hours])
    traffic = repo.traffic_by_publication(conn, ids, hours=hours)
    values: dict[int, Row] = {}
    for p in mature:
        pid = int(p["publication_id"])
        v = at.get((pid, hours)) or {}
        comp: Row = {
            "views": round_count(v["views"]) if v.get("views") is not None else 0,
            "likes": round_count(v["likes"]) if v.get("likes") is not None else 0,
            "comments": round_count(v["comments"]) if v.get("comments") is not None else 0,
        }
        comp.update({k: x for k, x in (traffic.get(pid) or {}).items() if k != "publication_id"})
        values[pid] = {**comp, "views_known": v.get("views") is not None}
        key = (_bucket_of(p["day"], bucket), _pub_dim(p, group))
        F.add_components(out.setdefault(key, F.empty_components()), comp)
    _spend_and_production(conn, ctx, out, group, bucket)
    info = {
        "age": CohortAge(ctx.cohort_age),
        "videos": len(pubs),
        "videos_mature": len(mature),
        "immature": len(pubs) - len(mature),
    }
    return _Collected(out, engaged=False, cohort_info=info, pubs=pubs, pub_values=values)


def _collect(
    conn: Any,
    ctx: _Ctx,
    *,
    group: str | None = None,
    bucket: str | None = None,
    levels: Sequence[str] = LEVELS_TOTAL,
) -> _Collected:
    if ctx.cohort:
        return _cohort(conn, ctx, group=group, bucket=bucket)
    return _activity(conn, ctx, group=group, bucket=bucket, levels=levels)


def _use_engaged(ctx: _Ctx, col: _Collected) -> bool:
    return ctx.basis == AnalyticsBasis.ENGAGED.value and col.engaged


def _fallback(ctx: _Ctx, col: _Collected) -> bool:
    return ctx.basis == AnalyticsBasis.ENGAGED.value and not col.engaged


def _totals(ctx: _Ctx, c: Mapping[str, Any], *, engaged: bool) -> Row:
    comps = dict(c)
    if ctx.basis == AnalyticsBasis.ENGAGED.value and engaged:
        comps["views"] = comps.get("engaged_views") or 0
    return F.compute_totals(
        comps,
        fx_usd_rub=ctx.fx,
        include_overhead=ctx.include_overhead,
        engaged_available=engaged,
        currency=CURRENCY,
    )


def _period_users(conn: Any, ctx: _Ctx, group: str | None, levels: Sequence[str]) -> dict[str | None, int]:
    """Уникальные пользователи за весь период по группам (дневные строки не складываются)."""
    rows = repo.users_rows(
        conn,
        scope=ctx.scope,
        tz=ctx.tz,
        since=ctx.since,
        until=ctx.until,
        group=group,
        bucket=None,
        levels=levels,
    )
    return {_key(r["key"], group): int(r["users"] or 0) for r in rows}


def _attribution(conn: Any, ctx: _Ctx) -> Row:
    """Уровни атрибуции по времени события в периоде (§6.6)."""
    levels: dict[str, F.Components] = {lvl: F.empty_components() for lvl in ("exact", "channel", "direct")}
    rows = list(
        repo.traffic_rows(
            conn, scope=ctx.scope, d_from=ctx.d_from, d_to=ctx.d_to, group="level", bucket=None, levels=None
        )
    ) + list(
        repo.users_rows(
            conn,
            scope=ctx.scope,
            tz=ctx.tz,
            since=ctx.since,
            until=ctx.until,
            group="level",
            bucket=None,
            levels=None,
        )
    )
    for r in rows:
        if r["key"] in levels:
            F.add_components(levels[r["key"]], r)
    return {
        lvl: {
            "visitors": int(c["unique_visitors"]) + int(c["direct_bot_starts"]),
            "bot_starts": int(c["bot_starts"]),
            "users": int(c["users"]),
            "signups": int(c["signups"]),
            "first_payments": int(c["first_payments"]),
            "revenue_minor": int(c["revenue_minor"]),
        }
        for lvl, c in levels.items()
    }


# ---------------------------------------------------------------- подписи


def _labels(conn: Any, group: str | None, keys: Iterable[str | None]) -> dict[str, str]:
    real = sorted({k for k in keys if k is not None and k not in (NONE_KEY, OTHER_KEY)})
    if group == "track":
        out = {k: LABELS[JobKind].get(k, k) for k in real}
    elif group == "platform":
        out = {k: LABELS[Platform].get(k, k) for k in real}
    elif group == "outcome":
        out = {k: OUTCOME_LABELS.get(k, k) for k in real}
    elif group in ("provider", "step"):
        out = {k: k for k in real}
    elif group is None:
        out = {}
    else:
        found = repo.labels(conn, group, real)
        out = {k: found.get(k, k) for k in real}
    out[NONE_KEY] = NONE_LABELS.get(group or "", "Без значения")
    out[OTHER_KEY] = OTHER_LABEL
    return out


# ---------------------------------------------------------------- свежесть данных


def _warning(code: FreshnessWarningCode, message: str) -> Row:
    return {"code": code, "message": message}


def freshness(conn: Any) -> Row:
    """`DataFreshness`: опрос метрик, ролл-ап, коллекторы, квоты и предупреждения."""
    from autoshorts.services import metrics as metrics_service

    now = repo.db_now(conn)
    fc = repo.freshness_counts(conn, grace_min=METRICS_OVERDUE_GRACE_MIN)
    rollup_at = repo.last_rollup_at(conn)
    quota = metrics_service.quota_usage(conn, date_from=None, date_to=None, apis=None, cluster_ids=None)
    collectors = quota["collectors"]
    # YouTube Analytics (services/yt_analytics.py) пишет кривые удержания — время последней из них
    pulled = conn.execute("SELECT max(captured_at) AS t FROM retention_curves").fetchone()["t"]
    warnings: list[Row] = []
    for c in collectors:
        name = COLLECTOR_NAMES.get(str(c["collector"]), str(c["collector"]))
        state = str(c["state"])
        if c["collector"] == "manual":
            continue
        if state == "disabled" and int(c["publications_total"] or 0) > 0:
            warnings.append(
                _warning(
                    FreshnessWarningCode.COLLECTOR_DISABLED,
                    f"{name} выключен ({c['message'] or c['reason'] or 'не настроен'}): "
                    f"метрики {c['publications_total']} публикаций не собираются автоматически",
                )
            )
        elif state == "failing":
            warnings.append(
                _warning(
                    FreshnessWarningCode.COLLECTOR_FAILING,
                    f"{name} не работает: {c['last_error'] or c['message'] or 'ошибки подряд'}",
                )
            )
        elif state == "paused" and c["reason"] == "quota_exhausted":
            until = c["paused_until"]
            warnings.append(
                _warning(
                    FreshnessWarningCode.QUOTA_EXHAUSTED,
                    f"{name}: квота на сутки исчерпана"
                    + (f", опрос на паузе до {until:%d.%m %H:%M} UTC" if until else ""),
                )
            )
    if not any(w["code"] is FreshnessWarningCode.QUOTA_EXHAUSTED for w in warnings):
        for qd in quota["today"]:
            if qd["limit"] and int(qd["units"]) >= int(qd["limit"]):
                warnings.append(
                    _warning(
                        FreshnessWarningCode.QUOTA_EXHAUSTED,
                        f"Квота {qd['api']} ({qd['cluster_id']}) на сутки исчерпана: "
                        f"{qd['units']} из {qd['limit']}",
                    )
                )
    stale_n = int(fc["publications_stale"] or 0)
    if stale_n:
        warnings.append(
            _warning(FreshnessWarningCode.METRICS_OVERDUE, f"Опрос метрик просрочен у {stale_n} публикаций")
        )
    manual_n = int(fc["publications_manual_due"] or 0)
    if manual_n:
        warnings.append(
            _warning(
                FreshnessWarningCode.MANUAL_INPUT_DUE, f"{manual_n} публикаций ждут ручного ввода метрик"
            )
        )
    has_data = bool(int(fc["publications_live"] or 0) or fc["clicks_last_at"] or fc["conversions_last_at"])
    if rollup_at is None and has_data:
        warnings.append(
            _warning(
                FreshnessWarningCode.ROLLUP_STALE, "Ролл-ап ещё не считался: просмотры и трафик по дням пусты"
            )
        )
    elif rollup_at is not None and now - rollup_at > timedelta(minutes=ROLLUP_STALE_MIN):
        minutes = int((now - rollup_at).total_seconds() // 60)
        warnings.append(
            _warning(
                FreshnessWarningCode.ROLLUP_STALE,
                f"Ролл-ап не обновлялся {minutes} мин: цифры по дням отстают",
            )
        )
    age = fc["public_counts_age_min"]
    return {
        "computed_at": now,
        "metrics_polled_at": fc["metrics_polled_at"],
        "metrics_oldest_due_at": fc["metrics_oldest_due_at"],
        "public_counts_age_min": int(round(float(age))) if age is not None else None,
        "publications_stale": stale_n,
        "publications_manual_due": manual_n,
        "rollup_at": rollup_at,
        "clicks_last_at": fc["clicks_last_at"],
        "conversions_last_at": fc["conversions_last_at"],
        "analytics_pulled_at": pulled,
        "collectors": collectors,
        "quota": quota["today"],
        "stale": any(w["code"] in STALE_WARNINGS for w in warnings),
        "warnings": warnings,
    }


def _with_basis(fresh: Row, fallback: bool) -> Row:
    if not fallback:
        return fresh
    warn = _warning(
        FreshnessWarningCode.ENGAGED_VIEWS_UNAVAILABLE,
        "Вовлечённых просмотров (YouTube Analytics) в снимках пока нет: цифры по публичным просмотрам",
    )
    return {**fresh, "warnings": [*fresh["warnings"], warn]}


# ---------------------------------------------------------------- сводка и воронка


def _summary_parts(conn: Any, ctx: _Ctx) -> tuple[_Collected, Row, _Collected, Row]:
    cur = _collect(conn, ctx)
    prev_ctx = ctx.shifted()
    prev = _collect(conn, prev_ctx)
    return (
        cur,
        _totals(ctx, cur.get(), engaged=cur.engaged),
        prev,
        _totals(prev_ctx, prev.get(), engaged=prev.engaged),
    )


def summary(conn: Any, query: Any) -> Row:
    """`GET /analytics/summary` → `AnalyticsSummary`."""
    ctx = _ctx(conn, query)
    cur, totals, _prev, previous = _summary_parts(conn, ctx)
    fallback = _fallback(ctx, cur)
    return {
        "range": _range(ctx),
        "previous_range": _range(ctx.shifted()),
        "filters": _filters(query),
        "basis_fallback": fallback,
        "totals": totals,
        "previous": previous,
        "attribution": _attribution(conn, ctx),
        "cohort": cur.cohort_info if ctx.cohort else None,
        "data_freshness": _with_basis(freshness(conn), fallback),
        "annotations": _annotations(ctx.d_from, ctx.d_to),
    }


def _funnel_costs(totals: Mapping[str, Any]) -> Row:
    return {
        "spend_usd": totals["spend_usd"],
        "cost_per_visitor_usd": totals["cost_per_visitor_usd"],
        "cost_per_user_usd": totals["cost_per_user_usd"],
        "cost_per_bot_start_usd": totals["cost_per_bot_start_usd"],
        "cost_per_first_payment_usd": totals["cost_per_first_payment_usd"],
    }


def funnel(conn: Any, query: Any) -> Row:
    """`GET /analytics/funnel` → `AnalyticsFunnel`: две ветки (домен и бот) с ценой шага."""
    ctx = _ctx(conn, query)
    cur = _collect(conn, ctx)
    totals = _totals(ctx, cur.get(), engaged=cur.engaged)
    return {
        "range": _range(ctx),
        "filters": _filters(query),
        "basis_fallback": _fallback(ctx, cur),
        "views": int(totals["views"]),
        "branches": F.funnel_branches(totals),
        "attribution": _attribution(conn, ctx),
        "costs": _funnel_costs(totals),
    }


# ---------------------------------------------------------------- ряды


def _metric(totals: Mapping[str, Any], metric: str) -> float | None:
    v = totals.get(metric)
    return float(v) if v is not None else None


def _rank(keys: Iterable[str], value: Mapping[str, float | None]) -> list[str]:
    present = sorted((k for k in keys if value.get(k) is not None), key=lambda k: k)
    present.sort(key=lambda k: value[k] or 0.0, reverse=True)
    missing = sorted(k for k in keys if value.get(k) is None)
    return present + missing


def timeseries(conn: Any, query: Any) -> Row:
    """`GET /analytics/timeseries` → `AnalyticsTimeseries`: топ групп по итогу метрики, хвост — `other`."""
    ctx = _ctx(conn, query)
    metric = _val(query.metric)
    granularity = _val(query.granularity)
    group = None if _val(query.group_by) == "none" else _val(query.group_by)
    levels = LEVELS_VIDEO if group == "video" else LEVELS_TOTAL
    col = _collect(conn, ctx, group=group, bucket=granularity, levels=levels)
    buckets = _buckets(ctx.d_from, ctx.d_to, granularity)
    by_key: dict[str | None, dict[date | None, F.Components]] = defaultdict(dict)
    for (b, k), c in col.comps.items():
        if group == "video" and k == NONE_KEY:
            continue  # без ролика — не ролик (ссылки канала, брак без публикации)
        by_key[k][b] = c
    users = _period_users(conn, ctx, group, levels) if not ctx.cohort else None

    def period(k: str | None, parts: Iterable[F.Components], users_n: int | None) -> F.Components:
        total = F.sum_components(parts)
        if users_n is not None:
            total["users"] = users_n
        return total

    engaged = col.engaged
    period_totals = {
        k: _totals(
            ctx, period(k, parts.values(), users.get(k, 0) if users is not None else None), engaged=engaged
        )
        for k, parts in by_key.items()
    }
    if group is None:
        keys: list[str | None] = [None]
        rest: list[str | None] = []
    else:
        ranked = _rank(
            [k for k in by_key if k is not None], {k: _metric(period_totals[k], metric) for k in by_key if k}
        )
        keys, rest = list(ranked[: query.top]), list(ranked[query.top :])
    labels = _labels(conn, group, [k for k in keys if k])
    series: list[Row] = []

    def points(parts: Mapping[date | None, F.Components]) -> list[Row]:
        return [
            {
                "t": b,
                "v": _metric(_totals(ctx, parts.get(b) or F.empty_components(), engaged=engaged), metric),
            }
            for b in buckets
        ]

    for k in keys:
        parts = by_key.get(k, {})
        total = period_totals.get(k) or _totals(ctx, F.empty_components(), engaged=engaged)
        series.append(
            {
                "key": TOTAL_KEY if k is None else k,
                "label": METRIC_LABELS[AnalyticsMetric(metric)][0] if k is None else labels.get(k, k),
                "total": _metric(total, metric),
                "points": points(parts),
            }
        )
    if rest:
        merged: dict[date | None, F.Components] = defaultdict(F.empty_components)
        for k in rest:
            for b, c in by_key[k].items():
                F.add_components(merged[b], c)
        users_rest = sum(users.get(k, 0) for k in rest) if users is not None else None
        total = _totals(ctx, period(OTHER_KEY, merged.values(), users_rest), engaged=engaged)
        series.append(
            {
                "key": OTHER_KEY,
                "label": OTHER_LABEL,
                "total": _metric(total, metric),
                "points": points(merged),
            }
        )
    return {
        "metric": metric,
        "granularity": granularity,
        "group_by": _val(query.group_by),
        "top": int(query.top),
        "range": _range(ctx),
        "filters": _filters(query),
        "basis_fallback": _fallback(ctx, col),
        "series": series,
        "annotations": _annotations(ctx.d_from, ctx.d_to),
    }


# ---------------------------------------------------------------- разрезы


SORT_EXTRA = frozenset({"n_videos", "views_median", "views_72h_median", "label"})


def _parse_sort(sort: str, dimension: str) -> tuple[str, bool]:
    desc = sort.startswith("-")
    key = sort[1:] if desc else sort
    allowed = METRIC_KEYS | SORT_EXTRA | ({"published_at"} if dimension == "video" else set())
    if key not in allowed:
        raise ValidationFailed(
            f"Сортировка «{sort}» не поддерживается",
            details={
                "errors": [
                    {
                        "loc": ["query", "sort"],
                        "msg": "метрика AnalyticsMetric или n_videos, views_median, views_72h_median, label"
                        + (", published_at" if dimension == "video" else ""),
                        "type": "sort",
                    }
                ]
            },
        )
    return key, desc


def _sort_value(row: Row, key: str) -> Any:
    if key in METRIC_KEYS:
        return row["metrics"].get(key)
    if key == "label":
        return str(row["label"]).casefold()
    if key == "published_at":
        return (row.get("video") or {}).get("published_at")
    return row.get(key)


def _sort_rows(rows: list[Row], key: str, desc: bool) -> list[Row]:
    present = sorted((r for r in rows if _sort_value(r, key) is not None), key=lambda r: r["key"])
    present.sort(key=lambda r: _sort_value(r, key), reverse=desc)
    return present + [r for r in rows if _sort_value(r, key) is None]


def _verdict(v24: float | None, v72: float | None, v7d: float | None) -> str | None:
    if v7d is not None:
        return "d7"
    if v72 is not None:
        return "h72"
    if v24 is not None:
        return "h24"
    return None


def _video_info(conn: Any, ctx: _Ctx, pub: Row, ages: Mapping[tuple[int, int | None], Row]) -> Row:
    pid = int(pub["publication_id"])
    grace = timedelta(minutes=METRICS_OVERDUE_GRACE_MIN)

    def at(h: int) -> int | None:
        v = (ages.get((pid, h)) or {}).get("views")
        return round_count(v) if v is not None else None

    v24, v72, v7d = at(24), at(72), at(168)
    variant = None
    if pub.get("banner_variant_id") is not None and pub.get("variant_code") is not None:
        variant = {
            "id": int(pub["banner_variant_id"]),
            "code": pub["variant_code"],
            "name": pub["variant_name"],
        }
    age = _hours_since(ctx.now, pub.get("published_at"))
    return {
        "publication_id": pid,
        "job_id": int(pub["job_id"]),
        "video_uid": pub["video_uid"],
        "title": pub.get("title"),
        "url": pub.get("url"),
        "platform": pub["platform"],
        "platform_video_id": pub.get("platform_video_id"),
        "channel": {"id": pub["channel_id"], "title": pub.get("channel_title"), "platform": pub["platform"]},
        "niche_id": pub.get("niche_id"),
        "product_id": pub.get("product_id"),
        "campaign_id": pub.get("campaign_id"),
        "banner_variant": variant,
        "track": pub["track"],
        "status": pub["publication_status"],
        "published_at": pub.get("published_at"),
        "age_hours": round(age, 2) if age is not None else None,
        "latest_views": pub.get("latest_views"),
        "views_24h": v24,
        "views_72h": v72,
        "views_7d": v7d,
        "verdict": _verdict(v24, v72, v7d),
        "poster_url": f"/api/v1/jobs/{int(pub['job_id'])}/media/poster",
        "stale": bool(pub.get("next_poll_at") is not None and pub["next_poll_at"] < ctx.now - grace),
        "manual_due": bool(pub.get("manual_due_at") is not None and pub["manual_due_at"] <= ctx.now),
    }


def _breakdown_rows(
    conn: Any, ctx: _Ctx, dimension: str, totals: Mapping[str, Any]
) -> tuple[list[Row], bool]:
    """Все строки разреза (без сортировки и страниц) и признак отката на публичные просмотры."""
    from autoshorts.analytics.stats import group_vs_rest, ln_views

    levels = LEVELS_VIDEO if dimension == "video" else LEVELS_TOTAL
    col = _collect(conn, ctx, group=dimension, levels=levels)
    comps = {k: c for (_b, k), c in col.comps.items() if k is not None}
    if dimension == "video":
        comps.pop(NONE_KEY, None)
    engaged = col.engaged
    min_videos = int(ctx.settings.ab_min_videos_per_variant)
    min_age = int(ctx.settings.ab_min_age_hours)
    # ролики периода и их просмотры в выбранном режиме — для медиан, зрелости и p-value
    pub_views: dict[int, float | None] = {}
    if ctx.cohort:
        values = col.pub_values or {}
        cohort_ids = [int(p["publication_id"]) for p in col.pubs or []]
        for pid in cohort_ids:
            v = values.get(pid)
            pub_views[pid] = float(v["views"]) if v and v.get("views_known") else None
    else:
        for r in repo.views_rows(
            conn, scope=ctx.scope, d_from=ctx.d_from, d_to=ctx.d_to, group="video", bucket=None
        ):
            if r["key"] is None:
                continue
            v = r["engaged_views"] if _use_engaged(ctx, col) else r["views"]
            pub_views[int(r["key"])] = float(v) if v is not None else None
    video_keys = [int(k) for k in comps] if dimension == "video" else []
    info = repo.publication_rows(conn, sorted(set(pub_views) | set(video_keys)))
    ages_needed = [
        pid for pid, p in info.items() if (_hours_since(ctx.now, p.get("published_at")) or 0) >= 24
    ]
    ages = _views_at(conn, ages_needed, [24, 72, 168] if dimension == "video" else [AB_HORIZON_HOURS])

    def is_mature(pid: int) -> bool:
        p = info.get(pid)
        return bool(p and (_hours_since(ctx.now, p.get("published_at")) or 0) >= min_age)

    def v72(pid: int) -> float | None:
        if not is_mature(pid):
            return None
        return (ages.get((pid, AB_HORIZON_HOURS)) or {}).get("views")

    members: dict[str, list[int]] = defaultdict(list)
    for pid in pub_views:
        p = info.get(pid)
        if p is None:
            continue
        members[_pub_dim(p, dimension) or NONE_KEY].append(pid)
    if dimension == "video":
        members = defaultdict(list, {k: [int(k)] for k in comps})
    p_values: dict[str, float | None] = {}
    if dimension != "video":
        ln = {
            k: [x for x in (ln_views(v72(pid)) for pid in ids) if x is not None]
            for k, ids in members.items()
            if k in comps
        }
        p_values = group_vs_rest(ln)
    labels = _labels(conn, dimension, comps)
    rows: list[Row] = []
    for k, c in comps.items():
        metrics = _totals(ctx, c, engaged=engaged)
        ids = members.get(k, [])
        views_list = [pub_views[pid] for pid in ids if pub_views.get(pid) is not None]
        if dimension == "video" and not views_list:
            views_list = [float(metrics["views"])]
        mature_ids = [pid for pid in ids if is_mature(pid)]
        v72s = [x for x in (v72(pid) for pid in mature_ids) if x is not None]
        video = None
        if dimension == "video" and int(k) in info:
            video = _video_info(conn, ctx, info[int(k)], ages)
        rows.append(
            {
                "key": k,
                "label": labels.get(k, k),
                "n_videos": 1 if dimension == "video" else len(ids),
                "n_mature": len(mature_ids),
                "metrics": metrics,
                "views_median": F.median(views_list),
                "views_p25": F.percentile(views_list, 25),
                "views_p75": F.percentile(views_list, 75),
                "views_72h_median": F.median(v72s),
                "share_views": F.ratio(metrics["views"], totals.get("views")),
                "share_spend": F.ratio(metrics["spend_usd"], totals.get("spend_usd")),
                "needs_more_n": len(mature_ids) < min_videos,
                "p_value": p_values.get(k),
                "video": video,
            }
        )
    return rows, _fallback(ctx, col)


def breakdown(conn: Any, query: Any) -> Row:
    """`GET /analytics/breakdown` → `AnalyticsBreakdown`."""
    dimension = _val(query.dimension)
    sort_key, desc = _parse_sort(query.sort, dimension)
    ctx = _ctx(conn, query)
    cur = _collect(conn, ctx)
    totals = _totals(ctx, cur.get(), engaged=cur.engaged)
    rows, fallback = _breakdown_rows(conn, ctx, dimension, totals)
    rows = _sort_rows(rows, sort_key, desc)
    offset, limit = int(query.offset), int(query.limit)
    return {
        "dimension": dimension,
        "range": _range(ctx),
        "filters": _filters(query),
        "basis_fallback": fallback,
        "sort": query.sort,
        "total_rows": len(rows),
        "limit": limit,
        "offset": offset,
        "min_videos_for_stats": int(ctx.settings.ab_min_videos_per_variant),
        "rows": rows[offset : offset + limit],
        "totals": totals,
    }


# ---------------------------------------------------------------- когорты


def _cohort_metric(metric: str, v: Mapping[str, Any]) -> float | None:
    visitors = int(v.get("unique_visitors") or 0) + int(v.get("direct_bot_starts") or 0)
    if metric == "views":
        return _f(v.get("views"))
    if metric == "visitors":
        return float(visitors)
    if metric == "ctr_per_1k_views":
        return F.per_1k(visitors, v.get("views"))
    return float(v.get(metric) or 0)


def _aggregate(values: list[float], how: str) -> float | None:
    if not values:
        return None
    if how == CohortAggregate.SUM.value:
        return round(sum(values), 6)
    if how == CohortAggregate.MEAN.value:
        return round(sum(values) / len(values), 6)
    return F.median(values)


def cohorts(conn: Any, query: Any) -> Row:
    """`GET /analytics/cohorts` → `AnalyticsCohorts`: когорта публикации × возраст (только зрелые ролики)."""
    ctx = _ctx(conn, query)
    metric = _val(query.metric)
    granularity = _val(query.granularity)
    how = _val(query.aggregate)
    pubs = repo.cohort_publications(conn, scope=ctx.scope, tz=ctx.tz, since=ctx.since, until=ctx.until)
    ages_h = [h for _, h in COHORT_AGES]
    reached = {h: [int(p["publication_id"]) for p in pubs if float(p["age_hours"] or 0) >= h] for h in ages_h}
    at = _views_at(conn, reached[ages_h[0]], ages_h)
    traffic: dict[int, dict[int, Row]] = {}
    if metric != CohortMetric.VIEWS.value:
        for h in ages_h:
            traffic[h] = repo.traffic_by_publication(conn, reached[h], hours=h)
    by_cohort: dict[date, list[Row]] = defaultdict(list)
    for p in pubs:
        by_cohort[_bucket_of(p["day"], granularity) or p["day"]].append(p)
    rows: list[Row] = []
    for cohort_day in sorted(by_cohort):
        members = by_cohort[cohort_day]
        values: Row = {}
        mature: Row = {}
        for name, h in COHORT_AGES:
            vals: list[Row] = []
            for p in members:
                pid = int(p["publication_id"])
                if float(p["age_hours"] or 0) < h:
                    continue
                v: Row = dict((traffic.get(h) or {}).get(pid) or {})
                views = (at.get((pid, h)) or {}).get("views")
                v["views"] = views
                vals.append(v)
            mature[name] = len(vals)
            if metric == CohortMetric.CTR_PER_1K_VIEWS.value and how == CohortAggregate.SUM.value:
                visitors = sum(
                    int(v.get("unique_visitors") or 0) + int(v.get("direct_bot_starts") or 0) for v in vals
                )
                views_sum = sum(float(v["views"]) for v in vals if v.get("views") is not None)
                values[name] = F.per_1k(visitors, views_sum) if vals else None
            else:
                xs = [x for x in (_cohort_metric(metric, v) for v in vals) if x is not None]
                values[name] = _aggregate(xs, how)
        rows.append({"cohort": cohort_day, "n_videos": len(members), "values": values, "mature": mature})
    return {
        "metric": metric,
        "granularity": granularity,
        "aggregate": how,
        "range": _range(ctx),
        "filters": _filters(query),
        "basis_fallback": ctx.basis == AnalyticsBasis.ENGAGED.value,
        "rows": rows,
    }


# ---------------------------------------------------------------- стоимость


def costs(conn: Any, query: Any) -> Row:
    """`GET /analytics/costs` → `AnalyticsCosts`: честная цена опубликованного ролика, расход по группам."""
    ctx = _ctx(conn, query)
    group = _val(query.group_by)
    granularity = _val(query.granularity)
    common = {"scope": ctx.scope, "tz": ctx.tz, "since": ctx.since, "until": ctx.until, "cohort": ctx.cohort}
    total_rows = repo.spend_rows(conn, **common, group=None, bucket=None, include_overhead=True)
    t: Row = dict(total_rows[0]) if total_rows else {}
    prod_rows = repo.production_rows(conn, **common, group=None, bucket=None)
    p: Row = dict(prod_rows[0]) if prod_rows else {}
    overhead = float(t.get("overhead_usd") or 0.0)
    spend = float(t.get("spend_usd") or 0.0) + (overhead if ctx.include_overhead else 0.0)
    produced = int(p.get("videos_produced") or 0)
    published = int(p.get("videos_published") or 0)
    totals = {
        "spend_usd": round(spend, F.MONEY_DIGITS),
        "spend_rub": round(spend * ctx.fx, 2),
        "fx_usd_rub": ctx.fx,
        "overhead_usd": round(overhead, F.MONEY_DIGITS),
        "published_spend_usd": round(float(t.get("published_spend_usd") or 0.0), F.MONEY_DIGITS),
        "rejected_spend_usd": round(float(t.get("rejected_spend_usd") or 0.0), F.MONEY_DIGITS),
        "in_progress_spend_usd": round(float(t.get("in_progress_spend_usd") or 0.0), F.MONEY_DIGITS),
        "videos_produced": produced,
        "videos_published": published,
        "videos_rejected": int(p.get("videos_rejected") or 0),
        "videos_failed": int(p.get("videos_failed") or 0),
        "cost_per_published_video_usd": F.cost_per(spend, published),
        "cost_per_video_usd": F.cost_per(spend, produced),
    }

    def amount(r: Mapping[str, Any]) -> float:
        return float(r.get("spend_usd") or 0.0) + float(r.get("overhead_usd") or 0.0)

    grouped = repo.spend_rows(conn, **common, group=group, bucket=None, include_overhead=ctx.include_overhead)
    by_group: dict[str, Row] = {}
    for r in grouped:
        k = _key(r["key"], group) or NONE_KEY
        g = by_group.setdefault(k, {"spend": 0.0, "calls": 0})
        g["spend"] += amount(r)
        g["calls"] += int(r.get("calls") or 0)
    order = _rank(list(by_group), {k: g["spend"] for k, g in by_group.items()})
    labels = _labels(conn, group, order)
    group_total = sum(g["spend"] for g in by_group.values())
    groups = [
        {
            "key": k,
            "label": labels.get(k, k),
            "spend_usd": round(by_group[k]["spend"], F.MONEY_DIGITS),
            "calls": by_group[k]["calls"],
            "share": F.ratio(by_group[k]["spend"], group_total),
        }
        for k in order
    ]
    buckets = _buckets(ctx.d_from, ctx.d_to, granularity)
    per_bucket: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    for r in repo.spend_rows(
        conn, **common, group=group, bucket=granularity, include_overhead=ctx.include_overhead
    ):
        per_bucket[_key(r["key"], group) or NONE_KEY][r["bucket"]] += amount(r)
    top, rest = order[: int(query.top)], order[int(query.top) :]
    series: list[Row] = []

    def line(key: str, label: str, parts: Mapping[date, float]) -> Row:
        return {
            "key": key,
            "label": label,
            "total": round(sum(parts.values()), F.MONEY_DIGITS),
            "points": [{"t": b, "v": round(parts.get(b, 0.0), F.MONEY_DIGITS)} for b in buckets],
        }

    for k in top:
        series.append(line(k, labels.get(k, k), per_bucket.get(k, {})))
    if rest:
        merged: dict[date, float] = defaultdict(float)
        for k in rest:
            for b, v in per_bucket.get(k, {}).items():
                merged[b] += v
        series.append(line(OTHER_KEY, OTHER_LABEL, merged))
    return {
        "range": _range(ctx),
        "filters": _filters(query),
        "granularity": granularity,
        "group_by": group,
        "totals": totals,
        "series": series,
        "groups": groups,
        "annotations": _annotations(ctx.d_from, ctx.d_to),
    }


# ---------------------------------------------------------------- карточка ролика


def _ladder(
    published_at: datetime, captured: list[datetime], schedule: Sequence[float], now: datetime
) -> list[Row]:
    dues = [published_at + timedelta(hours=h) for h in schedule]
    steps: list[Row] = []
    for i, (h, due) in enumerate(zip(schedule, dues, strict=True)):
        start = due - LADDER_TOLERANCE
        end = dues[i + 1] - LADDER_TOLERANCE if i + 1 < len(dues) else None
        hit = next((t for t in captured if t >= start and (end is None or t < end)), None)
        if hit is not None:
            status = "done"
        elif i + 1 < len(dues) and dues[i + 1] <= now:
            status = "missed"
        elif due <= now:
            status = "due"
        else:
            status = "future"
        steps.append({"step": i, "age_hours": float(h), "due_at": due, "captured_at": hit, "status": status})
    return steps


def video(conn: Any, publication_id: int, query: Any) -> Row:
    """`GET /analytics/videos/{id}` → `VideoAnalytics` · 404 publication_not_found."""
    from autoshorts.services import metrics as metrics_service
    from autoshorts.services import publications as publications_service

    row = publications_service.require(conn, publication_id)
    pid = int(row["id"])
    settings = _settings(conn)
    tz = settings.business_timezone
    fx = float(settings.fx_usd_rub)
    now = repo.db_now(conn)
    today = repo.business_today(conn, tz)
    publication = publications_service.publication_view(conn, row)
    publication["freshness"] = metrics_service.publication_freshness(conn, pid)
    source = metrics_service.latest_source_map(conn, [pid]).get(pid)
    if source:
        publication["latest"]["source"] = source
    snaps = repo.publication_snapshots(conn, pid)
    published_at = row.get("published_at") or row["created_at"]

    def series_of(field: str) -> list[tuple[datetime, float]]:
        return build_series(((s["captured_at"], s[field]) for s in snaps), published_at=published_at)

    def latest(field: str) -> int:
        v = value_at(series_of(field), now, carry_forward=True)
        return round_count(v) if v is not None else 0

    engaged_v = row.get("latest_engaged_views")
    if engaged_v is None:
        engaged_v = next(
            (s["engaged_views"] for s in reversed(snaps) if s["engaged_views"] is not None), None
        )
    engaged = engaged_v is not None
    traffic = repo.traffic_by_publication(conn, [pid], hours=None).get(pid) or {}
    job_id = int(row["job_id"])
    items = repo.job_cost_items(conn, job_id)
    shares = {"job": 0.0, "source": 0.0, "brief": 0.0}
    merged_items: dict[tuple[str, str], Row] = {}
    for it in items:
        shares[it["share"]] = shares.get(it["share"], 0.0) + float(it["cost_usd"] or 0.0)
        m = merged_items.setdefault((it["step_name"], it["provider"]), {"calls": 0, "cost_usd": 0.0})
        m["calls"] += int(it["calls"] or 0)
        m["cost_usd"] += float(it["cost_usd"] or 0.0)
    spend = sum(shares.values())
    comps: Row = {
        "videos_produced": 1,
        "videos_published": 1 if row.get("published_at") is not None else 0,
        "views": latest("views"),
        "engaged_views": int(engaged_v) if engaged else 0,
        "likes": latest("likes"),
        "comments": latest("comments"),
        **{k: v for k, v in traffic.items() if k != "publication_id"},
        "spend_usd": spend,
    }
    basis = _val(getattr(query, "basis", None) or AnalyticsBasis.PUBLIC)
    if basis == AnalyticsBasis.ENGAGED.value and engaged:
        comps["views"] = int(engaged_v)
    totals = F.compute_totals(comps, fx_usd_rub=fx, engaged_available=engaged, currency=CURRENCY)
    # просмотры на ровный возраст и коридор канала (ролики канала за 90 дней, без этого)
    views_series = series_of("views")
    peers = repo.channel_publications(conn, row["channel_id"], days=CHANNEL_CORRIDOR_DAYS, exclude_id=pid)
    corridor: dict[int, list[float]] = defaultdict(list)
    for r in repo.views_at_ages(conn, peers, list(AGE_POINT_HOURS.values())):
        if r["at"] > now:
            continue
        prev = (r["t0"], r["v0"]) if r["t0"] is not None else None
        nxt = (r["t1"], r["v1"]) if r["t1"] is not None else None
        v = bracket_value(r["published_at"], r["at"], prev, nxt)
        if v is not None:
            corridor[int(r["h"])].append(v)
    age_views = []
    for point, hours in AGE_POINT_HOURS.items():
        av = value_at_age(views_series, published_at, hours, now=now)
        peer = corridor.get(hours, [])
        age_views.append(
            {
                "age": point,
                "age_hours": hours,
                "views": round(av.value, 2) if av.value is not None else None,
                "interpolated": av.interpolated,
                "reached": av.reached,
                "channel_p25": F.percentile(peer, 25),
                "channel_median": F.median(peer),
                "channel_p75": F.percentile(peer, 75),
            }
        )
    age_h = _hours_since(now, published_at) or 0.0
    gran = getattr(query, "traffic_granularity", None)
    granularity = _val(gran) if gran else ("hour" if age_h < HOURLY_TRAFFIC_MAX_AGE_H else "day")
    traffic_points = [
        {
            k: (r[k] if k == "t" else int(r[k] or 0))
            for k in (
                "t",
                "clicks",
                "landing_views",
                "unique_visitors",
                "to_telegram",
                "to_site",
                "direct_bot_starts",
                "bot_starts",
                "signups",
                "first_payments",
                "revenue_minor",
            )
        }
        for r in repo.publication_traffic_series(conn, pid, unit=granularity, tz=tz)
    ]
    conversions = [
        {
            "event_type": r["event_type"],
            "count": int(r["count"] or 0),
            "unique_users": int(r["unique_users"] or 0),
            "via_landing": int(r["via_landing"] or 0),
            "amount_minor": int(r["amount_minor"] or 0),
        }
        for r in repo.publication_conversion_counts(conn, pid)
    ]
    recent = [
        {
            "occurred_at": r["occurred_at"],
            "event_type": r["event_type"],
            "via_landing": bool(r["via_landing"]),
            "amount_minor": int(r["amount_minor"]) if r["amount_minor"] is not None else None,
            "currency": (str(r["currency"]).strip() or None) if r["currency"] is not None else None,
        }
        for r in repo.publication_recent_conversions(conn, pid, limit=RECENT_CONVERSIONS)
    ]
    from autoshorts.services.metrics import poll_schedule_hours

    schedule = poll_schedule_hours(settings.metrics_poll_ladder_hours)
    ladder = _ladder(published_at, sorted(s["captured_at"] for s in snaps), schedule, now)
    first_day = published_at.astimezone(ZoneInfo(tz)).date()
    return {
        "publication": publication,
        "totals": totals,
        "snapshots": [metrics_service.snapshot_view(s) for s in snaps],
        "ladder": ladder,
        "age_views": age_views,
        "traffic_granularity": granularity,
        "traffic": traffic_points,
        "conversions": conversions,
        "recent_conversions": recent,
        "funnel": F.funnel_branches(totals),
        "costs": {
            "job_cost_usd": round(shares["job"], F.MONEY_DIGITS),
            "source_share_usd": round(shares["source"], F.MONEY_DIGITS),
            "brief_share_usd": round(shares["brief"], F.MONEY_DIGITS),
            "total_usd": round(spend, F.MONEY_DIGITS),
            "total_rub": round(spend * fx, 2),
            "items": [
                {
                    "step_name": step,
                    "provider": provider,
                    "calls": m["calls"],
                    "cost_usd": round(m["cost_usd"], F.MONEY_DIGITS),
                }
                for (step, provider), m in sorted(
                    merged_items.items(), key=lambda kv: (-kv[1]["cost_usd"], kv[0])
                )
            ],
            "cost_per_visitor_usd": totals["cost_per_visitor_usd"],
            "cost_per_user_usd": totals["cost_per_user_usd"],
            "cost_per_bot_start_usd": totals["cost_per_bot_start_usd"],
            "cost_per_first_payment_usd": totals["cost_per_first_payment_usd"],
            "revenue_minor": totals["revenue_minor"],
            "romi": totals["romi"],
        },
        "annotations": _annotations(first_day, today),
    }


# ---------------------------------------------------------------- A/B


def experiment(conn: Any, campaign_id: int, platform: Sequence[Any] | None = None) -> Row:
    """`GET /campaigns/{id}/experiment` → `Experiment` · 404 campaign_not_found."""
    from autoshorts.analytics.stats import ExperimentInput, VariantInput, build_experiment

    camp = repo.campaign(conn, campaign_id)
    if camp is None:
        raise NotFound("campaign", "Кампания не найдена")
    settings = _settings(conn)
    min_age = int(settings.ab_min_age_hours)
    min_videos = int(settings.ab_min_videos_per_variant)
    platforms = [_val(p) for p in (platform or [])]
    variants = repo.campaign_variants(conn, campaign_id)
    pubs = repo.campaign_publications(conn, campaign_id, platforms)
    known = {int(v["id"]) for v in variants}
    pubs = [p for p in pubs if int(p["banner_variant_id"]) in known]
    ids = [int(p["publication_id"]) for p in pubs]
    reached = [int(p["publication_id"]) for p in pubs if float(p["age_hours"] or 0) >= AB_HORIZON_HOURS]
    at72 = _views_at(conn, reached, [AB_HORIZON_HOURS])
    t72 = repo.traffic_by_publication(conn, ids, hours=AB_HORIZON_HOURS)
    life = repo.traffic_by_publication(conn, ids, hours=None)
    spend = repo.spend_by_job(conn, sorted({int(p["job_id"]) for p in pubs}))
    inputs: list[VariantInput] = []
    for v in variants:
        vid = int(v["id"])
        mine = [p for p in pubs if int(p["banner_variant_id"]) == vid]
        points = []
        for p in mine:
            pid = int(p["publication_id"])
            views = (at72.get((pid, AB_HORIZON_HOURS)) or {}).get("views")
            tr = t72.get(pid) or {}
            points.append(
                {
                    "publication_id": pid,
                    "video_uid": p["video_uid"],
                    "channel_id": p["channel_id"],
                    "platform": p["platform"],
                    "published_at": p["published_at"],
                    "age_hours": _f(p["age_hours"]),
                    "views_72h": round_count(views) if views is not None else None,
                    "visitors_72h": int(tr.get("unique_visitors") or 0)
                    + int(tr.get("direct_bot_starts") or 0),
                }
            )
        lt = [life.get(int(p["publication_id"])) or {} for p in mine]
        inputs.append(
            VariantInput(
                id=vid,
                code=v["code"],
                name=v["name"],
                status=v["status"],
                weight=int(v["weight"] or 0),
                points=points,
                bot_starts=sum(int(x.get("bot_starts") or 0) for x in lt),
                users=sum(int(x.get("users") or 0) for x in lt),
                first_payments=sum(int(x.get("first_payments") or 0) for x in lt),
                revenue_minor=sum(int(x.get("revenue_minor") or 0) for x in lt),
                spend_usd=sum(spend.get(j, 0.0) for j in {int(p["job_id"]) for p in mine}),
                visitors_lifetime=sum(
                    int(x.get("unique_visitors") or 0) + int(x.get("direct_bot_starts") or 0) for x in lt
                ),
            )
        )
    return build_experiment(
        ExperimentInput(
            campaign_id=int(camp["id"]),
            campaign_name=camp["name"],
            product_id=camp["product_id"],
            campaign_status=camp["status"],
            variants=inputs,
            min_videos_per_variant=min_videos,
            min_age_hours=min_age,
            computed_at=repo.db_now(conn),
        )
    )


# ---------------------------------------------------------------- Обзор


def _onboarding(conn: Any) -> Row:
    from autoshorts.services.secrets import is_set, status_map

    oc = repo.onboarding_counts(conn)
    db_status = status_map(conn)
    yt_names = {YOUTUBE_API_KEY_SECRET_DEFAULT, *(oc["cluster_key_secrets"] or [])}
    out = {
        "products": int(oc["products"] or 0),
        "channels": int(oc["channels"] or 0),
        "publications": int(oc["publications"] or 0),
        "conversions": int(oc["conversions"] or 0),
        "youtube_key_set": any(is_set(n, db_status) for n in yt_names),
        "webhook_secret_set": any(is_set(n, db_status) for n in (oc["webhook_secrets"] or [])),
    }
    out["done"] = all(bool(v) for v in out.values())
    return out


def _alerts(conn: Any) -> tuple[list[Row], int]:
    from autoshorts.repos import alerts as alerts_repo

    rows = alerts_repo.list_alerts(conn, statuses=["open"])
    rows = sorted(
        rows,
        key=lambda a: (SEVERITY_ORDER.get(str(a["severity"]), 9), -a["opened_at"].timestamp(), -int(a["id"])),
    )
    fields = (
        "id",
        "code",
        "severity",
        "status",
        "title",
        "details",
        "opened_at",
        "last_seen_at",
        "resolved_at",
        "acked_by",
    )
    items = [
        {f: (a[f] if f != "details" else (a[f] or {})) for f in fields} for a in rows[:OVERVIEW_ALERTS_LIMIT]
    ]
    return items, alerts_repo.count_open(conn)


def _experiments_ready(conn: Any) -> int:
    """Кампании с готовым вердиктом A/B. Сбой расчёта одной кампании не роняет «Пульт»: она пропускается
    с предупреждением в журнале (сама кампания по-прежнему покажет ошибку на своём экране)."""
    n = 0
    for cid in repo.active_campaign_ids(conn):
        try:
            ready = experiment(conn, cid)["ready"]
        except Exception:  # noqa: BLE001 - счётчик «Требует тебя» не должен ломать Обзор
            log.warning("overview: A/B кампании %s не посчитан", cid, exc_info=True)
            continue
        if ready:
            n += 1
    return n


def overview(conn: Any, query: Any) -> Row:
    """`GET /overview` → `Overview`: «Пульт» одним запросом (режим всегда activity)."""
    from autoshorts.services import budget as budget_service

    ctx = _ctx(conn, query, default_days=OVERVIEW_DEFAULT_DAYS)
    cur, totals, _prev, previous = _summary_parts(conn, ctx)
    daily = _collect(conn, ctx, bucket="day")
    days = _buckets(ctx.d_from, ctx.d_to, "day")
    per_day = {b: _totals(ctx, daily.get((b, None)), engaged=daily.engaged) for b in days}
    series = [
        {"metric": m, "points": [{"t": b, "v": _metric(per_day[b], m)} for b in days]} for m in KPI_METRICS
    ]
    rows, _ = _breakdown_rows(conn, ctx, "video", totals)
    top = _sort_rows(rows, "views", True)[:OVERVIEW_TOP_VIDEOS]
    todo = dict(repo.todo_counts(conn))
    todo = {k: int(v or 0) for k, v in todo.items()}
    todo["experiments_ready"] = _experiments_ready(conn)
    alerts, alerts_open = _alerts(conn)
    fallback = _fallback(ctx, cur)
    return {
        "range": _range(ctx),
        "previous_range": _range(ctx.shifted()),
        "filters": _filters(query, OverviewFilters),
        "basis_fallback": fallback,
        "kpi": {m: F.kpi_value(totals.get(m), previous.get(m)) for m in KPI_METRICS},
        "series": series,
        "funnel": F.funnel_branches(totals),
        "todo": todo,
        "top_videos": top,
        "alerts": alerts,
        "alerts_open": alerts_open,
        "budget": budget_service.get_budget(conn),
        "data_freshness": _with_basis(freshness(conn), fallback),
        "onboarding": _onboarding(conn),
        "annotations": _annotations(ctx.d_from, ctx.d_to),
    }


# ---------------------------------------------------------------- CSV


def _bad_param(name: str, message: str) -> ValidationFailed:
    return ValidationFailed(
        message, details={"errors": [{"loc": ["query", name], "msg": message, "type": "missing"}]}
    )


def _mlabel(metric: str, ru: bool) -> str:
    return METRIC_LABELS[AnalyticsMetric(metric)][0] if ru else metric


def _csv_summary(conn: Any, q: AnalyticsQuery, ru: bool) -> tuple[list[str], list[list[Any]]]:
    data = summary(conn, q)
    header = (
        ["Метрика", "Значение", "Прошлый период", "Разница", "Разница, %"]
        if ru
        else ["metric", "value", "previous", "delta", "delta_pct"]
    )
    rows = []
    for m in AnalyticsMetric:
        cur, prev = data["totals"].get(m.value), data["previous"].get(m.value)
        kv = F.kpi_value(cur, prev)
        rows.append([_mlabel(m.value, ru), cur, prev, kv["delta"], kv["delta_pct"]])
    return header, rows


def _csv_timeseries(conn: Any, q: TimeseriesQuery, ru: bool) -> tuple[list[str], list[list[Any]]]:
    data = timeseries(conn, q)
    series = data["series"]
    header = ["Дата" if ru else "t"] + [(s["label"] if ru else s["key"]) for s in series]
    stamps = [p["t"] for p in series[0]["points"]] if series else []
    rows = [[t] + [s["points"][i]["v"] for s in series] for i, t in enumerate(stamps)]
    return header, rows


def _csv_breakdown(conn: Any, q: BreakdownQuery, ru: bool) -> tuple[list[str], list[list[Any]]]:
    data = breakdown(conn, q)
    dim = data["dimension"]
    is_video = dim == AnalyticsDimension.VIDEO.value
    video_cols = [
        ("title", "Заголовок"),
        ("platform", "Площадка"),
        ("channel_id", "Канал"),
        ("track", "Трек"),
        ("published_at", "Опубликован"),
        ("url", "Ссылка"),
        ("views_24h", "Просмотры за 24 ч"),
        ("views_72h", "Просмотры за 72 ч"),
        ("views_7d", "Просмотры за 7 дней"),
    ]
    tail_cols = [
        ("views_median", "Медиана просмотров на ролик"),
        ("views_p25", "Просмотры на ролик, p25"),
        ("views_p75", "Просмотры на ролик, p75"),
        ("views_72h_median", "Медиана просмотров за 72 ч"),
        ("share_views", "Доля просмотров"),
        ("share_spend", "Доля расхода"),
        ("p_value", "p-value"),
    ]
    header = (
        ["Ключ", DIMENSION_LABELS[AnalyticsDimension(dim)], "Роликов", "Зрелых роликов"]
        if ru
        else ["key", "label", "n_videos", "n_mature"]
    )
    if is_video:
        header += [ru_name if ru else key for key, ru_name in video_cols]
    header += [_mlabel(m.value, ru) for m in AnalyticsMetric]
    header += [ru_name if ru else key for key, ru_name in tail_cols]
    rows = []
    for r in data["rows"]:
        line: list[Any] = [r["key"], r["label"], r["n_videos"], r["n_mature"]]
        if is_video:
            v = r["video"] or {}
            line += [
                v.get("title"),
                v.get("platform"),
                (v.get("channel") or {}).get("id"),
                v.get("track"),
                v.get("published_at"),
                v.get("url"),
                v.get("views_24h"),
                v.get("views_72h"),
                v.get("views_7d"),
            ]
        line += [r["metrics"].get(m.value) for m in AnalyticsMetric]
        line += [r[key] for key, _ in tail_cols]
        rows.append(line)
    return header, rows


def _csv_funnel(conn: Any, q: AnalyticsQuery, ru: bool) -> tuple[list[str], list[list[Any]]]:
    data = funnel(conn, q)
    header = (
        ["Ветка", "Шаг", "Значение", "Доля от прошлого шага", "На 1000 просмотров", "Цена шага, $"]
        if ru
        else ["branch", "step", "value", "rate_from_prev", "per_1k_views", "cost_per_usd"]
    )
    names = {"domain": "Домен", "bot": "Бот"}
    rows = []
    for branch in ("domain", "bot"):
        for s in data["branches"][branch]:
            code = _val(s["code"])
            rows.append(
                [
                    names[branch] if ru else branch,
                    _mlabel(code, ru),
                    s["value"],
                    s["rate_from_prev"],
                    s["per_1k_views"],
                    s["cost_per_usd"],
                ]
            )
    return header, rows


def _csv_cohorts(conn: Any, q: CohortsQuery, ru: bool) -> tuple[list[str], list[list[Any]]]:
    data = cohorts(conn, q)
    ru_ages = {"h24": "24 ч", "h72": "72 ч", "d7": "7 дней", "d14": "14 дней", "d30": "30 дней"}
    names = [name for name, _ in COHORT_AGES]
    header = (
        ["Когорта", "Роликов", *(ru_ages[n] for n in names), *(f"Зрелых к {ru_ages[n]}" for n in names)]
        if ru
        else ["cohort", "n_videos", *names, *(f"mature_{n}" for n in names)]
    )
    rows = [
        [r["cohort"], r["n_videos"], *(r["values"][n] for n in names), *(r["mature"][n] for n in names)]
        for r in data["rows"]
    ]
    return header, rows


def _csv_costs(conn: Any, q: CostsQuery, ru: bool) -> tuple[list[str], list[list[Any]]]:
    data = costs(conn, q)
    header = (
        ["Ключ", "Группа", "Расход, $", "Вызовов", "Доля"]
        if ru
        else ["key", "label", "spend_usd", "calls", "share"]
    )
    rows = [[g["key"], g["label"], g["spend_usd"], g["calls"], g["share"]] for g in data["groups"]]
    return header, rows


def _csv_experiment(
    conn: Any, campaign_id: int, platform: Any, ru: bool
) -> tuple[list[str], list[list[Any]]]:
    data = experiment(conn, campaign_id, platform)
    cols = [
        ("variant", "Вариант"),
        ("publication_id", "Публикация"),
        ("video_uid", "Ролик"),
        ("channel_id", "Канал"),
        ("platform", "Площадка"),
        ("published_at", "Опубликован"),
        ("age_hours", "Возраст, ч"),
        ("mature", "Зрелый"),
        ("views_72h", "Просмотры за 72 ч"),
        ("ln_views_72h", "ln(1 + просмотры за 72 ч)"),
        ("visitors", "Посетители за 72 ч"),
        ("ctr_per_1k", "Посетители на 1000 просмотров"),
    ]
    header = [ru_name if ru else key for key, ru_name in cols]
    rows = []
    for v in data["variants"]:
        for p in v["points"]:
            rows.append([v["code"], *(p[key] for key, _ in cols[1:])])
    return header, rows


def _csv_video(conn: Any, publication_id: int, basis: Any, ru: bool) -> tuple[list[str], list[list[Any]]]:
    data = video(conn, publication_id, VideoAnalyticsQuery(basis=basis))
    cols = [
        ("captured_at", "Снят"),
        ("age_hours", "Возраст, ч"),
        ("views", "Просмотры"),
        ("likes", "Лайки"),
        ("comments", "Комментарии"),
        ("shares", "Репосты"),
        ("engaged_views", "Вовлечённые просмотры"),
        ("source", "Источник"),
        ("note", "Заметка"),
    ]
    header = [ru_name if ru else key for key, ru_name in cols]
    rows = [[s[key] for key, _ in cols] for s in data["snapshots"]]
    return header, rows


def export_csv(conn: Any, query: Any) -> tuple[bytes, str]:
    """`GET /analytics/export` → (тело CSV с BOM, латинское имя файла)."""
    report = AnalyticsReport(_val(query.report))
    dialect = CsvDialect(_val(query.dialect))
    ru = dialect is CsvDialect.EXCEL_RU
    if report is AnalyticsReport.EXPERIMENT and len(query.campaign_id) != 1:
        raise _bad_param("campaign_id", "Для выгрузки эксперимента нужен ровно один campaign_id")
    if report is AnalyticsReport.VIDEO and query.publication_id is None:
        raise _bad_param("publication_id", "Для выгрузки ролика нужен publication_id")
    settings = _settings(conn)
    tz = settings.business_timezone
    d_from, d_to = resolve_range(query.date_from, query.date_to, repo.business_today(conn, tz))
    base = _filters(query)
    if report is AnalyticsReport.SUMMARY:
        header, rows = _csv_summary(conn, AnalyticsQuery.model_validate(base), ru)
    elif report is AnalyticsReport.TIMESERIES:
        q = TimeseriesQuery.model_validate(
            {
                **base,
                "metric": query.metric,
                "granularity": query.granularity,
                "group_by": query.group_by,
                "top": query.top,
            }
        )
        header, rows = _csv_timeseries(conn, q, ru)
    elif report is AnalyticsReport.BREAKDOWN:
        q = BreakdownQuery.model_validate(
            {
                **base,
                "dimension": query.dimension,
                "sort": query.sort,
                "limit": BREAKDOWN_LIMIT_MAX,
                "offset": 0,
            }
        )
        header, rows = _csv_breakdown(conn, q, ru)
    elif report is AnalyticsReport.FUNNEL:
        header, rows = _csv_funnel(conn, AnalyticsQuery.model_validate(base), ru)
    elif report is AnalyticsReport.COHORTS:
        q = CohortsQuery.model_validate(
            {**base, "metric": query.cohort_metric, "granularity": query.granularity}
        )
        header, rows = _csv_cohorts(conn, q, ru)
    elif report is AnalyticsReport.COSTS:
        q = CostsQuery.model_validate(
            {**base, "granularity": query.granularity, "group_by": query.cost_group_by, "top": query.top}
        )
        header, rows = _csv_costs(conn, q, ru)
    elif report is AnalyticsReport.EXPERIMENT:
        header, rows = _csv_experiment(conn, int(query.campaign_id[0]), query.platform, ru)
    else:
        header, rows = _csv_video(conn, int(query.publication_id), query.basis, ru)
    return encode_csv(header, rows, dialect, tz=tz), csv_filename(report, d_from, d_to)
