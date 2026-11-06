"""Приём загруженного креатива баннера: проверка, нормализация, контентное хранение.

Решение владельца (docs/OWNER-DECISIONS.md п. 1): баннер — готовый файл владельца. Принимаются
статичные PNG/JPG/WebP (в т. ч. с прозрачностью) и анимированные GIF, APNG, WebM (с альфой) и
MP4 (MOV — как MP4; анимированный WebP — бонусом через Pillow).

    creative = ingest_creative(upload_path, media_root)       # BannerCreative
    path = creative_file(media_root, creative)                # абсолютный путь нормализованного файла

Что делается:
* тип определяется по **сигнатуре** (magic bytes), расширение игнорируется;
* лимиты (`AssetLimits`): статика ≤ 20 МБ, анимация ≤ 50 МБ, сторона ≤ 4096 px, анимация ≤ 15 с;
* битые/обрезанные файлы и чужие форматы (SVG, HEIC, AVIF, BMP, TIFF, PDF …) отвергаются
  `BannerAssetError` с латинским `code`;
* статика → RGBA PNG: EXIF-поворот применён, ICC → sRGB, метаданные (EXIF/GPS/XMP/ICC) выброшены;
* анимация → VP9 WebM 30 fps: с альфой — yuva420p, MP4 без альфы остаётся непрозрачным (yuv420p);
  длительность одного цикла и признак loop сохраняются; сторона ужимается до канвы 1080×1920;
* рядом кладутся `thumb.png` (≤ 320 px) и `first_frame.png` для редактора;
* хранение по sha256 исходных байтов: `MEDIA_ROOT/banners/creatives/<sha[:2]>/<sha>/`
  (`creative.png|creative.webm`, `thumb.png`, `first_frame.png`, `creative.json`). Повторная
  загрузка того же файла возвращает готовую запись без перекодирования.

ffmpeg запускается через `probe.run_media_cmd` (`nice -n 10`, `-threads 2`).
TODO(M2): перейти на `pipeline/ffmpeg_exec.run_media`, когда M1-C его зафиксирует.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import struct
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from autoshorts.domain.banner_schema import BannerCreative, CreativeKind, CreativeSourceFormat
from autoshorts.pipeline.probe import (
    MediaCommandError,
    ProbeError,
    kill_media_process,
    open_media_process,
    probe_media,
    run_media_cmd,
)

NORMALIZER_VERSION = 1
CREATIVES_SUBDIR = "banners/creatives"
META_NAME = "creative.json"
MiB = 1024 * 1024

# GIF: задержка < 2 сотых → 10 сотых (как gif-демуксер ffmpeg и браузеры)
GIF_MIN_DELAY_CS = 2
GIF_DEFAULT_DELAY_CS = 10

_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1"}
_AVIF_BRANDS = {b"avif", b"avis"}
_QT_ATOMS = {b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot"}


class BannerAssetError(ValueError):
    """Файл не принят. `code` — латинский код для API/редактора, `message` — по-русски."""

    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True)
class AssetLimits:
    max_static_bytes: int = 20 * MiB
    max_animated_bytes: int = 50 * MiB
    max_side_px: int = 4096
    min_side_px: int = 16
    max_animated_sec: float = 15.0
    max_frames: int = 1800
    max_normalized_w: int = 1080
    max_normalized_h: int = 1920
    target_fps: int = 30
    thumb_px: int = 320
    vp9_crf: int = 22
    vp9_cpu_used: int = 4
    timeout_sec: float = 300.0


DEFAULT_LIMITS = AssetLimits()


# ---------------------------------------------------------------- распознавание по сигнатуре


@dataclass(frozen=True)
class SniffResult:
    source_format: CreativeSourceFormat | None
    animated: bool = False
    width: int | None = None
    height: int | None = None
    frame_count: int | None = None
    duration_sec: float | None = None
    loop: bool | None = None
    has_alpha: bool | None = None
    truncated: bool = False
    detail: str = ""

    @property
    def supported(self) -> bool:
        return self.source_format is not None


def _read_exact(f: BinaryIO, n: int) -> bytes:
    data = f.read(n)
    if len(data) != n:
        raise EOFError
    return data


def _skip_gif_subblocks(f: BinaryIO) -> None:
    while True:
        n = _read_exact(f, 1)[0]
        if n == 0:
            return
        f.seek(n, os.SEEK_CUR)


def _parse_gif(f: BinaryIO) -> SniffResult:
    """Разбор блоков GIF без декодирования: кадры, задержки, loop (NETSCAPE2.0), прозрачность."""
    f.seek(6)
    width, height, packed = struct.unpack("<HHB", _read_exact(f, 5))
    f.seek(2, os.SEEK_CUR)
    if packed & 0x80:
        f.seek(3 * (2 ** ((packed & 0x07) + 1)), os.SEEK_CUR)
    frames = 0
    total_cs = 0
    loop: bool | None = None
    transparent = False
    pending_delay: int | None = None
    truncated = False
    try:
        while True:
            b = f.read(1)
            if not b:
                break  # нет трейлера 0x3B на границе блока: браузеры и ffmpeg такое читают
            if b == b"\x3b":
                break
            if b == b"\x21":
                label = _read_exact(f, 1)[0]
                if label == 0xF9:
                    size = _read_exact(f, 1)[0]
                    data = _read_exact(f, size)
                    if size >= 4:
                        if data[0] & 0x01:
                            transparent = True
                        pending_delay = data[1] | (data[2] << 8)
                    _skip_gif_subblocks(f)
                elif label == 0xFF:
                    size = _read_exact(f, 1)[0]
                    ident = _read_exact(f, size)
                    if ident[:8] in {b"NETSCAPE", b"ANIMEXTS"}:
                        n = _read_exact(f, 1)[0]
                        if n >= 3:
                            sub = _read_exact(f, n)
                            if sub[0] == 1:
                                loop = True  # 0 — бесконечно, N — N повторов: в ролике это петля
                        elif n:
                            f.seek(n, os.SEEK_CUR)
                        if n:
                            _skip_gif_subblocks(f)
                    else:
                        _skip_gif_subblocks(f)
                else:
                    _skip_gif_subblocks(f)
            elif b == b"\x2c":
                desc = _read_exact(f, 9)
                lpacked = desc[8]
                if lpacked & 0x80:
                    f.seek(3 * (2 ** ((lpacked & 0x07) + 1)), os.SEEK_CUR)
                _read_exact(f, 1)  # LZW min code size
                _skip_gif_subblocks(f)
                frames += 1
                delay = pending_delay if pending_delay is not None else 0
                total_cs += GIF_DEFAULT_DELAY_CS if delay < GIF_MIN_DELAY_CS else delay
                pending_delay = None
            else:
                return SniffResult(
                    CreativeSourceFormat.GIF, width=width, height=height, truncated=True, detail="bad block"
                )
    except EOFError:
        truncated = True
    return SniffResult(
        CreativeSourceFormat.GIF,
        animated=frames > 1,
        width=width,
        height=height,
        frame_count=frames,
        duration_sec=total_cs / 100.0 if frames > 1 else 0.0,
        loop=bool(loop),
        has_alpha=transparent,
        truncated=truncated or frames == 0,
    )


# Минимальная длина данных служебных чанков PNG/APNG (спецификации PNG и APNG).
_PNG_MIN_CHUNK_LEN: dict[bytes, int] = {b"IHDR": 13, b"acTL": 8, b"fcTL": 26}


def _parse_png(f: BinaryIO) -> SniffResult:
    """PNG/APNG по чанкам: IHDR (альфа по типу цвета), tRNS, acTL/fcTL (кадры, задержки)."""
    f.seek(8)
    width = height = 0
    color_type = -1
    has_trns = False
    num_frames = 0
    num_plays = 0
    frames_seen = 0
    total = 0.0
    is_apng = False
    saw_iend = False
    truncated = False
    try:
        while True:
            head = f.read(8)
            if len(head) < 8:
                truncated = True
                break
            length, ctype = struct.unpack(">I4s", head)
            if length > 0x7FFFFFFF:
                truncated = True
                break
            min_len = _PNG_MIN_CHUNK_LEN.get(ctype)
            if min_len is not None and length < min_len:
                # короткий служебный чанк (IHDR < 13, acTL < 8, fcTL < 26) — файл битый
                truncated = True
                break
            if ctype == b"IHDR":
                data = _read_exact(f, length)
                width, height = struct.unpack(">II", data[:8])
                color_type = data[9]
            elif ctype == b"acTL":
                data = _read_exact(f, length)
                num_frames, num_plays = struct.unpack(">II", data[:8])
                is_apng = True
            elif ctype == b"fcTL":
                data = _read_exact(f, length)
                dnum, dden = struct.unpack(">HH", data[20:24])
                total += dnum / (dden or 100)
                frames_seen += 1
            elif ctype == b"tRNS":
                has_trns = True
                f.seek(length, os.SEEK_CUR)
            else:
                f.seek(length, os.SEEK_CUR)
            if len(f.read(4)) < 4:  # CRC
                truncated = True
                break
            if ctype == b"IEND":
                saw_iend = True
                break
    except EOFError:
        truncated = True
    alpha = color_type in (4, 6) or has_trns
    animated = is_apng and num_frames > 1
    return SniffResult(
        CreativeSourceFormat.APNG if animated else CreativeSourceFormat.PNG,
        animated=animated,
        width=width or None,
        height=height or None,
        frame_count=(num_frames or frames_seen) if animated else 1,
        duration_sec=total if animated else 0.0,
        loop=(num_plays != 1) if animated else None,
        has_alpha=alpha,
        truncated=truncated or not saw_iend,
    )


def _u24(b: bytes) -> int:
    return b[0] | (b[1] << 8) | (b[2] << 16)


def _parse_webp(f: BinaryIO, file_size: int) -> SniffResult:
    f.seek(12)
    width = height = None
    alpha = False
    animated_flag = False
    loop: bool | None = None
    frames = 0
    total_ms = 0
    truncated = False
    try:
        while f.tell() + 8 <= file_size:
            ctype, size = struct.unpack("<4sI", _read_exact(f, 8))
            start = f.tell()
            if ctype == b"VP8X":
                data = _read_exact(f, 10)
                flags = data[0]
                alpha = bool(flags & 0x10)
                animated_flag = bool(flags & 0x02)
                width, height = _u24(data[4:7]) + 1, _u24(data[7:10]) + 1
            elif ctype == b"ANIM":
                data = _read_exact(f, 6)
                loop = True if struct.unpack("<H", data[4:6])[0] != 1 else False
            elif ctype == b"ANMF":
                data = _read_exact(f, 16)
                dur = _u24(data[12:15])
                total_ms += dur if dur > 10 else 100
                frames += 1
            elif ctype == b"VP8L" and width is None:
                data = _read_exact(f, 5)
                bits = struct.unpack("<I", data[1:5])[0]
                width, height = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
                alpha = bool((bits >> 28) & 1)
            elif ctype == b"VP8 " and width is None:
                data = _read_exact(f, 10)
                width, height = struct.unpack("<HH", data[6:10])
                width, height = width & 0x3FFF, height & 0x3FFF
            elif ctype == b"ALPH":
                alpha = True
            f.seek(start + size + (size & 1))
        if f.tell() > file_size:
            truncated = True
    except (EOFError, struct.error):
        truncated = True
    animated = animated_flag and frames > 1
    return SniffResult(
        CreativeSourceFormat.WEBP_ANIMATED if animated else CreativeSourceFormat.WEBP,
        animated=animated,
        width=width,
        height=height,
        frame_count=frames if animated else 1,
        duration_sec=total_ms / 1000.0 if animated else 0.0,
        loop=loop if animated else None,
        has_alpha=alpha,
        truncated=truncated,
    )


# Что может бросить разбор битого или враждебного заголовка. Всё это — повреждённый файл
# (corrupt_file / 422), а не падение обработчика загрузки.
_MALFORMED_HEADER_ERRORS: tuple[type[Exception], ...] = (
    EOFError,
    IndexError,
    struct.error,
    ValueError,
    OverflowError,
)


def _guarded_parse(fmt: CreativeSourceFormat, parse: Callable[[], SniffResult]) -> SniffResult:
    try:
        return parse()
    except _MALFORMED_HEADER_ERRORS as exc:
        return SniffResult(fmt, truncated=True, detail=f"malformed_header:{type(exc).__name__}")


def sniff_file(path: Path) -> SniffResult:
    """Тип файла по содержимому. Неизвестное → `source_format=None` и `detail` (что это было)."""
    path = Path(path)
    size = path.stat().st_size
    with open(path, "rb") as f:
        head = f.read(64)
        if not head:
            return SniffResult(None, detail="empty")
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return _guarded_parse(CreativeSourceFormat.PNG, lambda: _parse_png(f))
        if head[:3] == b"\xff\xd8\xff":
            return SniffResult(CreativeSourceFormat.JPEG, has_alpha=False)
        if head[:6] in (b"GIF87a", b"GIF89a"):
            return _guarded_parse(CreativeSourceFormat.GIF, lambda: _parse_gif(f))
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return _guarded_parse(CreativeSourceFormat.WEBP, lambda: _parse_webp(f, size))
        if head[:4] == b"\x1a\x45\xdf\xa3":
            probe_head = head + f.read(256)
            if b"webm" in probe_head[:128] or b"matroska" in probe_head[:128]:
                return SniffResult(CreativeSourceFormat.WEBM, animated=True)
            return SniffResult(None, detail="ebml")
        if head[4:8] == b"ftyp":
            major = head[8:12]
            brands = {major} | {head[i : i + 4] for i in range(16, min(len(head), 40), 4)}
            if major in _AVIF_BRANDS or (major in _HEIF_BRANDS and brands & _AVIF_BRANDS):
                return SniffResult(None, detail="avif")
            if major in _HEIF_BRANDS:
                return SniffResult(None, detail="heic")
            if major == b"qt  ":
                return SniffResult(CreativeSourceFormat.MOV, animated=True)
            return SniffResult(CreativeSourceFormat.MP4, animated=True)
        if head[4:8] in _QT_ATOMS:
            return SniffResult(CreativeSourceFormat.MOV, animated=True)
    stripped = head.lstrip()
    for magic, name in (
        (b"<svg", "svg"),
        (b"<?xml", "xml_or_svg"),
        (b"<!DOCTYPE", "html"),
        (b"<html", "html"),
        (b"%PDF", "pdf"),
        (b"BM", "bmp"),
        (b"II*\x00", "tiff"),
        (b"MM\x00*", "tiff"),
        (b"\x00\x00\x01\x00", "ico"),
        (b"\xff\x0a", "jxl"),
        (b"8BPS", "psd"),
        (b"PK\x03\x04", "zip"),
    ):
        if stripped.startswith(magic) or head.startswith(magic):
            return SniffResult(None, detail=name)
    return SniffResult(None, detail="unknown")


# ---------------------------------------------------------------- хранение


def creative_dir(media_root: Path, sha256: str) -> Path:
    return Path(media_root) / CREATIVES_SUBDIR / sha256[:2] / sha256


def creative_file(media_root: Path, creative: BannerCreative) -> Path:
    return _resolve_rel(media_root, creative.normalized_path)


def creative_thumb(media_root: Path, creative: BannerCreative) -> Path:
    return _resolve_rel(media_root, creative.thumb_path)


def creative_first_frame(media_root: Path, creative: BannerCreative) -> Path:
    return _resolve_rel(media_root, creative.first_frame_path)


def _resolve_rel(media_root: Path, rel: str) -> Path:
    root = Path(media_root).resolve()
    p = (root / rel).resolve()
    if root not in p.parents:
        raise ValueError(f"path escapes media root: {rel!r}")
    return p


def load_creative(media_root: Path, sha256: str) -> BannerCreative | None:
    """Прочитать `creative.json` уже принятого креатива (или None)."""
    meta = creative_dir(media_root, sha256) / META_NAME
    if not meta.is_file():
        return None
    try:
        return BannerCreative.model_validate_json(meta.read_text(encoding="utf-8"))
    except ValueError:
        return None


def sha256_of(path: Path, chunk: int = MiB) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while data := fh.read(chunk):
            h.update(data)
    return h.hexdigest()


# ---------------------------------------------------------------- проверки


def check_limits(sniff: SniffResult, size_bytes: int, limits: AssetLimits = DEFAULT_LIMITS) -> None:
    """Лимиты по заголовку файла, до декодирования. Нарушение → BannerAssetError."""
    if size_bytes <= 0:
        raise BannerAssetError("empty_file", "Файл пустой")
    if not sniff.supported:
        raise BannerAssetError(
            "unsupported_format",
            "Формат не поддерживается: нужен PNG, JPG, WebP, GIF, APNG, WebM или MP4",
            detected=sniff.detail,
        )
    limit = limits.max_animated_bytes if sniff.animated else limits.max_static_bytes
    if size_bytes > limit:
        raise BannerAssetError(
            "file_too_large",
            f"Файл {size_bytes / MiB:.1f} МБ, лимит {limit / MiB:.0f} МБ",
            size_bytes=size_bytes,
            limit_bytes=limit,
        )
    if sniff.truncated:
        raise BannerAssetError("corrupt_file", "Файл обрезан или повреждён", format=sniff.source_format)
    if sniff.width and sniff.height:
        _check_dims(sniff.width, sniff.height, limits)
    if sniff.animated and sniff.duration_sec is not None:
        _check_duration(sniff.duration_sec, limits)
    if sniff.animated and sniff.frame_count and sniff.frame_count > limits.max_frames:
        raise BannerAssetError(
            "too_many_frames",
            f"Кадров {sniff.frame_count}, лимит {limits.max_frames}",
            frames=sniff.frame_count,
        )


def _check_dims(w: int, h: int, limits: AssetLimits) -> None:
    if max(w, h) > limits.max_side_px:
        raise BannerAssetError(
            "dimensions_too_large",
            f"Размер {w}×{h}, сторона не больше {limits.max_side_px} px",
            width=w,
            height=h,
        )
    if min(w, h) < limits.min_side_px:
        raise BannerAssetError(
            "dimensions_too_small",
            f"Размер {w}×{h}, сторона не меньше {limits.min_side_px} px",
            width=w,
            height=h,
        )


def _check_duration(duration: float, limits: AssetLimits) -> None:
    if duration > limits.max_animated_sec + 0.05:
        raise BannerAssetError(
            "animated_too_long",
            f"Анимация {duration:.1f} с, лимит {limits.max_animated_sec:.0f} с",
            duration_sec=round(duration, 3),
        )


def _fit_within(w: int, h: int, max_w: int, max_h: int, *, even: bool) -> tuple[int, int]:
    s = min(1.0, max_w / w, max_h / h)
    nw, nh = max(1, round(w * s)), max(1, round(h * s))
    if even:
        nw, nh = max(2, nw - nw % 2), max(2, nh - nh % 2)
    return nw, nh


# ---------------------------------------------------------------- статика (Pillow)


_SRGB = None


def _srgb_profile() -> ImageCms.ImageCmsProfile:
    global _SRGB
    if _SRGB is None:
        _SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    return _SRGB


def _to_srgb_rgba(im: Image.Image) -> Image.Image:
    """Любой режим → RGBA в sRGB. ICC-профиль применяется, затем выбрасывается."""
    icc = im.info.get("icc_profile")
    if im.mode in ("P", "PA") or "transparency" in im.info:
        im = im.convert("RGBA")
    elif im.mode in ("I;16", "I;16B", "I;16L", "I"):
        im = im.convert("I").point(lambda v: v * (1 / 256)).convert("L")
    alpha: Image.Image | None = None
    if im.mode in ("RGBA", "LA", "RGBa", "La"):
        if im.mode in ("RGBa", "La"):
            im = im.convert("RGBA" if im.mode == "RGBa" else "LA")
        alpha = im.getchannel("A")
        base = im.convert("RGB") if im.mode == "RGBA" else im.convert("L")
    else:
        base = im
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            out_mode = "RGB"
            if base.mode not in ("RGB", "CMYK", "L"):
                base = base.convert("RGB")
            base = ImageCms.profileToProfile(base, src, _srgb_profile(), outputMode=out_mode)
        except (ImageCms.PyCMSError, OSError, ValueError):
            base = base.convert("RGB")
    rgb = base.convert("RGB")
    rgba = rgb.convert("RGBA")
    if alpha is not None:
        rgba.putalpha(alpha)
    rgba.info = {}
    return rgba


def _premult_resize(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Ресайз RGBA через премультиплицированную альфу: без тёмной каймы по краям прозрачности."""
    if im.size == size:
        return im.copy()
    return im.convert("RGBa").resize(size, Image.Resampling.LANCZOS).convert("RGBA")


