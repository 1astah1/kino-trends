"""Deterministic mock ASR for tests and CI (docs/ARCHITECTURE.md §6.3.2 `mock`).

    MockAsrProvider(fixture="tests/fixtures/transcript.json.gz").transcribe(any_path)
    MockAsrProvider(text="Привет. Как дела?\n\nВторой абзац.").transcribe("audio16k.wav")

* `fixture` — a saved `Transcript` (json or json.gz); returned with provider="mock".
* `text` — words get synthetic timings from their length (like the legacy mock TTS):
  `0.06 s/char + 0.08 s`, 0.05 s between words, +0.35 s after `. ? ! …`, and an empty line
  (paragraph) inserts a `paragraph_pause` (default 1.5 s) so the forced pause split (> 1.2 s)
  is exercised. Same input -> same output, no randomness.
* No fixture and no text -> a short built-in Russian demo text.

Refused with `AsrUnavailable` when APP_ENV=prod and ALLOW_MOCK_IN_PROD is not set.
"""

from __future__ import annotations

from pathlib import Path

from autoshorts.clipping.transcript import Transcript, Word
from autoshorts.providers.asr.base import (
    AsrUnavailable,
    ProgressFn,
    emit_progress,
    mock_allowed,
    normalize_language,
    wav_info,
)

DEMO_TEXT = (
    "Привет! Сегодня разберём, почему короткие ролики досматривают до конца. "
    "Первое правило простое: сильный хук в первые две секунды.\n\n"
    "Второе правило: одна мысль на ролик. Зритель должен понять её без контекста. "
    "А третье правило вы узнаете в конце, так что не уходите."
)

CHAR_SEC = 0.06
WORD_BASE_SEC = 0.08
WORD_GAP_SEC = 0.05
SENTENCE_PAUSE_SEC = 0.35


def synth_words(text: str, *, start: float = 0.0, paragraph_pause: float = 1.5) -> list[Word]:
    """Deterministic word timings for `text` (see module docstring)."""
    words: list[Word] = []
    t = float(start)
    paragraphs = [p for p in text.replace("\r\n", "\n").split("\n\n") if p.strip()]
    for pi, para in enumerate(paragraphs):
        if pi:
            t += paragraph_pause
        for token in para.split():
            dur = len(token) * CHAR_SEC + WORD_BASE_SEC
            words.append(Word(token, round(t, 3), round(t + dur, 3), 0.99))
            t += dur + WORD_GAP_SEC
            if token.rstrip("\"'»”)").endswith((".", "?", "!", "…")):
                t += SENTENCE_PAUSE_SEC
    return words


class MockAsrProvider:
    provider_key = "mock"

    def __init__(
        self,
        fixture: str | Path | Transcript | None = None,
        *,
        text: str | None = None,
        paragraph_pause: float = 1.5,
        model: str = "mock-v1",
    ) -> None:
        if not mock_allowed():
            raise AsrUnavailable("mock ASR is disabled in prod (set ALLOW_MOCK_IN_PROD=true to override)")
        self.fixture = fixture
        self.text = text
        self.paragraph_pause = paragraph_pause
        self.model = model
        self.calls: list[tuple[str, str]] = []  # (audio_path, language) for assertions in tests

    def transcribe(
        self, audio_path: str | Path, language: str = "ru", progress: ProgressFn | None = None
    ) -> Transcript:
        self.calls.append((str(audio_path), language))
        lang = normalize_language(language)
        emit_progress(progress, 0.0)
        if self.fixture is not None:
            base = self.fixture if isinstance(self.fixture, Transcript) else Transcript.load(self.fixture)
            tr = Transcript(
                language=base.language or lang,
                duration=base.duration,
                provider=self.provider_key,
                model=base.model or self.model,
                words=list(base.words),
                sentences=list(base.sentences),
                vad=list(base.vad),
                rtfx=None,
                cost_usd=0.0,
            )
            emit_progress(progress, 1.0)
            return tr
        words = synth_words(
            self.text if self.text is not None else DEMO_TEXT, paragraph_pause=self.paragraph_pause
        )
        info = wav_info(audio_path) if Path(audio_path).is_file() else None
        end = words[-1].e + 0.5 if words else 0.0
        duration = max(info.duration, end) if info else end
        vad = _vad_from_words(words)
        emit_progress(progress, 1.0)
        return Transcript.from_words(
            words,
            language=lang,
            duration=round(duration, 3),
            provider=self.provider_key,
            model=self.model,
            vad=vad,
            rtfx=None,
            cost_usd=0.0,
        )


def _vad_from_words(words: list[Word], max_gap: float = 0.6) -> list[tuple[float, float]]:
    segs: list[tuple[float, float]] = []
    for wd in words:
        if segs and wd.s - segs[-1][1] <= max_gap:
            segs[-1] = (segs[-1][0], wd.e)
        else:
            segs.append((wd.s, wd.e))
    return segs


__all__ = ["DEMO_TEXT", "MockAsrProvider", "synth_words"]
