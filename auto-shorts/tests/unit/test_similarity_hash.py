"""dHash 8 ключевых кадров для самопроверки своей библиотеки (library_similarity)."""

from __future__ import annotations

import random
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from autoshorts.pipeline import similarity as sim

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def test_dhash_bytes_known_patterns() -> None:
    decreasing = bytes(v for _ in range(8) for v in range(90, 0, -10))  # каждая строка убывает
    assert sim.dhash_bytes(decreasing) == (1 << 64) - 1
    increasing = bytes(v for _ in range(8) for v in range(0, 90, 10))
    assert sim.dhash_bytes(increasing) == 0
    with pytest.raises(ValueError):
        sim.dhash_bytes(b"\x00" * 10)


def test_hamming_and_similarity() -> None:
    assert sim.hamming(0b1010, 0b0110) == 2
    a = sim.VideoFingerprint((0x0F0F_0F0F_0F0F_0F0F,) * 8, 10.0)
    b = sim.VideoFingerprint((0x0F0F_0F0F_0F0F_0F0E,) * 8, 10.0)
    inv = sim.VideoFingerprint((~0x0F0F_0F0F_0F0F_0F0F & ((1 << 64) - 1),) * 8, 10.0)
    assert sim.similarity(a, a) == 1.0
    assert sim.similarity(a, b) == pytest.approx(1 - 1 / 64, abs=1e-4)
    assert sim.similarity(a, inv) == 0.0
    other = sim.VideoFingerprint(a.hashes, 10.0, method="other")
    assert sim.similarity(a, other) == 0.0


def test_fingerprint_serialization_roundtrip() -> None:
    fp = sim.VideoFingerprint(tuple(random.Random(1).getrandbits(64) for _ in range(8)), 21.4)
    hexes = fp.to_hex()
    assert all(len(h) == 16 for h in hexes)
    assert sim.VideoFingerprint.from_hex(hexes, 21.4) == fp
    assert sim.VideoFingerprint.from_json(fp.to_json()) == fp


def test_find_similar_threshold_and_order() -> None:
    rnd = random.Random(7)
    base = sim.VideoFingerprint(tuple(rnd.getrandbits(64) for _ in range(8)))
    near = sim.VideoFingerprint(tuple(h ^ 0b111 for h in base.hashes))  # 3 бита из 64
    far = sim.VideoFingerprint(tuple(rnd.getrandbits(64) for _ in range(8)))
    hits = sim.find_similar(base, [("far", far), ("near", near), ("same", base)], threshold=0.9)
    assert [k for k, _ in hits] == ["same", "near"]


def _picture(seed: int, shift: int = 0) -> Image.Image:
    rnd = random.Random(seed)
    im = Image.new("L", (180, 320), 128)
    d = ImageDraw.Draw(im)
    for _ in range(12):
        x, y = rnd.randrange(0, 160), rnd.randrange(0, 300)
        d.rectangle(
            (x + shift, y, x + shift + rnd.randrange(10, 60), y + rnd.randrange(10, 60)),
            fill=rnd.randrange(256),
        )
    return im


def test_dhash_image_stable_for_small_changes() -> None:
    a = sim.dhash_image(_picture(1))
    noisy = _picture(1).point(lambda v: min(255, v + 3))
    other = sim.dhash_image(_picture(99))
    assert sim.hamming(a, sim.dhash_image(noisy)) <= 4
    assert sim.hamming(a, other) >= 12


@pytest.mark.media
@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_fingerprint_video_detects_own_reencode(tmp_path: Path) -> None:
    def make(out: Path, src: str, crf: str) -> Path:
        subprocess.run(
            [
                "nice",
                "-n",
                "10",
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                src,
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                crf,
                "-g",
                "15",
                "-pix_fmt",
                "yuv420p",
                "-threads",
                "2",
                str(out),
            ],
            check=True,
            timeout=120,
        )
        return out

    a = make(tmp_path / "a.mp4", "testsrc2=s=180x320:r=30:d=4", "20")
    a2 = make(tmp_path / "a2.mp4", "testsrc2=s=180x320:r=30:d=4", "35")  # тот же ролик, хуже качество
    b = make(tmp_path / "b.mp4", "mandelbrot=s=180x320:r=30,trim=duration=4", "20")
    fa, fa2, fb = (sim.fingerprint_video(p) for p in (a, a2, b))
    assert len(fa.hashes) == 8 and fa.duration_sec == pytest.approx(4.0, abs=0.1)
    assert sim.similarity(fa, fa2) >= sim.DEFAULT_THRESHOLD
    assert sim.similarity(fa, fb) < sim.DEFAULT_THRESHOLD
    # длинный GOP (мало ключевых кадров) → равномерная выборка по всем кадрам
    long_gop = tmp_path / "lg.mp4"
    subprocess.run(
        [
            "nice",
            "-n",
            "10",
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=180x320:r=30:d=4",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-g",
            "300",
            "-pix_fmt",
            "yuv420p",
            "-threads",
            "2",
            str(long_gop),
        ],
        check=True,
        timeout=120,
    )
    assert sim.similarity(fa, sim.fingerprint_video(long_gop)) >= sim.DEFAULT_THRESHOLD
