"""Рендер клипа по EDL на крошечном синтетическом источнике (ffmpeg, nice 10, 2 потока).

Источник: testsrc2 640×360 + «речь» (детерминированные тональные слоги с точными таймингами слов).
Клип: cold open + одна freeze-вставка + outro, голос автора — mock TTS, сценарий — текст оператора.
Проверяется: длительность и громкость в норме, обе дорожки субтитров на месте, стоп-кадры
действительно статичны (freezedetect их видит), но QC их не бракует (expected_static),
AI-disclosure в метаданных, отчёт об оригинальности собран, sendcmd двигает окно кропа.

Run: pytest -m media tests/media/test_clipping_render.py
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

from autoshorts.clipping.author.voice import synthesize_mock  # noqa: E402
from autoshorts.clipping.captions import parse_ass_events  # noqa: E402
from autoshorts.clipping.edl import EDL, LayoutConfig, VideoItem  # noqa: E402
from autoshorts.clipping.produce import ClipJobConfig, ClipSource, produce_clip  # noqa: E402
from autoshorts.clipping.qc_clip import run_clip_qc  # noqa: E402
from autoshorts.clipping.render_clip import RenderConfig, render_clip  # noqa: E402
from autoshorts.clipping.transcript import Transcript, Word  # noqa: E402
from autoshorts.pipeline.proxy import probe_media  # noqa: E402

TEXT = (
    "Почему одни ролики досматривают, а другие нет? Всё решают первые три секунды. "
    "Если зритель не понял, о чём ролик, он листает дальше. Поэтому хук должен быть конкретным. "
    "Второе правило касается темпа. Паузы длиннее секунды убивают внимание."
)
RIGHTS = {
    "rights_type": "licensed_written",
    "owner_name": "Test Owner",
    "proof_note": "test licence",
    "attribution_text": "Test Owner, youtube.com/@test",
    "allowlist_status": "allowlisted",
}
SCRIPT = {
    "cold_open": {
        "vo_text": "Одно правило решает, досмотрят ли ваш ролик.",
        "on_screen": "Правило досмотра",
        "duck_source": True,
    },
    "inserts": [
        {
            "after_sid": 2,
            "mode": "freeze_zoom",
            "vo_text": "Заметьте: речь о ясности, а не о громкости.",
            "on_screen": "Ясность важнее",
        }
    ],
    "outro": {"vo_text": "А вы на какой секунде листаете?", "cta_style": "question"},
    "on_screen_title": "Три секунды решают всё",
}


def ffmpeg(*args: str) -> None:
    subprocess.run(
        ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True
    )


@pytest.fixture(scope="module")
def speech_source(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Transcript]:
    d = tmp_path_factory.mktemp("clip_src")
    take = synthesize_mock("speech", TEXT, d, base_hz=140.0)
    lead, tail = 4.0, 3.0
    dur = lead + take.duration + tail
    src = d / "src.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size=640x360:rate=30:duration={dur:.3f}",
        "-i",
        str(take.path),
        "-af",
        f"adelay={int(lead * 1000)}:all=1,apad",
        "-t",
        f"{dur:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-threads",
        "2",
        str(src),
    )
    words = [Word(w.w, round(w.s + lead, 3), round(w.e + lead, 3)) for w in take.words]
    return src, Transcript.from_words(words, language="ru", duration=dur, provider="mock")


@pytest.fixture(scope="module")
def clip(speech_source: tuple[Path, Transcript], tmp_path_factory: pytest.TempPathFactory):
    src, tr = speech_source
    cs = ClipSource.from_file(src, rights=RIGHTS, watermark={"suspect": False, "severity": "ok"})
    # стоп-кадры без наезда: freezedetect обязан их увидеть, а QC — не забраковать
    cfg = ClipJobConfig(tts="mock", layout=LayoutConfig(freeze_pan="none", outro_pan="none"))
    wd = tmp_path_factory.mktemp("clip_job")
    res = produce_clip(
        cs,
        tr,
        start=tr.sentences[0].s - 0.2,
        end=tr.sentences[-1].e + 0.3,
        workdir=wd,
        author_script=SCRIPT,
        operator_edited=True,
        config=cfg,
        previous_clips=[{"id": "old", "sids": [0, 1]}],
    )
    return res


def test_clip_rendered_with_sane_duration_and_format(clip) -> None:
    assert clip.final_path is not None and clip.final_path.is_file()
    pr = probe_media(clip.final_path)
    assert pr.display_size == (1080, 1920)
    assert pr.has_audio
    assert pr.duration == pytest.approx(clip.edl.duration, abs=0.1)
    assert 20.0 <= pr.duration <= 58.0
    roles = [(v.type, v.role) for v in clip.edl.video]
    assert ("freeze", "insert") in roles and ("freeze", "outro") in roles
    assert {v.role for v in clip.edl.vo} == {"cold_open", "insert_0", "outro"}


def test_loudness_and_qc_pass(clip) -> None:
    qc = clip.qc
    assert qc is not None
    checks = {c.code: c for c in qc.checks}
    assert checks["loudness"].ok, checks["loudness"].measured
    assert abs(qc.stats["integrated_lufs"] - (-14.0)) <= 1.5
    assert checks["clipping"].ok and checks["silence"].ok and checks["duration"].ok
    assert checks["author_share"].ok
    assert qc.passed, qc.to_dict()


def test_expected_static_windows_are_respected(clip) -> None:
    # в готовом клипе поверх стоп-кадров идут субтитры автора, поэтому freezedetect там обычно
    # молчит; всё, что он всё же нашёл, должно лежать внутри expected_static
    qc = clip.qc
    exp = clip.edl.expected_static
    assert len(exp) == 2
    for a, b in qc.stats["freezes"]:
        assert any(x - 0.6 <= a and b <= y + 0.6 for x, y in exp), (a, b, exp)
    assert {c.code: c for c in qc.checks}["frozen_frames"].ok


def test_qc_subtracts_expected_static_on_real_freeze(speech_source, tmp_path: Path) -> None:
    """Без субтитров стоп-кадр виден freezedetect: с expected_static QC его пропускает, без — бракует."""
    src, _ = speech_source
    edl = EDL(canvas={"w": 270, "h": 480, "fps": 30.0})
    crop = {"w": 202, "h": 360, "x": 219, "y": 0}
    edl.video = [
        VideoItem("src", 0.0, 3.0, 90, in_=5.0, out=8.0, reframe="center", crop=crop),
        VideoItem("freeze", 3.0, 3.0, 90, at_src=8.0, pan="none", reframe="center", crop=crop, role="insert"),
        VideoItem("src", 6.0, 3.0, 90, in_=8.0, out=11.0, reframe="center", crop=crop),
    ]
    out = tmp_path / "frz.mp4"
    render_clip(edl, src_path=src, audio_path=None, out_path=out, workdir=tmp_path, wait_memory=False)
    ok = run_clip_qc(out, expected_static=[(3.0, 6.0)], workdir=tmp_path)
    frz = ok.stats["freezes"]
    assert frz and all(2.9 <= a and b <= 6.6 for a, b in frz), frz
    assert ok.check("frozen_frames").ok
    bad = run_clip_qc(out, expected_static=[], workdir=tmp_path)
    assert not bad.check("frozen_frames").ok


def test_captions_present(clip) -> None:
    ass = clip.workdir / "captions.ass"
    ev = parse_ass_events(ass)
    styles = {e["style"] for e in ev}
    assert {"Source", "Author", "Title", "OnScreen"} <= styles
    src_words = json.loads((clip.workdir / "src_words.json").read_text())
    vo_words = json.loads((clip.workdir / "vo_words.json").read_text())
    assert src_words and vo_words
    assert max(w["e"] for w in src_words + vo_words) <= clip.edl.duration + 1e-3


def test_metadata_and_reports(clip) -> None:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(clip.final_path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    tags = {k.lower(): v for k, v in json.loads(out)["format"].get("tags", {}).items()}
    # чистый файл: только служебные поля MP4, без наших тегов, версии Lavf и строки x264 с настройками
    assert set(tags) <= {"major_brand", "minor_version", "compatible_brands"}, tags
    raw = clip.final_path.read_bytes()
    assert b"x264 - core" not in raw and b"Lavf" not in raw
    rep = clip.originality
    assert rep.summary["author_share"] >= 0.30
    assert rep.check("author_share").status == "pass"
    assert rep.check("repeat_moment").status in ("warn", "fail")  # [0, 1] уже нарезаны
    assert rep.check("foreign_watermark").status == "pass"
    saved = json.loads((clip.workdir / "clip.json").read_text())
    assert saved["originality"]["verdict"] == rep.verdict
    assert EDL.load(clip.workdir / "edl.json").duration == pytest.approx(clip.edl.duration)
    assert clip.reframe["shots"] and clip.reframe["geometry"]["crop_w"] == 202  # 360·9/16 → чётное


def test_sendcmd_moves_crop_window(tmp_path: Path) -> None:
    src = tmp_path / "grad.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        # детерминированная горизонтальная рампа: у lavfi `gradients` случайная начальная фаза поворота
        "nullsrc=s=640x360:d=3:r=30,geq=lum='X*255/W':cb=128:cr=128",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-threads",
        "2",
        str(src),
    )
    (tmp_path / "crop_000.cmd").write_text("0.000 crop@c0 x 0;\n1.500 crop@c0 x 400;\n", encoding="ascii")
    edl = EDL(canvas={"w": 270, "h": 480, "fps": 30.0})
    edl.video = [
        VideoItem(
            "src",
            0.0,
            2.0,
            60,
            in_=0.0,
            out=2.0,
            reframe="track",
            crop_cmd="crop_000.cmd",
            crop={"w": 202, "h": 360, "x": 0, "y": 0},
        )
    ]
    out = tmp_path / "moved.mp4"
    res = render_clip(
        edl,
        src_path=src,
        audio_path=None,
        out_path=out,
        workdir=tmp_path,
        cfg=RenderConfig(),
        wait_memory=False,
    )
    assert res.duration == pytest.approx(2.0, abs=0.05)

    def mean_luma(t: float) -> float:
        raw = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                str(t),
                "-i",
                str(out),
                "-frames:v",
                "1",
                "-vf",
                "scale=16:16,format=gray",
                "-f",
                "rawvideo",
                "-",
            ],
            capture_output=True,
            check=True,
        ).stdout
        return sum(raw) / len(raw)

    assert mean_luma(1.8) > mean_luma(0.2) + 60  # окно уехало вправо, к светлой части градиента


def test_library_similarity_against_own_library(clip) -> None:
    from autoshorts.clipping.originality import library_similarity_for

    sims, note, fp = library_similarity_for(clip.final_path, [])
    assert note is None and fp is not None and sims == []
    sims2, _, _ = library_similarity_for(clip.final_path, [("same", fp.to_hex()), ("json", fp.to_json())])
    assert [round(s, 3) for _, s in sims2] == [1.0, 1.0]


def test_yunet_face_sampling_on_video(speech_source) -> None:
    from autoshorts.providers.faces import sample_faces
    from autoshorts.providers.faces.yunet import default_model_path

    if not default_model_path().is_file():
        pytest.skip("YuNet model missing: python scripts/models_fetch.py --groups faces")
    src, _ = speech_source
    track = sample_faces(src, 2.0, 5.0)
    assert 25 <= len(track.samples) <= 35  # ≈10 измерений в секунду
    assert track.frame_w == 640 and track.frame_h == 360
    assert all(2.0 - 0.05 <= s.t <= 5.05 for s in track.samples)
    assert all(not s.faces for s in track.samples)  # в testsrc2 лиц нет
