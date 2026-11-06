"""Режим «сцена» в отборе: маркеры структуры в промпте, границы по склейкам, пресет скоринга и ворота
законченности, кадры в уточняющем проходе, эвристика диалогов."""

from __future__ import annotations

import pytest

from autoshorts.clipping.features import EnergyTrack
from autoshorts.clipping.llm_client import _with_json_instructions, message_text
from autoshorts.clipping.llm_select import (
    LLMSelectConfig,
    build_detail_messages,
    build_window_messages,
    make_mock_client,
    mock_highlight_responder,
)
from autoshorts.clipping.scoring import (
    SCENE_LLM_WEIGHTS,
    ClipCandidate,
    NicheProfile,
    ScoringConfig,
    load_niche_profile,
)
from autoshorts.clipping.selection import SelectionConfig, apply_gates, select_clips
from autoshorts.clipping.snapping import snap_clip_to_words
from autoshorts.clipping.transcript import Transcript, Word
from autoshorts.clipping.windows import Markers, build_windows, marker_line, render_sentences
from autoshorts.domain.niche_schema import NicheConfig

_DIALOGUE = [
    "Ты уверен, что это сработает?",
    "Нет.",
    "Тогда зачем мы здесь?",
    "Потому что другого выхода нет!",
    "Через час они будут у ворот.",
    "Значит, у нас есть час.",
    "Открывай люк.",
    "Заводи!",
]


def _dialogue(gap_before: dict[int, float] | None = None, repeats: int = 6) -> Transcript:
    """Диалог: короткие реплики, между некоторыми длинные паузы (там и смены сцен)."""
    gap_before = gap_before or {}
    words: list[Word] = []
    t = 0.5
    k = 0
    for _ in range(repeats):
        for line in _DIALOGUE:
            t += gap_before.get(k % len(_DIALOGUE), 0.7)
            for w in line.split():
                words.append(Word(w, round(t, 3), round(t + 0.3, 3)))
                t += 0.38
            k += 1
    return Transcript.from_words(words, language="ru", duration=round(t + 2.0, 3), provider="mock")


def test_marker_lines_describe_pauses_silence_and_cuts() -> None:
    tr = _dialogue({0: 6.0, 4: 1.4})
    s = tr.sentences
    big = next(i for i in range(1, len(s)) if s[i].s - s[i - 1].e >= 5.5)
    cut_t = s[big - 1].e + 1.0
    m = Markers(scene_cuts=(cut_t,), pause_sec=1.0, silence_sec=4.0)
    line = marker_line(s[big - 1], s[big], m)
    assert line is not None and "без слов" in line and "смена сцены" in line
    small = next(i for i in range(1, len(s)) if 1.2 <= s[i].s - s[i - 1].e < 2.0)
    assert "пауза" in (marker_line(s[small - 1], s[small], m) or "")
    assert marker_line(s[1], s[2], m) is None  # 0,7 с — обычный зазор между репликами
    text = render_sentences(s, 0, len(s) - 1, m)
    assert "— без слов 6 с, смена сцены —" in text
    assert text.count("[громко]") == 0


def test_loud_tag_from_energy() -> None:
    tr = _dialogue()
    s = tr.sentences
    dur = tr.duration
    n = int(dur * 2)
    data = [0.1] * n
    a, b = int(s[3].s * 2), int(s[3].e * 2) + 1
    for i in range(a, min(b, n)):
        data[i] = 0.9
    energy = _energy_from(data, dur)
    m = Markers(energy=energy)
    assert render_sentences(s, 3, 3, m).endswith("[громко]")
    assert "[громко]" not in render_sentences(s, 1, 1, m)


def _energy_from(data: list[float], duration: float) -> EnergyTrack:
    from autoshorts.clipping.features import energy_from_peaks

    return energy_from_peaks({"data": data}, duration)


