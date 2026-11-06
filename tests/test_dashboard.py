"""Проверка HTML-рендера дашборда (без живого Postgres — данные мокаются)."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import os
os.environ.setdefault("DASHBOARD_PASSWORD", "test123")

from fastapi.testclient import TestClient


def _client():
    from autoshorts.webapp import dashboard
    dashboard._DASHBOARD_PASSWORD = "test123"
    dashboard._DASHBOARD_USER = "admin"
    return TestClient(dashboard.app)


def test_dashboard_requires_auth():
    client = _client()
    resp = client.get("/")
    assert resp.status_code == 401


def test_dashboard_rejects_wrong_password():
    client = _client()
    resp = client.get("/", auth=("admin", "wrong"))
    assert resp.status_code == 401


def test_health_endpoint_is_public():
    client = _client()
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@patch("autoshorts.webapp.dashboard.q")
def test_overview_renders_with_empty_data(mock_q):
    mock_q.queue_by_status.return_value = []
    mock_q.budget_status.return_value = {"daily_limit_usd": 15.0, "spent_today_usd": 0.0, "paused": False}
    mock_q.qc_pass_rate.return_value = {"passed": 0, "failed": 0, "total": 0, "pass_rate_pct": None}
    mock_q.reject_reason_breakdown.return_value = []
    mock_q.recent_jobs.return_value = []

    client = _client()
    resp = client.get("/", auth=("admin", "test123"))
    assert resp.status_code == 200
    assert "Auto Shorts" in resp.text
    assert "Очередь пуста" in resp.text


@patch("autoshorts.webapp.dashboard.q")
def test_budget_paused_shows_critical_card(mock_q):
    """Когда budget_state.paused=true, карточка должна визуально отличаться (см. ТЗ §9)."""
    mock_q.queue_by_status.return_value = []
    mock_q.budget_status.return_value = {"daily_limit_usd": 15.0, "spent_today_usd": 15.0, "paused": True}
    mock_q.qc_pass_rate.return_value = {"passed": 0, "failed": 0, "total": 0, "pass_rate_pct": None}
    mock_q.reject_reason_breakdown.return_value = []
    mock_q.recent_jobs.return_value = []

    client = _client()
    resp = client.get("/", auth=("admin", "test123"))
    assert "ПРИОСТАНОВЛЕНО" in resp.text
    assert 'card crit' in resp.text