def _save_png(im: Image.Image, path: Path) -> None:
    im.info = {}
    im.save(path, format="PNG", compress_level=6)


def _thumb_from(im: Image.Image, px: int) -> Image.Image:
    w, h = im.size
    tw, th = _fit_within(w, h, px, px, even=False)
    return _premult_resize(im, (tw, th))


def _ingest_static(
    src: Path, sniff: SniffResult, workdir: Path, limits: AssetLimits
) -> tuple[dict[str, object], list[str]]:
    try:
        with Image.open(src) as im:
            _check_dims(im.width, im.height, limits)
            im.load()
            if sniff.source_format == CreativeSourceFormat.GIF or getattr(im, "is_animated", False):
                im.seek(0)
            oriented = ImageOps.exif_transpose(im)
            if oriented is None:
                oriented = im
            rgba = _to_srgb_rgba(oriented)
    except BannerAssetError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise BannerAssetError("corrupt_file", "Не удалось декодировать изображение", error=str(exc)) from exc
    alpha_min = rgba.getchannel("A").getextrema()[0]
    has_alpha = alpha_min < 255
    _save_png(rgba, workdir / "creative.png")
    _save_png(_thumb_from(rgba, limits.thumb_px), workdir / "thumb.png")
    meta: dict[str, object] = {
        "kind": CreativeKind.STATIC,
        "width": rgba.width,
        "height": rgba.height,
        "source_width": rgba.width,
        "source_height": rgba.height,
        "has_alpha": has_alpha,
        "duration_sec": 0.0,
        "fps": 0.0,
        "frame_count": 1,
        "loop": False,
        "normalized_name": "creative.png",
        "normalized_mime": "image/png",
        "first_frame_name": "creative.png",
    }
    return meta, []


