"""EDL: раскладка авторского слоя, строки filter_complex рендера и звука, огибающие приглушения."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoshorts.clipping.author.script import AuthorScript
from autoshorts.clipping.edl import (
    EDL,
    LayoutConfig,
    apply_reframe,
    boundary_after,
    layout_clip,
    source_range,
)
from autoshorts.clipping.produce import ClipSource, mix_spec_from_edl
from autoshorts.clipping.reframe import CropGeometry, build_plan, static_plan
from autoshorts.clipping.render_clip import (
    RenderConfig,
    build_render_command,
    build_video_graph,
    freeze_pan_filters,
)
from autoshorts.clipping.transcript import Transcript, Word
from autoshorts.pipeline.audio_mix import (
    GapPiece,
    MixSpec,
    SrcPiece,
    VoClip,
    build_mix_graph,
    db_to_gain,
    duck_expression,
    envelope_value,
    parse_loudnorm_json,
)
from autoshorts.providers.faces import FaceBox, FaceSample

FPS = 30.0
SRC = {"path": "/media/src.mp4", "width": 1920, "height": 1080, "duration": 120.0, "has_audio": True}


def make_transcript(n_sent: int = 10, lead: float = 10.0) -> Transcript:
    words: list[Word] = []
    t = lead
    for i in range(n_sent):
        for j in range(6):
            w = f"слово{i}{j}" + ("." if j == 5 else "")
            words.append(Word(w, round(t, 3), round(t + 0.4, 3)))
            t += 0.5
        t += 0.6  # пауза между предложениями
    return Transcript.from_words(words, language="ru", duration=t + 30, provider="test")


def takes_for(script: AuthorScript, dur: float = 2.0) -> dict[str, dict]:
    out = {}
    for role, txt in script.vo_parts():
        out[role] = {
            "path": f"/tmp/{role}.wav",
            "duration": dur,
            "words": [Word("раз", 0.1, 0.6), Word("два", 0.8, 1.5)],
            "kind": "tts",
            "text": txt,
        }
    return out


FULL = {
    "cold_open": {
        "vo_text": "Почему это важно прямо сейчас.",
        "on_screen": "Главный вопрос",
        "duck_source": True,
    },
    "inserts": [
        {"after_sid": 3, "mode": "freeze_zoom", "vo_text": "Поясню термин.", "on_screen": "Термин"},
        {"after_sid": 6, "mode": "over_ducked", "vo_text": "Короткая реплика."},
    ],
    "outro": {"vo_text": "А вы как думаете?", "cta_style": "question"},
    "on_screen_title": "Свой заголовок ролика",
}


def full_edl(**cfg_kw) -> tuple[EDL, Transcript]:
    tr = make_transcript()
    script = AuthorScript.model_validate(FULL)
    edl = layout_clip(
        tr,
        start=tr.sentences[0].s - 0.2,
        end=tr.sentences[-1].e + 0.3,
        start_sid=0,
        end_sid=9,
        script=script,
        takes=takes_for(script),
        source=SRC,
        config=LayoutConfig(**cfg_kw),
    )
    return edl, tr


# ---------------------------------------------------------------------- раскладка


def test_layout_order_roles_and_frame_quantization() -> None:
    edl, tr = full_edl()
    roles = [(v.type, v.role) for v in edl.video]
    assert roles == [
        ("src", "cold_open"),  # preroll: перед клипом 9,8 с материала
        ("src", "body"),
        ("freeze", "insert"),
        ("src", "body"),
        ("freeze", "outro"),
    ]
    for v in edl.video:
        assert v.frames == round(v.dur * FPS)
        assert v.dur == pytest.approx(v.frames / FPS)
    # элементы идут встык по выходной шкале
    t = 0.0
    for v in edl.video:
        assert v.t == pytest.approx(t)
        t += v.dur
    assert edl.duration == pytest.approx(t)
    # исходник непрерывен между preroll → телом → вставкой → телом
    assert edl.video[0].out == pytest.approx(edl.video[1].in_)
    assert edl.video[2].at_src == pytest.approx(edl.video[1].out)
    assert edl.video[3].in_ == pytest.approx(edl.video[1].out)
    # outro замораживает первый кадр ролика (петля)
    assert edl.video[4].at_src == pytest.approx(edl.video[0].in_)
    assert edl.clip["cold_open_mode"] == "preroll"
    assert edl.meta["ai_disclosure"] is True
    assert [i["after_sid"] for i in edl.meta["inserts"]] == [3, 6]


def test_freeze_insert_at_sentence_boundary_and_expected_static() -> None:
    edl, tr = full_edl()
    cut = boundary_after(tr, 3)
    last = tr.words[tr.sentences[3].w1]
    assert last.e < cut <= last.e + 0.25
    frz = edl.video[2]
    assert frz.at_src == pytest.approx(cut, abs=1 / FPS)
    assert edl.expected_static == [(v.t, v.end) for v in edl.video if v.type == "freeze"]


def test_vo_and_duck_windows() -> None:
    edl, _ = full_edl()
    vo = {v.role: v for v in edl.vo}
    assert set(vo) == {"cold_open", "insert_0", "insert_1", "outro"}
    assert vo["cold_open"].at == pytest.approx(0.12)
    frz = edl.video[2]
    assert frz.t < vo["insert_0"].at < vo["insert_0"].end <= frz.end
    ducks = [(d.start, d.end, d.gain_db) for d in edl.src_duck]
    assert ducks[0] == (0.0, pytest.approx(edl.video[0].dur), -16.0)  # preroll под голосом
    assert any(g == -60.0 and a == pytest.approx(frz.t) for a, _, g in ducks)  # freeze без звука исходника
    over = vo["insert_1"]
    assert any(g == -16.0 and a < over.at and b > over.end for a, b, g in ducks)
    assert "vo_overlap" not in " ".join(edl.warnings)


def test_cold_open_modes() -> None:
    tr = make_transcript(lead=1.0)  # перед клипом ~0,8 с — preroll невозможен
    script = AuthorScript.model_validate(FULL)
    edl = layout_clip(
        tr,
        start=tr.sentences[0].s - 0.2,
        end=tr.sentences[-1].e,
        script=script,
        takes=takes_for(script),
        source=SRC,
    )
    assert edl.clip["cold_open_mode"] == "duck"
    assert edl.video[0].role == "body"
    assert edl.src_duck[0].start == 0.0 and edl.src_duck[0].gain_db == -16.0
    s2 = AuthorScript.model_validate({**FULL, "cold_open": {**FULL["cold_open"], "duck_source": False}})
    edl2 = layout_clip(
        tr, start=tr.sentences[0].s - 0.2, end=tr.sentences[-1].e, script=s2, takes=takes_for(s2), source=SRC
    )
    assert edl2.clip["cold_open_mode"] == "freeze"
    assert edl2.video[0].type == "freeze" and edl2.video[0].role == "cold_open"


def test_trim_to_max_total() -> None:
    tr = make_transcript(n_sent=20)
    script = AuthorScript.model_validate(FULL)
    edl = layout_clip(
        tr,
        start=tr.sentences[0].s,
        end=tr.sentences[-1].e,
        script=script,
        takes=takes_for(script, 3.0),
        source=SRC,
        config=LayoutConfig(max_total_sec=45),
    )
    assert edl.duration <= 45.0 + 1e-6
    assert any(w.startswith("clip_trimmed_to_fit") for w in edl.warnings)
    # конец подрезан по концу предложения
    last_src = max(v.out for v in edl.video if v.type == "src")
    ends = [tr.words[s.w1].e for s in tr.sentences]
    assert min(abs(last_src - e) for e in ends) < 0.4


def test_insert_out_of_clip_is_skipped() -> None:
    tr = make_transcript()
    script = AuthorScript.model_validate(
        {
            **FULL,
            "inserts": [
                {"after_sid": 9, "vo_text": "После последнего."},
                {"after_sid": 40, "vo_text": "Мимо."},
            ],
        }
    )
    edl = layout_clip(
        tr,
        start=tr.sentences[0].s,
        end=tr.sentences[-1].e,
        script=script,
        takes=takes_for(script),
        source=SRC,
    )
    assert not [v for v in edl.video if v.role == "insert"]
    assert any("insert_0_skipped" in w for w in edl.warnings)
    assert any("insert_1_bad_sid" in w for w in edl.warnings)


def test_source_and_author_words_on_output_timeline() -> None:
    edl, tr = full_edl()
    sw = edl.source_words(tr.words)
    aw = edl.author_words()
    vo_windows = edl.vo_windows()
    for w in sw:
        mid = (w.s + w.e) / 2
        assert not any(a <= mid <= b for a, b in vo_windows)  # исходник скрыт под голосом автора
        assert 0 <= w.s < w.e <= edl.duration + 1e-6
    assert aw and aw[0].s == pytest.approx(0.12 + 0.1)
    # без скрытия слов больше
    assert len(edl.source_words(tr.words, hide_under_vo=False)) > len(sw)


def test_texts_title_and_on_screen() -> None:
    edl, _ = full_edl()
    kinds = [(t.kind, t.role) for t in edl.texts]
    assert kinds[0] == ("title", "body")
    assert ("on_screen", "cold_open") in kinds and ("on_screen", "insert") in kinds
    title = edl.texts[0]
    assert title.start == 0 and title.end == pytest.approx(edl.duration)
    counted = {t.text: (t.counts_as_author, t.note) for t in edl.texts}
    assert counted["Свой заголовок ролика"] == (True, "")
    assert counted["Главный вопрос"] == (True, "")
    # одно слово — не своя формулировка: на экране есть, в author_share не идёт
    assert counted["Термин"] == (False, "no_words")


def test_title_duplicating_transcript_does_not_count() -> None:
    tr = make_transcript()
    script = AuthorScript.model_validate({**FULL, "on_screen_title": "слово00 слово01 слово02"})
    edl = layout_clip(
        tr,
        start=tr.sentences[0].s,
        end=tr.sentences[-1].e,
        script=script,
        takes=takes_for(script),
        source=SRC,
    )
    assert edl.texts[0].kind == "title" and edl.texts[0].counts_as_author is False


def test_edl_json_round_trip(tmp_path: Path) -> None:
    edl, _ = full_edl()
    p = edl.save(tmp_path / "edl.json")
    back = EDL.load(p)
    assert back.to_dict() == edl.to_dict()
    d = edl.to_dict()
    assert d["video"][2]["type"] == "freeze" and "at_src" in d["video"][2]
    assert d["audio"]["src_duck"][0][2] == -16.0
    assert d["captions"][0]["style"] == "Source" and d["captions"][1]["style"] == "Author"
    assert d["meta"]["ai_disclosure"] is True


# ---------------------------------------------------------------------- рефрейм в EDL


def test_apply_reframe_splits_at_scene_cut_and_writes_sendcmd(tmp_path: Path) -> None:
    edl, _ = full_edl()
    g = CropGeometry.for_source(1920, 1080)
    body = edl.video[1]
    cut = (body.in_ + body.out) / 2
    samples = [
        FaceSample(t / 10, [FaceBox(0.2 + 0.4 * ((t / 10) % 20) / 20, 0.3, 0.08, 0.15, 0.9)])
        for t in range(1200)
    ]
    plan = build_plan(g, 0.0, 120.0, face_samples=samples, scene_cuts=[cut])
    files = apply_reframe(edl, plan, workdir=tmp_path)
    total_frames = sum(v.frames for v in edl.video)
    assert edl.duration == pytest.approx(total_frames / FPS)
    tracks = [v for v in edl.video if v.reframe == "track"]
    assert tracks and all(v.crop_cmd for v in tracks)
    assert [f.name for f in files] == [v.crop_cmd for v in tracks]
    first = files[0].read_text().splitlines()
    assert first[0].startswith("0.000 crop@c0 x ")
    times = [float(line.split()[0]) for line in first]
    assert times == sorted(times) and times[-1] < tracks[0].dur
    # разрез внутри тела: кусок до склейки кончается на склейке
    ends = [v.out for v in edl.video if v.type == "src"]
    assert min(abs(e - cut) for e in ends) < 1 / FPS + 1e-6
    for v in edl.video:
        assert v.crop == {"w": 608, "h": 1080, "x": v.crop["x"], "y": 0}
        assert 0 <= v.crop["x"] <= 1920 - 608 and v.crop["x"] % 2 == 0


def test_source_range_covers_preroll_and_freezes() -> None:
    edl, _ = full_edl()
    a, b = source_range(edl)
    assert a == pytest.approx(edl.video[0].in_)
    assert b == pytest.approx(max(v.out for v in edl.video if v.type == "src"))


# ---------------------------------------------------------------------- граф видео


def test_render_command_filter_strings(tmp_path: Path) -> None:
    edl, _ = full_edl()
    g = CropGeometry.for_source(1920, 1080)
    samples = [
        FaceSample(t / 10, [FaceBox(0.1 + 0.6 * (t / 1200), 0.3, 0.08, 0.15, 0.9)]) for t in range(1200)
    ]
    apply_reframe(edl, build_plan(g, 0, 120, face_samples=samples), workdir=tmp_path)
    cmd = build_render_command(
        edl,
        src_path="/media/src.mp4",
        audio_path="mix.m4a",
        out_path="final.mp4",
        ass_path="captions.ass",
        cfg=RenderConfig(),
    )
    fc = cmd[cmd.index("-filter_complex") + 1]
    n = len(edl.video)
    assert cmd.count("-i") == n + 1  # по входу на элемент + звук
    assert "sendcmd=f=crop_000.cmd,crop@c0=w=608:h=1080:x=" in fc
    assert "scale=1080:1920:flags=lanczos" in fc
    assert f"concat=n={n}:v=1:a=0[vcat]" in fc
    assert "ass=filename=captions.ass:fontsdir=" in fc and fc.endswith("[vout]")
    assert "loop=loop=" in fc and "setpts=N/30/TB" in fc
    assert "zoompan" not in fc
    joined = " ".join(cmd)
    for frag in (
        "-c:v libx264",
        "-preset veryfast",
        "-crf 20",
        "-maxrate 12M",
        "-bufsize 24M",
        "-c:a copy",
        "+faststart",
        "-map_metadata -1",
        "filter_units=remove_types=6",
        "-threads 2",
    ):
        assert frag in joined, frag
    assert "-metadata" not in cmd  # чистый файл: своих тегов не пишем (RenderConfig.clean_metadata)
    assert cmd[-1] == "final.mp4" and cmd[-3] == "-t"
    # вход каждого куска ищется в источнике: -ss in -t dur+0.5
    first_ss = cmd[cmd.index("-ss") + 1]
    assert float(first_ss) == pytest.approx(edl.video[0].in_, abs=1e-3)


def test_fit_blur_graph_is_cheap_background() -> None:
    edl, _ = full_edl()
    plan = static_plan(1920, 1080, 0, 120, mode="fit_blur")
    apply_reframe(edl, plan)
    fc = ";".join(build_video_graph(edl, "/m/s.mp4").chains)
    assert (
        "scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=10:2,scale=1080:1920" in fc
    )
    assert "overlay=(W-w)/2:(H-h)/2" in fc
    assert "gblur" not in fc


def test_src_offset_for_sections() -> None:
    edl, _ = full_edl()
    apply_reframe(edl, static_plan(1920, 1080, 0, 120))
    g = build_video_graph(edl, "/m/section.mp4", src_offset=5.0)
    ss = [float(g.inputs[i + 1]) for i, x in enumerate(g.inputs) if x == "-ss"]
    assert ss[0] == pytest.approx(edl.video[0].in_ - 5.0, abs=1e-3)


def test_freeze_pan_matches_legacy_crop_pan() -> None:
    from autoshorts.pipeline.render import _crop_pan_filter  # legacy, только чтение

    for d in ("in", "out", "left", "right"):
        pre, post = freeze_pan_filters(d, 2.6, 1080, 1920)
        assert f"{pre},{post}" == _crop_pan_filter(0, 1080, 1920, 1080, 1920, 2.6, d)
    assert freeze_pan_filters("none", 2.0, 1080, 1920) == ("", "")


# ---------------------------------------------------------------------- звук


def test_duck_expression_string_and_values() -> None:
    wins = [(0.0, 2.4, -16.0), (12.28, 14.88, -60.0)]
    expr = duck_expression(wins)
    assert expr.count("clip(") == 2 and "*" in expr
    assert "(t-12.28)/0.08" in expr and "(14.88-t)/0.08" in expr
    g16 = db_to_gain(-16.0)
    assert envelope_value(1.2, wins) == pytest.approx(g16)
    assert envelope_value(13.5, wins) == pytest.approx(0.001, rel=1e-3)
    assert envelope_value(5.0, wins) == pytest.approx(1.0)
    assert envelope_value(2.4 - 0.04, wins) == pytest.approx(1 - (1 - g16) * 0.5)  # середина рампы
    assert duck_expression([]) == "1"


def test_mix_graph_exact_samples_gaps_and_vo() -> None:
    spec = MixSpec(
        source=Path("/m/s.mp4"),
        pieces=[SrcPiece(84.12, 12.28), GapPiece(2.6), SrcPiece(96.4, 25.5)],
        duration=40.38,
        duck=[(0.0, 2.4, -16.0)],
        vo=[VoClip(Path("vo_open.wav"), 0.0), VoClip(Path("vo_ins.wav"), 12.4, gain_db=2.0)],
    )
    g = build_mix_graph(spec)
    fc = g.filter_complex
    assert g.inputs[:6] == ["-ss", "84.12", "-t", "12.78", "-i", "/m/s.mp4"]
    assert f"atrim=end_sample={round(12.28 * 48000)}" in fc
    assert f"anullsrc=r=48000:cl=stereo,atrim=end_sample={round(2.6 * 48000)}" in fc
    assert "concat=n=3:v=0:a=1[srccat]" in fc
    assert "volume='(1-" in fc and ":eval=frame" in fc
    assert "adelay=delays=12400:all=1" in fc and "volume=2dB" in fc
    assert "amix=inputs=3:duration=longest:dropout_transition=0:normalize=0" in fc
    assert f"apad=whole_len={round(40.38 * 48000)},atrim=end_sample={round(40.38 * 48000)}[mix]" in fc


def test_mix_spec_from_edl_uses_offset_and_gaps() -> None:
    edl, _ = full_edl()
    src = ClipSource(Path("/m/section.mp4"), Path("/m/proxy.mp4"), 1920, 1080, 60.0, src_offset=5.0)
    spec = mix_spec_from_edl(edl, src)
    kinds = [type(p).__name__ for p in spec.pieces]
    assert kinds == ["SrcPiece", "SrcPiece", "GapPiece", "SrcPiece", "GapPiece"]  # over_ducked не режет видео
    assert spec.pieces[0].start == pytest.approx(edl.video[0].in_ - 5.0)
    assert spec.pieces_duration == pytest.approx(edl.duration)
    assert [v.at for v in spec.vo] == [v.at for v in edl.vo]


def test_parse_loudnorm_json() -> None:
    text = """[Parsed_loudnorm_0 @ 0x1]
{
	"input_i" : "-23.54",
	"input_tp" : "-7.12",
	"input_lra" : "5.30",
	"input_thresh" : "-33.98",
	"output_i" : "-14.02",
	"target_offset" : "0.02"
}"""
    d = parse_loudnorm_json(text)
    assert d is not None and d["input_i"] == "-23.54" and d["target_offset"] == "0.02"
    assert parse_loudnorm_json("nothing") is None


def test_banner_hook_uses_banner_overlay_graph() -> None:
    from types import SimpleNamespace

    from autoshorts.clipping.render_clip import banner_graph_from_plan

    fn = banner_graph_from_plan(SimpleNamespace(items=[]))
    if fn is None:
        pytest.skip("pipeline/banner_overlay недоступен (пишется параллельно)")
    edl, _ = full_edl()
    apply_reframe(edl, static_plan(1920, 1080, 0, 120))
    cmd = build_render_command(
        edl,
        src_path="/m/s.mp4",
        audio_path="mix.m4a",
        out_path="o.mp4",
        ass_path="captions.ass",
        banner_graph=fn,
    )
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert fc.count("ass=filename=") == 1  # субтитры рисует граф баннера, не мы второй раз
    assert "[vcat]" in fc and cmd[cmd.index("-map") + 1] == "[vout]"


def test_insert_snaps_to_piece_end_when_jump_cut_removed_the_pause() -> None:
    tr = make_transcript()
    st = tr.sentences[3]
    gap_a = tr.words[st.w1].e + 0.1  # пауза после предложения 3 вырезана джамп-катом
    gap_b = tr.words[st.w1 + 1].s - 0.1
    start, end = tr.sentences[0].s, tr.sentences[-1].e
    script = AuthorScript.model_validate(
        {"inserts": [{"after_sid": 3, "mode": "freeze_zoom", "vo_text": "Поясню."}]}
    )
    edl = layout_clip(
        tr,
        start=start,
        end=end,
        script=script,
        takes=takes_for(script),
        source=SRC,
        keep_segments=[(start, gap_a), (gap_b, end)],
        config=LayoutConfig(min_total_sec=0),
    )
    roles = [(v.type, v.role) for v in edl.video]
    assert roles == [("src", "body"), ("freeze", "insert"), ("src", "body")]
    assert edl.video[1].at_src == pytest.approx(edl.video[0].out)
    assert edl.video[2].in_ == pytest.approx(gap_b, abs=1 / FPS)


# ---------------------------------------------------------------------- подрезка под 58 с и вставки


def _dense_transcript(n_sent: int = 21, lead: float = 12.0) -> Transcript:
    """Предложения по 2,55 с: 21 предложение ≈ 53 с."""
    words: list[Word] = []
    t = lead
    for i in range(n_sent):
        for j in range(5):
            words.append(Word(f"слово{i}{j}" + ("." if j == 4 else ""), round(t, 3), round(t + 0.4, 3)))
            t += 0.45
        t += 0.3
    return Transcript.from_words(words, language="ru", duration=t + 30, provider="test")


def _late_insert_layout(inserts: list[dict]) -> tuple[EDL, Transcript]:
    tr = _dense_transcript()
    script = AuthorScript.model_validate(
        {
            "cold_open": {"vo_text": "Хук автора.", "duck_source": True},
            "inserts": inserts,
            "outro": {"vo_text": "Вопрос зрителю?", "cta_style": "question"},
            "on_screen_title": "Свой заголовок ролика",
        }
    )
    durs = {"cold_open": 8.0, "outro": 6.0, "insert_0": 6.0, "insert_1": 6.0}
    takes = {
        role: {"path": f"/tmp/{role}.wav", "duration": durs[role], "words": [], "kind": "tts", "text": txt}
        for role, txt in script.vo_parts()
    }
    edl = layout_clip(
        tr,
        start=tr.sentences[0].s,
        end=tr.sentences[-1].e,
        script=script,
        takes=takes,
        source=SRC,
        config=LayoutConfig(cold_open_mode="preroll"),
    )
    return edl, tr


def _body_src_sec(edl: EDL) -> float:
    return sum(v.dur for v in edl.video if v.type == "src" and v.role == "body")


def test_trim_does_not_reserve_time_for_insert_it_pushes_out() -> None:
    """Регрессия: резерв под вставку после sid 18 оставался, хотя подрезка её выкидывала —
    ролик 47 с, из них 32 с исходника, вставки нет."""
    edl, _ = _late_insert_layout(
        [{"after_sid": 18, "mode": "freeze_zoom", "vo_text": "Комментарий автора к этому месту."}]
    )
    assert "insert_0_dropped_to_fit:sid18" in edl.warnings
    assert not any("skipped_out_of_range" in w for w in edl.warnings)
    assert not [v for v in edl.video if v.type == "freeze" and v.role == "insert"]
    # резерв вставки вернулся исходнику: 58 − хук 8,42 − outro 6,62 − 0,1 ≈ 42,9 с (по концу предложения)
    assert _body_src_sec(edl) > 40.0
    assert 55.0 < edl.duration <= 58.0 + 1e-6
    assert edl.meta["inserts"] == []


def test_trim_keeps_insert_that_still_fits() -> None:
    edl, _ = _late_insert_layout(
        [
            {"after_sid": 5, "mode": "freeze_zoom", "vo_text": "Ранний комментарий автора."},
            {"after_sid": 18, "mode": "freeze_zoom", "vo_text": "Поздний комментарий автора."},
        ]
    )
    # поздняя снята, ранняя стоит; под раннюю резерв остаётся
    assert "insert_1_dropped_to_fit:sid18" in edl.warnings
    assert "insert_0_dropped_to_fit:sid5" not in edl.warnings
    assert [i["after_sid"] for i in edl.meta["inserts"]] == [5]
    assert len([v for v in edl.video if v.type == "freeze" and v.role == "insert"]) == 1
    assert edl.duration <= 58.0 + 1e-6
    assert _body_src_sec(edl) > 34.0


@pytest.mark.parametrize("title", ["!!!", "🔥🔥🔥", "Ну и ну?", "Это так"])
def test_title_without_words_does_not_count(title: str) -> None:
    tr = make_transcript()
    script = AuthorScript.model_validate({**FULL, "on_screen_title": title})
    edl = layout_clip(
        tr,
        start=tr.sentences[0].s,
        end=tr.sentences[-1].e,
        script=script,
        takes=takes_for(script),
        source=SRC,
    )
    t = edl.texts[0]
    assert t.kind == "title" and t.counts_as_author is False and t.note == "no_words"
    # в JSON причина сохраняется
    assert EDL.from_dict(edl.to_dict()).texts[0].note == "no_words"
    share = edl.author_share()
    assert share.own_text_sec < edl.duration  # заголовок не засчитан как свой текст


def test_duplicate_title_note() -> None:
    tr = make_transcript()
    script = AuthorScript.model_validate({**FULL, "on_screen_title": "слово00 слово01 слово02"})
    edl = layout_clip(
        tr,
        start=tr.sentences[0].s,
        end=tr.sentences[-1].e,
        script=script,
        takes=takes_for(script),
        source=SRC,
    )
    assert edl.texts[0].note == "duplicates_transcript"


def test_no_voice_channel_shows_script_as_text_comments() -> None:
    """Канал без голоса автора: реплики сценария идут текстом — заголовок на вступление, комментарии после своих
    фраз, вопрос в конце; пересказ сцены не выводится."""
    tr = make_transcript()
    script = AuthorScript.model_validate(FULL)
    kw = dict(start=tr.sentences[0].s - 0.2, end=tr.sentences[-1].e + 0.3, start_sid=0, end_sid=9, script=script, source=SRC)
    edl = layout_clip(tr, **kw, config=LayoutConfig(author_voice=False))
    assert not edl.vo and all(v.type == "src" for v in edl.video)  # без голоса нет стоп-кадров и озвучки
    texts = [(t.text, t.role, round(t.start, 1), round(t.end, 1)) for t in edl.texts]
    total = edl.video[-1].end
    assert texts[0][:2] == ("Свой заголовок ролика", "body") and texts[0][3] == 4.0  # заголовок только на вступление
    roles = {t.role: t for t in edl.texts}
    after3 = boundary_after(tr, 3) - kw["start"]
    assert abs(roles["insert_0"].start - after3) < 0.05 and roles["insert_0"].text == "Поясню термин."
    assert roles["insert_1"].start >= roles["insert_0"].end + 0.5
    assert roles["outro"].text == "А вы как думаете?" and abs(roles["outro"].end - total) < 0.01
    # пересказ сцены (слова исходника) вкладом не считается — такой вставки нет
    retell = dict(FULL, inserts=[{"after_sid": 3, "mode": "over_ducked", "vo_text": "слово40 слово41 слово42 слово43 слово44"}])
    edl2 = layout_clip(tr, **dict(kw, script=AuthorScript.model_validate(retell)), config=LayoutConfig(author_voice=False))
    assert "insert_0" not in {t.role for t in edl2.texts}
    # author_text=False — как раньше: только заголовок на весь ролик
    edl3 = layout_clip(tr, **kw, config=LayoutConfig(author_voice=False, author_text=False))
    assert [(t.role, round(t.end, 1)) for t in edl3.texts] == [("body", round(total, 1))]


def test_text_only_prompt_asks_for_short_commentary_not_description() -> None:
    from autoshorts.clipping.author.script import build_author_messages

    tr = make_transcript()
    voiced = build_author_messages(tr, 0, 9)[0]["content"]
    text = build_author_messages(tr, 0, 9, text_only=True)[0]["content"]
    assert "БЕЗ голоса автора" not in voiced
    assert "БЕЗ голоса автора" in text and "до 80 символов" in text and "НЕ описывай то, что видно" in text


def test_lag_ad_insert_freezes_mid_clip_and_voices_slogan() -> None:
    """Вставка «видео тормозит → BPN»: дёрганье, зависание, слоган; в долю автора и субтитры не идёт."""
    from autoshorts.clipping import lag_ad

    tr = make_transcript(n_sent=14)
    script = AuthorScript.model_validate(FULL)
    takes = takes_for(script)
    takes["ad_slogan"] = {"path": "/tmp/ad.wav", "duration": 4.0, "words": [Word("Тормозит?", 0.1, 0.8)], "kind": "tts", "text": lag_ad.SLOGAN}
    kw = dict(start=tr.sentences[0].s - 0.2, end=tr.sentences[-1].e + 0.3, start_sid=0, end_sid=13, script=script, source=SRC)
    edl = layout_clip(tr, **kw, takes=takes, config=LayoutConfig(lag_ad=True))
    meta = edl.meta["lag_ad"]
    stutter = [v for v in edl.video if v.role == "lag_stutter"]
    freeze = next(v for v in edl.video if v.role == "lag_ad")
    assert len(stutter) == len(lag_ad.STUTTER) and all(v.type == "freeze" and v.pan == "none" for v in stutter)
    assert stutter[0].t == pytest.approx(meta["stutter_from"], abs=1e-3) and freeze.t == pytest.approx(meta["start"], abs=1e-3)
    assert meta["dur"] == pytest.approx(lag_ad.BUFFER_SEC + 0.12 + 4.0 + lag_ad.TAIL_SEC, abs=0.05)
    ad_vo = next(v for v in edl.vo if v.role == "ad")
    assert ad_vo.at == pytest.approx(freeze.t + lag_ad.BUFFER_SEC + 0.12, abs=0.01)
    assert 6.0 <= freeze.t and freeze.end <= edl.duration - 6.0  # не на вопросе и не на развязке
    assert edl.duration <= 58.0 + 1e-6
    assert "Тормозит?" not in [w.w for w in edl.author_words()]
    assert any(p["role"] == "lag_ad" for p in edl.meta["inserts"])
    # без реплики слогана вставки нет, ролик собирается как обычно
    edl2 = layout_clip(tr, **kw, takes=takes_for(script), config=LayoutConfig(lag_ad=True))
    assert "lag_ad" not in edl2.meta and "lag_ad_not_voiced" in edl2.warnings


def test_render_command_overlays_lag_ad_last() -> None:
    from autoshorts.clipping.render_clip import RenderConfig, build_render_command

    tr = make_transcript(n_sent=14)
    script = AuthorScript.model_validate(FULL)
    takes = takes_for(script)
    takes["ad_slogan"] = {"path": "/tmp/ad.wav", "duration": 4.0, "words": [], "kind": "tts", "text": "x"}
    edl = layout_clip(tr, start=tr.sentences[0].s - 0.2, end=tr.sentences[-1].e + 0.3, start_sid=0, end_sid=13,
                      script=script, takes=takes, source=SRC, config=LayoutConfig(lag_ad=True))  # fmt: skip
    cmd = build_render_command(edl, src_path="/media/src.mp4", audio_path="mix.wav", out_path="final.mp4",
                               ass_path="captions.ass", cfg=RenderConfig())  # fmt: skip
    joined = " ".join(cmd)
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "lag_ad_spinner.png" in joined and "lag_ad_banner_bpn.png" in joined
    assert fc.index("ass=filename=captions.ass") < fc.index("[lagban]")  # плашка поверх субтитров
    assert cmd[cmd.index("-map") + 1] == "[vlag]"


def test_lag_ad_only_in_long_clips() -> None:
    from autoshorts.clipping import lag_ad

    tr = make_transcript(n_sent=7)  # ~25 с сцены — короче порога
    script = AuthorScript.model_validate(FULL)
    takes = takes_for(script)
    takes["ad_slogan"] = {"path": "/tmp/ad.wav", "duration": 4.0, "words": [], "kind": "tts", "text": lag_ad.SLOGAN}
    edl = layout_clip(tr, start=tr.sentences[0].s - 0.2, end=tr.sentences[-1].e + 0.3, start_sid=0, end_sid=6,
                      script=script, takes=takes, source=SRC, config=LayoutConfig(lag_ad=True))  # fmt: skip
    assert "lag_ad" not in edl.meta and "lag_ad_clip_short" in edl.warnings


def test_lag_ad_does_not_eat_the_scene() -> None:
    """Вступление, две подсказки на стоп-кадрах и концовка съели бы место: вставку не ставим, сцену не режем ради рекламы."""
    from autoshorts.clipping import lag_ad

    tr = make_transcript(n_sent=12)
    script = AuthorScript.model_validate(FULL)
    takes = takes_for(script, dur=8.0)
    takes["ad_slogan"] = {"path": "/tmp/ad.wav", "duration": 4.5, "words": [], "kind": "tts", "text": lag_ad.SLOGAN}
    kw = dict(start=tr.sentences[0].s - 0.2, end=tr.sentences[-1].e + 0.3, start_sid=0, end_sid=11, script=script, source=SRC)
    with_ad = layout_clip(tr, **kw, takes=takes, config=LayoutConfig(lag_ad=True))
    without = layout_clip(tr, **kw, takes=takes, config=LayoutConfig(lag_ad=False))
    assert "lag_ad" not in with_ad.meta and "lag_ad_clip_short" in with_ad.warnings
    assert with_ad.clip["end"] == without.clip["end"]  # сцена та же, что без вставки
