"""Плавная «камера» для кадра 9:16 внутри горизонтального источника (docs/ARCHITECTURE.md §6.3.6).

`SmoothedCameraman` превращает рваные центры лиц (10 измерений в секунду, пропуски, дрожание
детектора) в гладкую траекторию левого края окна кропа на каждый выходной кадр:

* EMA с постоянной времени τ = 0,6 с: `x += (1 − e^(−dt/τ)) · (цель − x)`;
* ограничение скорости: не быстрее 0,35 · crop_w в секунду (камера не «дёргается» за головой);
* мёртвая зона: цель меняется, только если сместилась больше чем на `deadband` (3% crop_w),
  чтобы окно не дрожало на месте;
* на склейке камера не едет, а сразу встаёт на цель нового шота.

Выход — список `(t, x)` и файл команд `sendcmd` для `crop@cN` (время относительно начала сегмента).
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

EMA_TAU_SEC = 0.6
MAX_SPEED_FRAC = 0.35  # доля crop_w в секунду
DEADBAND_FRAC = 0.03
SENDCMD_MIN_STEP_PX = 2


@dataclass(frozen=True, slots=True)
class CameraConfig:
    tau: float = EMA_TAU_SEC
    max_speed_frac: float = MAX_SPEED_FRAC
    deadband_frac: float = DEADBAND_FRAC
    warmup_sec: float = 0.25  # стартовая позиция шота — медиана целей первых warmup_sec (≈3 измерения)


def _interp_targets(samples: Sequence[tuple[float, float]], t: float) -> float | None:
    """Цель в момент t: линейная интерполяция между соседними измерениями, за краями — удержание."""
    if not samples:
        return None
    times = [s[0] for s in samples]
    i = bisect_right(times, t)
    if i <= 0:
        return samples[0][1]
    if i >= len(samples):
        return samples[-1][1]
    (t0, x0), (t1, x1) = samples[i - 1], samples[i]
    if t1 - t0 <= 1e-9:
        return x1
    k = (t - t0) / (t1 - t0)
    return x0 + (x1 - x0) * k


class SmoothedCameraman:
    """Гладкая траектория окна шириной `crop_w` в кадре шириной `frame_w` (пиксели источника)."""

    def __init__(self, crop_w: float, frame_w: float, config: CameraConfig | None = None) -> None:
        if crop_w <= 0 or frame_w <= 0:
            raise ValueError("crop_w and frame_w must be positive")
        self.crop_w = float(min(crop_w, frame_w))
        self.frame_w = float(frame_w)
        self.cfg = config or CameraConfig()

    @property
    def max_x(self) -> float:
        return self.frame_w - self.crop_w

    def clamp_x(self, x: float) -> float:
        return min(max(0.0, x), self.max_x)

    def left_for_center(self, cx: float) -> float:
        return self.clamp_x(cx - self.crop_w / 2)

    def track(
        self,
        targets: Sequence[tuple[float, float]],
        times: Sequence[float],
        *,
        cuts: Iterable[float] = (),
        fallback_cx: float | None = None,
    ) -> list[float]:
        """Левый край окна для каждого момента `times` (по возрастанию).

        `targets` — `(t, cx)` центров объекта в пикселях источника (пропуски просто отсутствуют).
        `cuts` — склейки: после них позиция сразу берётся из цели нового шота.
        """
        if not times:
            return []
        tg = sorted((float(t), float(c)) for t, c in targets)
        cut_list = sorted(float(c) for c in cuts)
        fb = self.frame_w / 2 if fallback_cx is None else float(fallback_cx)
        speed = self.cfg.max_speed_frac * self.crop_w
        dead = self.cfg.deadband_frac * self.crop_w

        def shot_bounds(t: float) -> tuple[float, float]:
            i = bisect_right(cut_list, t)
            lo = cut_list[i - 1] if i > 0 else -math.inf
            hi = cut_list[i] if i < len(cut_list) else math.inf
            return lo, hi

        def shot_targets(lo: float, hi: float) -> list[tuple[float, float]]:
            return [s for s in tg if lo - 1e-6 <= s[0] < hi]

        def start_x(t_start: float, st: list[tuple[float, float]]) -> float:
            win = sorted(c for t, c in st if t_start - 1e-6 <= t <= t_start + self.cfg.warmup_sec)
            if win:
                return self.left_for_center(win[len(win) // 2])
            v = _interp_targets(st, t_start)
            return self.left_for_center(fb if v is None else v)

        xs: list[float] = []
        prev_t = float(times[0])
        lo, hi = shot_bounds(prev_t)
        st = shot_targets(lo, hi)
        x = start_x(prev_t, st)
        anchor = x
        for t in times:
            t = float(t)
            if t >= hi:  # склейка: прыжок на цель нового шота, без сглаживания
                lo, hi = shot_bounds(t)
                st = shot_targets(lo, hi)
                x = start_x(t, st)
                anchor = x
                prev_t = t
                xs.append(x)
                continue
            dt = max(0.0, t - prev_t)
            prev_t = t
            cx = _interp_targets(st, t)
            desired = self.left_for_center(fb if cx is None else cx)
            if abs(desired - anchor) > dead:
                anchor = desired
            if dt > 0:
                alpha = 1.0 - math.exp(-dt / self.cfg.tau)
                step = alpha * (anchor - x)
                lim = speed * dt
                step = max(-lim, min(lim, step))
                x = self.clamp_x(x + step)
            xs.append(x)
        return xs


def frame_times(start: float, end: float, fps: float) -> list[float]:
    """Моменты выходных кадров на `[start, end)` с шагом 1/fps."""
    n = max(0, int(math.floor((end - start) * fps + 1e-6)))
    return [start + i / fps for i in range(n)]


def sendcmd_lines(
    points: Sequence[tuple[float, float]],
    *,
    target: str = "crop@c0",
    t_offset: float = 0.0,
    min_step_px: int = SENDCMD_MIN_STEP_PX,
    even: bool = True,
) -> list[str]:
    """`(t, x)` → строки `sendcmd`: «`0.033 crop@c0 x 412;`». Время сдвигается на `−t_offset`.

    Команда пишется, только если x изменился хотя бы на `min_step_px` (иначе файл на 30 строк в
    секунду без пользы). Для yuv420p x округляется до чётного.
    """
    lines: list[str] = []
    last: int | None = None
    for t, x in points:
        xi = int(round(x))
        if even:
            xi -= xi % 2
        if last is not None and abs(xi - last) < min_step_px:
            continue
        rel = max(0.0, float(t) - t_offset)
        lines.append(f"{rel:.3f} {target} x {xi};")
        last = xi
    return lines


def write_sendcmd(path: str | Path, lines: Sequence[str]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="ascii")
    return p


__all__ = [
    "DEADBAND_FRAC",
    "EMA_TAU_SEC",
    "MAX_SPEED_FRAC",
    "CameraConfig",
    "SmoothedCameraman",
    "frame_times",
    "sendcmd_lines",
    "write_sendcmd",
]
