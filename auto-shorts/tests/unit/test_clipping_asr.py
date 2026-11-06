"""ASR providers: mock, token->word merging, punctuation transfer, SpeechKit v3 and OpenAI-compatible
payload parsing with mocked HTTP (no network, no models, no ffmpeg except where noted)."""

from __future__ import annotations

import json
import wave
from pathlib import Path

import httpx
import pytest

from autoshorts.clipping.transcript import Transcript, Word
from autoshorts.providers.asr import ALIASES, PROVIDERS, get_asr_provider, provider_class
from autoshorts.providers.asr.base import (
    AsrBadResponse,
    AsrProvider,
    AsrTransient,
    AsrUnavailable,
    attach_punctuation,
    words_from_tokens,
)
from autoshorts.providers.asr.mock import MockAsrProvider, synth_words
from autoshorts.providers.asr.openai_compat import OpenAICompatAsrProvider, parse_verbose_json
from autoshorts.providers.asr.yandex_speechkit import (
    YandexSpeechKitProvider,
    build_request,
    iter_events,
    parse_recognition,
)

# ---------------------------------------------------------------------- helpers


def assert_contract(tr: Transcript) -> None:
    """Invariants every provider output must satisfy (the transcript contract)."""
    assert tr.words, "no words"
    for a, b in zip(tr.words, tr.words[1:], strict=False):
        assert a.s <= b.s, "words must be sorted by start"
    for w in tr.words:
        assert w.e > w.s >= 0
        assert w.w.strip() == w.w and w.w
    assert [s.sid for s in tr.sentences] == list(range(len(tr.sentences)))
    covered = [i for s in tr.sentences for i in range(s.w0, s.w1 + 1)]
    assert covered == list(range(len(tr.words))), "sentences must cover all words exactly once, in order"
    for s in tr.sentences:
        assert s.s == tr.words[s.w0].s
        assert s.text == " ".join(w.w for w in tr.words[s.w0 : s.w1 + 1])
    assert tr.duration >= tr.words[-1].e - 1e-6


def write_silence_wav(path: Path, seconds: float = 1.0, sr: int = 16000) -> Path:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(b"\x00\x00" * int(seconds * sr))
    return path


# ---------------------------------------------------------------------- mock + registry


def test_mock_provider_contract_and_sentences(tmp_path: Path) -> None:
    text = "Привет! Как дела?\n\nВторой абзац без точки в конце"
    p = MockAsrProvider(text=text)
    assert isinstance(p, AsrProvider)
    tr = p.transcribe(tmp_path / "missing.wav", "ru-RU")
    assert_contract(tr)
    assert tr.provider == "mock" and tr.language == "ru"
    assert [s.text for s in tr.sentences] == ["Привет!", "Как дела?", "Второй абзац без точки в конце"]
    # paragraph pause (1.5 s) is a forced split even without punctuation
    gap = tr.words[3].s - tr.words[2].e
    assert gap > 1.2
    assert tr.vad and tr.vad[0][0] == tr.words[0].s
    # deterministic
    assert MockAsrProvider(text=text).transcribe("x").to_dict() == tr.to_dict()


def test_mock_provider_fixture_roundtrip(tmp_path: Path) -> None:
    words = synth_words("Один. Два три. Четыре?")
    src = Transcript.from_words(words, language="ru", duration=10.0, provider="gigaam_v3_onnx", model="m")
    path = src.save(tmp_path / "t.json.gz")
    tr = MockAsrProvider(fixture=path).transcribe("any.wav")
    assert tr.provider == "mock"
    assert tr.words == src.words and tr.sentences == src.sentences
    assert tr.duration == 10.0


def test_mock_uses_wav_duration(tmp_path: Path) -> None:
    wav = write_silence_wav(tmp_path / "a.wav", seconds=30.0)
    tr = MockAsrProvider(text="Коротко.").transcribe(wav)
    assert tr.duration == pytest.approx(30.0)


