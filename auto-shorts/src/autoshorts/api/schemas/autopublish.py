"""Автозалив через YouTube Data API (0019, решение владельца 2026-09-27). Роутер — `api/routers/autopublish.py`."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from autoshorts.api.schemas.common import ApiModel, RequestModel
from autoshorts.domain.enums import AutopublishState, Platform, PublishMode, VideoPrivacy

__all__ = ["AutopublishQueue", "AutopublishQueueItem", "AutopublishRequest", "OAuthStart", "YoutubeConnection"]


class YoutubeConnection(ApiModel):
    channel_id: str
    platform: Platform
    client_configured: bool = Field(description="Есть ли OAuth-клиент Google (секрет куста или общий)")
    client_secret_name: str = Field(description="Имя секрета с JSON клиента OAuth")
    redirect_uri: str = Field(description="Этот адрес вписывается в Authorized redirect URIs клиента Google")
    connected: bool
    can_upload: bool = Field(description="Токен с правом youtube.upload")
    scopes: list[str]
    granted_at: datetime | None
    last_ok_at: datetime | None
    last_error: str | None
    google_channel_id: str | None
    google_channel_title: str | None
    mode: PublishMode
    slots: list[str] = Field(description="Слоты автозалива (местное время HH:MM)")
    timezone: str
    privacy: VideoPrivacy
    videos_per_day: int | None
    category_id: str = Field(description="Категория YouTube для залитых роликов (1 — фильмы и анимация)")
    language: str | None = Field(description="Язык ролика и звука, иначе не передаётся")


class OAuthStart(ApiModel):
    authorize_url: str
    redirect_uri: str


class AutopublishRequest(RequestModel):
    """Без полей — ближайший свободный слот канала; `now` — сразу; `at` — на это время."""

    now: bool = False
    at: datetime | None = Field(default=None, description="Время заливки (с поясом); в прошлом — сразу")
    privacy: VideoPrivacy | None = Field(default=None, description="Иначе из плана канала")


class AutopublishQueueItem(ApiModel):
    package_id: int
    job_id: int
    video_uid: str
    tracking_code: str
    channel_id: str
    channel_title: str | None
    title: str
    state: AutopublishState
    at: datetime | None
    privacy: VideoPrivacy | None
    attempts: int
    error: str | None
    video_id: str | None
    video_url: str | None


class AutopublishQueue(ApiModel):
    items: list[AutopublishQueueItem]
    generated_at: datetime
