"""Пакеты «К заливке» (docs/ARCHITECTURE.md §5.2 «Пакеты…», §5.4 UploadPackage, §7.3 «Пакеты»).

Публикация ручная (решение владельца): инструмент готовит пакет — файл, тексты, хэштеги, закреп,
чек-лист, трекинг-код, для нарезки ещё атрибуцию и итог отчёта об оригинальности; оператор заливает
сам и вставляет ссылку (`POST /publications`). Реализация — M3-A (`api/routers/packages.py`,
`stages/package_build.py`), тег `packages`:

* `GET /packages` `?status&channel_id&platform&limit&offset` → `UploadPackagePage`;
* `GET /packages/{package_id}` → `UploadPackage`;
* `GET /jobs/{job_id}/package` → `UploadPackage` · 404 package_not_found;
* `POST /jobs/{job_id}/package` `PackageRebuild` → `UploadPackage` · 409 package_not_ready;
* `PATCH /packages/{package_id}` `PackagePatch` → `UploadPackage` (texts_mode → operator);
* `PATCH /packages/{package_id}/checklist` `ChecklistPatch` → `UploadPackage`;
* `POST /packages/{package_id}/mark-uploaded` → `UploadPackage` (status downloaded, ссылка позже);
* `GET /packages/{package_id}/video` → mp4 attachment `<channel>_<date>_<code>.mp4` (Range;
  ставит downloaded);
* `GET /packages/{package_id}/text` → `text/plain` (все тексты одним файлом);
* `GET /packages/{package_id}/zip` → `application/zip` (ZIP_STORED: видео + тексты).

Без ключа LLM тексты собираются шаблоном: `texts.texts_mode = template` и подпись в `texts_label`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator
from pydantic_core import PydanticCustomError

from autoshorts.api.schemas.clipping import OriginalityVerdict
from autoshorts.api.schemas.common import ApiModel, PageMeta, RequestModel
from autoshorts.domain.enums import (
    AutopublishState,
    ChecklistCode,
    JobKind,
    PackageStatus,
    Platform,
    PublicationStatus,
    VerifyStatus,
    VideoPrivacy,
)

__all__ = [
    "PLATFORM_TEXT_LIMITS",
    "ChecklistItem",
    "ChecklistPatch",
    "PackageChannel",
    "PackageFile",
    "PackageOriginality",
    "PackagePatch",
    "PackagePublicationRef",
    "PackageRebuild",
    "PackageTexts",
    "PackageTextsMode",
    "PackageTracking",
    "UploadPackage",
    "UploadPackagePage",
]

PLATFORM_TEXT_LIMITS: dict[str, dict[str, int]] = {
    "youtube": {"title": 100, "description": 5000, "hashtags": 15},
    "vk": {"title": 100, "description": 5000, "hashtags": 15},
}
"""Лимиты площадок для текстов пакета (title_max, description_max в ответе)."""
HASHTAG_PATTERN = r"^#[^\s#]{1,100}$"


class PackageTextsMode(StrEnum):
    """Откуда тексты (`upload_packages.texts_mode`): template — без LLM, UI подписывает."""

    LLM = "llm"
    TEMPLATE = "template"
    OPERATOR = "operator"


class PackageChannel(ApiModel):
    id: str
    display_name: str = Field(description="Название канала, как на площадке")
    platform: Platform
    account_hint: str = Field(description="В какой аккаунт зайти: «Brand Account: … (@handle)»")
    platform_channel_ref: str | None


class PackageTexts(ApiModel):
    title: str
    title_max: int
    description: str
    description_max: int
    hashtags: list[str]
    pinned_comment: str | None
    related_video_hint: str | None
    audio_note: str | None
    attribution_text: str | None = Field(
        description="Чужие права (CC BY, лицензии): обязано быть в описании — уже вставлено в description"
    )
    texts_mode: PackageTextsMode
    texts_label: str = Field(description="«LLM umka/…» или «шаблон без LLM — проверьте тексты»")


class ChecklistItem(ApiModel):
    code: ChecklistCode
    done: bool
    done_at: datetime | None
    done_by: int | None = None
    required: bool = Field(default=True, description="Например, ai_disclosure — только при синтетике")


class PackageTracking(ApiModel):
    code: str = Field(description="Код пакета: 5 символов Crockford base32 (нижний регистр)")
    redirect_url: str = Field(description="https://u.<домен>/<code>")
    telegram_url: str | None = Field(description="tg://resolve?domain=<bot>&start=<code>")
    telegram_https_url: str | None = Field(description="https://telegram.me/<bot>?start=<code>")
    site_url: str | None = Field(description="Лендинг с utm_campaign=<code>")


class PackageFile(ApiModel):
    download_url: str
    filename: str
    size_bytes: int
    duration_sec: float | None
    text_url: str
    zip_url: str


class PackageOriginality(ApiModel):
    """Нарезка: итог отчёта об оригинальности на момент сборки пакета."""

    verdict: OriginalityVerdict
    author_share: float
    gate_applies: bool
    gate_passed: bool
    suggestions: list[str]


class PackagePublicationRef(ApiModel):
    id: int
    url: str | None
    status: PublicationStatus
    verify_status: VerifyStatus
    published_at: datetime | None


class PackageAutopublish(ApiModel):
    """Автозалив через API площадки (0019). Нет блока — пакет в автозалив не ставили."""

    state: AutopublishState
    at: datetime | None = Field(description="Слот заливки (UTC); для failed/done — последний")
    privacy: VideoPrivacy | None
    attempts: int
    error: str | None = Field(description="Почему не залито или отложено, по-русски")
    video_id: str | None = Field(description="id ролика на площадке сразу после заливки")
    video_url: str | None


class UploadPackage(ApiModel):
    id: int
    job_id: int
    video_uid: str
    kind: JobKind
    status: PackageStatus
    channel: PackageChannel
    texts: PackageTexts
    checklist: list[ChecklistItem]
    tracking: PackageTracking
    file: PackageFile
    ai_disclosure_required: bool
    originality: PackageOriginality | None = Field(description="Только нарезка")
    publication: PackagePublicationRef | None
    downloaded_at: datetime | None
    marked_uploaded_at: datetime | None
    autopublish: PackageAutopublish | None = None
    created_at: datetime
    updated_at: datetime


class UploadPackagePage(PageMeta):
    items: list[UploadPackage]


class PackageRebuild(RequestModel):
    regenerate_texts: bool = Field(default=False, description="true — заново через LLM (или шаблон)")


class PackagePatch(RequestModel):
    """Автосохранение текстов (оператор) → `texts_mode = operator`."""

    title: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    hashtags: list[str] | None = Field(default=None, max_length=15)
    pinned_comment: str | None = Field(default=None, max_length=10000)

    @field_validator("hashtags")
    @classmethod
    def _hashtags(cls, v: list[str] | None) -> list[str] | None:
        import re

        if v is None:
            return v
        bad = [h for h in v if not re.fullmatch(HASHTAG_PATTERN, h)]
        if bad:
            raise PydanticCustomError("hashtag_format", "Хэштег — «#слово» без пробелов: {bad}", {"bad": bad})
        return v


class ChecklistPatch(RequestModel):
    code: ChecklistCode
    done: bool