def test_mock_refused_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("ALLOW_MOCK_IN_PROD", raising=False)
    with pytest.raises(AsrUnavailable):
        MockAsrProvider()
    monkeypatch.setenv("ALLOW_MOCK_IN_PROD", "true")
    assert MockAsrProvider().transcribe("x").words


def test_registry_keys_and_aliases() -> None:
    assert set(PROVIDERS) == {"gigaam_v3_onnx", "yandex_speechkit", "openai_compat_asr", "mock"}
    assert provider_class("gigaam").__name__ == "GigaamOnnxProvider"
    assert provider_class("speechkit").__name__ == "YandexSpeechKitProvider"
    assert all(v in PROVIDERS for v in ALIASES.values())
    assert isinstance(get_asr_provider("mock", text="Да."), MockAsrProvider)
    with pytest.raises(ValueError):
        provider_class("whisper_cpp")


def test_gigaam_without_models_is_unavailable(tmp_path: Path) -> None:
    from autoshorts.providers.asr.gigaam_onnx import GigaamOnnxProvider, iter_ranges

    with pytest.raises(AsrUnavailable, match="models_fetch"):
        GigaamOnnxProvider(models_root=tmp_path)
    assert list(iter_ranges(1500.0, 600.0)) == [(0.0, 600.0), (600.0, 1200.0), (1200.0, 1500.0)]


# ---------------------------------------------------------------------- tokens -> words

# real GigaAM v3 e2e CTC output (onnx-asr: "▁" already replaced by a space, 40 ms frames)
GIGAAM_SEG = [
    (" С", 0.0), ("е", 0.12), ("го", 0.2), ("д", 0.28), ("н", 0.36), ("я", 0.4), (" ", 0.48),
    ("м", 0.52), ("ы", 0.56), (" по", 0.68), ("го", 0.76), ("во", 0.84), ("ри", 0.92), ("м", 1.0),
    (" о", 1.08), (" ", 1.16), ("то", 1.24), ("м", 1.32), (",", 1.52), (" как", 1.64), (" у", 1.88),
    ("ст", 2.0), ("ро", 2.04), ("е", 2.16), ("ны", 2.28), (" не", 2.48), ("й", 2.6), ("ро", 2.72),
    ("с", 2.88), ("е", 2.92), ("ти", 3.04), (".", 3.28),
]  # fmt: skip


def test_words_from_tokens_real_gigaam_segment() -> None:
    toks, ts = zip(*GIGAAM_SEG, strict=True)
    words = words_from_tokens(toks, ts, [0.0] * len(toks), offset=1.698, seg_end=5.022)
    assert [w.w for w in words] == [
        "Сегодня",
        "мы",
        "поговорим",
        "о",
        "том,",
        "как",
        "устроены",
        "нейросети.",
    ]
    assert words[0].s == pytest.approx(1.698, abs=0.03)
    assert words[1].s == pytest.approx(1.698 + 0.52, abs=0.03)
    for a, b in zip(words, words[1:], strict=False):
        assert a.e <= b.s + 1e-9, "a word ends before the next one starts"
        assert a.e - a.s >= 0.06
    assert words[-1].e <= 5.022
    assert all(w.p == 1.0 for w in words)  # logprob 0 -> p = 1
    tr = Transcript.from_words(words, language="ru", duration=6.0, provider="gigaam_v3_onnx")
    assert [s.text for s in tr.sentences] == ["Сегодня мы поговорим о том, как устроены нейросети."]


def test_words_from_tokens_punctuation_glue_and_sentencepiece_marker() -> None:
    toks = ["▁Он", "▁сказал", ":", " «", "при", "вет", "»", " —", " и", " ушёл", "."]
    ts = [0.0, 0.3, 0.6, 0.8, 0.84, 0.9, 1.0, 1.2, 1.4, 1.6, 1.9]
    words = words_from_tokens(toks, ts)
    assert [w.w for w in words] == ["Он", "сказал:", "«привет» —", "и", "ушёл."]
    assert words[2].s == pytest.approx(0.84)  # opener does not move the start


