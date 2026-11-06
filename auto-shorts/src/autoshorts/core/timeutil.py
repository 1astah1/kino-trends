"""Время: UTC внутри, ISO 8601 с `Z` наружу, бизнес-день в `business_timezone` (§5.1, §8.3).

* Все метки времени в коде — aware-datetime в UTC (`utcnow()`).
* Наружу (API, JSON) — `isoformat_z(dt)`: `2026-09-26T19:00:00Z`.
* Даты фильтров `date_from`/`date_to` — включительно, в бизнес-часовом поясе
  (`date_range_utc` превращает их в полуинтервал UTC `[start, end)` для SQL).
* Бизнес-день в SQL считается часами БД: `(now() AT TIME ZONE CAST(%(tz)s AS text))::date`
  (`BUSINESS_DAY_SQL`), а не `date.today()` процесса — урок M0.
"""

from __future__ import annotations

import time as _time
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from functools import lru_cache
from zoneinfo import ZoneInfo

DEFAULT_BUSINESS_TIMEZONE = "Europe/Moscow"

# Фрагмент SQL с именованным параметром tz (CAST — правило §1.5 про параметры).
BUSINESS_DAY_SQL = "(now() AT TIME ZONE CAST(%(tz)s AS text))::date"


def utcnow() -> datetime:
    return datetime.now(UTC)


def monotonic_ms() -> int:
    return int(_time.monotonic() * 1000)


@lru_cache(maxsize=32)
def zone(name: str | None = None) -> ZoneInfo:
    """ZoneInfo по имени IANA (по умолчанию Europe/Moscow). Неизвестное имя — ValueError."""
    try:
        return ZoneInfo(name or DEFAULT_BUSINESS_TIMEZONE)
    except Exception as exc:  # ZoneInfoNotFoundError, ValueError
        raise ValueError(f"Неизвестный часовой пояс: {name!r}") from exc


def ensure_utc(dt: datetime) -> datetime:
    """Aware-datetime в UTC. Naive считается UTC (так их отдаёт psycopg при TimeZone=UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def isoformat_z(dt: datetime | None, *, timespec: str = "seconds") -> str | None:
    """`2026-09-26T19:00:00Z` (формат меток времени API, §5.1)."""
    if dt is None:
        return None
    return ensure_utc(dt).isoformat(timespec=timespec).replace("+00:00", "Z")


def parse_datetime(text: str) -> datetime:
    """ISO 8601 (в т. ч. с `Z`) → aware UTC. Без смещения — считается UTC."""
    return ensure_utc(datetime.fromisoformat(text.strip().replace("Z", "+00:00").replace("z", "+00:00")))


def business_day(at: datetime | None = None, tz: str | tzinfo | None = None) -> date:
    """Календарная дата момента `at` (по умолчанию сейчас) в бизнес-часовом поясе."""
    tzi = tz if isinstance(tz, tzinfo) else zone(tz)
    return ensure_utc(at or utcnow()).astimezone(tzi).date()


def day_bounds_utc(day: date, tz: str | tzinfo | None = None) -> tuple[datetime, datetime]:
    """Сутки `day` бизнес-пояса как полуинтервал UTC `[start, end)` (учитывает переходы времени)."""
    tzi = tz if isinstance(tz, tzinfo) else zone(tz)
    start = datetime.combine(day, time.min, tzinfo=tzi)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tzi)
    return start.astimezone(UTC), end.astimezone(UTC)


def date_range_utc(
    date_from: date | None, date_to: date | None, tz: str | tzinfo | None = None
) -> tuple[datetime | None, datetime | None]:
    """`date_from`/`date_to` включительно → `[start_utc, end_utc)`; None остаётся открытой границей."""
    if date_from and date_to and date_from > date_to:
        raise ValueError("date_from позже date_to")
    start = day_bounds_utc(date_from, tz)[0] if date_from else None
    end = day_bounds_utc(date_to, tz)[1] if date_to else None
    return start, end


def next_local_time_utc(
    anchor: time, tz: str | tzinfo | None = None, *, after: datetime | None = None
) -> datetime:
    """Ближайший момент (строго после `after`), когда в поясе tz наступает `anchor`.

    Для суточных расписаний с `schedules.anchor_local_time` (например, cleanup в 04:00 МСК).
    """
    tzi = tz if isinstance(tz, tzinfo) else zone(tz)
    now_local = ensure_utc(after or utcnow()).astimezone(tzi)
    candidate = datetime.combine(now_local.date(), anchor, tzinfo=tzi)
    if candidate <= now_local:
        candidate = datetime.combine(now_local.date() + timedelta(days=1), anchor, tzinfo=tzi)
    return candidate.astimezone(UTC)


def age_hours(since: datetime, now: datetime | None = None) -> float:
    """Возраст в часах (для лестницы опроса метрик и нормировки аутлаеров)."""
    return (ensure_utc(now or utcnow()) - ensure_utc(since)).total_seconds() / 3600.0
