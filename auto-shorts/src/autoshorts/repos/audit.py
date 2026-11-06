"""Таблица `audit_log` (миграция 0002): журнал действий людей, токенов, бота и системы."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

Row = dict[str, Any]

COLUMNS = (
    "id, occurred_at, actor_kind, actor_user_id, action, entity_type, entity_id, summary, diff, request_id"
)


def insert(
    conn: Any,
    *,
    actor_kind: str,
    actor_user_id: int | None,
    action: str,
    entity_type: str,
    entity_id: str | None,
    summary: str | None,
    diff: Any | None,
    request_id: str | None,
    ip_hash: str | None,
) -> int:
    row = conn.execute(
        """
        INSERT INTO audit_log (actor_kind, actor_user_id, action, entity_type, entity_id, summary, diff,
                               request_id, ip_hash)
        VALUES (%(actor_kind)s, %(actor_user_id)s, %(action)s, %(entity_type)s, %(entity_id)s, %(summary)s,
                %(diff)s, %(request_id)s, %(ip_hash)s)
        RETURNING id
        """,
        {
            "actor_kind": actor_kind,
            "actor_user_id": actor_user_id,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "summary": summary,
            "diff": Jsonb(diff) if diff is not None else None,
            "request_id": request_id,
            "ip_hash": ip_hash,
        },
    ).fetchone()
    return int(row["id"])


def list_entries(
    conn: Any,
    *,
    actor_user_id: int | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    action: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    before_id: int | None = None,
    limit: int = 50,
) -> list[Row]:
    """Журнал, новые сверху. `action` с `*` на конце — префикс (`user.*`). Интервал времени [from, to)."""
    action_prefix = None
    if action and action.endswith("*"):
        action_prefix, action = action[:-1], None
    return conn.execute(
        f"""
        SELECT {COLUMNS} FROM audit_log
        WHERE (CAST(%(actor)s AS bigint) IS NULL OR actor_user_id = CAST(%(actor)s AS bigint))
          AND (CAST(%(etype)s AS text) IS NULL OR entity_type = CAST(%(etype)s AS text))
          AND (CAST(%(eid)s AS text) IS NULL OR entity_id = CAST(%(eid)s AS text))
          AND (CAST(%(action)s AS text) IS NULL OR action = CAST(%(action)s AS text))
          AND (CAST(%(prefix)s AS text) IS NULL
               OR left(action, length(CAST(%(prefix)s AS text))) = CAST(%(prefix)s AS text))
          AND (CAST(%(t_from)s AS timestamptz) IS NULL OR occurred_at >= CAST(%(t_from)s AS timestamptz))
          AND (CAST(%(t_to)s AS timestamptz) IS NULL OR occurred_at < CAST(%(t_to)s AS timestamptz))
          AND (CAST(%(before)s AS bigint) IS NULL OR id < CAST(%(before)s AS bigint))
        ORDER BY id DESC
        LIMIT %(limit)s
        """,
        {
            "actor": actor_user_id,
            "etype": entity_type,
            "eid": entity_id,
            "action": action,
            "prefix": action_prefix,
            "t_from": occurred_from,
            "t_to": occurred_to,
            "before": before_id,
            "limit": limit,
        },
    ).fetchall()


def count_login_failures(
    conn: Any,
    user_id: int,
    *,
    window_minutes: int,
    since: datetime | None,
    failure_action: str,
    reset_actions: tuple[str, ...],
) -> int:
    """Сколько неудачных входов пользователя за окно, после последнего успеха (`since`) и сброса.

    Сброс — последняя запись с действием из `reset_actions` (смена/сброс пароля, снятие блокировки).
    """
    row = conn.execute(
        """
        SELECT count(*) AS n FROM audit_log a
        WHERE a.entity_type = 'user' AND a.entity_id = %(uid)s AND a.action = %(fail)s
          AND a.occurred_at > now() - make_interval(mins => CAST(%(window)s AS int))
          AND (CAST(%(since)s AS timestamptz) IS NULL OR a.occurred_at > CAST(%(since)s AS timestamptz))
          AND NOT EXISTS (
            SELECT 1 FROM audit_log r
            WHERE r.entity_type = 'user' AND r.entity_id = %(uid)s AND r.action = ANY(%(resets)s)
              AND r.id > a.id)
        """,
        {
            "uid": str(user_id),
            "fail": failure_action,
            "window": window_minutes,
            "since": since,
            "resets": list(reset_actions),
        },
    ).fetchone()
    return int(row["n"])
