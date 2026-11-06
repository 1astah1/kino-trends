"""Детекторы лиц для рефрейма 16:9 → 9:16 (docs/ARCHITECTURE.md §5.5 `FaceDetector`, §6.3.6).

    det = get_face_detector()                      # YuNet (OpenCV), модель из MODELS_DIR
    boxes = det.detect(frame_bgr)                  # list[FaceBox] в пикселях кадра
    track = sample_faces("proxy_540p.mp4", 84.0, 130.0)   # 10 fps, 640×360, каждый 3-й кадр

Импорт пакета дешёвый: cv2 и numpy подгружаются только при создании детектора.
Детектор нужен только для кадрирования (куда поставить окно 9:16), распознавания личности нет.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class FaceBox:
    """Лицо в пикселях кадра, на котором запускался детектор."""

    x: float
    y: float
    w: float
    h: float
    score: float
    landmarks: tuple[float, ...] = ()

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def area(self) -> float:
        return self.w * self.h

    def scaled(self, sx: float, sy: float) -> FaceBox:
        lm = tuple(v * (sx if i % 2 == 0 else sy) for i, v in enumerate(self.landmarks))
        return FaceBox(self.x * sx, self.y * sy, self.w * sx, self.h * sy, self.score, lm)

    def to_dict(self) -> dict[str, float]:
        return {
            "x": round(self.x, 1),
            "y": round(self.y, 1),
            "w": round(self.w, 1),
            "h": round(self.h, 1),
            "score": round(self.score, 3),
        }


@dataclass(slots=True)
class FaceSample:
    """Лица в момент `t` (секунды источника). Координаты нормированы к кадру: 0..1."""

    t: float
    faces: list[FaceBox] = field(default_factory=list)


@dataclass(slots=True)
class FaceTrack:
    """Результат выборки лиц на отрезке источника."""

    start: float
    end: float
    fps: float  # частота анализа (≈10)
    frame_w: int  # размер кадра источника анализа (прокси), пиксели
    frame_h: int
    samples: list[FaceSample] = field(default_factory=list)
    frames_decoded: int = 0
    wall_sec: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "fps": self.fps,
            "frame_w": self.frame_w,
            "frame_h": self.frame_h,
            "frames_decoded": self.frames_decoded,
            "wall_sec": round(self.wall_sec, 3),
            "samples": [{"t": round(s.t, 3), "faces": [f.to_dict() for f in s.faces]} for s in self.samples],
        }


class FaceDetector(Protocol):
    def detect(self, frame_bgr: Any) -> list[FaceBox]: ...


def get_face_detector(key: str = "yunet", **kwargs: Any) -> FaceDetector:
    if key in ("yunet", "yunet_onnx", "opencv_yunet"):
        from autoshorts.providers.faces.yunet import YuNetDetector

        return YuNetDetector(**kwargs)
    raise ValueError(f"unknown face detector: {key}")


def sample_faces(video: Any, start: float, end: float, **kwargs: Any) -> FaceTrack:
    """Лица на отрезке `[start, end]` видео (обычно прокси). См. `yunet.sample_faces`."""
    from autoshorts.providers.faces.yunet import sample_faces as _sample

    return _sample(video, start, end, **kwargs)


__all__ = ["FaceBox", "FaceDetector", "FaceSample", "FaceTrack", "get_face_detector", "sample_faces"]
