"""Runner в процессе теста: исходы задач, отмена, остановка, допуск, тяжёлый слот, планировщик."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any

from autoshorts.db.pool import connection
from autoshorts.domain.settings_schema import RuntimeSettings
from autoshorts.services import tasks as svc
from tests.worker.conftest import enqueue_raw, make_job, make_runner, run_in_thread, task_row, wait_until


def status_of(conn: Any, task_id: int) -> str:
    return task_row(conn, task_id)["status"]


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def test_runner_success_result_log_and_worker_row(queue_env: Any, conn: Any, tmp_path: Path) -> None:
    out = tmp_path / "exec.txt"
    t = enqueue_raw(conn, "wtest_record", payload={"file": str(out)})
    p = enqueue_raw(conn, "wtest_progress", payload={"hold_sec": 0.8})
    runner = make_runner(queue_env, concurrency=2, worker_id="t-1-light")
    with run_in_thread(runner):
        wait_until(lambda: status_of(conn, t["id"]) == "succeeded")
        wait_until(lambda: (task_row(conn, p["id"])["progress"] or 0) >= 42, timeout=5)
        w = wait_until(lambda: conn.execute("SELECT * FROM workers WHERE id = 't-1-light'").fetchone())
        assert w["resource_class"] == "light" and w["pid"] == os.getpid()
        wait_until(lambda: status_of(conn, p["id"]) == "succeeded")
    row = task_row(conn, t["id"])
    assert row["result"] == {"ok": True, "worker": "t-1-light"} and row["attempts"] == 1
    assert row["locked_by"] is None and float(row["progress"]) == 100.0
    assert out.read_text().split()[:2] == [str(t["id"]), "t-1-light"]
    log_text = (Path(queue_env.media_root) / "logs" / "tasks" / f"{p['id']}.log").read_text()
    assert (
        "start kind=wtest_progress" in log_text
        and "progress set to 42%" in log_text
        and "finish: succeed" in log_text
    )
    # остановка удалила запись воркера
    assert conn.execute("SELECT count(*) AS n FROM workers WHERE id = 't-1-light'").fetchone()["n"] == 0


def test_runner_resource_deferred_keeps_attempt(queue_env: Any, conn: Any) -> None:
    """Критерий M1 (через воркер): ResourceDeferred — queued, attempts не растёт, defer_reason."""
    t = enqueue_raw(conn, "wtest_defer", payload={"reason": "budget_exhausted", "delay": 300}, max_attempts=3)
    runner = make_runner(queue_env)
    with run_in_thread(runner):
        wait_until(lambda: task_row(conn, t["id"])["defer_reason"] == "budget_exhausted")
    row = task_row(conn, t["id"])
    assert row["status"] == "queued" and row["attempts"] == 0 and row["error_class"] is None
    left = conn.execute(
        "SELECT extract(epoch FROM run_after - now()) AS s FROM tasks WHERE id = %s", (t["id"],)
    )
    assert float(left.fetchone()["s"]) > 250


def test_runner_retry_permanent_and_crash(queue_env: Any, conn: Any) -> None:
    r = enqueue_raw(conn, "wtest_retry", max_attempts=2)
    f = enqueue_raw(conn, "wtest_permanent", max_attempts=3)
    c = enqueue_raw(conn, "wtest_crash", max_attempts=1)
    runner = make_runner(queue_env, concurrency=3)
    with run_in_thread(runner):
        wait_until(lambda: task_row(conn, r["id"])["attempts"] == 1 and status_of(conn, r["id"]) == "queued")
        wait_until(lambda: status_of(conn, f["id"]) == "failed")
        wait_until(lambda: status_of(conn, c["id"]) == "failed")
    rr = task_row(conn, r["id"])
    assert rr["error_class"] == "retryable_error" and "temporary failure" in rr["last_error"]
    assert conn.execute("SELECT run_after > now() AS f FROM tasks WHERE id = %s", (r["id"],)).fetchone()["f"]
    ff = task_row(conn, f["id"])
    assert (ff["error_class"], ff["attempts"]) == ("bad_input", 1)
    cc = task_row(conn, c["id"])
    assert cc["error_class"] == "zero_division_error" and "Traceback" in cc["last_error"]


def test_cancel_running_task_kills_sleep_group_via_notify(queue_env: Any, conn: Any, tmp_path: Path) -> None:
    """Критерий M1: отмена из API убивает группу процессов `sleep 60`, задача — cancelled."""
    pid_file = tmp_path / "pid"
    t = enqueue_raw(conn, "wtest_media_sleep", payload={"pid_file": str(pid_file)})
    runner = make_runner(queue_env, heartbeat_interval_sec=60.0)  # только NOTIFY cancel:<id>
    with run_in_thread(runner):
        pgid = int(wait_until(lambda: pid_file.exists() and pid_file.read_text().strip(), timeout=15))
        assert group_alive(pgid)
        started = time.monotonic()
        with connection() as c:
            svc.request_cancel(c, t["id"])
        wait_until(lambda: status_of(conn, t["id"]) == "cancelled", timeout=15)
        assert time.monotonic() - started < 10
        assert not group_alive(pgid)
    row = task_row(conn, t["id"])
    assert row["error_class"] == "cancelled" and row["finished_at"] is not None


def test_cancel_detected_by_heartbeat(queue_env: Any, conn: Any, tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    t = enqueue_raw(conn, "wtest_media_sleep", payload={"pid_file": str(pid_file)})
    runner = make_runner(queue_env, heartbeat_interval_sec=0.3)
    with run_in_thread(runner):
        pgid = int(wait_until(lambda: pid_file.exists() and pid_file.read_text().strip(), timeout=15))
        conn.execute("UPDATE tasks SET cancel_requested = true WHERE id = %s", (t["id"],))  # без NOTIFY
        wait_until(lambda: status_of(conn, t["id"]) == "cancelled", timeout=15)
        assert not group_alive(pgid)


def test_shutdown_requeues_without_attempt(queue_env: Any, conn: Any, tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    t = enqueue_raw(conn, "wtest_media_sleep", payload={"pid_file": str(pid_file)}, max_attempts=2)
    runner = make_runner(queue_env)
    with run_in_thread(runner):
        pgid = int(wait_until(lambda: pid_file.exists() and pid_file.read_text().strip(), timeout=15))
    # run_in_thread остановил воркер (как SIGTERM)
    row = task_row(conn, t["id"])
    assert (row["status"], row["attempts"], row["error_class"], row["locked_by"]) == (
        "queued",
        0,
        "worker_shutdown",
        None,
    )
    assert not group_alive(pgid)


def test_lease_lost_outcome_not_recorded(queue_env: Any, conn: Any, tmp_path: Path) -> None:
    release = tmp_path / "release"
    started = tmp_path / "started"
    t = enqueue_raw(conn, "wtest_block", payload={"release_file": str(release), "started_file": str(started)})
    runner = make_runner(queue_env, heartbeat_interval_sec=0.3)
    with run_in_thread(runner):
        wait_until(started.exists)
        # reaper «забрал» задачу и отдал другому воркеру
        conn.execute("UPDATE tasks SET locked_by = 'other-worker' WHERE id = %s", (t["id"],))
        wait_until(lambda: not runner.active_task_ids(), timeout=10)
    row = task_row(conn, t["id"])
    assert row["status"] == "running" and row["locked_by"] == "other-worker"


def test_job_failure_propagates_from_worker(queue_env: Any, conn: Any, monkeypatch: Any) -> None:
    from autoshorts.tasks import registry
    from autoshorts.tasks.registry import TaskContext, register_task, unregister_task

    # настоящий исполнитель job_stage (tasks/job_stage.py) на время теста подменяется своим
    without_job_stage = {k: v for k, v in registry._registry.items() if k != "job_stage"}
    monkeypatch.setattr(registry, "_registry", without_job_stage)

    @register_task("job_stage", "light", max_attempts=1)
    def boom(ctx: TaskContext) -> None:
        from autoshorts.core.errors import PermanentError

        raise PermanentError("stage exploded", error_class="stage_error")

    try:
        job_id = make_job(conn, status="processing", stage="script")
        with connection() as c:
            res = svc.enqueue_job_stage(c, job_id, "script")
        runner = make_runner(queue_env)
        with run_in_thread(runner):
            wait_until(lambda: status_of(conn, res.task["id"]) == "failed")
        job = conn.execute("SELECT status, error_class, error FROM jobs WHERE id = %s", (job_id,)).fetchone()
        assert (
            job["status"] == "failed"
            and job["error_class"] == "stage_error"
            and "stage exploded" in job["error"]
        )
    finally:
        unregister_task("job_stage")


def test_failed_handler_releases_budget_reservation(queue_env: Any, conn: Any) -> None:
    """Резерв бюджета, не закрытый обработчиком (упал до settle), снимает воркер."""
    from autoshorts.core.errors import RetryableError
    from autoshorts.tasks.registry import TaskContext, register_task, unregister_task

    seen: list[float] = []

    @register_task("wtest_budget_leak", "light", max_attempts=1)
    def leak(ctx: TaskContext) -> None:
        ctx.budget.reserve(0.3, provider="openai_compat")
        seen.append(float(conn.execute("SELECT reserved_usd FROM budget_state").fetchone()["reserved_usd"]))
        raise RetryableError("provider down")

    try:
        t = enqueue_raw(conn, "wtest_budget_leak", max_attempts=1)
        runner = make_runner(queue_env)
        with run_in_thread(runner):
            wait_until(lambda: status_of(conn, t["id"]) == "failed")
        assert seen == [0.3]
        state = conn.execute("SELECT reserved_usd, paused FROM budget_state").fetchone()
        assert float(state["reserved_usd"]) == 0.0 and state["paused"] is False
    finally:
        unregister_task("wtest_budget_leak")


def test_error_while_stopping_requeues_without_attempt(queue_env: Any, conn: Any) -> None:
    """SIGTERM убил подпроцесс раньше, чем _shutdown() отменил задачу: всё равно requeue."""
    from autoshorts.core.errors import RetryableError
    from autoshorts.tasks.registry import TaskContext, register_task, unregister_task

    started: list[int] = []

    @register_task("wtest_killed_on_stop", "light", max_attempts=1)
    def killed(ctx: TaskContext) -> None:
        started.append(ctx.task_id)
        deadline = time.monotonic() + 20
        while not runner.stopping and time.monotonic() < deadline:
            time.sleep(0.005)
        # как ffmpeg, получивший SIGTERM от systemd: ошибка без отмены от воркера
        raise RetryableError("ffmpeg exited with -15")

    try:
        t = enqueue_raw(conn, "wtest_killed_on_stop", max_attempts=1)
        runner = make_runner(queue_env)
        with run_in_thread(runner):
            wait_until(lambda: started)
        row = task_row(conn, t["id"])
        assert (row["status"], row["attempts"], row["error_class"]) == ("queued", 0, "worker_shutdown")
    finally:
        unregister_task("wtest_killed_on_stop")


def test_heavy_admission_defers_without_claim(queue_env: Any, conn: Any) -> None:
    t = enqueue_raw(conn, "wtest_heavy_record", "heavy")
    settings = RuntimeSettings.from_overrides({"heavy_min_mem_available_mb": 10**9})
    runner = make_runner(
        queue_env,
        "heavy",
        admission=True,
        settings=settings,
        worker_id="t-1-heavy",
        heavy_lock_path=Path(queue_env.media_root) / "heavy.lock",
    )
    with run_in_thread(runner):
        wait_until(lambda: task_row(conn, t["id"])["defer_reason"] == "mem_low")
        w = wait_until(lambda: conn.execute("SELECT state FROM workers WHERE id = 't-1-heavy'").fetchone())
        wait_until(
            lambda: (
                conn.execute("SELECT state FROM workers WHERE id = 't-1-heavy'").fetchone()["state"]
                == "deferred:mem_low"
            )
        )
        assert w is not None
        time.sleep(0.5)
    row = task_row(conn, t["id"])
    assert row["status"] == "queued" and row["attempts"] == 0 and row["started_at"] is None


def test_second_heavy_runner_idles_while_first_alive(queue_env: Any, conn: Any, tmp_path: Path) -> None:
    """Критерий M1 (в процессе): второй тяжёлый воркер стоит в standby, пока жив первый.

    Разные файлы flock — держит одна только advisory lock 771001 (как два контейнера на одну БД).
    """
    release = tmp_path / "release"
    started = tmp_path / "started"
    t1 = enqueue_raw(
        conn,
        "wtest_heavy_block",
        "heavy",
        payload={"release_file": str(release), "started_file": str(started)},
    )
    t2 = enqueue_raw(conn, "wtest_heavy_record", "heavy", priority=200)
    a = make_runner(queue_env, "heavy", worker_id="host-a-heavy", heavy_lock_path=tmp_path / "a.lock")
    b = make_runner(queue_env, "heavy", worker_id="host-b-heavy", heavy_lock_path=tmp_path / "b.lock")
    with run_in_thread(a):
        wait_until(started.exists)
        with run_in_thread(b):
            wait_until(
                lambda: (
                    (
                        conn.execute("SELECT state FROM workers WHERE id = 'host-b-heavy'").fetchone() or {}
                    ).get("state")
                    == "standby"
                )
            )
            release.touch()
            wait_until(lambda: status_of(conn, t1["id"]) == "succeeded")
            # A свободен и жив — второй задачей займётся он, B так и стоит в standby
            wait_until(lambda: status_of(conn, t2["id"]) == "succeeded")
            assert task_row(conn, t2["id"])["result"]["worker"] == "host-a-heavy"
            assert (
                conn.execute("SELECT state FROM workers WHERE id = 'host-b-heavy'").fetchone()["state"]
                == "standby"
            )
            t3 = enqueue_raw(conn, "wtest_heavy_record", "heavy")
            a.request_stop()  # первый умер — слот переходит ко второму
            wait_until(lambda: status_of(conn, t3["id"]) == "succeeded", timeout=15)
            assert task_row(conn, t3["id"])["result"]["worker"] in ("host-a-heavy", "host-b-heavy")
            t4 = enqueue_raw(conn, "wtest_heavy_record", "heavy")
            wait_until(lambda: status_of(conn, t4["id"]) == "succeeded", timeout=15)
            assert task_row(conn, t4["id"])["result"]["worker"] == "host-b-heavy"


def test_scheduler_enqueues_known_kinds_and_single_leader(queue_env: Any, conn: Any) -> None:
    from autoshorts.workers.scheduler import Scheduler, next_run_at

    conn.execute("UPDATE schedules SET next_run_at = now() - interval '5 minutes'")
    known = lambda: ["task_reaper", "cleanup"]  # noqa: E731
    s1 = Scheduler(queue_env.database_url, known_kinds=known, tick_sec=0)
    s2 = Scheduler(queue_env.database_url, known_kinds=known, tick_sec=0)
    try:
        created = s1.tick()
        assert s1.is_leader and s2.tick() == [] and not s2.is_leader
        kinds = {
            r["kind"]
            for r in conn.execute("SELECT kind FROM tasks WHERE id = ANY(%s)", (created,)).fetchall()
        }
        assert kinds == {"task_reaper", "cleanup"}
        rows = conn.execute("SELECT dedup_key FROM tasks ORDER BY id").fetchall()
        assert {r["dedup_key"] for r in rows} == {"sched:task_reaper", "sched:cleanup"}
        # все расписания сдвинуты в будущее, в том числе без обработчика
        assert (
            conn.execute("SELECT count(*) AS n FROM schedules WHERE next_run_at <= now()").fetchone()["n"]
            == 0
        )
        reaper = conn.execute("SELECT * FROM schedules WHERE name = 'task_reaper'").fetchone()
        assert reaper["last_task_id"] is not None
        # повторный тик ничего не ставит: не созрело
        assert s1.tick() == []
    finally:
        s1.close()
        s2.close()
    # s1 отпустил лидерство — s2 может стать лидером
    s3 = Scheduler(queue_env.database_url, known_kinds=known, tick_sec=0)
    try:
        s3.tick()
        assert s3.is_leader
    finally:
        s3.close()

    now = datetime(2026, 9, 26, 12, 0, 30, tzinfo=UTC)
    plain = {
        "interval_sec": 60,
        "anchor_local_time": None,
        "next_run_at": now - timedelta(minutes=10, seconds=10),
    }
    assert next_run_at(plain, now, "Europe/Moscow") == now + timedelta(seconds=50)
    daily = {"interval_sec": 86400, "anchor_local_time": dtime(4, 0), "next_run_at": now}
    # 12:00 UTC = 15:00 МСК → следующее 04:00 МСК = 01:00 UTC следующего дня
    assert next_run_at(daily, now, "Europe/Moscow") == datetime(2026, 9, 27, 1, 0, tzinfo=UTC)


def test_light_runner_with_scheduler_runs_task_reaper(queue_env: Any, conn: Any) -> None:
    conn.execute("UPDATE schedules SET next_run_at = now() + interval '1 day'")
    conn.execute("UPDATE schedules SET next_run_at = now() WHERE name = 'task_reaper'")
    runner = make_runner(queue_env, scheduler=True, scheduler_tick_sec=0.1)
    with run_in_thread(runner):
        wait_until(
            lambda: (
                conn.execute(
                    "SELECT count(*) AS n FROM tasks WHERE kind = 'task_reaper' AND status = 'succeeded'"
                ).fetchone()["n"]
                == 1
            )
        )
    row = conn.execute("SELECT result FROM tasks WHERE kind = 'task_reaper'").fetchone()
    assert json.loads(json.dumps(row["result"]))["requeued"] == []


def test_task_runs_even_if_task_log_cannot_be_created(queue_env: Any, conn: Any, tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("file, not a directory")
    t = enqueue_raw(conn, "wtest_record")
    runner = make_runner(queue_env, media_root=blocker / "media")
    with run_in_thread(runner):
        wait_until(lambda: status_of(conn, t["id"]) == "succeeded")


def test_loop_survives_transient_db_error(queue_env: Any, conn: Any, monkeypatch) -> None:
    import psycopg

    from autoshorts.repos import tasks as tasks_repo

    real = tasks_repo.has_claimable
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise psycopg.OperationalError("server closed the connection unexpectedly")
        return real(*args, **kwargs)

    monkeypatch.setattr(tasks_repo, "has_claimable", flaky)
    t = enqueue_raw(conn, "wtest_record")
    runner = make_runner(queue_env)
    with run_in_thread(runner):
        wait_until(lambda: status_of(conn, t["id"]) == "succeeded", timeout=15)
    assert calls["n"] >= 2


def test_commit_hooks_run_in_success_transaction(queue_env: Any, conn: Any) -> None:
    ok = enqueue_raw(conn, "wtest_commit")
    bad = enqueue_raw(conn, "wtest_commit", payload={"hook_error": "permanent"})
    flaky = enqueue_raw(conn, "wtest_commit", payload={"hook_error": "retry"}, max_attempts=2)
    runner = make_runner(queue_env, concurrency=3)
    with run_in_thread(runner):
        wait_until(lambda: status_of(conn, ok["id"]) == "succeeded")
        wait_until(lambda: status_of(conn, bad["id"]) == "failed")
        wait_until(
            lambda: task_row(conn, flaky["id"])["attempts"] == 1 and status_of(conn, flaky["id"]) == "queued"
        )

    def follow(task_id: int) -> int:
        q = "SELECT count(*) AS n FROM tasks WHERE dedup_key = %s"
        return conn.execute(q, (f"follow:{task_id}",)).fetchone()["n"]

    assert follow(ok["id"]) == 1  # хук выполнен вместе с succeeded
    assert follow(bad["id"]) == 0 and follow(flaky["id"]) == 0  # откат вместе с succeeded
    b = task_row(conn, bad["id"])
    assert (b["error_class"], b["result"]) == ("hook_rejected", None)
    assert task_row(conn, flaky["id"])["error_class"] == "retryable_error"
