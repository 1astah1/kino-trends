"""Очередь задач: постановка, переходы, reaper, управление из API (docs/ARCHITECTURE.md §6.1).

Функции с `conn` работают в транзакции вызывающего (NOTIFY уходит на COMMIT); API открывает её
сам (`with connection() as conn`). `cleanup_preview()` открывает транзакцию пула сама.

Постановка:

    with connection() as conn:
        res = enqueue(conn, "metrics_poll", payload={"publication_id": 7}, dedup_key="metrics:7")
        res = enqueue_job_stage(conn, job_id=42, stage="render", rev=0)

Переходы, которые выполняет воркер (`claim`, `complete`, `fail`, `retry_later`, `defer`,
`requeue_for_shutdown`, `cancelled_by_worker`), защищены арендой: если задачу уже забрал reaper,
они возвращают None.

Политика повторов §6.1: `retry_backoff_sec(n) = min(30·4^(n−1), 1800)` ±20% джиттера.

Уборка (§8.2, каркас M1): `plan_cleanup` → `execute_cleanup` (задача `cleanup`),
`cleanup_preview()` и `enqueue_cleanup()` — для `/maintenance/cleanup-preview|cleanup` (M1-B).
"""

from __future__ import annotations

import logging
import os
import random
import shutil
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoshorts.core.errors import AppError, Conflict, NotFound
from autoshorts.db.notify import notify, publish_event, wake_workers
from autoshorts.domain.enums import JobStatus
from autoshorts.domain.events import TASKS_CHANNEL, EventType
from autoshorts.repos import artifacts as artifacts_repo
from autoshorts.repos import schedules as schedules_repo
from autoshorts.repos import tasks as tasks_repo
from autoshorts.repos import workers as workers_repo

if TYPE_CHECKING:
    from autoshorts.core.storage import MediaLayout

log = logging.getLogger(__name__)

Row = dict[str, Any]

RETRY_BASE_SEC = 30.0
RETRY_FACTOR = 4.0
RETRY_MAX_SEC = 1800.0
RETRY_JITTER = 0.20
STALE_HEARTBEAT_SEC = 180.0  # reaper: heartbeat старше 3 минут (§6.1)
WORKER_ALIVE_SEC = 90.0  # как WORKER_STALE_SEC фронта
WORKER_PRUNE_SEC = 86400.0  # записи мёртвых воркеров старше суток удаляются reaper'ом
CANCEL_NOTIFY_PREFIX = "cancel:"

# Статусы job, из которых окончательный сбой задачи переводит ролик в failed (§6.2).
JOB_FAILABLE_STATUSES = (JobStatus.QUEUED.value, JobStatus.PROCESSING.value)


class TaskStateConflict(Conflict):
    code = "task_state_conflict"
    default_message = "Задача в неподходящем статусе для этого действия"


def retry_backoff_sec(
    attempt: int, *, rng: random.Random | None = None, jitter: float = RETRY_JITTER
) -> float:
    """§6.1: `min(30·4^(n−1), 1800)` с ±20% джиттера; n — номер неудачной попытки (с 1)."""
    n = max(int(attempt), 1)
    base = min(RETRY_BASE_SEC * RETRY_FACTOR ** (n - 1), RETRY_MAX_SEC)
    r = rng if rng is not None else random
    return base * (1.0 + r.uniform(-jitter, jitter))


