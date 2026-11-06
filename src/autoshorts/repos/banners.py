"""Таблица `banner_templates` (миграции 0003, 0011): пресеты варианта, `layout` = BannerVariantDraft JSON."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

Row = dict[str, Any]

COLUMNS = "id, name, status, layout, version, created_at, updated_at"


def get(conn: Any, template_id: int, *, for_update: bool = False) -> Row | None:
    return conn.execute(
        f"SELECT {COLUMNS} FROM banner_templates WHERE id = %(id)s" + (" FOR UPDATE" if for_update else ""),
        {"id": template_id},
    ).fetchone()


def get_by_name(conn: Any, name: str) -> Row | None:
    return conn.execute(
        f"SELECT {COLUMNS} FROM banner_templates WHERE name = %(name)s", {"name": name}
    ).fetchone()


def list_page(conn: Any, *, status: str | None, limit: int, offset: int) -> tuple[list[Row], int]:
    where = "WHERE status = %(status)s" if status else ""
    params = {"status": status, "limit": limit, "offset": offset}
    total = conn.execute(f"SELECT count(*) AS n FROM banner_templates {where}", params).fetchone()["n"]
    rows = conn.execute(
        f"SELECT {COLUMNS} FROM banner_templates {where}"
        " ORDER BY status, name, id LIMIT %(limit)s OFFSET %(offset)s",
        params,
    ).fetchall()
    return rows, int(total)


def insert(conn: Any, *, name: str, layout: dict[str, Any], status: str = "active") -> Row:
    return conn.execute(
        f"""
        INSERT INTO banner_templates (name, layout, status) VALUES (%(name)s, %(layout)s, %(status)s)
        RETURNING {COLUMNS}
        """,
        {"name": name, "layout": Jsonb(layout), "status": status},
    ).fetchone()


def update(
    conn: Any, template_id: int, *, name: str | None, status: str | None, layout: dict[str, Any] | None
) -> Row:
    return conn.execute(
        f"""
        UPDATE banner_templates SET name = COALESCE(%(name)s, name), status = COALESCE(%(status)s, status),
               layout = COALESCE(%(layout)s, layout), version = version + 1, updated_at = now()
        WHERE id = %(id)s RETURNING {COLUMNS}
        """,
        {
            "id": template_id,
            "name": name,
            "status": status,
            "layout": Jsonb(layout) if layout is not None else None,
        },
    ).fetchone()


def template_ids_using_creative(conn: Any, sha256: str) -> list[int]:
    rows = conn.execute(
        """
        SELECT id FROM banner_templates
        WHERE layout -> 'layers'
              @> jsonb_build_array(jsonb_build_object('creative_sha256', CAST(%(sha)s AS text)))
        ORDER BY id
        """,
        {"sha": sha256},
    ).fetchall()
    return [int(r["id"]) for r in rows]
