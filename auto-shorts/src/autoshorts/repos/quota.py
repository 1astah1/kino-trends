"""SQL квот внешних API `api_quota_usage` (миграции 0006, 0017; docs/ARCHITECTURE.md §6.5). Владелец — M4-A.

Сутки квоты считаются в поясе квоты через БД: `(now() AT TIME ZONE %(qtz)s)::date`, для YouTube —
America/Los_Angeles (`schemas.metrics.YT_QUOTA_TIMEZONE`). Строка — (api, сутки, куст); куст —
«ведро» ключа: id куста со своим ключом или `default` для общего ключа `YOUTUBE_API_KEY`.

`try_consume` — атомарное списание перед вызовом: одна инструкция INSERT … ON CONFLICT DO UPDATE …
WHERE units + n <= hard, поэтому два воркера не перескочат жёсткий стоп (`yt_quota_hard_units`).
Коллектор метрик и трендоскаут делят одну квоту куста и списывают через эти же функции.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

Row = dict[str, Any]

__all__ = [
    "add",
    "clock",
    "exhaust",
    "list_usage",
    "try_consume",
    "used",
]

_DAY = "(now() AT TIME ZONE %(qtz)s)::date"


def clock(conn: Any, tz: str) -> Row:
    """Текущие сутки квоты, момент сброса (полночь пояса квоты, UTC) и now() БД."""
    return conn.execute(
        """
        SELECT (now() AT TIME ZONE %(qtz)s)::date AS quota_day,
               ((date_trunc('day', now() AT TIME ZONE %(qtz)s) + interval '1 day') AT TIME ZONE %(qtz)s)
                 AS reset_at,
               now() AS now
        """,
        {"qtz": tz},
    ).fetchone()


def used(conn: Any, *, api: str, cluster_id: str, tz: str) -> int:
    row = conn.execute(
        f"SELECT units FROM api_quota_usage"
        f" WHERE api = %(api)s AND cluster_id = %(cl)s AND quota_day = {_DAY}",
        {"api": api, "cl": cluster_id, "qtz": tz},
    ).fetchone()
    return int(row["units"]) if row else 0


def try_consume(
    conn: Any, *, api: str, cluster_id: str, units: int, hard_limit: int | None, tz: str, calls: int = 1
) -> int | None:
    """Списать `units` за текущие сутки, если итог не превысит `hard_limit`. Итог или None (стоп)."""
    if hard_limit is not None and units > hard_limit:
        return None
    row = conn.execute(
        f"""
        INSERT INTO api_quota_usage (api, quota_day, cluster_id, units, calls, updated_at)
        VALUES (%(api)s, {_DAY}, %(cl)s, CAST(%(units)s AS int), CAST(%(calls)s AS int), now())
        ON CONFLICT (api, quota_day, cluster_id) DO UPDATE
          SET units = api_quota_usage.units + EXCLUDED.units,
              calls = api_quota_usage.calls + EXCLUDED.calls,
              updated_at = now()
          WHERE CAST(%(hard)s AS int) IS NULL
             OR api_quota_usage.units + EXCLUDED.units <= CAST(%(hard)s AS int)
        RETURNING units
        """,
        {"api": api, "cl": cluster_id, "units": units, "calls": calls, "hard": hard_limit, "qtz": tz},
    ).fetchone()
    return int(row["units"]) if row else None


def add(
    conn: Any, *, api: str, cluster_id: str, tz: str, units: int = 0, calls: int = 0, errors: int = 0
) -> int:
    """Безусловно добавить к счётчикам текущих суток (доплата за ретраи, ошибки). Возвращает units."""
    row = conn.execute(
        f"""
        INSERT INTO api_quota_usage (api, quota_day, cluster_id, units, calls, errors, updated_at)
        VALUES (%(api)s, {_DAY}, %(cl)s, CAST(%(units)s AS int), CAST(%(calls)s AS int),
                CAST(%(errors)s AS int), now())
        ON CONFLICT (api, quota_day, cluster_id) DO UPDATE
          SET units = api_quota_usage.units + EXCLUDED.units,
              calls = api_quota_usage.calls + EXCLUDED.calls,
              errors = api_quota_usage.errors + EXCLUDED.errors,
              updated_at = now()
        RETURNING units
        """,
        {"api": api, "cl": cluster_id, "units": units, "calls": calls, "errors": errors, "qtz": tz},
    ).fetchone()
    return int(row["units"])


def exhaust(conn: Any, *, api: str, cluster_id: str, tz: str, floor_units: int) -> int:
    """Площадка сказала «квота исчерпана»: поднять units до `floor_units` (не ниже текущего)."""
    row = conn.execute(
        f"""
        INSERT INTO api_quota_usage (api, quota_day, cluster_id, units, updated_at)
        VALUES (%(api)s, {_DAY}, %(cl)s, CAST(%(floor)s AS int), now())
        ON CONFLICT (api, quota_day, cluster_id) DO UPDATE
          SET units = GREATEST(api_quota_usage.units, EXCLUDED.units), updated_at = now()
        RETURNING units
        """,
        {"api": api, "cl": cluster_id, "floor": floor_units, "qtz": tz},
    ).fetchone()
    return int(row["units"])


def list_usage(
    conn: Any,
    *,
    date_from: date,
    date_to: date,
    apis: Sequence[str] | None = None,
    cluster_ids: Sequence[str] | None = None,
) -> list[Row]:
    """Строки за сутки квоты [date_from, date_to], новые сверху."""
    return conn.execute(
        """
        SELECT api, cluster_id, quota_day, units, calls, errors, updated_at
        FROM api_quota_usage
        WHERE quota_day BETWEEN %(df)s AND %(dt)s
          AND (CAST(%(apis)s AS text[]) IS NULL OR api = ANY(CAST(%(apis)s AS text[])))
          AND (CAST(%(cls)s AS text[]) IS NULL OR cluster_id = ANY(CAST(%(cls)s AS text[])))
        ORDER BY quota_day DESC, api, cluster_id
        """,
        {
            "df": date_from,
            "dt": date_to,
            "apis": list(apis) if apis else None,
            "cls": list(cluster_ids) if cluster_ids else None,
        },
    ).fetchall()
