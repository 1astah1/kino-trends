"""Очередь на PostgreSQL: постановка, dedup, взятие (SKIP LOCKED), исходы, reaper, отмена."""

from __future__ import annotations

import random
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

from autoshorts.db.notify import Listener, Notification
from autoshorts.db.pool import connection
from autoshorts.repos import artifacts as artifacts_repo
from autoshorts.repos import schedules as schedules_repo
from autoshorts.repos import tasks as tasks_repo
from autoshorts.repos import workers as workers_repo
from autoshorts.services import tasks as svc
from tests.worker.conftest import enqueue_raw, make_job, task_row


def test_enqueue_defaults_dedup_and_wake(queue_env: Any, conn: Any) -> None:
    with Listener(["as_tasks"], queue_env.database_url) as listener:
        with connection() as c:
            first = svc.enqueue(c, "task_reaper", dedup_key="sched:x")
        assert first.created
        t = first.task
        assert (t["resource_class"], t["max_attempts"], t["priority"], t["status"]) == (
            "light",
            5,
            200,
            "queued",
        )
        notes = listener.wait(5)
        assert any(isinstance(n, Notification) and n.payload == "light" for n in notes)
    with connection() as c:
        dup = svc.enqueue(c, "task_reaper", dedup_key="sched:x")
    assert not dup.created and dup.task["id"] == t["id"]
    # после завершения тот же ключ снова свободен
    conn.execute("UPDATE tasks SET status = 'succeeded', finished_at = now() WHERE id = %s", (t["id"],))
    with connection() as c:
        again = svc.enqueue(c, "task_reaper", dedup_key="sched:x")
    assert again.created and again.task["id"] != t["id"]
    with pytest.raises(ValueError), connection() as c:
        svc.enqueue(c, "no_such_kind_without_class")


def test_enqueue_job_stage_uses_stage_table(queue_env: Any, conn: Any) -> None:
    job_id = make_job(conn)
    with connection() as c:
        r = svc.enqueue_job_stage(c, job_id, "render")
        dup = svc.enqueue_job_stage(c, job_id, "render")
        v = svc.enqueue_job_stage(c, job_id, "voice", rev=1, payload={"a": 1})
    assert r.created and not dup.created
    assert (r.task["kind"], r.task["resource_class"], r.task["priority"], r.task["max_attempts"]) == (
        "job_stage",
        "heavy",
        30,
        2,
    )
    assert r.task["dedup_key"] == f"job:{job_id}:render:0"
    assert (v.task["resource_class"], v.task["priority"], v.task["payload"]) == ("light", 50, {"a": 1})


def test_claim_order_kinds_filter_and_skip_locked(queue_env: Any, conn: Any) -> None:
    low = enqueue_raw(conn, "wtest_record", priority=100)
    high = enqueue_raw(conn, "wtest_record", priority=10)
    later = enqueue_raw(conn, "wtest_record", priority=1)
    conn.execute("UPDATE tasks SET run_after = now() + interval '1 hour' WHERE id = %s", (later["id"],))
    other_kind = enqueue_raw(conn, "not_registered_kind", priority=0)
    heavy = enqueue_raw(conn, "wtest_record", "heavy", priority=0)

    kinds = ["wtest_record"]
    c1 = psycopg.connect(queue_env.database_url, row_factory=dict_row)
    c2 = psycopg.connect(queue_env.database_url, row_factory=dict_row)
    try:
        a = tasks_repo.claim_next(c1, "light", "w1", kinds)  # транзакция c1 держит строку
        b = tasks_repo.claim_next(c2, "light", "w2", kinds)  # SKIP LOCKED: берёт следующую
        assert a["id"] == high["id"] and b["id"] == low["id"]
        assert a["attempts"] == 1 and a["locked_by"] == "w1" and a["status"] == "running"
        c1.commit()
        c2.commit()
        assert tasks_repo.claim_next(c1, "light", "w1", kinds) is None  # later ещё не созрела
        c1.commit()
    finally:
        c1.close()
        c2.close()
    assert task_row(conn, other_kind["id"])["status"] == "queued"  # чужой вид не взят
    assert task_row(conn, heavy["id"])["status"] == "queued"  # чужой класс не взят
    assert tasks_repo.has_claimable(conn, "light", ["not_registered_kind"])
    assert tasks_repo.count_pending(conn, "light", kinds) == 1  # later


