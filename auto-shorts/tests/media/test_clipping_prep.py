"""Source preparation on tiny lavfi sources: proxy, audio tracks, peaks, storyboard, watermark check,
the local ingest manifest, and (when the models and the benchmark sample exist) real GigaAM ASR.

Run: pytest -m media tests/media/test_clipping_prep.py   (ffmpeg, a few seconds of CPU at nice 10)
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.media

if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
    pytest.skip("ffmpeg/ffprobe not installed", allow_module_level=True)

from autoshorts.clipping.source_ingest import PreparedSource, ingest_local, prepare_source  # noqa: E402
from autoshorts.pipeline.peaks import compute_peaks  # noqa: E402
from autoshorts.pipeline.proxy import make_proxy, probe_media  # noqa: E402
from autoshorts.pipeline.storyboard import make_storyboard  # noqa: E402
from autoshorts.providers.asr.base import wav_info  # noqa: E402
from autoshorts.providers.asr.mock import MockAsrProvider  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OWN = {"rights_type": "own", "owner": "Umka", "proof_note": "own test footage"}


def ffmpeg(*args: str) -> None:
    cmd = ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-threads", "2", *args]
    subprocess.run(cmd, check=True)


def lavfi_source(
    path: Path, *, size: str = "640x360", rate: int = 30, seconds: float = 6.0, audio: bool = True
) -> Path:
    args = ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}:sample_rate=44100"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    args += ["-c:a", "aac", "-shortest"] if audio else ["-an"]
    ffmpeg(*args, "-threads", "2", str(path))
    return path


def keyframe_times(path: Path) -> list[float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,flags",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return [float(line.split(",")[0]) for line in out.splitlines() if "K" in line.split(",")[1]]


@pytest.fixture(scope="module")
def landscape(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return lavfi_source(tmp_path_factory.mktemp("src") / "land.mp4")


def test_proxy_audio_peaks_storyboard(landscape: Path, tmp_path: Path) -> None:
    res = make_proxy(landscape, tmp_path)
    assert res.sha256 and len(res.sha256) == 64
    assert res.probe.width == 640 and res.probe.has_audio
    # proxy: short side min(540, source) = 360, GOP 1 s, H.264 + AAC
    pp = probe_media(res.proxy_path)
    assert (pp.width, pp.height) == (640, 360) and pp.video_codec == "h264" and pp.has_audio
    kf = keyframe_times(res.proxy_path)
    assert kf[:4] == pytest.approx([0.0, 1.0, 2.0, 3.0], abs=0.05)
    # audio tracks
    assert probe_media(res.audio_path).audio_codec == "aac"
    info = wav_info(res.audio16k_path)
    assert info and (info.sample_rate, info.channels, info.sample_width) == (16000, 1, 2)
    assert info.duration == pytest.approx(6.0, abs=0.1)
    assert not list(tmp_path.glob("*.part*")), "temporary outputs must be renamed"
    # peaks: 2000 points per minute
    pk = compute_peaks(res.audio16k_path)
    assert pk["samples_per_point"] == 480 and pk["length"] == pytest.approx(200, abs=2)
    assert 0.05 < pk["max"] <= 1.0 and len(pk["peaks"]) == 1
    pk_m4a = compute_peaks(res.audio_path)  # ffmpeg pipe path
    assert pk_m4a["length"] == pytest.approx(pk["length"], abs=3)
    # storyboard: one tile per 2 s
    sb = make_storyboard(res.proxy_path, tmp_path, duration=res.probe.duration)
    assert sb.to_dict() | {"sheets": None} == {
        "sheets": None, "cols": 10, "rows": 10, "tile_w": 160, "tile_h": 90, "interval_sec": 2.0, "count": 3,
    }  # fmt: skip
    assert sb.tile_at(5.0) == (0, 320, 0)


def test_vertical_60fps_is_capped(tmp_path: Path) -> None:
    src = lavfi_source(tmp_path / "vert.mp4", size="720x1280", rate=60, seconds=3.0, audio=False)
    res = make_proxy(src, tmp_path / "out")
    pp = probe_media(res.proxy_path)
    assert (pp.width, pp.height) == (540, 960)
    assert pp.fps == pytest.approx(30.0, abs=0.1)
    assert res.audio_path is None and res.audio16k_path is None


def test_url_mode_separate_audio(tmp_path: Path) -> None:
    video = lavfi_source(tmp_path / "v.mp4", seconds=4.0, audio=False)
    audio = tmp_path / "a.m4a"
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=500:duration=4", "-c:a", "aac", str(audio))
    res = make_proxy(video, tmp_path / "out", audio_src=audio)
    assert probe_media(res.proxy_path).has_audio
    assert wav_info(res.audio16k_path).duration == pytest.approx(4.0, abs=0.1)


def test_prepare_source_flags_foreign_logo(tmp_path: Path) -> None:
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    logo = np.zeros((80, 240, 4), dtype=np.uint8)
    cv2.rectangle(logo, (0, 0), (239, 79), (30, 30, 30, 255), -1)
    cv2.circle(logo, (40, 40), 28, (90, 40, 250, 255), -1)
    cv2.rectangle(logo, (85, 22), (225, 34), (255, 255, 255, 255), -1)
    cv2.rectangle(logo, (85, 46), (190, 58), (200, 200, 200, 255), -1)
    logos = tmp_path / "logos"
    logos.mkdir()
    cv2.imwrite(str(logos / "foreign.png"), logo)
    src = tmp_path / "wm.mp4"
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=960x540:rate=25:duration=6", "-i", str(logos / "foreign.png"),
        "-filter_complex", "[1:v]scale=110:-1[l];[0:v][l]overlay=W-w-30:30", "-c:v", "libx264",
        "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(src),
    )  # fmt: skip
    prep = prepare_source(src, tmp_path / "out", logos_dir=logos, storyboard=False)
    wm = prep["watermark"]
    assert wm["suspect"] is True and wm["severity"] == "warning"
    assert wm["frames_hit"]["foreign"] >= 10
    clean = prepare_source(
        lavfi_source(tmp_path / "c.mp4"), tmp_path / "out2", logos_dir=logos, storyboard=False
    )
    assert clean["watermark"]["suspect"] is False


def test_ingest_local_manifest_with_mock_asr(landscape: Path, tmp_path: Path) -> None:
    asr = MockAsrProvider(text="Это наш эфир. Здесь есть момент для клипа!")
    src = ingest_local(landscape, OWN, title="Эфир", out_root=tmp_path, asr=asr, logos_dir=tmp_path / "none")
    d = src.dir
    assert d.parent == tmp_path and src.source_id.startswith("src_")
    for name in (
        "source.json",
        "rights.json",
        "proxy_540p.mp4",
        "audio.m4a",
        "peaks.json",
        "transcript.json.gz",
    ):
        assert (d / name).is_file(), name
    assert not (d / "audio16k.wav").exists(), "audio16k.wav is removed after ASR (§8.2)"
    assert (d / "original.mp4").stat().st_ino == landscape.stat().st_ino  # hard link, no copy
    manifest = json.loads((d / "source.json").read_text(encoding="utf-8"))
    assert manifest["rights"]["rights_type"] == "own" and manifest["watermark_suspect"] is False
    assert manifest["transcript"]["sentences"] == 2 and manifest["storyboard"]["count"] == 3
    again = PreparedSource.load(d)
    assert again.to_dict() == src.to_dict()
    assert asr.calls and asr.calls[0][0].endswith("audio16k.wav")


BENCH = ROOT / "var" / "bench" / "10min_ru.wav"


def test_gigaam_on_benchmark_slice() -> None:
    from autoshorts.clipping.source_ingest import mem_available_mb
    from autoshorts.providers.asr.models import missing_files

    if missing_files(["asr", "vad"]) or not BENCH.is_file():
        pytest.skip("GigaAM models or var/bench/10min_ru.wav missing (scripts/models_fetch.py, bench_asr.py)")
    if (mem_available_mb() or 0) < 1500:
        pytest.skip("MemAvailable < 1500 MB")
    from autoshorts.providers.asr.gigaam_onnx import GigaamOnnxProvider

    truth = json.loads(BENCH.with_suffix(".words.json").read_text(encoding="utf-8"))
    provider = GigaamOnnxProvider(threads=2)
    res = provider.transcribe_range(BENCH, 0.0, 30.0)
    assert all(seg[0] < 30.0 for seg in res.vad) and res.next_start >= 30.0
    assert all(w.s < res.next_start for w in res.words)
    ref = [t["w"].lower().strip(".,!?:;«»") for t in truth if t["s"] + 0.15 < res.next_start]
    hyp = [w.w.lower().strip(".,!?:;«»") for w in res.words]
    common = sum(1 for w in hyp if w in set(ref))
    assert len(hyp) >= 0.9 * len(ref) and common >= 0.85 * len(hyp)
    assert all(a.s <= b.s for a, b in zip(res.words, res.words[1:], strict=False))
    # the next chunk starts where this one ended: nothing is recognized twice
    res2 = provider.transcribe_range(BENCH, res.next_start, res.next_start + 20.0)
    assert res2.words and res2.words[0].s >= res.next_start - 0.05
