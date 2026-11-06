"""Таблицы `campaigns` и `banner_variants` (миграции 0003, 0011).

Вариант A/B после 0011: `layers` (domain BannerLayer[]), `timing` (BannerTiming, `{}` — по
умолчанию), `ad_marking` {required, erid, advertiser_text}, `code_url_template`, `version`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

Row = dict[str, Any]

CAMPAIGN_COLUMNS = (
    "c.id, c.product_id, c.name, c.status, c.banner_template_id, c.allocation, c.priority, c.targeting,"
    " c.starts_at, c.ends_at, c.notes, c.created_by, c.created_at, c.updated_at,"
    " (SELECT count(*) FROM banner_variants v WHERE v.campaign_id = c.id) AS variants_count"
)
CAMPAIGN_JSON = frozenset({"targeting"})
CAMPAIGN_EDITABLE = frozenset(
    {
        "name",
        "status",
        "banner_template_id",
        "allocation",
        "priority",
        "targeting",
        "starts_at",
        "ends_at",
        "notes",
    }
)

VARIANT_COLUMNS = (
    "v.id, v.campaign_id, c.product_id, v.code, v.name, v.status, v.weight, v.layers, v.timing, v.ad_marking,"
    " v.code_url_template, v.voice_cta_text, v.pinned_comment_template, v.description_cta_template, v.notes,"
    " v.version, v.created_by, v.created_at, v.updated_at"
)
VARIANT_JSON = frozenset({"layers", "timing", "ad_marking"})
VARIANT_EDITABLE = frozenset(
    {
        "code",
        "name",
        "status",
        "weight",
        "layers",
        "timing",
        "ad_marking",
        "code_url_template",
        "voice_cta_text",
        "pinned_comment_template",
        "description_cta_template",
        "notes",
    }
)


def _params(fields: Mapping[str, Any], json_cols: frozenset[str]) -> dict[str, Any]:
    return {k: (Jsonb(v) if k in json_cols and v is not None else v) for k, v in fields.items()}


def _insert(conn: Any, table: str, fields: Mapping[str, Any], json_cols: frozenset[str]) -> int:
    names = sorted(fields)
    row = conn.execute(
        sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING id").format(
            sql.Identifier(table),
            sql.SQL(", ").join(sql.Identifier(n) for n in names),
            sql.SQL(", ").join(sql.Placeholder(n) for n in names),
        ),
        _params(fields, json_cols),
    ).fetchone()
    return int(row["id"])


# ---------------------------------------------------------------- кампании


def get_campaign(conn: Any, campaign_id: int, *, for_update: bool = False) -> Row | None:
    if for_update:
        conn.execute("SELECT 1 FROM campaigns WHERE id = %(id)s FOR UPDATE", {"id": campaign_id})
    return conn.execute(
        f"SELECT {CAMPAIGN_COLUMNS} FROM campaigns c WHERE c.id = %(id)s", {"id": campaign_id}
    ).fetchone()


def list_campaigns(
    conn: Any, *, product_id: str | None, statuses: Sequence[str] | None, limit: int, offset: int
) -> tuple[list[Row], int]:
    conds: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if product_id:
        conds.append("c.product_id = %(product_id)s")
        params["product_id"] = product_id
    if statuses:
        conds.append("c.status = ANY(%(statuses)s)")
        params["statuses"] = list(statuses)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    total = conn.execute(f"SELECT count(*) AS n FROM campaigns c {where}", params).fetchone()["n"]
    rows = conn.execute(
        f"""
        SELECT {CAMPAIGN_COLUMNS} FROM campaigns c {where}
        ORDER BY (c.status = 'active') DESC, c.priority, c.id DESC LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    ).fetchall()
    return rows, int(total)


def insert_campaign(conn: Any, fields: Mapping[str, Any]) -> int:
    return _insert(conn, "campaigns", fields, CAMPAIGN_JSON)


