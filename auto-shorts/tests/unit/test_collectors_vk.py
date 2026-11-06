"""Коллектор VK (M4-A): vk_api при токене → yt-dlp (с паузой) → ручной ввод. БД — тестовая, сеть — подмены."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from autoshorts.collectors.vk_video import YtdlpMetaFetcher
from autoshorts.domain.settings_schema import RuntimeSettings
from autoshorts.services.metrics import PollDeps, poll_metrics
from tests.unit.test_collectors_support import make_publication, secrets_from, seed_channels


@pytest.fixture
def db(db_pool: Any, db_conn: Any) -> Any:
    seed_channels(db_conn)
    return db_conn


class FakeYtdlp:
    def __init__(self, answers: dict[str, Any] | None = None, *, available: bool = True) -> None:
        self.answers = answers or {}
        self.urls: list[str] = []
        self._available = available

    def extract(self, url: str) -> dict[str, Any]:
        self.urls.append(url)
        answer = self.answers.get(url.rsplit("clip", 1)[-1], {"view_count": 300, "like_count": 7})
        if isinstance(answer, Exception):
            raise answer
        return answer

    def fetcher(self) -> YtdlpMetaFetcher:
        fetcher = YtdlpMetaFetcher(min_interval_sec=0, sleep=lambda _s: None, extract=self.extract)
        if not self._available:
            fetcher.available = lambda: False  # type: ignore[method-assign]
        return fetcher


def run(ytdlp: FakeYtdlp, *, keys: dict[str, str] | None = None, transport: Any = None, **kw: Any):
    deps = PollDeps(
        transport=transport, ytdlp=ytdlp.fetcher(), sleep=lambda _s: None, secret=secrets_from(keys or {})
    )
    return poll_metrics(settings=RuntimeSettings(), deps=deps, **kw)


def pub_row(db: Any, pid: int) -> dict[str, Any]:
    return db.execute("SELECT * FROM publications WHERE id = %s", (pid,)).fetchone()


def test_ytdlp_snapshot(db: Any) -> None:
    pub = make_publication(db, channel_id="vk_main", platform="vk")
    fake = FakeYtdlp()
    report = run(fake)
    assert report.counts() == {"snapshot": 1}
    assert fake.urls == [f"https://vk.com/clip{pub['platform_video_id']}"]
    row = pub_row(db, pub["id"])
    assert row["latest_views"] == 300 and row["latest_likes"] == 7 and row["metrics_collector"] == "ytdlp"
    snap = db.execute("SELECT * FROM metrics_snapshots WHERE publication_id = %s", (pub["id"],)).fetchone()
    assert snap["source"] == "ytdlp"
    st = db.execute("SELECT * FROM collector_state WHERE collector = 'ytdlp'").fetchone()
    assert st["state"] == "ok"


def test_ytdlp_rate_limited_per_run(db: Any) -> None:
    from autoshorts.services import metrics

    pubs = [
        make_publication(db, channel_id="vk_main", platform="vk") for _ in range(metrics.VK_MAX_PER_RUN + 3)
    ]
    fake = FakeYtdlp()
    report = run(fake)
    assert len(fake.urls) == metrics.VK_MAX_PER_RUN and report.counts() == {
        "snapshot": metrics.VK_MAX_PER_RUN
    }
    waiting = [p for p in pubs if pub_row(db, p["id"])["latest_views"] is None]
    assert len(waiting) == 3  # остальные — в следующем прогоне
    assert all(pub_row(db, p["id"])["next_poll_at"] <= datetime.now(UTC) for p in waiting)


def test_ytdlp_missing_tool_asks_for_manual_input(db: Any) -> None:
    pub = make_publication(db, channel_id="vk_main", platform="vk")
    report = run(FakeYtdlp(available=False))
    assert report.counts() == {"manual_due": 1}
    assert pub_row(db, pub["id"])["manual_due_at"] is not None
    st = db.execute("SELECT * FROM collector_state WHERE collector = 'ytdlp'").fetchone()
    assert st["state"] == "disabled" and st["reason"] == "tool_missing"


def test_ytdlp_gone_twice_marks_removed(db: Any) -> None:
    pub = make_publication(db, channel_id="vk_main", platform="vk")
    fake = FakeYtdlp({pub["platform_video_id"]: RuntimeError("ERROR: [vk] Видео удалено или недоступно")})
    run(fake)
    row = pub_row(db, pub["id"])
    assert (
        row["status"] == "live" and row["poll_failures"] == 1 and row["last_poll_error"].startswith("gone:")
    )
    db.execute(
        "UPDATE publications SET next_poll_at = now() - interval '1 minute' WHERE id = %s", (pub["id"],)
    )
    report = run(fake)
    assert report.counts() == {"removed": 1}
    row = pub_row(db, pub["id"])
    assert row["status"] == "removed_by_platform"
    alerts = db.execute(
        "SELECT * FROM alerts WHERE code = 'publication_removed' AND status = 'open'"
    ).fetchall()
    assert len(alerts) == 1 and alerts[0]["details"]["platform"] == "vk"


def test_ytdlp_repeated_errors_fall_back_to_manual(db: Any) -> None:
    pub = make_publication(db, channel_id="vk_main", platform="vk")
    fake = FakeYtdlp({pub["platform_video_id"]: {"title": "без счётчика"}})
    for _ in range(3):
        db.execute(
            "UPDATE publications SET next_poll_at = now() - interval '1 minute' WHERE id = %s", (pub["id"],)
        )
        run(fake)
    row = pub_row(db, pub["id"])
    assert row["poll_failures"] == 3 and row["manual_due_at"] is not None and row["status"] == "live"
    st = db.execute("SELECT * FROM collector_state WHERE collector = 'ytdlp'").fetchone()
    assert st["state"] == "failing"


def test_vk_api_first_then_ytdlp_for_the_rest(db: Any) -> None:
    by_api = make_publication(db, channel_id="vk_main", platform="vk")
    by_ytdlp = make_publication(db, channel_id="vk_main", platform="vk")
    owner, vid = by_api["platform_video_id"].split("_")
    seen: list[dict[str, Any]] = []

    def vk(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        item = {"owner_id": int(owner), "id": int(vid), "views": 999, "likes": {"count": 5}, "comments": 1}
        return httpx.Response(200, json={"response": {"count": 1, "items": [item]}})

    fake = FakeYtdlp()
    report = run(fake, keys={"VK_ACCESS_TOKEN": "vk-token"}, transport=httpx.MockTransport(vk))
    assert report.counts() == {"snapshot": 2}
    assert len(seen) == 1 and seen[0]["access_token"] == "vk-token"
    assert (
        pub_row(db, by_api["id"])["latest_views"] == 999
        and pub_row(db, by_api["id"])["metrics_collector"] == "vk_api"
    )
    assert fake.urls == [f"https://vk.com/clip{by_ytdlp['platform_video_id']}"]
    assert pub_row(db, by_ytdlp["id"])["latest_views"] == 300
    q = db.execute("SELECT * FROM api_quota_usage WHERE api = 'vk_api'").fetchone()
    assert q["calls"] == 1


def test_ytdlp_without_views_stores_partial_snapshot_and_asks_for_views(db: Any) -> None:
    """Как на живом VK (27.09.2026): yt-dlp отдаёт лайки и комментарии, а просмотров нет."""
    pub = make_publication(db, channel_id="vk_main", platform="vk")
    fake = FakeYtdlp({pub["platform_video_id"]: {"like_count": 197, "comment_count": 4, "extractor": "vk"}})
    report = run(fake)
    assert report.counts() == {"manual_due": 1}
    row = pub_row(db, pub["id"])
    assert row["latest_views"] is None and row["latest_likes"] == 197 and row["latest_comments"] == 4
    assert row["manual_due_at"] is not None and row["poll_failures"] == 0 and row["last_poll_error"] is None
    assert row["next_poll_at"] == pub["published_at"] + timedelta(hours=36)  # лестница, не пауза ошибки
    snap = db.execute("SELECT * FROM metrics_snapshots WHERE publication_id = %s", (pub["id"],)).fetchone()
    assert snap["source"] == "ytdlp" and snap["views"] is None and snap["likes"] == 197
    st = db.execute("SELECT * FROM collector_state WHERE collector = 'ytdlp'").fetchone()
    assert st["state"] == "degraded" and st["reason"] == "manual_only" and "просмотры" in st["message"]
