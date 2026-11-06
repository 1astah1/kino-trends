"""Нарезка: кандидаты, повторный отбор, clip-job, авторский слой, голос автора, отчёт об оригинальности.

docs/ARCHITECTURE.md §5.2 «Загрузки, источники, нарезка», §5.4 ClipCandidate/JobCreate(clip), §6.3.3–§6.3.9;
docs/OWNER-DECISIONS.md п. 5: клипы должны зарабатывать рекомендации YouTube настоящим авторским слоем
(хук голосом, вставки-комментарии, вывод, свой заголовок, двойные субтитры) и проходить отчёт об
оригинальности перед заливкой. Технической маскировки чужого контента нет.

Формы здесь — зеркало движка `clipping/**` (его не переписываем): `scoring.ClipCandidate.to_dict()`,
`selection.SelectionConfig`, `snapping.SnapResult`, `author.script.AuthorScript` и
`AuthorScriptResult.to_dict()`, `author.voice.VoiceTake`, `originality.OriginalityReport.to_dict()`,
`author.share.AuthorShare.to_dict()`. Совпадение сверяет `tests/contract/test_clipping_contract.py`.

Эндпоинты (тег `clipping`, реализация — M5-B: `api/routers/{clip_candidates,author_voice}.py`):

* `GET /sources/{source_id}/candidates` `?status&include_superseded` → `ClipCandidateList`
  (+ подпись режима отбора); `POST` того же пути `ClipCandidateCreate` (ручной) → 201 `ClipCandidate`;
* `POST /sources/{source_id}/select` `SourceSelectRequest` → 202 `TaskAccepted` (повторный отбор);
* `POST /sources/{source_id}/clips` `ClipBatchCreate` → `JobList` (пачка clip-job);
* `GET /clip-candidates/{candidate_id}` → `ClipCandidate`;
* `PATCH /clip-candidates/{candidate_id}` `ClipCandidatePatch` → `ClipCandidate` (снаппинг пересчитан);
* `POST /clip-candidates/{candidate_id}/jobs` `ClipJobOptions` → 201 `Job`
  · 409 clip_candidate_blocked | clip_candidate_in_use | source_not_ready · 423 budget_paused;
* `POST /clip-candidates/{candidate_id}/originality-estimate` `OriginalityEstimateRequest`
  → `OriginalityReportOut` (до рендера: индикатор 70/30 в студии);
* `GET /jobs/{job_id}/author-script` → `JobAuthorScript`;
  `PUT` `AuthorScriptPut` → `JobAuthorScript` · 409 version_conflict | job_state_conflict;
* `POST /jobs/{job_id}/author-script/draft` `AuthorScriptDraftRequest` → 202 `TaskAccepted`;
* `GET /jobs/{job_id}/author-voice` → `JobAuthorVoice`;
* `POST /jobs/{job_id}/author-voice` multipart `audio` (webm/ogg/wav ≤ 20 МБ) + `segment`
  → 202 `AuthorVoiceAccepted` · 413 · 415;
* `GET /jobs/{job_id}/author-voice/{segment}/audio` → аудио реплики (Range);
* `DELETE /jobs/{job_id}/author-voice/{segment}` → 204 (запись удалена, реплика озвучится TTS);
* `POST /jobs/{job_id}/originality/estimate` `OriginalityEstimateRequest` → `OriginalityReportOut`
  (дёшево, без рендера);
* `GET /jobs/{job_id}/originality` → `OriginalityReportOut` финального рендера · 404 originality_not_found.

Пути кандидатов взяты из постановки ведущего M5M3-0: в §5.2 они записаны как
`/sources/{source_id}/clip-candidates` и `/clip-candidates/{candidate_id}/create-job`.

Без ключа LLM отбор идёт эвристикой, авторский текст — шаблонным черновиком: оба режима приходят
в ответах (`origin`/`mode` + `label`), UI обязан показывать подпись, отчёт об оригинальности требует
переписать шаблон своими словами.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from autoshorts.api.schemas.common import ApiModel, RequestModel, Slug, TaskAccepted
from autoshorts.api.schemas.sources import SelectorChoice, SourceSelectionInfo
from autoshorts.clipping.author.script import (
    COLD_OPEN_MAX,
    INSERT_MAX,
    MAX_INSERTS,
    ON_SCREEN_MAX,
    OUTRO_MAX,
    TITLE_MAX,
)
from autoshorts.clipping.schemas import HookType
from autoshorts.domain.banner_schema import CANVAS_H, CANVAS_W, BannerLayerKind
from autoshorts.domain.enums import (
    AuthorLayerMode,
    AuthorVoiceSegment,
    AuthorVoiceSource,
    CandidateCreatedByKind,
    CandidateStatus,
    CaptionsMode,
    JobStatus,
    ReframeMode,
    RightsType,
)
from autoshorts.domain.settings_schema import CanvasSize, Rect

__all__ = [
    "AUTHOR_VOICE_MAX_BYTES",
    "AUTHOR_VOICE_MIME_TYPES",
    "BLOCKING_CANDIDATE_FLAGS",
    "CANDIDATE_ORIGIN_LABELS",
    "AuthorColdOpen",
    "AuthorCtaStyle",
    "AuthorInsert",
    "AuthorInsertMode",
    "AuthorLayerInput",
    "JobLayout",
    "LayoutBannerShift",
    "LayoutConflict",
    "LayoutMode",
    "LayoutOverride",
    "LayoutPut",
    "LayoutRect",
    "LayoutRectKind",
    "LayoutSnapshotOut",
    "LayoutTexts",
    "RenderedLayout",
    "AuthorOutro",
    "AuthorScriptBody",
    "AuthorScriptDraftRequest",
    "AuthorScriptIssue",
    "AuthorScriptIssueSeverity",
    "AuthorScriptMode",
    "AuthorScriptPut",
    "AuthorScriptSummary",
    "AuthorShareBreakdown",
    "AuthorVoiceAccepted",
    "AuthorVoiceKind",
    "AuthorVoiceSegmentState",
    "AuthorVoiceStatus",
    "AuthorVoiceTiming",
    "CandidateFlag",
    "CandidateOrigin",
    "CandidateSnap",
    "ClipBatchCreate",
    "ClipBatchItem",
    "ClipCandidate",
    "ClipCandidateCreate",
    "ClipCandidateList",
    "ClipCandidatePatch",
    "ClipJobOptions",
    "ForbiddenHit",
    "HookType",
    "JobAuthorScript",
    "JobAuthorVoice",
    "LlmScores",
    "OriginalityCheckCode",
    "OriginalityCheckOut",
    "OriginalityCheckStatus",
    "OriginalityEstimateRequest",
    "OriginalityReportOut",
    "OriginalityStage",
    "OriginalitySummary",
    "OriginalityVerdict",
    "ReframeOptions",
    "SourceSelectRequest",
]

# ---------------------------------------------------------------- кандидаты


class CandidateOrigin(StrEnum):
    """Откуда кандидат (`clip_candidates.origin`, `scoring.Origin` движка)."""

    LLM = "llm"
    MOCK = "mock"
    HEURISTIC = "heuristic"
    MANUAL = "manual"


CANDIDATE_ORIGIN_LABELS: dict[str, str] = {
    "llm": "Выбрано LLM",
    "mock": "Заглушка LLM (mock)",
    "heuristic": "Эвристика без LLM",
    "manual": "Отмечено вручную",
}


class CandidateFlag(StrEnum):
    """Флаги содержания: `clipping.schemas.ContentFlag` движка + `forbidden_phrase` (словарь ниши)."""

    MEDICAL_CLAIM = "medical_claim"
    GUARANTEE_CLAIM = "guarantee_claim"
    FINANCIAL_ADVICE = "financial_advice"
    LEGAL_ADVICE = "legal_advice"
    PROFANITY = "profanity"
    POLITICS = "politics"
    VIOLENCE = "violence"
    SEXUAL = "sexual"
    HATE = "hate"
    DANGEROUS_ACT = "dangerous_act"
    PERSONAL_DATA = "personal_data"
    ADVERTISING = "advertising"
    FORBIDDEN_PHRASE = "forbidden_phrase"


BLOCKING_CANDIDATE_FLAGS: frozenset[str] = frozenset({"medical_claim", "guarantee_claim", "forbidden_phrase"})
"""Блокируют кандидата до явной разблокировки оператором (§6.3.3); = BLOCKING_FLAGS + forbidden_phrase."""


class LlmScores(ApiModel):
    """Оценки LLM 0..10 (§6.3.3); у эвристики — её собственные прикидки в той же шкале."""

    hook: float
    standalone: float
    payoff: float
    emotion: float
    niche_fit: float
    novelty: float


class ForbiddenHit(ApiModel):
    phrase: str
    start: int = Field(description="Позиция в нормализованном тексте кандидата")
    end: int


class CandidateSnap(ApiModel):
    """Результат снаппинга (`snapping.snap_clip_to_words`): границы в тишине у слов."""

    w0: int = Field(description="Индекс первого слова клипа в транскрипте")
    w1: int = Field(description="Индекс последнего слова (включительно)")
    lead_sec: float = Field(description="Тишина перед первым словом")
    tail_sec: float = Field(description="Тишина после последнего слова")
    raw_start_sec: float | None = Field(description="Граница до снаппинга (предложение или клик оператора)")
    raw_end_sec: float | None


class ClipCandidate(ApiModel):
    id: int
    source_id: int
    start_sec: float
    end_sec: float
    duration_sec: float
    start_sid: int | None
    end_sid: int | None
    hook_sid: int | None
    start_word_idx: int | None
    end_word_idx: int | None
    snap: CandidateSnap | None
    final_score: float | None = Field(description="0..1 (§6.3.4)")
    trend_prior: float | None
    score_parts: dict[str, float] = Field(description="llm, feat, len_fit, trend")
    llm_scores: LlmScores | None = Field(description="Нет у ручных кандидатов")
    features: dict[str, Any] = Field(
        description="speech_rate_wps, dead_air_ratio, first_word_latency, ends_on_terminal_punct, n_words, …"
    )
    hook_type: HookType | None
    reason: str | None = Field(description="Почему момент хорош (why от LLM)")
    transcript_excerpt: str | None
    title: str | None
    hook_text: str | None
    content_flags: list[CandidateFlag]
    forbidden_hits: list[ForbiddenHit]
    unblocked_flags: list[CandidateFlag]
    blocked: bool = Field(description="Есть блокирующий флаг, не снятый оператором")
    block_reasons: list[CandidateFlag]
    needs_context: bool
    status: CandidateStatus
    created_by_kind: CandidateCreatedByKind
    origin: CandidateOrigin
    origin_label: str = Field(description="Подпись по-русски (эвристику и mock UI показывает всегда)")
    notes: str | None
    job_id: int | None = Field(description="Последний clip-job кандидата")
    superseded: bool = Field(description="Из прошлого прогона отбора")
    created_at: datetime
    updated_at: datetime


class ClipCandidateList(ApiModel):
    items: list[ClipCandidate]
    selection: SourceSelectionInfo | None = Field(description="Как получены автоматические кандидаты")


class _WordOrSecRange(RequestModel):
    """Граница — индексом слова транскрипта (клик по слову) или секундой; снаппинг — по `snap`."""

    start_word_idx: int | None = Field(default=None, ge=0)
    end_word_idx: int | None = Field(default=None, ge=0)
    start_sec: float | None = Field(default=None, ge=0)
    end_sec: float | None = Field(default=None, gt=0)
    snap: bool = Field(default=True, description="Увести границы в тишину у слов (§6.3.5)")

    @model_validator(mode="after")
    def _one_way_per_edge(self) -> _WordOrSecRange:
        if self.start_word_idx is not None and self.start_sec is not None:
            raise PydanticCustomError("edge_twice", "Начало задаётся либо start_word_idx, либо start_sec")
        if self.end_word_idx is not None and self.end_sec is not None:
            raise PydanticCustomError("edge_twice", "Конец задаётся либо end_word_idx, либо end_sec")
        if (
            self.start_word_idx is not None
            and self.end_word_idx is not None
            and self.end_word_idx < self.start_word_idx
        ):
            raise PydanticCustomError("edge_order", "end_word_idx раньше start_word_idx")
        if self.start_sec is not None and self.end_sec is not None and self.end_sec <= self.start_sec:
            raise PydanticCustomError("edge_order", "end_sec должен быть больше start_sec")
        return self


class ClipCandidateCreate(_WordOrSecRange):
    """Ручной кандидат: обе границы обязательны (словом или секундой)."""

    title: str | None = Field(default=None, max_length=90)
    hook_text: str | None = Field(default=None, max_length=140)
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _both_edges(self) -> ClipCandidateCreate:
        if self.start_word_idx is None and self.start_sec is None:
            raise PydanticCustomError("edge_missing", "Нужно начало: start_word_idx или start_sec")
        if self.end_word_idx is None and self.end_sec is None:
            raise PydanticCustomError("edge_missing", "Нужен конец: end_word_idx или end_sec")
        return self


class ClipCandidatePatch(_WordOrSecRange):
    """Правка границ (пересчитывается снаппинг и признаки), статуса, текстов, флагов, заметок.

    `status`: proposed | approved | dismissed (`used` ставит только создание clip-job).
    `unblock_flags` снимают блок (аудит), `block_flags` возвращают.
    """

    status: CandidateStatus | None = None
    title: str | None = Field(default=None, max_length=90)
    hook_text: str | None = Field(default=None, max_length=140)
    notes: str | None = Field(default=None, max_length=2000)
    unblock_flags: list[CandidateFlag] | None = None
    block_flags: list[CandidateFlag] | None = None

    @model_validator(mode="after")
    def _no_used(self) -> ClipCandidatePatch:
        if self.status == CandidateStatus.USED:
            raise PydanticCustomError("status_used", "Статус used ставится созданием clip-job")
        return self


class SourceSelectRequest(RequestModel):
    """Повторный отбор с параметрами (`selection.select_clips`). Прежние автоматические кандидаты
    без clip-job получают `superseded`; ручные и уже взятые в работу остаются."""

    selector: SelectorChoice = SelectorChoice.AUTO
    target_clip_sec: float | None = Field(default=None, ge=10, le=58)
    min_clip_sec: float | None = Field(default=None, ge=5, le=58)
    max_clip_sec: float | None = Field(default=None, ge=10, le=60)
    max_candidates: int | None = Field(default=None, ge=1, le=30)
    target_count: int | None = Field(default=None, ge=1, le=12)
    detail_pass: bool | None = None
    tighten_pauses: bool | None = None
    niche_id: Slug | None = Field(default=None, description="Словарь запретов и профиль ниши для отбора")
    save_as_default: bool = Field(default=False, description="Записать параметры в sources.options")


# ---------------------------------------------------------------- авторский слой (§6.3.8)


class AuthorInsertMode(StrEnum):
    FREEZE_ZOOM = "freeze_zoom"
    OVER_DUCKED = "over_ducked"


class AuthorCtaStyle(StrEnum):
    QUESTION = "question"
    LOOP_BACK = "loop_back"
    OFFER = "offer"


class AuthorColdOpen(RequestModel):
    vo_text: str = Field(default="", max_length=COLD_OPEN_MAX, description="Хук голосом автора")
    on_screen: str = Field(default="", max_length=ON_SCREEN_MAX, description="Тезис на экране")
    duck_source: bool = Field(default=True, description="Голос поверх приглушённого исходника")


class AuthorInsert(RequestModel):
    after_sid: int = Field(ge=0, description="Вставка после этого предложения транскрипта")
    mode: AuthorInsertMode = AuthorInsertMode.FREEZE_ZOOM
    vo_text: str = Field(default="", max_length=INSERT_MAX, description="Комментарий автора")
    on_screen: str = Field(default="", max_length=ON_SCREEN_MAX)


class AuthorOutro(RequestModel):
    vo_text: str = Field(default="", max_length=OUTRO_MAX, description="Вывод или вопрос зрителю")
    cta_style: AuthorCtaStyle = AuthorCtaStyle.QUESTION


class AuthorScriptBody(RequestModel):
    """Сценарий авторского слоя = `author.script.AuthorScript` движка. Пустой `vo_text` — части нет.

    Лимиты — те же константы движка; движок молча обрезает длинный текст LLM, API отвечает 422.
    """

    cold_open: AuthorColdOpen = Field(default_factory=AuthorColdOpen)
    inserts: list[AuthorInsert] = Field(default_factory=list, max_length=MAX_INSERTS)
    outro: AuthorOutro = Field(default_factory=AuthorOutro)
    on_screen_title: str = Field(default="", max_length=TITLE_MAX, description="Свой заголовок")

    @model_validator(mode="after")
    def _unique_inserts(self) -> AuthorScriptBody:
        sids = [i.after_sid for i in self.inserts]
        if len(set(sids)) != len(sids):
            raise PydanticCustomError("insert_duplicate_sid", "Две вставки после одного предложения")
        return self


# ---------------------------------------------------------------- раскладка ролика (редактор приёмки)


class LayoutMode(StrEnum):
    """`auto` — расчёт рендера как есть (поля ниже игнорируются); `manual` — ручные позиции оператора."""

    AUTO = "auto"
    MANUAL = "manual"


class LayoutBannerShift(RequestModel):
    """Сдвиг и масштаб блока оффера (креатив + код + erid) относительно авто-раскладки варианта."""

    dx: int = Field(default=0, ge=-CANVAS_W, le=CANVAS_W, description="Сдвиг по X, px канвы 1080×1920")
    dy: int = Field(default=0, ge=-CANVAS_H, le=CANVAS_H, description="Сдвиг по Y, px канвы")
    scale: float = Field(default=1.0, ge=0.25, le=2.0, description="Масштаб блока вокруг его центра")


class LayoutOverride(RequestModel):
    """Ручная раскладка ролика (`author_layer.layout`), пиксели канвы 1080×1920.

    Рендер сдвигает блок баннера и вписывает его в safe zone площадки, субтитры ставит низом в
    `captions_bottom`, заголовок и тезисы — верхом в `title_top` / `onscreen_top`; затем, как и в
    авто-режиме, уводит заголовок и тезисы из-под баннера и предупреждает о пересечениях.
    """

    mode: LayoutMode = LayoutMode.AUTO
    banner: LayoutBannerShift | None = None
    captions_bottom: int | None = Field(default=None, ge=0, le=CANVAS_H, description="Низ полосы субтитров")
    title_top: int | None = Field(default=None, ge=0, le=CANVAS_H, description="Верх своего заголовка")
    onscreen_top: int | None = Field(default=None, ge=0, le=CANVAS_H, description="Верх тезисов автора")


HEX_COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"


class FrameShape(StrEnum):
    """Форма кадра ролика: как у канала, авто (круг или прямоугольник по лицам), круг, скруглённая рамка
    или обычный кадр 9:16 без рамки и живого фона."""

    CHANNEL = "channel"
    AUTO = "auto"
    CIRCLE = "circle"
    ROUNDED = "rounded"
    NONE = "none"


class FrameBackground(StrEnum):
    """Набор живых фонов (папки MEDIA_ROOT/backgrounds)."""

    NATURE = "nature"
    NIGHT = "night"


class FrameAspect(StrEnum):
    """Пропорции скруглённой рамки: ширина к высоте."""

    SQUARE = "square"  # 1:1
    LANDSCAPE = "landscape"  # 4:3
    PORTRAIT = "portrait"  # 3:4
    WIDE = "wide"  # 16:9


class FrameOptions(RequestModel):
    """Оформление одного ролика поверх профиля канала (`author_layer.frame`). Пустые поля — как у канала.

    Координаты — в пикселях кадра 1080×1920."""

    shape: FrameShape = FrameShape.CHANNEL
    size: int | None = Field(default=None, ge=480, le=1040, description="Диаметр круга или сторона рамки")
    aspect: FrameAspect | None = Field(default=None, description="Пропорции скруглённой рамки")
    cy: int | None = Field(default=None, ge=700, le=1400, description="Центр фигуры по вертикали")
    radius: int | None = Field(default=None, ge=0, le=240, description="Скругление углов рамки")
    ring_px: int | None = Field(default=None, ge=0, le=24, description="Толщина обводки")
    ring_color: str | None = Field(default=None, pattern=HEX_COLOR_PATTERN)
    accent: str | None = Field(default=None, pattern=HEX_COLOR_PATTERN, description="Цвет слова в субтитрах")
    bg_set: FrameBackground | None = None
    bg_dim: float | None = Field(default=None, ge=0, le=0.5, description="Притемнение фона")
    caption_size: int | None = Field(default=None, ge=48, le=110, description="Размер субтитров")


class AuthorLayerInput(RequestModel):
    """Авторский слой нарезки при создании clip-job (§6.3.8), колонка `jobs.author_layer`.

    `llm_draft` — черновик LLM (без ключа — шаблонный черновик с пометкой), `operator_text` — свой
    текст: `script` целиком или только `hook_text`/`title_overlay`, остальное — через
    `PUT /jobs/{job_id}/author-script` до стадии author_voice. `layout` — ручная раскладка
    (редактор приёмки, `PUT /jobs/{job_id}/layout`).
    """

    mode: AuthorLayerMode = AuthorLayerMode.LLM_DRAFT
    hook_text: str | None = Field(default=None, max_length=COLD_OPEN_MAX)
    voice: AuthorVoiceSource = AuthorVoiceSource.TTS
    captions: CaptionsMode = CaptionsMode.BOTH
    title_overlay: str | None = Field(default=None, max_length=TITLE_MAX)
    script: AuthorScriptBody | None = Field(default=None, description="Готовый сценарий оператора")
    layout: LayoutOverride | None = Field(default=None, description="Ручная раскладка баннера и текста")
    frame: FrameOptions | None = Field(default=None, description="Оформление ролика поверх профиля канала")


class LayoutRectKind(StrEnum):
    BANNER = "banner"
    CAPTIONS = "captions"
    TITLE = "title"
    ONSCREEN = "onscreen"


class LayoutRect(ApiModel):
    """Прямоугольник раскладки на канве 1080×1920: слой баннера или текстовая зона."""

    id: str = Field(description="id слоя варианта или captions | title | onscreen")
    kind: LayoutRectKind
    layer_kind: BannerLayerKind | None = Field(default=None, description="Для kind=banner")
    label: str
    x1: int
    y1: int
    x2: int
    y2: int
    movable: bool = Field(description="Двигается ли в редакторе (микробренд — нет)")
    has_text: bool = Field(default=True, description="Зона с текстом; пустая в кадре не рисуется")


class LayoutConflict(ApiModel):
    a: str
    b: str
    overlap_px: int = Field(description="Глубина наложения: меньшая сторона пересечения")
    rect: Rect


class LayoutSnapshotOut(ApiModel):
    """Раскладка целиком: блок оффера, зоны текста, все прямоугольники и их пересечения."""

    block: Rect | None = Field(description="Общий прямоугольник двигаемого блока баннера")
    captions: Rect
    title: Rect
    onscreen: Rect
    rects: list[LayoutRect]
    conflicts: list[LayoutConflict]
    adjustments: list[str] = Field(
        description="Сдвиги расчёта: banner_clamped_to_safe_zone, title_zone:280->436, captions:1080->900",
    )


class LayoutTexts(ApiModel):
    title: str | None = Field(description="Свой заголовок (зона title показывается, если он есть)")
    onscreen: list[str] = Field(description="Тезисы автора на экране (cold open, вставки)")


class RenderedLayout(ApiModel):
    """Что было в последнем рендере: ручная ли раскладка и какие зоны налезли на баннер."""

    manual: bool
    overlap_zones: list[str] = Field(description="source_zone | author_zone | title_zone | onscreen_zone")
    conflicts: list[LayoutConflict]


class JobLayout(ApiModel):
    """Раскладка ролика для редактора приёмки (`GET/PUT /jobs/{job_id}/layout`).

    `auto` — расчёт без переопределения (от него редактор считает черновик), `effective` — с текущим
    `override` и теми же сдвигами, что сделает рендер. Канва всегда 1080×1920.
    """

    job_id: int
    status: JobStatus
    canvas: CanvasSize
    profile_id: str = Field(description="Профиль safe zone площадки канала")
    safe: Rect
    offer_area: Rect | None
    micro_brand_area: Rect | None
    override: LayoutOverride
    auto: LayoutSnapshotOut
    effective: LayoutSnapshotOut
    texts: LayoutTexts
    rendered: RenderedLayout | None = Field(description="null — ролик ещё не рендерился")
    rerender_allowed: bool
    rerender_hint: str | None = Field(description="Почему перерендер сейчас недоступен")
    rerender_started: bool = Field(default=False, description="PUT: повтор со стадии render поставлен")


class LayoutPut(RequestModel):
    layout: LayoutOverride
    rerender: bool = Field(default=False, description="Сразу перерендерить со стадии render")


class JobCaptions(ApiModel):
    """Субтитры ролика одной галочкой (приёмка): `enabled=false` — без субтитров
    (`author_layer.captions=none`). Заголовок и тезисы остаются."""

    job_id: int
    enabled: bool
    mode: CaptionsMode
    rerender_allowed: bool
    rerender_hint: str | None = None
    rerender_started: bool = False


class JobCaptionsPut(RequestModel):
    enabled: bool
    rerender: bool = Field(default=True, description="Сразу перерендерить со стадии render")


class JobChannelPut(RequestModel):
    """Куда ролик уйдёт после одобрения: другой канал той же площадки (до одобрения)."""

    channel_id: Slug
    rerender: bool = Field(default=True, description="Перерендерить в оформлении и с баннером нового канала")


class JobChannel(ApiModel):
    job_id: int
    channel_id: str
    channel_title: str | None = None
    banner_variant_id: int | None = None
    change_allowed: bool
    change_hint: str | None = None
    rerender_started: bool = False


class ReframeOptions(RequestModel):
    mode: ReframeMode = ReframeMode.AUTO


class ClipJobOptions(RequestModel):
    """Общие опции clip-job; тело `POST /clip-candidates/{candidate_id}/jobs`."""

    channel_id: Slug
    campaign_id: int | None = None
    banner_variant_id: int | None = None
    author_layer: AuthorLayerInput | None = None
    reframe: ReframeOptions | None = None
    priority: int = Field(default=100, ge=0, le=1000)
    start: bool = True


class ClipBatchItem(ClipJobOptions):
    """Клип пачки (§5.2): отрезок источника; `candidate_id` — если это (подправленный) кандидат."""

    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)
    candidate_id: int | None = None

    @model_validator(mode="after")
    def _order(self) -> ClipBatchItem:
        if self.end_sec <= self.start_sec:
            raise PydanticCustomError("edge_order", "end_sec должен быть больше start_sec")
        return self


class ClipBatchCreate(RequestModel):
    items: list[ClipBatchItem] = Field(min_length=1, max_length=20)


class AuthorScriptMode(StrEnum):
    """Откуда текст (`AuthorScriptResult.mode`). heuristic и mock — шаблон, его надо переписать."""

    LLM = "llm"
    MOCK = "mock"
    HEURISTIC = "heuristic"
    OPERATOR = "operator"


class AuthorScriptIssueSeverity(StrEnum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class AuthorScriptIssue(ApiModel):
    """Замечание `lint_script`.

    `code`: filler | retells_source | title_from_transcript | forbidden_phrase | content_flag | link |
    template_text | …; `where`: cold_open | insert_0 | insert_1 | outro | title | script.
    """

    code: str
    severity: AuthorScriptIssueSeverity
    where: str
    detail: str


class JobAuthorScript(ApiModel):
    job_id: int
    script: AuthorScriptBody | None = Field(description="null — черновика ещё нет (стадия не дошла)")
    mode: AuthorScriptMode | None
    label: str | None = Field(
        description="«LLM umka/…», «[черновик без LLM — перепишите…]», «текст оператора»"
    )
    llm_configured: bool
    needs_operator_edit: bool = Field(description="Шаблон или заглушка: переписать своими словами")
    blocked: bool = Field(description="Есть ошибка (severity=error): рендер не пойдёт")
    issues: list[AuthorScriptIssue]
    warnings: list[str]
    version: int = Field(description="Для expected_version в PUT")
    drafting_task_id: int | None = Field(description="Черновик сейчас пишется этой задачей")
    editable: bool = Field(description="Можно править: ролик ещё не дошёл до author_voice/render")
    updated_at: datetime | None
    updated_by: int | None


class AuthorScriptPut(RequestModel):
    """Текст оператора → `mode=operator`; lint пересчитывается сервером."""

    script: AuthorScriptBody
    expected_version: int | None = Field(
        default=None, ge=0, description="Несовпадение → 409 version_conflict"
    )


class AuthorScriptDraftRequest(RequestModel):
    """Новый черновик: `llm` (нужен ключ, платно, через бюджет) или `heuristic` (шаблон с пометкой)."""

    mode: SelectorChoice = SelectorChoice.AUTO
    keep_operator_edits: bool = Field(default=False, description="false — перезаписать текст оператора")


class AuthorScriptSummary(ApiModel):
    """Кратко для деталей ролика и приёмки."""

    mode: AuthorScriptMode | None
    label: str | None
    needs_operator_edit: bool
    blocked: bool
    issues_count: int


# ---------------------------------------------------------------- голос автора


AUTHOR_VOICE_MAX_BYTES = 20 * 1024 * 1024
AUTHOR_VOICE_MIME_TYPES: tuple[str, ...] = (
    "audio/webm",
    "audio/ogg",
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
)
"""MediaRecorder в Chrome/Firefox пишет webm/ogg (opus); wav — из внешнего рекордера. Тип — по сигнатуре."""


class AuthorVoiceKind(StrEnum):
    """Чем озвучена реплика (`VoiceTake.kind`): живой голос — лучший ответ на «синтетический голос»."""

    TTS = "tts"
    OPERATOR = "operator"
    MOCK = "mock"


class AuthorVoiceTiming(StrEnum):
    """Откуда пословные тайминги (`VoiceTake.timing`)."""

    TTS = "tts"
    ASR = "asr"
    ESTIMATED = "estimated"
    MOCK = "mock"


class AuthorVoiceStatus(StrEnum):
    MISSING = "missing"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class AuthorVoiceSegmentState(ApiModel):
    segment: AuthorVoiceSegment
    text: str | None = Field(description="Текст реплики из сценария")
    kind: AuthorVoiceKind | None
    status: AuthorVoiceStatus
    duration_sec: float | None
    timing: AuthorVoiceTiming | None
    words_count: int | None
    audio_url: str | None
    task_id: int | None
    error: str | None
    uploaded_at: datetime | None


class JobAuthorVoice(ApiModel):
    job_id: int
    voice: AuthorVoiceSource = Field(description="Выбор при создании: tts | operator_recording")
    segments: list[AuthorVoiceSegmentState]
    synthetic: bool = Field(description="Есть синтетический голос → пункт ai_disclosure обязателен")


class AuthorVoiceAccepted(TaskAccepted):
    """202: запись принята, тишина по краям и пословные тайминги (ASR) — в задаче `task_id`."""

    segment: AuthorVoiceSegment
    bytes: int
    mime: str


# ---------------------------------------------------------------- оригинальность (OWNER-DECISIONS п. 5)


class OriginalityCheckCode(StrEnum):
    """Проверки `originality.build_originality_report` (ключи `CHECK_TITLES_RU` движка)."""

    AUTHOR_SHARE = "author_share"
    VOICE_BALANCE = "voice_balance"
    OWN_VOICE = "own_voice"
    OWN_TEXT = "own_text"
    AUTHOR_TEXT = "author_text"
    AUTHOR_TEXT_REPETITION = "author_text_repetition"
    REPEAT_MOMENT = "repeat_moment"
    LIBRARY_SIMILARITY = "library_similarity"
    FOREIGN_WATERMARK = "foreign_watermark"
    RIGHTS = "rights"


class OriginalityCheckStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    INFO = "info"
    SKIP = "skip"


class OriginalityVerdict(StrEnum):
    """Итог отчёта (`jobs.originality_verdict`): fail — не заливать, пока не исправлено."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class OriginalityStage(StrEnum):
    ESTIMATE = "estimate"
    FINAL = "final"


