"""Кусты изоляции каналов (ТЗ §10; docs/ARCHITECTURE.md §5.2, таблица clusters)."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from autoshorts.api.schemas.common import SECRET_NAME_PATTERN, ApiModel, RequestModel, Slug


class Cluster(ApiModel):
    id: str
    title: str
    notes: str | None
    youtube_api_key_secret: str | None = Field(description="Имя секрета с ключом YouTube Data API куста")
    youtube_oauth_client_secret: str | None
    channels_count: int
    created_at: datetime


class ClusterList(ApiModel):
    items: list[Cluster]


class ClusterCreate(RequestModel):
    id: Slug
    title: str = Field(min_length=1, max_length=100)
    notes: str | None = None


class ClusterPatch(RequestModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    notes: str | None = None
    youtube_api_key_secret: str | None = Field(default=None, pattern=SECRET_NAME_PATTERN)
    youtube_oauth_client_secret: str | None = Field(default=None, pattern=SECRET_NAME_PATTERN)
