"""author_share строго по §6.3.8 и гейт 0,30 (критерий M5: лицензия с 0,2 → провал, own с 0,2 → показ)."""

from __future__ import annotations

import pytest

from autoshorts.clipping.author.script import AuthorScript
from autoshorts.clipping.author.share import (
    AUTHOR_SHARE_MIN,
    TEXT_PART_MAX,
    TITLE_CREDIT_SEC,
    TextEvent,
    VoiceEvent,
    compute_author_share,
    extra_needed,
    share_gate,
)
from autoshorts.clipping.edl import LayoutConfig, layout_clip
from autoshorts.clipping.transcript import Transcript, Word


def test_freeze_only_counts_fully() -> None:
    sh = compute_author_share(40.0, freeze_windows=[(10.0, 13.0), (30.0, 33.0)])
    assert sh.author_only_sec == pytest.approx(6.0)
    assert sh.value == pytest.approx(6.0 / 40.0)
    assert sh.freeze_sec == pytest.approx(6.0)


def test_cold_open_duck_threshold_12db() -> None:
    strong = compute_author_share(30.0, voice=[VoiceEvent(0.0, 3.0, "cold_open", duck_db=16.0)])
    weak = compute_author_share(30.0, voice=[VoiceEvent(0.0, 3.0, "cold_open", duck_db=6.0)])
    assert strong.author_only_sec == pytest.approx(3.0) and strong.voice_over_sec == 0
    assert weak.author_only_sec == 0 and weak.voice_over_sec == pytest.approx(3.0)
    assert strong.value == pytest.approx(3.0 / 30.0)
    assert weak.value == pytest.approx(0.5 * 3.0 / 30.0)


def test_over_ducked_insert_is_half_weight_even_with_deep_duck() -> None:
    sh = compute_author_share(30.0, voice=[VoiceEvent(10.0, 14.0, "insert", duck_db=16.0)])
    assert sh.voice_over_sec == pytest.approx(4.0)
    assert sh.value == pytest.approx(0.5 * 4.0 / 30.0)


def test_voice_over_freeze_is_author_only() -> None:
    sh = compute_author_share(
        30.0, freeze_windows=[(10.0, 13.0)], voice=[VoiceEvent(10.1, 12.8, "insert", 60.0, over_freeze=True)]
    )
    assert sh.author_only_sec == pytest.approx(3.0)
    assert sh.voice_over_sec == 0
    assert sh.own_voice_sec == pytest.approx(2.7)


def test_text_quarter_weight_and_no_double_counting() -> None:
    sh = compute_author_share(
        40.0,
        freeze_windows=[(10.0, 14.0)],
        texts=[
            TextEvent(0.0, 40.0, "title"),
            TextEvent(10.0, 14.0, "on_screen"),
            TextEvent(20, 26, "on_screen"),
        ],
    )
    # 4 с freeze (1.0); заголовок засчитан только первые 4 с (0.25), тезис 20–26 с (0.25);
    # тезис на freeze второй раз не считается
    assert sh.author_only_sec == pytest.approx(4.0)
    assert sh.text_sec == pytest.approx(TITLE_CREDIT_SEC + 6.0)
    assert sh.value == pytest.approx((4.0 + 0.25 * (TITLE_CREDIT_SEC + 6.0)) / 40.0)
    assert sh.own_text_sec == pytest.approx(40.0)  # на экране — все 40 с
    assert not sh.text_capped


def test_persistent_title_counts_only_reading_time() -> None:
    """Регрессия: заголовок на весь ролик давал ≈0,25 сам по себе."""
    sh = compute_author_share(40.0, texts=[TextEvent(0.0, 40.0, "title")])
    assert sh.text_sec == pytest.approx(TITLE_CREDIT_SEC)
    assert sh.value == pytest.approx(0.25 * TITLE_CREDIT_SEC / 40.0)
    # заголовок + 3 с хука больше не проходят гейт 0,30 (раньше было 0,306)
    sh2 = compute_author_share(
        40.0, voice=[VoiceEvent(0.12, 3.12, "cold_open", 16.0)], texts=[TextEvent(0.0, 40.0, "title")]
    )
    assert sh2.value < 0.15
    assert share_gate(sh2.value, "cc_by").failed


def test_text_part_is_capped() -> None:
    sh = compute_author_share(40.0, texts=[TextEvent(0.0, 40.0, "on_screen")])
    assert sh.text_capped
    assert sh.text_part == pytest.approx(TEXT_PART_MAX)
    assert sh.value == pytest.approx(TEXT_PART_MAX)
    assert sh.intervals["text"] == [[0.0, 16.0]]  # 0,10 · 40 / 0,25 = 16 с


def test_duplicate_text_not_counted() -> None:
    sh = compute_author_share(20.0, texts=[TextEvent(0.0, 20.0, "title", counts_as_author=False)])
    assert sh.value == 0.0 and sh.own_text_sec == 0.0


