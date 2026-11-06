"""Рефрейм: геометрия окна 9:16, SmoothedCameraman (EMA τ=0,6 с, скорость ≤ 0,35·crop_w/с),
стратегии шотов и строки sendcmd."""

from __future__ import annotations

import math

import pytest

from autoshorts.clipping.reframe import CropGeometry, StrategyConfig, build_plan, choose_strategy
from autoshorts.clipping.reframe.cameraman import CameraConfig, SmoothedCameraman, frame_times, sendcmd_lines
from autoshorts.clipping.reframe.shots import split_by_cuts
from autoshorts.providers.faces import FaceBox, FaceSample


def face(cx: float, w: float = 0.08, area_scale: float = 1.0, score: float = 0.9) -> FaceBox:
    ww = w * area_scale
    return FaceBox(cx - ww / 2, 0.3, ww, ww * 1.6, score)


def test_geometry_landscape_and_vertical() -> None:
    g = CropGeometry.for_source(1920, 1080)
    assert (g.crop_w, g.crop_h, g.y) == (608, 1080, 0)
    assert g.needs_horizontal and g.center_x() == 656
    assert g.x_for_center(0.0) == 0 and g.x_for_center(5000) == 1920 - 608
    v = CropGeometry.for_source(1080, 1920)
    assert not v.needs_horizontal and (v.crop_w, v.crop_h) == (1080, 1920)
    sq = CropGeometry.for_source(1080, 1080)
    assert sq.crop_h == 1080 and sq.crop_w == 608


def test_cameraman_converges_with_ema_and_speed_limit() -> None:
    cam = SmoothedCameraman(608, 1920)
    fps = 30.0
    times = frame_times(0.0, 9.0, fps)
    # цель прыгает с центра 400 на 1500 в момент 1 с; 1100 px при 213 px/с — это ≈ 5,2 с пути
    targets = [(t / 10, 400.0 if t < 10 else 1500.0) for t in range(0, 90)]
    xs = cam.track(targets, times)
    start = cam.left_for_center(400)
    goal = cam.left_for_center(1500)
    assert xs[0] == pytest.approx(start)
    max_step = max(abs(b - a) for a, b in zip(xs, xs[1:], strict=False))
    assert max_step <= 0.35 * 608 / fps + 1e-6
    assert xs[-1] == pytest.approx(goal, abs=15)
    assert all(b >= a - 1e-9 for a, b in zip(xs, xs[1:], strict=False))  # без перелёта


def test_cameraman_ema_time_constant_without_speed_limit() -> None:
    cfg = CameraConfig(max_speed_frac=100.0, deadband_frac=0.0)
    cam = SmoothedCameraman(608, 1920, cfg)
    times = frame_times(0.0, 3.0, 30.0)
    targets = [(0.0, 400.0), (0.5, 400.0), (0.501, 1000.0), (3.0, 1000.0)]
    xs = cam.track(targets, times)
    a, b = cam.left_for_center(400), cam.left_for_center(1000)
    assert xs[0] == pytest.approx(a)
    # через τ = 0,6 с после скачка цели пройдено ≈ 63% пути
    i0 = 15  # кадр 0,5 с
    i = i0 + int(round(0.6 * 30))
    frac = (xs[i] - a) / (b - a)
    assert frac == pytest.approx(1 - math.exp(-1.0), abs=0.06)


def test_cameraman_deadband_keeps_window_still() -> None:
    cam = SmoothedCameraman(608, 1920)
    times = frame_times(0.0, 4.0, 30.0)
    targets = [(t / 10, 960.0 + (8.0 if t % 2 else -8.0)) for t in range(40)]  # дрожание детектора ±8 px
    xs = cam.track(targets, times)
    assert max(xs) - min(xs) < 2.0


def test_cameraman_jumps_on_cut() -> None:
    cam = SmoothedCameraman(608, 1920)
    times = frame_times(0.0, 4.0, 30.0)
    targets = [(t / 10, 400.0 if t < 20 else 1600.0) for t in range(40)]
    xs = cam.track(targets, times, cuts=[2.0])
    i = times.index(next(t for t in times if t >= 2.0))
    assert xs[i] == pytest.approx(cam.left_for_center(1600))
    assert xs[i - 1] == pytest.approx(cam.left_for_center(400))


