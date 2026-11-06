"""Коллекторы M4-A без БД: лестница опроса, интерполяция, разбор YouTube Data API, HTTP-ретраи,
VK (vk_api и yt-dlp), обложки. Сеть — только `httpx.MockTransport`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from autoshorts.collectors import http as chttp
from autoshorts.collectors.http import CollectorError, ErrorKind, make_client, redact, request
from autoshorts.collectors.thumbnails import fetch_thumbnail, host_allowed
from autoshorts.collectors.vk_video import VkApiClient, YtdlpMetaFetcher, is_gone_message, vk_clip_url
from autoshorts.collectors.youtube_data import (
    YouTubeDataClient,
    chunks,
    classify_error,
    interpret_video,
    parse_video,
)
from autoshorts.domain.enums import PublicationStatus
from autoshorts.domain.settings_schema import RuntimeSettings
from autoshorts.services.metrics import interpolate_views, next_poll, poll_schedule_hours
from tests.unit.test_collectors_support import FakeYouTube, video_item, yt_id

NO_SLEEP = lambda _s: None  # noqa: E731


# ---------------------------------------------------------------- лестница


def test_schedule_follows_settings_then_daily_then_weekly() -> None:
    ladder = RuntimeSettings().metrics_poll_ladder_hours
    sched = poll_schedule_hours(ladder)
    assert sched[: len(ladder)] == [float(h) for h in ladder]
    # раз в сутки до 30 дней, раз в неделю до 90
    daily = [h for h in sched if 168 < h <= 720]
    assert daily == [168.0 + 24 * k for k in range(1, 24)]
    weekly = [h for h in sched if h > 720]
    assert weekly[0] == 720 + 168 and weekly[-1] == 90 * 24
    assert all(b - a <= 168 for a, b in zip(weekly, weekly[1:], strict=False))
    # ровные возрасты, нужные когортам, есть в расписании
    for h in (1, 24, 72, 168, 336, 720):
        assert float(h) in sched
    assert sched == sorted(set(sched))


def test_next_poll_picks_first_point_after_now() -> None:
    sched = poll_schedule_hours([1, 3, 24, 72, 168])
    pub = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    step, at = next_poll(pub, pub + timedelta(minutes=10), sched)
    assert (step, at) == (0, pub + timedelta(hours=1))
    step, at = next_poll(pub, pub + timedelta(hours=1, minutes=1), sched)
    assert (step, at) == (1, pub + timedelta(hours=3))
    # опоздавший на сутки опрос не догоняет пропущенные точки
    step, at = next_poll(pub, pub + timedelta(hours=50), sched)
    assert at == pub + timedelta(hours=72) and sched[step] == 72
    # точка через минуту — уже «сейчас», берётся следующая
    step, at = next_poll(pub, pub + timedelta(hours=24) - timedelta(minutes=1), sched)
    assert at == pub + timedelta(hours=72)
    assert next_poll(pub, pub + timedelta(days=91), sched) is None


def test_interpolation_linear_without_extrapolation() -> None:
    pts = [(12.0, 1000), (36.0, 4000), (96.0, 10000)]
    assert interpolate_views(pts, 24) == 2500
    assert interpolate_views(pts, 72) == 7600
    assert interpolate_views(pts, 36) == 4000
    assert interpolate_views(pts, 12) == 1000
    assert interpolate_views(pts, 11.9) is None  # раньше первого снимка
    assert interpolate_views(pts, 168) is None  # позже последнего: без экстраполяции
    assert interpolate_views([], 24) is None
    assert interpolate_views([(24.0, 700)], 24) == 700
    # несколько снимков одного возраста — берётся последний
    assert interpolate_views([(10.0, 100), (20.0, 300), (20.0, 500), (30.0, 700)], 25) == 600


# ---------------------------------------------------------------- YouTube: разбор и вердикт


def test_parse_video_and_verdicts() -> None:
    obs = parse_video(video_item("abcdefghijk", views=321))
    assert (obs.views, obs.likes, obs.comments, obs.privacy_status) == (321, 12, 3, "public")
    verdict = interpret_video(obs)
    assert verdict.status is PublicationStatus.LIVE and not verdict.removed and not verdict.claim

    assert interpret_video(None).status is PublicationStatus.REMOVED_BY_PLATFORM
    assert interpret_video(None).removed and interpret_video(None).code == "missing"

    susp = interpret_video(
        parse_video(
            video_item("abcdefghijk", uploadStatus="rejected", rejectionReason="uploaderAccountSuspended")
        )
    )
    assert susp.removed and susp.sanction and susp.status is PublicationStatus.REMOVED_BY_PLATFORM
    assert "аккаунт автора заблокирован" in (susp.reason or "")

    claim = interpret_video(
        parse_video(video_item("abcdefghijk", uploadStatus="rejected", rejectionReason="copyright"))
    )
    assert claim.removed and claim.claim and not claim.sanction

    unlisted = interpret_video(parse_video(video_item("abcdefghijk", privacyStatus="unlisted")))
    assert unlisted.status is PublicationStatus.UNLISTED

    item = video_item("abcdefghijk")
    item["contentDetails"]["regionRestriction"] = {"blocked": ["DE", "AT"]}
    region = interpret_video(parse_video(item))
    assert region.status is PublicationStatus.LIVE and region.claim and "DE, AT" in (region.reason or "")

    hidden = video_item("abcdefghijk")
    del hidden["statistics"]["likeCount"]
    assert parse_video(hidden).likes is None


def test_classify_google_errors() -> None:
    def err(status: int, reason: str, message: str = "") -> httpx.Response:
        return httpx.Response(
            status, json={"error": {"code": status, "message": message, "errors": [{"reason": reason}]}}
        )

    assert classify_error(err(403, "quotaExceeded")).kind is ErrorKind.QUOTA
    assert classify_error(err(403, "dailyLimitExceeded")).kind is ErrorKind.QUOTA
    assert classify_error(err(403, "rateLimitExceeded")).kind is ErrorKind.RATE_LIMITED
    bad_key = classify_error(err(400, "badRequest", "API key not valid. Please pass a valid API key."))
    assert bad_key.kind is ErrorKind.AUTH
    assert classify_error(err(403, "accessNotConfigured")).kind is ErrorKind.AUTH
    assert classify_error(err(500, "backendError")).kind is ErrorKind.HTTP
    assert classify_error(httpx.Response(502, text="<html>")).kind is ErrorKind.HTTP


# ---------------------------------------------------------------- YouTube: клиент на MockTransport


def test_videos_list_is_one_call_per_50_ids() -> None:
    fake = FakeYouTube(missing={yt_id(7)}, views={yt_id(3): 4242})
    ids = [yt_id(i) for i in range(1, 121)]
    with make_client(transport=httpx.MockTransport(fake)) as client:
        api = YouTubeDataClient("test-key", client, sleep=NO_SLEEP)
        results = [api.videos_list(batch) for batch in chunks(ids)]
        with pytest.raises(ValueError):
            api.videos_list(ids[:51])
        with pytest.raises(ValueError):
            api.videos_list(["not-an-id"])
    assert [len(c["params"]["id"].split(",")) for c in fake.video_calls] == [50, 50, 20]
    assert all(c["params"]["part"] == "snippet,statistics,status,contentDetails" for c in fake.video_calls)
    assert all(c["params"]["key"] == "test-key" for c in fake.video_calls)
    assert results[0].found[yt_id(3)].views == 4242
    assert results[0].missing == [yt_id(7)]
    assert sum(r.units for r in results) == 3


def test_videos_list_errors_carry_attempts_and_hide_key() -> None:
    fake = FakeYouTube(
        error=(403, {"error": {"code": 403, "message": "quota", "errors": [{"reason": "quotaExceeded"}]}})
    )
    with make_client(transport=httpx.MockTransport(fake)) as client:
        api = YouTubeDataClient("secret-key-123", client, sleep=NO_SLEEP)
        with pytest.raises(CollectorError) as exc:
            api.videos_list([yt_id(1)])
    assert exc.value.kind is ErrorKind.QUOTA and exc.value.http_attempts == 1
    assert "secret-key-123" not in str(exc.value)


def test_http_retries_5xx_and_network_then_gives_up() -> None:
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    slept: list[float] = []
    with make_client(transport=httpx.MockTransport(flaky)) as client:
        res = request(client, "GET", "https://example.invalid/x?key=abc", sleep=slept.append)
    assert res.response.status_code == 200 and res.http_attempts == 3 and len(slept) == 2

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with make_client(transport=httpx.MockTransport(down)) as client:
        with pytest.raises(CollectorError) as exc:
            request(client, "GET", "https://example.invalid/", sleep=NO_SLEEP)
    assert exc.value.kind is ErrorKind.NETWORK and exc.value.http_attempts == 0


def test_redact_and_transport_override(monkeypatch: pytest.MonkeyPatch) -> None:
    assert redact("GET https://x/y?id=1&key=AIzaSECRET&v=2") == "GET https://x/y?id=1&key=***&v=2"
    assert redact("access_token=tok123 failed") == "access_token=*** failed"
    monkeypatch.setattr(chttp, "transport_override", httpx.MockTransport(lambda r: httpx.Response(204)))
    with make_client(proxy="http://proxy.invalid:3128") as client:
        assert client.get("https://anything.invalid/").status_code == 204


# ---------------------------------------------------------------- VK


def test_vk_api_video_get() -> None:
    seen: list[dict[str, Any]] = []

    def vk(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "response": {
                    "count": 1,
                    "items": [
                        {
                            "owner_id": -123,
                            "id": 456,
                            "views": 777,
                            "likes": {"count": 9},
                            "comments": 2,
                            "reposts": {"count": 1},
                        },
                    ],
                }
            },
        )

    with make_client(transport=httpx.MockTransport(vk)) as client:
        api = VkApiClient("vk-token", client, sleep=NO_SLEEP)
        found = api.video_get(["-123_456", "-123_457"])
    assert seen[0]["videos"] == "-123_456,-123_457" and seen[0]["v"]
    obs = found["-123_456"]
    assert (obs.views, obs.likes, obs.comments, obs.shares, obs.source) == (777, 9, 2, 1, "vk_api")
    assert "-123_457" not in found

    def denied(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"error": {"error_code": 5, "error_msg": "User authorization failed"}}
        )

    with make_client(transport=httpx.MockTransport(denied)) as client:
        with pytest.raises(CollectorError) as exc:
            VkApiClient("vk-token", client, sleep=NO_SLEEP).video_get(["-1_2"])
    assert exc.value.kind is ErrorKind.AUTH and "vk-token" not in exc.value.message


def test_ytdlp_fetcher_paces_calls_and_flags_gone() -> None:
    clock = {"t": 100.0}
    slept: list[float] = []

    def sleep(s: float) -> None:
        slept.append(s)
        clock["t"] += s

    def extract(url: str) -> dict[str, Any]:
        if url.endswith("_2"):
            raise RuntimeError("ERROR: [vk] -1_2: This video has been removed from public access")
        if url.endswith("_3"):
            return {"title": "без счётчика"}
        return {"view_count": 150, "like_count": 4, "comment_count": 1, "extractor": "vk"}

    fetcher = YtdlpMetaFetcher(min_interval_sec=3.0, clock=lambda: clock["t"], sleep=sleep, extract=extract)
    ok = fetcher.fetch("-1_1", vk_clip_url("-1_1"))
    gone = fetcher.fetch("-1_2", vk_clip_url("-1_2"))
    empty = fetcher.fetch("-1_3", vk_clip_url("-1_3"))
    assert ok.found and ok.views == 150 and ok.source == "ytdlp"
    assert not gone.found and gone.gone and gone.error and gone.error.startswith("yt-dlp: [vk]")
    assert not empty.found and not empty.gone and empty.error_kind is ErrorKind.BAD_RESPONSE
    assert not empty.partial and not ok.partial
    partial = YtdlpMetaFetcher(min_interval_sec=0, extract=lambda u: {"like_count": 5}).fetch("-1_4", "u")
    assert partial.partial and not partial.found and partial.likes == 5
    assert slept == [3.0, 3.0]  # пауза между вызовами, первый — сразу
    assert is_gone_message("Видео удалено") and not is_gone_message("HTTP Error 503")
    assert vk_clip_url("-1_2", "https://vk.com/clip-1_2") == "https://vk.com/clip-1_2"


# ---------------------------------------------------------------- обложки


def test_thumbnail_only_from_platform_hosts(tmp_path: Any) -> None:
    jpeg = b"\xff\xd8\xff\xe0" + b"x" * 100
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, content=jpeg if r.url.path.endswith(".jpg") else b"<html>")
    )
    assert host_allowed("https://i.ytimg.com/vi/x/hqdefault.jpg")
    assert not host_allowed("https://evil.example/ytimg.com.jpg") and not host_allowed(
        "http://i.ytimg.com/a.jpg"
    )
    with make_client(transport=transport) as client:
        dest = tmp_path / "thumbs" / "youtube" / "abc.jpg"
        res = fetch_thumbnail(client, "https://i.ytimg.com/vi/abc/hqdefault.jpg", dest)
        assert res.saved and dest.read_bytes() == jpeg
        assert fetch_thumbnail(client, "https://i.ytimg.com/vi/abc/hqdefault.jpg", dest).skipped == "exists"
        assert (
            fetch_thumbnail(client, "https://evil.example/a.jpg", tmp_path / "b.jpg").skipped
            == "host_not_allowed"
        )
        assert fetch_thumbnail(client, "https://i.ytimg.com/page", tmp_path / "c.jpg").skipped == "not_jpeg"
