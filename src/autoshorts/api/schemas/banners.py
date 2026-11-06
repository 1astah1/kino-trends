"""Баннерный слой в API: загруженные креативы, safe zones, шрифты, валидация, PNG-кадр, шаблоны-пресеты.

Решение владельца от 2026-09-26 (docs/OWNER-DECISIONS.md п. 1) главнее ARCHITECTURE.md §5.2/§5.4:
баннер — это **загруженный владельцем креатив** (PNG/JPG/WebP, GIF, APNG, WebM с альфой, MP4).
Редактор: загрузить файл → он встаёт на кадр 1080×1920 → перетащить/масштабировать/привязать внутри
слота на фоне safe zones площадки → тайминг (весь ролик / импульс % / финальный повтор) → служебные
слои (код ролика, «Реклама · erid») → живые предупреждения → «Кадр из рендера» (PNG тем же кодом,
что рендер). Текстовая плашка системы (`fallback_plate`) — только запасной вариант.

Модели слоёв, слота, стиля текста, тайминга и маркировки — это модели домена
`domain/banner_schema.py` **без копий** (`BannerLayer`, `BannerSlot`, `TextStyle`, `ShadowSpec`,
`BannerTiming`, `TimeWindow`, `AdMarking` и их enum). API добавляет только обёртки: черновик варианта
(`BannerVariantDraft` = поля `domain.BannerVariant`), креатив с URL, ответы валидации.

Эндпоинты (тег `banners`):
- креативы: `GET|POST /banner-creatives`, `GET|PATCH|DELETE /banner-creatives/{creative_sha256}`,
  `GET /banner-creatives/{creative_sha256}/media/{media_kind}`;
- справочники редактора: `GET /banner-safe-zones`, `GET /banner-fonts`, `GET /banner-fonts/{font_name}`;
- проверка и кадр: `POST /banner-variants/validate`, `POST /banner-variants/preview`;
- вариант целиком: `GET /banner-variants/{variant_id}`, `POST /banner-variants/{variant_id}/duplicate`;
- шаблоны-пресеты (`/banner-templates*`): `layout` — это `BannerVariantDraft`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import Field, StringConstraints, ValidationError, model_validator

from autoshorts.api.schemas.common import ApiModel, PageMeta, RequestModel, Slug
from autoshorts.domain.banner_schema import (
    CANVAS_H,
    CANVAS_W,
    AdMarking,
    BannerLayer,
    BannerLayerKind,
    BannerTiming,
    BannerValidationCode,
    BannerValidationWarning,
    CreativeKind,
    CreativeSourceFormat,
    LayerShow,
    SafeZoneMargins,
    SafeZoneProfile,
    SafeZoneStatus,
)
from autoshorts.domain.banner_schema import BannerVariant as DomainBannerVariant
from autoshorts.domain.enums import BannerTemplateStatus, Platform, WarningSeverity
from autoshorts.domain.settings_schema import CanvasSize, DeadZone, Rect

__all__ = [
    "AdMarking",
    "BannerCreative",
    "BannerCreativeBrief",
    "BannerCreativePage",
    "BannerCreativePatch",
    "BannerCreativeUploadResult",
    "BannerFont",
    "BannerFontList",
    "BannerLayer",
    "BannerLayerPlacement",
    "BannerPreviewRequest",
    "BannerSafeZoneCatalog",
    "BannerSafeZoneProfile",
    "BannerTemplate",
    "BannerTemplateCreate",
    "BannerTemplatePage",
    "BannerTemplatePatch",
    "BannerTiming",
    "BannerValidateRequest",
    "BannerValidateResponse",
    "BannerVariantDraft",
    "BannerWarning",
    "BannerWindow",
    "BannerBox",
    "CaptionsZoneInfo",
    "CreativeMediaKind",
    "CreativeRejectCode",
    "CreativeStatus",
    "PlatformSafeZone",
    "PreviewMoment",
    "PreviewSize",
    "SafeZoneSourceInfo",
]

# ---------------------------------------------------------------- константы и типы

SHA256_PATTERN = r"^[0-9a-f]{64}$"
VARIANT_CODE_PATTERN = r"^[A-Za-z0-9_-]{1,16}$"
SAFE_ZONE_PROFILE_PATTERN = r"^[a-z][a-z0-9_]{1,39}$"
FONT_NAME_PATTERN = r"^[A-Za-z0-9._-]{1,80}\.(ttf|otf)$"
CODE_URL_TEMPLATE_PATTERN = r"^[^\s]*\{code\}[^\s]*$"
VIDEO_CODE_PATTERN = r"^[A-Za-z0-9_-]{2,64}$"
PREVIEW_BACKGROUND_PATTERN = r"^(sample|black|checker|job:[0-9]+|asset:[0-9]+)$"

MAX_CREATIVE_UPLOAD_BYTES = 50 * 1024 * 1024
"""Лимит тела `POST /banner-creatives` (анимация ≤ 50 МБ, статика ≤ 20 МБ — `banner_assets.AssetLimits`)."""
MAX_LAYERS = 16
MAX_DURATION_SEC = 180.0

Sha256 = Annotated[str, StringConstraints(pattern=SHA256_PATTERN)]
SafeZoneProfileId = Annotated[str, StringConstraints(pattern=SAFE_ZONE_PROFILE_PATTERN)]
CodeUrlTemplate = Annotated[str, StringConstraints(pattern=CODE_URL_TEMPLATE_PATTERN, max_length=120)]


class CreativeStatus(StrEnum):
    """Состояние загруженного креатива. Статика нормализуется сразу (`ready` в ответе загрузки),
    анимация — тяжёлой задачей очереди (`processing` → `ready` | `failed`)."""

    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class CreativeRejectCode(StrEnum):
    """Почему файл не принят: `BannerAssetError.code` из `pipeline/banner_assets.py` + сбой нормализации.

    Приходит в `details.asset_error` ошибки 413/415/422 загрузки и в `BannerCreative.error_code`
    (асинхронная нормализация анимации упала).
    """

    EMPTY_FILE = "empty_file"
    CORRUPT_FILE = "corrupt_file"
    UNSUPPORTED_FORMAT = "unsupported_format"
    FILE_TOO_LARGE = "file_too_large"
    DIMENSIONS_TOO_LARGE = "dimensions_too_large"
    DIMENSIONS_TOO_SMALL = "dimensions_too_small"
    ANIMATED_TOO_LONG = "animated_too_long"
    TOO_MANY_FRAMES = "too_many_frames"
    NORMALIZE_FAILED = "normalize_failed"


class CreativeMediaKind(StrEnum):
    FILE = "file"  # нормализованный файл: image/png (статика) | video/webm (анимация, VP9, альфа)
    THUMB = "thumb"  # image/png ≤ 320 px
    FIRST_FRAME = "first_frame"  # image/png, первый кадр в размере нормализованного файла


class PreviewMoment(StrEnum):
    AT_TIME = "at_time"  # кадр на `t_sec`
    IMPULSE = "impulse"  # середина первого окна импульса (группа with_creative)
    FINAL_REPEAT = "final_repeat"  # середина окна финального повтора


class PreviewSize(StrEnum):
    HALF = "half"  # 540×960
    FULL = "full"  # 1080×1920


# ---------------------------------------------------------------- креативы


class BannerCreative(ApiModel):
    """Загруженный креатив владельца (таблица `banner_creatives`, ключ — sha256 исходных байтов).

    Повторная загрузка того же файла возвращает ту же запись (без перекодирования). Поля
    нормализованного файла (`width`…`first_frame_url`) пусты, пока `status=processing`, и после `failed`.
    Координаты и размеры для редактора — `width`×`height` нормализованного файла.
    """

    sha256: str = Field(description="sha256 исходного файла, 64 hex; так креатив адресуют слои варианта")
    name: str = Field(description="Подпись в библиотеке; по умолчанию имя загруженного файла")
    status: CreativeStatus
    error_code: CreativeRejectCode | None = Field(description="Только при status=failed")
    error_message: str | None = Field(description="Для человека, по-русски")
    kind: CreativeKind
    source_format: CreativeSourceFormat = Field(description="Определён по сигнатуре, не по расширению")
    source_bytes: int
    source_width: int | None
    source_height: int | None
    width: int | None = Field(description="Нормализованный файл, px (ужат до канвы 1080×1920)")
    height: int | None
    has_alpha: bool | None
    duration_sec: float | None = Field(description="Один цикл анимации; 0 у статики")
    fps: float | None
    frame_count: int | None
    loop: bool | None = Field(description="Повторять анимацию, если окно показа длиннее креатива")
    normalized_mime: str | None = Field(description="image/png | video/webm")
    normalized_bytes: int | None
    file_url: str | None = Field(description="/api/v1/banner-creatives/{sha256}/media/file")
    thumb_url: str | None = Field(description="/api/v1/banner-creatives/{sha256}/media/thumb")
    first_frame_url: str | None = Field(description="/api/v1/banner-creatives/{sha256}/media/first_frame")
    product_id: str | None = Field(description="Продукт для фильтра библиотеки (необязательно)")
    notes: str | None
    used_by_variants: int = Field(
        description="Сколько вариантов ссылается на креатив (удалить нельзя, пока > 0)"
    )
    task_id: int | None = Field(description="Задача нормализации (анимация), прогресс — SSE task_progress")
    created_by: int | None
    created_at: datetime
    updated_at: datetime


class BannerCreativeBrief(ApiModel):
    """Креатив в списках вариантов: миниатюра без полной карточки."""

    sha256: str
    name: str
    kind: CreativeKind
    status: CreativeStatus
    width: int | None
    height: int | None
    thumb_url: str | None


class BannerCreativePage(PageMeta):
    items: list[BannerCreative]


class BannerCreativeUploadResult(ApiModel):
    creative: BannerCreative
    deduplicated: bool = Field(
        description="Такой файл (тот же sha256) уже был в библиотеке: запись возвращена как есть"
    )


class BannerCreativePatch(RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    product_id: Slug | None = None


# ---------------------------------------------------------------- safe zones и шрифты


class SafeZoneSourceInfo(ApiModel):
    url: str | None
    note: str
    status: SafeZoneStatus
    measured: Rect | None


class BannerSafeZoneProfile(ApiModel):
    """Профиль безопасной зоны площадки (`config/safe_zones.json`, research §7.1).

    Координаты — пиксели канвы 1080×1920, края `x1,y1,x2,y2`. `dead_zones` посчитаны сервером
    вокруг `safe` (top_bar, bottom_ui, left_margin, right_rail) — для оверлея редактора.
    """

    id: str
    label: str
    status: SafeZoneStatus
    canvas: CanvasSize
    safe: Rect
    margins: SafeZoneMargins
    offer_area: Rect | None = Field(description="Рекомендуемая зона оффера/креатива")
    offer_max_height_px: int | None
    micro_brand_area: Rect | None
    dead_zones: list[DeadZone]
    platforms: list[str] = Field(description="youtube, vk, instagram, tiktok — к каким площадкам относится")
    sources: list[SafeZoneSourceInfo]
    notes: str

    @classmethod
    def from_domain(cls, profile: SafeZoneProfile) -> BannerSafeZoneProfile:
        def edges(box: Any) -> Rect | None:
            return None if box is None else Rect(x1=box.x1, y1=box.y1, x2=box.x2, y2=box.y2)

        return cls(
            id=profile.id,
            label=profile.label,
            status=profile.status,
            canvas=CanvasSize(w=profile.canvas_w, h=profile.canvas_h),
            safe=Rect(x1=profile.safe.x1, y1=profile.safe.y1, x2=profile.safe.x2, y2=profile.safe.y2),
            margins=profile.margins,
            offer_area=edges(profile.offer_area),
            offer_max_height_px=profile.offer_max_height_px,
            micro_brand_area=edges(profile.micro_brand_area),
            dead_zones=[
                DeadZone(id=zone_id, x1=r.x, y1=r.y, x2=r.x2, y2=r.y2)
                for zone_id, r in profile.dead_zones().items()
            ],
            platforms=list(profile.platforms),
            sources=[
                SafeZoneSourceInfo(url=s.url, note=s.note, status=s.status, measured=edges(s.measured))
                for s in profile.sources
            ],
            notes=profile.notes,
        )


class PlatformSafeZone(ApiModel):
    platform: Platform
    profile_id: str = Field(description="Профиль по умолчанию для площадки (youtube → youtube_shorts)")


class BannerSafeZoneCatalog(ApiModel):
    version: int
    canvas: CanvasSize = Field(description="Дизайн-канва редактора: 1080×1920")
    default_profile: str = Field(description="Общий консервативный профиль (common_organic)")
    platform_profiles: list[PlatformSafeZone]
    items: list[BannerSafeZoneProfile]


class BannerFont(ApiModel):
    name: str = Field(description="Имя файла — значение TextStyle.font_file: Montserrat-Bold.ttf")
    family: str
    style: str
    cyrillic_ok: bool
    file_url: str = Field(description="/api/v1/banner-fonts/{name} — для FontFace в превью редактора")


class BannerFontList(ApiModel):
    items: list[BannerFont]


# ---------------------------------------------------------------- вариант (черновик)


def check_domain_variant(layers: list[BannerLayer]) -> None:
    """Правила `domain.BannerVariant` поверх отдельных слоёв (уникальные id) → ValueError для 422."""
    try:
        DomainBannerVariant(layers=layers)
    except ValidationError as exc:
        raise ValueError("; ".join(str(e.get("msg", "")) for e in exc.errors())) from None


class BannerVariantDraft(RequestModel):
    """Вариант баннера без id и статуса: поля `domain.banner_schema.BannerVariant` один в один.

    Слои (`layers`) — обычно один `creative` (ссылка `creative_sha256` на загруженный креатив) +
    служебные `text_code`/`text_erid` + `micro_brand`; `fallback_plate` включается, только если
    креатива нет. Все правила домена (уникальные id слоёв, обязательный текст у текстовых слоёв,
    `attach` только у кода/erid, окна у `timing.mode=windows`) проверяются здесь же → 422.

    `code_url_template: null` — шаблон продукта: `<products.redirect_host>/{code}`.
    """

    code: str = Field(default="A", pattern=VARIANT_CODE_PATTERN)
    name: str = Field(default="", max_length=200)
    layers: list[BannerLayer] = Field(max_length=MAX_LAYERS)
    timing: BannerTiming = Field(default_factory=BannerTiming)
    ad_marking: AdMarking = Field(default_factory=AdMarking)
    code_url_template: CodeUrlTemplate | None = Field(
        default=None, description="Как печатать код ролика: «u.umka.chat/{code}»; null — из продукта"
    )

    @model_validator(mode="after")
    def _domain_rules(self) -> BannerVariantDraft:
        check_domain_variant(self.layers)
        return self

    def to_domain(self, *, default_code_url_template: str = "u.umka.chat/{code}") -> DomainBannerVariant:
        """Доменная модель для `banner_place`/`banner_validate`/`banner_overlay`."""
        return DomainBannerVariant(
            code=self.code,
            name=self.name,
            layers=self.layers,
            timing=self.timing,
            ad_marking=self.ad_marking,
            code_url_template=self.code_url_template or default_code_url_template,
        )

    @classmethod
    def from_domain(cls, variant: DomainBannerVariant) -> BannerVariantDraft:
        return cls.model_validate(variant.model_dump())


# ---------------------------------------------------------------- валидация и кадр


class _BannerCheckBase(RequestModel):
    variant_id: int | None = Field(default=None, description="Сохранённый вариант")
    draft: BannerVariantDraft | None = Field(
        default=None, description="Несохранённый вариант из редактора; если задан, важнее variant_id"
    )
    campaign_id: int | None = Field(
        default=None, description="Контекст черновика: продукт кампании даёт домен для {code_url}"
    )
    platform: Platform = Platform.YOUTUBE
    duration_sec: float = Field(
        default=30.0, gt=0, le=MAX_DURATION_SEC, description="Длительность ролика — от неё окна показа"
    )


class BannerValidateRequest(_BannerCheckBase):
    """Серверная проверка — источник истины для редактора (debounce 150 мс) и рендера.

    `/banner-variants/validate`: нужен `draft` или `variant_id` (иначе 422).
    `/banner-templates/{template_id}/validate`: без обоих проверяется `layout` шаблона.
    """

    profile_ids: list[SafeZoneProfileId] = Field(
        default_factory=list,
        max_length=8,
        description="Профили safe zone; пусто — профиль площадки + common_organic. Первый — основной",
    )
    captions_zone: Rect | None = Field(
        default=None, description="Фиксированная зона субтитров (трек нарезки) — проверка перекрытия"
    )


class BannerPreviewRequest(_BannerCheckBase):
    """PNG-кадр тем же кодом, что финальный рендер (`banner_overlay`), для «Кадр из рендера».

    Ответ `image/png`; фактическое время кадра — в заголовке `X-Preview-T-Sec`.
    """

    profile_id: SafeZoneProfileId | None = Field(
        default=None, description="Профиль safe zone (зона субтитров); пусто — профиль площадки"
    )
    moment: PreviewMoment = PreviewMoment.IMPULSE
    t_sec: float | None = Field(default=None, ge=0, le=MAX_DURATION_SEC, description="Для moment=at_time")
    background: str = Field(
        default="sample",
        pattern=PREVIEW_BACKGROUND_PATTERN,
        description="sample | black | checker | job:<id> (кадр ролика в t) | asset:<id> (картинка)",
    )
    size: PreviewSize = PreviewSize.HALF
    code: str | None = Field(
        default=None, pattern=VIDEO_CODE_PATTERN, description="Код ролика для {code}; пусто — образец"
    )


class BannerWindow(ApiModel):
    """Окно показа группы слоёв: `persistent` — весь ролик, `with_creative` — импульс и финал."""

    group: LayerShow
    start_sec: float
    end_sec: float


class BannerBox(ApiModel):
    """Видимый прямоугольник слоя на канве 1080×1920 (оверлей приёмки)."""

    layer_id: str
    kind: BannerLayerKind
    x1: int
    y1: int
    x2: int
    y2: int


class BannerLayerPlacement(ApiModel):
    """Где слой оказался после вписывания (`banner_place.place_variant`), канва 1080×1920."""

    layer_id: str
    kind: BannerLayerKind
    show: LayerShow
    creative_sha256: str | None
    slot: Rect = Field(description="Итоговый слот (для attach — пересчитанный от креатива)")
    rect: Rect = Field(description="Видимая часть; у текста до рендера — весь слот")
    full_rect: Rect | None = Field(description="Креатив целиком до обрезки слотом и кадром")
    scale: float | None = Field(description="Пикселей канвы на пиксель нормализованного креатива")
    clipped: bool
    z: int

    @classmethod
    def from_domain(cls, placement: Any) -> BannerLayerPlacement:
        """`banner_place.LayerPlacement` → API (утиная типизация: схемы не импортируют pipeline)."""
        fit = placement.fit
        return cls(
            layer_id=placement.layer_id,
            kind=placement.kind,
            show=placement.show,
            creative_sha256=placement.creative_sha256,
            slot=_rect(placement.slot_rect),
            rect=_rect(placement.rect),
            full_rect=_rect(fit.full_rect) if fit is not None else None,
            scale=round(float(fit.scale), 6) if fit is not None else None,
            clipped=bool(fit is not None and (fit.clipped or fit.canvas_clipped)),
            z=placement.z,
        )


def _rect(r: Any) -> Rect:
    """`domain.banner_schema.Rect` (x, y, w, h) → края `x1, y1, x2, y2`."""
    return Rect(x1=r.x, y1=r.y, x2=r.x + r.w, y2=r.y + r.h)


class CaptionsZoneInfo(ApiModel):
    rect: Rect
    fits: bool = Field(description="Хватает ли места под субтитры (минимум из настроек)")


class BannerWarning(ApiModel):
    """Предупреждение редактора/рендера: `domain.banner_schema.BannerValidationWarning` один в один."""

    code: BannerValidationCode
    severity: WarningSeverity
    layer_id: str | None = None
    profile_id: str | None = None
    message: str = Field(default="", description="Объяснение для человека, по-русски")
    details: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_domain(cls, warning: BannerValidationWarning) -> BannerWarning:
        return cls(
            code=warning.code,
            severity=WarningSeverity(warning.severity.value),
            layer_id=warning.layer_id,
            profile_id=warning.profile_id,
            message=warning.message,
            details=warning.details,
        )


class BannerValidateResponse(ApiModel):
    ok: bool = Field(description="Нет предупреждений с severity=error")
    profile_ids: list[str] = Field(description="Проверенные профили; первый — основной")
    duration_sec: float
    windows: list[BannerWindow]
    placements: list[BannerLayerPlacement]
    captions_zone: CaptionsZoneInfo
    fallback_used: bool = Field(description="Креатива нет — показывается запасная плашка")
    missing_creatives: list[str] = Field(description="sha256 из слоёв, которых нет в библиотеке")
    warnings: list[BannerWarning] = Field(description="Сначала error, затем warn, info")

    @classmethod
    def from_result(
        cls, result: Any, *, profile_ids: list[str], duration_sec: float
    ) -> BannerValidateResponse:
        """`banner_validate.BannerValidateResult` → ответ API.

        Окна: `with_creative` — из результата (импульс, финальный повтор); `persistent` — весь ролик,
        если есть включённый слой с `show=persistent`.
        """
        placement = result.placement
        windows = [
            BannerWindow(group=LayerShow.WITH_CREATIVE, start_sec=w.start_sec, end_sec=w.end_sec)
            for w in result.windows
        ]
        if any(pl.show == LayerShow.PERSISTENT for pl in placement.layers):
            windows.insert(0, BannerWindow(group=LayerShow.PERSISTENT, start_sec=0.0, end_sec=duration_sec))
        warnings = [BannerWarning.from_domain(w) for w in result.warnings]
        return cls(
            ok=not any(w.severity == WarningSeverity.ERROR for w in warnings),
            profile_ids=list(profile_ids),
            duration_sec=duration_sec,
            windows=windows,
            placements=[BannerLayerPlacement.from_domain(pl) for pl in placement.layers],
            captions_zone=CaptionsZoneInfo(
                rect=_rect(result.captions_zone.rect), fits=result.captions_zone.fits
            ),
            fallback_used=placement.fallback_used,
            missing_creatives=list(placement.missing_creatives),
            warnings=warnings,
        )


# ---------------------------------------------------------------- шаблоны (пресеты варианта)


class BannerTemplate(ApiModel):
    """Пресет варианта: из него создаются варианты кампании; импорт/экспорт формы config/banner.yaml."""

    id: int
    name: str
    status: BannerTemplateStatus
    layout: BannerVariantDraft = Field(description="Слои, тайминг, маркировка — как у варианта")
    version: int = Field(description="Передавать как expected_version при правке")
    created_at: datetime
    updated_at: datetime


class BannerTemplatePage(PageMeta):
    items: list[BannerTemplate]


class BannerTemplateCreate(RequestModel):
    name: str = Field(min_length=1, max_length=200)
    layout: BannerVariantDraft


class BannerTemplatePatch(RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: BannerTemplateStatus | None = None
    layout: BannerVariantDraft | None = None
    expected_version: int = Field(ge=1)


CANVAS = CanvasSize(w=CANVAS_W, h=CANVAS_H)
"""Дизайн-канва редактора и координат слоёв."""
