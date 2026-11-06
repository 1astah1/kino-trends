"""Баннерный слой: загруженные креативы владельца, слоты, тайминг, safe zones, предупреждения.

Решение владельца от 2026-09-26 (docs/OWNER-DECISIONS.md, п. 1) главнее ARCHITECTURE.md:
баннер — это **загруженный владельцем креатив** (PNG/JPG/WebP, GIF, APNG, WebM с альфой, MP4).
Система баннер не рисует. Она проверяет и нормализует файл (`pipeline/banner_assets.py`),
вписывает его в слот внутри safe zone (`pipeline/banner_place.py`), показывает в окнах времени
(`pipeline/banner_overlay.py`) и при необходимости добавляет служебный текст: код ролика и
«Реклама · erid» (`pipeline/banner_text.py`). Текстовая плашка системы (`fallback_plate`) —
только запасной вариант, когда креатива нет.

Координаты — пиксели дизайн-канвы 1080×1920 (`CANVAS_W`×`CANVAS_H`). Под другие размеры
выхода геометрия масштабируется в `banner_place.to_output`.

Модуль чистый: не читает файлы и не импортирует `db`/`services`/`api` (правило §3.1).
Идентификаторы и коды — латиница, кириллица только в текстах.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CANVAS_W = 1080
CANVAS_H = 1920

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LAYER_ID_RE = r"^[a-z][a-z0-9_]{0,39}$"
HEX_COLOR_RE = r"^#[0-9A-Fa-f]{6}$"

# Плейсхолдеры служебного текста. Подставляются при рендере конкретного ролика.
PLACEHOLDER_CODE_URL = "{code_url}"  # «u.umka.chat/k7m2q»
PLACEHOLDER_CODE = "{code}"  # «k7m2q»
PLACEHOLDER_ERID = "{erid}"
PLACEHOLDER_ADVERTISER = "{advertiser}"
KNOWN_PLACEHOLDERS = (PLACEHOLDER_CODE_URL, PLACEHOLDER_CODE, PLACEHOLDER_ERID, PLACEHOLDER_ADVERTISER)


# ---------------------------------------------------------------- перечисления


class Anchor(StrEnum):
    TOP_LEFT = "top_left"
    TOP_CENTER = "top_center"
    TOP_RIGHT = "top_right"
    CENTER_LEFT = "center_left"
    CENTER = "center"
    CENTER_RIGHT = "center_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_CENTER = "bottom_center"
    BOTTOM_RIGHT = "bottom_right"

    @property
    def fx(self) -> float:
        """Доля свободного места слева: 0 — прижат влево, 0.5 — по центру, 1 — вправо."""
        return {"left": 0.0, "center": 0.5, "right": 1.0}[self._parts()[1]]

    @property
    def fy(self) -> float:
        return {"top": 0.0, "center": 0.5, "bottom": 1.0}[self._parts()[0]]

    def _parts(self) -> tuple[str, str]:
        if self.value == "center":
            return "center", "center"
        v, h = self.value.split("_", 1)
        return v, h


class FitMode(StrEnum):
    CONTAIN = "contain"  # целиком внутри слота, пропорции сохраняются (по умолчанию)
    COVER = "cover"  # заполняет слот, лишнее обрезается по слоту
    NONE = "none"  # родной размер × scale, лишнее обрезается по слоту


class CreativeKind(StrEnum):
    STATIC = "static"
    ANIMATED = "animated"


class CreativeSourceFormat(StrEnum):
    PNG = "png"
    JPEG = "jpeg"
    WEBP = "webp"
    GIF = "gif"
    APNG = "apng"
    WEBP_ANIMATED = "webp_animated"
    WEBM = "webm"
    MP4 = "mp4"
    MOV = "mov"


class BannerLayerKind(StrEnum):
    CREATIVE = "creative"  # загруженный креатив владельца
    TEXT_CODE = "text_code"  # короткий код ролика: «u.umka.chat/k7m2q»
    TEXT_ERID = "text_erid"  # «Реклама · erid …»
    MICRO_BRAND = "micro_brand"  # домен/@бот сверху (текст или загруженный логотип)
    FALLBACK_PLATE = "fallback_plate"  # запасная текстовая плашка, только если креатива нет


class LayerShow(StrEnum):
    PERSISTENT = "persistent"  # весь ролик
    WITH_CREATIVE = "with_creative"  # в окнах импульса и финального повтора


class TextAttach(StrEnum):
    NONE = "none"  # слот абсолютный
    BELOW_CREATIVE = "below_creative"  # сразу под итоговым прямоугольником креатива
    ABOVE_CREATIVE = "above_creative"
    INSIDE_BOTTOM = "inside_bottom"  # поверх креатива, у нижнего края
    INSIDE_TOP = "inside_top"


class TextAlign(StrEnum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class TimingMode(StrEnum):
    IMPULSE = "impulse"  # импульс на 40–65% + финальный повтор (гипотеза research §7.2)
    PERSISTENT = "persistent"  # весь ролик
    WINDOWS = "windows"  # явные окна в секундах


class SafeZoneStatus(StrEnum):
    OFFICIAL = "official"
    COMMUNITY = "community"
    UNCONFIRMED = "unconfirmed"


class WarningSeverity(StrEnum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class BannerValidationCode(StrEnum):
    """Коды предупреждений редактора и рендера (латиница, стабильны для API и аналитики)."""

    CREATIVE_OUTSIDE_SAFE_ZONE = "creative_outside_safe_zone"
    CREATIVE_IN_BOTTOM_DEAD_ZONE = "creative_in_bottom_dead_zone"
    MICRO_BRAND_IN_TOP_BAR = "micro_brand_in_top_bar"
    CREATIVE_TOO_SMALL_TO_READ = "creative_too_small_to_read"
    CREATIVE_UPSCALED_BLURRY = "creative_upscaled_blurry"
    CREATIVE_ASPECT_DISTORTED = "creative_aspect_distorted"
    ANIMATED_TOO_LONG = "animated_too_long"
    ERID_MISSING_WHEN_REQUIRED = "erid_missing_when_required"
    BANNER_OVERLAPS_CAPTIONS_ZONE = "banner_overlaps_captions_zone"
    FILE_TOO_LARGE = "file_too_large"
    UNSUPPORTED_FORMAT = "unsupported_format"
    # дополнительные
    LAYER_OUTSIDE_CANVAS = "layer_outside_canvas"
    CREATIVE_CLIPPED_BY_SLOT = "creative_clipped_by_slot"
    CREATIVE_MISSING = "creative_missing"
    FALLBACK_PLATE_USED = "fallback_plate_used"
    TEXT_SHRUNK_BELOW_MIN = "text_shrunk_below_min"
    TEXT_TRUNCATED = "text_truncated"
    ANIMATED_CUT_BY_WINDOW = "animated_cut_by_window"
    IMPULSE_WINDOW_STRETCHED = "impulse_window_stretched"
    OFFER_AREA_TOO_TALL = "offer_area_too_tall"
    CAPTIONS_ZONE_TOO_SMALL = "captions_zone_too_small"
    EFFECT_UNSUPPORTED_FOR_ANIMATED = "effect_unsupported_for_animated"


# ---------------------------------------------------------------- геометрия


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Rect(BaseModel):
    """Прямоугольник в пикселях: левый верхний угол (x, y), ширина и высота."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    x: int
    y: int
    w: int = Field(ge=0)
    h: int = Field(ge=0)

    @classmethod
    def from_edges(cls, x1: float, y1: float, x2: float, y2: float) -> Rect:
        x1i, y1i, x2i, y2i = round(x1), round(y1), round(x2), round(y2)
        return cls(x=x1i, y=y1i, w=max(0, x2i - x1i), h=max(0, y2i - y1i))

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    @property
    def area(self) -> int:
        return self.w * self.h

    @property
    def is_empty(self) -> bool:
        return self.w <= 0 or self.h <= 0

    def intersect(self, other: Rect) -> Rect | None:
        x1, y1 = max(self.x, other.x), max(self.y, other.y)
        x2, y2 = min(self.x2, other.x2), min(self.y2, other.y2)
        if x2 <= x1 or y2 <= y1:
            return None
        return Rect(x=x1, y=y1, w=x2 - x1, h=y2 - y1)

    def intersects(self, other: Rect) -> bool:
        return self.intersect(other) is not None

    def contains(self, other: Rect) -> bool:
        return self.x <= other.x and self.y <= other.y and other.x2 <= self.x2 and other.y2 <= self.y2

    def union(self, other: Rect) -> Rect:
        return Rect.from_edges(
            min(self.x, other.x), min(self.y, other.y), max(self.x2, other.x2), max(self.y2, other.y2)
        )

    def translate(self, dx: int, dy: int) -> Rect:
        return Rect(x=self.x + dx, y=self.y + dy, w=self.w, h=self.h)

    def inflate(self, px: int) -> Rect:
        return Rect.from_edges(self.x - px, self.y - px, self.x2 + px, self.y2 + px)

    def edges(self) -> dict[str, int]:
        return {"x1": self.x, "y1": self.y, "x2": self.x2, "y2": self.y2}


