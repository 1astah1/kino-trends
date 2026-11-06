"""Источники нарезки: приём с правами, карточка с прогрессом, медиа студии (§5.2, §5.4 Source, §6.3.1).

Владелец реализации — M5-A (`api/routers/sources.py`, тег `sources`). Движок уже есть:
`clipping/source_ingest.py` (права, ingest_local/ingest_url, подготовка),
`pipeline/{proxy,peaks,storyboard}.py`, `providers/asr/*`. Каталог источника —
`MEDIA_ROOT/<sources.media_dir>` (по умолчанию `sources/<id>`), там же `source.json` движка.

**Нет прав — нет источника.** `SourceRightsIn` проверяется тем же `source_ingest.validate_rights`, что
и в CLI (`scripts/clip_local.py`): без записи прав — 422; `cc_by` и лицензии без `attribution_text` —
422; CC BY-SA, любые NC и ND (в `license`, `license_url`, `attribution_text`, для cc_by — и в
`proof_note`) — 422; `valid_until` в прошлом — 422. Ответ — `validation_error`, у ошибки
`type = "rights_rejected"`, в `msg` перечислены коды (`license_incompatible`, `attribution_required`,
`rights_expired`, `rights_invalid`). `rights_type: "licensed"` принимается как `licensed_written`,
ключ `owner` — как `owner_name`.

Эндпоинты:

* `POST /sources/from-upload` `SourceFromUpload` → 202 `Source` · 404 upload_not_found
  · 409 upload_not_complete | upload_consumed · 422;
* `POST /sources/from-url` `SourceFromUrl` → 202 `Source` · 422 unsupported_url | права;
* `GET /sources` `?status&q&niche_id&origin&limit&offset` → `SourcePage`;
* `GET /sources/{source_id}` → `Source` (статус, прогресс шагов, место в тяжёлой очереди, URL медиа);
* `PATCH /sources/{source_id}` `SourcePatch` → `Source`; `DELETE /sources/{source_id}?purge_files=` → 204;
* `GET, PUT /sources/{source_id}/rights` `SourceRightsIn` → `SourceRights` (аудит);
* `POST /sources/{source_id}/reprocess` `SourceReprocess` → 202 `TaskAccepted`;
* `GET /sources/{source_id}/transcript` `?format=json|srt|vtt` → `SourceTranscript` или текст;
* `GET /sources/{source_id}/media/proxy`, `/media/audio` → mp4 / m4a (Range);
* `GET /sources/{source_id}/peaks` → `SourcePeaks` (файл peaks.json как есть);
* `GET /sources/{source_id}/storyboard` → `SourceStoryboard`; `/storyboard/{sheet_idx}` → jpeg;
* `GET /sources/{source_id}/scenes` `?start_sec&end_sec` → `SourceScenes`.

Отбор кандидатов, кандидаты и clip-job — `api/schemas/clipping.py` (тег `clipping`, M5-B).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import AliasChoices, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from autoshorts.api.schemas.common import ApiModel, PageMeta, ProviderKey, RequestModel, Slug
from autoshorts.api.schemas.uploads import UPLOAD_ID_PATTERN
from autoshorts.domain.enums import (
    AllowlistStatus,
    DeferReason,
    RightsType,
    SourceOrigin,
    SourceStatus,
)

__all__ = [
    "LANGUAGE_PATTERN",
    "RIGHTS_WARNING_LABELS",
    "SELECTION_MODE_LABELS",
    "SelectionMode",
    "SelectorChoice",
    "SceneSpan",
    "Source",
    "SourceFromUpload",
    "SourceFromUrl",
    "SourceMedia",
    "SourceOptions",
    "SourcePage",
    "SourcePatch",
    "SourcePeaks",
    "SourcePipelineStep",
    "SourcePlatformInfo",
    "SourceReprocess",
    "SourceReprocessStep",
    "SourceRights",
    "SourceRightsIn",
    "SourceRow",
    "SourceScenes",
    "SourceSelectionInfo",
    "SourceStep",
    "SourceStepStatus",
    "SourceStoryboard",
    "SourceTranscriptInfo",
    "SourceWatermark",
    "RightsWarning",
    "WatermarkSeverity",
]

LANGUAGE_PATTERN = r"^[a-z]{2}$"
HTTP_URL_PATTERN = r"^https?://\S{3,2000}$"

# ---------------------------------------------------------------- права


class SourceRightsIn(RequestModel):
    """Запись прав (таблица `source_rights`) — обязательна при создании источника.

    Поля — это `source_ingest.RightsRecord` движка; проверка — `source_ingest.validate_rights`.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)

    rights_type: RightsType = Field(
        description="own | licensed_written | licensed_platform | cc_by | public_domain "
        "(`licensed` = licensed_written)"
    )
    owner_name: str = Field(
        min_length=1,
        max_length=300,
        validation_alias=AliasChoices("owner_name", "owner"),
        description="Правообладатель (ключ `owner` тоже принимается)",
    )
    proof_note: str = Field(
        min_length=3, max_length=4000, description="Чем подтверждено: договор, лицензия на странице, дата"
    )
    owner_channel_url: str | None = Field(default=None, max_length=2000)
    proof_upload_id: str | None = Field(
        default=None,
        pattern=UPLOAD_ID_PATTERN,
        description="Скан договора или скриншот лицензии: tus-загрузка с purpose=rights_doc",
    )
    allowlist_status: AllowlistStatus | None = Field(
        default=None, description="Content ID allowlist; по умолчанию own → not_needed, иначе unknown"
    )
    attribution_text: str | None = Field(
        default=None,
        max_length=1000,
        description="Обязательна, кроме own: автор, название, источник, лицензия, изменения",
    )
    license: str | None = Field(default=None, max_length=200, description="Например «CC BY 4.0»")
    license_url: str | None = Field(default=None, max_length=2000)
    valid_until: date | None = Field(default=None, description="Срок действия прав (лицензии)")

    @field_validator("rights_type", mode="before")
    @classmethod
    def _rights_alias(cls, v: Any) -> Any:
        if isinstance(v, str):
            from autoshorts.clipping.source_ingest import RIGHTS_ALIASES

            key = v.strip().lower()
            return RIGHTS_ALIASES.get(key, key.replace("-", "_"))
        return v

    @field_validator(
        "owner_channel_url", "attribution_text", "license", "license_url", "proof_upload_id", mode="before"
    )
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:
        return None if isinstance(v, str) and not v.strip() else v

    @model_validator(mode="after")
    def _engine_rules(self) -> SourceRightsIn:
        from autoshorts.clipping.source_ingest import RightsError

        try:
            self.validate_with_engine()
        except RightsError as exc:
            detail = "; ".join(exc.details) if exc.details else str(exc)
            raise PydanticCustomError(
                "rights_rejected",
                "Права не приняты ({codes}): {detail}",
                {"codes": ",".join(exc.codes), "detail": detail},
            ) from None
        return self

    def engine_record(self) -> dict[str, Any]:
        """Словарь для `source_ingest.validate_rights` / `RightsRecord` (без `proof_upload_id`)."""
        data = self.model_dump(mode="json", exclude={"proof_upload_id"})
        return {k: v for k, v in data.items() if v is not None}

    def validate_with_engine(self, *, today: date | None = None) -> tuple[Any, list[str]]:
        """(RightsRecord, warnings) движка; RightsError, если права надо отклонить."""
        from autoshorts.clipping.source_ingest import validate_rights

        return validate_rights(self.engine_record(), today=today)


