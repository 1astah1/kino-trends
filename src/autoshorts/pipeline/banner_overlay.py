"""Субтитры и баннер одним проходом ffmpeg: входы и filter_complex.

Порядок слоёв: база → субтитры (ASS/libass в зоне субтитров) → баннер (креатив или запасная
плашка) → служебный текст (код ролика, «Реклама · erid»). Субтитры не перекрывают баннер:
зона субтитров считается `banner_place.compute_captions_zone`, стили ASS переписываются под неё
(`restyle_ass_for_zone`), всё кодируется одним проходом (research §7.2, §7.3).

    plan = prepare_banner_plan(variant, creatives, media_root=..., duration_sec=21.4,
                               out_size=(1080, 1920), profile=profile, cache_dir=...,
                               code="k7m2q", erid=None)
    ass = restyle_ass_for_zone(src_ass, workdir / "captions.ass", plan.captions_zone)
    cmd = build_render_command(base_video, plan, out_path, ass_path=ass, audio_path=voice)
    run_media_cmd(cmd, timeout_sec=...)          # TODO(M2): ffmpeg_exec.run_media

Как показываются слои:
* статика (креатив PNG, текст, их композит) — зацикленная картинка `-loop 1 -framerate F -t d
  -itsoffset S -i x.png` на каждое окно; появление/исчезание — `fade` по альфе;
* анимация (нормализованный VP9 WebM; альфа — декодер `-c:v libvpx-vp9` перед `-i`) —
  `-stream_loop -1` при loop, `trim` до длины окна, `setpts=…+S/TB`; без loop последний кадр
  держится `tpad=stop_mode=clone`;
* микро-бренд — `persistent`, весь ролик; креатив, код и erid — в окнах группы `with_creative`
  (импульс на 40–65% длительности 3–5 с + финальный повтор 2–3 с, всё настраивается);
* все статические слои одной группы, стоящие рядом по z, склеиваются в Pillow в один PNG —
  одно наложение на окно вместо трёх;
* RGBA-слои переводятся в YUV матрицей BT.709, выход помечается BT.709 (цвета креатива не плывут).
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageFilter

from autoshorts.domain.banner_schema import (
    BannerCreative,
    BannerLayer,
    BannerLayerKind,
    BannerTiming,
    BannerValidationCode,
    BannerValidationWarning,
    BannerVariant,
    CreativeKind,
    LayerShow,
    Rect,
    SafeZoneProfile,
    ShadowSpec,
    TextStyle,
    TimeWindow,
    TimingMode,
    WarningSeverity,
)
from autoshorts.pipeline.banner_assets import creative_file
from autoshorts.pipeline.banner_place import (
    CaptionsZone,
    FitResult,
    VariantPlacement,
    anchor_in_slot,
    compute_captions_zone,
    output_scale,
    place_variant,
    to_output,
)
from autoshorts.pipeline.banner_text import (
    TextRenderResult,
    code_url_from_template,
    default_fonts_dir,
    fill_placeholders,
    render_text_layer,
    rounded_mask,
)
from autoshorts.pipeline.encode_profiles import EncodeProfile, final_profile
from autoshorts.pipeline.probe import MediaInfo, ffmpeg_threads, probe_media, run_media_cmd

PLAN_VERSION = 1
MIN_WINDOW_SEC = 0.05
BT709_TAGS = "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
TO_YUVA_BT709 = "scale=out_color_matrix=bt709:out_range=tv,format=yuva420p"
DEFAULT_TEXT_STYLES: dict[BannerLayerKind, TextStyle] = {
    BannerLayerKind.TEXT_CODE: TextStyle(max_font_px=36, min_font_px=24),
    BannerLayerKind.TEXT_ERID: TextStyle(
        font_file="Montserrat-SemiBold.ttf", max_font_px=24, min_font_px=18, bg_opacity=0.45
    ),
    BannerLayerKind.MICRO_BRAND: TextStyle(
        font_file="Montserrat-SemiBold.ttf", max_font_px=44, min_font_px=36, bg_opacity=0.35
    ),
    BannerLayerKind.FALLBACK_PLATE: TextStyle(
        max_font_px=66, min_font_px=44, bg_opacity=0.7, max_lines=2, corner_radius_px=14, hug_text=False
    ),
}


# ---------------------------------------------------------------- тайминг


def _merge(windows: list[tuple[float, float]], gap: float) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for s, e in sorted(windows):
        if out and s - out[-1][1] <= gap:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def compute_windows(timing: BannerTiming, duration_sec: float) -> tuple[list[TimeWindow], list[str]]:
    """Окна показа группы `with_creative` для ролика длиной `duration_sec`.

    → (окна, заметки). Заметка `impulse_window_stretched` — ролик короткий, импульс растянут
    за 65% до `impulse_min_sec` (или до конца ролика).
    """
    d = float(duration_sec)
    notes: list[str] = []
    if d <= MIN_WINDOW_SEC:
        return [], notes
    if timing.mode == TimingMode.PERSISTENT:
        return [TimeWindow(start_sec=0.0, end_sec=round(d, 3))], notes
    raw: list[tuple[float, float]] = []
    if timing.mode == TimingMode.WINDOWS:
        for w in timing.windows:
            s, e = max(0.0, w.start_sec), min(d, w.end_sec)
            if e - s >= MIN_WINDOW_SEC:
                raw.append((s, e))
    else:
        lo, hi = timing.impulse_window_pct
        start = max(timing.hook_silence_sec, d * lo)
        if start >= d - MIN_WINDOW_SEC:
            start = max(0.0, d - timing.impulse_min_sec)
            notes.append("impulse_window_stretched")
        end = min(start + timing.impulse_duration_sec, d * hi)
        if end - start < timing.impulse_min_sec:
            end = min(start + timing.impulse_min_sec, d)
            if "impulse_window_stretched" not in notes:
                notes.append("impulse_window_stretched")
        if end - start >= MIN_WINDOW_SEC:
            raw.append((start, end))
        if timing.final_repeat:
            # финальный повтор тоже не залезает в первые секунды хука (важно на роликах 4–6 с)
            final_start = max(d - timing.final_repeat_sec, min(timing.hook_silence_sec, start), 0.0)
            if d - final_start >= MIN_WINDOW_SEC:
                raw.append((final_start, d))
    merged = _merge(raw, timing.merge_gap_sec if timing.mode == TimingMode.IMPULSE else 0.0)
    return [TimeWindow(start_sec=round(s, 3), end_sec=round(e, 3)) for s, e in merged], notes


# ---------------------------------------------------------------- ASS в зону субтитров


def _split_fields(line: str, n: int) -> list[str]:
    head, _, rest = line.partition(":")
    parts = rest.lstrip().split(",", n - 1)
    return [head, *parts]


def restyle_ass_for_zone(src: Path, dst: Path, zone: CaptionsZone) -> Path:
    """Копия ASS, где все стили стоят в зоне субтитров: MarginL/R по краям зоны, MarginV —
    от низа (Alignment 1–3) или от верха (7–9); выравнивание по центру (4–6) переводится вниз.
    Поля отступов событий обнуляются (действует стиль). Координаты — в PlayRes скрипта.
    """
    text = Path(src).read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    play_x, play_y = 384, 288  # умолчания libass, если PlayRes не задан
    for ln in lines:
        low = ln.strip().lower()
        if low.startswith("playresx:"):
            play_x = int(float(ln.split(":", 1)[1]))
        elif low.startswith("playresy:"):
            play_y = int(float(ln.split(":", 1)[1]))
    kx, ky = play_x / zone.canvas_w, play_y / zone.canvas_h
    section = ""
    style_fmt: list[str] = []
    event_fmt: list[str] = []
    out: list[str] = []
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped.lower()
            out.append(ln)
            continue
        low = stripped.lower()
        if low.startswith("format:"):
            fields = [f.strip().lower() for f in stripped.split(":", 1)[1].split(",")]
            if section in ("[v4+ styles]", "[v4 styles]"):
                style_fmt = fields
            elif section == "[events]":
                event_fmt = fields
            out.append(ln)
            continue
        if low.startswith("style:") and style_fmt:
            parts = _split_fields(stripped, len(style_fmt))
            vals = [p.strip() for p in parts[1:]]
            idx = {name: i for i, name in enumerate(style_fmt)}
            align = int(vals[idx["alignment"]]) if "alignment" in idx else 2
            if 4 <= align <= 6:
                align -= 3
            if "alignment" in idx:
                vals[idx["alignment"]] = str(align)
            if "marginl" in idx:
                vals[idx["marginl"]] = str(round(zone.ass_margin_l * kx))
            if "marginr" in idx:
                vals[idx["marginr"]] = str(round(zone.ass_margin_r * kx))
            if "marginv" in idx:
                mv = zone.ass_margin_v_top if align >= 7 else zone.ass_margin_v_bottom
                vals[idx["marginv"]] = str(round(mv * ky))
            out.append(f"Style: {','.join(vals)}")
            continue
        if section == "[events]" and event_fmt and ":" in stripped and not low.startswith("format:"):
            kind = stripped.split(":", 1)[0]
            if kind.lower() in ("dialogue", "comment"):
                parts = _split_fields(stripped, len(event_fmt))
                vals = [p.strip() if i < len(event_fmt) - 1 else p for i, p in enumerate(parts[1:])]
                idx = {name: i for i, name in enumerate(event_fmt)}
                for key in ("marginl", "marginr", "marginv"):
                    if key in idx:
                        vals[idx[key]] = "0"
                out.append(f"{kind}: {','.join(vals)}")
                continue
        out.append(ln)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(out) + "\n", encoding="utf-8")
    return dst


# ---------------------------------------------------------------- экранирование filtergraph


def escape_filter_value(value: str) -> str:
    """Значение опции фильтра внутри filter_complex (два уровня экранирования ffmpeg)."""
    level1 = value.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
    out = []
    for ch in level1:
        if ch in "\\'[],;":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _t(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".") if v else "0"


# ---------------------------------------------------------------- план


@dataclass(frozen=True)
class OverlayItem:
    """Один накладываемый поток (статичный PNG или анимированный WebM) с окнами показа."""

    item_id: str
    kind: str  # "static" | "animated"
    path: Path
    x: int
    y: int
    w: int
    h: int
    windows: tuple[TimeWindow, ...]
    group: LayerShow
    layer_ids: tuple[str, ...]
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    opacity: float = 1.0  # только для анимации (у статики вшита в PNG)
    loop: bool = True
    duration_sec: float = 0.0
    has_alpha: bool = True
    crop: Rect | None = None  # анимация: какая часть исходника видна


@dataclass
class BannerPlan:
    items: list[OverlayItem]
    windows: dict[str, list[TimeWindow]]
    placement: VariantPlacement
    captions_zone: CaptionsZone
    output_rects: dict[str, Rect]
    out_size: tuple[int, int]
    duration_sec: float
    fps: float
    warnings: list[BannerValidationWarning] = field(default_factory=list)
    text_results: dict[str, TextRenderResult] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def rects_design(self) -> dict[str, Rect]:
        return {p.layer_id: p.rect for p in self.placement.layers}

    def to_json(self) -> dict[str, object]:
        """Сводка для stage_payload/артефакта: окна, прямоугольники, зона субтитров, предупреждения."""
        return {
            "v": PLAN_VERSION,
            "out_size": list(self.out_size),
            "duration_sec": self.duration_sec,
            "windows": {k: [w.model_dump() for w in v] for k, v in self.windows.items()},
            "rects": {k: r.edges() for k, r in self.rects_design().items()},
            "captions_zone": self.captions_zone.rect.edges(),
            "warnings": [w.model_dump(mode="json") for w in self.warnings],
            "items": [
                {
                    "id": i.item_id,
                    "kind": i.kind,
                    "layers": list(i.layer_ids),
                    "x": i.x,
                    "y": i.y,
                    "w": i.w,
                    "h": i.h,
                }
                for i in self.items
            ],
        }


@dataclass
class _Piece:
    """Слой, готовый к наложению: статичная картинка в пикселях выхода или анимация."""

    layer_id: str
    z: int
    group: LayerShow
    rect: Rect  # пиксели выхода
    image: Image.Image | None = None  # статика
    key: str = ""  # идентичность картинки для кеша композита
    creative: BannerCreative | None = None  # анимация
    creative_path: Path | None = None
    fit: FitResult | None = None
    opacity: float = 1.0
    loop: bool = True


def _sha(obj: object) -> str:
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _premult_resize(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    if im.size == size:
        return im.copy()
    return im.convert("RGBa").resize(size, Image.Resampling.LANCZOS).convert("RGBA")


def _apply_opacity(im: Image.Image, opacity: float) -> Image.Image:
    if opacity >= 1.0:
        return im
    out = im.copy()
    out.putalpha(im.getchannel("A").point(lambda v: round(v * opacity)))
    return out


def _with_shadow(im: Image.Image, rect: Rect, shadow: ShadowSpec, s: float) -> tuple[Image.Image, Rect]:
    blur = max(0, round(shadow.blur_px * s))
    dx, dy = round(shadow.offset_x * s), round(shadow.offset_y * s)
    pad = blur * 2 + max(abs(dx), abs(dy))
    w, h = im.width + 2 * pad, im.height + 2 * pad
    alpha = Image.new("L", (w, h), 0)
    alpha.paste(im.getchannel("A"), (pad + dx, pad + dy))
    if blur:
        alpha = alpha.filter(ImageFilter.GaussianBlur(blur))
    c = shadow.color.lstrip("#")
    rgb = (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    shadow_im = Image.new("RGBA", (w, h), (*rgb, 0))
    shadow_im.putalpha(alpha.point(lambda v: round(v * shadow.opacity)))
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out = Image.alpha_composite(out, shadow_im)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    layer.paste(im, (pad, pad))
    out = Image.alpha_composite(out, layer)
    return out, Rect(x=rect.x - pad, y=rect.y - pad, w=w, h=h)


def _static_creative_image(
    creative: BannerCreative,
    path: Path,
    fit: FitResult,
    out_rect: Rect,
    layer: BannerLayer,
    s: float,
    cache_dir: Path,
) -> tuple[Image.Image, Rect, str]:
    """Креатив PNG: обрезка по src_crop, ресайз под пиксели выхода, скругление, тень, прозрачность."""
    key = _sha(
        {
            "v": PLAN_VERSION,
            "creative": creative.sha256,
            "crop": fit.src_crop.edges(),
            "size": [out_rect.w, out_rect.h],
            "radius": layer.slot.corner_radius_px,
            "shadow": layer.slot.shadow.model_dump() if layer.slot.shadow else None,
            "opacity": layer.slot.opacity,
            "s": round(s, 5),
        }
    )
    cached = cache_dir / f"cr_{key}.png"
    meta = cache_dir / f"cr_{key}.json"
    if cached.is_file() and meta.is_file():
        r = json.loads(meta.read_text(encoding="utf-8"))
        with Image.open(cached) as im:
            return im.convert("RGBA"), Rect(**r["rect"]), key
    with Image.open(path) as src:
        im = src.convert("RGBA")
    c = fit.src_crop
    if (c.x, c.y, c.w, c.h) != (0, 0, im.width, im.height):
        im = im.crop((c.x, c.y, c.x2, c.y2))
    im = _premult_resize(im, (out_rect.w, out_rect.h))
    radius = round(layer.slot.corner_radius_px * s)
    if radius > 0:
        mask = rounded_mask(im.width, im.height, radius)
        a = Image.composite(im.getchannel("A"), Image.new("L", im.size, 0), mask)
        im.putalpha(a)
    im = _apply_opacity(im, layer.slot.opacity)
    rect = out_rect
    if layer.slot.shadow is not None:
        im, rect = _with_shadow(im, out_rect, layer.slot.shadow, s)
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = cached.with_suffix(".tmp.png")
    im.save(tmp, format="PNG", compress_level=3)
    tmp.replace(cached)
    meta.write_text(json.dumps({"rect": rect.model_dump()}), encoding="utf-8")
    return im, rect, key


def _text_rect_out(
    res: TextRenderResult, layer: BannerLayer, slot_rect: Rect, out_size: tuple[int, int]
) -> Rect:
    """Где стоит готовая текстовая плашка в пикселях выхода (якорь и сдвиг слота)."""
    out_w, out_h = out_size
    sx, sy, _ = output_scale(out_w, out_h)
    slot_out = to_output(slot_rect, out_w, out_h)
    slot = layer.slot.model_copy(
        update={
            "x": slot_out.x,
            "y": slot_out.y,
            "w": max(1, slot_out.w),
            "h": max(1, slot_out.h),
            "offset_x": round(layer.slot.offset_x * sx),
            "offset_y": round(layer.slot.offset_y * sy),
        }
    )
    return anchor_in_slot(res.width, res.height, slot)


def _warn(
    code: BannerValidationCode,
    message: str,
    *,
    layer_id: str | None = None,
    severity=WarningSeverity.WARN,
    **details,
) -> BannerValidationWarning:
    return BannerValidationWarning(
        code=code, severity=severity, layer_id=layer_id, message=message, details=details
    )


def _compose_runs(pieces: list[_Piece]) -> list[list[_Piece]]:
    """Соседние по z статичные слои группы → один композит, если он не раздувает площадь."""
    runs: list[list[_Piece]] = []
    cur: list[_Piece] = []
    for p in pieces:
        if p.image is None:
            if cur:
                runs.append(cur)
                cur = []
            runs.append([p])
            continue
        if cur:
            union = cur[0].rect
            for q in cur[1:]:
                union = union.union(q.rect)
            union = union.union(p.rect)
            area_sum = sum(q.rect.area for q in cur) + p.rect.area
            if union.area <= 1.6 * area_sum:
                cur.append(p)
                continue
            runs.append(cur)
        cur = [p]
    if cur:
        runs.append(cur)
    return runs


def _composite(run: list[_Piece], frame: Rect, cache_dir: Path) -> tuple[Path, Rect]:
    union = run[0].rect
    for p in run[1:]:
        union = union.union(p.rect)
    union = union.intersect(frame) or Rect(x=0, y=0, w=1, h=1)
    key = _sha(
        {"v": PLAN_VERSION, "parts": [[p.key, p.rect.model_dump()] for p in run], "u": union.model_dump()}
    )
    path = cache_dir / f"comp_{key}.png"
    if not path.is_file():
        canvas = Image.new("RGBA", (union.w, union.h), (0, 0, 0, 0))
        for p in run:
            assert p.image is not None
            layer = Image.new("RGBA", (union.w, union.h), (0, 0, 0, 0))
            layer.paste(p.image, (p.rect.x - union.x, p.rect.y - union.y))
            canvas = Image.alpha_composite(canvas, layer)
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp.png")
        canvas.save(tmp, format="PNG", compress_level=3)
        tmp.replace(path)
    return path, union


def prepare_banner_plan(
    variant: BannerVariant,
    creatives: Mapping[str, BannerCreative],
    *,
    media_root: Path,
    duration_sec: float,
    out_size: tuple[int, int],
    profile: SafeZoneProfile,
    cache_dir: Path,
    fps: float = 30.0,
    code: str | None = None,
    erid: str | None = None,
    advertiser: str | None = None,
    fonts_dir: Path | None = None,
) -> BannerPlan:
    """Всё, что нужно для прохода ffmpeg: готовые PNG, позиции в пикселях выхода, окна, зона субтитров.

    `creatives`: sha256 → BannerCreative (файлы лежат под `media_root`). Отсутствующий креатив
    заменяется запасной плашкой (`fallback_plate`) с предупреждением.
    """
    out_w, out_h = out_size
    fonts_dir = Path(fonts_dir or default_fonts_dir())
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    _, _, s = output_scale(out_w, out_h)
    frame = Rect(x=0, y=0, w=out_w, h=out_h)
    warnings: list[BannerValidationWarning] = []
    notes: list[str] = []
    erid = erid or variant.ad_marking.erid
    advertiser = advertiser or variant.ad_marking.advertiser_text
    code_url = code_url_from_template(variant.code_url_template, code)

    available = {sha: c for sha, c in creatives.items() if creative_file(media_root, c).is_file()}
    sizes = {sha: (c.width, c.height) for sha, c in available.items()}

    # 1) первая раскладка — чтобы знать итоговые слоты служебного текста (attach к креативу)
    first = place_variant(variant, sizes, profile)
    layers_by_id = {la.id: la for la in variant.layers}

    # 2) текстовые слои: рендер под слот в пикселях выхода
    text_results: dict[str, TextRenderResult] = {}
    text_sizes_design: dict[str, tuple[int, int]] = {}
    skipped: set[str] = set()
    for pl in first.layers:
        layer = layers_by_id[pl.layer_id]
        if pl.fit is not None:
            continue
        raw = layer.text or ""
        text = fill_placeholders(raw, code=code, code_url=code_url, erid=erid, advertiser=advertiser)
        if text is None:
            skipped.add(layer.id)
            if layer.kind == BannerLayerKind.TEXT_ERID:
                warnings.append(
                    _warn(
                        BannerValidationCode.ERID_MISSING_WHEN_REQUIRED,
                        "Слой «Реклама · erid» включён, но erid не задан — слой не показан",
                        layer_id=layer.id,
                        severity=WarningSeverity.ERROR
                        if variant.ad_marking.required
                        else WarningSeverity.WARN,
                    )
                )
            else:
                notes.append(f"text_placeholder_unresolved:{layer.id}")
            continue
        style = layer.style or DEFAULT_TEXT_STYLES.get(layer.kind, TextStyle())
        box_out = to_output(pl.slot_rect, out_w, out_h)
        res = render_text_layer(
            text,
            style,
            (max(4, box_out.w), max(4, box_out.h)),
            cache_dir=cache_dir,
            scale=s,
            opacity=layer.slot.opacity,
            fonts_dir=fonts_dir,
        )
        text_results[layer.id] = res
        text_sizes_design[layer.id] = (max(1, round(res.width / s)), max(1, round(res.height / s)))
        if res.truncated:
            warnings.append(
                _warn(
                    BannerValidationCode.TEXT_TRUNCATED,
                    "Текст не поместился даже минимальным кеглем и обрезан",
                    layer_id=layer.id,
                    font_px=res.font_px,
                )
            )

    # 3) финальная раскладка с точными размерами текста (для зоны субтитров и проверок)
    placed = place_variant(variant, sizes, profile, text_sizes=text_sizes_design)
    shown = [p for p in placed.layers if p.layer_id not in skipped]
    placement = VariantPlacement(
        layers=shown,
        captions_zone=compute_captions_zone([p.rect for p in shown], profile),
        profile_id=placed.profile_id,
        missing_creatives=placed.missing_creatives,
        fallback_used=placed.fallback_used,
    )
    for sha in placement.missing_creatives:
        warnings.append(
            _warn(BannerValidationCode.CREATIVE_MISSING, "Файл креатива не найден", creative_sha256=sha)
        )
    if placement.fallback_used:
        warnings.append(
            _warn(
                BannerValidationCode.FALLBACK_PLATE_USED,
                "Креатива нет — показана запасная текстовая плашка",
                severity=WarningSeverity.INFO,
            )
        )

    # 4) окна
    windows_creative, win_notes = compute_windows(variant.timing, duration_sec)
    notes.extend(win_notes)
    if "impulse_window_stretched" in win_notes:
        warnings.append(
            _warn(
                BannerValidationCode.IMPULSE_WINDOW_STRETCHED,
                "Ролик короткий: импульс растянут до минимальной длительности",
                severity=WarningSeverity.INFO,
                duration_sec=duration_sec,
            )
        )
    windows = {
        LayerShow.PERSISTENT.value: [TimeWindow(start_sec=0.0, end_sec=round(float(duration_sec), 3))],
        LayerShow.WITH_CREATIVE.value: windows_creative,
    }

    # 5) куски в пикселях выхода
    pieces: list[_Piece] = []
    output_rects: dict[str, Rect] = {}
    for pl in placement.layers:
        layer = layers_by_id[pl.layer_id]
        if pl.rect.is_empty:
            continue
        if pl.fit is not None and pl.creative_sha256:
            creative = available[pl.creative_sha256]
            out_rect = to_output(pl.fit.rect, out_w, out_h)
            if out_rect.is_empty:
                continue
            output_rects[pl.layer_id] = out_rect
            cpath = creative_file(media_root, creative)
            if creative.kind == CreativeKind.STATIC:
                im, rect, key = _static_creative_image(creative, cpath, pl.fit, out_rect, layer, s, cache_dir)
                pieces.append(_Piece(pl.layer_id, pl.z, pl.show, rect, image=im, key=key))
            else:
                if layer.slot.corner_radius_px or layer.slot.shadow:
                    warnings.append(
                        _warn(
                            BannerValidationCode.EFFECT_UNSUPPORTED_FOR_ANIMATED,
                            "Скругление и тень применяются только к статичным креативам",
                            layer_id=layer.id,
                            severity=WarningSeverity.INFO,
                        )
                    )
                loop = creative.loop if layer.loop_override is None else layer.loop_override
                pieces.append(
                    _Piece(
                        pl.layer_id,
                        pl.z,
                        pl.show,
                        out_rect,
                        creative=creative,
                        creative_path=cpath,
                        fit=pl.fit,
                        opacity=layer.slot.opacity,
                        loop=loop,
                    )
                )
            continue
        res = text_results.get(pl.layer_id)
        if res is None:
            continue
        rect = _text_rect_out(res, layer, pl.slot_rect, (out_w, out_h))
        output_rects[pl.layer_id] = rect
        with Image.open(res.path) as im:
            pieces.append(
                _Piece(pl.layer_id, pl.z, pl.show, rect, image=im.convert("RGBA"), key=res.manifest_sha)
            )

    # 6) группы → наложения
    items: list[OverlayItem] = []
    timing = variant.timing
    for group in (LayerShow.PERSISTENT, LayerShow.WITH_CREATIVE):
        wins = windows[group.value]
        if not wins:
            continue
        group_pieces = sorted((p for p in pieces if p.group == group), key=lambda p: p.z)
        if group == LayerShow.PERSISTENT:
            fade_in, fade_out = timing.persistent_fade_in_ms, 0
        else:
            fade_in, fade_out = timing.fade_in_ms, timing.fade_out_ms
        for n, run in enumerate(_compose_runs(group_pieces)):
            head = run[0]
            ids = tuple(p.layer_id for p in run)
            if head.image is None:
                c = head.creative
                assert c is not None and head.creative_path is not None and head.fit is not None
                for w in wins:
                    if c.duration_sec > w.duration + 0.05:
                        warnings.append(
                            _warn(
                                BannerValidationCode.ANIMATED_CUT_BY_WINDOW,
                                "Анимация длиннее окна показа: зритель увидит только начало",
                                layer_id=head.layer_id,
                                severity=WarningSeverity.INFO,
                                animation_sec=c.duration_sec,
                                window_sec=round(w.duration, 3),
                            )
                        )
                        break
                crop = head.fit.src_crop
                full = crop.w == c.width and crop.h == c.height and crop.x == 0 and crop.y == 0
                items.append(
                    OverlayItem(
                        item_id=f"{group.value}_{n}_anim",
                        kind="animated",
                        path=head.creative_path,
                        x=head.rect.x,
                        y=head.rect.y,
                        w=head.rect.w,
                        h=head.rect.h,
                        windows=tuple(wins),
                        group=group,
                        layer_ids=ids,
                        fade_in_ms=fade_in,
                        fade_out_ms=fade_out,
                        opacity=head.opacity,
                        loop=head.loop,
                        duration_sec=c.duration_sec,
                        has_alpha=c.has_alpha,
                        crop=None if full else crop,
                    )
                )
                continue
            path, rect = _composite(run, frame, cache_dir)
            items.append(
                OverlayItem(
                    item_id=f"{group.value}_{n}_static",
                    kind="static",
                    path=path,
                    x=rect.x,
                    y=rect.y,
                    w=rect.w,
                    h=rect.h,
                    windows=tuple(wins),
                    group=group,
                    layer_ids=ids,
                    fade_in_ms=fade_in,
                    fade_out_ms=fade_out,
                )
            )

    return BannerPlan(
        items=items,
        windows={k: list(v) for k, v in windows.items()},
        placement=placement,
        captions_zone=placement.captions_zone,
        output_rects=output_rects,
        out_size=(out_w, out_h),
        duration_sec=float(duration_sec),
        fps=float(fps),
        warnings=warnings,
        text_results=text_results,
        notes=notes,
    )


# ---------------------------------------------------------------- filter_complex


@dataclass(frozen=True)
class FilterGraph:
    input_args: list[str]  # всё, что идёт до filter_complex (входы наложений; база — отдельно)
    filter_complex: str
    out_label: str
    n_inputs: int


def _fade_chain(w: TimeWindow, fade_in_ms: int, fade_out_ms: int, duration_sec: float) -> list[str]:
    chain: list[str] = []
    fi = min(fade_in_ms / 1000.0, w.duration / 2)
    fo = min(fade_out_ms / 1000.0, w.duration / 2)
    if fi > 0:
        chain.append(f"fade=t=in:st={_t(w.start_sec)}:d={_t(fi)}:alpha=1")
    if fo > 0 and w.end_sec < duration_sec - 1e-3:
        chain.append(f"fade=t=out:st={_t(w.end_sec - fo)}:d={_t(fo)}:alpha=1")
    return chain


def build_filter_graph(
    items: Sequence[OverlayItem],
    *,
    duration_sec: float,
    fps: float = 30.0,
    first_input_index: int = 1,
    base_label: str = "0:v",
    ass_path: Path | None = None,
    fonts_dir: Path | None = None,
    tag_bt709: bool = True,
) -> FilterGraph:
    """Входы наложений и filter_complex: база → ASS → наложения по порядку → yuv420p."""
    fps_s = _t(round(fps, 3))
    input_args: list[str] = []
    chains: list[str] = []
    base_ops: list[str] = []
    if tag_bt709:
        base_ops.append(BT709_TAGS)
    if ass_path is not None:
        fd = Path(fonts_dir or default_fonts_dir())
        base_ops.append(
            f"ass=filename={escape_filter_value(str(ass_path))}:fontsdir={escape_filter_value(str(fd))}"
        )
    cur = "vbase"
    chains.append(f"[{base_label}]{','.join(base_ops) or 'null'}[{cur}]")
    idx = first_input_index
    n = 0
    for item in items:
        for w in item.windows:
            d = w.duration
            label_in = f"{idx}:v"
            if item.kind == "static":
                input_args += [
                    "-loop",
                    "1",
                    "-framerate",
                    fps_s,
                    "-t",
                    _t(d),
                    "-itsoffset",
                    _t(w.start_sec),
                    "-i",
                    str(item.path),
                ]
                ops = [
                    "format=rgba",
                    *_fade_chain(w, item.fade_in_ms, item.fade_out_ms, duration_sec),
                    TO_YUVA_BT709,
                ]
            else:
                if item.loop:
                    input_args += ["-stream_loop", "-1", "-t", _t(d + 0.5)]
                if item.has_alpha:
                    input_args += ["-c:v", "libvpx-vp9"]
                input_args += ["-i", str(item.path)]
                ops = []
                if item.crop is not None:
                    c = item.crop
                    ops.append(f"crop={c.w}:{c.h}:{c.x}:{c.y}")
                ops += [f"scale={item.w}:{item.h}:flags=lanczos", "format=yuva420p"]
                if not item.loop and item.duration_sec < d:
                    ops.append(f"tpad=stop_mode=clone:stop_duration={_t(d - item.duration_sec + 0.1)}")
                ops += [f"trim=duration={_t(d)}", f"setpts=PTS-STARTPTS+{_t(w.start_sec)}/TB"]
                if item.opacity < 1.0:
                    ops.append(f"lut=a=val*{item.opacity:.3f}")
                ops += _fade_chain(w, item.fade_in_ms, item.fade_out_ms, duration_sec)
            ov = f"ov{n}"
            nxt = f"v{n}"
            chains.append(f"[{label_in}]{','.join(ops)}[{ov}]")
            chains.append(f"[{cur}][{ov}]overlay=x={item.x}:y={item.y}:eof_action=pass:format=yuv420[{nxt}]")
            cur = nxt
            idx += 1
            n += 1
    chains.append(f"[{cur}]format=yuv420p[vout]")
    return FilterGraph(input_args, ";".join(chains), "vout", idx - first_input_index)


def build_render_command(
    base_video: Path,
    plan: BannerPlan,
    out_path: Path,
    *,
    ass_path: Path | None = None,
    audio_path: Path | None = None,
    profile: EncodeProfile | None = None,
    fonts_dir: Path | None = None,
    base_info: MediaInfo | None = None,
    base_input_args: Sequence[str] = (),
) -> list[str]:
    """Полная команда ffmpeg (без nice и глобальных опций — их добавляет запускающий)."""
    profile = profile or final_profile()
    threads = str(profile.threads or ffmpeg_threads())
    cmd = ["ffmpeg", "-y", "-threads", threads, *base_input_args, "-i", str(base_video)]
    first = 1
    if audio_path is not None:
        cmd += ["-i", str(audio_path)]
        first = 2
    graph = build_filter_graph(
        plan.items,
        duration_sec=plan.duration_sec,
        fps=plan.fps,
        first_input_index=first,
        ass_path=ass_path,
        fonts_dir=fonts_dir,
        tag_bt709=profile.tag_bt709,
    )
    cmd += graph.input_args
    cmd += ["-filter_complex", graph.filter_complex, "-map", f"[{graph.out_label}]"]
    has_audio = audio_path is not None or (base_info is None or base_info.audio is not None)
    if audio_path is not None:
        cmd += ["-map", "1:a:0"]
    elif has_audio:
        cmd += ["-map", "0:a:0?"]
    cmd += profile.output_args(fps=plan.fps, with_audio=has_audio)
    cmd += ["-t", _t(plan.duration_sec), str(out_path)]
    return cmd


# ---------------------------------------------------------------- запуск целиком


@dataclass(frozen=True)
class BannerRenderResult:
    output_path: Path
    duration_sec: float
    wall_sec: float
    cmd: list[str]
    plan: BannerPlan


def render_with_banner(
    base_video: Path,
    out_path: Path,
    *,
    variant: BannerVariant,
    creatives: Mapping[str, BannerCreative],
    media_root: Path,
    profile: SafeZoneProfile,
    workdir: Path,
    ass_path: Path | None = None,
    audio_path: Path | None = None,
    encode: EncodeProfile | None = None,
    code: str | None = None,
    erid: str | None = None,
    fonts_dir: Path | None = None,
    duration_sec: float | None = None,
    timeout_sec: float = 900.0,
) -> BannerRenderResult:
    """Удобная обёртка: probe базы → план → ASS в зону субтитров → один проход ffmpeg."""
    info = probe_media(base_video)
    if info.video is None:
        raise ValueError(f"no video stream in {base_video}")
    duration = float(
        duration_sec or (probe_media(audio_path).duration_sec if audio_path else info.duration_sec)
    )
    fps = info.video.avg_fps or info.video.fps or 30.0
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    plan = prepare_banner_plan(
        variant,
        creatives,
        media_root=media_root,
        duration_sec=duration,
        out_size=(info.video.width, info.video.height),
        profile=profile,
        cache_dir=workdir / "banner_cache",
        fps=fps,
        code=code,
        erid=erid,
        fonts_dir=fonts_dir,
    )
    ass_zoned = (
        restyle_ass_for_zone(ass_path, workdir / "captions_zoned.ass", plan.captions_zone)
        if ass_path
        else None
    )
    cmd = build_render_command(
        base_video,
        plan,
        out_path,
        ass_path=ass_zoned,
        audio_path=audio_path,
        profile=encode,
        fonts_dir=fonts_dir,
        base_info=info,
    )
    t0 = time.monotonic()
    run_media_cmd(cmd, timeout_sec=timeout_sec)
    wall = time.monotonic() - t0
    return BannerRenderResult(Path(out_path), duration, round(wall, 3), cmd, plan)


def window_contains(windows: Sequence[TimeWindow], t: float) -> bool:
    return any(w.start_sec <= t < w.end_sec for w in windows)


def overlap_sec(a: TimeWindow, b: TimeWindow) -> float:
    return max(0.0, min(a.end_sec, b.end_sec) - max(a.start_sec, b.start_sec))


def total_shown_sec(windows: Sequence[TimeWindow]) -> float:
    return math.fsum(w.duration for w in windows)
