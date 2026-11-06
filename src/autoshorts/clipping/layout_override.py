"""Ручная раскладка ролика: оператор на приёмке двигает баннер и текстовые зоны (§6.3.7, редактор раскладки).

Переопределение хранится в `jobs.author_layer.layout` (схема `api.schemas.clipping.LayoutOverride`),
все числа — пиксели дизайн-канвы 1080×1920:

    layout = {"mode": "manual", "banner": {"dx": 0, "dy": -400, "scale": 1.0},
              "captions_bottom": 900, "title_top": None, "onscreen_top": None}

Как применяется при рендере (`services/clipping.banner_setup` + `clipping/produce.produce_clip`):

* `shift_variant` — слои оффера (креатив, запасная плашка, код ролика, «Реклама · erid») двигаются
  единым блоком: слоты сдвигаются на dx/dy и масштабируются вокруг центра блока, после чего блок
  целиком вписывается в safe zone площадки (`clamp_into_safe_zone`). Микробренд остаётся на месте;
* `apply_caption_override` — полоса субтитров ставится низом в `captions_bottom`, заголовок и тезисы —
  верхом в `title_top` / `onscreen_top` (всё в пределах safe zone). Дальше рендер, как и в авто-режиме,
  прогоняет `CaptionLayout.avoiding` и `check_clear`, так что ручные значения — предпочтение, а не приказ;
* `layout_conflicts` — список пересечений прямоугольников для редактора и `edl.meta`.

`compute_layout` собирает всё вместе без рендера — так `GET /jobs/{id}/layout` показывает «как будет».
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from autoshorts.clipping.captions import SAFE_BOTTOM, SAFE_TOP, CaptionLayout, Zone
from autoshorts.domain.banner_schema import (
    CANVAS_H,
    CANVAS_W,
    BannerLayer,
    BannerLayerKind,
    BannerSlot,
    BannerVariant,
    Rect,
    SafeZoneProfile,
    TextAttach,
)
from autoshorts.pipeline.banner_place import clamp_into_safe_zone, place_variant

LAYOUT_MODES = ("auto", "manual")
BLOCK_KINDS = frozenset(
    {
        BannerLayerKind.CREATIVE,
        BannerLayerKind.FALLBACK_PLATE,
        BannerLayerKind.TEXT_CODE,
        BannerLayerKind.TEXT_ERID,
    }
)
SCALE_MIN, SCALE_MAX = 0.25, 2.0
ZONE_IDS = ("captions", "title", "onscreen")
ZONE_LABELS = {"captions": "Субтитры", "title": "Заголовок", "onscreen": "Тезисы"}
PLACEHOLDER_ERID = "{erid}"
PLACEHOLDER_ADVERTISER = "{advertiser}"


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _rect_of_zone(z: Zone) -> Rect:
    return Rect.from_edges(z.x1, z.y1, z.x2, z.y2)


def _zone_of_rect(r: Rect) -> Zone:
    return Zone(r.x, r.y, r.x2, r.y2)


def _union(rects: Sequence[Rect]) -> Rect | None:
    live = [r for r in rects if not r.is_empty]
    if not live:
        return None
    out = live[0]
    for r in live[1:]:
        out = out.union(r)
    return out


# ---------------------------------------------------------------- спецификация


@dataclass(frozen=True, slots=True)
class BannerShift:
    dx: int = 0
    dy: int = 0
    scale: float = 1.0

    @property
    def is_identity(self) -> bool:
        return self.dx == 0 and self.dy == 0 and abs(self.scale - 1.0) < 1e-6

    def to_json(self) -> dict[str, Any]:
        return {"dx": self.dx, "dy": self.dy, "scale": round(self.scale, 4)}


@dataclass(frozen=True, slots=True)
class LayoutSpec:
    """Разобранный `author_layer.layout`. В режиме `auto` все поля игнорируются."""

    mode: str = "auto"
    banner: BannerShift | None = None
    captions_bottom: int | None = None
    title_top: int | None = None
    onscreen_top: int | None = None

    @property
    def is_manual(self) -> bool:
        return self.mode == "manual"

    @property
    def has_banner_shift(self) -> bool:
        return self.is_manual and self.banner is not None and not self.banner.is_identity

    @property
    def has_caption_override(self) -> bool:
        return self.is_manual and any(
            v is not None for v in (self.captions_bottom, self.title_top, self.onscreen_top)
        )

    @classmethod
    def from_json(cls, data: Any) -> LayoutSpec | None:
        """Терпимый разбор JSON из БД: не словарь или мусор → None (как будто раскладки нет)."""
        if not isinstance(data, Mapping):
            return None
        mode = str(data.get("mode") or "auto")
        if mode not in LAYOUT_MODES:
            return None

        def px(key: str) -> int | None:
            v = data.get(key)
            if isinstance(v, bool) or not isinstance(v, int | float):
                return None
            return int(_clamp(int(v), 0, CANVAS_H))

        banner: BannerShift | None = None
        raw = data.get("banner")
        if isinstance(raw, Mapping):
            try:
                banner = BannerShift(
                    dx=int(_clamp(int(raw.get("dx") or 0), -CANVAS_W, CANVAS_W)),
                    dy=int(_clamp(int(raw.get("dy") or 0), -CANVAS_H, CANVAS_H)),
                    scale=float(_clamp(float(raw.get("scale") or 1.0), SCALE_MIN, SCALE_MAX)),
                )
            except (TypeError, ValueError):
                banner = None
        return cls(
            mode=mode,
            banner=banner,
            captions_bottom=px("captions_bottom"),
            title_top=px("title_top"),
            onscreen_top=px("onscreen_top"),
        )

    @classmethod
    def coerce(cls, value: Any) -> LayoutSpec | None:
        if value is None or isinstance(value, LayoutSpec):
            return value
        return cls.from_json(value)

    def to_json(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "banner": self.banner.to_json() if self.banner else None,
            "captions_bottom": self.captions_bottom,
            "title_top": self.title_top,
            "onscreen_top": self.onscreen_top,
        }


# ---------------------------------------------------------------- баннер единым блоком


def block_layers(variant: BannerVariant) -> list[BannerLayer]:
    """Слои, которые редактор двигает как один блок: оффер и его служебный текст (без микробренда)."""
    return [la for la in variant.layers if la.enabled and la.kind in BLOCK_KINDS]


def _layer_shown(layer: BannerLayer, variant: BannerVariant) -> bool:
    """Повторяет пропуск текстовых слоёв рендером: `{erid}`/`{advertiser}` без значения — слоя нет."""
    text = layer.text or ""
    if PLACEHOLDER_ERID in text and not variant.ad_marking.erid:
        return False
    return not (PLACEHOLDER_ADVERTISER in text and not variant.ad_marking.advertiser_text)


def _transform_slot(slot: BannerSlot, *, s: float, cx: float, cy: float, dx: int, dy: int) -> BannerSlot:
    """Масштаб вокруг (cx, cy) и сдвиг; значения удерживаются в допустимых пределах BannerSlot."""
    w = int(_clamp(max(1, round(slot.w * s)), 1, 2 * CANVAS_W))
    h = int(_clamp(max(1, round(slot.h * s)), 1, 2 * CANVAS_H))
    x = int(_clamp(round(cx + (slot.x - cx) * s) + dx, -CANVAS_W, 2 * CANVAS_W))
    y = int(_clamp(round(cy + (slot.y - cy) * s) + dy, -CANVAS_H, 2 * CANVAS_H))
    return slot.model_copy(
        update={
            "x": x,
            "y": y,
            "w": w,
            "h": h,
            "offset_x": int(_clamp(round(slot.offset_x * s), -CANVAS_W, CANVAS_W)),
            "offset_y": int(_clamp(round(slot.offset_y * s), -CANVAS_H, CANVAS_H)),
        }
    )


def _transform_block(
    variant: BannerVariant, *, s: float, cx: float, cy: float, dx: int, dy: int
) -> BannerVariant:
    layers: list[BannerLayer] = []
    for la in variant.layers:
        if not (la.enabled and la.kind in BLOCK_KINDS):
            layers.append(la)
            continue
        if la.attach != TextAttach.NONE:
            # Привязанный текст встаёт относительно креатива сам; у него масштабируются размер и зазор.
            slot = la.slot.model_copy(
                update={
                    "w": int(_clamp(max(1, round(la.slot.w * s)), 1, 2 * CANVAS_W)),
                    "h": int(_clamp(max(1, round(la.slot.h * s)), 1, 2 * CANVAS_H)),
                }
            )
            gap = int(_clamp(round(la.attach_gap_px * s), 0, 200))
            layers.append(la.model_copy(update={"slot": slot, "attach_gap_px": gap}))
            continue
        slot = _transform_slot(la.slot, s=s, cx=cx, cy=cy, dx=dx, dy=dy)
        layers.append(la.model_copy(update={"slot": slot}))
    return variant.model_copy(update={"layers": layers})


def block_rect(
    variant: BannerVariant, sizes: Mapping[str, tuple[int, int]], profile: SafeZoneProfile
) -> Rect | None:
    """Общий прямоугольник блока оффера после вписывания (как его видит редактор)."""
    ids = {la.id for la in block_layers(variant) if _layer_shown(la, variant)}
    placed = place_variant(variant, sizes, profile)
    rects = [p.rect for p in placed.layers if p.layer_id in ids]
    if not rects:
        # креатив без файла и выключенная плашка: берём слоты, чтобы было что двигать
        rects = [la.slot.rect for la in block_layers(variant)]
    return _union(rects)


@dataclass(frozen=True, slots=True)
class ShiftedVariant:
    variant: BannerVariant
    block_before: Rect | None
    block_after: Rect | None
    clamped: bool
    scale_applied: float
    notes: list[str] = field(default_factory=list)


def shift_variant(
    variant: BannerVariant,
    shift: BannerShift,
    profile: SafeZoneProfile,
    sizes: Mapping[str, tuple[int, int]] | None = None,
) -> ShiftedVariant:
    """Вариант со сдвинутым/масштабированным блоком оффера, вписанным в safe zone.

    `sizes`: sha256 → (w, h) креативов — чтобы центр и габарит блока считались по реальным
    прямоугольникам, как в редакторе. Без размеров — по слотам.
    """
    sizes = dict(sizes or {})
    before = block_rect(variant, sizes, profile)
    if before is None or shift.is_identity:
        return ShiftedVariant(variant, before, before, False, 1.0)
    # Целевой прямоугольник блока считается аналитически (как в редакторе): масштаб вокруг центра,
    # сдвиг, затем вписывание в safe zone. Так блок не теряется, даже если его утащили за кадр.
    s = float(_clamp(shift.scale, SCALE_MIN, SCALE_MAX))
    cx, cy = before.x + before.w / 2, before.y + before.h / 2
    pred = Rect.from_edges(
        cx + (before.x - cx) * s + shift.dx,
        cy + (before.y - cy) * s + shift.dy,
        cx + (before.x2 - cx) * s + shift.dx,
        cy + (before.y2 - cy) * s + shift.dy,
    )
    notes: list[str] = []
    safe = profile.safe_rect
    k = 1.0
    if pred.w > safe.w or pred.h > safe.h:
        # не влезает даже сдвигом — уменьшаем вокруг центра до safe zone
        k = min(safe.w / pred.w, safe.h / pred.h) * 0.995
        pcx, pcy = pred.x + pred.w / 2, pred.y + pred.h / 2
        pred = Rect.from_edges(
            pcx + (pred.x - pcx) * k,
            pcy + (pred.y - pcy) * k,
            pcx + (pred.x2 - pcx) * k,
            pcy + (pred.y2 - pcy) * k,
        )
        notes.append(f"banner_scaled_to_safe_zone:{s * k:.3f}")
    target, changed = clamp_into_safe_zone(pred, profile)
    ddx, ddy = target.x - pred.x, target.y - pred.y
    if changed and (ddx or ddy):
        notes.append("banner_clamped_to_safe_zone")
    # масштаб s, затем k вокруг центра сдвинутого блока = масштаб s·k вокруг исходного центра + сдвиг
    out = _transform_block(variant, s=s * k, cx=cx, cy=cy, dx=shift.dx + ddx, dy=shift.dy + ddy)
    after = block_rect(out, sizes, profile)
    return ShiftedVariant(out, before, after, bool(notes), s * k, notes)


# ---------------------------------------------------------------- текстовые зоны


def apply_caption_override(
    layout: CaptionLayout, spec: LayoutSpec, *, safe: Rect | None = None
) -> tuple[CaptionLayout, list[str]]:
    """Ручные позиции зон: низ субтитров, верх заголовка и тезисов — в пределах safe zone.

    Возвращает (раскладка, что применено: `captions:1080->900`). Авто-режим — без изменений.
    """
    if not spec.has_caption_override:
        return layout, []
    top = safe.y if safe is not None else SAFE_TOP
    bottom = safe.y2 if safe is not None else SAFE_BOTTOM
    applied: list[str] = []
    source, author = layout.source_zone, layout.author_zone
    title, onscreen = layout.title_zone, layout.onscreen_zone
    if spec.captions_bottom is not None:
        h = source.y2 - source.y1
        y2 = int(_clamp(spec.captions_bottom, top + h, bottom)) if h <= bottom - top else bottom
        if y2 != source.y2:
            applied.append(f"captions:{source.y2}->{y2}")
        source = Zone(source.x1, y2 - h, source.x2, y2)
        ah = author.y2 - author.y1
        author = Zone(author.x1, y2 - ah, author.x2, y2)

    def put_top(zone: Zone, y_top: int | None, name: str) -> Zone:
        if y_top is None:
            return zone
        h = zone.y2 - zone.y1
        y1 = int(_clamp(y_top, top, max(top, bottom - h)))
        if y1 != zone.y1:
            applied.append(f"{name}:{zone.y1}->{y1}")
        return Zone(zone.x1, y1, zone.x2, y1 + h)

    title = put_top(title, spec.title_top, "title")
    onscreen = put_top(onscreen, spec.onscreen_top, "onscreen")
    return (
        CaptionLayout(
            canvas_w=layout.canvas_w,
            canvas_h=layout.canvas_h,
            source_zone=source,
            author_zone=author,
            title_zone=title,
            onscreen_zone=onscreen,
        ),
        applied,
    )


def caption_zones_json(layout: CaptionLayout) -> dict[str, dict[str, int]]:
    return {
        "source": _rect_of_zone(layout.source_zone).edges(),
        "author": _rect_of_zone(layout.author_zone).edges(),
        "title": _rect_of_zone(layout.title_zone).edges(),
        "onscreen": _rect_of_zone(layout.onscreen_zone).edges(),
    }


# ---------------------------------------------------------------- пересечения


def layout_conflicts(
    rects: Mapping[str, Rect],
    *,
    groups: Mapping[str, str] | None = None,
    skip: Collection[str] = (),
) -> list[dict[str, Any]]:
    """Попарные пересечения: [{a, b, overlap_px, rect}], глубже — первыми.

    `overlap_px` — глубина наложения (меньшая сторона пересечения): «налезает на 40 px».
    Пары из одной группы (`groups[id]`) не считаются — служебный текст лежит на креативе по замыслу;
    `skip` — зоны без текста (пустой заголовок в кадре не рисуется).
    """
    groups = groups or {}
    ids = [k for k, r in rects.items() if not r.is_empty and k not in set(skip)]
    out: list[dict[str, Any]] = []
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            ga, gb = groups.get(a), groups.get(b)
            if ga is not None and ga == gb:
                continue
            inter = rects[a].intersect(rects[b])
            if inter is None:
                continue
            first, second = sorted((a, b))
            out.append({"a": first, "b": second, "overlap_px": min(inter.w, inter.h), "rect": inter.edges()})
    out.sort(key=lambda c: (-c["overlap_px"], c["a"], c["b"]))
    return out


# ---------------------------------------------------------------- всё вместе (без рендера)


@dataclass(slots=True)
class LayoutRectOut:
    id: str
    kind: str  # banner | captions | title | onscreen
    layer_kind: str | None
    label: str
    rect: Rect
    movable: bool
    has_text: bool = True

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "layer_kind": self.layer_kind,
            "label": self.label,
            **self.rect.edges(),
            "movable": self.movable,
            "has_text": self.has_text,
        }


@dataclass(slots=True)
class LayoutSnapshot:
    block: Rect | None
    captions: Rect
    title: Rect
    onscreen: Rect
    rects: list[LayoutRectOut]
    conflicts: list[dict[str, Any]]
    adjustments: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "block": self.block.edges() if self.block else None,
            "captions": self.captions.edges(),
            "title": self.title.edges(),
            "onscreen": self.onscreen.edges(),
            "rects": [r.to_json() for r in self.rects],
            "conflicts": self.conflicts,
            "adjustments": list(self.adjustments),
        }


LAYER_LABELS = {
    BannerLayerKind.CREATIVE.value: "Креатив",
    BannerLayerKind.FALLBACK_PLATE.value: "Запасная плашка",
    BannerLayerKind.TEXT_CODE.value: "Код ролика",
    BannerLayerKind.TEXT_ERID.value: "Реклама · erid",
    BannerLayerKind.MICRO_BRAND.value: "Микробренд",
}


def _banner_rects(
    variant: BannerVariant | None, sizes: Mapping[str, tuple[int, int]], profile: SafeZoneProfile
) -> tuple[list[LayoutRectOut], Rect | None, Rect]:
    """Прямоугольники слоёв варианта, блок оффера и авто-зона субтитров."""
    if variant is None:
        return [], None, Rect.from_edges(65, 820, 895, 1080)
    placed = place_variant(variant, sizes, profile)
    by_id = {la.id: la for la in variant.layers}
    out: list[LayoutRectOut] = []
    for p in placed.layers:
        layer = by_id.get(p.layer_id)
        if layer is None or p.rect.is_empty or not _layer_shown(layer, variant):
            continue
        movable = layer.kind in BLOCK_KINDS
        out.append(
            LayoutRectOut(
                id=p.layer_id,
                kind="banner",
                layer_kind=p.kind.value,
                label=LAYER_LABELS.get(p.kind.value, p.kind.value),
                rect=p.rect,
                movable=movable,
            )
        )
    block = _union([r.rect for r in out if r.movable])
    return out, block, placed.captions_zone.rect


def _snapshot(
    variant: BannerVariant | None,
    sizes: Mapping[str, tuple[int, int]],
    profile: SafeZoneProfile,
    spec: LayoutSpec | None,
    *,
    empty_zones: Collection[str] = (),
) -> LayoutSnapshot:
    rects, block, captions_rect = _banner_rects(variant, sizes, profile)
    layout = CaptionLayout.from_rect(captions_rect.x, captions_rect.y, captions_rect.x2, captions_rect.y2)
    adjustments: list[str] = []
    if spec is not None and spec.has_caption_override:
        layout, applied = apply_caption_override(layout, spec, safe=profile.safe_rect)
        adjustments += applied
    banner_zones = [_zone_of_rect(r.rect) for r in rects]
    layout, moved = layout.avoiding(banner_zones)
    adjustments += moved
    zones = {
        "captions": _rect_of_zone(layout.source_zone),
        "title": _rect_of_zone(layout.title_zone),
        "onscreen": _rect_of_zone(layout.onscreen_zone),
    }
    all_rects: dict[str, Rect] = {r.id: r.rect for r in rects}
    groups = {r.id: "banner" for r in rects}
    all_rects.update(zones)
    conflicts = layout_conflicts(all_rects, groups=groups, skip=empty_zones)
    out_rects = list(rects) + [
        LayoutRectOut(
            id=k,
            kind=k,
            layer_kind=None,
            label=ZONE_LABELS[k],
            rect=v,
            movable=True,
            has_text=k not in empty_zones,
        )
        for k, v in zones.items()
    ]
    return LayoutSnapshot(
        block, zones["captions"], zones["title"], zones["onscreen"], out_rects, conflicts, adjustments
    )


@dataclass(slots=True)
class LayoutComputation:
    profile: SafeZoneProfile
    spec: LayoutSpec
    auto: LayoutSnapshot
    effective: LayoutSnapshot


def compute_layout(
    variant: BannerVariant | None,
    sizes: Mapping[str, tuple[int, int]],
    profile: SafeZoneProfile,
    spec: LayoutSpec | None,
    *,
    empty_zones: Collection[str] = (),
) -> LayoutComputation:
    """Авто-раскладка и раскладка с переопределением — тем же кодом, что при рендере (без текста и ffmpeg).

    `empty_zones` — зоны без текста (`title`, `onscreen`): показываются, но в пересечения не идут.
    """
    spec = spec or LayoutSpec()
    auto = _snapshot(variant, sizes, profile, None, empty_zones=empty_zones)
    if not spec.is_manual:
        return LayoutComputation(profile, spec, auto, auto)
    shifted_variant = variant
    notes: list[str] = []
    if variant is not None and spec.has_banner_shift and spec.banner is not None:
        shifted = shift_variant(variant, spec.banner, profile, sizes)
        shifted_variant = shifted.variant
        notes = list(shifted.notes)
    effective = _snapshot(shifted_variant, sizes, profile, spec, empty_zones=empty_zones)
    effective.adjustments = notes + effective.adjustments
    return LayoutComputation(profile, spec, auto, effective)


__all__ = [
    "BLOCK_KINDS",
    "LAYOUT_MODES",
    "SCALE_MAX",
    "SCALE_MIN",
    "ZONE_IDS",
    "BannerShift",
    "LayoutComputation",
    "LayoutRectOut",
    "LayoutSnapshot",
    "LayoutSpec",
    "ShiftedVariant",
    "apply_caption_override",
    "block_layers",
    "block_rect",
    "caption_zones_json",
    "compute_layout",
    "layout_conflicts",
    "shift_variant",
]
