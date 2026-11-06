"""Сборка трека нарезки: URL-источник со звуком отдельно, фабрика баннера, ASR для готового
источника, вспомогательные функции scripts/clip_local.py."""

from __future__ import annotations

import importlib.util
import json
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoshorts.clipping.edl import LayoutConfig
from autoshorts.clipping.produce import ClipJobConfig, ClipSource, mix_spec_from_edl, produce_clip
from autoshorts.clipping.source_ingest import PreparedSource, transcribe_prepared, validate_rights
from autoshorts.clipping.transcript import Transcript, Word

ROOT = Path(__file__).resolve().parents[2]
CC = {
    "rights_type": "cc_by",
    "owner_name": "Канал автора",
    "proof_note": "лицензия на странице",
    "attribution_text": "«Лекция», Канал автора, CC BY 3.0",
    "license": "CC BY 3.0",
}


def _transcript(n_sent: int = 12, lead: float = 12.0) -> Transcript:
    words: list[Word] = []
    t = lead
    for i in range(n_sent):
        for j in range(6):
            words.append(Word(f"слово{i}{j}" + ("." if j == 5 else ""), round(t, 3), round(t + 0.4, 3)))
            t += 0.5
        t += 0.6
    return Transcript.from_words(words, language="ru", duration=t + 20, provider="test")


def _prepared(tmp_path: Path, files: dict[str, str], probe: dict | None = None) -> PreparedSource:
    for name in files.values():
        (tmp_path / name).write_bytes(b"x")
    return PreparedSource(
        source_id="url_abc",
        dir=tmp_path,
        title="Лекция",
        origin="url",
        rights=dict(CC),
        rights_warnings=["cc_by_attribution_in_description"],
        probe=probe or {"width": 960, "height": 540, "duration": 120.0, "fps": 25.0, "has_audio": False},
        sha256=None,
        files=files,
    )


def test_url_source_takes_audio_from_separate_file(tmp_path: Path) -> None:
    prep = _prepared(
        tmp_path,
        {
            "original_video": "original_video.mp4",
            "original_audio": "original_audio.m4a",
            "audio": "audio.m4a",
        },
    )
    src = ClipSource.from_prepared(prep)
    assert src.render_path.name == "original_video.mp4"
    assert src.audio_path is not None and src.audio_path.name == "audio.m4a"
    assert src.has_audio  # видео без звука, но звук источника есть
    assert src.mix_audio_path == src.audio_path
    assert src.info()["audio_path"].endswith("audio.m4a")


def test_upload_source_keeps_audio_in_video(tmp_path: Path) -> None:
    prep = _prepared(
        tmp_path,
        {"original": "original.mp4", "audio": "audio.m4a"},
        probe={"width": 1280, "height": 720, "duration": 60.0, "fps": 30.0, "has_audio": True},
    )
    src = ClipSource.from_prepared(prep)
    assert src.audio_path is None
    assert src.mix_audio_path == src.render_path


def test_mix_spec_uses_audio_offset_not_section_offset(tmp_path: Path) -> None:
    from autoshorts.clipping.author.script import AuthorScript
    from autoshorts.clipping.edl import layout_clip

    tr = _transcript()
    script = AuthorScript.model_validate({"cold_open": {"vo_text": "Хук автора.", "duck_source": True}})
    takes = {"cold_open": {"path": "c.wav", "duration": 2.0, "words": [], "kind": "tts", "text": "Хук."}}
    edl = layout_clip(
        tr,
        start=tr.sentences[2].s,
        end=tr.sentences[8].e,
        script=script,
        takes=takes,
        source={"path": "s.mp4", "width": 1920, "height": 1080, "duration": tr.duration},
        config=LayoutConfig(min_total_sec=0),
    )
    # 1080p-секция начинается с 10 с источника, звук — полный audio.m4a с нуля
    src = ClipSource(
        Path("/m/section.mp4"), Path("/m/proxy.mp4"), 1920, 1080, 60.0, src_offset=10.0,
        audio_path=Path("/m/audio.m4a"),
    )  # fmt: skip
    spec = mix_spec_from_edl(edl, src)
    first_src = next(v for v in edl.video if v.type == "src")
    assert spec.source == Path("/m/audio.m4a")
    assert spec.pieces[0].start == pytest.approx(first_src.in_)
    # без отдельного звука — прежнее поведение: смещение секции
    src2 = ClipSource(Path("/m/section.mp4"), Path("/m/proxy.mp4"), 1920, 1080, 60.0, src_offset=10.0)
    spec2 = mix_spec_from_edl(edl, src2)
    assert spec2.source == Path("/m/section.mp4")
    assert spec2.pieces[0].start == pytest.approx(first_src.in_ - 10.0)


