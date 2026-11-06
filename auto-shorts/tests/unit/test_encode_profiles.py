"""Профили кодирования финала: CRF 19–20, VBV 12M/24M, GOP в половину fps, AAC 192k 48 кГц."""

from __future__ import annotations

import pytest

from autoshorts.pipeline import encode_profiles as ep
from autoshorts.pipeline import probe


def value(args: list[str], key: str) -> str:
    return args[args.index(key) + 1]


def test_final_profile_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ep.NVENC_ENV, raising=False)
    monkeypatch.delenv(probe.THREADS_ENV, raising=False)
    args = ep.final_profile().output_args(fps=30)
    assert value(args, "-c:v") == "libx264" and value(args, "-preset") == "veryfast"
    assert value(args, "-crf") == "20"
    assert value(args, "-maxrate") == "12M" and value(args, "-bufsize") == "24M"
    assert value(args, "-g") == "15" and value(args, "-keyint_min") == "15" and value(args, "-bf") == "2"
    assert value(args, "-pix_fmt") == "yuv420p" and value(args, "-profile:v") == "high"
    assert value(args, "-colorspace") == "bt709" and value(args, "-color_range") == "tv"
    assert value(args, "-c:a") == "aac" and value(args, "-b:a") == "192k"
    assert value(args, "-ar") == "48000" and value(args, "-ac") == "2"
    assert value(args, "-movflags") == "+faststart"
    assert value(args, "-threads") == "2"


@pytest.mark.parametrize(("crf", "expected"), [(23, 20), (20, 20), (19, 19), (10, 19)])
def test_final_crf_is_clamped_to_research_range(crf: int, expected: int) -> None:
    assert ep.final_profile(crf=crf, use_nvenc=False).crf == expected


@pytest.mark.parametrize(("fps", "g"), [(30, 15), (60, 30), (24, 12), (29.97, 15)])
def test_keyint_is_half_fps(fps: float, g: int) -> None:
    assert value(ep.final_profile(use_nvenc=False).video_args(fps=fps), "-g") == str(g)


def test_nvenc_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ep.NVENC_ENV, "1")
    prof = ep.final_profile()
    args = prof.output_args(fps=30)
    assert prof.encoder == ep.VideoEncoder.H264_NVENC
    assert value(args, "-c:v") == "h264_nvenc" and value(args, "-preset") == "p5"
    assert value(args, "-rc") == "vbr" and value(args, "-cq") == "21" and "-crf" not in args
    assert value(args, "-maxrate") == "12M" and "-sc_threshold" not in args
    assert not ep.final_profile(use_nvenc=False).encoder == ep.VideoEncoder.H264_NVENC


@pytest.mark.parametrize(("env", "expected"), [("3", "3"), ("0", "1"), ("99", "8"), ("abc", "2"), ("", "2")])
def test_threads_cap_from_env(monkeypatch: pytest.MonkeyPatch, env: str, expected: str) -> None:
    monkeypatch.setenv(probe.THREADS_ENV, env)
    assert value(ep.final_profile(use_nvenc=False).video_args(), "-threads") == expected


def test_with_threads_and_no_audio() -> None:
    prof = ep.with_threads(ep.final_profile(use_nvenc=False), 4)
    args = prof.output_args(with_audio=False)
    assert value(args, "-threads") == "4" and "-an" in args and "-c:a" not in args


def test_preview_profile_is_lighter() -> None:
    prev = ep.preview_profile()
    args = prev.output_args(fps=30)
    assert int(value(args, "-crf")) > ep.final_profile(use_nvenc=False).crf
    assert value(args, "-g") == "30" and value(args, "-b:a") == "96k"


def test_niced_command_adds_global_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(probe.THREADS_ENV, raising=False)
    cmd = probe.build_niced_cmd(["ffmpeg", "-i", "in.mp4", "-c:v", "libx264", "out.mp4"])
    assert cmd[:3] == ["nice", "-n", "10"] and cmd[3] == "ffmpeg"
    assert "-nostdin" in cmd and cmd[cmd.index("-filter_complex_threads") + 1] == "1"
    assert cmd[-3:] == ["-threads", "2", "out.mp4"]
    own = probe.build_niced_cmd(["ffmpeg", "-i", "a", "-threads", "1", "b"])
    assert own.count("-threads") == 1
    fp = probe.build_niced_cmd(["ffprobe", "-v", "error", "x"])
    assert fp == ["nice", "-n", "10", "ffprobe", "-v", "error", "x"]
