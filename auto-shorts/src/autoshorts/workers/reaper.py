"""Reaper протухших задач (docs/ARCHITECTURE.md §6.1).

Штатно запускается расписанием `task_reaper` (каждую минуту, обработчик в `tasks/system.py`).
Логика — `services.tasks.reap_stale_tasks`: `running` с `heartbeat_at < now() - 3 min` при
`attempts < max_attempts` возвращается в `queued` с backoff, иначе `failed`
(`error_class='worker_lost'`), и job тоже становится `failed`.
"""

from __future__ import annotations

import random

from autoshorts.db.pool import connection
from autoshorts.services.tasks import STALE_HEARTBEAT_SEC, ReapReport, reap_stale_tasks


def reap_once(
    *, stale_after_sec: float = STALE_HEARTBEAT_SEC, rng: random.Random | None = None
) -> ReapReport:
    """Один проход reaper'а в собственной транзакции."""
    with connection() as conn:
        return reap_stale_tasks(conn, stale_after_sec=stale_after_sec, rng=rng)