def test_banner_factory_gets_final_duration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Фабрика плана баннера вызывается после раскладки с итоговой длительностью ролика."""
    from autoshorts.clipping import reframe

    monkeypatch.setattr(
        reframe,
        "plan_reframe",
        lambda video, a, b, *, src_w, src_h, **kw: reframe.static_plan(src_w, src_h, a, b, "center"),
    )
    tr = _transcript()
    calls: list[dict] = []

    class FakePlan:
        items: list = []
        captions_zone = None

        def to_json(self) -> dict:
            return {"v": 1, "windows": {"with_creative": [[5.0, 9.0]]}}

    def factory(**kw):  # noqa: ANN003, ANN202
        calls.append(kw)
        return FakePlan()

    src = ClipSource(tmp_path / "s.mp4", tmp_path / "s.mp4", 1920, 1080, tr.duration, rights=CC)
    res = produce_clip(
        src,
        tr,
        start=tr.sentences[2].s,
        end=tr.sentences[9].e,
        workdir=tmp_path / "w",
        config=ClipJobConfig(tts="mock", detect_faces=False, detect_scenes=False, run_qc=False),
        banner_plan=factory,
        render=False,
    )
    assert len(calls) == 1
    assert calls[0]["duration_sec"] == pytest.approx(res.edl.duration)
    assert calls[0]["out_size"] == (1080, 1920)
    assert res.edl.banner == {"v": 1, "windows": {"with_creative": [[5.0, 9.0]]}}
    assert json.loads((tmp_path / "w" / "edl.json").read_text())["banner"]["v"] == 1
    # шаблонный черновик честно помечен и не проходит как авторский текст
    assert res.author.mode == "heuristic"
    # чужие права (CC BY): неотредактированный шаблон — стоп, а не совет (OWNER-DECISIONS п. 5)
    assert res.originality.check("author_text").status == "fail"


def test_banner_factory_failure_renders_without_banner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoshorts.clipping import reframe

    monkeypatch.setattr(
        reframe,
        "plan_reframe",
        lambda video, a, b, *, src_w, src_h, **kw: reframe.static_plan(src_w, src_h, a, b, "center"),
    )
    tr = _transcript()

    def factory(**kw):  # noqa: ANN003, ANN202
        raise ValueError("creative missing")

    src = ClipSource(tmp_path / "s.mp4", tmp_path / "s.mp4", 1920, 1080, tr.duration, rights=CC)
    res = produce_clip(
        src,
        tr,
        start=tr.sentences[2].s,
        end=tr.sentences[9].e,
        workdir=tmp_path / "w",
        config=ClipJobConfig(tts="mock", detect_faces=False, detect_scenes=False, run_qc=False),
        banner_plan=factory,
        render=False,
    )
    assert res.edl.banner is None
    assert any(w.startswith("banner_plan_failed") for w in res.warnings)


def _wav(path: Path, sec: float = 1.0) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * int(16000 * sec))


def test_transcribe_prepared_saves_transcript_and_drops_wav(tmp_path: Path) -> None:
    from autoshorts.providers.asr import get_asr_provider

    prep = _prepared(tmp_path, {"original": "original.mp4"})
    _wav(tmp_path / "audio16k.wav")
    prep.files["audio16k"] = "audio16k.wav"
    asr = get_asr_provider("mock", text="Первое предложение. Второе предложение!")
    out = transcribe_prepared(prep, asr, min_mem_mb=0)
    assert out.files["transcript"] == "transcript.json.gz"
    assert "audio16k" not in out.files and not (tmp_path / "audio16k.wav").exists()
    tr = Transcript.load(tmp_path / "transcript.json.gz")
    assert len(tr.sentences) == 2
    saved = json.loads((tmp_path / "source.json").read_text(encoding="utf-8"))
    assert saved["transcript"]["sentences"] == 2


# ---------------------------------------------------------------------- scripts/clip_local.py


@pytest.fixture(scope="module")
def clip_local():  # noqa: ANN201
    path = ROOT / "scripts" / "clip_local.py"
    spec = importlib.util.spec_from_file_location("clip_local_script", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["clip_local_script"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_clip_local_parser_and_rights(clip_local) -> None:  # noqa: ANN001
    a = clip_local.build_parser().parse_args(
        [
            "--url", "https://example.org/v", "--rights", "cc_by", "--owner", "Канал",
            "--license", "CC BY 3.0", "--attribution", "«Лекция», Канал, CC BY 3.0",
            "--proof", "лицензия на странице", "--top", "2", "--banner", "b.png", "--banner-clips", "2",
        ]
    )  # fmt: skip
    rec, warns = validate_rights(clip_local._rights_raw(a))
    assert rec.rights_type.value == "cc_by"
    assert "cc_by_attribution_in_description" in warns
    assert clip_local._parse_index_set(a.banner_clips, 3) == {2}
    assert clip_local._parse_index_set("all", 3) == {1, 2, 3}
    assert a.asr == "gigaam_v3_onnx" and a.url_mode == "full"


def test_clip_local_rejects_sa_license_before_network(clip_local, tmp_path: Path) -> None:  # noqa: ANN001
    a = clip_local.build_parser().parse_args(
        [
            "--url", "https://example.org/v", "--rights", "cc_by", "--owner", "Канал",
            "--license", "CC BY-SA 4.0", "--attribution", "x", "--proof", "страница",
        ]
    )  # fmt: skip
    log = clip_local.RunLog(tmp_path / "run.log")
    with pytest.raises(SystemExit) as exc:
        clip_local._check_rights(a, log)
    log.close()
    assert exc.value.code == 2
    assert "license_incompatible" in (tmp_path / "run.log").read_text(encoding="utf-8")


def test_clip_local_history_roundtrip(clip_local, tmp_path: Path) -> None:  # noqa: ANN001
    prep = SimpleNamespace(dir=tmp_path, source_id="src_1")
    assert clip_local._load_history(prep) == []
    clip_local._save_history(prep, [{"id": "auto/clip_01", "sids": [3, 4, 5], "author_text": "хук"}])
    assert clip_local._load_history(prep)[0]["sids"] == [3, 4, 5]
    (tmp_path / clip_local.HISTORY_NAME).write_text("{broken", encoding="utf-8")
    assert clip_local._load_history(prep) == []


def test_clip_local_runlog_timings(clip_local, tmp_path: Path) -> None:  # noqa: ANN001
    log = clip_local.RunLog(tmp_path / "run.log")
    with log.timed("selection"):
        pass
    log("готово")
    log.close()
    text = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "selection:" in text and "готово" in text
    assert "selection" in log.timings


def test_fit_panel_is_lifted_above_banner() -> None:
    from autoshorts.clipping.edl import EDL, VideoItem
    from autoshorts.clipping.produce import fit_panel_bottom_for_banner
    from autoshorts.clipping.render_clip import build_video_graph

    banner = {
        "rects": {
            "micro_brand": {"x1": 65, "y1": 200, "x2": 297, "y2": 258},
            "creative": {"x1": 89, "y1": 1120, "x2": 866, "y2": 1310},
            "code": {"x1": 327, "y1": 1316, "x2": 628, "y2": 1366},
        }
    }
    assert fit_panel_bottom_for_banner(banner, 1920) == 1104
    assert fit_panel_bottom_for_banner({"rects": {"micro_brand": {"y1": 200}}}, 1920) is None
    assert fit_panel_bottom_for_banner(None, 1920) is None

    edl = EDL(
        video=[VideoItem(type="src", t=0.0, dur=2.0, frames=60, in_=10.0, out=12.0, reframe="fit_blur")]
    )
    centred = ";".join(build_video_graph(edl, "s.mp4").chains)
    assert "overlay=(W-w)/2:(H-h)/2" in centred
    edl.meta["fit_panel_bottom"] = 1104
    lifted = ";".join(build_video_graph(edl, "s.mp4").chains)
    assert "y='max(0,min((H-h)/2,1104-h))'" in lifted


def test_clip_local_asr_runs_in_child_process(clip_local, tmp_path: Path) -> None:  # noqa: ANN001
    """ASR в дочернем процессе: транскрипт появляется, манифест перечитывается, wav удаляется."""
    prep = _prepared(tmp_path, {"original": "original.mp4"})
    _wav(tmp_path / "audio16k.wav", 2.0)
    prep.files["audio16k"] = "audio16k.wav"
    prep.save()
    a = clip_local.build_parser().parse_args(["--source-dir", str(tmp_path), "--asr", "mock"])
    log = clip_local.RunLog(tmp_path / "run.log")
    tr, prep2 = clip_local._ensure_transcript(prep, a, log)
    log.close()
    assert tr.provider == "mock" and len(tr.words) > 10
    assert prep2 is not prep and prep2.files["transcript"] == "transcript.json.gz"
    assert not (tmp_path / "audio16k.wav").exists()
    assert "asr" in (tmp_path / "run.log").read_text(encoding="utf-8")


def test_loudnorm_retries_when_aac_overshoots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AAC добавляет выброс пика поверх loudnorm: второй проход повторяется с более низким TP."""
    from autoshorts.pipeline import audio_mix

    cmds: list[list[str]] = []
    encoded = iter([0.5, -1.3])  # true peak файла после кодирования: сначала выше потолка

    def fake_run(cmd, **kw):  # noqa: ANN001, ANN003, ANN202
        cmds.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"aac")
        return SimpleNamespace(stderr_tail="")

    def fake_measure(path, **kw):  # noqa: ANN001, ANN003, ANN202
        if Path(path).name == "in.wav":
            return {"input_i": -17.5, "input_tp": 0.2, "input_lra": 5.0, "input_thresh": -28.0,
                    "target_offset": 0.1}  # fmt: skip
        return {"input_i": -14.1, "input_tp": next(encoded), "input_lra": 4.7, "input_thresh": -24.6,
                "target_offset": 0.0}  # fmt: skip

    monkeypatch.setattr(audio_mix, "_run", fake_run)
    monkeypatch.setattr(audio_mix, "measure_loudness", fake_measure)
    (tmp_path / "in.wav").write_bytes(b"x")
    out = audio_mix.two_pass_loudnorm(tmp_path / "in.wav", tmp_path / "mix.m4a", workdir=tmp_path)
    assert len(cmds) == 2
    assert "TP=-1:" in cmds[0][cmds[0].index("-af") + 1]
    # −1,0 − (0,5 − (−1,0)) − 0,3 = −2,8
    assert "TP=-2.8:" in cmds[1][cmds[1].index("-af") + 1]
    assert out["tp_used"] == pytest.approx(-2.8) and out["encoded_tp"] == pytest.approx(-1.3)
    assert (tmp_path / "mix.m4a").read_bytes() == b"aac"


