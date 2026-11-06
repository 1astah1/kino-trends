"""Системные задачи очереди (docs/ARCHITECTURE.md §6.1, §8.2).

* `task_reaper` (каждую минуту, расписание 0009) — возврат протухших задач, уборка записей
  мёртвых воркеров (`services.tasks.reap_stale_tasks`); затем, отдельной транзакцией, снятие
  резервов бюджета без живого хозяина (`services.budget.reap_orphan_reservations`, 0016: задача
  уже не running — в том числе только что возвращённая в очередь, — её взял другой воркер или
  это другая попытка, резерв без задачи старше часа) и уборка закрытых резервов старше
  `RESERVATIONS_RETAIN_DAYS`.
* `cleanup` (ежедневно 04:00, вручную `/maintenance/cleanup`, аварийно при диске < 5 ГБ) —
  **каркас** политики хранения §8.2 (`services.tasks.plan_cleanup`): `MEDIA_ROOT/tmp` старше
  суток (аварийно — часа), логи задач старше 14 дней, брошенные tus-части, файлы артефактов с
  `deleted_at`, старые завершённые системные задачи. Сроки хранения роликов и источников
  (`*_retain_days`) добавляют владельцы этих областей (M2/M5).
"""

from __future__ import annotations

import logging
from typing import Any

from autoshorts.core.resources import disk_free_gb
from autoshorts.db.pool import connection
from autoshorts.repos import tasks as tasks_repo
from autoshorts.services import budget as budget_service
from autoshorts.services.tasks import (
    SYSTEM_TASKS_RETAIN_DAYS,
    execute_cleanup,
    plan_cleanup,
    reap_stale_tasks,
    summarize_cleanup,
)
from autoshorts.tasks.registry import TaskContext, register_task

log = logging.getLogger(__name__)


def reap_budget_reservations(*, retain_days: int | None = None) -> dict[str, Any]:
    """Резервы бюджета без хозяина → `worker_lost`; закрытые старше срока — удалить.

    Своя транзакция: сбой здесь не откатывает возврат задач. Ошибка не бросается, а попадает в
    отчёт и лог — reaper задач важнее и должен отработать каждую минуту.
    """
    try:
        with connection() as conn:
            result = budget_service.reap_orphan_reservations(conn)
            result["purged"] = budget_service.purge_closed_reservations(
                conn,
                older_than_days=int(
                    budget_service.RESERVATIONS_RETAIN_DAYS if retain_days is None else retain_days
                ),
            )
        return result
    except Exception as exc:  # noqa: BLE001 - см. docstring
        log.warning("task_reaper: budget reservations not reaped: %s", exc, exc_info=True)
        return {"error": f"{type(exc).__name__}: {exc}"[:300]}


@register_task("task_reaper", "light", max_attempts=5)
def task_reaper(ctx: TaskContext) -> dict[str, Any]:
    stale = float(ctx.payload.get("stale_after_sec", 180))
    with connection() as conn:
        report = reap_stale_tasks(conn, stale_after_sec=stale)
    # после возврата задач: их резервы уже видны как task_not_running и снимаются в этом же проходе
    result = report.as_dict()
    retain = ctx.payload.get("budget_reservations_retain_days")
    result["budget_reservations"] = reap_budget_reservations(
        retain_days=int(retain) if retain is not None else None
    )
    return result


@register_task("cleanup", "light", max_attempts=5)
def cleanup(ctx: TaskContext) -> dict[str, Any]:
    """Каркас уборки §8.2. `payload.emergency=true` — аварийный режим (диск < disk_emergency_gb):
    tmp старше часа вместо суток."""
    emergency = bool(ctx.payload.get("emergency"))
    with connection() as conn:
        plan = plan_cleanup(conn, ctx.media, emergency=emergency)
    ctx.progress(0.2)
    ctx.check_cancelled()
    removed = execute_cleanup(plan, cancel=ctx.cancel)
    ctx.progress(0.9)
    with connection() as conn:
        tasks_deleted = tasks_repo.delete_finished_before(conn, SYSTEM_TASKS_RETAIN_DAYS)
    free = None
    try:
        free = round(disk_free_gb(ctx.media.root), 2)
    except OSError:
        pass
    result = {
        "emergency": emergency,
        "planned": summarize_cleanup(plan),
        "removed": removed,
        "system_tasks_deleted": tasks_deleted,
        "disk_free_gb": free,
    }
    ctx.log.info("cleanup: %s", result)
    return result
