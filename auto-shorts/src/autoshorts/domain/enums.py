"""Единый словарь enum-значений для БД, API и TS (docs/ARCHITECTURE.md §4.3).

Значения совпадают с CHECK-ограничениями миграций 0002–0009 (сверяет tests/contract/test_enums_vs_sql.py).
Все значения — латиница `^[a-z0-9_]+$`; русские подписи для UI лежат отдельно в `LABELS`
и отдаются через `GET /api/v1/meta/enums`.
"""

from __future__ import annotations

from enum import StrEnum

# ---------------------------------------------------------------- ролики и приёмка


class JobKind(StrEnum):
    GENERATE = "generate"
    CLIP = "clip"


class JobStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    PROCESSING = "processing"
    QC_FAILED = "qc_failed"
    PENDING_REVIEW = "pending_review"
    REWORK = "rework"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"


JOB_TERMINAL_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.PUBLISHED, JobStatus.REJECTED, JobStatus.CANCELLED}
)


class JobStage(StrEnum):
    # generate
    SCRIPT = "script"
    VOICE = "voice"
    VISUALS = "visuals"
    # clip
    CLIP_FETCH = "clip_fetch"
    CLIP_ANALYZE = "clip_analyze"
    AUTHOR_SCRIPT = "author_script"
    AUTHOR_VOICE = "author_voice"
    # общие
    RENDER = "render"
    QC = "qc"
    # после одобрения
    PACKAGE = "package"


GENERATE_STAGES: tuple[JobStage, ...] = (
    JobStage.SCRIPT,
    JobStage.VOICE,
    JobStage.VISUALS,
    JobStage.RENDER,
    JobStage.QC,
)
# clip_fetch выполняется только для источников по URL
CLIP_STAGES: tuple[JobStage, ...] = (
    JobStage.CLIP_FETCH,
    JobStage.CLIP_ANALYZE,
    JobStage.AUTHOR_SCRIPT,
    JobStage.AUTHOR_VOICE,
    JobStage.RENDER,
    JobStage.QC,
)


class ReviewDecision(StrEnum):
    APPROVE = "approve"
    REWORK = "rework"
    REJECT = "reject"
    OVERRIDE_QC = "override_qc"
    UNDO = "undo"


class DecidedVia(StrEnum):
    WEB = "web"
    TELEGRAM = "telegram"
    API = "api"


class RejectReason(StrEnum):
    """Коды из legacy `models.REJECT_REASONS` + новые. `tehnicheskiy_sboy` убран: это status=failed."""

    HOOK_SLABYY = "hook_slabyy"
    VIZUAL_ARTEFAKTY = "vizual_artefakty"
    SUBTITRY_RASSINHRON = "subtitry_rassinhron"
    BANNER_PEREKRYVAET = "banner_perekryvaet"
    ZVUK_PROBLEMA = "zvuk_problema"
    ZAPRESHCHENNAYA_FRAZA = "zapreshchennaya_fraza"
    DUBLIKAT = "dublikat"
    NE_TA_DLITELNOST = "ne_ta_dlitelnost"
    DRUGOE = "drugoe"
    AVTORSKIY_SLOY_SLABYY = "avtorskiy_sloy_slabyy"
    PRAVA_SOMNITELNY = "prava_somnitelny"
    POVTOR_SHABLONA = "povtor_shablona"


class QcCheck(StrEnum):
    SILENCE = "silence"
    CLIPPING = "clipping"
    LOUDNESS = "loudness"
    BLACK_FRAMES = "black_frames"
    FROZEN_FRAMES = "frozen_frames"
    DURATION = "duration"
    SUBTITLE_SYNC = "subtitle_sync"
    AUTHOR_SHARE = "author_share"
    BANNER_FACE_OVERLAP = "banner_face_overlap"
    FOREIGN_WATERMARK = "foreign_watermark"
    LIBRARY_SIMILARITY = "library_similarity"
    FORBIDDEN_PHRASE = "forbidden_phrase"


