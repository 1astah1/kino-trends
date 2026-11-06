"""Фикстуры контрактных тестов: приложение, OpenAPI и модуль scripts/export_openapi.py.

`contract_db_name(base)` — имя одноразовой базы для тестов миграций. При
`AUTOSHORTS_TEST_DB_ISOLATE=1` к имени добавляется pid, чтобы параллельные прогоны нескольких
потоков на одной машине не удаляли базы друг друга (как в tests/conftest.py).
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[2]


def contract_db_name(base: str) -> str:
    assert base.startswith("autoshorts_test"), base
    isolate = os.environ.get("AUTOSHORTS_TEST_DB_ISOLATE", "").strip().lower() in {"1", "true", "yes", "on"}
    return f"{base}_{os.getpid()}" if isolate else base


def _load_export_module() -> ModuleType:
    path = ROOT / "scripts" / "export_openapi.py"
    spec = importlib.util.spec_from_file_location("autoshorts_export_openapi", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def export_openapi() -> ModuleType:
    return _load_export_module()


@pytest.fixture(scope="session")
def app() -> FastAPI:
    from autoshorts.api.app import create_app

    return create_app()


@pytest.fixture(scope="session")
def openapi_spec(app: FastAPI) -> dict[str, Any]:
    return app.openapi()
