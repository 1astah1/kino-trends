"""Журнал аудита (operator+). Владелец — M1-B.

`action` точным совпадением или префиксом со звёздочкой: `?action=user.*`.
"""

from __future__ import annotations

from fastapi import APIRouter

from autoshorts.api.deps import OperatorDep
from autoshorts.api.pagination import journal
from autoshorts.api.schemas.common import BeforeIdQuery, DateFromQuery, DateToQuery, LimitQuery
from autoshorts.api.schemas.system import AuditJournal
from autoshorts.services import audit as audit_service

router = APIRouter(tags=["system"])


@router.get("/audit-log", name="audit_log", response_model=AuditJournal, summary="Журнал аудита")
def audit_log(
    principal: OperatorDep,
    actor_user_id: int | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    action: str | None = None,
    date_from: DateFromQuery = None,
    date_to: DateToQuery = None,
    before_id: BeforeIdQuery = None,
    limit: LimitQuery = 50,
):
    rows = audit_service.list_entries(
        actor_user_id=actor_user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        date_from=date_from,
        date_to=date_to,
        before_id=before_id,
        limit=limit,
    )
    return journal(rows, limit=limit)
