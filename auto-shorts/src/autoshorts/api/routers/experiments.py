"""A/B баннеров между роликами (docs/ARCHITECTURE.md §6.7). Контракт M4-0, реализация M4-B.

Один ролик с двумя баннерами не публикуется — A/B идёт между разными роликами кампании: Манн–Уитни по
`ln(1 + views_72h)` и CTR роликов (посетители за первые 72 ч на 1000 просмотров за 72 ч), pooled CTR
варианта с интервалом Пуассон–Гамма, вердикт — только после 72 ч и при ≥ 20 зрелых роликах в каждом
варианте (`analytics/stats.py`). CSV эксперимента — `GET /analytics/export?report=experiment&campaign_id=…`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from autoshorts.api.deps import ViewerDep
from autoshorts.api.schemas.experiments import Experiment, ExperimentQueryParams

router = APIRouter(tags=["experiments"])


def _connection() -> Any:
    from autoshorts.db.pool import connection

    return connection()


@router.get(
    "/campaigns/{campaign_id}/experiment",
    name="get",
    response_model=Experiment,
    summary="A/B-статистика кампании: варианты, зрелость, Манн–Уитни, вердикт после 72 ч и 20 роликов",
)
def get_experiment(campaign_id: int, principal: ViewerDep, query: ExperimentQueryParams):
    from autoshorts.services import analytics

    with _connection() as conn:
        return analytics.experiment(conn, campaign_id, query.platform)
