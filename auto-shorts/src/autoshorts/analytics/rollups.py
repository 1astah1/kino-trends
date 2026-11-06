"""Ролл-апы аналитики (docs/ARCHITECTURE.md §6.7; задача `stats_rollup`, расписание 0009:
`stats_rollup_recent` каждые 15 минут за 3 дня и `stats_rollup_full` в 03:30 за 35 дней).

Инкрементально по окну: пересчитываются только последние `days` бизнес-дней (`business_timezone`,
Europe/Moscow — день берётся часами БД), строки окна заменяются целиком (DELETE + INSERT в одной
транзакции под `pg_advisory_xact_lock`, чтобы два прогона не сталкивались). Повторный прогон с теми же
данными даёт те же строки. Если коллектор лежал дольше окна, прирост до начала окна перераспределит ночной
прогон за 35 дней (до него он виден целиком в первом дне окна).

1. `stats_daily_views` — значение на конец бизнес-дня (`analytics.interp.daily_eod`: интерполяция
   снимков, до первого снимка — якорь `(published_at, 0)`, после последнего — последнее известное значение,
   без экстраполяции) и прирост `views_delta = eod(d) − eod(d − 1)`. Пересчитываются публикации «в
   движении»: опубликованные внутри окна или со снимком после его начала; у остальных прирост в окне —
   ноль, и строк они не получают.
2. `stats_daily_traffic` — клики и конверсии по дню и трекинг-ссылке одним SQL:
   * клики без ботов (`NOT is_bot`); `unique_visitors` — уникальные `visitor_key` лендинга (`landing`,
     `to_telegram`, `to_site`) и прямых переходов на сайт; прямой переход в Telegram (`direct` у продукта
     в режиме `direct_telegram`) посетителем не считается — его старт придёт без `_l` и попадёт в
     `direct_bot_starts`, иначе человек был бы посчитан дважды;
   * конверсии без тестовых (`NOT is_test`); `unique_bot_starts` — уникальные пользователи
     (`user_ref_hash`, у события без него — само событие); `direct_bot_starts` — то же **без `_l`**
     (`NOT via_landing`); выручка — оплаты (`first_payment`, `payment`), возвраты — отдельно.
3. `stats_daily_unattributed` — прямой трафик продукта (уровень `direct`): клики без ссылки
   (`unknown_code` и переходы с лендинга по умолчанию) и события без тега или с неизвестным тегом.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from autoshorts.analytics.interp import build_series, daily_eod

__all__ = [
    "ROLLUP_LOCK_KEY",
    "rollup_traffic",
    "rollup_unattributed",
    "rollup_views",
    "rollup_window",
    "stats_rollup",
]

log = logging.getLogger(__name__)

ROLLUP_LOCK_KEY = "autoshorts.stats_rollup"
ROLLUP_MAX_DAYS = 400


def rollup_window(conn: Any, tz: str, days: int) -> dict[str, Any]:
    """Окно пересчёта по часам БД: первый и последний бизнес-день и их границы в UTC."""
    row = conn.execute(
        """
        WITH b AS (
          SELECT (now() AT TIME ZONE CAST(%(tz)s AS text))::date AS today
        )
        SELECT b.today - CAST(%(days)s AS int) + 1 AS d0, b.today,
               ((b.today - CAST(%(days)s AS int) + 1)::timestamp
                 AT TIME ZONE CAST(%(tz)s AS text)) AS start_ts,
               ((b.today + 1)::timestamp AT TIME ZONE CAST(%(tz)s AS text)) AS end_ts
        FROM b
        """,
        {"tz": tz, "days": int(days)},
    ).fetchone()
    return {"d0": row["d0"], "today": row["today"], "start_ts": row["start_ts"], "end_ts": row["end_ts"]}


# ---------------------------------------------------------------- просмотры


_PUBS_IN_MOTION_SQL = """
SELECT p.id, COALESCE(p.published_at, p.created_at) AS published_at
FROM publications p
WHERE p.deleted_at IS NULL
  AND COALESCE(p.published_at, p.created_at) < %(end_ts)s
  AND (COALESCE(p.published_at, p.created_at) >= %(start_ts)s
       OR EXISTS (SELECT 1 FROM metrics_snapshots s
                  WHERE s.publication_id = p.id AND s.captured_at > %(start_ts)s))