# ---------------------------------------------------------------- анимация (ffmpeg)


def _vp9_args(limits: AssetLimits, alpha: bool, *, rgb_source: bool) -> list[str]:
    args = [
        "-c:v",
        "libvpx-vp9",
        "-pix_fmt",
        "yuva420p" if alpha else "yuv420p",
        "-b:v",
        "0",
        "-crf",
        str(limits.vp9_crf),
        "-deadline",
        "good",
        "-cpu-used",
        str(limits.vp9_cpu_used),
        "-row-mt",
        "1",
        "-g",
        str(limits.target_fps * 2),
    ]
    if alpha:
        args += ["-auto-alt-ref", "0"]
    if rgb_source:
        args += [
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_range",
            "tv",
        ]
    return args + ["-f", "webm"]


def _scale_chain(w: int, h: int, alpha: bool, *, rgb_source: bool, fps: int) -> str:
    matrix = ":out_color_matrix=bt709:out_range=tv" if rgb_source else ""
    fmt = "yuva420p" if alpha else "yuv420p"
    return f"fps={fps},scale={w}:{h}:flags=lanczos{matrix},format={fmt},setsar=1"


def _decoder_for(codec: str, alpha: bool) -> list[str]:
    if alpha and codec == "vp9":
        return ["-c:v", "libvpx-vp9"]
    if alpha and codec == "vp8":
        return ["-c:v", "libvpx"]
    return []


