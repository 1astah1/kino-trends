"""Настоящие процессы `python -m autoshorts.workers.main` на тестовой базе (критерии M1-C)."""

from __future__ import annotations

import os
import signal
import time
from collections import Counter
from pathlib import Path
from typing import Any

from autoshorts.db.pool import connection
from autoshorts.services import tasks as svc
from tests.worker.conftest import enqueue_raw, task_row, wait_until

TASKS = 200


def worker_state(conn: Any, worker_id: str) -> str | None:
    row = conn.execute("SELECT state FROM workers WHERE id = %s", (worker_id,)).fetchone()
    return row["state"] if row else None


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def test_two_processes_200_tasks_no_double_claim(
    queue_env: Any, conn: Any, tmp_path: Path, spawn_worker
) -> None:
    """Критерий M1: 2 процесса × 200 задач без двойного захвата."""
    out = tmp_path / "exec.txt"
    conn.execute(
        """
        INSERT INTO tasks (kind, resource_class, payload, run_after, max_attempts)
        SELECT 'wtest_record', 'light', jsonb_build_object('file', CAST(%(f)s AS text), 'sleep_ms', 5),
               now() + interval '3 seconds', 1
        FROM generate_series(1, %(n)s)
        """,
        {"f": str(out), "n": TASKS},
    )
    procs = [
        spawn_worker("light", "--concurrency", "2", "--exit-when-idle", "--worker-id", f"mp-{i}-light")
        for i in range(2)
    ]
    for p in procs:
        assert p.wait(120) == 0
    lines = out.read_text().splitlines()
    ids = [int(line.split()[0]) for line in lines]
    per_worker = Counter(line.split()[1] for line in lines)
    assert len(lines) == TASKS, "каждая задача исполнена ровно один раз"
    assert len(set(ids)) == TASKS
    assert set(per_worker) == {"mp-0-light", "mp-1-light"}, per_worker  # работали оба процесса
    pids = {line.split()[2] for line in lines}
    assert len(pids) == 2
    stats = conn.execute(
        "SELECT status, attempts, count(*) AS n FROM tasks WHERE kind = 'wtest_record' GROUP BY 1, 2"
    ).fetchall()
    assert stats == [{"status": "succeeded", "attempts": 1, "n": TASKS}]
    assert conn.execute("SELECT count(*) AS n FROM workers").fetchone()["n"] == 0  # оба вышли штатно


def test_second_heavy_process_idles_while_first_alive(
    queue_env: Any, conn: Any, tmp_path: Path, spawn_worker
) -> None:
    """Критерий M1: второй heavy-воркер не берёт задачи, пока жив первый (общий flock-файл + advisory)."""
    lock = tmp_path / "heavy.lock"
    release, started = tmp_path / "release", tmp_path / "started"
    first = enqueue_raw(
        conn,
        "wtest_heavy_block",
        "heavy",
        payload={"release_file": str(release), "started_file": str(started)},
    )
    a = spawn_worker("heavy", "--lock-path", str(lock), "--worker-id", "hp-a-heavy", log_name="a.log")
    wait_until(started.exists, timeout=30)
    b = spawn_worker("heavy", "--lock-path", str(lock), "--worker-id", "hp-b-heavy", log_name="b.log")
    wait_until(lambda: worker_state(conn, "hp-b-heavy") == "standby", timeout=30)
    second = enqueue_raw(conn, "wtest_heavy_record", "heavy")
    time.sleep(2.0)  # B жив, в очереди есть задача, слот занят A
    assert task_row(conn, second["id"])["status"] == "queued"
    assert worker_state(conn, "hp-b-heavy") == "standby"

    a.send_signal(signal.SIGTERM)  # первый умирает посреди задачи: она вернётся в очередь
    assert a.wait(30) == 0
    wait_until(lambda: task_row(conn, second["id"])["status"] == "succeeded", timeout=30)
    assert task_row(conn, second["id"])["result"]["worker"] == "hp-b-heavy"
    release.touch()
    wait_until(lambda: task_row(conn, first["id"])["status"] == "succeeded", timeout=30)
    row = task_row(conn, first["id"])
    assert row["result"]["worker"] == "hp-b-heavy" and row["attempts"] == 1  # остановка A не съела попытку
    b.send_signal(signal.SIGTERM)
    assert b.wait(30) == 0


