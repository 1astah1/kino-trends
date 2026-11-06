"""Голос автора для реплик клипа (docs/ARCHITECTURE.md §6.3.6 стадия `author_voice`).

Источники голоса:

* `edge` — edge-tts с пословными таймингами WordBoundary. Используется legacy-код
  `pipeline/voice.synthesize_voiceover` как есть (только чтение, файл не меняется);
* `operator` — запись оператора (`POST /jobs/{id}/author-voice`): файл на реплику. Тишина по
  краям срезается, пословные тайминги — ASR (GigaAM), а без ASR — оценка по длине слов
  (помечается `timing="estimated"`). Живой голос — лучший ответ на «синтетический голос без
  вариаций» из правил YouTube про неаутентичный контент;
* `mock` — детерминированный «голос» из тональных посылок: без сети, для тестов. При
  APP_ENV=prod запрещён (как и другие заглушки).

Все файлы кладутся в `out_dir`, тяжёлые вызовы ffmpeg идут через `ffmpeg_exec.run_media`
(nice 10, 2 потока).
"""

from __future__ import annotations

import math
import struct
import time
import wave
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from autoshorts.clipping.transcript import Word

VoiceKind = Literal["tts", "operator", "mock"]
# Задержка начала речи в MP3 edge-tts относительно WordBoundary (замер bench_asr: 0,155 с).
# Субтитрам лучше чуть раньше, чем позже, поэтому сдвиг берётся с запасом в меньшую сторону.
EDGE_WORD_SHIFT_SEC = 0.12
MOCK_RATE = 24000


@dataclass(slots=True)
class VoiceTake:
    role: str  # cold_open | insert_0 | insert_1 | outro
    path: Path
    duration: float
    words: list[Word]  # время от начала файла
    kind: VoiceKind
    text: str = ""
    voice: str | None = None
    provider: str = ""
    timing: Literal["tts", "asr", "estimated", "mock"] = "tts"
    latency_ms: int = 0
    cost_usd: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def synthetic(self) -> bool:
        return self.kind != "operator"

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "path": str(self.path),
            "duration": round(self.duration, 3),
            "kind": self.kind,
            "text": self.text,
            "voice": self.voice,
            "provider": self.provider,
            "timing": self.timing,
            "latency_ms": self.latency_ms,
            "cost_usd": round(self.cost_usd, 6),
            "words": [{"w": w.w, "s": round(w.s, 3), "e": round(w.e, 3)} for w in self.words],
            "warnings": list(self.warnings),
        }


def _duration(path: Path) -> float:
    from autoshorts.pipeline.proxy import probe_media

    return float(probe_media(path).duration)


# ---------------------------------------------------------------------- edge-tts


def synthesize_edge(
    role: str, text: str, out_dir: Path, *, voice: str | None = None, shift: float = EDGE_WORD_SHIFT_SEC
) -> VoiceTake:
    """edge-tts через legacy `synthesize_voiceover` (WordBoundary, ретраи сети внутри)."""
    from autoshorts.pipeline import voice as legacy_voice

    v = voice or legacy_voice.EDGE_VOICE_RU
    path, timings, latency = legacy_voice.synthesize_voiceover(text, out_dir, role, voice=v)
    dur = _duration(path)
    words = [Word(t.word, max(0.0, t.start_sec + shift), min(dur, t.end_sec + shift)) for t in timings]
    try:  # WordBoundary отдаёт слова без пунктуации — возвращаем её из текста реплики
        from autoshorts.providers.asr.base import attach_punctuation

        words = attach_punctuation(words, text)
    except Exception:  # noqa: BLE001 - субтитры без точек лучше, чем упавшая стадия
        pass
    return VoiceTake(role, Path(path), dur, words, "tts", text, v, "edge_tts", "tts", latency)


# ---------------------------------------------------------------------- OpenAI gpt-audio (через OpenRouter)