def _extract_png(
    webm: Path, out: Path, *, t_sec: float, alpha: bool, fit_px: int | None, limits: AssetLimits
) -> None:
    cmd = ["ffmpeg", "-y", "-ss", f"{max(0.0, t_sec):.3f}"]
    if alpha:
        cmd += ["-c:v", "libvpx-vp9"]
    cmd += ["-i", str(webm), "-frames:v", "1", "-an"]
    vf = []
    if fit_px:
        vf.append(
            f"scale='min({fit_px},iw)':'min({fit_px},ih)':force_original_aspect_ratio=decrease:flags=lanczos"
        )
    vf.append("format=rgba")
    cmd += ["-vf", ",".join(vf), "-f", "image2", "-c:v", "png", str(out)]
    run_media_cmd(cmd, timeout_sec=60)


def _ingest_ffmpeg_animated(
    src: Path, sniff: SniffResult, workdir: Path, limits: AssetLimits
) -> tuple[dict[str, object], list[str]]:
    notes: list[str] = []
    fmt = sniff.source_format
    demuxer = {
        CreativeSourceFormat.GIF: "gif",
        CreativeSourceFormat.APNG: "apng",
        CreativeSourceFormat.WEBM: "matroska",
        CreativeSourceFormat.MP4: "mov",
        CreativeSourceFormat.MOV: "mov",
    }[fmt]  # type: ignore[index]
    try:
        info = probe_media(src)
    except ProbeError as exc:
        raise BannerAssetError(
            "corrupt_file", "ffprobe не смог прочитать файл", error=exc.stderr_tail
        ) from exc
    if info.video is None:
        raise BannerAssetError("corrupt_file", "В файле нет видеодорожки")
    src_w, src_h = info.video.display_size
    if not src_w or not src_h:
        raise BannerAssetError("corrupt_file", "Не удалось определить размер кадра")
    _check_dims(src_w, src_h, limits)
    rgb_source = fmt in (CreativeSourceFormat.GIF, CreativeSourceFormat.APNG)
    if rgb_source:
        duration = float(sniff.duration_sec or info.duration_sec or 0.0)
        alpha = bool(sniff.has_alpha)
        loop = bool(sniff.loop)
    else:
        duration = float(info.duration_sec or 0.0)
        alpha = info.video.has_alpha and info.video.codec in ("vp8", "vp9")
        if info.video.has_alpha and not alpha:
            notes.append(f"alpha_dropped_codec_{info.video.codec}")
        loop = True
    if duration <= 0:
        raise BannerAssetError("corrupt_file", "Не удалось определить длительность анимации")
    _check_duration(duration, limits)
    out_w, out_h = _fit_within(src_w, src_h, limits.max_normalized_w, limits.max_normalized_h, even=True)
    out = workdir / "creative.webm"
    cmd = [
        "ffmpeg",
        "-y",
        "-protocol_whitelist",
        "file",
        "-f",
        demuxer,
        *_decoder_for(info.video.codec, alpha),
        "-i",
        str(src),
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        "-vf",
        _scale_chain(out_w, out_h, alpha, rgb_source=rgb_source, fps=limits.target_fps),
        "-t",
        f"{min(duration, limits.max_animated_sec) + 0.05:.3f}",
        *_vp9_args(limits, alpha, rgb_source=rgb_source),
        str(out),
    ]
    try:
        run_media_cmd(cmd, timeout_sec=limits.timeout_sec)
    except MediaCommandError as exc:
        raise BannerAssetError(
            "corrupt_file", "ffmpeg не смог декодировать анимацию", error=exc.stderr_tail[-800:]
        ) from exc
    return _finish_animated(out, workdir, limits, alpha, loop, src_w, src_h, notes)


