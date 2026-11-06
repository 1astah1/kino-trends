"""Алерты: список viewer+, подтверждение operator+. Владелец — M1-B.

`?status=open&status=acked` — повтором; без параметра — все статусы (новые сверху, до 200).
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response

from autoshorts.api.deps import OperatorDep, ViewerDep, audit_actor
from autoshorts.api.schemas.system import AlertList
from autoshorts.domain.enums import AlertStatus
from autoshorts.services import alerts as alerts_service

router = APIRouter(tags=["system"])


@router.get("/alerts", name="alerts_list", response_model=AlertList, summary="Алерты")
def list_alerts(principal: ViewerDep, status: list[AlertStatus] | None = Query(default=None)):
    return {"items": alerts_service.list_alerts(status)}


@router.post(
    "/alerts/{alert_id}/ack",
    name="alerts_ack",
    status_code=204,
    response_class=Response,
    summary="Подтвердить алерт",
)
def ack_alert(alert_id: int, principal: OperatorDep, request: Request):
    alerts_service.ack_alert(alert_id, audit_actor(request, principal))
    return Response(status_code=204)
