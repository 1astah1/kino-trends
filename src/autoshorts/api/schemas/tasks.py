"""Задачи очереди, воркеры, расписания (docs/ARCHITECTURE.md §5.2 «Ролики, приёмка, задачи», §6.1)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from autoshorts.api.schemas.common import ApiModel, PageMeta, RequestModel
from autoshorts.domain.enums import DeferReason, JobStage, ResourceClass, TaskStatus


class Task(ApiModel):
    id: int
    kind: str = Field(description="Вид задачи: job_stage, source_prepare, metrics_poll, … (см. TaskKind)")
    resource_class: ResourceClass
    status: TaskStatus
    priority: int = Field(description="Меньше — раньше")
    run_after: datetime
    job_id: int | None
    source_id: int | None
    stage: JobStage | None
    payload: dict[str, Any]
    dedup_key: str | None
    attempts: int
    max_attempts: int
    locked_by: str | None
    heartbeat_at: datetime | None
    cancel_requested: bool
    progress: float | None = Field(description="0–100")
    started_at: datetime | None
    finished_at: datetime | None
    error_class: str | None
    last_error: str | None = Field(description="Хвост лога ≤ 4 КБ")
    defer_reason: DeferReason | None
    result: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class TaskPage(PageMeta):
    items: list[Task]


class TaskDetail(ApiModel):
    task: Task
    log_tail: list[str] = Field(description="Последние 200 строк лога задачи")


class Worker(ApiModel):
    id: str = Field(description="<host>-<pid>-<class>")
    resource_class: ResourceClass
    hostname: str
    pid: int
    version: str | None
    started_at: datetime
    heartbeat_at: datetime
    current_task_id: int | None
    state: str = Field(description="idle | busy | deferred:<reason> | stopping")


class WorkerList(ApiModel):
    items: list[Worker]


class Schedule(ApiModel):
    name: str
    task_kind: str
    resource_class: ResourceClass
    payload: dict[str, Any]
    interval_sec: int
    anchor_local_time: str | None = Field(description="HH:MM в business_timezone для суточных расписаний")
    next_run_at: datetime
    enabled: bool
    last_task_id: int | None


class ScheduleList(ApiModel):
    items: list[Schedule]


class SchedulePatch(RequestModel):
    enabled: bool | None = None
    interval_sec: int | None = Field(default=None, ge=30)
