"""Значения метрик между снимками (docs/ARCHITECTURE.md §6.5, §6.7).

Снимки (`metrics_snapshots`) приходят по лестнице опроса (1, 2, 4 … 168 ч, дальше раз в сутки и раз в
неделю). Всё, что показывается «на ровный возраст» (24 ч, 72 ч, 7 дней) и «на конец бизнес-дня»,
получается **линейной интерполяцией** между соседними снимками. Правила:

* **без экстраполяции вперёд.** Возраст, после которого снимка ещё нет, — неизвестен (`None`), даже если
  ролик уже старше: коллектор догонит, и число появится. Исключение — конец дня (`carry_forward=True`):
  для ролл-апа последнее известное значение держится до следующего снимка, иначе «сегодня» было бы пустым;
* **якорь публикации.** В момент `published_at` у ролика 0 просмотров, лайков и комментариев: точка
  `(published_at, 0)` добавляется перед первым снимком, если снимок не раньше публикации (иначе время
  публикации записано неверно, и якорь не ставится);
* снимки с пустым значением (`None`) пропускаются; из снимков с одинаковым временем берётся последний.

Всё время — aware UTC. Бизнес-день — `business_timezone` (Europe/Moscow): конец дня `d` — полночь
начала `d + 1` в этом поясе (`day_end_utc`), переходы времени учитываются `zoneinfo`.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

__all__ = [
    "AgeValue",
    "Series",
    "bracket_value",
    "build_series",
    "daily_eod",
    "day_end_utc",
    "round_count",
    "value_at",
    "value_at_age",
]

Series = list[tuple[datetime, float]]
"""Точки (время UTC, значение), по возрастанию времени, без дублей."""


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def round_count(value: float) -> int:
    """Счётчик из интерполяции: округление половины вверх (не банковское), без отрицательных нулей."""
    return int(math.floor(value + 0.5))


def build_series(
    samples: Iterable[tuple[datetime, float | int | None]], *, published_at: datetime | None = None
) -> Series:
    """Отсортированный ряд без пустых значений и дублей времени; якорь `(published_at, 0)` — см. модуль."""
    by_time: dict[datetime, float] = {}
    for t, v in samples:
        if v is None:
            continue
        by_time[_utc(t)] = float(v)  # одинаковое время — побеждает последний
    series: Series = sorted(by_time.items())
    if published_at is not None:
        anchor = _utc(published_at)
        if not series or anchor < series[0][0]:
            series.insert(0, (anchor, 0.0))
    return series


def value_at(
    series: Sequence[tuple[datetime, float]], t: datetime, *, carry_forward: bool = False
) -> float | None:
    """Значение в момент `t`: точное, интерполированное или `None` (раньше первой точки; позже
    последней — только без `carry_forward`)."""
    if not series:
        return None
    t = _utc(t)
    times = [p[0] for p in series]
    idx = bisect_right(times, t)
    if idx == 0:
        return None
    t0, v0 = series[idx - 1]
    if t0 == t:
        return v0
    if idx == len(series):
        return v0 if carry_forward else None
    t1, v1 = series[idx]
    span = (t1 - t0).total_seconds()
    if span <= 0:
        return v1
    return v0 + (v1 - v0) * ((t - t0).total_seconds() / span)


@dataclass(frozen=True)
class AgeValue:
    value: float | None
    interpolated: bool
    reached: bool


def value_at_age(
    series: Sequence[tuple[datetime, float]],
    published_at: datetime | None,
    age_hours: float,
    *,
    now: datetime,
) -> AgeValue:
    """Значение на возраст `age_hours` от публикации. Не достигнут — `reached=False, value=None`;
    достигнут, но снимка после этого возраста ещё нет — `value=None` (без экстраполяции)."""
    if published_at is None:
        return AgeValue(None, False, False)
    at = _utc(published_at) + timedelta(hours=age_hours)
    if at > _utc(now):
        return AgeValue(None, False, False)
    v = value_at(series, at)
    exact = any(p[0] == at for p in series)
    return AgeValue(v, v is not None and not exact, True)


def bracket_value(
    published_at: datetime | None,
    at: datetime,
    prev: tuple[datetime, float] | None,
    nxt: tuple[datetime, float] | None,
    *,
    carry_forward: bool = False,
) -> float | None:
    """То же, что `value_at`, по двум соседним снимкам из SQL (последний не позже `at` и первый после):
    так значения на возраст для сотен роликов считаются без загрузки всех снимков."""
    points: list[tuple[datetime, float | None]] = []
    if prev is not None:
        points.append(prev)
    if nxt is not None:
        points.append(nxt)
    anchor = published_at if prev is None else None  # якорь нужен, только если слева снимка нет
    series = build_series(points, published_at=anchor)
    return value_at(series, at, carry_forward=carry_forward)


def day_end_utc(day: date, tz: str | ZoneInfo) -> datetime:
    """Конец бизнес-дня `day` (= начало следующего) в UTC."""
    zone = tz if isinstance(tz, ZoneInfo) else ZoneInfo(tz)
    return datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)


def daily_eod(
    series: Sequence[tuple[datetime, float]], first_day: date, last_day: date, tz: str | ZoneInfo
) -> list[tuple[date, int, int]]:
    """`(день, значение на конец дня, прирост за день)` для дней `first_day..last_day` включительно.

    Значение на конец дня — интерполяция с удержанием последнего известного значения (`carry_forward`);
    до первой точки — 0. Прирост считается от конца предыдущего дня, в том числе для `first_day`, поэтому
    сумма приростов за окно равна разнице значений на его краях (ряд «телескопический»). Прирост может быть
    отрицательным: площадки пересчитывают просмотры (накрутки, спам).
    """
    zone = tz if isinstance(tz, ZoneInfo) else ZoneInfo(tz)
    out: list[tuple[date, int, int]] = []
    if last_day < first_day:
        return out
    prev_v = value_at(series, day_end_utc(first_day - timedelta(days=1), zone), carry_forward=True)
    prev = round_count(prev_v) if prev_v is not None else 0
    d = first_day
    while d <= last_day:
        v = value_at(series, day_end_utc(d, zone), carry_forward=True)
        cur = round_count(v) if v is not None else 0
        out.append((d, cur, cur - prev))
        prev = cur
        d += timedelta(days=1)
    return out
