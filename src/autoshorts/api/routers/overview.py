"""Обзор — «Пульт» (docs/ARCHITECTURE.md §5.2 `/overview`, §7.3). Контракт M4-0, реализация M4-B.

Один запрос на экран: KPI с дельтами к прошлому периоду той же длины, дневные ряды для спарклайнов,
мини-воронка, «Требует тебя», топ-10 роликов, открытые алерты, бюджет дня, свежесть данных и
онбординг. Период по умолчанию — 7 бизнес-дней, режим всегда `activity`. Расчёт —
`services/analytics.overview`, схема ответа — `schemas.overview`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from autoshorts.api.deps import ViewerDep
from autoshorts.api.schemas.overview import Overview, OverviewQueryParams

router = APIRouter(tags=["overview"])


def _connection() -> Any:
    from autoshorts.db.pool import connection

    return connection()


@router.get(
    "/overview",
    name="get",
    response_model=Overview,
    summary="Сводка «Пульта»: KPI с дельтами, ряды, «Требует тебя», топ роликов, алерты, свежесть",
)
def get_overview(principal: ViewerDep, query: OverviewQueryParams):
    from autoshorts.services import analytics

    with _connection() as conn:
        return analytics.overview(conn, query)