def claim(conn_url: str, cls: str = "light", worker: str = "w1", kinds: list[str] | None = None) -> dict:
    with connection() as c:
        row = svc.claim(c, cls, worker, kinds or ["wtest_record", "wtest_defer", "wtest_retry", "job_stage"])
    assert row is not None
    return row


def test_resource_deferred_keeps_attempt(queue_env: Any, conn: Any) -> None:
    """Критерий M1: ResourceDeferred не тратит попытку."""
    t = enqueue_raw(conn, "wtest_defer", max_attempts=3)
    row = claim(queue_env.database_url)
    assert row["attempts"] == 1
    with connection() as c:
        out = svc.defer(c, row, "w1", "budget_exhausted", 120)
    assert out is not None
    after = task_row(conn, t["id"])
    assert (
        after["status"] == "queued" and after["attempts"] == 0 and after["defer_reason"] == "budget_exhausted"
    )
    assert after["locked_by"] is None
    delta = conn.execute("SELECT run_after - now() AS d FROM tasks WHERE id = %s", (t["id"],)).fetchone()["d"]
    assert delta.total_seconds() > 100
    # следующее взятие снова первая попытка, причина сброшена
    conn.execute("UPDATE tasks SET run_after = now() WHERE id = %s", (t["id"],))
    again = claim(queue_env.database_url)
    assert again["attempts"] == 1 and again["defer_reason"] is None


def test_retry_then_final_failure_fails_job(queue_env: Any, conn: Any) -> None:
    job_id = make_job(conn, status="processing", stage="voice")
    t = enqueue_raw(conn, "job_stage", job_id=job_id, stage="voice", max_attempts=2)
    row = claim(queue_env.database_url)
    with connection() as c:
        assert svc.retry_later(c, row, "w1", 30, "retryable_error", "net") is not None
    r = task_row(conn, t["id"])
    assert (r["status"], r["attempts"], r["error_class"]) == ("queued", 1, "retryable_error")
    conn.execute("UPDATE tasks SET run_after = now() WHERE id = %s", (t["id"],))
    row = claim(queue_env.database_url)
    assert row["attempts"] == 2
    with Listener(["as_events"], queue_env.database_url) as listener:
        with connection() as c:
            assert svc.fail(c, row, "w1", "retryable_error", "x" * 10000) is not None
        notes = listener.wait(5)
    r = task_row(conn, t["id"])
    assert r["status"] == "failed" and r["finished_at"] is not None and len(r["last_error"]) <= 4000
    job = conn.execute("SELECT status, error_class FROM jobs WHERE id = %s", (job_id,)).fetchone()
    assert job == {"status": "failed", "error_class": "retryable_error"}
    assert any(
        '"job_status"' in n.payload and '"failed"' in n.payload for n in notes if isinstance(n, Notification)
    )


def test_outcome_after_lost_lease_is_noop(queue_env: Any, conn: Any) -> None:
    enqueue_raw(conn, "wtest_record")
    row = claim(queue_env.database_url, worker="w1")
    conn.execute("UPDATE tasks SET locked_by = 'someone-else' WHERE id = %s", (row["id"],))
    with connection() as c:
        assert svc.complete(c, row, "w1", {"x": 1}) is None
        assert tasks_repo.heartbeat(c, row["id"], "w1") is None
    assert task_row(conn, row["id"])["status"] == "running"


