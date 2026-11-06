"""Контракт транскрипта для всех потоков нарезки (docs/ARCHITECTURE.md §6.3.2, §5 `transcripts`).

Структуры:
    Word(w, s, e, p)                       — слово, время в секундах от начала источника,
                                             p — уверенность или None
    Sentence(sid, s, e, text, w0, w1)      — предложение; sid с 0 подряд; w0..w1 — индексы слов ВКЛЮЧИТЕЛЬНО
    Transcript(language, duration, provider, model, words, sentences, vad, rtfx, cost_usd)

Файл `transcript.json.gz` (gzip + JSON, версия формата FORMAT_VERSION):
    {"version": 1, "language", "duration", "provider", "model", "rtfx", "cost_usd",
     "words": [{"w","s","e","sid"[,"p"]}], "sentences": [{"sid","s","e","text","w0","w1"}], "vad": [[s,e]]}
Время и уверенность округляются до 3 знаков (миллисекунды), поэтому round-trip точен для
значений с точностью до мс. `sid` у слова выводится из предложений и при загрузке игнорируется.

Разбиение на предложения (`segment_sentences`): по `. ? ! …` в конце слова (закрывающие
кавычки и скобки не мешают), принудительный разрез на паузе > 1,2 с и страховка длины:
предложение не длиннее `max_sentence_sec` и `max_sentence_words`, режем в лучшей точке
второй части буфера (самая длинная пауза, бонус за `, ; : —`).
"""

from __future__ import annotations

import gzip
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FORMAT_VERSION = 1

PAUSE_SPLIT_SEC = 1.2
MAX_SENTENCE_SEC = 15.0
MAX_SENTENCE_WORDS = 50

_TERMINAL = (".", "?", "!", "…")
_SOFT = (",", ";", ":", "—", "–")
_CLOSERS = "\"'»”’)]}"
_SOFT_BONUS = 0.5
_EPS = 1e-6  # шум float: 4.5 - 3.3 = 1.2000000000000002, время у провайдеров с точностью до мс


@dataclass(frozen=True, slots=True)
class Word:
    w: str
    s: float
    e: float
    p: float | None = None


@dataclass(frozen=True, slots=True)
class Sentence:
    sid: int
    s: float
    e: float
    text: str
    w0: int  # индекс первого слова
    w1: int  # индекс последнего слова (включительно)

    @property
    def n_words(self) -> int:
        return self.w1 - self.w0 + 1

    @property
    def duration(self) -> float:
        return self.e - self.s