def _snake(name: str) -> str:
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i and not name[i - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def error_class_of(exc: BaseException) -> str:
    """`error_class` для tasks/jobs: явный у PermanentError, иначе имя класса в snake_case."""
    explicit = getattr(exc, "error_class", None)
    if isinstance(explicit, str) and explicit:
        return explicit
    return _snake(type(exc).__name__)[:64]


# ---------------------------------------------------------------- постановка


@dataclass(frozen=True)
class EnqueueResult:
    task: Row
    created: bool  # False — активный дубль по dedup_key уже был, возвращён он


def enqueue(
    conn: Any,
    kind: str,
    *,
    resource_class: str | None = None,
    priority: int | None = None,
    max_attempts: int | None = None,
    run_after: datetime | None = None,
    delay_sec: float | None = None,
    job_id: int | None = None,
    source_id: int | None = None,
    stage: str | None = None,
    payload: dict[str, Any] | None = None,
    dedup_key: str | None = None,
    wake: bool = True,
) -> EnqueueResult:
    """Поставить задачу. Дефолты класса/попыток/приоритета — из реестра (`register_task`), иначе
    из таблицы §6.1 (`tasks.registry.KIND_DEFAULTS`: API и бот ставят задачи, не загружая
    обработчики). Для вида, которого нет ни там, ни там, нужен явный `resource_class`.
    """
    from autoshorts.tasks.registry import default_priority, kind_defaults

    known = kind_defaults(kind)
    cls = resource_class or (known[0] if known else None)
    if cls is None:
        raise ValueError(f"unknown task kind {kind!r}: pass resource_class explicitly")
    attempts = int(max_attempts if max_attempts is not None else (known[1] if known else 3))
    prio = int(priority if priority is not None else (known[2] if known else default_priority(kind)))
    params = {
        "kind": kind,
        "resource_class": cls,
        "priority": prio,
        "max_attempts": attempts,
        "run_after": run_after,
        "delay_sec": delay_sec,
        "job_id": job_id,
        "source_id": source_id,
        "stage": stage,
        "payload": payload,
        "dedup_key": dedup_key,
    }
    for _ in range(3):
        row = tasks_repo.insert(conn, **params)
        if row is not None:
            if wake:
                wake_workers(conn, cls)
            return EnqueueResult(row, True)
        existing = tasks_repo.get_active_by_dedup(conn, dedup_key) if dedup_key else None
        if existing is not None:
            return EnqueueResult(existing, False)
        # дубль успел завершиться между INSERT и SELECT — пробуем ещё раз
    raise RuntimeError(f"could not enqueue {kind} with dedup_key {dedup_key!r}")


def enqueue_job_stage(
    conn: Any,
    job_id: int,
    stage: str,
    *,
    job_kind: str = "generate",
    rev: int = 0,
    payload: dict[str, Any] | None = None,
    run_after: datetime | None = None,
    priority: int | None = None,
    wake: bool = True,
) -> EnqueueResult:
    """Задача стадии ролика: `kind='job_stage'`, dedup `job:<id>:<stage>:<rev>`, класс по §6.1."""
    from autoshorts.stages.base import JOB_STAGE_TASK_KIND, stage_dedup_key, stage_task_defaults

    d = stage_task_defaults(stage, job_kind)
    return enqueue(
        conn,
        JOB_STAGE_TASK_KIND,
        resource_class=d.resource_class,
        priority=priority if priority is not None else d.priority,
        max_attempts=d.max_attempts,
        run_after=run_after,
        job_id=job_id,
        stage=stage,
        payload=payload,
        dedup_key=stage_dedup_key(job_id, stage, rev),
        wake=wake,
    )


# ---------------------------------------------------------------- события


def publish_progress(conn: Any, task: Row, pct: float) -> None:
    """`task_progress` (+ `job_progress` для задач ролика), pct — проценты 0..100."""
    pct = round(max(0.0, min(100.0, float(pct))), 2)
    publish_event(
        conn,
        EventType.TASK_PROGRESS,
        {
            "task_id": int(task["id"]),
            "kind": str(task["kind"]),
            "pct": pct,
            "source_id": task.get("source_id"),
        },
    )
    if task.get("job_id") is not None:
        publish_event(
            conn,
            EventType.JOB_PROGRESS,
            {
                "job_id": int(task["job_id"]),
                "task_id": int(task["id"]),
                "stage": task.get("stage"),
                "pct": pct,
            },
        )


def publish_worker(conn: Any, worker: Row) -> None:
    publish_event(
        conn,
        EventType.WORKER,
        {
            "worker_id": worker["id"],
            "resource_class": worker["resource_class"],
            "state": worker["state"],
            "task_id": worker.get("current_task_id"),
        },
    )


# ---------------------------------------------------------------- связь с job


def fail_job_for_task(conn: Any, task: Row, error_class: str, message: str | None) -> Row | None:
    """Окончательный сбой задачи ролика → job `failed` (§6.1, §6.2), событие `job_status`.

    Трогает только job в queued/processing; остальное (приёмка, rework) не меняет.
    """
    job_id = task.get("job_id")
    if job_id is None:
        return None
    row = conn.execute(
        """
        UPDATE jobs SET status = 'failed', error_class = %(ec)s, error = %(err)s, updated_at = now()
        WHERE id = %(id)s AND status = ANY(%(allowed)s)
        RETURNING id, status, stage
        """,
        {
            "id": job_id,
            "ec": error_class,
            "err": tasks_repo.clip_error(message),
            "allowed": list(JOB_FAILABLE_STATUSES),
        },
    ).fetchone()
    if row is not None:
        publish_event(
            conn, EventType.JOB_STATUS, {"job_id": row["id"], "status": row["status"], "stage": row["stage"]}
        )
    return row


def revive_job_for_task(conn: Any, task: Row) -> Row | None:
    """Ручной повтор задачи стадии упавшего ролика: job `failed` → `processing` на этой стадии."""
    job_id = task.get("job_id")
    if job_id is None or task.get("kind") != "job_stage":
        return None
    row = conn.execute(
        """
        UPDATE jobs SET status = 'processing', stage = COALESCE(%(stage)s, stage), error_class = NULL,
               error = NULL, updated_at = now()
        WHERE id = %(id)s AND status = 'failed'
        RETURNING id, status, stage
        """,
        {"id": job_id, "stage": task.get("stage")},
    ).fetchone()
    if row is not None:
        publish_event(
            conn, EventType.JOB_STATUS, {"job_id": row["id"], "status": row["status"], "stage": row["stage"]}
        )
    return row


# ---------------------------------------------------------------- переходы воркера


def claim(conn: Any, resource_class: str, worker_id: str, kinds: Sequence[str] | None) -> Row | None:
    return tasks_repo.claim_next(conn, resource_class, worker_id, kinds)


def complete(conn: Any, task: Row, worker_id: str, result: dict[str, Any] | None) -> Row | None:
    row = tasks_repo.mark_succeeded(conn, int(task["id"]), worker_id, result)
    if row is not None:
        publish_progress(conn, row, 100.0)
    return row


def fail(conn: Any, task: Row, worker_id: str, error_class: str, message: str | None) -> Row | None:
    row = tasks_repo.mark_failed(conn, int(task["id"]), worker_id, error_class, message)
    if row is not None:
        fail_job_for_task(conn, row, error_class, message)
    return row


def retry_later(
    conn: Any, task: Row, worker_id: str, delay_sec: float, error_class: str, message: str | None
) -> Row | None:
    return tasks_repo.mark_retry(conn, int(task["id"]), worker_id, delay_sec, error_class, message)


def defer(conn: Any, task: Row, worker_id: str, reason: str, delay_sec: float) -> Row | None:
    return tasks_repo.mark_deferred(conn, int(task["id"]), worker_id, reason, delay_sec)


def requeue_for_shutdown(conn: Any, task: Row, worker_id: str) -> Row | None:
    row = tasks_repo.mark_requeued(
        conn, int(task["id"]), worker_id, "worker_shutdown", "воркер остановлен, задача возвращена в очередь"
    )
    if row is not None:
        wake_workers(conn, str(row["resource_class"]))
    return row


def cancelled_by_worker(conn: Any, task: Row, worker_id: str, note: str | None = None) -> Row | None:
    return tasks_repo.mark_cancelled(conn, int(task["id"]), worker_id, note)


# ---------------------------------------------------------------- reaper (§6.1)


@dataclass
class ReapReport:
    requeued: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    workers_pruned: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"requeued": self.requeued, "failed": self.failed, "workers_pruned": self.workers_pruned}


