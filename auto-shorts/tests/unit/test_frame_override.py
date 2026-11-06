"""Оформление ролика поверх профиля канала (`author_layer.frame`)."""

from __future__ import annotations

from autoshorts.services.clipping import FRAME_DEFAULTS, apply_frame_override

CHANNEL = {"shape": "rounded", "size": 900, "ring_color": "#FF3CAC", "bg_set": "night", "accent": "#7FE3FF"}


def job(frame: dict | None) -> dict:
    return {"id": 7, "author_layer": {"mode": "llm_draft", "frame": frame} if frame is not None else {"mode": "llm_draft"}}


def test_no_override_keeps_channel_style() -> None:
    assert apply_frame_override(dict(CHANNEL), job(None)) == CHANNEL
    assert apply_frame_override(dict(CHANNEL), job({"shape": "channel"})) == CHANNEL


def test_plain_frame_disables_style() -> None:
    assert apply_frame_override(dict(CHANNEL), job({"shape": "none"})) == {"enabled": False}


def test_circle_with_tuning_over_channel() -> None:
    out = apply_frame_override(dict(CHANNEL), job({"shape": "circle", "size": 760, "ring_color": "#FFFFFF", "cy": 1000}))
    assert out["shape"] == "circle" and out["size"] == 760 and out["cy"] == 1000
    assert out["ring_color"] == "#FFFFFF" and out["bg_set"] == "night" and out["enabled"] is True


def test_rounded_aspect_sets_width_and_height() -> None:
    wide = apply_frame_override(dict(CHANNEL), job({"shape": "rounded", "size": 1000, "aspect": "landscape"}))
    assert (wide["width"], wide["height"]) == (1000, 750)
    tall = apply_frame_override(dict(CHANNEL), job({"shape": "rounded", "size": 900, "aspect": "portrait"}))
    assert (tall["width"], tall["height"]) == (675, 900)


def test_channel_without_style_gets_defaults() -> None:
    out = apply_frame_override({}, job({"shape": "circle"}))
    assert out["shape"] == "circle" and out["bg_set"] == FRAME_DEFAULTS["bg_set"] and out["enabled"] is True
    assert apply_frame_override({}, job({"shape": "channel"})) == {}
