"""Heartbeat и прогресс исполняемой задачи (docs/ARCHITECTURE.md §6.1).

`TaskHandle` — состояние одного исполнения в памяти воркера: событие отмены (его видят
обработчик и `run_media`), причина отмены, последний прогресс.

`TaskHeartbeat` — поток на время исполнения:
* раз в `interval_sec` (20 с) продлевает аренду (`tasks.heartbeat_at`) и читает
  `cancel_requested` — если оператор отменил задачу, выставляет событие отмены, `run_media`
  убивает группу процессов;
* раз в `progress_interval_sec` (2 с) пишет изменившийся прогресс и шлёт `task_progress`
  (+ `job_progress`);
* если строка больше не наша (reaper забрал задачу), отмечает `lease_lost` и отменяет исполнение.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from autoshorts.db.pool import connection
from autoshorts.repos import tasks as tasks_repo
from autoshorts.services.tasks import publish_progress
from autoshorts.workers.policies import CancelReason

log = logging.getLogger(__name__)

DEFAULT_HEARTBEAT_SEC = 20.0
DEFAULT_PROGRESS_SEC = 2.0
MIN_PROGRESS_DELTA_PCT = 0.5


class TaskHandle:
    def __init__(self, task: dict[str, Any]) -> None:
        self.task = task
        self.cancel = threading.Event()
        self.cancel_reason: CancelReason | None = None
        self._lock = threading.Lock()
        self._pct: float | None = None
        self.started_monotonic = time.monotonic()
        self.thread: threading.Thread | None = None
        self.finished = threading.Event()
        self.commit_hooks: list[Any] = []  # ctx.on_commit: выполняются в транзакции succeeded
        self.budget: Any = None  # TaskBudgetGate исполнения (ctx.budget)

    @property
    def task_id(self) -> int:
        return int(self.task["id"])

    def request_cancel(self, reason: CancelReason) -> None:
        with self._lock:
            # «пользователь» не перебивает «остановку воркера» и «потерю аренды»
            if self.cancel_reason is None or (self.cancel_reason == "user" and reason != "user"):
                self.cancel_reason = reason
        self.cancel.set()

    def set_progress(self, fraction: float) -> None:
        """Колбэк `ctx.progress`: доля 0..1 (в БД — проценты)."""
        try:
            pct = max(0.0, min(100.0, float(fraction) * 100.0))
        except (TypeError, ValueError):
            return
        with self._lock:
            self._pct = pct

    @property
    def progress_pct(self) -> float | None:
        with self._lock:
            return self._pct


class TaskHeartbeat(threading.Thread):
    def __init__(
        self,
        handle: TaskHandle,
        worker_id: str,
        *,
        interval_sec: float = DEFAULT_HEARTBEAT_SEC,
        progress_interval_sec: float = DEFAULT_PROGRESS_SEC,
    ) -> None:
        super().__init__(name=f"heartbeat-{handle.task_id}", daemon=True)
        self.handle = handle
        self.worker_id = worker_id
        self.interval_sec = max(float(interval_sec), 0.05)
        self.progress_interval_sec = max(min(float(progress_interval_sec), self.interval_sec), 0.05)
        self._stop_event = threading.Event()
        self._last_beat = time.monotonic()
        self._last_pct: float | None = None

    def stop(self) -> None:
        self._stop_event.set()

    def beat(self, *, force: bool = False) -> None:
        """Одна итерация (вызывается из run; открыта для тестов)."""
        now = time.monotonic()
        pct = self.handle.progress_pct
        pct_changed = pct is not None and (
            self._last_pct is None or abs(pct - self._last_pct) >= MIN_PROGRESS_DELTA_PCT
        )
        due = force or now - self._last_beat >= self.interval_sec
        if not (due or pct_changed):
            return
        with connection() as conn:
            row = tasks_repo.heartbeat(
                conn, self.handle.task_id, self.worker_id, pct if pct_changed else None
            )
            if row is None:
                log.warning("task %s: lease lost (reaped or finished elsewhere)", self.handle.task_id)
                self.handle.request_cancel("lease_lost")
                self._stop_event.set()
                return
            if pct_changed and pct is not None:
                publish_progress(conn, {**self.handle.task, **row, "id": self.handle.task_id}, pct)
        self._last_beat = now
        if pct_changed:
            self._last_pct = pct
        if row["cancel_requested"] and not self.handle.cancel.is_set():
            log.info("task %s: cancel requested by operator", self.handle.task_id)
            self.handle.request_cancel("user")

    def run(self) -> None:
        while not self._stop_event.wait(self.progress_interval_sec):
            try:
                self.beat()
            except Exception as exc:  # noqa: BLE001 - БД моргнула: аренда продлится на следующей итерации
                log.warning("task %s: heartbeat failed: %s", self.handle.task_id, exc)
