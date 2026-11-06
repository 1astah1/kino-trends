"""Тестовые виды задач (`wtest_*`) для тестов очереди.

Импортируется и в процессе pytest, и в подпроцессах воркера (`autoshorts-worker --import
tests.worker.task_fixtures`). Параметры — в `payload`.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from autoshorts.core.errors import PermanentError, ResourceDeferred, RetryableError
from autoshorts.pipeline.ffmpeg_exec import run_media
from autoshorts.tasks.registry import TaskContext, register_task

TEST_KINDS = (
    "wtest_record",
    "wtest_heavy_record",
    "wtest_block",
    "wtest_heavy_block",
    "wtest_media_sleep",
    "wtest_defer",
    "wtest_retry",
    "wtest_permanent",
    "wtest_crash",
    "wtest_progress",
    "wtest_result_file",
    "wtest_commit",
)


def _append(path: str, line: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (line + "\n").encode())
    finally:
        os.close(fd)


def _record(ctx: TaskContext) -> dict[str, Any]:
    p = ctx.payload
    sleep_ms = float(p.get("sleep_ms", 0))
    if sleep_ms:
        time.sleep(sleep_ms / 1000.0)
    if p.get("file"):
        _append(p["file"], f"{ctx.task_id} {ctx.worker_id} {os.getpid()}")
    return {"ok": True, "worker": ctx.worker_id}


@register_task("wtest_record", "light", max_attempts=3)
def wtest_record(ctx: TaskContext) -> dict[str, Any]:
    return _record(ctx)


@register_task("wtest_heavy_record", "heavy", max_attempts=2)
def wtest_heavy_record(ctx: TaskContext) -> dict[str, Any]:
    return _record(ctx)


def _block(ctx: TaskContext) -> dict[str, Any]:
    """Отметиться в started_file и ждать release_file (или отмену), не дольше timeout_sec."""
    p = ctx.payload
    if p.get("started_file"):
        _append(p["started_file"], f"{ctx.task_id} {ctx.worker_id} {os.getpid()}")
    deadline = time.monotonic() + float(p.get("timeout_sec", 60))
    release = Path(p["release_file"]) if p.get("release_file") else None
    while time.monotonic() < deadline:
        ctx.check_cancelled()
        if release is not None and release.exists():
            return {"released": True, "worker": ctx.worker_id}
        time.sleep(0.05)
    return {"released": False, "worker": ctx.worker_id}


@register_task("wtest_block", "light", max_attempts=3)
def wtest_block(ctx: TaskContext) -> dict[str, Any]:
    return _block(ctx)


@register_task("wtest_heavy_block", "heavy", max_attempts=2)
def wtest_heavy_block(ctx: TaskContext) -> dict[str, Any]:
    return _block(ctx)


@register_task("wtest_media_sleep", "light", max_attempts=2)
def wtest_media_sleep(ctx: TaskContext) -> dict[str, Any]:
    """Группа процессов `sleep 60`: sh пишет свой pid (= pgid) в pid_file и ждёт двух sleep."""
    pid_file = ctx.payload["pid_file"]
    script = f"echo $$ > {pid_file}; sleep 60 & sleep 60 & wait"
    run_media(
        ["sh", "-c", script],
        timeout_sec=float(ctx.payload.get("timeout_sec", 120)),
        threads=1,
        cancel=ctx.cancel,
        log_path=ctx.log_path,
        kill_grace_sec=2.0,
    )
    return {"finished": True}


@register_task("wtest_defer", "light", max_attempts=3)
def wtest_defer(ctx: TaskContext) -> None:
    raise ResourceDeferred(
        ctx.payload.get("reason", "budget_exhausted"), retry_after_sec=float(ctx.payload.get("delay", 120))
    )


@register_task("wtest_retry", "light", max_attempts=2)
def wtest_retry(ctx: TaskContext) -> None:
    raise RetryableError("temporary failure", retry_after_sec=ctx.payload.get("retry_after_sec"))


@register_task("wtest_permanent", "light", max_attempts=3)
def wtest_permanent(ctx: TaskContext) -> None:
    raise PermanentError("invalid input", error_class="bad_input")


@register_task("wtest_crash", "light", max_attempts=3)
def wtest_crash(ctx: TaskContext) -> None:
    raise ZeroDivisionError("unexpected bug")


@register_task("wtest_progress", "light", max_attempts=1)
def wtest_progress(ctx: TaskContext) -> dict[str, Any]:
    ctx.progress(0.42)
    ctx.log.info("progress set to 42%")
    time.sleep(float(ctx.payload.get("hold_sec", 1.0)))
    return {"done": True}


@register_task("wtest_result_file", "light", max_attempts=1)
def wtest_result_file(ctx: TaskContext) -> dict[str, Any]:
    folder = ctx.media.tmp_dir(create=True)
    path = folder / f"result_{ctx.task_id}.mp3"
    path.write_bytes(b"ID3fake-mp3-bytes")
    return {"file": ctx.media.relpath(path), "media_type": "audio/mpeg", "filename": "sample.mp3"}


@register_task("wtest_commit", "light", max_attempts=2)
def wtest_commit(ctx: TaskContext) -> dict[str, Any]:
    """Следующая задача ставится хуком в транзакции успеха; `hook_error` — хук падает."""
    from autoshorts.services.tasks import enqueue

    def hook(conn: Any, row: dict[str, Any]) -> None:
        assert row["status"] == "succeeded"
        enqueue(conn, "wtest_record", dedup_key=f"follow:{ctx.task_id}", wake=False)
        if ctx.payload.get("hook_error") == "permanent":
            raise PermanentError("hook rejected", error_class="hook_rejected")
        if ctx.payload.get("hook_error") == "retry":
            raise RetryableError("hook flaky")

    ctx.on_commit(hook)
    return {"committed": True}