@dataclass(slots=True)
class Transcript:
    language: str
    duration: float
    provider: str
    model: str | None
    words: list[Word]
    sentences: list[Sentence]
    vad: list[tuple[float, float]] = field(default_factory=list)
    rtfx: float | None = None
    cost_usd: float = 0.0

    # ------------------------------------------------------------------ сборка

    @classmethod
    def from_words(
        cls,
        words: Sequence[Word],
        *,
        language: str,
        duration: float,
        provider: str,
        model: str | None = None,
        **kwargs: Any,
    ) -> Transcript:
        """Транскрипт из слов провайдера: предложения строятся `segment_sentences`."""
        ws = list(words)
        return cls(language, duration, provider, model, ws, segment_sentences(ws), **kwargs)

    def sentence_words(self, sid: int) -> list[Word]:
        st = self.sentences[sid]
        return self.words[st.w0 : st.w1 + 1]

    # ------------------------------------------------------------------ сериализация

    def to_dict(self) -> dict[str, Any]:
        word_sid = [-1] * len(self.words)
        for st in self.sentences:
            for i in range(st.w0, st.w1 + 1):
                word_sid[i] = st.sid
        words_out = []
        for i, wd in enumerate(self.words):
            item: dict[str, Any] = {"w": wd.w, "s": _r(wd.s), "e": _r(wd.e), "sid": word_sid[i]}
            if wd.p is not None:
                item["p"] = _r(wd.p)
            words_out.append(item)
        return {
            "version": FORMAT_VERSION,
            "language": self.language,
            "duration": _r(self.duration),
            "provider": self.provider,
            "model": self.model,
            "rtfx": None if self.rtfx is None else _r(self.rtfx),
            "cost_usd": round(float(self.cost_usd), 6),
            "words": words_out,
            "sentences": [
                {"sid": st.sid, "s": _r(st.s), "e": _r(st.e), "text": st.text, "w0": st.w0, "w1": st.w1}
                for st in self.sentences
            ],
            "vad": [[_r(a), _r(b)] for a, b in self.vad],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transcript:
        version = int(data.get("version", FORMAT_VERSION))
        if version > FORMAT_VERSION:
            raise ValueError(f"transcript format version {version} is newer than supported {FORMAT_VERSION}")
        words = [
            Word(
                str(it["w"]), float(it["s"]), float(it["e"]), None if it.get("p") is None else float(it["p"])
            )
            for it in data.get("words", [])
        ]
        sentences = [
            Sentence(
                int(it["sid"]), float(it["s"]), float(it["e"]), str(it["text"]), int(it["w0"]), int(it["w1"])
            )
            for it in data.get("sentences", [])
        ]
        n = len(words)
        for i, st in enumerate(sentences):
            if st.sid != i or not (0 <= st.w0 <= st.w1 < n):
                raise ValueError(f"bad sentence #{i}: sid={st.sid} w0={st.w0} w1={st.w1} words={n}")
        rtfx = data.get("rtfx")
        return cls(
            language=str(data["language"]),
            duration=float(data["duration"]),
            provider=str(data["provider"]),
            model=None if data.get("model") is None else str(data["model"]),
            words=words,
            sentences=sentences,
            vad=[(float(a), float(b)) for a, b in data.get("vad", [])],
            rtfx=None if rtfx is None else float(rtfx),
            cost_usd=float(data.get("cost_usd", 0.0)),
        )

    def save(self, path: str | Path) -> Path:
        """Атомарная запись: `.gz` — gzip, иначе обычный JSON. Возвращает путь."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if path.suffix == ".gz":
            raw = gzip.compress(raw, compresslevel=6, mtime=0)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_bytes(raw)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        return path

    @classmethod
    def load(cls, path: str | Path) -> Transcript:
        """Читает gzip или обычный JSON (формат определяется по сигнатуре, не по имени)."""
        raw = Path(path).read_bytes()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return cls.from_dict(json.loads(raw.decode("utf-8")))


# ---------------------------------------------------------------------- разбиение на предложения


def _r(x: float) -> float:
    return round(float(x), 3)


def _core(text: str) -> str:
    return text.strip().rstrip(_CLOSERS).rstrip()


def _is_terminal(text: str) -> bool:
    return _core(text).endswith(_TERMINAL)


def _is_soft(text: str) -> bool:
    return _core(text).endswith(_SOFT)


def _best_cut(words: Sequence[Word], a: int, b: int) -> int:
    """Индекс последнего слова первой части при разрезе буфера a..b+1 (b+1 — слово, которое не влезло).

    Кандидаты k из второй части буфера [a + (b-a+1)//3, b], очки = пауза после k (+ бонус за мягкую
    пунктуацию); при равенстве берём более поздний k, чтобы первая часть была длиннее.
    """
    lo = a + (b - a + 1) // 3
    best_k, best_score = b, float("-inf")
    for k in range(lo, b + 1):
        score = words[k + 1].s - words[k].e
        if _is_soft(words[k].w):
            score += _SOFT_BONUS
        if score >= best_score:
            best_k, best_score = k, score
    return best_k


def segment_sentences(
    words: Sequence[Word],
    *,
    pause_split: float = PAUSE_SPLIT_SEC,
    max_sentence_sec: float = MAX_SENTENCE_SEC,
    max_sentence_words: int = MAX_SENTENCE_WORDS,
) -> list[Sentence]:
    """Режет слова (отсортированные по времени) на предложения, sid с 0.

    1. Конец предложения — слово на `. ? ! …` (после снятия закрывающих кавычек и скобок).
    2. Пауза между соседними словами строго больше `pause_split` — принудительный разрез.
    3. Предложение длиннее `max_sentence_sec` или `max_sentence_words` режется в лучшей точке.
       Одно слово длиннее лимита остаётся отдельным предложением.
    """
    out: list[Sentence] = []

    def emit(a: int, b: int) -> None:
        chunk = words[a : b + 1]
        text = " ".join(t for t in (wd.w.strip() for wd in chunk) if t)
        out.append(Sentence(len(out), chunk[0].s, max(wd.e for wd in chunk), text, a, b))

    def too_long(a: int, i: int) -> bool:
        return (i - a + 1) > max_sentence_words or (words[i].e - words[a].s) > max_sentence_sec + _EPS

    start = 0
    for i, wd in enumerate(words):
        if i > start and wd.s - words[i - 1].e > pause_split + _EPS:
            emit(start, i - 1)
            start = i
        while i > start and too_long(start, i):
            k = _best_cut(words, start, i - 1)
            emit(start, k)
            start = k + 1
        if _is_terminal(wd.w):
            emit(start, i)
            start = i + 1
    if start < len(words):
        emit(start, len(words) - 1)
    return out


__all__ = [
    "FORMAT_VERSION",
    "MAX_SENTENCE_SEC",
    "MAX_SENTENCE_WORDS",
    "PAUSE_SPLIT_SEC",
    "Sentence",
    "Transcript",
    "Word",
    "segment_sentences",
]