def test_reaper_returns_stale_task(queue_env: Any, conn: Any) -> None:
    """Критерий M1: reaper возвращает задачу с протухшим heartbeat."""
    t = enqueue_raw(conn, "wtest_record", max_attempts=3)
    fresh = enqueue_raw(conn, "wtest_record", max_attempts=3)
    row = claim(queue_env.database_url, worker="dead-worker")
    live = claim(queue_env.database_url, worker="live-worker")
    assert row["id"] == t["id"] and live["id"] == fresh["id"]
    conn.execute("UPDATE tasks SET heartbeat_at = now() - interval '10 minutes' WHERE id = %s", (t["id"],))
    with connection() as c:
        report = svc.reap_stale_tasks(c, rng=random.Random(0))
    assert report.requeued == [t["id"]] and report.failed == []
    r = task_row(conn, t["id"])
    assert (r["status"], r["attempts"], r["error_class"], r["locked_by"]) == (
        "queued",
        1,
        "worker_lost",
        None,
    )
    assert conn.execute("SELECT run_after > now() AS f FROM tasks WHERE id = %s", (t["id"],)).fetchone()["f"]
    assert task_row(conn, fresh["id"])["status"] == "running"  # живую не трогает


def test_reaper_fails_exhausted_task_and_job(queue_env: Any, conn: Any) -> None:
    job_id = make_job(conn, status="processing", stage="render")
    t = enqueue_raw(conn, "job_stage", "heavy", job_id=job_id, stage="render", max_attempts=1)
    claim(queue_env.database_url, cls="heavy", worker="dead")
    conn.execute("UPDATE tasks SET heartbeat_at = now() - interval '4 minutes' WHERE id = %s", (t["id"],))
    from autoshorts.tasks.registry import get_task_spec, load_all

    load_all()
    report = get_task_spec("task_reaper").handler(_FakeCtx())  # обработчик расписания task_reaper
    assert report["failed"] == [t["id"]]
    r = task_row(conn, t["id"])
    assert (r["status"], r["error_class"]) == ("failed", "worker_lost")
    job = conn.execute("SELECT status, error_class FROM jobs WHERE id = %s", (job_id,)).fetchone()
    assert job == {"status": "failed", "error_class": "worker_lost"}


class _FakeCtx:
    payload: dict[str, Any] = {}


def test_request_cancel_and_retry(queue_env: Any, conn: Any) -> None:
    q = enqueue_raw(conn, "wtest_record")
    with connection() as c:
        out = svc.request_cancel(c, q["id"])
    assert out["status"] == "cancelled"
    enqueue_raw(conn, "wtest_record")
    running = claim(queue_env.database_url)
    with Listener(["as_tasks"], queue_env.database_url) as listener:
        with connection() as c:
            out = svc.request_cancel(c, running["id"])
        notes = listener.wait(5)
    assert out["status"] == "running" and out["cancel_requested"] is True
    assert any(n.payload == f"cancel:{running['id']}" for n in notes if isinstance(n, Notification))
    with pytest.raises(svc.TaskStateConflict), connection() as c:
        svc.request_cancel(c, q["id"])  # уже cancelled
    with pytest.raises(svc.NotFound) as nf, connection() as c:
        svc.request_cancel(c, 999999)
    assert nf.value.code == "task_not_found"

    # повтор: cancelled -> queued с новой серией попыток
    with connection() as c:
        again = svc.retry_task(c, q["id"])
    assert (again["status"], again["attempts"], again["cancel_requested"]) == ("queued", 0, False)
    with pytest.raises(svc.TaskStateConflict), connection() as c:
        svc.retry_task(c, q["id"])  # queued нельзя повторить


def test_retry_failed_stage_revives_job_and_respects_dedup(queue_env: Any, conn: Any) -> None:
    job_id = make_job(conn, status="failed", stage="voice")
    failed = enqueue_raw(conn, "job_stage", job_id=job_id, stage="voice", dedup_key=f"job:{job_id}:voice:0")
    conn.execute("UPDATE tasks SET status = 'failed', attempts = 3 WHERE id = %s", (failed["id"],))
    with connection() as c:
        row = svc.retry_task(c, failed["id"])
    assert row["status"] == "queued" and row["attempts"] == 0
    job = conn.execute("SELECT status, stage FROM jobs WHERE id = %s", (job_id,)).fetchone()
    assert job == {"status": "processing", "stage": "voice"}
    # активный дубль по dedup_key — 409
    conn.execute("UPDATE tasks SET status = 'failed' WHERE id = %s", (failed["id"],))
    enqueue_raw(conn, "job_stage", job_id=job_id, stage="voice", dedup_key=f"job:{job_id}:voice:0")
    with pytest.raises(svc.TaskStateConflict), connection() as c:
        svc.retry_task(c, failed["id"])


