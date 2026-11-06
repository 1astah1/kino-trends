"""Стиль канала «кадр в фигуре на живом фоне»: квадратный кроп, слежение, маска, раскладка текста, рендер.
Run: pytest -m media tests/media/test_frame_style.py
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.media
if shutil.which("ffmpeg") is None:
    pytest.skip("ffmpeg not installed", allow_module_level=True)

from autoshorts.clipping import frame_style as fstyle  # noqa: E402
from autoshorts.clipping.captions import parse_ass_events  # noqa: E402
from autoshorts.clipping.edl import LayoutConfig  # noqa: E402
from autoshorts.clipping.produce import ClipJobConfig, ClipSource, produce_clip  # noqa: E402
from autoshorts.pipeline.proxy import probe_media  # noqa: E402
from tests.media.test_clipping_render import RIGHTS, SCRIPT, ffmpeg, speech_source  # noqa: E402, F401


def test_square_crop_keeps_center_and_tracking_shift() -> None:
    sq = fstyle.square_crop({"x": 578, "y": 0, "w": 404, "h": 720}, 1280, 720)
    assert sq == {"x": 420, "y": 0, "w": 720, "h": 720}  # центр 780 сохранён
    edge = fstyle.square_crop({"x": 870, "y": 0, "w": 404, "h": 720}, 1280, 720)
    assert edge["x"] == 560  # упёрлись в правый край
    cmd = "0.000 crop@c0 x 578;\n4.971 crop@c0 x 0;\n"
    assert fstyle.shift_crop_cmd(cmd, 404, 720, 1280) == "0.000 crop@c0 x 420;\n4.971 crop@c0 x 0;\n"


def test_caption_zones_sit_inside_the_frame_and_titles_above() -> None:
    fs = fstyle.FrameStyle(size=880, cx=540, cy=1090)  # как у канала «Кадр дня»
    lay = fstyle.caption_layout(fs)
    assert fs.x1 < lay.source_zone.x1 and lay.source_zone.x2 < fs.x2
    assert fs.y1 < lay.source_zone.y1 < lay.source_zone.y2 < fs.y2
    assert lay.title_zone.y2 <= fs.y1 and lay.onscreen_zone.y2 <= fs.y1
    assert lay.title_zone.y1 >= 440  # под стикером


def _pixel(path: Path, t: float, x: int, y: int) -> tuple[int, int, int]:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
         "-vf", f"crop=2:2:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout  # fmt: skip
    return raw[0], raw[1], raw[2]


@pytest.mark.parametrize("shape", ["circle", "rounded"])
def test_clip_renders_in_shape_on_background_video(speech_source, tmp_path: Path, shape: str) -> None:  # noqa: F811
    src, tr = speech_source
    bg = tmp_path / "bg.mp4"
    # однотонный ярко-зелёный фон: его легко узнать по пикселю
    ffmpeg("-f", "lavfi", "-i", "color=c=0x00C040:size=720x1280:rate=30:duration=4", "-c:v", "libx264",
           "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(bg))  # fmt: skip
    style = {"shape": shape, "size": 800, "cx": 540, "cy": 1040, "bg_path": str(bg), "bg_dim": 0.0,
             "ring_color": "#FF0000", "accent": "#FFD84D"}  # fmt: skip
    cs = ClipSource.from_file(src, rights=RIGHTS, watermark={"suspect": False, "severity": "ok"})
    cfg = ClipJobConfig(
        tts="mock", layout=LayoutConfig(freeze_pan="none", outro_pan="none"), frame_style=style
    )
    res = produce_clip(cs, tr, start=tr.sentences[0].s - 0.2, end=tr.sentences[-1].e + 0.3,
        workdir=tmp_path / "w",
                       author_script=SCRIPT, operator_edited=True, config=cfg)  # fmt: skip
    assert res.final_path is not None and res.final_path.is_file()
    pr = probe_media(res.final_path)
    assert pr.display_size == (1080, 1920) and pr.duration == pytest.approx(res.edl.duration, abs=0.15)
    assert res.edl.meta["frame_style"]["shape"] == shape
    assert all(v.reframe != "fit_blur" and v.crop["w"] == v.crop["h"] for v in res.edl.video)
    r, g, b = _pixel(res.final_path, 1.0, 60, 1860)  # нижний левый угол — фон, не размытый кадр
    assert g > 140 and r < 90 and b < 110, (r, g, b)
    r, g, b = _pixel(res.final_path, 1.0, 140, 1040)  # левая кромка фигуры — обводка или кадр, но не фон
    assert not (g > 140 and r < 90 and b < 110), (r, g, b)
    styles = {e["style"] for e in parse_ass_events(res.workdir / "captions.ass")}
    assert {"Author", "Title"} <= styles
    ass = (res.workdir / "captions.ass").read_text(encoding="utf-8")
    assert "Style: Author,Montserrat" in ass or "Style: Author," in ass


def test_clip_without_cold_open_starts_with_the_scene(speech_source, tmp_path: Path) -> None:  # noqa: F811
    """Канал без вступления: ни стоп-кадра с голосом в начале, ни реплики cold_open в звуке."""
    src, tr = speech_source
    cs = ClipSource.from_file(src, rights=RIGHTS, watermark={"suspect": False, "severity": "ok"})
    cfg = ClipJobConfig(tts="mock", layout=LayoutConfig(freeze_pan="none", outro_pan="none", cold_open=False))
    res = produce_clip(cs, tr, start=tr.sentences[0].s - 0.2, end=tr.sentences[-1].e + 0.3,
                       workdir=tmp_path / "w", author_script=SCRIPT, operator_edited=True, config=cfg)  # fmt: skip
    assert res.final_path is not None and res.final_path.is_file()
    assert res.edl.video[0].type == "src" and res.edl.video[0].role != "cold_open"
    assert "cold_open" not in {v.role for v in res.edl.vo}
    assert {v.role for v in res.edl.vo} >= {"outro"}


def test_clip_without_author_voice_is_just_the_scene(speech_source, tmp_path: Path) -> None:  # noqa: F811
    """Канал без нейроозвучки: ни вступления, ни вставок, ни концовки — только сцена со своим звуком."""
    src, tr = speech_source
    cs = ClipSource.from_file(src, rights=RIGHTS, watermark={"suspect": False, "severity": "ok"})
    cfg = ClipJobConfig(tts="mock", layout=LayoutConfig(author_voice=False))
    res = produce_clip(cs, tr, start=tr.sentences[0].s - 0.2, end=tr.sentences[-1].e + 0.3,
                       workdir=tmp_path / "w", author_script=SCRIPT, operator_edited=True, config=cfg)  # fmt: skip
    assert res.final_path is not None and res.final_path.is_file()
    assert list(res.edl.vo) == []
    assert all(v.type == "src" for v in res.edl.video)


def test_auto_shape_circle_for_single_centered_face_else_rectangle() -> None:
    fs = fstyle.FrameStyle.coerce({"shape": "auto", "size": 880, "cy": 1090})
    single = [{"start": 0, "end": 30, "mode": "track", "stats": {"face_coverage": 0.95, "group_spread_share": 0.0}}]
    group = [{"start": 0, "end": 30, "mode": "fit_blur", "reason": "wide_group",
              "stats": {"face_coverage": 1.0, "group_spread_share": 0.7}}]  # fmt: skip
    cartoon = [{"start": 0, "end": 30, "mode": "fit_blur", "reason": "no_faces", "stats": {"face_coverage": 0.0}}]
    c = fstyle.choose_shape(fs, single)
    assert c.shape == "circle" and (c.w, c.h) == (880, 880)
    for shots in (group, cartoon):
        r = fstyle.choose_shape(fs, shots)
        assert r.shape == "rounded" and (r.w, r.h) == (1000, 750)
    crop = fstyle.square_crop({"x": 578, "y": 0, "w": 404, "h": 720}, 1280, 720, 1000 / 750)
    assert crop == {"x": 300, "y": 0, "w": 960, "h": 720}  # 4:3 вокруг того же центра 780


def test_square_crop_stays_inside_letterbox_box() -> None:
    from autoshorts.clipping.frame_style import square_crop

    # кадр 1280×720, картинка 2,39:1 — 1280×536 с полосами по 92 px сверху и снизу
    box = {"x": 0, "y": 92, "w": 1280, "h": 536}
    sq = square_crop({"x": 400, "y": 0, "w": 405, "h": 720}, 1280, 720, 4 / 3, box=box)
    assert sq["y"] >= 92 and sq["y"] + sq["h"] <= 92 + 536  # полосы не попали в рамку
    assert sq["h"] == 536 and abs(sq["w"] / sq["h"] - 4 / 3) < 0.01
    full = square_crop({"x": 400, "y": 0, "w": 405, "h": 720}, 1280, 720, 4 / 3)
    assert full["h"] == 720  # без области — как раньше


def test_detect_content_box_on_letterboxed_video(tmp_path) -> None:
    import shutil
    import subprocess

    import pytest

    from autoshorts.clipping.frame_style import detect_content_box

    if not shutil.which("ffmpeg"):
        pytest.skip("нет ffmpeg")
    lb = tmp_path / "lb.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=640x268:r=10:d=4",
         "-vf", "pad=640:360:0:46:black", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(lb)],
        check=True,
    )  # fmt: skip
    box = detect_content_box(lb, 0.0, 4.0, 1280, 720, samples=3)
    assert box is not None
    assert abs(box["y"] - 92) <= 8 and abs(box["h"] - 536) <= 12 and box["w"] >= 1260
    plain = tmp_path / "plain.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=10:d=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(plain)],
        check=True,
    )  # fmt: skip
    assert detect_content_box(plain, 0.0, 3.0, 1280, 720, samples=2) is None
