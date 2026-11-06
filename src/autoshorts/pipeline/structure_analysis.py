"""
Извлечение СТРУКТУРЫ чужого залетевшего ролика — темп монтажа, число склеек, длительность.
Это не хранение и не редистрибуция контента: файл скачивается временно, анализируется,
удаляется. В БД (trend_briefs) остаются только числа, ни одного байта видео.

Именно так работает outlier-движок из ТЗ §8: "разбор чужого ролика в бриф... PySceneDetect,
а не спрашивать у LLM — в этих числах модели систематически врут". Результат идёт в
trend_mimicry.build_scenario_from_brief — на выходе СВОЙ сценарий, не чужой файл.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from autoshorts.pipeline.ingest import SceneCut, detect_scenes, download_source_clip

logger = logging.getLogger("autoshorts.structure_analysis")


@dataclass
class StructuralMetrics:
    external_video_id: str
    total_duration_sec: float
    cuts_per_sec_first5: float
    scene_count: int
    total_duration_bucket: str
    loop_seam_guess: bool  # эвристика: первая и последняя сцена визуально близки по длительности


def _duration_bucket(total_sec: float) -> str:
    if total_sec <= 15:
        return "0-15"
    if total_sec <= 30:
        return "15-30"
    if total_sec <= 45:
        return "30-45"
    return "45-60"


def compute_cuts_per_sec_first5(scenes: list[SceneCut]) -> float:
    """Считает число склеек в первые 5 секунд ролика — темп хука, который переносится в свой ролик."""
    cuts_in_window = sum(1 for s in scenes if s.start_sec > 0 and s.start_sec <= 5.0)
    return round(cuts_in_window / 5.0, 3)


def analyze_and_discard(source_url: str, external_video_id: str, tmp_dir: Path) -> StructuralMetrics:
    """
    Полный цикл: скачать во временную директорию -> посчитать метрики -> удалить файл.
    Файл НИКОГДА не переживает эту функцию — см. finally.

    ТРЕБУЕТ живой сети (yt-dlp), не тестировано в этой песочнице — см. README.
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    clip = None
    try:
        clip = download_source_clip(source_url, tmp_dir)
        scenes = detect_scenes(clip.local_path)
        cuts_first5 = compute_cuts_per_sec_first5(scenes)

        return StructuralMetrics(
            external_video_id=external_video_id,
            total_duration_sec=clip.original_duration_sec,
            cuts_per_sec_first5=cuts_first5,
            scene_count=len(scenes),
            total_duration_bucket=_duration_bucket(clip.original_duration_sec),
            loop_seam_guess=len(scenes) >= 2 and abs(scenes[0].duration_sec - scenes[-1].duration_sec) < 0.5,
        )
    finally:
        # Файл всегда удаляется — анализ структуры, не архивация чужого контента.
        if clip is not None and clip.local_path.exists():
            clip.local_path.unlink()
            logger.info(f"Временный файл {clip.local_path} удалён после анализа структуры")
