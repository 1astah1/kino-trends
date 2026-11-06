"""Окна транскрипта для LLM-отбора (docs/ARCHITECTURE.md §6.3.3).

LLM плохо считает миллисекунды, поэтому видит пронумерованные предложения
`[sid] (мм:сс) текст` и отвечает диапазонами sid.

* `build_windows` — окна ~90 с с перекрытием ~30 с, границы только по предложениям.
* `batch_windows` — по 6 окон в запрос (на час ≈ 10 запросов).
* `render_sentences` / `render_batch` — текст для промпта.
* `context_range` — контекст ±60 с вокруг кандидата для detail-прохода.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from autoshorts.clipping.transcript import Sentence

WINDOW_SEC = 90.0
OVERLAP_SEC = 30.0
WINDOWS_PER_REQUEST = 6
DETAIL_CONTEXT_SEC = 60.0


@dataclass(frozen=True, slots=True)
class Window:
    """Окно: предложения sid0..sid1 включительно, время [s, e]."""

    idx: int
    sid0: int
    sid1: int
    s: float
    e: float

    @property
    def duration(self) -> float:
        return self.e - self.s

    def contains(self, sid: int) -> bool:
        return self.sid0 <= sid <= self.sid1


def build_windows(
    sentences: Sequence[Sentence],
    *,
    window_sec: float = WINDOW_SEC,
    overlap_sec: float = OVERLAP_SEC,
) -> list[Window]:
    """Нарезает предложения на окна по границам предложений.

    Окно начинается с предложения `a` и набирает следующие, пока конец не выходит за
    `sentences[a].s + window_sec` (минимум одно предложение). Следующее окно начинается с первого
    предложения, стартующего не раньше `конец_окна − overlap_sec` (и строго после `a`), так что
    перекрытие ≈ overlap_sec и никогда не рвёт предложение. Последнее окно доходит до конца.
    """
    if window_sec <= 0 or overlap_sec < 0 or overlap_sec >= window_sec:
        raise ValueError("need window_sec > overlap_sec >= 0")
    n = len(sentences)
    out: list[Window] = []
    a = 0
    while a < n:
        limit = sentences[a].s + window_sec
        b = a
        while b + 1 < n and sentences[b + 1].e <= limit + 1e-6:
            b += 1
        out.append(Window(len(out), sentences[a].sid, sentences[b].sid, sentences[a].s, sentences[b].e))
        if b >= n - 1:
            break
        cut = sentences[b].e - overlap_sec
        nxt = b + 1
        for k in range(a + 1, b + 1):
            if sentences[k].s >= cut - 1e-6:
                nxt = k
                break
        a = nxt
    return out


def batch_windows(windows: Sequence[Window], per_request: int = WINDOWS_PER_REQUEST) -> list[list[Window]]:
    if per_request <= 0:
        raise ValueError("per_request must be positive")
    return [list(windows[i : i + per_request]) for i in range(0, len(windows), per_request)]


def fmt_ts(sec: float) -> str:
    """мм:сс; минуты не сворачиваются в часы (75:12), так LLM проще сравнивать."""
    sec = max(0.0, float(sec))
    total = int(sec)
    return f"{total // 60:02d}:{total % 60:02d}"


DATA_OPEN, DATA_CLOSE = "<transcript>", "</transcript>"
_DATA_TAG = re.compile(r"<\s*/?\s*transcript\b[^>]*>?", re.IGNORECASE)


def prompt_safe(text: str) -> str:
    """Текст транскрипта для промпта: без тегов-разделителей данных (транскрипт не может
    «закрыть» блок данных и дописать инструкции от имени системы) и в одну строку."""
    return " ".join(_DATA_TAG.sub(" ", text or "").split())


def wrap_data(block: str) -> str:
    """Блок чужих данных (расшифровки) между разделителями `<transcript>` … `</transcript>`."""
    return f"{DATA_OPEN}\n{block}\n{DATA_CLOSE}"


@dataclass(frozen=True, slots=True)
class Markers:
    """Подсказки о структуре видео для промпта отбора: паузы между репликами, тишина без слов
    (музыка, экшен), смены сцены по картинке и места заметно громче среднего. Без них модель видит
    только текст и режет диалог посреди обмена репликами; с ними границы сцен ей видны."""

    scene_cuts: tuple[float, ...] = ()
    energy: Any = None  # features.EnergyTrack | None
    pause_sec: float = 1.0
    silence_sec: float = 4.0
    loud_rel: float = 1.3

    def cuts_between(self, a: float, b: float) -> int:
        if not self.scene_cuts or b <= a:
            return 0
        return max(0, bisect_right(self.scene_cuts, b) - bisect_left(self.scene_cuts, a))

    def loud(self, a: float, b: float) -> bool:
        if self.energy is None or b - a < 0.3:
            return False
        rel = self.energy.relative(a, b)
        return rel is not None and rel >= self.loud_rel


MARKERS_LEGEND_RU = (
    "Строки «— … —» между репликами — паузы, отрезки без слов (музыка, экшен) и смены сцены по видео; "
    "пометка [громко] — звук заметно громче среднего по видео."
)
MARKERS_LEGEND_EN = (
    "Lines «— … —» between sentences mark pauses, stretches without speech (music, action) and scene "
    "cuts in the video; the [loud] tag marks audio well above the video's median."
)


def marker_line(prev: Sentence, cur: Sentence, markers: Markers, ru: bool = True) -> str | None:
    """Строка-маркер между двумя соседними предложениями или None, если между ними ничего заметного."""
    gap = cur.s - prev.e
    cuts = markers.cuts_between(prev.e, cur.s)
    parts: list[str] = []
    if gap >= markers.silence_sec:
        loud = markers.loud(prev.e, cur.s)
        if ru:
            parts.append(f"без слов {gap:.0f} с" + (", громко: экшен или музыка" if loud else ""))
        else:
            parts.append(f"no speech {gap:.0f} s" + (", loud: action or music" if loud else ""))
    elif gap >= markers.pause_sec:
        parts.append(f"пауза {gap:.1f} с" if ru else f"pause {gap:.1f} s")
    if cuts:
        word = "смена сцены" if ru else "scene cut"
        parts.append(word if cuts == 1 else f"{word} ×{cuts}")
    return f"— {', '.join(parts)} —" if parts else None


def render_sentence(st: Sentence, markers: Markers | None = None, ru: bool = True) -> str:
    line = f"[{st.sid}] ({fmt_ts(st.s)}) {prompt_safe(st.text)}"
    if markers is not None and markers.loud(st.s, st.e):
        line += " [громко]" if ru else " [loud]"
    return line


def render_sentences(
    sentences: Sequence[Sentence], sid0: int, sid1: int, markers: Markers | None = None, ru: bool = True
) -> str:
    """Строки `[sid] (мм:сс) текст` для sid0..sid1 включительно (sid == индекс в списке); с `markers`
    между предложениями появляются строки о паузах, тишине и сменах сцены."""
    sid0 = max(0, sid0)
    sid1 = min(len(sentences) - 1, sid1)
    lines: list[str] = []
    for i in range(sid0, sid1 + 1):
        if markers is not None and i > sid0:
            m = marker_line(sentences[i - 1], sentences[i], markers, ru)
            if m:
                lines.append(m)
        lines.append(render_sentence(sentences[i], markers, ru))
    return "\n".join(lines)


def render_batch(
    sentences: Sequence[Sentence], batch: Sequence[Window], markers: Markers | None = None, ru: bool = True
) -> str:
    """Несколько окон одним текстом, у каждого заголовок с номером, диапазоном sid и временем."""
    blocks = []
    for w in batch:
        head = f"### Окно {w.idx + 1}: предложения {w.sid0}–{w.sid1}, {fmt_ts(w.s)}–{fmt_ts(w.e)}"
        blocks.append(head + "\n" + render_sentences(sentences, w.sid0, w.sid1, markers, ru))
    return "\n\n".join(blocks)


def context_range(
    sentences: Sequence[Sentence], start_sid: int, end_sid: int, *, pad_sec: float = DETAIL_CONTEXT_SEC
) -> tuple[int, int]:
    """Диапазон sid с контекстом ±pad_sec вокруг [start_sid, end_sid] по границам предложений."""
    n = len(sentences)
    start_sid = max(0, min(start_sid, n - 1))
    end_sid = max(start_sid, min(end_sid, n - 1))
    lo_t = sentences[start_sid].s - pad_sec
    hi_t = sentences[end_sid].e + pad_sec
    a = start_sid
    while a > 0 and sentences[a - 1].s >= lo_t - 1e-6:
        a -= 1
    b = end_sid
    while b + 1 < n and sentences[b + 1].e <= hi_t + 1e-6:
        b += 1
    return a, b


__all__ = [
    "DATA_CLOSE",
    "DATA_OPEN",
    "DETAIL_CONTEXT_SEC",
    "OVERLAP_SEC",
    "MARKERS_LEGEND_EN",
    "MARKERS_LEGEND_RU",
    "Markers",
    "WINDOWS_PER_REQUEST",
    "marker_line",
    "WINDOW_SEC",
    "Window",
    "batch_windows",
    "build_windows",
    "context_range",
    "fmt_ts",
    "prompt_safe",
    "render_batch",
    "render_sentence",
    "render_sentences",
    "wrap_data",
]
