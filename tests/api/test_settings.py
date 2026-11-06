"""Настройки, провайдеры, справочники, аудит и алерты."""

from __future__ import annotations


def _items(resp) -> dict:
    return {i["key"]: i for i in resp.json()["items"]}


# ---------------------------------------------------------------- настройки


def test_settings_list_has_defaults(login_as) -> None:
    op = login_as("operator")
    resp = op.get("/settings")
    assert resp.status_code == 200
    items = _items(resp)
    assert items["business_timezone"]["value"] == "Europe/Moscow"
    assert items["business_timezone"]["is_overridden"] is False
    assert items["allow_mock_in_prod"]["default"] is False
    assert items["disk_hard_min_gb"]["group"] == "worker"
    assert items["safe_zone_presets"]["value"][0]["platform"] == "youtube"


def test_settings_patch_and_reset(login_as, db) -> None:
    admin = login_as("admin")
    resp = admin.patch("/settings", json={"render_crf": 20, "review_chat_id": "-1001", "fx_usd_rub": "96.5"})
    assert resp.status_code == 200, resp.text
    items = _items(resp)
    assert items["render_crf"]["value"] == 20 and items["render_crf"]["is_overridden"]
    assert items["fx_usd_rub"]["value"] == 96.5
    assert items["render_crf"]["updated_at"] is not None
    stored = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings").fetchall()}
    assert stored == {"render_crf": 20, "review_chat_id": "-1001", "fx_usd_rub": 96.5}

    from autoshorts.services.settings import get_runtime_settings

    assert get_runtime_settings().render_crf == 20  # кеш процесса сброшен после PATCH

    reset = admin.patch("/settings", json={"render_crf": None})
    assert _items(reset)["render_crf"] == {**_items(reset)["render_crf"], "value": 23, "is_overridden": False}
    audit = db.execute("SELECT diff FROM audit_log WHERE action = 'settings.update' ORDER BY id").fetchall()
    assert audit[0]["diff"]["render_crf"] == {"from": None, "to": 20}
    assert audit[1]["diff"]["render_crf"] == {"from": 20, "to": None}


def test_settings_patch_validation_is_all_or_nothing(login_as, db) -> None:
    admin = login_as("admin")
    resp = admin.patch("/settings", json={"render_crf": 99, "no_such_setting": 1, "ffmpeg_threads": 2})
    assert resp.status_code == 422
    errors = {tuple(e["loc"]): e["type"] for e in resp.json()["error"]["details"]["errors"]}
    assert errors == {("body", "render_crf"): "value_error", ("body", "no_such_setting"): "unknown_setting"}
    assert db.execute("SELECT count(*) AS n FROM settings").fetchone()["n"] == 0

    order = admin.patch("/settings", json={"disk_hard_min_gb": 20})  # hard > soft (12)
    assert order.status_code == 422
    assert order.json()["error"]["details"]["errors"][0]["type"] == "disk_thresholds_order"


def test_bad_override_in_db_is_ignored(login_as, db) -> None:
    db.execute("""INSERT INTO settings (key, value) VALUES ('render_crf', '"not-a-number"')""")
    op = login_as("operator")
    items = _items(op.get("/settings"))
    assert items["render_crf"]["value"] == 23 and items["render_crf"]["is_overridden"] is False


def test_safe_zones_follow_setting(login_as) -> None:
    admin = login_as("admin")
    zones = admin.get("/meta/safe-zones").json()["items"]
    assert {z["platform"] for z in zones} == {"youtube", "vk"}
    custom = [dict(zones[0], source_note="своё")]
    assert admin.patch("/settings", json={"safe_zone_presets": custom}).status_code == 200
    assert admin.get("/meta/safe-zones").json()["items"] == custom


# ---------------------------------------------------------------- справочники


def test_meta_enums_and_reject_reasons(login_as) -> None:
    viewer = login_as("viewer")
    enums = viewer.get("/meta/enums").json()
    assert {"code": "pending_review", "label": "Ждёт приёмки"} in enums["job_status"]
    assert [o["code"] for o in enums["role"]] == ["admin", "operator", "viewer"]
    reasons = viewer.get("/meta/reject-reasons").json()["items"]
    assert reasons[0] == {"code": "hook_slabyy", "label": "Слабый хук", "hotkey": "1"}
    hotkeys = [r["hotkey"] for r in reasons if r["hotkey"]]
    assert len(hotkeys) == len(set(hotkeys)) == 10


# ---------------------------------------------------------------- провайдеры


