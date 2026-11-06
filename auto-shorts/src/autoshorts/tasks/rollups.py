"""Задача `stats_rollup` (docs/ARCHITECTURE.md §6.1, §6.7; расписание 0009: `stats_rollup_recent` раз в
15 минут с `{"days": 3}`, `stats_rollup_full` в 03:30 с `{"days": 35}`).

Лёгкий класс, без внешних вызовов и без бюджета. Логика — `analytics/rollups.py`; вручную —
`autoshorts-admin stats-rollup --days N`.
"""

from __future__ import annotations

from typing import Any

from autoshorts.tasks.registry import TaskContext, register_task

DEFAULT_DAYS = 3


@register_task("stats_rollup", "light", max_attempts=5)
def stats_rollup(ctx: TaskContext) -> dict[str, Any]:
    from autoshorts.analytics.rollups import stats_rollup as run
    from autoshorts.db.pool import connection

    days = int(ctx.payload.get("days") or DEFAULT_DAYS)
    ctx.check_cancelled()
    with connection() as conn:
        result = run(conn, days=days, tz=ctx.settings.business_timezone)
    ctx.progress(1.0)
    return result
