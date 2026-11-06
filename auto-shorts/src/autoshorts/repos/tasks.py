"""SQL очереди задач `tasks` (docs/ARCHITECTURE.md §4.2 0004, §6.1).

Только SQL: функции принимают открытое соединение (`conn`, строки — dict) и не управляют
транзакциями. Переходы, выполняемые воркером, защищены условием `status = 'running' AND
locked_by = <worker>`: если reaper уже забрал задачу, запись результата — no-op (None).

Параметры внутри CASE и интервальной арифметики — только через CAST / make_interval (§1.5).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from autoshorts.core.logging import redact_text

Row = dict[str, Any]

TASK_COLUMNS = (
    "id, kind, resource_class, status, priority, run_after, job_id, source_id, stage, payload, dedup_key,"
    " attempts, max_attempts, locked_by, locked_at, heartbeat_at, cancel_requested, progress, started_at,"
    " finished_at, error_class, last_error, defer_reason, result, created_at, updated_at"
)
ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")
LAST_ERROR_MAX_CHARS = 4000  # «хвост 4 КБ» (§4.2)


def clip_error(text: str | None, limit: int = LAST_ERROR_MAX_CHARS) -> str | None:
    """Хвост сообщения об ошибке не длиннее `limit` символов (важен конец трейсбека).

    Секреты маскируются до обрезки: `last_error` и `jobs.error` отдаются viewer и выше.
    """
    if text is None:
        return None
    text = redact_text(str(text))
    return text if len(text) <= limit else "…" + text[-(limit - 1) :]


# ---------------------------------------------------------------- вставка


def insert(
    conn: Any,
    *,
    kind: str,
    resource_class: str,
    priority: int = 100,
    max_attempts: int = 3,
    run_after: datetime | None = None,
    delay_sec: float | None = None,
    job_id: int | None = None,
    source_id: int | None = None,
    stage: str | None = None,
    payload: dict[str, Any] | None = None,
    dedup_key: str | None = None,
) -> Row | None:
    """Вставить задачу. При активном дубле `dedup_key` (queued/running) — None, ничего не вставлено."""
    row = conn.execute(
        f"""
        INSERT INTO tasks (kind, resource_class, priority, max_attempts, run_after, job_id, source_id,
                           stage, payload, dedup_key)
        VALUES (%(kind)s, %(cls)s, %(priority)s, %(max_attempts)s,
                COALESCE(CAST(%(run_after)s AS timestamptz), now())
                  + make_interval(secs => CAST(%(delay)s AS double precision)),
                %(job_id)s, %(source_id)s, %(stage)s, %(payload)s, %(dedup)s)
        ON CONFLICT (dedup_key) WHERE status IN ('queued','running') DO NOTHING
        RETURNING {TASK_COLUMNS}
        """,
        {
            "kind": kind,
            "cls": resource_class,
            "priority": int(priority),
            "max_attempts": int(max_attempts),
            "run_after": run_after,
            "delay": float(delay_sec or 0.0),
            "job_id": job_id,
            "source_id": source_id,
            "stage": stage,
            "payload": Jsonb(payload or {}),
            "dedup": dedup_key,
        },
    ).fetchone()
    return row


def get_active_by_dedup(conn: Any, dedup_key: str) -> Row | None:
    return conn.execute(
        f"SELECT {TASK_COLUMNS} FROM tasks WHERE dedup_key = %(k)s AND status IN ('queued','running')",
        {"k": dedup_key},
    ).fetchone()


# ---------------------------------------------------------------- взятие (§6.1)


def has_claimable(conn: Any, resource_class: str, kinds: Sequence[str] | None = None) -> bool:
    row = conn.execute(
        """
        SELECT EXISTS (
          SELECT 1 FROM tasks
          WHERE status = 'queued' AND resource_class = %(cls)s AND run_after <= now()
            AND (CAST(%(kinds)s AS text[]) IS NULL OR kind = ANY(CAST(%(kinds)s AS text[])))
        ) AS has
        """,
        {"cls": resource_class, "kinds": list(kinds) if kinds is not None else None},
    ).fetchone()
    return bool(row["has"])


def count_pending(conn: Any, resource_class: str, kinds: Sequence[str] | None = None) -> int:
    """Ждущие задачи класса с любым run_after (в том числе отложенные на будущее)."""
    row = conn.execute(
        """
        SELECT count(*) AS n FROM tasks
        WHERE status = 'queued' AND resource_class = %(cls)s
          AND (CAST(%(kinds)s AS text[]) IS NULL OR kind = ANY(CAST(%(kinds)s AS text[])))
        """,
        {"cls": resource_class, "kinds": list(kinds) if kinds is not None else None},
    ).fetchone()
    return int(row["n"])


def claim_next(
    conn: Any, resource_class: str, worker_id: str, kinds: Sequence[str] | None = None
) -> Row | None:
    """Взять следующую задачу класса (одна транзакция, SKIP LOCKED). `kinds` — только известные
    воркеру виды (None — любые). attempts += 1, defer_reason сбрасывается."""
    return conn.execute(
        f"""
        WITH next AS (
          SELECT id FROM tasks
          WHERE status = 'queued' AND resource_class = %(cls)s AND run_after <= now()
            AND (CAST(%(kinds)s AS text[]) IS NULL OR kind = ANY(CAST(%(kinds)s AS text[])))
          ORDER BY priority, run_after, id
          FOR UPDATE SKIP LOCKED
          LIMIT 1)
        UPDATE tasks t SET status = 'running', locked_by = %(worker)s, locked_at = now(),
               heartbeat_at = now(), started_at = now(), finished_at = NULL,
               attempts = t.attempts + 1, defer_reason = NULL, progress = NULL, updated_at = now()
        FROM next WHERE t.id = next.id
        RETURNING {", ".join("t." + c.strip() for c in TASK_COLUMNS.split(","))}
        """,
        {"cls": resource_class, "worker": worker_id, "kinds": list(kinds) if kinds is not None else None},
    ).fetchone()


# ---------------------------------------------------------------- heartbeat и прогресс


def heartbeat(conn: Any, task_id: int, worker_id: str, progress_pct: float | None = None) -> Row | None:
    """Продлить аренду; вернуть `{cancel_requested, progress}` или None, если аренда потеряна."""
    return conn.execute(
        """
        UPDATE tasks SET heartbeat_at = now(),
               progress = COALESCE(CAST(%(pct)s AS numeric), progress), updated_at = now()
        WHERE id = %(id)s AND status = 'running' AND locked_by = %(worker)s
        RETURNING cancel_requested, progress, kind, job_id, source_id, stage
        """,
        {"id": task_id, "worker": worker_id, "pct": progress_pct},
    ).fetchone()


# ---------------------------------------------------------------- исходы воркера


def _finish(conn: Any, task_id: int, worker_id: str, set_sql: str, params: dict[str, Any]) -> Row | None:
    return conn.execute(
        f"""
        UPDATE tasks SET {set_sql}, updated_at = now()
        WHERE id = %(id)s AND status = 'running' AND locked_by = %(worker)s
        RETURNING {TASK_COLUMNS}
        """,
        {"id": task_id, "worker": worker_id, **params},
    ).fetchone()


def mark_succeeded(conn: Any, task_id: int, worker_id: str, result: dict[str, Any] | None) -> Row | None:
    return _finish(
        conn,
        task_id,
        worker_id,
        "status = 'succeeded', finished_at = now(), locked_by = NULL, progress = 100,"
        " result = %(result)s, error_class = NULL, last_error = NULL, defer_reason = NULL",
        {"result": Jsonb(result) if result is not None else None},
    )


def mark_failed(
    conn: Any, task_id: int, worker_id: str, error_class: str, last_error: str | None
) -> Row | None:
    return _finish(
        conn,
        task_id,
        worker_id,
        "status = 'failed', finished_at = now(), locked_by = NULL,"
        " error_class = %(ec)s, last_error = %(err)s",
        {"ec": error_class, "err": clip_error(last_error)},
    )


def mark_retry(
    conn: Any, task_id: int, worker_id: str, delay_sec: float, error_class: str, last_error: str | None
) -> Row | None:
    """Вернуть в очередь с backoff; попытка засчитана (attempts не меняется)."""
    return _finish(
        conn,
        task_id,
        worker_id,
        "status = 'queued', locked_by = NULL, locked_at = NULL, heartbeat_at = NULL,"
        " run_after = now() + make_interval(secs => CAST(%(delay)s AS double precision)),"
        " error_class = %(ec)s, last_error = %(err)s",
        {"delay": float(delay_sec), "ec": error_class, "err": clip_error(last_error)},
    )


def mark_deferred(conn: Any, task_id: int, worker_id: str, reason: str, delay_sec: float) -> Row | None:
    """`ResourceDeferred`: в очередь, run_after сдвинут, попытка НЕ тратится (attempts − 1)."""
    return _finish(
        conn,
        task_id,
        worker_id,
        "status = 'queued', locked_by = NULL, locked_at = NULL, heartbeat_at = NULL,"
        " attempts = GREATEST(attempts - 1, 0), defer_reason = %(reason)s,"
        " run_after = now() + make_interval(secs => CAST(%(delay)s AS double precision))",
        {"reason": reason, "delay": float(delay_sec)},
    )


def mark_requeued(conn: Any, task_id: int, worker_id: str, error_class: str, note: str | None) -> Row | None:
    """Остановка воркера: вернуть задачу в очередь сразу и без траты попытки."""
    return _finish(
        conn,
        task_id,
        worker_id,
        "status = 'queued', locked_by = NULL, locked_at = NULL, heartbeat_at = NULL,"
        " attempts = GREATEST(attempts - 1, 0), run_after = now(),"
        " error_class = %(ec)s, last_error = %(err)s",
        {"ec": error_class, "err": clip_error(note)},
    )


def mark_cancelled(conn: Any, task_id: int, worker_id: str, note: str | None = None) -> Row | None:
    return _finish(
        conn,
        task_id,
        worker_id,
        "status = 'cancelled', finished_at = now(), locked_by = NULL,"
        " error_class = 'cancelled', last_error = COALESCE(%(err)s, last_error)",
        {"err": clip_error(note)},
    )


# ---------------------------------------------------------------- управление из API


def get(conn: Any, task_id: int, *, for_update: bool = False) -> Row | None:
    lock = " FOR UPDATE" if for_update else ""
    return conn.execute(
        f"SELECT {TASK_COLUMNS} FROM tasks WHERE id = %(id)s{lock}", {"id": task_id}
    ).fetchone()


def cancel_queued(conn: Any, task_id: int) -> Row | None:
    return conn.execute(
        f"""
        UPDATE tasks SET status = 'cancelled', cancel_requested = true, finished_at = now(),
               error_class = 'cancelled', updated_at = now()
        WHERE id = %(id)s AND status = 'queued'
        RETURNING {TASK_COLUMNS}
        """,
        {"id": task_id},
    ).fetchone()


def request_cancel_running(conn: Any, task_id: int) -> Row | None:
    return conn.execute(
        f"""
        UPDATE tasks SET cancel_requested = true, updated_at = now()
        WHERE id = %(id)s AND status = 'running'
        RETURNING {TASK_COLUMNS}
        """,
        {"id": task_id},
    ).fetchone()


def requeue_terminal(conn: Any, task_id: int) -> Row | None:
    """Ручной повтор failed/cancelled: новая серия попыток (attempts = 0), сразу в очередь."""
    return conn.execute(
        f"""
        UPDATE tasks SET status = 'queued', attempts = 0, run_after = now(), cancel_requested = false,
               locked_by = NULL, locked_at = NULL, heartbeat_at = NULL, started_at = NULL,
               finished_at = NULL, progress = NULL, error_class = NULL, last_error = NULL,
               defer_reason = NULL, result = NULL, updated_at = now()
        WHERE id = %(id)s AND status IN ('failed','cancelled')
        RETURNING {TASK_COLUMNS}
        """,
        {"id": task_id},
    ).fetchone()


def is_cancel_requested(conn: Any, task_id: int) -> bool:
    row = conn.execute("SELECT cancel_requested FROM tasks WHERE id = %(id)s", {"id": task_id}).fetchone()
    return bool(row and row["cancel_requested"])


# ---------------------------------------------------------------- reaper (§6.1)


def reap_stale(conn: Any, stale_after_sec: float, *, limit: int = 100) -> list[Row]:
    """Забрать `running` с протухшим heartbeat. Возвращает строки ДО изменения (FOR UPDATE SKIP
    LOCKED) — решение (в очередь или failed) принимает сервис."""
    return conn.execute(
        f"""
        SELECT {TASK_COLUMNS} FROM tasks
        WHERE status = 'running'
          AND COALESCE(heartbeat_at, locked_at, started_at, updated_at)
              < now() - make_interval(secs => CAST(%(stale)s AS double precision))
        ORDER BY id
        LIMIT %(limit)s
        FOR UPDATE SKIP LOCKED
        """,
        {"stale": float(stale_after_sec), "limit": int(limit)},
    ).fetchall()


def reaper_requeue(conn: Any, task_id: int, delay_sec: float, note: str) -> Row | None:
    return conn.execute(
        f"""
        UPDATE tasks SET status = 'queued', locked_by = NULL, locked_at = NULL, heartbeat_at = NULL,
               run_after = now() + make_interval(secs => CAST(%(delay)s AS double precision)),
               error_class = 'worker_lost', last_error = %(err)s, updated_at = now()
        WHERE id = %(id)s AND status = 'running'
        RETURNING {TASK_COLUMNS}
        """,
        {"id": task_id, "delay": float(delay_sec), "err": clip_error(note)},
    ).fetchone()


def reaper_fail(conn: Any, task_id: int, note: str) -> Row | None:
    return conn.execute(
        f"""
        UPDATE tasks SET status = 'failed', locked_by = NULL, finished_at = now(),
               error_class = 'worker_lost', last_error = %(err)s, updated_at = now()
        WHERE id = %(id)s AND status = 'running'
        RETURNING {TASK_COLUMNS}
        """,
        {"id": task_id, "err": clip_error(note)},
    ).fetchone()


# ---------------------------------------------------------------- допуск: пометка ожидающих


def mark_waiting_deferred(conn: Any, resource_class: str, reason: str) -> int:
    """Допуск не пустил воркер: пометить ждущие задачи класса причиной (для UI и счётчика deferred)."""
    cur = conn.execute(
        """
        UPDATE tasks SET defer_reason = %(reason)s, updated_at = now()
        WHERE status = 'queued' AND resource_class = %(cls)s AND run_after <= now()
          AND defer_reason IS DISTINCT FROM %(reason)s
        """,
        {"cls": resource_class, "reason": reason},
    )
    return cur.rowcount


def clear_waiting_deferred(conn: Any, resource_class: str, reasons: Sequence[str]) -> int:
    cur = conn.execute(
        """
        UPDATE tasks SET defer_reason = NULL, updated_at = now()
        WHERE status = 'queued' AND resource_class = %(cls)s AND defer_reason = ANY(%(reasons)s)
        """,
        {"cls": resource_class, "reasons": list(reasons)},
    )
    return cur.rowcount


# ---------------------------------------------------------------- чтение для API и статуса


def list_tasks(
    conn: Any,
    *,
    statuses: Sequence[str] | None = None,
    kinds: Sequence[str] | None = None,
    resource_class: str | None = None,
    job_id: int | None = None,
    source_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Row], int]:
    where = ["true"]
    params: dict[str, Any] = {"limit": int(limit), "offset": int(offset)}
    if statuses:
        where.append("status = ANY(%(statuses)s)")
        params["statuses"] = list(statuses)
    if kinds:
        where.append("kind = ANY(%(kinds)s)")
        params["kinds"] = list(kinds)
    if resource_class:
        where.append("resource_class = %(cls)s")
        params["cls"] = resource_class
    if job_id is not None:
        where.append("job_id = %(job_id)s")
        params["job_id"] = job_id
    if source_id is not None:
        where.append("source_id = %(source_id)s")
        params["source_id"] = source_id
    cond = " AND ".join(where)
    total = conn.execute(f"SELECT count(*) AS n FROM tasks WHERE {cond}", params).fetchone()["n"]
    rows = conn.execute(
        f"""
        SELECT {TASK_COLUMNS} FROM tasks WHERE {cond}
        ORDER BY (status = 'running') DESC, (status = 'queued') DESC, id DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    ).fetchall()
    return rows, int(total)


