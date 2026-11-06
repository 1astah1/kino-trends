"""Приёмка: очередь, контекст экрана, решение (docs/ARCHITECTURE.md §5.2, §5.4 ReviewContext, §6.2).

Нарезка (M5): в контексте есть источник с правами (`source`), авторский слой (`author_script`) и отчёт
об оригинальности (`originality`, OWNER-DECISIONS п. 5); в очереди — `originality_verdict`.
У generate-роликов эти поля null.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from autoshorts.api.schemas.banners import BannerBox, BannerWarning, BannerWindow
from autoshorts.api.schemas.clipping import (
    AuthorScriptBody,
    AuthorScriptIssue,
    AuthorScriptMode,
    AuthorVoiceKind,
    OriginalityReportOut,
    OriginalityVerdict,
)
from autoshorts.api.schemas.common import ApiModel, RequestModel
from autoshorts.api.schemas.jobs import QcCheckResult
from autoshorts.domain.enums import (
    JobKind,
    JobStage,
    JobStatus,
    RejectReason,
    ReviewDecision,
    ReviewFlagCode,
    RightsType,
    WarningSeverity,
)
from autoshorts.domain.settings_schema import Rect


class ReviewFlag(ApiModel):
    code: ReviewFlagCode
    severity: WarningSeverity
    detail: str | None


class ReviewQueueItem(ApiModel):
    job_id: int
    video_uid: str
    kind: JobKind
    status: JobStatus = Field(description="pending_review | qc_failed")
    channel_id: str
    niche_id: str
    poster_url: str | None
    preview_url: str | None
    duration_sec: float | None
    attempt_count: int
    flags: list[ReviewFlag]
    ready_at: datetime | None = Field(description="Когда ролик попал в очередь приёмки")
    originality_verdict: OriginalityVerdict | None = Field(
        default=None, description="Нарезка: итог отчёта об оригинальности"
    )


class ReviewQueue(ApiModel):
    items: list[ReviewQueueItem]


class ReviewJobInfo(ApiModel):
    id: int
    video_uid: str
    kind: JobKind
    status: JobStatus
    channel_id: str
    niche_id: str
    product_id: str | None
    banner_variant_id: int | None
    attempt_count: int


class ReviewMedia(ApiModel):
    preview_url: str | None
    final_url: str | None
    poster_url: str | None
    duration_sec: float | None


class ReviewWord(ApiModel):
    word: str
    start_sec: float
    end_sec: float


class ReviewScript(ApiModel):
    topic: str | None
    hook_pattern: str | None
    voiceover_text: str | None
    cta_text: str | None
    words: list[ReviewWord]


class ReviewBanner(ApiModel):
    """Баннер ролика для SVG-оверлея приёмки: из плана рендера (`BannerPlan.to_json()` в артефакте)."""

    variant_id: int | None
    variant_code: str | None
    creative_sha256s: list[str] = Field(description="Креативы, попавшие в кадр")
    fallback_used: bool = Field(description="Креатива не было — показана запасная плашка")
    windows: list[BannerWindow]
    boxes: list[BannerBox] = Field(description="Видимые прямоугольники слоёв, канва 1080×1920")
    captions_zone: Rect | None = Field(description="Зона субтитров, канва 1080×1920")
    warnings: list[BannerWarning] = Field(description="Предупреждения баннера на момент рендера")
    # default_factory, а не default: в TS поле остаётся необязательным, старые моки приёмки не ломаются
    layout_manual: bool = Field(
        default_factory=bool, description="Последний рендер — с ручной раскладкой оператора"
    )
    captions_overlap: list[str] = Field(
        default_factory=list,
        description="Зоны текста, налезшие на баннер в последнем рендере: source_zone | title_zone | …",
    )


class ReviewQc(ApiModel):
    passed: bool
    checks: list[QcCheckResult]


class SimilarityInfo(ApiModel):
    nearest_job_id: int | None
    score: float | None
    method: Literal["dhash"]


class PreviousAttempt(ApiModel):
    job_id: int
    decision: ReviewDecision
    reject_reason: RejectReason | None
    comment: str | None


class ReviewSourceInfo(ApiModel):
    """Источник клипа и права: что показать рядом с плеером и что обязано попасть в описание."""

    source_id: int
    title: str
    start_sec: float | None
    end_sec: float | None
    rights_type: RightsType
    owner_name: str
    attribution_text: str | None
    license: str | None
    watermark_suspect: bool


class ReviewAuthorScript(ApiModel):
    """Авторский слой клипа: текст (реплики и заголовок), откуда он и чем озвучен."""

    mode: AuthorScriptMode | None
    label: str | None
    needs_operator_edit: bool
    blocked: bool
    script: AuthorScriptBody | None
    issues: list[AuthorScriptIssue]
    voice_kinds: list[AuthorVoiceKind] = Field(description="tts | operator | mock по репликам")


class ReviewContext(ApiModel):
    job: ReviewJobInfo
    media: ReviewMedia
    script: ReviewScript
    banner: ReviewBanner
    scene_cuts_sec: list[float]
    qc: ReviewQc | None
    flags: list[ReviewFlag]
    similarity: SimilarityInfo | None
    cost_usd: float
    previous_attempts: list[PreviousAttempt]
    source: ReviewSourceInfo | None = Field(default=None, description="Только нарезка")
    author_script: ReviewAuthorScript | None = Field(default=None, description="Только нарезка")
    originality: OriginalityReportOut | None = Field(
        default=None, description="Нарезка: отчёт об оригинальности финального рендера"
    )


class ReviewRequest(RequestModel):
    """Решение по ролику. `undo` — отдельным эндпоинтом `/review/undo`.

    Конфликт (уже решено в другом канале) — 409 `job_state_conflict` c `details.decided_by`, `decided_via`.
    """

    decision: Literal["approve", "rework", "reject", "override_qc"]
    reject_reason: RejectReason | None = Field(default=None, description="Обязательна при reject")
    rework_from_stage: JobStage | None = None
    auto_restart: bool = True
    comment: str | None = Field(default=None, max_length=2000)
    expected_status: Literal["pending_review", "qc_failed"]
