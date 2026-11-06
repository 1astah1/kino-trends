"""Фикстуры тестов очереди и воркера (M1-C).

* `queue_env` — окружение процесса (tmp MEDIA_ROOT, чистая тестовая БД, пул `db.pool`).
* `enqueue_raw(conn, kind, cls, **cols)` — вставить задачу SQL-ом в обход реестра.
* `run_in_thread(runner)` — запустить `Runner` в потоке и остановить в конце.
* `spawn_worker(...)` — подпроцесс `python -m autoshorts.workers.main` на тестовой базе.
* `wait_until(pred, timeout)`.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def wait_until(pred: Callable[[], Any], timeout: float = 10.0, interval: float = 0.05) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = pred()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"condition not met in {timeout}s (last={last!r})")


@pytest.fixture(scope="session", autouse=True)
def _register_test_kinds() -> Iterator[None]:
    import tests.worker.task_fixtures as fixtures  # noqa: F401 - регистрация wtest_*
    from autoshorts.tasks.registry import load_all

    load_all(strict=True)  # как воркер при старте: task_reaper, cleanup, стадии
    yield
    from autoshorts.tasks.registry import unregister_task

    for kind in fixtures.TEST_KINDS:
        unregister_task(kind)


@pytest.fixture
def queue_env(app_env: Any) -> Iterator[Any]:
    """env_settings + чистая тестовая БД + пул процесса на ней."""
    from autoshorts.db.pool import close_pool, init_pool

    init_pool(app_env.database_url, min_size=1, max_size=8, application_name="autoshorts-worker-tests")
    try:
        yield app_env
    finally:
        close_pool()


@pytest.fixture
def conn(queue_env: Any) -> Iterator[Any]:
    """Отдельное autocommit-соединение теста (dict_row, UTC)."""
    import psycopg
    from psycopg.rows import dict_row

    c = psycopg.connect(queue_env.database_url, autocommit=True, row_factory=dict_row)
    c.execute("SET TIME ZONE 'UTC'")
    try:
        yield c
    finally:
        c.close()


def enqueue_raw(conn: Any, kind: str, resource_class: str = "light", **cols: Any) -> dict[str, Any]:
    from psycopg.types.json import Jsonb

    cols = dict(cols)
    if "payload" in cols:
        cols["payload"] = Jsonb(cols["payload"])
    names = ["kind", "resource_class", *cols]
    params = {"kind": kind, "resource_class": resource_class, **cols}
    sql = (
        f"INSERT INTO tasks ({', '.join(names)}) VALUES ({', '.join(f'%({n})s' for n in names)}) RETURNING *"
    )
    return conn.execute(sql, params).fetchone()


def task_row(conn: Any, task_id: int) -> dict[str, Any]:
    return conn.execute("SELECT * FROM tasks WHERE id = %s", (task_id,)).fetchone()


def make_job(
    conn: Any, *, status: str = "processing", stage: str | None = "voice", uid: str = "tjob1"
) -> int:
    """Минимальный job для тестов связи task→job (канал и ниша создаются при нужде)."""
    conn.execute("INSERT INTO niches (id, label) VALUES ('tniche', 'Тест') ON CONFLICT DO NOTHING")
    conn.execute("INSERT INTO clusters (id, title) VALUES ('tcluster', 'Тест') ON CONFLICT DO NOTHING")
    conn.execute(
        """INSERT INTO channels (id, niche_id, platform, cluster)
           VALUES ('tchan', 'tniche', 'youtube', 'tcluster') ON CONFLICT DO NOTHING"""
    )
    row = conn.execute(
        """INSERT INTO jobs (video_uid, channel_id, niche_id, template_version, status, stage)
           VALUES (%s, 'tchan', 'tniche', 'v1', %s, %s) RETURNING id""",
        (uid, status, stage),
    ).fetchone()
    return int(row["id"])


@contextlib.contextmanager
def run_in_thread(runner: Any, *, join_timeout: float = 30.0) -> Iterator[threading.Thread]:
    thread = threading.Thread(target=runner.run, name=f"runner-{runner.worker_id}", daemon=True)
    thread.start()
    try:
        yield thread
    finally:
        runner.request_stop()
        thread.join(join_timeout)
        assert not thread.is_alive(), "runner did not stop"


def make_runner(queue_env: Any, resource_class: str = "light", **overrides: Any) -> Any:
    from autoshorts.domain.settings_schema import RuntimeSettings
    from autoshorts.workers.runner import Runner, RunnerConfig

    settings = overrides.pop("settings", None) or RuntimeSettings()
    defaults: dict[str, Any] = {
        "resource_class": resource_class,
        "conninfo": queue_env.database_url,
        "poll_interval_sec": 0.2,
        "heartbeat_interval_sec": 0.5,
        "progress_interval_sec": 0.1,
        "worker_heartbeat_sec": 0.5,
        "standby_retry_sec": 0.2,
        "shutdown_grace_sec": 5.0,
        "kill_grace_sec": 1.0,
        "admission": False,
        "scheduler": False,
        "media_root": Path(queue_env.media_root),
    }
    defaults.update(overrides)
    return Runner(RunnerConfig(**defaults), settings_provider=lambda: settings)


@pytest.fixture
def spawn_worker(queue_env: Any, tmp_path: Path) -> Iterator[Callable[..., subprocess.Popen[bytes]]]:
    """Фабрика подпроцессов воркера; все процессы гасятся в конце теста."""
    procs: list[subprocess.Popen[bytes]] = []

    def factory(resource_class: str, *extra: str, log_name: str | None = None) -> subprocess.Popen[bytes]:
        env = dict(os.environ)
        env.update(
            {
                "DATABASE_URL": queue_env.database_url,
                "MEDIA_ROOT": str(queue_env.media_root),
                "APP_ENV": "test",
                "LOG_FORMAT": "text",
                "LOG_LEVEL": "INFO",
                "PYTHONUNBUFFERED": "1",
            }
        )
        cmd = [
            sys.executable,
            "-m",
            "autoshorts.workers.main",
            "--class",
            resource_class,
            "--import",
            "tests.worker.task_fixtures",
            "--poll-interval",
            "0.2",
            "--heartbeat-interval",
            "0.5",
            "--progress-interval",
            "0.2",
            "--standby-retry",
            "0.2",
            "--shutdown-grace",
            "5",
            "--no-scheduler",
            "--no-admission",
            *extra,
        ]
        log_file = open(tmp_path / (log_name or f"worker-{len(procs)}.log"), "wb")  # noqa: SIM115
        proc = subprocess.Popen(cmd, cwd=PROJECT_ROOT, env=env, stdout=log_file, stderr=subprocess.STDOUT)
        log_file.close()
        procs.append(proc)
        return proc

    try:
        yield factory
    finally:
        for p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
        for p in procs:
            try:
                p.wait(15)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(5)
