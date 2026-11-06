"""Стратегия кадрирования на шот и план рефрейма клипа (docs/ARCHITECTURE.md §6.3.6).

Для каждого шота (отрезок между склейками) выбирается одно из:

* `track` — окно 9:16 следует за главным лицом (`SmoothedCameraman`, EMA τ = 0,6 с,
  скорость ≤ 0,35·crop_w/с), выход — команды `sendcmd` для `crop@cN`;
* `center` — неподвижное окно: на медиане лица, если человек почти не двигается (так нет
  микродрожания), или по центру кадра (вертикальный источник, оператор выбрал сам);
* `fit_blur` — весь кадр целиком по ширине на размытом фоне того же кадра: лиц нет (слайды,
  экран, общий план) или в кадре группа, которая не помещается в окно 9:16.

Анализ идёт на прокси 540p (§6.3), координаты нормируются и переводятся в пиксели того файла,
который пойдёт в рендер (оригинал загрузки или 1080p-секция).
"""

from __future__ import annotations

import math
import statistics
import time
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from autoshorts.clipping.reframe.cameraman import (
    CameraConfig,
    SmoothedCameraman,
    frame_times,
    sendcmd_lines,
)
from autoshorts.clipping.reframe.shots import ShotDetection, detect_shots, split_by_cuts

ReframeMode = Literal["track", "center", "fit_blur"]
REFRAME_MODES: tuple[str, ...] = ("track", "center", "fit_blur")
TARGET_ASPECT = 9 / 16
DEFAULT_FPS = 30.0


def _even(v: float) -> int:
    i = int(round(v))
    return i - (i % 2)


@dataclass(frozen=True, slots=True)
class CropGeometry:
    """Окно 9:16 в пикселях файла рендера."""

    src_w: int
    src_h: int
    crop_w: int
    crop_h: int
    y: int

    @classmethod
    def for_source(
        cls, src_w: int, src_h: int, aspect: float = TARGET_ASPECT, box: dict[str, int] | None = None
    ) -> CropGeometry:
        """`box` — картинка без чёрных полос (frame_style.detect_content_box): у широкоэкранного кино с вшитым
        letterbox окно 9:16 берётся по высоте картинки, а не файла — полосы не попадают в вертикальный кадр."""
        if src_w <= 0 or src_h <= 0:
            raise ValueError("bad source size")
        if box and 0 < int(box.get("h") or 0) < src_h - 8 and src_w / max(1, int(box["h"])) > aspect + 1e-3:
            crop_h = _even(int(box["h"]))
            crop_w = min(src_w - (src_w % 2), max(2, _even(crop_h * aspect)))
            y = _even(min(max(0, int(box.get("y") or 0)), src_h - crop_h))
            return cls(int(src_w), int(src_h), int(crop_w), int(crop_h), int(y))
        if src_w / src_h > aspect + 1e-3:  # шире 9:16 — режем по ширине
            crop_h = src_h - (src_h % 2)
            crop_w = min(src_w - (src_w % 2), max(2, _even(crop_h * aspect)))
            y = 0
        else:  # уже вертикальный или уже — режем по высоте (почти всегда ничего)
            crop_w = src_w - (src_w % 2)
            crop_h = min(src_h - (src_h % 2), max(2, _even(crop_w / aspect)))
            y = _even((src_h - crop_h) / 2)
        return cls(int(src_w), int(src_h), int(crop_w), int(crop_h), int(max(0, y)))

    @property
    def needs_horizontal(self) -> bool:
        return self.crop_w < self.src_w - 2

    @property
    def max_x(self) -> int:
        return max(0, self.src_w - self.crop_w)

    def center_x(self) -> int:
        return _even(self.max_x / 2)

    def x_for_center(self, cx: float) -> int:
        return _even(min(max(0.0, cx - self.crop_w / 2), self.max_x))

    def to_dict(self) -> dict[str, int]:
        return {
            "src_w": self.src_w,
            "src_h": self.src_h,
            "crop_w": self.crop_w,
            "crop_h": self.crop_h,
            "y": self.y,
        }


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    no_face_mode: ReframeMode = "fit_blur"
    min_face_coverage: float = 0.3  # доля измерений с лицом, ниже — «лиц нет»
    group_area_ratio: float = 0.35  # лицо входит в группу, если площадь ≥ этой доли главного
    group_spread_share: float = 0.4  # доля измерений, где группа шире окна → fit_blur
    wide_fit_margin: float = 0.95  # группа «не влезает», если шире crop_w · margin
    static_motion_frac: float = 0.02  # σ центра лица (доля ширины кадра) ниже — неподвижное окно
    switch_area_ratio: float = 0.6  # удерживать прежнее лицо, если оно не меньше этой доли главного