def test_words_from_tokens_validates_lengths() -> None:
    with pytest.raises(ValueError):
        words_from_tokens([" а"], [0.0, 0.1])
    assert words_from_tokens([], []) == []


# ---------------------------------------------------------------------- punctuation transfer


def test_attach_punctuation_copies_case_and_marks() -> None:
    raw = [Word("привет", 0.0, 0.4), Word("мир", 0.5, 0.8), Word("как", 1.0, 1.2), Word("дела", 1.3, 1.6)]
    out = attach_punctuation(raw, "Привет, мир! Как дела?")
    assert [w.w for w in out] == ["Привет,", "мир!", "Как", "дела?"]
    assert [(w.s, w.e) for w in out] == [(w.s, w.e) for w in raw]


def test_attach_punctuation_tolerates_normalization_mismatch() -> None:
    raw = [Word("мне", 0, 0.2), Word("двадцать", 0.3, 0.7), Word("пять", 0.8, 1.0), Word("лет", 1.1, 1.3)]
    out = attach_punctuation(raw, "Мне 25 лет.")
    assert out[0].w == "Мне" and out[-1].w == "лет."
    assert out[1].w == "двадцать"  # unmatched words stay raw


# ---------------------------------------------------------------------- SpeechKit v3

SK_STREAM = "\n".join(
    json.dumps(x, ensure_ascii=False)
    for x in [
        {"result": {"channelTag": "0", "partial": {"alternatives": [{"text": "добрый"}]}}},
        {
            "result": {
                "channelTag": "0",
                "audioCursors": {"finalIndex": "0"},
                "final": {
                    "channelTag": "0",
                    "alternatives": [
                        {
                            "text": "добрый день коллеги",
                            "words": [
                                {"text": "добрый", "startTimeMs": "120", "endTimeMs": "480"},
                                {"text": "день", "startTimeMs": "500", "endTimeMs": "820"},
                                {"text": "коллеги", "startTimeMs": "900", "endTimeMs": "1500"},
                            ],
                        }
                    ],
                },
            }
        },
        {
            "result": {
                "channelTag": "0",
                "finalRefinement": {
                    "finalIndex": "0",
                    "normalizedText": {"alternatives": [{"text": "Добрый день, коллеги!"}]},
                },
            }
        },
        {
            "result": {
                "channelTag": "0",
                "audioCursors": {"finalIndex": "1"},
                "final": {
                    "alternatives": [
                        {
                            "words": [
                                {"text": "начнём", "startTimeMs": "3200", "endTimeMs": "3700"},
                                {"text": "работу", "startTimeMs": "3750", "endTimeMs": "4300"},
                            ]
                        }
                    ]
                },
            }
        },
        {
            "result": {
                "finalRefinement": {
                    "finalIndex": "1",
                    "normalizedText": {"alternatives": [{"text": "Начнём работу."}]},
                }
            }
        },
    ]
)


def test_speechkit_request_body() -> None:
    body = build_request(content_b64="QUJD", model="deferred-general", language="ru")
    rm = body["recognitionModel"]
    assert body["content"] == "QUJD" and "uri" not in body
    assert rm["model"] == "deferred-general"
    assert rm["audioFormat"] == {"containerAudio": {"containerAudioType": "OGG_OPUS"}}
    assert rm["languageRestriction"] == {"restrictionType": "WHITELIST", "languageCode": ["ru-RU"]}
    assert rm["textNormalization"]["literatureText"] is True
    with pytest.raises(ValueError):
        build_request(content_b64="x", uri="https://s")


def test_speechkit_parse_stream_with_refinement() -> None:
    words = parse_recognition(iter_events(SK_STREAM))
    assert [w.w for w in words] == ["Добрый", "день,", "коллеги!", "Начнём", "работу."]
    assert (words[0].s, words[0].e) == (0.12, 0.48)
    tr = Transcript.from_words(words, language="ru", duration=5.0, provider="yandex_speechkit")
    assert_contract(tr)
    assert [s.text for s in tr.sentences] == ["Добрый день, коллеги!", "Начнём работу."]


