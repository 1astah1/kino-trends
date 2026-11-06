"""Коллектор YouTube Data API (критерии M4, ARCHITECTURE.md §10.2) на `httpx.MockTransport` и тестовой БД:

* пачки по 50 id, 1 единица квоты за пачку, квота по «ведру» ключа (общий ключ / свой ключ куста);
* пропавший из ответа ролик → `removed_by_platform` + алерт `publication_removed` (вернулся — статус
  восстановлен, алерт закрыт);
* `rejectionReason=uploaderAccountSuspended` → критический алерт `channel_sanction_signal` + инцидент канала;
* квота учитывается, на 8000 единиц опрос откладывается до сброса суток квоты (America/Los_Angeles);
* без ключа — коллектор `disabled/no_api_key`, публикации ждут ручного ввода, вызовов нет;
* сеть лежит — пауза с ростом, с 3-й ошибки `failing` + алерт `collector_failing`, ручной ввод;
* лестница опроса, интерполяция `views_24h`, уточнение `published_at` и канала из API, аренда.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from autoshorts.domain.settings_schema import RuntimeSettings
from autoshorts.repos import metrics as metrics_repo
from autoshorts.services.metrics import PollDeps, poll_metrics, poll_schedule_hours
from tests.unit.test_collectors_support import (
    UC_MAIN,
    FakeYouTube,
    add_snapshot,
    make_publication,
    secrets_from,
    seed_channels,
)

KEYS = {"YOUTUBE_API_KEY": "key-main", "YOUTUBE_API_KEY_SOLO": "key-solo"}


@pytest.fixture
def db(db_pool: Any, db_conn: Any) -> Any:
    seed_channels(db_conn)
    return db_conn


def run(
    fake: FakeYouTube,
    *,
    keys: dict[str, str] | None = None,
    settings: RuntimeSettings | None = None,
    **kw: Any,
):
    deps = PollDeps(
        transport=httpx.MockTransport(fake),
        sleep=lambda _s: None,
        secret=secrets_from(KEYS if keys is None else keys),
    )
    return poll_metrics(settings=settings or RuntimeSettings(), deps=deps, **kw)


def pub_row(db: Any, pid: int) -> dict[str, Any]:
    return db.execute("SELECT * FROM publications WHERE id = %s", (pid,)).fetchone()


def quota(db: Any, bucket: str = "default") -> dict[str, Any] | None:
    return db.execute(
        "SELECT * FROM api_quota_usage WHERE api = 'youtube_data' AND cluster_id = %s"
        " AND quota_day = (now() AT TIME ZONE 'America/Los_Angeles')::date",
        (bucket,),
    ).fetchone()


def open_alerts(db: Any, code: str) -> list[dict[str, Any]]:
    return db.execute(
        "SELECT * FROM alerts WHERE code = %s AND status <> 'resolved' ORDER BY id", (code,)
    ).fetchall()


def state(db: Any, collector: str = "yt_data", bucket: str = "default") -> dict[str, Any] | None:
    return db.execute(
        "SELECT * FROM collector_state WHERE collector = %s AND cluster_id = %s", (collector, bucket)
    ).fetchone()


# ---------------------------------------------------------------- пачки и квота


def test_batches_of_50_per_key_bucket_and_quota_accounting(db: Any) -> None:
    main = [make_publication(db) for _ in range(120)]
    for _ in range(3):
        make_publication(db, channel_id="yt_solo")
    not_due = make_publication(db, next_poll_at=datetime.now(UTC) + timedelta(hours=2))
    fake = FakeYouTube(views={main[0]["platform_video_id"]: 5555})
    report = run(fake)

    by_key: dict[str, list[int]] = {}
    for call in fake.video_calls:
        by_key.setdefault(call["params"]["key"], []).append(len(call["params"]["id"].split(",")))
    assert by_key == {"key-main": [50, 50, 20], "key-solo": [3]}
    polled = {vid for c in fake.video_calls for vid in c["params"]["id"].split(",")}
    assert not_due["platform_video_id"] not in polled

    assert report.counts() == {"snapshot": 123}
    assert (
        db.execute("SELECT count(*) AS n FROM metrics_snapshots WHERE source = 'yt_data'").fetchone()["n"]
        == 123
    )
    first = pub_row(db, main[0]["id"])
    assert first["latest_views"] == 5555 and first["latest_likes"] == 12 and first["latest_comments"] == 3
    assert (
        first["metrics_collector"] == "yt_data"
        and first["poll_failures"] == 0
        and first["manual_due_at"] is None
    )
    # лестница: опубликован 30 ч назад → следующая точка 36 ч, её номер в расписании
    sched = poll_schedule_hours(RuntimeSettings().metrics_poll_ladder_hours)
    assert first["next_poll_at"] == main[0]["published_at"] + timedelta(hours=36)
    assert sched[first["poll_step"]] == 36.0
    # квота: единица за пачку, по ведру ключа
    q_main, q_solo = quota(db), quota(db, "solo")
    assert (q_main["units"], q_main["calls"], q_main["errors"]) == (3, 3, 0)
    assert (q_solo["units"], q_solo["calls"]) == (1, 1)
    assert state(db)["state"] == "ok" and state(db, bucket="solo")["state"] == "ok"
    # повторный прогон: всё уже по расписанию, вызовов нет
    before = len(fake.calls)
    assert run(fake).outcomes == [] and len(fake.calls) == before


def test_quota_hard_stop_defers_polling_until_reset(db: Any) -> None:
    pubs = [make_publication(db) for _ in range(120)]
    db.execute(
        "INSERT INTO api_quota_usage (api, quota_day, cluster_id, units, calls)"
        " VALUES ('youtube_data', (now() AT TIME ZONE 'America/Los_Angeles')::date, 'default', 7999, 7999)"
    )
    fake = FakeYouTube()
    report = run(fake)
    assert len(fake.video_calls) == 1  # 7999 + 1 = 8000 — последняя разрешённая пачка
    counts = report.counts()
    assert counts == {"snapshot": 50, "deferred": 70}
    assert quota(db)["units"] == 8000
    reset = db.execute(
        "SELECT ((date_trunc('day', now() AT TIME ZONE 'America/Los_Angeles') + interval '1 day')"
        " AT TIME ZONE 'America/Los_Angeles') AS r"
    ).fetchone()["r"]
    deferred = [pub_row(db, p["id"]) for p in pubs[50:]]
    assert all(reset <= r["next_poll_at"] < reset + timedelta(minutes=10) for r in deferred)
    assert all(r["latest_views"] is None for r in deferred)
    st = state(db)
    assert st["state"] == "paused" and st["reason"] == "quota_exhausted" and st["paused_until"] == reset
    alerts = open_alerts(db, "quota_80")
    assert (
        len(alerts) == 1
        and alerts[0]["details"]["exhausted"] is True
        and alerts[0]["details"]["units"] == 8000
    )
    # пока пауза — новые созревшие публикации откладываются без вызовов
    extra = make_publication(db)
    report2 = run(fake)
    assert len(fake.video_calls) == 1 and report2.counts() == {"deferred": 1}
    assert pub_row(db, extra["id"])["next_poll_at"] >= reset


def test_soft_quota_alert_below_hard_stop(db: Any) -> None:
    make_publication(db)
    db.execute(
        "INSERT INTO api_quota_usage (api, quota_day, cluster_id, units)"
        " VALUES ('youtube_data', (now() AT TIME ZONE 'America/Los_Angeles')::date, 'default', 7000)"
    )
    settings = RuntimeSettings(yt_quota_soft_pct=70, yt_quota_hard_units=9000)
    run(FakeYouTube(), settings=settings)
    st = state(db)
    assert st["state"] == "degraded" and st["reason"] == "quota_soft_limit"
    alert = open_alerts(db, "quota_80")[0]
    assert alert["details"]["exhausted"] is False and alert["details"]["units"] == 7001


def test_quota_exceeded_answer_pauses_collector(db: Any) -> None:
    pubs = [make_publication(db) for _ in range(3)]
    body = {
        "error": {
            "code": 403,
            "message": "quota",
            "errors": [{"reason": "quotaExceeded", "domain": "youtube.quota"}],
        }
    }
    report = run(FakeYouTube(error=(403, body)))
    assert report.counts() == {"deferred": 3}
    assert quota(db)["units"] >= RuntimeSettings().yt_quota_hard_units
    assert state(db)["state"] == "paused"
    assert all(pub_row(db, p["id"])["poll_failures"] == 0 for p in pubs)


# ---------------------------------------------------------------- снятие и санкции


def test_missing_video_becomes_removed_with_alert_and_can_return(db: Any) -> None:
    keep = make_publication(db)
    gone = make_publication(db)
    fake = FakeYouTube(missing={gone["platform_video_id"]})
    report = run(fake)
    assert sorted(o.outcome for o in report.outcomes) == ["removed", "snapshot"]
    row = pub_row(db, gone["id"])
    assert row["status"] == "removed_by_platform" and "пропал из ответа" in row["status_reason"]
    assert row["next_poll_at"] >= datetime.now(UTC) + timedelta(hours=23)  # перепроверка раз в сутки
    alerts = open_alerts(db, "publication_removed")
    assert len(alerts) == 1 and alerts[0]["severity"] == "warning"
    assert alerts[0]["details"]["publication_id"] == gone["id"] and alerts[0]["details"]["code"] == "missing"
    assert pub_row(db, keep["id"])["status"] == "live"

    # снова в ответе — статус восстановлен, алерт закрыт
    db.execute(
        "UPDATE publications SET next_poll_at = now() - interval '1 minute' WHERE id = %s", (gone["id"],)
    )
    fake.missing.clear()
    report = run(fake)
    assert [o.outcome for o in report.outcomes] == ["restored"]
    row = pub_row(db, gone["id"])
    assert row["status"] == "live" and row["status_reason"] is None and row["latest_views"] == 1000
    assert open_alerts(db, "publication_removed") == []


def test_private_by_operator_missing_is_not_an_alert(db: Any) -> None:
    pub = make_publication(db, status="private")
    report = run(FakeYouTube(missing={pub["platform_video_id"]}))
    assert [o.outcome for o in report.outcomes] == ["missing"]
    assert pub_row(db, pub["id"])["status"] == "private" and open_alerts(db, "publication_removed") == []


def test_uploader_account_suspended_is_critical_channel_alert(db: Any) -> None:
    bad = make_publication(db)
    other = make_publication(db)
    fake = FakeYouTube(
        overrides={
            bad["platform_video_id"]: {
                "uploadStatus": "rejected",
                "rejectionReason": "uploaderAccountSuspended",
            }
        }
    )
    run(fake)
    row = pub_row(db, bad["id"])
    assert row["status"] == "removed_by_platform" and "uploaderAccountSuspended" in row["status_reason"]
    sanction = open_alerts(db, "channel_sanction_signal")
    assert len(sanction) == 1
    assert (
        sanction[0]["severity"] == "critical"
        and sanction[0]["dedup_key"] == "channel_sanction_signal:yt_main"
    )
    assert sanction[0]["details"]["rejection_reason"] == "uploaderAccountSuspended"
    assert len(open_alerts(db, "publication_removed")) == 1
    incidents = db.execute("SELECT * FROM channel_incidents WHERE channel_id = 'yt_main'").fetchall()
    assert len(incidents) == 1 and incidents[0]["kind"] == "sanction"
    assert pub_row(db, other["id"])["status"] == "live"
    # повторный опрос не плодит алерты и инциденты
    db.execute(
        "UPDATE publications SET next_poll_at = now() - interval '1 minute' WHERE id = %s", (bad["id"],)
    )
    run(fake)
    assert len(open_alerts(db, "channel_sanction_signal")) == 1
    assert db.execute("SELECT count(*) AS n FROM channel_incidents").fetchone()["n"] == 1


def test_copyright_rejection_and_region_block_mark_claim(db: Any) -> None:
    rejected = make_publication(db)
    blocked = make_publication(db)
    fake = FakeYouTube(
        overrides={
            rejected["platform_video_id"]: {"uploadStatus": "rejected", "rejectionReason": "copyright"}
        }
    )
    run(fake)
    assert pub_row(db, rejected["id"])["has_claim"] is True
    assert pub_row(db, rejected["id"])["status"] == "removed_by_platform"
    assert open_alerts(db, "channel_sanction_signal") == []
    # регион: ролик жив, но с признаком претензии
    db.execute(
        "UPDATE publications SET next_poll_at = now() - interval '1 minute' WHERE id = %s", (blocked["id"],)
    )

    def with_region(request: httpx.Request) -> httpx.Response:
        resp = FakeYouTube()(request)
        body = resp.json()
        for item in body["items"]:
            item["contentDetails"]["regionRestriction"] = {"blocked": ["DE"]}
        return httpx.Response(200, json=body)

    deps = PollDeps(
        transport=httpx.MockTransport(with_region), sleep=lambda _s: None, secret=secrets_from(KEYS)
    )
    poll_metrics(settings=RuntimeSettings(), deps=deps)
    row = pub_row(db, blocked["id"])
    assert row["status"] == "live" and row["has_claim"] is True and "DE" in row["status_reason"]


# ---------------------------------------------------------------- без ключа и ошибки сети


def test_no_api_key_disables_collector_and_asks_for_manual_input(db: Any) -> None:
    pub = make_publication(db)
    fake = FakeYouTube()
    report = run(fake, keys={})
    assert fake.calls == [] and report.counts() == {"manual_due": 1}
    st = state(db)
    assert st["state"] == "disabled" and st["reason"] == "no_api_key" and "YOUTUBE_API_KEY" in st["message"]
    row = pub_row(db, pub["id"])
    assert row["manual_due_at"] is not None and row["manual_due_at"] <= datetime.now(UTC)
    assert row["next_poll_at"] == pub["published_at"] + timedelta(hours=36)  # лестница идёт дальше
    # свой ключ куста не подменяется общим: без YOUTUBE_API_KEY_SOLO куст solo тоже выключен
    make_publication(db, channel_id="yt_solo")
    run(fake, keys={"YOUTUBE_API_KEY": "key-main"})
    assert state(db, bucket="solo")["state"] == "disabled" and fake.calls == []


def test_network_failures_back_off_then_fail_collector(db: Any) -> None:
    pub = make_publication(db)
    fake = FakeYouTube(fail_network=True)
    for attempt in range(1, 4):
        start = datetime.now(UTC)
        report = run(fake)
        assert report.counts() == {"failed": 1}
        row = pub_row(db, pub["id"])
        assert row["poll_failures"] == attempt and "сеть недоступна" in row["last_poll_error"]
        if attempt < 3:
            delay = timedelta(minutes=15 * 2 ** (attempt - 1))
            assert start + delay - timedelta(seconds=5) <= row["next_poll_at"] <= datetime.now(UTC) + delay
            assert row["manual_due_at"] is None
        db.execute(
            "UPDATE publications SET next_poll_at = now() - interval '1 minute' WHERE id = %s", (pub["id"],)
        )
    st = state(db)
    assert st["state"] == "failing" and st["reason"] == "network_error" and st["consecutive_failures"] == 3
    assert pub_row(db, pub["id"])["manual_due_at"] is not None  # с 3-й ошибки — ручной ввод
    assert len(open_alerts(db, "collector_failing")) == 1
    assert quota(db)["errors"] == 3
    # сеть вернулась — снимок, ошибки сброшены, алерт закрыт
    fake.fail_network = False
    run(fake)
    row = pub_row(db, pub["id"])
    assert row["poll_failures"] == 0 and row["last_poll_error"] is None and row["manual_due_at"] is None
    assert state(db)["state"] == "ok" and open_alerts(db, "collector_failing") == []


def test_bad_key_is_auth_error(db: Any) -> None:
    make_publication(db)
    body = {
        "error": {
            "code": 400,
            "message": "API key not valid. Please pass a valid API key.",
            "errors": [{"reason": "badRequest"}],
        }
    }
    run(FakeYouTube(error=(400, body)))
    st = state(db)
    assert (
        st["reason"] == "auth_error"
        and st["state"] == "degraded"
        and "key-main" not in (st["last_error"] or "")
    )


# ---------------------------------------------------------------- лестница, интерполяция, уточнения


def test_interpolated_age_views_and_published_at_from_api(db: Any) -> None:
    published = datetime.now(UTC) - timedelta(hours=80)
    pub = make_publication(db, published_at=published)
    add_snapshot(db, pub, age_hours=12, views=1000)
    add_snapshot(db, pub, age_hours=36, views=4000)
    fake = FakeYouTube(views={pub["platform_video_id"]: 8800})
    run(fake)
    row = pub_row(db, pub["id"])
    assert row["views_24h"] == 2500  # между 12 ч и 36 ч
    assert 7000 < row["views_72h"] < 8800  # между 36 ч и сейчас (80 ч)
    assert row["views_7d"] is None and row["latest_views"] == 8800
    assert row["verify_status"] == "ok" and UC_MAIN in row["verify_detail"]

    # API уточняет время публикации: возраст снимков пересчитан
    true_published = published - timedelta(hours=6)
    fake.snippet[pub["platform_video_id"]] = {
        "publishedAt": true_published.isoformat().replace("+00:00", "Z")
    }
    db.execute(
        "UPDATE publications SET next_poll_at = now() - interval '1 minute' WHERE id = %s", (pub["id"],)
    )
    run(fake)
    row = pub_row(db, pub["id"])
    assert abs(row["published_at"] - true_published) < timedelta(seconds=1)
    ages = [float(r["age_hours"]) for r in metrics_repo.snapshots(db, pub["id"])]
    assert ages[:2] == [18.0, 42.0]
    assert row["views_24h"] == 1750  # (18 ч, 1000) → (42 ч, 4000)


def test_channel_mismatch_from_api(db: Any) -> None:
    pub = make_publication(db, verify_status="ok")
    fake = FakeYouTube(snippet={})
    fake.snippet[pub["platform_video_id"]] = {"channelId": "UC" + "z" * 22}
    run(fake)
    row = pub_row(db, pub["id"])
    assert row["verify_status"] == "channel_mismatch" and "UCzzzz" in row["verify_detail"]


def test_forced_poll_and_lease(db: Any) -> None:
    later = make_publication(db, next_poll_at=datetime.now(UTC) + timedelta(hours=5))
    due = make_publication(db)
    # аренда: второй «воркер» не берёт то, что взял первый
    first = metrics_repo.claim_due(db, lease_sec=900, limit=10, platform="youtube")
    second = metrics_repo.claim_due(db, lease_sec=900, limit=10, platform="youtube")
    assert [r["id"] for r in first] == [due["id"]] and second == []
    assert first[0]["due_at"] == due["next_poll_at"]
    # внеочередной опрос по id — вне расписания
    fake = FakeYouTube()
    report = run(fake, publication_ids=[later["id"]])
    assert [o.publication_id for o in report.outcomes] == [later["id"]]
    assert fake.video_calls[0]["params"]["id"] == later["platform_video_id"]
    assert pub_row(db, later["id"])["latest_views"] == 1000


def test_cancel_releases_claimed_publications(db: Any) -> None:
    import threading

    pub = make_publication(db)
    cancel = threading.Event()
    cancel.set()
    report = run(FakeYouTube(), cancel=cancel)
    assert report.counts() == {"released": 1}
    assert pub_row(db, pub["id"])["next_poll_at"] == pub["next_poll_at"]


def test_manual_collector_publications_only_arm_manual_due(db: Any) -> None:
    pub = make_publication(db, metrics_collector="manual")
    fake = FakeYouTube()
    report = run(fake)
    assert fake.calls == [] and report.counts() == {"manual_due": 1}
    assert pub_row(db, pub["id"])["manual_due_at"] == pub["next_poll_at"]


def test_channel_stats_snapshots(db: Any) -> None:
    from autoshorts.services.metrics import collect_channel_stats

    fake = FakeYouTube(channels_missing={"UC" + "b" * 22})
    deps = PollDeps(transport=httpx.MockTransport(fake), sleep=lambda _s: None, secret=secrets_from(KEYS))
    out = collect_channel_stats(settings=RuntimeSettings(), deps=deps)
    assert out["youtube"]["snapshots"] == 1 and out["youtube"]["missing"] == ["yt_solo"]
    snap = db.execute("SELECT * FROM channel_snapshots WHERE channel_id = 'yt_main'").fetchone()
    assert (snap["subscribers"], snap["total_views"], snap["video_count"], snap["source"]) == (
        1500,
        99000,
        42,
        "yt_data",
    )
    assert (
        db.execute("SELECT subscribers FROM channels WHERE id = 'yt_main'").fetchone()["subscribers"] == 1500
    )
    assert [a["dedup_key"] for a in open_alerts(db, "channel_sanction_signal")] == ["channel_missing:yt_solo"]
    assert out["vk"]["skipped"] == "not_configured"
    assert quota(db)["units"] == 1 and quota(db, "solo")["units"] == 1


def test_processing_bug_in_one_publication_does_not_break_the_batch(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoshorts.services import metrics as metrics_service

    good = make_publication(db)
    bad = make_publication(db)
    real = metrics_service._apply_youtube

    def flaky(conn: Any, row: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
        if row["id"] == bad["id"]:
            raise RuntimeError("boom")
        return real(conn, row, *args, **kwargs)

    monkeypatch.setattr(metrics_service, "_apply_youtube", flaky)
    report = run(FakeYouTube())
    assert sorted(o.outcome for o in report.outcomes) == ["failed", "snapshot"]
    assert pub_row(db, good["id"])["latest_views"] == 1000
    row = pub_row(db, bad["id"])
    assert row["poll_failures"] == 1 and "сбой обработки: RuntimeError: boom" in row["last_poll_error"]
    assert row["next_poll_at"] > datetime.now(UTC) + timedelta(minutes=10)
    assert (
        db.execute(
            "SELECT count(*) AS n FROM metrics_snapshots WHERE publication_id = %s", (bad["id"],)
        ).fetchone()["n"]
        == 0
    )


def test_freshness_and_latest_source_helpers_for_other_streams(db: Any) -> None:
    from autoshorts.db.pool import connection
    from autoshorts.services.metrics import freshness_map, latest_source_map

    fresh = make_publication(db)
    stale = make_publication(db, next_poll_at=datetime.now(UTC) - timedelta(hours=3))
    db.execute(
        "UPDATE publications SET next_poll_at = now() + interval '1 hour' WHERE id = %s", (fresh["id"],)
    )
    add_snapshot(db, fresh, age_hours=10, views=50, source="manual")
    add_snapshot(db, fresh, age_hours=20, views=90, source="yt_data")
    db.execute(
        "UPDATE publications SET manual_due_at = now() - interval '5 minutes' WHERE id = %s", (stale["id"],)
    )
    with connection() as conn:
        fm = freshness_map(conn, [fresh["id"], stale["id"]])
        sources = latest_source_map(conn, [fresh["id"], stale["id"]])
    assert sources == {fresh["id"]: "yt_data"}
    a, b = fm[fresh["id"]], fm[stale["id"]]
    assert a["collector"] == "yt_data" and a["stale"] is False and a["manual_due"] is False
    assert (
        a["last_captured_at"] == fresh["published_at"] + timedelta(hours=20) and a["metrics_age_min"] >= 599
    )
    assert b["stale"] is True and b["manual_due"] is True and b["last_captured_at"] is None


def test_vk_channel_stats_with_token(db: Any) -> None:
    from autoshorts.services.metrics import collect_channel_stats

    def vk(request: httpx.Request) -> httpx.Response:
        assert request.url.params["group_ids"] == "123"
        return httpx.Response(200, json={"response": {"groups": [{"id": 123, "members_count": 4321}]}})

    deps = PollDeps(
        transport=httpx.MockTransport(vk),
        sleep=lambda _s: None,
        secret=secrets_from({"VK_ACCESS_TOKEN": "t"}),
    )
    db.execute("UPDATE channels SET platform_channel_ref = NULL WHERE platform = 'youtube'")
    out = collect_channel_stats(settings=RuntimeSettings(), deps=deps)
    assert out["vk"] == {"snapshots": 1, "skipped": None}
    snap = db.execute("SELECT * FROM channel_snapshots WHERE channel_id = 'vk_main'").fetchone()
    assert snap["subscribers"] == 4321 and snap["source"] == "vk_api"


def test_malformed_legacy_id_does_not_poison_the_batch(db: Any) -> None:
    good = make_publication(db)
    bad = make_publication(db, video_id="legacy-id-with-bad-format")
    fake = FakeYouTube()
    report = run(fake)
    assert sorted(o.outcome for o in report.outcomes) == ["failed", "snapshot"]
    assert fake.video_calls[0]["params"]["id"] == good["platform_video_id"]
    row = pub_row(db, bad["id"])
    assert row["poll_failures"] == 1 and "некорректный ID ролика YouTube" in row["last_poll_error"]
    assert pub_row(db, good["id"])["latest_views"] == 1000
