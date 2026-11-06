"""Интерполяция снимков метрик (M4-B, `analytics/interp.py`): ровный возраст без экстраполяции,
конец бизнес-дня МСК, якорь публикации и «телескопические» приросты."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from autoshorts.analytics.interp import (
    bracket_value,
    build_series,
    daily_eod,
    day_end_utc,
    round_count,
    value_at,
    value_at_age,
)

PUB = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)  # 12:00 МСК


def h(hours: float) -> datetime:
    return PUB + timedelta(hours=hours)


def test_build_series_sorts_drops_none_dedups_and_anchors() -> None:
    s = build_series([(h(2), 20), (h(1), None), (h(1), 10), (h(2), 25)], published_at=PUB)
    assert s == [(PUB, 0.0), (h(1), 10.0), (h(2), 25.0)]  # одинаковое время — последний
    # снимок раньше публикации: время публикации неверное, якорь не ставится
    s2 = build_series([(PUB - timedelta(minutes=5), 3)], published_at=PUB)
    assert s2 == [(PUB - timedelta(minutes=5), 3.0)]
    # naive — считается UTC
    s3 = build_series([(datetime(2026, 9, 20, 10, 0), 1)])
    assert s3[0][0].tzinfo is UTC


def test_value_at_interpolates_without_extrapolation() -> None:
    s = build_series([(h(24), 1000), (h(48), 3000)], published_at=PUB)
    assert value_at(s, h(24)) == 1000
    assert value_at(s, h(36)) == pytest.approx(2000)
    assert value_at(s, h(12)) == pytest.approx(500)  # между якорем и первым снимком
    assert value_at(s, PUB - timedelta(hours=1)) is None
    assert value_at(s, h(60)) is None
    assert value_at(s, h(60), carry_forward=True) == 3000
    assert value_at([], h(1)) is None


def test_value_at_age_reached_and_flags() -> None:
    now = h(80)
    s = build_series([(h(1), 10), (h(70), 7000), (h(74), 7400)], published_at=PUB)
    v72 = value_at_age(s, PUB, 72, now=now)
    assert v72.reached and v72.interpolated and v72.value == pytest.approx(7200)
    exact = value_at_age(s, PUB, 70, now=now)
    assert exact.value == 7000 and not exact.interpolated
    not_yet = value_at_age(s, PUB, 168, now=now)
    assert not not_yet.reached and not_yet.value is None
    # возраст достигнут, но снимка после него нет — неизвестно (коллектор догонит)
    lagging = value_at_age(s, PUB, 76, now=now)
    assert lagging.reached and lagging.value is None
    assert value_at_age(s, None, 24, now=now).value is None


def test_bracket_value_matches_full_series() -> None:
    at = h(72)
    assert bracket_value(PUB, at, (h(70), 7000), (h(74), 7400)) == pytest.approx(7200)
    # слева снимка нет — от якоря публикации
    assert bracket_value(PUB, h(1), None, (h(2), 200)) == pytest.approx(100)
    # справа нет — без экстраполяции, с удержанием — последнее известное
    assert bracket_value(PUB, at, (h(70), 7000), None) is None
    assert bracket_value(PUB, at, (h(70), 7000), None, carry_forward=True) == 7000
    assert bracket_value(PUB, at, None, None) is None


def test_day_end_utc_moscow() -> None:
    assert day_end_utc(date(2026, 9, 26), "Europe/Moscow") == datetime(2026, 9, 26, 21, 0, tzinfo=UTC)
    assert day_end_utc(date(2026, 9, 26), "UTC") == datetime(2026, 9, 27, 0, 0, tzinfo=UTC)


def test_round_count_half_up() -> None:
    assert round_count(2.5) == 3 and round_count(3.5) == 4 and round_count(0.4) == 0


def test_daily_eod_moscow_days_and_telescoping_deltas() -> None:
    # публикация 20.09 12:00 МСК; снимки: 20.09 23:00 МСК = 900, 21.09 23:00 МСК = 1900
    snaps = [
        (datetime(2026, 9, 20, 20, 0, tzinfo=UTC), 900),
        (datetime(2026, 9, 21, 20, 0, tzinfo=UTC), 1900),
    ]
    s = build_series(snaps, published_at=PUB)
    rows = daily_eod(s, date(2026, 9, 20), date(2026, 9, 23), "Europe/Moscow")
    # конец 20.09 МСК = 21:00Z: между 900 (20:00Z) и 1900 (следующие сутки) → 900 + 1000/24
    assert rows[0] == (date(2026, 9, 20), 942, 942)
    # конец 21.09 = 21:00Z 21.09: после последнего снимка — удерживается 1900
    assert rows[1] == (date(2026, 9, 21), 1900, 958)
    assert rows[2] == (date(2026, 9, 22), 1900, 0)
    assert sum(r[2] for r in rows) == rows[-1][1]  # сумма приростов = значение на конец окна
    # окно, начатое позже публикации: прирост первого дня — от конца предыдущего дня
    later = daily_eod(s, date(2026, 9, 21), date(2026, 9, 21), "Europe/Moscow")
    assert later == [(date(2026, 9, 21), 1900, 958)]
    assert daily_eod(s, date(2026, 9, 22), date(2026, 9, 21), "Europe/Moscow") == []


def test_daily_eod_negative_recount_kept() -> None:
    snaps = [
        (datetime(2026, 9, 20, 20, 0, tzinfo=UTC), 1000),
        (datetime(2026, 9, 21, 20, 0, tzinfo=UTC), 800),  # площадка вычла накрутку
    ]
    s = build_series(snaps, published_at=PUB)
    rows = daily_eod(s, date(2026, 9, 21), date(2026, 9, 21), "Europe/Moscow")
    assert rows[0][2] < 0 and rows[0][1] == 800