class OriginalityCheckOut(ApiModel):
    code: OriginalityCheckCode
    title: str = Field(description="Название проверки по-русски")
    status: OriginalityCheckStatus
    value: Any = Field(description="Измерено: число или код")
    threshold: Any = Field(description="Порог, если есть")
    detail: str


class AuthorShareBreakdown(ApiModel):
    """`AuthorShare.to_dict()`: слагаемые метрики §6.3.8 и окна на выходной шкале."""

    value: float
    duration_sec: float
    author_only_sec: float
    voice_over_sec: float
    text_sec: float
    own_voice_sec: float
    own_text_sec: float
    freeze_sec: float
    voice_share: float
    text_part: float
    text_capped: bool
    weights: dict[str, float] = Field(description="author_only, voice_over, text")
    intervals: dict[str, list[list[float]]] = Field(description="Окна [s, e] по видам вклада")


class OriginalitySummary(ApiModel):
    author_share: float
    author_share_min: float
    gate_applies: bool = Field(description="Чужие права: гейт обязателен")
    gate_passed: bool
    voice_share: float
    own_voice_sec: float
    own_text_sec: float
    duration_sec: float
    rights_type: RightsType | None
    author_mode: str | None = Field(description="Режим авторского текста: llm | operator | heuristic | mock")