@dataclass(slots=True)
class ShotPlan:
    idx: int
    start: float
    end: float
    mode: ReframeMode
    reason: str
    x_static: int
    points: list[tuple[float, float]] = field(default_factory=list)  # (t источника, x) для track
    stats: dict[str, Any] = field(default_factory=dict)

    def x_at(self, t: float) -> int:
        if self.mode != "track" or not self.points:
            return self.x_static
        times = [p[0] for p in self.points]
        i = max(0, min(len(self.points) - 1, bisect_right(times, t) - 1))
        return _even(self.points[i][1])

    def to_dict(self) -> dict[str, Any]:
        return {
            "idx": self.idx,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "mode": self.mode,
            "reason": self.reason,
            "x_static": self.x_static,
            "n_points": len(self.points),
            "stats": self.stats,
        }


@dataclass(slots=True)
class ReframePlan:
    geometry: CropGeometry
    start: float
    end: float
    fps: float = DEFAULT_FPS
    shots: list[ShotPlan] = field(default_factory=list)
    scene_cuts: list[float] = field(default_factory=list)
    requested_mode: str = "auto"
    shot_detection: dict[str, Any] | None = None
    faces: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    def shot_at(self, t: float) -> ShotPlan:
        if not self.shots:
            raise ValueError("empty reframe plan")
        for sh in self.shots:
            if sh.start - 1e-6 <= t < sh.end:
                return sh
        return self.shots[0] if t < self.shots[0].start else self.shots[-1]

    def x_at(self, t: float) -> int:
        return self.shot_at(t).x_at(t)

    def mode_at(self, t: float) -> ReframeMode:
        return self.shot_at(t).mode

    def pieces(self, a: float, b: float, *, min_len: float = 1e-3) -> list[tuple[float, float, ShotPlan]]:
        """Отрезок источника `[a, b]` → куски по границам шотов (у каждого одна стратегия)."""
        out: list[tuple[float, float, ShotPlan]] = []
        for sh in self.shots:
            lo, hi = max(a, sh.start), min(b, sh.end)
            if hi - lo > min_len:
                out.append((lo, hi, sh))
        if not out:
            out.append((a, b, self.shot_at(a)))
        # края плана: то, что вышло за [start, end] анализа, берёт крайний шот
        if out[0][0] > a + min_len:
            out[0] = (a, out[0][1], out[0][2])
        if out[-1][1] < b - min_len:
            out[-1] = (out[-1][0], b, out[-1][2])
        return out

    def sendcmd_for(self, a: float, b: float, *, target: str) -> list[str]:
        """Команды `sendcmd` для куска `[a, b]` одного track-шота; время относительно `a`."""
        sh = self.shot_at((a + b) / 2)
        if sh.mode != "track" or not sh.points:
            return [f"0.000 {target} x {sh.x_at(a)};"]
        pts = [(t, x) for t, x in sh.points if a - 1e-6 <= t < b]
        if not pts or pts[0][0] > a + 1e-3:
            pts.insert(0, (a, float(sh.x_at(a))))
        return sendcmd_lines(pts, target=target, t_offset=a)

    def to_dict(self) -> dict[str, Any]:
        return {
            "geometry": self.geometry.to_dict(),
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "fps": self.fps,
            "requested_mode": self.requested_mode,
            "shots": [s.to_dict() for s in self.shots],
            "scene_cuts": [round(c, 3) for c in self.scene_cuts],
            "shot_detection": self.shot_detection,
            "warnings": list(self.warnings),
            "timings": {k: round(v, 3) for k, v in self.timings.items()},
        }


# ---------------------------------------------------------------------- стратегия шота


