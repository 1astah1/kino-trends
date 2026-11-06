"""Цепочка рекламной вставки на настоящем ffmpeg: затемнение, крутилка и плашка появляются и уходят вовремя."""

import subprocess

from PIL import Image

from autoshorts.clipping.lag_ad import overlay_chains


def _px(path, t, x, y):
    png = path.with_suffix(f".{t}.png")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", str(path), "-frames:v", "1", str(png)], check=True)
    return Image.open(png).convert("RGB").getpixel((x, y))


def test_overlay_chain_renders(tmp_path) -> None:
    out = tmp_path / "out.mp4"
    ins, chain = overlay_chains("0:v", 1, {"start": 1.0, "dur": 3.0, "buffer": 1.0, "cy": 960}, fps=30, width=1080, height=1920, out="v")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=white:s=1080x1920:r=30:d=5", *ins,
                    "-filter_complex", chain, "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-t", "5", str(out)],
                   check=True)  # fmt: skip
    before, dimmed, banner, after = _px(out, 0.5, 100, 100), _px(out, 1.5, 100, 100), _px(out, 3.0, 540, 960), _px(out, 4.6, 100, 100)
    assert min(before) > 240 and min(after) > 240  # до и после вставки кадр чистый
    assert max(dimmed) < 200  # затемнение
    assert banner[2] > banner[0]  # в центре синяя плашка BPN
