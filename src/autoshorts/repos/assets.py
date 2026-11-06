"""Таблица `assets` (миграция 0003) и потребление tus-загрузок `uploads` (0005) под ассет."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

Row = dict[str, Any]

COLUMNS = "id, kind, name, path, bytes, mime, license_note, meta, created_by, created_at, deleted_at"


def get(conn: Any, asset_id: int, *, include_deleted: bool = False) -> Row | None:
    where = "" if include_deleted else " AND deleted_at IS NULL"
    return conn.execute(f"SELECT {COLUMNS} FROM assets WHERE id = %(id)s{where}", {"id": asset_id}).fetchone()


def list_live(conn: Any, *, kind: str | None) -> list[Row]:
    where = "AND kind = %(kind)s" if kind else ""
    return conn.execute(
        f"SELECT {COLUMNS} FROM assets WHERE deleted_at IS NULL {where} ORDER BY kind, name, id",
        {"kind": kind},
    ).fetchall()


def insert(
    conn: Any,
    *,
    kind: str,
    name: str,
    path: str,
    bytes_: int,
    mime: str,
    license_note: str,
    meta: dict[str, Any],
    created_by: int | None,
) -> Row:
    return conn.execute(
        f"""
        INSERT INTO assets (kind, name, path, bytes, mime, license_note, meta, created_by)
        VALUES (%(kind)s, %(name)s, %(path)s, %(bytes)s, %(mime)s, %(license)s, %(meta)s, %(by)s)
        RETURNING {COLUMNS}
        """,
        {
            "kind": kind,
            "name": name,
            "path": path,
            "bytes": bytes_,
            "mime": mime,
            "license": license_note,
            "meta": Jsonb(meta),
            "by": created_by,
        },
    ).fetchone()


def soft_delete(conn: Any, asset_id: int) -> bool:
    row = conn.execute(
        "UPDATE assets SET deleted_at = now() WHERE id = %(id)s AND deleted_at IS NULL RETURNING id",
        {"id": asset_id},
    ).fetchone()
    return row is not None


def get_upload(conn: Any, upload_id: str, *, for_update: bool = False) -> Row | None:
    return conn.execute(
        "SELECT id, user_id, purpose, filename, filetype, length_bytes, received_bytes, path, status"
        " FROM uploads WHERE id = %(id)s" + (" FOR UPDATE" if for_update else ""),
        {"id": upload_id},
    ).fetchone()


def consume_upload(conn: Any, upload_id: str) -> None:
    conn.execute(
        "UPDATE uploads SET status = 'consumed', updated_at = now() WHERE id = %(id)s", {"id": upload_id}
    )