def _primary_targets(
    samples: Sequence[Any], geometry: CropGeometry, cfg: StrategyConfig
) -> list[tuple[float, float]]:
    """(t, cx в пикселях рендера) главного лица с удержанием прежнего лица при близких площадях."""
    out: list[tuple[float, float]] = []
    prev: float | None = None
    for s in samples:
        if not s.faces:
            continue
        faces = sorted(s.faces, key=lambda f: -f.area)
        big = faces[0].area
        pick = faces[0]
        if prev is not None:
            near = [f for f in faces if f.area >= cfg.switch_area_ratio * big]
            pick = min(near, key=lambda f: abs(f.cx * geometry.src_w - prev))
        cx = pick.cx * geometry.src_w
        out.append((float(s.t), cx))
        prev = cx
    return out


def choose_strategy(
    samples: Sequence[Any], geometry: CropGeometry, cfg: StrategyConfig | None = None
) -> tuple[ReframeMode, str, dict[str, Any], list[tuple[float, float]]]:
    """Стратегия для шота по измерениям лиц (нормированные FaceBox). Возвращает режим, причину,
    статистику и цели `(t, cx_px)` для камеры."""
    cfg = cfg or StrategyConfig()
    n = len(samples)
    if not geometry.needs_horizontal:
        return "center", "vertical_source", {"n": n}, []
    with_faces = [s for s in samples if s.faces]
    coverage = len(with_faces) / n if n else 0.0
    stats: dict[str, Any] = {"n": n, "face_coverage": round(coverage, 3)}
    if n == 0 or coverage < cfg.min_face_coverage:
        return cfg.no_face_mode, "no_faces", stats, []
    crop_norm = geometry.crop_w / geometry.src_w
    wide = 0
    for s in with_faces:
        big = max(f.area for f in s.faces)
        group = [f for f in s.faces if f.area >= cfg.group_area_ratio * big]
        span = max(f.x + f.w for f in group) - min(f.x for f in group)
        if len(group) > 1 and span > crop_norm * cfg.wide_fit_margin:
            wide += 1
    spread_share = wide / len(with_faces)
    stats["group_spread_share"] = round(spread_share, 3)
    targets = _primary_targets(samples, geometry, cfg)
    if spread_share >= cfg.group_spread_share:
        return "fit_blur", "wide_group", stats, targets
    cxs = [c / geometry.src_w for _, c in targets]
    motion = statistics.pstdev(cxs) if len(cxs) > 1 else 0.0
    stats["motion"] = round(motion, 4)
    if motion < cfg.static_motion_frac:
        return "center", "static_subject", stats, targets
    return "track", "moving_subject", stats, targets


# ---------------------------------------------------------------------- план


def _static_x(targets: list[tuple[float, float]], geometry: CropGeometry) -> int:
    if not targets:
        return geometry.center_x()
    return geometry.x_for_center(statistics.median(c for _, c in targets))


def build_plan(
    geometry: CropGeometry,
    start: float,
    end: float,
    *,
    face_samples: Sequence[Any] = (),
    scene_cuts: Sequence[float] = (),
    fps: float = DEFAULT_FPS,
    mode: str = "auto",
    strategy: StrategyConfig | None = None,
    camera: CameraConfig | None = None,
) -> ReframePlan:
    """План рефрейма из готовых измерений (без видео): чистая функция, её проверяют unit-тесты."""
    cfg = strategy or StrategyConfig()
    cam = SmoothedCameraman(geometry.crop_w, geometry.src_w, camera)
    cuts = sorted(float(c) for c in scene_cuts)
    plan = ReframePlan(geometry, float(start), float(end), fps=float(fps), requested_mode=mode)
    plan.scene_cuts = [c for c in cuts if start < c < end]
    samples = sorted(face_samples, key=lambda s: s.t)
    stimes = [s.t for s in samples]
    for i, (a, b) in enumerate(split_by_cuts(start, end, cuts)):
        lo = bisect_right(stimes, a - 1e-6)
        hi = bisect_right(stimes, b)
        shot_samples = samples[lo:hi]
        auto_mode, reason, stats, targets = choose_strategy(shot_samples, geometry, cfg)
        chosen: ReframeMode = auto_mode
        if mode in REFRAME_MODES:
            chosen = mode  # type: ignore[assignment]
            reason = f"operator:{mode}" if mode != auto_mode else reason
        if not geometry.needs_horizontal:
            chosen, reason = "center", "vertical_source"
        shot = ShotPlan(i, a, b, chosen, reason, _static_x(targets, geometry), stats=stats)
        if chosen == "track":
            if targets:
                times = frame_times(a, b, fps)
                xs = cam.track(targets, times, cuts=(), fallback_cx=geometry.src_w / 2)
                shot.points = [(t, x) for t, x in zip(times, xs, strict=True)]
            else:
                shot.mode, shot.reason = "center", "track_without_faces"
        plan.shots.append(shot)
    return plan


