"""Голос gpt-audio: реплика переозвучивается, пока не прочитана дословно; тишина срезается, цена суммируется."""

import base64
import json
import math
import struct

import httpx
import pytest

from autoshorts.clipping.author.voice import speech_match, synthesize_openai_audio
from autoshorts.clipping.transcript import Word

TEXT = "Угадаешь фильм по этой сцене?"


def _pcm(sec: float = 1.0) -> bytes:
    n = int(24000 * sec)
    silence = b"\x00\x00" * 4800
    tone = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * i / 24000))) for i in range(n))
    return silence + tone + silence


def _sse(transcript: str, cost: float) -> bytes:
    data = base64.b64encode(_pcm()).decode()
    events = [
        {"choices": [{"delta": {"audio": {"transcript": transcript}}}]},
        {"choices": [{"delta": {"audio": {"data": data}}}]},
        {"choices": [], "usage": {"cost": cost}},
    ]
    return ("".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events) + "data: [DONE]\n\n").encode()


def test_rereads_until_verbatim_and_trims(tmp_path) -> None:
    replies = iter([_sse("Извините, я не могу угадать фильм.", 0.01), _sse(f"<read>{TEXT}</read>", 0.012)])
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        return httpx.Response(200, content=next(replies), headers={"content-type": "text/event-stream"})

    take = synthesize_openai_audio(
        "cold_open", TEXT, tmp_path, voice="ash", model="openai/gpt-audio", api_key="k",
        words_fn=lambda wav: [Word("Угадаешь", 0.0, 0.4), Word("фильм", 0.4, 0.8)],
        transport=httpx.MockTransport(handler),
    )  # fmt: skip
    assert len(seen) == 2 and seen[0]["audio"] == {"voice": "ash", "format": "pcm16"} and seen[0]["stream"] is True
    assert "<read>" in seen[0]["messages"][1]["content"]
    assert take.kind == "tts" and take.provider == "openrouter:openai/gpt-audio" and take.voice == "ash"
    assert take.timing == "asr" and [w.w for w in take.words] == ["Угадаешь", "фильм"]
    assert take.cost_usd == pytest.approx(0.022)
    assert take.path.is_file() and 0.9 < take.duration < 1.3  # из 1,4 с: тишина по краям срезана до 0,05–0,1 с


def test_gives_up_after_tries(tmp_path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse("Конечно! Это отличный фильм.", 0.01))

    with pytest.raises(RuntimeError, match="дословно"):
        synthesize_openai_audio("outro", TEXT, tmp_path, voice="ash", model="m", api_key="k", tries=2,
                                transport=httpx.MockTransport(handler))  # fmt: skip


def test_speech_match_ignores_case_and_punctuation() -> None:
    assert speech_match("Это «Обитель зла», 2002 год. Угадали?", "это обитель зла 2002 год угадали") == 1.0
    assert speech_match(TEXT, "Извините, я не могу") < 0.3
