"""YuNet (OpenCV Zoo, MIT) — детектор лиц для рефрейма (docs/ARCHITECTURE.md §6.3.6, research §5.6 п. 6).

Параметры §6.3.6: кадр анализа 640×360 (ширина 640, высота по пропорции), каждый 3-й кадр при
30 fps, то есть около 10 кадров в секунду. OpenCV работает в 1 поток (`cv2.setNumThreads(1)`,
§8.1), декодер FFmpeg внутри OpenCV — не больше 2 потоков.

Модель `face_detection_yunet_2026may.onnx` лежит в MODELS_DIR (закреплённая ревизия и sha256 в
`providers/asr/models.py`, скачивает `scripts/models_fetch.py --groups faces`). В рантайме ничего
не скачивается.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from autoshorts.providers.faces import FaceBox, FaceSample, FaceTrack

MODEL_FILENAME = "face_detection_yunet_2026may.onnx"
ANALYSIS_W = 640
ANALYSIS_FPS = 10.0
SCORE_THRESHOLD = 0.6
NMS_THRESHOLD = 0.3
TOP_K = 50


class FaceModelMissing(RuntimeError):
    """Нет файла модели YuNet в MODELS_DIR."""


def _cv2() -> Any:
    import cv2

    cv2.setNumThreads(1)
    try:  # OpenCV 5 пишет WARN «setPreferableTarget … not supported» на каждый детектор — шум в логах
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except AttributeError:
        pass
    return cv2


def default_model_path() -> Path:
    from autoshorts.providers.asr.models import models_dir

    return models_dir() / MODEL_FILENAME


class YuNetDetector:
    """`detect(frame_bgr) -> list[FaceBox]` в пикселях переданного кадра."""

    def __init__(
        self,
        model_path: str | Path | None = None,
        *,
        analysis_w: int = ANALYSIS_W,
        score_threshold: float = SCORE_THRESHOLD,
        nms_threshold: float = NMS_THRESHOLD,
        top_k: int = TOP_K,
    ) -> None:
        path = Path(model_path) if model_path else default_model_path()
        if not path.is_file():
            raise FaceModelMissing(
                f"YuNet model not found: {path}. Run: python scripts/models_fetch.py --groups faces"
            )
        self.model_path = path
        self.analysis_w = int(analysis_w)
        self.score_threshold = float(score_threshold)
        self._cv2 = _cv2()
        self._net = self._cv2.FaceDetectorYN.create(
            str(path), "", (self.analysis_w, 360), float(score_threshold), float(nms_threshold), int(top_k)
        )
        self._size: tuple[int, int] | None = None

    def _analysis_size(self, w: int, h: int) -> tuple[int, int]:
        if w <= self.analysis_w:
            return w, h
        return self.analysis_w, max(2, int(round(h * self.analysis_w / w)))

    def detect(self, frame_bgr: Any) -> list[FaceBox]:
        h, w = frame_bgr.shape[:2]
        aw, ah = self._analysis_size(w, h)
        img = (
            frame_bgr
            if (aw, ah) == (w, h)
            else self._cv2.resize(frame_bgr, (aw, ah), interpolation=self._cv2.INTER_AREA)
        )
        if self._size != (aw, ah):
            self._net.setInputSize((aw, ah))
            self._size = (aw, ah)
        _, faces = self._net.detect(img)
        if faces is None:
            return []
        sx, sy = w / aw, h / ah
        out: list[FaceBox] = []
        for row in faces:
            x, y, fw, fh = (float(v) for v in row[:4])
            lm = tuple(float(v) for v in row[4:14])
            score = float(row[14])
            if score < self.score_threshold or fw <= 1 or fh <= 1:
                continue
            out.append(FaceBox(x, y, fw, fh, score, lm).scaled(sx, sy))
        out.sort(key=lambda b: -b.area)
        return out


def sample_faces(
    video: str | Path,
    start: float,
    end: float,
    *,
    detector: YuNetDetector | None = None,
    analysis_fps: float = ANALYSIS_FPS,
    min_face_frac: float = 0.025,
    decoder_threads: int = 2,
) -> FaceTrack:
    """Лица на отрезке `[start, end]` видео с шагом ≈ 1/analysis_fps.

    Кадры, которые не анализируются, только `grab()`-ятся (без конвертации в BGR). Координаты в
    результате нормированы к кадру (0..1). Лица с высотой меньше `min_face_frac` кадра
    отбрасываются (зрители на фоне, портреты на стене).
    """
    cv2 = _cv2()
    det = detector or YuNetDetector()
    t0 = time.monotonic()
    params: list[int] = []
    if hasattr(cv2, "CAP_PROP_N_THREADS"):
        params = [cv2.CAP_PROP_N_THREADS, int(decoder_threads)]
    try:
        cap = cv2.VideoCapture(str(video), cv2.CAP_FFMPEG, params)
    except Exception:  # noqa: BLE001 - старые сборки без params
        cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise FileNotFoundError(f"cannot open video: {video}")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
        fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        step = max(1, int(round(fps / max(analysis_fps, 0.1))))
        track = FaceTrack(start=float(start), end=float(end), fps=fps / step, frame_w=fw, frame_h=fh)
        if start > 0:
            cap.set(cv2.CAP_PROP_POS_MSEC, float(start) * 1000.0)
        idx = 0
        decoded = 0
        while True:
            if not cap.grab():
                break
            decoded += 1
            t = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0) / 1000.0
            if t > end + 1e-3:
                break
            if t + 1e-3 < start:
                continue
            if idx % step == 0:
                ok, frame = cap.retrieve()
                if ok and frame is not None:
                    h, w = frame.shape[:2]
                    if not fw:
                        fw, fh = w, h
                        track.frame_w, track.frame_h = w, h
                    boxes = [
                        b.scaled(1.0 / w, 1.0 / h) for b in det.detect(frame) if b.h >= min_face_frac * h
                    ]
                    track.samples.append(FaceSample(t, boxes))
            idx += 1
        track.frames_decoded = decoded
    finally:
        cap.release()
    track.wall_sec = time.monotonic() - t0
    return track


__all__ = [
    "ANALYSIS_FPS",
    "ANALYSIS_W",
    "MODEL_FILENAME",
    "FaceModelMissing",
    "YuNetDetector",
    "default_model_path",
    "sample_faces",
]