def test_killed_worker_task_is_reaped_and_finished_elsewhere(
    queue_env: Any, conn: Any, tmp_path: Path, spawn_worker
) -> None:
    """kill -9 посреди задачи → heartbeat протух → reaper возвращает → другой воркер доделывает."""
    release, started = tmp_path / "release", tmp_path / "started"
    t = enqueue_raw(
        conn,
        "wtest_block",
        payload={"release_file": str(release), "started_file": str(started)},
        max_attempts=3,
    )
    victim = spawn_worker("light", "--worker-id", "kill-me-light", log_name="victim.log")
    wait_until(started.exists, timeout=30)
    victim.send_signal(signal.SIGKILL)
    victim.wait(10)
    assert task_row(conn, t["id"])["status"] == "running"
    time.sleep(1.2)
    with connection() as c:
        report = svc.reap_stale_tasks(c, stale_after_sec=1.0)
    assert t["id"] in report.requeued
    row = task_row(conn, t["id"])
    assert (row["status"], row["attempts"], row["error_class"]) == ("queued", 1, "worker_lost")
    conn.execute("UPDATE tasks SET run_after = now() WHERE id = %s", (t["id"],))  # не ждать backoff
    release.touch()
    rescuer = spawn_worker(
        "light", "--worker-id", "rescuer-light", "--exit-when-idle", log_name="rescuer.log"
    )
    assert rescuer.wait(60) == 0
    row = task_row(conn, t["id"])
    assert (
        row["status"] == "succeeded" and row["attempts"] == 2 and row["result"]["worker"] == "rescuer-light"
    )


def test_cancel_from_api_kills_group_in_worker_process(
    queue_env: Any, conn: Any, tmp_path: Path, spawn_worker
) -> None:
    """Критерий M1 (процесс воркера): отмена убивает группу процессов `sleep 60`."""
    pid_file = tmp_path / "pid"
    t = enqueue_raw(conn, "wtest_media_sleep", payload={"pid_file": str(pid_file)})
    worker = spawn_worker("light", "--worker-id", "cancel-light")
    pgid = int(wait_until(lambda: pid_file.exists() and pid_file.read_text().strip(), timeout=30))
    assert group_alive(pgid)
    with connection() as c:
        svc.request_cancel(c, t["id"])
    wait_until(lambda: task_row(conn, t["id"])["status"] == "cancelled", timeout=20)
    wait_until(lambda: not group_alive(pgid), timeout=10)
    assert worker.poll() is None  # воркер жив и работает дальше
    worker.send_signal(signal.SIGTERM)
    assert worker.wait(30) == 0


def test_sigterm_requeues_running_task_and_kills_group(
    queue_env: Any, conn: Any, tmp_path: Path, spawn_worker
) -> None:
    pid_file = tmp_path / "pid"
    t = enqueue_raw(conn, "wtest_media_sleep", payload={"pid_file": str(pid_file)}, max_attempts=2)
    worker = spawn_worker("light", "--worker-id", "term-light")
    pgid = int(wait_until(lambda: pid_file.exists() and pid_file.read_text().strip(), timeout=30))
    worker.send_signal(signal.SIGTERM)
    assert worker.wait(30) == 0
    row = task_row(conn, t["id"])
    assert (row["status"], row["attempts"], row["error_class"]) == ("queued", 0, "worker_shutdown")
    assert not group_alive(pgid)


def test_worker_refuses_to_start_on_pending_migrations(
    queue_env: Any, conn: Any, make_database, spawn_worker, tmp_path: Path
) -> None:
    import subprocess
    import sys

    empty = make_database("worker_pending")
    env = dict(os.environ, DATABASE_URL=empty, MEDIA_ROOT=str(tmp_path / "m"), APP_ENV="test")
    proc = subprocess.run(
        [sys.executable, "-m", "autoshorts.workers.main", "--class", "light"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        timeout=60,
    )
    assert proc.returncode == 3, proc.stderr.decode()[-2000:]
