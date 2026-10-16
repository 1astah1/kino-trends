"""Плоские списки роликов через yt-dlp: страница хэштега (вкладка Shorts) и вкладка Shorts канала.

Эти страницы отдаются без проверки «вы не бот», в отличие от страницы плеера.
Отдают id, заголовок (не всегда), просмотры и миниатюру. Дат и длительности здесь нет.
"""
from __future__ import annotations

import logging
import urllib.parse

import yt_dlp

log = logging.getLogger("kinotrends.flat")


class _Logger:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def debug(self, msg: str) -> None:  # noqa: D401
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        self.errors.append(str(msg))


def _opts(depth: int, logger: _Logger) -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "playlistend": depth,
        "ignoreerrors": True,
        "socket_timeout": 30,
        "retries": 2,
        "extractor_retries": 2,
        "geo_bypass_country": "RU",
        "extractor_args": {"youtube": {"lang": ["ru"]}, "youtubetab": {"approximate_date": ["true"]}},
        "logger": logger,
    }


def _entries(info: dict | None) -> list[dict]:
    if not info:
        return []
    out = []
    for e in info.get("entries") or []:
        if not e or not e.get("id"):
            continue
        thumbs = e.get("thumbnails") or []
        thumb = thumbs[-1]["url"] if thumbs else None
        url = e.get("url") or ""
        out.append({
            "video_id": e["id"],
            "title": (e.get("title") or "").strip() or None,
            "views": e.get("view_count"),
            "url": url,
            "is_short": "/shorts/" in url if url else None,
            "thumb": thumb,
            "channel_id": e.get("channel_id"),
            "channel": e.get("channel") or e.get("uploader"),
            "duration": e.get("duration"),
        })
    return out


def hashtag_shorts(tag: str, depth: int = 200) -> dict:
    url = f"https://www.youtube.com/hashtag/{urllib.parse.quote(tag)}/shorts"
    logger = _Logger()
    with yt_dlp.YoutubeDL(_opts(depth, logger)) as ydl:
        info = ydl.extract_info(url, download=False)
    return {"entries": _entries(info), "errors": logger.errors, "url": url}


def channel_shorts(channel_id: str, depth: int = 60) -> dict:
    if channel_id.startswith("@"):
        url = f"https://www.youtube.com/{channel_id}/shorts"
    else:
        url = f"https://www.youtube.com/channel/{channel_id}/shorts"
    logger = _Logger()
    with yt_dlp.YoutubeDL(_opts(depth, logger)) as ydl:
        info = ydl.extract_info(url, download=False)
    meta = {}
    if info:
        meta = {
            "channel_id": info.get("channel_id"),
            "name": info.get("channel") or info.get("uploader"),
            "handle": info.get("uploader_id"),
            "followers": info.get("channel_follower_count"),
        }
    return {"entries": _entries(info), "errors": logger.errors, "url": url, "channel": meta}


def search_shorts(query: str, depth: int = 30) -> dict:
    """Поиск YouTube по запросу; оставляем только то, что похоже на шортсы (адрес /shorts/ или до 3 минут)."""
    logger = _Logger()
    with yt_dlp.YoutubeDL(_opts(depth, logger)) as ydl:
        info = ydl.extract_info(f"ytsearch{depth}:{query}", download=False)
    entries = [e for e in _entries(info) if e.get("is_short") or (e.get("duration") and e["duration"] <= 180)]
    for e in entries:
        e["is_short"] = True
    return {"entries": entries, "errors": logger.errors, "url": f"ytsearch:{query}"}
