"""Тип задачи для `test_budget_reservations.py::test_killed_worker_process_reservation_released`.

Импортируется подпроцессом воркера (`--import tests.worker.test_budget_reservations_hold`): резерв
через гейт исполнения и долгий «платный вызов», посреди которого воркер убивают SIGKILL. Тестов в
модуле нет.
"""

from __future__ import annotations

import time
from typing import Any

from autoshorts.tasks.registry import TaskContext, register_task

HOLD_KIND = "wtest_budget_hold"


@register_task(HOLD_KIND, "light", max_attempts=3)
def wtest_budget_hold(ctx: TaskContext) -> dict[str, Any]:
    amount = float(ctx.payload.get("amount", 0.5))
    ctx.budget.reserve(amount, provider="openai_compat")
    deadline = time.monotonic() + float(ctx.payload.get("hold_sec", 30))
    while time.monotonic() < deadline and not ctx.cancel.is_set():
        time.sleep(0.05)
    return {"held": amount}
