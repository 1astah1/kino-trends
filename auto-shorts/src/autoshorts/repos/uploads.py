"""Таблица `uploads` (миграции 0005, 0012): сессии tus 1.0.0 (docs/ARCHITECTURE.md §5.2, §8.2).

Только SQL: функции принимают открытое соединение (строки — dict) и не управляют транзакциями.
Путь файла (`path`) хранится относительно MEDIA_ROOT: `uploads/<id>.part` пока идёт загрузка,
`uploads/<id>.<ext>` после последнего байта (уборка `.part` старше суток его не трогает).
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

Row = dict[str, Any]

COLUMNS = (
    "id, user_id, purpose, filename, filetype, length_bytes, received_bytes, path, status, metadata,"
    " created_at, updated_at, expires_at, completed_at"
)
ACTIVE_STATUSES = ("uploading", "complete")


def insert(
    conn: Any,
    *,
    upload_id: str,
    user_id: int,
    purpose: str,
    filename: str,
    filetype: str | None,
    length_bytes: int,
    path: str,
    metadata: dict[str, Any],
    expires_hours: int,
) -> Row:
    return conn.execute(
        f"""
        INSERT INTO uploads (id, user_id, purpose, filename, filetype, length_bytes, path, metadata,
                             expires_at)
        VALUES (%(id)s, %(user_id)s, %(purpose)s, %(filename)s, %(filetype)s, %(length)s, %(path)s,
                %(metadata)s, now() + make_interval(hours => CAST(%(hours)s AS int)))
        RETURNING {COLUMNS}
        """,
        {
            "id": upload_id,
            "user_id": user_id,
            "purpose": purpose,
            "filename": filename,
            "filetype": filetype,
            "length": int(length_bytes),
            "path": path,
            "metadata": Jsonb(metadata),
            "hours": int(expires_hours),
        },
    ).fetchone()


def get(conn: Any, upload_id: str, *, for_update: bool = False) -> Row | None:
    return conn.execute(
        f"SELECT {COLUMNS}, (expires_at < now()) AS is_expired FROM uploads WHERE id = %(id)s"
        + (" FOR UPDATE" if for_update else ""),
        {"id": upload_id},
    ).fetchone()


def pending_bytes(conn: Any) -> int:
    """Сколько байт ещё придёт во все незавершённые живые загрузки (для дискового сторожа)."""
    row = conn.execute(
        """
        SELECT COALESCE(sum(length_bytes - received_bytes), 0) AS n FROM uploads
        WHERE status = 'uploading' AND expires_at > now()
        """
    ).fetchone()
    return int(row["n"])


def record_progress(conn: Any, upload_id: str, *, received_bytes: int, expires_hours: int) -> Row | None:
    """Новое смещение после PATCH; срок жизни сессии отсчитывается заново (24 ч с последнего чанка)."""
    return conn.execute(
        f"""
        UPDATE uploads SET received_bytes = %(received)s, updated_at = now(),
               expires_at = now() + make_interval(hours => CAST(%(hours)s AS int))
        WHERE id = %(id)s AND status = 'uploading'
        RETURNING {COLUMNS}
        """,
        {"id": upload_id, "received": int(received_bytes), "hours": int(expires_hours)},
    ).fetchone()


def mark_complete(conn: Any, upload_id: str, *, path: str) -> Row | None:
    return conn.execute(
        f"""
        UPDATE uploads SET status = 'complete', received_bytes = length_bytes, path = %(path)s,
               completed_at = now(), updated_at = now()
        WHERE id = %(id)s AND status = 'uploading'
        RETURNING {COLUMNS}
        """,
        {"id": upload_id, "path": path},
    ).fetchone()


def set_status(conn: Any, upload_id: str, status: str, *, only_from: tuple[str, ...] = ()) -> Row | None:
    where = " AND status = ANY(%(from)s)" if only_from else ""
    return conn.execute(
        f"UPDATE uploads SET status = %(status)s, updated_at = now() WHERE id = %(id)s{where}"
        f" RETURNING {COLUMNS}",
        {"id": upload_id, "status": status, "from": list(only_from)},
    ).fetchone()


def consume(conn: Any, upload_id: str, *, path: str | None = None) -> Row | None:
    """complete → consumed (загрузка стала источником или документом прав); `path` — новое место файла."""
    return conn.execute(
        f"""
        UPDATE uploads SET status = 'consumed', path = COALESCE(%(path)s, path), updated_at = now()
        WHERE id = %(id)s AND status = 'complete'
        RETURNING {COLUMNS}
        """,
        {"id": upload_id, "path": path},
    ).fetchone()


def expire_stale(conn: Any, *, limit: int = 500) -> list[Row]:
    """uploading с истёкшим сроком → expired (файлы удаляет вызывающий или уборка)."""
    return conn.execute(
        f"""
        UPDATE uploads SET status = 'expired', updated_at = now()
        WHERE id IN (SELECT id FROM uploads WHERE status = 'uploading' AND expires_at < now()
                     ORDER BY expires_at LIMIT %(limit)s)
        RETURNING {COLUMNS}
        """,
        {"limit": int(limit)},
    ).fetchall()


__all__ = [
    "ACTIVE_STATUSES",
    "COLUMNS",
    "consume",
    "expire_stale",
    "get",
    "insert",
    "mark_complete",
    "pending_bytes",
    "record_progress",
    "set_status",
]
