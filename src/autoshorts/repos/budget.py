"""Бюджет дня `budget_state` (одна строка id=1; миграции 0001, 0004), журнал `generation_events`
и постоянные резервы `budget_reservations` (0016; правила закрытия — в заголовке миграции)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg import sql

Row = dict[str, Any]

STATE_COLUMNS = (
    "daily_limit_usd, spent_today_usd, reserved_usd, day, paused, pause_reason, alert_threshold_pct,"
    " alerted_at"
)
EDITABLE = frozenset(
    {
        "daily_limit_usd",
        "spent_today_usd",
        "reserved_usd",
        "day",
        "paused",
        "pause_reason",
        "alert_threshold_pct",
        "alerted_at",
    }
)

GENEV_COLUMNS = (
    "id, job_id, task_id, source_id, trend_brief_id, step_name, provider, model, units, unit_price_usd,"
    " cost_usd, latency_ms, http_status, status, error, cpu_ms, created_at"
)


def get_state(conn: Any, *, tz: str, for_update: bool = False) -> Row | None:
    """Строка бюджета + `business_day` — текущий бизнес-день по часам БД в поясе `tz`."""
    return conn.execute(
        f"""
        SELECT {STATE_COLUMNS}, (now() AT TIME ZONE CAST(%(tz)s AS text))::date AS business_day
        FROM budget_state WHERE id = 1
        """
        + (" FOR UPDATE" if for_update else ""),
        {"tz": tz},
    ).fetchone()


def update(conn: Any, fields: dict[str, Any]) -> None:
    unknown = set(fields) - EDITABLE
    if unknown:
        raise ValueError(f"нельзя менять колонки budget_state: {sorted(unknown)}")
    if not fields:
        return
    assignments = sql.SQL(", ").join(
        sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder(k)) for k in sorted(fields)
    )
    conn.execute(sql.SQL("UPDATE budget_state SET {} WHERE id = 1").format(assignments), fields)


def list_generation_events(
    conn: Any,
    *,
    job_id: int | None = None,
    source_id: int | None = None,
    provider: str | None = None,
    step_name: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    before_id: int | None = None,
    limit: int = 50,
) -> list[Row]:
    """Журнал вызовов провайдеров, новые сверху; интервал времени [from, to)."""
    return conn.execute(
        f"""
        SELECT {GENEV_COLUMNS} FROM generation_events
        WHERE (CAST(%(job)s AS bigint) IS NULL OR job_id = CAST(%(job)s AS bigint))
          AND (CAST(%(src)s AS bigint) IS NULL OR source_id = CAST(%(src)s AS bigint))
          AND (CAST(%(provider)s AS text) IS NULL OR provider = CAST(%(provider)s AS text))
          AND (CAST(%(step)s AS text) IS NULL OR step_name = CAST(%(step)s AS text))
          AND (CAST(%(t_from)s AS timestamptz) IS NULL OR created_at >= CAST(%(t_from)s AS timestamptz))
          AND (CAST(%(t_to)s AS timestamptz) IS NULL OR created_at < CAST(%(t_to)s AS timestamptz))
          AND (CAST(%(before)s AS bigint) IS NULL OR id < CAST(%(before)s AS bigint))
        ORDER BY id DESC
        LIMIT %(limit)s
        """,
        {
            "job": job_id,
            "src": source_id,
            "provider": provider,
            "step": step_name,
            "t_from": created_from,
            "t_to": created_to,
            "before": before_id,
            "limit": limit,
        },
    ).fetchall()


GENEV_INSERTABLE = (
    "job_id",
    "task_id",
    "source_id",
    "trend_brief_id",
    "step_name",
    "provider",
    "model",
    "units",
    "unit_price_usd",
    "cost_usd",
    "latency_ms",
    "http_status",
    "status",
    "error",
    "cpu_ms",
)


def insert_generation_event(conn: Any, fields: dict[str, Any]) -> int:
    """Строка `generation_events` (нужен хотя бы один из job_id/task_id/source_id/trend_brief_id)."""
    unknown = set(fields) - set(GENEV_INSERTABLE)
    if unknown:
        raise ValueError(f"неизвестные поля generation_events: {sorted(unknown)}")
    columns = [c for c in GENEV_INSERTABLE if c in fields]
    query = sql.SQL("INSERT INTO generation_events ({}) VALUES ({}) RETURNING id").format(
        sql.SQL(", ").join(sql.Identifier(c) for c in columns),
        sql.SQL(", ").join(sql.Placeholder(c) for c in columns),
    )
    return int(conn.execute(query, fields).fetchone()["id"])


def refresh_job_cost(conn: Any, job_id: int) -> None:
    """`jobs.cost_usd` — кеш суммы `generation_events` ролика (§6.1)."""
    conn.execute(
        """
        UPDATE jobs SET cost_usd = COALESCE(
          (SELECT sum(cost_usd) FROM generation_events WHERE job_id = %(id)s), 0)
        WHERE id = %(id)s
        """,
        {"id": job_id},
    )


# ---------------------------------------------------------------- резервы (0016)

RESERVATION_COLUMNS = (
    "id, task_id, task_attempt, worker_id, job_id, source_id, trend_brief_id, provider, amount_usd,"
    " business_day, status, actual_usd, generation_event_id, release_reason, created_at, closed_at"
)


def insert_reservation(
    conn: Any,
    *,
    amount_usd: Any,
    provider: str,
    business_day: Any,
    task_id: int | None = None,
    job_id: int | None = None,
    source_id: int | None = None,
    trend_brief_id: int | None = None,
) -> Row:
    """Открытый резерв (правила — заголовок 0016). Попытка и воркер берутся из строки задачи
    (`tasks.attempts`, `tasks.locked_by`) в момент резерва. Ссылка на несуществующую задачу, ролик,
    источник или бриф обнуляется, а не роняет резерв (резерв без задачи reaper снимет через час)."""
    return conn.execute(
        f"""
        INSERT INTO budget_reservations
          (task_id, task_attempt, worker_id, job_id, source_id, trend_brief_id, provider, amount_usd,
           business_day)
        SELECT t.id, t.attempts, t.locked_by,
               (SELECT j.id FROM jobs j WHERE j.id = CAST(%(job)s AS bigint)),
               (SELECT s.id FROM sources s WHERE s.id = CAST(%(src)s AS bigint)),
               (SELECT b.id FROM trend_briefs b WHERE b.id = CAST(%(brief)s AS bigint)),
               CAST(%(provider)s AS text), CAST(%(amount)s AS numeric), CAST(%(day)s AS date)
        FROM (SELECT 1) AS one
        LEFT JOIN tasks t ON t.id = CAST(%(task)s AS bigint)
        RETURNING {RESERVATION_COLUMNS}
        """,
        {
            "task": task_id,
            "job": job_id,
            "src": source_id,
            "brief": trend_brief_id,
            "provider": provider,
            "amount": amount_usd,
            "day": business_day,
        },
    ).fetchone()


def close_reservation(
    conn: Any,
    reservation_id: int,
    *,
    status: str,
    actual_usd: Any = None,
    release_reason: str | None = None,
    generation_event_id: int | None = None,
) -> Row | None:
    """Закрыть открытый резерв: `UPDATE … WHERE id = … AND status = 'open' RETURNING`.

    None — резерв уже закрыт (гейт, reaper или повторный вызов): вычитать из `reserved_usd` нечего.
    """
    if status not in ("settled", "released"):
        raise ValueError(f"status must be settled|released, got {status!r}")
    return conn.execute(
        f"""
        UPDATE budget_reservations
        SET status = CAST(%(status)s AS text), closed_at = now(),
            actual_usd = CAST(%(actual)s AS numeric),
            release_reason = CAST(%(reason)s AS text),
            generation_event_id = CAST(%(event)s AS bigint)
        WHERE id = %(id)s AND status = 'open'
        RETURNING {RESERVATION_COLUMNS}
        """,
        {
            "id": reservation_id,
            "status": status,
            "actual": actual_usd,
            "reason": release_reason if status == "released" else None,
            "event": generation_event_id,
        },
    ).fetchone()


def annotate_closed_reservation(
    conn: Any, reservation_id: int, *, actual_usd: Any, generation_event_id: int | None = None
) -> Row | None:
    """Вызов досчитан после того, как резерв уже сняли (reaper, `release_open`): статус не меняется,
    в строку дописываются фактический расход и событие."""
    return conn.execute(
        f"""
        UPDATE budget_reservations
        SET actual_usd = CAST(%(actual)s AS numeric),
            generation_event_id = COALESCE(CAST(%(event)s AS bigint), generation_event_id)
        WHERE id = %(id)s AND status <> 'open'
        RETURNING {RESERVATION_COLUMNS}
        """,
        {"id": reservation_id, "actual": actual_usd, "event": generation_event_id},
    ).fetchone()


def get_reservation(conn: Any, reservation_id: int) -> Row | None:
    return conn.execute(
        f"SELECT {RESERVATION_COLUMNS} FROM budget_reservations WHERE id = %(id)s", {"id": reservation_id}
    ).fetchone()


def list_orphan_reservations(conn: Any, *, limit: int = 500) -> list[Row]:
    """Открытые резервы без живого хозяина (`v_budget_orphan_reservations`), старые первыми."""
    return conn.execute(
        """
        SELECT id, task_id, worker_id, provider, amount_usd, business_day, created_at, orphan_reason
        FROM v_budget_orphan_reservations
        ORDER BY id
        LIMIT %(limit)s
        """,
        {"limit": limit},
    ).fetchall()


def open_reserved_total(conn: Any, business_day: Any) -> Any:
    """Сумма открытых резервов дня — то, чему должен быть равен `budget_state.reserved_usd`
    (кроме резервов без строки, выданных до 0016)."""
    row = conn.execute(
        """
        SELECT COALESCE(sum(amount_usd), 0) AS total FROM budget_reservations
        WHERE status = 'open' AND business_day = CAST(%(day)s AS date)
        """,
        {"day": business_day},
    ).fetchone()
    return row["total"]


def delete_closed_reservations(conn: Any, *, older_than_days: int, limit: int = 5000) -> int:
    """Уборка закрытых резервов старше N дней (открытые не трогаются никогда)."""
    cur = conn.execute(
        """
        DELETE FROM budget_reservations WHERE id IN (
          SELECT id FROM budget_reservations
          WHERE created_at < now() - make_interval(days => CAST(%(days)s AS int))
            AND status <> 'open'
          ORDER BY id
          LIMIT %(limit)s)
        """,
        {"days": older_than_days, "limit": limit},
    )
    return int(cur.rowcount or 0)
