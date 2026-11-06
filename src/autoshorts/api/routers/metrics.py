"""Ручной ввод метрик и очередь ручного ввода (docs/ARCHITECTURE.md §6.5). Контракт M4-0, реализация M4-A.

Ручной ввод — последняя ступень цепочки коллекторов (VK без yt-dlp, YouTube без ключа, долгие ошибки):
снимок `source='manual'` с автором и заметкой, сразу пересчитывает `publications.latest_*` и
`views_24h…30d` (`services.metrics`). Удалить можно только ручной снимок (иначе 409). Ряд снимков
публикации — `GET /publications/{publication_id}/metrics` (M3-A), внеочередной опрос —
`POST /publications/{publication_id}/refresh-metrics` (M3-A).
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from autoshorts.api.deps import OperatorDep, ViewerDep, audit_actor
from autoshorts.api.schemas.common import LimitQuery, OffsetQuery, Slug
from autoshorts.api.schemas.metrics import ManualDuePage, ManualMetricsCreate, ManualMetricsResult
from autoshorts.domain.enums import Platform

router = APIRouter(tags=["metrics"])


def _connection():
    from autoshorts.db.pool import connection

    return connection()


def _svc():
    from autoshorts.services import metrics

    return metrics


@router.get(
    "/metrics/manual-due",
    name="manual_due",
    response_model=ManualDuePage,
    summary="Публикации, которые ждут ручного ввода метрик (VK без yt-dlp, YouTube без ключа)",
)
def manual_due(
    principal: ViewerDep,
    platform: list[Platform] | None = Query(default=None),
    channel_id: list[Slug] | None = Query(default=None),
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
):
    with _connection() as conn:
        return _svc().manual_due(
            conn,
            platforms=[p.value for p in platform] if platform else None,
            channel_ids=list(channel_id) if channel_id else None,
            limit=limit,
            offset=offset,
        )


@router.post(
    "/publications/{publication_id}/metrics/manual",
    name="manual_create",
    status_code=201,
    response_model=ManualMetricsResult,
    summary="Ручной снимок счётчиков публикации (source=manual)",
)
def create_manual_metrics(
    publication_id: int, body: ManualMetricsCreate, principal: OperatorDep, request: Request
):
    with _connection() as conn:
        return _svc().create_manual(conn, publication_id, body, audit_actor(request, principal))


@router.delete(
    "/publications/{publication_id}/metrics/{snapshot_id}",
    name="snapshot_delete",
    status_code=204,
    response_class=Response,
    summary="Удалить ошибочный ручной снимок (снимки коллекторов не удаляются: 409)",
)
def delete_manual_snapshot(publication_id: int, snapshot_id: int, principal: OperatorDep, request: Request):
    with _connection() as conn:
        _svc().delete_manual(conn, publication_id, snapshot_id, audit_actor(request, principal))
    return Response(status_code=204)
