"""Служебные текстовые слои баннера в Pillow: код ролика, «Реклама · erid», микро-бренд и
запасная текстовая плашка (только когда загруженного креатива нет).

    res = render_text_layer("u.umka.chat/k7m2q", style, box=(825, 50), cache_dir=d)
    res.path, res.width, res.height, res.font_px, res.truncated

* шрифты — локальные `config/fonts` (Montserrat), никаких системных и внешних;
* перенос по словам с учётом кириллицы: короткие предлоги и союзы («в», «на», «и» …)
  не остаются в конце строки; две строки балансируются по ширине;
* кегль подбирается сверху вниз от `max_font_px` до `min_font_px`; если не влезло и на минимуме —
  текст обрезается с «…» и `truncated=True` (validate выдаёт предупреждение);
* текст рисуется маской и накладывается `alpha_composite` — без тёмной каймы на прозрачном;
* результат кешируется по sha256 канонического манифеста (текст, стиль, размер, масштаб,
  sha шрифта, движок раскладки): `<cache_dir>/<sha>.png` + `<sha>.json`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, features

from autoshorts.domain.banner_schema import (
    PLACEHOLDER_ADVERTISER,
    PLACEHOLDER_CODE,
    PLACEHOLDER_CODE_URL,
    PLACEHOLDER_ERID,
    TextAlign,
    TextStyle,
)

RENDERER_VERSION = 1
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FONTS_DIR = PROJECT_ROOT / "config" / "fonts"
NBSP = " "
ELLIPSIS = "…"
SUPERSAMPLE = 4

# Короткие слова, которые не оставляем висеть в конце строки (привязываются к следующему)
_HANGING_WORDS = frozenset(
    "а в и к о с у во да за из ко на не ни но об от по со до для без над под при про".split()
)
_PLACEHOLDER_RE = re.compile(r"\{[a-z_]+\}")


class TextLayerError(ValueError):
    pass


def default_fonts_dir() -> Path:
    env = os.environ.get("AUTOSHORTS_FONTS_DIR")
    return Path(env) if env else DEFAULT_FONTS_DIR


def _layout_engine() -> tuple[ImageFont.Layout, str]:
    if features.check("raqm"):
        return ImageFont.Layout.RAQM, "raqm"
    return ImageFont.Layout.BASIC, "basic"


@lru_cache(maxsize=16)
def _font_sha(path: str, mtime_ns: int) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


@lru_cache(maxsize=256)
def _font(path: str, size: int, engine: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size=size, layout_engine=ImageFont.Layout(engine))


def font_path(style: TextStyle, fonts_dir: Path | None = None) -> Path:
    p = Path(fonts_dir or default_fonts_dir()) / style.font_file
    if not p.is_file():
        raise TextLayerError(f"font not found: {p}")
    return p


# ---------------------------------------------------------------- плейсхолдеры


def fill_placeholders(
    text: str,
    *,
    code: str | None = None,
    code_url: str | None = None,
    erid: str | None = None,
    advertiser: str | None = None,
) -> str | None:
    """Подставить {code_url}/{code}/{erid}/{advertiser}. Если чего-то не хватает — None."""
    values = {
        PLACEHOLDER_CODE_URL: code_url,
        PLACEHOLDER_CODE: code,
        PLACEHOLDER_ERID: erid,
        PLACEHOLDER_ADVERTISER: advertiser,
    }
    out = text
    for key, val in values.items():
        if key in out:
            if not val:
                return None
            out = out.replace(key, val)
    if _PLACEHOLDER_RE.search(out):
        return None
    return " ".join(out.split())


def code_url_from_template(template: str, code: str | None) -> str | None:
    if not code:
        return None
    return template.replace(PLACEHOLDER_CODE, code)


# ---------------------------------------------------------------- перенос


def _tokens(text: str) -> list[str]:
    words = text.split()
    out: list[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        while w.split(NBSP)[-1].lower().strip("«»\"'()") in _HANGING_WORDS and i + 1 < len(words):
            i += 1
            w = f"{w}{NBSP}{words[i]}"
        out.append(w)
        i += 1
    return out


def _width(font: ImageFont.FreeTypeFont, s: str) -> float:
    return font.getlength(s.replace(NBSP, " "))


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_w: float, max_lines: int) -> list[str] | None:
    """Разбить на ≤ max_lines строк шириной ≤ max_w или None. Две строки — сбалансированно."""
    tokens = _tokens(text)
    if not tokens:
        return [""]
    one = " ".join(tokens)
    if _width(font, one) <= max_w:
        return [one]
    if max_lines == 1 or len(tokens) == 1:
        return None
    if max_lines == 2:
        best: tuple[float, list[str]] | None = None
        for i in range(1, len(tokens)):
            a, b = " ".join(tokens[:i]), " ".join(tokens[i:])
            wa, wb = _width(font, a), _width(font, b)
            if wa <= max_w and wb <= max_w:
                score = max(wa, wb)
                if best is None or score < best[0]:
                    best = (score, [a, b])
        return best[1] if best else None
    lines: list[str] = []
    cur = ""
    for t in tokens:
        cand = f"{cur} {t}" if cur else t
        if _width(font, cand) <= max_w:
            cur = cand
            continue
        if not cur:
            return None
        lines.append(cur)
        cur = t
        if _width(font, cur) > max_w:
            return None
    if cur:
        lines.append(cur)
    return lines if len(lines) <= max_lines else None


def _truncate(text: str, font: ImageFont.FreeTypeFont, max_w: float) -> str:
    s = text.replace(NBSP, " ")
    if _width(font, s) <= max_w:
        return s
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _width(font, s[:mid].rstrip() + ELLIPSIS) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return s[:lo].rstrip() + ELLIPSIS


# ---------------------------------------------------------------- отрисовка


@dataclass(frozen=True)
class TextRenderResult:
    path: Path
    width: int
    height: int
    font_px: int
    lines: list[str]
    truncated: bool
    manifest_sha: str
    cached: bool = False

    def meta(self) -> dict[str, object]:
        d = asdict(self)
        d["path"] = self.path.name
        d.pop("cached", None)
        return d


def _hex_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def rounded_mask(w: int, h: int, radius: int) -> Image.Image:
    """Сглаженная маска скруглённого прямоугольника (суперсэмплинг ×4)."""
    if radius <= 0:
        return Image.new("L", (w, h), 255)
    r = min(radius, w // 2, h // 2)
    big = Image.new("L", (w * SUPERSAMPLE, h * SUPERSAMPLE), 0)
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, w * SUPERSAMPLE - 1, h * SUPERSAMPLE - 1), radius=r * SUPERSAMPLE, fill=255
    )
    return big.resize((w, h), Image.Resampling.LANCZOS)


def _solid(
    size: tuple[int, int], rgb: tuple[int, int, int], alpha_mask: Image.Image, opacity: float
) -> Image.Image:
    layer = Image.new("RGBA", size, (*rgb, 0))
    a = alpha_mask if opacity >= 1.0 else alpha_mask.point(lambda v: round(v * opacity))
    layer.putalpha(a)
    return layer


def _manifest(
    text: str, style: TextStyle, box: tuple[int, int], scale: float, opacity: float, fonts_dir: Path
) -> tuple[dict[str, object], str]:
    fp = font_path(style, fonts_dir)
    _, engine_name = _layout_engine()
    manifest = {
        "v": RENDERER_VERSION,
        "text": text,
        "style": style.model_dump(mode="json"),
        "box": [int(box[0]), int(box[1])],
        "scale": round(float(scale), 5),
        "opacity": round(float(opacity), 4),
        "font_sha": _font_sha(str(fp), fp.stat().st_mtime_ns),
        "engine": engine_name,
    }
    blob = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return manifest, hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _atomic_save_png(im: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".png", dir=path.parent)
    os.close(fd)
    try:
        im.save(tmp, format="PNG", compress_level=6)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def layout_text(
    text: str, style: TextStyle, box: tuple[int, int], *, scale: float = 1.0, fonts_dir: Path | None = None
) -> tuple[ImageFont.FreeTypeFont, int, list[str], bool]:
    """Подобрать кегль и строки под бокс (в пикселях выхода). → (font, px, lines, truncated)."""
    fp = str(font_path(style, fonts_dir))
    engine, _ = _layout_engine()
    pad_x, pad_y = style.padding_x * scale, style.padding_y * scale
    stroke = style.stroke_px * scale
    avail_w = box[0] - 2 * pad_x - 2 * stroke
    avail_h = box[1] - 2 * pad_y - 2 * stroke
    hi = max(1, round(style.max_font_px * scale))
    lo = max(1, min(hi, round(style.min_font_px * scale)))
    for px in range(hi, lo - 1, -1):
        font = _font(fp, px, int(engine))
        lines = wrap_text(text, font, avail_w, style.max_lines)
        if lines is None:
            continue
        asc, desc = font.getmetrics()
        block_h = (len(lines) - 1) * round(px * style.line_spacing) + asc + desc
        if block_h <= avail_h:
            return font, px, lines, False
    font = _font(fp, lo, int(engine))
    lines = wrap_text(text, font, avail_w, style.max_lines)
    if lines is None:
        lines = [_truncate(text, font, max(1.0, avail_w))]
    return font, lo, lines, True


def _draw(
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    px: int,
    style: TextStyle,
    box: tuple[int, int],
    scale: float,
    opacity: float,
) -> Image.Image:
    pad_x, pad_y = round(style.padding_x * scale), round(style.padding_y * scale)
    stroke = round(style.stroke_px * scale)
    asc, desc = font.getmetrics()
    advance = round(px * style.line_spacing)
    widths = [_width(font, ln) for ln in lines]
    text_w = int(max(widths) + 0.999) + 2 * stroke
    text_h = (len(lines) - 1) * advance + asc + desc + 2 * stroke
    if style.hug_text:
        w = min(box[0], text_w + 2 * pad_x)
        h = min(box[1], text_h + 2 * pad_y)
    else:
        w, h = box
    w, h = max(1, w), max(1, h)
    radius = round(style.corner_radius_px * scale)
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if style.bg_opacity > 0:
        canvas = Image.alpha_composite(
            canvas, _solid((w, h), _hex_rgb(style.bg_color), rounded_mask(w, h, radius), style.bg_opacity)
        )
    fill_mask = Image.new("L", (w, h), 0)
    stroke_mask = Image.new("L", (w, h), 0) if stroke else None
    d_fill = ImageDraw.Draw(fill_mask)
    d_stroke = ImageDraw.Draw(stroke_mask) if stroke_mask is not None else None
    top = (h - text_h) / 2 + stroke
    for i, (ln, lw) in enumerate(zip(lines, widths, strict=True)):
        if style.align == TextAlign.LEFT:
            x = pad_x + stroke
        elif style.align == TextAlign.RIGHT:
            x = w - pad_x - stroke - lw
        else:
            x = (w - lw) / 2
        y = top + i * advance
        text = ln.replace(NBSP, " ")
        d_fill.text((x, y), text, font=font, fill=255, anchor="la")
        if d_stroke is not None:
            d_stroke.text(
                (x, y), text, font=font, fill=255, anchor="la", stroke_width=stroke, stroke_fill=255
            )
    if stroke_mask is not None:
        canvas = Image.alpha_composite(
            canvas, _solid((w, h), _hex_rgb(style.stroke_color), stroke_mask, style.text_opacity)
        )
    canvas = Image.alpha_composite(
        canvas, _solid((w, h), _hex_rgb(style.color), fill_mask, style.text_opacity)
    )
    if opacity < 1.0:
        canvas.putalpha(canvas.getchannel("A").point(lambda v: round(v * opacity)))
    return canvas


def render_text_layer(
    text: str,
    style: TextStyle,
    box: tuple[int, int],
    *,
    cache_dir: Path,
    scale: float = 1.0,
    opacity: float = 1.0,
    fonts_dir: Path | None = None,
) -> TextRenderResult:
    """Отрисовать служебный текст в RGBA PNG под бокс `box` (пиксели выхода) с кешем.

    `scale` — масштаб дизайн-канвы 1080×1920 к выходу: кегли, отступы и радиусы стиля
    заданы для 1080×1920 и умножаются на него.
    """
    text = " ".join((text or "").split())
    if not text:
        raise TextLayerError("empty text")
    if box[0] < 4 or box[1] < 4:
        raise TextLayerError(f"box too small: {box}")
    fonts_dir = Path(fonts_dir or default_fonts_dir())
    _, sha = _manifest(text, style, box, scale, opacity, fonts_dir)
    cache_dir = Path(cache_dir)
    png, meta_path = cache_dir / f"{sha}.png", cache_dir / f"{sha}.json"
    if png.is_file() and meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return TextRenderResult(
                path=png,
                width=int(meta["width"]),
                height=int(meta["height"]),
                font_px=int(meta["font_px"]),
                lines=list(meta["lines"]),
                truncated=bool(meta["truncated"]),
                manifest_sha=sha,
                cached=True,
            )
        except (ValueError, KeyError):
            pass
    font, px, lines, truncated = layout_text(text, style, box, scale=scale, fonts_dir=fonts_dir)
    im = _draw(lines, font, px, style, box, scale, opacity)
    _atomic_save_png(im, png)
    res = TextRenderResult(png, im.width, im.height, px, lines, truncated, sha)
    tmp = meta_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(res.meta(), ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, meta_path)
    return res