def plan_reframe(
    analysis_video: str | Path,
    start: float,
    end: float,
    *,
    src_w: int,
    src_h: int,
    fps: float = DEFAULT_FPS,
    mode: str = "auto",
    detect_scenes: bool = True,
    detect_faces: bool = True,
    detector: Any = None,
    strategy: StrategyConfig | None = None,
    camera: CameraConfig | None = None,
    shot_pad: float = 3.0,
    content_box: dict[str, int] | None = None,
) -> ReframePlan:
    """Анализ `[start, end]` источника на прокси: склейки + лица → стратегии шотов (§6.3.6).

    `src_w/src_h` — размер файла, который пойдёт в рендер. Нет модели лиц — план строится без
    лиц (fit_blur или то, что выбрал оператор) и с предупреждением.
    """
    geometry = CropGeometry.for_source(src_w, src_h, box=content_box)
    warnings: list[str] = []
    if content_box and geometry.crop_h < src_h - 8:
        warnings.append(f"letterbox_cropped: окно по картинке {geometry.crop_h} из {src_h} px")
    timings: dict[str, float] = {}
    cuts: list[float] = []
    shot_info: ShotDetection | None = None
    if detect_scenes:
        shot_info = detect_shots(analysis_video, start, end, pad=shot_pad)
        cuts = shot_info.cuts
        timings["scenes_sec"] = shot_info.wall_sec
        if shot_info.error:
            warnings.append(f"scene_detect_failed: {shot_info.error}")
    samples: list[Any] = []
    faces_dict: dict[str, Any] | None = None
    need_faces = detect_faces and geometry.needs_horizontal and mode in ("auto", "track")
    if need_faces:
        t0 = time.monotonic()
        try:
            from autoshorts.providers.faces import sample_faces

            track = sample_faces(analysis_video, start, end, detector=detector)
            samples = track.samples
            faces_dict = {
                "fps": round(track.fps, 2),
                "samples": len(track.samples),
                "with_faces": sum(1 for s in track.samples if s.faces),
                "frames_decoded": track.frames_decoded,
            }
        except Exception as exc:  # noqa: BLE001 - без лиц рефрейм всё равно возможен
            warnings.append(f"face_detect_failed: {type(exc).__name__}: {exc}"[:300])
        timings["faces_sec"] = time.monotonic() - t0
    plan = build_plan(
        geometry,
        start,
        end,
        face_samples=samples,
        scene_cuts=cuts,
        fps=fps,
        mode=mode,
        strategy=strategy,
        camera=camera,
    )
    plan.shot_detection = shot_info.to_dict() if shot_info else None
    plan.faces = faces_dict
    plan.warnings.extend(warnings)
    plan.timings.update(timings)
    return plan


def static_plan(src_w: int, src_h: int, start: float, end: float, mode: str = "center") -> ReframePlan:
    """План без анализа: одно окно по центру (или fit_blur) — для тестов и вертикальных источников."""
    geometry = CropGeometry.for_source(src_w, src_h)
    m: ReframeMode = mode if mode in REFRAME_MODES and mode != "track" else "center"  # type: ignore[assignment]
    if not geometry.needs_horizontal:
        m = "center"
    plan = ReframePlan(geometry, start, end, requested_mode=mode)
    plan.shots.append(ShotPlan(0, start, end, m, "static_plan", geometry.center_x()))
    return plan


def shot_changes_near(plan: ReframePlan, t: float, tol: float = 0.2) -> bool:
    return any(math.isclose(c, t, abs_tol=tol) for c in plan.scene_cuts)


__all__ = [
    "REFRAME_MODES",
    "TARGET_ASPECT",
    "CropGeometry",
    "ReframeMode",
    "ReframePlan",
    "ShotPlan",
    "StrategyConfig",
    "build_plan",
    "choose_strategy",
    "plan_reframe",
    "static_plan",
]