def update_campaign(conn: Any, campaign_id: int, fields: Mapping[str, Any]) -> None:
    unknown = set(fields) - CAMPAIGN_EDITABLE
    if unknown:
        raise ValueError(f"cannot update campaigns columns: {sorted(unknown)}")
    if not fields:
        return
    assignments = sql.SQL(", ").join(
        sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder(k)) for k in sorted(fields)
    )
    conn.execute(
        sql.SQL("UPDATE campaigns SET {}, updated_at = now() WHERE id = %(pk)s").format(assignments),
        {**_params(fields, CAMPAIGN_JSON), "pk": campaign_id},
    )


def active_campaigns(conn: Any, *, product_id: str | None = None) -> list[Row]:
    """Активные кампании в окне starts_at/ends_at, по приоритету (меньше — важнее)."""
    return conn.execute(
        f"""
        SELECT {CAMPAIGN_COLUMNS} FROM campaigns c
        WHERE c.status = 'active'
          AND (c.starts_at IS NULL OR c.starts_at <= now()) AND (c.ends_at IS NULL OR c.ends_at > now())
          AND (CAST(%(product_id)s AS text) IS NULL OR c.product_id = %(product_id)s)
        ORDER BY c.priority, c.id
        """,
        {"product_id": product_id},
    ).fetchall()


# ---------------------------------------------------------------- варианты


def get_variant(conn: Any, variant_id: int, *, for_update: bool = False) -> Row | None:
    if for_update:
        conn.execute("SELECT 1 FROM banner_variants WHERE id = %(id)s FOR UPDATE", {"id": variant_id})
    return conn.execute(
        f"SELECT {VARIANT_COLUMNS} FROM banner_variants v JOIN campaigns c ON c.id = v.campaign_id"
        " WHERE v.id = %(id)s",
        {"id": variant_id},
    ).fetchone()


def list_variants(conn: Any, campaign_id: int, *, statuses: Sequence[str] | None = None) -> list[Row]:
    return conn.execute(
        f"""
        SELECT {VARIANT_COLUMNS} FROM banner_variants v JOIN campaigns c ON c.id = v.campaign_id
        WHERE v.campaign_id = %(id)s
          AND (CAST(%(statuses)s AS text[]) IS NULL OR v.status = ANY(%(statuses)s))
        ORDER BY v.code, v.id
        """,
        {"id": campaign_id, "statuses": list(statuses) if statuses else None},
    ).fetchall()


def insert_variant(conn: Any, fields: Mapping[str, Any]) -> int:
    return _insert(conn, "banner_variants", fields, VARIANT_JSON)


def update_variant(
    conn: Any, variant_id: int, fields: Mapping[str, Any], *, bump_version: bool = True
) -> None:
    unknown = set(fields) - VARIANT_EDITABLE
    if unknown:
        raise ValueError(f"cannot update banner_variants columns: {sorted(unknown)}")
    if not fields:
        return
    assignments = sql.SQL(", ").join(
        sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder(k)) for k in sorted(fields)
    )
    bump = sql.SQL(", version = version + 1") if bump_version else sql.SQL("")
    conn.execute(
        sql.SQL("UPDATE banner_variants SET {}{}, updated_at = now() WHERE id = %(pk)s").format(
            assignments, bump
        ),
        {**_params(fields, VARIANT_JSON), "pk": variant_id},
    )


def code_taken(conn: Any, campaign_id: int, code: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM banner_variants WHERE campaign_id = %(c)s AND code = %(code)s",
            {"c": campaign_id, "code": code},
        ).fetchone()
        is not None
    )


def count_jobs(conn: Any, campaign_id: int) -> int:
    """Сколько роликов уже назначено кампании (для round_robin)."""
    row = conn.execute(
        "SELECT count(*) AS n FROM jobs WHERE campaign_id = %(id)s AND banner_variant_id IS NOT NULL",
        {"id": campaign_id},
    ).fetchone()
    return int(row["n"])
