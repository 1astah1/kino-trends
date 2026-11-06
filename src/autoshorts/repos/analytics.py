"""SQL аналитики (docs/ARCHITECTURE.md §6.6, §6.7; поток M4-B; перенос `dashboard_queries.py`).

Только чтение. Все запросы получают `conn` (строки — dict), бизнес-день — `(ts AT TIME ZONE tz)::date`
часами БД, параметры в интервалах и CASE — `CAST(%(x)s AS int)`.

**Факты и измерения.** Каждый источник отдаёт строки с одинаковыми колонками измерений
(`level, publication_id, channel_id, niche_id, product_id, campaign_id, banner_variant_id, track,
platform`) — поэтому фильтры (`Scope`) и группировка (`group`) одни на все источники:

* просмотры — `stats_daily_views` × `v_publication_dims` (уровень всегда `exact`);
* трафик — `stats_daily_traffic` × `link_dims` (измерения трекинг-ссылки: ролик, иначе канал, иначе
  только продукт) и `stats_daily_unattributed` (уровень `direct`, только продукт);
* пользователи — сырые `conversions` (`bot_start`), потому что уникальность за период не складывается
  из дневных строк;
* расход — `generation_events` × `job_dims` (ASR и LLM источника размазываются поровну по его клипам,
  разбор брифа — по роликам брифа; остальное без ролика — `overhead`);
* производство — `jobs` (сделано, отклонено, сбои) и `v_publication_dims` (опубликовано).

Измерение, которого у уровня нет (кампания у ссылки канала, канал у прямого трафика), — `NULL`: такие
строки не проходят фильтр по этому измерению и в группировке попадают в ключ `none`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

__all__ = [
    "GROUP_COLUMNS",
    "LINK_DIMS_CTE",
    "JOB_DIMS_CTE",
    "active_campaign_ids",
    "channel_publications",
    "quota_day",
    "Scope",
    "business_today",
    "campaign",
    "campaign_publications",
    "campaign_variants",
    "channel_stats",
    "cohort_publications",
    "collector_pub_counts",
    "collector_states",
    "db_now",
    "freshness_counts",
    "job_cost_items",
    "labels",
    "last_rollup_at",
    "onboarding_counts",
    "production_rows",
    "publication_conversion_counts",
    "publication_recent_conversions",
    "publication_rows",
    "publication_snapshots",
    "publication_traffic_series",
    "qc_pass_rate",
    "quota_today",
    "reject_reasons",
    "spend_by_job",
    "spend_rows",
    "todo_counts",
    "traffic_by_publication",
    "traffic_rows",
    "users_rows",
    "views_at_ages",
    "views_rows",
]

Row = dict[str, Any]

# ---------------------------------------------------------------- общее


def db_now(conn: Any) -> datetime:
    return conn.execute("SELECT now() AS t").fetchone()["t"]


def business_today(conn: Any, tz: str) -> date:
    return conn.execute("SELECT (now() AT TIME ZONE CAST(%(tz)s AS text))::date AS d", {"tz": tz}).fetchone()[
        "d"
    ]


GROUP_COLUMNS: dict[str, str] = {
    "video": "f.publication_id::text",
    "channel": "f.channel_id",
    "niche": "f.niche_id",
    "product": "f.product_id",
    "campaign": "f.campaign_id::text",
    "variant": "f.banner_variant_id::text",
    "track": "f.track",
    "platform": "f.platform",
    "level": "f.level",
    # только расход
    "provider": "f.provider",
    "step": "f.step_name",
    "outcome": "f.outcome",
}
"""Ключ группировки → выражение над фактами `f` (белый список: в SQL попадает только отсюда)."""


def _bucket(granularity: str | None) -> str:
    if granularity == "day":
        return "f.day"
    if granularity == "week":
        return "date_trunc('week', f.day)::date"
    return "NULL::date"


def _key(group: str | None) -> str:
    if group is None:
        return "NULL::text"
    return GROUP_COLUMNS[group]


@dataclass(frozen=True)
class Scope:
    """Фильтры аналитики (пусто — без ограничения). Значения идут параметрами, не в текст SQL."""

    platforms: tuple[str, ...] = ()
    channel_ids: tuple[str, ...] = ()
    niche_ids: tuple[str, ...] = ()
    product_ids: tuple[str, ...] = ()
    campaign_ids: tuple[int, ...] = ()
    variant_ids: tuple[int, ...] = ()
    tracks: tuple[str, ...] = ()

    @classmethod
    def of(cls, filters: Any) -> Scope:
        def vals(name: str) -> tuple[Any, ...]:
            return tuple(str(getattr(v, "value", v)) for v in (getattr(filters, name, None) or ()))

        return cls(
            platforms=vals("platform"),
            channel_ids=vals("channel_id"),
            niche_ids=vals("niche_id"),
            product_ids=vals("product_id"),
            campaign_ids=tuple(int(v) for v in (getattr(filters, "campaign_id", None) or ())),
            variant_ids=tuple(int(v) for v in (getattr(filters, "banner_variant_id", None) or ())),
            tracks=vals("track"),
        )

    @property
    def narrows_to_videos(self) -> bool:
        """Фильтр по измерению, которого нет у ссылок канала (кампания, вариант, трек)."""
        return bool(self.campaign_ids or self.variant_ids or self.tracks)

    def sql(self, alias: str = "f") -> tuple[str, dict[str, Any]]:
        conds: list[str] = []
        params: dict[str, Any] = {}
        spec: list[tuple[str, str, tuple[Any, ...], str]] = [
            ("sc_platform", "platform", self.platforms, "text[]"),
            ("sc_channel", "channel_id", self.channel_ids, "text[]"),
            ("sc_niche", "niche_id", self.niche_ids, "text[]"),
            ("sc_product", "product_id", self.product_ids, "text[]"),
            ("sc_campaign", "campaign_id", self.campaign_ids, "bigint[]"),
            ("sc_variant", "banner_variant_id", self.variant_ids, "bigint[]"),
            ("sc_track", "track", self.tracks, "text[]"),
        ]
        for pname, column, values, pg_type in spec:
            if values:
                conds.append(f"{alias}.{column} = ANY(CAST(%({pname})s AS {pg_type}))")
                params[pname] = list(values)
        return (" AND ".join(conds) if conds else "TRUE"), params


def _levels_sql(levels: Sequence[str] | None) -> tuple[str, dict[str, Any]]:
    if not levels:
        return "TRUE", {}
    return "f.level = ANY(CAST(%(sc_levels)s AS text[]))", {"sc_levels": list(levels)}


# Измерения трекинг-ссылки: ролик (публикация ссылки или единственная живая публикация job ссылки),
# иначе канал ссылки, иначе только продукт. `link_product_id` — продукт самой ссылки (режим редиректа).
LINK_DIMS_CTE = """
link_dims AS MATERIALIZED (
  SELECT tl.id AS tracking_link_id, tl.product_id AS link_product_id,
         CASE WHEN d.publication_id IS NOT NULL OR tl.job_id IS NOT NULL THEN 'exact'
              WHEN tl.channel_id IS NOT NULL OR tl.level = 'channel' THEN 'channel'
              ELSE 'direct' END AS level,
         d.publication_id, d.published_at,
         COALESCE(d.channel_id, j.channel_id, tl.channel_id) AS channel_id,
         COALESCE(d.niche_id, j.niche_id, ch.niche_id) AS niche_id,
         COALESCE(d.product_id, j.product_id, tl.product_id) AS product_id,
         COALESCE(d.campaign_id, j.campaign_id, tl.campaign_id) AS campaign_id,
         COALESCE(d.banner_variant_id, j.banner_variant_id, tl.banner_variant_id) AS banner_variant_id,
         COALESCE(d.track, j.kind) AS track,
         COALESCE(d.platform, tl.platform, ch.platform) AS platform
  FROM tracking_links tl
  LEFT JOIN LATERAL (
    SELECT p.id FROM publications p
    WHERE p.deleted_at IS NULL
      AND (p.id = tl.publication_id
           OR (tl.publication_id IS NULL AND tl.job_id IS NOT NULL AND p.job_id = tl.job_id
               AND (tl.platform IS NULL OR p.platform = tl.platform)))
    ORDER BY (p.id = tl.publication_id) DESC, p.id
    LIMIT 1
  ) pp ON true
  LEFT JOIN v_publication_dims d ON d.publication_id = pp.id
  LEFT JOIN jobs j ON j.id = COALESCE(d.job_id, tl.job_id)
  LEFT JOIN channels ch ON ch.id = COALESCE(d.channel_id, j.channel_id, tl.channel_id)
)"""

# Измерения ролика для расхода и производства: первая живая публикация job (если есть), иначе job.
JOB_DIMS_CTE = """
job_dims AS MATERIALIZED (
  SELECT j.id AS job_id, j.status AS job_status, j.source_id, j.trend_brief_id, j.kind AS track,
         j.deleted_at AS job_deleted_at, j.created_at AS job_created_at,
         (j.created_at AT TIME ZONE CAST(%(tz)s AS text))::date AS created_day,
         pd.publication_id, pd.published_at,
         (pd.published_at AT TIME ZONE CAST(%(tz)s AS text))::date AS published_day,
         COALESCE(pd.channel_id, j.channel_id) AS channel_id, j.niche_id,
         COALESCE(pd.product_id, j.product_id) AS product_id,
         COALESCE(pd.campaign_id, j.campaign_id) AS campaign_id,
         COALESCE(pd.banner_variant_id, j.banner_variant_id) AS banner_variant_id,
         COALESCE(pd.platform, ch.platform) AS platform,
         CASE WHEN pd.publication_id IS NOT NULL OR j.status = 'published' THEN 'published'
              WHEN j.status = 'approved' THEN 'approved'
              WHEN j.status IN ('rejected', 'failed', 'cancelled') THEN j.status
              ELSE 'in_progress' END AS outcome
  FROM jobs j
  LEFT JOIN LATERAL (
    SELECT d.publication_id, d.published_at, d.channel_id, d.product_id, d.campaign_id,
           d.banner_variant_id, d.platform
    FROM v_publication_dims d WHERE d.job_id = j.id
    ORDER BY d.published_at NULLS LAST, d.publication_id
    LIMIT 1
  ) pd ON true
  LEFT JOIN channels ch ON ch.id = j.channel_id
)"""

REACHED_REVIEW = ("qc_failed", "pending_review", "rework", "approved", "published", "rejected")
_REACHED_SQL = "('qc_failed','pending_review','rework','approved','published','rejected')"

_VISITOR_ROUTE = (
    "(c.route IN ('landing','to_telegram','to_site') "
    "OR (c.route = 'direct' AND COALESCE(pr.redirect_mode, '') <> 'direct_telegram'))"
)
_USER_KEY = "COALESCE(v.user_ref_hash, 'ev:' || v.id::text)"


def _aggregate(
    conn: Any,
    facts: str,
    measures: str,
    params: dict[str, Any],
    *,
    scope: Scope,
    group: str | None,
    bucket: str | None,
    levels: Sequence[str] | None = None,
    extra_where: str = "TRUE",
) -> list[Row]:
    scope_sql, scope_params = scope.sql("f")
    level_sql, level_params = _levels_sql(levels)
    sql = f"""
        SELECT {_bucket(bucket)} AS bucket, {_key(group)} AS key, {measures}
        FROM ({facts}) f
        WHERE {scope_sql} AND {level_sql} AND {extra_where}
        GROUP BY 1, 2
    """
    return conn.execute(sql, {**params, **scope_params, **level_params}).fetchall()


# ---------------------------------------------------------------- активность: источники


_VIEWS_FACTS = """
SELECT s.day, 'exact'::text AS level, d.publication_id, d.channel_id, d.niche_id, d.product_id,
       d.campaign_id, d.banner_variant_id, d.track, d.platform,
       s.views_delta, s.engaged_views_delta, s.likes_delta, s.comments_delta