class ReviewFlagCode(StrEnum):
    """Флаги экрана приёмки (ReviewContext.flags, §5.4)."""

    FORBIDDEN_PHRASE = "forbidden_phrase"
    DURATION_DEAD_ZONE = "duration_dead_zone"
    BANNER_IN_DEAD_ZONE = "banner_in_dead_zone"
    LIBRARY_SIMILARITY = "library_similarity"
    TTS_MOCK = "tts_mock"
    AUTHOR_SHARE_LOW = "author_share_low"
    FOREIGN_WATERMARK = "foreign_watermark"


class WarningSeverity(StrEnum):
    """Важность флагов и предупреждений (приёмка, валидация баннера). Не путать с AlertSeverity."""

    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class ArtifactKind(StrEnum):
    SCRIPT_JSON = "script_json"
    WORDS_JSON = "words_json"
    VOICE = "voice"
    SCENE_IMAGE = "scene_image"
    SCENES_VIDEO = "scenes_video"
    BANNER_PNG = "banner_png"
    SUBTITLES_ASS = "subtitles_ass"
    FINAL = "final"
    PREVIEW = "preview"
    POSTER = "poster"
    AUTHOR_VOICE = "author_voice"
    EDL_JSON = "edl_json"
    CROP_CMD = "crop_cmd"
    QC_REPORT = "qc_report"


class MediaKind(StrEnum):
    """`GET /jobs/{job_id}/media/{media_kind}`."""

    PREVIEW = "preview"
    FINAL = "final"
    POSTER = "poster"
    VOICE = "voice"
    BANNER_IMPULSE = "banner_impulse"
    BANNER_FINAL = "banner_final"


class AuthorLayerMode(StrEnum):
    LLM_DRAFT = "llm_draft"
    OPERATOR_TEXT = "operator_text"


class AuthorVoiceSource(StrEnum):
    TTS = "tts"
    OPERATOR_RECORDING = "operator_recording"


class CaptionsMode(StrEnum):
    BOTH = "both"
    SOURCE = "source"
    AUTHOR = "author"
    NONE = "none"


class AuthorVoiceSegment(StrEnum):
    COLD_OPEN = "cold_open"
    INSERT_0 = "insert_0"
    INSERT_1 = "insert_1"
    OUTRO = "outro"


class ReframeMode(StrEnum):
    AUTO = "auto"
    TRACK = "track"
    CENTER = "center"
    FIT_BLUR = "fit_blur"


# ---------------------------------------------------------------- очередь задач


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResourceClass(StrEnum):
    HEAVY = "heavy"
    LIGHT = "light"


class DeferReason(StrEnum):
    DISK_LOW = "disk_low"
    LOAD_HIGH = "load_high"
    MEM_LOW = "mem_low"
    BUDGET_EXHAUSTED = "budget_exhausted"
    QUOTA_EXHAUSTED = "quota_exhausted"


class TaskKind(StrEnum):
    """Известные виды задач (§6.1, 0009_seed). Колонка `tasks.kind` — свободный TEXT: реестр
    обработчиков (`tasks/registry.py`) может добавлять виды; в API поле `kind` остаётся строкой."""

    JOB_STAGE = "job_stage"
    PACKAGE_BUILD = "package_build"
    SOURCE_DOWNLOAD = "source_download"
    SOURCE_PREPARE = "source_prepare"
    SOURCE_TRANSCRIBE_CHUNK = "source_transcribe_chunk"
    SOURCE_TRANSCRIBE_CLOUD = "source_transcribe_cloud"
    SOURCE_SELECT = "source_select"
    SOURCE_SCENES = "source_scenes"
    COMPETITOR_ANALYZE = "competitor_analyze"
    VOICE_SAMPLE = "voice_sample"
    METRICS_POLL = "metrics_poll"
    COMPETITOR_DISCOVERY = "competitor_discovery"
    COMPETITOR_STATS_POLL = "competitor_stats_poll"
    OUTLIER_COMPUTE = "outlier_compute"
    YT_ANALYTICS_PULL = "yt_analytics_pull"
    CHANNEL_STATS = "channel_stats"
    STATS_ROLLUP = "stats_rollup"
    CLEANUP = "cleanup"
    ALERTS_EVAL = "alerts_eval"
    TASK_REAPER = "task_reaper"
    BASELINES_REFRESH = "baselines_refresh"
    BRIEF_FIELD_STATS = "brief_field_stats"
    RSS_HEALTH_CHECK = "rss_health_check"