class EdgeBox(_Model):
    """Прямоугольник краями (x1, y1)–(x2, y2): так записаны safe zones в research §7.1."""

    x1: int
    y1: int
    x2: int
    y2: int

    @model_validator(mode="after")
    def _ordered(self) -> EdgeBox:
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("x2/y2 must be greater than x1/y1")
        return self

    @property
    def rect(self) -> Rect:
        return Rect.from_edges(self.x1, self.y1, self.x2, self.y2)


class ShadowSpec(_Model):
    """Тень под креативом. Выключена по умолчанию (слот `shadow=None`)."""

    offset_x: int = Field(0, ge=-40, le=40)
    offset_y: int = Field(6, ge=-40, le=40)
    blur_px: int = Field(12, ge=0, le=60)
    opacity: float = Field(0.35, ge=0, le=1)
    color: str = Field("#000000", pattern=HEX_COLOR_RE)


class BannerSlot(_Model):
    """Куда и как вписать слой. x, y, w, h — область слота на канве 1080×1920."""

    x: int = Field(ge=-CANVAS_W, le=2 * CANVAS_W)
    y: int = Field(ge=-CANVAS_H, le=2 * CANVAS_H)
    w: int = Field(gt=0, le=2 * CANVAS_W)
    h: int = Field(gt=0, le=2 * CANVAS_H)
    anchor: Anchor = Anchor.CENTER
    fit: FitMode = FitMode.CONTAIN
    scale: float = Field(1.0, ge=0.05, le=4.0, description="Множитель поверх fit")
    offset_x: int = Field(0, ge=-CANVAS_W, le=CANVAS_W)
    offset_y: int = Field(0, ge=-CANVAS_H, le=CANVAS_H)
    opacity: float = Field(1.0, ge=0, le=1)
    corner_radius_px: int = Field(0, ge=0, le=400)
    shadow: ShadowSpec | None = None
    clip_to_slot: bool = Field(True, description="Обрезать всё, что вышло за слот (cover/none/scale>1)")

    @property
    def rect(self) -> Rect:
        return Rect(x=self.x, y=self.y, w=self.w, h=self.h)