FROM stats_daily_views s
JOIN v_publication_dims d ON d.publication_id = s.publication_id
WHERE s.day BETWEEN %(d_from)s AND %(d_to)s
"""


def views_rows(
    conn: Any, *, scope: Scope, d_from: date, d_to: date, group: str | None, bucket: str | None
) -> list[Row]:
    """Прирост просмотров, лайков и комментариев по бизнес-дням (ролл-ап `stats_daily_views`)."""
    measures = """
        COALESCE(sum(f.views_delta), 0)::bigint AS views,
        COALESCE(sum(f.likes_delta), 0)::bigint AS likes,
        COALESCE(sum(f.comments_delta), 0)::bigint AS comments,
        sum(f.engaged_views_delta)::bigint AS engaged_views,
        count(DISTINCT f.publication_id) AS n_pubs
    """
    return _aggregate(
        conn,
        _VIEWS_FACTS,
        measures,
        {"d_from": d_from, "d_to": d_to},
        scope=scope,
        group=group,
        bucket=bucket,
    )


_TRAFFIC_FACTS = f"""
WITH {LINK_DIMS_CTE}
SELECT s.day, ld.level, ld.publication_id, ld.channel_id, ld.niche_id, ld.product_id, ld.campaign_id,
       ld.banner_variant_id, ld.track, ld.platform,
       s.landing_views, s.unique_visitors, s.to_telegram, s.to_site, s.direct, s.direct_bot_starts,
       s.bot_starts, s.tma_opens, s.signups, s.first_payments, s.payments,
       s.revenue_minor - s.refund_minor AS revenue_minor
