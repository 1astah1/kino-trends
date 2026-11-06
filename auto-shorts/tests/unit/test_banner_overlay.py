"""Один проход субтитры + баннер: окна показа, строки filter_complex, ASS в зоне, план слоёв."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from autoshorts.domain.banner_schema import (
    BannerTiming,
    LayerShow,
    Rect,
    TimeWindow,
    TimingMode,
    default_banner_variant,
)
from autoshorts.pipeline import banner_assets as ba
from autoshorts.pipeline import banner_overlay as bo
from autoshorts.pipeline import banner_place as bp
from autoshorts.pipeline.encode_profiles import final_profile

WIN = LayerShow.WITH_CREATIVE


def spans(ws: list[TimeWindow]) -> list[tuple[float, float]]:
    return [(w.start_sec, w.end_sec) for w in ws]


# ---------------------------------------------------------------- окна


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        (30.0, [(12.0, 16.0), (27.5, 30.0)]),
        (21.4, [(8.56, 12.56), (18.9, 21.4)]),
        (180.0, [(72.0, 76.0), (177.5, 180.0)]),
        (6.0, [(2.4, 6.0)]),  # импульс растянут до 3 с и слит с финальным повтором
        (5.0, [(2.0, 5.0)]),
        (4.0, [(2.0, 4.0)]),  # финальный повтор не начинается раньше тишины хука
    ],
)
def test_impulse_windows(duration: float, expected: list[tuple[float, float]]) -> None:
    ws, _ = bo.compute_windows(BannerTiming(), duration)
    assert spans(ws) == pytest.approx(expected)
    for w in ws:
        assert 0 <= w.start_sec < w.end_sec <= duration


def test_impulse_respects_hook_silence_and_pct() -> None:
    t = BannerTiming(hook_silence_sec=2.0, impulse_window_pct=(0.4, 0.65), impulse_duration_sec=5.0)
    ws, notes = bo.compute_windows(t, 60.0)
    assert ws[0].start_sec == 24.0 and ws[0].end_sec == 29.0 and not notes
    short, notes2 = bo.compute_windows(t, 6.0)
    assert short[0].start_sec >= 2.0 and "impulse_window_stretched" in notes2


def test_every_duration_5_to_180_gives_sane_windows() -> None:
    for d10 in range(50, 1801, 7):
        d = d10 / 10
        ws, _ = bo.compute_windows(BannerTiming(), d)
        assert ws, d
        assert ws[0].start_sec >= min(2.0, d)
        assert ws[-1].end_sec == pytest.approx(d)
        assert sum(w.duration for w in ws) >= 3.0 - 1e-6 or d < 5.0
        for a, b in zip(ws, ws[1:], strict=False):
            assert b.start_sec - a.end_sec > 1.0  # ближе — слились


def test_persistent_and_explicit_windows() -> None:
    ws, _ = bo.compute_windows(BannerTiming(mode=TimingMode.PERSISTENT), 12.5)
    assert spans(ws) == [(0.0, 12.5)]
    explicit = BannerTiming(
        mode=TimingMode.WINDOWS,
        windows=[
            TimeWindow(start_sec=1, end_sec=3),
            TimeWindow(start_sec=2.5, end_sec=4),
            TimeWindow(start_sec=9, end_sec=20),
        ],
    )
    ws2, _ = bo.compute_windows(explicit, 10.0)
    assert spans(ws2) == [(1.0, 4.0), (9.0, 10.0)]


# ---------------------------------------------------------------- filter_complex


def static_item(**kw) -> bo.OverlayItem:
    base = dict(
        item_id="with_creative_0_static",
        kind="static",
        path=Path("/m/comp.png"),
        x=89,
        y=1076,
        w=777,
        h=290,
        windows=(TimeWindow(start_sec=12, end_sec=16), TimeWindow(start_sec=27.5, end_sec=30)),
        group=WIN,
        layer_ids=("creative", "code", "erid"),
        fade_in_ms=200,
        fade_out_ms=200,
    )
    base.update(kw)
    return bo.OverlayItem(**base)


def test_static_windows_use_itsoffset_and_alpha_fades() -> None:
    g = bo.build_filter_graph([static_item()], duration_sec=30.0, fps=30)
    args = " ".join(g.input_args)
    assert "-loop 1 -framerate 30 -t 4 -itsoffset 12 -i /m/comp.png" in args
    assert "-loop 1 -framerate 30 -t 2.5 -itsoffset 27.5 -i /m/comp.png" in args
    fc = g.filter_complex
    assert "[1:v]format=rgba,fade=t=in:st=12:d=0.2:alpha=1,fade=t=out:st=15.8:d=0.2:alpha=1," in fc
    # окно до самого конца ролика не гаснет (петля)
    assert "fade=t=in:st=27.5:d=0.2:alpha=1,scale=out_color_matrix=bt709" in fc
    assert "fade=t=out:st=29.8" not in fc
    assert fc.count("overlay=x=89:y=1076:eof_action=pass") == 2
    assert fc.endswith("[v1]format=yuv420p[vout]") and g.n_inputs == 2


def test_captions_come_before_banner_and_paths_are_escaped(tmp_path: Path) -> None:
    ass = Path("/tmp/a,b'c:d [x]/subs.ass")
    g = bo.build_filter_graph([static_item()], duration_sec=30.0, ass_path=ass, fonts_dir=Path("/opt/fonts"))
    fc = g.filter_complex
    assert fc.startswith("[0:v]setparams=colorspace=bt709")
    assert fc.index("ass=filename=") < fc.index("overlay=")
    assert r"ass=filename=/tmp/a\,b\\\'c\\:d \[x\]/subs.ass:fontsdir=/opt/fonts[vbase]" in fc
    assert bo.escape_filter_value("/plain/path.ass") == "/plain/path.ass"


def test_animated_item_loops_trims_and_shifts() -> None:
    item = bo.OverlayItem(
        item_id="a",
        kind="animated",
        path=Path("/m/creative.webm"),
        x=100,
        y=1120,
        w=700,
        h=190,
        windows=(TimeWindow(start_sec=12, end_sec=16),),
        group=WIN,
        layer_ids=("creative",),
        fade_in_ms=200,
        fade_out_ms=200,
        opacity=0.8,
        loop=True,
        duration_sec=1.5,
        has_alpha=True,
        crop=Rect(x=10, y=0, w=880, h=220),
    )
    g = bo.build_filter_graph([item], duration_sec=30.0, first_input_index=2)
    args = g.input_args
    i = args.index("-i")
    assert args[:i] == ["-stream_loop", "-1", "-t", "4.5", "-c:v", "libvpx-vp9"]  # декодер до -i: альфа
    fc = g.filter_complex
    assert "[2:v]crop=880:220:10:0,scale=700:190:flags=lanczos,format=yuva420p,trim=duration=4," in fc
    assert "setpts=PTS-STARTPTS+12/TB,lut=a=val*0.800,fade=t=in:st=12:d=0.2:alpha=1" in fc
    once = bo.OverlayItem(
        **{**item.__dict__, "loop": False, "has_alpha": False, "crop": None, "opacity": 1.0}
    )
    g2 = bo.build_filter_graph([once], duration_sec=30.0)
    assert "-stream_loop" not in g2.input_args and "libvpx-vp9" not in g2.input_args
    assert "tpad=stop_mode=clone:stop_duration=2.6" in g2.filter_complex
    assert "lut=" not in g2.filter_complex


def test_build_render_command_maps_audio_and_encode_profile() -> None:
    plan = bo.BannerPlan(
        items=[static_item()],
        windows={},
        placement=bp.VariantPlacement(
            [], bp.CaptionsZone(Rect(x=65, y=700, w=830, h=360), True), "common_organic"
        ),
        captions_zone=bp.CaptionsZone(Rect(x=65, y=700, w=830, h=360), True),
        output_rects={},
        out_size=(1080, 1920),
        duration_sec=30.0,
        fps=30.0,
    )
    cmd = bo.build_render_command(
        Path("/w/base.mp4"), plan, Path("/w/final.mp4"), audio_path=Path("/w/voice.m4a")
    )
    s = " ".join(cmd)
    assert s.startswith("ffmpeg -y -threads")
    assert "-i /w/base.mp4 -i /w/voice.m4a -loop 1" in s
    assert "[2:v]format=rgba" in s and "-map [vout] -map 1:a:0" in s
    for part in (
        "-crf 20",
        "-maxrate 12M",
        "-bufsize 24M",
        "-g 15",
        "-bf 2",
        "-pix_fmt yuv420p",
        "+faststart",
    ):
        assert part in s
    assert "-b:a 192k -ar 48000" in s and s.endswith("-t 30 /w/final.mp4")
    assert "nice" not in cmd  # nice и глобальные опции ставит запускающий
    cmd2 = bo.build_render_command(Path("/w/base.mp4"), plan, Path("/w/o.mp4"), profile=final_profile(crf=19))
    assert "-map 0:a:0?" in " ".join(cmd2) and "-crf" in cmd2 and cmd2[cmd2.index("-crf") + 1] == "19"


# ---------------------------------------------------------------- ASS в зоне


ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
{style_format}
Style: Base,Montserrat,84,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,520,1
Style: Mid,Montserrat,84,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,5,60,60,520,1
Style: Top,Montserrat,84,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,8,60,60,520,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.00,Base,,0,0,300,,Привет, мир
""".replace(
    "{style_format}",
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
    "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
    "MarginL, MarginR, MarginV, Encoding",
)