def test_speechkit_iter_events_formats() -> None:
    objs = [json.loads(line) for line in SK_STREAM.splitlines()]
    as_array = json.dumps(objs)
    concatenated = "".join(json.dumps(o) for o in objs)
    for payload in (as_array, concatenated, objs, SK_STREAM.encode()):
        assert len(list(iter_events(payload))) == len(objs)
    with pytest.raises(AsrBadResponse):
        list(iter_events('{"error": {"code": 3, "message": "bad audio"}}'))


def _sk_transport(calls: list[httpx.Request], *, done_after: int = 2, recog_status: int = 200):
    state = {"polls": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.url.path.endswith("/stt/v3/recognizeFileAsync"):
            return httpx.Response(200, json={"id": "op-123", "done": False})
        if req.url.path == "/operations/op-123":
            state["polls"] += 1
            return httpx.Response(200, json={"id": "op-123", "done": state["polls"] >= done_after})
        if req.url.path.endswith("/stt/v3/getRecognition"):
            assert req.url.params["operationId"] == "op-123"
            return httpx.Response(recog_status, text=SK_STREAM)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_speechkit_async_flow_mocked_http() -> None:
    calls: list[httpx.Request] = []
    client = httpx.Client(transport=_sk_transport(calls))
    p = YandexSpeechKitProvider(
        api_key="k-1", folder_id="b1g", model="general", client=client, sleep=lambda s: None
    )
    tr = p.transcribe_uri("https://storage.yandexcloud.net/b/a.ogg", duration=3600.0)
    assert_contract(tr)
    assert tr.provider == "yandex_speechkit" and tr.model == "general"
    assert tr.cost_usd == pytest.approx(36.36 / 90.0, rel=1e-3)
    post = calls[0]
    assert post.method == "POST" and post.headers["Authorization"] == "Api-Key k-1"
    assert post.headers["x-folder-id"] == "b1g"
    assert json.loads(post.content)["uri"].startswith("https://storage.yandexcloud.net/")
    assert sum(1 for c in calls if c.url.path.startswith("/operations/")) == 2


@pytest.mark.parametrize(("status", "exc"), [(429, AsrTransient), (503, AsrTransient), (401, AsrBadResponse)])
def test_speechkit_error_mapping(status: int, exc: type[Exception]) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, text="nope")))
    p = YandexSpeechKitProvider(api_key="k", client=client, sleep=lambda s: None)
    with pytest.raises(exc):
        p.submit(build_request(uri="https://s/x.ogg"))


def test_speechkit_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YANDEX_SPEECHKIT_API_KEY", raising=False)
    with pytest.raises(AsrUnavailable):
        YandexSpeechKitProvider()


