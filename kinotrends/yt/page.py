"""Детали ролика со страницы youtube.com/shorts/<id>: в HTML вшит ytInitialPlayerResponse.

Страница отдаётся с датацентровых IP без проверки на бота (сам плеер режется). Из неё берём
название, описание, длительность, дату публикации, категорию, ключевые слова, канал, просмотры
и список стран, где ролик доступен. Лайков и комментариев здесь нет: их даёт RSS канала
(последние 15 роликов) или YouTube Data API, если задан ключ.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
from datetime import datetime, timezone
from typing import Awaitable, Callable

import httpx

from ..config import USER_AGENT
from .models import VideoDetails

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
    "Accept": "text/html,application/xhtml+xml",
}
COOKIES = {"SOCS": "CAI", "CONSENT": "YES+cb", "PREF": "hl=ru&gl=RU"}
_PR = re.compile(r"ytInitialPlayerResponse\s*=\s*")


def _to_utc(s: str | None) -> str | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def extract_player_response(html: str) -> dict | None:
    m = _PR.search(html)
    if not m:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(html, m.end())
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def parse_page(video_id: str, html: str, final_url: str = "") -> VideoDetails:
    d = VideoDetails(video_id=video_id, source="page")
    pr = extract_player_response(html)
    if pr is None:
        d.status = "bot" if ("не бот" in html or "not a bot" in html) else "error"
        d.error = "нет ytInitialPlayerResponse"
        return d
    ps = pr.get("playabilityStatus") or {}
    vd = pr.get("videoDetails") or {}
    mf = (pr.get("microformat") or {}).get("playerMicroformatRenderer") or {}
    if not vd:
        status = ps.get("status")
        reason = (ps.get("reason") or "")[:200]
        if status == "LOGIN_REQUIRED":
            d.status = "bot"
        elif status in ("ERROR", "UNPLAYABLE") or not status:
            d.status = "removed"
        else:
            d.status = "error"
        d.error = f"{status}: {reason}"
        return d
    d.title = vd.get("title") or None
    d.description = vd.get("shortDescription") or None
    d.channel_id = vd.get("channelId")
    d.channel_name = vd.get("author") or mf.get("ownerChannelName")
    try:
        d.duration_s = int(vd.get("lengthSeconds") or mf.get("lengthSeconds") or 0) or None
    except ValueError:
        d.duration_s = None
    d.published_at = _to_utc(mf.get("publishDate") or mf.get("uploadDate"))
    d.category = mf.get("category")
    d.keywords = list(vd.get("keywords") or [])
    vc = mf.get("viewCount") or vd.get("viewCount")
    try:
        d.views = int(vc) if vc is not None else None
    except ValueError:
        d.views = None
    ac = mf.get("availableCountries")
    d.avail_ru = ("RU" in ac) if isinstance(ac, list) and ac else None
    if final_url:
        d.is_short = "/shorts/" in final_url
    if vd.get("isLiveContent"):
        d.status = "not_short"
    return d


async def fetch_one(client: httpx.AsyncClient, video_id: str) -> VideoDetails:
    url = f"https://www.youtube.com/shorts/{video_id}"
    try:
        r = await client.get(url)
    except httpx.HTTPError as e:
        return VideoDetails(video_id=video_id, status="error", error=f"сеть: {e.__class__.__name__}")
    if r.status_code == 429:
        return VideoDetails(video_id=video_id, status="ratelimit", error="HTTP 429")
    if r.status_code == 404:
        return VideoDetails(video_id=video_id, status="removed", error="HTTP 404")
    if r.status_code != 200:
        return VideoDetails(video_id=video_id, status="error", error=f"HTTP {r.status_code}")
    return parse_page(video_id, r.text, str(r.url))


async def fetch_many(
    video_ids: list[str],
    concurrency: int = 4,
    on_result: Callable[[VideoDetails], Awaitable[None] | None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    max_consecutive_blocks: int = 6,
) -> dict:
    """Параллельная выгрузка страниц. Останавливается, если подряд идут блокировки."""
    sem = asyncio.Semaphore(concurrency)
    stats = {"ok": 0, "removed": 0, "blocked": 0, "error": 0, "aborted": False}
    consecutive = 0
    lock = asyncio.Lock()
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(headers=HEADERS, cookies=COOKIES, timeout=40, follow_redirects=True, limits=limits) as client:
        async def worker(vid: str) -> None:
            nonlocal consecutive
            if stats["aborted"] or (should_stop and should_stop()):
                return
            async with sem:
                if stats["aborted"]:
                    return
                await asyncio.sleep(random.uniform(0.15, 0.6))
                d = await fetch_one(client, vid)
            async with lock:
                if d.status in ("bot", "ratelimit"):
                    stats["blocked"] += 1
                    consecutive += 1
                    if consecutive >= max_consecutive_blocks:
                        stats["aborted"] = True
                elif d.status == "error":
                    stats["error"] += 1
                    consecutive = 0
                else:
                    stats["ok" if d.status in ("ok", "not_short") else "removed"] += 1
                    consecutive = 0
            if on_result:
                res = on_result(d)
                if asyncio.iscoroutine(res):
                    await res

        await asyncio.gather(*(worker(v) for v in video_ids))
    return stats