ORDER BY p.id
"""

_SNAPSHOTS_SQL = """
(SELECT DISTINCT ON (publication_id) publication_id, captured_at, views, likes, comments, engaged_views
   FROM metrics_snapshots
  WHERE publication_id = ANY(%(ids)s) AND captured_at <= %(start_ts)s AND views IS NOT NULL
  ORDER BY publication_id, captured_at DESC)
UNION ALL
(SELECT publication_id, captured_at, views, likes, comments, engaged_views
   FROM metrics_snapshots
  WHERE publication_id = ANY(%(ids)s) AND captured_at > %(start_ts)s)
"""


def rollup_views(conn: Any, *, days: int, tz: str, window: dict[str, Any] | None = None) -> dict[str, int]:
    w = window or rollup_window(conn, tz, days)
    zone = ZoneInfo(tz)
    pubs = conn.execute(_PUBS_IN_MOTION_SQL, {"start_ts": w["start_ts"], "end_ts": w["end_ts"]}).fetchall()
    samples: dict[int, list[dict[str, Any]]] = defaultdict(list)
    ids = [int(p["id"]) for p in pubs]
    if ids:
        for s in conn.execute(_SNAPSHOTS_SQL, {"ids": ids, "start_ts": w["start_ts"]}).fetchall():
            samples[int(s["publication_id"])].append(s)
    rows: list[tuple[Any, ...]] = []
    for p in pubs:
        pid = int(p["id"])
        published: datetime = p["published_at"]
        pub_day: date = published.astimezone(zone).date()
        first = max(w["d0"], pub_day)
        if first > w["today"]:
            continue
        snaps = samples.get(pid, [])
        per_metric: dict[str, list[tuple[date, int, int]]] = {}
        for metric in ("views", "likes", "comments", "engaged_views"):
            points = [(s["captured_at"], s[metric]) for s in snaps]
            if metric == "engaged_views" and not any(v is not None for _, v in points):
                continue
            series = build_series(points, published_at=published)
            per_metric[metric] = daily_eod(series, first, w["today"], zone)
        engaged = per_metric.get("engaged_views")
        for i, (day, eod, delta) in enumerate(per_metric["views"]):
            rows.append(
                (
                    day,
                    pid,
                    delta,
                    engaged[i][2] if engaged else None,
                    per_metric["likes"][i][2],
                    per_metric["comments"][i][2],
                    eod,
                )
            )
    conn.execute("DELETE FROM stats_daily_views WHERE day >= %(d0)s", {"d0": w["d0"]})
    if rows:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO stats_daily_views (day, publication_id, views_delta, engaged_views_delta,
                                                  likes_delta, comments_delta, views_eod, computed_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, now())""",
                rows,
            )
    return {"publications": len(pubs), "rows": len(rows)}


# ---------------------------------------------------------------- трафик по ссылкам

_VISITOR_ROUTE = (
    "(c.route IN ('landing','to_telegram','to_site') "
    "OR (c.route = 'direct' AND COALESCE(pr.redirect_mode, '') <> 'direct_telegram'))"
)
_USER_KEY = "COALESCE(v.user_ref_hash, 'ev:' || v.id::text)"