# ---------------------------------------------------------------- креатив


class BannerCreative(_Model):
    """Загруженный и нормализованный креатив (результат `banner_assets.ingest_creative`).

    Пути — относительно MEDIA_ROOT, POSIX. `width`/`height` — размер нормализованного файла.
    Для статики `duration_sec=0`, `fps=0`, `frame_count=1`.
    """

    sha256: str = Field(description="sha256 исходного загруженного файла (контентная адресация)")
    kind: CreativeKind
    source_format: CreativeSourceFormat
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    source_width: int = Field(gt=0)
    source_height: int = Field(gt=0)
    has_alpha: bool
    duration_sec: float = Field(0.0, ge=0)
    fps: float = Field(0.0, ge=0)
    frame_count: int = Field(1, ge=1)
    loop: bool = Field(True, description="Повторять анимацию, если окно показа длиннее креатива")
    source_bytes: int = Field(ge=0)
    normalized_path: str
    normalized_mime: str
    thumb_path: str
    first_frame_path: str
    normalizer_version: int = 1

    @field_validator("sha256")
    @classmethod
    def _sha(cls, v: str) -> str:
        if not SHA256_RE.fullmatch(v):
            raise ValueError("sha256 must be 64 lowercase hex chars")
        return v

    @property
    def aspect(self) -> float:
        return self.width / self.height


