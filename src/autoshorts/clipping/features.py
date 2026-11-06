"""Признаки клипа по словам транскрипта (docs/ARCHITECTURE.md §6.3.4).

Все функции чистые, без numpy: работают со списком `Word` и границами клипа в секундах источника.

* `speech_rate` — слов в секунду на длительности клипа (цель 2,3–3,5).
* `dead_air_ratio` — доля клипа в паузах длиннее `min_gap` (0,5 с), включая подход и хвост.
  Короткие межсловные зазоры (CTC-тайминги плотные, но зазоры 0,05–0,3 с есть всегда) не
  считаются: иначе `1 − 3·dead_air` штрафовал бы любую нормальную речь.
* `first_word_latency` — от начала клипа до первого слова (цель < 0,3 с).
* `ends_on_terminal_punct` — последнее слово на `. ? ! …`.

`energy_*` — необязательная огибающая громкости для эвристического отбора (без LLM).
"""

from __future__ import annotations

import json
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from autoshorts.clipping.transcript import Word

DEAD_AIR_MIN_GAP = 0.5
_TERMINAL = (".", "?", "!", "…")
_CLOSERS = "\"'»”’)]}"


_MAX_WORD_SEC = 30.0  # страховка поиска: слово длиннее не бывает


def word_index_range(words: Sequence[Word], start: float, end: float) -> tuple[int, int] | None:
    """Индексы (i0, i1) включительно для слов с серединой в [start, end]; None, если слов нет.

    Слова отсортированы по времени: бинарный поиск по началу слова, затем проверка середины.
    """
    lo = bisect_left(words, start - _MAX_WORD_SEC, key=lambda w: w.s)
    hi = bisect_right(words, end + 1e-6, key=lambda w: w.s)
    i0 = i1 = None
    for i in range(lo, hi):
        mid = (words[i].s + words[i].e) / 2
        if start - 1e-6 <= mid <= end + 1e-6:
            if i0 is None:
                i0 = i
            i1 = i
    if i0 is None or i1 is None:
        return None
    return i0, i1


def words_in_range(words: Sequence[Word], start: float, end: float) -> list[Word]:
    """Слова, у которых середина лежит в [start, end]."""
    rng = word_index_range(words, start, end)
    if rng is None:
        return []
    return [w for w in words[rng[0] : rng[1] + 1] if start - 1e-6 <= (w.s + w.e) / 2 <= end + 1e-6]


def speech_rate(words: Sequence[Word], start: float, end: float) -> float:
    dur = end - start
    if dur <= 0:
        return 0.0
    return len(words) / dur


def dead_air_ratio(
    words: Sequence[Word], start: float, end: float, *, min_gap: float = DEAD_AIR_MIN_GAP
) -> float:
    dur = end - start
    if dur <= 0:
        return 1.0
    if not words:
        return 1.0
    silent = 0.0
    cursor = start
    for w in words:
        ws = max(start, w.s)
        gap = ws - cursor
        if gap > min_gap:
            silent += gap
        cursor = max(cursor, min(end, w.e))
    tail = end - cursor
    if tail > min_gap:
        silent += tail
    return max(0.0, min(1.0, silent / dur))


def first_word_latency(words: Sequence[Word], start: float) -> float:
    if not words:
        return float("inf")
    return max(0.0, words[0].s - start)


def ends_on_terminal_punct(words: Sequence[Word]) -> bool:
    if not words:
        return False
    core = words[-1].w.strip().rstrip(_CLOSERS).rstrip()
    return core.endswith(_TERMINAL)


@dataclass(slots=True)
class ClipFeatures:
    duration_sec: float
    n_words: int
    speech_rate_wps: float
    dead_air_ratio: float
    first_word_latency: float
    ends_on_terminal_punct: bool
    energy_rel: float | None = None  # средняя громкость клипа / медиана источника (эвристика)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, float):
                d[k] = round(v, 3) if v != float("inf") else None
        return d


def compute_features(
    words: Sequence[Word],
    start: float,
    end: float,
    *,
    min_gap: float = DEAD_AIR_MIN_GAP,
    energy: EnergyTrack | None = None,
) -> ClipFeatures:
    """Признаки клипа [start, end] по всем словам источника (слова внутри выбираются сами)."""
    inside = words_in_range(words, start, end)
    return ClipFeatures(
        duration_sec=max(0.0, end - start),
        n_words=len(inside),
        speech_rate_wps=speech_rate(inside, start, end),
        dead_air_ratio=dead_air_ratio(inside, start, end, min_gap=min_gap),
        first_word_latency=first_word_latency(inside, start),
        ends_on_terminal_punct=ends_on_terminal_punct(inside),
        energy_rel=None if energy is None else energy.relative(start, end),
    )


# ---------------------------------------------------------------------- огибающая громкости


@dataclass(slots=True)
class EnergyTrack:
    """Огибающая громкости с равномерным шагом `hop` секунд (значения ≥ 0, масштаб любой)."""

    hop: float
    values: list[float]
    _median: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.hop <= 0:
            raise ValueError("hop must be positive")
        vals = sorted(v for v in self.values if v > 0)
        self._median = vals[len(vals) // 2] if vals else 0.0

    @property
    def median(self) -> float:
        return self._median

    def mean(self, start: float, end: float) -> float:
        if not self.values:
            return 0.0
        i0 = max(0, int(start / self.hop))
        i1 = min(len(self.values), max(i0 + 1, int(end / self.hop + 0.999)))
        chunk = self.values[i0:i1]
        return sum(chunk) / len(chunk) if chunk else 0.0

    def relative(self, start: float, end: float) -> float | None:
        if self._median <= 0:
            return None
        return self.mean(start, end) / self._median


def energy_from_peaks(data: Any, duration: float) -> EnergyTrack:
    """Огибающая из peaks.json (формат audiowaveform/wavesurfer: {"data": [min, max, …]} или список).

    Пики растягиваются равномерно на `duration`. Пары min/max сворачиваются в max(|min|, |max|).
    """
    if isinstance(data, str | Path):
        data = json.loads(Path(data).read_text("utf-8"))
    raw = data.get("data") if isinstance(data, dict) else data
    if not raw or duration <= 0:
        return EnergyTrack(1.0, [])
    vals = [abs(float(v)) for v in raw]
    length = data.get("length") if isinstance(data, dict) else None
    if isinstance(length, int) and length > 0 and len(vals) == 2 * length:
        vals = [max(vals[i], vals[i + 1]) for i in range(0, len(vals), 2)]
    return EnergyTrack(duration / len(vals), vals)


def energy_from_wav(path: str | Path, *, hop: float = 0.5) -> EnergyTrack:
    """RMS-огибающая из WAV (лёгкая операция: soundfile + numpy, читает блоками)."""
    import numpy as np  # лениво
    import soundfile as sf

    out: list[float] = []
    with sf.SoundFile(str(path)) as f:
        block = max(1, int(f.samplerate * hop))
        for chunk in f.blocks(blocksize=block, dtype="float32", always_2d=True):
            mono = chunk.mean(axis=1)
            out.append(float(np.sqrt(np.mean(mono * mono))) if len(mono) else 0.0)
    return EnergyTrack(hop, out)


__all__ = [
    "DEAD_AIR_MIN_GAP",
    "ClipFeatures",
    "EnergyTrack",
    "compute_features",
    "dead_air_ratio",
    "ends_on_terminal_punct",
    "energy_from_peaks",
    "energy_from_wav",
    "first_word_latency",
    "speech_rate",
    "word_index_range",
    "words_in_range",
]
