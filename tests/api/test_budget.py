"""Бюджет дня: API, резерв/расчёт для воркеров, пауза, смена бизнес-дня, алерты, журнал вызовов."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from autoshorts.core.errors import ResourceDeferred


def _business_day(db, tz: str = "Europe/Moscow"):
    return db.execute("SELECT (now() AT TIME ZONE %s)::date AS d", (tz,)).fetchone()["d"]


def test_get_budget_uses_moscow_business_day(login_as, db) -> None:
    viewer = login_as("viewer")
    body = viewer.get("/budget").json()
    assert body == {
        "day": _business_day(db).isoformat(),
        "daily_limit_usd": 15.0,
        "spent_today_usd": 0.0,
        "reserved_usd": 0.0,
        "paused": False,
        "pause_reason": None,
        "alert_threshold_pct": 80,
    }


def test_business_timezone_setting_changes_day(login_as, db) -> None:
    admin = login_as("admin")
    assert admin.patch("/settings", json={"business_timezone": "Pacific/Kiritimati"}).status_code == 200
    assert admin.get("/budget").json()["day"] == _business_day(db, "Pacific/Kiritimati").isoformat()


def test_patch_pause_resume(login_as, db) -> None:
    admin = login_as("admin")
    resp = admin.patch("/budget", json={"daily_limit_usd": 20, "alert_threshold_pct": 90})
    assert resp.status_code == 200
    assert resp.json()["daily_limit_usd"] == 20.0 and resp.json()["alert_threshold_pct"] == 90
    assert admin.patch("/budget", json={"daily_limit_usd": -1}).status_code == 422

    paused = admin.post("/budget/pause", json={"reason": "проверяю расходы"}).json()
    assert paused["paused"] is True and paused["pause_reason"] == "проверяю расходы"
    resumed = admin.post("/budget/resume").json()
    assert resumed["paused"] is False and resumed["pause_reason"] is None
    actions = [
        r["action"]
        for r in db.execute("SELECT action FROM audit_log WHERE entity_type = 'budget' ORDER BY id")
    ]
    assert actions == ["budget.update", "budget.pause", "budget.resume"]


def test_reserve_settle_and_threshold_alert(client, db) -> None:
    from autoshorts.db.pool import connection
    from autoshorts.services import budget

    with connection() as conn:
        r = budget.reserve(conn, 5, task_id=None)
    assert r.ok and r.budget["reserved_usd"] == 5.0
    with connection() as conn:
        after = budget.settle(conn, estimate_usd=5, actual_usd=4.5)
    assert after["reserved_usd"] == 0.0 and after["spent_today_usd"] == 4.5
    assert db.execute("SELECT count(*) AS n FROM alerts").fetchone()["n"] == 0

    with connection() as conn:
        assert budget.reserve(conn, 7.5).ok  # 4.5 + 7.5 = 12 = 80% от 15
    alert = db.execute("SELECT code, dedup_key, severity FROM alerts").fetchone()
    assert alert["code"] == "budget_80" and alert["dedup_key"].startswith("budget_80:")
    with connection() as conn:
        assert budget.reserve(conn, 0.5).ok
    assert db.execute("SELECT count(*) AS n FROM alerts").fetchone()["n"] == 1  # раз в день


def test_over_limit_pauses_and_defers(client, login_as, db) -> None:
    from autoshorts.db.pool import connection
    from autoshorts.services import budget

    with connection() as conn:
        assert budget.reserve(conn, 14).ok
        r = budget.reserve(conn, 2)
    assert not r.ok and r.reason == "budget_exhausted" and r.retry_after_sec > 300
    state = db.execute("SELECT paused, pause_reason, reserved_usd FROM budget_state").fetchone()
    assert (
        state["paused"] and state["pause_reason"] == "budget_exhausted" and float(state["reserved_usd"]) == 14
    )
    assert db.execute("SELECT code FROM alerts WHERE status = 'open' AND code = 'budget_paused'").fetchone()

    with pytest.raises(ResourceDeferred) as exc:
        budget.reserve_or_defer(0.01)
    assert exc.value.reason == "budget_exhausted" and exc.value.retry_after_sec > 300

    # поднятый лимит снимает автоматическую паузу и закрывает алерт
    admin = login_as("admin")
    body = admin.patch("/budget", json={"daily_limit_usd": 50}).json()
    assert body["paused"] is False
    assert not db.execute("SELECT 1 FROM alerts WHERE status = 'open' AND code = 'budget_paused'").fetchone()
    assert budget.reserve_or_defer(1)["reserved_usd"] == 15.0


def test_manual_pause_defers_workers(client, login_as) -> None:
    from autoshorts.services import budget

    admin = login_as("admin")
    admin.post("/budget/pause", json={"reason": "стоп"})
    with pytest.raises(ResourceDeferred) as exc:
        budget.reserve_or_defer(0.01)
    assert exc.value.retry_after_sec == budget.MANUAL_PAUSE_RETRY_SEC


def test_day_rollover(client, login_as, db) -> None:
    from autoshorts.db.pool import connection
    from autoshorts.services import budget

    viewer = login_as("viewer")
    db.execute(
        """UPDATE budget_state SET day = (now() AT TIME ZONE 'Europe/Moscow')::date - 1, spent_today_usd = 14,
           reserved_usd = 1, paused = true, pause_reason = 'budget_exhausted'"""
    )
    shown = viewer.get("/budget").json()
    assert shown["day"] == _business_day(db).isoformat()
    assert shown["spent_today_usd"] == 0 and shown["reserved_usd"] == 0 and shown["paused"] is False
    stored = db.execute("SELECT day FROM budget_state").fetchone()["day"]
    assert stored != _business_day(db)  # GET не пишет

    with connection() as conn:
        assert budget.reserve(conn, 1).ok  # запись смены дня
    row = db.execute("SELECT day, spent_today_usd, reserved_usd, paused FROM budget_state").fetchone()
    assert (
        row["day"] == _business_day(db)
        and float(row["spent_today_usd"]) == 0
        and float(row["reserved_usd"]) == 1
    )

    # ручная пауза переживает полночь
    db.execute("""UPDATE budget_state SET day = day - 1, paused = true, pause_reason = 'ручная'""")
    assert viewer.get("/budget").json()["paused"] is True
    with connection() as conn:
        assert not budget.reserve(conn, 1).ok


def test_budget_event_is_published(client, login_as, app_env) -> None:
    from autoshorts.db.notify import Listener, Notification
    from autoshorts.domain.events import EVENTS_CHANNEL

    admin = login_as("admin")
    with Listener([EVENTS_CHANNEL], app_env.database_url) as listener:
        admin.post("/budget/pause", json={"reason": "событие"})
        notes = [n for n in listener.wait(5) if isinstance(n, Notification)]
    payload = json.loads(notes[0].payload)
    assert payload == {
        "event": "budget",
        "data": {"spent_usd": 0.0, "reserved_usd": 0.0, "limit_usd": 15.0, "paused": True},
    }


def test_generation_events_journal(login_as, db) -> None:
    viewer = login_as("viewer")
    task = db.execute(
        "INSERT INTO tasks (kind, resource_class) VALUES ('job_stage', 'light') RETURNING id"
    ).fetchone()
    for provider, cost in (
        ("fal_flux_schnell", 0.003),
        ("openai_compat", 0.001),
        ("fal_flux_schnell", 0.003),
    ):
        db.execute(
            "INSERT INTO generation_events (task_id, step_name, provider, cost_usd, units)"
            " VALUES (%s, 'image', %s, %s, 1)",
            (task["id"], provider, cost),
        )
    page = viewer.get("/generation-events", params={"provider": "fal_flux_schnell", "limit": 1}).json()
    assert len(page["items"]) == 1 and page["items"][0]["cost_usd"] == 0.003
    assert page["items"][0]["status"] == "ok" and page["items"][0]["task_id"] == task["id"]
    rest = viewer.get(
        "/generation-events", params={"provider": "fal_flux_schnell", "before_id": page["next_before_id"]}
    ).json()
    assert len(rest["items"]) == 1 and rest["next_before_id"] is None
    assert len(viewer.get("/generation-events").json()["items"]) == 3
    today = _business_day(db).isoformat()
    assert (
        len(viewer.get("/generation-events", params={"date_from": today, "date_to": today}).json()["items"])
        == 3
    )


def test_task_budget_gate(client, db) -> None:
    from autoshorts.services import budget

    db.execute("INSERT INTO niches (id, label) VALUES ('taro', 'Таро')")
    db.execute("INSERT INTO clusters (id, title) VALUES ('c1', 'c1')")
    db.execute(
        "INSERT INTO channels (id, niche_id, platform, cluster) VALUES ('ch1', 'taro', 'youtube', 'c1')"
    )
    job = db.execute(
        "INSERT INTO jobs (video_uid, channel_id, niche_id, template_version)"
        " VALUES ('u1', 'ch1', 'taro', 'v1') RETURNING id"
    ).fetchone()["id"]
    task = db.execute(
        "INSERT INTO tasks (kind, resource_class, job_id) VALUES ('job_stage', 'light', %s) RETURNING id",
        (job,),
    ).fetchone()["id"]

    gate = budget.TaskBudgetGate(task_id=task, job_id=job)
    r = gate.reserve(0.02, provider="fal_flux_schnell")
    assert float(db.execute("SELECT reserved_usd FROM budget_state").fetchone()["reserved_usd"]) == 0.02
    after = gate.settle(r, 0.018, step_name="image", model="fal-ai/flux/schnell", units=1, latency_ms=900)
    assert after["reserved_usd"] == 0.0 and after["spent_today_usd"] == 0.018
    ev = db.execute("SELECT * FROM generation_events").fetchone()
    assert (ev["job_id"], ev["task_id"], ev["provider"], ev["step_name"], float(ev["cost_usd"])) == (
        job,
        task,
        "fal_flux_schnell",
        "image",
        0.018,
    )
    assert (
        float(db.execute("SELECT cost_usd FROM jobs WHERE id = %s", (job,)).fetchone()["cost_usd"]) == 0.018
    )

    r2 = gate.reserve(0.5, provider="openai_compat")
    assert gate.release(r2)["reserved_usd"] == 0.0
    with pytest.raises(TypeError):
        gate.settle(r2, 0.1, step_name="script", bogus_field=1)
    with pytest.raises(TypeError):
        gate.settle(r2, 0.1)

    # резерв вчерашнего дня: после смены дня из нового резерва ничего не вычитается
    r3 = gate.reserve(1, provider="openai_compat")
    db.execute("UPDATE budget_state SET day = day - 1")
    gate.reserve(2, provider="openai_compat")  # смена дня: резерв 0 + 2
    yesterday = budget.BudgetReservation(r3.amount_usd, r3.provider, r3.day - timedelta(days=1))
    state = gate.settle(yesterday, 0.9, step_name="script")
    assert state["reserved_usd"] == 2.0 and state["spent_today_usd"] == 0.9


def test_task_budget_gate_release_open_is_idempotent(client, db) -> None:
    """Незакрытые резервы гейта снимаются `release_open`; повторный settle их второй раз не вычитает."""
    from autoshorts.services import budget

    task = db.execute(
        "INSERT INTO tasks (kind, resource_class) VALUES ('wtest_budget', 'light') RETURNING id"
    ).fetchone()["id"]
    gate = budget.TaskBudgetGate(task_id=task)
    other = budget.TaskBudgetGate(task_id=task)
    r1 = gate.reserve(1, provider="openai_compat")
    gate.reserve(0.5, provider="openai_compat")
    closed = gate.reserve(0.25, provider="openai_compat")
    gate.release(closed)
    other.reserve(2, provider="openai_compat")

    def reserved() -> float:
        return float(db.execute("SELECT reserved_usd FROM budget_state").fetchone()["reserved_usd"])

    assert reserved() == 3.5 and len(gate.open_reservations) == 2
    assert gate.release_open() == 2
    assert reserved() == 2.0 and gate.open_reservations == [] and gate.release_open() == 0
    # обработчик всё-таки досчитал вызов: расход пишется, чужой резерв не трогается
    state = gate.settle(r1, 0.4, step_name="script")
    assert state["reserved_usd"] == 2.0 and state["spent_today_usd"] == 0.4
    assert gate.release(closed)["reserved_usd"] == 2.0