# ---------------------------------------------------------------- текст и слои


class TextStyle(_Model):
    """Стиль служебного текста (код, erid, микро-бренд, запасная плашка)."""

    font_file: str = Field("Montserrat-Bold.ttf", pattern=r"^[A-Za-z0-9._-]{1,80}\.(ttf|otf)$")
    max_font_px: int = Field(44, ge=8, le=200)
    min_font_px: int = Field(24, ge=8, le=200)
    color: str = Field("#FFFFFF", pattern=HEX_COLOR_RE)
    text_opacity: float = Field(1.0, ge=0, le=1)
    bg_color: str = Field("#000000", pattern=HEX_COLOR_RE)
    bg_opacity: float = Field(0.55, ge=0, le=1)
    padding_x: int = Field(16, ge=0, le=120)
    padding_y: int = Field(8, ge=0, le=120)
    corner_radius_px: int = Field(10, ge=0, le=200)
    align: TextAlign = TextAlign.CENTER
    max_lines: int = Field(1, ge=1, le=4)
    line_spacing: float = Field(1.12, ge=0.8, le=2.0)
    stroke_px: int = Field(0, ge=0, le=12)
    stroke_color: str = Field("#000000", pattern=HEX_COLOR_RE)
    hug_text: bool = Field(True, description="Подложка по размеру текста, а не во весь слот")

    @model_validator(mode="after")
    def _sizes(self) -> TextStyle:
        if self.min_font_px > self.max_font_px:
            raise ValueError("min_font_px must be <= max_font_px")
        return self


class BannerLayer(_Model):
    id: str = Field(pattern=LAYER_ID_RE)
    kind: BannerLayerKind
    enabled: bool = True
    slot: BannerSlot
    show: LayerShow = LayerShow.WITH_CREATIVE
    creative_sha256: str | None = Field(
        None, description="creative: обязателен; micro_brand: логотип вместо текста (необязательно)"
    )
    text: str | None = Field(None, max_length=400, description="Текст или шаблон с {code_url}, {erid} …")
    style: TextStyle | None = None
    attach: TextAttach = TextAttach.NONE
    attach_gap_px: int = Field(6, ge=0, le=200)
    loop_override: bool | None = Field(None, description="Перебить loop креатива")
    z: int = Field(0, ge=-100, le=100, description="Порядок внутри группы: больше — выше")

    @field_validator("creative_sha256")
    @classmethod
    def _sha(cls, v: str | None) -> str | None:
        if v is not None and not SHA256_RE.fullmatch(v):
            raise ValueError("creative_sha256 must be 64 lowercase hex chars")
        return v

    @model_validator(mode="after")
    def _kind_rules(self) -> BannerLayer:
        text_kinds = {BannerLayerKind.TEXT_CODE, BannerLayerKind.TEXT_ERID, BannerLayerKind.FALLBACK_PLATE}
        if self.kind in text_kinds and not (self.text or "").strip():
            raise ValueError(f"layer {self.id}: kind {self.kind.value} needs text")
        if self.kind == BannerLayerKind.MICRO_BRAND and not (self.text or self.creative_sha256):
            raise ValueError(f"layer {self.id}: micro_brand needs text or creative_sha256")
        if self.attach != TextAttach.NONE and self.kind not in {
            BannerLayerKind.TEXT_CODE,
            BannerLayerKind.TEXT_ERID,
        }:
            raise ValueError(f"layer {self.id}: attach is only for text_code/text_erid")
        return self

    @property
    def is_text(self) -> bool:
        return self.creative_sha256 is None and self.kind != BannerLayerKind.CREATIVE


