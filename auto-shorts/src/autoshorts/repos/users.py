"""Таблица `users` (миграция 0002)."""

from __future__ import annotations

from typing import Any

from psycopg import sql

Row = dict[str, Any]

PUBLIC_COLUMNS = (
    "id, login, display_name, role, tg_user_id, is_active, failed_logins, locked_until,"
    " last_login_at, created_at, updated_at"
)
# колонки, которые можно менять через update_fields (без пароля и счётчиков входа)
EDITABLE_COLUMNS = frozenset({"display_name", "role", "is_active", "tg_user_id"})


def get(conn: Any, user_id: int, *, for_update: bool = False) -> Row | None:
    return conn.execute(
        f"SELECT {PUBLIC_COLUMNS} FROM users WHERE id = %(id)s" + (" FOR UPDATE" if for_update else ""),
        {"id": user_id},
    ).fetchone()


def get_with_password(
    conn: Any, *, login: str | None = None, user_id: int | None = None, for_update: bool = False
) -> Row | None:
    """Строка пользователя вместе с `password_hash` — только для проверки пароля."""
    if (login is None) == (user_id is None):
        raise ValueError("нужен ровно один из login / user_id")
    where = "login = %(v)s" if login is not None else "id = %(v)s"
    return conn.execute(
        f"SELECT {PUBLIC_COLUMNS}, password_hash FROM users WHERE {where}"
        + (" FOR UPDATE" if for_update else ""),
        {"v": login if login is not None else user_id},
    ).fetchone()


def list_all(conn: Any) -> list[Row]:
    return conn.execute(f"SELECT {PUBLIC_COLUMNS} FROM users ORDER BY id").fetchall()


def insert(
    conn: Any, *, login: str, display_name: str, password_hash: str, role: str, tg_user_id: int | None
) -> Row:
    return conn.execute(
        f"""
        INSERT INTO users (login, display_name, password_hash, role, tg_user_id)
        VALUES (%(login)s, %(display_name)s, %(password_hash)s, %(role)s, %(tg_user_id)s)
        RETURNING {PUBLIC_COLUMNS}
        """,
        {
            "login": login,
            "display_name": display_name,
            "password_hash": password_hash,
            "role": role,
            "tg_user_id": tg_user_id,
        },
    ).fetchone()


def update_fields(conn: Any, user_id: int, fields: dict[str, Any]) -> Row | None:
    """UPDATE разрешённых колонок; пустой `fields` — просто вернуть строку."""
    unknown = set(fields) - EDITABLE_COLUMNS
    if unknown:
        raise ValueError(f"нельзя менять колонки users: {sorted(unknown)}")
    if not fields:
        return get(conn, user_id)
    assignments = sql.SQL(", ").join(
        sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder(k)) for k in sorted(fields)
    )
    query = sql.SQL("UPDATE users SET {}, updated_at = now() WHERE id = %(id)s RETURNING {}").format(
        assignments, sql.SQL(PUBLIC_COLUMNS)
    )
    return conn.execute(query, {**fields, "id": user_id}).fetchone()


def set_password(conn: Any, user_id: int, password_hash: str, *, clear_lock: bool = True) -> bool:
    lock = ", failed_logins = 0, locked_until = NULL" if clear_lock else ""
    cur = conn.execute(
        f"UPDATE users SET password_hash = %(h)s{lock}, updated_at = now() WHERE id = %(id)s",
        {"h": password_hash, "id": user_id},
    )
    return cur.rowcount > 0


def record_login_success(conn: Any, user_id: int, *, new_password_hash: str | None = None) -> None:
    conn.execute(
        """
        UPDATE users SET failed_logins = 0, locked_until = NULL, last_login_at = now(),
               password_hash = COALESCE(%(h)s, password_hash)
        WHERE id = %(id)s
        """,
        {"id": user_id, "h": new_password_hash},
    )


def set_login_failures(conn: Any, user_id: int, count: int, *, lock_minutes: int | None) -> Row:
    """Записать счётчик неудач; `lock_minutes` — заблокировать вход на столько минут от now()."""
    return conn.execute(
        """
        UPDATE users SET failed_logins = %(n)s,
               locked_until = CASE WHEN CAST(%(lock)s AS int) IS NULL THEN locked_until
                                   ELSE now() + make_interval(mins => CAST(%(lock)s AS int)) END
        WHERE id = %(id)s
        RETURNING failed_logins, locked_until
        """,
        {"n": count, "lock": lock_minutes, "id": user_id},
    ).fetchone()


def count_active_admins(conn: Any, *, exclude_user_id: int | None = None) -> int:
    row = conn.execute(
        """
        SELECT count(*) AS n FROM users
        WHERE role = 'admin' AND is_active
          AND (CAST(%(ex)s AS bigint) IS NULL OR id <> CAST(%(ex)s AS bigint))
        """,
        {"ex": exclude_user_id},
    ).fetchone()
    return int(row["n"])
