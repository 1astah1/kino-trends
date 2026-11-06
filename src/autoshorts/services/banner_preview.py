"""Проверка варианта баннера и «Кадр из рендера» (PNG) тем же кодом, что финальный рендер.

    result, profile_ids = validate_domain_variant(variant, creatives, platform="youtube", duration_sec=21.4)
    png, t_sec = render_preview(variant, creatives, platform="youtube", duration_sec=21.4, moment="impulse")

Проверка — `pipeline/banner_validate.validate_variant` по профилям `config/safe_zones.json`
(пусто — профиль площадки + общий `common_organic`) плюс замер служебного текста
`banner_text.layout_text` (не влез даже минимальным кеглем — `text_truncated`). Без записи на диск:
редактор зовёт проверку на каждое перетаскивание (debounce 150 мс).

Кадр: `banner_overlay.prepare_banner_plan` (те же PNG-куски, позиции в пикселях выхода и окна, что
в рендере) → `banner_overlay.build_filter_graph` на коротком отрезке времени вокруг `t` (окна,
содержащие `t`, сдвигаются к нулю; статике хватает 6,2 с для любых fade, у анимации сохраняется
фаза) → один кадр ffmpeg в PNG (`nice -n 10`, 2 потока, по одному рендеру за раз на процесс).
Готовый кадр кешируется по sha манифеста (вариант, куски плана, фон, момент, размер).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import math
import os
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps

from autoshorts.core.errors import AppError, NotFound, RateLimited
from autoshorts.core.storage import get_layout, prune_older_than
from autoshorts.domain.banner_schema import (
    BannerCreative,
    BannerLayerKind,
    BannerValidationCode,
    BannerValidationWarning,
    BannerVariant,
    Rect,
    SafeZoneProfile,
    TimeWindow,
    WarningSeverity,
)
from autoshorts.pipeline.banner_overlay import (
    DEFAULT_TEXT_STYLES,
    OverlayItem,
    build_filter_graph,
    prepare_banner_plan,
)
from autoshorts.pipeline.banner_place import PLATFORM_PROFILE, load_safe_zone_catalog
from autoshorts.pipeline.banner_text import (
    TextLayerError,
    code_url_from_template,
    default_fonts_dir,
    fill_placeholders,
    layout_text,
)
from autoshorts.pipeline.banner_validate import BannerValidateResult, validate_variant
from autoshorts.pipeline.probe import MediaCommandError, run_media_cmd
from autoshorts.services.products import field_error

log = logging.getLogger(__name__)

SAMPLE_CODE = "k7m2q"
PREVIEW_FPS = 30.0
FADE_GUARD_SEC = 6.2  # 2 × максимальный fade (3 с) + запас: статике сдвиг окна не меняет альфу в t
PREVIEW_VERSION = 1
PREVIEW_TIMEOUT_SEC = 60.0
CACHE_MAX_AGE_SEC = 24 * 3600
SIZES = {"half": (540, 960), "full": (1080, 1920)}
_SEVERITY_ORDER = {WarningSeverity.ERROR: 0, WarningSeverity.WARN: 1, WarningSeverity.INFO: 2}
_TEXT_KINDS = frozenset(
    {
        BannerLayerKind.TEXT_CODE,
        BannerLayerKind.TEXT_ERID,
        BannerLayerKind.MICRO_BRAND,
        BannerLayerKind.FALLBACK_PLATE,
    }
)

_render_slot = threading.BoundedSemaphore(1)
_prune_lock = threading.Lock()
_last_prune = 0.0


# ---------------------------------------------------------------- профили и шрифты


def resolve_profiles(platform: str, profile_ids: Sequence[str] | None = None) -> list[SafeZoneProfile]:
    """Профили safe zone: заданные (первый — основной) или [профиль площадки, common_organic]."""
    catalog = load_safe_zone_catalog()
    ids = list(profile_ids or [])
    if not ids:
        for pid in (PLATFORM_PROFILE.get(platform), catalog.default_profile):
            if pid and pid not in ids and any(p.id == pid for p in catalog.profiles):
                ids.append(pid)
    out: list[SafeZoneProfile] = []
    for idx, pid in enumerate(dict.fromkeys(ids)):
        try:
            out.append(catalog.get(pid))
        except KeyError:
            raise field_error(["profile_ids", idx], f"Нет профиля safe zone {pid}", "not_found") from None
    return out


def check_fonts(layers: Sequence[Any], *, loc: str = "layers") -> None:
    """Шрифты служебного текста должны лежать в config/fonts (редактор предлагает только их)."""
    fonts = Path(default_fonts_dir())
    for idx, layer in enumerate(layers):
        style = layer.get("style") if isinstance(layer, dict) else getattr(layer, "style", None)
        font = style.get("font_file") if isinstance(style, dict) else getattr(style, "font_file", None)
        if font and not (fonts / font).is_file():
            raise field_error(
                [loc, idx, "style", "font_file"], f"Шрифта {font} нет в config/fonts", "not_found"
            )


# ---------------------------------------------------------------- проверка


def _text_warnings(variant: BannerVariant, result: BannerValidateResult) -> list[BannerValidationWarning]:
    """Служебный текст, который не влезает в слот даже минимальным кеглем (без отрисовки на диск)."""
    out: list[BannerValidationWarning] = []
    layers = {la.id: la for la in variant.layers}
    code_url = code_url_from_template(variant.code_url_template, SAMPLE_CODE)
    marking = variant.ad_marking
    for pl in result.placement.layers:
        layer = layers.get(pl.layer_id)
        if layer is None or pl.fit is not None or layer.kind not in _TEXT_KINDS or not layer.text:
            continue
        text = fill_placeholders(
            layer.text,
            code=SAMPLE_CODE,
            code_url=code_url,
            erid=marking.erid,
            advertiser=marking.advertiser_text,
        )
        if not text:
            continue
        style = layer.style or DEFAULT_TEXT_STYLES.get(layer.kind)
        if style is None:
            continue
        try:
            _, px, _, truncated = layout_text(text, style, (max(4, pl.slot_rect.w), max(4, pl.slot_rect.h)))
        except (TextLayerError, OSError, ValueError):
            continue
        if truncated:
            out.append(
                BannerValidationWarning(
                    code=BannerValidationCode.TEXT_TRUNCATED,
                    severity=WarningSeverity.WARN,
                    layer_id=layer.id,
                    message="Текст не помещается в слот даже минимальным кеглем и будет обрезан",
                    details={"font_px": px},
                )
            )
    return out


def validate_domain_variant(
    variant: BannerVariant,
    creatives: Mapping[str, BannerCreative],
    *,
    platform: str,
    duration_sec: float,
    profile_ids: Sequence[str] | None = None,
    captions_zone: Rect | None = None,
) -> tuple[BannerValidateResult, list[str]]:
    """`banner_validate.validate_variant` + замер текста. → (результат, id проверенных профилей)."""
    profiles = resolve_profiles(platform, profile_ids)
    result = validate_variant(
        variant, creatives, profiles=profiles, duration_sec=duration_sec, captions_zone=captions_zone
    )
    extra = _text_warnings(variant, result)
    if extra:
        seen = {(w.code, w.layer_id) for w in result.warnings}
        merged = result.warnings + [w for w in extra if (w.code, w.layer_id) not in seen]
        result.warnings = sorted(merged, key=lambda w: _SEVERITY_ORDER[w.severity])
    return result, [p.id for p in profiles]


# ---------------------------------------------------------------- фон кадра


def _cache_root() -> Path:
    root = get_layout().tmp_dir(create=True) / "banner_preview"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _atomic_png(im: Image.Image, path: Path) -> Path:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    im.save(tmp, format="PNG", compress_level=3)
    os.replace(tmp, path)
    return path


def _sample_background(size: tuple[int, int]) -> Image.Image:
    """Нейтральный «кадр ролика»: тёмный градиент и мягкие пятна, чтобы видеть прозрачность креатива."""
    w, h = size
    grad = Image.linear_gradient("L").resize((w, h))
    im = ImageOps.colorize(grad, black="#4b5667", white="#141820").convert("RGBA")
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for fx, fy, fr, color in ((0.25, 0.3, 0.22, (120, 150, 190, 38)), (0.8, 0.62, 0.3, (190, 140, 90, 30))):
        r = fr * w
        draw.ellipse((fx * w - r, fy * h - r, fx * w + r, fy * h + r), fill=color)
    return Image.alpha_composite(im, overlay).convert("RGB")


def _checker_background(size: tuple[int, int]) -> Image.Image:
    w, h = size
    tile = max(4, round(24 * w / 1080))
    im = Image.new("RGB", (w, h), "#bdbdbd")
    draw = ImageDraw.Draw(im)
    for y in range(0, h, tile):
        for x in range((y // tile) % 2 * tile, w, tile * 2):
            draw.rectangle((x, y, x + tile - 1, y + tile - 1), fill="#8f8f8f")
    return im


def _job_background(job_id: int, t_sec: float, size: tuple[int, int], cache: Path) -> tuple[Path, str]:
    """Кадр ролика в момент t: база без баннера (scenes_video), иначе превью или финал."""
    from autoshorts.db.pool import connection

    with connection() as conn:
        if conn.execute("SELECT 1 FROM jobs WHERE id = %(id)s", {"id": job_id}).fetchone() is None:
            raise NotFound("job", "Ролик не найден")
        row = conn.execute(
            """
            SELECT path FROM artifacts
            WHERE job_id = %(id)s AND deleted_at IS NULL AND kind IN ('scenes_video', 'preview', 'final')
            ORDER BY CASE kind WHEN 'scenes_video' THEN 0 WHEN 'preview' THEN 1 ELSE 2 END, id DESC
            LIMIT 1
            """,
            {"id": job_id},
        ).fetchone()
    if row is None:
        raise NotFound("job_media", "У ролика ещё нет видео для фона")
    src = get_layout().resolve(row["path"])
    if not src.is_file():
        raise NotFound("job_media", "Видео ролика не найдено на диске")
    w, h = size
    key = hashlib.sha256(f"{src}|{src.stat().st_mtime_ns}|{t_sec:.3f}|{w}x{h}".encode()).hexdigest()[:32]
    out = cache / f"bg_job_{key}.png"
    if not out.is_file():
        tmp = out.with_name(f".{out.name}.tmp.png")
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
        run_media_cmd(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{t_sec:.3f}",
                "-i",
                str(src),
                "-frames:v",
                "1",
                "-vf",
                vf,
                "-an",
                str(tmp),
            ],
            timeout_sec=PREVIEW_TIMEOUT_SEC,
        )
        os.replace(tmp, out)
    return out, f"job:{key}"


def _asset_background(asset_id: int, size: tuple[int, int], cache: Path) -> tuple[Path, str]:
    from autoshorts.db.pool import connection
    from autoshorts.repos import assets as assets_repo

    with connection() as conn:
        row = assets_repo.get(conn, asset_id)
    if row is None:
        raise NotFound("asset", "Ассет не найден")
    if not str(row["mime"]).startswith("image/"):
        raise field_error("background", "Фон — только картинка", "not_image")
    src = get_layout().resolve(row["path"])
    w, h = size
    key = hashlib.sha256(f"{row['path']}|{w}x{h}".encode()).hexdigest()[:32]
    out = cache / f"bg_asset_{key}.png"
    if not out.is_file():
        with Image.open(src) as im:
            _atomic_png(ImageOps.fit(im.convert("RGB"), (w, h), Image.Resampling.LANCZOS), out)
    return out, f"asset:{key}"


def background_input(spec: str, size: tuple[int, int], t_sec: float, cache: Path) -> tuple[Path | None, str]:
    """(PNG фона или None для чёрного, ключ фона для манифеста)."""
    w, h = size
    if spec == "black":
        return None, "black"
    if spec in ("sample", "checker"):
        out = cache / f"bg_{spec}_v1_{w}x{h}.png"
        if not out.is_file():
            _atomic_png(_sample_background(size) if spec == "sample" else _checker_background(size), out)
        return out, spec
    kind, _, raw_id = spec.partition(":")
    if kind == "job":
        return _job_background(int(raw_id), t_sec, size, cache)
    if kind == "asset":
        return _asset_background(int(raw_id), size, cache)
    raise field_error("background", "Неизвестный фон", "value_error")


# ---------------------------------------------------------------- момент и отрезок времени


def preview_moment(
    plan_windows: Mapping[str, list[TimeWindow]],
    *,
    moment: str,
    t_sec: float | None,
    duration_sec: float,
    fps: float = PREVIEW_FPS,
) -> float:
    """Время кадра: `at_time` — t_sec; `impulse` / `final_repeat` — середина первого / последнего окна."""
    wins = list(plan_windows.get("with_creative") or [])
    if moment == "at_time":
        t = float(t_sec or 0.0)
    elif moment == "final_repeat":
        t = (wins[-1].start_sec + wins[-1].end_sec) / 2 if wins else duration_sec - 1.0
    else:
        t = (wins[0].start_sec + wins[0].end_sec) / 2 if wins else duration_sec / 2
    t = min(max(0.0, t), max(0.0, duration_sec - 1.0 / fps))
    return round(round(t * fps) / fps, 3)


def localize_items(
    items: Sequence[OverlayItem], t_sec: float, *, fps: float = PREVIEW_FPS
) -> tuple[list[OverlayItem], float]:
    """Оставить окна, содержащие t, и сдвинуть время к нулю. → (наложения, сдвиг).

    Статика: окна, начавшиеся раньше сдвига, начинаются с 0 — к моменту t их fade-in давно
    закончился, fade-out считается от конца окна и не меняется. Анимация: сдвиг не дальше начала
    её окна, чтобы кадр анимации в t был тем же, что в рендере.
    """
    selected = [(item, w) for item in items for w in item.windows if w.start_sec <= t_sec < w.end_sec]
    shift = max(0.0, t_sec - FADE_GUARD_SEC)
    for item, w in selected:
        if item.kind != "static":
            shift = min(shift, w.start_sec)
    shift = math.floor(shift * fps) / fps
    out = [
        dataclasses.replace(
            item, windows=(TimeWindow(start_sec=max(0.0, w.start_sec - shift), end_sec=w.end_sec - shift),)
        )
        for item, w in selected
    ]
    return out, shift


def _ts(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".") or "0"


# ---------------------------------------------------------------- кадр


def _maybe_prune(cache: Path) -> None:
    global _last_prune
    now = time.monotonic()
    with _prune_lock:
        if now - _last_prune < 3600:
            return
        _last_prune = now
    prune_older_than(cache, CACHE_MAX_AGE_SEC)
    prune_older_than(cache / "parts", CACHE_MAX_AGE_SEC)


def render_preview(
    variant: BannerVariant,
    creatives: Mapping[str, BannerCreative],
    *,
    platform: str,
    duration_sec: float,
    moment: str = "impulse",
    t_sec: float | None = None,
    profile_id: str | None = None,
    background: str = "sample",
    size: str = "half",
    code: str | None = None,
) -> tuple[bytes, float]:
    """PNG-кадр варианта. → (байты PNG, фактическое время кадра)."""
    out_size = SIZES[size]
    profile = resolve_profiles(platform, [profile_id] if profile_id else None)[0]
    layout = get_layout()
    cache = _cache_root()
    _maybe_prune(cache)
    plan = prepare_banner_plan(
        variant,
        creatives,
        media_root=layout.root,
        duration_sec=duration_sec,
        out_size=out_size,
        profile=profile,
        cache_dir=cache / "parts",
        fps=PREVIEW_FPS,
        code=code or SAMPLE_CODE,
    )
    t = preview_moment(plan.windows, moment=moment, t_sec=t_sec, duration_sec=duration_sec)
    bg_path, bg_key = background_input(background, out_size, t, cache)
    items, shift = localize_items(plan.items, t)
    local_t = round(t - shift, 3)
    manifest = {
        "v": PREVIEW_VERSION,
        "size": list(out_size),
        "t": t,
        "shift": shift,
        "duration": round(duration_sec, 3),
        "bg": bg_key,
        "items": [
            {
                "path": str(i.path),
                "kind": i.kind,
                "xywh": [i.x, i.y, i.w, i.h],
                "crop": i.crop.model_dump() if i.crop else None,
                "windows": [w.model_dump() for w in i.windows],
                "fade": [i.fade_in_ms, i.fade_out_ms],
                "opacity": i.opacity,
                "loop": i.loop,
            }
            for i in items
        ],
    }
    sha = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    out = cache / f"frame_{sha}.png"
    if out.is_file():
        return out.read_bytes(), t
    w, h = out_size
    base_dur = _ts(local_t + 0.5)
    fps_s = _ts(PREVIEW_FPS)
    if bg_path is None:
        base = ["-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r={fps_s}:d={base_dur}"]
    else:
        base = ["-loop", "1", "-framerate", fps_s, "-t", base_dur, "-i", str(bg_path)]
    graph = build_filter_graph(items, duration_sec=duration_sec - shift, fps=PREVIEW_FPS, first_input_index=1)
    tmp = out.with_name(f".{out.name}.{os.getpid()}.tmp.png")
    cmd = [
        "ffmpeg", "-y", "-threads", "2", *base, *graph.input_args,
        "-filter_complex", graph.filter_complex, "-map", f"[{graph.out_label}]",
        "-ss", _ts(local_t), "-frames:v", "1", "-an", "-c:v", "png", "-f", "image2", "-update", "1", str(tmp),
    ]  # fmt: skip
    if not _render_slot.acquire(timeout=20):
        raise RateLimited("Кадр уже рендерится, повторите через пару секунд", details={"retry_after_sec": 3})
    try:
        run_media_cmd(cmd, timeout_sec=PREVIEW_TIMEOUT_SEC)
        os.replace(tmp, out)
    except MediaCommandError as exc:
        log.warning("banner preview ffmpeg failed: %s; %s", exc, getattr(exc, "stderr_tail", "")[-800:])
        raise AppError(
            "Кадр не отрендерился: ffmpeg завершился с ошибкой",
            status=500,
            code="internal_error",
            details={"reason": "ffmpeg_failed"},
        ) from None
    finally:
        _render_slot.release()
        tmp.unlink(missing_ok=True)
    return out.read_bytes(), t
