"""Ниши с версиями конфига (docs/ARCHITECTURE.md §5.2 «Ниши и каналы»).

`config` валидируется `domain/niche_schema.NicheConfig` (поток M2-A); в контракте M1 это JSON-объект
формы `config/niches/*.yaml`. Инструменты ниши (test-script, voice-sample, check-forbidden) — M6-B.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from autoshorts.api.schemas.common import ApiModel, PageMeta, RequestModel, Slug
from autoshorts.domain.enums import NicheStatus, NicheVersionSource


class NicheRow(ApiModel):
    id: str
    label: str
    status: NicheStatus
    current_version: int
    used_by_channels: list[str]
    updated_at: datetime


class NichePage(PageMeta):
    items: list[NicheRow]


class Niche(ApiModel):
    id: str
    label: str
    status: NicheStatus
    current_version: int = Field(description="Передавать как expected_version в PUT")
    config: dict[str, Any] = Field(description="NicheConfig")
    used_by_channels: list[str]
    created_at: datetime
    updated_at: datetime


class NicheCreate(RequestModel):
    id: Slug
    label: str = Field(min_length=1, max_length=100)
    config: dict[str, Any] = Field(default_factory=dict)
    clone_from: Slug | None = Field(default=None, description="Скопировать конфиг текущей версии другой ниши")


class NicheVersionPut(RequestModel):
    config: dict[str, Any]
    expected_version: int = Field(ge=0)


class NichePatch(RequestModel):
    label: str | None = Field(default=None, min_length=1, max_length=100)
    status: NicheStatus | None = None


class NicheVersionItem(ApiModel):
    version: int
    source: NicheVersionSource
    created_by: int | None
    created_at: datetime


class NicheVersionList(ApiModel):
    items: list[NicheVersionItem]


class NicheVersionConfig(ApiModel):
    version: int
    config: dict[str, Any]


class NicheValidateRequest(RequestModel):
    config: dict[str, Any]


class ConfigIssue(ApiModel):
    path: str = Field(description="Путь в конфиге: script.hooks[2].text")
    msg: str


class NicheValidateResponse(ApiModel):
    ok: bool
    errors: list[ConfigIssue]
    warnings: list[ConfigIssue]
