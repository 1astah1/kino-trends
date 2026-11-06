"""Контракт транскрипта (§6.3.2): разбиение на предложения и round-trip transcript.json.gz."""

from __future__ import annotations

import gzip
import json

import pytest

from autoshorts.clipping.transcript import (
    FORMAT_VERSION,
    Sentence,
    Transcript,
    Word,
    segment_sentences,
)


def _words(spec: list[tuple[str, float, float]]) -> list[Word]:
    return [Word(w, s, e) for w, s, e in spec]


def _flow(tokens: list[str], *, t0: float = 0.0, dur: float = 0.3, gap: float = 0.1) -> list[Word]:
    out, t = [], t0
    for tok in tokens:
        out.append(Word(tok, round(t, 3), round(t + dur, 3)))
        t += dur + gap
    return out


def _check_cover(sents: list[Sentence], n_words: int) -> None:
    assert [st.sid for st in sents] == list(range(len(sents)))
    assert sents[0].w0 == 0 and sents[-1].w1 == n_words - 1
    for prev, nxt in zip(sents, sents[1:], strict=False):
        assert nxt.w0 == prev.w1 + 1


def test_split_on_terminal_punctuation() -> None:
    words = _flow(["Привет,", "друзья.", "Как", "дела?", "Отлично!", "Ну", "что…", "поехали"])
    sents = segment_sentences(words)
    assert [st.text for st in sents] == ["Привет, друзья.", "Как дела?", "Отлично!", "Ну что…", "поехали"]
    _check_cover(sents, len(words))
    assert (sents[1].w0, sents[1].w1) == (2, 3)
    assert sents[1].s == words[2].s and sents[1].e == words[3].e


def test_closing_quotes_and_ellipsis_dots_count_as_terminal() -> None:
    words = _flow(["Он", "сказал:", "«хватит.»", "И", "ушёл...", "Всё)"])
    assert [st.text for st in segment_sentences(words)] == ["Он сказал: «хватит.»", "И ушёл...", "Всё)"]


def test_forced_split_on_long_pause() -> None:
    words = _words(
        [
            ("без", 0.0, 0.3),
            ("точек", 0.4, 0.8),
            ("вообще", 0.9, 1.3),  # пауза 1.25 > 1.2 — разрез
            ("дальше", 2.55, 2.9),
            ("идём", 3.0, 3.3),  # пауза ровно 1.2 — не разрез
            ("вперёд", 4.5, 4.9),
        ]
    )
    sents = segment_sentences(words)
    assert [st.text for st in sents] == ["без точек вообще", "дальше идём вперёд"]
    assert (sents[1].s, sents[1].e) == (2.55, 4.9)
    assert [st.text for st in segment_sentences(words, pause_split=1.0)] == [
        "без точек вообще",
        "дальше идём",
        "вперёд",
    ]


def test_max_sentence_guard_prefers_soft_punctuation_and_pauses() -> None:
    tokens = [f"w{i}" for i in range(30)]
    tokens[20] = "w20,"
    words = _flow(tokens)  # 30 слов по 0.4 с = 12 с без точек и пауз
    sents = segment_sentences(words, max_sentence_words=25)
    assert [(st.w0, st.w1) for st in sents] == [(0, 20), (21, 29)]
    assert sents[0].text.endswith("w20,")

    by_time = segment_sentences(words, max_sentence_sec=5.0)
    _check_cover(by_time, len(words))
    assert all(st.duration <= 5.0 for st in by_time)
    assert all(st.n_words >= 3 for st in by_time)  # не мелкая нарезка по одному слову


def test_guard_never_exceeds_limits_on_long_monologue() -> None:
    words = _flow([f"слово{i}" for i in range(400)], gap=0.05)
    sents = segment_sentences(words)
    _check_cover(sents, len(words))
    assert all(st.n_words <= 50 and st.duration <= 15.0 for st in sents)


def test_edge_cases() -> None:
    assert segment_sentences([]) == []
    one = segment_sentences([Word("  один  ", 1.0, 30.0)])  # одно слово длиннее лимита
    assert [(st.sid, st.text, st.w0, st.w1) for st in one] == [(0, "один", 0, 0)]


def test_save_load_roundtrip_gz(tmp_path) -> None:
    words = [
        Word("Привет,", 0.12, 0.48, 0.97),
        Word("мир.", 0.52, 0.901, None),
        Word("Второе", 2.5, 2.85, 0.5),
        Word("предложение?", 2.9, 3.6, 0.812),
    ]
    tr = Transcript.from_words(
        words,
        language="ru",
        duration=4.25,
        provider="mock",
        model="gigaam-v3-e2e-ctc",
        vad=[(0.1, 0.95), (2.45, 3.7)],
        rtfx=11.5,
        cost_usd=0.0012,
    )
    assert [st.text for st in tr.sentences] == ["Привет, мир.", "Второе предложение?"]

    path = tr.save(tmp_path / "src" / "transcript.json.gz")
    assert path.read_bytes()[:2] == b"\x1f\x8b"
    assert not list(path.parent.glob(".*.tmp"))
    loaded = Transcript.load(path)
    assert loaded == tr
    assert loaded.sentence_words(1) == words[2:]

    payload = json.loads(gzip.decompress(path.read_bytes()))
    assert payload["version"] == FORMAT_VERSION
    assert [w["sid"] for w in payload["words"]] == [0, 0, 1, 1]
    assert "p" not in payload["words"][1]

    plain = tr.save(tmp_path / "transcript.json")
    assert Transcript.load(plain) == tr


def test_save_rounds_to_milliseconds(tmp_path) -> None:
    tr = Transcript.from_words([Word("да.", 0.123456, 0.98765)], language="ru", duration=1.0, provider="mock")
    loaded = Transcript.load(tr.save(tmp_path / "t.json.gz"))
    assert (loaded.words[0].s, loaded.words[0].e) == (0.123, 0.988)
    assert loaded.model is None and loaded.rtfx is None and loaded.vad == []


def test_load_rejects_broken_sentences_and_future_version(tmp_path) -> None:
    good = Transcript.from_words(_flow(["раз.", "два."]), language="ru", duration=1.0, provider="mock")
    data = good.to_dict()
    data["sentences"][1]["w1"] = 5
    with pytest.raises(ValueError, match="bad sentence"):
        Transcript.from_dict(data)
    data = good.to_dict()
    data["version"] = FORMAT_VERSION + 1
    with pytest.raises(ValueError, match="newer"):
        Transcript.from_dict(data)
