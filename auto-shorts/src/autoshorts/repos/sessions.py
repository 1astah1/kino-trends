"""Таблица `sessions` (миграция 0002): cookie-сессии операторов.

Сессия жива, если не отозвана, не истёк абсолютный срок (`expires_at`) и с последнего
обращения (`last_seen_at`) прошло меньше idle-срока. Сам токен в БД не хранится — только sha256.
"""

from __future__ import annotations

from typing import Any

Row = dict[str, Any]

_ACTIVE = (
    "s.revoked_at IS NULL AND s.expires_at > now()"
    " AND s.last_seen_at > now() - make_interval(secs => CAST(%(idle_sec)s AS double precision))"
)


def insert(
    conn: Any,
    *,
    token_hash: bytes,
    csrf_token: str,
    user_id: int,
    absolute_days: int,
    ip_hash: str | None,
    user_agent: str | None,
) -> Row:
    return conn.execute(
        """
        INSERT INTO sessions (token_hash, csrf_token, user_id, expires_at, ip_hash, user_agent)
        VALUES (%(token_hash)s, %(csrf)s, %(user_id)s,
                now() + make_interval(days => CAST(%(days)s AS int)), %(ip_hash)s, %(ua)s)
        RETURNING id, user_id, csrf_token, created_at, last_seen_at, expires_at
        """,
        {
            "token_hash": token_hash,
            "csrf": csrf_token,
            "user_id": user_id,
            "days": absolute_days,
            "ip_hash": ip_hash,
            "ua": user_agent,
        },
    ).fetchone()


def get_active_by_token_hash(conn: Any, token_hash: bytes, *, idle_sec: int) -> Row | None:
    """Живая сессия + пользователь (активный). Поля пользователя с префиксом `user_`."""
    return conn.execute(
        f"""
        SELECT s.id AS session_id, s.csrf_token, s.last_seen_at, s.expires_at,
               EXTRACT(EPOCH FROM (now() - s.last_seen_at)) AS idle_for_sec,
               u.id AS user_id, u.login, u.display_name, u.role, u.tg_user_id
        FROM sessions s JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = %(token_hash)s AND {_ACTIVE} AND u.is_active
        """,
        {"token_hash": token_hash, "idle_sec": idle_sec},
    ).fetchone()


def touch(conn: Any, session_id: int) -> None:
    conn.execute("UPDATE sessions SET last_seen_at = now() WHERE id = %(id)s", {"id": session_id})


def list_active_for_user(conn: Any, user_id: int, *, idle_sec: int) -> list[Row]:
    return conn.execute(
        f"""
        SELECT s.id, s.created_at, s.last_seen_at, s.user_agent
        FROM sessions s
        WHERE s.user_id = %(user_id)s AND {_ACTIVE}
        ORDER BY s.last_seen_at DESC, s.id DESC
        """,
        {"user_id": user_id, "idle_sec": idle_sec},
    ).fetchall()


def revoke(conn: Any, session_id: int, *, user_id: int | None = None) -> bool:
    """Отозвать сессию (если `user_id` задан — только свою). True, если что-то отозвано."""
    cur = conn.execute(
        """
        UPDATE sessions SET revoked_at = now()
        WHERE id = %(id)s AND revoked_at IS NULL
          AND (CAST(%(user_id)s AS bigint) IS NULL OR user_id = CAST(%(user_id)s AS bigint))
        """,
        {"id": session_id, "user_id": user_id},
    )
    return cur.rowcount > 0


def exists_for_user(conn: Any, session_id: int, user_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 AS ok FROM sessions WHERE id = %(id)s AND user_id = %(user_id)s",
        {"id": session_id, "user_id": user_id},
    ).fetchone()
    return row is not None


def revoke_all_for_user(conn: Any, user_id: int, *, except_session_id: int | None = None) -> int:
    cur = conn.execute(
        """
        UPDATE sessions SET revoked_at = now()
        WHERE user_id = %(user_id)s AND revoked_at IS NULL
          AND (CAST(%(keep)s AS bigint) IS NULL OR id <> CAST(%(keep)s AS bigint))
        """,
        {"user_id": user_id, "keep": except_session_id},
    )
    return cur.rowcount
