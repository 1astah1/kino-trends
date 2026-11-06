"""Таблица `banner_creatives` (миграция 0011): загруженные креативы баннера, ключ — sha256 исходника.

Сколько вариантов ссылается на креатив — по GIN-индексу `banner_variants.layers jsonb_path_ops`:
`layers @> jsonb_build_array(jsonb_build_object('creative_sha256', <sha>))`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

Row = dict[str, Any]

_USED_BY = (
    "(SELECT count(*) FROM banner_variants v"
    " WHERE v.layers @> jsonb_build_array(jsonb_build_object('creative_sha256', b.sha256)))"
    " AS used_by_variants"
)
COLUMNS = (
    "b.sha256, b.name, b.status, b.error_code, b.error_message, b.kind, b.source_format, b.source_bytes,"
    " b.source_width, b.source_height, b.upload_path, b.width, b.height, b.has_alpha, b.duration_sec, b.fps,"
    " b.frame_count, b.loop, b.normalized_path, b.normalized_mime, b.normalized_bytes, b.thumb_path,"
    " b.first_frame_path, b.normalizer_version, b.meta, b.product_id, b.notes, b.task_id, b.created_by,"
    " b.created_at, b.updated_at, b.deleted_at, " + _USED_BY
)
SORTABLE = {
    "created_at": "b.created_at",
    "updated_at": "b.updated_at",
    "name": "b.name",
    "source_bytes": "b.source_bytes",
}
READY_FIELDS = (
    "kind",
    "source_format",
    "source_bytes",
    "source_width",
    "source_height",
    "width",
    "height",
    "has_alpha",
    "duration_sec",
    "fps",
    "frame_count",
    "loop",
    "normalized_path",
    "normalized_mime",
    "normalized_bytes",
    "thumb_path",
    "first_frame_path",
    "normalizer_version",
)
EDITABLE_COLUMNS = frozenset({"name", "notes", "product_id"})


def get(conn: Any, sha256: str, *, include_deleted: bool = False, for_update: bool = False) -> Row | None:
    if for_update:
        conn.execute("SELECT 1 FROM banner_creatives WHERE sha256 = %(sha)s FOR UPDATE", {"sha": sha256})
    where = "" if include_deleted else " AND b.deleted_at IS NULL"
    return conn.execute(
        f"SELECT {COLUMNS} FROM banner_creatives b WHERE b.sha256 = %(sha)s{where}", {"sha": sha256}
    ).fetchone()


def get_many(conn: Any, shas: Sequence[str], *, include_deleted: bool = False) -> dict[str, Row]:
    shas = sorted(set(shas))
    if not shas:
        return {}
    where = "" if include_deleted else " AND b.deleted_at IS NULL"
    rows = conn.execute(
        f"SELECT {COLUMNS} FROM banner_creatives b WHERE b.sha256 = ANY(%(shas)s){where}", {"shas": shas}
    ).fetchall()
    return {r["sha256"]: r for r in rows}


def list_page(
    conn: Any,
    *,
    kind: str | None,
    statuses: Sequence[str] | None,
    product_id: str | None,
    q: str | None,
    order: sql.Composable,
    limit: int,
    offset: int,
) -> tuple[list[Row], int]:
    conds = [sql.SQL("b.deleted_at IS NULL")]
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if kind:
        conds.append(sql.SQL("b.kind = %(kind)s"))
        params["kind"] = kind
    if statuses:
        conds.append(sql.SQL("b.status = ANY(%(statuses)s)"))
        params["statuses"] = list(statuses)
    if product_id:
        conds.append(sql.SQL("b.product_id = %(product_id)s"))
        params["product_id"] = product_id
    if q:
        conds.append(sql.SQL("b.name ILIKE %(q)s"))
        params["q"] = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    where = sql.SQL(" AND ").join(conds)
    total = conn.execute(
        sql.SQL("SELECT count(*) AS n FROM banner_creatives b WHERE {}").format(where), params
    ).fetchone()["n"]
    rows = conn.execute(
        sql.SQL("SELECT {} FROM banner_creatives b WHERE {} {} LIMIT %(limit)s OFFSET %(offset)s").format(
            sql.SQL(COLUMNS), where, order
        ),
        params,
    ).fetchall()
    return rows, int(total)


def insert(conn: Any, fields: Mapping[str, Any]) -> bool:
    """Новая строка; занятый sha256 → False (строку вернёт `get`)."""
    data = dict(fields)
    if "meta" in data:
        data["meta"] = Jsonb(data["meta"])
    names = sorted(data)
    row = conn.execute(
        sql.SQL(
            "INSERT INTO banner_creatives ({}) VALUES ({}) ON CONFLICT (sha256) DO NOTHING RETURNING sha256"
        ).format(
            sql.SQL(", ").join(sql.Identifier(n) for n in names),
            sql.SQL(", ").join(sql.Placeholder(n) for n in names),
        ),
        data,
    ).fetchone()
    return row is not None


def set_ready(
    conn: Any, sha256: str, fields: Mapping[str, Any], *, meta: Mapping[str, Any] | None = None
) -> None:
    """Нормализация готова: заполнить поля файла, снять ошибку и исходник."""
    data = {k: fields[k] for k in READY_FIELDS}
    assignments = sql.SQL(", ").join(
        sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder(k)) for k in READY_FIELDS
    )
    conn.execute(
        sql.SQL(
            "UPDATE banner_creatives SET {}, status = 'ready', error_code = NULL, error_message = NULL,"
            " upload_path = NULL, meta = meta || %(meta)s, updated_at = now() WHERE sha256 = %(sha)s"
        ).format(assignments),
        {**data, "meta": Jsonb(dict(meta or {})), "sha": sha256},
    )


def set_processing(conn: Any, sha256: str, *, upload_path: str, task_id: int | None = None) -> None:
    conn.execute(
        """
        UPDATE banner_creatives SET status = 'processing', error_code = NULL, error_message = NULL,
               upload_path = %(path)s, task_id = COALESCE(%(task)s, task_id), updated_at = now()
        WHERE sha256 = %(sha)s
        """,
        {"sha": sha256, "path": upload_path, "task": task_id},
    )


def set_task(conn: Any, sha256: str, task_id: int) -> None:
    conn.execute(
        "UPDATE banner_creatives SET task_id = %(task)s, updated_at = now() WHERE sha256 = %(sha)s",
        {"sha": sha256, "task": task_id},
    )


def set_failed(conn: Any, sha256: str, *, error_code: str, error_message: str) -> None:
    conn.execute(
        """
        UPDATE banner_creatives SET status = 'failed', error_code = %(code)s, error_message = %(msg)s,
               upload_path = NULL, updated_at = now()
        WHERE sha256 = %(sha)s
        """,
        {"sha": sha256, "code": error_code, "msg": error_message[:2000]},
    )


def update_meta(conn: Any, sha256: str, fields: Mapping[str, Any]) -> None:
    unknown = set(fields) - EDITABLE_COLUMNS
    if unknown:
        raise ValueError(f"cannot update banner_creatives columns: {sorted(unknown)}")
    if not fields:
        return
    assignments = sql.SQL(", ").join(
        sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder(k)) for k in sorted(fields)
    )
    conn.execute(
        sql.SQL("UPDATE banner_creatives SET {}, updated_at = now() WHERE sha256 = %(pk)s").format(
            assignments
        ),
        {**fields, "pk": sha256},
    )


def revive(conn: Any, sha256: str, *, name: str | None, product_id: str | None) -> None:
    """Повторная загрузка мягко удалённого креатива: вернуть в библиотеку."""
    conn.execute(
        """
        UPDATE banner_creatives SET deleted_at = NULL, name = COALESCE(%(name)s, name),
               product_id = COALESCE(%(product)s, product_id), updated_at = now()
        WHERE sha256 = %(sha)s
        """,
        {"sha": sha256, "name": name, "product": product_id},
    )


def soft_delete(conn: Any, sha256: str) -> None:
    conn.execute(
        "UPDATE banner_creatives SET deleted_at = now(), updated_at = now() WHERE sha256 = %(sha)s",
        {"sha": sha256},
    )


def variants_using(conn: Any, sha256: str) -> list[int]:
    rows = conn.execute(
        """
        SELECT id FROM banner_variants
        WHERE layers @> jsonb_build_array(jsonb_build_object('creative_sha256', CAST(%(sha)s AS text)))
        ORDER BY id
        """,
        {"sha": sha256},
    ).fetchall()
    return [int(r["id"]) for r in rows]
