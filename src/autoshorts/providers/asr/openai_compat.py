"""OpenAI-compatible `/audio/transcriptions` with word timestamps (ARCHITECTURE.md §6.3.2).

Fallback cloud provider. Request (multipart/form-data):

    file=<audio>, model=<model>, language=ru, response_format=verbose_json,
    timestamp_granularities[]=word, timestamp_granularities[]=segment

Response (verbose_json): {text, language, duration, words:[{word,start,end}], segments:[{start,end,text}]}.
Words carry no punctuation, so punctuation and case are copied from the segment text onto the words of
that segment (`attach_punctuation`); without words the segment is spread over its tokens by length.

Config: base URL `ASR_CLOUD_BASE_URL` (fallback `LLM_BASE_URL`, then https://umka.chat/v1), key
`ASR_CLOUD_API_KEY` (fallback `LLM_API_KEY`), model `ASR_CLOUD_MODEL` (default whisper-1; OpenAI
retires whisper-1 on 2027-02-26, and only whisper-1 returns word timestamps there — pick a model the
endpoint supports). Uploads above `max_upload_bytes` (25 MB) are re-encoded to OGG/Opus 24 kbit/s;
still too big -> split into `part_sec` parts, timings are shifted by the part offset.
Covered by unit tests with mocked HTTP only.
"""

from __future__ import annotations

import math
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from autoshorts.clipping.transcript import Transcript, Word
from autoshorts.providers.asr.base import (
    AsrBadResponse,
    AsrTransient,
    AsrUnavailable,
    ProgressFn,
    attach_punctuation,
    emit_progress,
    ffmpeg_audio,
    ffprobe_duration,
    normalize_language,
)

DEFAULT_BASE_URL = "https://umka.chat/v1"
DEFAULT_MODEL = "whisper-1"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
SUPPORTED_SUFFIXES = frozenset({".flac", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".ogg", ".wav", ".webm"})
USD_PER_MINUTE = 0.006


def parse_verbose_json(data: dict[str, Any], *, offset: float = 0.0) -> list[Word]:
    """Timed words with punctuation from a verbose_json transcription response."""
    raw = [
        Word(str(w.get("word", "")).strip(), float(w["start"]) + offset, float(w["end"]) + offset)
        for w in data.get("words") or []
        if str(w.get("word", "")).strip()
    ]
    segments = [s for s in data.get("segments") or [] if str(s.get("text", "")).strip()]
    if raw and segments:
        out: list[Word] = []
        i = 0
        for k, seg in enumerate(segments):
            s1 = float(seg["end"]) + offset
            chunk: list[Word] = []
            # words whose midpoint lies before the segment end (the last segment takes the rest)
            while i < len(raw) and (k == len(segments) - 1 or (raw[i].s + raw[i].e) / 2 <= s1 + 1e-3):
                chunk.append(raw[i])
                i += 1
            if chunk:
                out.extend(attach_punctuation(chunk, str(seg["text"])))
        out.extend(raw[i:])
        return out
    if raw:
        text = str(data.get("text", ""))
        return attach_punctuation(raw, text) if text else raw
    # no word timestamps: spread every segment over its tokens proportionally to their length
    out = []
    for seg in segments:
        toks = str(seg["text"]).split()
        s0, s1 = float(seg["start"]) + offset, float(seg["end"]) + offset
        total = sum(len(t) for t in toks) or 1
        t = s0
        for tok in toks:
            dur = (s1 - s0) * len(tok) / total
            out.append(Word(tok, round(t, 3), round(t + dur, 3)))
            t += dur
    return out


