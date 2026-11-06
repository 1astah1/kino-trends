"""
Beat-sync монтаж под трендовый звук. Применяется ИЗБИРАТЕЛЬНО — см. audio_strategy.py:
на YouTube (основная площадка) с марта 2026 действует original sound bonus, трендовый звук
там скорее вредит малым каналам. Beat-sync актуален для VK-ветки (трек §5 ТЗ, второй контур),
где VK-тренды идут с лагом 1-3 недели за TikTok и своего трекера трендов у VK нет вообще
(audio.get/search/getPopular закрыты для сторонних приложений с 2016, лазейки добиты 09.02.2026).

librosa.beat.beat_track — стандартный инструмент MIR-пайплайнов для onset/beat detection.
Смена сцены НА бите выглядит профессионально смонтированной, не требует ручной раскадровки.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np


@dataclass
class BeatMap:
    tempo_bpm: float
    beat_times_sec: list[float]
    duration_sec: float


def analyze_beats(audio_path: Path) -> BeatMap:
    y, sr = librosa.load(str(audio_path), sr=None, mono=True)
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    duration = librosa.get_duration(y=y, sr=sr)

    # librosa в некоторых версиях возвращает tempo как np.ndarray с одним элементом
    tempo_val = float(tempo) if np.isscalar(tempo) else float(tempo[0])

    return BeatMap(tempo_bpm=round(tempo_val, 1), beat_times_sec=beat_times, duration_sec=duration)


def align_scene_cuts_to_beats(scene_count: int, beat_map: BeatMap,
                               min_scene_sec: float = 0.8, max_scene_sec: float = 4.0) -> list[float]:
    """
    Возвращает список длительностей сцен, выровненных на ближайшие биты — вместо равномерного
    деления. Отбрасывает биты, дающие сцену короче min_scene_sec (слишком частые склейки читаются
    как брак, не как монтаж) или длиннее max_scene_sec (редкие биты — берём каждый N-й).
    """
    if not beat_map.beat_times_sec or scene_count <= 0:
        return [beat_map.duration_sec / max(scene_count, 1)] * max(scene_count, 1)

    beats = beat_map.beat_times_sec
    total = beat_map.duration_sec

    # Целевая длительность сцены при равномерном делении — ориентир для выбора шага по битам
    target_scene_sec = total / scene_count
    step = max(1, round(target_scene_sec / (total / len(beats)))) if len(beats) > 1 else 1

    cut_points = [0.0] + beats[step - 1::step] + [total]
    cut_points = sorted(set(round(t, 3) for t in cut_points))

    # ВАЖНО: сумма durations должна ТОЧНО равняться total — ни одна секунда аудио не должна
    # молча теряться (был баг: короткий финальный интервал отбрасывался вместо слияния —
    # поймано тестом test_align_scene_cuts_sums_to_total_duration).
    durations: list[float] = []
    prev = 0.0
    for t in cut_points[1:]:
        d = t - prev
        if d < min_scene_sec and durations:
            # Слишком короткий интервал — не выбрасываем время, присоединяем к предыдущей сцене.
            durations[-1] += d
            prev = t
            continue
        if d < min_scene_sec and not durations:
            # Первый интервал короткий и присоединить не к чему — оставляем как есть,
            # иначе теряем самое начало ролика.
            durations.append(d)
            prev = t
            continue
        if d > max_scene_sec:
            # Дробим слишком длинный интервал поровну — сумма частей равна d, время не теряется.
            n_parts = max(1, round(d / max_scene_sec))
            for _ in range(n_parts):
                durations.append(d / n_parts)
            prev = t
            continue
        durations.append(d)
        prev = t

    if not durations:
        return [total / scene_count] * scene_count
    return durations
