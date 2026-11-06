"""Склейки в окне клипа: PySceneDetect `AdaptiveDetector` на прокси (docs/ARCHITECTURE.md §6.3.6).

Детектор запускается только на отрезке `[start − pad, end + pad]` (pad = 3 с), а не на всём
источнике. Склейки нужны рефрейму: на склейке камера не «едет» к новому лицу, а прыгает сразу,
и стратегия кадрирования выбирается для каждого шота отдельно.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SHOT_PAD_SEC = 3.0
ADAPTIVE_THRESHOLD = 3.0
MIN_SCENE_LEN_FRAMES = 15


@dataclass(slots=True)
class ShotDetection:
    start: float  # фактически просмотренный отрезок источника
    end: float
    cuts: list[float] = field(default_factory=list)  # моменты склеек, секунды источника
    frames: int = 0
    wall_sec: float = 0.0
    backend: str = "pyscenedetect_adaptive"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "cuts": [round(c, 3) for c in self.cuts],
            "frames": self.frames,
            "wall_sec": round(self.wall_sec, 3),
            "backend": self.backend,
            "error": self.error,
        }


def detect_shots(
    video: str | Path,
    start: float,
    end: float,
    *,
    pad: float = SHOT_PAD_SEC,
    adaptive_threshold: float = ADAPTIVE_THRESHOLD,
    min_scene_len: int = MIN_SCENE_LEN_FRAMES,
) -> ShotDetection:
    """Склейки на отрезке `[start − pad, end + pad]`; ошибки декодера не валят клип (cuts = [])."""
    a = max(0.0, float(start) - pad)
    b = float(end) + pad
    t0 = time.monotonic()
    res = ShotDetection(start=a, end=b)
    try:
        import cv2
        from scenedetect import SceneManager, open_video
        from scenedetect.detectors import AdaptiveDetector

        cv2.setNumThreads(1)
        vid = open_video(str(video), backend="opencv")
        if a > 0:
            vid.seek(a)
        sm = SceneManager()
        sm.auto_downscale = True
        sm.add_detector(AdaptiveDetector(adaptive_threshold=adaptive_threshold, min_scene_len=min_scene_len))
        res.frames = int(sm.detect_scenes(video=vid, end_time=b))
        scenes = sm.get_scene_list()
        res.cuts = sorted(float(s.get_seconds()) for s, _ in scenes[1:])
    except Exception as exc:  # noqa: BLE001 - рефрейм без склеек лучше, чем упавший клип
        res.error = f"{type(exc).__name__}: {exc}"[:300]
        res.cuts = []
    res.wall_sec = time.monotonic() - t0
    return res


def split_by_cuts(
    start: float, end: float, cuts: list[float], *, min_len: float = 0.4
) -> list[tuple[float, float]]:
    """Отрезок `[start, end]` → шоты по склейкам внутри него; шоты короче `min_len` сливаются с соседним."""
    inner = [c for c in sorted(cuts) if start + min_len <= c <= end - min_len]
    bounds = [start, *inner, end]
    shots: list[tuple[float, float]] = []
    for a, b in zip(bounds, bounds[1:], strict=False):
        if shots and b - a < min_len:
            shots[-1] = (shots[-1][0], b)
        else:
            shots.append((a, b))
    return shots


__all__ = [
    "ADAPTIVE_THRESHOLD",
    "MIN_SCENE_LEN_FRAMES",
    "SHOT_PAD_SEC",
    "ShotDetection",
    "detect_shots",
    "split_by_cuts",
]