def reap_stale_tasks(
    conn: Any,
    *,
    stale_after_sec: float = STALE_HEARTBEAT_SEC,
    rng: random.Random | None = None,
    prune_workers_after_sec: float | None = WORKER_PRUNE_SEC,
) -> ReapReport:
    """`running` с протухшим heartbeat: attempts < max → queued с backoff, иначе failed
    (`error_class='worker_lost'`) и job тоже failed."""
    report = ReapReport()
    for task in tasks_repo.reap_stale(conn, stale_after_sec):
        note = f"heartbeat старше {int(stale_after_sec)} с, воркер {task.get('locked_by') or '?'} потерян"
        if int(task["attempts"]) < int(task["max_attempts"]):
            row = tasks_repo.reaper_requeue(
                conn, int(task["id"]), retry_backoff_sec(int(task["attempts"]), rng=rng), note
            )
            if row is not None:
                report.requeued.append(int(row["id"]))
                wake_workers(conn, str(row["resource_class"]))
        else:
            row = tasks_repo.reaper_fail(conn, int(task["id"]), note)
            if row is not None:
                report.failed.append(int(row["id"]))
                fail_job_for_task(conn, row, "worker_lost", note)
    if prune_workers_after_sec:
        report.workers_pruned = workers_repo.prune_stale(conn, prune_workers_after_sec)
    if report.requeued or report.failed:
        log.warning("reaper: requeued=%s failed=%s", report.requeued, report.failed)
    return report