RIGHTS_WARNING_LABELS: dict[str, str] = {
    "content_id_allowlist_not_confirmed": "Канал не подтверждён в allowlist Content ID правообладателя",
    "author_share_gate_applies": "Чужие права: нужен авторский вклад не меньше 30%",
    "cc_by_attribution_in_description": "CC BY: атрибуция обязательна в описании ролика",
    "license_version_unknown": "Не указана версия лицензии CC BY",
    "license_not_recognized_as_cc_by": "Лицензия не распознана как CC BY — проверьте вручную",
    "public_domain_check_embedded_music": "Общественное достояние: проверьте, нет ли внутри чужой музыки",
    "rights_expire_within_30_days": "Права истекают в ближайшие 30 дней",
    "platform_license_mismatch": "Лицензия на площадке не совпадает с заявленной",
    "platform_license_missing": "На площадке не указана лицензия",
    "owner_channel_differs_from_uploader": "Канал загрузившего не совпадает с правообладателем",
}
"""Предупреждения `validate_rights` / `platform_rights_warnings` движка → подпись для UI."""


class RightsWarning(ApiModel):
    code: str = Field(description="Код предупреждения движка (латиница), см. RIGHTS_WARNING_LABELS")
    label: str = Field(description="Подпись по-русски")


class SourceRights(ApiModel):
    rights_type: RightsType
    owner_name: str
    owner_channel_url: str | None
    proof_note: str
    proof_file_url: str | None = Field(description="`/api/v1/...`, если приложен документ")
    allowlist_status: AllowlistStatus
    attribution_text: str | None
    license: str | None
    license_url: str | None
    valid_until: date | None
    warnings: list[RightsWarning]
    gate_applies: bool = Field(description="Чужие права: гейт author_share ≥ clip_author_share_min")
    recorded_by: int | None
    recorded_at: datetime
    updated_at: datetime | None


