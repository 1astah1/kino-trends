"""ASR provider protocol, errors and shared helpers (docs/ARCHITECTURE.md §5.5, §6.3.2).

    class AsrProvider(Protocol):
        provider_key: str
        def transcribe(self, audio_path, language="ru", progress=None) -> Transcript: ...

Helpers shared by providers (pure Python, no numpy):

* `words_from_tokens` — CTC tokens with frame timestamps -> `Word` list. The idea (sentencepiece
  tokens, a leading "▁"/space opens a word, punctuation glues to the neighbour, the end of a word is
  bounded by the next word) follows `_words_from_tokens` in mutonby/openshorts (MIT); this is a
  re-implementation for GigaAM v3 e2e CTC tokens, where "▁" can also be a standalone token.
* `attach_punctuation` — cloud ASR returns word timings without punctuation plus a punctuated text
  (segment / normalized text); we copy punctuation and case onto the timed words so that
  `segment_sentences` can split on `. ? ! …`.
* `wav_info`, `mock_allowed`, `ProgressFn`.
"""

from __future__ import annotations

import math
import os
import re
import subprocess
import unicodedata
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from autoshorts.clipping.transcript import Transcript, Word

try:  # worker error classes (M1); the provider package must stay importable without them
    from autoshorts.core.errors import PermanentError as _PermanentBase
    from autoshorts.core.errors import RetryableError as _RetryableBase
except Exception:  # pragma: no cover - fallback for a partial checkout

    class _RetryableBase(Exception):  # type: ignore[no-redef]
        pass

    class _PermanentBase(Exception):  # type: ignore[no-redef]
        pass


ProgressFn = Callable[[float], None]


class AsrError(Exception):
    """Base class of ASR provider failures."""


class AsrUnavailable(AsrError, _PermanentBase):
    """Provider cannot run here: models missing, no API key, mock in prod, unsupported input."""


class AsrBadResponse(AsrError, _PermanentBase):
    """Remote API answered with something we cannot parse, or rejected the request (4xx)."""


class AsrTransient(AsrError, _RetryableBase):
    """Network error, 5xx, 429, timeout: worth retrying later."""


@runtime_checkable
class AsrProvider(Protocol):
    provider_key: str

    def transcribe(
        self, audio_path: str | Path, language: str = "ru", progress: ProgressFn | None = None
    ) -> Transcript: ...


# ---------------------------------------------------------------------- env helpers