# ---------------------------------------------------------------- управление из API


def _raise_if_missing(row: Row | None, entity: str) -> Row:
    if row is None:
        raise NotFound(entity, "Задача не найдена" if entity == "task" else "Не найдено")
    return row


def get_task(conn: Any, task_id: int) -> Row:
    return _raise_if_missing(tasks_repo.get(conn, task_id), "task")


def request_cancel(conn: Any, task_id: int) -> Row:
    """queued → cancelled сразу; running → `cancel_requested` + NOTIFY воркеру (он убьёт группу
    процессов); завершённая → 409 `task_state_conflict`."""
    task = _raise_if_missing(tasks_repo.get(conn, task_id, for_update=True), "task")
    status = task["status"]
    if status == "queued":
        return tasks_repo.cancel_queued(conn, task_id) or task
    if status == "running":
        row = tasks_repo.request_cancel_running(conn, task_id) or task
        notify(conn, TASKS_CHANNEL, f"{CANCEL_NOTIFY_PREFIX}{int(task_id)}")
        return row
    raise TaskStateConflict(details={"task_id": task_id, "status": status})


def retry_task(conn: Any, task_id: int) -> Row:
    """failed/cancelled → queued с новой серией попыток. Задача стадии упавшего ролика
    возвращает ролик в processing. Активный дубль по dedup_key — 409."""
    task = _raise_if_missing(tasks_repo.get(conn, task_id, for_update=True), "task")
    if task["status"] not in ("failed", "cancelled"):
        raise TaskStateConflict(details={"task_id": task_id, "status": task["status"]})
    if task.get("dedup_key"):
        dup = tasks_repo.get_active_by_dedup(conn, task["dedup_key"])
        if dup is not None and dup["id"] != task_id:
            raise TaskStateConflict(
                "Уже есть активная задача с тем же ключом",
                details={"task_id": task_id, "active_task_id": dup["id"]},
            )
    row = tasks_repo.requeue_terminal(conn, task_id)
    if row is None:
        raise TaskStateConflict(details={"task_id": task_id})
    revive_job_for_task(conn, row)
    wake_workers(conn, str(row["resource_class"]))
    return row


