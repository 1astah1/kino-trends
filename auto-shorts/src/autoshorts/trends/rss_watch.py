"""
RSS-фиды каналов конкурентов — без ключа, без квоты (ТЗ §8).
youtube.com/feeds/videos.xml отдаёт 15 последних роликов канала. Факт публикации приходит
за минуты — этого достаточно, чтобы поставить видео в очередь наблюдения на 72 часа.

Готового «API трендов» нет ни у одной нужной площадки (YouTube убил Trending в июле 2025) —
это единственный бесплатный источник свежих публикаций конкурентов.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx

YT_RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


@dataclass
class FeedVideo:
    video_id: str
    title: str
    published: str
    channel_id: str


def fetch_channel_feed(channel_id: str, timeout: float = 10.0) -> list[FeedVideo]:
    url = YT_RSS_URL.format(channel_id=channel_id)
    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)

    videos = []
    for entry in root.findall("atom:entry", _NS):
        video_id_el = entry.find("yt:videoId", _NS)
        title_el = entry.find("atom:title", _NS)
        published_el = entry.find("atom:published", _NS)
        if video_id_el is None or video_id_el.text is None:
            continue
        videos.append(FeedVideo(
            video_id=video_id_el.text,
            title=title_el.text if title_el is not None else "",
            published=published_el.text if published_el is not None else "",
            channel_id=channel_id,
        ))
    return videos


def is_shorts_duration(duration_sec: float, threshold: float = 180.0) -> bool:
    """Эвристика отделения Shorts от обычных видео — по длительности, см. ТЗ §8."""
    return duration_sec <= threshold