# Чат-модель со звуком, а не синтезатор: без жёсткой роли отвечает («Извините, я не могу…») вместо чтения.
# Проба 29.10.2026: openai/gpt-audio прочитал дословно с первой попытки 3 голоса из 3, gpt-audio-mini — 3 из 8.
# Поэтому каждая реплика сверяется с текстом (`AUDIO_TTS_MATCH`) и при расхождении переозвучивается.
AUDIO_TTS_STYLE = (
    "You are a text-to-speech engine, not an assistant. You never reply, comment or answer questions. "
    "Your only output is speech of the exact text between <read> and </read>, word for word, in Russian, "
    "nothing before or after it. Delivery: lively confident quiz-show host, intriguing, slightly fast pace, "
    "native Russian pronunciation."
)
AUDIO_TTS_MATCH = 0.95
AUDIO_TTS_RATE = 24000  # pcm16 моно


def _speech_words(text: str) -> list[str]:
    import re

    return re.sub(r"[^а-яё0-9 ]", " ", str(text).lower().replace("ё", "е")).split()


def speech_match(expected: str, heard: str) -> float:
    """Насколько прочитанное совпадает с репликой (0..1, по словам)."""
    from difflib import SequenceMatcher

    return SequenceMatcher(None, _speech_words(expected), _speech_words(heard)).ratio()


def _openai_audio_once(
    text: str, *, voice: str, model: str, api_key: str, base_url: str, style: str, timeout: float, transport: Any = None
) -> tuple[bytes, str, float]:
    """Один запрос: (pcm16 24 кГц моно, расшифровка модели, цена $). Звук приходит только потоком."""
    import base64
    import json

    import httpx

    body = {
        "model": model,
        "modalities": ["text", "audio"],
        "audio": {"voice": voice, "format": "pcm16"},
        "stream": True,
        "messages": [{"role": "system", "content": style}, {"role": "user", "content": f"<read>{text}</read>"}],
    }
    pcm = bytearray()
    heard = ""
    cost = 0.0
    with httpx.Client(timeout=timeout, transport=transport) as cl, cl.stream(
        "POST", f"{base_url.rstrip('/')}/chat/completions", json=body, headers={"Authorization": f"Bearer {api_key}"}
    ) as r:
        if r.status_code != 200:
            raise RuntimeError(f"gpt-audio HTTP {r.status_code}: {r.read()[:200]!r}")
        for line in r.iter_lines():
            if not line.startswith("data: ") or line.strip() == "data: [DONE]":
                continue
            ev = json.loads(line[6:])
            if ev.get("usage"):
                cost = float(ev["usage"].get("cost") or 0.0)
            for ch in ev.get("choices") or []:
                audio = (ch.get("delta") or {}).get("audio") or {}
                if audio.get("data"):
                    pcm += base64.b64decode(audio["data"])
                if audio.get("transcript"):
                    heard += audio["transcript"]
    return bytes(pcm), heard.replace("<read>", "").replace("</read>", ""), cost