FROM stats_daily_traffic s
JOIN link_dims ld ON ld.tracking_link_id = s.tracking_link_id
WHERE s.day BETWEEN %(d_from)s AND %(d_to)s
UNION ALL
SELECT u.day, 'direct', NULL::bigint, NULL::text, NULL::text, u.product_id, NULL::bigint, NULL::bigint,
       NULL::text, NULL::text,
       u.clicks_unknown_code, u.unique_visitors, 0, 0, 0, u.unique_bot_starts,
       u.bot_starts, u.tma_opens, u.signups, u.first_payments, u.payments,
       u.revenue_minor - u.refund_minor
FROM stats_daily_unattributed u
WHERE u.day BETWEEN %(d_from)s AND %(d_to)s
"""

_TRAFFIC_MEASURES = """
    COALESCE(sum(f.landing_views), 0)::bigint AS landing_views,
    COALESCE(sum(f.unique_visitors), 0)::bigint AS unique_visitors,
    COALESCE(sum(f.to_telegram), 0)::bigint AS to_telegram,
    COALESCE(sum(f.to_site), 0)::bigint AS to_site,
    COALESCE(sum(f.direct), 0)::bigint AS direct,
    COALESCE(sum(f.direct_bot_starts), 0)::bigint AS direct_bot_starts,
    COALESCE(sum(f.bot_starts), 0)::bigint AS bot_starts,
    COALESCE(sum(f.tma_opens), 0)::bigint AS tma_opens,
    COALESCE(sum(f.signups), 0)::bigint AS signups,
    COALESCE(sum(f.first_payments), 0)::bigint AS first_payments,
    COALESCE(sum(f.payments), 0)::bigint AS payments,
    COALESCE(sum(f.revenue_minor), 0)::bigint AS revenue_minor
"""


def traffic_rows(
    conn: Any,
    *,
    scope: Scope,
    d_from: date,
    d_to: date,
    group: str | None,
    bucket: str | None,
    levels: Sequence[str] | None,
) -> list[Row]:
    """Клики и конверсии по бизнес-дням из ролл-апов (без ботов и тестовых событий)."""
    return _aggregate(
        conn,
        _TRAFFIC_FACTS,
        _TRAFFIC_MEASURES,
        {"d_from": d_from, "d_to": d_to},
        scope=scope,
        group=group,
        bucket=bucket,
        levels=levels,
    )


_USERS_FACTS = f"""
WITH {LINK_DIMS_CTE}
SELECT (v.occurred_at AT TIME ZONE CAST(%(tz)s AS text))::date AS day, COALESCE(ld.level, 'direct') AS level,
       ld.publication_id, ld.channel_id, ld.niche_id, COALESCE(ld.product_id, v.product_id) AS product_id,
       ld.campaign_id, ld.banner_variant_id, ld.track, ld.platform, {_USER_KEY} AS user_key
FROM conversions v
LEFT JOIN link_dims ld ON ld.tracking_link_id = v.tracking_link_id
WHERE NOT v.is_test AND v.event_type = 'bot_start'
  AND v.occurred_at >= %(since)s AND v.occurred_at < %(until)s
"""


def users_rows(
    conn: Any,
    *,
    scope: Scope,
    tz: str,
    since: datetime,
    until: datetime,
    group: str | None,
    bucket: str | None,
    levels: Sequence[str] | None,
) -> list[Row]:
    """Уникальные пользователи бота (`user_ref_hash` у `bot_start`) — из сырых событий."""
    return _aggregate(
        conn,
        _USERS_FACTS,
        "count(DISTINCT f.user_key) AS users",
        {"tz": tz, "since": since, "until": until},
        scope=scope,
        group=group,
        bucket=bucket,
        levels=levels,
    )


# ---------------------------------------------------------------- расход и производство

_SPEND_FACTS = f"""
WITH {JOB_DIMS_CTE},
src_n AS (
  SELECT source_id, count(*) AS n FROM jobs
  WHERE kind = 'clip' AND source_id IS NOT NULL AND status <> 'draft' GROUP BY source_id
),
brief_n AS (
  SELECT trend_brief_id, count(*) AS n FROM jobs
  WHERE trend_brief_id IS NOT NULL AND status <> 'draft' GROUP BY trend_brief_id
),
ev AS (
  SELECT ge.id AS ge_id, ge.created_at, ge.provider, ge.step_name, ge.cost_usd::float8 AS cost,
         'job'::text AS share, ge.job_id
  FROM generation_events ge WHERE ge.job_id IS NOT NULL
  UNION ALL
  SELECT ge.id, ge.created_at, ge.provider, ge.step_name, ge.cost_usd::float8 / sn.n, 'source', j.id
  FROM generation_events ge
  JOIN src_n sn ON sn.source_id = ge.source_id
  JOIN jobs j ON j.source_id = ge.source_id AND j.kind = 'clip' AND j.status <> 'draft'
  WHERE ge.job_id IS NULL AND ge.source_id IS NOT NULL
  UNION ALL
  SELECT ge.id, ge.created_at, ge.provider, ge.step_name, ge.cost_usd::float8 / bn.n, 'brief', j.id
  FROM generation_events ge
  JOIN brief_n bn ON bn.trend_brief_id = ge.trend_brief_id
  JOIN jobs j ON j.trend_brief_id = ge.trend_brief_id AND j.status <> 'draft'
  WHERE ge.job_id IS NULL AND ge.source_id IS NULL AND ge.trend_brief_id IS NOT NULL
  UNION ALL
  SELECT ge.id, ge.created_at, ge.provider, ge.step_name, ge.cost_usd::float8, 'overhead', NULL::bigint
  FROM generation_events ge
  WHERE ge.job_id IS NULL
    AND NOT (ge.source_id IS NOT NULL AND EXISTS (SELECT 1 FROM src_n WHERE src_n.source_id = ge.source_id))
    AND NOT (ge.source_id IS NULL AND ge.trend_brief_id IS NOT NULL
             AND EXISTS (SELECT 1 FROM brief_n WHERE brief_n.trend_brief_id = ge.trend_brief_id))
)
SELECT (ev.created_at AT TIME ZONE CAST(%(tz)s AS text))::date AS event_day, ev.created_at, ev.ge_id,
       ev.provider, ev.step_name, ev.cost, ev.share, ev.job_id,
       CASE WHEN ev.job_id IS NULL THEN 'overhead' ELSE 'exact' END AS level,
       jd.publication_id, jd.channel_id, jd.niche_id, jd.product_id, jd.campaign_id, jd.banner_variant_id,
       jd.track, jd.platform, COALESCE(jd.outcome, 'overhead') AS outcome,
       jd.published_at, jd.job_created_at,
       CASE WHEN CAST(%(cohort)s AS boolean) AND ev.job_id IS NOT NULL
            THEN COALESCE(jd.published_day, jd.created_day)
            ELSE (ev.created_at AT TIME ZONE CAST(%(tz)s AS text))::date END AS day
