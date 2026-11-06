"""Каркас приложения M1-0: автообнаружение роутеров, 501 у заглушек, формат ошибок, нет /docs."""

from __future__ import annotations

import enum
import typing

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from autoshorts.api.app import API_PREFIX, discover_routers

# §10.2: M1-B, M1-C, M2-A, M2-C — по файлу на область; banner_editor — креативы владельца (M2-0)
EXPECTED_ROUTER_MODULES = {
    "auth", "users", "api_tokens", "system", "meta", "settings", "secrets", "providers", "audit", "alerts",
    "events", "budget",
    "tasks", "workers", "schedules",
    "products", "clusters", "niches", "banner_templates", "campaigns", "banner_variants", "channels",
    "assets", "banner_editor",
    "jobs", "review",
    # M5M3-0: нарезка (M5-A, M5-B) и пакеты/публикации/атрибуция (M3-A, M3-B)
    "uploads", "sources", "clip_candidates", "author_voice",
    "packages", "publications", "tracking_links", "clicks", "conversions", "product_integration",
    # M4-0: коллекторы (M4-A) и аналитика (M4-B)
    "overview", "analytics", "experiments", "quota", "metrics",
}  # fmt: skip

PATH_SAMPLES = {
    "secret_name": "FAL_API_KEY",
    "provider_key": "mock_llm",
    "schedule_name": "cleanup",
    "creative_sha256": "0" * 64,
    "font_name": "Montserrat-Bold.ttf",
    "upload_id": "0" * 32,
}


@pytest.fixture(scope="module")
def client(app: FastAPI) -> TestClient:
    app.state.schema_gate.state = None  # ворота миграций — не предмет этих тестов (см. _open_schema_gate)
    return TestClient(app, raise_server_exceptions=True)


def _sample(name: str, annotation: typing.Any) -> str:
    if name in PATH_SAMPLES:
        return PATH_SAMPLES[name]
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return str(next(iter(annotation)).value)
    if annotation is int:
        return "1"
    return "abc"


def _api_routes(app: FastAPI) -> list[typing.Any]:
    # FastAPI 0.141: в app.routes лежат обёртки _IncludedRouter, маршруты — в effective_route_contexts()
    routes: list[typing.Any] = []
    for r in app.routes:
        if isinstance(r, APIRoute):
            routes.append(r)
        elif hasattr(r, "effective_route_contexts"):
            routes.extend(c for c in r.effective_route_contexts() if isinstance(c.original_route, APIRoute))
    return routes


# Открытые эндпоинты без входа (M1-B): отвечают не форматом ошибки, а своим телом
PUBLIC_OK = {
    ("GET", f"{API_PREFIX}/health"): {200},
    ("GET", f"{API_PREFIX}/health/ready"): {200, 503},
}


def _concrete_path(route: APIRoute) -> str:
    path = route.path
    for p in route.dependant.path_params:
        path = path.replace("{" + p.name + "}", _sample(p.name, p.field_info.annotation))
    return path


def _open_schema_gate(app: FastAPI) -> None:
    app.state.schema_gate.state = None


def test_all_expected_router_modules_discovered() -> None:
    found = {name for name, _ in discover_routers()}
    assert EXPECTED_ROUTER_MODULES <= found, EXPECTED_ROUTER_MODULES - found


def test_every_stub_answers_501_or_422_in_error_format(app: FastAPI, client: TestClient) -> None:
    # Заглушки -> 501; эндпоинты, реализованные в M1-B/M1-C, без сессии -> 401 not_authenticated.
    problems = []
    routes = _api_routes(app)
    assert len(routes) >= 100, "маршруты не найдены — проверка была бы пустой"
    for route in routes:
        url = _concrete_path(route)
        for method in sorted(route.methods):
            kwargs = {"json": {}} if route.body_field is not None else {}
            resp = client.request(method, url, **kwargs)
            if (method, route.path) in PUBLIC_OK:
                if resp.status_code not in PUBLIC_OK[(method, route.path)]:
                    problems.append(f"{method} {url}: {resp.status_code} {resp.text[:120]}")
                # /health/ready сверяет схему общей тестовой базы и закрывает ворота миграций, если та
                # отстала от файлов (новые миграции этапа ещё не накатаны). Тест — про заглушки и вход,
                # не про ворота: без этого результат зависел бы от состояния чужой базы.
                _open_schema_gate(app)
                continue
            allowed = {501, 401, 422} if route.body_field is not None else {501, 401}
            is_json = resp.headers.get("content-type", "").startswith("application/json")
            if method == "HEAD":  # tus HEAD: у ответа нет тела, формат ошибки виден только по статусу и типу
                if resp.status_code not in allowed or not is_json or resp.content:
                    problems.append(f"{method} {url}: {resp.status_code} {resp.headers}")
                continue
            body = resp.json() if is_json else None
            if resp.status_code not in allowed or not (body and "error" in body and "code" in body["error"]):
                problems.append(f"{method} {url}: {resp.status_code} {resp.text[:120]}")
    assert not problems, problems


def test_not_implemented_body() -> None:
    # После M2 открытых заглушек не осталось (оставшиеся 501 — только под admin), поэтому формат
    # тела проверяется на самом помощнике, которым отвечают все заглушки.
    import json

    from autoshorts.api.routers import not_implemented

    resp = not_implemented()
    assert resp.status_code == 501
    assert json.loads(resp.body) == {
        "error": {"code": "not_implemented", "message": "Эндпоинт ещё не реализован", "details": {}}
    }


def test_validation_error_format(client: TestClient) -> None:
    resp = client.post(f"{API_PREFIX}/auth/login", json={"login": "x"})
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "validation_error"
    assert err["details"]["errors"] and {"loc", "msg", "type"} <= set(err["details"]["errors"][0])


def test_unknown_path_is_404_in_error_format(client: TestClient) -> None:
    resp = client.get(f"{API_PREFIX}/no-such-thing")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_docs_disabled_openapi_served(client: TestClient) -> None:
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    resp = client.get(f"{API_PREFIX}/openapi.json")
    assert resp.status_code == 200 and resp.json()["info"]["title"] == "Auto Shorts API"
