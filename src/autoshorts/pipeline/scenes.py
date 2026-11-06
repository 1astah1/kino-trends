"""Смены сцены по видео и контактные листы кадров для отбора моментов (режим «сцена»).

* `detect_scene_cuts(proxy, duration)` — моменты монтажных склеек (секунды) через ffmpeg
  `select='gt(scene,T)'` на уменьшенной картинке. Результат кладётся в `scenes.json` рядом с прокси:
  `{"threshold": 0.35, "cuts": [12.04, 17.9, …], "frames": N}`. Склейки нужны отбору: маркеры
  «— смена сцены —» в расшифровке для LLM и снаппинг границ клипа на монтаж (`clipping.snapping`).
* `frames_sheet(proxy, start, end)` — JPEG-плитка 3×2 кадров фрагмента как data-URL для
  уточняющего запроса LLM: модель видит картинку сцены, а не только текст реплик.

Оба вызова терпимы к ошибкам: нет ffmpeg, битый файл, таймаут — возвращают пусто, отбор идёт
дальше по тексту.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCENES_NAME = "scenes.json"
DEFAULT_THRESHOLD = 0.35
MIN_GAP_SEC = 0.6  # две склейки ближе этого — одна (мерцание, вспышка)
MAX_DETECT_HOURS = 3.0
_PTS_RE = re.compile(r"pts_time:\s*([0-9]+(?:\.[0-9]+)?)")


def _nice(cmd: list[str], nice: int = 10) -> list[str]:
    return ["nice", "-n", str(nice), *cmd]


def detect_scene_cuts(
    proxy: str | Path,
    *,
    duration: float | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    threads: int = 2,
    timeout_sec: float = 2400.0,
    nice: int = 10,
) -> list[float]:
    """Секунды монтажных склеек по возрастанию (пусто при ошибке или слишком длинном видео)."""
    path = Path(proxy)
    if not path.is_file():
        return []
    if duration is not None and duration > MAX_DETECT_HOURS * 3600:
        log.info("scene detect skipped: %.1f h is over the limit", duration / 3600)
        return []
    cmd = _nice(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "info", "-threads", str(threads),
            "-i", str(path), "-an", "-sn",
            "-vf", f"scale=160:-2,select='gt(scene,{threshold})',showinfo",
            "-f", "null", "-",
        ],
        nice,
    )  # fmt: skip
    t0 = time.monotonic()
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("scene detect failed: %s", exc)
        return []
    if res.returncode != 0:
        log.warning("scene detect: ffmpeg exit %s: %s", res.returncode, (res.stderr or "")[-300:])
        return []
    raw = sorted(float(m.group(1)) for m in _PTS_RE.finditer(res.stderr or ""))
    cuts: list[float] = []
    for t in raw:
        if t <= 0.2:
            continue
        if cuts and t - cuts[-1] < MIN_GAP_SEC:
            continue
        cuts.append(round(t, 3))
    log.info("scene detect: %d cuts in %.0f s", len(cuts), time.monotonic() - t0)
    return cuts


def write_scenes(out_dir: str | Path, cuts: list[float], *, threshold: float = DEFAULT_THRESHOLD) -> Path:
    out = Path(out_dir) / SCENES_NAME
    out.write_text(json.dumps({"threshold": threshold, "cuts": cuts, "n": len(cuts)}, ensure_ascii=False))
    return out


def read_scenes(path: str | Path | None) -> list[float]:
    if not path:
        return []
    p = Path(path)
    if not p.is_file():
        return []
    try:
        data: Any = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    cuts = data.get("cuts") if isinstance(data, dict) else data
    try:
        return sorted(float(c) for c in (cuts or []))
    except (TypeError, ValueError):
        return []


def frames_sheet(
    proxy: str | Path,
    start: float,
    end: float,
    *,
    cols: int = 3,
    rows: int = 2,
    tile_w: int = 320,
    quality: int = 6,
    timeout_sec: float = 90.0,
    nice: int = 10,
) -> str | None:
    """Data-URL (image/jpeg) с плиткой cols×rows кадров фрагмента [start, end] или None."""
    path = Path(proxy)
    n = cols * rows
    dur = max(0.5, float(end) - float(start))
    if not path.is_file() or n <= 0:
        return None
    # кадры равномерно по фрагменту, первый — чуть после начала, чтобы не попасть на склейку
    fps = n / dur
    cmd = _nice(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-threads", "1",
            "-ss", f"{max(0.0, float(start)):.3f}", "-t", f"{dur:.3f}", "-i", str(path), "-an", "-sn",
            "-vf", f"fps={fps:.6f},scale={tile_w}:-2,tile={cols}x{rows}",
            "-frames:v", "1", "-q:v", str(quality), "-f", "image2pipe", "-vcodec", "mjpeg", "-",
        ],
        nice,
    )  # fmt: skip
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout_sec, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("frames sheet failed: %s", exc)
        return None
    if res.returncode != 0 or not res.stdout:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(res.stdout).decode("ascii")


def frames_provider(proxy: str | Path | None, *, max_calls: int = 16) -> Any:
    """Callable(start, end) → data-URL для `select_clips(frames=…)`; None, если прокси нет.
    Ограничивает число вызовов: кадры нужны только для шортлиста."""
    if not proxy or not Path(proxy).is_file():
        return None
    state = {"calls": 0}

    def provide(start: float, end: float) -> str | None:
        if state["calls"] >= max_calls:
            return None
        state["calls"] += 1
        return frames_sheet(proxy, start, end)

    return provide


__all__ = [
    "DEFAULT_THRESHOLD",
    "SCENES_NAME",
    "detect_scene_cuts",
    "frames_provider",
    "frames_sheet",
    "read_scenes",
    "write_scenes",
]
