"""Ролики: создание, деталь, стадии, сцены, история.

docs/ARCHITECTURE.md §5.2 «Ролики», §5.4 JobCreate, Job.

Опции clip-job (`ClipJobOptions`, `AuthorLayerInput`, `ReframeOptions`) живут в `api/schemas/clipping.py`
(контракт M5) и реэкспортируются отсюда. У нарезки в детали ролика есть `author_script` (кратко) и
`originality` — отчёт об оригинальности финального рендера (OWNER-DECISIONS п. 5).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, RootModel

from autoshorts.api.schemas.clipping import (  # AuthorLayerInput, ReframeOptions — реэкспорт
    AuthorLayerInput,  # noqa: F401
    AuthorScriptSummary,
    ClipJobOptions,
    OriginalityReportOut,
    OriginalityVerdict,
    ReframeOptions,  # noqa: F401
)
from autoshorts.api.schemas.common import ApiModel, PageMeta, RequestModel, Slug
from autoshorts.domain.enums import (
    DecidedVia,
    JobKind,
    JobStage,
    JobStatus,
    PackageStatus,
    Platform,
    PublicationStatus,
    QcCheck,
    RejectReason,
    ReviewDecision,
    TaskStatus,
)

# ---------------------------------------------------------------- создание


class GenerateOptions(RequestModel):
    """Переопределения провайдеров и формы ролика; пусто — из ниши и профиля канала."""

    llm_provider: str | None = None
    tts_provider: str | None = None
    image_provider: str | None = None
    scene_count: int | None = Field(default=None, ge=2, le=20)
    duration_target_sec: list[float] | None = Field(
        default=None, min_length=2, max_length=2, description="[min, max], секунды"
    )


class ClipSourceRange(RequestModel):
    source_id: int
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)


class JobCreateGenerate(RequestModel):
    kind: Literal["generate"]
    channel_id: Slug
    topic: str | None = Field(default=None, max_length=300)
    trend_brief_id: int | None = None
    campaign_id: int | None = None
    banner_variant_id: int | None = None
    scheduled_for: datetime | None = None
    priority: int = Field(default=100, ge=0, le=1000)
    options: GenerateOptions = Field(default_factory=GenerateOptions)
    start: bool = True


class JobCreateClip(ClipJobOptions):
    kind: Literal["clip"]
    clip_candidate_id: int | None = Field(default=None, description="Либо кандидат, либо `source`")
    source: ClipSourceRange | None = None


class JobCreate(RootModel[Annotated[JobCreateGenerate | JobCreateClip, Field(discriminator="kind")]]):
    """Тело `POST /jobs`: generate или clip, различаются полем `kind`."""


class JobBatchCreate(RequestModel):
    kind: Literal["generate"] = "generate"
    channel_id: Slug
    topics: list[str] = Field(min_length=1, max_length=20)
    campaign_id: int | None = None
    banner_variant_id: int | None = None
    start: bool = True


class JobPatch(RequestModel):
    """Правка до старта: только в статусах draft | queued | rework."""

    topic: str | None = Field(default=None, max_length=300)
    script_override: dict[str, Any] | None = None
    banner_variant_id: int | None = None
    author_layer: AuthorLayerInput | None = None
    priority: int | None = Field(default=None, ge=0, le=1000)


class JobRetry(RequestModel):
    from_stage: JobStage | None = None


class JobCloneOverrides(RequestModel):
    channel_id: Slug | None = None
    topic: str | None = Field(default=None, max_length=300)
    campaign_id: int | None = None
    banner_variant_id: int | None = None
    options: GenerateOptions | None = None
    author_layer: AuthorLayerInput | None = None
    priority: int | None = Field(default=None, ge=0, le=1000)
    start: bool = False


class JobClone(RequestModel):
    overrides: JobCloneOverrides = Field(default_factory=JobCloneOverrides)


class JobAdapt(RequestModel):
    """Перерендер под другую площадку (VK): дочерний job со стадии render."""

    channel_id: Slug


# ---------------------------------------------------------------- список


class JobRow(ApiModel):
    id: int
    video_uid: str
    kind: JobKind
    status: JobStatus
    stage: JobStage | None
    channel_id: str
    niche_id: str
    product_id: str | None
    campaign_id: int | None
    banner_variant_id: int | None
    topic: str | None
    priority: int
    progress: float | None = Field(description="Прогресс текущей задачи, 0–100")
    duration_sec: float | None
    cost_usd: float
    author_share: float | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    originality_verdict: OriginalityVerdict | None = Field(
        default=None, description="Нарезка: итог отчёта об оригинальности (null — ещё не считался)"
    )


class JobPage(PageMeta):
    items: list[JobRow]


class StageCount(ApiModel):
    status: JobStatus
    stage: JobStage | None
    count: int


class StageCountList(ApiModel):
    items: list[StageCount]


# ---------------------------------------------------------------- деталь


class ChannelRef(ApiModel):
    id: str
    title: str | None
    platform: Platform


class BannerVariantRef(ApiModel):
    id: int
    code: str
    name: str


class QcCheckResult(ApiModel):
    code: QcCheck
    ok: bool
    measured: str | None = Field(description="Измерено, для человека: «-14.2 LUFS»")
    threshold: str | None = Field(description="Порог, для человека: «-14±1.5»")


class QcWarning(ApiModel):
    code: QcCheck
    detail: str | None
    job_id: int | None = None
    similarity: float | None = None


class JobQc(ApiModel):
    passed: bool
    failed_checks: list[QcCheck]
    checks: list[QcCheckResult]
    warnings: list[QcWarning]


class JobScript(ApiModel):
    hook_text: str | None
    body_text: str | None
    cta_text: str | None
    voice_cta_text: str | None
    voiceover_text: str | None


class WordTiming(ApiModel):
    word: str
    start_sec: float
    end_sec: float


class JobStageInfo(ApiModel):
    stage: JobStage
    status: TaskStatus
    task_id: int | None
    attempts: int
    duration_ms: int | None
    cost_usd: float
    error: str | None


class JobMedia(ApiModel):
    """Непрозрачные URL `/api/v1/...`; null — файла ещё нет."""

    preview_url: str | None
    final_url: str | None
    poster_url: str | None


class ReviewDecisionOut(ApiModel):
    id: int
    decision: ReviewDecision
    reject_reason: RejectReason | None
    rework_from_stage: JobStage | None
    comment: str | None
    decided_by: int | None
    decided_by_name: str | None
    decided_via: DecidedVia
    created_at: datetime


class JobPackageRef(ApiModel):
    id: int
    status: PackageStatus
    code: str = Field(description="Код пакета (трекинг)")


class JobPublicationRef(ApiModel):
    id: int
    platform: Platform
    url: str | None
    status: PublicationStatus


class JobSourceRef(ApiModel):
    source_id: int
    title: str
    clip_candidate_id: int | None
    start_sec: float | None
    end_sec: float | None


class Job(ApiModel):
    id: int
    video_uid: str
    kind: JobKind
    status: JobStatus
    stage: JobStage | None
    channel: ChannelRef
    niche_id: str
    niche_version: int | None
    product_id: str | None
    campaign_id: int | None
    banner_variant: BannerVariantRef | None
    topic: str | None
    priority: int
    scheduled_for: datetime | None
    duration_sec: float | None
    cost_usd: float
    author_share: float | None
    qc: JobQc | None
    script: JobScript | None
    words: list[WordTiming] | None = Field(description="Только при `?include=words`")
    stages: list[JobStageInfo]
    media: JobMedia
    reviews: list[ReviewDecisionOut]
    package: JobPackageRef | None
    publications: list[JobPublicationRef]
    source: JobSourceRef | None
    parent_job_id: int | None
    error_class: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    author_script: AuthorScriptSummary | None = Field(
        default=None, description="Нарезка: режим и состояние авторского текста (полностью — /author-script)"
    )
    originality: OriginalityReportOut | None = Field(
        default=None, description="Нарезка: отчёт об оригинальности финального рендера (стадия qc)"
    )


class JobList(ApiModel):
    items: list[Job]


# ---------------------------------------------------------------- сцены, таймлайн, история, похожие


class Scene(ApiModel):
    idx: int
    prompt: str | None
    image_url: str | None
    seed: int | None
    duration_sec: float | None


class SceneList(ApiModel):
    items: list[Scene]


class SceneRegenerate(RequestModel):
    prompt: str | None = Field(default=None, max_length=2000)


class JobTaskItem(ApiModel):
    stage: JobStage | None
    status: TaskStatus
    task_id: int
    attempts: int
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    cost_usd: float
    error: str | None
    progress: float | None


class JobTaskList(ApiModel):
    items: list[JobTaskItem]


class JobHistoryItem(ApiModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    at: datetime
    kind: Literal["status", "review", "task", "edit"]
    from_: str | None = Field(alias="from", description="Было (статус/стадия)")
    to: str | None
    by: str | None = Field(description="Кто: display_name пользователя, bot, system")
    via: DecidedVia | None
    comment: str | None


class JobHistory(ApiModel):
    items: list[JobHistoryItem]


class SimilarJob(ApiModel):
    job_id: int
    video_uid: str
    similarity: float
    channel_id: str


class SimilarJobList(ApiModel):
    items: list[SimilarJob]