def test_status_helpers(queue_env: Any, conn: Any) -> None:
    enqueue_raw(conn, "wtest_record")
    enqueue_raw(conn, "wtest_record", defer_reason="mem_low")
    h = enqueue_raw(conn, "job_stage", "heavy", stage="render")
    heavy = claim(queue_env.database_url, cls="heavy", worker="host-1-heavy")
    assert heavy["id"] == h["id"]
    conn.execute("UPDATE tasks SET progress = 42.5 WHERE id = %s", (h["id"],))
    with connection() as c:
        counters = svc.queue_counters(c)
        slot = svc.heavy_slot(c)
        assert tasks_repo.mark_waiting_deferred(c, "light", "disk_low") == 2
        assert tasks_repo.clear_waiting_deferred(c, "light", ["disk_low"]) == 2
    assert counters == {
        "heavy": {"queued": 0, "running": 1, "deferred": 0},
        "light": {"queued": 1, "running": 0, "deferred": 1},
    }
    assert slot == {"owner": "host-1-heavy", "task_id": h["id"], "stage": "render", "pct": 42.5}
    rows, total = tasks_repo.list_tasks(conn, statuses=["queued"], kinds=["wtest_record"], limit=1)
    assert total == 2 and len(rows) == 1


def test_workers_schedules_artifacts_repos(queue_env: Any, conn: Any) -> None:
    w = workers_repo.upsert(
        conn, worker_id="h-1-light", resource_class="light", hostname="h", pid=1, version="t"
    )
    assert w["state"] == "idle"
    hb = workers_repo.heartbeat(conn, "h-1-light", state="busy", current_task_id=5)
    assert (hb["state"], hb["prev_state"], hb["current_task_id"]) == ("busy", "idle", 5)
    conn.execute("UPDATE workers SET heartbeat_at = now() - interval '2 days' WHERE id = 'h-1-light'")
    assert workers_repo.prune_stale(conn, 86400) == 1

    names = [s["name"] for s in schedules_repo.list_all(conn)]
    assert "task_reaper" in names and "cleanup" in names
    s = schedules_repo.update(conn, "task_reaper", enabled=False, interval_sec=120)
    assert (s["enabled"], s["interval_sec"]) == (False, 120)
    conn.execute("UPDATE schedules SET next_run_at = now() - interval '1 day' WHERE name = 'task_reaper'")
    s = schedules_repo.update(conn, "task_reaper", enabled=True)
    assert (
        s["enabled"]
        and conn.execute(
            "SELECT next_run_at >= now() - interval '1 second' AS ok"
            " FROM schedules WHERE name = 'task_reaper'"
        ).fetchone()["ok"]
    )
    assert schedules_repo.update(conn, "nope_schedule", enabled=True) is None

    job_id = make_job(conn)
    a1 = artifacts_repo.insert(
        conn, job_id=job_id, kind="final", path="jobs/tjob1/final.mp4", input_hash="h1", stage="render"
    )
    a2 = artifacts_repo.insert(
        conn,
        job_id=job_id,
        kind="final",
        path="jobs/tjob1/final2.mp4",
        input_hash="h1",
        stage="render",
        bytes_=10,
    )
    assert a1["id"] == a2["id"] and a2["path"] == "jobs/tjob1/final2.mp4"  # тот же вход — upsert
    assert artifacts_repo.find_live(conn, job_id, "final", "h1")["id"] == a1["id"]
    assert artifacts_repo.soft_delete_stages(conn, job_id, ["render"]) == 1
    assert artifacts_repo.find_live(conn, job_id, "final", "h1") is None
    assert [r["id"] for r in artifacts_repo.list_deleted(conn)] == [a1["id"]]
