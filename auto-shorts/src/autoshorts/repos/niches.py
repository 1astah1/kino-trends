"""Таблицы `niches` и `niche_versions` (миграция 0003): конфиг ниши с историей версий."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

Row = dict[str, Any]

COLUMNS = (
    "n.id, n.label, n.status, n.current_version, n.config, n.created_at, n.updated_at,"
    " COALESCE((SELECT array_agg(ch.id ORDER BY ch.id) FROM channels ch WHERE ch.niche_id = n.id),"
    " '{}'::text[]) AS used_by_channels"
)
EDITABLE_COLUMNS = frozenset({"label", "status"})


def get(conn: Any, niche_id: str, *, for_update: bool = False) -> Row | None:
    if for_update:
        conn.execute("SELECT 1 FROM niches WHERE id = %(id)s FOR UPDATE", {"id": niche_id})
    return conn.execute(f"SELECT {COLUMNS} FROM niches n WHERE n.id = %(id)s", {"id": niche_id}).fetchone()


def list_page(conn: Any, *, status: str | None, limit: int, offset: int) -> tuple[list[Row], int]:
    where = "WHERE n.status = %(status)s" if status else ""
    params = {"status": status, "limit": limit, "offset": offset}
    total = conn.execute(f"SELECT count(*) AS n FROM niches n {where}", params).fetchone()["n"]
    rows = conn.execute(
        f"SELECT {COLUMNS} FROM niches n {where} ORDER BY n.id LIMIT %(limit)s OFFSET %(offset)s", params
    ).fetchall()
    return rows, int(total)


def insert(
    conn: Any, *, niche_id: str, label: str, status: str, config: dict[str, Any], current_version: int
) -> Row:
    conn.execute(
        """
        INSERT INTO niches (id, label, status, config, current_version)
        VALUES (%(id)s, %(label)s, %(status)s, %(config)s, %(version)s)
        """,
        {
            "id": niche_id,
            "label": label,
            "status": status,
            "config": Jsonb(config),
            "version": current_version,
        },
    )
    row = get(conn, niche_id)
    assert row is not None
    return row


def set_config(
    conn: Any,
    niche_id: str,
    *,
    config: dict[str, Any],
    version: int,
    status: str | None = None,
    label: str | None = None,
) -> Row | None:
    conn.execute(
        """
        UPDATE niches SET config = %(config)s, current_version = %(version)s,
               status = COALESCE(%(status)s, status), label = COALESCE(%(label)s, label), updated_at = now()
        WHERE id = %(id)s
        """,
        {"id": niche_id, "config": Jsonb(config), "version": version, "status": status, "label": label},
    )
    return get(conn, niche_id)


def update_meta(conn: Any, niche_id: str, fields: Mapping[str, Any]) -> Row | None:
    unknown = set(fields) - EDITABLE_COLUMNS
    if unknown:
        raise ValueError(f"cannot update niches columns: {sorted(unknown)}")
    if fields:
        assignments = sql.SQL(", ").join(
            sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder(k)) for k in sorted(fields)
        )
        conn.execute(
            sql.SQL("UPDATE niches SET {}, updated_at = now() WHERE id = %(pk)s").format(assignments),
            {**fields, "pk": niche_id},
        )
    return get(conn, niche_id)


def insert_version(
    conn: Any, *, niche_id: str, version: int, config: dict[str, Any], source: str, created_by: int | None
) -> None:
    conn.execute(
        """
        INSERT INTO niche_versions (niche_id, version, config, source, created_by)
        VALUES (%(id)s, %(version)s, %(config)s, %(source)s, %(by)s)
        """,
        {"id": niche_id, "version": version, "config": Jsonb(config), "source": source, "by": created_by},
    )


def list_versions(conn: Any, niche_id: str) -> list[Row]:
    return conn.execute(
        """
        SELECT version, source, created_by, created_at FROM niche_versions
        WHERE niche_id = %(id)s ORDER BY version DESC
        """,
        {"id": niche_id},
    ).fetchall()


def get_version(conn: Any, niche_id: str, version: int) -> Row | None:
    return conn.execute(
        "SELECT version, config FROM niche_versions WHERE niche_id = %(id)s AND version = %(v)s",
        {"id": niche_id, "v": version},
    ).fetchone()
