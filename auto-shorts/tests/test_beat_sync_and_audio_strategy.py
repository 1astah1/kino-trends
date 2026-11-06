"""Проверка beat-sync (VK-ветка) и платформенной развилки по звуку."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autoshorts.pipeline.audio_strategy import resolve_audio_strategy
from autoshorts.pipeline.beat_sync import BeatMap, align_scene_cuts_to_beats


def test_youtube_small_channel_defaults_to_own_voiceover():
    """Ключевая находка рисёрча: original sound bonus для каналов <50k на YouTube."""
    s = resolve_audio_strategy("youtube", channel_subscriber_count=5000)
    assert s.use_own_voiceover is True
    assert s.use_trending_sound is False


def test_youtube_large_channel_can_use_trending():
    s = resolve_audio_strategy("youtube", channel_subscriber_count=60000)
    assert s.use_trending_sound is True


def test_vk_always_prefers_trending_over_own_voiceover():
    """VK: своего трендового трекера нет, сигнал идёт с TikTok с лагом 1-3 недели."""
    s = resolve_audio_strategy("vk", channel_subscriber_count=100)
    assert s.use_trending_sound is True
    assert s.beat_sync_enabled is True


def test_align_scene_cuts_falls_back_to_even_division_without_beats():
    """Деградация: если биты не найдены (напр. плохой аудиосигнал), не падаем — делим поровну."""
    empty_beat_map = BeatMap(tempo_bpm=0.0, beat_times_sec=[], duration_sec=18.0)
    durations = align_scene_cuts_to_beats(scene_count=6, beat_map=empty_beat_map)
    assert len(durations) == 6
    assert abs(sum(durations) - 18.0) < 0.01


def test_align_scene_cuts_sums_to_total_duration():
    """Регрессия: короткий финальный интервал раньше молча пропадал из суммы вместо слияния."""
    beat_map = BeatMap(tempo_bpm=120.0, beat_times_sec=[0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0], duration_sec=4.5)
    durations = align_scene_cuts_to_beats(scene_count=4, beat_map=beat_map)
    assert abs(sum(durations) - 4.5) < 0.01  # сумма ДОЛЖНА точно совпадать — ни одна секунда не теряется


def test_align_scene_cuts_never_loses_duration_on_real_beat_map():
    """То же на плотной сетке битов, ближе к реальному сигналу (интервал ~0.5с, 38 битов на 20с)."""
    beats = [round(i * 0.51, 2) for i in range(38)]
    beat_map = BeatMap(tempo_bpm=117.5, beat_times_sec=beats, duration_sec=20.0)
    for scene_count in (3, 6, 9, 12):
        durations = align_scene_cuts_to_beats(scene_count=scene_count, beat_map=beat_map)
        assert abs(sum(durations) - 20.0) < 0.01, f"потеря длительности при scene_count={scene_count}"
