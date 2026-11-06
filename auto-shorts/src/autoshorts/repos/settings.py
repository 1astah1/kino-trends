"""Таблица `settings` (миграция 0002): только переопределения; дефолты — domain/settings_schema.py."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

Row = dict[str, Any]


def list_overrides(conn: Any) -> list[Row]:
    return conn.execute("SELECT key, value, updated_by, updated_at FROM settings ORDER BY key").fetchall()


def upsert(conn: Any, key: str, value: Any, *, updated_by: int | None) -> None:
    conn.execute(
        """
        INSERT INTO settings (key, value, updated_by, updated_at)
        VALUES (%(key)s, %(value)s, %(by)s, now())
        ON CONFLICT (key) DO UPDATE
          SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = now()
        """,
        {"key": key, "value": Jsonb(value), "by": updated_by},
    )


def delete(conn: Any, key: str) -> bool:
    return conn.execute("DELETE FROM settings WHERE key = %(key)s", {"key": key}).rowcount > 0


def lock_table(conn: Any) -> None:
    """Сериализовать PATCH настроек (одновременные правки не перетирают проверку порогов)."""
    conn.execute("LOCK TABLE settings IN SHARE ROW EXCLUSIVE MODE")
