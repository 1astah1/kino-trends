"""Один проход рендера с загруженным креативом (PNG и анимированный GIF) — на настоящем ffmpeg.

Маленькие входы lavfi 320×568, 4–6 с. Проверяется по пикселям кадра:
* в окне импульса пиксели креатива есть, на 1-й секунде — нет;
* субтитры стоят в своей зоне и не заходят в прямоугольник баннера.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageStat

from autoshorts.domain.banner_schema import Rect, default_banner_variant
from autoshorts.models import WordTiming
from autoshorts.pipeline import banner_assets as ba
from autoshorts.pipeline import banner_overlay as bo
from autoshorts.pipeline import banner_place as bp
from autoshorts.pipeline.probe import extract_frame_rgba, probe_media
from autoshorts.pipeline.subtitles import build_karaoke_ass

pytestmark = [
    pytest.mark.media,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
]

W, H = 320, 568
BASE_RGB = (32, 48, 64)


def _base_video(path: Path, duration: float) -> Path:
    subprocess.run(
        [
            "nice",
            "-n",
            "10",
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x203040:s={W}x{H}:r=30:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=f=330:d={duration}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-threads",
            "2",
            str(path),
        ],
        check=True,
        timeout=120,
    )
    return path


def _png_creative(path: Path) -> Path:
    im = Image.new("RGBA", (900, 220), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, 899, 219), radius=40, fill=(255, 0, 255, 255))
    im.save(path)
    return path


def _gif_creative(path: Path) -> Path:
    frames = []
    for i in range(4):
        im = Image.new("RGBA", (300, 80), (0, 0, 0, 0))
        im.paste((255, 0, 0, 255) if i % 2 == 0 else (0, 255, 0, 255), (0, 0, 300, 80))
        frames.append(im)
    frames[0].save(path, format="GIF", save_all=True, append_images=frames[1:], duration=250, loop=0)
    return path


def _frame(video: Path, t: float) -> Image.Image:
    w, h, raw = extract_frame_rgba(video, t)
    return Image.frombytes("RGBA", (w, h), raw).convert("RGB")


def _region_mean(im: Image.Image, r: Rect, inset: int = 3) -> tuple[float, float, float]:
    box = (r.x + inset, r.y + inset, r.x2 - inset, r.y2 - inset)
    return tuple(ImageStat.Stat(im.crop(box)).mean)  # type: ignore[return-value]


def _near(a, b, tol: float = 22.0) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def _bright_pixels(im: Image.Image, r: Rect) -> int:
    crop = im.crop((r.x, r.y, r.x2, r.y2)).convert("L")
    return sum(1 for v in crop.get_flattened_data() if v > 150)


def _render(tmp_path: Path, upload: Path, duration: float, name: str):
    media = tmp_path / "media"
    creative = ba.ingest_creative(upload, media)
    base = _base_video(tmp_path / f"base_{name}.mp4", duration)
    words = [
        WordTiming("Подпишись", 0.0, duration / 2),
        WordTiming("сейчас", duration / 2, duration),
    ]
    ass = build_karaoke_ass(words, tmp_path / f"subs_{name}.ass", font_size=96)
    profile = bp.load_safe_zone_catalog().get()
    variant = default_banner_variant(creative_sha256=creative.sha256, erid="2VtzqvTest")
    out = tmp_path / f"out_{name}.mp4"
    res = bo.render_with_banner(
        base,
        out,
        variant=variant,
        creatives={creative.sha256: creative},
        media_root=media,
        profile=profile,
        workdir=tmp_path / f"work_{name}",
        ass_path=ass,
        code="k7m2q",
        timeout_sec=300,
    )
    return creative, res


def _check_captions_do_not_overlap(res: bo.BannerRenderResult, frame_t1: Image.Image) -> None:
    plan = res.plan
    zone = plan.captions_zone.rect
    for p in plan.placement.layers:
        assert not p.rect.intersects(zone), (p.layer_id, p.rect, zone)
    zone_out = bp.to_output(zone, W, H)
    assert _bright_pixels(frame_t1, zone_out) > 30, "субтитры должны быть видны в своей зоне"
    for layer_id in ("creative", "code", "erid"):
        r = plan.output_rects[layer_id]
        assert _bright_pixels(frame_t1, r) == 0, f"субтитры залезли в прямоугольник {layer_id}"


def test_png_creative_in_impulse_window_only(tmp_path: Path) -> None:
    creative, res = _render(tmp_path, _png_creative(tmp_path / "upload.png"), 6.0, "png")
    assert creative.kind.value == "static"
    info = probe_media(res.output_path)
    assert info.duration_sec == pytest.approx(6.0, abs=0.1)
    assert info.video is not None and (info.video.width, info.video.height) == (W, H)
    assert info.video.color_space == "bt709" and info.audio is not None
    windows = res.plan.windows["with_creative"]
    assert [(w.start_sec, w.end_sec) for w in windows] == [(2.4, 6.0)]
    rect = res.plan.output_rects["creative"]
    t1 = _frame(res.output_path, 1.0)
    t4 = _frame(res.output_path, 4.0)
    assert _near(_region_mean(t4, rect), (255, 0, 255)), _region_mean(t4, rect)
    assert _near(_region_mean(t1, rect), BASE_RGB), _region_mean(t1, rect)
    # служебный текст виден вместе с креативом и не виден без него
    code = res.plan.output_rects["code"]
    assert _bright_pixels(t4, code) > 5 and _bright_pixels(t1, code) == 0
    _check_captions_do_not_overlap(res, t1)
    # микро-бренд — весь ролик
    micro = res.plan.output_rects["micro_brand"]
    assert _bright_pixels(t1, micro) > 5 and _bright_pixels(t4, micro) > 5


def test_animated_gif_creative_in_window(tmp_path: Path) -> None:
    creative, res = _render(tmp_path, _gif_creative(tmp_path / "upload.gif"), 4.0, "gif")
    assert creative.kind.value == "animated" and creative.loop
    assert any(i.kind == "animated" for i in res.plan.items)
    windows = res.plan.windows["with_creative"]
    assert [(w.start_sec, w.end_sec) for w in windows] == [(2.0, 4.0)]
    rect = res.plan.output_rects["creative"]
    t1 = _frame(res.output_path, 1.0)
    assert _near(_region_mean(t1, rect), BASE_RGB), _region_mean(t1, rect)
    seen = set()
    for t in (2.6, 2.85, 3.1, 3.35):  # кадры GIF по 250 мс: красный/зелёный чередуются и зацикливаются
        mean = _region_mean(_frame(res.output_path, t), rect)
        if _near(mean, (255, 0, 0), 40):
            seen.add("red")
        elif _near(mean, (0, 255, 0), 40):
            seen.add("green")
        else:
            pytest.fail(f"t={t}: креатива нет в окне, среднее {mean}")
    assert seen == {"red", "green"}, "анимация должна идти, а не стоять на одном кадре"
    _check_captions_do_not_overlap(res, t1)
