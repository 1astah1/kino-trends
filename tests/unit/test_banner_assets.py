"""Приём загруженного креатива: сигнатуры, лимиты, нормализация, контентное хранение.

Картинки генерируются в tmp. Случаи, где нужен ffmpeg (анимация), помечены `media`.
"""

from __future__ import annotations

import io
import json
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest
from PIL import Image, ImageCms

from autoshorts.domain.banner_schema import CreativeKind, CreativeSourceFormat
from autoshorts.pipeline import banner_assets as ba
from autoshorts.pipeline.banner_validate import validate_upload
from autoshorts.pipeline.probe import parse_ffprobe_json

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")


# ---------------------------------------------------------------- генераторы


def png_with_alpha(path: Path, size: tuple[int, int] = (120, 60)) -> Path:
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    im.paste((255, 0, 128, 255), (10, 10, size[0] - 10, size[1] - 10))
    im.save(path, format="PNG")
    return path


def opaque_png(path: Path, size: tuple[int, int] = (120, 60)) -> Path:
    Image.new("RGBA", size, (10, 200, 30, 255)).save(path, format="PNG")
    return path


def jpg_rotated(path: Path) -> Path:
    """200×100: левая четверть красная. EXIF Orientation=6 → на экране 100×200, красное сверху."""
    im = Image.new("RGB", (200, 100), (0, 0, 255))
    im.paste((255, 0, 0), (0, 0, 50, 100))
    exif = Image.Exif()
    exif[0x0112] = 6
    exif[0x010F] = "SecretCam"  # Make — должно исчезнуть
    im.save(path, format="JPEG", exif=exif.tobytes(), quality=95)
    return path


