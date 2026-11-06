"""Воркеры (docs/ARCHITECTURE.md §5.2, §6.1). Владелец — M1-C. Чтение — viewer и выше."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from autoshorts.api.routers.tasks import app_errors, require_viewer
from autoshorts.api.schemas.tasks import Worker, WorkerList

router = APIRouter(tags=["tasks"])


@router.get(
    "/workers",
    name="workers_list",
    response_model=WorkerList,
    summary="Воркеры и их состояние",
    dependencies=[Depends(require_viewer)],
)
def list_workers():
    from autoshorts.db.pool import connection
    from autoshorts.services import tasks as svc

    with app_errors(), connection() as conn:
        rows = svc.list_workers(conn)
    return WorkerList(items=[Worker.model_validate(r) for r in rows])
