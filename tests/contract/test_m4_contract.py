"""Контракт M4 (поток M4-0): коллекторы метрик, квоты, аналитика, Обзор, A/B, CSV.

Сверяется: таблица эндпоинтов M4 и их теги; query-модели (мультифильтры повтором, 422 на кривой период
и кириллицу в слагах) на мини-приложении и на настоящем с подменённым входом; словари метрик
(`AnalyticsMetric` = поля `AnalyticsTotals`, подписи CSV, KPI Обзора); эталонный CSV (BOM, диалекты,
экранирование формул); правило готовности A/B; аддитивность правок `Publication`/`Channel`.
"""

from __future__ import annotations

import re
import typing
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from autoshorts.api.schemas import analytics as A
from autoshorts.api.schemas import budget as BU
from autoshorts.api.schemas import channels as CH
from autoshorts.api.schemas import experiments as X
from autoshorts.api.schemas import metrics as M
from autoshorts.api.schemas import overview as O
from autoshorts.api.schemas import publications as PUB
from autoshorts.api.schemas import quota as Q
from autoshorts.api.schemas.common import ERROR_CODES, SECRET_NAME_PATTERN
from autoshorts.domain.enums import MetricsSource, Platform

MIGRATIONS = Path(__file__).resolve().parents[2] / "src" / "autoshorts" / "db" / "migrations"

# ---------------------------------------------------------------- эндпоинты M4

M4_ENDPOINTS = {
    ("GET", "/api/v1/overview"): "overview_get",
    ("GET", "/api/v1/analytics/summary"): "analytics_summary",
    ("GET", "/api/v1/analytics/timeseries"): "analytics_timeseries",
    ("GET", "/api/v1/analytics/breakdown"): "analytics_breakdown",
    ("GET", "/api/v1/analytics/funnel"): "analytics_funnel",
    ("GET", "/api/v1/analytics/cohorts"): "analytics_cohorts",
    ("GET", "/api/v1/analytics/costs"): "analytics_costs",
    ("GET", "/api/v1/analytics/freshness"): "analytics_freshness",
    ("GET", "/api/v1/analytics/videos/{publication_id}"): "analytics_video",
    ("GET", "/api/v1/analytics/export"): "analytics_export",
    ("GET", "/api/v1/campaigns/{campaign_id}/experiment"): "experiments_get",
    ("GET", "/api/v1/quota"): "quota_get",
    ("GET", "/api/v1/metrics/manual-due"): "metrics_manual_due",
    ("POST", "/api/v1/publications/{publication_id}/metrics/manual"): "metrics_manual_create",
    ("DELETE", "/api/v1/publications/{publication_id}/metrics/{snapshot_id}"): "metrics_snapshot_delete",
}

RESPONSE_SCHEMAS = {
    "overview_get": "Overview",
    "analytics_summary": "AnalyticsSummary",
    "analytics_timeseries": "AnalyticsTimeseries",
    "analytics_breakdown": "AnalyticsBreakdown",
    "analytics_funnel": "AnalyticsFunnel",
    "analytics_cohorts": "AnalyticsCohorts",
    "analytics_costs": "AnalyticsCosts",
    "analytics_freshness": "DataFreshness",
    "analytics_video": "VideoAnalytics",
    "experiments_get": "Experiment",
    "quota_get": "QuotaUsage",
    "metrics_manual_due": "ManualDuePage",
}


def _ops(spec: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(m.upper(), path): op for path, item in spec["paths"].items() for m, op in item.items()}


def test_m4_endpoints_present(openapi_spec: dict[str, Any]) -> None:
    ops = _ops(openapi_spec)
    missing = {k: v for k, v in M4_ENDPOINTS.items() if ops.get(k, {}).get("operationId") != v}
    assert not missing, missing