def animated_gif(path: Path, *, frames: int = 4, ms: int = 250, loop: bool = True, size=(60, 30)) -> Path:
    ims = []
    for i in range(frames):
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        color = (255, 0, 0, 255) if i % 2 == 0 else (0, 255, 0, 255)
        im.paste(color, (0, 0, size[0] // 2, size[1]))
        ims.append(im)
    kw = {"loop": 0} if loop else {}
    ims[0].save(path, format="GIF", save_all=True, append_images=ims[1:], duration=ms, disposal=2, **kw)
    return path


def ffmpeg(*args: str) -> None:
    subprocess.run(
        ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args],
        check=True,
        timeout=120,
    )


@pytest.fixture
def media_root(tmp_path: Path) -> Path:
    root = tmp_path / "media"
    root.mkdir()
    return root


# ---------------------------------------------------------------- сигнатуры


def test_sniff_png_alpha_and_jpeg(tmp_path: Path) -> None:
    s = ba.sniff_file(png_with_alpha(tmp_path / "a.png"))
    assert s.source_format == CreativeSourceFormat.PNG
    assert not s.animated and s.has_alpha and (s.width, s.height) == (120, 60)
    j = ba.sniff_file(jpg_rotated(tmp_path / "r.jpg"))
    assert j.source_format == CreativeSourceFormat.JPEG and not j.animated


def test_sniff_gif_frames_duration_loop(tmp_path: Path) -> None:
    s = ba.sniff_file(animated_gif(tmp_path / "a.gif", frames=4, ms=250))
    assert s.source_format == CreativeSourceFormat.GIF
    assert s.animated and s.frame_count == 4
    assert s.duration_sec == pytest.approx(1.0)
    assert s.loop is True and not s.truncated
    once = ba.sniff_file(animated_gif(tmp_path / "b.gif", loop=False))
    assert once.loop is False


def test_sniff_gif_zero_delay_uses_browser_default(tmp_path: Path) -> None:
    s = ba.sniff_file(animated_gif(tmp_path / "z.gif", frames=3, ms=0))
    assert s.duration_sec == pytest.approx(0.3)  # 3 × 100 мс, как ffmpeg и браузеры


def test_sniff_apng_and_webp(tmp_path: Path) -> None:
    frames = [Image.new("RGBA", (40, 20), (255, 0, 0, 128)), Image.new("RGBA", (40, 20), (0, 255, 0, 255))]
    frames[0].save(tmp_path / "a.png", save_all=True, append_images=frames[1:], duration=250, loop=0)
    frames[0].save(tmp_path / "a.webp", save_all=True, append_images=frames[1:], duration=250, loop=0)
    frames[0].save(tmp_path / "s.webp")
    apng = ba.sniff_file(tmp_path / "a.png")
    assert apng.source_format == CreativeSourceFormat.APNG and apng.frame_count == 2
    assert apng.duration_sec == pytest.approx(0.5) and apng.has_alpha
    webp = ba.sniff_file(tmp_path / "a.webp")
    assert webp.source_format == CreativeSourceFormat.WEBP_ANIMATED and webp.duration_sec == pytest.approx(
        0.5
    )
    static = ba.sniff_file(tmp_path / "s.webp")
    assert static.source_format == CreativeSourceFormat.WEBP and not static.animated and static.has_alpha


def test_sniff_ignores_fake_extension(tmp_path: Path) -> None:
    gif_as_png = tmp_path / "banner.png"
    animated_gif(tmp_path / "x.gif").rename(gif_as_png)
    assert ba.sniff_file(gif_as_png).source_format == CreativeSourceFormat.GIF
    txt = tmp_path / "evil.png"
    txt.write_text("definitely not an image", encoding="utf-8")
    s = ba.sniff_file(txt)
    assert s.source_format is None and not s.supported
    svg = tmp_path / "logo.png"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    assert ba.sniff_file(svg).detail == "svg"


@pytest.mark.parametrize(
    ("brand", "expected", "detail"),
    [
        (b"isom", CreativeSourceFormat.MP4, ""),
        (b"mp42", CreativeSourceFormat.MP4, ""),
        (b"qt  ", CreativeSourceFormat.MOV, ""),
        (b"heic", None, "heic"),
        (b"avif", None, "avif"),
    ],
)
def test_sniff_iso_bmff_brands(tmp_path: Path, brand: bytes, expected, detail: str) -> None:
    p = tmp_path / "f.bin"
    p.write_bytes(b"\x00\x00\x00\x18ftyp" + brand + b"\x00\x00\x00\x00" + b"isom" * 2 + b"\x00" * 64)
    s = ba.sniff_file(p)
    assert s.source_format == expected
    if detail:
        assert s.detail == detail


def test_sniff_webm_ebml(tmp_path: Path) -> None:
    p = tmp_path / "x.dat"
    p.write_bytes(b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\x82\x84webm" + b"\x00" * 64)
    s = ba.sniff_file(p)
    assert s.source_format == CreativeSourceFormat.WEBM and s.animated


# ---------------------------------------------------------------- лимиты и битые файлы


def test_limits_file_too_large(tmp_path: Path, media_root: Path) -> None:
    p = png_with_alpha(tmp_path / "a.png")
    with pytest.raises(ba.BannerAssetError) as ei:
        ba.ingest_creative(p, media_root, limits=ba.AssetLimits(max_static_bytes=50))
    assert ei.value.code == "file_too_large"


def test_limits_dimensions(tmp_path: Path, media_root: Path) -> None:
    big = png_with_alpha(tmp_path / "big.png", size=(300, 40))
    with pytest.raises(ba.BannerAssetError) as ei:
        ba.ingest_creative(big, media_root, limits=ba.AssetLimits(max_side_px=256))
    assert ei.value.code == "dimensions_too_large"
    tiny = opaque_png(tmp_path / "tiny.png", size=(8, 8))
    with pytest.raises(ba.BannerAssetError) as ei2:
        ba.ingest_creative(tiny, media_root)
    assert ei2.value.code == "dimensions_too_small"


def test_limits_animated_too_long_before_decoding(tmp_path: Path, media_root: Path) -> None:
    p = animated_gif(tmp_path / "long.gif", frames=2, ms=9000)  # 18 с
    with pytest.raises(ba.BannerAssetError) as ei:
        ba.ingest_creative(p, media_root)
    assert ei.value.code == "animated_too_long"
    assert not any(media_root.rglob("*.webm"))


def test_unsupported_and_empty(tmp_path: Path, media_root: Path) -> None:
    bmp = tmp_path / "x.png"
    Image.new("RGB", (40, 40)).save(bmp, format="BMP")
    with pytest.raises(ba.BannerAssetError) as ei:
        ba.ingest_creative(bmp, media_root)
    assert ei.value.code == "unsupported_format"
    assert ei.value.details["detected"] == "bmp"
    empty = tmp_path / "e.png"
    empty.write_bytes(b"")
    with pytest.raises(ba.BannerAssetError) as ei2:
        ba.ingest_creative(empty, media_root)
    assert ei2.value.code == "empty_file"


def test_corrupt_png_and_jpeg(tmp_path: Path, media_root: Path) -> None:
    good = png_with_alpha(tmp_path / "g.png", size=(200, 100)).read_bytes()
    cut = tmp_path / "cut.png"
    cut.write_bytes(good[: len(good) // 2])
    with pytest.raises(ba.BannerAssetError) as ei:
        ba.ingest_creative(cut, media_root)
    assert ei.value.code == "corrupt_file"
    jpg = jpg_rotated(tmp_path / "j.jpg").read_bytes()
    cutj = tmp_path / "cut.jpg"
    cutj.write_bytes(jpg[: len(jpg) // 2])
    with pytest.raises(ba.BannerAssetError) as ej:
        ba.ingest_creative(cutj, media_root)
    assert ej.value.code == "corrupt_file"
    assert not list((media_root / "banners" / "creatives").rglob("creative.json"))


def _png_chunk(ctype: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", zlib.crc32(ctype + data))


_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_IHDR_OK = _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 10, 10, 8, 6, 0, 0, 0))
_IEND = _png_chunk(b"IEND", b"")


@pytest.mark.parametrize(
    "name, data",
    [
        ("short_ihdr.png", _PNG_SIG + _png_chunk(b"IHDR", b"\0" * 8) + _IEND),
        ("short_actl.png", _PNG_SIG + _IHDR_OK + _png_chunk(b"acTL", b"\0\0") + _IEND),
        (
            "short_fctl.png",
            _PNG_SIG
            + _IHDR_OK
            + _png_chunk(b"acTL", struct.pack(">II", 2, 0))
            + _png_chunk(b"fcTL", b"\0" * 10)
            + _IEND,
        ),
        ("short_gif_header.gif", b"GIF89a\x01\x00"),
        ("short_webp.webp", b"RIFF\x20\x00\x00\x00WEBPVP8X\x0a\x00\x00\x00\x10"),
    ],
)
def test_malformed_headers_are_corrupt_not_crash(
    tmp_path: Path, media_root: Path, name: str, data: bytes
) -> None:
    """Короткие служебные чанки и обрезанные заголовки → corrupt_file (422), а не IndexError/struct.error."""
    path = tmp_path / name
    path.write_bytes(data)
    sniff = ba.sniff_file(path)
    assert sniff.supported and sniff.truncated
    with pytest.raises(ba.BannerAssetError) as ei:
        ba.check_limits(sniff, path.stat().st_size)
    assert ei.value.code == "corrupt_file"
    with pytest.raises(ba.BannerAssetError) as ei2:
        ba.ingest_creative(path, media_root)
    assert ei2.value.code == "corrupt_file"
    warnings = validate_upload(path)
    assert len(warnings) == 1 and warnings[0].details["asset_error"] == "corrupt_file"


# ---------------------------------------------------------------- статика


def test_ingest_png_alpha_normalized_and_content_addressed(tmp_path: Path, media_root: Path) -> None:
    src = png_with_alpha(tmp_path / "upload.bin")
    c = ba.ingest_creative(src, media_root)
    assert c.kind == CreativeKind.STATIC and c.source_format == CreativeSourceFormat.PNG
    assert (c.width, c.height) == (120, 60) and c.has_alpha
    assert c.sha256 == ba.sha256_of(src)
    assert c.normalized_path == f"banners/creatives/{c.sha256[:2]}/{c.sha256}/creative.png"
    out = ba.creative_file(media_root, c)
    with Image.open(out) as im:
        assert im.mode == "RGBA" and im.getpixel((0, 0))[3] == 0 and im.getpixel((60, 30))[3] == 255
    with Image.open(ba.creative_thumb(media_root, c)) as th:
        assert max(th.size) <= 320
    meta = json.loads((out.parent / "creative.json").read_text(encoding="utf-8"))
    assert meta["sha256"] == c.sha256
    # повторная загрузка — без перекодирования
    mtime = out.stat().st_mtime_ns
    again = ba.ingest_creative(src, media_root)
    assert again == c and out.stat().st_mtime_ns == mtime
    assert ba.load_creative(media_root, c.sha256) == c
    assert not [p for p in out.parent.parent.iterdir() if p.name.startswith(".tmp-")]


def test_opaque_png_reports_no_alpha(tmp_path: Path, media_root: Path) -> None:
    c = ba.ingest_creative(opaque_png(tmp_path / "o.png"), media_root)
    assert c.has_alpha is False


def test_ingest_jpeg_applies_exif_orientation_and_strips_metadata(tmp_path: Path, media_root: Path) -> None:
    c = ba.ingest_creative(jpg_rotated(tmp_path / "r.jpg"), media_root)
    assert (c.width, c.height) == (100, 200)
    with Image.open(ba.creative_file(media_root, c)) as im:
        assert im.format == "PNG" and im.mode == "RGBA"
        r, g, b, a = im.getpixel((50, 20))  # красная полоса после поворота — сверху
        assert r > 200 and b < 60 and a == 255
        assert "exif" not in im.info and "icc_profile" not in im.info
        assert b"SecretCam" not in ba.creative_file(media_root, c).read_bytes()


def test_ingest_png_with_icc_profile_is_converted_and_stripped(tmp_path: Path, media_root: Path) -> None:
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    buf = io.BytesIO()
    Image.new("RGBA", (64, 32), (200, 100, 50, 255)).save(buf, format="PNG", icc_profile=srgb.tobytes())
    p = tmp_path / "icc.png"
    p.write_bytes(buf.getvalue())
    c = ba.ingest_creative(p, media_root)
    with Image.open(ba.creative_file(media_root, c)) as im:
        assert "icc_profile" not in im.info
        r, g, b, _ = im.getpixel((10, 10))
        assert abs(r - 200) <= 3 and abs(g - 100) <= 3 and abs(b - 50) <= 3


def test_single_frame_gif_is_static(tmp_path: Path, media_root: Path) -> None:
    p = tmp_path / "one.gif"
    im = Image.new("P", (50, 20), 0)
    im.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
    im.paste(1, (0, 0, 25, 20))
    im.save(p, format="GIF", transparency=0)
    c = ba.ingest_creative(p, media_root)
    assert c.kind == CreativeKind.STATIC and c.has_alpha and c.normalized_mime == "image/png"


def test_remove_creative(tmp_path: Path, media_root: Path) -> None:
    c = ba.ingest_creative(png_with_alpha(tmp_path / "a.png"), media_root)
    assert ba.remove_creative(media_root, c.sha256)
    assert ba.load_creative(media_root, c.sha256) is None
    assert not ba.remove_creative(media_root, c.sha256)


def test_parse_ffprobe_json_alpha_and_rotation() -> None:
    data = {
        "format": {"format_name": "matroska,webm", "size": "1234"},
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "vp9",
                "width": 320,
                "height": 180,
                "r_frame_rate": "30/1",
                "avg_frame_rate": "30/1",
                "pix_fmt": "yuv420p",
                "tags": {"alpha_mode": "1", "DURATION": "00:00:02.500000000"},
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "opus",
                "sample_rate": "48000",
                "channels": 2,
            },
        ],
    }
    info = parse_ffprobe_json(data)
    assert info.has_alpha and info.fps == 30.0 and info.duration_sec == pytest.approx(2.5)
    assert info.audio is not None and info.audio.sample_rate == 48000
    rot = parse_ffprobe_json(
        {
            "format": {"duration": "1.0"},
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "pix_fmt": "yuv420p",
                    "side_data_list": [{"rotation": -90}],
                }
            ],
        }
    )
    assert rot.video is not None and rot.video.display_size == (1080, 1920) and not rot.has_alpha


# ---------------------------------------------------------------- анимация (ffmpeg)


@pytest.mark.media
@needs_ffmpeg
def test_ingest_animated_gif_to_vp9_alpha(tmp_path: Path, media_root: Path) -> None:
    c = ba.ingest_creative(animated_gif(tmp_path / "a.gif", frames=4, ms=250), media_root)
    assert c.kind == CreativeKind.ANIMATED and c.source_format == CreativeSourceFormat.GIF
    assert c.normalized_mime == "video/webm" and c.normalized_path.endswith("creative.webm")
    assert c.has_alpha and c.loop and c.fps == 30.0
    assert c.duration_sec == pytest.approx(1.0, abs=0.05)
    assert c.frame_count == pytest.approx(30, abs=1)
    assert (c.width, c.height) == (60, 30)
    first = ba.creative_first_frame(media_root, c)
    with Image.open(first) as im:
        assert im.mode == "RGBA"
        assert im.getpixel((5, 15))[0] > 200 and im.getpixel((5, 15))[3] > 200  # красная половина
        assert im.getpixel((50, 15))[3] < 30  # прозрачная половина
    assert ba.creative_thumb(media_root, c).is_file()


@pytest.mark.media
@needs_ffmpeg
def test_ingest_gif_with_fake_png_extension(tmp_path: Path, media_root: Path) -> None:
    src = tmp_path / "banner.png"
    animated_gif(tmp_path / "a.gif").rename(src)
    c = ba.ingest_creative(src, media_root)
    assert c.kind == CreativeKind.ANIMATED and c.source_format == CreativeSourceFormat.GIF


@pytest.mark.media
@needs_ffmpeg
def test_ingest_apng_and_animated_webp(tmp_path: Path, media_root: Path) -> None:
    frames = [Image.new("RGBA", (40, 20), (255, 0, 0, 128)), Image.new("RGBA", (40, 20), (0, 255, 0, 255))]
    frames[0].save(
        tmp_path / "a.apng", format="PNG", save_all=True, append_images=frames[1:], duration=250, loop=0
    )
    frames[0].save(tmp_path / "a.webp", save_all=True, append_images=frames[1:], duration=250, loop=0)
    apng = ba.ingest_creative(tmp_path / "a.apng", media_root)
    webp = ba.ingest_creative(tmp_path / "a.webp", media_root)
    for c in (apng, webp):
        assert c.kind == CreativeKind.ANIMATED and c.has_alpha
        assert c.duration_sec == pytest.approx(0.5, abs=0.05)
    assert webp.source_format == CreativeSourceFormat.WEBP_ANIMATED


@pytest.mark.media
@needs_ffmpeg
def test_ingest_mp4_stays_opaque_and_webm_keeps_alpha(tmp_path: Path, media_root: Path) -> None:
    mp4 = tmp_path / "clip.mp4"
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=96x48:r=25:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(mp4)
    )
    webm = tmp_path / "alpha.webm"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=red@0.5:s=64x32:r=30:d=1,format=yuva420p",
        "-c:v",
        "libvpx-vp9",
        "-pix_fmt",
        "yuva420p",
        "-auto-alt-ref",
        "0",
        str(webm),
    )
    a = ba.ingest_creative(mp4, media_root)
    assert a.kind == CreativeKind.ANIMATED and a.source_format == CreativeSourceFormat.MP4
    assert a.has_alpha is False and a.fps == 30.0 and a.duration_sec == pytest.approx(1.0, abs=0.1)
    b = ba.ingest_creative(webm, media_root)
    assert b.source_format == CreativeSourceFormat.WEBM and b.has_alpha is True
    with Image.open(ba.creative_first_frame(media_root, b)) as im:
        assert 100 <= im.getpixel((10, 10))[3] <= 156  # альфа 0.5 пережила нормализацию


@pytest.mark.media
@needs_ffmpeg
def test_ingest_mp4_too_long(tmp_path: Path, media_root: Path) -> None:
    mp4 = tmp_path / "long.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=32x32:r=10:d=16",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(mp4),
    )
    with pytest.raises(ba.BannerAssetError) as ei:
        ba.ingest_creative(mp4, media_root)
    assert ei.value.code == "animated_too_long"


@pytest.mark.media
@needs_ffmpeg
def test_ingest_corrupt_mp4(tmp_path: Path, media_root: Path) -> None:
    p = tmp_path / "bad.mp4"
    p.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isomiso2" + b"\x00garbage" * 50)
    with pytest.raises(ba.BannerAssetError) as ei:
        ba.ingest_creative(p, media_root)
    assert ei.value.code == "corrupt_file"
