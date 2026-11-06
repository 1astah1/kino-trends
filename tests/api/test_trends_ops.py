"""Очередь — фон Kino-trends (`api/routers/trends_ops.py`): прокси прогонов и лога, действия
operator (запуск/остановка сбора, чарты, шаг конвейера), тумблеры автоматики по белому списку
(чтение viewer, запись admin, 422 на чужие ключи и типы) и журнал аудита. Kino-trends — `FakeKino`
из test_trends.py, расширенный прогонами и настройками."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from autoshorts.api.routers import trends as trends_router
from autoshorts.api.schemas.trends_ops import SETTING_SPECS
from tests.api.test_trends import FakeKino, _configure

RUN_DONE = {
    "id": 37,
    "started_at": "2026-10-24T11:41:03Z",
    "finished_at": "2026-10-24T13:14:18Z",
    "status": "ok",
    "trigger": "schedule",
    "stats": {"new_videos": 924, "duration_s": 5595, "pop_clean": 5, "aborted": False},
    "error": None,
}
RUN_FAILED = {
    "id": 36,
    "started_at": "2026-10-24T05:40:58Z",
    "finished_at": "2026-10-24T05:41:10Z",
    "status": "error",
    "trigger": "manual",
    "stats": {},
    "error": "YouTube ответил 429",
}
IDLE = {
    "running": False,
    "run_id": None,
    "trigger": None,
    "started_at": None,
    "phase": "",
    "progress": "",
    "stats": {},
    "log": [],
}
BUSY = {
    "running": True,
    "run_id": 38,
    "trigger": "manual",
    "started_at": "2026-10-24T15:00:00Z",
    "phase": "детали (Data API)",
    "progress": "120/560",
    "stats": {"discovered": 1200},
    "log": [{"ts": "2026-10-24T15:00:01Z", "level": "info", "msg": "старт: manual"}],
}
JOBS = {
    "dict_import": {"running": False, "last": None},
    "recognize": {"running": False, "last": None},
    "neuro": {"running": False, "last": {"ok": True, "ts": "2026-10-24T13:10:00Z", "checked": 111}},
    "popular": {"running": True, "last": None},
}
LOG_LINES = [
    {"ts": "2026-10-24T11:41:03Z", "level": "info", "msg": "старт: schedule"},
    {"ts": "2026-10-24T13:14:18Z", "level": "info", "msg": "финиш: ok, 5595 с"},
]
KINO_SETTINGS = {
    "schedule_hours": "6",
    "pop_auto_pick": "1",
    "pipeline_enabled": "1",
    "pipeline_tick_min": "5",
    "pipeline_max_per_tick": "20",
    "pipeline_clips_per_source": "3",
    "pipeline_auto_enqueue": "0",
    "pipeline_auto_review": "1",
    "pipeline_autopublish": "0",
    "pipeline_publish_now": "0",
    "rights_policy": "balanced",
    "youtube_api_key": "••••abcd",
    "autoshorts_password": "",
    "pipeline_channel_film": "kadr-dnya",
}
KINO_DEFAULTS = {
    **{k: v for k, v in KINO_SETTINGS.items()},
    "pipeline_enabled": "0",
    "pipeline_auto_review": "0",
}


class FakeKinoOps(FakeKino):
    """FakeKino + прогоны, лог, настройки (с состоянием) и фоновые работы."""

    def __init__(self) -> None:
        super().__init__()
        self.settings = dict(KINO_SETTINGS)
        self.running = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if self.fail is not None or key in self.responses:
            return super().handler(request)
        self.requests.append(request)
        if key == ("GET", "/api/runs"):
            items = [RUN_DONE, RUN_FAILED]
            if self.running:
                items = [{**RUN_DONE, "id": 38, "status": "running", "finished_at": None}, *items]
            return httpx.Response(
                200, json={"items": items, "current": BUSY if self.running else IDLE, "jobs": JOBS}
            )
        if key == ("GET", "/api/runs/37/log"):
            return httpx.Response(200, json={"items": LOG_LINES})
        if request.url.path.startswith("/api/runs/") and request.url.path.endswith("/log"):
            return httpx.Response(200, json={"items": []})  # Kino-trends на неизвестный id отдаёт пусто
        if key == ("POST", "/api/runs"):
            if self.running:
                return httpx.Response(409, json={"detail": "прогон уже идёт"})
            self.running = True
            return httpx.Response(200, json={"run_id": 38, "state": BUSY})
        if key == ("POST", "/api/runs/cancel"):
            was = self.running
            self.running = False
            return httpx.Response(200, json={"cancelling": was})
        if key == ("POST", "/api/videos/refresh"):
            if not self.settings.get("youtube_api_key"):
                return httpx.Response(400, json={"detail": "нет ключа YouTube Data API"})
            return httpx.Response(200, json={"ok": True, "running": True})
        if key == ("POST", "/api/pipeline/tick"):
            return httpx.Response(
                200,
                json={"processed": 3, "changed": 1, "sent": 1, "errors": 2, "health": "ok", "skipped": None},
            )
        if key == ("GET", "/api/settings"):
            return httpx.Response(200, json={"settings": dict(self.settings), "defaults": KINO_DEFAULTS})
        if key == ("PUT", "/api/settings"):
            payload = json.loads(request.content)
            for k, v in payload.items():
                if k not in KINO_DEFAULTS:
                    return httpx.Response(400, json={"detail": f"неизвестная настройка {k}"})
                self.settings[k] = str(v).strip()
            return httpx.Response(200, json={"ok": True})
        self.requests.pop()  # дальше считает базовый обработчик
        return super().handler(request)


@pytest.fixture
def kino(monkeypatch: pytest.MonkeyPatch) -> FakeKinoOps:
    fake = FakeKinoOps()
    monkeypatch.setattr(trends_router, "transport_override", fake.transport())
    return fake


def _paths(kino: FakeKinoOps) -> list[str]:
    return [f"{r.method} {r.url.path}" for r in kino.requests]


# ---------------------------------------------------------------- мост не настроен / недоступен


def test_requires_login_and_bridge(client: Any, login_as: Any, kino: FakeKinoOps) -> None:
    assert client.get("/api/v1/trends/runs").status_code == 401

    viewer = login_as("viewer")
    for path in ("/trends/runs", "/trends/runs/37/log", "/trends/settings"):
        resp = viewer.get(path)
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "trends_not_configured"
    assert kino.requests == []

    _configure(login_as)
    kino.fail = httpx.ConnectError("connection refused")
    resp = viewer.get("/trends/runs")
    assert resp.status_code == 502, resp.text
    assert resp.json()["error"]["code"] == "trends_unreachable"


# ---------------------------------------------------------------- прогоны и лог


def test_runs_and_log_proxy(login_as: Any, kino: FakeKinoOps) -> None:
    _configure(login_as)
    viewer = login_as("viewer")

    resp = viewer.get("/trends/runs", params={"limit": 5})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [r["id"] for r in body["items"]] == [37, 36]
    assert (
        body["items"][0]["stats"]["new_videos"] == 924 and body["items"][1]["error"] == "YouTube ответил 429"
    )
    assert body["current"]["running"] is False and body["current"]["log"] == []
    assert body["jobs"]["popular"]["running"] is True and body["jobs"]["neuro"]["last"]["checked"] == 111
    # интервал автосбора из настроек Kino-trends и расчёт следующего прогона: старт последнего + 6 ч
    assert body["schedule_hours"] == 6
    assert body["next_run_at"] == "2026-10-24T17:41:03+00:00"
    assert _paths(kino) == ["GET /api/runs", "GET /api/settings"]
    assert dict(kino.requests[0].url.params) == {"limit": "5"}

    # идёт сбор: текущий прогон с фазой, прогрессом и хвостом лога проходит насквозь
    kino.running = True
    body = viewer.get("/trends/runs").json()
    assert body["items"][0]["status"] == "running"
    assert body["current"] == {**BUSY}
    assert body["next_run_at"] == "2026-10-24T17:41:03+00:00"  # считается по последнему завершённому

    # без интервала (0) следующего автосбора нет; сбой чтения настроек не ломает список
    kino.settings["schedule_hours"] = "0"
    assert viewer.get("/trends/runs").json()["next_run_at"] is None
    kino.responses[("GET", "/api/settings")] = (503, {"detail": "занято"})
    body = viewer.get("/trends/runs").json()
    assert body["schedule_hours"] is None and body["next_run_at"] is None and len(body["items"]) == 3
    kino.responses.clear()

    kino.requests.clear()
    log = viewer.get("/trends/runs/37/log", params={"limit": 50})
    assert log.status_code == 200, log.text
    assert [line["msg"] for line in log.json()["items"]] == ["старт: schedule", "финиш: ok, 5595 с"]
    assert dict(kino.requests[-1].url.params) == {"limit": "50"}
    assert viewer.get("/trends/runs/0/log").status_code == 422
    assert viewer.get("/trends/runs", params={"limit": 0}).status_code == 422

    kino.responses[("GET", "/api/runs/777/log")] = (404, {"detail": "нет такого прогона"})
    missing = viewer.get("/trends/runs/777/log")
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"]["code"] == "trends_run_not_found"


# ---------------------------------------------------------------- действия оператора


def test_actions_roles_errors_and_audit(login_as: Any, kino: FakeKinoOps, db: Any) -> None:
    _configure(login_as)
    viewer = login_as("viewer")
    operator = login_as("operator")

    for path in ("/trends/runs", "/trends/runs/cancel", "/trends/videos-refresh", "/trends/pipeline/tick"):
        assert viewer.post(path).status_code == 403, path
    assert kino.requests == []

    started = operator.post("/trends/runs")
    assert started.status_code == 200, started.text
    assert started.json()["run_id"] == 38 and started.json()["state"]["phase"] == "детали (Data API)"

    again = operator.post("/trends/runs")  # Kino-trends: 409 «прогон уже идёт» → 400 с его текстом
    assert again.status_code == 400, again.text
    assert again.json()["error"] == {
        "code": "bad_request",
        "message": "прогон уже идёт",
        "details": {"status": 409},
    }

    cancel = operator.post("/trends/runs/cancel")
    assert cancel.status_code == 200 and cancel.json() == {"cancelling": True}
    assert operator.post("/trends/runs/cancel").json() == {"cancelling": False}

    kino.requests.clear()
    refresh = operator.post("/trends/videos-refresh")
    assert refresh.status_code == 200, refresh.text
    assert refresh.json() == {"ok": True, "running": True}
    assert json.loads(kino.requests[-1].content) == {}
    assert operator.post("/trends/videos-refresh", json={"charts_only": True}).status_code == 200
    assert json.loads(kino.requests[-1].content) == {"charts_only": True}
    assert operator.post("/trends/videos-refresh", json={"foo": 1}).status_code == 422

    kino.settings["youtube_api_key"] = ""
    no_key = operator.post("/trends/videos-refresh")
    assert no_key.status_code == 400 and "YouTube" in no_key.json()["error"]["message"]

    tick = operator.post("/trends/pipeline/tick")
    assert tick.status_code == 200, tick.text
    assert tick.json() == {
        "processed": 3,
        "changed": 1,
        "sent": 1,
        "errors": 2,
        "health": "ok",
        "skipped": None,
    }

    rows = db.execute(
        "SELECT action, entity_type, entity_id, summary FROM audit_log "
        "WHERE action LIKE 'trends.%' ORDER BY id"
    ).fetchall()
    assert [(r["action"], r["entity_type"], r["entity_id"]) for r in rows] == [
        ("trends.run_start", "trends_run", "38"),
        ("trends.run_cancel", "trends_run", None),
        ("trends.run_cancel", "trends_run", None),
        ("trends.videos_refresh", "trends_job", "popular"),
        ("trends.videos_refresh", "trends_job", "popular"),
        ("trends.pipeline_tick", "trends_pipeline", None),
    ]
    assert "#38" in rows[0]["summary"]
    assert "не шёл" in rows[2]["summary"] and "не шёл" not in rows[1]["summary"]
    assert "только чарты" in rows[4]["summary"]
    assert "обработано 3" in rows[5]["summary"]


# ---------------------------------------------------------------- тумблеры автоматики


def test_settings_whitelist_roles_and_audit(login_as: Any, kino: FakeKinoOps, db: Any) -> None:
    _configure(login_as)
    viewer = login_as("viewer")
    operator = login_as("operator")
    admin = login_as("admin")

    resp = viewer.get("/trends/settings")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [item["key"] for item in items] == [spec.key for spec in SETTING_SPECS]
    by_key = {item["key"]: item for item in items}
    assert by_key["pipeline_enabled"] == {
        "key": "pipeline_enabled",
        "kind": "bool",
        "label": "Конвейер включён",
        "description": by_key["pipeline_enabled"]["description"],
        "group": "pipeline",
        "value": True,
        "default": False,
        "choices": None,
        "min": None,
        "max": None,
        "unit": None,
    }
    assert by_key["pipeline_autopublish"]["value"] is False
    assert by_key["schedule_hours"]["value"] == 6 and by_key["schedule_hours"]["unit"] == "ч"
    assert by_key["pipeline_tick_min"]["min"] == 1
    assert by_key["rights_policy"]["value"] == "balanced"
    assert by_key["rights_policy"]["choices"] == ["strict", "balanced", "off"]
    assert "youtube_api_key" not in by_key and "pipeline_channel_film" not in by_key  # только белый список
    assert "abcd" not in resp.text

    # запись — только владелец
    patch = {"pipeline_autopublish": True}
    assert viewer.put("/trends/settings", json=patch).status_code == 403
    assert operator.put("/trends/settings", json=patch).status_code == 403
    kino.requests.clear()

    # чужой ключ, чужой тип, выход за границы, пустое тело — 422 до обращения к Kino-trends
    for body, key, kind in (
        ({"youtube_api_key": "x"}, "youtube_api_key", "unknown_key"),
        ({"pipeline_channel_film": "x"}, "pipeline_channel_film", "unknown_key"),
        ({"pipeline_autopublish": "maybe"}, "pipeline_autopublish", "bool_expected"),
        ({"pipeline_tick_min": 0}, "pipeline_tick_min", "too_small"),
        ({"pipeline_tick_min": True}, "pipeline_tick_min", "int_expected"),
        ({"schedule_hours": 1000}, "schedule_hours", "too_large"),
        ({"rights_policy": "maybe"}, "rights_policy", "choice_expected"),
    ):
        bad = admin.put("/trends/settings", json=body)
        assert bad.status_code == 422, (body, bad.text)
        error = bad.json()["error"]
        assert error["code"] == "validation_error"
        assert error["details"]["errors"] == [
            {"loc": [key], "msg": error["details"]["errors"][0]["msg"], "type": kind}
        ]
    assert admin.put("/trends/settings", json={}).status_code == 422
    assert admin.put("/trends/settings", json=[1]).status_code == 422
    assert kino.requests == []

    ok = admin.put(
        "/trends/settings",
        json={
            "pipeline_autopublish": True,
            "pipeline_tick_min": 10,
            "rights_policy": "strict",
            "schedule_hours": 0,
        },
    )
    assert ok.status_code == 200, ok.text
    sent = json.loads(next(r for r in kino.requests if r.method == "PUT").content)
    assert sent == {
        "pipeline_autopublish": "1",
        "pipeline_tick_min": "10",
        "rights_policy": "strict",
        "schedule_hours": "0",
    }
    after = {item["key"]: item["value"] for item in ok.json()["items"]}
    assert after["pipeline_autopublish"] is True and after["pipeline_tick_min"] == 10
    assert after["rights_policy"] == "strict" and after["schedule_hours"] == 0
    assert after["pipeline_enabled"] is True  # нетронутое осталось

    # bool принимает и 0/1 — удобно для скриптов
    assert (
        admin.put("/trends/settings", json={"pipeline_autopublish": 0}).json()["items"][8]["value"] is False
    )

    # отказ Kino-trends пробрасывается как 400 с его текстом
    kino.responses[("PUT", "/api/settings")] = (400, {"detail": "rights_policy: strict|balanced|off"})
    rejected = admin.put("/trends/settings", json={"rights_policy": "off"})
    assert (
        rejected.status_code == 400
        and rejected.json()["error"]["message"] == "rights_policy: strict|balanced|off"
    )

    rows = db.execute(
        "SELECT action, entity_type, entity_id, summary, diff FROM audit_log "
        "WHERE action = 'trends.settings' ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["entity_type"] == "trends_settings" and rows[0]["entity_id"] is None
    assert "pipeline_autopublish=True" in rows[0]["summary"] and "rights_policy=strict" in rows[0]["summary"]
    assert rows[0]["diff"]["pipeline_autopublish"] == {"from": False, "to": True}
    assert rows[0]["diff"]["pipeline_tick_min"] == {"from": 5, "to": 10}
    assert rows[1]["diff"] == {"pipeline_autopublish": {"from": True, "to": False}}
