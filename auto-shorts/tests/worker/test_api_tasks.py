"""API задач, воркеров и расписаний (роутеры M1-C) поверх тестовой БД.

Права подменяются через `app.dependency_overrides` на объектах зависимостей роутера
(`require_viewer` / `require_operator` / `require_admin`); без подмены работает слой доступа
M1-B, и запрос без сессии получает 401.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from autoshorts.api.routers import tasks as tasks_router
from tests.worker.conftest import enqueue_raw, task_row

API = "/api/v1"


@pytest.fixture
def client(app_env: Any) -> Iterator[TestClient]:
    from autoshorts.api.app import create_app

    app = create_app()
    for dep in (tasks_router.require_viewer, tasks_router.require_operator, tasks_router.require_admin):
        app.dependency_overrides[dep] = lambda: None
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db(app_env: Any) -> Iterator[Any]:
    import psycopg
    from psycopg.rows import dict_row

    c = psycopg.connect(app_env.database_url, autocommit=True, row_factory=dict_row)
    try:
        yield c
    finally:
        c.close()


def test_requires_auth_without_override(app_env: Any) -> None:
    from autoshorts.api.app import create_app

    with TestClient(create_app()) as c:
        for method, path in [
            ("GET", "/tasks"),
            ("GET", "/tasks/1"),
            ("POST", "/tasks/1/cancel"),
            ("GET", "/workers"),
            ("GET", "/schedules"),
        ]:
            r = c.request(method, API + path)
            assert r.status_code == 401, (path, r.status_code, r.text)
            assert r.json()["error"]["code"] == "not_authenticated"


def test_list_get_filters_and_log_tail(client: TestClient, db: Any, app_env: Any) -> None:
    a = enqueue_raw(db, "wtest_record", payload={"x": 1})
    b = enqueue_raw(db, "job_stage", "heavy", stage="render")
    db.execute(
        "UPDATE tasks SET status = 'failed', progress = 12.5, error_class = 'x' WHERE id = %s", (b["id"],)
    )
    r = client.get(f"{API}/tasks", params={"status": ["queued", "failed"], "limit": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2 and body["limit"] == 1 and len(body["items"]) == 1
    r = client.get(f"{API}/tasks", params={"resource_class": "heavy"})
    item = r.json()["items"][0]
    assert item["id"] == b["id"] and item["progress"] == 12.5 and item["stage"] == "render"
    assert client.get(f"{API}/tasks", params={"kind": "wtest_record"}).json()["total"] == 1
    assert client.get(f"{API}/tasks", params={"status": "bogus"}).status_code == 422

    log = Path(app_env.media_root) / "logs" / "tasks" / f"{a['id']}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join(f"line {i}" for i in range(300)) + "\n")
    r = client.get(f"{API}/tasks/{a['id']}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["task"]["payload"] == {"x": 1} and len(detail["log_tail"]) == 200
    assert detail["log_tail"][-1] == "line 299"
    r = client.get(f"{API}/tasks/999999")
    assert r.status_code == 404 and r.json()["error"]["code"] == "task_not_found"


def test_cancel_and_retry_endpoints(client: TestClient, db: Any) -> None:
    t = enqueue_raw(db, "wtest_record")
    r = client.post(f"{API}/tasks/{t['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    r = client.post(f"{API}/tasks/{t['id']}/cancel")
    assert r.status_code == 409 and r.json()["error"]["code"] == "task_state_conflict"
    r = client.post(f"{API}/tasks/{t['id']}/retry")
    assert r.status_code == 200 and r.json()["status"] == "queued" and r.json()["attempts"] == 0
    assert client.post(f"{API}/tasks/424242/retry").status_code == 404
    audit = db.execute("SELECT action, entity_id FROM audit_log ORDER BY id").fetchall()
    assert [a["action"] for a in audit] == ["task.cancel", "task.retry"]
    assert audit[0]["entity_id"] == str(t["id"])


def test_workers_and_schedules(client: TestClient, db: Any) -> None:
    db.execute(
        "INSERT INTO workers (id, resource_class, hostname, pid, state)"
        " VALUES ('h-9-heavy', 'heavy', 'h', 9, 'standby')"
    )
    r = client.get(f"{API}/workers")
    assert r.status_code == 200
    assert r.json()["items"][0]["id"] == "h-9-heavy" and r.json()["items"][0]["state"] == "standby"

    r = client.get(f"{API}/schedules")
    items = {s["name"]: s for s in r.json()["items"]}
    assert items["cleanup"]["anchor_local_time"] == "04:00" and items["task_reaper"]["interval_sec"] == 60
    r = client.patch(f"{API}/schedules/metrics_poll", json={"enabled": False, "interval_sec": 600})
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["interval_sec"] == 600
    assert client.patch(f"{API}/schedules/metrics_poll", json={"interval_sec": 5}).status_code == 422
    assert client.patch(f"{API}/schedules/metrics_poll", json={"bogus": 1}).status_code == 422
    r = client.patch(f"{API}/schedules/no_such_schedule", json={"enabled": True})
    assert r.status_code == 404 and r.json()["error"]["code"] == "schedule_not_found"
    assert client.patch(f"{API}/schedules/Кириллица", json={"enabled": True}).status_code in (404, 422)
    assert (
        db.execute("SELECT count(*) AS n FROM audit_log WHERE action = 'schedule.update'").fetchone()["n"]
        == 1
    )


def test_result_file_and_log_stream(client: TestClient, db: Any, app_env: Any) -> None:
    media = Path(app_env.media_root)
    (media / "tmp").mkdir(parents=True, exist_ok=True)
    (media / "tmp" / "sample.mp3").write_bytes(b"ID3data")
    t = enqueue_raw(db, "voice_sample")
    assert client.get(f"{API}/tasks/{t['id']}/result-file").status_code == 404  # ещё не готово
    from psycopg.types.json import Jsonb

    db.execute(
        "UPDATE tasks SET status = 'succeeded', finished_at = now(), result = %s WHERE id = %s",
        (Jsonb({"file": "tmp/sample.mp3", "media_type": "audio/mpeg", "filename": "sample.mp3"}), t["id"]),
    )
    r = client.get(f"{API}/tasks/{t['id']}/result-file")
    assert r.status_code == 200 and r.content == b"ID3data"
    assert r.headers["content-type"] == "audio/mpeg" and "sample.mp3" in r.headers["content-disposition"]
    db.execute("UPDATE tasks SET result = %s WHERE id = %s", (Jsonb({"file": "../../etc/passwd"}), t["id"]))
    assert client.get(f"{API}/tasks/{t['id']}/result-file").status_code == 404

    log = media / "logs" / "tasks" / f"{t['id']}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("first\nsecond\n")
    with client.stream("GET", f"{API}/tasks/{t['id']}/log/stream") as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        assert r.headers.get("x-accel-buffering") == "no"
        text = "".join(r.iter_text())
    assert "data: first\n\n" in text and "data: second\n\n" in text and "event: end" in text
    assert client.get(f"{API}/tasks/999999/log/stream").status_code == 404
    assert task_row(db, t["id"])["status"] == "succeeded"
