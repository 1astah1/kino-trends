"""Секреты (docs/ARCHITECTURE.md §5.2, §8.3). Значения API не отдаёт никогда."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from autoshorts.api.schemas.common import ApiModel, RequestModel


class SecretItem(ApiModel):
    name: str
    is_set: bool
    source: Literal["db", "env"] | None = Field(description="Откуда берётся значение: БД, env или нигде")
    last4: str | None
    updated_at: datetime | None
    used_by: list[str] = Field(
        description="Ключи провайдеров, продуктов и кустов, которые ссылаются на секрет"
    )


class SecretList(ApiModel):
    items: list[SecretItem]


class SecretWrite(RequestModel):
    value: str = Field(min_length=1, max_length=16384)
