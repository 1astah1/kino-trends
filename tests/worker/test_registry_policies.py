"""Реестры задач и стадий, политика исходов и backoff (без БД)."""

from __future__ import annotations

import random
import threading
from pathlib import Path

import pytest

from autoshorts.core.errors import Cancelled, PermanentError, ResourceDeferred, RetryableError
from autoshorts.pipeline.ffmpeg_exec import MediaError
from autoshorts.services.tasks import error_class_of, retry_backoff_sec
from autoshorts.stages.base import (
    STAGE_DEFAULTS,
    NewArtifact,
    StageResult,
    get_stage,
    input_hash,
    register_stage,
    stage_dedup_key,
    stage_task_defaults,
    unregister_stage,
)
from autoshorts.tasks.registry import (
    TaskContext,
    default_priority,
    get_task_spec,
    load_all,
    register_task,
    registered_kinds,
    unregister_task,
)
from autoshorts.workers.policies import classify


def test_load_all_registers_system_tasks() -> None:
    imported = load_all(strict=True)
    assert "autoshorts.tasks.system" in imported
    assert {"task_reaper", "cleanup"} <= set(registered_kinds())
    spec = get_task_spec("task_reaper")
    assert (
        spec is not None
        and spec.resource_class == "light"
        and spec.max_attempts == 5
        and spec.priority == 200
    )


def test_register_task_validation_and_duplicates() -> None:
    @register_task("zz_probe", "heavy", max_attempts=2, paid=True)
    def probe(ctx: TaskContext) -> None:
        return None

    try:
        spec = get_task_spec("zz_probe")
        assert spec is not None and spec.paid and spec.priority == 100 and spec.resource_class == "heavy"
        register_task("zz_probe", "heavy", max_attempts=2, paid=True)(probe)  # повтор того же — ок

        def other(ctx: TaskContext) -> None:
            return None

        with pytest.raises(ValueError):
            register_task("zz_probe", "light", max_attempts=1)(other)
    finally:
        unregister_task("zz_probe")
    with pytest.raises(ValueError):
        register_task("Плохой", "light", max_attempts=1)
    with pytest.raises(ValueError):
        register_task("ok_kind", "medium", max_attempts=1)
    with pytest.raises(ValueError):
        register_task("ok_kind", "light", max_attempts=0)
    assert default_priority("package_build") == 10 and default_priority("unknown_kind") == 100


def test_task_context_cancel_point(tmp_path: Path) -> None:
    from autoshorts.core.storage import MediaLayout
    from autoshorts.domain.settings_schema import RuntimeSettings

    ev = threading.Event()
    ctx = TaskContext(
        task={"id": 5, "kind": "k", "payload": {"a": 1}, "attempts": 2, "job_id": None},
        worker_id="w",
        cancel=ev,
        progress=lambda f: None,
        log=__import__("logging").getLogger("t"),
        settings=RuntimeSettings(),
        media=MediaLayout(tmp_path),
        log_path=tmp_path / "5.log",
    )
    assert ctx.task_id == 5 and ctx.payload == {"a": 1} and ctx.attempt == 2
    ctx.check_cancelled()
    ev.set()
    with pytest.raises(Cancelled):
        ctx.check_cancelled()


def test_stage_registry_and_defaults() -> None:
    assert stage_task_defaults("render").resource_class == "heavy"
    assert stage_task_defaults("render").priority == 30 and stage_task_defaults("qc").priority == 20
    assert stage_task_defaults("script").paid and stage_task_defaults("script").max_attempts == 3
    assert stage_task_defaults("visuals").max_attempts == 2
    assert stage_task_defaults("clip_fetch").priority == 60
    assert set(STAGE_DEFAULTS) >= {"script", "voice", "visuals", "render", "qc"}

    @register_stage("generate", "voice", resource_class="heavy", max_attempts=2, paid=True)
    def voice(ctx):  # noqa: ANN001
        return StageResult()

    try:
        spec = get_stage("generate", "voice")
        assert spec is not None and spec.handler is voice
        d = stage_task_defaults("voice", "generate")
        assert (d.resource_class, d.max_attempts, d.paid, d.priority) == ("heavy", 2, True, 50)
    finally:
        unregister_stage("generate", "voice")
    with pytest.raises(ValueError):
        register_stage("generate", "nonexistent_stage", "light", 1, False)
    assert stage_dedup_key(42, "render", 1) == "job:42:render:1"
    with pytest.raises(KeyError):
        stage_task_defaults("package")