def _ingest_webp_animated(
    src: Path, sniff: SniffResult, workdir: Path, limits: AssetLimits
) -> tuple[dict[str, object], list[str]]:
    """Анимированный WebP: ffmpeg 6.1 его не декодирует, кадры идут из Pillow сырыми в ffmpeg."""
    fps = limits.target_fps
    try:
        im = Image.open(src)
    except (UnidentifiedImageError, OSError) as exc:
        raise BannerAssetError("corrupt_file", "Не удалось открыть WebP", error=str(exc)) from exc
    with im:
        _check_dims(im.width, im.height, limits)
        n = getattr(im, "n_frames", 1)
        if n > limits.max_frames:
            raise BannerAssetError("too_many_frames", f"Кадров {n}, лимит {limits.max_frames}", frames=n)
        out_w, out_h = _fit_within(
            im.width, im.height, limits.max_normalized_w, limits.max_normalized_h, even=True
        )
        alpha = bool(sniff.has_alpha)
        out = workdir / "creative.webm"
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            "-s",
            f"{out_w}x{out_h}",
            "-framerate",
            str(fps),
            "-i",
            "pipe:0",
            "-vf",
            f"scale=out_color_matrix=bt709:out_range=tv,format={'yuva420p' if alpha else 'yuv420p'},setsar=1",
            *_vp9_args(limits, alpha, rgb_source=True),
            str(out),
        ]
        proc = open_media_process(cmd)
        written = 0
        t_ms = 0.0
        raw = b""
        try:
            assert proc.stdin is not None
            for i in range(n):
                im.seek(i)
                im.load()  # WebP-плагин Pillow заполняет info["duration"] только при загрузке кадра
                dur = float(im.info.get("duration") or 0)
                t_ms += dur if dur > 10 else 100.0
                if t_ms / 1000.0 > limits.max_animated_sec + 0.05:
                    raise BannerAssetError(
                        "animated_too_long",
                        f"Анимация длиннее {limits.max_animated_sec:.0f} с",
                        duration_sec=round(t_ms / 1000.0, 3),
                    )
                raw = _premult_resize(im.convert("RGBA"), (out_w, out_h)).tobytes()
                target = round(t_ms / 1000.0 * fps)  # постоянные 30 fps: кадр повторяется по задержке
                for _ in range(target - written):
                    proc.stdin.write(raw)
                written = max(written, target)
            if written == 0 and raw:
                proc.stdin.write(raw)
            _, err = proc.communicate(timeout=limits.timeout_sec)
        except BrokenPipeError as exc:
            kill_media_process(proc)
            raise BannerAssetError("corrupt_file", "ffmpeg прервал кодирование WebP") from exc
        except BaseException:
            kill_media_process(proc)
            raise
        if proc.returncode != 0:
            raise BannerAssetError(
                "corrupt_file",
                "ffmpeg не смог закодировать WebP",
                error=(err or b"").decode("utf-8", "replace"),
            )
        return _finish_animated(out, workdir, limits, alpha, bool(sniff.loop), im.width, im.height, [])


