"""Трендоскаут — мост к Kino-trends (docs/ARCHITECTURE.md §5.2 «Трендоскаут», тег `trends`).

Своего бэкенда у Трендоскаута нет: `api/routers/trends.py` проксирует отдельный сервис Kino-trends
(`kino_trends_url`, по умолчанию он живёт на 127.0.0.1:18320) и типизирует его ответы, чтобы фронт
получал привычные схемы в OpenAPI. Kino-trends развивается независимо, поэтому модели записей —
`extra="allow"`: новые поля проходят насквозь, а типы известных полей мягкие (`str | None`, числа).

* `GET /trends/status` → `TrendsStatus`: настроен ли мост, отвечает ли Kino-trends, счётчики дня
  (`/api/today`) и очереди (`/api/pipeline/status`). Никогда не падает из-за моста — ошибки в `error`.
* `GET /trends/today` → `TrendsToday` (`/api/today`): календарь кино на сегодня/завтра, подборка чистых
  роликов, кандидаты на проверку, счётчики, состояние сбора.
* `GET /trends/videos` → `TrendsVideoList` (`/api/videos`): популярные видео СНГ с уровнем чистоты.
* `GET /trends/pipeline` → `TrendsPipeline` (`/api/pipeline`): очередь конвейера Kino-trends → auto-shorts.
* `POST /trends/pipeline/enqueue`, `POST /trends/pipeline/{item_id}/retry|cancel` → `TrendsPipelineItem`.
* `GET /trends/videos/{video_id}` → `TrendsVideoDetail` (`/api/videos/{id}`): большой разбор ролика —
  карточка YouTube, оценка чистоты, разбор нейросети по тексту и кадрам, замеры просмотров, файл на
  хранилище, блок для заливщика. `frames_url` указывает на наш прокси кадров
  (`GET /trends/videos/{video_id}/frames`, image/jpeg).
* `POST /trends/videos/{video_id}/pick|level|analyze|file` → `TrendsVideoDetail`: решения оператора
  (в подборку / уровень чистоты руками), разбор заново, заказ mp4 на хранилище Kino-trends.
* `GET /trends/films` → `TrendsFilmList` (`/api/films`): фильмы и сериалы, которые режут в шортсы, с правами,
  лучшим исходником и признаком `ready` («можно резать сейчас»: права ok/caution, исходник подтверждён,
  не в конвейере) — фильтр `ready=1`.
* `GET /trends/films/{title_id}` → `TrendsFilmDetail`: бриф `/api/brief/{id}` (почему заходит, план, сцены,
  права, исходники, референсы, блок нарезчику) плюс статистика `/api/films/{id}` (каналы, по дням, сезоны,
  чужие нарезки) одним ответом; постер — `GET /trends/films/{title_id}/poster`.
* `GET /trends/films/{title_id}/rights`, `POST …/rights/refresh|manual` → `TrendsFilmRights`;
  `GET /trends/films/{title_id}/sources`, `POST …/sources` (поиск исходников заново) → `TrendsFilmSources`;
  `POST /trends/films/find-sources?limit=` — фоновый поиск исходников для популярных произведений без них
  (ход — в `GET /trends/runs` → `jobs.sources_top`).

Ошибки моста: 409 `trends_not_configured` (пустая `kino_trends_url`), 502 `trends_unreachable`
(нет ответа, 5xx или не JSON); 400/404 Kino-trends пробрасываются как `bad_request`/`trends_item_not_found`.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

from autoshorts.api.schemas.common import ApiModel, RequestModel

__all__ = [
    "PIPELINE_STATES",
    "TrendsEnqueueRequest",
    "TrendsFilmCutter",
    "TrendsFilmDetail",
    "TrendsFilmDetailQuery",
    "TrendsFilmDetailQueryParams",
    "TrendsFilmFindSourcesQuery",
    "TrendsFilmFindSourcesQueryParams",
    "TrendsFilmFindSourcesStarted",
    "TrendsFilmList",
    "TrendsFilmPlan",
    "TrendsFilmReference",
    "TrendsFilmRights",
    "TrendsFilmRightsManualRequest",
    "TrendsFilmRow",
    "TrendsFilmSlot",
    "TrendsFilmSource",
    "TrendsFilmSources",
    "TrendsFilmStats",
    "TrendsFilmTitle",
    "TrendsFilmVideo",
    "TrendsFilmsQuery",
    "TrendsFilmsQueryParams",
    "TrendsPickVideo",
    "TrendsPipeline",
    "TrendsPipelineBridge",
    "TrendsPipelineCounts",
    "TrendsPipelineItem",
    "TrendsPipelineQuery",
    "TrendsPipelineQueryParams",
    "TrendsRights",
    "TrendsStatus",
    "TrendsToday",
    "TrendsTodayCounts",
    "TrendsTodaySchedule",
    "TrendsVideo",
    "TrendsVideoAnalysis",
    "TrendsVideoDetail",
    "TrendsVideoFile",
    "TrendsVideoFrames",
    "TrendsVideoLevelRequest",
    "TrendsVideoList",
    "TrendsVideoPickRequest",
    "TrendsVideoSeriesPoint",
    "TrendsVideosQuery",
    "TrendsVideosQueryParams",
]

PIPELINE_STATES: tuple[str, ...] = (
    "new",
    "file_pending",
    "needs_rights",
    "sent",
    "cutting",
    "review",
    "qc_failed",
    "approved",
    "publishing",
    "published",
    "failed",
    "cancelled",
)
"""Состояния очереди Kino-trends (`kinotrends/pipeline.py: STATES`); в схеме — `str`, чтобы новое
состояние на той стороне не ломало прокси. Русские подписи приходят в `states_ru`/`status_ru`."""

# в счётчиках статуса: что считать «в работе», «ждёт человека» и «сломалось»
PIPELINE_ACTIVE_STATES = frozenset({"new", "file_pending", "sent", "cutting", "approved", "publishing"})
PIPELINE_REVIEW_STATES = frozenset({"review", "needs_rights"})
PIPELINE_FAILED_STATES = frozenset({"failed", "qc_failed"})


class PassthroughModel(ApiModel):
    """Запись Kino-trends: известные поля типизированы, остальные проходят насквозь."""

    model_config = ConfigDict(from_attributes=True, extra="allow")


# ---------------------------------------------------------------- статус моста


class TrendsTodayCounts(ApiModel):
    films: int = Field(description="Слотов кино в календаре на сегодня")
    films_done: int = Field(0, description="Из них отмечено «сделано»")
    videos: int = Field(description="Роликов в чартах всего")
    clean: int = Field(description="Чистых роликов (уровень clean) — бейдж сайдбара")
    check: int = Field(0, description="Роликов на ручную проверку (уровень check)")
    picks: int = Field(0, description="Роликов в подборке под перезалив")


class TrendsPipelineCounts(ApiModel):
    active: int = Field(description="В работе: new, file_pending, sent, cutting, approved, publishing")
    review: int = Field(description="Ждут человека: review, needs_rights")
    failed: int = Field(description="Сломалось: failed, qc_failed")
    published: int = Field(0, description="Опубликовано")
    linked: bool = Field(False, description="Kino-trends видит auto-shorts (его health ответил ok)")
    enabled: bool = Field(False, description="Цикл конвейера на стороне Kino-trends включён")


class TrendsStatus(ApiModel):
    configured: bool = Field(description="Адрес Kino-trends задан и похож на http(s)-URL")
    url_set: bool = Field(description="Настройка kino_trends_url непустая")
    reachable: bool = Field(description="Kino-trends ответил на оба запроса статуса")
    public_url: str | None = Field(description="kino_trends_public_url — ссылка «Открыть Kino-trends»")
    error: str | None = Field(description="Почему недоступен / не настроен (по-русски)")
    today: TrendsTodayCounts | None
    pipeline: TrendsPipelineCounts | None
    checked_at: str | None = Field(None, description="Когда опрашивали Kino-trends (ISO, UTC)")


# ---------------------------------------------------------------- подборка дня (`/api/today`)


class TrendsRights(PassthroughModel):
    """Проверка авторских прав произведения (`rights.check`)."""

    level: str | None = Field(None, description="ok | caution | block | unknown")
    level_ru: str | None = None
    risk: float | None = Field(None, description="Риск 0–100")
    reasons: list[str] = Field(default_factory=list)
    rule: str | None = None
    clip_max_s: int | None = None
    holder: str | None = Field(None, description="Правообладатель")
    holder_policy: str | None = Field(None, description="claim | block | …")
    checked_at: str | None = None


class TrendsFilmSlot(PassthroughModel):
    """Слот календаря кино: что резать сегодня/завтра."""

    id: int
    day: str | None = None
    slot: int | None = None
    title_id: str | None = Field(None, description="Wikidata id произведения (ref_id для enqueue kind=film)")
    label: str | None = None
    year: int | None = None
    kind_ru: str | None = Field(None, description="фильм | сериал | мультфильм …")
    reason: str | None = Field(None, description="Почему в тренде (просмотры, рост)")
    plan: dict[str, Any] | None = Field(None, description="duration, hook, best_time, hashtags, refs[]")
    status: str = Field("planned", description="planned | done | skipped")
    locked: bool = False
    source: dict[str, Any] | None = Field(None, description="Исходник: url, channel, duration_s, language")
    source_alt: dict[str, Any] | None = None
    explain: str | None = Field(None, description="Пояснение нейросети: что резать")
    rights: TrendsRights | None = None
    kinopoisk_url: str | None = None


class TrendsPickVideo(PassthroughModel):
    """Ролик подборки под перезалив (или кандидат на проверку)."""

    video_id: str
    title: str | None = None
    channel: str | None = None
    channel_subs: int | None = None
    source_url: str | None = None
    thumb: str | None = Field(None, description="Обложка YouTube (внешний URL из данных)")
    frames_url: str | None = Field(
        None, description="Плитка кадров через наш API (/trends/videos/{id}/frames)"
    )
    duration_s: int | None = None
    is_short: int | None = None
    lang: str | None = None
    license: str | None = Field(None, description="creativeCommon | youtube")
    level: str | None = Field(None, description="clean | check | no | unknown")
    score: float | None = None
    reasons: list[str] = Field(default_factory=list)
    topic: str | None = None
    format: str | None = None
    hook: str | None = None
    banner_advice: str | None = None
    audience_fit: float | None = Field(None, description="Совпадение с нашей аудиторией 0–1")
    audience_why: str | None = None
    free_zones: list[str] = Field(default_factory=list)
    orientation: str | None = None
    views: int | None = None
    growth_per_day: float | None = None
    published_at: str | None = None
    direct_file_url: str | None = None
    file_status: str | None = None
    pick: int | None = None
    picked_at: str | None = None
    pick_note: str | None = None
    used_at: str | None = Field(None, description="Когда отмечен залитым")


class TrendsTodaySchedule(ApiModel):
    today: list[TrendsFilmSlot]
    tomorrow: list[TrendsFilmSlot]


class TrendsTodayRawCounts(PassthroughModel):
    film_total: int = 0
    film_done: int = 0
    picks: int = 0
    used: int = 0
    clean: int = 0
    check: int = 0
    videos: int = 0


class TrendsToday(PassthroughModel):
    today: str = Field(description="Дата дня, YYYY-MM-DD")
    schedule: TrendsTodaySchedule
    picks: list[TrendsPickVideo]
    candidates: list[TrendsPickVideo] = Field(default_factory=list)
    counts: TrendsTodayRawCounts
    run: dict[str, Any] | None = Field(None, description="Текущий сбор: running, phase, progress")
    last_run: dict[str, Any] | None = Field(
        None, description="Последний сбор: started_at, finished_at, status"
    )
    videos_job: dict[str, Any] | None = Field(None, description="Фоновый разбор видео: running, last")


# ---------------------------------------------------------------- популярные видео (`/api/videos`)


class TrendsVideosQuery(BaseModel):
    """Параметры `GET /trends/videos` (повторяют `/api/videos` Kino-trends)."""

    level: Literal["clean", "check", "no", "unknown"] | None = Field(None, description="Уровень чистоты")
    fit: float | None = Field(None, ge=0, le=1, description="Минимальное совпадение с аудиторией (0.6)")
    short: int | None = Field(None, ge=0, le=1, description="1 — только шортсы, 0 — только длинные")
    pick: bool = Field(False, description="Только подборка")
    sort: Literal["score", "audience", "growth", "views", "published", "seen"] = "score"
    q: str | None = Field(None, max_length=200, description="Поиск по названию, каналу, описанию")
    lang: str | None = Field(None, max_length=8, description="ru (по умолчанию в Kino-trends — все)")
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)


TrendsVideosQueryParams = Annotated[TrendsVideosQuery, Query()]


class TrendsVideo(PassthroughModel):
    """Строка чарта популярных видео."""

    video_id: str
    title: str | None = None
    url: str | None = None
    channel_id: str | None = None
    channel_name: str | None = None
    channel_subs: int | None = None
    duration_s: int | None = None
    is_short: int | None = None
    published_at: str | None = None
    category: str | None = None
    lang: str | None = None
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    vpd: float | None = Field(None, description="Просмотров в день в среднем")
    growth: float | None = Field(None, description="Прирост просмотров в день")
    thumb: str | None = None
    license: str | None = None
    licensed_content: int | None = None
    made_for_kids: int | None = None
    blocked_ru: int | None = None
    level: str | None = Field(None, description="clean | check | no | unknown")
    level_ru: str | None = None
    score: float | None = None
    reasons: list[str] = Field(default_factory=list)
    analysis: dict[str, Any] | None = Field(None, description="topic, format, audience_fit, audience_why, …")
    frames: dict[str, Any] | None = Field(None, description="Разбор кадров: watermark, free_zones, …")
    frames_available: bool | None = None
    pick: int | None = None
    pick_auto: int | None = None
    used_at: str | None = None
    file: dict[str, Any] | None = Field(None, description="status, size, error, file_url")
    regions: dict[str, Any] | None = None


class TrendsVideoList(PassthroughModel):
    items: list[TrendsVideo]
    total: int
    levels: dict[str, int] = Field(default_factory=dict, description="clean / check / no / unknown")
    picks: int = 0
    shorts: int = 0
    regions_ru: dict[str, str] = Field(default_factory=dict)
    categories_ru: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------- разбор ролика (`/api/videos/{id}`)


class TrendsVideoAnalysis(PassthroughModel):
    """Разбор нейросети по названию, описанию, тегам и цифрам (`analysis`)."""

    topic: str | None = None
    format: str | None = None
    niche: str | None = None
    audience: str | None = Field(None, description="Кто смотрит")
    audience_fit: float | None = Field(None, description="Совпадение с нашей аудиторией 0–1")
    audience_why: str | None = None
    why_popular: str | None = None
    hook: str | None = None
    banner_advice: str | None = Field(None, description="Где ставить наш баннер")
    reuse_fit: float | None = Field(None, description="Пригодность под чужой баннер 0–1")
    is_reupload: bool | None = Field(None, description="Уже перезалив или компиляция")
    has_sponsor: bool | None = None
    music_heavy: bool | None = None
    film_title: str | None = Field(None, description="Кадры из фильма — его название")
    model: str | None = None


class TrendsVideoFrames(PassthroughModel):
    """Что в картинке по шести кадрам с хранилища (`frames`)."""

    watermark: bool | None = None
    watermark_where: str | None = None
    text_overlay: bool | None = None
    subtitles: bool | None = None
    subscribe_cta: bool | None = None
    banner: bool | None = Field(None, description="Чужая реклама или плашка")
    borders: bool | None = None
    faces: bool | None = None
    screen_recording: bool | None = None
    clean: bool | None = Field(None, description="Чужого брендинга нет")
    orientation: str | None = Field(None, description="vertical | horizontal | square")
    quality: str | None = None
    free_zones: list[str] = Field(default_factory=list, description="Свободно под наш баннер: верх, низ, …")
    note: str | None = None
    error: str | None = Field(None, description="Почему кадры не вынулись")
    width: int | None = None
    height: int | None = None
    model: str | None = None


class TrendsVideoFile(PassthroughModel):
    """mp4 на хранилище Kino-trends (`file`)."""

    status: str | None = Field(None, description="running | done | error")
    size: int | None = None
    error: str | None = None
    file_url: str | None = Field(None, description="Прямая ссылка на mp4 (внешний адрес хранилища)")


class TrendsVideoSeriesPoint(PassthroughModel):
    ts: str
    views: int | None = None
    likes: int | None = None
    comments: int | None = None


class TrendsVideoDetail(TrendsVideo):
    """Большой разбор ролика: строка чарта плюс описание, карточка YouTube, типизированные разбор и кадры,
    замеры просмотров, файл, блок для заливщика. `frames_url` — наш прокси кадров (если есть)."""

    description: str | None = None
    category_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list, description="Темы YouTube (англ.)")
    lang_hint: str | None = None
    stats_at: str | None = None
    cats: dict[str, Any] | None = Field(None, description="Категория → место в чарте России")
    origin: str | None = Field(None, description="cc — поиск Creative Commons, иначе чарт")
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    status: str | None = None
    embeddable: int | None = None
    caption: int | None = None
    definition: str | None = Field(None, description="hd | sd")
    analysis: TrendsVideoAnalysis | None = None  # type: ignore[assignment]
    analysis_at: str | None = None
    frames: TrendsVideoFrames | None = None  # type: ignore[assignment]
    frames_at: str | None = None
    frames_url: str | None = Field(None, description="Плитка 6 кадров через наш API (если frames_available)")
    assessed_at: str | None = None
    manual_level: str | None = Field(None, description="Решение оператора: clean | check | no; null — авто")
    pick_at: str | None = None
    pick_note: str | None = None
    series: list[TrendsVideoSeriesPoint] = Field(default_factory=list, description="Замеры между прогонами")
    file: TrendsVideoFile | None = None  # type: ignore[assignment]
    holder: str | None = Field(None, description="Канал правообладателя (если распознан)")
    cutter: TrendsPickVideo | None = Field(None, description="Блок для заливщика (как в подборке)")
    notes: list[str] = Field(default_factory=list, description="Заметки последнего разбора (после analyze)")


class TrendsVideoPickRequest(RequestModel):
    pick: bool = Field(description="true — в подборку, false — убрать")
    note: str | None = Field(None, max_length=200)


class TrendsVideoLevelRequest(RequestModel):
    level: Literal["clean", "check", "no"] | None = Field(
        None, description="Решение оператора; null — вернуть автоматическую оценку"
    )


# ---------------------------------------------------------------- кино и сериалы (`/api/films`, `/api/brief`)

FILM_KINDS = ("all", "film", "series", "animated", "animated_series", "anime", "tv_show", "franchise")
"""`kind` Kino-trends: series и animated — группы (сериалы + мультсериалы + аниме + шоу; вся анимация)."""


class TrendsFilmsQuery(BaseModel):
    """Параметры `GET /trends/films` (повторяют `/api/films` Kino-trends; сортировки — его ключи)."""

    period: Literal["7", "30", "90", "all"] = Field("30", description="За сколько дней брать чужие нарезки")
    lang: str = Field("ru", max_length=8, description="Язык нарезок: ru | all")
    sort: Literal["score", "growth", "shorts", "views", "median", "vpd", "fresh", "outliers", "channels"] = (
        "score"
    )
    kind: Literal[FILM_KINDS] = "all"  # type: ignore[valid-type]
    genre: str | None = Field(None, max_length=60)
    q: str | None = Field(None, max_length=200, description="Поиск по названию")
    ready: bool = Field(
        False, description="Только то, с чем можно работать сейчас (права, исходник, не в работе)"
    )
    limit: int = Field(100, ge=1, le=500)
    offset: int = Field(0, ge=0)


TrendsFilmsQueryParams = Annotated[TrendsFilmsQuery, Query()]


class TrendsFilmDetailQuery(BaseModel):
    period: Literal["7", "30", "90", "all"] = "30"
    lang: str = Field("ru", max_length=8)


TrendsFilmDetailQueryParams = Annotated[TrendsFilmDetailQuery, Query()]


class TrendsFilmSource(PassthroughModel):
    """Исходник произведения: полный фильм или серия на YouTube / Rutube (`title_sources`)."""

    id: int | None = None
    title_id: str | None = None
    video_id: str | None = None
    url: str | None = None
    label: str | None = None
    channel: str | None = None
    duration_s: float | None = None
    views: int | None = None
    kind: str | None = Field(None, description="film | episode | pack | other")
    season: int | None = None
    episode: int | None = None
    language: str | None = Field(None, description="русский дубляж | оригинал | …")
    trust: float | None = Field(None, description="Уверенность модели 0–1")
    verified: bool | int | None = Field(None, description="Подтверждён моделью")
    note: str | None = None
    score: float | None = None
    found_at: str | None = None
    alive: int | None = Field(None, description="1 — жив, 0 — удалён, null — не проверяли")
    checked_at: str | None = None
    file_status: str | None = Field(None, description="running | done | error — mp4 на хранилище")
    file_error: str | None = None


class TrendsFilmRow(PassthroughModel):
    """Строка списка кино: статистика чужих нарезок, права, лучший исходник, готовность."""

    title_id: str
    label: str | None = None
    label_en: str | None = None
    year: int | None = None
    kind: str | None = Field(None, description="film | series | animated | animated_series | anime | tv_show")
    kind_ru: str | None = None
    genres: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    franchise: str | None = None
    kp_id: str | None = None
    n_shorts: int | None = Field(None, description="Чужих нарезок за период")
    n_fresh7: int | None = Field(None, description="Из них за 7 дней")
    n_channels: int | None = None
    views_sum: int | None = None
    views_median: float | None = None
    views_p90: float | None = None
    views_max: int | None = None
    vpd_sum: float | None = None
    growth7: float | None = Field(None, description="Прирост просмотров за 7 дней по замерам")
    growth7_measured: int | None = None
    dur_avg: float | None = None
    like_rate: float | None = None
    outliers: int | None = None
    outlier_rated: int | None = None
    outlier_max: float | None = None
    outlier_share: float | None = Field(None, description="Доля роликов в 3+ раза выше нормы канала")
    small_channel_median: float | None = None
    small_channel_n: int | None = None
    score: float | None = Field(None, description="Сводный балл темы 0–100")
    opportunity: bool | float | None = Field(
        None, description="Свободная ниша (мало каналов, высокая медиана)"
    )
    wiki_7d: float | None = None
    wiki_ratio: float | None = None
    trends_7d: float | None = None
    trends_ratio: float | None = None
    top_video: dict[str, Any] | None = Field(
        None, description="Лучшая чужая нарезка: video_id, title, views, thumb"
    )
    last_pub: str | None = None
    rights: TrendsRights | None = None
    source: TrendsFilmSource | None = Field(None, description="Лучший исходник (если Kino-trends его отдаёт)")
    in_pipeline: str | None = Field(None, description="Статус задания конвейера по произведению, если есть")
    ready: bool | None = Field(None, description="Можно резать сейчас (считает Kino-trends)")
    ready_reasons: list[str] = Field(
        default_factory=list, description="Почему нельзя: права, исходник, конвейер"
    )
    poster_url: str | None = Field(None, description="Постер через наш API")


class TrendsFilmList(PassthroughModel):
    items: list[TrendsFilmRow]
    total: int


class TrendsFilmTitle(PassthroughModel):
    title_id: str
    label: str | None = None
    label_en: str | None = None
    original_title: str | None = None
    year: int | None = None
    kind: str | None = None
    kind_ru: str | None = None
    genres: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    kp_id: str | None = None
    imdb_id: str | None = None
    franchise: str | None = None
    sitelinks: int | None = None
    is_manual: bool | None = None
    aliases: list[str] = Field(default_factory=list)


class TrendsFilmStats(PassthroughModel):
    n_shorts: int | None = None
    n_channels: int | None = None
    views_sum: int | None = None
    views_median: float | None = None
    views_p90: float | None = None
    views_max: int | None = None
    vpd_sum: float | None = None
    growth7: float | None = None
    dur_avg: float | None = None


class TrendsFilmPlan(PassthroughModel):
    duration: str | None = Field(None, description="«46–60 с»")
    hook: str | None = None
    best_time: str | None = None
    best_time_is_global: bool | None = None
    hashtags: list[str] = Field(default_factory=list)
    refs: list[dict[str, Any]] = Field(default_factory=list)


class TrendsFilmCutter(PassthroughModel):
    """Блок для нарезчика: исходник, прямой mp4, длина клипа, подсказка «что искать»."""

    source_url: str | None = None
    source_kind: str | None = Field(None, description="youtube | rutube")
    direct_file_url: str | None = Field(None, description="Прямой mp4 из хранилища (внешний адрес)")
    alt_url: str | None = None
    hint: str | None = None
    clip_min_s: int | None = None
    clip_max_s: int | None = None
    rights_ok: bool | None = None
    rights_level: str | None = None
    rights_note: str | None = None


class TrendsFilmReference(PassthroughModel):
    """Чужая нарезка, которая зашла (доказательство темы)."""

    url: str | None = None
    video_id: str | None = Field(None, description="Из url, чтобы показать миниатюру через наш прокси")
    title: str | None = None
    views: int | None = None
    duration_s: int | None = None
    channel: str | None = None
    thumb: str | None = None


class TrendsFilmVideo(PassthroughModel):
    """Чужая нарезка из статистики произведения (`/api/films/{id}: videos[]`)."""

    video_id: str
    title: str | None = None
    url: str | None = None
    channel_id: str | None = None
    channel_name: str | None = None
    duration_s: int | None = None
    published_at: str | None = None
    views: int | None = None
    likes: int | None = None
    vpd: float | None = None
    growth7: float | None = None
    outlier_x: float | None = Field(None, description="Во сколько раз выше нормы канала")
    thumb: str | None = None
    season: int | None = None
    episode: int | None = None
    recog_conf: float | None = None
    recog_method: str | None = None


class TrendsFilmRights(TrendsRights):
    """Полная оценка прав произведения (`/api/films/{id}/rights`)."""

    title_id: str | None = None
    label: str | None = None
    auto_level: str | None = Field(None, description="Уровень без решения оператора")
    holder_policy_ru: str | None = None
    public_domain: bool | None = None
    signals: dict[str, Any] | None = Field(
        None, description="rated, removed_strict, claimants, source, llm, events, manual_level, manual_note"
    )
    passes: bool | None = Field(None, description="Проходит в календарь и к нарезчику")
    notes: list[str] = Field(default_factory=list, description="Что сделала перепроверка")


class TrendsFilmSources(PassthroughModel):
    items: list[TrendsFilmSource]
    checked_at: str | None = None


class TrendsFilmDetail(PassthroughModel):
    """Карточка произведения: бриф Kino-trends (`/api/brief/{id}`) и статистика (`/api/films/{id}`) вместе."""

    title: TrendsFilmTitle
    stats: TrendsFilmStats | None = None
    rights: TrendsFilmRights | None = None
    why: list[str] = Field(default_factory=list, description="Почему заходит")
    opportunity: bool | float | None = None
    plan: TrendsFilmPlan | None = None
    scenes: list[dict[str, Any]] = Field(
        default_factory=list, description="name, what_to_cut, quotes, where, n, views, top"
    )
    hooks: list[str] = Field(default_factory=list)
    advice: list[str] = Field(default_factory=list)
    insights_at: str | None = None
    explain: str | None = Field(None, description="Нейросеть: почему стоит резать")
    explain_at: str | None = None
    top_channels: list[dict[str, Any]] = Field(default_factory=list)
    hashtags_all: list[dict[str, Any]] = Field(default_factory=list)
    schedule: list[dict[str, Any]] = Field(
        default_factory=list, description="Слоты календаря: id, day, slot, status"
    )
    interest: dict[str, Any] | None = Field(
        None, description="Википедия и Google Trends: summary, lines, series"
    )
    kinopoisk_url: str | None = None
    imdb_url: str | None = None
    poster_url: str | None = Field(None, description="Постер через наш API")
    sources: list[TrendsFilmSource] = Field(default_factory=list)
    source: TrendsFilmSource | None = Field(None, description="Лучший исходник")
    source_alt: TrendsFilmSource | None = None
    references: list[TrendsFilmReference] = Field(default_factory=list)
    deliverable: dict[str, Any] | None = None
    cutter: TrendsFilmCutter | None = None
    channels: list[dict[str, Any]] = Field(default_factory=list, description="channel_id, name, n, views")
    hashtags: list[dict[str, Any]] = Field(default_factory=list, description="tag, n")
    durations: list[dict[str, Any]] = Field(default_factory=list, description="bucket, n")
    daily: list[dict[str, Any]] = Field(
        default_factory=list, description="day, n, views — публикации по дням"
    )
    seasons: list[dict[str, Any]] = Field(default_factory=list, description="season, n, views (для сериалов)")
    videos: list[TrendsFilmVideo] = Field(default_factory=list, description="Чужие нарезки за период")


class TrendsFilmFindSourcesQuery(BaseModel):
    limit: int = Field(30, ge=1, le=200, description="Сколько популярных произведений без исходника обойти")


TrendsFilmFindSourcesQueryParams = Annotated[TrendsFilmFindSourcesQuery, Query()]


class TrendsFilmFindSourcesStarted(ApiModel):
    """Фоновый поиск исходников для топа запущен; ход виден в `GET /trends/runs` → `jobs.sources_top`."""

    started: bool = True
    limit: int | None = None


class TrendsFilmRightsManualRequest(RequestModel):
    level: Literal["ok", "caution", "block"] | None = Field(
        None, description="Решение оператора по правам; null — вернуть автоматическую оценку"
    )
    note: str | None = Field(None, max_length=200)


# ---------------------------------------------------------------- конвейер (`/api/pipeline`)


class TrendsPipelineQuery(BaseModel):
    status: str | None = Field(None, max_length=32, pattern=r"^[a-z_]+$")
    limit: int = Field(200, ge=1, le=500)


TrendsPipelineQueryParams = Annotated[TrendsPipelineQuery, Query()]


class TrendsPipelineItem(PassthroughModel):
    """Задание конвейера Kino-trends → auto-shorts."""

    id: int
    kind: str = Field(description="film | video")
    ref_id: str = Field(description="title_id кино или video_id ролика")
    label: str | None = None
    channel: str | None = None
    source_url: str | None = None
    source_video_id: str | None = None
    direct_url: str | None = None
    license: str | None = None
    is_short: int | None = None
    duration_s: int | None = None
    hint: str | None = Field(None, description="Подсказка нарезчику")
    clip_min_s: int | None = None
    clip_max_s: int | None = None
    niche_id: str | None = None
    channel_slug: str | None = None
    status: str = Field(description="Одно из PIPELINE_STATES")
    status_ru: str | None = None
    rights_type: str | None = None
    as_source_id: str | None = Field(None, description="id источника в auto-shorts")
    as_jobs: list[Any] = Field(default_factory=list)
    as_packages: list[Any] = Field(default_factory=list)
    as_publications: list[Any] = Field(default_factory=list)
    metrics: dict[str, Any] | None = None
    last_error: str | None = None
    note: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    sent_at: str | None = None
    published_at: str | None = None


class TrendsPipelineBridge(PassthroughModel):
    """Как Kino-trends видит auto-shorts (`pipeline.status`)."""

    configured: bool = False
    health: dict[str, Any] | None = Field(None, description="/health/ready auto-shorts глазами Kino-trends")
    channels: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    enabled: bool = False
    auto_review: bool = False
    autopublish: bool = False
    rights_default: str | None = None


class TrendsPipeline(PassthroughModel):
    items: list[TrendsPipelineItem]
    counts: dict[str, int] = Field(default_factory=dict, description="Состояние → число заданий")
    states_ru: dict[str, str] = Field(default_factory=dict, description="Состояние → русская подпись")
    status: TrendsPipelineBridge


class TrendsEnqueueRequest(RequestModel):
    kind: Literal["film", "video"]
    ref_id: str = Field(min_length=1, max_length=64, description="title_id кино или video_id ролика")
    note: str | None = Field(None, max_length=200)
