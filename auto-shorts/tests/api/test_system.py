"""Здоровье, готовность, статус системы, ворота миграций, /docs выключен."""

from __future__ import annotations

from pathlib import Path

from tests.api.conftest import API


def test_health_without_db_or_auth(api_app) -> None:
    from fastapi.testclient import TestClient

    plain = TestClient(api_app)  # без lifespan и без входа
    resp = plain.get(f"{API}/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ready_ok(client, app_env) -> None:
    resp = client.get(f"{API}/health/ready")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert set(body["checks"]) == {"db", "migrations", "media_writable", "disk"}
    assert all(c["ok"] for c in body["checks"].values())
    assert body["checks"]["migrations"]["detail"].startswith("version ")
    assert (Path(app_env.media_root) / "tmp").is_dir()
    assert not list((Path(app_env.media_root) / "tmp").iterdir())  # пробный файл убран


def test_ready_503_when_media_not_writable(client, monkeypatch, tmp_path) -> None:
    from autoshorts.core.config import reload_env

    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x")
    monkeypatch.setenv("MEDIA_ROOT", str(blocker / "media"))
    reload_env()
    resp = client.get(f"{API}/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["media_writable"]["ok"] is False
    assert body["checks"]["db"]["ok"] is True


def test_ready_503_when_disk_below_hard_min(client, login_as) -> None:
    admin = login_as("admin")
    resp = admin.patch("/settings", json={"disk_hard_min_gb": 100000, "disk_soft_min_gb": 200000})
    assert resp.status_code == 200, resp.text
    ready = client.get(f"{API}/health/ready")
    assert ready.status_code == 503
    assert ready.json()["checks"]["disk"]["ok"] is False


def test_migrations_gate(client, login_as, monkeypatch) -> None:
    from autoshorts.db import migrate

    admin = login_as("admin")
    real = migrate.file_versions()
    monkeypatch.setattr(migrate, "file_versions", lambda migrations_dir=None: (*real, real[-1] + 1))
    ready = client.get(f"{API}/health/ready")  # готовность видит отставание и закрывает ворота
    assert ready.status_code == 503
    assert ready.json()["checks"]["migrations"]["ok"] is False
    assert "migrations_pending" in ready.json()["checks"]["migrations"]["detail"]

    blocked = admin.get("/system/status")
    assert blocked.status_code == 503
    err = blocked.json()["error"]
    assert err["code"] == "migrations_pending"
    assert err["details"]["pending"] == [real[-1] + 1]
    assert client.get(f"{API}/health").status_code == 200
    assert client.get(f"{API}/openapi.json").status_code == 200

    monkeypatch.setattr(migrate, "file_versions", lambda migrations_dir=None: real)
    assert client.get(f"{API}/health/ready").status_code == 200  # после migrate ворота открываются
    assert admin.get("/system/status").status_code == 200


def test_lifespan_detects_pending_migrations(api_app, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from autoshorts.db import migrate

    real = migrate.file_versions()
    monkeypatch.setattr(migrate, "file_versions", lambda migrations_dir=None: (*real, real[-1] + 1))
    with TestClient(api_app) as c:
        assert api_app.state.schema_gate.blocked
        resp = c.get(f"{API}/auth/me")
        assert resp.status_code == 503 and resp.json()["error"]["code"] == "migrations_pending"
        assert c.get(f"{API}/health").status_code == 200


def test_system_status(login_as, db) -> None:
    viewer = login_as("viewer")
    db.execute(
        """
        INSERT INTO workers (id, resource_class, hostname, pid, version, state, current_task_id)
        VALUES ('box-100-heavy', 'heavy', 'box', 100, '0.1', 'busy', NULL),
               ('box-101-light', 'light', 'box', 101, '0.1', 'idle', NULL),
               ('box-102-light', 'light', 'box', 102, '0.1', 'idle', NULL)
        """
    )
    db.execute("UPDATE workers SET heartbeat_at = now() - interval '2 days' WHERE id = 'box-102-light'")
    task = db.execute(
        """
        INSERT INTO tasks (kind, resource_class, status, stage, progress, locked_by, started_at)
        VALUES ('job_stage', 'heavy', 'running', 'render', 42.5, 'box-100-heavy', now())
        RETURNING id
        """
    ).fetchone()["id"]
    db.execute(
        """
        INSERT INTO tasks (kind, resource_class, status, defer_reason) VALUES
          ('cleanup', 'light', 'queued', NULL), ('metrics_poll', 'light', 'queued', 'load_high'),
          ('source_prepare', 'heavy', 'queued', NULL), ('cleanup', 'light', 'succeeded', NULL)
        """
    )
    resp = viewer.get("/system/status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [w["id"] for w in body["workers"]] == ["box-100-heavy", "box-101-light"]
    assert body["heavy_slot"] == {"owner": "box-100-heavy", "task_id": task, "stage": "render", "pct": 42.5}
    assert body["queue"] == {
        "heavy": {"queued": 1, "running": 1, "deferred": 0},
        "light": {"queued": 1, "running": 0, "deferred": 1},
    }
    assert body["disk"]["free_gb"] > 0
    assert body["disk"]["thresholds"] == {"soft_gb": 12.0, "hard_gb": 8.0, "emergency_gb": 5.0}
    assert body["mem_available_mb"] > 0
    assert body["versions"]["db_schema"] >= 9
    assert body["budget"]["daily_limit_usd"] > 0


def test_docs_disabled_in_prod(api_app, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from autoshorts.core.config import reload_env

    monkeypatch.setenv("APP_ENV", "prod")
    reload_env()
    plain = TestClient(api_app)
    assert plain.get("/docs").status_code == 404
    assert plain.get("/redoc").status_code == 404
    assert plain.get(f"{API}/openapi.json").status_code == 200


def test_request_id_propagates(client) -> None:
    resp = client.get(f"{API}/health", headers={"X-Request-ID": "nginx-req-0123456789"})
    assert resp.headers["x-request-id"] == "nginx-req-0123456789"
    bad = client.get(f"{API}/health", headers={"X-Request-ID": "bad id <script>"})
    assert bad.headers["x-request-id"] != "bad id <script>"
    assert len(bad.headers["x-request-id"]) == 16


def test_maintenance_requires_admin(login_as) -> None:
    op = login_as("operator")
    assert op.post("/maintenance/cleanup").status_code == 403
    admin = login_as("admin")
    resp = admin.post("/maintenance/cleanup")
    assert resp.status_code == 501 and resp.json()["error"]["code"] == "not_implemented"