def list_tasks(
    conn: Any,
    *,
    statuses: Sequence[str] | None = None,
    kinds: Sequence[str] | None = None,
    resource_class: str | None = None,
    job_id: int | None = None,
    source_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Row], int]:
    return tasks_repo.list_tasks(
        conn,
        statuses=statuses,
        kinds=kinds,
        resource_class=resource_class,
        job_id=job_id,
        source_id=source_id,
        limit=limit,
        offset=offset,
    )


def task_log_path(task_id: int) -> Path:
    from autoshorts.core.storage import get_layout

    return get_layout().task_log_path(task_id)


def task_log_tail(task_id: int, lines: int = 200) -> list[str]:
    from autoshorts.core.storage import tail_lines

    return tail_lines(task_log_path(task_id), lines)


def list_workers(conn: Any) -> list[Row]:
    return workers_repo.list_all(conn)


def list_schedules(conn: Any) -> list[Row]:
    return schedules_repo.list_all(conn)


def update_schedule(conn: Any, name: str, *, enabled: bool | None, interval_sec: int | None) -> Row:
    if interval_sec is not None and interval_sec < 30:
        raise AppError("interval_sec ≥ 30", code="validation_error", status=422)
    row = schedules_repo.update(conn, name, enabled=enabled, interval_sec=interval_sec)
    if row is None:
        raise NotFound("schedule", "Расписание не найдено")
    return row


# ---------------------------------------------------------------- для /system/status (M1-B)


def queue_counters(conn: Any) -> dict[str, dict[str, int]]:
    return tasks_repo.queue_counters(conn)


def heavy_slot(conn: Any) -> dict[str, Any]:
    """`{owner, task_id, stage, pct}`: занятый тяжёлый слот (или все None)."""
    task = tasks_repo.running_heavy(conn)
    if task is None:
        return {"owner": None, "task_id": None, "stage": None, "pct": None}
    progress = task.get("progress")
    return {
        "owner": task.get("locked_by"),
        "task_id": int(task["id"]),
        "stage": task.get("stage"),
        "pct": float(progress) if progress is not None else None,
    }


# ---------------------------------------------------------------- уборка (§8.2, каркас)

CLEANUP_CATEGORIES: tuple[str, ...] = (
    "work_artifacts",
    "rejected_media",
    "proxies",
    "originals",
    "tus_parts",
    "task_logs",
    "tmp",
)
CLEANUP_DEDUP_MANUAL = "cleanup:manual"
CLEANUP_DEDUP_EMERGENCY = "cleanup:emergency"
TMP_MAX_AGE_SEC = 24 * 3600
TMP_EMERGENCY_MAX_AGE_SEC = 3600
TUS_PART_MAX_AGE_SEC = 24 * 3600
TASK_LOG_RETAIN_DAYS = 14
SYSTEM_TASKS_RETAIN_DAYS = 14
PROTECTED_PREFIXES = ("masters/",)  # мастер опубликованного не удаляется никогда (§8.2)


@dataclass(frozen=True)
class CleanupCandidate:
    category: str
    path: Path
    bytes: int
    artifact_id: int | None = None


def _entry_size(path: Path) -> int:
    from autoshorts.core.resources import dir_size_bytes

    try:
        if path.is_dir() and not path.is_symlink():
            return dir_size_bytes(path)
        return path.lstat().st_size
    except OSError:
        return 0


def _old_entries(
    folder: Path, max_age_sec: float, category: str, *, suffix: str | None = None
) -> list[CleanupCandidate]:
    if not folder.is_dir():
        return []
    cutoff = time.time() - max_age_sec
    out: list[CleanupCandidate] = []
    with os.scandir(folder) as it:
        for entry in it:
            try:
                if suffix and not entry.name.endswith(suffix):
                    continue
                if entry.stat(follow_symlinks=False).st_mtime >= cutoff:
                    continue
            except OSError:
                continue
            path = Path(entry.path)
            out.append(CleanupCandidate(category, path, _entry_size(path)))
    return out


