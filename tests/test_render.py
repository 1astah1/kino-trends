"""Проверка вычислительных хелперов рендера (без реального ffmpeg-вызова)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autoshorts.pipeline.render import (
    _banner_enable_expression, caption_margin_v_above_banner, fit_offer_fontsize,
)


def test_caption_margin_stays_above_banner_zone():
    """Регрессия на баг, пойманный на первом рендер-тесте: субтитры оказывались ВНУТРИ баннера."""
    banner_cfg = {"layer_2_offer_banner": {"enabled": True, "position": {"y1": 1150}}}
    margin_v = caption_margin_v_above_banner(1920, banner_cfg)
    caption_bottom_y = 1920 - margin_v
    assert caption_bottom_y < 1150, "субтитры должны заканчиваться выше верхней границы баннера"


def test_fit_offer_fontsize_shrinks_long_text():
    long_size = fit_offer_fontsize("umka.chat — бесплатный расклад", box_w_px=840, max_size_px=66)
    short_size = fit_offer_fontsize("umka.chat", box_w_px=840, max_size_px=66)
    assert long_size < short_size
    assert short_size == 66  # короткий текст остаётся на максимальном кегле
    assert long_size >= 40   # не ниже минимально читаемого порога


def test_banner_enable_expression_has_two_windows():
    timing = {
        "hook_silence_sec": 2.0,
        "impulse_window_pct": [0.40, 0.65],
        "impulse_duration_sec": [3, 5],
        "final_repeat": True,
        "final_repeat_sec": 2.5,
    }
    expr = _banner_enable_expression(timing, total_duration_sec=20.0)
    # должно быть ДВА непересекающихся окна: импульс (~8-13с) и финал (~17.5-20с)
    assert expr.count("between(") == 2


def test_banner_enable_expression_impulse_starts_after_hook():
    timing = {
        "hook_silence_sec": 2.0,
        "impulse_window_pct": [0.40, 0.65],
        "impulse_duration_sec": [3, 5],
        "final_repeat": False,
    }
    expr = _banner_enable_expression(timing, total_duration_sec=20.0)
    # импульс на 40% от 20с = 8.0, что больше hook_silence_sec=2.0
    assert "between(t,8.00" in expr