def synthesize_openai_audio(
    role: str,
    text: str,
    out_dir: Path,
    *,
    voice: str,
    model: str,
    api_key: str,
    base_url: str = "https://openrouter.ai/api/v1",
    words_fn: Any = None,
    tries: int = 4,
    style: str = AUDIO_TTS_STYLE,
    timeout: float = 120.0,
    transport: Any = None,
) -> VoiceTake:
    """Реплика голосом gpt-audio: повтор, пока не прочитано дословно; тишина по краям срезается,
    пословные тайминги — `words_fn(wav16k) -> list[Word]` (ASR), иначе оценка по длине слов."""
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    spent = 0.0
    best = (0.0, b"", "")
    for _ in range(max(1, tries)):
        pcm, heard, cost = _openai_audio_once(
            text, voice=voice, model=model, api_key=api_key, base_url=base_url, style=style, timeout=timeout,
            transport=transport,
        )  # fmt: skip
        spent += cost
        score = speech_match(text, heard)
        if pcm and score > best[0]:
            best = (score, pcm, heard)
        if pcm and score >= AUDIO_TTS_MATCH:
            break
    score, pcm, heard = best
    if not pcm or score < AUDIO_TTS_MATCH:
        raise RuntimeError(f"gpt-audio не прочитал реплику {role} дословно за {tries} попыток (совпадение {score:.2f})")
    raw = out_dir / f"{role}_gptaudio_raw.wav"
    with wave.open(str(raw), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(AUDIO_TTS_RATE)
        w.writeframes(pcm)
    take = prepare_operator_take(role, raw, out_dir, text=text, asr=None, tag="tts")
    raw.unlink(missing_ok=True)
    timing: Literal["tts", "asr", "estimated", "mock"] = "estimated"
    words = take.words
    if words_fn is not None:
        try:
            got = list(words_fn(take.path) or [])
            if got:
                words, timing = got, "asr"
        except Exception as exc:  # noqa: BLE001 - без ASR тайминги по длине слов
            take.warnings.append(f"asr_failed: {type(exc).__name__}: {exc}"[:200])
    take.words = words
    take.kind = "tts"
    take.voice = voice
    take.provider = f"openrouter:{model}"
    take.timing = timing
    take.cost_usd = spent
    take.latency_ms = int((time.monotonic() - t0) * 1000)
    if timing == "asr":
        take.warnings = [w for w in take.warnings if w != "word_timing_estimated"]
    return take


# ---------------------------------------------------------------------- mock


def _mock_words(text: str) -> list[Word]:
    words: list[Word] = []
    t = 0.08
    for w in text.split():
        dur = 0.06 * len(w) + 0.1
        words.append(Word(w, round(t, 3), round(t + dur, 3)))
        t += dur + (0.22 if w.endswith((".", "?", "!", "…", ",")) else 0.06)
    return words


def synthesize_mock(role: str, text: str, out_dir: Path, *, base_hz: float = 220.0) -> VoiceTake:
    """WAV из «слогов»-посылок (гармоники с огибающей) с точными таймингами слов. Без сети."""
    from autoshorts.pipeline.voice import ensure_mock_allowed

    ensure_mock_allowed("tts")
    out_dir.mkdir(parents=True, exist_ok=True)
    words = _mock_words(text)
    total = (words[-1].e + 0.15) if words else 0.5
    n = int(total * MOCK_RATE)
    samples = [0.0] * n
    for k, w in enumerate(words):
        f0 = base_hz * (1.0 + 0.08 * math.sin(k * 1.7))
        a = int(w.s * MOCK_RATE)
        b = min(n, int(w.e * MOCK_RATE))
        length = max(1, b - a)
        for i in range(a, b):
            x = (i - a) / length
            env = math.sin(math.pi * x) ** 0.6
            t = i / MOCK_RATE
            s = (
                math.sin(2 * math.pi * f0 * t)
                + 0.45 * math.sin(4 * math.pi * f0 * t)
                + 0.25 * math.sin(6 * math.pi * f0 * t)
            )
            samples[i] = 0.32 * env * s
    path = out_dir / f"{role}_voice.mock.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(MOCK_RATE)
        wf.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples))
    return VoiceTake(role, path, n / MOCK_RATE, words, "mock", text, "mock", "mock_tts", "mock")


# ---------------------------------------------------------------------- запись оператора


def _estimate_words(text: str, speech_start: float, speech_end: float) -> list[Word]:
    toks = text.split()
    if not toks:
        return []
    weights = [len(t) + 2 for t in toks]
    span = max(0.1, speech_end - speech_start)
    unit = span / sum(weights)
    out: list[Word] = []
    t = speech_start
    for tok, wgt in zip(toks, weights, strict=True):
        d = wgt * unit
        out.append(Word(tok, round(t, 3), round(t + d * 0.92, 3)))
        t += d
    return out