_PROD_ENVS = frozenset({"prod", "production"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def mock_allowed() -> bool:
    """Mock providers are allowed everywhere except APP_ENV=prod (unless ALLOW_MOCK_IN_PROD)."""
    if os.getenv("APP_ENV", "").strip().lower() not in _PROD_ENVS:
        return True
    return os.getenv("ALLOW_MOCK_IN_PROD", "").strip().lower() in _TRUTHY


def normalize_language(language: str | None) -> str:
    lang = (language or "ru").strip().lower().replace("_", "-")
    return lang.split("-")[0] or "ru"


def emit_progress(progress: ProgressFn | None, fraction: float) -> None:
    if progress is None:
        return
    try:
        progress(max(0.0, min(1.0, float(fraction))))
    except Exception:  # noqa: BLE001 - a broken callback must not kill transcription
        pass


# ---------------------------------------------------------------------- audio


@dataclass(frozen=True, slots=True)
class WavInfo:
    sample_rate: int
    channels: int
    frames: int
    sample_width: int

    @property
    def duration(self) -> float:
        return self.frames / float(self.sample_rate) if self.sample_rate else 0.0


def wav_info(path: str | Path) -> WavInfo | None:
    """Header of a PCM WAV file or None when it is not a readable PCM WAV."""
    try:
        with wave.open(str(path), "rb") as wf:
            return WavInfo(wf.getframerate(), wf.getnchannels(), wf.getnframes(), wf.getsampwidth())
    except (wave.Error, EOFError, OSError):
        return None


def ffmpeg_audio(
    src: str | Path,
    dst: str | Path,
    codec_args: Sequence[str],
    *,
    start: float | None = None,
    duration: float | None = None,
    threads: int = 2,
    timeout_sec: float = 1800.0,
) -> Path:
    """Decode `src` and write mono audio to `dst` with `codec_args` (nice 10, ffmpeg -threads 2).

    Used by cloud providers to shrink uploads (OGG/Opus) and to cut parts; the local provider uses it to
    get a 16 kHz PCM WAV. Raises `AsrUnavailable` when ffmpeg fails (the input is not decodable).
    """
    cmd = ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
           "-threads", str(threads)]  # fmt: skip
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vn", "-ac", "1", *codec_args, "-threads", str(threads), str(dst)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)
    except subprocess.TimeoutExpired as exc:
        raise AsrTransient(f"ffmpeg timed out on {Path(src).name}") from exc
    if proc.returncode != 0:
        Path(dst).unlink(missing_ok=True)
        raise AsrUnavailable(f"ffmpeg could not process {Path(src).name}: {proc.stderr.strip()[-500:]}")
    return Path(dst)


def ffprobe_duration(path: str | Path) -> float | None:
    """Container duration in seconds (ffprobe), None when unknown."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=60, check=False,
        )  # fmt: skip
        return float(proc.stdout.strip())
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return None


# ---------------------------------------------------------------------- tokens -> words

CTC_FRAME_SEC = 0.04  # GigaAM: 10 ms hop x subsampling 4
_OPENERS = frozenset("«„“\"'([{")
_DASHES = frozenset("—–-")
MIN_WORD_SEC = 0.06


def _has_alnum(text: str) -> bool:
    return any(ch.isalnum() for ch in text)


@dataclass(slots=True)
class _Group:
    text: str
    t_first: float | None
    t_last: float | None
    last_len: int
    lps: list[float]


def words_from_tokens(
    tokens: Sequence[str],
    timestamps: Sequence[float],
    logprobs: Sequence[float] | None = None,
    *,
    offset: float = 0.0,
    seg_end: float | None = None,
    frame: float = CTC_FRAME_SEC,
    start_shift: float = 0.0,
    char_sec: float = 0.06,
    min_tail: float = 0.06,
    max_tail: float = 0.40,
    gap: float = 0.0,
) -> list[Word]:
    """Merge CTC sub-word tokens into timed words.

    tokens      — decoded token strings; "▁" may already be replaced by " " (onnx-asr does that).
                  A token that starts with a space (or a lone space token) opens a new word.
    timestamps  — spike time of every token, seconds relative to the segment start.
    offset      — absolute time of the segment start (added to every timestamp).
    seg_end     — absolute end of the audio segment; no word ends after it.

    Start of a word = first alphanumeric token spike + `start_shift`. CTC spikes mark the *start* of a
    token, so the end is estimated as `last spike + frame + tail(len(last token))` and then clipped by
    the next word start (minus `gap`) and by `seg_end`. `p` is exp(mean token logprob) or None.
    """
    n = len(tokens)
    if len(timestamps) != n:
        raise ValueError(f"tokens ({n}) and timestamps ({len(timestamps)}) differ in length")
    lps = list(logprobs) if logprobs is not None else None
    if lps is not None and len(lps) != n:
        lps = None

    groups: list[_Group] = []
    open_new = True
    for i, raw in enumerate(tokens):
        tok = raw.replace("▁", " ")
        if tok[:1].isspace():
            open_new = True
        body = tok.strip()
        if not body:
            continue
        ts = float(timestamps[i])
        alnum = _has_alnum(body)
        if open_new or not groups:
            groups.append(_Group(body, ts if alnum else None, ts if alnum else None, len(body), []))
            open_new = False
        else:
            g = groups[-1]
            g.text += body
            if alnum:
                if g.t_first is None:
                    g.t_first = ts
                g.t_last = ts
                g.last_len = len(body)
        if lps is not None and alnum:
            groups[-1].lps.append(float(lps[i]))

    groups = _merge_punct_groups(groups)

    out: list[Word] = []
    starts = [offset + (g.t_first or 0.0) + start_shift for g in groups]
    for k, g in enumerate(groups):
        s = max(starts[k], out[-1].s + 1e-3 if out else starts[k])
        tail = min(max(char_sec * g.last_len, min_tail), max_tail)
        e = offset + (g.t_last or 0.0) + frame + tail
        if k + 1 < len(groups):
            e = min(e, starts[k + 1] - gap)
        if seg_end is not None:
            e = min(e, seg_end)
        e = max(e, s + MIN_WORD_SEC)
        p = None
        if g.lps:
            p = min(1.0, max(0.0, math.exp(sum(g.lps) / len(g.lps))))
        out.append(Word(g.text, round(s, 3), round(e, 3), None if p is None else round(p, 3)))
    return out


def _merge_punct_groups(groups: list[_Group]) -> list[_Group]:
    """Punctuation-only 'words': openers glue to the next word, the rest to the previous one."""
    res: list[_Group] = []
    carry = ""
    for g in groups:
        if g.t_first is None:  # no alphanumeric token inside
            if all(ch in _OPENERS for ch in g.text):
                carry += g.text
                continue
            if res:
                sep = " " if g.text[:1] in _DASHES else ""
                res[-1].text += sep + g.text
            else:
                carry += g.text
            continue
        if carry:
            g.text = carry + g.text
            carry = ""
        res.append(g)
    if carry and res:
        res[-1].text += carry
    return res


# ---------------------------------------------------------------------- punctuation transfer

_WORD_CORE = re.compile(r"[\w]+", re.UNICODE)


def norm_token(text: str) -> str:
    """Lower-case letters and digits only, ё -> е (for alignment and WER)."""
    t = unicodedata.normalize("NFKC", text).lower().replace("ё", "е")
    return "".join(_WORD_CORE.findall(t))


def attach_punctuation(words: Sequence[Word], text: str) -> list[Word]:
    """Copy case and punctuation from `text` (punctuated) onto timed `words` (raw).

    Alignment is a greedy walk over normalized tokens: equal tokens are matched one-to-one; when the
    sequences diverge (normalization turned "двадцать пять" into "25", etc.) the unmatched raw words
    stay as they are. If the text ends with terminal punctuation that was not transferred, it is put on
    the last word so that sentence splitting still works.
    """
    ws = list(words)
    if not ws or not text.strip():
        return ws
    toks = text.split()
    tnorm = [norm_token(t) for t in toks]
    out = list(ws)
    j = 0
    last_matched = -1
    for i, wd in enumerate(ws):
        wn = norm_token(wd.w)
        if not wn:
            continue
        # look ahead a little in the text for the same token
        for k in range(j, min(j + 4, len(toks))):
            if tnorm[k] == wn:
                if k > j and last_matched >= 0:
                    # skipped text tokens that are pure punctuation belong to the previous word
                    extra = "".join(t for t in toks[j:k] if not norm_token(t))
                    if extra:
                        prev = out[last_matched]
                        out[last_matched] = Word(prev.w + extra, prev.s, prev.e, prev.p)
                out[i] = Word(toks[k], wd.s, wd.e, wd.p)
                last_matched = i
                j = k + 1
                break
    stripped = text.rstrip()
    if stripped and stripped[-1] in ".?!…" and not out[-1].w.rstrip("\"'»”)").endswith((".", "?", "!", "…")):
        last = out[-1]
        out[-1] = Word(last.w + stripped[-1], last.s, last.e, last.p)
    return out


__all__ = [
    "CTC_FRAME_SEC",
    "AsrBadResponse",
    "AsrError",
    "AsrProvider",
    "AsrTransient",
    "AsrUnavailable",
    "ProgressFn",
    "WavInfo",
    "attach_punctuation",
    "emit_progress",
    "ffmpeg_audio",
    "ffprobe_duration",
    "mock_allowed",
    "norm_token",
    "normalize_language",
    "wav_info",
    "words_from_tokens",
]