def test_m4_response_schemas(openapi_spec: dict[str, Any]) -> None:
    by_id = {op["operationId"]: op for op in _ops(openapi_spec).values()}
    for op_id, schema in RESPONSE_SCHEMAS.items():
        content = by_id[op_id]["responses"]["200"]["content"]["application/json"]["schema"]
        assert content == {"$ref": f"#/components/schemas/{schema}"}, op_id
    manual = by_id["metrics_manual_create"]
    assert "201" in manual["responses"]
    assert manual["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ManualMetricsCreate"
    }
    assert "204" in by_id["metrics_snapshot_delete"]["responses"]
    export = by_id["analytics_export"]["responses"]
    assert set(export["200"]["content"]) == {A.CSV_MEDIA_TYPE}
    assert export["4XX"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")


def test_filters_are_query_params_of_every_analytics_endpoint(openapi_spec: dict[str, Any]) -> None:
    common = set(A.AnalyticsFilters.model_fields)
    for (method, path), op in _ops(openapi_spec).items():
        if not path.startswith("/api/v1/analytics/") or path.endswith(("/freshness", "/{publication_id}")):
            continue
        names = {p["name"] for p in op.get("parameters", []) if p["in"] == "query"}
        assert common <= names, (method, path, common - names)
    video = _ops(openapi_spec)[("GET", "/api/v1/analytics/videos/{publication_id}")]
    assert {p["name"] for p in video["parameters"]} == {"publication_id", "basis", "traffic_granularity"}


def test_task_dimensions_and_modes() -> None:
    assert [d.value for d in A.AnalyticsDimension] == [
        "video", "channel", "niche", "product", "campaign", "variant", "track", "platform",
    ]  # fmt: skip
    assert {g.value for g in A.AnalyticsGroupBy} == {"none"} | {d.value for d in A.AnalyticsDimension}
    assert {m.value for m in A.AnalyticsFilters.model_fields["mode"].annotation} == {"activity", "cohort"}
    assert A.AnalyticsFilters().basis == "public"  # engaged — только с OAuth (M4-D), не в этом прогоне


# ---------------------------------------------------------------- query-модели


@pytest.fixture(scope="module")
def echo_client() -> TestClient:
    app = FastAPI()

    @app.get("/breakdown")
    def breakdown(query: A.BreakdownQueryParams) -> dict[str, Any]:
        return query.model_dump(mode="json")

    @app.get("/overview")
    def overview(query: O.OverviewQueryParams) -> dict[str, Any]:
        return query.model_dump(mode="json")

    @app.get("/quota")
    def quota(query: Q.QuotaQueryParams) -> dict[str, Any]:
        return query.model_dump(mode="json")

    @app.get("/export")
    def export(query: A.ExportQueryParams) -> dict[str, Any]:
        return query.model_dump(mode="json")

    return TestClient(app)


def test_query_defaults(echo_client: TestClient) -> None:
    got = echo_client.get("/breakdown").json()
    assert got["dimension"] == "channel" and got["mode"] == "activity" and got["basis"] == "public"
    assert got["channel_id"] == [] and got["date_from"] is None and got["sort"] == "-views"
    assert got["cohort_age"] == "lifetime" and got["include_overhead"] is False
    exp = echo_client.get("/export").json()
    assert exp["report"] == "breakdown" and exp["dialect"] == "excel_ru" and exp["dimension"] == "video"


def test_multi_filters_by_repetition(echo_client: TestClient) -> None:
    resp = echo_client.get(
        "/breakdown",
        params=[
            ("channel_id", "taro_main"),
            ("channel_id", "astro-2"),
            ("campaign_id", "7"),
            ("track", "clip"),
            ("platform", "vk"),
            ("mode", "cohort"),
            ("cohort_age", "d7"),
            ("dimension", "variant"),
            ("date_from", "2026-09-01"),
            ("date_to", "2026-09-27"),
        ],
    )
    assert resp.status_code == 200, resp.text
    got = resp.json()
    assert got["channel_id"] == ["taro_main", "astro-2"] and got["campaign_id"] == [7]
    assert got["track"] == ["clip"] and got["platform"] == ["vk"] and got["dimension"] == "variant"
    assert (got["mode"], got["cohort_age"]) == ("cohort", "d7")
    quota = echo_client.get("/quota", params=[("api", "youtube_data"), ("api", "vk_api")]).json()
    assert quota["api"] == ["youtube_data", "vk_api"]


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/breakdown", {"date_from": "2026-09-27", "date_to": "2026-09-01"}),
        ("/breakdown", {"date_from": "2025-01-01", "date_to": "2026-09-01"}),  # > 400 дней
        ("/breakdown", {"channel_id": "канал"}),
        ("/breakdown", {"dimension": "hook_type"}),
        ("/breakdown", {"mode": "lifetime"}),
        ("/breakdown", {"campaign_id": "0"}),
        ("/breakdown", {"sort": "-views; DROP"}),
        ("/breakdown", {"limit": "501"}),
        ("/breakdown", {"date_from": "27.09.2026"}),
        ("/overview", {"date_from": "2026-09-27", "date_to": "2026-09-01"}),
        ("/quota", {"api": "youtube"}),
        ("/quota", {"date_from": "2026-01-01", "date_to": "2026-09-01"}),
        ("/export", {"report": "videos.csv"}),
        ("/export", {"dialect": "excel"}),
    ],
)
def test_bad_query_is_422(echo_client: TestClient, path: str, params: dict[str, str]) -> None:
    assert echo_client.get(path, params=params).status_code == 422