def plan_cleanup(
    conn: Any, media: MediaLayout, *, emergency: bool = False, settings: Any | None = None
) -> list[CleanupCandidate]:
    """Что удалит уборка сейчас. Каркас M1: tmp, логи задач, брошенные tus-части и файлы
    артефактов, помеченных `deleted_at` (rework). Сроки хранения роликов и источников
    (`*_retain_days`, §8.2) добавляют владельцы этих областей (M2/M5)."""
    out: list[CleanupCandidate] = []
    out += _old_entries(media.tmp_dir(), TMP_EMERGENCY_MAX_AGE_SEC if emergency else TMP_MAX_AGE_SEC, "tmp")
    out += _old_entries(media.task_logs_dir(), TASK_LOG_RETAIN_DAYS * 86400, "task_logs", suffix=".log")
    out += _old_entries(media.root / "uploads", TUS_PART_MAX_AGE_SEC, "tus_parts", suffix=".part")

    live_paths = {
        r["path"]
        for r in conn.execute("SELECT DISTINCT path FROM artifacts WHERE deleted_at IS NULL").fetchall()
    }
    seen: set[str] = set()
    for art in artifacts_repo.list_deleted(conn, limit=5000):
        rel = art["path"]
        if rel in live_paths or rel in seen or rel.startswith(PROTECTED_PREFIXES):
            continue
        seen.add(rel)
        try:
            path = media.resolve(rel)
            st = path.lstat()
        except (OSError, ValueError):
            continue
        # файл переписан после пометки (стадия уже пишет новый вывод по тому же пути) — не трогать
        if st.st_mtime > art["deleted_at"].timestamp():
            continue
        out.append(CleanupCandidate("work_artifacts", path, st.st_size, int(art["id"])))
    if settings is None:
        from autoshorts.services.settings import get_runtime_settings

        settings = get_runtime_settings(conn, fallback_to_defaults=True)
    out += _dead_job_dirs(conn, media, settings, emergency=emergency)
    out += _spent_source_media(conn, media, settings, emergency=emergency)
    return out


# Сроки хранения §8.2 (решение 29.10: стенд на диске 30 ГБ вставал «мало места» — копились файлы
# отклонённых роликов и исходные видео отработанных фильмов).
DEAD_JOB_STATUSES: tuple[str, ...] = ("cancelled", "rejected", "failed")
# ролики источника в этих статусах источник больше не трогают (одобренный может уйти на доработку — держим)
DONE_JOB_STATUSES: tuple[str, ...] = (*DEAD_JOB_STATUSES, "published")
# тяжёлые файлы источника: видео и звук; расшифровка, сцены, раскадровка остаются
SOURCE_HEAVY_PREFIXES: tuple[str, ...] = ("original_", "proxy_", "audio.")
EMERGENCY_DEAD_JOB_SEC = 3600


def _dead_job_dirs(conn: Any, media: MediaLayout, settings: Any, *, emergency: bool) -> list[CleanupCandidate]:
    """Папки отклонённых, отменённых и упавших роликов старше `rejected_jobs_retain_days`
    (в аварийном режиме — старше часа). Папку не трогаем, если на её файлы ссылается живой ролик (клон)."""
    max_age = EMERGENCY_DEAD_JOB_SEC if emergency else int(settings.rejected_jobs_retain_days) * 86400
    rows = conn.execute(
        "SELECT video_uid FROM jobs WHERE status = ANY(%s) AND video_uid IS NOT NULL"
        " AND updated_at < now() - make_interval(secs => %s)",
        (list(DEAD_JOB_STATUSES), max_age),
    ).fetchall()
    if not rows:
        return []
    shared = {
        r["uid"]
        for r in conn.execute(
            "SELECT DISTINCT split_part(a.path, '/', 2) AS uid FROM artifacts a JOIN jobs j ON j.id = a.job_id"
            " WHERE a.deleted_at IS NULL AND a.path LIKE 'jobs/%%' AND j.status <> ALL(%s)",
            (list(DEAD_JOB_STATUSES),),
        ).fetchall()
    }
    out: list[CleanupCandidate] = []
    for r in rows:
        uid = str(r["video_uid"])
        if uid in shared or not uid or "/" in uid:
            continue
        path = media.jobs_dir() / uid
        if path.is_dir() and not path.is_symlink():
            out.append(CleanupCandidate("rejected_media", path, _entry_size(path)))
    return out


