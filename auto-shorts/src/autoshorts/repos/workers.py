"""SQL реестра воркеров `workers` (docs/ARCHITECTURE.md §4.2 0004, §6.1).

`workers.state`: `idle | busy | deferred:<reason> | standby | stopping`. `standby` — тяжёлый
воркер, который ждёт освобождения тяжёлого слота (его держит другой процесс).
"""

from __future__ import annotations

from typing import Any

Row = dict[str, Any]

WORKER_COLUMNS = (
    "id, resource_class, hostname, pid, version, started_at, heartbeat_at, current_task_id, state"
)


def upsert(
    conn: Any,
    *,
    worker_id: str,
    resource_class: str,
    hostname: str,
    pid: int,
    version: str | None,
    state: str = "idle",
) -> Row:
    return conn.execute(
        f"""
        INSERT INTO workers (id, resource_class, hostname, pid, version, state, started_at, heartbeat_at)
        VALUES (%(id)s, %(cls)s, %(host)s, %(pid)s, %(version)s, %(state)s, now(), now())
        ON CONFLICT (id) DO UPDATE SET resource_class = EXCLUDED.resource_class,
            hostname = EXCLUDED.hostname, pid = EXCLUDED.pid, version = EXCLUDED.version,
            state = EXCLUDED.state, started_at = now(), heartbeat_at = now(), current_task_id = NULL
        RETURNING {WORKER_COLUMNS}
        """,
        {
            "id": worker_id,
            "cls": resource_class,
            "host": hostname,
            "pid": pid,
            "version": version,
            "state": state,
        },
    ).fetchone()


def heartbeat(conn: Any, worker_id: str, *, state: str, current_task_id: int | None) -> Row | None:
    """Обновить heartbeat и состояние. Возвращает строку ДО изменения состояния в поле `prev_state`."""
    return conn.execute(
        f"""
        WITH prev AS (SELECT state, current_task_id FROM workers WHERE id = %(id)s FOR UPDATE)
        UPDATE workers w SET heartbeat_at = now(), state = %(state)s, current_task_id = %(task)s
        FROM prev WHERE w.id = %(id)s
        RETURNING {", ".join("w." + c.strip() for c in WORKER_COLUMNS.split(","))},
                  prev.state AS prev_state, prev.current_task_id AS prev_task_id
        """,
        {"id": worker_id, "state": state, "task": current_task_id},
    ).fetchone()


def delete(conn: Any, worker_id: str) -> None:
    conn.execute("DELETE FROM workers WHERE id = %(id)s", {"id": worker_id})


def get(conn: Any, worker_id: str) -> Row | None:
    return conn.execute(
        f"SELECT {WORKER_COLUMNS} FROM workers WHERE id = %(id)s", {"id": worker_id}
    ).fetchone()


def list_all(conn: Any) -> list[Row]:
    return conn.execute(
        f"SELECT {WORKER_COLUMNS} FROM workers ORDER BY resource_class, started_at, id"
    ).fetchall()


def prune_stale(conn: Any, older_than_sec: float) -> int:
    """Удалить записи воркеров без heartbeat дольше `older_than_sec` (процесс умер без выхода)."""
    cur = conn.execute(
        """
        DELETE FROM workers
        WHERE heartbeat_at < now() - make_interval(secs => CAST(%(sec)s AS double precision))
        """,
        {"sec": float(older_than_sec)},
    )
    return cur.rowcount


def heavy_slot_owner(conn: Any, alive_within_sec: float) -> Row | None:
    """Живой тяжёлый воркер в состоянии busy (для /system/status → heavy_slot.owner)."""
    return conn.execute(
        f"""
        SELECT {WORKER_COLUMNS} FROM workers
        WHERE resource_class = 'heavy' AND state = 'busy'
          AND heartbeat_at >= now() - make_interval(secs => CAST(%(sec)s AS double precision))
        ORDER BY heartbeat_at DESC LIMIT 1
        """,
        {"sec": float(alive_within_sec)},
    ).fetchone()