# ---------------------------------------------------------------- доступ и эксплуатация


class Role(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class ActorKind(StrEnum):
    USER = "user"
    API_TOKEN = "api_token"
    BOT = "bot"
    SYSTEM = "system"
    WEBHOOK = "webhook"


class ApiTokenScope(StrEnum):
    READ = "read"
    INGEST = "ingest"


class ProviderKind(StrEnum):
    LLM = "llm"
    TTS = "tts"
    IMAGE = "image"
    ASR = "asr"
    VISION = "vision"
    FACES = "faces"


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    OPEN = "open"
    ACKED = "acked"
    RESOLVED = "resolved"


class AlertCode(StrEnum):
    """Коды алертов `alerts_eval` (§8.5). Колонка `alerts.code` — TEXT, список может расти."""

    DISK_LOW = "disk_low"
    WORKER_DOWN = "worker_down"
    HEAVY_SLOT_STUCK = "heavy_slot_stuck"
    BUDGET_80 = "budget_80"
    BUDGET_PAUSED = "budget_paused"
    PROVIDER_FAILING = "provider_failing"
    COLLECTOR_FAILING = "collector_failing"
    QUOTA_80 = "quota_80"
    PUBLICATION_REMOVED = "publication_removed"
    CHANNEL_SANCTION_SIGNAL = "channel_sanction_signal"
    CHANNEL_REACH_DROP = "channel_reach_drop"
    QC_FAIL_SPIKE = "qc_fail_spike"
    MIGRATIONS_PENDING = "migrations_pending"
    PRODUCT_WEBHOOK_SILENT = "product_webhook_silent"
    TTS_MOCK_IN_PROD = "tts_mock_in_prod"
    AD_MARKING_DEADLINE = "ad_marking_deadline"
    PACKAGE_BLOCKED = "package_blocked"  # одобренный ролик без пакета: права, оригинальность, erid
    AUTOPUBLISH_FAILED = "autopublish_failed"  # автозалив не прошёл: метаданные, права, канал
    AUTOPUBLISH_AUTH = "autopublish_auth"  # токен канала отозван или протух: подключить заново
    AUTOPUBLISH_PRIVATE_LOCK = "autopublish_private_lock"  # ролик заперт в private: проект без аудита


class ResourceProfile(StrEnum):
    SHARED_DEV = "shared_dev"
    DEDICATED_SMALL = "dedicated_small"


# ---------------------------------------------------------------- каталог


class ProductKind(StrEnum):
    WEB_SERVICE = "web_service"
    TELEGRAM_BOT = "telegram_bot"
    TELEGRAM_MINI_APP = "telegram_mini_app"
    OTHER = "other"


class ProductStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class RedirectMode(StrEnum):
    LANDING_SPLIT = "landing_split"
    DIRECT_SITE = "direct_site"
    DIRECT_TELEGRAM = "direct_telegram"


class TelegramLinkStyle(StrEnum):
    TG_SCHEME_WITH_FALLBACK = "tg_scheme_with_fallback"
    HTTPS_TME = "https_tme"


class NicheStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    NEEDS_IMPORT = "needs_import"


class NicheVersionSource(StrEnum):
    UI = "ui"
    YAML_IMPORT = "yaml_import"
    MIGRATION = "migration"


class ChannelStatus(StrEnum):
    WARMING = "warming"
    ACTIVE = "active"
    PAUSED = "paused"
    SANCTIONED = "sanctioned"
    TERMINATED = "terminated"
    RETIRED = "retired"


class IncidentKind(StrEnum):
    REACH_DROP = "reach_drop"
    SANCTION = "sanction"
    STRIKE = "strike"
    CLAIM = "claim"
    UNIQUE_PHRASE_TEST = "unique_phrase_test"
    FREEZE = "freeze"
    NOTE = "note"


class AssetKind(StrEnum):
    FONT = "font"
    LOGO = "logo"
    MUSIC = "music"
    IMAGE = "image"
    BROLL = "broll"
    WATERMARK_REF = "watermark_ref"


class BannerTemplateStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class BannerLayer(StrEnum):
    MICRO_BRAND = "micro_brand"
    OFFER = "offer"


class BannerWarningCode(StrEnum):
    OFFER_IN_BOTTOM_DEAD_ZONE = "offer_in_bottom_dead_zone"
    OFFER_UNDER_RIGHT_RAIL = "offer_under_right_rail"
    OFFER_IN_HOOK_WINDOW = "offer_in_hook_window"
    TEXT_SHRUNK_BELOW_MIN = "text_shrunk_below_min"
    MICRO_BRAND_OPACITY_OUT_OF_RANGE = "micro_brand_opacity_out_of_range"
    LAYER_OUTSIDE_CANVAS = "layer_outside_canvas"
    VK_TOP10_BOTTOM20_VIOLATION = "vk_top10_bottom20_violation"


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"


class CampaignAllocation(StrEnum):
    WEIGHTED = "weighted"
    ROUND_ROBIN = "round_robin"
    MANUAL = "manual"


class VariantStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


# ---------------------------------------------------------------- публикация и атрибуция


class Platform(StrEnum):
    YOUTUBE = "youtube"
    VK = "vk"


class PlatformKind(StrEnum):
    SHORT = "short"
    CLIP = "clip"
    VIDEO = "video"


class PublicationStatus(StrEnum):
    LIVE = "live"
    UNLISTED = "unlisted"
    PRIVATE = "private"
    REMOVED_BY_PLATFORM = "removed_by_platform"
    DELETED_BY_US = "deleted_by_us"


class VerifyStatus(StrEnum):
    PENDING = "pending"
    OK = "ok"
    CHANNEL_MISMATCH = "channel_mismatch"
    NOT_FOUND = "not_found"
    UNVERIFIABLE = "unverifiable"


class PackageStatus(StrEnum):
    READY = "ready"
    DOWNLOADED = "downloaded"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"


class AutopublishState(StrEnum):
    """Автозалив пакета через API площадки (0019): очередь → заливка → готово, ошибка или отмена."""

    SCHEDULED = "scheduled"
    UPLOADING = "uploading"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PublishMode(StrEnum):
    """Как канал получает ролики: оператор заливает руками по пакету или система через API."""

    MANUAL = "manual"
    AUTO = "auto"


class VideoPrivacy(StrEnum):
    PUBLIC = "public"
    UNLISTED = "unlisted"
    PRIVATE = "private"


class ChecklistCode(StrEnum):
    FILE_DOWNLOADED = "file_downloaded"
    RIGHT_ACCOUNT = "right_account"
    TEXTS_PASTED = "texts_pasted"
    HASHTAGS = "hashtags"
    RELATED_VIDEO = "related_video"
    PINNED_COMMENT = "pinned_comment"
    AI_DISCLOSURE = "ai_disclosure"
    AD_MARKING = "ad_marking"  # «Реклама · erid» в кадре и описании
    CONTENT_ID = "content_id"  # лицензированный источник: проверка allowlist/риска Content ID
    PUBLISHED = "published"


class TrackingLevel(StrEnum):
    PACKAGE = "package"
    PUBLICATION = "publication"
    CHANNEL = "channel"
    CAMPAIGN = "campaign"
    MANUAL = "manual"


class ClickRoute(StrEnum):
    LANDING = "landing"
    TO_TELEGRAM = "to_telegram"
    TO_SITE = "to_site"
    DIRECT = "direct"
    UNKNOWN_CODE = "unknown_code"


class ConversionEventType(StrEnum):
    BOT_START = "bot_start"
    TMA_OPEN = "tma_open"
    SITE_VISIT = "site_visit"
    SIGNUP = "signup"
    TRIAL_START = "trial_start"
    LEAD = "lead"
    FIRST_PAYMENT = "first_payment"
    PAYMENT = "payment"
    REFUND = "refund"


class MetricsSource(StrEnum):
    YT_DATA = "yt_data"
    YT_ANALYTICS = "yt_analytics"
    VK_API = "vk_api"
    YTDLP = "ytdlp"
    MANUAL = "manual"


class RetentionFailureClass(StrEnum):
    HOOK_DROP = "hook_drop"
    MIDDLE_SAG = "middle_sag"
    NO_LOOP_LIFT = "no_loop_lift"
    NONE = "none"


class AnalyticsBasis(StrEnum):
    ENGAGED = "engaged"
    PUBLIC = "public"


class AnalyticsMode(StrEnum):
    ACTIVITY = "activity"
    COHORT = "cohort"


# ---------------------------------------------------------------- нарезка


class UploadPurpose(StrEnum):
    SOURCE = "source"
    ASSET = "asset"
    RIGHTS_DOC = "rights_doc"


class UploadStatus(StrEnum):
    UPLOADING = "uploading"
    COMPLETE = "complete"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    ABORTED = "aborted"


class SourceOrigin(StrEnum):
    UPLOAD = "upload"
    URL = "url"


class SourceStatus(StrEnum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PREPARING = "preparing"
    TRANSCRIBING = "transcribing"
    SELECTING = "selecting"
    READY = "ready"
    FAILED = "failed"
    ARCHIVED = "archived"


class RightsType(StrEnum):
    """CC-BY-SA и NC не принимаются: несовместимы с промо-баннером (§0.1 п. 14)."""

    OWN = "own"
    LICENSED_WRITTEN = "licensed_written"
    LICENSED_PLATFORM = "licensed_platform"
    CC_BY = "cc_by"
    PUBLIC_DOMAIN = "public_domain"


class AllowlistStatus(StrEnum):
    NOT_NEEDED = "not_needed"
    REQUESTED = "requested"
    ALLOWLISTED = "allowlisted"
    UNKNOWN = "unknown"


class CandidateStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    DISMISSED = "dismissed"
    USED = "used"


class CandidateCreatedByKind(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


# ---------------------------------------------------------------- трендоскаут


class CompetitorPool(StrEnum):
    RU = "ru"
    DONOR_EN = "donor_en"


class DiscoveryMode(StrEnum):
    AUTO = "auto"
    RSS = "rss"
    UUSH = "uush"
    UU = "uu"


class OutlierTier(StrEnum):
    NOISE = "noise"
    ATTENTION = "attention"
    FORMAT_WORKS = "format_works"


class CompetitorVideoStatus(StrEnum):
    WATCHING = "watching"
    DONE = "done"
    ANALYZED = "analyzed"
    IGNORED = "ignored"


class AgeBucket(StrEnum):
    H1 = "1h"
    H3 = "3h"
    H6 = "6h"
    H12 = "12h"
    H24 = "24h"
    H48 = "48h"
    H72 = "72h"
    D7 = "7d"
    MATURE = "mature"


class BriefStatus(StrEnum):
    NEW = "new"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    USED = "used"


# ---------------------------------------------------------------- подписи для UI

LABELS: dict[type[StrEnum], dict[str, str]] = {
    JobKind: {"generate": "Генерация", "clip": "Нарезка"},
    JobStatus: {
        "draft": "Черновик",
        "queued": "В очереди",
        "processing": "В работе",
        "qc_failed": "Не прошёл QC",
        "pending_review": "Ждёт приёмки",
        "rework": "На доработке",
        "approved": "Одобрен",
        "published": "Опубликован",
        "rejected": "Отклонён",
        "failed": "Сбой",
        "cancelled": "Отменён",
    },
    JobStage: {
        "script": "Сценарий",
        "voice": "Озвучка",
        "visuals": "Визуал",
        "clip_fetch": "Загрузка секции",
        "clip_analyze": "Анализ кадра",
        "author_script": "Авторский текст",
        "author_voice": "Авторский голос",
        "render": "Рендер",
        "qc": "QC",
        "package": "Пакет",
    },
    ReviewDecision: {
        "approve": "Одобрить",
        "rework": "На доработку",
        "reject": "Отклонить",
        "override_qc": "Принять вопреки QC",
        "undo": "Отмена решения",
    },
    DecidedVia: {"web": "Веб", "telegram": "Telegram", "api": "API"},
    RejectReason: {
        "hook_slabyy": "Слабый хук",
        "vizual_artefakty": "Артефакты визуала",
        "subtitry_rassinhron": "Рассинхрон субтитров",
        "banner_perekryvaet": "Баннер перекрывает смысл",
        "zvuk_problema": "Проблема со звуком",
        "zapreshchennaya_fraza": "Запрещённая фраза",
        "dublikat": "Дубликат",
        "ne_ta_dlitelnost": "Не та длительность",
        "drugoe": "Другое",
        "avtorskiy_sloy_slabyy": "Слабый авторский слой",
        "prava_somnitelny": "Сомнительные права",
        "povtor_shablona": "Повтор шаблона",
    },
    QcCheck: {
        "silence": "Тишина",
        "clipping": "Клиппинг",
        "loudness": "Громкость",
        "black_frames": "Чёрные кадры",
        "frozen_frames": "Зависший кадр",
        "duration": "Длительность",
        "subtitle_sync": "Синхрон субтитров",
        "author_share": "Доля авторского слоя",
        "banner_face_overlap": "Баннер на лице",
        "foreign_watermark": "Чужой водяной знак",
        "library_similarity": "Похож на свой ролик",
        "forbidden_phrase": "Запрещённая фраза",
    },
    ReviewFlagCode: {
        "forbidden_phrase": "Запрещённая фраза",
        "duration_dead_zone": "Длительность в мёртвой зоне",
        "banner_in_dead_zone": "Баннер в мёртвой зоне",
        "library_similarity": "Похож на свой ролик",
        "tts_mock": "Озвучка-заглушка",
        "author_share_low": "Мало авторского слоя",
        "foreign_watermark": "Чужой водяной знак",
    },
    TaskStatus: {
        "queued": "В очереди",
        "running": "Выполняется",
        "succeeded": "Готово",
        "failed": "Ошибка",
        "cancelled": "Отменено",
    },
    ResourceClass: {"heavy": "Тяжёлая", "light": "Лёгкая"},
    DeferReason: {
        "disk_low": "Мало места на диске",
        "load_high": "Высокая нагрузка",
        "mem_low": "Мало памяти",
        "budget_exhausted": "Бюджет исчерпан",
        "quota_exhausted": "Квота API исчерпана",
    },
    Role: {"admin": "Владелец", "operator": "Оператор", "viewer": "Наблюдатель"},
    ProviderKind: {
        "llm": "LLM",
        "tts": "Озвучка",
        "image": "Картинки",
        "asr": "Распознавание речи",
        "vision": "Зрение",
        "faces": "Лица",
    },
    AlertSeverity: {"info": "Инфо", "warning": "Внимание", "critical": "Критично"},
    AlertStatus: {"open": "Открыт", "acked": "Принят", "resolved": "Закрыт"},
    ProductKind: {
        "web_service": "Веб-сервис",
        "telegram_bot": "Telegram-бот",
        "telegram_mini_app": "Mini App",
        "other": "Другое",
    },
    ProductStatus: {"active": "Активен", "archived": "В архиве"},
    RedirectMode: {
        "landing_split": "Лендинг с выбором",
        "direct_site": "Сразу на сайт",
        "direct_telegram": "Сразу в Telegram",
    },
    NicheStatus: {"active": "Активна", "archived": "В архиве", "needs_import": "Нужен импорт"},
    ChannelStatus: {
        "warming": "Прогрев",
        "active": "Активен",
        "paused": "На паузе",
        "sanctioned": "Санкции",
        "terminated": "Удалён площадкой",
        "retired": "Выведен",
    },
    IncidentKind: {
        "reach_drop": "Падение охвата",
        "sanction": "Санкция",
        "strike": "Страйк",
        "claim": "Клейм",
        "unique_phrase_test": "Тест уникальной фразы",
        "freeze": "Заморозка",
        "note": "Заметка",
    },
    CampaignStatus: {"draft": "Черновик", "active": "Активна", "paused": "На паузе", "ended": "Завершена"},
    VariantStatus: {"draft": "Черновик", "active": "Активен", "paused": "На паузе", "archived": "В архиве"},
    Platform: {"youtube": "YouTube", "vk": "VK"},
    PublicationStatus: {
        "live": "Опубликован",
        "unlisted": "По ссылке",
        "private": "Скрыт",
        "removed_by_platform": "Снят площадкой",
        "deleted_by_us": "Удалён нами",
    },
    VerifyStatus: {
        "pending": "Проверяется",
        "ok": "Подтверждён",
        "channel_mismatch": "Чужой канал",
        "not_found": "Не найден",
        "unverifiable": "Не проверить",
    },
    PackageStatus: {
        "ready": "Готов к заливке",
        "downloaded": "Скачан",
        "published": "Опубликован",
        "superseded": "Заменён",
    },
    ClickRoute: {
        "landing": "Лендинг",
        "to_telegram": "В Telegram",
        "to_site": "На сайт",
        "direct": "Напрямую",
        "unknown_code": "Неизвестный код",
    },
    ConversionEventType: {
        "bot_start": "Старт бота",
        "tma_open": "Открытие Mini App",
        "site_visit": "Визит на сайт",
        "signup": "Регистрация",
        "trial_start": "Начало пробного",
        "lead": "Заявка",
        "first_payment": "Первая оплата",
        "payment": "Оплата",
        "refund": "Возврат",
    },
    RightsType: {
        "own": "Своё",
        "licensed_written": "Лицензия (договор)",
        "licensed_platform": "Лицензия (площадка)",
        "cc_by": "CC BY",
        "public_domain": "Общественное достояние",
    },
    AllowlistStatus: {
        "not_needed": "Не нужен",
        "requested": "Запрошен",
        "allowlisted": "В allowlist",
        "unknown": "Неизвестно",
    },
    SourceStatus: {
        "queued": "В очереди",
        "downloading": "Загрузка",
        "preparing": "Подготовка",
        "transcribing": "Распознавание",
        "selecting": "Отбор моментов",
        "ready": "Готов",
        "failed": "Ошибка",
        "archived": "В архиве",
    },
    CandidateStatus: {
        "proposed": "Предложен",
        "approved": "Утверждён",
        "dismissed": "Отброшен",
        "used": "Использован",
    },
    OutlierTier: {"noise": "Шум", "attention": "Внимание", "format_works": "Формат работает"},
    BriefStatus: {"new": "Новый", "accepted": "Принят", "rejected": "Отклонён", "used": "Использован"},
}

# Публичные имена словарей для `GET /meta/enums` (ключи ответа, латиница snake_case).
ENUM_REGISTRY: dict[str, type[StrEnum]] = {
    "job_kind": JobKind,
    "job_status": JobStatus,
    "job_stage": JobStage,
    "review_decision": ReviewDecision,
    "decided_via": DecidedVia,
    "reject_reason": RejectReason,
    "qc_check": QcCheck,
    "review_flag": ReviewFlagCode,
    "warning_severity": WarningSeverity,
    "media_kind": MediaKind,
    "reframe_mode": ReframeMode,
    "task_status": TaskStatus,
    "resource_class": ResourceClass,
    "defer_reason": DeferReason,
    "role": Role,
    "api_token_scope": ApiTokenScope,
    "provider_kind": ProviderKind,
    "alert_severity": AlertSeverity,
    "alert_status": AlertStatus,
    "resource_profile": ResourceProfile,
    "product_kind": ProductKind,
    "product_status": ProductStatus,
    "redirect_mode": RedirectMode,
    "telegram_link_style": TelegramLinkStyle,
    "niche_status": NicheStatus,
    "channel_status": ChannelStatus,
    "incident_kind": IncidentKind,
    "asset_kind": AssetKind,
    "banner_layer": BannerLayer,
    "banner_warning_code": BannerWarningCode,
    "campaign_status": CampaignStatus,
    "campaign_allocation": CampaignAllocation,
    "variant_status": VariantStatus,
    "platform": Platform,
    "platform_kind": PlatformKind,
    "publication_status": PublicationStatus,
    "verify_status": VerifyStatus,
    "package_status": PackageStatus,
    "checklist_code": ChecklistCode,
    "tracking_level": TrackingLevel,
    "click_route": ClickRoute,
    "conversion_event_type": ConversionEventType,
    "rights_type": RightsType,
    "allowlist_status": AllowlistStatus,
    "source_status": SourceStatus,
    "candidate_status": CandidateStatus,
    "outlier_tier": OutlierTier,
    "brief_status": BriefStatus,
}


def label(member: StrEnum) -> str:
    """Русская подпись значения; если подписи нет — сам код."""
    return LABELS.get(type(member), {}).get(member.value, member.value)


def enum_options(enum_cls: type[StrEnum]) -> list[dict[str, str]]:
    """`[{code, label}]` в порядке объявления — формат `GET /meta/enums`."""
    return [{"code": m.value, "label": label(m)} for m in enum_cls]