# ---------------------------------------------------------------- тайминг


class TimeWindow(_Model):
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> TimeWindow:
        if self.end_sec <= self.start_sec:
            raise ValueError("end_sec must be greater than start_sec")
        return self

    @property
    def duration(self) -> float:
        return self.end_sec - self.start_sec


class BannerTiming(_Model):
    """Тайминг группы `with_creative`. Числа — гипотеза для A/B из research §7.2."""

    mode: TimingMode = TimingMode.IMPULSE
    hook_silence_sec: float = Field(2.0, ge=0, le=30, description="Первые секунды без оффера")
    impulse_window_pct: tuple[float, float] = (0.40, 0.65)
    impulse_duration_sec: float = Field(4.0, ge=0.5, le=30)
    impulse_min_sec: float = Field(3.0, ge=0.3, le=30, description="Короче не показываем (растягиваем)")
    fade_in_ms: int = Field(200, ge=0, le=3000)
    fade_out_ms: int = Field(200, ge=0, le=3000)
    final_repeat: bool = True
    final_repeat_sec: float = Field(2.5, ge=0.5, le=30)
    merge_gap_sec: float = Field(1.0, ge=0, le=10, description="Окна ближе этого сливаются в одно")
    persistent_fade_in_ms: int = Field(0, ge=0, le=3000)
    windows: list[TimeWindow] = Field(default_factory=list, description="Для mode=windows")

    @field_validator("impulse_window_pct")
    @classmethod
    def _pct(cls, v: tuple[float, float]) -> tuple[float, float]:
        lo, hi = v
        if not (0 <= lo < hi <= 1):
            raise ValueError("impulse_window_pct must satisfy 0 <= lo < hi <= 1")
        return v

    @model_validator(mode="after")
    def _windows(self) -> BannerTiming:
        if self.mode == TimingMode.WINDOWS and not self.windows:
            raise ValueError("mode=windows needs at least one window")
        if self.impulse_min_sec > self.impulse_duration_sec:
            raise ValueError("impulse_min_sec must be <= impulse_duration_sec")
        return self


class AdMarking(_Model):
    """Маркировка рекламы (38-ФЗ ст. 18.1): «Реклама», рекламодатель, erid от ОРД."""

    required: bool = Field(False, description="Если true, без erid рендер не должен уходить в публикацию")
    erid: str | None = Field(None, max_length=64, pattern=r"^[A-Za-z0-9_-]{4,64}$")
    advertiser_text: str | None = Field(None, max_length=120)


class BannerVariant(_Model):
    """Вариант баннера A/B: набор слоёв (обычно один креатив), тайминг и маркировка.

    Варианты A/B — разные загруженные креативы и/или их позиции и тайминги (решение владельца).
    """

    code: str = Field("A", pattern=r"^[A-Za-z0-9_-]{1,16}$")
    name: str = Field("", max_length=200)
    layers: list[BannerLayer]
    timing: BannerTiming = Field(default_factory=BannerTiming)
    ad_marking: AdMarking = Field(default_factory=AdMarking)
    code_url_template: str = Field(
        "u.umka.chat/{code}", max_length=120, description="Как печатать код ролика в кадре"
    )

    @model_validator(mode="after")
    def _unique_ids(self) -> BannerVariant:
        ids = [layer.id for layer in self.layers]
        if len(ids) != len(set(ids)):
            raise ValueError("layer ids must be unique")
        return self

    def layers_of(self, kind: BannerLayerKind, *, enabled_only: bool = True) -> list[BannerLayer]:
        return [la for la in self.layers if la.kind == kind and (la.enabled or not enabled_only)]

    def creative_shas(self) -> list[str]:
        return [la.creative_sha256 for la in self.layers if la.creative_sha256 and la.enabled]