_TRAFFIC_SQL = f"""
INSERT INTO stats_daily_traffic (day, tracking_link_id, landing_views, unique_visitors, to_telegram, to_site,
    direct, bot_starts, unique_bot_starts, direct_bot_starts, signups, leads, first_payments, payments,
    revenue_minor, tma_opens, site_visits, trial_starts, refunds, refund_minor, computed_at)
SELECT COALESCE(c.day, v.day), COALESCE(c.link, v.link),
       COALESCE(c.landing_views, 0), COALESCE(c.unique_visitors, 0), COALESCE(c.to_telegram, 0),
       COALESCE(c.to_site, 0), COALESCE(c.direct, 0),
       COALESCE(v.bot_starts, 0), COALESCE(v.unique_bot_starts, 0), COALESCE(v.direct_bot_starts, 0),
       COALESCE(v.signups, 0), COALESCE(v.leads, 0), COALESCE(v.first_payments, 0), COALESCE(v.payments, 0),
       COALESCE(v.revenue_minor, 0), COALESCE(v.tma_opens, 0), COALESCE(v.site_visits, 0),
       COALESCE(v.trial_starts, 0), COALESCE(v.refunds, 0), COALESCE(v.refund_minor, 0), now()
FROM (
  SELECT (c.occurred_at AT TIME ZONE CAST(%(tz)s AS text))::date AS day, c.tracking_link_id AS link,
         count(*) FILTER (WHERE c.route = 'landing') AS landing_views,
         count(DISTINCT c.visitor_key) FILTER (WHERE {_VISITOR_ROUTE}) AS unique_visitors,
         count(*) FILTER (WHERE c.route = 'to_telegram') AS to_telegram,
         count(*) FILTER (WHERE c.route = 'to_site') AS to_site,
         count(*) FILTER (WHERE c.route = 'direct') AS direct
  FROM clicks c
  JOIN tracking_links tl ON tl.id = c.tracking_link_id
  LEFT JOIN products pr ON pr.id = tl.product_id
  WHERE NOT c.is_bot AND c.tracking_link_id IS NOT NULL AND c.occurred_at >= %(start_ts)s
  GROUP BY 1, 2
) c
FULL JOIN (
  SELECT (v.occurred_at AT TIME ZONE CAST(%(tz)s AS text))::date AS day, v.tracking_link_id AS link,
         count(*) FILTER (WHERE v.event_type = 'bot_start') AS bot_starts,
         count(DISTINCT {_USER_KEY}) FILTER (WHERE v.event_type = 'bot_start') AS unique_bot_starts,
         count(DISTINCT {_USER_KEY}) FILTER (WHERE v.event_type = 'bot_start' AND NOT v.via_landing)
           AS direct_bot_starts,
         count(*) FILTER (WHERE v.event_type = 'signup') AS signups,
         count(*) FILTER (WHERE v.event_type = 'lead') AS leads,
         count(*) FILTER (WHERE v.event_type = 'first_payment') AS first_payments,
         count(*) FILTER (WHERE v.event_type = 'payment') AS payments,
         COALESCE(sum(v.amount_minor) FILTER (WHERE v.event_type IN ('first_payment','payment')), 0)
           AS revenue_minor,
         count(*) FILTER (WHERE v.event_type = 'tma_open') AS tma_opens,
         count(*) FILTER (WHERE v.event_type = 'site_visit') AS site_visits,
         count(*) FILTER (WHERE v.event_type = 'trial_start') AS trial_starts,
         count(*) FILTER (WHERE v.event_type = 'refund') AS refunds,
         COALESCE(sum(v.amount_minor) FILTER (WHERE v.event_type = 'refund'), 0) AS refund_minor
  FROM conversions v
  WHERE NOT v.is_test AND v.tracking_link_id IS NOT NULL AND v.occurred_at >= %(start_ts)s
  GROUP BY 1, 2
) v ON v.day = c.day AND v.link = c.link
"""


def rollup_traffic(conn: Any, *, days: int, tz: str, window: dict[str, Any] | None = None) -> dict[str, int]:
    w = window or rollup_window(conn, tz, days)
    conn.execute("DELETE FROM stats_daily_traffic WHERE day >= %(d0)s", {"d0": w["d0"]})
    cur = conn.execute(_TRAFFIC_SQL, {"tz": tz, "start_ts": w["start_ts"]})
    return {"rows": int(cur.rowcount or 0)}


# ---------------------------------------------------------------- прямой трафик продукта