def test_snap_moves_edges_onto_scene_cuts_without_breaking_limits() -> None:
    words = [Word("a", 10.0, 10.3), Word("b", 10.4, 10.7), Word("c", 10.8, 11.2), Word("d", 20.0, 20.3)]
    # склейка за 0,9 с до первого слова и через 1,0 с после последнего
    snap = snap_clip_to_words(words, 10.0, 11.2, scene_cuts=[9.1, 12.2, 30.0])
    assert snap.start == 9.1 and snap.end == pytest.approx(12.16)
    # слишком далёкая склейка (2,5 с) не берётся — обычный хвост
    snap2 = snap_clip_to_words(words, 10.0, 11.2, scene_cuts=[7.0, 13.7])
    assert snap2.start == pytest.approx(9.65) and snap2.end <= 11.65
    # лимит длины: расширение до склейки перевалило бы за max_duration → остаёмся на словах
    snap3 = snap_clip_to_words(words, 10.0, 11.2, scene_cuts=[9.1, 12.2], max_duration=2.0)
    assert snap3.end - snap3.start <= 2.0 + 1e-6
    # склейка не должна зацепить соседнее слово: между c и d слово d стартует в 20.0, склейка 12.2 — ок,
    # а склейка в 10.75 (между b и c) началом не берётся, потому что она после первого слова
    snap4 = snap_clip_to_words(words, 10.0, 11.2, scene_cuts=[10.75])
    assert snap4.start == pytest.approx(9.65)


def test_scene_mode_from_niche_applies_preset_and_gates() -> None:
    niche = NicheProfile(id="kino", label="Кино", selection_mode="scene", min_payoff=6)
    cfg = SelectionConfig.from_options({"target_clip_sec": 35}, {"spacing_sec": 100}, niche=niche)
    assert cfg.mode == "scene" and cfg.llm.mode == "scene"
    assert cfg.scoring.llm_weights == SCENE_LLM_WEIGHTS
    assert cfg.scoring.min_payoff == 6 and cfg.scoring.min_emotion == 4.0
    assert cfg.scoring.spacing_sec == 100  # явная настройка поверх пресета
    talk = SelectionConfig.from_options({}, None, niche=NicheProfile(id="vpn"))
    assert talk.mode == "talk" and talk.scoring.min_payoff is None
    weak = ClipCandidate(start_sid=0, end_sid=1, hook_sid=0, start_sec=0, end_sec=20,
                         llm_scores={"payoff": 3, "emotion": 8})  # fmt: skip
    strong = ClipCandidate(start_sid=2, end_sid=3, hook_sid=2, start_sec=30, end_sec=55,
                           llm_scores={"payoff": 7, "emotion": 6})  # fmt: skip
    manual = ClipCandidate(start_sid=None, end_sid=None, hook_sid=None, start_sec=60, end_sec=80,
                           llm_scores={"payoff": 1, "emotion": 1}, origin="manual")  # fmt: skip
    dropped: list[dict] = []
    kept = apply_gates([weak, strong, manual], cfg, dropped)
    assert kept == [strong, manual]
    assert dropped[0]["reason"] == "weak_payoff"


def test_scene_prompts_and_legend_are_used() -> None:
    tr = _dialogue({0: 6.0})
    cfg = LLMSelectConfig(mode="scene")
    windows = build_windows(tr.sentences, window_sec=cfg.window_sec, overlap_sec=cfg.overlap_sec)
    m = Markers(scene_cuts=(tr.sentences[8].s - 1.0,))
    msgs = build_window_messages(tr, windows[:1], cfg, None, m)
    assert "ЗАКОНЧЕННЫЕ СЦЕНЫ" in msgs[0]["content"] and "клиффхэнгер" in msgs[0]["content"]
    assert "смены сцены по видео" in msgs[1]["content"]  # легенда маркеров
    assert "без слов 6 с, смена сцены —" in msgs[1]["content"]  # маркер между репликами
    plain = build_window_messages(tr, windows[:1], LLMSelectConfig(), None, None)
    assert "ЗАКОНЧЕННЫЕ СЦЕНЫ" not in plain[0]["content"] and "Строки «—" not in plain[1]["content"]


