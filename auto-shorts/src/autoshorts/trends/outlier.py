"""
Outlier-score против МЕДИАНЫ КАНАЛА, а не абсолютных просмотров (ТЗ §8) —
абсолютные просмотры не значат ничего, значит только превышение над собственной нормой канала.
Порог внимания 3-5x, "формат работает" — от 10x.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass


@dataclass
class OutlierResult:
    video_id: str
    views: int
    channel_median: float
    score: float
    tier: str  # "noise" | "attention" | "format_works"


def compute_outlier_score(video_views: int, channel_recent_views: list[int]) -> OutlierResult:
    """channel_recent_views — просмотры последних 20-30 роликов канала на сопоставимом возрасте (7 суток)."""
    if not channel_recent_views:
        median = 1.0  # избегаем деления на ноль на старте наблюдения за каналом
    else:
        median = statistics.median(channel_recent_views) or 1.0

    score = video_views / median

    if score >= 10:
        tier = "format_works"
    elif score >= 3:
        tier = "attention"
    else:
        tier = "noise"

    return OutlierResult(video_id="", views=video_views, channel_median=median, score=score, tier=tier)


def velocity_acceleration(vph_samples: list[tuple[float, float]]) -> float:
    """
    vph_samples: [(age_hours, views_per_hour), ...] минимум 2 точки.
    a = (VPH[t2] - VPH[t1]) / Δt. Положительно — тренд ещё растёт, отрицательно — выгорает.
    """
    if len(vph_samples) < 2:
        return 0.0
    (t1, vph1), (t2, vph2) = vph_samples[-2], vph_samples[-1]
    dt = t2 - t1
    if dt <= 0:
        return 0.0
    return (vph2 - vph1) / dt