def test_providers_list_and_update(login_as, db) -> None:
    admin = login_as("admin")
    items = {p["key"]: p for p in admin.get("/providers").json()["items"]}
    assert items["fal_flux_schnell"]["adapter"] == "images.fal"
    assert items["fal_flux_schnell"]["price"] == {"unit": "image", "usd": 0.003}
    assert items["fal_flux_schnell"]["secret_set"] is False
    assert items["gigaam_v3_onnx"]["resource_class"] == "heavy"
    assert items["openai_compat"]["is_default"] is True

    resp = admin.put(
        "/providers/mock_llm",
        json={"enabled": True, "is_default": True, "params": {"seed": 1}, "secret_name": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_default"] is True
    defaults = db.execute("SELECT key FROM provider_configs WHERE kind = 'llm' AND is_default").fetchall()
    assert [r["key"] for r in defaults] == ["mock_llm"]

    bad = admin.put("/providers/mock_llm", json={"enabled": False, "is_default": True, "params": {}})
    assert bad.status_code == 422
    unknown = admin.put("/providers/no_such", json={"enabled": True, "is_default": False, "params": {}})
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "provider_not_found"
    audit = db.execute("SELECT diff FROM audit_log WHERE action = 'provider.update'").fetchone()
    assert audit["diff"]["is_default"] == {"from": False, "to": True}


def test_provider_last_error_from_generation_events(login_as, db) -> None:
    admin = login_as("admin")
    task = db.execute(
        "INSERT INTO tasks (kind, resource_class) VALUES ('job_stage', 'light') RETURNING id"
    ).fetchone()
    db.execute(
        """
        INSERT INTO generation_events
          (task_id, step_name, provider, cost_usd, status, error, created_at) VALUES
          (%(t)s, 'image', 'fal_flux_schnell', 0.003, 'ok', NULL, now() - interval '1 hour'),
          (%(t)s, 'image', 'fal_flux_schnell', 0, 'error', 'HTTP 429', now())
        """,
        {"t": task["id"]},
    )
    item = next(p for p in admin.get("/providers").json()["items"] if p["key"] == "fal_flux_schnell")
    assert item["last_ok_at"] is not None and item["last_error"] == "HTTP 429"


def test_provider_test_offline(login_as) -> None:
    admin = login_as("admin")
    mock = admin.post("/providers/mock_llm/test").json()
    assert mock["ok"] is True and mock["latency_ms"] >= 0
    fal = admin.post("/providers/fal_flux_schnell/test").json()
    assert fal["ok"] is False and "FAL_API_KEY" in fal["detail"]
    assert admin.put("/secrets/FAL_API_KEY", json={"value": "fal-secret-value-1234"}).status_code == 204
    fal = admin.post("/providers/fal_flux_schnell/test").json()
    assert fal["ok"] is True
    item = next(p for p in admin.get("/providers").json()["items"] if p["key"] == "fal_flux_schnell")
    assert item["secret_set"] is True and item["last_ok_at"] is not None
    assert login_as("operator").post("/providers/mock_llm/test").status_code == 403


def test_mock_provider_test_fails_in_prod(login_as, monkeypatch) -> None:
    from autoshorts.core.config import reload_env

    admin = login_as("admin")
    monkeypatch.setenv("APP_ENV", "prod")
    reload_env()
    res = admin.post("/providers/mock_tts/test").json()
    assert res["ok"] is False and "allow_mock_in_prod" in res["detail"]
    assert admin.patch("/settings", json={"allow_mock_in_prod": True}).status_code == 200
    assert admin.post("/providers/mock_tts/test").json()["ok"] is True


# ---------------------------------------------------------------- аудит


def test_audit_log_journal(login_as) -> None:
    admin = login_as("admin")
    for i in range(3):
        admin.post("/budget/pause", json={"reason": f"пауза {i}"})
    page1 = admin.get("/audit-log", params={"action": "budget.*", "limit": 2}).json()
    assert [e["action"] for e in page1["items"]] == ["budget.pause", "budget.pause"]
    assert page1["next_before_id"] == page1["items"][-1]["id"]
    page2 = admin.get(
        "/audit-log", params={"action": "budget.*", "limit": 2, "before_id": page1["next_before_id"]}
    ).json()
    assert len(page2["items"]) == 1 and page2["next_before_id"] is None
    logins = admin.get(
        "/audit-log", params={"action": "auth.login", "actor_user_id": admin.user["id"]}
    ).json()
    assert logins["items"][0]["actor_kind"] == "user" and logins["items"][0]["request_id"]
    bad_range = admin.get("/audit-log", params={"date_from": "2026-09-27", "date_to": "2026-09-01"})
    assert bad_range.status_code == 422
    empty = admin.get("/audit-log", params={"date_from": "2020-01-01", "date_to": "2020-01-02"}).json()
    assert empty["items"] == []


# ---------------------------------------------------------------- алерты


def test_alerts_list_and_ack(login_as, db) -> None:
    from autoshorts.db.pool import connection
    from autoshorts.services.alerts import open_alert, resolve_alert

    with connection() as conn:
        row, created = open_alert(
            conn,
            code="disk_low",
            severity="critical",
            dedup_key="disk_low",
            title="Мало места",
            details={"free_gb": 7.5},
        )
        again, created_again = open_alert(
            conn,
            code="disk_low",
            severity="critical",
            dedup_key="disk_low",
            title="Мало места",
            details={"free_gb": 7.1},
        )
        open_alert(
            conn, code="worker_down", severity="warning", dedup_key="worker_down:heavy", title="Воркер"
        )
    assert created and not created_again and again["id"] == row["id"]

    viewer = login_as("viewer")
    all_alerts = viewer.get("/alerts").json()["items"]
    assert len(all_alerts) == 2
    assert viewer.post(f"/alerts/{row['id']}/ack").status_code == 403

    op = login_as("operator")
    assert op.post(f"/alerts/{row['id']}/ack").status_code == 204
    assert op.post(f"/alerts/{row['id']}/ack").status_code == 204  # идемпотентно
    assert op.post("/alerts/999999/ack").status_code == 404
    open_only = viewer.get("/alerts", params={"status": "open"}).json()["items"]
    assert [a["code"] for a in open_only] == ["worker_down"]
    both = viewer.get("/alerts", params=[("status", "open"), ("status", "acked")]).json()["items"]
    assert len(both) == 2
    acked = db.execute("SELECT status, acked_by FROM alerts WHERE id = %s", (row["id"],)).fetchone()
    assert acked == {"status": "acked", "acked_by": op.user["id"]}

    with connection() as conn:
        assert resolve_alert(conn, "disk_low")["status"] == "resolved"
        assert resolve_alert(conn, "disk_low") is None
    assert viewer.get("/alerts", params={"status": "bogus"}).status_code == 422