# ---------------------------------------------------------------- опции и приём


class SelectorChoice(StrEnum):
    """Кто выбирает моменты. `auto` — LLM, если настроен ключ (LLM_API_KEY), иначе эвристика."""

    AUTO = "auto"
    LLM = "llm"
    HEURISTIC = "heuristic"


class SelectionMode(StrEnum):
    """Кто фактически выбрал (`SelectionResult.mode` движка). UI обязан подписывать mock и heuristic."""

    LLM = "llm"
    MOCK = "mock"
    HEURISTIC = "heuristic"


SELECTION_MODE_LABELS: dict[str, str] = {
    "llm": "LLM",
    "mock": "Заглушка LLM (mock) — только для проверки",
    "heuristic": "Эвристика без LLM — ключ LLM не задан",
}


class SourceOptions(RequestModel):
    """Опции отбора и распознавания (`sources.options`, `SelectionConfig.from_options` движка)."""

    asr_provider: ProviderKey | None = Field(
        default=None, description="Ключ провайдера ASR; по умолчанию — default из /providers"
    )
    selector: SelectorChoice = SelectorChoice.AUTO
    target_clip_sec: float = Field(default=30.0, ge=10, le=58)
    min_clip_sec: float = Field(default=15.0, ge=5, le=58)
    max_clip_sec: float = Field(default=58.0, ge=10, le=60, description="VK жёстко ≤ 58 с")
    max_candidates: int = Field(default=12, ge=1, le=30, description="Сколько моментов показать")
    target_count: int = Field(default=6, ge=1, le=12, description="Сколько клипов нужно (шортлист × 2)")
    detail_pass: bool = Field(default=True, description="Второй проход LLM по шортлисту с контекстом ±60 с")
    tighten_pauses: bool = Field(default=False, description="Джамп-каты по паузам; для музыки не включать")

    @model_validator(mode="after")
    def _lengths(self) -> SourceOptions:
        if not (self.min_clip_sec <= self.target_clip_sec <= self.max_clip_sec):
            raise PydanticCustomError(
                "clip_length_order", "Нужно min_clip_sec ≤ target_clip_sec ≤ max_clip_sec"
            )
        return self


class _SourceCreateBase(RequestModel):
    rights: SourceRightsIn = Field(description="Обязательно: без прав источник не создаётся")
    niche_id: Slug | None = None
    language: str = Field(default="ru", pattern=LANGUAGE_PATTERN)
    options: SourceOptions | None = None


class SourceFromUpload(_SourceCreateBase):
    upload_id: str = Field(pattern=UPLOAD_ID_PATTERN, description="tus-загрузка с purpose=source")
    title: str = Field(min_length=1, max_length=300)


class SourceFromUrl(_SourceCreateBase):
    """Только своё или лицензированное видео. Звук сначала (audio-first), 1080p — секциями на clip_fetch."""

    url: str = Field(pattern=HTTP_URL_PATTERN, max_length=2000)
    title: str | None = Field(default=None, min_length=1, max_length=300, description="Пусто — с площадки")


