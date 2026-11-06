"""Арифметика отрезков времени `[a, b]` (секунды): объединение, пересечение, вычитание.

Нужна метрике `author_share` (каждая секунда засчитывается один раз), QC (`expected_static`
вычитается из найденных «зависших» участков) и отчёту об оригинальности.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

Interval = tuple[float, float]
EPS = 1e-6


def normalize(items: Iterable[Sequence[float]], *, eps: float = EPS) -> list[Interval]:
    """Отсортированные непересекающиеся отрезки; касающиеся склеиваются, пустые выбрасываются."""
    spans = sorted((float(x[0]), float(x[1])) for x in items if float(x[1]) - float(x[0]) > eps)
    out: list[Interval] = []
    for a, b in spans:
        if out and a <= out[-1][1] + eps:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def union(*groups: Iterable[Sequence[float]]) -> list[Interval]:
    return normalize(x for g in groups for x in g)


def total(items: Iterable[Sequence[float]]) -> float:
    return sum(b - a for a, b in normalize(items))


def intersect(xs: Iterable[Sequence[float]], ys: Iterable[Sequence[float]]) -> list[Interval]:
    a_list, b_list = normalize(xs), normalize(ys)
    out: list[Interval] = []
    i = j = 0
    while i < len(a_list) and j < len(b_list):
        lo = max(a_list[i][0], b_list[j][0])
        hi = min(a_list[i][1], b_list[j][1])
        if hi - lo > EPS:
            out.append((lo, hi))
        if a_list[i][1] < b_list[j][1]:
            i += 1
        else:
            j += 1
    return out


def subtract(xs: Iterable[Sequence[float]], ys: Iterable[Sequence[float]]) -> list[Interval]:
    """xs минус ys."""
    cut = normalize(ys)
    out: list[Interval] = []
    for a, b in normalize(xs):
        cur = a
        for c, d in cut:
            if d <= cur or c >= b:
                continue
            if c > cur:
                out.append((cur, min(c, b)))
            cur = max(cur, d)
            if cur >= b:
                break
        if b - cur > EPS:
            out.append((cur, b))
    return normalize(out)


def clip_to(items: Iterable[Sequence[float]], lo: float, hi: float) -> list[Interval]:
    return intersect(items, [(lo, hi)])


def pad(items: Iterable[Sequence[float]], before: float, after: float | None = None) -> list[Interval]:
    after = before if after is None else after
    return normalize((a - before, b + after) for a, b in normalize(items))


def rounded(items: Iterable[Sequence[float]], nd: int = 3) -> list[list[float]]:
    return [[round(a, nd), round(b, nd)] for a, b in normalize(items)]


__all__ = ["Interval", "clip_to", "intersect", "normalize", "pad", "rounded", "subtract", "total", "union"]
