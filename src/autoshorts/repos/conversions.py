"""SQL конверсий `conversions`, журнала приёма `ingest_requests` и вебхук-полей `products`
(docs/ARCHITECTURE.md §4.2 0006/0015, §5.3, §6.6). Владелец — M3-B.

Идемпотентность приёма — `ON CONFLICT (product_id, external_event_id) DO NOTHING`: повтор события
возвращает None (статус `duplicate`). Тестовые события (`is_test`) в итоги и ряды не входят.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

Row = dict[str, Any]

COLUMNS = (
    "c.id, c.product_id, c.external_event_id, c.event_type, c.occurred_at, c.received_at, c.tag,"
    " c.via_landing, c.tracking_link_id, c.publication_id, c.job_id, c.channel_id, c.campaign_id,"
    " c.banner_variant_id, c.platform, c.amount_minor, c.currency, c.meta, c.is_test,"
    " tl.level AS link_level"
)
INGEST_COLUMNS = (
    "id, received_at, product_id, product_header, outcome, http_status, events_total, accepted, duplicates,"
    " unknown_tag, invalid, is_test, body_bytes, detail, request_id"
)


# ---------------------------------------------------------------- приём


def insert_event(conn: Any, fields: dict[str, Any]) -> Row | None:
    """Вставить событие. Уже было (тот же product_id + event_id) — None."""
    params = dict(fields)
    params["meta"] = Jsonb(params.get("meta") or {})
    return conn.execute(
        """
        INSERT INTO conversions (product_id, external_event_id, event_type, occurred_at, tag, via_landing,
                                 tracking_link_id, publication_id, job_id, channel_id, campaign_id,
                                 banner_variant_id, platform, user_ref_hash, amount_minor, currency, meta,
                                 is_test)
        VALUES (%(product_id)s, %(external_event_id)s, %(event_type)s, %(occurred_at)s, %(tag)s,
                %(via_landing)s, %(tracking_link_id)s, %(publication_id)s, %(job_id)s, %(channel_id)s,
                %(campaign_id)s, %(banner_variant_id)s, %(platform)s, %(user_ref_hash)s, %(amount_minor)s,
                %(currency)s, %(meta)s, %(is_test)s)
        ON CONFLICT (product_id, external_event_id) DO NOTHING
        RETURNING id
        """,
        params,
    ).fetchone()


def insert_ingest_request(conn: Any, fields: dict[str, Any]) -> Row:
    return conn.execute(
        f"""
        INSERT INTO ingest_requests (product_id, product_header, outcome, http_status, events_total,
                                     accepted, duplicates, unknown_tag, invalid, is_test, body_bytes,
                                     detail, request_id)
        VALUES (%(product_id)s, %(product_header)s, %(outcome)s, %(http_status)s, %(events_total)s,
                %(accepted)s, %(duplicates)s, %(unknown_tag)s, %(invalid)s, %(is_test)s, %(body_bytes)s,
                %(detail)s, %(request_id)s)
        RETURNING {INGEST_COLUMNS}
        """,
        {
            "product_id": fields.get("product_id"),
            "product_header": (fields.get("product_header") or None) and str(fields["product_header"])[:64],
            "outcome": fields["outcome"],
            "http_status": int(fields["http_status"]),
            "events_total": int(fields.get("events_total") or 0),
            "accepted": int(fields.get("accepted") or 0),
            "duplicates": int(fields.get("duplicates") or 0),
            "unknown_tag": int(fields.get("unknown_tag") or 0),
            "invalid": int(fields.get("invalid") or 0),
            "is_test": bool(fields.get("is_test")),
            "body_bytes": fields.get("body_bytes"),
            "detail": (fields.get("detail") or None) and str(fields["detail"])[:500],
            "request_id": fields.get("request_id"),
        },
    ).fetchone()


def recent_ingest_requests(conn: Any, product_id: str, *, limit: int = 20) -> list[Row]:
    return conn.execute(
        f"""
        SELECT {INGEST_COLUMNS} FROM ingest_requests
        WHERE product_id = %(p)s ORDER BY received_at DESC, id DESC LIMIT %(limit)s
        """,
        {"p": product_id, "limit": int(limit)},
    ).fetchall()


def last_event_at(conn: Any, product_id: str) -> datetime | None:
    row = conn.execute(
        "SELECT max(received_at) AS t FROM conversions WHERE product_id = %(p)s AND NOT is_test",
        {"p": product_id},
    ).fetchone()
    return row["t"] if row else None


# ---------------------------------------------------------------- вебхук продукта


def product_webhook(conn: Any, product_id: str, *, for_update: bool = False) -> Row | None:
    lock = " FOR UPDATE" if for_update else ""
    return conn.execute(
        f"""
        SELECT id, name, kind, status, landing_url, redirect_host, telegram_bot_username, tma_short_name,
               telegram_link_style, utm_defaults, webhook_secret_name, webhook_secret_rotated_at,
               webhook_prev_secret_name, webhook_prev_valid_until
        FROM products WHERE id = %(id)s{lock}
        """,
        {"id": product_id},
    ).fetchone()


def set_product_webhook(
    conn: Any,
    product_id: str,
    *,
    secret_name: str,
    rotated_at: datetime,
    prev_secret_name: str | None,
    prev_valid_until: datetime | None,
) -> Row:
    return conn.execute(
        """
        UPDATE products SET webhook_secret_name = %(name)s, webhook_secret_rotated_at = %(at)s,
               webhook_prev_secret_name = %(prev)s, webhook_prev_valid_until = %(until)s, updated_at = now()
        WHERE id = %(id)s
        RETURNING id, webhook_secret_name, webhook_secret_rotated_at, webhook_prev_secret_name,
                  webhook_prev_valid_until
        """,
        {
            "id": product_id,
            "name": secret_name,
            "at": rotated_at,
            "prev": prev_secret_name,
            "until": prev_valid_until,
        },
    ).fetchone()


# ---------------------------------------------------------------- журнал и сводки


def journal(
    conn: Any,
    *,
    product_id: str | None,
    tracking_link_id: int | None,
    publication_id: int | None,
    event_types: list[str] | None,
    include_test: bool,
    before_id: int | None,
    limit: int,
) -> list[Row]:
    where = ["true"]
    params: dict[str, Any] = {"limit": int(limit)}
    if product_id:
        where.append("c.product_id = %(product)s")
        params["product"] = product_id
    if tracking_link_id is not None:
        where.append("c.tracking_link_id = %(tl)s")
        params["tl"] = int(tracking_link_id)
    if publication_id is not None:
        where.append("c.publication_id = %(pub)s")
        params["pub"] = int(publication_id)
    if event_types:
        where.append("c.event_type = ANY(%(types)s)")
        params["types"] = list(event_types)
    if not include_test:
        where.append("NOT c.is_test")
    if before_id is not None:
        where.append("c.id < %(before)s")
        params["before"] = int(before_id)
    return conn.execute(
        f"""
        SELECT {COLUMNS} FROM conversions c LEFT JOIN tracking_links tl ON tl.id = c.tracking_link_id
        WHERE {" AND ".join(where)} ORDER BY c.id DESC LIMIT %(limit)s
        """,
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
    cond, params = _scope(tracking_link_id, publication_id)
    params.update({"since": since, "until": until})
    return conn.execute(
        f"""
        SELECT count(*) FILTER (WHERE event_type = 'bot_start') AS bot_starts,
               count(*) FILTER (WHERE event_type = 'bot_start' AND NOT via_landing) AS direct_bot_starts,
               count(*) FILTER (WHERE event_type = 'signup') AS signups,
               count(*) FILTER (WHERE event_type = 'first_payment') AS first_payments,
               COALESCE(sum(amount_minor) FILTER (WHERE event_type IN ('first_payment','payment')), 0)
                 - COALESCE(sum(amount_minor) FILTER (WHERE event_type = 'refund'), 0) AS revenue_minor,
               max(currency) FILTER (WHERE amount_minor IS NOT NULL) AS currency
        FROM conversions
        WHERE {cond} AND NOT is_test
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
    cond, params = _scope(tracking_link_id, publication_id)
    unit = "hour" if granularity == "hour" else "day"
    params.update({"since": since, "until": until, "tz": tz, "unit": unit})
    return conn.execute(
        f"""
        SELECT date_trunc(%(unit)s, occurred_at AT TIME ZONE %(tz)s) AT TIME ZONE %(tz)s AS t,
               count(*) FILTER (WHERE event_type = 'bot_start') AS bot_starts,
               count(*) FILTER (WHERE event_type = 'signup') AS signups,
               count(*) FILTER (WHERE event_type = 'first_payment') AS first_payments,
               COALESCE(sum(amount_minor) FILTER (WHERE event_type IN ('first_payment','payment')), 0)
                 - COALESCE(sum(amount_minor) FILTER (WHERE event_type = 'refund'), 0) AS revenue_minor
        FROM conversions
        WHERE {cond} AND NOT is_test
          AND (CAST(%(since)s AS timestamptz) IS NULL OR occurred_at >= CAST(%(since)s AS timestamptz))
          AND (CAST(%(until)s AS timestamptz) IS NULL OR occurred_at < CAST(%(until)s AS timestamptz))
        GROUP BY 1 ORDER BY 1
        """,
        params,
    ).fetchall()


__all__ = [
    "COLUMNS",
    "INGEST_COLUMNS",
    "insert_event",
    "insert_ingest_request",
    "journal",
    "last_event_at",
    "product_webhook",
    "recent_ingest_requests",
    "series",
    "set_product_webhook",
    "totals",
]
