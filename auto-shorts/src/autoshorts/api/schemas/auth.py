"""Auth: вход, сессии, API-токены (docs/ARCHITECTURE.md §5.1, §5.2 «Auth и пользователи»)."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from autoshorts.api.schemas.common import ApiModel, RequestModel
from autoshorts.domain.enums import ApiTokenScope, Role


class LoginRequest(RequestModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class CurrentUser(ApiModel):
    id: int
    login: str
    display_name: str
    role: Role
    tg_user_id: int | None


class AuthState(ApiModel):
    """Ответ `POST /auth/login` и `GET /auth/me`. `csrf_token` фронт шлёт в `X-CSRF-Token`."""

    user: CurrentUser
    csrf_token: str


class ChangePasswordRequest(RequestModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class SessionItem(ApiModel):
    id: int
    created_at: datetime
    last_seen_at: datetime
    user_agent: str | None
    current: bool


class SessionList(ApiModel):
    items: list[SessionItem]


class ApiToken(ApiModel):
    id: int
    name: str
    prefix: str = Field(description="Видимая часть токена `ast_xxxx` для опознания")
    scopes: list[ApiTokenScope]
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class ApiTokenList(ApiModel):
    items: list[ApiToken]


class ApiTokenCreate(RequestModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[ApiTokenScope] = Field(min_length=1)
    expires_at: datetime | None = None


class ApiTokenCreated(ApiModel):
    id: int
    token: str = Field(description="Полный токен `ast_…`; отдаётся один раз")
