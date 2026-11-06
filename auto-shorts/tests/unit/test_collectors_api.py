# ruff: noqa: F811 - фикстуры tests/api/conftest.py подключены импортом и приходят параметрами
"""API коллекторов (M4-A): ручной ввод метрик, удаление ручного снимка, очередь ручного ввода, `GET /quota`.

Приложение и вход — фикстуры `tests/api/conftest.py` (подключены импортом ниже).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from autoshorts.api.schemas.metrics import ManualDuePage, ManualMetricsResult
from autoshorts.api.schemas.quota import QuotaUsage
from tests.api.conftest import api_app, client, db, login_as, make_user, new_client  # noqa: F401
from tests.unit.test_collectors_support import add_snapshot, make_publication, seed_channels


@pytest.fixture
def seeded(db: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    for name in ("YOUTUBE_API_KEY", "YOUTUBE_API_KEY_SOLO", "VK_ACCESS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    seed_channels(db)
    return db


@pytest.fixture
def operator(login_as: Any) -> Any:
    return login_as("operator")


@pytest.fixture
def viewer(login_as: Any) -> Any:
    return login_as("viewer")


def pub_row(db: Any, pid: int) -> dict[str, Any]:
    return db.execute("SELECT * FROM publications WHERE id = %s", (pid,)).fetchone()


# ---------------------------------------------------------------- ручной ввод


def test_manual_snapshot_updates_publication(client: Any, seeded: Any, operator: Any) -> None:
    published = datetime.now(UTC) - timedelta(hours=40)
    pub = make_publication(seeded, platform="vk", channel_id="vk_main", published_at=published)
    seeded.execute(
        "UPDATE publications SET manual_due_at = now() - interval '2 hours' WHERE id = %s", (pub["id"],)
    )
    add_snapshot(seeded, pub, age_hours=12, views=1000, source="ytdlp")
    captured = published + timedelta(hours=36)
    resp = operator.post(
        f"/publications/{pub['id']}/metrics/manual",
        json={"captured_at": captured.isoformat(), "views": 4000, "likes": 50, "note": "с экрана VK"},
    )
    assert resp.status_code == 201, resp.text
    result = ManualMetricsResult.model_validate(resp.json())
    snap = result.snapshot
    assert snap.source.value == "manual" and snap.views == 4000 and snap.created_by == operator.user["id"]
    assert snap.note == "с экрана VK" and snap.age_hours == 36.0
    assert result.warnings == []
    assert result.freshness.manual_due is False and result.freshness.last_captured_at == captured
    assert result.freshness.collector.value == "ytdlp"
    row = pub_row(seeded, pub["id"])
    assert row["latest_views"] == 4000 and row["latest_likes"] == 50 and row["manual_due_at"] is None
    assert row["views_24h"] == 2500  # интерполяция (12 ч, 1000) → (36 ч, 4000)
    audit = seeded.execute(
        "SELECT * FROM audit_log WHERE action = 'publication.metrics_manual' AND entity_id = %s",
        (str(pub["id"]),),
    ).fetchone()
    assert audit is not None and audit["actor_user_id"] == operator.user["id"]


def test_manual_snapshot_warnings(client: Any, seeded: Any, operator: Any) -> None:
    pub = make_publication(seeded, platform="vk", channel_id="vk_main")
    add_snapshot(seeded, pub, age_hours=5, views=100, source="ytdlp")
    drop = operator.post(f"/publications/{pub['id']}/metrics/manual", json={"views": 90})
    assert drop.status_code == 201 and drop.json()["warnings"] == ["views_decreased"]
    jump = operator.post(f"/publications/{pub['id']}/metrics/manual", json={"views": 90 * 21})
    assert jump.status_code == 201 and jump.json()["warnings"] == ["large_jump"]


@pytest.mark.parametrize(
    ("shift", "reason"),
    [(timedelta(hours=1), "in_future"), (-timedelta(hours=50), "before_publication")],
)
def test_manual_snapshot_rejected_times(
    client: Any,
    seeded: Any,
    operator: Any,
    shift: timedelta,
    reason: str,
) -> None:
    pub = make_publication(seeded, published_at=datetime.now(UTC) - timedelta(hours=30))
    at = datetime.now(UTC) + shift
    resp = operator.post(
        f"/publications/{pub['id']}/metrics/manual", json={"views": 5, "captured_at": at.isoformat()}
    )
    assert resp.status_code == 422, resp.text
    err = resp.json()["error"]
    assert err["code"] == "metrics_rejected" and err["details"]["reason"] == reason


def test_manual_snapshot_deleted_or_missing_publication(client: Any, seeded: Any, operator: Any) -> None:
    pub = make_publication(seeded)
    seeded.execute("UPDATE publications SET deleted_at = now() WHERE id = %s", (pub["id"],))
    resp = operator.post(f"/publications/{pub['id']}/metrics/manual", json={"views": 5})
    assert resp.status_code == 422 and resp.json()["error"]["details"]["reason"] == "publication_deleted"
    missing = operator.post("/publications/999999/metrics/manual", json={"views": 5})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "publication_not_found"


def test_viewer_cannot_write_metrics(client: Any, seeded: Any, viewer: Any) -> None:
    pub = make_publication(seeded)
    resp = viewer.post(f"/publications/{pub['id']}/metrics/manual", json={"views": 5})
    assert resp.status_code == 403
    assert viewer.get("/metrics/manual-due").status_code == 200


def test_delete_only_manual_snapshots(client: Any, seeded: Any, operator: Any) -> None:
    pub = make_publication(seeded)
    add_snapshot(seeded, pub, age_hours=10, views=700, source="yt_data")
    created = operator.post(f"/publications/{pub['id']}/metrics/manual", json={"views": 900}).json()
    assert pub_row(seeded, pub["id"])["latest_views"] == 900
    auto_id = seeded.execute(
        "SELECT id FROM metrics_snapshots WHERE publication_id = %s AND source = 'yt_data'", (pub["id"],)
    ).fetchone()["id"]
    conflict = operator.delete(f"/publications/{pub['id']}/metrics/{auto_id}")
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "metrics_snapshot_not_manual"
    other = make_publication(seeded)
    wrong = operator.delete(f"/publications/{other['id']}/metrics/{created['snapshot']['id']}")
    assert wrong.status_code == 404 and wrong.json()["error"]["code"] == "snapshot_not_found"
    ok = operator.delete(f"/publications/{pub['id']}/metrics/{created['snapshot']['id']}")
    assert ok.status_code == 204
    assert pub_row(seeded, pub["id"])["latest_views"] == 700  # пересчитано по оставшимся снимкам


def test_manual_due_queue(client: Any, seeded: Any, operator: Any) -> None:
    old = make_publication(seeded, platform="vk", channel_id="vk_main")
    new = make_publication(seeded)
    later = make_publication(seeded)
    removed = make_publication(seeded, status="removed_by_platform")
    seeded.execute(
        "UPDATE publications SET manual_due_at = now() - interval '5 hours' WHERE id = %s", (old["id"],)
    )
    seeded.execute(
        "UPDATE publications SET manual_due_at = now() - interval '1 hour' WHERE id = %s", (new["id"],)
    )
    seeded.execute(
        "UPDATE publications SET manual_due_at = now() + interval '1 hour' WHERE id = %s", (later["id"],)
    )
    seeded.execute(
        "UPDATE publications SET manual_due_at = now() - interval '1 hour' WHERE id = %s", (removed["id"],)
    )
    seeded.execute(
        "INSERT INTO collector_state (collector, cluster_id, state, reason, message)"
        " VALUES ('yt_data', 'default', 'disabled', 'no_api_key', 'нет ключа')"
    )
    add_snapshot(seeded, old, age_hours=3, views=55, source="manual")
    page = ManualDuePage.model_validate(operator.get("/metrics/manual-due").json())
    assert page.total == 2 and [i.publication_id for i in page.items] == [old["id"], new["id"]]
    first, second = page.items
    assert first.platform.value == "vk" and first.channel.id == "vk_main" and first.last_views == 55
    assert first.url == f"https://vk.com/clip{old['platform_video_id']}" and first.age_hours == pytest.approx(
        30, abs=0.2
    )
    assert second.collector_reason is not None and second.collector_reason.value == "no_api_key"
    only_vk = operator.get("/metrics/manual-due", params={"platform": "vk"}).json()
    assert [i["publication_id"] for i in only_vk["items"]] == [old["id"]]
    by_channel = operator.get("/metrics/manual-due", params=[("channel_id", "yt_main"), ("limit", 1)]).json()
    assert by_channel["total"] == 1 and by_channel["limit"] == 1


# ---------------------------------------------------------------- квоты и коллекторы


def test_quota_usage(client: Any, seeded: Any, operator: Any) -> None:
    seeded.execute(
        "INSERT INTO api_quota_usage (api, quota_day, cluster_id, units, calls, errors)"
        " SELECT 'youtube_data', (now() AT TIME ZONE 'America/Los_Angeles')::date - d, 'default', u, u, e"
        " FROM (VALUES (0, 2500, 3), (3, 40, 0), (30, 9, 0)) AS v(d, u, e)"
    )
    make_publication(seeded)
    resp = operator.get("/quota")
    assert resp.status_code == 200, resp.text
    usage = QuotaUsage.model_validate(resp.json())
    assert (usage.date_to - usage.date_from).days == 13
    assert [i.units for i in usage.items] == [2500, 40]  # старше 14 суток не попало
    today = {(t.api.value, t.cluster_id): t for t in usage.today}
    main = today[("youtube_data", "default")]
    assert (main.units, main.limit, main.hard_stop, main.soft_pct, main.pct) == (2500, 10000, 8000, 80, 25.0)
    assert today[("youtube_data", "solo")].units == 0  # куст со своим ключом, вызовов не было
    assert usage.reset_at > datetime.now(UTC)
    collectors = {(c.collector.value, c.cluster_id): c for c in usage.collectors}
    yt = collectors[("yt_data", "default")]
    assert (
        yt.state.value == "disabled"
        and yt.reason.value == "no_api_key"
        and yt.key_secret == "YOUTUBE_API_KEY"
    )
    assert yt.key_set is False and yt.publications_total == 1 and yt.publications_due == 1
    assert collectors[("yt_data", "solo")].key_secret == "YOUTUBE_API_KEY_SOLO"
    assert ("ytdlp", "default") in collectors and collectors[
        ("manual", "default")
    ].reason.value == "manual_only"
    assert ("vk_api", "default") not in collectors  # токена VK нет
    narrowed = operator.get("/quota", params=[("api", "vk_api")]).json()
    assert narrowed["items"] == [] and narrowed["today"] == []
    old = operator.get("/quota", params={"date_from": "2026-01-01"})
    assert old.status_code == 422


def test_quota_shows_key_once_set(
    client: Any, seeded: Any, operator: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YOUTUBE_API_KEY", "set-in-env")
    seeded.execute(
        "INSERT INTO collector_state (collector, cluster_id, state, reason, message)"
        " VALUES ('yt_data', 'default', 'disabled', 'no_api_key', 'нет ключа')"
    )
    yt = next(c for c in operator.get("/quota").json()["collectors"] if c["collector"] == "yt_data")
    assert yt["key_set"] is True and yt["state"] == "idle" and yt["reason"] is None
    assert "set-in-env" not in str(operator.get("/quota").json())
