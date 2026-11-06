"""M0: озвучка edge-tts с WordBoundary и запрет mock в prod (ARCHITECTURE §3.3 п.1, §10.2 M0)."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edge_tts.exceptions import NoAudioReceived  # noqa: E402

from autoshorts.pipeline import voice  # noqa: E402

_TICKS = 10_000_000  # edge-tts отдаёт offset/duration в 100-нс тиках


class _FakeCommunicate:
    """Подмена edge_tts.Communicate: запоминает аргументы и отдаёт заданные чанки."""

    calls: list[dict] = []
    script: list = []  # по элементу на вызов: список чанков или исключение

    def __init__(self, text, voice=None, **kwargs):
        type(self).calls.append({"text": text, "voice": voice, "kwargs": kwargs})
        self._plan = type(self).script.pop(0) if type(self).script else []

    async def stream(self):
        if isinstance(self._plan, BaseException):
            raise self._plan
        for chunk in self._plan:
            yield chunk


def _word(text, start, dur):
    return {"type": "WordBoundary", "offset": start * _TICKS, "duration": dur * _TICKS, "text": text}


def _good_stream():
    return [
        {"type": "audio", "data": b"ID3fake-mp3-frame-1"},
        _word("Карта", 0.1, 0.4),
        {"type": "audio", "data": b"fake-mp3-frame-2"},
        _word("дня", 0.5, 0.3),
    ]


@pytest.fixture
def fake_edge(monkeypatch):
    _FakeCommunicate.calls = []
    _FakeCommunicate.script = []
    monkeypatch.setattr(voice.edge_tts, "Communicate", _FakeCommunicate)
    monkeypatch.setattr(voice, "EDGE_RETRY_DELAY_SEC", 0.0)
    return _FakeCommunicate


def test_communicate_gets_word_boundary(fake_edge, tmp_path):
    fake_edge.script = [_good_stream()]
    path, words, _latency = voice.synthesize_voiceover("Карта дня", tmp_path, "job1")

    assert len(fake_edge.calls) == 1
    call = fake_edge.calls[0]
    assert call["kwargs"].get("boundary") == "WordBoundary"
    assert call["voice"] == voice.EDGE_VOICE_RU
    assert path == tmp_path / "job1_voice.mp3"
    assert path.read_bytes() == b"ID3fake-mp3-frame-1fake-mp3-frame-2"
    assert [w.word for w in words] == ["Карта", "дня"]
    assert words[0].start_sec == pytest.approx(0.1)
    assert words[0].end_sec == pytest.approx(0.5)
    assert words[1].end_sec == pytest.approx(0.8)


def test_zero_words_raises_and_removes_audio(fake_edge, tmp_path):
    # Ровно то, что edge-tts 7.x отдаёт с boundary по умолчанию: аудио и SentenceBoundary, ни одного слова.
    fake_edge.script = [[
        {"type": "audio", "data": b"fake-mp3"},
        {"type": "SentenceBoundary", "offset": 0, "duration": 5 * _TICKS, "text": "Карта дня"},
    ]]
    with pytest.raises(voice.VoiceSynthesisError):
        voice.synthesize_voiceover("Карта дня", tmp_path, "job2")
    assert not (tmp_path / "job2_voice.mp3").exists()


def test_zero_audio_bytes_raises(fake_edge, tmp_path):
    fake_edge.script = [[_word("Карта", 0.1, 0.4)]]
    with pytest.raises(voice.VoiceSynthesisError):
        voice.synthesize_voiceover("Карта", tmp_path, "job3")
    assert not (tmp_path / "job3_voice.mp3").exists()


def test_transient_transport_error_is_retried(fake_edge, tmp_path):
    fake_edge.script = [NoAudioReceived("flaky"), _good_stream()]
    _path, words, _ = voice.synthesize_voiceover("Карта дня", tmp_path, "job4")
    assert len(fake_edge.calls) == 2
    assert all(c["kwargs"].get("boundary") == "WordBoundary" for c in fake_edge.calls)
    assert len(words) == 2


def test_transient_error_gives_up_after_limit(fake_edge, tmp_path):
    fake_edge.script = [NoAudioReceived("down")] * voice.EDGE_TRANSPORT_ATTEMPTS
    with pytest.raises(NoAudioReceived):
        voice.synthesize_voiceover("Карта дня", tmp_path, "job5")
    assert len(fake_edge.calls) == voice.EDGE_TRANSPORT_ATTEMPTS
    assert not (tmp_path / "job5_voice.mp3").exists()


def test_real_synthesis_removes_stale_mock_text(fake_edge, tmp_path):
    """Иначе render может взять *_voice.mock.txt и подставить синус 220 Гц."""
    stale = tmp_path / "job6_voice.mock.txt"
    stale.write_text("старый mock", encoding="utf-8")
    fake_edge.script = [_good_stream()]
    voice.synthesize_voiceover("Карта дня", tmp_path, "job6")
    assert not stale.exists()
    assert sorted(p.name for p in tmp_path.glob("job6_voice.*")) == ["job6_voice.mp3"]


# --- запрет mock в prod ---

def test_mock_forbidden_in_prod(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("ALLOW_MOCK_IN_PROD", raising=False)
    with pytest.raises(voice.MockInProdError):
        voice.mock_synthesize_voiceover("Карта дня", tmp_path, "job7")
    assert not list(tmp_path.iterdir())


def test_mock_allowed_in_prod_with_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("ALLOW_MOCK_IN_PROD", "true")
    _path, words, _ = voice.mock_synthesize_voiceover("Карта дня", tmp_path, "job8")
    assert [w.word for w in words] == ["Карта", "дня"]


@pytest.mark.parametrize("app_env", ["", "dev", "stand", "test"])
def test_mock_allowed_outside_prod(monkeypatch, tmp_path, app_env):
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.delenv("ALLOW_MOCK_IN_PROD", raising=False)
    _path, words, _ = voice.mock_synthesize_voiceover("раз два три", tmp_path, "job9")
    assert len(words) == 3


# --- живой синтез (сеть). Включается явно: AUTOSHORTS_NETWORK_TESTS=1 ---

@pytest.mark.skipif(os.getenv("AUTOSHORTS_NETWORK_TESTS") != "1", reason="сетевой тест edge-tts выключен")
def test_real_edge_tts_returns_words(tmp_path):
    path, words, _ = voice.synthesize_voiceover(
        "Карта дня подскажет, что тебя ждёт в отношениях.", tmp_path, "live",
    )
    assert path.suffix == ".mp3" and path.stat().st_size > 1000
    assert len(words) > 0
