"""SQL расписаний планировщика `schedules` (docs/ARCHITECTURE.md §4.2 0004/0009, §6.1 «Планировщик»)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

Row = dict[str, Any]

SCHEDULE_COLUMNS = (
    "name, task_kind, resource_class, payload, interval_sec, anchor_local_time, next_run_at, enabled,"
    " last_task_id"
)


def list_all(conn: Any) -> list[Row]:
    return conn.execute(f"SELECT {SCHEDULE_COLUMNS} FROM schedules ORDER BY name").fetchall()


def get(conn: Any, name: str, *, for_update: bool = False) -> Row | None:
    lock = " FOR UPDATE" if for_update else ""
    return conn.execute(
        f"SELECT {SCHEDULE_COLUMNS} FROM schedules WHERE name = %(name)s{lock}", {"name": name}
    ).fetchone()


def claim_due(conn: Any, *, limit: int = 20) -> list[Row]:
    """Созревшие расписания под блокировкой строки (SKIP LOCKED: второй лидер их не увидит)."""
    return conn.execute(
        f"""
        SELECT {SCHEDULE_COLUMNS} FROM schedules
        WHERE enabled AND next_run_at <= now()
        ORDER BY next_run_at, name
        LIMIT %(limit)s
        FOR UPDATE SKIP LOCKED
        """,
        {"limit": int(limit)},
    ).fetchall()


def advance(conn: Any, name: str, *, next_run_at: datetime, last_task_id: int | None) -> Row | None:
    return conn.execute(
        f"""
        UPDATE schedules SET next_run_at = %(next)s, last_task_id = COALESCE(%(task)s, last_task_id)
        WHERE name = %(name)s
        RETURNING {SCHEDULE_COLUMNS}
        """,
        {"name": name, "next": next_run_at, "task": last_task_id},
    ).fetchone()


def update(
    conn: Any, name: str, *, enabled: bool | None = None, interval_sec: int | None = None
) -> Row | None:
    """Правка из API (admin). Включение просроченного расписания не запускает «догонялки»:
    следующий запуск — не раньше чем сейчас."""
    return conn.execute(
        f"""
        UPDATE schedules SET
          enabled = COALESCE(%(enabled)s, enabled),
          interval_sec = COALESCE(CAST(%(interval)s AS int), interval_sec),
          next_run_at = CASE WHEN CAST(%(enabled)s AS boolean) IS TRUE AND NOT enabled
                             THEN GREATEST(next_run_at, now()) ELSE next_run_at END
        WHERE name = %(name)s
        RETURNING {SCHEDULE_COLUMNS}
        """,
        {"name": name, "enabled": enabled, "interval": interval_sec},
    ).fetchone()
