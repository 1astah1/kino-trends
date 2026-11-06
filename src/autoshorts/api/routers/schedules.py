"""Расписания планировщика (docs/ARCHITECTURE.md §5.2, §6.1). Владелец — M1-C.

Чтение — viewer и выше (§5.1: viewer видит всё, кроме секретов, настроек и пользователей),
правка — admin.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request

from autoshorts.api.routers.tasks import app_errors, audit, require_admin, require_viewer
from autoshorts.api.schemas.tasks import Schedule, ScheduleList, SchedulePatch

router = APIRouter(tags=["tasks"])

ScheduleNamePath = Annotated[str, Path(pattern=r"^[a-z0-9_]{2,63}$")]


def schedule_out(row: dict[str, Any]) -> Schedule:
    data = dict(row)
    anchor = data.get("anchor_local_time")
    data["anchor_local_time"] = anchor.strftime("%H:%M") if anchor is not None else None
    data["payload"] = data.get("payload") or {}
    return Schedule.model_validate(data)


@router.get(
    "/schedules",
    name="schedules_list",
    response_model=ScheduleList,
    summary="Расписания",
    dependencies=[Depends(require_viewer)],
)
def list_schedules():
    from autoshorts.db.pool import connection
    from autoshorts.services import tasks as svc

    with app_errors(), connection() as conn:
        rows = svc.list_schedules(conn)
    return ScheduleList(items=[schedule_out(r) for r in rows])


@router.patch(
    "/schedules/{schedule_name}",
    name="schedules_update",
    response_model=Schedule,
    summary="Правка расписания",
    dependencies=[Depends(require_admin)],
)
def update_schedule(schedule_name: ScheduleNamePath, body: SchedulePatch, request: Request):
    from autoshorts.db.pool import connection
    from autoshorts.services import tasks as svc

    with app_errors(), connection() as conn:
        row = svc.update_schedule(conn, schedule_name, enabled=body.enabled, interval_sec=body.interval_sec)
        audit(
            conn,
            request,
            "schedule.update",
            "schedule",
            schedule_name,
            diff=body.model_dump(exclude_none=True),
        )
    return schedule_out(row)