class SourcePatch(RequestModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    niche_id: Slug | None = None
    original_retain_until: date | None = None


class SourceReprocessStep(StrEnum):
    PREPARE = "prepare"
    TRANSCRIBE = "transcribe"
    SELECT = "select"
    SCENES = "scenes"


class SourceReprocess(RequestModel):
    steps: list[SourceReprocessStep] = Field(min_length=1, max_length=4)
    asr_provider: ProviderKey | None = None

    @field_validator("steps")
    @classmethod
    def _unique(cls, v: list[SourceReprocessStep]) -> list[SourceReprocessStep]:
        if len(set(v)) != len(v):
            raise PydanticCustomError("duplicate_steps", "Шаги не должны повторяться")
        return v


# ---------------------------------------------------------------- карточка


class SourceStep(StrEnum):
    """Шаги конвейера источника (§6.3.1) → задачи source_download | source_prepare |
    source_transcribe_chunk × N / source_transcribe_cloud | source_select | source_scenes."""

    DOWNLOAD = "download"
    PREPARE = "prepare"
    TRANSCRIBE = "transcribe"
    SELECT = "select"
    SCENES = "scenes"


class SourceStepStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class SourcePipelineStep(ApiModel):
    step: SourceStep
    status: SourceStepStatus
    task_id: int | None
    progress: float | None = Field(description="0–100")
    chunks_done: int | None = Field(default=None, description="ASR по кускам")
    chunks_total: int | None = None
    queue_position: int | None = Field(default=None, description="Место в тяжёлой очереди (1 — следующий)")
    defer_reason: DeferReason | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class SourceMedia(ApiModel):
    """Непрозрачные URL `/api/v1/...`; null — файла ещё нет."""

    proxy_url: str | None
    audio_url: str | None
    peaks_url: str | None
    storyboard_url: str | None
    transcript_url: str | None


class SourceTranscriptInfo(ApiModel):
    provider_key: str
    model: str | None
    language: str
    words_count: int
    sentences_count: int
    duration_sec: float | None
    rtfx: float | None
    cost_usd: float
    created_at: datetime


class SourceSelectionInfo(ApiModel):
    """Итог последнего отбора (`sources.selection`, `SelectionResult.to_dict()` без кандидатов)."""

    mode: SelectionMode
    label: str = Field(description="Подпись для UI: «LLM umka/…», «Эвристика без LLM — ключ не задан»")
    llm_configured: bool = Field(description="Ключ LLM задан (LLM_API_KEY или секрет провайдера)")
    fallback: bool = Field(description="LLM был выбран, но упал — кандидаты от эвристики")
    params: SourceOptions
    n_candidates: int
    n_blocked: int
    n_requests: int
    cost_usd: float
    warnings: list[str]
    task_id: int | None
    finished_at: datetime | None


class WatermarkSeverity(StrEnum):
    OK = "ok"
    HINT = "hint"
    WARNING = "warning"


class SourceWatermark(ApiModel):
    """Проверка чужих логотипов на подготовке (`clipping/watermark_check.py`). Маскировки нет."""

    suspect: bool
    severity: WatermarkSeverity
    detail: str | None = Field(description="Что найдено: логотип и счёт, угол статичного оверлея")
    frames_checked: int | None
    logos_checked: int | None


class SourcePlatformInfo(ApiModel):
    """Сводка площадки для URL-источника (`source_ingest.platform_summary`): сверка лицензии."""

    extractor: str | None
    video_id: str | None
    webpage_url: str | None
    uploader: str | None
    license: str | None
    upload_date: str | None = Field(description="YYYYMMDD, как отдаёт yt-dlp")


class Source(ApiModel):
    id: int
    title: str
    origin: SourceOrigin
    origin_url: str | None
    status: SourceStatus
    error: str | None
    language: str
    niche_id: str | None
    duration_sec: float | None
    width: int | None
    height: int | None
    fps: float | None
    video_codec: str | None
    has_audio: bool | None
    file_bytes: int | None
    sha256: str | None
    rights: SourceRights
    watermark_suspect: bool
    watermark: SourceWatermark | None
    platform: SourcePlatformInfo | None
    options: SourceOptions
    pipeline: list[SourcePipelineStep]
    progress: float | None = Field(description="Общий прогресс до ready, 0–100")
    media: SourceMedia
    transcript: SourceTranscriptInfo | None
    selection: SourceSelectionInfo | None
    candidates_count: int
    jobs_count: int
    original_retain_until: date | None
    proxy_retain_until: date | None
    created_by: int | None
    created_at: datetime
    updated_at: datetime


class SourceRow(ApiModel):
    id: int
    title: str
    origin: SourceOrigin
    status: SourceStatus
    error: str | None
    language: str
    niche_id: str | None
    duration_sec: float | None
    rights_type: RightsType
    watermark_suspect: bool
    current_step: SourceStep | None
    progress: float | None
    queue_position: int | None
    candidates_count: int
    jobs_count: int
    selection_mode: SelectionMode | None
    created_at: datetime
    updated_at: datetime


class SourcePage(PageMeta):
    items: list[SourceRow]


# ---------------------------------------------------------------- медиа студии


class SourcePeaks(ApiModel):
    """`peaks.json` (`pipeline/peaks.py`), грузится в `WaveSurfer.create({peaks, duration})` как есть."""

    version: int
    format: Literal["wavesurfer"]
    duration: float
    sample_rate: int
    points_per_min: int
    samples_per_point: int
    length: int
    channels: int
    max: float
    peaks: list[list[float]]


class SourceStoryboard(ApiModel):
    """Раскадровка (`pipeline/storyboard.py`): кадр каждые `interval_sec`, листы cols×rows плиток."""

    interval_sec: float
    tile_w: int
    tile_h: int
    cols: int
    rows: int
    count: int = Field(description="Всего плиток")
    sheets: list[str] = Field(description="URL листов `/api/v1/sources/{id}/storyboard/{sheet_idx}`")


class SceneSpan(ApiModel):
    start_sec: float
    end_sec: float


class SourceScenes(ApiModel):
    items: list[SceneSpan]
    coverage: list[list[float]] = Field(description="Какие отрезки [s, e] источника уже просчитаны")
