"""Таблица `clusters` — «кусты» изоляции каналов (миграция 0003, ТЗ §10)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from psycopg import sql

Row = dict[str, Any]

COLUMNS = (
    "c.id, c.title, c.notes, c.youtube_api_key_secret, c.youtube_oauth_client_secret, c.created_at,"
    " (SELECT count(*) FROM channels ch WHERE ch.cluster = c.id) AS channels_count"
)
EDITABLE_COLUMNS = frozenset({"title", "notes", "youtube_api_key_secret", "youtube_oauth_client_secret"})


def get(conn: Any, cluster_id: str) -> Row | None:
    return conn.execute(
        f"SELECT {COLUMNS} FROM clusters c WHERE c.id = %(id)s", {"id": cluster_id}
    ).fetchone()


def exists(conn: Any, cluster_id: str) -> bool:
    return conn.execute("SELECT 1 FROM clusters WHERE id = %(id)s", {"id": cluster_id}).fetchone() is not None


def list_all(conn: Any) -> list[Row]:
    return conn.execute(f"SELECT {COLUMNS} FROM clusters c ORDER BY c.id").fetchall()


def insert(conn: Any, *, cluster_id: str, title: str, notes: str | None) -> Row:
    conn.execute(
        "INSERT INTO clusters (id, title, notes) VALUES (%(id)s, %(title)s, %(notes)s)",
        {"id": cluster_id, "title": title, "notes": notes},
    )
    row = get(conn, cluster_id)
    assert row is not None
    return row


def update(conn: Any, cluster_id: str, fields: Mapping[str, Any]) -> Row | None:
    unknown = set(fields) - EDITABLE_COLUMNS
    if unknown:
        raise ValueError(f"cannot update clusters columns: {sorted(unknown)}")
    if fields:
        assignments = sql.SQL(", ").join(
            sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder(k)) for k in sorted(fields)
        )
        conn.execute(
            sql.SQL("UPDATE clusters SET {} WHERE id = %(pk)s").format(assignments),
            {**fields, "pk": cluster_id},
        )
    return get(conn, cluster_id)
