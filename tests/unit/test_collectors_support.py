"""Помощники тестов коллекторов (M4-A). Тестов здесь нет.

`seed_channels(db)` — ниша, два куста (`main` с общим ключом, `solo` со своим секретом), каналы YouTube
в обоих и канал VK. `make_publication(db, …)` — ролик и публикация SQL-ом в обход регистрации.
`FakeYouTube` — обработчик `httpx.MockTransport` для videos.list/channels.list: пишет каждый вызов,
отдаёт ролики со статусами из `overrides`, пропускает `missing`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from tests.api.test_jobs_support import make_job

UC_MAIN = "UC" + "a" * 22
UC_SOLO = "UC" + "b" * 22


def seed_channels(db: Any) -> dict[str, Any]:
    db.execute(
        "INSERT INTO niches (id, label, status, current_version, config)"
        " VALUES ('taro', 'Таро', 'active', 1, '{}')"
    )
    db.execute(
        "INSERT INTO clusters (id, title, youtube_api_key_secret) VALUES ('main', 'Основной', NULL),"
        " ('solo', 'Свой ключ', 'YOUTUBE_API_KEY_SOLO')"
    )
    db.execute(
        """INSERT INTO channels (id, niche_id, platform, cluster, title, status, default_product_id, url,
                                 platform_channel_ref)
           VALUES ('yt_main', 'taro', 'youtube', 'main', 'Таро · основной', 'active', 'umka',
                   'https://www.youtube.com/@yt_main', %s),
                  ('yt_solo', 'taro', 'youtube', 'solo', 'Таро · соло', 'active', 'umka',
                   'https://www.youtube.com/@yt_solo', %s),
                  ('vk_main', 'taro', 'vk', 'main', 'Таро · VK', 'active', 'umka', NULL, '-123')""",
        (UC_MAIN, UC_SOLO),
    )
    return {"yt": "yt_main", "solo": "yt_solo", "vk": "vk_main"}


def yt_id(n: int) -> str:
    return f"vid{n:08d}"


def make_publication(
    db: Any,
    *,
    channel_id: str = "yt_main",
    platform: str = "youtube",
    video_id: str | None = None,
    published_at: datetime | None = None,
    next_poll_at: datetime | None | str = "due",
    status: str = "live",
    metrics_collector: str | None = "default",
    verify_status: str = "pending",
) -> dict[str, Any]:
    job = make_job(db, channel_id=channel_id, niche_id="taro", status="published")
    now = datetime.now(UTC)
    published_at = published_at or now - timedelta(hours=30)
    if next_poll_at == "due":
        next_poll_at = now - timedelta(minutes=1)
    if metrics_collector == "default":
        metrics_collector = "yt_data" if platform == "youtube" else "ytdlp"
    video_id = video_id or (yt_id(int(job["id"])) if platform == "youtube" else f"-123_{job['id']}")
    canonical = (
        f"https://www.youtube.com/shorts/{video_id}"
        if platform == "youtube"
        else f"https://vk.com/clip{video_id}"
    )
    return db.execute(
        """INSERT INTO publications (job_id, channel_id, platform, platform_video_id, master_file_path, url,
                                     canonical_url, published_at, status, next_poll_at, metrics_collector,
                                     verify_status, platform_kind)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (
            job["id"],
            channel_id,
            platform,
            video_id,
            f"masters/2026/09/{job['video_uid']}.mp4",
            canonical,
            canonical,
            published_at,
            status,
            next_poll_at,
            metrics_collector,
            verify_status,
            "short" if platform == "youtube" else "clip",
        ),
    ).fetchone()


def add_snapshot(
    db: Any, pub: dict[str, Any], *, age_hours: float, views: int, source: str = "yt_data"
) -> None:
    captured = pub["published_at"] + timedelta(hours=age_hours)
    db.execute(
        """INSERT INTO metrics_snapshots (publication_id, captured_at, age_hours, views, source)
           VALUES (%s, %s, %s, %s, %s)""",
        (pub["id"], captured, age_hours, views, source),
    )


def video_item(
    video_id: str, *, views: int = 1000, channel_id: str = UC_MAIN, **status: Any
) -> dict[str, Any]:
    st = {"uploadStatus": "processed", "privacyStatus": "public", "license": "youtube", "embeddable": True}
    st.update(status)
    return {
        "kind": "youtube#video",
        "id": video_id,
        "snippet": {
            "publishedAt": None,
            "channelId": channel_id,
            "title": "Расклад",
            "thumbnails": {"high": {"url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"}},
        },
        "statistics": {"viewCount": str(views), "likeCount": "12", "commentCount": "3", "favoriteCount": "0"},
        "status": st,
        "contentDetails": {"duration": "PT21S"},
    }


@dataclass
class FakeYouTube:
    """Эмулятор YouTube Data API v3 для `httpx.MockTransport`."""

    views: dict[str, int] = field(default_factory=dict)
    missing: set[str] = field(default_factory=set)
    overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    snippet: dict[str, dict[str, Any]] = field(default_factory=dict)
    error: tuple[int, dict[str, Any]] | None = None
    fail_network: bool = False
    channels_missing: set[str] = field(default_factory=set)
    calls: list[dict[str, Any]] = field(default_factory=list)
    default_views: int = 1000

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.calls.append({"path": request.url.path, "params": params})
        if self.fail_network:
            raise httpx.ConnectError("connection refused", request=request)
        if self.error is not None:
            status, body = self.error
            return httpx.Response(status, json=body)
        ids = [i for i in params.get("id", "").split(",") if i]
        if request.url.path.endswith("/channels"):
            items = [
                {
                    "id": cid,
                    "statistics": {
                        "subscriberCount": "1500",
                        "viewCount": "99000",
                        "videoCount": "42",
                        "hiddenSubscriberCount": False,
                    },
                }
                for cid in ids
                if cid not in self.channels_missing
            ]
            return httpx.Response(200, json={"kind": "youtube#channelListResponse", "items": items})
        items = []
        for vid in ids:
            if vid in self.missing:
                continue
            item = video_item(
                vid, views=self.views.get(vid, self.default_views), **self.overrides.get(vid, {})
            )
            item["snippet"].update(self.snippet.get(vid, {}))
            if item["snippet"].get("publishedAt") is None:
                item["snippet"].pop("publishedAt")
            items.append(item)
        return httpx.Response(200, json={"kind": "youtube#videoListResponse", "items": items})

    @property
    def video_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["path"].endswith("/videos")]


def secrets_from(mapping: dict[str, str]) -> Any:
    return lambda name, conn: mapping.get(name)


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