def test_restyle_ass_for_zone(tmp_path: Path) -> None:
    src = tmp_path / "in.ass"
    src.write_text(ASS, encoding="utf-8")
    zone = bp.CaptionsZone(Rect(x=65, y=692, w=830, h=360), True)
    out = bo.restyle_ass_for_zone(src, tmp_path / "out.ass", zone)
    text = out.read_text(encoding="utf-8")
    styles = {ln.split(",")[0][7:]: ln.split(",") for ln in text.splitlines() if ln.startswith("Style:")}
    assert styles["Base"][18:22] == ["2", "65", "185", str(1920 - 1052)]
    assert styles["Mid"][18] == "2"  # центр по вертикали → низ
    assert styles["Top"][18] == "8" and styles["Top"][21] == "692"
    dialog = next(ln for ln in text.splitlines() if ln.startswith("Dialogue:"))
    assert dialog == "Dialogue: 0,0:00:00.00,0:00:01.00,Base,,0,0,0,,Привет, мир"


def test_restyle_ass_scales_to_playres(tmp_path: Path) -> None:
    src = tmp_path / "in.ass"
    src.write_text(
        ASS.replace("PlayResX: 1080", "PlayResX: 540").replace("PlayResY: 1920", "PlayResY: 960"), "utf-8"
    )
    zone = bp.CaptionsZone(Rect(x=66, y=700, w=830, h=360), True)
    text = bo.restyle_ass_for_zone(src, tmp_path / "o.ass", zone).read_text(encoding="utf-8")
    base = next(ln for ln in text.splitlines() if ln.startswith("Style: Base")).split(",")
    assert base[19:22] == ["33", "92", str(round((1920 - 1060) / 2))]