def test_top_banner_moves_title_out_from_under_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Регрессия: при верхнем баннере свой заголовок (засчитанный в author_share) оказывался под
    плашкой, а проверка перекрытия работала только без баннера."""
    from autoshorts.clipping import reframe
    from autoshorts.clipping.author.script import AuthorScript

    monkeypatch.setattr(
        reframe,
        "plan_reframe",
        lambda video, a, b, *, src_w, src_h, **kw: reframe.static_plan(src_w, src_h, a, b, "center"),
    )
    tr = _transcript()

    class TopPlan:
        items: list = []
        captions_zone = SimpleNamespace(rect=SimpleNamespace(x=65, y=820, x2=895, y2=1080))

        def to_json(self) -> dict:
            return {
                "v": 1,
                "out_size": [1080, 1920],
                "rects": {"creative": {"x1": 65, "y1": 230, "x2": 890, "y2": 420}},
                "items": [{"id": "a", "x": 65, "y": 230, "w": 825, "h": 190}],
            }

    op = AuthorScript.model_validate(
        {
            "cold_open": {"vo_text": "Мой хук про главное.", "on_screen": "Тезис автора на экране"},
            "outro": {"vo_text": "А вы как думаете?"},
            "on_screen_title": "Мой заголовок про лекцию",
        }
    )
    src = ClipSource(tmp_path / "s.mp4", tmp_path / "s.mp4", 1920, 1080, tr.duration, rights=CC)
    res = produce_clip(
        src,
        tr,
        start=tr.sentences[2].s,
        end=tr.sentences[9].e,
        workdir=tmp_path / "w",
        author_script=op,
        config=ClipJobConfig(tts="mock", detect_faces=False, detect_scenes=False, run_qc=False),
        banner_plan=TopPlan(),
        render=False,
    )
    assert res.edl.meta["caption_zones_moved"] == ["title_zone:280->436", "onscreen_zone:480->612"]
    assert not any(w.startswith("captions_overlap_banner") for w in res.warnings)
    styles = {
        ln.split(",")[0].split(":")[1].strip(): ln.split(",")
        for ln in (tmp_path / "w" / "captions.ass").read_text(encoding="utf-8").splitlines()
        if ln.startswith("Style:")
    }
    assert styles["Title"][-2] == "436" and styles["OnScreen"][-2] == "612"  # MarginV от верха


def test_banner_covering_all_free_space_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from autoshorts.clipping import reframe

    monkeypatch.setattr(
        reframe,
        "plan_reframe",
        lambda video, a, b, *, src_w, src_h, **kw: reframe.static_plan(src_w, src_h, a, b, "center"),
    )
    tr = _transcript()

    class HugePlan:
        items: list = []
        captions_zone = SimpleNamespace(rect=SimpleNamespace(x=65, y=1100, x2=895, y2=1360))

        def to_json(self) -> dict:
            return {
                "v": 1,
                "out_size": [1080, 1920],
                "rects": {"c": {"x1": 0, "y1": 150, "x2": 1080, "y2": 1090}},
            }

    src = ClipSource(tmp_path / "s.mp4", tmp_path / "s.mp4", 1920, 1080, tr.duration, rights=CC)
    res = produce_clip(
        src,
        tr,
        start=tr.sentences[2].s,
        end=tr.sentences[9].e,
        workdir=tmp_path / "w",
        title="Лекция о главном для зрителя",
        config=ClipJobConfig(tts="mock", detect_faces=False, detect_scenes=False, run_qc=False),
        banner_plan=HugePlan(),
        render=False,
    )
    assert any(w.startswith("captions_overlap_banner:") and "title_zone" in w for w in res.warnings)


def test_blocked_author_script_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from autoshorts.clipping import reframe

    monkeypatch.setattr(
        reframe,
        "plan_reframe",
        lambda video, a, b, *, src_w, src_h, **kw: reframe.static_plan(src_w, src_h, a, b, "center"),
    )
    tr = _transcript()
    src = ClipSource(tmp_path / "s.mp4", tmp_path / "s.mp4", 1920, 1080, tr.duration, rights=CC)
    res = produce_clip(
        src,
        tr,
        start=tr.sentences[2].s,
        end=tr.sentences[9].e,
        workdir=tmp_path / "w",
        title="Гарантированно вылечит за неделю",  # шаблонный черновик берёт его в заголовок
        config=ClipJobConfig(tts="mock", detect_faces=False, detect_scenes=False, run_qc=False),
        render=False,
    )
    assert res.author.mode == "heuristic" and res.author.blocked
    assert any(w.startswith("author_script_blocked:content_flag") for w in res.warnings)
    assert res.originality.check("author_text").status == "fail"
    assert res.originality.verdict == "fail"
