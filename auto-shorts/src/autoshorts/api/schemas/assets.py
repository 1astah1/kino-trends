"""Ассеты: шрифты, логотипы, музыка, эталоны водяных знаков (docs/ARCHITECTURE.md §5.2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from autoshorts.api.schemas.common import ApiModel, RequestModel
from autoshorts.domain.enums import AssetKind


class Asset(ApiModel):
    id: int
    kind: AssetKind
    name: str
    bytes: int
    mime: str
    license_note: str
    meta: dict[str, Any] = Field(description='Шрифт: {"cyrillic_ok": true, "missing": []}')
    file_url: str = Field(description="/api/v1/assets/{id}/file")
    created_at: datetime


class AssetList(ApiModel):
    items: list[Asset]


class AssetCreate(RequestModel):
    upload_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    kind: AssetKind
    name: str = Field(min_length=1, max_length=200)
    license_note: str = Field(min_length=3, max_length=2000)
