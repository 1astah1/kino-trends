"""Таблица `products` (миграция 0003) и сводка трафика за 7 дней для списка (§5.2 `GET /products`)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

Row = dict[str, Any]

COLUMNS = (
    "id, name, kind, status, landing_url, redirect_host, redirect_mode, telegram_bot_username,"
    " tma_short_name, telegram_link_style, utm_defaults, brand, landing, webhook_secret_name,"
    " created_at, updated_at"
)
JSON_COLUMNS = frozenset({"utm_defaults", "brand", "landing"})
EDITABLE_COLUMNS = frozenset(
    {
        "name",
        "kind",
        "status",
        "landing_url",
        "redirect_host",
        "redirect_mode",
        "telegram_bot_username",
        "tma_short_name",
        "telegram_link_style",
        "utm_defaults",
        "brand",
        "landing",
    }
)


def _params(fields: Mapping[str, Any]) -> dict[str, Any]:
    return {k: (Jsonb(v) if k in JSON_COLUMNS and v is not None else v) for k, v in fields.items()}


def get(conn: Any, product_id: str, *, for_update: bool = False) -> Row | None:
    return conn.execute(
        f"SELECT {COLUMNS} FROM products WHERE id = %(id)s" + (" FOR UPDATE" if for_update else ""),
        {"id": product_id},
    ).fetchone()


def list_page(conn: Any, *, status: str | None, limit: int, offset: int) -> tuple[list[Row], int]:
    where = "WHERE status = %(status)s" if status else ""
    params = {"status": status, "limit": limit, "offset": offset}
    total = conn.execute(f"SELECT count(*) AS n FROM products {where}", params).fetchone()["n"]
    rows = conn.execute(
        f"SELECT {COLUMNS} FROM products {where} ORDER BY status, name, id LIMIT %(limit)s OFFSET %(offset)s",
        params,
    ).fetchall()
    return rows, int(total)


def insert(conn: Any, fields: Mapping[str, Any]) -> Row:
    unknown = set(fields) - EDITABLE_COLUMNS - {"id"}
    if unknown:
        raise ValueError(f"unknown products columns: {sorted(unknown)}")
    names = sorted(fields)
    query = sql.SQL("INSERT INTO products ({}) VALUES ({}) RETURNING {}").format(
        sql.SQL(", ").join(sql.Identifier(n) for n in names),
        sql.SQL(", ").join(sql.Placeholder(n) for n in names),
        sql.SQL(COLUMNS),
    )
    return conn.execute(query, _params(fields)).fetchone()


def update(conn: Any, product_id: str, fields: Mapping[str, Any]) -> Row | None:
    unknown = set(fields) - EDITABLE_COLUMNS
    if unknown:
        raise ValueError(f"cannot update products columns: {sorted(unknown)}")
    if not fields:
        return get(conn, product_id)
    assignments = sql.SQL(", ").join(
        sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder(k)) for k in sorted(fields)
    )
    query = sql.SQL("UPDATE products SET {}, updated_at = now() WHERE id = %(pk)s RETURNING {}").format(
        assignments, sql.SQL(COLUMNS)
    )
    return conn.execute(query, {**_params(fields), "pk": product_id}).fetchone()


def existing_secrets(conn: Any, names: Sequence[str]) -> set[str]:
    """Какие из имён секретов записаны в БД (значения не читаются)."""
    names = [n for n in names if n]
    if not names:
        return set()
    rows = conn.execute("SELECT name FROM secrets WHERE name = ANY(%(names)s)", {"names": names}).fetchall()
    return {r["name"] for r in rows}


def stats_7d(conn: Any, product_ids: Sequence[str]) -> dict[str, Row]:
    """Клики (без ботов), уникальные посетители, старты бота, первые оплаты и выручка за 7 дней."""
    if not product_ids:
        return {}
    rows = conn.execute(
        """
        SELECT p.id,
               COALESCE(c.clicks, 0) AS clicks,
               COALESCE(c.unique_visitors, 0) AS unique_visitors,
               COALESCE(v.bot_starts, 0) AS bot_starts,
               COALESCE(v.first_payments, 0) AS first_payments,
               COALESCE(v.revenue_minor, 0) AS revenue_minor,
               v.currency
        FROM unnest(%(ids)s::text[]) AS p(id)
        LEFT JOIN LATERAL (
            SELECT count(*) AS clicks, count(DISTINCT visitor_key) AS unique_visitors
            FROM clicks
            WHERE product_id = p.id AND NOT is_bot AND occurred_at >= now() - interval '7 days'
        ) c ON true
        LEFT JOIN LATERAL (
            SELECT count(*) FILTER (WHERE event_type = 'bot_start') AS bot_starts,
                   count(*) FILTER (WHERE event_type = 'first_payment') AS first_payments,
                   COALESCE(sum(amount_minor) FILTER (WHERE event_type IN ('first_payment', 'payment')), 0)
                   - COALESCE(sum(amount_minor) FILTER (WHERE event_type = 'refund'), 0) AS revenue_minor,
                   mode() WITHIN GROUP (ORDER BY currency) AS currency
            FROM conversions
            WHERE product_id = p.id AND occurred_at >= now() - interval '7 days'
        ) v ON true
        """,
        {"ids": list(product_ids)},
    ).fetchall()
    return {r["id"]: r for r in rows}