FROM ev LEFT JOIN job_dims jd ON jd.job_id = ev.job_id
"""

_SPEND_MEASURES = """
    COALESCE(sum(f.cost) FILTER (WHERE f.level <> 'overhead'), 0)::float8 AS spend_usd,
    COALESCE(sum(f.cost) FILTER (WHERE f.level = 'overhead'), 0)::float8 AS overhead_usd,
    COALESCE(sum(f.cost) FILTER (WHERE f.outcome IN ('rejected','failed','cancelled')), 0)::float8
      AS rejected_spend_usd,
    COALESCE(sum(f.cost) FILTER (WHERE f.outcome = 'published'), 0)::float8 AS published_spend_usd,
    COALESCE(sum(f.cost) FILTER (WHERE f.outcome IN ('in_progress','approved')), 0)::float8
      AS in_progress_spend_usd,
    count(DISTINCT f.ge_id) AS calls
"""

# Когорта роликов: опубликованные в периоде и неопубликованные (брак, в работе), созданные в периоде.
_COHORT_JOB_COND = """
  ((f.published_at >= %(since)s AND f.published_at < %(until)s)
   OR (f.publication_id IS NULL AND f.job_created_at >= %(since)s AND f.job_created_at < %(until)s))
"""


def spend_rows(
    conn: Any,
    *,
    scope: Scope,
    tz: str,
    since: datetime,
    until: datetime,
    cohort: bool,
    group: str | None,
    bucket: str | None,
    include_overhead: bool = True,
) -> list[Row]:
    """Расход: `activity` — события периода по времени вызова; `cohort` — весь расход роликов когорты
    (опубликованы в периоде или, если не опубликованы, созданы в нём), накладные — по времени вызова."""
    time_cond = "f.created_at >= %(since)s AND f.created_at < %(until)s"
    if cohort:
        where = f"(CASE WHEN f.level = 'overhead' THEN {time_cond} ELSE {_COHORT_JOB_COND} END)"
    else:
        where = time_cond
    if not include_overhead:
        where += " AND f.level <> 'overhead'"
    return _aggregate(
        conn,
        _SPEND_FACTS,
        _SPEND_MEASURES,
        {"tz": tz, "since": since, "until": until, "cohort": cohort},
        scope=scope,
        group=group,
        bucket=bucket,
        extra_where=where,
    )


_PRODUCTION_FACTS = f"""
WITH {JOB_DIMS_CTE}
SELECT CASE WHEN CAST(%(cohort)s AS boolean) THEN COALESCE(jd.published_day, jd.created_day)
            ELSE jd.created_day END AS day,
       'exact'::text AS level, jd.publication_id, jd.channel_id, jd.niche_id, jd.product_id, jd.campaign_id,
       jd.banner_variant_id, jd.track, jd.platform, jd.published_at, jd.job_created_at,
       (jd.job_status IN {_REACHED_SQL})::int AS produced,
       (jd.job_status = 'rejected')::int AS rejected,
       (jd.job_status = 'failed')::int AS failed,
       CASE WHEN CAST(%(cohort)s AS boolean) AND jd.publication_id IS NOT NULL THEN jd.job_id END
         AS published_job,
       'job'::text AS kind
FROM job_dims jd
WHERE jd.job_deleted_at IS NULL
UNION ALL
SELECT (d.published_at AT TIME ZONE CAST(%(tz)s AS text))::date, 'exact', d.publication_id, d.channel_id,
       d.niche_id, d.product_id, d.campaign_id, d.banner_variant_id, d.track, d.platform, d.published_at,
       NULL::timestamptz, 0, 0, 0, d.job_id, 'publication'
