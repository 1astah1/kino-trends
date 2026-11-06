"""Реестр обработчиков задач очереди (docs/ARCHITECTURE.md §5.5, §6.1, §10.1 п. 2).

Обработчик — функция `handle(ctx: TaskContext) -> dict | None`, регистрируется декоратором в
своём модуле `tasks/<area>.py`; `load_all()` импортирует все модули пакета `autoshorts.tasks` и
все стадии (`autoshorts.stages.discover_stages()`), общих файлов при добавлении области трогать
не нужно:

    from autoshorts.tasks.registry import register_task, TaskContext, RetryableError

    @register_task("metrics_poll", "light", max_attempts=5)
    def metrics_poll(ctx: TaskContext) -> dict | None:
        ctx.check_cancelled()
        ctx.progress(0.5)              # доля 0..1 (в БД и событиях — проценты)
        return {"polled": 12}          # -> tasks.result

Исключения-сигналы (реэкспорт из `core.errors`): `RetryableError` (backoff, попытка
засчитывается), `PermanentError` (сразу failed), `ResourceDeferred(reason)` (в очередь, попытка
не тратится), `Cancelled`.

Результат с файлом для `GET /tasks/{id}/result-file`: `{"file": "<путь от MEDIA_ROOT>",
"media_type": "audio/mpeg", "filename": "sample.mp3"}`.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoshorts.core.errors import Cancelled, PermanentError, ResourceDeferred, RetryableError, TaskError
from autoshorts.domain.enums import ResourceClass

if TYPE_CHECKING:
    from autoshorts.core.storage import MediaLayout
    from autoshorts.domain.settings_schema import RuntimeSettings

__all__ = [
    "Cancelled",
    "DEFAULT_PRIORITIES",
    "PermanentError",
    "ResourceDeferred",
    "RetryableError",
    "CommitHook",
    "TaskContext",
    "TaskError",
    "TaskHandler",
    "TaskSpec",
    "default_priority",
    "get_task_spec",
    "kind_defaults",
    "load_all",
    "register_task",
    "registered_kinds",
    "unregister_task",
]

log = logging.getLogger(__name__)

# §6.1 «Виды задач, классы и приоритеты» (меньше = раньше). job_stage — по стадии, см. stages/base.py.
DEFAULT_PRIORITIES: dict[str, int] = {
    "package_build": 10,
    "job_stage": 50,
    "banner_creative_normalize": 60,
    "metrics_poll": 80,
    "autopublish_tick": 70,
    "autopublish_upload": 75,
    "competitor_discovery": 80,
    "competitor_stats_poll": 80,
    "yt_analytics_pull": 80,
    "channel_stats": 80,
    "source_download": 110,
    "source_prepare": 120,
    "source_transcribe_chunk": 150,
    "source_transcribe_cloud": 150,
    "source_select": 160,
    "source_scenes": 170,
    "competitor_analyze": 180,
    "stats_rollup": 200,
    "cleanup": 200,
    "alerts_eval": 200,
    "task_reaper": 200,
    "brief_field_stats": 200,
    "baselines_refresh": 200,
    "outlier_compute": 200,
    "rss_health_check": 200,
}
FALLBACK_PRIORITY = 100

# §6.1: класс и max_attempts известных видов — чтобы ставить задачи из процессов, где обработчики
# не загружены (API, бот). Зарегистрированный обработчик главнее этой таблицы.
KIND_DEFAULTS: dict[str, tuple[str, int]] = {
    "package_build": ("light", 3),
    "voice_sample": ("light", 2),
    "banner_creative_normalize": ("heavy", 2),
    "metrics_poll": ("light", 5),
    "autopublish_tick": ("light", 3),
    "autopublish_upload": ("light", 6),
    "competitor_discovery": ("light", 5),
    "competitor_stats_poll": ("light", 5),
    "yt_analytics_pull": ("light", 5),
    "channel_stats": ("light", 5),
    "source_download": ("light", 3),
    "source_prepare": ("heavy", 2),
    "source_transcribe_chunk": ("heavy", 2),
    "source_transcribe_cloud": ("light", 3),
    "source_select": ("light", 3),
    "source_scenes": ("heavy", 2),
    "competitor_analyze": ("heavy", 2),
    "stats_rollup": ("light", 5),
    "cleanup": ("light", 5),
    "alerts_eval": ("light", 5),
    "task_reaper": ("light", 5),
    "brief_field_stats": ("light", 5),
    "baselines_refresh": ("light", 5),
    "outlier_compute": ("light", 5),
    "rss_health_check": ("light", 5),
}


def default_priority(kind: str) -> int:
    return DEFAULT_PRIORITIES.get(kind, FALLBACK_PRIORITY)


def kind_defaults(kind: str) -> tuple[str, int, int] | None:
    """(resource_class, max_attempts, priority): из реестра, иначе из таблицы §6.1, иначе None."""
    spec = _registry.get(kind)
    if spec is not None:
        return spec.resource_class, spec.max_attempts, spec.priority
    base = KIND_DEFAULTS.get(kind)
    if base is None:
        return None
    return base[0], base[1], default_priority(kind)


CommitHook = Callable[[Any, dict[str, Any]], None]
"""`hook(conn, task_row)`: conn — транзакция фиксации успеха, task_row — строка tasks после UPDATE."""


@dataclass
class TaskContext:
    """Контекст одного исполнения задачи (строится воркером, `workers/runner.py`)."""

    task: dict[str, Any]
    worker_id: str
    cancel: threading.Event
    progress: Callable[[float], None]
    log: logging.Logger | logging.LoggerAdapter
    settings: RuntimeSettings
    media: MediaLayout
    log_path: Path
    extras: dict[str, Any] = field(default_factory=dict)
    commit_hooks: list[CommitHook] = field(default_factory=list)
    # бюджет-гейт исполнения (`services.budget.TaskBudgetGate`, для `StageContext.budget`): воркер
    # создаёт его на каждое исполнение и после обработчика снимает незакрытые резервы
    budget: Any = None

    @property
    def task_id(self) -> int:
        return int(self.task["id"])

    @property
    def kind(self) -> str:
        return str(self.task["kind"])

    @property
    def payload(self) -> dict[str, Any]:
        return dict(self.task.get("payload") or {})

    @property
    def attempt(self) -> int:
        return int(self.task.get("attempts") or 0)

    @property
    def job_id(self) -> int | None:
        return self.task.get("job_id")

    @property
    def source_id(self) -> int | None:
        return self.task.get("source_id")

    @property
    def stage(self) -> str | None:
        return self.task.get("stage")

    def check_cancelled(self) -> None:
        """Точка отмены для долгих обработчиков: бросает `Cancelled`, если отмена запрошена."""
        if self.cancel.is_set():
            raise Cancelled("cancel requested")

    def on_commit(self, hook: CommitHook) -> None:
        """Выполнить `hook(conn, task_row)` в ТОЙ ЖЕ транзакции, что и `tasks.status='succeeded'`
        (§6.1: artifacts, патч job, следующая задача и NOTIFY фиксируются вместе с успехом).

        Хуки выполняются, только если обработчик вернулся без исключения и аренда ещё наша.
        Исключение в хуке откатывает всё (в том числе succeeded), и задача получает исход по
        этому исключению (RetryableError/PermanentError/…), как если бы его бросил обработчик.
        """
        self.commit_hooks.append(hook)


TaskHandler = Callable[[TaskContext], "dict[str, Any] | None"]


@dataclass(frozen=True)
class TaskSpec:
    kind: str
    resource_class: str
    max_attempts: int
    paid: bool
    priority: int
    handler: TaskHandler
    module: str = ""


_registry: dict[str, TaskSpec] = {}
_lock = threading.Lock()


def register_task(
    kind: str,
    resource_class: str,
    max_attempts: int,
    paid: bool = False,
    *,
    priority: int | None = None,
) -> Callable[[TaskHandler], TaskHandler]:
    """Декоратор регистрации обработчика. `resource_class` — класс по умолчанию при постановке
    (`services.tasks.enqueue`); воркер берёт задачу по колонке `tasks.resource_class`."""
    cls = ResourceClass(resource_class).value
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    if not kind or not kind.replace("_", "").isalnum() or not kind.isascii() or kind != kind.lower():
        raise ValueError(f"task kind must be latin snake_case: {kind!r}")

    def decorator(func: TaskHandler) -> TaskHandler:
        spec = TaskSpec(
            kind=kind,
            resource_class=cls,
            max_attempts=int(max_attempts),
            paid=bool(paid),
            priority=int(priority if priority is not None else default_priority(kind)),
            handler=func,
            module=getattr(func, "__module__", ""),
        )
        with _lock:
            existing = _registry.get(kind)
            if (
                existing is not None
                and existing.handler is not func
                and (existing.module != spec.module or existing.handler.__qualname__ != func.__qualname__)
            ):
                raise ValueError(f"task kind {kind!r} already registered by {existing.module}")
            _registry[kind] = spec
        return func

    return decorator


def unregister_task(kind: str) -> None:
    """Для тестов."""
    with _lock:
        _registry.pop(kind, None)


def get_task_spec(kind: str) -> TaskSpec | None:
    return _registry.get(kind)


def registered_kinds() -> list[str]:
    """Виды задач с обработчиком. Воркер берёт из очереди только их (`kind = ANY(...)`).

    Класс исполнения задаёт колонка `tasks.resource_class`, а не spec: `job_stage` бывает и
    heavy (render, qc), и light (script, voice, visuals).
    """
    with _lock:
        return sorted(_registry)


def all_specs() -> dict[str, TaskSpec]:
    with _lock:
        return dict(_registry)


_loaded = False


def load_all(extra_modules: Iterable[str] = (), *, strict: bool = True) -> list[str]:
    """Импортировать все `autoshorts.tasks.*`, все стадии и дополнительные модули.

    `strict=True` (воркер): сломанный модуль роняет старт — лучше, чем молча не брать его задачи.
    `strict=False` (API): сломанный модуль пропускается с предупреждением.
    Возвращает список импортированных модулей.
    """
    global _loaded
    import autoshorts.tasks as pkg

    imported: list[str] = []
    names = [
        f"{pkg.__name__}.{m.name}"
        for m in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name)
        if not m.name.startswith("_") and m.name != "registry"
    ]
    for name in [*names, *extra_modules]:
        try:
            importlib.import_module(name)
            imported.append(name)
        except Exception:
            if strict:
                raise
            log.warning("task module %s failed to import", name, exc_info=True)
    try:
        from autoshorts.stages import discover_stages

        imported.extend(discover_stages(strict=strict))
    except Exception:
        if strict:
            raise
        log.warning("stage discovery failed", exc_info=True)
    _loaded = True
    return imported
