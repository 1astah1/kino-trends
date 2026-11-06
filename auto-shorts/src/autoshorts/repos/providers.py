"""Таблица `provider_configs` (миграция 0002, сиды 0009) и сводка вызовов из `generation_events`."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

Row = dict[str, Any]

COLUMNS = "key, kind, enabled, is_default, params, secret_name, updated_by, updated_at"


def list_configs(conn: Any) -> list[Row]:
    return conn.execute(f"SELECT {COLUMNS} FROM provider_configs ORDER BY kind, key").fetchall()


def get(conn: Any, key: str, *, for_update: bool = False) -> Row | None:
    return conn.execute(
        f"SELECT {COLUMNS} FROM provider_configs WHERE key = %(key)s" + (" FOR UPDATE" if for_update else ""),
        {"key": key},
    ).fetchone()


def clear_default(conn: Any, kind: str, *, except_key: str) -> int:
    """Снять `is_default` у остальных провайдеров вида (уникальный индекс uq_provider_default_per_kind)."""
    return conn.execute(
        """
        UPDATE provider_configs SET is_default = false, updated_at = now()
        WHERE kind = %(kind)s AND is_default AND key <> %(key)s
        """,
        {"kind": kind, "key": except_key},
    ).rowcount


def upsert(
    conn: Any,
    *,
    key: str,
    kind: str,
    enabled: bool,
    is_default: bool,
    params: dict[str, Any],
    secret_name: str | None,
    updated_by: int | None,
) -> Row:
    return conn.execute(
        f"""
        INSERT INTO provider_configs
          (key, kind, enabled, is_default, params, secret_name, updated_by, updated_at)
        VALUES (%(key)s, %(kind)s, %(enabled)s, %(is_default)s, %(params)s, %(secret_name)s, %(by)s, now())
        ON CONFLICT (key) DO UPDATE
          SET enabled = EXCLUDED.enabled, is_default = EXCLUDED.is_default, params = EXCLUDED.params,
              secret_name = EXCLUDED.secret_name, updated_by = EXCLUDED.updated_by, updated_at = now()
        RETURNING {COLUMNS}
        """,
        {
            "key": key,
            "kind": kind,
            "enabled": enabled,
            "is_default": is_default,
            "params": Jsonb(params),
            "secret_name": secret_name,
            "by": updated_by,
        },
    ).fetchone()


def call_stats(conn: Any, *, days: int = 30) -> dict[str, Row]:
    """По провайдеру: последний успешный вызов и последняя ошибка за `days` дней (generation_events)."""
    rows = conn.execute(
        """
        SELECT provider,
               max(created_at) FILTER (WHERE status = 'ok') AS last_ok_at,
               max(created_at) FILTER (WHERE status = 'error') AS last_error_at,
               (array_agg(error ORDER BY created_at DESC, id DESC) FILTER (WHERE status = 'error'))[1]
                 AS last_error
        FROM generation_events
        WHERE created_at > now() - make_interval(days => CAST(%(days)s AS int))
        GROUP BY provider
        """,
        {"days": days},
    ).fetchall()
    return {r["provider"]: r for r in rows}
