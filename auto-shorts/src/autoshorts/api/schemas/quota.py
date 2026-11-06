"""Квоты внешних API и состояние коллекторов (docs/ARCHITECTURE.md §5.2 `/system/quota`, §6.5, контракт M4-0).

Реализация — M4-A (`repos/quota.py`, `api/routers/quota.py`), тег `quota`:

* `GET /quota` `?date_from&date_to&api&cluster_id` → `QuotaUsage` (viewer+): расход квот по API, кусту и
  суткам квоты (`api_quota_usage`), текущие сутки с процентом от лимита и состояние коллекторов.
  Пусто в датах — последние `QUOTA_DEFAULT_DAYS` суток квоты.

Сутки квоты у YouTube считаются по America/Los_Angeles (`schemas.metrics.YT_QUOTA_TIMEZONE`), а не по
бизнес-дню: `quota_day` — дата в этом поясе. Лимит YouTube Data — `YT_DAILY_QUOTA_UNITS`, жёсткий стоп
опроса — настройка `yt_quota_hard_units` (8000), алерт `quota_80` — `yt_quota_soft_pct`.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, Field, model_validator

from autoshorts.api.schemas.common import ApiModel, Slug
from autoshorts.api.schemas.metrics import CollectorStatus

__all__ = [
    "QUOTA_DEFAULT_DAYS",
    "QUOTA_MAX_DAYS",
    "QuotaApi",
    "QuotaDay",
    "QuotaQuery",
    "QuotaUsage",
]

QUOTA_DEFAULT_DAYS = 14
QUOTA_MAX_DAYS = 92


class QuotaApi(StrEnum):
    """`api_quota_usage.api` (CHECK в 0017)."""

    YOUTUBE_DATA = "youtube_data"  # коллектор метрик и трендоскаут делят одну квоту куста
    YOUTUBE_ANALYTICS = "youtube_analytics"  # M4-D, не в этом прогоне
    VK_API = "vk_api"


class QuotaQuery(BaseModel):
    """Параметры `GET /quota` (query-модель FastAPI)."""

    date_from: date | None = Field(default=None, description="YYYY-MM-DD, сутки квоты")
    date_to: date | None = Field(default=None, description="YYYY-MM-DD включительно")
    api: list[QuotaApi] = Field(default_factory=list, description="Повтором: ?api=youtube_data&api=vk_api")
    cluster_id: list[Slug] = Field(default_factory=list)

    @model_validator(mode="after")
    def _range(self) -> QuotaQuery:
        if self.date_from and self.date_to:
            if self.date_from > self.date_to:
                raise ValueError("date_from позже date_to")
            if (self.date_to - self.date_from).days + 1 > QUOTA_MAX_DAYS:
                raise ValueError(f"период длиннее {QUOTA_MAX_DAYS} дней")
        return self


QuotaQueryParams = Annotated[QuotaQuery, Query()]


class QuotaDay(ApiModel):
    api: QuotaApi
    cluster_id: str
    quota_day: date
    units: int
    calls: int
    errors: int
    limit: int | None = Field(description="Суточный лимит единиц (YouTube Data — 10 000)")
    hard_stop: int | None = Field(description="Жёсткий стоп опроса (yt_quota_hard_units)")
    soft_pct: int | None = Field(description="Порог алерта quota_80, %")
    pct: float | None = Field(description="units / limit × 100")
    updated_at: datetime | None


class QuotaUsage(ApiModel):
    date_from: date
    date_to: date
    items: list[QuotaDay] = Field(description="По дням, новые сверху")
    today: list[QuotaDay] = Field(
        description="Текущие сутки квоты по API и кусту (нули, если вызовов не было)"
    )
    reset_at: datetime = Field(description="Следующий сброс квоты YouTube (полночь America/Los_Angeles), UTC")
    collectors: list[CollectorStatus]
