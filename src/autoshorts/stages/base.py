"""Контракт стадий ролика (docs/ARCHITECTURE.md §5.5, §6.1, §6.2).

Стадия — функция `run(ctx: StageContext) -> StageResult`, регистрируется в своём модуле
`stages/<name>.py`:

    from autoshorts.stages.base import StageContext, StageResult, NewArtifact, register_stage

    @register_stage("generate", "render", resource_class="heavy", max_attempts=2, paid=False)
    def render(ctx: StageContext) -> StageResult:
        ctx.check_cancelled()
        out = ctx.workdir / "final.mp4"
        run_media([...], timeout_sec=900, threads=ctx.settings.ffmpeg_threads,
                  progress_cb=ctx.progress, cancel=ctx.cancel, log_path=ctx.log_path)
        return StageResult(artifacts=[NewArtifact("final", out, input_hash=h)],
                           job_fields={"duration_sec": 21.4})

Задача очереди для стадии — `kind='job_stage'`, `stage=<стадия>`, dedup `job:<id>:<stage>:<rev>`
(`stage_dedup_key`), класс/попытки/приоритет по таблице §6.1 (`stage_task_defaults`).
Исполнитель `job_stage` (загрузка job, провайдеры, бюджет) регистрируется треком генерации
(`tasks/generate.py`, M2-B) через `tasks.registry.register_task("job_stage", ...)`; этот модуль
даёт типы, реестр и справочники. Фиксация `StageResult` одной транзакцией с `tasks.succeeded`
(§6.1: artifacts → `repos.artifacts.insert`, патч job, следующая стадия →
`services.tasks.enqueue_job_stage`, NOTIFY) — через `task_ctx.on_commit(lambda conn, row: ...)`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from autoshorts.core.errors import Cancelled
from autoshorts.domain.enums import JobKind, JobStage, JobStatus, ResourceClass

if TYPE_CHECKING:
    from autoshorts.domain.settings_schema import RuntimeSettings

log = logging.getLogger(__name__)

JOB_STAGE_TASK_KIND = "job_stage"


# ---------------------------------------------------------------- типы контракта


@dataclass(frozen=True)
class NewArtifact:
    """Артефакт стадии: `(kind, idx, path, input_hash)`. `path` — абсолютный внутри MEDIA_ROOT
    или уже относительный; в `artifacts.path` пишется относительный (`MediaLayout.relpath`)."""

    kind: str
    path: Path | str
    input_hash: str
    idx: int = 0
    is_master: bool = False


@dataclass
class StageResult:
    artifacts: list[NewArtifact] = field(default_factory=list)
    payload_patch: dict[str, Any] = field(default_factory=dict)  # мерж в jobs.stage_payload
    job_fields: dict[str, Any] = field(
        default_factory=dict
    )  # {"duration_sec": 21.4, "qc_failed_checks": [...]}
    next_stage: str | None = None  # None -> по таблице стадий (domain/stages.py, M2)
    job_status: str | None = None  # 'pending_review' | 'qc_failed'

    def __post_init__(self) -> None:
        if self.next_stage is not None:
            JobStage(self.next_stage)
        if self.job_status is not None and JobStatus(self.job_status) not in (
            JobStatus.PENDING_REVIEW,
            JobStatus.QC_FAILED,
        ):
            raise ValueError(
                f"stage may only set job_status pending_review|qc_failed, got {self.job_status!r}"
            )


class BudgetGate(Protocol):
    """Бюджет-предохранитель платных вызовов (реализация — `services/budget.py`, M1-B, §6.1).

    `reserve` бросает `ResourceDeferred('budget_exhausted')`, если лимит дня исчерпан.
    """

    def reserve(self, amount_usd: float, *, provider: str) -> Any: ...

    def settle(self, reservation: Any, actual_usd: float, **event: Any) -> None: ...


class ProviderSet(Protocol):
    """Набор провайдеров задачи (llm, tts, images, asr, faces) — `providers/registry.py`, M2-B."""


@dataclass
class StageContext:
    job: dict[str, Any]
    task: dict[str, Any]
    workdir: Path
    settings: RuntimeSettings
    providers: Any  # ProviderSet
    budget: Any  # BudgetGate
    cancel: threading.Event
    progress: Callable[[float], None]  # доля 0..1
    log: logging.Logger | logging.LoggerAdapter
    log_path: Path | None = None  # лог задачи (MEDIA_ROOT/logs/tasks/<id>.log) — для run_media

    @property
    def job_id(self) -> int:
        return int(self.job["id"])

    @property
    def video_uid(self) -> str:
        return str(self.job["video_uid"])

    @property
    def stage(self) -> str:
        return str(self.task["stage"])

    def check_cancelled(self) -> None:
        if self.cancel.is_set():
            raise Cancelled("cancel requested")


StageHandler = Callable[[StageContext], StageResult]


@dataclass(frozen=True)
class StageSpec:
    job_kind: str
    stage: str
    resource_class: str
    max_attempts: int
    paid: bool
    priority: int
    handler: StageHandler


# ---------------------------------------------------------------- справочник §6.1


@dataclass(frozen=True)
class StageTaskDefaults:
    resource_class: str
    max_attempts: int
    paid: bool
    priority: int


# §6.1 «Виды задач, классы и приоритеты» для kind=job_stage. voice/author_voice: light для edge/fal;
# heavy (silero, ASR записи) стадия объявляет сама через register_stage.
STAGE_DEFAULTS: dict[str, StageTaskDefaults] = {
    JobStage.QC: StageTaskDefaults("heavy", 2, False, 20),
    JobStage.RENDER: StageTaskDefaults("heavy", 2, False, 30),
    JobStage.CLIP_ANALYZE: StageTaskDefaults("heavy", 2, False, 40),
    JobStage.SCRIPT: StageTaskDefaults("light", 3, True, 50),
    JobStage.AUTHOR_SCRIPT: StageTaskDefaults("light", 3, True, 50),
    JobStage.VOICE: StageTaskDefaults("light", 3, False, 50),
    JobStage.AUTHOR_VOICE: StageTaskDefaults("light", 3, False, 50),
    JobStage.VISUALS: StageTaskDefaults("light", 2, True, 50),
    JobStage.CLIP_FETCH: StageTaskDefaults("light", 3, False, 60),
}


# ---------------------------------------------------------------- реестр


_stages: dict[tuple[str, str], StageSpec] = {}
_lock = threading.Lock()


def register_stage(
    kind: str,
    stage: str,
    resource_class: str,
    max_attempts: int,
    paid: bool,
    *,
    priority: int | None = None,
) -> Callable[[StageHandler], StageHandler]:
    """Зарегистрировать стадию `stage` трека `kind` (generate|clip)."""
    job_kind = JobKind(kind).value
    stage_name = JobStage(stage).value
    cls = ResourceClass(resource_class).value
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    base = STAGE_DEFAULTS.get(stage_name)
    prio = int(priority if priority is not None else (base.priority if base else 50))

    def decorator(func: StageHandler) -> StageHandler:
        spec = StageSpec(job_kind, stage_name, cls, int(max_attempts), bool(paid), prio, func)
        with _lock:
            existing = _stages.get((job_kind, stage_name))
            if (
                existing is not None
                and existing.handler is not func
                and (
                    existing.handler.__module__ != func.__module__
                    or existing.handler.__qualname__ != func.__qualname__
                )
            ):
                raise ValueError(f"stage {job_kind}:{stage_name} already registered")
            _stages[(job_kind, stage_name)] = spec
        return func

    return decorator


def get_stage(kind: str, stage: str) -> StageSpec | None:
    return _stages.get((str(kind), str(stage)))


def registered_stages() -> dict[tuple[str, str], StageSpec]:
    with _lock:
        return dict(_stages)


def unregister_stage(kind: str, stage: str) -> None:
    """Для тестов."""
    with _lock:
        _stages.pop((kind, stage), None)


def stage_task_defaults(stage: str, job_kind: str = JobKind.GENERATE) -> StageTaskDefaults:
    """Класс, попытки, платность и приоритет задачи стадии: из реестра, иначе из таблицы §6.1."""
    spec = get_stage(job_kind, stage)
    if spec is not None:
        return StageTaskDefaults(spec.resource_class, spec.max_attempts, spec.paid, spec.priority)
    base = STAGE_DEFAULTS.get(JobStage(stage).value)
    if base is None:
        raise KeyError(f"no task defaults for stage {stage!r}")
    return base


def stage_dedup_key(job_id: int, stage: str, rev: int = 0) -> str:
    """`job:<id>:<stage>:<rev>` (§6.1): одна активная задача стадии на ревизию."""
    return f"job:{int(job_id)}:{JobStage(stage).value}:{int(rev)}"


def input_hash(*parts: Any) -> str:
    """Стабильный sha256 входов стадии (`artifacts.input_hash`): hash(video_uid, stage, входы,
    template_version). Части сериализуются канонично (JSON с сортировкой ключей)."""
    h = hashlib.sha256()
    for part in parts:
        if isinstance(part, bytes):
            data = part
        elif isinstance(part, Path):
            data = part.as_posix().encode()
        else:
            data = json.dumps(
                part, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
            ).encode()
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    return h.hexdigest()
