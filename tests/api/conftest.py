"""Фикстуры API-тестов (M1-B): приложение на чистой тестовой БД, пользователи, вход.

База — общая фикстура `app_env` из tests/conftest.py (autoshorts_test, очищается перед тестом).
Для параллельных прогонов нескольких потоков: `AUTOSHORTS_TEST_DB_ISOLATE=1`.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest

warnings.filterwarnings("ignore", message=".*httpx2.*")

API = "/api/v1"


@dataclass
class ApiUser:
    """Клиент, вошедший под пользователем: сам добавляет `X-CSRF-Token` к небезопасным методам."""

    client: Any
    user: dict[str, Any]
    password: str
    csrf: str | None = None

    def login(self) -> Any:
        resp = self.client.post(
            f"{API}/auth/login", json={"login": self.user["login"], "password": self.password}
        )
        assert resp.status_code == 200, resp.text
        self.csrf = resp.json()["csrf_token"]
        return resp

    def request(self, method: str, path: str, *, csrf: bool = True, **kwargs: Any) -> Any:
        headers = dict(kwargs.pop("headers", None) or {})
        if csrf and self.csrf and method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            headers.setdefault("X-CSRF-Token", self.csrf)
        return self.client.request(method, f"{API}{path}", headers=headers, **kwargs)

    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self.request("POST", path, **kw)

    def put(self, path: str, **kw: Any) -> Any:
        return self.request("PUT", path, **kw)

    def patch(self, path: str, **kw: Any) -> Any:
        return self.request("PATCH", path, **kw)

    def delete(self, path: str, **kw: Any) -> Any:
        return self.request("DELETE", path, **kw)


@pytest.fixture
def api_app(app_env: Any, fast_password_hasher: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Свежее приложение на чистой тестовой базе; кеши и лимиты процесса сброшены."""
    from autoshorts.api.app import create_app
    from autoshorts.services import auth, providers, secrets, settings

    auth.login_ip_limiter.reset()
    settings.invalidate_cache()
    secrets.invalidate_cache()
    monkeypatch.setattr(secrets, "ENV_FILE", None)  # настоящий .env проекта в тестах не читаем
    for name in secrets.KNOWN_SECRETS:
        monkeypatch.delenv(name, raising=False)
    providers._test_state.clear()
    app = create_app()
    app.state.sse_ping_interval = 0.3
    yield app
    settings.invalidate_cache()
    secrets.invalidate_cache()
    auth.login_ip_limiter.reset()


@pytest.fixture
def client(api_app: Any) -> Iterator[Any]:
    """TestClient с lifespan (пул БД, ворота миграций)."""
    from fastapi.testclient import TestClient

    with TestClient(api_app) as c:
        yield c


@pytest.fixture
def new_client(api_app: Any, client: Any) -> Callable[[], Any]:
    """Ещё один клиент (свои cookie) к тому же приложению — второй пользователь или вторая сессия."""
    from fastapi.testclient import TestClient

    def factory() -> Any:
        return TestClient(api_app)

    return factory


@pytest.fixture
def make_user(client: Any) -> Callable[..., dict[str, Any]]:
    """Создать пользователя напрямую через сервис (без API). Пароль по умолчанию — `<login>-password-1`."""
    from autoshorts.services.audit import SYSTEM
    from autoshorts.services.users import create_user

    def factory(login: str, role: str = "admin", password: str | None = None, **kw: Any) -> dict[str, Any]:
        row = create_user(
            login=login,
            display_name=kw.pop("display_name", login.title()),
            role=role,
            password=password or f"{login}-password-1",
            tg_user_id=kw.pop("tg_user_id", None),
            actor=SYSTEM,
        )
        return {**row, "password": password or f"{login}-password-1"}

    return factory


@pytest.fixture
def login_as(client: Any, new_client: Callable[[], Any], make_user: Callable[..., dict[str, Any]]):
    """`login_as("admin")` → ApiUser с новой сессией (пользователь создаётся, если его нет)."""
    created: dict[str, dict[str, Any]] = {}

    def factory(role: str, login: str | None = None, *, own_client: bool = True) -> ApiUser:
        name = login or f"{role}1"
        if name not in created:
            created[name] = make_user(name, role)
        user = created[name]
        api_user = ApiUser(new_client() if own_client else client, user, user["password"])
        api_user.login()
        return api_user

    return factory


@pytest.fixture
def db(app_env: Any) -> Iterator[Any]:
    """Прямое соединение с тестовой БД (autocommit, dict_row) для подготовки и проверки данных."""
    import psycopg
    from psycopg.rows import dict_row

    conn = psycopg.connect(app_env.database_url, autocommit=True, row_factory=dict_row)
    conn.execute("SET TIME ZONE 'UTC'")
    try:
        yield conn
    finally:
        conn.close()
