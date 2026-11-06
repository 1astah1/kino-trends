"""Каналы на площадках, здоровье, инциденты (docs/ARCHITECTURE.md §5.2 «Ниши и каналы», §1.3).

Трекинг-ссылка канала — контракт M3, OAuth YouTube Analytics — M4-D.
M4-0 (аддитивно): `Channel.stats` — последний `channel_snapshots` и сводка публикаций канала
(коридор p25–p75 просмотров на 72 ч — фон карточки ролика). Заполняет M4-A/M4-B, до этого `null`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator
from pydantic_core import PydanticCustomError

from autoshorts.api.schemas.common import ApiModel, PageMeta, RequestModel, Slug
from autoshorts.domain.enums import (
    ChannelStatus,
    IncidentKind,
    MetricsSource,
    Platform,
    PublicationStatus,
    PublishMode,
    VideoPrivacy,
)


class ChannelProfile(RequestModel):
    """Профиль канала — свой голос, шаблон, стиль (разные профили против детекта сетки, §1.3)."""

    tts_provider: str | None = None
    tts_voice: str | None = None
    banner_template_id: int | None = None
    visual_style_suffix: str | None = Field(default=None, max_length=300)
    seed_family: str | None = Field(default=None, max_length=40)


class PublishingPlan(RequestModel):
    videos_per_day: int | None = Field(default=None, ge=0, le=50)
    mode: PublishMode | None = Field(
        default=None, description="manual — пакет к ручной заливке; auto — система заливает сама по слотам"
    )
    slots: list[str] | None = Field(
        default=None, max_length=24, description="Слоты автозалива, местное время HH:MM"
    )
    timezone: str | None = Field(default=None, max_length=64, description="IANA-пояс слотов, иначе бизнес-пояс")
    privacy: VideoPrivacy | None = Field(default=None, description="Приватность автозалива (public по умолчанию)")
    category_id: str | None = Field(
        default=None,
        pattern=r"^[0-9]{1,3}$",
        description="Категория YouTube: 1 — фильмы и анимация, 22 — люди и блоги (умолч.)",
    )
    language: str | None = Field(
        default=None,
        pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$",
        description="Язык ролика и звука (BCP-47, например ru)",
    )
    clips_per_day: int | None = Field(
        default=None,
        ge=0,
        le=50,
        description="Нарезок чужого видео в сутки (остальные слоты — под своё); пусто — без ограничения",
    )

    @field_validator("slots")
    @classmethod
    def _slots(cls, v: list[str] | None) -> list[str] | None:
        import re

        if v is None:
            return v
        bad = [x for x in v if not re.fullmatch(r"([01][0-9]|2[0-3]):[0-5][0-9]", x)]
        if bad:
            raise PydanticCustomError("slot_format", "Слот — время HH:MM: {bad}", {"bad": bad})
        return sorted(set(v))

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        if v is None:
            return v
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise PydanticCustomError("timezone", "Неизвестный часовой пояс: {tz}", {"tz": v}) from exc
        return v


class ChannelStats(ApiModel):
    """Статистика канала (M4): снимок площадки (`channel_snapshots`, задача `channel_stats`) + свои ролики."""

    captured_at: datetime | None = Field(description="Время последнего снимка канала")
    source: MetricsSource | None
    subscribers: int | None
    total_views: int | None
    video_count: int | None
    publications_total: int = Field(description="Живые публикации канала у нас")
    publications_30d: int
    views_30d: int | None = Field(description="Прирост просмотров своих роликов за 30 дней (views_eod)")
    views_72h_median: float | None = Field(description="Медиана просмотров на 72 ч, ролики за 90 дней")
    views_72h_p25: float | None
    views_72h_p75: float | None


class Channel(ApiModel):
    id: str
    platform: Platform
    niche_id: str
    cluster: str
    title: str | None
    platform_channel_ref: str | None = Field(description="UC… для YouTube, -<group_id> для VK")
    url: str | None
    status: ChannelStatus
    replaced_by: str | None
    default_product_id: str | None
    profile: ChannelProfile
    publishing_plan: PublishingPlan
    advanced_features: bool
    subscribers: int | None
    notes: str | None
    profile_duplicate_of: list[str] = Field(description="Каналы с совпадающим профилем (предупреждение)")
    created_at: datetime
    updated_at: datetime
    stats: ChannelStats | None = Field(default=None, description="M4: статистика канала и своих роликов")


class ChannelPage(PageMeta):
    items: list[Channel]


class ChannelCreate(RequestModel):
    id: Slug
    platform: Platform
    niche_id: Slug
    cluster: Slug
    title: str | None = Field(default=None, max_length=200)
    platform_channel_ref: str | None = Field(default=None, pattern=r"^(UC[A-Za-z0-9_-]{22}|-?[0-9]{1,20})$")
    url: str | None = Field(default=None, max_length=500)
    status: ChannelStatus = ChannelStatus.ACTIVE
    default_product_id: Slug | None = None
    profile: ChannelProfile = Field(default_factory=ChannelProfile)
    publishing_plan: PublishingPlan = Field(default_factory=PublishingPlan)
    advanced_features: bool = False
    notes: str | None = None


class ChannelPatch(RequestModel):
    niche_id: Slug | None = None
    cluster: Slug | None = None
    title: str | None = Field(default=None, max_length=200)
    platform_channel_ref: str | None = Field(default=None, pattern=r"^(UC[A-Za-z0-9_-]{22}|-?[0-9]{1,20})$")
    url: str | None = Field(default=None, max_length=500)
    status: ChannelStatus | None = None
    replaced_by: Slug | None = None
    default_product_id: Slug | None = None
    profile: ChannelProfile | None = None
    publishing_plan: PublishingPlan | None = None
    advanced_features: bool | None = None
    subscribers: int | None = Field(default=None, ge=0)
    notes: str | None = None


class ChannelRecentPublication(ApiModel):
    publication_id: int
    published_at: datetime | None
    status: PublicationStatus
    views: int | None


class ChannelHealth(ApiModel):
    status: ChannelStatus
    last_publications: list[ChannelRecentPublication]
    removed_count_30d: int
    sanction_signals: list[str] = Field(description="Коды сигналов: uploader_suspended, reach_drop, …")
    views_trend_7d: float | None = Field(description="Отношение медианы просмотров за 7 дней к предыдущим 7")
    below_p25_streak: int = Field(description="Сколько последних роликов ниже p25 канала подряд")
    shorts_feed_share: float | None = Field(description="Доля ленты Shorts (только при OAuth)")


class ChannelIncident(ApiModel):
    id: int
    channel_id: str
    kind: IncidentKind
    occurred_at: datetime
    details: dict[str, Any]
    note: str | None
    created_by: int | None


class ChannelIncidentList(ApiModel):
    items: list[ChannelIncident]


class ChannelIncidentCreate(RequestModel):
    kind: IncidentKind
    note: str | None = Field(default=None, max_length=2000)
    details: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None
