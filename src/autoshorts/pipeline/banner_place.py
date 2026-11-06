"""Геометрия баннерного слоя: вписать креатив в слот, сверить с safe zone, найти зону субтитров.

Всё считается в пикселях дизайн-канвы 1080×1920, в размер выхода переводит `to_output`.

    fit = fit_into_slot(creative.width, creative.height, layer.slot)     # FitResult
    report = check_safe_zone(fit.rect, profile)                          # SafeZoneReport
    placement = place_variant(variant, {sha: (w, h)}, profile)          # VariantPlacement
    placement.captions_zone        # CaptionsZone: полоса субтитров без пересечения с баннером

Правила:
* `contain` сохраняет пропорции и целиком помещается в слот (при `scale ≤ 1`); `cover` заполняет
  слот и обрезается по нему; `none` — родной размер × `scale`. Искажение пропорций не делается
  никогда: ширина и высота округляются от одной стороны.
* якорь (`Anchor`) делит свободное место слота, `offset_x/offset_y` сдвигают поверх;
* `clip_to_slot` обрезает видимую часть по слоту; всё, что за канвой, обрезается всегда;
* служебный текст с `attach` ставится относительно итогового прямоугольника креатива.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from autoshorts.domain.banner_schema import (
    CANVAS_H,
    CANVAS_W,
    BannerLayer,
    BannerLayerKind,
    BannerSlot,
    BannerVariant,
    FitMode,
    LayerShow,
    Rect,
    SafeZoneCatalog,
    SafeZoneProfile,
    TextAttach,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SAFE_ZONES_PATH = PROJECT_ROOT / "config" / "safe_zones.json"
PLATFORM_PROFILE = {
    "youtube": "youtube_shorts",
    "vk": "vk_clips",
    "instagram": "instagram_reels",
    "tiktok": "tiktok",
}

CAPTIONS_GAP_PX = 24
CAPTIONS_MIN_H_PX = 220
CAPTIONS_TARGET_H_PX = 360
CAPTIONS_PREFERRED_BOTTOM = 1100  # низ блока субтитров ≈ 57% высоты — над зоной оффера


# ---------------------------------------------------------------- профили safe zones


@lru_cache(maxsize=8)
def _load_catalog_cached(path: str, mtime_ns: int) -> SafeZoneCatalog:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return SafeZoneCatalog.model_validate(data)


def load_safe_zone_catalog(path: Path | None = None) -> SafeZoneCatalog:
    """config/safe_zones.json → SafeZoneCatalog (кеш по mtime)."""
    p = Path(path or DEFAULT_SAFE_ZONES_PATH)
    return _load_catalog_cached(str(p), p.stat().st_mtime_ns)


def profile_for_platform(platform: str, catalog: SafeZoneCatalog | None = None) -> SafeZoneProfile:
    catalog = catalog or load_safe_zone_catalog()
    return catalog.get(PLATFORM_PROFILE.get(platform, platform))


# ---------------------------------------------------------------- вписывание


@dataclass(frozen=True)
class FitResult:
    """Итог вписывания креатива `src_w×src_h` в слот.

    full_rect — куда лёг бы креатив целиком (может выходить за слот/канву);
    rect — видимая часть на канве (после обрезки по слоту и канве);
    src_crop — какая часть исходника видна (в пикселях исходника);
    scale — пикселей канвы на пиксель исходника (одинаково по осям).
    """

    full_rect: Rect
    rect: Rect
    src_crop: Rect
    scale: float
    src_w: int
    src_h: int
    clipped: bool
    canvas_clipped: bool = False  # обрезано краем кадра (а не только слотом)

    @property
    def upscale_factor(self) -> float:
        return self.scale

    @property
    def aspect_error(self) -> float:
        """Относительное расхождение пропорций видимой части и соответствующего куска исходника."""
        if self.rect.is_empty or self.src_crop.is_empty:
            return 0.0
        a_dst = self.rect.w / self.rect.h
        a_src = self.src_crop.w / self.src_crop.h
        return abs(a_dst - a_src) / a_src

    @property
    def visible(self) -> bool:
        return not self.rect.is_empty


def _scaled_size(src_w: int, src_h: int, s: float, width_limited: bool) -> tuple[int, int]:
    """Целые размеры с сохранением пропорций: округляется одна сторона, вторая выводится из неё."""
    if width_limited:
        w = max(1, round(src_w * s))
        h = max(1, round(w * src_h / src_w))
    else:
        h = max(1, round(src_h * s))
        w = max(1, round(h * src_w / src_h))
    return w, h


def fit_into_slot(
    src_w: int, src_h: int, slot: BannerSlot, *, canvas: tuple[int, int] = (CANVAS_W, CANVAS_H)
) -> FitResult:
    if src_w <= 0 or src_h <= 0:
        raise ValueError("source size must be positive")
    kx, ky = slot.w / src_w, slot.h / src_h
    if slot.fit == FitMode.CONTAIN:
        base = min(kx, ky)
        width_limited = kx <= ky
    elif slot.fit == FitMode.COVER:
        base = max(kx, ky)
        width_limited = kx >= ky
    else:
        base = 1.0
        width_limited = src_w >= src_h
    s = base * slot.scale
    w, h = _scaled_size(src_w, src_h, s, width_limited)
    if slot.fit == FitMode.CONTAIN and slot.scale <= 1.0:
        # округление не должно вывести contain за слот
        if w > slot.w or h > slot.h:
            w, h = _scaled_size(
                src_w, src_h, min(slot.w / src_w, slot.h / src_h) * slot.scale * 0.999, width_limited
            )
    x = slot.x + (slot.w - w) * slot.anchor.fx + slot.offset_x
    y = slot.y + (slot.h - h) * slot.anchor.fy + slot.offset_y
    full = Rect(x=math.floor(x + 0.5), y=math.floor(y + 0.5), w=w, h=h)
    visible: Rect | None = full
    if slot.clip_to_slot:
        visible = full.intersect(slot.rect)
    canvas_clipped = False
    if visible is not None:
        in_canvas = visible.intersect(Rect(x=0, y=0, w=canvas[0], h=canvas[1]))
        canvas_clipped = in_canvas != visible
        visible = in_canvas
    if visible is None:
        return FitResult(
            full_rect=full,
            rect=Rect(x=full.x, y=full.y, w=0, h=0),
            src_crop=Rect(x=0, y=0, w=0, h=0),
            scale=w / src_w,
            src_w=src_w,
            src_h=src_h,
            clipped=True,
            canvas_clipped=canvas_clipped,
        )
    sx, sy = w / src_w, h / src_h
    cx1 = (visible.x - full.x) / sx
    cy1 = (visible.y - full.y) / sy
    cx2 = (visible.x2 - full.x) / sx
    cy2 = (visible.y2 - full.y) / sy
    x1c, y1c = max(0, math.floor(cx1 + 0.5)), max(0, math.floor(cy1 + 0.5))
    x2c, y2c = min(src_w, math.floor(cx2 + 0.5)), min(src_h, math.floor(cy2 + 0.5))
    crop = Rect.from_edges(x1c, y1c, max(x2c, x1c + 1), max(y2c, y1c + 1))
    return FitResult(
        full_rect=full,
        rect=visible,
        src_crop=crop,
        scale=w / src_w,
        src_w=src_w,
        src_h=src_h,
        clipped=visible != full,
        canvas_clipped=canvas_clipped,
    )


def anchor_in_slot(content_w: int, content_h: int, slot: BannerSlot) -> Rect:
    """Прямоугольник готового содержимого (текстовой плашки) внутри слота по якорю."""
    x = slot.x + (slot.w - content_w) * slot.anchor.fx + slot.offset_x
    y = slot.y + (slot.h - content_h) * slot.anchor.fy + slot.offset_y
    return Rect(x=math.floor(x + 0.5), y=math.floor(y + 0.5), w=content_w, h=content_h)


# ---------------------------------------------------------------- safe zone


@dataclass(frozen=True)
class SafeZoneReport:
    profile_id: str
    inside: bool
    overflow: dict[str, int]  # top/bottom/left/right, пиксели за безопасной зоной (≥ 0)
    outside_fraction: float  # доля площади вне безопасной зоны

    @property
    def sides(self) -> list[str]:
        return [k for k, v in self.overflow.items() if v > 0]


def check_safe_zone(rect: Rect, profile: SafeZoneProfile) -> SafeZoneReport:
    safe = profile.safe_rect
    overflow = {
        "top": max(0, safe.y - rect.y),
        "bottom": max(0, rect.y2 - safe.y2),
        "left": max(0, safe.x - rect.x),
        "right": max(0, rect.x2 - safe.x2),
    }
    inter = rect.intersect(safe)
    inside_area = inter.area if inter else 0
    frac = 0.0 if rect.area == 0 else 1.0 - inside_area / rect.area
    return SafeZoneReport(profile.id, all(v == 0 for v in overflow.values()), overflow, round(frac, 4))


def clamp_into_safe_zone(rect: Rect, profile: SafeZoneProfile) -> tuple[Rect, bool]:
    """Сдвинуть (а если не влезает — пропорционально уменьшить) прямоугольник внутрь safe zone.

    Возвращает (новый прямоугольник, изменён ли он).
    """
    safe = profile.safe_rect
    w, h = rect.w, rect.h
    if w > safe.w or h > safe.h:
        s = min(safe.w / w, safe.h / h)
        w, h = max(1, math.floor(w * s)), max(1, math.floor(h * s))
    x = min(max(rect.x, safe.x), safe.x2 - w)
    y = min(max(rect.y, safe.y), safe.y2 - h)
    out = Rect(x=x, y=y, w=w, h=h)
    return out, out != rect


def clamp_slot_into_safe_zone(slot: BannerSlot, profile: SafeZoneProfile) -> BannerSlot:
    """Слот целиком внутрь safe zone (для кнопки «Вписать в безопасную зону» редактора)."""
    r, changed = clamp_into_safe_zone(slot.rect, profile)
    if not changed:
        return slot
    return slot.model_copy(update={"x": r.x, "y": r.y, "w": r.w, "h": r.h})


# ---------------------------------------------------------------- масштаб выхода


def output_scale(
    out_w: int, out_h: int, design: tuple[int, int] = (CANVAS_W, CANVAS_H)
) -> tuple[float, float, float]:
    """(sx, sy, s): по осям для позиций и единый s для размеров (пропорции сохраняются)."""
    sx, sy = out_w / design[0], out_h / design[1]
    return sx, sy, min(sx, sy)


def to_output(rect: Rect, out_w: int, out_h: int, design: tuple[int, int] = (CANVAS_W, CANVAS_H)) -> Rect:
    """Прямоугольник дизайн-канвы → пиксели выхода (другое разрешение, почти те же 9:16)."""
    if (out_w, out_h) == design:
        return rect
    sx, sy, s = output_scale(out_w, out_h, design)
    w = max(1, round(rect.w * s)) if rect.w else 0
    h = max(1, round(rect.h * s)) if rect.h else 0
    # центр переносится по осям, размер — единым масштабом
    cx = (rect.x + rect.w / 2) * sx
    cy = (rect.y + rect.h / 2) * sy
    return Rect(x=math.floor(cx - w / 2 + 0.5), y=math.floor(cy - h / 2 + 0.5), w=w, h=h)


# ---------------------------------------------------------------- зона субтитров


@dataclass(frozen=True)
class CaptionsZone:
    rect: Rect
    fits: bool  # хватило ли места под min_h
    canvas_w: int = CANVAS_W
    canvas_h: int = CANVAS_H

    @property
    def ass_margin_l(self) -> int:
        return self.rect.x

    @property
    def ass_margin_r(self) -> int:
        return self.canvas_w - self.rect.x2

    @property
    def ass_margin_v_bottom(self) -> int:
        """MarginV для ASS Alignment 1–3 (низ): отступ от нижнего края кадра до низа зоны."""
        return self.canvas_h - self.rect.y2

    @property
    def ass_margin_v_top(self) -> int:
        return self.rect.y


def compute_captions_zone(
    blocked: Sequence[Rect],
    profile: SafeZoneProfile,
    *,
    gap: int = CAPTIONS_GAP_PX,
    min_h: int = CAPTIONS_MIN_H_PX,
    target_h: int = CAPTIONS_TARGET_H_PX,
    preferred_bottom: int = CAPTIONS_PREFERRED_BOTTOM,
) -> CaptionsZone:
    """Горизонтальная полоса в safe zone, не пересекающая ни один баннерный слой (+ зазор).

    Из свободных полос выбирается та, где низ блока ближе всего к `preferred_bottom`
    (субтитры в нижней трети, над плашкой). Если места меньше `min_h`, берётся самая высокая
    полоса и `fits=False` (validate выдаст предупреждение).
    """
    safe = profile.safe_rect
    spans = sorted(
        (max(safe.y, r.y - gap), min(safe.y2, r.y2 + gap))
        for r in blocked
        if not r.is_empty and r.x < safe.x2 and r.x2 > safe.x and r.y - gap < safe.y2 and r.y2 + gap > safe.y
    )
    bands: list[tuple[int, int]] = []
    cursor = safe.y
    for y1, y2 in spans:
        if y1 > cursor:
            bands.append((cursor, y1))
        cursor = max(cursor, y2)
    if cursor < safe.y2:
        bands.append((cursor, safe.y2))
    cw, ch = profile.canvas_w, profile.canvas_h
    if not bands:
        return CaptionsZone(Rect(x=safe.x, y=safe.y, w=safe.w, h=0), False, cw, ch)
    good = [b for b in bands if b[1] - b[0] >= min_h]
    if not good:
        y1, y2 = max(bands, key=lambda b: b[1] - b[0])
        return CaptionsZone(Rect.from_edges(safe.x, y1, safe.x2, y2), False, cw, ch)

    def placed(band: tuple[int, int]) -> tuple[int, int]:
        b1, b2 = band
        height = min(target_h, b2 - b1)
        bottom = min(max(preferred_bottom, b1 + height), b2)
        return max(b1, bottom - height), bottom

    best = min(good, key=lambda b: (abs(placed(b)[1] - preferred_bottom), -(b[1] - b[0])))
    y1, y2 = placed(best)
    return CaptionsZone(Rect.from_edges(safe.x, y1, safe.x2, y2), True, cw, ch)


# ---------------------------------------------------------------- вариант целиком


@dataclass(frozen=True)
class LayerPlacement:
    layer_id: str
    kind: BannerLayerKind
    show: LayerShow
    slot_rect: Rect  # итоговый слот (для attach — пересчитанный от креатива)
    rect: Rect  # видимая часть; для текста до рендера — весь слот (консервативно)
    fit: FitResult | None = None
    creative_sha256: str | None = None
    z: int = 0

    def with_rect(self, rect: Rect) -> LayerPlacement:
        return LayerPlacement(
            self.layer_id, self.kind, self.show, self.slot_rect, rect, self.fit, self.creative_sha256, self.z
        )


@dataclass(frozen=True)
class VariantPlacement:
    layers: list[LayerPlacement]
    captions_zone: CaptionsZone
    profile_id: str
    missing_creatives: list[str] = field(default_factory=list)
    fallback_used: bool = False

    def by_id(self, layer_id: str) -> LayerPlacement | None:
        return next((p for p in self.layers if p.layer_id == layer_id), None)

    def banner_rects(self) -> list[Rect]:
        return [p.rect for p in self.layers if not p.rect.is_empty]


def attached_slot(layer: BannerLayer, anchor_rect: Rect) -> BannerSlot:
    """Слот служебного текста относительно итогового прямоугольника креатива."""
    slot = layer.slot
    gap = layer.attach_gap_px
    inside = layer.attach in (TextAttach.INSIDE_BOTTOM, TextAttach.INSIDE_TOP)
    w = min(slot.w, anchor_rect.w) if inside else slot.w
    x = anchor_rect.x + (anchor_rect.w - w) * slot.anchor.fx
    if layer.attach == TextAttach.BELOW_CREATIVE:
        y = anchor_rect.y2 + gap
    elif layer.attach == TextAttach.ABOVE_CREATIVE:
        y = anchor_rect.y - gap - slot.h
    elif layer.attach == TextAttach.INSIDE_BOTTOM:
        y = anchor_rect.y2 - gap - slot.h
    else:
        y = anchor_rect.y + gap
    return slot.model_copy(update={"x": math.floor(x + 0.5), "y": int(y), "w": max(1, int(w))})


def resolve_offer_layer(
    variant: BannerVariant, creative_sizes: Mapping[str, tuple[int, int]]
) -> tuple[BannerLayer | None, list[str], bool]:
    """Какой слой показывается в зоне оффера. Возвращает (слой, ненайденные sha, использована ли плашка).

    1. Первый включённый креатив с известным файлом.
    2. Включённый креатив есть, но файла нет (не загружен или пропал из хранилища) — запасная
       плашка подменяет его, даже если сама плашка выключена (по умолчанию она выключена,
       пока креатив задан): владелец хотел баннер в этом месте.
    3. Включённых креативов нет (слой выключен или его нет вовсе) — плашка только если она
       сама включена. Выключенный креатив + выключенная плашка = вариант без оффера
       (контроль в A/B, временно снятый баннер), системный текст не вшивается.
    """
    missing: list[str] = []
    creatives = variant.layers_of(BannerLayerKind.CREATIVE)
    for layer in creatives:
        if layer.creative_sha256 and layer.creative_sha256 in creative_sizes:
            return layer, missing, False
        if layer.creative_sha256:
            missing.append(layer.creative_sha256)
    plates = variant.layers_of(BannerLayerKind.FALLBACK_PLATE, enabled_only=not creatives)
    if plates:
        return plates[0], missing, True
    return None, missing, False


def place_variant(
    variant: BannerVariant,
    creative_sizes: Mapping[str, tuple[int, int]],
    profile: SafeZoneProfile,
    *,
    text_sizes: Mapping[str, tuple[int, int]] | None = None,
    captions_gap: int = CAPTIONS_GAP_PX,
    captions_min_h: int = CAPTIONS_MIN_H_PX,
) -> VariantPlacement:
    """Раскладка всех слоёв варианта на канве 1080×1920 и зона субтитров.

    `creative_sizes`: sha256 → (w, h) нормализованного креатива. `text_sizes`: layer_id → (w, h)
    уже отрендеренной текстовой плашки (если есть — прямоугольник точный, иначе весь слот).
    """
    text_sizes = text_sizes or {}
    offer, missing, fallback_used = resolve_offer_layer(variant, creative_sizes)
    placements: list[LayerPlacement] = []
    offer_rect: Rect | None = None

    def text_rect(layer: BannerLayer, slot: BannerSlot) -> Rect:
        size = text_sizes.get(layer.id)
        if size is None:
            return slot.rect
        return anchor_in_slot(size[0], size[1], slot)

    if offer is not None:
        if offer.kind == BannerLayerKind.CREATIVE and offer.creative_sha256:
            w, h = creative_sizes[offer.creative_sha256]
            fit = fit_into_slot(w, h, offer.slot)
            offer_rect = fit.rect
            placements.append(
                LayerPlacement(
                    offer.id,
                    offer.kind,
                    offer.show,
                    offer.slot.rect,
                    fit.rect,
                    fit,
                    offer.creative_sha256,
                    offer.z,
                )
            )
        else:
            offer_rect = text_rect(offer, offer.slot)
            placements.append(
                LayerPlacement(offer.id, offer.kind, offer.show, offer.slot.rect, offer_rect, z=offer.z)
            )

    for layer in variant.layers:
        if not layer.enabled or layer.kind in (BannerLayerKind.CREATIVE, BannerLayerKind.FALLBACK_PLATE):
            continue
        if layer.kind == BannerLayerKind.MICRO_BRAND and layer.creative_sha256:
            size = creative_sizes.get(layer.creative_sha256)
            if size is None:
                missing.append(layer.creative_sha256)
                if not layer.text:
                    continue
            else:
                fit = fit_into_slot(size[0], size[1], layer.slot)
                placements.append(
                    LayerPlacement(
                        layer.id,
                        layer.kind,
                        layer.show,
                        layer.slot.rect,
                        fit.rect,
                        fit,
                        layer.creative_sha256,
                        layer.z,
                    )
                )
                continue
        slot = layer.slot
        if layer.attach != TextAttach.NONE and offer_rect is not None and not offer_rect.is_empty:
            slot = attached_slot(layer, offer_rect)
        placements.append(
            LayerPlacement(layer.id, layer.kind, layer.show, slot.rect, text_rect(layer, slot), z=layer.z)
        )

    zone = compute_captions_zone(
        [p.rect for p in placements], profile, gap=captions_gap, min_h=captions_min_h
    )
    return VariantPlacement(
        layers=sorted(placements, key=lambda p: p.z),
        captions_zone=zone,
        profile_id=profile.id,
        missing_creatives=missing,
        fallback_used=fallback_used,
    )
