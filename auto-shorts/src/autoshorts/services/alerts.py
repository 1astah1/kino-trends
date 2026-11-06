"""Алерты (docs/ARCHITECTURE.md §8.5): открытие с дедупликацией, подтверждение, закрытие.

Для `alerts_eval` и других источников (бюджет, воркеры) — функции в чужой транзакции:

    from autoshorts.services.alerts import open_alert, resolve_alert
    open_alert(conn, code="disk_low", severity="critical", dedup_key="disk_low",
               title="Мало места на диске", details={"free_gb": 7.4})
    resolve_alert(conn, "disk_low")

При открытии нового алерта и при подтверждении в `as_events` уходит событие `alert`
(доставляется на COMMIT). Отправка в Telegram — дело `alerts_eval` (колонка `notified_at`).
"""

from __future__ import annotations

from typing import Any

from autoshorts.core.errors import NotFound
from autoshorts.db.notify import publish_event
from autoshorts.domain.enums import AlertSeverity, AlertStatus
from autoshorts.domain.events import EventType
from autoshorts.repos import alerts as alerts_repo
from autoshorts.services import audit
from autoshorts.services.audit import Actor


def _event(conn: Any, row: dict[str, Any]) -> None:
    publish_event(
        conn, EventType.ALERT, {"alert_id": row["id"], "code": row["code"], "severity": row["severity"]}
    )


def open_alert(
    conn: Any,
    *,
    code: str,
    severity: AlertSeverity | str,
    dedup_key: str,
    title: str,
    details: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Открыть (или продлить `last_seen_at` незакрытого с тем же ключом). (строка, новый ли)."""
    row, created = alerts_repo.upsert_open(
        conn,
        code=code,
        severity=str(AlertSeverity(severity)),
        dedup_key=dedup_key,
        title=title,
        details=details or {},
    )
    if created:
        _event(conn, row)
    return row, created


def resolve_alert(conn: Any, dedup_key: str) -> dict[str, Any] | None:
    """Закрыть незакрытый алерт с этим ключом; None — нечего закрывать."""
    return alerts_repo.resolve(conn, dedup_key)


# ---------------------------------------------------------------- API


def list_alerts(statuses: list[AlertStatus] | None) -> list[dict[str, Any]]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        return alerts_repo.list_alerts(conn, statuses=[str(s) for s in statuses] if statuses else None)


def ack_alert(alert_id: int, actor: Actor) -> None:
    """open → acked. Повторное подтверждение и закрытый алерт — без ошибки (идемпотентно)."""
    from autoshorts.db.pool import connection

    with connection() as conn:
        row = alerts_repo.get(conn, alert_id, for_update=True)
        if row is None:
            raise NotFound("alert", "Алерт не найден")
        changed = alerts_repo.ack(conn, alert_id, user_id=actor.user_id)
        if changed is not None:
            audit.record(
                conn, actor, "alert.ack", "alert", alert_id, summary=f"подтверждён алерт {row['code']}"
            )
            _event(conn, changed)
