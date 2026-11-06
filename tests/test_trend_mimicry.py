"""Проверка моста 'тренд-бриф -> свой сценарий' (легальный ответ на 'видео как трендовые')."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autoshorts.config import load_niche
from autoshorts.pipeline.trend_mimicry import (
    TrendBrief, build_scenario_from_brief, compute_scene_count_from_cuts, resolve_target_duration,
)


def _brief(**overrides) -> TrendBrief:
    base = dict(
        external_video_id="ext1", hook_type="incomplete_visual", hook_duration_sec=1.2,
        cuts_per_sec_first5=0.5, total_duration_bucket="15-30", text_on_screen_density=0.5,
        payoff_timestamp_ratio=0.8, loop_seam=True, cta_position="end", outlier_score=5.0,
    )
    base.update(overrides)
    return TrendBrief(**base)


def test_fast_paced_brief_produces_more_scenes_than_slow():
    niche = load_niche("taro")
    fast = build_scenario_from_brief(niche, "тема", _brief(cuts_per_sec_first5=1.5), None)
    slow = build_scenario_from_brief(niche, "тема", _brief(cuts_per_sec_first5=0.2), None)
    assert len(fast.scenes) > len(slow.scenes)


def test_duration_bucket_clamped_to_niche_range():
    """Не даём формату конкурента увести длительность далеко за пределы диапазона ниши (15-25с)."""
    niche = load_niche("taro")
    lo, hi = resolve_target_duration(niche, _brief(total_duration_bucket="45-60"))
    niche_lo, niche_hi = niche.duration_range
    assert lo >= niche_lo * 0.7
    assert hi <= niche_hi * 1.3


def test_missing_brief_fields_fall_back_to_niche_defaults():
    niche = load_niche("taro")
    empty_brief = _brief(cuts_per_sec_first5=None, total_duration_bucket=None, hook_type=None)
    scenario = build_scenario_from_brief(niche, "тема", empty_brief, None)
    assert len(scenario.scenes) == niche.visuals.get("scene_count", 6)


def test_scene_count_bounded_even_for_extreme_cuts():
    niche = load_niche("taro")
    huge = compute_scene_count_from_cuts(_brief(cuts_per_sec_first5=10.0), target_duration_sec=20.0,
                                          default_scene_count=6)
    assert huge <= 12  # верхний предел из compute_scene_count_from_cuts