# ---------------------------------------------------------------- план (Pillow, без ffmpeg)


@pytest.fixture
def media_with_creative(tmp_path: Path):
    im = Image.new("RGBA", (900, 220), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, 899, 219), radius=30, fill=(255, 0, 255, 255))
    src = tmp_path / "creative_upload.png"
    im.save(src)
    media = tmp_path / "media"
    c = ba.ingest_creative(src, media)
    return media, c


def test_plan_static_creative_composites_layers(tmp_path: Path, media_with_creative) -> None:
    media, c = media_with_creative
    profile = bp.load_safe_zone_catalog().get()
    v = default_banner_variant(creative_sha256=c.sha256, erid="2VtzqvAbCd")
    plan = bo.prepare_banner_plan(
        v,
        {c.sha256: c},
        media_root=media,
        duration_sec=30,
        out_size=(1080, 1920),
        profile=profile,
        cache_dir=tmp_path / "cache",
        code="k7m2q",
    )
    kinds = [(i.group, i.layer_ids) for i in plan.items]
    assert kinds == [(LayerShow.PERSISTENT, ("micro_brand",)), (WIN, ("creative", "code", "erid"))]
    persistent, offer = plan.items
    assert spans(list(persistent.windows)) == [(0.0, 30.0)]
    assert spans(list(offer.windows)) == [(12.0, 16.0), (27.5, 30.0)]
    assert offer.path.is_file() and offer.kind == "static"
    creative_rect = plan.output_rects["creative"]
    assert creative_rect == Rect(x=89, y=1120, w=777, h=190)
    for r in plan.output_rects.values():
        assert not r.intersects(plan.captions_zone.rect)
    with Image.open(offer.path) as im:
        assert im.size == (offer.w, offer.h)
        px = im.getpixel((creative_rect.x - offer.x + 388, creative_rect.y - offer.y + 95))
        assert px[0] > 240 and px[2] > 240 and px[3] == 255
    assert plan.text_results["code"].lines == ["u.umka.chat/k7m2q"]
    assert plan.text_results["erid"].lines == ["Реклама · erid 2VtzqvAbCd"]
    json.dumps(plan.to_json(), ensure_ascii=False)
    # повторная подготовка берёт всё из кеша и даёт тот же результат
    again = bo.prepare_banner_plan(
        v,
        {c.sha256: c},
        media_root=media,
        duration_sec=30,
        out_size=(1080, 1920),
        profile=profile,
        cache_dir=tmp_path / "cache",
        code="k7m2q",
    )
    assert [i.path for i in again.items] == [i.path for i in plan.items]


