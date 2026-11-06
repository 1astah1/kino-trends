"""SQL решений приёмки `review_decisions` и привязки Telegram (docs/ARCHITECTURE.md §4.2 0004, §6.2).

Только SQL, транзакциями управляет сервис (`services/review.py`).
"""

from __future__ import annotations

from typing import Any

Row = dict[str, Any]

DECISION_COLUMNS = (
    "d.id, d.job_id, d.decision, d.reject_reason, d.rework_from_stage, d.comment, d.undoes_id,"
    " d.decided_by, d.decided_via, d.created_at"
)


def insert(
    conn: Any,
    *,
    job_id: int,
    decision: str,
    decided_via: str,
    decided_by: int | None,
    reject_reason: str | None = None,
    rework_from_stage: str | None = None,
    comment: str | None = None,
    undoes_id: int | None = None,
) -> Row:
    row = conn.execute(
        """
        INSERT INTO review_decisions (job_id, decision, reject_reason, rework_from_stage, comment, undoes_id,
                                      decided_by, decided_via)
        VALUES (%(job_id)s, %(decision)s, %(reason)s, %(stage)s, %(comment)s, %(undoes)s, %(by)s, %(via)s)
        RETURNING id
        """,
        {
            "job_id": job_id,
            "decision": decision,
            "reason": reject_reason,
            "stage": rework_from_stage,
            "comment": comment,
            "undoes": undoes_id,
            "by": decided_by,
            "via": decided_via,
        },
    ).fetchone()
    out = get(conn, int(row["id"]))
    assert out is not None
    return out


def get(conn: Any, decision_id: int) -> Row | None:
    return conn.execute(
        f"""
        SELECT {DECISION_COLUMNS}, u.display_name AS decided_by_name
        FROM review_decisions d LEFT JOIN users u ON u.id = d.decided_by
        WHERE d.id = %(id)s
        """,
        {"id": decision_id},
    ).fetchone()


def list_for_job(conn: Any, job_id: int) -> list[Row]:
    return conn.execute(
        f"""
        SELECT {DECISION_COLUMNS}, u.display_name AS decided_by_name
        FROM review_decisions d LEFT JOIN users u ON u.id = d.decided_by
        WHERE d.job_id = %(id)s
        ORDER BY d.created_at, d.id
        """,
        {"id": job_id},
    ).fetchall()


def list_for_jobs(conn: Any, job_ids: list[int]) -> list[Row]:
    if not job_ids:
        return []
    return conn.execute(
        f"""
        SELECT {DECISION_COLUMNS}, u.display_name AS decided_by_name
        FROM review_decisions d LEFT JOIN users u ON u.id = d.decided_by
        WHERE d.job_id = ANY(%(ids)s)
        ORDER BY d.created_at, d.id
        """,
        {"ids": job_ids},
    ).fetchall()


def latest_effective(conn: Any, job_id: int) -> Row | None:
    """Последнее решение, которое ещё действует: не `undo` и не отменённое отменой."""
    return conn.execute(
        f"""
        SELECT {DECISION_COLUMNS}, u.display_name AS decided_by_name
        FROM review_decisions d LEFT JOIN users u ON u.id = d.decided_by
        WHERE d.job_id = %(id)s AND d.decision <> 'undo'
          AND NOT EXISTS (SELECT 1 FROM review_decisions x WHERE x.undoes_id = d.id)
        ORDER BY d.created_at DESC, d.id DESC
        LIMIT 1
        """,
        {"id": job_id},
    ).fetchone()


def latest(conn: Any, job_id: int) -> Row | None:
    """Самая последняя запись приёмки (включая `undo`)."""
    return conn.execute(
        f"""
        SELECT {DECISION_COLUMNS}, u.display_name AS decided_by_name
        FROM review_decisions d LEFT JOIN users u ON u.id = d.decided_by
        WHERE d.job_id = %(id)s
        ORDER BY d.created_at DESC, d.id DESC
        LIMIT 1
        """,
        {"id": job_id},
    ).fetchone()


def is_within_minutes(conn: Any, decision_id: int, minutes: int) -> bool:
    row = conn.execute(
        """
        SELECT created_at > now() - make_interval(mins => CAST(%(m)s AS int)) AS fresh
        FROM review_decisions WHERE id = %(id)s
        """,
        {"id": decision_id, "m": int(minutes)},
    ).fetchone()
    return bool(row and row["fresh"])


def ancestor_job_ids(conn: Any, job_id: int, *, max_depth: int = 10) -> list[int]:
    """Цепочка родителей ролика (клоны, адаптации): ближайший первым."""
    rows = conn.execute(
        """
        WITH RECURSIVE chain(id, parent_job_id, depth) AS (
          SELECT id, parent_job_id, 0 FROM jobs WHERE id = %(id)s
          UNION ALL
          SELECT j.id, j.parent_job_id, chain.depth + 1
          FROM jobs j JOIN chain ON j.id = chain.parent_job_id
          WHERE chain.depth < CAST(%(depth)s AS int)
        )
        SELECT id FROM chain WHERE depth > 0 ORDER BY depth
        """,
        {"id": job_id, "depth": int(max_depth)},
    ).fetchall()
    return [int(r["id"]) for r in rows]


# ---------------------------------------------------------------- кто может решать из Telegram


def reviewer_by_tg(conn: Any, tg_user_id: int) -> Row | None:
    """Активный пользователь с привязанным `users.tg_user_id` (кнопки приёмки в боте, §2.2)."""
    return conn.execute(
        """
        SELECT id, login, display_name, role, tg_user_id, is_active
        FROM users WHERE tg_user_id = %(tg)s AND is_active
        """,
        {"tg": int(tg_user_id)},
    ).fetchone()
