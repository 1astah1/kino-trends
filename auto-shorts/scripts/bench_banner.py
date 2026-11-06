#!/usr/bin/env python3
"""Замер одного прохода «субтитры + загруженный креатив» на 30-секундном ролике 1080×1920.

    .venv/bin/python scripts/bench_banner.py                 # статичный PNG-креатив
    .venv/bin/python scripts/bench_banner.py --animated      # анимированный GIF → VP9 с альфой
    .venv/bin/python scripts/bench_banner.py --keep /tmp/x   # не удалять файлы

Подготовка (база testsrc2 + синус, креатив, ASS) не входит в замер. Замеряется только проход
`banner_overlay.render_with_banner` (probe + план + ffmpeg). Все процессы — `nice -n 10`,
`-threads` из `AUTOSHORTS_FFMPEG_THREADS` (по умолчанию 2). Запускать по одному разу: бокс общий.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from autoshorts.domain.banner_schema import default_banner_variant  # noqa: E402
from autoshorts.models import WordTiming  # noqa: E402
from autoshorts.pipeline import banner_assets as ba  # noqa: E402
from autoshorts.pipeline import banner_overlay as bo  # noqa: E402
from autoshorts.pipeline import banner_place as bp  # noqa: E402
from autoshorts.pipeline.probe import ffmpeg_threads, probe_media, run_media_cmd  # noqa: E402
from autoshorts.pipeline.subtitles import build_karaoke_ass  # noqa: E402

FONT = ROOT / "config" / "fonts" / "Montserrat-Bold.ttf"
WORDS = (
    "Эти нейросети помогут вам сделать больше за один вечер без лишних подписок и сложных настроек".split()
)


def make_base(path: Path, duration: float) -> None:
    run_media_cmd(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=s=1080x1920:r=30:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=f=220:d={duration}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            str(path),
        ],
        timeout_sec=600,
    )


def make_png_creative(path: Path) -> None:
    im = Image.new("RGBA", (900, 220), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, 899, 219), radius=36, fill=(18, 20, 38, 235))
    font = ImageFont.truetype(str(FONT), 58)
    d.text((450, 110), "Umka — все нейросети", font=font, fill=(255, 255, 255, 255), anchor="mm")
    im.save(path)


def make_gif_creative(path: Path) -> None:
    font = ImageFont.truetype(str(FONT), 58)
    frames = []
    for i in range(20):
        im = Image.new("RGBA", (900, 220), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((0, 0, 899, 219), radius=36, fill=(18, 20, 38, 235))
        d.text(
            (450, 110),
            "Umka — все нейросети",
            font=font,
            fill=(255, 255 - i * 6, 80 + i * 8, 255),
            anchor="mm",
        )
        frames.append(im)
    frames[0].save(
        path, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0, disposal=2
    )


def words_for(duration: float) -> list[WordTiming]:
    step = 0.4
    out, t, i = [], 0.0, 0
    while t + step <= duration:
        out.append(WordTiming(WORDS[i % len(WORDS)], round(t, 2), round(t + step, 2)))
        t += step
        i += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--animated", action="store_true", help="GIF-креатив вместо PNG")
    ap.add_argument("--keep", type=Path, default=None, help="каталог для файлов (не удаляется)")
    args = ap.parse_args()
    if shutil.which("ffmpeg") is None:
        print("ffmpeg not found", file=sys.stderr)
        return 2
    os.nice(10)  # сам Python тоже щадящий: Pillow-подготовка идёт в этом процессе
    tmp_ctx = None
    if args.keep:
        work = args.keep
        work.mkdir(parents=True, exist_ok=True)
    else:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="bench_banner_")
        work = Path(tmp_ctx.name)
    try:
        t0 = time.monotonic()
        base = work / "base.mp4"
        make_base(base, args.duration)
        upload = work / ("upload.gif" if args.animated else "upload.png")
        (make_gif_creative if args.animated else make_png_creative)(upload)
        media = work / "media"
        t_ing = time.monotonic()
        creative = ba.ingest_creative(upload, media)
        ingest_sec = time.monotonic() - t_ing
        ass = build_karaoke_ass(words_for(args.duration), work / "subs.ass")
        prep_sec = time.monotonic() - t0
        variant = default_banner_variant(creative_sha256=creative.sha256, erid="2VtzqvBench")
        out = work / "final.mp4"
        t1 = time.monotonic()
        res = bo.render_with_banner(
            base,
            out,
            variant=variant,
            creatives={creative.sha256: creative},
            media_root=media,
            profile=bp.load_safe_zone_catalog().get(),
            workdir=work / "work",
            ass_path=ass,
            code="k7m2q",
            timeout_sec=1800,
        )
        wall = time.monotonic() - t1
        info = probe_media(out)
        summary = {
            "duration_sec": round(info.duration_sec, 2),
            "size": f"{info.width}x{info.height}",
            "creative": creative.kind.value,
            "ingest_sec": round(ingest_sec, 2),
            "prep_sec_excluded": round(prep_sec, 2),
            "render_wall_sec": round(wall, 2),
            "ffmpeg_wall_sec": res.wall_sec,
            "realtime_x": round(info.duration_sec / wall, 2) if wall else None,
            "out_mb": round(out.stat().st_size / 1e6, 2),
            "video_kbps": round(info.bit_rate / 1000) if info.bit_rate else None,
            "threads": ffmpeg_threads(),
            "overlay_inputs": sum(len(i.windows) for i in res.plan.items),
            "windows": {k: [(w.start_sec, w.end_sec) for w in v] for k, v in res.plan.windows.items()},
            "loadavg": os.getloadavg()[0],
        }
        print(f"render wall time: {wall:.2f} s for {info.duration_sec:.1f} s of 1080x1920")
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