def _dependency(annotated: Any) -> Any:
    return typing.get_args(annotated)[1].dependency


@pytest.fixture(scope="module")
def logged_in_client() -> TestClient:
    """Настоящее приложение с подменённым входом (viewer и operator): проверка валидации до обработчика."""
    from autoshorts.api.app import create_app
    from autoshorts.api.deps import OperatorDep, ViewerDep
    from autoshorts.domain.enums import ActorKind, Role
    from autoshorts.services.auth import Principal

    app = create_app()
    principal = Principal(
        kind=ActorKind.USER, user_id=1, login="oper", display_name="Оператор", role=Role.OPERATOR
    )
    app.dependency_overrides[_dependency(ViewerDep)] = lambda: principal
    app.dependency_overrides[_dependency(OperatorDep)] = lambda: principal
    return TestClient(app)


@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("GET", "/api/v1/analytics/summary?date_from=2026-09-27&date_to=2026-09-01", None),
        ("GET", "/api/v1/analytics/breakdown?dimension=hook_type", None),
        ("GET", "/api/v1/analytics/timeseries?metric=views_per_hour", None),
        ("GET", "/api/v1/analytics/timeseries?top=21", None),
        ("GET", "/api/v1/analytics/cohorts?metric=spend_usd", None),
        ("GET", "/api/v1/analytics/costs?group_by=model", None),
        ("GET", "/api/v1/analytics/export?report=videos", None),
        ("GET", "/api/v1/analytics/videos/abc", None),
        ("GET", "/api/v1/overview?channel_id=%D0%BA%D0%B0%D0%BD%D0%B0%D0%BB", None),
        ("GET", "/api/v1/campaigns/1/experiment?platform=tiktok", None),
        ("GET", "/api/v1/quota?api=google", None),
        ("GET", "/api/v1/metrics/manual-due?limit=0", None),
        ("POST", "/api/v1/publications/1/metrics/manual", {"views": -1}),
        ("POST", "/api/v1/publications/1/metrics/manual", {"views": 10, "impressions": 5}),
        ("POST", "/api/v1/publications/1/metrics/manual", {}),
    ],
)
def test_real_app_validates_before_handler(
    logged_in_client: TestClient, method: str, url: str, body: dict[str, Any] | None
) -> None:
    resp = logged_in_client.request(method, url, json=body)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "validation_error"


# ---------------------------------------------------------------- словари метрик


def test_metric_enum_equals_totals_fields() -> None:
    numeric = set(A.AnalyticsTotals.model_fields) - {"currency"}
    assert {m.value for m in A.AnalyticsMetric} == numeric
    assert set(A.METRIC_LABELS) == set(A.AnalyticsMetric)
    assert all(label and not label.isascii() or label == "ROMI" for label, _ in A.METRIC_LABELS.values())


def test_kpis_funnel_cohorts_use_metric_codes() -> None:
    metrics = {m.value for m in A.AnalyticsMetric}
    assert set(O.OverviewKpis.model_fields) <= metrics
    assert {s.value for s in A.FunnelStepCode} <= metrics
    assert {c.value for c in A.CohortMetric} <= metrics
    assert set(A.DIMENSION_LABELS) == set(A.AnalyticsDimension)


def test_age_maps_cover_enums() -> None:
    assert set(A.COHORT_AGE_HOURS) == set(A.CohortAge)
    assert set(A.AGE_POINT_HOURS) == set(A.AgePoint)
    hours = [A.AGE_POINT_HOURS[a] for a in A.AgePoint]
    assert hours == sorted(hours) and hours[0] == 1 and hours[-1] == 720
    assert set(A.CohortValues.model_fields) == {a.value for a in A.CohortAge} - {"lifetime"}
    assert set(A.CohortCounts.model_fields) == set(A.CohortValues.model_fields)


def test_ranges() -> None:
    today = date(2026, 9, 27)
    assert A.resolve_range(None, None, today) == (date(2026, 8, 31), today)
    assert A.resolve_range(None, None, today, default_days=O.OVERVIEW_DEFAULT_DAYS) == (
        date(2026, 9, 21),
        today,
    )
    assert A.resolve_range(date(2026, 9, 1), date(2026, 9, 3), today) == (date(2026, 9, 1), date(2026, 9, 3))
    assert A.resolve_range(date(2026, 9, 20), None, today) == (date(2026, 9, 20), today)
    assert A.resolve_range(None, date(2026, 9, 10), today)[0] == date(2026, 8, 14)
    assert A.previous_range(date(2026, 9, 21), date(2026, 9, 27)) == (date(2026, 9, 14), date(2026, 9, 20))
    assert A.YT_PUBLIC_VIEWS_CHANGE_DATE == date(2026, 8, 24)


