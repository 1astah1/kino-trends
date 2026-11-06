"""Исполнитель стадий роликов: задачи `kind='job_stage'` (§5.5, §6.1; M2-C).

Сам исполнитель — `services.jobs.run_stage_task`: queued → processing, обработчик стадии из
реестра `stages` (`@register_stage(kind, stage, …)`), фиксация `StageResult` в транзакции успеха.
Класс ресурса и попытки конкретной задачи берутся из колонок `tasks` (их ставит
`services.tasks.enqueue_job_stage` по таблице стадий), значения здесь — только по умолчанию.
Незарегистрированная стадия → `PermanentError(error_class='stage_not_registered')`.
"""

from autoshorts.services.jobs import run_stage_task
from autoshorts.tasks.registry import register_task

register_task("job_stage", "light", max_attempts=3)(run_stage_task)