def test_sendcmd_lines_relative_even_and_sparse() -> None:
    pts = [(10.0 + k / 30, 100.0 + k * 0.5) for k in range(60)]
    lines = sendcmd_lines(pts, target="crop@c3", t_offset=10.0)
    assert lines[0] == "0.000 crop@c3 x 100;"
    xs = [int(line.split()[3].rstrip(";")) for line in lines]
    assert all(x % 2 == 0 for x in xs)
    assert all(b - a >= 2 for a, b in zip(xs, xs[1:], strict=False))
    assert len(lines) < len(pts)


@pytest.mark.parametrize(
    ("samples", "mode", "reason"),
    [
        ([FaceSample(t / 10, []) for t in range(30)], "fit_blur", "no_faces"),
        ([FaceSample(t / 10, [face(0.2), face(0.8)]) for t in range(30)], "fit_blur", "wide_group"),
        ([FaceSample(t / 10, [face(0.5)]) for t in range(30)], "center", "static_subject"),
        ([FaceSample(t / 10, [face(0.3 + 0.4 * t / 30)]) for t in range(30)], "track", "moving_subject"),
    ],
)
def test_choose_strategy(samples: list[FaceSample], mode: str, reason: str) -> None:
    g = CropGeometry.for_source(1920, 1080)
    m, r, stats, targets = choose_strategy(samples, g)
    assert (m, r) == (mode, reason)
    assert stats["n"] == len(samples)


def test_small_background_face_does_not_make_group() -> None:
    g = CropGeometry.for_source(1920, 1080)
    samples = [FaceSample(t / 10, [face(0.5), face(0.9, area_scale=0.3)]) for t in range(30)]
    m, r, _, _ = choose_strategy(samples, g)
    assert m == "center"


def test_no_face_mode_configurable() -> None:
    g = CropGeometry.for_source(1920, 1080)
    m, _, _, _ = choose_strategy([FaceSample(0.0, [])], g, StrategyConfig(no_face_mode="center"))
    assert m == "center"


def test_build_plan_per_shot_and_operator_override() -> None:
    g = CropGeometry.for_source(1920, 1080)
    samples = [FaceSample(t / 10, [face(0.5)] if t < 50 else []) for t in range(100)]
    plan = build_plan(g, 0.0, 10.0, face_samples=samples, scene_cuts=[5.0])
    assert [s.mode for s in plan.shots] == ["center", "fit_blur"]
    assert plan.scene_cuts == [5.0]
    assert plan.mode_at(2.0) == "center" and plan.mode_at(7.0) == "fit_blur"
    forced = build_plan(g, 0.0, 10.0, face_samples=samples, scene_cuts=[5.0], mode="fit_blur")
    assert {s.mode for s in forced.shots} == {"fit_blur"}
    assert forced.shots[0].reason == "operator:fit_blur"
    # track без лиц превращается в неподвижное окно
    tr = build_plan(g, 0.0, 10.0, face_samples=[FaceSample(t / 10, []) for t in range(100)], mode="track")
    assert tr.shots[0].mode == "center" and tr.shots[0].reason == "track_without_faces"


def test_plan_pieces_and_sendcmd() -> None:
    g = CropGeometry.for_source(1920, 1080)
    samples = [FaceSample(t / 10, [face(0.2 + 0.6 * t / 100)]) for t in range(100)]
    plan = build_plan(g, 0.0, 10.0, face_samples=samples, scene_cuts=[4.0])
    pieces = plan.pieces(2.0, 8.0)
    assert [(round(a, 3), round(b, 3)) for a, b, _ in pieces] == [(2.0, 4.0), (4.0, 8.0)]
    lines = plan.sendcmd_for(4.0, 8.0, target="crop@c1")
    assert lines[0].startswith("0.000 crop@c1 x ")
    assert float(lines[-1].split()[0]) < 4.0
    xs = [int(line.split()[3].rstrip(";")) for line in lines]
    assert xs == sorted(xs)  # лицо едет вправо — окно тоже


def test_vertical_source_is_center_without_faces() -> None:
    g = CropGeometry.for_source(1080, 1920)
    plan = build_plan(g, 0.0, 5.0, face_samples=[FaceSample(0.0, [face(0.2)])])
    assert plan.shots[0].mode == "center" and plan.shots[0].reason == "vertical_source"


def test_split_by_cuts_merges_tiny_shots() -> None:
    assert split_by_cuts(0.0, 10.0, [3.0, 3.2, 7.0]) == [(0.0, 3.2), (3.2, 7.0), (7.0, 10.0)]
    assert split_by_cuts(0.0, 10.0, [0.1, 9.95]) == [(0.0, 10.0)]
    assert split_by_cuts(0.0, 10.0, []) == [(0.0, 10.0)]
