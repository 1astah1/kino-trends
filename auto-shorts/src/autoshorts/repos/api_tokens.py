"""Таблица `api_tokens` (миграция 0002): машинные токены `Authorization: Bearer ast_…`."""

from __future__ import annotations

from datetime import datetime
from typing import Any

Row = dict[str, Any]

COLUMNS = "id, user_id, name, prefix, scopes, created_at, last_used_at, expires_at, revoked_at"


def insert(
    conn: Any,
    *,
    user_id: int,
    name: str,
    prefix: str,
    token_hash: bytes,
    scopes: list[str],
    expires_at: datetime | None,
) -> Row:
    return conn.execute(
        f"""
        INSERT INTO api_tokens (user_id, name, prefix, token_hash, scopes, expires_at)
        VALUES (%(user_id)s, %(name)s, %(prefix)s, %(token_hash)s, %(scopes)s, %(expires_at)s)
        RETURNING {COLUMNS}
        """,
        {
            "user_id": user_id,
            "name": name,
            "prefix": prefix,
            "token_hash": token_hash,
            "scopes": scopes,
            "expires_at": expires_at,
        },
    ).fetchone()


def get_active_by_hash(conn: Any, token_hash: bytes) -> Row | None:
    """Живой токен + активный владелец (user_id, login, display_name, role, tg_user_id)."""
    return conn.execute(
        """
        SELECT t.id AS token_id, t.scopes, t.last_used_at,
               EXTRACT(EPOCH FROM (now() - COALESCE(t.last_used_at, t.created_at))) AS idle_for_sec,
               u.id AS user_id, u.login, u.display_name, u.role, u.tg_user_id
        FROM api_tokens t JOIN users u ON u.id = t.user_id
        WHERE t.token_hash = %(h)s AND t.revoked_at IS NULL
          AND (t.expires_at IS NULL OR t.expires_at > now()) AND u.is_active
        """,
        {"h": token_hash},
    ).fetchone()


def touch(conn: Any, token_id: int) -> None:
    conn.execute("UPDATE api_tokens SET last_used_at = now() WHERE id = %(id)s", {"id": token_id})


def list_for_user(conn: Any, user_id: int | None) -> list[Row]:
    """Токены пользователя (`None` — все), новые сверху."""
    return conn.execute(
        f"""
        SELECT {COLUMNS} FROM api_tokens
        WHERE CAST(%(user_id)s AS bigint) IS NULL OR user_id = CAST(%(user_id)s AS bigint)
        ORDER BY id DESC
        """,
        {"user_id": user_id},
    ).fetchall()


def get(conn: Any, token_id: int) -> Row | None:
    return conn.execute(f"SELECT {COLUMNS} FROM api_tokens WHERE id = %(id)s", {"id": token_id}).fetchone()


def revoke(conn: Any, token_id: int) -> bool:
    cur = conn.execute(
        "UPDATE api_tokens SET revoked_at = now() WHERE id = %(id)s AND revoked_at IS NULL",
        {"id": token_id},
    )
    return cur.rowcount > 0


def revoke_all_for_user(conn: Any, user_id: int) -> int:
    cur = conn.execute(
        "UPDATE api_tokens SET revoked_at = now() WHERE user_id = %(id)s AND revoked_at IS NULL",
        {"id": user_id},
    )
    return cur.rowcount
