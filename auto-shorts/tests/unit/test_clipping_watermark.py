"""Foreign watermark check on synthetic frames (numpy + OpenCV, no ffmpeg)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from autoshorts.clipping.watermark_check import (  # noqa: E402
    Logo,
    check_frames,
    detect_static_overlay,
    load_logos,
)

H, W = 540, 960


def make_logo() -> np.ndarray:
    """BGRA logo with a transparent margin: dark plate, red circle, white bars."""
    img = np.zeros((90, 260, 4), dtype=np.uint8)
    cv2.rectangle(img, (5, 5), (254, 84), (25, 25, 25, 235), -1)
    cv2.circle(img, (45, 45), 30, (80, 40, 255, 255), -1)
    cv2.rectangle(img, (95, 25), (240, 38), (255, 255, 255, 255), -1)
    cv2.rectangle(img, (95, 52), (200, 64), (190, 190, 190, 255), -1)
    return img


def moving_frames(n: int = 12, seed: int = 7) -> list[np.ndarray]:
    """Frames with large moving shapes (real motion between samples)."""
    rng = np.random.default_rng(seed)
    frames = []
    for i in range(n):
        f = np.full((H, W, 3), 60 + 10 * (i % 5), dtype=np.uint8)
        for _ in range(6):
            x, y = int(rng.integers(0, W - 200)), int(rng.integers(0, H - 150))
            color = tuple(int(c) for c in rng.integers(0, 255, 3))
            cv2.rectangle(
                f, (x, y), (x + int(rng.integers(60, 200)), y + int(rng.integers(40, 150))), color, -1
            )
        cv2.GaussianBlur(f, (0, 0), 1.2, dst=f)
        frames.append(f)
    return frames


def paste(frame: np.ndarray, logo_bgra: np.ndarray, x: int, y: int, height: int) -> np.ndarray:
    lh, lw = logo_bgra.shape[:2]
    w = int(round(lw * height / lh))
    lg = cv2.resize(logo_bgra, (w, height), interpolation=cv2.INTER_AREA)
    a = lg[:, :, 3:4].astype(np.float32) / 255.0
    roi = frame[y : y + height, x : x + w].astype(np.float32)
    frame[y : y + height, x : x + w] = (roi * (1 - a) + lg[:, :, :3].astype(np.float32) * a).astype(np.uint8)
    return frame


@pytest.fixture()
def logos_dir(tmp_path: Path) -> Path:
    d = tmp_path / "logos"
    d.mkdir()
    cv2.imwrite(str(d / "tiktok_like.png"), make_logo())
    (d / "_ignored.png").write_bytes(b"not an image")
    (d / "README.md").write_text("docs")
    return d


def test_load_logos_crops_alpha(logos_dir: Path) -> None:
    logos = load_logos(logos_dir)
    assert [lg.name for lg in logos] == ["tiktok_like"]
    assert logos[0].image.shape == (80, 250)  # transparent 5 px margin removed
    assert logos[0].mask is None  # >= 90% opaque after the crop -> fast unmasked match
    assert load_logos(logos_dir.parent / "missing") == []


def test_known_logo_is_flagged(logos_dir: Path) -> None:
    logos = load_logos(logos_dir)
    frames = [(float(i), paste(f, make_logo(), W - 150, 20, 40)) for i, f in enumerate(moving_frames())]
    rep = check_frames(frames, logos)
    assert rep.suspect and rep.severity == "warning"
    assert rep.frames_hit["tiktok_like"] == 12
    top = max(rep.hits, key=lambda h: h.score)
    assert top.score >= 0.8
    assert abs(top.x - (W - 150)) <= 4 and abs(top.y - 20) <= 4
    d = rep.to_dict()
    assert d["suspect"] is True and len(d["hits"]) <= 20


def test_clean_frames_pass(logos_dir: Path) -> None:
    rep = check_frames([(float(i), f) for i, f in enumerate(moving_frames())], load_logos(logos_dir))
    assert not rep.suspect and rep.severity == "ok"
    assert rep.best["tiktok_like"] < 0.8


def test_min_frames_threshold(logos_dir: Path) -> None:
    frames = [(float(i), f) for i, f in enumerate(moving_frames())]
    frames[3] = (3.0, paste(frames[3][1], make_logo(), 30, 30, 40))
    logos = load_logos(logos_dir)
    assert check_frames(frames, logos, min_frames=1).suspect
    assert not check_frames(frames, logos, min_frames=2).suspect


def test_static_overlay_hint_without_logo_library() -> None:
    frames = [paste(f, make_logo(), 20, H - 70, 50) for f in moving_frames()]
    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
    ov = detect_static_overlay(grays)
    assert ov and ov["flagged"] and ov["corner"] == "bottom_left"
    rep = check_frames([(float(i), f) for i, f in enumerate(frames)], [])
    assert not rep.suspect and rep.severity == "hint"
    assert any("config/logos is empty" in n for n in rep.notes)


def test_static_overlay_ignores_letterbox_and_static_video() -> None:
    frames = moving_frames()
    for f in frames:
        f[:60] = 0
        f[-60:] = 0
    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
    ov = detect_static_overlay(grays)
    assert ov and ov["checked"] and not ov["flagged"]
    still = [grays[0].copy() for _ in range(12)]
    assert detect_static_overlay(still) == {"checked": False, "reason": "low_motion", "motion": 0.0}


def test_flat_template_is_skipped() -> None:
    flat = Logo("flat", np.full((40, 120), 200, dtype=np.uint8), None)
    rep = check_frames([(0.0, moving_frames(1)[0])], [flat], static_hint=False)
    assert not rep.suspect and "flat" not in rep.best