# ---------------------------------------------------------------- CSV


def test_csv_excel_ru() -> None:
    header = ["Ролик", "Просмотры", "CTR", "Опубликован", "День", "Пусто", "Флаг"]
    rows = [
        [
            "Таро; расклад",
            12345,
            2.51,
            datetime(2026, 9, 26, 21, 30, tzinfo=UTC),
            date(2026, 9, 27),
            None,
            True,
        ],
        ['=HYPERLINK("x")', 0, 1e-05, datetime(2026, 9, 27, 0, 0, tzinfo=UTC), date(2026, 9, 1), "", False],
        ["-5 минут", -3, Decimal("0.0512000"), None, None, "+7", None],
    ]
    data = A.encode_csv(header, rows)
    assert data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8")[1:]
    lines = text.split("\r\n")
    assert lines[-1] == "" and len(lines) == 5
    assert lines[0] == "Ролик;Просмотры;CTR;Опубликован;День;Пусто;Флаг"
    assert lines[1] == '"Таро; расклад";12345;2,51;2026-09-27 00:30:00;2026-09-27;;1'  # UTC → МСК
    assert lines[2] == '"\'=HYPERLINK(""x"")";0;0,00001;2026-09-27 03:00:00;2026-09-01;;0'
    assert lines[3] == "'-5 минут;-3;0,0512;;;'+7;"


def test_csv_rfc4180() -> None:
    data = A.encode_csv(
        ["video_uid", "views", "ctr_per_1k_views", "published_at"],
        [
            ["a1b2c3d4e5f6", 10, 2.5, datetime(2026, 9, 26, 21, 30, tzinfo=UTC)],
            ["x,y", None, float("nan"), None],
        ],
        A.CsvDialect.RFC4180,
    )
    assert data.decode("utf-8") == (
        "﻿video_uid,views,ctr_per_1k_views,published_at\r\n"
        "a1b2c3d4e5f6,10,2.5,2026-09-26T21:30:00Z\r\n"
        '"x,y",,,\r\n'
    )


def test_csv_row_width_and_names() -> None:
    with pytest.raises(ValueError):
        A.encode_csv(["a", "b"], [[1]])
    assert A.encode_csv(["a"], []) == "﻿a\r\n".encode()
    name = A.csv_filename(A.AnalyticsReport.BREAKDOWN, date(2026, 9, 1), date(2026, 9, 27))
    assert name == "autoshorts_breakdown_2026-09-01_2026-09-27.csv"
    assert A.csv_filename("experiment") == "autoshorts_experiment.csv"
    assert A.content_disposition(name) == f'attachment; filename="{name}"'
    for bad in ("отчёт.csv", 'a"b.csv', "../x.csv"):
        with pytest.raises(ValueError):
            A.content_disposition(bad)
    with pytest.raises(ValueError):
        A.csv_filename("videos.csv")


# ---------------------------------------------------------------- A/B


@pytest.mark.parametrize(
    ("mature", "visitors", "expected"),
    [
        ({}, 0, (False, "no_variants", 0)),
        ({"A": 25}, 100, (False, "single_variant", 0)),
        ({"A": 25, "B": 12}, 100, (False, "not_enough_mature_videos", 8)),
        ({"A": 3, "B": 12}, 100, (False, "not_enough_mature_videos", 25)),
        ({"A": 20, "B": 20}, 49, (False, "not_enough_visitors", 0)),
        ({"A": 20, "B": 21, "C": 30}, 50, (True, None, 0)),
    ],
)
def test_experiment_readiness(mature: dict[str, int], visitors: int, expected: tuple[Any, ...]) -> None:
    ready, reason, needed = X.experiment_readiness(mature, visitors, min_videos_per_variant=20)
    assert (ready, reason.value if reason else None, needed) == expected