def test_plan_scales_to_small_output(tmp_path: Path, media_with_creative) -> None:
    media, c = media_with_creative
    profile = bp.load_safe_zone_catalog().get()
    v = default_banner_variant(creative_sha256=c.sha256)
    plan = bo.prepare_banner_plan(
        v,
        {c.sha256: c},
        media_root=media,
        duration_sec=6,
        out_size=(320, 568),
        profile=profile,
        cache_dir=tmp_path / "cache",
        code="k7m2q",
    )
    r = plan.output_rects["creative"]
    assert r.w == pytest.approx(777 * 568 / 1920, abs=1) and r.x2 <= 320 and r.y2 <= 568
    assert all(0 <= i.x and i.x + i.w <= 320 and i.y + i.h <= 568 for i in plan.items)


def test_plan_without_creative_uses_fallback_and_skips_missing_erid(tmp_path: Path) -> None:
    profile = bp.load_safe_zone_catalog().get()
    v = default_banner_variant(creative_sha256="d" * 64, ad_marking_required=True)
    plan = bo.prepare_banner_plan(
        v,
        {},
        media_root=tmp_path / "media",
        duration_sec=20,
        out_size=(1080, 1920),
        profile=profile,
        cache_dir=tmp_path / "cache",
        code=None,
    )
    ids = [lid for i in plan.items for lid in i.layer_ids]
    assert "fallback_plate" in ids and "erid" not in ids and "code" not in ids
    codes = {w.code.value for w in plan.warnings}
    assert {"fallback_plate_used", "creative_missing", "erid_missing_when_required"} <= codes
    plate = plan.text_results["fallback_plate"]
    assert plate.width == 825 and len(plate.lines) <= 2


def test_window_helpers() -> None:
    ws = [TimeWindow(start_sec=1, end_sec=2), TimeWindow(start_sec=5, end_sec=6.5)]
    assert bo.window_contains(ws, 1.5) and not bo.window_contains(ws, 3)
    assert bo.total_shown_sec(ws) == pytest.approx(2.5)
    assert bo.overlap_sec(ws[0], TimeWindow(start_sec=1.5, end_sec=9)) == pytest.approx(0.5)
