"""Аудит действий (docs/ARCHITECTURE.md §4.2 `audit_log`, §5.2 `GET /audit-log`).

Запись делается **в той же транзакции**, что и само действие: `record(conn, actor, ...)`.
`diff` проходит через `core.logging.redact` — значения под ключами `*key*`, `*token*`,
`*secret*`, `password` вырезаются до записи; секреты в аудит не попадают никогда.

Имена действий — латиница `<сущность>.<глагол>`: `auth.login`, `auth.login_failed`, `user.create`,
`user.password_reset`, `secret.set`, `settings.update`, `budget.pause`, …
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from autoshorts.core.logging import redact
from autoshorts.domain.enums import ActorKind
from autoshorts.repos import audit as audit_repo


@dataclass(frozen=True)
class Actor:
    """Кто действует: человек по сессии, машинный токен, бот, система, вебхук."""

    kind: ActorKind
    user_id: int | None = None
    request_id: str | None = None
    ip_hash: str | None = None


SYSTEM = Actor(kind=ActorKind.SYSTEM)


def record(
    conn: Any,
    actor: Actor,
    action: str,
    entity_type: str,
    entity_id: str | int | None = None,
    *,
    summary: str | None = None,
    diff: dict[str, Any] | None = None,
    safe_keys: frozenset[str] = frozenset(),
) -> int:
    """Записать действие. `safe_keys` — ключи верхнего уровня `diff`, которые не надо маскировать
    (например, `secret_name` — имя секрета, а не значение)."""
    clean: dict[str, Any] | None = None
    if diff is not None:
        clean = {k: (v if k in safe_keys else redact({k: v})[k]) for k, v in diff.items()}
    return audit_repo.insert(
        conn,
        actor_kind=str(actor.kind),
        actor_user_id=actor.user_id,
        action=action,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        summary=summary,
        diff=clean,
        request_id=actor.request_id,
        ip_hash=actor.ip_hash,
    )


def list_entries(
    *,
    actor_user_id: int | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    action: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    before_id: int | None = None,
    limit: int = 50,
    tz: str | None = None,
) -> list[dict[str, Any]]:
    """Журнал для `GET /audit-log`; даты — включительно, в бизнес-поясе."""
    from autoshorts.db.pool import connection
    from autoshorts.services.settings import business_range, get_runtime_settings

    with connection() as conn:
        zone = tz or get_runtime_settings(conn).business_timezone
        t_from, t_to = business_range(date_from, date_to, zone)
        return audit_repo.list_entries(
            conn,
            actor_user_id=actor_user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            occurred_from=t_from,
            occurred_to=t_to,
            before_id=before_id,
            limit=limit,
        )
