"""Storyboard sprite sheets for scrubbing and candidate previews (ARCHITECTURE.md §6.3.1).

    sb = make_storyboard("proxy_540p.mp4", out_dir)       # fps=1/2, scale=160:-2, tile=10x10
    sheet, x, y = sb.tile_at(125.0)                        # which sheet and pixel offset shows t=125 s

One ffmpeg pass over the proxy writes `storyboard_000.jpg`, `storyboard_001.jpg`, ... Each sheet holds
cols x rows tiles, one tile every `interval_sec` seconds, tile width 160 px, height by aspect. With the
1-second GOP of the proxy only key frames are decoded (`-skip_frame nokey`), which makes this pass
several times cheaper than a full decode. The last sheet is padded by the tile filter.

Result JSON (goes to `sources.storyboard`): {sheets, cols, rows, tile_w, tile_h, interval_sec, count}.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SHEET_PREFIX = "storyboard_"
DEFAULT_INTERVAL = 2.0
DEFAULT_TILE_W = 160
DEFAULT_COLS = 10
DEFAULT_ROWS = 10


@dataclass(slots=True)
class Storyboard:
    sheets: list[Path]
    cols: int
    rows: int
    tile_w: int
    tile_h: int
    interval_sec: float
    count: int
    wall_sec: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def per_sheet(self) -> int:
        return self.cols * self.rows

    def tile_at(self, t: float) -> tuple[int, int, int]:
        """(sheet index, x, y) of the tile covering time `t` seconds."""
        idx = max(0, min(self.count - 1, int(t // self.interval_sec)))
        sheet, pos = divmod(idx, self.per_sheet)
        row, col = divmod(pos, self.cols)
        return sheet, col * self.tile_w, row * self.tile_h

    def to_dict(self) -> dict[str, Any]:
        return {
            "sheets": [p.name for p in self.sheets],
            "cols": self.cols,
            "rows": self.rows,
            "tile_w": self.tile_w,
            "tile_h": self.tile_h,
            "interval_sec": self.interval_sec,
            "count": self.count,
        }


def build_storyboard_cmd(
    video: str | Path,
    out_dir: str | Path,
    *,
    interval_sec: float = DEFAULT_INTERVAL,
    tile_w: int = DEFAULT_TILE_W,
    cols: int = DEFAULT_COLS,
    rows: int = DEFAULT_ROWS,
    keyframes_only: bool = True,
    threads: int = 2,
    quality: int = 5,
) -> list[str]:
    pattern = Path(out_dir) / f"{SHEET_PREFIX}%03d.jpg"
    # eof_action=pass keeps the last tile when only key frames are decoded
    fps = f"fps=1/{interval_sec:g}:round=near:eof_action=pass"
    vf = f"{fps},scale={tile_w}:-2:flags=bilinear,tile={cols}x{rows}"
    cmd = ["ffmpeg", "-y"]
    if keyframes_only:
        cmd += ["-skip_frame", "nokey"]
    cmd += ["-threads", str(threads), "-i", str(video), "-an", "-sn", "-vf", vf]
    cmd += [
        "-fps_mode",
        "vfr",
        "-q:v",
        str(quality),
        "-start_number",
        "0",
        "-threads",
        str(threads),
        str(pattern),
    ]
    return cmd


def make_storyboard(
    video: str | Path,
    out_dir: str | Path,
    *,
    duration: float | None = None,
    interval_sec: float = DEFAULT_INTERVAL,
    tile_w: int = DEFAULT_TILE_W,
    cols: int = DEFAULT_COLS,
    rows: int = DEFAULT_ROWS,
    keyframes_only: bool = True,
    threads: int = 2,
    nice: int = 10,
    timeout_sec: float | None = None,
    progress_cb: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
    log_path: Path | None = None,
) -> Storyboard:
    from PIL import Image

    from autoshorts.pipeline.ffmpeg_exec import run_media

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob(f"{SHEET_PREFIX}*.jpg"):
        old.unlink()
    if duration is None:
        from autoshorts.pipeline.proxy import probe_media

        duration = probe_media(video).duration
    cmd = build_storyboard_cmd(
        video,
        out,
        interval_sec=interval_sec,
        tile_w=tile_w,
        cols=cols,
        rows=rows,
        keyframes_only=keyframes_only,
        threads=threads,
    )
    res = run_media(
        cmd,
        timeout_sec=timeout_sec if timeout_sec is not None else 120.0 + 0.5 * max(duration, 1.0),
        threads=threads,
        nice=nice,
        progress_cb=progress_cb,
        cancel=cancel,
        log_path=log_path,
        duration_sec=duration or None,
    )
    sheets = sorted(out.glob(f"{SHEET_PREFIX}*.jpg"))
    if not sheets:
        raise RuntimeError(f"storyboard: ffmpeg produced no sheets for {Path(video).name}")
    with Image.open(sheets[0]) as im:
        sheet_w, sheet_h = im.size
    count = max(1, int(math.ceil(duration / interval_sec))) if duration else len(sheets) * cols * rows
    count = min(count, len(sheets) * cols * rows)
    return Storyboard(
        sheets=sheets,
        cols=cols,
        rows=rows,
        tile_w=sheet_w // cols,
        tile_h=sheet_h // rows,
        interval_sec=float(interval_sec),
        count=count,
        wall_sec=res.duration_ms / 1000.0,
    )


__all__ = ["SHEET_PREFIX", "Storyboard", "build_storyboard_cmd", "make_storyboard"]