def _finish_animated(
    out: Path,
    workdir: Path,
    limits: AssetLimits,
    alpha: bool,
    loop: bool,
    src_w: int,
    src_h: int,
    notes: list[str],
) -> tuple[dict[str, object], list[str]]:
    try:
        info = probe_media(out, count_frames=True)
    except ProbeError as exc:
        raise BannerAssetError(
            "corrupt_file", "Нормализованный файл не читается", error=exc.stderr_tail
        ) from exc
    if info.video is None or not info.duration_sec:
        raise BannerAssetError("corrupt_file", "Анимация не содержит кадров")
    frames = int(info.video.nb_frames or round(info.duration_sec * limits.target_fps))
    if frames < 1:
        raise BannerAssetError("corrupt_file", "Анимация не содержит кадров")
    _extract_png(out, workdir / "first_frame.png", t_sec=0.0, alpha=alpha, fit_px=None, limits=limits)
    _extract_png(
        out,
        workdir / "thumb.png",
        t_sec=min(info.duration_sec / 2, max(0.0, info.duration_sec - 1.0 / limits.target_fps)),
        alpha=alpha,
        fit_px=limits.thumb_px,
        limits=limits,
    )
    meta: dict[str, object] = {
        "kind": CreativeKind.ANIMATED,
        "width": info.video.width,
        "height": info.video.height,
        "source_width": src_w,
        "source_height": src_h,
        "has_alpha": alpha,
        "duration_sec": round(float(info.duration_sec), 3),
        "fps": float(limits.target_fps),
        "frame_count": frames,
        "loop": loop,
        "normalized_name": "creative.webm",
        "normalized_mime": "video/webm",
        "first_frame_name": "first_frame.png",
    }
    return meta, notes