_UNATTRIBUTED_SQL = f"""
INSERT INTO stats_daily_unattributed (day, product_id, clicks_unknown_code, unique_visitors, bot_starts,
    unique_bot_starts, tma_opens, site_visits, signups, trial_starts, leads, first_payments, payments,
    revenue_minor, refunds, refund_minor, computed_at)
SELECT COALESCE(c.day, v.day), COALESCE(c.product_id, v.product_id),
       COALESCE(c.clicks_unknown_code, 0), COALESCE(c.unique_visitors, 0),
       COALESCE(v.bot_starts, 0), COALESCE(v.unique_bot_starts, 0), COALESCE(v.tma_opens, 0),
       COALESCE(v.site_visits, 0), COALESCE(v.signups, 0), COALESCE(v.trial_starts, 0), COALESCE(v.leads, 0),
       COALESCE(v.first_payments, 0), COALESCE(v.payments, 0), COALESCE(v.revenue_minor, 0),
       COALESCE(v.refunds, 0), COALESCE(v.refund_minor, 0), now()
FROM (
  SELECT (c.occurred_at AT TIME ZONE CAST(%(tz)s AS text))::date AS day, c.product_id,
         count(*) FILTER (WHERE c.route = 'unknown_code') AS clicks_unknown_code,
         count(DISTINCT c.visitor_key) AS unique_visitors
  FROM clicks c
  JOIN products pr ON pr.id = c.product_id
  WHERE NOT c.is_bot AND c.tracking_link_id IS NULL AND c.occurred_at >= %(start_ts)s
  GROUP BY 1, 2
) c
FULL JOIN (
  SELECT (v.occurred_at AT TIME ZONE CAST(%(tz)s AS text))::date AS day, v.product_id,
         count(*) FILTER (WHERE v.event_type = 'bot_start') AS bot_starts,
         count(DISTINCT {_USER_KEY}) FILTER (WHERE v.event_type = 'bot_start') AS unique_bot_starts,
         count(*) FILTER (WHERE v.event_type = 'tma_open') AS tma_opens,
         count(*) FILTER (WHERE v.event_type = 'site_visit') AS site_visits,
         count(*) FILTER (WHERE v.event_type = 'signup') AS signups,
         count(*) FILTER (WHERE v.event_type = 'trial_start') AS trial_starts,
         count(*) FILTER (WHERE v.event_type = 'lead') AS leads,
         count(*) FILTER (WHERE v.event_type = 'first_payment') AS first_payments,
         count(*) FILTER (WHERE v.event_type = 'payment') AS payments,
         COALESCE(sum(v.amount_minor) FILTER (WHERE v.event_type IN ('first_payment','payment')), 0)
           AS revenue_minor,
         count(*) FILTER (WHERE v.event_type = 'refund') AS refunds,
         COALESCE(sum(v.amount_minor) FILTER (WHERE v.event_type = 'refund'), 0) AS refund_minor
  FROM conversions v
  WHERE NOT v.is_test AND v.tracking_link_id IS NULL AND v.occurred_at >= %(start_ts)s
  GROUP BY 1, 2
) v ON v.day = c.day AND v.product_id = c.product_id
"""


def rollup_unattributed(
    conn: Any, *, days: int, tz: str, window: dict[str, Any] | None = None
) -> dict[str, int]:
    w = window or rollup_window(conn, tz, days)
    conn.execute("DELETE FROM stats_daily_unattributed WHERE day >= %(d0)s", {"d0": w["d0"]})
    cur = conn.execute(_UNATTRIBUTED_SQL, {"tz": tz, "start_ts": w["start_ts"]})
    return {"rows": int(cur.rowcount or 0)}


# ---------------------------------------------------------------- всё вместе


def stats_rollup(conn: Any, *, days: int, tz: str | None = None) -> dict[str, Any]:
    """Пересчитать окно `days` бизнес-дней (1…400) всех трёх ролл-апов в транзакции `conn`."""
    days = max(1, min(int(days), ROLLUP_MAX_DAYS))
    if tz is None:
        from autoshorts.services.settings import get_runtime_settings

        tz = get_runtime_settings(conn).business_timezone
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%(k)s))", {"k": ROLLUP_LOCK_KEY})
    w = rollup_window(conn, tz, days)
    views = rollup_views(conn, days=days, tz=tz, window=w)
    traffic = rollup_traffic(conn, days=days, tz=tz, window=w)
    unattributed = rollup_unattributed(conn, days=days, tz=tz, window=w)
    result = {
        "days": days,
        "timezone": tz,
        "date_from": w["d0"].isoformat(),
        "date_to": w["today"].isoformat(),
        "publications": views["publications"],
        "views_rows": views["rows"],
        "traffic_rows": traffic["rows"],
        "unattributed_rows": unattributed["rows"],
    }
    log.info("stats_rollup: %s", result)
    return result
