"""Реестр провайдеров и их конфиги (docs/ARCHITECTURE.md §5.2, таблица provider_configs)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from autoshorts.api.schemas.common import SECRET_NAME_PATTERN, ApiModel, RequestModel
from autoshorts.domain.enums import ProviderKind, ResourceClass


class ProviderPrice(ApiModel):
    unit: str = Field(description="call | image | 1k_tokens | audio_minute | char …")
    usd: float


class Provider(ApiModel):
    key: str
    kind: ProviderKind
    adapter: str = Field(description="Модуль адаптера, напр. llm.openai_compat")
    label: str
    enabled: bool
    is_default: bool
    params: dict[str, Any]
    secret_name: str | None
    secret_set: bool
    price: ProviderPrice | None
    license_note: str | None
    resource_class: ResourceClass
    last_ok_at: datetime | None
    last_error: str | None


class ProviderList(ApiModel):
    items: list[Provider]


class ProviderUpdate(RequestModel):
    enabled: bool
    is_default: bool
    params: dict[str, Any] = Field(default_factory=dict)
    secret_name: str | None = Field(default=None, pattern=SECRET_NAME_PATTERN)


class ProviderTestResult(ApiModel):
    ok: bool
    latency_ms: int | None
    detail: str | None