def test_experiment_constants_and_ci_shape() -> None:
    assert (X.AB_MIN_VISITORS_TOTAL, X.AB_ALPHA, X.AB_CI_LEVEL) == (50, 0.05, 0.95)
    assert {m.value for m in X.ExperimentTestMetric} == {"ln_views_72h", "ctr_per_1k"}
    base = {
        "id": 21, "code": "A", "name": "Домен", "status": "active", "weight": 50, "videos": 14,
        "videos_mature": 10, "needs_more": 10, "views_72h_median": 6400.0, "views_72h_p25": None,
        "views_72h_p75": None, "visitors": 190, "ctr_per_1k_pooled": 2.34, "bot_starts": 72, "users": 60,
        "first_payments": 2, "revenue_minor": 98000, "spend_usd": 0.72, "cost_per_visitor_usd": 0.0041,
        "cost_per_bot_start_usd": 0.01, "points": [],
    }  # fmt: skip
    assert X.ExperimentVariant.model_validate({**base, "ctr_ci": [1.9, 2.8]}).ctr_ci == [1.9, 2.8]
    assert X.ExperimentVariant.model_validate({**base, "ctr_ci": None}).ctr_ci is None
    with pytest.raises(ValidationError):
        X.ExperimentVariant.model_validate({**base, "ctr_ci": [1.9]})


# ---------------------------------------------------------------- коллекторы и квоты


def test_collector_chains_and_constants() -> None:
    assert set(M.COLLECTOR_CHAINS) == set(Platform)
    for chain in M.COLLECTOR_CHAINS.values():
        assert chain[-1] is MetricsSource.MANUAL and MetricsSource.YT_ANALYTICS not in chain
    assert M.COLLECTOR_CHAINS[Platform.YOUTUBE][0] is MetricsSource.YT_DATA
    assert M.COLLECTOR_CHAINS[Platform.VK] == (MetricsSource.YTDLP, MetricsSource.MANUAL)
    assert re.fullmatch(SECRET_NAME_PATTERN, M.YOUTUBE_API_KEY_SECRET_DEFAULT)
    assert (M.YT_VIDEOS_LIST_BATCH, M.YT_VIDEOS_LIST_COST_UNITS, M.YT_DAILY_QUOTA_UNITS) == (50, 1, 10_000)
    assert M.YT_QUOTA_TIMEZONE == "America/Los_Angeles"
    assert set(M.COLLECTOR_STATE_LABELS) == set(M.CollectorState)
    assert set(M.COLLECTOR_REASON_LABELS) == set(M.CollectorReason)


def test_manual_metrics_body() -> None:
    ok = M.ManualMetricsCreate.model_validate({"views": 1500, "likes": 12, "note": "из приложения VK"})
    assert ok.captured_at is None and ok.comments is None
    for bad in (
        {"views": -1},
        {"views": M.METRICS_VALUE_MAX + 1},
        {"views": 1, "likes": -2},
        {"views": 1, "note": "x" * 501},
        {"views": 1, "engaged_views": 5},
        {"likes": 1},
    ):
        with pytest.raises(ValidationError):
            M.ManualMetricsCreate.model_validate(bad)


def test_error_codes_for_m4() -> None:
    assert "metrics_rejected" in ERROR_CODES[422]
    assert "metrics_snapshot_not_manual" in ERROR_CODES[409]
    assert {r.value for r in M.MetricsRejectReason} == {
        "before_publication",
        "in_future",
        "publication_deleted",
    }


# ---------------------------------------------------------------- аддитивные правки чужих схем


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (PUB.Publication, "freshness"),
        (PUB.PublicationLatest, "source"),
        (PUB.PublicationAgeViews, "d14"),
        (PUB.PublicationAgeViews, "d30"),
        (CH.Channel, "stats"),
    ],
)
def test_additive_fields_are_optional(model: type[Any], field: str) -> None:
    """Реализации M3-A/M2-A, которые ещё не заполняют поле M4, не должны падать на response_model."""
    info = model.model_fields[field]
    assert not info.is_required() and info.default is None


def test_publication_age_views_without_new_fields() -> None:
    got = PUB.PublicationAgeViews.model_validate({"h24": 1500, "h72": 6400, "d7": None})
    assert (got.d14, got.d30) == (None, None)


# ---------------------------------------------------------------- резервы бюджета (0016)


def test_orphan_view_reasons_match_enum() -> None:
    sql = re.sub(r"--[^\n]*", "", (MIGRATIONS / "0016_budget_reservations.sql").read_text(encoding="utf-8"))
    view = sql[sql.index("CREATE VIEW v_budget_orphan_reservations") :]
    reasons = set(re.findall(r"(?:THEN|ELSE)\s+'([a-z_]+)'", view.split("FROM budget_reservations", 1)[0]))
    assert reasons == {r.value for r in BU.BudgetOrphanReason}
    assert "interval '1 hour'" in view and BU.BUDGET_TASKLESS_RESERVATION_TTL_SEC == 3600
