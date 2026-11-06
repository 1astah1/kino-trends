"""Все идентификаторы — латиница (docs/ARCHITECTURE.md §1.5, §5.1).

Проверяются: пути и параметры в `app.routes`, теги, operationId, имена схем, ключи свойств,
значения enum/const в OpenAPI, а также словари домена (enum, ключи настроек, события).
Кириллица допустима только в значениях данных и текстах (summary, description, label).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from autoshorts.domain.enums import ENUM_REGISTRY, LABELS
from autoshorts.domain.events import EventType
from autoshorts.domain.settings_schema import RuntimeSettings, setting_specs

SNAKE = re.compile(r"^[a-z0-9_]+$")
PARAM = re.compile(r"^[a-z][a-z0-9_]*$")
PATH_SEGMENT = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
PATH_PARAM_SEGMENT = re.compile(r"^\{([^}:]+)(:[a-z]+)?\}$")
HEADER = re.compile(r"^[A-Za-z][A-Za-z0-9-]*$")
ENUM_VALUE = re.compile(r"^-?[a-z0-9_]+$")
SCHEMA_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
TAG = re.compile(r"^[a-z0-9_]+$")
API_PREFIX = "/api/v1"


def _path_problems(path: str) -> list[str]:
    problems = []
    for seg in path.strip("/").split("/"):
        if not seg:
            continue
        m = PATH_PARAM_SEGMENT.match(seg)
        if m:
            if not PARAM.match(m.group(1)):
                problems.append(f"{path}: path param {m.group(1)!r}")
        elif not PATH_SEGMENT.match(seg) and seg != "openapi.json":
            problems.append(f"{path}: segment {seg!r}")
    return problems


def _api_routes(app: FastAPI) -> list[Any]:
    # FastAPI 0.141: include_router кладёт в app.routes обёртки _IncludedRouter, а не APIRoute.
    # Их effective_route_contexts() дают маршруты с полным путем, методами, тегами, name и dependant.
    routes: list[Any] = []
    for r in app.routes:
        if isinstance(r, APIRoute):
            routes.append(r)
        elif hasattr(r, "effective_route_contexts"):
            routes.extend(c for c in r.effective_route_contexts() if isinstance(c.original_route, APIRoute))
    return routes


def test_api_routes_are_discovered(app: FastAPI) -> None:
    # страховка от пустого обхода: без неё проверки маршрутов ниже молча ничего не проверяют
    assert len(_api_routes(app)) >= 100


# ---------------------------------------------------------------- самопроверка детектора


@pytest.mark.parametrize(
    ("path", "bad"),
    [
        ("/api/v1/jobs/{job_id}", False),
        ("/api/v1/banner-templates/{template_id}/export-yaml", False),
        ("/api/v1/ролики/{job_id}", True),
        ("/api/v1/jobs/{номер}", True),
        ("/api/v1/Jobs", True),
        ("/api/v1/stage_counts", True),
    ],
)
def test_path_checker_catches_cyrillic_and_style(path: str, bad: bool) -> None:
    assert bool(_path_problems(path)) is bad


def test_schema_walker_catches_cyrillic_property() -> None:
    spec = {"components": {"schemas": {"X": {"properties": {"статус": {"enum": ["ок"]}}}}}}
    problems = list(_schema_problems(spec))
    assert any("статус" in p for p in problems)
    assert any("ок" in p for p in problems)


# ---------------------------------------------------------------- app.routes


def test_routes_are_under_api_prefix(app: FastAPI) -> None:
    outside = [r.path for r in _api_routes(app) if not r.path.startswith(API_PREFIX + "/")]
    assert not outside, f"routes outside {API_PREFIX}: {outside}"


def test_route_paths_latin(app: FastAPI) -> None:
    routes = [*app.routes, *_api_routes(app)]
    problems = [p for r in routes for p in _path_problems(getattr(r, "path", ""))]
    assert not problems, problems


def test_route_param_names_latin(app: FastAPI) -> None:
    problems = []
    for route in _api_routes(app):
        dep = route.dependant
        for p in (*dep.path_params, *dep.query_params, *dep.cookie_params):
            if not PARAM.match(p.alias):
                problems.append(f"{route.path}: param {p.alias!r}")
        for p in dep.header_params:
            if not HEADER.match(p.alias):
                problems.append(f"{route.path}: header {p.alias!r}")
        for p in dep.body_params:
            if not PARAM.match(p.alias):
                problems.append(f"{route.path}: body field {p.alias!r}")
    assert not problems, problems


def test_routes_have_single_latin_tag_and_name(app: FastAPI) -> None:
    problems = []
    for route in _api_routes(app):
        if len(route.tags) != 1:
            problems.append(f"{route.path} {sorted(route.methods)}: tags={route.tags} (нужен ровно один)")
        elif not TAG.match(str(route.tags[0])):
            problems.append(f"{route.path}: tag {route.tags[0]!r}")
        if not SNAKE.match(route.name):
            problems.append(f"{route.path}: name {route.name!r}")
    assert not problems, problems


# ---------------------------------------------------------------- OpenAPI


def _walk(node: Any, path: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            yield from _walk(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}/{i}")


def _schema_problems(spec: dict[str, Any]) -> Iterator[str]:
    for name in spec.get("components", {}).get("schemas", {}):
        if not SCHEMA_NAME.match(name):
            yield f"schema name {name!r}"
    for where, node in _walk(spec):
        props = node.get("properties")
        if isinstance(props, dict) and all(isinstance(v, dict) for v in props.values()):
            for key in props:
                if not SNAKE.match(key):
                    yield f"{where}: property {key!r}"
        req = node.get("required")
        if isinstance(req, list):
            for key in req:
                if isinstance(key, str) and not SNAKE.match(key):
                    yield f"{where}: required {key!r}"
        enum = node.get("enum")
        if isinstance(enum, list):
            for value in enum:
                if isinstance(value, str) and not ENUM_VALUE.match(value):
                    yield f"{where}: enum value {value!r}"
        const = node.get("const")
        if isinstance(const, str) and not ENUM_VALUE.match(const):
            yield f"{where}: const {const!r}"
        disc = node.get("discriminator")
        if isinstance(disc, dict):
            if not SNAKE.match(str(disc.get("propertyName", ""))):
                yield f"{where}: discriminator {disc.get('propertyName')!r}"
            for key in disc.get("mapping", {}):
                if not ENUM_VALUE.match(key):
                    yield f"{where}: discriminator mapping {key!r}"


def test_openapi_schemas_latin(openapi_spec: dict[str, Any]) -> None:
    problems = list(_schema_problems(openapi_spec))
    assert not problems, problems[:50]


def test_openapi_paths_params_tags_operation_ids(openapi_spec: dict[str, Any]) -> None:
    problems: list[str] = []
    seen_ids: set[str] = set()
    for path, item in openapi_spec["paths"].items():
        problems += _path_problems(path)
        for method, op in item.items():
            tags = op.get("tags", [])
            op_id = op.get("operationId", "")
            if len(tags) != 1 or not TAG.match(tags[0]):
                problems.append(f"{method} {path}: tags {tags}")
            if not SNAKE.match(op_id) or (tags and not op_id.startswith(f"{tags[0]}_")):
                problems.append(f"{method} {path}: operationId {op_id!r} (ожидается <tag>_<verb>)")
            if op_id in seen_ids:
                problems.append(f"{method} {path}: duplicate operationId {op_id!r}")
            seen_ids.add(op_id)
            for p in op.get("parameters", []):
                pattern = HEADER if p.get("in") == "header" else PARAM
                if not pattern.match(p["name"]):
                    problems.append(f"{method} {path}: parameter {p['name']!r}")
    for tag in {
        t for item in openapi_spec["paths"].values() for op in item.values() for t in op.get("tags", [])
    }:
        if not TAG.match(tag):
            problems.append(f"tag {tag!r}")
    assert not problems, problems


# ---------------------------------------------------------------- домен


def test_domain_enum_values_latin() -> None:
    problems = []
    for name, enum_cls in ENUM_REGISTRY.items():
        if not SNAKE.match(name):
            problems.append(f"registry key {name!r}")
        for member in enum_cls:
            if not SNAKE.match(member.value):
                problems.append(f"{enum_cls.__name__}.{member.name} = {member.value!r}")
    for enum_cls, labels in LABELS.items():
        unknown = set(labels) - {m.value for m in enum_cls}
        if unknown:
            problems.append(f"LABELS[{enum_cls.__name__}] has unknown codes {sorted(unknown)}")
    assert not problems, problems


def test_setting_keys_match_db_check() -> None:
    db_key = re.compile(r"^[a-z][a-z0-9_]{2,63}$")  # CHECK settings.key в 0002
    bad = [k for k in RuntimeSettings.model_fields if not db_key.match(k)]
    assert not bad, bad
    assert set(setting_specs()) == set(RuntimeSettings.model_fields)


def test_event_names_snake_case() -> None:
    assert all(SNAKE.match(e.value) for e in EventType)