class OpenAICompatAsrProvider:
    provider_key = "openai_compat_asr"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        model: str | None = None,
        max_upload_bytes: int = MAX_UPLOAD_BYTES,
        part_sec: float = 1200.0,
        usd_per_minute: float = USD_PER_MINUTE,
        timeout_sec: float = 600.0,
        client: Any = None,
        tmp_dir: str | Path | None = None,
    ) -> None:
        env = os.environ
        self.base_url = (
            base_url or env.get("ASR_CLOUD_BASE_URL") or env.get("LLM_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self.api_key = (
            api_key if api_key is not None else (env.get("ASR_CLOUD_API_KEY") or env.get("LLM_API_KEY"))
        )
        if not self.api_key:
            raise AsrUnavailable("ASR_CLOUD_API_KEY (or LLM_API_KEY) is not set")
        self.model = model or env.get("ASR_CLOUD_MODEL") or DEFAULT_MODEL
        self.max_upload_bytes = max_upload_bytes
        self.part_sec = part_sec
        self.usd_per_minute = usd_per_minute
        self.timeout_sec = timeout_sec
        self._client = client
        self.tmp_dir = Path(tmp_dir) if tmp_dir else None

    def _http(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=httpx.Timeout(self.timeout_sec, connect=15.0))
        return self._client

    def request_part(self, path: Path, language: str, *, offset: float = 0.0) -> list[Word]:
        import httpx

        data = {
            "model": self.model,
            "language": normalize_language(language),
            "response_format": "verbose_json",
            "timestamp_granularities[]": ["word", "segment"],
        }
        try:
            with open(path, "rb") as fh:
                resp = self._http().post(
                    f"{self.base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    data=data,
                    files={"file": (path.name, fh, "application/octet-stream")},
                )
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            raise AsrTransient(f"transcription network error: {exc}") from exc
        if resp.status_code == 429 or resp.status_code >= 500:
            raise AsrTransient(f"transcription HTTP {resp.status_code}: {resp.text[:300]}")
        if resp.status_code >= 400:
            raise AsrBadResponse(f"transcription HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise AsrBadResponse(f"transcription: not JSON: {resp.text[:200]}") from exc
        if not isinstance(payload, dict):
            raise AsrBadResponse("transcription: unexpected JSON shape")
        return parse_verbose_json(payload, offset=offset)

    def transcribe(
        self, audio_path: str | Path, language: str = "ru", progress: ProgressFn | None = None
    ) -> Transcript:
        path = Path(audio_path)
        if not path.is_file():
            raise AsrUnavailable(f"audio file not found: {path}")
        started = time.monotonic()
        duration = ffprobe_duration(path) or 0.0
        words: list[Word] = []
        tmp_root = self.tmp_dir or Path(tempfile.gettempdir())
        tmp_root.mkdir(parents=True, exist_ok=True)
        emit_progress(progress, 0.02)
        if path.suffix.lower() in SUPPORTED_SUFFIXES and path.stat().st_size <= self.max_upload_bytes:
            words = self.request_part(path, language)
        else:
            with tempfile.TemporaryDirectory(prefix="asr_cloud_", dir=tmp_root) as td:
                opus = ["-ar", "16000", "-c:a", "libopus", "-b:a", "24k"]
                small = ffmpeg_audio(path, Path(td) / "audio.ogg", opus)
                if small.stat().st_size <= self.max_upload_bytes:
                    words = self.request_part(small, language)
                else:
                    n_parts = max(1, math.ceil(duration / self.part_sec))
                    for k in range(n_parts):
                        part = ffmpeg_audio(
                            path,
                            Path(td) / f"part_{k:03d}.ogg",
                            opus,
                            start=k * self.part_sec,
                            duration=self.part_sec,
                        )
                        words.extend(self.request_part(part, language, offset=k * self.part_sec))
                        part.unlink(missing_ok=True)
                        emit_progress(progress, (k + 1) / n_parts)
        emit_progress(progress, 1.0)
        words.sort(key=lambda w: (w.s, w.e))
        duration = max(duration, words[-1].e if words else 0.0)
        wall = time.monotonic() - started
        return Transcript.from_words(
            words,
            language=normalize_language(language),
            duration=round(duration, 3),
            provider=self.provider_key,
            model=self.model,
            rtfx=round(duration / wall, 2) if wall > 0 and duration else None,
            cost_usd=round(duration / 60.0 * self.usd_per_minute, 6),
        )


__all__ = ["DEFAULT_BASE_URL", "DEFAULT_MODEL", "OpenAICompatAsrProvider", "parse_verbose_json"]