# ---------------------------------------------------------------- safe zones


class SafeZoneSource(_Model):
    url: str | None = None
    note: str = ""
    status: SafeZoneStatus
    measured: EdgeBox | None = None


class SafeZoneMargins(_Model):
    top: int | None = None
    bottom: int | None = None
    left: int | None = None
    right: int | None = None


class SafeZoneProfile(_Model):
    """Профиль безопасной зоны площадки (config/safe_zones.json, research §7.1)."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    label: str
    status: SafeZoneStatus
    canvas_w: int = CANVAS_W
    canvas_h: int = CANVAS_H
    safe: EdgeBox
    margins: SafeZoneMargins = Field(default_factory=SafeZoneMargins)
    offer_area: EdgeBox | None = None
    offer_max_height_px: int | None = Field(None, gt=0)
    micro_brand_area: EdgeBox | None = None
    platforms: list[str] = Field(default_factory=list)
    sources: list[SafeZoneSource] = Field(default_factory=list)
    notes: str = ""

    @property
    def safe_rect(self) -> Rect:
        return self.safe.rect

    @property
    def canvas_rect(self) -> Rect:
        return Rect(x=0, y=0, w=self.canvas_w, h=self.canvas_h)

    def dead_zones(self) -> dict[str, Rect]:
        """Мёртвые зоны вокруг безопасного прямоугольника (для оверлея редактора)."""
        s, cw, ch = self.safe, self.canvas_w, self.canvas_h
        zones = {
            "top_bar": Rect.from_edges(0, 0, cw, s.y1),
            "bottom_ui": Rect.from_edges(0, s.y2, cw, ch),
            "left_margin": Rect.from_edges(0, s.y1, s.x1, s.y2),
            "right_rail": Rect.from_edges(s.x2, s.y1, cw, s.y2),
        }
        return {k: v for k, v in zones.items() if not v.is_empty}


class SafeZoneCatalog(_Model):
    version: int = 1
    default_profile: str = "common_organic"
    profiles: list[SafeZoneProfile]

    @model_validator(mode="after")
    def _default_exists(self) -> SafeZoneCatalog:
        ids = [p.id for p in self.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("profile ids must be unique")
        if self.default_profile not in ids:
            raise ValueError(f"default_profile {self.default_profile!r} not in profiles")
        return self

    def get(self, profile_id: str | None = None) -> SafeZoneProfile:
        pid = profile_id or self.default_profile
        for p in self.profiles:
            if p.id == pid:
                return p
        raise KeyError(f"unknown safe zone profile: {pid!r}")


# ---------------------------------------------------------------- предупреждения


class BannerValidationWarning(_Model):
    code: BannerValidationCode
    severity: WarningSeverity = WarningSeverity.WARN
    layer_id: str | None = None
    profile_id: str | None = None
    message: str = Field("", description="Человеческое объяснение (рус.)")
    details: dict[str, Any] = Field(default_factory=dict)


class ValidationThresholds(_Model):
    """Пороги `banner_validate`. Все меняются настройками."""

    min_creative_height_px: int = Field(90, ge=1, description="На канве 1080×1920")
    min_creative_width_px: int = Field(240, ge=1)
    max_upscale_factor: float = Field(1.5, ge=1.0, le=8.0)
    max_aspect_error: float = Field(0.01, ge=0, le=0.5)
    max_animated_sec: float = Field(15.0, gt=0)
    offer_max_height_px: int = Field(250, gt=0)
    min_captions_zone_h_px: int = Field(220, ge=0)


# ---------------------------------------------------------------- значения по умолчанию


def default_banner_variant(
    *,
    creative_sha256: str | None = None,
    micro_brand_text: str = "umka.chat",
    offer_text: str = "umka.chat — все нейросети в одном месте",
    erid: str | None = None,
    ad_marking_required: bool = False,
    show_code: bool = True,
) -> BannerVariant:
    """Вариант «из коробки» по research §7.2: микро-бренд сверху, креатив в зоне оффера
    x 65–890, y 1120–1370, код ролика под креативом, «Реклама · erid» над ним.
    Если креатива нет, показывается запасная плашка с `offer_text`.
    """
    layers: list[BannerLayer] = [
        BannerLayer(
            id="micro_brand",
            kind=BannerLayerKind.MICRO_BRAND,
            show=LayerShow.PERSISTENT,
            text=micro_brand_text,
            slot=BannerSlot(x=65, y=200, w=440, h=58, anchor=Anchor.TOP_LEFT, opacity=0.75),
            style=TextStyle(
                font_file="Montserrat-SemiBold.ttf",
                max_font_px=44,
                min_font_px=36,
                bg_opacity=0.35,
                padding_x=14,
                padding_y=6,
                align=TextAlign.LEFT,
            ),
            z=0,
        ),
    ]
    if creative_sha256:
        layers.append(
            BannerLayer(
                id="creative",
                kind=BannerLayerKind.CREATIVE,
                creative_sha256=creative_sha256,
                slot=BannerSlot(x=65, y=1120, w=825, h=190, anchor=Anchor.BOTTOM_CENTER),
                z=10,
            )
        )
    layers.append(
        BannerLayer(
            id="fallback_plate",
            kind=BannerLayerKind.FALLBACK_PLATE,
            enabled=creative_sha256 is None,
            text=offer_text,
            slot=BannerSlot(x=65, y=1120, w=825, h=190, anchor=Anchor.BOTTOM_CENTER),
            style=TextStyle(
                font_file="Montserrat-Bold.ttf",
                max_font_px=66,
                min_font_px=44,
                bg_opacity=0.7,
                padding_x=28,
                padding_y=18,
                corner_radius_px=14,
                max_lines=2,
                hug_text=False,
            ),
            z=10,
        )
    )
    layers.append(
        BannerLayer(
            id="code",
            kind=BannerLayerKind.TEXT_CODE,
            enabled=show_code,
            text=PLACEHOLDER_CODE_URL,
            slot=BannerSlot(x=65, y=1316, w=825, h=50, anchor=Anchor.TOP_CENTER),
            attach=TextAttach.BELOW_CREATIVE,
            style=TextStyle(font_file="Montserrat-Bold.ttf", max_font_px=36, min_font_px=24, bg_opacity=0.6),
            z=20,
        )
    )
    marking_on = bool(erid) or ad_marking_required
    layers.append(
        BannerLayer(
            id="erid",
            kind=BannerLayerKind.TEXT_ERID,
            enabled=marking_on,
            text="Реклама · erid {erid}",
            slot=BannerSlot(x=65, y=1078, w=825, h=38, anchor=Anchor.BOTTOM_CENTER),
            attach=TextAttach.ABOVE_CREATIVE,
            style=TextStyle(
                font_file="Montserrat-SemiBold.ttf",
                max_font_px=24,
                min_font_px=18,
                bg_opacity=0.45,
                padding_x=12,
                padding_y=5,
                corner_radius_px=8,
                text_opacity=0.92,
            ),
            z=20,
        )
    )
    return BannerVariant(
        code="A",
        name="default",
        layers=layers,
        ad_marking=AdMarking(required=ad_marking_required, erid=erid),
    )