def queue_counters(conn: Any) -> dict[str, dict[str, int]]:
    """`{heavy: {queued, running, deferred}, light: {...}}` для /system/status.

    `queued` — ждущие без причины отсрочки, `deferred` — ждущие с `defer_reason` (не пересекаются).
    """
    rows = conn.execute(
        """
        SELECT resource_class,
               count(*) FILTER (WHERE status = 'queued' AND defer_reason IS NULL) AS queued,
               count(*) FILTER (WHERE status = 'running') AS running,
               count(*) FILTER (WHERE status = 'queued' AND defer_reason IS NOT NULL) AS deferred
        FROM tasks WHERE status IN ('queued','running')
        GROUP BY resource_class
        """
    ).fetchall()
    out = {cls: {"queued": 0, "running": 0, "deferred": 0} for cls in ("heavy", "light")}
    for r in rows:
        out[r["resource_class"]] = {
            "queued": int(r["queued"]),
            "running": int(r["running"]),
            "deferred": int(r["deferred"]),
        }
    return out


def running_heavy(conn: Any) -> Row | None:
    return conn.execute(
        f"""
        SELECT {TASK_COLUMNS} FROM tasks
        WHERE status = 'running' AND resource_class = 'heavy'
        ORDER BY started_at DESC NULLS LAST, id DESC LIMIT 1
        """
    ).fetchone()


def delete_finished_before(conn: Any, older_than_days: int, *, limit: int = 5000) -> int:
    """Уборка старых завершённых задач без job/source (системные). Для cleanup."""
    cur = conn.execute(
        """
        DELETE FROM tasks WHERE id IN (
          SELECT id FROM tasks
          WHERE status IN ('succeeded','failed','cancelled') AND job_id IS NULL AND source_id IS NULL
            AND finished_at < now() - make_interval(days => CAST(%(days)s AS int))
            AND NOT EXISTS (SELECT 1 FROM generation_events g WHERE g.task_id = tasks.id)
            AND NOT EXISTS (SELECT 1 FROM schedules s WHERE s.last_task_id = tasks.id)
          ORDER BY id LIMIT %(limit)s)
        """,
        {"days": int(older_than_days), "limit": int(limit)},
    )
    return cur.rowcount
