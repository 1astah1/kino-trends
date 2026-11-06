"""Рантайм-настройки (docs/ARCHITECTURE.md §5.2, §8.3). Дефолты — domain/settings_schema.py."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, RootModel

from autoshorts.api.schemas.common import ApiModel


class SettingItem(ApiModel):
    key: str
    value: Any = Field(description="Текущее значение (переопределение или дефолт)")
    default: Any
    description: str
    group: str = Field(description="Группа в UI: general, worker, render, analytics, clipping, qc, …")
    is_overridden: bool
    updated_at: datetime | None


class SettingsList(ApiModel):
    items: list[SettingItem]


class SettingsPatch(RootModel[dict[str, Any]]):
    """`{key: value, …}`; `null` сбрасывает переопределение к дефолту. Только admin."""
