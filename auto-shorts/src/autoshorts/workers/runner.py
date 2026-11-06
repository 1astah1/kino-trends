"""Цикл воркера очереди (docs/ARCHITECTURE.md §6.1, §8.1).

Один процесс = один класс ресурсов:

* `heavy` — одна задача за раз и только при захваченном тяжёлом слоте (`HeavySlot`: flock +
  advisory lock 771001). Второй тяжёлый воркер стоит в `standby` и задачи не берёт, пока жив
  первый. Перед взятием — допуск (`Admission`): нет ресурсов — состояние `deferred:<reason>`,
  ждущие задачи помечаются `defer_reason`, попытки не тратятся.
* `light` — до `concurrency` задач в потоках; внутри работает планировщик (`Scheduler`).

Цикл: `LISTEN as_tasks` (пробуждение по вставке задачи и `cancel:<id>` для мгновенной отмены) +
опрос очереди раз в `poll_interval_sec` (5 с). Воркер берёт только виды задач, для которых
есть обработчик в реестре (`kind = ANY(...)`).

Остановка (SIGTERM/SIGINT): не брать новые, отменить исполняемые с причиной `shutdown`, убить
группы подпроцессов `run_media`, дождаться потоков `shutdown_grace_sec`, оставшиеся задачи
вернуть в очередь без траты попытки, удалить запись воркера.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from autoshorts.core.ids import new_worker_id
from autoshorts.core.logging import RedactingFilter, log_context, redact_text
from autoshorts.core.resources import DiskLevel
from autoshorts.core.storage import MediaLayout, get_layout
from autoshorts.db.notify import Listener, Notification, Reconnected
from autoshorts.db.pool import connection
from autoshorts.domain.enums import ResourceClass
from autoshorts.domain.events import TASKS_CHANNEL
from autoshorts.domain.settings_schema import RuntimeSettings, validate_setting_value
from autoshorts.pipeline import ffmpeg_exec
from autoshorts.repos import tasks as tasks_repo
from autoshorts.repos import workers as workers_repo
from autoshorts.services import tasks as task_service
from autoshorts.services.budget import TaskBudgetGate
from autoshorts.tasks.registry import TaskContext, get_task_spec, registered_kinds
from autoshorts.workers.guards import Admission, HeavySlot
from autoshorts.workers.heartbeat import (
    DEFAULT_HEARTBEAT_SEC,
    DEFAULT_PROGRESS_SEC,
    TaskHandle,
    TaskHeartbeat,
)
from autoshorts.workers.policies import Outcome, classify

log = logging.getLogger(__name__)

STATE_IDLE = "idle"
STATE_BUSY = "busy"
STATE_STANDBY = "standby"
STATE_STOPPING = "stopping"


def _app_version() -> str:
    try:
        return version("autoshorts")
    except PackageNotFoundError:
        return "0.0.0"


# ---------------------------------------------------------------- настройки из БД


class SettingsCache:
    """RuntimeSettings = дефолты + переопределения из таблицы `settings` (кеш `ttl_sec`).

    Сломанное или неизвестное переопределение пропускается с предупреждением: воркер не должен
    падать из-за одной кривой настройки.
    """

    def __init__(self, ttl_sec: float = 60.0, loader: Callable[[], RuntimeSettings] | None = None) -> None:
        self.ttl_sec = ttl_sec
        self._loader = loader or self._load_from_db
        self._value: RuntimeSettings | None = None
        self._at = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def _load_from_db() -> RuntimeSettings:
        try:  # единая реализация из services/settings.py (M1-B), если она есть
            from autoshorts.services.settings import load_runtime_settings
        except ImportError:
            load_runtime_settings = None
        if load_runtime_settings is not None:
            with connection() as conn:
                return load_runtime_settings(conn)
        overrides: dict[str, Any] = {}
        with connection() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        for row in rows:
            try:
                overrides[row["key"]] = validate_setting_value(row["key"], row["value"])
            except ValueError as exc:
                log.warning("setting %s ignored: %s", row["key"], exc)
        return RuntimeSettings.from_overrides(overrides)

    def get(self) -> RuntimeSettings:
        with self._lock:
            now = time.monotonic()
            if self._value is None or now - self._at >= self.ttl_sec:
                try:
                    self._value = self._loader()
                except Exception as exc:  # noqa: BLE001 - БД недоступна: последнее известное или дефолты
                    log.warning("settings load failed: %s", exc)
                    if self._value is None:
                        self._value = RuntimeSettings()
                self._at = now
            return self._value


# ---------------------------------------------------------------- конфигурация


@dataclass
class RunnerConfig:
    resource_class: str
    concurrency: int = 1
    conninfo: str | None = None
    worker_id: str | None = None
    poll_interval_sec: float = 5.0
    heartbeat_interval_sec: float = DEFAULT_HEARTBEAT_SEC
    progress_interval_sec: float = DEFAULT_PROGRESS_SEC
    worker_heartbeat_sec: float = 20.0
    shutdown_grace_sec: float = 20.0
    kill_grace_sec: float = 5.0
    heavy_lock_path: Path | None = None
    standby_retry_sec: float = 5.0
    admission: bool = True
    scheduler: bool | None = None  # None -> только у light
    scheduler_tick_sec: float = 10.0
    max_tasks: int | None = None  # выйти после N взятых задач (тесты, разовые прогоны)
    exit_when_idle: bool = False  # выйти, когда очередь класса пуста (тесты)
    media_root: Path | None = None
    settings_ttl_sec: float = 60.0
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.resource_class = ResourceClass(self.resource_class).value
        if self.resource_class == ResourceClass.HEAVY:
            self.concurrency = 1  # §8.1: ровно один тяжёлый процесс и одна задача в нём
        self.concurrency = max(int(self.concurrency), 1)
        if self.scheduler is None:
            self.scheduler = self.resource_class == ResourceClass.LIGHT


# ---------------------------------------------------------------- лог задачи


class _TaskLogFormatter(logging.Formatter):
    """Формат файла лога задачи; трейсбек тоже проходит маскировку секретов (`RedactingFilter`
    видит только msg/args). Замаскированный `exc_text` кешируется в записи, поэтому и лог воркера,
    куда запись всплывает дальше, получает уже чистый трейсбек."""

    def formatException(self, ei: Any) -> str:  # noqa: N802 - имя из logging.Formatter
        return redact_text(super().formatException(ei))


class _TaskLog:
    """Логгер задачи: файл `MEDIA_ROOT/logs/tasks/<id>.log` + всплытие в лог воркера."""

    def __init__(self, task: dict[str, Any], path: Path) -> None:
        self.path = path
        # Logger вне реестра logging: не копится по одному на задачу в logging.Logger.manager
        self.logger = logging.Logger(f"autoshorts.task.{task['id']}", logging.INFO)
        self.logger.parent = logging.getLogger("autoshorts.task")
        self.handler: logging.Handler | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(path, encoding="utf-8")
        except OSError as exc:  # MEDIA_ROOT недоступен на запись: задача всё равно исполнится
            log.warning("task %s: cannot open log %s: %s", task["id"], path, exc)
            return
        handler.setFormatter(_TaskLogFormatter("%(asctime)s %(levelname)s %(message)s"))
        handler.addFilter(RedactingFilter())
        self.logger.addHandler(handler)
        self.handler = handler

    def close(self) -> None:
        if self.handler is not None:
            self.logger.removeHandler(self.handler)
            self.handler.close()


def _json_result(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        value = {"value": value}
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


# ---------------------------------------------------------------- воркер


class Runner:
    def __init__(
        self,
        config: RunnerConfig,
        *,
        settings_provider: Callable[[], RuntimeSettings] | None = None,
        media: MediaLayout | None = None,
    ) -> None:
        self.config = config
        self.cls = config.resource_class
        self.worker_id = config.worker_id or new_worker_id(self.cls)
        self.hostname = socket.gethostname()
        self.pid = os.getpid()
        self.version = _app_version()
        self._settings_cache = SettingsCache(config.settings_ttl_sec) if settings_provider is None else None
        self.settings_provider = settings_provider or self._settings_cache.get  # type: ignore[union-attr]
        self.media = media or (MediaLayout(config.media_root) if config.media_root else get_layout())
        self._conninfo = config.conninfo or self._default_conninfo()

        self._stop = threading.Event()
        self._wakeup = threading.Event()
        self._active: dict[int, TaskHandle] = {}
        self._active_lock = threading.Lock()
        self._state: str | None = None
        self._state_task: int | None = None
        self._last_worker_beat = 0.0
        self._claimed = 0
        self._defer_until = 0.0
        self._deferred_reason: str | None = None
        self.exit_code = 0

        self.slot: HeavySlot | None = None
        self.admission: Admission | None = None
        if self.cls == ResourceClass.HEAVY:
            self.slot = HeavySlot(self._conninfo, lock_path=config.heavy_lock_path)
            if config.admission:
                self.admission = Admission(self.media.root, self.settings_provider)
        self.scheduler = None
        if config.scheduler:
            from autoshorts.workers.scheduler import Scheduler

            self.scheduler = Scheduler(
                self._conninfo,
                known_kinds=registered_kinds,
                timezone_provider=lambda: self.settings_provider().business_timezone,
                tick_sec=config.scheduler_tick_sec,
            )

    @staticmethod
    def _default_conninfo() -> str:
        from autoshorts.core.config import get_env

        return get_env().database_url

    # ------------------------------------------------------------ управление

    def request_stop(self) -> None:
        self._stop.set()
        self._wakeup.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def active_task_ids(self) -> list[int]:
        with self._active_lock:
            return list(self._active)

    def install_signal_handlers(self) -> None:
        def _handler(signum: int, _frame: Any) -> None:
            log.info("worker %s: signal %s, stopping", self.worker_id, signal.Signals(signum).name)
            self.request_stop()

        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)

    # ------------------------------------------------------------ состояние воркера

    def _set_state(self, state: str, *, force: bool = False) -> None:
        current_task = next(iter(self.active_task_ids()), None)
        now = time.monotonic()
        changed = state != self._state or current_task != self._state_task
        if not (changed or force or now - self._last_worker_beat >= self.config.worker_heartbeat_sec):
            return
        try:
            with connection() as conn:
                row = workers_repo.heartbeat(conn, self.worker_id, state=state, current_task_id=current_task)
                if row is None:  # запись удалили (reaper чистит мёртвых) — зарегистрироваться заново
                    workers_repo.upsert(
                        conn,
                        worker_id=self.worker_id,
                        resource_class=self.cls,
                        hostname=self.hostname,
                        pid=self.pid,
                        version=self.version,
                        state=state,
                    )
                    row = workers_repo.heartbeat(
                        conn, self.worker_id, state=state, current_task_id=current_task
                    )
                if row is not None and (
                    row.get("prev_state") != state or row.get("prev_task_id") != current_task
                ):
                    task_service.publish_worker(conn, row)
            self._state = state
            self._state_task = current_task
            self._last_worker_beat = now
        except Exception as exc:  # noqa: BLE001
            log.warning("worker heartbeat failed: %s", exc)

    def _register(self) -> None:
        with connection() as conn:
            row = workers_repo.upsert(
                conn,
                worker_id=self.worker_id,
                resource_class=self.cls,
                hostname=self.hostname,
                pid=self.pid,
                version=self.version,
                state=STATE_IDLE,
            )
            task_service.publish_worker(conn, row)
        self._state = STATE_IDLE
        self._last_worker_beat = time.monotonic()

    def _unregister(self) -> None:
        try:
            with connection() as conn:
                row = workers_repo.heartbeat(conn, self.worker_id, state=STATE_STOPPING, current_task_id=None)
                if row is not None:
                    task_service.publish_worker(conn, row)
                workers_repo.delete(conn, self.worker_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("worker unregister failed: %s", exc)

    # ------------------------------------------------------------ основной цикл

    def run(self, *, install_signals: bool = False) -> int:
        if install_signals:
            self.install_signal_handlers()
        kinds = registered_kinds()
        log.info(
            "worker %s (%s, concurrency %s) starting; kinds: %s",
            self.worker_id,
            self.cls,
            self.config.concurrency,
            ", ".join(kinds) or "—",
        )
        self._register()
        listener = Listener([TASKS_CHANNEL], self._conninfo, application_name=f"autoshorts-worker-{self.cls}")
        try:
            listener.connect()
        except Exception as exc:  # noqa: BLE001 - без LISTEN работаем опросом
            log.warning("LISTEN %s failed, polling only: %s", TASKS_CHANNEL, exc)
        try:
            self._loop(listener)
        finally:
            self._shutdown()
            listener.close()
        return self.exit_code

    def _loop(self, listener: Listener) -> None:
        """Цикл с защитой от временных сбоев (БД перезапускается и т. п.): ошибка итерации
        логируется, следующая попытка — через 1→30 с; исполняемые задачи продолжают работать."""
        failures = 0
        last_failure = 0.0
        while not self._stop.is_set():
            try:
                self._loop_body(listener)
                return
            except Exception:  # noqa: BLE001
                if time.monotonic() - last_failure > 300.0:
                    failures = 0
                last_failure = time.monotonic()
                failures += 1
                delay = min(2.0 ** (failures - 1), 30.0)
                log.exception("worker loop error (#%s), retry in %.0fs", failures, delay)
                self._stop.wait(delay)

    def _loop_body(self, listener: Listener) -> None:
        next_poll = 0.0
        woken = True
        while not self._stop.is_set():
            now = time.monotonic()
            if self.scheduler is not None:
                self.scheduler.maybe_tick()

            if self.slot is not None and not self._ensure_slot():
                self._set_state(STATE_STANDBY)
                self._wait(listener, self.config.standby_retry_sec)
                continue

            if woken or now >= next_poll:
                woken = False
                next_poll = now + self.config.poll_interval_sec
                idle_exit = self._fill_slots()
                if idle_exit:
                    break
            if (
                self.config.max_tasks is not None
                and self._claimed >= self.config.max_tasks
                and not self._active
            ):
                break

            if self._deferred_reason and time.monotonic() < self._defer_until:
                self._set_state(f"deferred:{self._deferred_reason}")
            else:
                self._set_state(STATE_BUSY if self._active else STATE_IDLE)
            woken = self._wait(listener, 0.5) or woken

    def _ensure_slot(self) -> bool:
        assert self.slot is not None
        if self.slot.held:
            if self._active or time.monotonic() - self._last_worker_beat < self.config.worker_heartbeat_sec:
                return True
            return self.slot.check()
        return self.slot.try_acquire()

    def _wait(self, listener: Listener, timeout: float) -> bool:
        """Ждать уведомлений или внутреннего пробуждения. True — стоит проверить очередь."""
        woken = False
        if self._wakeup.is_set():
            self._wakeup.clear()
            return True
        if listener.connected:
            try:
                notes = listener.wait(timeout)
            except Exception as exc:  # noqa: BLE001
                log.warning("listener failed: %s", exc)
                notes = []
                self._stop.wait(timeout)
        else:
            notes = []
            self._stop.wait(timeout)
        for note in notes:
            if isinstance(note, Reconnected):
                woken = True
            elif isinstance(note, Notification):
                payload = note.payload
                if payload == self.cls:
                    woken = True
                elif payload.startswith(task_service.CANCEL_NOTIFY_PREFIX):
                    self._on_cancel_notify(payload)
        if self._wakeup.is_set():
            self._wakeup.clear()
            woken = True
        return woken

    def _on_cancel_notify(self, payload: str) -> None:
        try:
            task_id = int(payload[len(task_service.CANCEL_NOTIFY_PREFIX) :])
        except ValueError:
            return
        with self._active_lock:
            handle = self._active.get(task_id)
        if handle is not None:
            log.info("task %s: cancel notification", task_id)
            handle.request_cancel("user")

    def _fill_slots(self) -> bool:
        """Взять задачи в свободные слоты. True — пора выходить (exit_when_idle и очередь пуста)."""
        kinds = registered_kinds()
        if not kinds:
            return self.config.exit_when_idle and not self._active
        while not self._stop.is_set():
            with self._active_lock:
                free = self.config.concurrency - len(self._active)
            if free <= 0:
                return False
            if self.config.max_tasks is not None and self._claimed >= self.config.max_tasks:
                return False
            with connection() as conn:
                has = tasks_repo.has_claimable(conn, self.cls, kinds)
            if not has:
                if self.config.exit_when_idle and not self._active:
                    with connection() as conn:
                        pending = tasks_repo.count_pending(conn, self.cls, kinds)
                    return pending == 0
                return False
            if self.admission is not None and not self._admit():
                return False
            with connection() as conn:
                task = task_service.claim(conn, self.cls, self.worker_id, kinds)
            if task is None:
                return False
            self._claimed += 1
            self._start(task)
        return False

    def _admit(self) -> bool:
        assert self.admission is not None
        if time.monotonic() < self._defer_until:
            return False
        decision = self.admission.evaluate()
        if decision.ok:
            if self._deferred_reason is not None:
                log.info("admission ok again (was %s)", self._deferred_reason)
                try:
                    with connection() as conn:
                        tasks_repo.clear_waiting_deferred(conn, self.cls, [self._deferred_reason])
                except Exception as exc:  # noqa: BLE001
                    log.warning("clear defer marks failed: %s", exc)
                self._deferred_reason = None
            return True
        reason = decision.reason or "load_high"
        if reason != self._deferred_reason:
            log.warning("heavy admission denied: %s (%s)", reason, decision.detail)
        self._deferred_reason = reason
        self._defer_until = time.monotonic() + max(decision.retry_after_sec, 1.0)
        try:
            with connection() as conn:
                tasks_repo.mark_waiting_deferred(conn, self.cls, reason)
                if decision.disk == DiskLevel.EMERGENCY:
                    # §8.1: диск < disk_emergency_gb — аварийная уборка (задача light, dedup)
                    if task_service.enqueue_cleanup(conn, emergency=True).created:
                        log.error("disk below emergency threshold: emergency cleanup queued")
        except Exception as exc:  # noqa: BLE001
            log.warning("mark deferred failed: %s", exc)
        self._set_state(f"deferred:{reason}")
        return False

    # ------------------------------------------------------------ исполнение

    def _start(self, task: dict[str, Any]) -> None:
        handle = TaskHandle(task)
        with self._active_lock:
            self._active[handle.task_id] = handle
        thread = threading.Thread(
            target=self._execute, args=(handle,), name=f"task-{handle.task_id}", daemon=True
        )
        handle.thread = thread
        self._set_state(STATE_BUSY)
        thread.start()

    def _execute(self, handle: TaskHandle) -> None:
        task = handle.task
        spec = get_task_spec(str(task["kind"]))
        task_log: _TaskLog | None = None
        heartbeat = TaskHeartbeat(
            handle,
            self.worker_id,
            interval_sec=self.config.heartbeat_interval_sec,
            progress_interval_sec=self.config.progress_interval_sec,
        )
        outcome: Outcome
        try:
            with log_context(task_id=handle.task_id, worker_id=self.worker_id, job_id=task.get("job_id")):
                log_path = self.media.task_log_path(handle.task_id)
                task_log = _TaskLog(task, log_path)
                task_log.logger.info(
                    "start kind=%s stage=%s attempt=%s/%s worker=%s",
                    task["kind"],
                    task.get("stage"),
                    task["attempts"],
                    task["max_attempts"],
                    self.worker_id,
                )
                heartbeat.start()
                try:
                    if spec is None:
                        from autoshorts.core.errors import PermanentError

                        raise PermanentError(
                            f"no handler for task kind {task['kind']!r}", error_class="unknown_kind"
                        )
                    handle.budget = TaskBudgetGate(
                        task_id=handle.task_id, job_id=task.get("job_id"), source_id=task.get("source_id")
                    )
                    ctx = TaskContext(
                        task=task,
                        worker_id=self.worker_id,
                        cancel=handle.cancel,
                        progress=handle.set_progress,
                        log=task_log.logger,
                        settings=self.settings_provider(),
                        media=self.media,
                        log_path=log_path,
                        budget=handle.budget,
                    )
                    handle.commit_hooks = ctx.commit_hooks
                    result = spec.handler(ctx)
                    outcome = classify(
                        None, task, cancel_reason=handle.cancel_reason, result=_json_result(result)
                    )
                except BaseException as exc:  # noqa: BLE001 - любое исключение обработчика — исход задачи
                    cancel_reason = handle.cancel_reason
                    if cancel_reason is None and self._stop.is_set():
                        # SIGTERM мог убить подпроцесс раньше, чем _shutdown() отменил задачи:
                        # это остановка воркера, а не вина задачи — requeue без траты попытки
                        cancel_reason = "shutdown"
                    outcome = classify(exc, task, cancel_reason=cancel_reason)
                    level = (
                        logging.INFO if outcome.action in ("cancel", "defer", "requeue") else logging.WARNING
                    )
                    task_log.logger.log(
                        level,
                        "%s: %s",
                        outcome.action,
                        outcome.message or outcome.reason or "",
                        exc_info=outcome.action in ("retry", "fail"),
                    )
                    if not isinstance(exc, Exception):
                        # KeyboardInterrupt/SystemExit в потоке: исход фиксируется, дальше не пробрасываем
                        log.warning("task %s interrupted by %s", handle.task_id, type(exc).__name__)
                finally:
                    heartbeat.stop()
                    heartbeat.join(timeout=5.0)
                    self._release_budget(handle)
                self._apply(handle, outcome)
                task_log.logger.info("finish: %s", outcome.action)
        except Exception:  # noqa: BLE001 - сбой записи исхода: задачу подберёт reaper
            log.exception("task %s: failed to record outcome", handle.task_id)
        finally:
            if task_log is not None:
                task_log.close()
            with self._active_lock:
                self._active.pop(handle.task_id, None)
            handle.finished.set()
            self._wakeup.set()

    @staticmethod
    def _release_budget(handle: TaskHandle) -> None:
        """Снять резервы бюджета, которые обработчик не закрыл settle/release (упал, отменён,
        остановлен, потерял аренду): иначе фантомный резерв копится до конца бизнес-дня."""
        gate = handle.budget
        if gate is None:
            return
        try:
            released = gate.release_open()
        except Exception as exc:  # noqa: BLE001 - сбой БД не должен мешать записи исхода
            log.warning("task %s: releasing budget reservations failed: %s", handle.task_id, exc)
            return
        if released:
            log.info("task %s: released %s open budget reservation(s)", handle.task_id, released)

    def _apply(self, handle: TaskHandle, outcome: Outcome) -> None:
        """Записать исход; временный сбой БД — ещё 3 попытки (1, 2, 4 с), потом задачу вернёт reaper.

        Если упал хук `ctx.on_commit`, транзакция успеха откатывается и записывается исход по
        исключению хука (retry/fail/…), уже без хуков.
        """
        import psycopg
        from psycopg_pool import PoolTimeout

        delays = [1.0, 2.0, 4.0]
        while True:
            try:
                self._apply_once(handle, outcome)
                return
            except (psycopg.OperationalError, PoolTimeout) as exc:
                if not delays:
                    raise
                delay = delays.pop(0)
                log.warning("task %s: recording %s failed: %s", handle.task_id, outcome.action, exc)
                time.sleep(delay)
            except Exception as exc:
                if outcome.action != "succeed" or not handle.commit_hooks:
                    raise
                log.warning("task %s: commit hook failed: %s", handle.task_id, exc, exc_info=True)
                handle.commit_hooks = []
                outcome = classify(exc, handle.task, cancel_reason=None)

    def _apply_once(self, handle: TaskHandle, outcome: Outcome) -> None:
        task = handle.task
        if outcome.action == "abandon":
            log.warning("task %s: lease lost, outcome not recorded", handle.task_id)
            return
        with connection() as conn:
            if outcome.action == "succeed":
                row = task_service.complete(conn, task, self.worker_id, outcome.result)
                if row is not None:
                    for hook in handle.commit_hooks:
                        hook(conn, row)
            elif outcome.action == "retry":
                row = task_service.retry_later(
                    conn,
                    task,
                    self.worker_id,
                    outcome.delay_sec,
                    outcome.error_class or "error",
                    outcome.message,
                )
            elif outcome.action == "fail":
                row = task_service.fail(
                    conn, task, self.worker_id, outcome.error_class or "error", outcome.message
                )
            elif outcome.action == "defer":
                row = task_service.defer(
                    conn, task, self.worker_id, outcome.reason or "load_high", outcome.delay_sec
                )
            elif outcome.action == "cancel":
                row = task_service.cancelled_by_worker(conn, task, self.worker_id, outcome.message)
            elif outcome.action == "requeue":
                row = task_service.requeue_for_shutdown(conn, task, self.worker_id)
            else:  # pragma: no cover - защитный
                raise ValueError(f"unknown outcome {outcome.action}")
        if row is None:
            log.warning("task %s: lease lost before %s was recorded", handle.task_id, outcome.action)

    # ------------------------------------------------------------ остановка

    def _shutdown(self) -> None:
        self._stop.set()
        handles = [h for h in (self._active.get(i) for i in self.active_task_ids()) if h is not None]
        if handles:
            log.info("worker %s: stopping, %s task(s) in flight", self.worker_id, len(handles))
            self._set_state(STATE_STOPPING, force=True)
            for h in handles:
                h.request_cancel("shutdown")
            ffmpeg_exec.kill_all_active(grace_sec=self.config.kill_grace_sec)
            deadline = time.monotonic() + self.config.shutdown_grace_sec
            for h in handles:
                h.finished.wait(max(deadline - time.monotonic(), 0.0))
            leftovers = [h for h in handles if not h.finished.is_set()]
            for h in leftovers:
                try:
                    with connection() as conn:
                        task_service.requeue_for_shutdown(conn, h.task, self.worker_id)
                    log.warning("task %s: handler did not stop in time, requeued", h.task_id)
                except Exception as exc:  # noqa: BLE001
                    log.warning("task %s: requeue on shutdown failed: %s", h.task_id, exc)
                # процесс сейчас выйдет: резервы зависшего обработчика снимаются (повторный settle
                # уже снятого резерва его второй раз не вычтет)
                self._release_budget(h)
        if self.scheduler is not None:
            self.scheduler.close()
        if self.slot is not None:
            self.slot.release()
        self._unregister()
        log.info("worker %s stopped (%s task(s) claimed)", self.worker_id, self._claimed)