def prepare_operator_take(
    role: str,
    recording: str | Path,
    out_dir: Path,
    *,
    text: str = "",
    asr: Any = None,
    threshold_db: float = -45.0,
    timeout_sec: float = 120.0,
    tag: str = "operator",
) -> VoiceTake:
    """Запись оператора → WAV 48 кГц моно без тишины по краям + пословные тайминги."""
    from autoshorts.pipeline.ffmpeg_exec import run_media

    out_dir.mkdir(parents=True, exist_ok=True)
    src = Path(recording)
    if not src.is_file():
        raise FileNotFoundError(f"operator recording not found: {src}")
    dst = out_dir / f"{role}_{tag}.wav"
    trim = (
        f"silenceremove=start_periods=1:start_threshold={threshold_db}dB:start_silence=0.05,"
        f"areverse,silenceremove=start_periods=1:start_threshold={threshold_db}dB:start_silence=0.1,areverse"
    )
    run_media(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-vn",
            "-af",
            trim,
            "-ac",
            "1",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            str(dst),
        ],
        timeout_sec=timeout_sec,
        threads=2,
        nice=10,
    )
    dur = _duration(dst)
    warnings: list[str] = []
    words: list[Word] = []
    timing: Literal["asr", "estimated"] = "estimated"
    if asr is not None:
        wav16 = out_dir / f"{role}_operator16k.wav"
        try:
            run_media(
                ["ffmpeg", "-y", "-i", str(dst), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav16)],
                timeout_sec=timeout_sec,
                threads=2,
                nice=10,
            )
            tr = asr.transcribe(wav16, "ru")
            words = list(tr.words)
            timing = "asr"
        except Exception as exc:  # noqa: BLE001 - без ASR тайминги оцениваются
            warnings.append(f"asr_failed: {type(exc).__name__}: {exc}"[:200])
        finally:
            wav16.unlink(missing_ok=True)
    if not words:
        words = _estimate_words(text, 0.0, dur)
        if text:
            warnings.append("word_timing_estimated")
    return VoiceTake(
        role,
        dst,
        dur,
        words,
        "operator",
        text or " ".join(w.w for w in words),
        "operator",
        "operator_recording",
        timing,
        warnings=warnings,
    )


# ---------------------------------------------------------------------- все реплики сценария


@dataclass(slots=True)
class VoiceResult:
    takes: dict[str, VoiceTake] = field(default_factory=dict)
    wall_sec: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def synthetic(self) -> bool:
        return any(t.synthetic for t in self.takes.values())

    @property
    def kinds(self) -> list[str]:
        return sorted({t.kind for t in self.takes.values()})

    def to_dict(self) -> dict[str, Any]:
        return {
            "takes": {k: v.to_dict() for k, v in self.takes.items()},
            "wall_sec": round(self.wall_sec, 3),
            "kinds": self.kinds,
            "warnings": list(self.warnings),
        }


def voice_script(
    parts: list[tuple[str, str]],
    out_dir: str | Path,
    *,
    tts: Literal["edge", "mock"] = "edge",
    voice: str | None = None,
    operator_files: Mapping[str, str | Path] | None = None,
    asr: Any = None,
    fallback_to_mock: bool = False,
) -> VoiceResult:
    """Озвучить реплики `(role, text)` (см. `AuthorScript.vo_parts`). Запись оператора важнее TTS."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    res = VoiceResult()
    t0 = time.monotonic()
    ops = dict(operator_files or {})
    for role, text in parts:
        if role in ops:
            res.takes[role] = prepare_operator_take(role, ops[role], out, text=text, asr=asr)
            continue
        if tts == "mock":
            res.takes[role] = synthesize_mock(role, text, out)
            continue
        try:
            res.takes[role] = synthesize_edge(role, text, out, voice=voice)
        except Exception as exc:  # noqa: BLE001
            if not fallback_to_mock:
                raise
            res.warnings.append(f"edge_tts_failed_{role}: {type(exc).__name__}")
            res.takes[role] = synthesize_mock(role, text, out)
    res.wall_sec = time.monotonic() - t0
    return res


__all__ = [
    "EDGE_WORD_SHIFT_SEC",
    "VoiceKind",
    "VoiceResult",
    "VoiceTake",
    "prepare_operator_take",
    "speech_match",
    "synthesize_edge",
    "synthesize_mock",
    "synthesize_openai_audio",
    "voice_script",
]
