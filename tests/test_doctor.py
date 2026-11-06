"""Доктор системы: оценка проб, шум логов, журнал инцидентов, самолечение, проверки конвейера."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kinotrends import db, doctor, files

TMP = Path(os.environ["KT_VAR"])


def healthy_probe(**over):
    p = {
        "disk_free_gb": 12.0, "disk_total_gb": 31.6, "mem_avail_mb": 800, "mem_total_mb": 1967, "load1": 1.2, "cores": 2,
        "services": {s: True for s in doctor.STAND_SERVICES}, "http": {"api": "200", "web": "200", "storage": "200"},
        "logs": {"api.log": {"size_mb": 3.0, "lines": []}},
        "deferred": [], "queued": [], "running": [], "failed_tasks_24h": [], "jobs": [], "stuck_jobs": [], "failed_jobs_24h": [],
        "qc_failed": [], "review_ready": 12, "qc_done_6h": 9, "last_qc_min": 20, "autopublish_errors": [], "autopublish_next": [],
        "oauth": [{"channel_id": "kadr-dnya", "status": "active", "connected": True, "last_error": None,
                   "scopes": "{https://www.googleapis.com/auth/youtube.upload,https://www.googleapis.com/auth/yt-analytics.readonly}"}],
        "cleanup_last_min": 30, "published_24h": 4,
    }
    p.update(over)
    return p


def by_id(checks):
    return {c.id: c for c in checks}


def test_healthy_stand_is_all_ok():
    checks = doctor.evaluate_stand(healthy_probe(), {"pipeline_enabled": "1"})
    bad = [c for c in checks if c.status not in ("ok", "info")]
    assert not bad, [(c.id, c.status, c.value) for c in bad]


def test_unreachable_stand_is_single_fail():
    checks = doctor.evaluate_stand({"probe_error": "Connection timed out"})
    assert [(c.id, c.status) for c in checks] == [("stand.reachable", "fail")]


@pytest.mark.parametrize("free, status", [(12.0, "ok"), (6.0, "warn"), (3.0, "fail")])
def test_disk_thresholds(free, status):
    assert by_id(doctor.evaluate_stand(healthy_probe(disk_free_gb=free)))["stand.disk"].status == status


def test_down_service_and_bad_http_fail():
    p = healthy_probe(services={"api": True, "web": True, "worker-light": True, "worker-heavy": False}, http={"api": "200", "web": "502", "storage": "200"})
    c = by_id(doctor.evaluate_stand(p))
    assert c["stand.services"].status == "fail" and "worker-heavy" in c["stand.services"].value
    assert c["stand.http"].status == "fail" and "web: 502" in c["stand.http"].value


def test_old_disk_deferral_fails_short_one_ignored():
    p = healthy_probe(deferred=[{"defer_reason": "disk_low", "kind": "job_stage", "n": 3, "oldest_min": 120},
                                {"defer_reason": "load_high", "kind": "job_stage", "n": 1, "oldest_min": 5}])
    c = by_id(doctor.evaluate_stand(p))["tasks.deferred"]
    assert c.status == "fail" and "disk_low" in c.detail and "load_high" not in c.detail


def test_review_below_target_and_idle_production():
    c = by_id(doctor.evaluate_stand(healthy_probe(review_ready=2, last_qc_min=600), {"pipeline_enabled": "1"}))
    assert c["review.ready"].status == "warn"
    assert c["production.flow"].status == "fail"
    off = by_id(doctor.evaluate_stand(healthy_probe(last_qc_min=600), {"pipeline_enabled": "0"}))
    assert off["production.flow"].status == "ok"


def test_oauth_error_fails_and_unconnected_warns():
    p = healthy_probe(oauth=[{"channel_id": "a", "status": "active", "connected": True, "last_error": "invalid_grant"},
                             {"channel_id": "b", "status": "active", "connected": False, "last_error": None}])
    c = by_id(doctor.evaluate_stand(p))["publish.oauth"]
    assert c.status == "fail" and "invalid_grant" in c.detail and "b: не подключён" in c.detail


def test_log_noise_filtered():
    p = healthy_probe(logs={"api.log": {"size_mb": 1, "lines": [
        "asyncio.exceptions.CancelledError: Task cancelled, timeout graceful shutdown exceeded",
        "Traceback (most recent call last):",
        "2026-10-29 01:00:00 ERROR autoshorts.api: boom in /jobs",
        "2026-10-29 01:00:01 WARNING autoshorts.services.packages: LLM HTTP 429",
    ]}})
    checks, rows = doctor.evaluate_logs(p)
    assert len(rows) == 2 and rows[0]["line"].endswith("boom in /jobs") and rows[1]["line"].endswith("LLM HTTP 429")
    assert checks[0].status == "warn" and "1 ошибок" in checks[0].value


def test_storage_checks():
    ok = by_id(doctor.evaluate_storage({"host": "h", "free_gb": 40, "size_gb": 80, "ytdlp": "2026.10.26", "files": 3, "used_gb": 2}))
    assert ok["storage.disk"].status == "ok" and ok["storage.ytdlp"].status == "ok"
    low = by_id(doctor.evaluate_storage({"host": "h", "free_gb": 2, "size_gb": 80, "ytdlp": "none", "files": 3, "used_gb": 70}))
    assert low["storage.disk"].status == "fail" and low["storage.ytdlp"].status == "fail"
    assert doctor.evaluate_storage({"skipped": True}) == []


@pytest.fixture()
def conn():
    c = db.connect(TMP / "doctor.sqlite")
    db.init_db(c)
    files.init(c)
    doctor.init(c)
    c.execute("DELETE FROM health_incidents")
    c.execute("DELETE FROM health_runs")
    c.commit()
    yield c
    c.close()


def test_incidents_open_escalate_resolve(conn):
    warn = doctor.Check("stand.disk", "Стенд", "Диск стенда", "warn", "6 ГБ")
    opened, closed = doctor.track_incidents(conn, [warn], "2026-10-29T01:00:00Z")
    assert opened == [] and closed == []  # предупреждение — в журнал, без тревоги
    fail = doctor.Check("stand.disk", "Стенд", "Диск стенда", "fail", "3 ГБ")
    opened, _ = doctor.track_incidents(conn, [fail], "2026-10-29T01:05:00Z")
    assert [o["id"] for o in opened] == ["stand.disk"]  # стало хуже — тревога
    opened, _ = doctor.track_incidents(conn, [fail], "2026-10-29T01:10:00Z")
    assert opened == []  # тот же fail повторно — без повторной тревоги
    row = conn.execute("SELECT * FROM health_incidents WHERE check_id = 'stand.disk'").fetchone()
    assert row["seen"] == 3 and row["status"] == "fail" and row["resolved_at"] is None
    ok = doctor.Check("stand.disk", "Стенд", "Диск стенда", "ok", "12 ГБ")
    _, closed = doctor.track_incidents(conn, [ok], "2026-10-29T01:15:00Z")
    assert [c["check_id"] for c in closed] == ["stand.disk"]
    assert conn.execute("SELECT resolved_at FROM health_incidents WHERE check_id = 'stand.disk'").fetchone()[0] == "2026-10-29T01:15:00Z"


def test_remediation_restarts_down_services_cleans_disk_and_trims_logs(monkeypatch):
    calls = []

    class R:
        returncode, stdout, stderr = 0, "ok", ""

    monkeypatch.setattr(doctor, "_ssh", lambda host, key, cmd, stdin=None, timeout=90: calls.append(cmd) or R())
    probe = healthy_probe(services={"api": True, "web": True, "worker-light": False, "worker-heavy": True}, disk_free_gb=2.0,
                          logs={"api.log": {"size_mb": 90.0, "lines": []}, "web.log": {"size_mb": 1.0, "lines": []}})
    checks = doctor.evaluate_stand(probe)
    actions = doctor.remediate(checks, probe, {})
    assert any("as_restart.sh worker-light" in c for c in calls)
    assert any("enqueue_cleanup" in c and "emergency=True" in c for c in calls)
    assert any("api.log" in c and "tail -c" in c for c in calls) and not any("web.log" in c for c in calls)
    assert len(actions) == 3


def test_remediation_does_nothing_when_healthy(monkeypatch):
    calls = []
    monkeypatch.setattr(doctor, "_ssh", lambda *a, **k: calls.append(a))
    assert doctor.remediate(doctor.evaluate_stand(healthy_probe()), healthy_probe(), {}) == [] and calls == []


def test_conveyor_checks(conn):
    now = datetime.now(timezone.utc)
    iso = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    db.set_meta(conn, "pipeline_tick_at", iso(now - timedelta(minutes=2)))
    conn.execute("DELETE FROM pipeline_items")
    conn.execute("DELETE FROM downloads")
    for i, st in enumerate(("file_pending", "sent", "failed", "failed", "failed")):
        conn.execute("INSERT INTO pipeline_items(kind, ref_id, label, status, last_error, created_at, updated_at) VALUES ('film', ?, ?, ?, ?, ?, ?)",
                     (f"Q{i}", f"Фильм {i}", st, "файл не скачался" if st == "failed" else None, iso(now), iso(now)))
    conn.execute("INSERT INTO downloads(video_id, url, status, error, created_at) VALUES ('x', 'u', 'error', 'YouTube требует вход (бот-проверка)', ?)", (iso(now),))
    conn.commit()
    c = by_id(doctor.evaluate_conveyor(conn, {"pipeline_enabled": "1"}))
    assert c["pipeline.tick"].status == "ok"
    assert c["pipeline.queue"].status == "ok" and "ждут 1, в работе 1" in c["pipeline.queue"].value
    assert c["pipeline.failed"].status == "warn" and c["pipeline.failed"].value == "3"
    assert c["downloads.errors"].status == "warn" and "cookies" in c["downloads.errors"].hint
    db.set_meta(conn, "pipeline_tick_at", iso(now - timedelta(hours=2)))
    assert by_id(doctor.evaluate_conveyor(conn, {"pipeline_enabled": "1"}))["pipeline.tick"].status == "fail"


def test_format_text_lists_failures_and_actions():
    rep = doctor.Report(at="2026-10-29T01:00:00Z", status="fail",
                        checks=[doctor.Check("stand.disk", "Стенд", "Диск стенда", "fail", "3 ГБ", "мало")],
                        actions=["диск стенда: поставлена аварийная уборка"]).to_dict()
    text = doctor.format_text(rep)
    assert "✗ Диск стенда: 3 ГБ — мало" in text and "аварийная уборка" in text
