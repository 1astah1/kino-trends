"""Миниатюры роликов: кэш в var/thumbs, источник i.ytimg.com."""
from __future__ import annotations

import httpx

from .config import THUMB_DIR, USER_AGENT


def thumb_path(video_id: str):
    return THUMB_DIR / f"{video_id}.jpg"


def get_thumb(video_id: str, stored_url: str | None = None, timeout: float = 15.0) -> bytes | None:
    path = thumb_path(video_id)
    if path.exists():
        return path.read_bytes()
    urls = []
    if stored_url:
        urls.append(stored_url)
    urls += [f"https://i.ytimg.com/vi/{video_id}/oar2.jpg", f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"]
    with httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as cl:
        for u in urls:
            try:
                r = cl.get(u)
            except httpx.HTTPError:
                continue
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image") and len(r.content) > 1000:
                path.write_bytes(r.content)
                return r.content
    return None