FROM v_publication_dims d
WHERE NOT CAST(%(cohort)s AS boolean) AND d.published_at IS NOT NULL
"""


def production_rows(
    conn: Any,
    *,
    scope: Scope,
    tz: str,
    since: datetime,
    until: datetime,
    cohort: bool,
    group: str | None,
    bucket: str | None,
) -> list[Row]:
    """Сделано (дошло до приёмки), отклонено, сбоев — по дате создания ролика; опубликовано — по дате
    публикации (`activity`) или по когорте (`cohort`)."""
    if cohort:
        where = f"f.kind = 'job' AND {_COHORT_JOB_COND}"
    else:
        where = (
            "((f.kind = 'job' AND f.job_created_at >= %(since)s AND f.job_created_at < %(until)s)"
            " OR (f.kind = 'publication' AND f.published_at >= %(since)s AND f.published_at < %(until)s))"
        )
    measures = """
        COALESCE(sum(f.produced), 0)::bigint AS videos_produced,
        COALESCE(sum(f.rejected), 0)::bigint AS videos_rejected,
        COALESCE(sum(f.failed), 0)::bigint AS videos_failed,
        count(DISTINCT f.published_job) AS videos_published
    """
    return _aggregate(
        conn,
        _PRODUCTION_FACTS,
        measures,
        {"tz": tz, "since": since, "until": until, "cohort": cohort},
        scope=scope,
        group=group,
        bucket=bucket,
        extra_where=where,
    )


def spend_by_job(conn: Any, job_ids: Sequence[int]) -> dict[int, float]:
    """Весь расход роликов (свои вызовы + доли источника и брифа)."""
    if not job_ids:
        return {}
    rows = conn.execute(
        f"""
        SELECT f.job_id, COALESCE(sum(f.cost), 0)::float8 AS spend
        FROM ({_SPEND_FACTS}) f
        WHERE f.job_id = ANY(%(ids)s)
        GROUP BY f.job_id
        """,
        {"tz": "UTC", "cohort": False, "ids": list(job_ids)},
    ).fetchall()
    return {int(r["job_id"]): float(r["spend"]) for r in rows}


def job_cost_items(conn: Any, job_id: int) -> list[Row]:
    """Расход ролика по шагам и провайдерам, с долями источника и брифа (`share`)."""
    return conn.execute(
        f"""
        SELECT f.share, f.step_name, f.provider, count(DISTINCT f.ge_id) AS calls,
               COALESCE(sum(f.cost), 0)::float8 AS cost_usd
        FROM ({_SPEND_FACTS}) f
        WHERE f.job_id = %(job_id)s
        GROUP BY 1, 2, 3
        ORDER BY 5 DESC, 2, 3
        """,
        {"tz": "UTC", "cohort": False, "job_id": int(job_id)},
    ).fetchall()


# ---------------------------------------------------------------- когорты и публикации

_PUB_DIMS_COLUMNS = """
d.publication_id, d.job_id, d.video_uid, d.platform, d.channel_id, d.niche_id, d.product_id, d.campaign_id,
d.banner_variant_id, d.track, d.published_at, d.publication_status
"""


def cohort_publications(conn: Any, *, scope: Scope, tz: str, since: datetime, until: datetime) -> list[Row]:
    """Публикации, вышедшие в периоде (живые и снятые, кроме удалённых нами как ошибочные)."""
    scope_sql, params = scope.sql("d")
    return conn.execute(
        f"""
        SELECT {_PUB_DIMS_COLUMNS},
               (d.published_at AT TIME ZONE CAST(%(tz)s AS text))::date AS day,
               EXTRACT(EPOCH FROM (now() - d.published_at)) / 3600.0 AS age_hours
        FROM v_publication_dims d
        WHERE d.published_at >= %(since)s AND d.published_at < %(until)s AND {scope_sql}
        ORDER BY d.published_at, d.publication_id
        """,
        {**params, "tz": tz, "since": since, "until": until},
    ).fetchall()


def views_at_ages(conn: Any, pub_ids: Sequence[int], hours: Sequence[int | None]) -> list[Row]:
    """Соседние снимки вокруг `published_at + h` (для `h = None` — вокруг «сейчас»): последний не позже
    и первый после. Интерполяция — `analytics.interp.bracket_value`."""
    if not pub_ids or not hours:
        return []
    ages = [-1 if h is None else int(h) for h in hours]
    return conn.execute(
        """
        SELECT p.id AS publication_id, COALESCE(p.published_at, p.created_at) AS published_at, a.h,
               t.at, prev.captured_at AS t0, prev.views AS v0, prev.likes AS l0, prev.comments AS c0,
               nxt.captured_at AS t1, nxt.views AS v1, nxt.likes AS l1, nxt.comments AS c1
        FROM publications p
        CROSS JOIN unnest(CAST(%(ages)s AS int[])) AS a(h)
        CROSS JOIN LATERAL (
          SELECT CASE WHEN a.h < 0 THEN now()
                      ELSE COALESCE(p.published_at, p.created_at) + make_interval(hours => a.h) END AS at
        ) t
        LEFT JOIN LATERAL (
          SELECT s.captured_at, s.views, s.likes, s.comments FROM metrics_snapshots s
          WHERE s.publication_id = p.id AND s.views IS NOT NULL AND s.captured_at <= t.at
          ORDER BY s.captured_at DESC LIMIT 1
        ) prev ON true
        LEFT JOIN LATERAL (
          SELECT s.captured_at, s.views, s.likes, s.comments FROM metrics_snapshots s
          WHERE s.publication_id = p.id AND s.views IS NOT NULL AND s.captured_at > t.at
          ORDER BY s.captured_at ASC LIMIT 1
        ) nxt ON true
        WHERE p.id = ANY(%(ids)s)
        """,
        {"ids": list(pub_ids), "ages": ages},
    ).fetchall()


def traffic_by_publication(conn: Any, pub_ids: Sequence[int], *, hours: int | None = None) -> dict[int, Row]:
    """Клики и конверсии ролика (уровень `exact`) из сырых таблиц: за первые `hours` часов после
    публикации или за всю жизнь (`None`). Для когорт и A/B, где нужен ровный возраст."""
    if not pub_ids:
        return {}
    rows = conn.execute(
        f"""
        WITH {LINK_DIMS_CTE},
        pubs AS (
          SELECT p.id AS publication_id,
                 CASE WHEN CAST(%(hours)s AS int) IS NULL THEN now()
                      ELSE LEAST(now(), COALESCE(p.published_at, p.created_at)
                                 + make_interval(hours => CAST(%(hours)s AS int))) END AS cutoff
          FROM publications p WHERE p.id = ANY(%(ids)s)
        ),
        c AS (
          SELECT ld.publication_id,
                 count(*) FILTER (WHERE c.route = 'landing') AS landing_views,
                 count(DISTINCT c.visitor_key) FILTER (WHERE {_VISITOR_ROUTE}) AS unique_visitors,
                 count(*) FILTER (WHERE c.route = 'to_telegram') AS to_telegram,
                 count(*) FILTER (WHERE c.route = 'to_site') AS to_site,
                 count(*) FILTER (WHERE c.route = 'direct') AS direct
          FROM clicks c
          JOIN link_dims ld ON ld.tracking_link_id = c.tracking_link_id
          JOIN pubs p ON p.publication_id = ld.publication_id
          LEFT JOIN products pr ON pr.id = ld.link_product_id
          WHERE NOT c.is_bot AND c.occurred_at < p.cutoff
          GROUP BY 1
        ),
        v AS (
          SELECT ld.publication_id,
                 count(*) FILTER (WHERE v.event_type = 'bot_start') AS bot_starts,
                 count(DISTINCT {_USER_KEY}) FILTER (WHERE v.event_type = 'bot_start') AS users,
                 count(DISTINCT {_USER_KEY}) FILTER (WHERE v.event_type = 'bot_start' AND NOT v.via_landing)
                   AS direct_bot_starts,
                 count(*) FILTER (WHERE v.event_type = 'tma_open') AS tma_opens,
                 count(*) FILTER (WHERE v.event_type = 'signup') AS signups,
                 count(*) FILTER (WHERE v.event_type = 'first_payment') AS first_payments,
                 count(*) FILTER (WHERE v.event_type = 'payment') AS payments,
                 COALESCE(sum(v.amount_minor) FILTER (WHERE v.event_type IN ('first_payment','payment')), 0)
                   - COALESCE(sum(v.amount_minor) FILTER (WHERE v.event_type = 'refund'), 0) AS revenue_minor
          FROM conversions v
          JOIN link_dims ld ON ld.tracking_link_id = v.tracking_link_id
          JOIN pubs p ON p.publication_id = ld.publication_id
          WHERE NOT v.is_test AND v.occurred_at < p.cutoff
          GROUP BY 1
        )
        SELECT p.publication_id,
               COALESCE(c.landing_views, 0) AS landing_views,
               COALESCE(c.unique_visitors, 0) AS unique_visitors,
               COALESCE(c.to_telegram, 0) AS to_telegram, COALESCE(c.to_site, 0) AS to_site,
               COALESCE(c.direct, 0) AS direct, COALESCE(v.bot_starts, 0) AS bot_starts,
               COALESCE(v.users, 0) AS users, COALESCE(v.direct_bot_starts, 0) AS direct_bot_starts,
               COALESCE(v.tma_opens, 0) AS tma_opens, COALESCE(v.signups, 0) AS signups,
               COALESCE(v.first_payments, 0) AS first_payments, COALESCE(v.payments, 0) AS payments,
               COALESCE(v.revenue_minor, 0) AS revenue_minor
        FROM pubs p
        LEFT JOIN c ON c.publication_id = p.publication_id
        LEFT JOIN v ON v.publication_id = p.publication_id
        """,
        {"ids": list(pub_ids), "hours": hours},
    ).fetchall()
    return {int(r["publication_id"]): r for r in rows}


def publication_rows(conn: Any, pub_ids: Sequence[int]) -> dict[int, Row]:
    """Строки лидерборда роликов: измерения, тексты, свежесть, подписи канала и варианта."""
    if not pub_ids:
        return {}
    rows = conn.execute(
        f"""
        SELECT {_PUB_DIMS_COLUMNS}, p.title, p.url, p.platform_video_id, p.latest_views, p.next_poll_at,
               p.manual_due_at, p.last_polled_at, p.created_at,
               ch.title AS channel_title, bv.code AS variant_code, bv.name AS variant_name
        FROM v_publication_dims d
        JOIN publications p ON p.id = d.publication_id
        LEFT JOIN channels ch ON ch.id = d.channel_id
        LEFT JOIN banner_variants bv ON bv.id = d.banner_variant_id
        WHERE d.publication_id = ANY(%(ids)s)
        """,
        {"ids": list(pub_ids)},
    ).fetchall()
    return {int(r["publication_id"]): r for r in rows}


def labels(conn: Any, group: str, keys: Iterable[str]) -> dict[str, str]:
    """Подписи ключей группы из справочников (канал, ниша, продукт, кампания, вариант, ролик)."""
    keys = [k for k in keys if k is not None]
    if not keys:
        return {}
    if group == "channel":
        sql = "SELECT id AS key, COALESCE(title, id) AS label FROM channels WHERE id = ANY(%(k)s)"
    elif group == "niche":
        sql = "SELECT id AS key, label FROM niches WHERE id = ANY(%(k)s)"
    elif group == "product":
        sql = "SELECT id AS key, name AS label FROM products WHERE id = ANY(%(k)s)"
    elif group == "campaign":
        sql = "SELECT id::text AS key, name AS label FROM campaigns WHERE id::text = ANY(%(k)s)"
    elif group == "variant":
        sql = (
            "SELECT bv.id::text AS key, bv.code || ' · ' || bv.name || ' (' || c.name || ')' AS label "
            "FROM banner_variants bv JOIN campaigns c ON c.id = bv.campaign_id WHERE bv.id::text = ANY(%(k)s)"
        )
    elif group == "video":
        sql = (
            "SELECT p.id::text AS key, COALESCE(NULLIF(p.title, ''), j.video_uid) AS label "
            "FROM publications p JOIN jobs j ON j.id = p.job_id WHERE p.id::text = ANY(%(k)s)"
        )
    else:
        return {}
    return {str(r["key"]): str(r["label"]) for r in conn.execute(sql, {"k": keys}).fetchall()}


# ---------------------------------------------------------------- свежесть данных


def freshness_counts(conn: Any, *, grace_min: int) -> Row:
    return conn.execute(
        """
        WITH live AS (
          SELECT p.id, p.next_poll_at, p.last_polled_at, p.manual_due_at, p.status
          FROM publications p WHERE p.deleted_at IS NULL
        ),
        last_snap AS (
          SELECT s.publication_id, max(s.captured_at) AS t
          FROM metrics_snapshots s JOIN live l ON l.id = s.publication_id AND l.status = 'live'
          GROUP BY 1
        )
        SELECT
          (SELECT max(last_polled_at) FROM live) AS metrics_polled_at,
          (SELECT min(next_poll_at) FROM live WHERE status = 'live' AND next_poll_at < now())
            AS metrics_oldest_due_at,
          (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (now() - t)) / 60.0)
             FROM last_snap) AS public_counts_age_min,
          (SELECT count(*) FROM live WHERE status = 'live'
             AND next_poll_at < now() - make_interval(mins => CAST(%(grace)s AS int))) AS publications_stale,
          (SELECT count(*) FROM live WHERE manual_due_at IS NOT NULL AND manual_due_at <= now())
            AS publications_manual_due,
          (SELECT count(*) FROM live WHERE status = 'live') AS publications_live,
          (SELECT max(occurred_at) FROM clicks WHERE NOT is_bot) AS clicks_last_at,
          (SELECT max(occurred_at) FROM conversions WHERE NOT is_test) AS conversions_last_at
        """,
        {"grace": int(grace_min)},
    ).fetchone()


def last_rollup_at(conn: Any) -> datetime | None:
    return conn.execute(
        """
        SELECT GREATEST(
          (SELECT max(finished_at) FROM tasks WHERE kind = 'stats_rollup' AND status = 'succeeded'),
          (SELECT max(computed_at) FROM stats_daily_views),
          (SELECT max(computed_at) FROM stats_daily_traffic),
          (SELECT max(computed_at) FROM stats_daily_unattributed)) AS t
        """
    ).fetchone()["t"]


def collector_states(conn: Any) -> list[Row]:
    return conn.execute(
        """
        SELECT cs.*, cl.youtube_api_key_secret
        FROM collector_state cs
        LEFT JOIN clusters cl ON cl.id = cs.cluster_id
        ORDER BY cs.collector, cs.cluster_id
        """
    ).fetchall()


def collector_pub_counts(conn: Any) -> list[Row]:
    """Публикации по коллектору и кусту канала: всего живых и с наступившим `next_poll_at`."""
    return conn.execute(
        """
        SELECT COALESCE(p.metrics_collector,
                        CASE p.platform WHEN 'youtube' THEN 'yt_data' ELSE 'ytdlp' END) AS collector,
               ch.cluster AS cluster_id, cl.youtube_api_key_secret,
               count(*) AS total,
               count(*) FILTER (WHERE p.next_poll_at IS NOT NULL AND p.next_poll_at <= now()) AS due
        FROM publications p
        JOIN channels ch ON ch.id = p.channel_id
        LEFT JOIN clusters cl ON cl.id = ch.cluster
        WHERE p.deleted_at IS NULL AND p.status = 'live'
        GROUP BY 1, 2, 3
        """
    ).fetchall()


def quota_today(conn: Any, *, quota_tz: str) -> list[Row]:
    return conn.execute(
        """
        SELECT api, cluster_id, quota_day, units, calls, errors, updated_at
        FROM api_quota_usage
        WHERE quota_day = (now() AT TIME ZONE CAST(%(qtz)s AS text))::date
        ORDER BY api, cluster_id
        """,
        {"qtz": quota_tz},
    ).fetchall()


def quota_day(conn: Any, *, quota_tz: str) -> date:
    return conn.execute(
        "SELECT (now() AT TIME ZONE CAST(%(qtz)s AS text))::date AS d", {"qtz": quota_tz}
    ).fetchone()["d"]


# ---------------------------------------------------------------- «Требует тебя» и онбординг


def todo_counts(conn: Any) -> Row:
    return conn.execute(
        """
        SELECT
          (SELECT count(*) FROM jobs WHERE status = 'pending_review' AND deleted_at IS NULL)
            AS pending_review,
          (SELECT count(*) FROM jobs WHERE status = 'qc_failed' AND deleted_at IS NULL) AS qc_failed,
          (SELECT count(*) FROM upload_packages WHERE status = 'ready' AND marked_uploaded_at IS NULL)
            AS packages_ready,
          (SELECT count(*) FROM upload_packages
             WHERE status = 'downloaded' OR (status = 'ready' AND marked_uploaded_at IS NOT NULL))
            AS packages_awaiting_url,
          (SELECT count(*) FROM publications WHERE deleted_at IS NULL AND verify_status <> 'ok'
             AND NOT (verify_status = 'pending' AND created_at > now() - interval '1 hour'))
            AS unverified_publications,
          (SELECT count(*) FROM publications WHERE deleted_at IS NULL AND manual_due_at IS NOT NULL
             AND manual_due_at <= now()) AS manual_metrics_due,
          (SELECT count(*) FROM publications WHERE deleted_at IS NULL AND published_at IS NOT NULL
             AND published_at + make_interval(hours => CAST(%(h)s AS int)) <= now()
             AND published_at + make_interval(hours => CAST(%(h)s AS int)) > now() - interval '1 day')
            AS verdicts_72h_ready
        """,
        {"h": 72},
    ).fetchone()


def onboarding_counts(conn: Any) -> Row:
    return conn.execute(
        """
        SELECT
          (SELECT count(*) FROM products WHERE status = 'active') AS products,
          (SELECT count(*) FROM channels WHERE status NOT IN ('terminated','retired')) AS channels,
          (SELECT count(*) FROM publications WHERE deleted_at IS NULL) AS publications,
          (SELECT count(*) FROM conversions WHERE NOT is_test) AS conversions,
          (SELECT array_agg(DISTINCT youtube_api_key_secret) FROM clusters
             WHERE youtube_api_key_secret IS NOT NULL) AS cluster_key_secrets,
          (SELECT array_agg(DISTINCT webhook_secret_name) FROM products
             WHERE webhook_secret_name IS NOT NULL) AS webhook_secrets
        """
    ).fetchone()


# ---------------------------------------------------------------- карточка ролика


def publication_snapshots(conn: Any, publication_id: int) -> list[Row]:
    return conn.execute(
        """
        SELECT id, publication_id, captured_at, age_hours, views, likes, comments, shares, engaged_views,
               source, created_by, note
        FROM metrics_snapshots WHERE publication_id = %(id)s ORDER BY captured_at, id
        """,
        {"id": int(publication_id)},
    ).fetchall()


def publication_traffic_series(conn: Any, publication_id: int, *, unit: str, tz: str) -> list[Row]:
    """Клики и конверсии ролика по часам или дням (`t` — начало интервала в бизнес-поясе, UTC)."""
    unit = "hour" if unit == "hour" else "day"
    return conn.execute(
        f"""
        WITH {LINK_DIMS_CTE},
        links AS (SELECT tracking_link_id, link_product_id FROM link_dims WHERE publication_id = %(id)s),
        c AS (
          SELECT date_trunc(%(unit)s, c.occurred_at AT TIME ZONE CAST(%(tz)s AS text))
                   AT TIME ZONE CAST(%(tz)s AS text) AS t,
                 count(*) FILTER (WHERE c.route IN ('landing','direct')) AS clicks,
                 count(*) FILTER (WHERE c.route = 'landing') AS landing_views,
                 count(DISTINCT c.visitor_key) FILTER (WHERE {_VISITOR_ROUTE}) AS unique_visitors,
                 count(*) FILTER (WHERE c.route = 'to_telegram') AS to_telegram,
                 count(*) FILTER (WHERE c.route = 'to_site') AS to_site
          FROM clicks c JOIN links l ON l.tracking_link_id = c.tracking_link_id
          LEFT JOIN products pr ON pr.id = l.link_product_id
          WHERE NOT c.is_bot GROUP BY 1
        ),
        v AS (
          SELECT date_trunc(%(unit)s, v.occurred_at AT TIME ZONE CAST(%(tz)s AS text))
                   AT TIME ZONE CAST(%(tz)s AS text) AS t,
                 count(DISTINCT {_USER_KEY}) FILTER (WHERE v.event_type = 'bot_start' AND NOT v.via_landing)
                   AS direct_bot_starts,
                 count(*) FILTER (WHERE v.event_type = 'bot_start') AS bot_starts,
                 count(*) FILTER (WHERE v.event_type = 'signup') AS signups,
                 count(*) FILTER (WHERE v.event_type = 'first_payment') AS first_payments,
                 COALESCE(sum(v.amount_minor) FILTER (WHERE v.event_type IN ('first_payment','payment')), 0)
                   - COALESCE(sum(v.amount_minor) FILTER (WHERE v.event_type = 'refund'), 0) AS revenue_minor
          FROM conversions v JOIN links l ON l.tracking_link_id = v.tracking_link_id
          WHERE NOT v.is_test GROUP BY 1
        )
        SELECT COALESCE(c.t, v.t) AS t,
               COALESCE(c.clicks, 0) AS clicks, COALESCE(c.landing_views, 0) AS landing_views,
               COALESCE(c.unique_visitors, 0) AS unique_visitors, COALESCE(c.to_telegram, 0) AS to_telegram,
               COALESCE(c.to_site, 0) AS to_site, COALESCE(v.direct_bot_starts, 0) AS direct_bot_starts,
               COALESCE(v.bot_starts, 0) AS bot_starts, COALESCE(v.signups, 0) AS signups,
               COALESCE(v.first_payments, 0) AS first_payments, COALESCE(v.revenue_minor, 0) AS revenue_minor
        FROM c FULL JOIN v ON v.t = c.t
        ORDER BY 1
        """,
        {"id": int(publication_id), "unit": unit, "tz": tz},
    ).fetchall()


def publication_conversion_counts(conn: Any, publication_id: int) -> list[Row]:
    return conn.execute(
        f"""
        WITH {LINK_DIMS_CTE}
        SELECT v.event_type, count(*) AS count, count(DISTINCT {_USER_KEY}) AS unique_users,
               count(*) FILTER (WHERE v.via_landing) AS via_landing,
               COALESCE(sum(v.amount_minor), 0) AS amount_minor
        FROM conversions v JOIN link_dims ld ON ld.tracking_link_id = v.tracking_link_id
        WHERE NOT v.is_test AND ld.publication_id = %(id)s
        GROUP BY 1 ORDER BY 1
        """,
        {"id": int(publication_id)},
    ).fetchall()


def publication_recent_conversions(conn: Any, publication_id: int, *, limit: int = 20) -> list[Row]:
    return conn.execute(
        f"""
        WITH {LINK_DIMS_CTE}
        SELECT v.occurred_at, v.event_type, v.via_landing, v.amount_minor, v.currency
        FROM conversions v JOIN link_dims ld ON ld.tracking_link_id = v.tracking_link_id
        WHERE NOT v.is_test AND ld.publication_id = %(id)s
        ORDER BY v.occurred_at DESC, v.id DESC LIMIT %(limit)s
        """,
        {"id": int(publication_id), "limit": int(limit)},
    ).fetchall()


def channel_publications(
    conn: Any, channel_id: str, *, days: int, exclude_id: int | None = None
) -> list[int]:
    rows = conn.execute(
        """
        SELECT id FROM publications
        WHERE channel_id = %(ch)s AND deleted_at IS NULL AND published_at IS NOT NULL
          AND published_at >= now() - make_interval(days => CAST(%(days)s AS int))
          AND (CAST(%(ex)s AS bigint) IS NULL OR id <> CAST(%(ex)s AS bigint))
        """,
        {"ch": channel_id, "days": int(days), "ex": exclude_id},
    ).fetchall()
    return [int(r["id"]) for r in rows]


def channel_stats(
    conn: Any, channel_id: str, *, tz: str = "Europe/Moscow", days: int = 90, age_hours: int = 72
) -> Row:
    """`ChannelStats` (для M2-A `services/channels.py`): последний снимок канала, живые публикации,
    прирост просмотров за 30 дней (`stats_daily_views`) и коридор p25–p75 просмотров на 72 ч по роликам
    канала за 90 дней (интерполяция снимков, без экстраполяции)."""
    from autoshorts.analytics.formulas import percentile
    from autoshorts.analytics.interp import bracket_value

    snap = conn.execute(
        """
        SELECT captured_at, source, subscribers, total_views, video_count FROM channel_snapshots
        WHERE channel_id = %(ch)s ORDER BY captured_at DESC LIMIT 1
        """,
        {"ch": channel_id},
    ).fetchone()
    counts = conn.execute(
        """
        SELECT count(*) FILTER (WHERE status = 'live') AS publications_total,
               count(*) FILTER (WHERE published_at >= now() - interval '30 days') AS publications_30d,
               (SELECT sum(s.views_delta) FROM stats_daily_views s
                  JOIN publications p2 ON p2.id = s.publication_id
                 WHERE p2.channel_id = %(ch)s AND p2.deleted_at IS NULL
                   AND s.day > (now() AT TIME ZONE CAST(%(tz)s AS text))::date - 30) AS views_30d
        FROM publications WHERE channel_id = %(ch)s AND deleted_at IS NULL
        """,
        {"ch": channel_id, "tz": tz},
    ).fetchone()
    ids = channel_publications(conn, channel_id, days=days)
    now = db_now(conn)
    values: list[float] = []
    for r in views_at_ages(conn, ids, [age_hours]):
        if r["at"] > now:
            continue
        v = bracket_value(
            r["published_at"],
            r["at"],
            (r["t0"], r["v0"]) if r["t0"] is not None else None,
            (r["t1"], r["v1"]) if r["t1"] is not None else None,
        )
        if v is not None:
            values.append(v)
    return {
        "captured_at": snap["captured_at"] if snap else None,
        "source": snap["source"] if snap else None,
        "subscribers": snap["subscribers"] if snap else None,
        "total_views": snap["total_views"] if snap else None,
        "video_count": snap["video_count"] if snap else None,
        "publications_total": int(counts["publications_total"] or 0),
        "publications_30d": int(counts["publications_30d"] or 0),
        "views_30d": int(counts["views_30d"]) if counts["views_30d"] is not None else None,
        "views_72h_median": percentile(values, 50),
        "views_72h_p25": percentile(values, 25),
        "views_72h_p75": percentile(values, 75),
    }


# ---------------------------------------------------------------- A/B


def campaign(conn: Any, campaign_id: int) -> Row | None:
    return conn.execute(
        "SELECT id, product_id, name, status FROM campaigns WHERE id = %(id)s", {"id": int(campaign_id)}
    ).fetchone()


def campaign_variants(conn: Any, campaign_id: int) -> list[Row]:
    """Варианты, участвующие в тесте: активные и на паузе (архивные и черновики — нет)."""
    return conn.execute(
        """
        SELECT id, code, name, status, weight FROM banner_variants
        WHERE campaign_id = %(id)s AND status IN ('active','paused')
        ORDER BY code, id
        """,
        {"id": int(campaign_id)},
    ).fetchall()


def campaign_publications(conn: Any, campaign_id: int, platforms: Sequence[str]) -> list[Row]:
    return conn.execute(
        """
        SELECT d.publication_id, d.job_id, d.video_uid, d.channel_id, d.platform, d.published_at,
               d.banner_variant_id,
               EXTRACT(EPOCH FROM (now() - d.published_at)) / 3600.0 AS age_hours
        FROM v_publication_dims d
        WHERE d.campaign_id = %(id)s AND d.banner_variant_id IS NOT NULL AND d.published_at IS NOT NULL
          AND (cardinality(CAST(%(pl)s AS text[])) = 0 OR d.platform = ANY(CAST(%(pl)s AS text[])))
        ORDER BY d.published_at, d.publication_id
        """,
        {"id": int(campaign_id), "pl": list(platforms)},
    ).fetchall()


def active_campaign_ids(conn: Any) -> list[int]:
    rows = conn.execute("SELECT id FROM campaigns WHERE status IN ('active','paused') ORDER BY id").fetchall()
    return [int(r["id"]) for r in rows]


# ---------------------------------------------------------------- перенос dashboard_queries.py


def qc_pass_rate(conn: Any, last_n: int = 100) -> Row:
    """Доля роликов, прошедших QC, среди последних `last_n`, дошедших до QC (legacy `qc_pass_rate`)."""
    row = conn.execute(
        """
        SELECT count(*) FILTER (WHERE status IN ('pending_review','approved','published','rework','rejected'))
                 AS passed,
               count(*) FILTER (WHERE status = 'qc_failed') AS failed
        FROM (
          SELECT status FROM jobs
          WHERE status IN ('qc_failed','pending_review','approved','published','rework','rejected')
            AND deleted_at IS NULL
          ORDER BY updated_at DESC LIMIT %(n)s
        ) recent
        """,
        {"n": int(last_n)},
    ).fetchone()
    passed, failed = int(row["passed"] or 0), int(row["failed"] or 0)
    total = passed + failed
    return {
        "passed": passed,
        "failed": failed,
        "total": total,
        "pass_rate_pct": round(100 * passed / total, 1) if total else None,
    }


def reject_reasons(conn: Any, last_n: int = 100) -> list[Row]:
    """Частота причин отказа на последних `last_n` отклонениях (legacy `reject_reason_breakdown`)."""
    return conn.execute(
        """
        SELECT reject_reason, count(*) AS count
        FROM (SELECT reject_reason FROM jobs WHERE reject_reason IS NOT NULL
              ORDER BY updated_at DESC LIMIT %(n)s) recent
        GROUP BY reject_reason ORDER BY count DESC, reject_reason
        """,
        {"n": int(last_n)},
    ).fetchall()
