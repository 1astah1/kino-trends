"""Коллекторы метрик своих публикаций и ручной ввод (docs/ARCHITECTURE.md §6.5, контракт M4-0).

Реализация — M4-A (`collectors/*`, `tasks/collectors.py`, `services/metrics.py`,
`api/routers/metrics.py`), тег `metrics`:

* `POST /publications/{publication_id}/metrics/manual` `ManualMetricsCreate` → 201 `ManualMetricsResult`
  (operator+) · 404 publication_not_found · 422 metrics_rejected (`details.reason` =
  `MetricsRejectReason`). Ручной ввод — последняя ступень цепочки VK и единственный способ для
  YouTube без ключа API; снимок пишется с `source='manual'`, `created_by`, и сразу обновляет
  `publications.latest_*` и интерполированные `views_24h/72h/7d/14d/30d`;
* `DELETE /publications/{publication_id}/metrics/{snapshot_id}` → 204 (operator+): удалить ошибочный
  ручной снимок · 404 snapshot_not_found · 409 metrics_snapshot_not_manual (снимки коллекторов не
  удаляются);
* `GET /metrics/manual-due` `?platform&channel_id&limit&offset` → `ManualDuePage` (viewer+): публикации,
  которые ждут ручного ввода (`publications.manual_due_at <= now()`), самые просроченные первыми.

Решения владельца на M4 (не в этом прогоне: YouTube Analytics OAuth, M4-D):

* YouTube: только YouTube Data API `videos.list?part=statistics,status` по ключу **куста**
  (`clusters.youtube_api_key_secret`, иначе секрет `YOUTUBE_API_KEY`). Пачки по 50 id, 1 единица за
  пачку, учёт в `api_quota_usage` (`api='youtube_data'`, сутки квоты по America/Los_Angeles).
  Без ключа коллектор `yt_data` в состоянии `disabled` с причиной `no_api_key` и понятным текстом,
  публикации YouTube получают `manual_due_at` (ручной ввод).
* VK: `ytdlp` (метаданные клипа) → `manual`. `vk_api` зарезервирован (нужен токен сообщества).
* Все HTTP-вызовы — через `collectors/http.py`: таймаут 15 с, 3 ретрая, прокси `collectors_proxy_url`.
* Опрос по лестнице `metrics_poll_ladder_hours` (1…168 ч), дальше раз в сутки до 30 дней и раз в
  неделю до 90; значения на ровный возраст — линейная интерполяция между снимками, без экстраполяции.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from autoshorts.api.schemas.common import ApiModel, PageMeta, RequestModel
from autoshorts.api.schemas.jobs import ChannelRef
from autoshorts.domain.enums import MetricsSource, Platform, PublicationStatus

__all__ = [
    "COLLECTOR_CHAINS",
    "METRICS_LADDER_DAILY_UNTIL_DAYS",
    "METRICS_LADDER_WEEKLY_UNTIL_DAYS",
    "METRICS_OVERDUE_GRACE_MIN",
    "METRICS_VALUE_MAX",
    "YOUTUBE_API_KEY_SECRET_DEFAULT",
    "YT_DAILY_QUOTA_UNITS",
    "YT_QUOTA_TIMEZONE",
    "YT_VIDEOS_LIST_BATCH",
    "YT_VIDEOS_LIST_COST_UNITS",
    "CollectorReason",
    "CollectorState",
    "CollectorStatus",
    "ManualDueItem",
    "ManualDuePage",
    "ManualMetricsCreate",
    "ManualMetricsResult",
    "MetricsRejectReason",
    "MetricsSnapshot",
    "PublicationFreshness",
]

# ---------------------------------------------------------------- константы коллекторов

YOUTUBE_API_KEY_SECRET_DEFAULT = "YOUTUBE_API_KEY"
"""Секрет ключа YouTube Data API, если у куста не задан свой `clusters.youtube_api_key_secret`."""
YT_VIDEOS_LIST_BATCH = 50
"""`videos.list` принимает до 50 id за вызов."""
YT_VIDEOS_LIST_COST_UNITS = 1
YT_DAILY_QUOTA_UNITS = 10_000
"""Квота проекта Google Cloud по умолчанию; жёсткий стоп опроса — настройка `yt_quota_hard_units`."""
YT_QUOTA_TIMEZONE = "America/Los_Angeles"
"""Сутки квоты YouTube: сброс в полночь по тихоокеанскому времени (`api_quota_usage.quota_day`)."""
METRICS_LADDER_DAILY_UNTIL_DAYS = 30
METRICS_LADDER_WEEKLY_UNTIL_DAYS = 90
METRICS_OVERDUE_GRACE_MIN = 60
"""Публикация «устарела» (`PublicationFreshness.stale`), если `next_poll_at` просрочен больше чем на час."""
METRICS_VALUE_MAX = 100_000_000_000

COLLECTOR_CHAINS: dict[Platform, tuple[MetricsSource, ...]] = {
    Platform.YOUTUBE: (MetricsSource.YT_DATA, MetricsSource.MANUAL),
    Platform.VK: (MetricsSource.YTDLP, MetricsSource.MANUAL),
}
"""Цепочка коллекторов по площадке: следующий берётся, когда предыдущий выключен или не справился."""


class CollectorState(StrEnum):
    """Состояние коллектора (`collector_state.state`)."""

    OK = "ok"  # последний прогон успешен
    IDLE = "idle"  # опрашивать нечего или ещё не запускался
    DISABLED = "disabled"  # выключен: нет ключа, не настроен
    DEGRADED = "degraded"  # работает частично: мягкий порог квоты, часть ошибок
    PAUSED = "paused"  # квота исчерпана — до `paused_until` (сброс суток квоты)
    FAILING = "failing"  # подряд ошибки, данные не обновляются


class CollectorReason(StrEnum):
    """Причина не-ok состояния (`collector_state.reason`)."""

    NO_API_KEY = "no_api_key"
    QUOTA_EXHAUSTED = "quota_exhausted"
    QUOTA_SOFT_LIMIT = "quota_soft_limit"
    NETWORK_ERROR = "network_error"
    HTTP_ERROR = "http_error"
    AUTH_ERROR = "auth_error"  # ключ отклонён (400 keyInvalid, 403 accessNotConfigured)
    TOOL_MISSING = "tool_missing"  # нет yt-dlp в окружении
    NOT_CONFIGURED = "not_configured"
    MANUAL_ONLY = "manual_only"


COLLECTOR_STATE_LABELS: dict[CollectorState, str] = {
    CollectorState.OK: "Работает",
    CollectorState.IDLE: "Ждёт работы",
    CollectorState.DISABLED: "Выключен",
    CollectorState.DEGRADED: "Работает с ошибками",
    CollectorState.PAUSED: "На паузе до сброса квоты",
    CollectorState.FAILING: "Не работает",
}
COLLECTOR_REASON_LABELS: dict[CollectorReason, str] = {
    CollectorReason.NO_API_KEY: "Нет ключа YouTube Data API: задайте секрет в Настройках → Провайдеры",
    CollectorReason.QUOTA_EXHAUSTED: "Квота API на сутки исчерпана",
    CollectorReason.QUOTA_SOFT_LIMIT: "Квота API близка к пределу",
    CollectorReason.NETWORK_ERROR: "Сеть недоступна (проверьте прокси коллекторов)",
    CollectorReason.HTTP_ERROR: "Площадка отвечает ошибкой",
    CollectorReason.AUTH_ERROR: "Ключ API отклонён площадкой",
    CollectorReason.TOOL_MISSING: "Не установлен yt-dlp",
    CollectorReason.NOT_CONFIGURED: "Коллектор не настроен",
    CollectorReason.MANUAL_ONLY: "Только ручной ввод",
}


class CollectorStatus(ApiModel):
    """Строка `collector_state` + счётчики публикаций, для плашки свежести, Обзора и Настроек."""

    collector: MetricsSource
    cluster_id: str = Field(description="Куст (свой ключ YouTube на куст); 'default' — общий")
    state: CollectorState
    reason: CollectorReason | None
    message: str | None = Field(description="Пояснение для человека, по-русски")
    key_secret: str | None = Field(
        description="Имя секрета ключа API (yt_data); значение никогда не отдаётся"
    )
    key_set: bool | None = Field(description="Задан ли секрет ключа (null — коллектору ключ не нужен)")
    last_run_at: datetime | None
    last_ok_at: datetime | None
    last_error_at: datetime | None
    last_error: str | None
    consecutive_failures: int
    paused_until: datetime | None
    publications_total: int = Field(description="Публикаций, которые ведёт этот коллектор")
    publications_due: int = Field(description="Из них с наступившим next_poll_at")


class PublicationFreshness(ApiModel):
    """Свежесть метрик одной публикации (`Publication.freshness`, карточка ролика)."""

    collector: MetricsSource | None = Field(description="Коллектор, который сейчас ведёт публикацию")
    collector_state: CollectorState | None
    collector_reason: CollectorReason | None
    last_polled_at: datetime | None
    last_captured_at: datetime | None = Field(description="Время последнего снимка (любой источник)")
    next_poll_at: datetime | None
    poll_step: int = Field(description="Ступень лестницы опроса")
    metrics_age_min: int | None = Field(description="Минут с последнего снимка")
    stale: bool = Field(description="next_poll_at просрочен больше чем на METRICS_OVERDUE_GRACE_MIN")
    manual_due: bool = Field(description="Ждёт ручного ввода (manual_due_at <= now)")
    manual_due_at: datetime | None
    poll_failures: int
    last_poll_error: str | None


class MetricsSnapshot(ApiModel):
    id: int
    publication_id: int
    captured_at: datetime
    age_hours: float | None
    views: int | None
    likes: int | None
    comments: int | None
    shares: int | None
    engaged_views: int | None
    source: MetricsSource
    created_by: int | None = Field(description="Ручной ввод: кто")
    note: str | None


class ManualMetricsCreate(RequestModel):
    """Ручной ввод счётчиков (VK Клипы, YouTube без ключа). Пусто в `captured_at` — сейчас."""

    captured_at: datetime | None = Field(
        default=None, description="Когда сняты цифры; не раньше публикации и не в будущем"
    )
    views: int = Field(ge=0, le=METRICS_VALUE_MAX)
    likes: int | None = Field(default=None, ge=0, le=METRICS_VALUE_MAX)
    comments: int | None = Field(default=None, ge=0, le=METRICS_VALUE_MAX)
    shares: int | None = Field(default=None, ge=0, le=METRICS_VALUE_MAX)
    note: str | None = Field(default=None, max_length=500)


class MetricsRejectReason(StrEnum):
    """`details.reason` у 422 metrics_rejected."""

    BEFORE_PUBLICATION = "before_publication"
    IN_FUTURE = "in_future"
    PUBLICATION_DELETED = "publication_deleted"


class ManualMetricsResult(ApiModel):
    snapshot: MetricsSnapshot
    freshness: PublicationFreshness
    warnings: list[str] = Field(
        description="Коды предупреждений: views_decreased (меньше прошлого снимка), "
        "large_jump (рост больше чем в 20 раз)"
    )


class ManualDueItem(ApiModel):
    publication_id: int
    job_id: int
    video_uid: str
    platform: Platform
    url: str | None
    channel: ChannelRef
    status: PublicationStatus
    published_at: datetime | None
    age_hours: float | None
    manual_due_at: datetime
    last_captured_at: datetime | None
    last_views: int | None
    collector_reason: CollectorReason | None = Field(description="Почему не собралось автоматически")


class ManualDuePage(PageMeta):
    items: list[ManualDueItem]
