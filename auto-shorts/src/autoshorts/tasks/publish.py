"""Задачи автозалива (0019, решение владельца 2026-09-27). Логика — `services/autopublish.py`.

* `autopublish_tick` (light, расписание раз в минуту): новые пакеты auto-каналов → слоты, созревшие
  слоты → `autopublish_upload`, зависшие заливки → повтор с места обрыва.
* `autopublish_upload` (light, 6 попыток): `payload.package_id` — залить и зарегистрировать публикацию.
  Сбой сети повторяется с backoff и продолжает resumable-сессию; квота и лимит канала переносят слот,
  а не тратят попытки.
"""

from __future__ import annotations

from typing import Any

from autoshorts.core.errors import PermanentError
from autoshorts.tasks.registry import TaskContext, register_task


@register_task("autopublish_tick", "light", max_attempts=3)
def autopublish_tick(ctx: TaskContext) -> dict[str, Any]:
    from autoshorts.services import autopublish

    result = autopublish.tick()
    if any(result.values()):
        ctx.log.info("autopublish_tick: %s", result)
    return result


@register_task("autopublish_upload", "light", max_attempts=6)
def autopublish_upload(ctx: TaskContext) -> dict[str, Any]:
    from autoshorts.services import autopublish

    try:
        package_id = int(ctx.payload["package_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PermanentError("autopublish_upload: bad package_id", error_class="bad_payload") from exc
    attempts = int(ctx.task.get("attempts") or 1)
    max_attempts = int(ctx.task.get("max_attempts") or 1)
    result = autopublish.run_upload(
        package_id,
        settings=ctx.settings,
        last_attempt=attempts >= max_attempts,
        progress=ctx.progress,
        cancelled=ctx.cancel.is_set,
    )
    ctx.log.info("autopublish_upload %s: %s", package_id, result)
    return result