# ---------------------------------------------------------------- точка входа


def ingest_creative(
    upload_path: Path,
    media_root: Path,
    *,
    limits: AssetLimits = DEFAULT_LIMITS,
    force: bool = False,
) -> BannerCreative:
    """Проверить, нормализовать и сохранить загруженный креатив. Ошибка → BannerAssetError.

    Идемпотентно: тот же файл (sha256 байтов) второй раз не перекодируется (`force=True` — заново).
    """
    upload_path = Path(upload_path)
    media_root = Path(media_root)
    if not upload_path.is_file():
        raise BannerAssetError("empty_file", "Файл не найден")
    size = upload_path.stat().st_size
    sniff = sniff_file(upload_path)
    check_limits(sniff, size, limits)
    sha = sha256_of(upload_path)
    final_dir = creative_dir(media_root, sha)
    if not force:
        existing = load_creative(media_root, sha)
        if existing is not None and existing.normalizer_version == NORMALIZER_VERSION:
            return existing
    parent = final_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix=f".tmp-{sha[:12]}-", dir=parent))
    try:
        fmt = sniff.source_format
        if not sniff.animated:
            meta, notes = _ingest_static(upload_path, sniff, workdir, limits)
        elif fmt == CreativeSourceFormat.WEBP_ANIMATED:
            meta, notes = _ingest_webp_animated(upload_path, sniff, workdir, limits)
        else:
            meta, notes = _ingest_ffmpeg_animated(upload_path, sniff, workdir, limits)
        rel_dir = final_dir.relative_to(media_root).as_posix()
        creative = BannerCreative(
            sha256=sha,
            kind=meta["kind"],  # type: ignore[arg-type]
            source_format=fmt,  # type: ignore[arg-type]
            width=int(meta["width"]),  # type: ignore[call-overload]
            height=int(meta["height"]),  # type: ignore[call-overload]
            source_width=int(meta["source_width"]),  # type: ignore[call-overload]
            source_height=int(meta["source_height"]),  # type: ignore[call-overload]
            has_alpha=bool(meta["has_alpha"]),
            duration_sec=float(meta["duration_sec"]),  # type: ignore[arg-type]
            fps=float(meta["fps"]),  # type: ignore[arg-type]
            frame_count=int(meta["frame_count"]),  # type: ignore[call-overload]
            loop=bool(meta["loop"]),
            source_bytes=size,
            normalized_path=f"{rel_dir}/{meta['normalized_name']}",
            normalized_mime=str(meta["normalized_mime"]),
            thumb_path=f"{rel_dir}/thumb.png",
            first_frame_path=f"{rel_dir}/{meta['first_frame_name']}",
            normalizer_version=NORMALIZER_VERSION,
        )
        payload = json.loads(creative.model_dump_json())
        if notes:
            payload_notes = {"notes": notes}
            (workdir / "notes.json").write_text(json.dumps(payload_notes), encoding="utf-8")
        (workdir / META_NAME).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        for p in workdir.iterdir():
            os.chmod(p, 0o640)
        if final_dir.exists():
            shutil.rmtree(final_dir)
        try:
            os.replace(workdir, final_dir)
            os.chmod(final_dir, 0o750)
        except OSError:
            # гонка двух одинаковых загрузок: победитель уже положил каталог
            existing = load_creative(media_root, sha)
            if existing is None:
                raise
            return existing
        return creative
    finally:
        if workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)


def remove_creative(media_root: Path, sha256: str) -> bool:
    """Удалить нормализованный креатив (когда на него больше нет ссылок в БД)."""
    d = creative_dir(media_root, sha256)
    if not d.exists():
        return False
    shutil.rmtree(d)
    with contextlib.suppress(OSError):
        d.parent.rmdir()
    return True