def _spent_source_media(conn: Any, media: MediaLayout, settings: Any, *, emergency: bool) -> list[CleanupCandidate]:
    """Видео и звук источника, который отработан: архивный, или все его ролики закрыты (опубликованы,
    отклонены, отменены, упали) дольше `original_retain_days`. Расшифровка и раскадровка остаются."""
    days = 0 if emergency else int(settings.original_retain_days)
    rows = conn.execute(
        """
        SELECT s.id FROM sources s
        WHERE (s.status = 'archived' AND s.updated_at < now() - interval '1 hour')
           OR (s.status IN ('ready', 'failed')
               AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.source_id = s.id AND j.status <> ALL(%s))
               AND COALESCE((SELECT max(j.updated_at) FROM jobs j WHERE j.source_id = s.id), s.updated_at)
                   < now() - make_interval(days => %s))
        """,
        (list(DONE_JOB_STATUSES), days),
    ).fetchall()
    out: list[CleanupCandidate] = []
    for r in rows:
        folder = media.source_dir(int(r["id"]))
        if not folder.is_dir():
            continue
        with os.scandir(folder) as it:
            for entry in it:
                if not entry.is_file(follow_symlinks=False) or not entry.name.startswith(SOURCE_HEAVY_PREFIXES):
                    continue
                path = Path(entry.path)
                out.append(CleanupCandidate("originals", path, _entry_size(path)))
    return out


def summarize_cleanup(candidates: Sequence[CleanupCandidate]) -> list[dict[str, Any]]:
    """`[{category, files, bytes}]` по всем категориям §8.2 (нереализованные — нули)."""
    totals = {c: {"category": c, "files": 0, "bytes": 0} for c in CLEANUP_CATEGORIES}
    for cand in candidates:
        t = totals.setdefault(cand.category, {"category": cand.category, "files": 0, "bytes": 0})
        t["files"] += 1
        t["bytes"] += int(cand.bytes)
    return list(totals.values())


def execute_cleanup(
    candidates: Sequence[CleanupCandidate], *, cancel: threading.Event | None = None
) -> list[dict[str, Any]]:
    """Удалить кандидатов плана. Возвращает сводку фактически удалённого."""
    done: list[CleanupCandidate] = []
    for cand in candidates:
        if cancel is not None and cancel.is_set():
            break
        try:
            if cand.path.is_dir() and not cand.path.is_symlink():
                shutil.rmtree(cand.path)
            else:
                cand.path.unlink()
            done.append(cand)
        except FileNotFoundError:
            continue
        except OSError as exc:
            log.warning("cleanup: cannot remove %s: %s", cand.path, exc)
    return summarize_cleanup(done)


def cleanup_preview(media: MediaLayout | None = None) -> dict[str, Any]:
    """Для `POST /maintenance/cleanup-preview`: `{items: [{category, files, bytes}]}`."""
    from autoshorts.core.storage import get_layout
    from autoshorts.db.pool import connection

    with connection() as conn:
        plan = plan_cleanup(conn, media or get_layout())
    return {"items": summarize_cleanup(plan)}


def enqueue_cleanup(conn: Any, *, emergency: bool = False) -> EnqueueResult:
    """Для `POST /maintenance/cleanup` (202 `{task_id}`) и аварийной уборки при диске < 5 ГБ."""
    return enqueue(
        conn,
        "cleanup",
        payload={"emergency": True} if emergency else {"manual": True},
        dedup_key=CLEANUP_DEDUP_EMERGENCY if emergency else CLEANUP_DEDUP_MANUAL,
        priority=150 if emergency else None,
    )
