"""RSS-лента канала: точные даты публикации, просмотры и лайки последних 15 роликов. Без ключей."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx

from ..config import USER_AGENT

NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


def fetch_feed(channel_id: str, timeout: float = 30.0) -> list[dict]:
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    with httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}) as c:
        r = c.get(url)
    if r.status_code != 200:
        raise RuntimeError(f"RSS HTTP {r.status_code}")
    root = ET.fromstring(r.content)
    out = []
    for e in root.findall("a:entry", NS):
        vid = e.findtext("yt:videoId", default="", namespaces=NS)
        if not vid:
            continue
        st = e.find("media:group/media:community/media:statistics", NS)
        sr = e.find("media:group/media:community/media:starRating", NS)
        pub = e.findtext("a:published", default="", namespaces=NS)
        out.append({
            "video_id": vid,
            "title": e.findtext("a:title", default="", namespaces=NS) or None,
            "published_at": pub.replace("+00:00", "Z") if pub else None,
            "description": e.findtext("media:group/media:description", default="", namespaces=NS) or None,
            "views": int(st.get("views")) if st is not None and st.get("views") else None,
            "likes": int(sr.get("count")) if sr is not None and sr.get("count") else None,
        })
    return out
