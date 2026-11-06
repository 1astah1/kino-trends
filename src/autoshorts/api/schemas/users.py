"""Пользователи и роли (docs/ARCHITECTURE.md §5.2). Роли: admin (в UI «Владелец») / operator / viewer."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from autoshorts.api.schemas.common import LOGIN_PATTERN, ApiModel, RequestModel
from autoshorts.domain.enums import Role


class User(ApiModel):
    id: int
    login: str
    display_name: str
    role: Role
    tg_user_id: int | None
    is_active: bool
    locked_until: datetime | None
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class UserList(ApiModel):
    items: list[User]


class UserCreate(RequestModel):
    login: str = Field(pattern=LOGIN_PATTERN)
    display_name: str = Field(min_length=1, max_length=100)
    role: Role
    password: str = Field(min_length=12, max_length=1024)
    tg_user_id: int | None = None


class UserUpdate(RequestModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    role: Role | None = None
    is_active: bool | None = None
    tg_user_id: int | None = None


class ResetPasswordRequest(RequestModel):
    new_password: str = Field(min_length=12, max_length=1024)
