"""Аналитика (docs/ARCHITECTURE.md §5.2 «Аналитика», §6.6, §6.7, §7.3). Контракт M4-0, реализация M4-B.

Тела — `services/analytics.py` (SQL — `repos/analytics.py`, формулы — `analytics/formulas.py`,
статистика — `analytics/stats.py`). Определения метрик, режимы activity/cohort и уровни атрибуции — в
докстрингах `schemas.analytics` и `services.analytics`. Все эндпоинты — только чтение, viewer+.

CSV (`/analytics/export`) — `schemas.analytics.encode_csv` (UTF-8 с BOM, CRLF; `excel_ru` — `;`,
десятичная запятая, русские заголовки, время МСК; `rfc4180` — `,`, точка, ключи латиницей, UTC) и
заголовок `Content-Disposition: attachment` с латинским именем `csv_filename(…)`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import Response

from autoshorts.api.deps import ViewerDep
from autoshorts.api.schemas.analytics import (
    CSV_MEDIA_TYPE,
    AnalyticsBreakdown,
    AnalyticsCohorts,
    AnalyticsCosts,
    AnalyticsFunnel,
    AnalyticsQueryParams,
    AnalyticsSummary,
    AnalyticsTimeseries,
    BreakdownQueryParams,
    CohortsQueryParams,
    CostsQueryParams,
    DataFreshness,
    ExportQueryParams,
    TimeseriesQueryParams,
    VideoAnalytics,
    VideoAnalyticsQueryParams,
    content_disposition,
)
from autoshorts.api.schemas.common import text_responses

router = APIRouter(tags=["analytics"])


class CsvResponse(Response):
    media_type = CSV_MEDIA_TYPE


def _connection() -> Any:
    from autoshorts.db.pool import connection

    return connection()


def _svc() -> Any:
    from autoshorts.services import analytics

    return analytics


@router.get(
    "/analytics/summary",
    name="summary",
    response_model=AnalyticsSummary,
    summary="Итоги периода, прошлый период, уровни атрибуции, свежесть данных",
)
def analytics_summary(principal: ViewerDep, query: AnalyticsQueryParams):
    with _connection() as conn:
        return _svc().summary(conn, query)


@router.get(
    "/analytics/timeseries",
    name="timeseries",
    response_model=AnalyticsTimeseries,
    summary="Ряд метрики по дням или неделям, с группировкой (хвост — в other)",
)
def analytics_timeseries(principal: ViewerDep, query: TimeseriesQueryParams):
    with _connection() as conn:
        return _svc().timeseries(conn, query)


@router.get(
    "/analytics/breakdown",
    name="breakdown",
    response_model=AnalyticsBreakdown,
    summary="Разрез по ролику, каналу, нише, продукту, кампании, варианту, треку или площадке",
)
def analytics_breakdown(principal: ViewerDep, query: BreakdownQueryParams):
    with _connection() as conn:
        return _svc().breakdown(conn, query)


@router.get(
    "/analytics/funnel",
    name="funnel",
    response_model=AnalyticsFunnel,
    summary="Воронка двух веток: просмотры → переходы → старты → оплаты, цена шага",
)
def analytics_funnel(principal: ViewerDep, query: AnalyticsQueryParams):
    with _connection() as conn:
        return _svc().funnel(conn, query)


@router.get(
    "/analytics/cohorts",
    name="cohorts",
    response_model=AnalyticsCohorts,
    summary="Когорты по дате публикации × возраст ролика (24 ч … 30 дней)",
)
def analytics_cohorts(principal: ViewerDep, query: CohortsQueryParams):
    with _connection() as conn:
        return _svc().cohorts(conn, query)


@router.get(
    "/analytics/costs",
    name="costs",
    response_model=AnalyticsCosts,
    summary="Расход по провайдерам, шагам и исходам; честная цена опубликованного ролика",
)
def analytics_costs(principal: ViewerDep, query: CostsQueryParams):
    with _connection() as conn:
        return _svc().costs(conn, query)


@router.get(
    "/analytics/freshness",
    name="freshness",
    response_model=DataFreshness,
    summary="Свежесть данных: опрос метрик, ролл-ап, коллекторы, квоты",
)
def analytics_freshness(principal: ViewerDep):
    with _connection() as conn:
        return _svc().freshness(conn)


@router.get(
    "/analytics/videos/{publication_id}",
    name="video",
    response_model=VideoAnalytics,
    summary="Карточка ролика: лестница снимков, просмотры от возраста, трафик, конверсии, стоимость",
)
def analytics_video(publication_id: int, principal: ViewerDep, query: VideoAnalyticsQueryParams):
    with _connection() as conn:
        return _svc().video(conn, publication_id, query)


@router.get(
    "/analytics/export",
    name="export",
    response_class=CsvResponse,
    responses=text_responses(CSV_MEDIA_TYPE, "CSV (UTF-8 с BOM), Content-Disposition: attachment"),
    summary="Выгрузка отчёта в CSV: summary, timeseries, breakdown, funnel, cohorts, costs, …",
)
def analytics_export(principal: ViewerDep, query: ExportQueryParams):
    with _connection() as conn:
        body, filename = _svc().export_csv(conn, query)
    return CsvResponse(
        content=body,
        headers={"Content-Disposition": content_disposition(filename), "Cache-Control": "no-store"},
    )