def test_stage_result_validation_and_input_hash(tmp_path: Path) -> None:
    StageResult(artifacts=[NewArtifact("final", tmp_path / "f.mp4", "h")], job_status="pending_review")
    with pytest.raises(ValueError):
        StageResult(job_status="approved")
    with pytest.raises(ValueError):
        StageResult(next_stage="bogus")
    a = input_hash("uid", "render", {"b": 1, "a": 2}, "tpl-v1")
    b = input_hash("uid", "render", {"a": 2, "b": 1}, "tpl-v1")
    assert a == b and len(a) == 64
    assert input_hash("uid", "render") != input_hash("uidr", "ender")


def test_backoff_formula() -> None:
    rng = random.Random(1)
    for n, base in [(1, 30), (2, 120), (3, 480), (4, 1800), (9, 1800)]:
        for _ in range(20):
            v = retry_backoff_sec(n, rng=rng)
            assert base * 0.8 <= v <= base * 1.2, (n, v)
    assert retry_backoff_sec(2, jitter=0) == 120


def test_classify_outcomes() -> None:
    task = {"attempts": 1, "max_attempts": 3}
    assert classify(None, task, result={"x": 1}).action == "succeed"
    o = classify(ResourceDeferred("budget_exhausted", retry_after_sec=300), task)
    assert (o.action, o.reason, o.delay_sec) == ("defer", "budget_exhausted", 300)
    assert classify(ResourceDeferred("weird"), task).reason == "load_high"
    o = classify(RetryableError("net", retry_after_sec=7), task)
    assert (o.action, o.delay_sec, o.error_class) == ("retry", 7, "retryable_error")
    try:
        1 / 0  # noqa: B018
    except ZeroDivisionError as exc:
        o = classify(exc, task, rng=random.Random(0))
    assert o.action == "retry" and o.error_class == "zero_division_error"
    assert "Traceback" in (o.message or "") and (o.message or "").endswith(
        "ZeroDivisionError: division by zero"
    )
    assert classify(ZeroDivisionError("bug"), {"attempts": 3, "max_attempts": 3}).action == "fail"
    o = classify(PermanentError("bad", error_class="bad_input"), task)
    assert (o.action, o.error_class) == ("fail", "bad_input")
    assert classify(Cancelled(), task).action == "cancel"
    # ffmpeg убит при остановке воркера — не вина задачи
    media = MediaError("killed", returncode=-15, stderr_tail="", cmd=["ffmpeg"])
    assert classify(media, task, cancel_reason="shutdown").action == "requeue"
    assert classify(media, task, cancel_reason="user").action == "cancel"
    assert classify(None, task, cancel_reason="lease_lost").action == "abandon"
    assert error_class_of(MediaError("x", returncode=1, stderr_tail="", cmd=[])) == "media_error"


def test_error_text_is_redacted() -> None:
    """last_error видят viewer и выше: ключи в URL, Bearer и пароль в URL БД маскируются."""
    from autoshorts.repos.tasks import clip_error
    from autoshorts.workers.policies import describe_error

    url = "https://x/v1beta/models/m:generateContent?key=AIzaSECRET123"
    task = {"attempts": 1, "max_attempts": 3}
    o = classify(RuntimeError(f"HTTP 500 for url '{url}'"), task, rng=random.Random(0))
    assert "AIzaSECRET123" not in (o.message or "") and "key=***" in (o.message or "")
    media = MediaError(
        "ffmpeg exited with 1", returncode=1, stderr_tail="Authorization: Bearer sk-live-XYZ", cmd=["ffmpeg"]
    )
    assert "sk-live-XYZ" not in describe_error(media)
    assert "hunter2" not in (clip_error("postgresql://u:hunter2@h/db") or "")
    assert clip_error(None) is None


def test_task_log_traceback_is_redacted(tmp_path: Path) -> None:
    from autoshorts.workers.runner import _TaskLog

    task_log = _TaskLog({"id": 7}, tmp_path / "7.log")
    try:
        try:
            raise RuntimeError("HTTP 403 for url 'https://x/m:generateContent?key=AIzaSECRET123'")
        except RuntimeError:
            task_log.logger.warning("%s: %s", "retry", "boom", exc_info=True)
    finally:
        task_log.close()
    text = (tmp_path / "7.log").read_text()
    assert "Traceback" in text and "AIzaSECRET123" not in text and "key=***" in text
