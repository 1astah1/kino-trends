"""Квоты внешних API и состояние коллекторов (docs/ARCHITECTURE.md §6.5). Контракт M4-0, реализация M4-A.

`GET /quota` — расход `api_quota_usage` по суткам квоты (YouTube — America/Los_Angeles), текущие сутки
с процентом от лимита (нули, если вызовов не было), момент сброса и состояние коллекторов
(`services.metrics.quota_usage`). Контракт — `schemas.quota`.
"""

from __future__ import annotations

from fastapi import APIRouter

from autoshorts.api.deps import ViewerDep
from autoshorts.api.schemas.quota import QuotaQueryParams, QuotaUsage

router = APIRouter(tags=["quota"])


@router.get(
    "/quota",
    name="get",
    response_model=QuotaUsage,
    summary="Расход квот API по дням и кустам, текущие сутки, состояние коллекторов",
)
def get_quota(principal: ViewerDep, query: QuotaQueryParams):
    from autoshorts.db.pool import connection
    from autoshorts.services import metrics as metrics_service

    with connection() as conn:
        return metrics_service.quota_usage(
            conn,
            date_from=query.date_from,
            date_to=query.date_to,
            apis=[a.value for a in query.api],
            cluster_ids=list(query.cluster_id),
        )
