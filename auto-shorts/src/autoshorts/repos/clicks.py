"""SQL журнала кликов редиректора `clicks` (docs/ARCHITECTURE.md §4.2 0006, §5.3, §6.6). Владелец — M3-B.

Измерения ссылки (продукт, публикация, ролик, канал, кампания, вариант, площадка) денормализуются
при вставке. Сырой IP не хранится: `visitor_key = hmac(REDIRECT_SALT, day|ip|ua)[:32]`. Боты и HEAD
пишутся с `is_bot=true` и в статистику не идут.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

Row = dict[str, Any]

COLUMNS = (
    "id, occurred_at, code, tracking_link_id, route, product_id, publication_id, job_id, channel_id,"
    " campaign_id, banner_variant_id, platform, visitor_key, user_agent, referer_host, is_bot"
)
USER_AGENT_MAX = 512


def insert(
    conn: Any,
    *,
    code: str,
    route: str,
    visitor_key: str,
    is_bot: bool,
    link: Row | None,
    product_id: str | None,
    user_agent: str | None,
    referer_host: str | None,
) -> Row:
    dims = link or {}
    return conn.execute(
        f"""
        INSERT INTO clicks (code, tracking_link_id, route, product_id, publication_id, job_id, channel_id,
                            campaign_id, banner_variant_id, platform, visitor_key, user_agent,
                            referer_host, is_bot)
        VALUES (%(code)s, %(tl)s, %(route)s, %(product)s, %(pub)s, %(job)s, %(ch)s, %(camp)s, %(var)s,
                %(platform)s, %(vk)s, %(ua)s, %(ref)s, %(bot)s)
        RETURNING {COLUMNS}
        """,
        {
            "code": code[:64],
            "tl": dims.get("id"),
            "route": route,
            "product": dims.get("product_id") or product_id,
            "pub": dims.get("publication_id"),
            "job": dims.get("job_id"),
            "ch": dims.get("channel_id"),
            "camp": dims.get("campaign_id"),
            "var": dims.get("banner_variant_id"),
            "platform": dims.get("platform"),
            "vk": visitor_key,
            "ua": (user_agent or "")[:USER_AGENT_MAX] or None,
            "ref": referer_host,
            "bot": bool(is_bot),
        },
    ).fetchone()


def journal(
    conn: Any,
    *,
    tracking_link_id: int | None,
    publication_id: int | None,
    product_id: str | None,
    channel_id: str | None,
    routes: list[str] | None,
    include_bots: bool,
    before_id: int | None,
    limit: int,
) -> list[Row]:
    where = ["true"]
    params: dict[str, Any] = {"limit": int(limit)}
    if tracking_link_id is not None:
        where.append("tracking_link_id = %(tl)s")
        params["tl"] = int(tracking_link_id)
    if publication_id is not None:
        where.append("publication_id = %(pub)s")
        params["pub"] = int(publication_id)
    if product_id:
        where.append("product_id = %(product)s")
        params["product"] = product_id
    if channel_id:
        where.append("channel_id = %(ch)s")
        params["ch"] = channel_id
    if routes:
        where.append("route = ANY(%(routes)s)")
        params["routes"] = list(routes)
    if not include_bots:
        where.append("NOT is_bot")
    if before_id is not None:
        where.append("id < %(before)s")
        params["before"] = int(before_id)
    return conn.execute(
        f"SELECT {COLUMNS} FROM clicks WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT %(limit)s",
        params,
    ).fetchall()


def _scope(tracking_link_id: int | None, publication_id: int | None) -> tuple[str, dict[str, Any]]:
    if tracking_link_id is not None:
        return "tracking_link_id = %(scope)s", {"scope": int(tracking_link_id)}
    if publication_id is not None:
        return "publication_id = %(scope)s", {"scope": int(publication_id)}
    raise ValueError("scope: tracking_link_id or publication_id")


def totals(
    conn: Any,
    *,
    tracking_link_id: int | None = None,
    publication_id: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Row:
    """Итоги кликов (без ботов; боты — отдельным счётчиком)."""
    cond, params = _scope(tracking_link_id, publication_id)
    params.update({"since": since, "until": until})
    return conn.execute(
        f"""
        SELECT count(*) FILTER (WHERE NOT is_bot) AS clicks,
               count(DISTINCT visitor_key) FILTER (WHERE NOT is_bot) AS unique_visitors,
               count(*) FILTER (WHERE is_bot) AS bot_clicks,
               count(*) FILTER (WHERE NOT is_bot AND route = 'landing') AS landing_views,
               count(*) FILTER (WHERE NOT is_bot AND route = 'to_telegram') AS to_telegram,
               count(*) FILTER (WHERE NOT is_bot AND route = 'to_site') AS to_site,
               count(*) FILTER (WHERE NOT is_bot AND route = 'direct') AS direct
        FROM clicks
        WHERE {cond}
          AND (CAST(%(since)s AS timestamptz) IS NULL OR occurred_at >= CAST(%(since)s AS timestamptz))
          AND (CAST(%(until)s AS timestamptz) IS NULL OR occurred_at < CAST(%(until)s AS timestamptz))
        """,
        params,
    ).fetchone()


def series(
    conn: Any,
    *,
    granularity: str,
    tz: str,
    tracking_link_id: int | None = None,
    publication_id: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[Row]:
    """Ряд по часам/дням бизнес-пояса: `t` — начало интервала (aware UTC)."""
    cond, params = _scope(tracking_link_id, publication_id)
    unit = "hour" if granularity == "hour" else "day"
    params.update({"since": since, "until": until, "tz": tz, "unit": unit})
    return conn.execute(
        f"""
        SELECT date_trunc(%(unit)s, occurred_at AT TIME ZONE %(tz)s) AT TIME ZONE %(tz)s AS t,
               count(*) AS clicks,
               count(DISTINCT visitor_key) AS unique_visitors,
               count(*) FILTER (WHERE route = 'to_telegram') AS to_telegram,
               count(*) FILTER (WHERE route = 'to_site') AS to_site
        FROM clicks
        WHERE {cond} AND NOT is_bot
          AND (CAST(%(since)s AS timestamptz) IS NULL OR occurred_at >= CAST(%(since)s AS timestamptz))
          AND (CAST(%(until)s AS timestamptz) IS NULL OR occurred_at < CAST(%(until)s AS timestamptz))
        GROUP BY 1 ORDER BY 1
        """,
        params,
    ).fetchall()


__all__ = ["COLUMNS", "insert", "journal", "series", "totals"]