class OriginalityReportOut(ApiModel):
    """`OriginalityReport.to_dict()` + подписи; `suggestions` — конкретные подсказки по-русски."""

    stage: OriginalityStage
    verdict: OriginalityVerdict
    verdict_label: str = Field(description="«можно заливать» / «…после правок» / «не заливать…»")
    summary: OriginalitySummary
    fails: list[OriginalityCheckCode]
    warns: list[OriginalityCheckCode]
    checks: list[OriginalityCheckOut]
    suggestions: list[str]
    author_share: AuthorShareBreakdown
    rights: dict[str, Any] = Field(
        description="rights_type, owner_name, allowlist_status, attribution_text, valid_until, license"
    )
    notes: list[str]
    computed_at: datetime | None


class OriginalityEstimateRequest(RequestModel):
    """Оценка до рендера по черновику (длительности реплик — по длине текста или готовым записям).

    Пусто — текущий сценарий и голос ролика (кандидата: черновик ещё не написан → по `author_layer`).
    """

    script: AuthorScriptBody | None = None
    voice: AuthorVoiceSource | None = None
    start_sec: float | None = Field(default=None, ge=0)
    end_sec: float | None = Field(default=None, gt=0)
    channel_id: Slug | None = Field(default=None, description="Для повтора реплик и похожести (кандидат)")
