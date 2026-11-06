"""Обложки своих публикаций (необязательно, docs/ARCHITECTURE.md §6.5, раскладка `thumbs/` из §8.2).

Веб не ходит на чужие CDN (§7.7), поэтому обложка, если нужна, скачивается к себе:
`MEDIA_ROOT/thumbs/<platform>/<platform_video_id>.jpg`. Скачивание включается явно
(`collectors-once --thumbnails`, `payload.thumbnails=true` у `metrics_poll`) и идёт только с
хостов площадок (`ALLOWED_HOST_SUFFIXES`), только JPEG и не больше 2 МБ; уже скачанная обложка
не перекачивается.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from autoshorts.collectors.http import CollectorError, request

__all__ = [
    "ALLOWED_HOST_SUFFIXES",
    "THUMB_MAX_BYTES",
    "ThumbResult",
    "fetch_thumbnail",
    "host_allowed",
    "youtube_thumbnail_url",
]

THUMB_MAX_BYTES = 2 * 1024 * 1024
ALLOWED_HOST_SUFFIXES = (
    "ytimg.com",
    "ggpht.com",
    "userapi.com",
    "vkuserphoto.ru",
    "vkuservideo.net",
    "vk.com",
    "vk.me",
    "mycdn.me",
    "okcdn.ru",
)
JPEG_MAGIC = b"\xff\xd8\xff"


def youtube_thumbnail_url(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def host_allowed(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        return False
    return any(host == s or host.endswith("." + s) for s in ALLOWED_HOST_SUFFIXES)


@dataclass
class ThumbResult:
    saved: bool
    path: Path
    bytes: int = 0
    skipped: str | None = None  # exists | host_not_allowed | not_jpeg | too_large | http_<code> | error


def fetch_thumbnail(client: httpx.Client, url: str, dest: Path, *, overwrite: bool = False) -> ThumbResult:
    """Скачать обложку в `dest` атомарно. Ошибки не бросаются — возвращаются в `skipped`."""
    from autoshorts.core.storage import atomic_write_bytes

    if dest.exists() and not overwrite:
        return ThumbResult(False, dest, dest.stat().st_size, "exists")
    if not host_allowed(url):
        return ThumbResult(False, dest, 0, "host_not_allowed")
    try:
        res = request(client, "GET", url, retries=1)
    except CollectorError:
        return ThumbResult(False, dest, 0, "error")
    resp = res.response
    if resp.status_code != 200:
        return ThumbResult(False, dest, 0, f"http_{resp.status_code}")
    data = resp.content
    if len(data) > THUMB_MAX_BYTES:
        return ThumbResult(False, dest, len(data), "too_large")
    if not data.startswith(JPEG_MAGIC):
        return ThumbResult(False, dest, len(data), "not_jpeg")
    atomic_write_bytes(dest, data)
    return ThumbResult(True, dest, len(data))