def test_detail_messages_attach_frames_and_json_tail_survives() -> None:
    tr = _dialogue()
    cand = ClipCandidate(start_sid=0, end_sid=5, hook_sid=0, start_sec=tr.sentences[0].s,
                         end_sec=tr.sentences[5].e, title="Час до штурма")  # fmt: skip
    cfg = LLMSelectConfig(mode="scene")
    msgs, _ = build_detail_messages(tr, cand, cfg, None, None, "data:image/jpeg;base64,AAAA")
    content = msgs[1]["content"]
    assert isinstance(content, list) and content[1]["type"] == "image_url"
    assert "приложены кадры" in content[0]["text"]
    tailed = _with_json_instructions(msgs, "json_object", {"type": "object"})
    parts = tailed[1]["content"]
    assert parts[-1]["type"] == "text" and "JSON" in parts[-1]["text"]
    assert "start_sid=0, end_sid=5" in message_text(parts)
    # мок-модель читает текст и из списка частей
    body = mock_highlight_responder(msgs, "json_object")
    assert '"candidates"' in body
    plain, _ = build_detail_messages(tr, cand, cfg)
    assert isinstance(plain[1]["content"], str) and "приложены кадры" not in plain[1]["content"]


def test_select_clips_scene_mode_end_to_end_with_mock() -> None:
    tr = _dialogue({0: 6.0, 4: 1.4}, repeats=10)
    cuts = [s.s - 0.8 for s in tr.sentences if s.sid % len(_DIALOGUE) == 0 and s.sid > 0]
    niche = NicheProfile(id="kino", selection_mode="scene", min_payoff=0, min_emotion=0)
    cfg = SelectionConfig.from_options({"target_clip_sec": 25, "min_clip_sec": 12}, None, niche=niche)
    cfg.scene_cuts = cuts
    calls: list[tuple[float, float]] = []

    def frames(a: float, b: float) -> str | None:
        calls.append((a, b))
        return "data:image/jpeg;base64,QUJD"

    res = select_clips(tr, llm=make_mock_client(target=25), niche=niche, config=cfg, frames=frames)
    assert res.mode == "mock" and res.candidates
    assert calls, "уточняющий проход должен запрашивать кадры"
    assert any(e.get("frames") for e in res.llm_events if e.get("pass") == "detail")
    for c in res.candidates:
        assert c.duration <= cfg.max_clip_sec + 1e-6


def test_scene_heuristic_prefers_punchlines() -> None:
    from autoshorts.clipping.heuristic_select import pseudo_scores

    tr = _dialogue()
    s = tr.sentences
    scene_scores, _, reasons = pseudo_scores(s[0:4], scene=True)
    assert scene_scores["payoff"] >= 6.5 and scene_scores["emotion"] >= 4.5
    assert "восклицание в конце" in reasons and "обмен репликами" in reasons
    flat, _, _ = pseudo_scores(s[4:6], scene=True)  # «Через час…» → «Значит, у нас есть час.» — ровно
    assert flat["payoff"] <= scene_scores["payoff"] and flat["emotion"] < scene_scores["emotion"]
    cliff, _, r2 = pseudo_scores(s[0:3], scene=True)
    assert "вопрос-клиффхэнгер" in r2 and cliff["payoff"] >= 4.5


def test_niche_schema_accepts_selection_and_yaml_profile_reads_it(tmp_path) -> None:
    cfg = NicheConfig.model_validate(
        {"id": "kino", "label": "Кино", "selection": {"mode": "scene", "min_payoff": 5}}
    )
    assert cfg.selection.mode == "scene" and cfg.selection.frames_in_detail is True
    with pytest.raises(ValueError):
        NicheConfig.model_validate({"selection": {"mode": "epic"}})
    y = tmp_path / "kino.yaml"
    y.write_text("id: kino\nlabel: Кино\nselection:\n  mode: scene\n  min_emotion: 3\n", encoding="utf-8")
    prof = load_niche_profile(y)
    assert prof.selection_mode == "scene" and prof.min_emotion == 3.0 and prof.min_payoff is None


def test_repo_kino_niche_is_scene_mode() -> None:
    prof = load_niche_profile("kino")
    assert prof.selection_mode == "scene"
    assert ScoringConfig.from_mapping({"min_payoff": 5}).gate_reason({"payoff": 4}) == "weak_payoff"