def test_value_is_capped_and_intervals_clipped() -> None:
    sh = compute_author_share(
        10.0,
        freeze_windows=[(-5.0, 20.0)],
        voice=[VoiceEvent(0.0, 10.0, "outro", 60.0)],
        texts=[TextEvent(0.0, 10.0, "title")],
    )
    assert sh.value == pytest.approx(1.0)
    assert sh.author_only_sec == pytest.approx(10.0)


def test_voice_share_shows_text_dominance() -> None:
    sh = compute_author_share(
        40.0, voice=[VoiceEvent(0, 2, "cold_open", 16.0)], texts=[TextEvent(2, 40, "on_screen")]
    )
    assert sh.voice_share == pytest.approx(2 / 40)
    assert sh.text_part > sh.voice_share


@pytest.mark.parametrize(
    ("rights", "value", "applies", "passed"),
    [
        ("licensed_written", 0.2, True, False),
        ("licensed_platform", 0.31, True, True),
        ("cc_by", 0.30, True, True),
        ("own", 0.2, False, False),
        (None, 0.5, True, True),
    ],
)
def test_gate(rights: str | None, value: float, applies: bool, passed: bool) -> None:
    g = share_gate(value, rights)
    assert g.applies is applies
    assert g.passed is passed
    assert g.failed is (applies and not passed)
    assert g.min_share == AUTHOR_SHARE_MIN


def test_gate_for_own_setting() -> None:
    assert share_gate(0.2, "own", gate_for_own=True).failed


def test_extra_needed_formulas() -> None:
    sh = compute_author_share(40.0, freeze_windows=[(0.0, 4.0)])  # 0.1
    x = extra_needed(sh, 0.3, 1.0, extends_duration=True)
    assert (4.0 + x) / (40.0 + x) == pytest.approx(0.3)
    y = extra_needed(sh, 0.3, 0.5, extends_duration=False)
    assert (4.0 + 0.5 * y) / 40.0 == pytest.approx(0.3)
    assert extra_needed(sh, 0.05, 1.0, extends_duration=True) == 0.0


# ---------------------------------------------------------------------- через EDL


def _transcript(n_sent: int = 8, lead: float = 1.0) -> Transcript:
    words: list[Word] = []
    t = lead
    for i in range(n_sent):
        for j in range(6):
            w = f"слово{i}{j}" + ("." if j == 5 else "")
            words.append(Word(w, round(t, 3), round(t + 0.4, 3)))
            t += 0.5
        t += 0.5
    return Transcript.from_words(words, language="ru", duration=t + 2, provider="test")


def _takes(script: AuthorScript, dur: float) -> dict[str, dict]:
    return {
        role: {
            "path": f"{role}.wav",
            "duration": dur,
            "words": [Word("а", 0.1, 0.4)],
            "kind": "tts",
            "text": txt,
        }
        for role, txt in script.vo_parts()
    }


def _edl(script: AuthorScript, dur: float, cfg: LayoutConfig | None = None):
    tr = _transcript()
    return layout_clip(
        tr,
        start=tr.sentences[0].s,
        end=tr.sentences[-1].e,
        script=script,
        takes=_takes(script, dur),
        source={"path": "s.mp4", "width": 1920, "height": 1080, "duration": tr.duration},
        config=cfg or LayoutConfig(min_total_sec=0),
    )


def test_licensed_clip_with_low_share_fails_gate_own_passes_with_note() -> None:
    script = AuthorScript.model_validate(
        {
            "cold_open": {"vo_text": "Коротко: почему это важно.", "duck_source": False},
            "outro": {"vo_text": ""},
        }
    )
    edl = _edl(script, 1.0)
    sh = edl.author_share()
    assert 0.0 < sh.value < AUTHOR_SHARE_MIN
    assert share_gate(sh.value, "licensed_written").failed
    own = share_gate(sh.value, "own")
    assert not own.applies and not own.failed


def test_edl_share_components_match_layout() -> None:
    script = AuthorScript.model_validate(
        {
            "cold_open": {"vo_text": "Хук автора.", "duck_source": True},
            "inserts": [
                {"after_sid": 2, "mode": "freeze_zoom", "vo_text": "Пояснение термина."},
                {"after_sid": 5, "mode": "over_ducked", "vo_text": "Короткая реплика."},
            ],
            "outro": {"vo_text": "Вопрос зрителю?", "cta_style": "question"},
            "on_screen_title": "Свой заголовок ролика",
        }
    )
    edl = _edl(script, 2.0)
    sh = edl.author_share()
    freeze = sum(b - a for a, b in edl.freeze_windows())
    assert sh.freeze_sec == pytest.approx(freeze)
    assert sh.voice_over_sec == pytest.approx(2.0, abs=0.01)  # over_ducked вставка
    assert sh.own_voice_sec == pytest.approx(8.0, abs=0.01)  # 4 реплики по 2 с
    assert 0 < sh.value <= 1
