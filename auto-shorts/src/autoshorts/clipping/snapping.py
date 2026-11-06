"""Снаппинг границ клипа к словам и джамп-каты по паузам (docs/ARCHITECTURE.md §6.3.5).

1. sid → [sentence.s, sentence.e] (делает вызывающий).
2. `snap_clip_to_words(search_window=1.5, max_lead=0.35, max_tail=0.45)` выбирает слово, с
   которого начинается клип, и слово, которым он заканчивается (в окне ±search_window от
   исходной границы, с предпочтением естественных стыков: пауза ≥ min_pause или конец
   предложения), и уводит разрез в тишину: не дальше max_lead до первого слова и max_tail
   после последнего, и не глубже середины паузы, чтобы не зацепить соседнее слово.
3. `tighten_pauses(max_gap=0.45, keep=0.15)` — план джамп-катов: паузы длиннее max_gap
   сжимаются до keep. Для контента с музыкой вызывающий его не включает.
5. `scene_cuts` (режим «сцена»): если незадолго до первого слова (≤ cut_lead) есть монтажная
   склейка — клип открывается с нового плана; если вскоре после последнего слова (≤ cut_tail)
   есть склейка — реакция/план доживает до неё и разрез ложится на монтаж, а не обрывает кадр.
4. Правка оператора кликом по слову — это тот же `snap_clip_to_words` с новой границей.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass, field

from autoshorts.clipping.features import words_in_range
from autoshorts.clipping.transcript import Word

SEARCH_WINDOW = 1.5
MAX_LEAD = 0.35
MAX_TAIL = 0.45
MIN_PAUSE = 0.15
TIGHTEN_MAX_GAP = 0.45
TIGHTEN_KEEP = 0.15
SCENE_CUT_LEAD = 1.2  # склейка не раньше чем за столько секунд до первого слова
SCENE_CUT_TAIL = 1.6  # склейка не позже чем через столько секунд после последнего слова
_TERMINAL = (".", "?", "!", "…")
_CLOSERS = "\"'»”’)]}"


def _is_terminal(w: Word) -> bool:
    return w.w.strip().rstrip(_CLOSERS).rstrip().endswith(_TERMINAL)


@dataclass(frozen=True, slots=True)
class SnapResult:
    start: float
    end: float
    w0: int  # индекс первого слова клипа в общем списке слов
    w1: int  # индекс последнего слова (включительно)
    lead: float  # тишина перед первым словом
    tail: float  # тишина после последнего слова

    @property
    def duration(self) -> float:
        return self.end - self.start


def _gap_before(words: Sequence[Word], i: int) -> float:
    return float("inf") if i == 0 else words[i].s - words[i - 1].e


def _gap_after(words: Sequence[Word], j: int) -> float:
    return float("inf") if j == len(words) - 1 else words[j + 1].s - words[j].e


def _candidates(words: Sequence[Word], lo_t: float, hi_t: float, attr: str) -> list[int]:
    """Индексы слов, у которых время `attr` ('s' или 'e') лежит в [lo_t, hi_t]."""
    # e >= s, а слово не длиннее 30 с: начинаем поиск по s с запасом
    k = bisect_left(words, lo_t - (30.0 if attr == "e" else 0.0), key=lambda w: w.s)
    out = []
    while k < len(words) and words[k].s <= hi_t:
        if lo_t <= getattr(words[k], attr) <= hi_t:
            out.append(k)
        k += 1
    return out


def _pick_start(words: Sequence[Word], t: float, sw: float, min_pause: float) -> int:
    """Первое слово клипа: ближайший к t естественный стык (пауза или конец предложения перед ним)."""
    n = len(words)
    idx = _candidates(words, t - sw, t + sw, "s")
    if not idx:
        return min(bisect_left(words, t, key=lambda w: w.s), n - 1)

    def natural(i: int) -> bool:
        return i == 0 or _gap_before(words, i) >= min_pause or _is_terminal(words[i - 1])

    return min(idx, key=lambda i: (not natural(i), abs(words[i].s - t), i))


def _pick_end(words: Sequence[Word], t: float, sw: float, min_pause: float) -> int:
    """Последнее слово клипа: ближайший к t естественный стык (пауза после или конец предложения)."""
    n = len(words)
    idx = _candidates(words, t - sw, t + sw, "e")
    if not idx:
        return max(0, min(bisect_left(words, t, key=lambda w: w.s) - 1, n - 1))

    def natural(j: int) -> bool:
        return j == n - 1 or _gap_after(words, j) >= min_pause or _is_terminal(words[j])

    return min(idx, key=lambda j: (not natural(j), abs(words[j].e - t), -j))


def snap_clip_to_words(
    words: Sequence[Word],
    start: float,
    end: float,
    *,
    search_window: float = SEARCH_WINDOW,
    max_lead: float = MAX_LEAD,
    max_tail: float = MAX_TAIL,
    min_pause: float = MIN_PAUSE,
    duration: float | None = None,
    scene_cuts: Sequence[float] = (),
    cut_lead: float = SCENE_CUT_LEAD,
    cut_tail: float = SCENE_CUT_TAIL,
    max_duration: float | None = None,
) -> SnapResult:
    """Границы клипа в тишине у слов. Слова отсортированы по времени.

    `scene_cuts` — отсортированные моменты смены сцены по видео: разрез уводится на склейку, если она
    близко (см. п. 5 в описании модуля) и соседнее слово при этом не задевается; `max_duration`
    не даёт расширению за склейку превысить лимит длины."""
    if not words:
        return SnapResult(start, end, -1, -1, 0.0, 0.0)
    i = _pick_start(words, start, search_window, min_pause)
    j = _pick_end(words, end, search_window, min_pause)
    if j < i:
        j = i
    gb = _gap_before(words, i)
    ga = _gap_after(words, j)
    lead = min(max_lead, gb / 2) if gb != float("inf") else max_lead
    tail = min(max_tail, ga / 2) if ga != float("inf") else max_tail
    lead = max(0.0, lead)
    tail = max(0.0, tail)
    s = max(0.0, words[i].s - lead)
    e = words[j].e + tail
    if duration is not None:
        e = min(e, duration)
    if scene_cuts:
        cuts = list(scene_cuts)
        prev_end = words[i - 1].e if i > 0 else 0.0
        lo = max(words[i].s - cut_lead, prev_end)
        k = bisect_left(cuts, lo)
        c_start = None
        while k < len(cuts) and cuts[k] <= words[i].s - 0.05:
            c_start = cuts[k]  # последняя склейка перед первым словом
            k += 1
        next_start = words[j + 1].s if j + 1 < len(words) else float("inf")
        hi = min(words[j].e + cut_tail, next_start - 0.05, duration if duration is not None else float("inf"))
        k2 = bisect_left(cuts, words[j].e + 0.1)
        c_end = cuts[k2] if k2 < len(cuts) and cuts[k2] <= hi else None
        new_s = c_start if c_start is not None else s
        new_e = (c_end - 0.04) if c_end is not None else e
        if max_duration is not None and new_e - new_s > max_duration + 1e-6:
            # сначала жертвуем расширением хвоста, затем — началом
            if c_end is not None and e - new_s <= max_duration + 1e-6:
                new_e = e
            elif c_start is not None and new_e - s <= max_duration + 1e-6:
                new_s = s
            else:
                new_s, new_e = s, e
        s, e = max(0.0, new_s), new_e
    return SnapResult(round(s, 3), round(e, 3), i, j, round(words[i].s - s, 3), round(e - words[j].e, 3))


# ---------------------------------------------------------------------- джамп-каты


@dataclass(slots=True)
class JumpCutPlan:
    """Куски источника, которые остаются после сжатия пауз, по порядку."""

    segments: list[tuple[float, float]] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return sum(b - a for a, b in self.segments)

    @property
    def removed_sec(self) -> float:
        if not self.segments:
            return 0.0
        return (self.segments[-1][1] - self.segments[0][0]) - self.duration

    def map_time(self, t: float) -> float | None:
        """Время источника → время смонтированного клипа; None, если момент вырезан."""
        acc = 0.0
        for a, b in self.segments:
            if a - 1e-6 <= t <= b + 1e-6:
                return acc + (min(max(t, a), b) - a)
            acc += b - a
        return None


def tighten_pauses(
    words: Sequence[Word],
    start: float,
    end: float,
    *,
    max_gap: float = TIGHTEN_MAX_GAP,
    keep: float = TIGHTEN_KEEP,
) -> JumpCutPlan:
    """Паузы между словами клипа длиннее max_gap сжимаются до keep (поровну по краям паузы).

    Подход и хвост клипа не трогаются: их уже ограничил снаппинг.
    """
    if keep < 0 or max_gap < keep:
        raise ValueError("need 0 <= keep <= max_gap")
    inside = words_in_range(words, start, end)
    segs: list[tuple[float, float]] = []
    seg_start = start
    half = keep / 2
    for a, b in zip(inside, inside[1:], strict=False):
        gap = b.s - a.e
        if gap > max_gap:
            cut_from = a.e + half
            cut_to = b.s - half
            segs.append((seg_start, cut_from))
            seg_start = cut_to
    segs.append((seg_start, end))
    return JumpCutPlan([(round(x, 3), round(y, 3)) for x, y in segs if y > x])


__all__ = [
    "MAX_LEAD",
    "MAX_TAIL",
    "MIN_PAUSE",
    "SEARCH_WINDOW",
    "TIGHTEN_KEEP",
    "TIGHTEN_MAX_GAP",
    "JumpCutPlan",
    "SnapResult",
    "snap_clip_to_words",
    "tighten_pauses",
]
