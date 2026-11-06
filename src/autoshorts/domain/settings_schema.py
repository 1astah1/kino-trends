"""Схема рантайм-настроек (docs/ARCHITECTURE.md §8.3).

Дефолты живут здесь, в таблице `settings` хранятся только переопределения (`key`, `value JSONB`).
Ключи совпадают с CHECK `settings.key ~ '^[a-z][a-z0-9_]{2,63}$'`.

Использование:
    specs = setting_specs()                      # ключ -> SettingSpec (дефолт, описание, группа)
    value = validate_setting_value("render_crf", 21)   # ValueError: неизвестный ключ или значение
    rs = RuntimeSettings.from_overrides({"render_crf": 21})
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from autoshorts.domain.enums import Platform, ResourceProfile

# ---------------------------------------------------------------- пресеты мёртвых зон (ТЗ §6)


class CanvasSize(BaseModel):
    model_config = ConfigDict(extra="forbid")
    w: int = Field(gt=0)
    h: int = Field(gt=0)


class Rect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x1: int
    y1: int
    x2: int
    y2: int


class DeadZone(Rect):
    id: str = Field(pattern=r"^[a-z0-9_]{2,40}$")


class SafeZonePreset(BaseModel):
    """Форма `GET /meta/safe-zones` (§5.4). Координаты в пикселях канвы 1080×1920."""

    model_config = ConfigDict(extra="forbid")
    platform: Platform
    canvas: CanvasSize
    dead_zones: list[DeadZone]
    conservative_safe_rect: Rect
    source_note: str


DEFAULT_SAFE_ZONE_PRESETS: list[dict[str, Any]] = [
    {
        "platform": "youtube",
        "canvas": {"w": 1080, "h": 1920},
        "dead_zones": [
            {"id": "top_bar", "x1": 0, "y1": 0, "x2": 1080, "y2": 170},
            {"id": "bottom_ui", "x1": 0, "y1": 1500, "x2": 1080, "y2": 1920},
            {"id": "right_rail", "x1": 930, "y1": 0, "x2": 1080, "y2": 1920},
        ],
        "conservative_safe_rect": {"x1": 90, "y1": 380, "x2": 990, "y2": 1540},
        "source_note": "ТЗ §6, community-derived",
    },
    {
        "platform": "vk",
        "canvas": {"w": 1080, "h": 1920},
        "dead_zones": [
            {"id": "top_10pct", "x1": 0, "y1": 0, "x2": 1080, "y2": 192},
            {"id": "bottom_20pct", "x1": 0, "y1": 1536, "x2": 1080, "y2": 1920},
        ],
        "conservative_safe_rect": {"x1": 90, "y1": 192, "x2": 990, "y2": 1536},
        "source_note": "ТЗ §6: VK Клипы — верхние 10% и нижние 20% кадра заняты интерфейсом",
    },
]


# ---------------------------------------------------------------- настройки


class ClipScoreWeights(BaseModel):
    """Веса итогового скоринга кандидата нарезки (§6.3.4)."""

    model_config = ConfigDict(extra="forbid")
    llm: float = Field(0.55, ge=0, le=1)
    feat: float = Field(0.20, ge=0, le=1)
    len_fit: float = Field(0.15, ge=0, le=1)
    trend: float = Field(0.10, ge=0, le=1)


def _field(default: Any, description: str, group: str, **kw: Any) -> Any:
    return Field(default, description=description, json_schema_extra={"group": group}, **kw)


class RuntimeSettings(BaseModel):
    """Все настройки v1 с дефолтами. Порядок полей = порядок в UI внутри группы."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    # общее
    business_timezone: str = _field("Europe/Moscow", "Часовой пояс бизнес-дня (бюджет, ролл-апы)", "general")
    resource_profile: ResourceProfile = _field(
        ResourceProfile.SHARED_DEV, "Профиль ресурсов: shared_dev или dedicated_small (§8.1)", "worker"
    )
    # диск
    disk_soft_min_gb: float = _field(12.0, "Меньше — новые загрузки не принимаются (507)", "worker", gt=0)
    disk_hard_min_gb: float = _field(8.0, "Меньше — пауза тяжёлых задач и алерт", "worker", gt=0)
    disk_emergency_gb: float = _field(5.0, "Меньше — аварийная уборка", "worker", gt=0)
    media_max_gb: float | None = _field(None, "Квота MEDIA_ROOT, ГБ (пусто — без квоты)", "worker", gt=0)
    # допуск тяжёлых задач
    heavy_max_load1: float = _field(3.0, "Максимальный load1 для старта тяжёлой задачи", "worker", gt=0)
    heavy_min_mem_available_mb: int = _field(1200, "Минимум MemAvailable, МБ", "worker", ge=0)
    heavy_max_psi_mem10: float = _field(10.0, "Максимум PSI memory some avg10", "worker", ge=0)
    heavy_max_psi_cpu10: float = _field(40.0, "Максимум PSI cpu some avg10", "worker", ge=0)
    # рендер
    ffmpeg_threads: int = _field(2, "Потоки ffmpeg в тяжёлом слоте", "render", ge=1, le=8)
    render_preset: str = _field("veryfast", "Пресет libx264 финального рендера", "render")
    render_crf: int = _field(23, "CRF libx264 финального рендера", "render", ge=0, le=51)
    preview_preset: str = _field("veryfast", "Пресет libx264 превью", "render")
    # метрики и аналитика
    metrics_poll_ladder_hours: list[int] = _field(
        [1, 2, 4, 8, 12, 24, 36, 48, 72, 96, 120, 168],
        "Лестница опроса метрик публикации, часы от публикации (§6.5)",
        "analytics",
        min_length=1,
    )
    fx_usd_rub: float = _field(95.0, "Курс USD→RUB для отчётов (вручную)", "analytics", gt=0)
    ab_min_videos_per_variant: int = _field(20, "A/B: минимум зрелых роликов в варианте", "analytics", ge=1)
    ab_min_age_hours: int = _field(72, "A/B: минимальный возраст ролика, часы", "analytics", ge=1)
    # нарезка
    clip_author_share_min: float = _field(
        0.30, "Минимальная доля авторского слоя для чужих источников (§6.3.8)", "clipping", ge=0, le=1
    )
    clip_author_share_gate_for_own: bool = _field(
        False, "Применять гейт author_share к своим источникам", "clipping"
    )
    clip_score_weights: ClipScoreWeights = _field(
        ClipScoreWeights(), "Веса скоринга кандидатов нарезки", "clipping"
    )
    source_max_hours: float = _field(3.0, "Максимальная длительность источника, часы", "clipping", gt=0)
    upload_max_gb: float = _field(6.0, "Максимальный размер загрузки, ГБ", "clipping", gt=0)
    # QC
    similarity_warn_threshold: float = _field(
        0.90, "Порог похожести на свои ролики (dHash) для предупреждения", "qc", ge=0, le=1
    )
    qc_subtitle_sync_enabled: bool = _field(False, "Проверять синхрон субтитров через ASR", "qc")
    # хранение
    proxy_retain_days: int = _field(
        14, "Сколько дней хранить прокси источника после ready", "retention", ge=1
    )
    original_retain_days: int = _field(30, "Сколько дней хранить оригинал загрузки", "retention", ge=1)
    work_artifacts_retain_days: int = _field(
        7, "Сколько дней хранить рабочие файлы ролика", "retention", ge=1
    )
    rejected_jobs_retain_days: int = _field(
        3, "Сколько дней хранить final/preview отклонённых", "retention", ge=1
    )
    # уведомления
    review_chat_id: str | None = _field(None, "Telegram-чат приёмки", "notifications")
    alert_chat_id: str | None = _field(None, "Telegram-чат алертов", "notifications")
    telegram_proxy_url: str | None = _field(None, "Прокси для api.telegram.org", "notifications")
    # трендоскаут и коллекторы
    outlier_attention: float = _field(3.0, "Порог тира attention (×медиана)", "trends", gt=0)
    outlier_format_works: float = _field(10.0, "Порог тира format_works (×медиана)", "trends", gt=0)
    yt_quota_soft_pct: int = _field(80, "Порог алерта по квоте YouTube Data API, %", "trends", ge=1, le=100)
    yt_quota_hard_units: int = _field(8000, "Жёсткий стоп по квоте YouTube Data API, единиц", "trends", ge=1)
    collectors_proxy_url: str | None = _field(None, "Прокси для googleapis/VK/RSS", "trends")
    kino_trends_url: str | None = _field(
        None, "Адрес Kino-trends (трендоскаут), например http://127.0.0.1:18320", "trends"
    )
    kino_trends_public_url: str | None = _field(
        None, "Публичный адрес Kino-trends для ссылки из консоли", "trends"
    )
    kino_trends_timeout_sec: int = _field(20, "Таймаут запросов к Kino-trends, с", "trends", ge=1, le=120)
    yt_upload_units: int = _field(
        1600, "Цена одной заливки videos.insert в единицах квоты YouTube Data API", "publishing", ge=1
    )
    # атрибуция
    redirect_default_product: str = _field(
        "umka",
        "Продукт для неизвестного кода в редиректоре",
        "attribution",
        pattern=r"^[a-z0-9][a-z0-9_-]{1,39}$",
    )
    # баннер
    safe_zone_presets: list[SafeZonePreset] = _field(
        [SafeZonePreset.model_validate(p) for p in DEFAULT_SAFE_ZONE_PRESETS],
        "Пресеты мёртвых зон площадок",
        "banner",
    )
    # безопасность
    allow_mock_in_prod: bool = _field(False, "Разрешить mock-провайдеры при APP_ENV=prod", "security")

    @classmethod
    def from_overrides(cls, overrides: dict[str, Any]) -> RuntimeSettings:
        """Дефолты + переопределения из таблицы `settings`. Неизвестные ключи -> ValidationError."""
        return cls.model_validate(overrides)


SETTING_KEY_PATTERN = r"^[a-z][a-z0-9_]{2,63}$"


@dataclass(frozen=True)
class SettingSpec:
    key: str
    default: Any  # JSON-совместимое значение
    description: str
    group: str
    json_schema: dict[str, Any]


def setting_specs() -> dict[str, SettingSpec]:
    """Ключ -> описание настройки, в порядке объявления."""
    defaults = RuntimeSettings().model_dump(mode="json")
    schema = RuntimeSettings.model_json_schema()
    props: dict[str, Any] = schema.get("properties", {})
    out: dict[str, SettingSpec] = {}
    for key, field in RuntimeSettings.model_fields.items():
        extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
        out[key] = SettingSpec(
            key=key,
            default=defaults[key],
            description=field.description or "",
            group=str(extra.get("group", "general")),
            json_schema=props.get(key, {}),
        )
    return out


def validate_setting_value(key: str, value: Any) -> Any:
    """Проверяет одно переопределение; возвращает JSON-совместимое нормализованное значение."""
    if key not in RuntimeSettings.model_fields:
        raise ValueError(f"unknown setting: {key}")
    try:
        rs = RuntimeSettings.model_validate({key: value})
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
    return rs.model_dump(mode="json")[key]
