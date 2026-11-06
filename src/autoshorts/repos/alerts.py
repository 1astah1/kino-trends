"""Таблица `alerts` (миграция 0002): открытые/подтверждённые/закрытые алерты с дедупликацией."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

Row = dict[str, Any]

COLUMNS = (
    "id, code, severity, status, dedup_key, title, details, opened_at, last_seen_at, resolved_at,"
    " acked_by, notified_at"
)


def list_alerts(conn: Any, *, statuses: list[str] | None, limit: int = 200) -> list[Row]:
    return conn.execute(
        f"""
        SELECT {COLUMNS} FROM alerts
        WHERE CAST(%(statuses)s AS text[]) IS NULL OR status = ANY(CAST(%(statuses)s AS text[]))
        ORDER BY opened_at DESC, id DESC
        LIMIT %(limit)s
        """,
        {"statuses": statuses, "limit": limit},
    ).fetchall()


def get(conn: Any, alert_id: int, *, for_update: bool = False) -> Row | None:
    return conn.execute(
        f"SELECT {COLUMNS} FROM alerts WHERE id = %(id)s" + (" FOR UPDATE" if for_update else ""),
        {"id": alert_id},
    ).fetchone()


def ack(conn: Any, alert_id: int, *, user_id: int | None) -> Row | None:
    """open → acked. Возвращает строку, если статус сменился."""
    return conn.execute(
        f"""
        UPDATE alerts SET status = 'acked', acked_by = %(by)s
        WHERE id = %(id)s AND status = 'open'
        RETURNING {COLUMNS}
        """,
        {"id": alert_id, "by": user_id},
    ).fetchone()


def upsert_open(
    conn: Any, *, code: str, severity: str, dedup_key: str, title: str, details: dict[str, Any]
) -> tuple[Row, bool]:
    """Открыть алерт или продлить незакрытый с тем же `dedup_key`. (строка, создан ли новый)."""
    existing = conn.execute(
        f"SELECT {COLUMNS} FROM alerts WHERE dedup_key = %(k)s AND status <> 'resolved' FOR UPDATE",
        {"k": dedup_key},
    ).fetchone()
    if existing is not None:
        row = conn.execute(
            f"""
            UPDATE alerts
            SET last_seen_at = now(), severity = %(sev)s, title = %(title)s, details = %(details)s
            WHERE id = %(id)s
            RETURNING {COLUMNS}
            """,
            {"id": existing["id"], "sev": severity, "title": title, "details": Jsonb(details)},
        ).fetchone()
        return row, False
    row = conn.execute(
        f"""
        INSERT INTO alerts (code, severity, dedup_key, title, details)
        VALUES (%(code)s, %(sev)s, %(k)s, %(title)s, %(details)s)
        ON CONFLICT (dedup_key) WHERE status <> 'resolved' DO UPDATE SET last_seen_at = now()
        RETURNING {COLUMNS}, (xmax = 0) AS inserted
        """,
        {"code": code, "sev": severity, "k": dedup_key, "title": title, "details": Jsonb(details)},
    ).fetchone()
    inserted = bool(row.pop("inserted"))
    return row, inserted


def resolve(conn: Any, dedup_key: str) -> Row | None:
    return conn.execute(
        f"""
        UPDATE alerts SET status = 'resolved', resolved_at = now()
        WHERE dedup_key = %(k)s AND status <> 'resolved'
        RETURNING {COLUMNS}
        """,
        {"k": dedup_key},
    ).fetchone()


def count_open(conn: Any) -> int:
    return int(conn.execute("SELECT count(*) AS n FROM alerts WHERE status = 'open'").fetchone()["n"])