def test_speechkit_operation_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "op", "done": True, "error": {"code": 3, "message": "bad"}})

    p = YandexSpeechKitProvider(api_key="k", client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(AsrBadResponse):
        p.poll("op")


# ---------------------------------------------------------------------- OpenAI-compatible

VERBOSE = {
    "task": "transcribe",
    "language": "russian",
    "duration": 4.2,
    "text": "Привет, мир! Как дела?",
    "words": [
        {"word": "Привет", "start": 0.0, "end": 0.4},
        {"word": "мир", "start": 0.5, "end": 0.9},
        {"word": "Как", "start": 2.4, "end": 2.6},
        {"word": "дела", "start": 2.7, "end": 3.1},
    ],
    "segments": [
        {"id": 0, "start": 0.0, "end": 1.0, "text": " Привет, мир!"},
        {"id": 1, "start": 2.3, "end": 3.2, "text": " Как дела?"},
    ],
}


def test_openai_parse_words_with_segment_punctuation() -> None:
    words = parse_verbose_json(VERBOSE, offset=10.0)
    assert [w.w for w in words] == ["Привет,", "мир!", "Как", "дела?"]
    assert words[0].s == 10.0 and words[-1].e == pytest.approx(13.1)


def test_openai_parse_segments_only_spreads_by_length() -> None:
    words = parse_verbose_json({"segments": [{"start": 0.0, "end": 2.0, "text": "Раз два."}]})
    assert [w.w for w in words] == ["Раз", "два."]
    assert words[0].e == pytest.approx(2.0 * 3 / 7, abs=1e-3)
    assert words[-1].e == pytest.approx(2.0, abs=1e-3)


def test_openai_multipart_request_mocked_http(tmp_path: Path) -> None:
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["Authorization"]
        seen["body"] = req.content.decode("utf-8", "replace")
        return httpx.Response(200, json=VERBOSE)

    wav = write_silence_wav(tmp_path / "a.wav", 4.2)
    p = OpenAICompatAsrProvider(
        base_url="https://llm.example/v1/", api_key="sk-x", model="whisper-1",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )  # fmt: skip
    tr = p.transcribe(wav, "ru")
    assert_contract(tr)
    assert seen["url"] == "https://llm.example/v1/audio/transcriptions"
    assert seen["auth"] == "Bearer sk-x"
    body = seen["body"]
    for field in (
        'name="model"',
        "whisper-1",
        'name="timestamp_granularities[]"',
        "verbose_json",
        'name="file"',
    ):
        assert field in body
    assert body.count('name="timestamp_granularities[]"') == 2
    assert [s.text for s in tr.sentences] == ["Привет, мир!", "Как дела?"]
    assert tr.cost_usd == pytest.approx(4.2 / 60 * 0.006, abs=1e-6)


def test_openai_errors_and_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ASR_CLOUD_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(AsrUnavailable):
        OpenAICompatAsrProvider()
    monkeypatch.setenv("LLM_API_KEY", "from-llm")
    monkeypatch.delenv("ASR_CLOUD_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    p = OpenAICompatAsrProvider(
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    )
    assert p.api_key == "from-llm" and p.base_url == "https://umka.chat/v1"
    with pytest.raises(AsrTransient):
        p.request_part(write_silence_wav(tmp_path / "b.wav"), "ru")


def test_chunk_results_join_into_one_transcript() -> None:
    from autoshorts.providers.asr.gigaam_onnx import RangeResult, transcript_from_ranges

    a = RangeResult(
        words=synth_words("Первый кусок."), vad=[(0.0, 1.5)], audio_sec=600.0, wall_sec=40.0,
        start=0.0, stop=600.0, next_start=600.0,
    )  # fmt: skip
    b = RangeResult(
        words=synth_words("Второй кусок!", start=600.2), vad=[(600.2, 601.5)], audio_sec=300.0, wall_sec=20.0,
        start=600.0, stop=900.0, next_start=900.0,
    )  # fmt: skip
    tr = transcript_from_ranges([b, a], duration=900.0)
    assert_contract(tr)
    assert [s.text for s in tr.sentences] == ["Первый кусок.", "Второй кусок!"]
    assert tr.vad == [(0.0, 1.5), (600.2, 601.5)] and tr.rtfx == 15.0
    assert tr.provider == "gigaam_v3_onnx"


def test_chunk_join_drops_words_recognized_twice() -> None:
    from autoshorts.providers.asr.gigaam_onnx import RangeResult, transcript_from_ranges

    # chunk A's last segment ran past its nominal end (600) up to 603.5
    a_words = synth_words("Начало.") + synth_words("Длинная фраза через границу.", start=600.5)
    a = RangeResult(words=a_words, vad=[(0.0, 0.9), (600.5, 603.5)], start=0.0, stop=600.0, next_start=603.5)
    dup = synth_words("фраза через границу.", start=601.3)
    b = RangeResult(
        words=dup + synth_words("Дальше.", start=610.0), vad=[(600.0, 603.4), (610.0, 610.7)],
        start=600.0, stop=900.0, next_start=900.0,
    )  # fmt: skip
    tr = transcript_from_ranges([a, b], duration=900.0)
    assert [s.text for s in tr.sentences] == ["Начало.", "Длинная фраза через границу.", "Дальше."]
    assert tr.vad == [(0.0, 0.9), (600.5, 603.5), (610.0, 610.7)]
