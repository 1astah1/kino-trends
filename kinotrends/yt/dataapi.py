"""YouTube Data API v3 (необязательно). videos.list стоит 1 единицу квоты на 50 роликов,
даёт лайки, комментарии, длительность, дату, теги и описание. Квота по умолчанию 10 000 единиц в сутки."""
from __future__ import annotations

import re

import httpx

from .models import VideoDetails

_DUR = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")
CATEGORY_NAMES = {
    "1": "Film & Animation", "2": "Autos & Vehicles", "10": "Music", "15": "Pets & Animals", "17": "Sports",
    "19": "Travel & Events", "20": "Gaming", "22": "People & Blogs", "23": "Comedy", "24": "Entertainment",
    "25": "News & Politics", "26": "Howto & Style", "27": "Education", "28": "Science & Technology", "29": "Nonprofits & Activism",
}


def parse_duration(s: str | None) -> int | None:
    if not s:
        return None
    m = _DUR.fullmatch(s)
    if not m:
        return None
    h, mi, se = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + se


def fetch_videos(api_key: str, video_ids: list[str], timeout: float = 30.0) -> tuple[list[VideoDetails], int]:
    """Возвращает детали и потраченную квоту. Ролики, которых нет в ответе, помечаются removed."""
    out: list[VideoDetails] = []
    quota = 0
    with httpx.Client(timeout=timeout) as c:
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i + 50]
            r = c.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"part": "snippet,statistics,contentDetails", "id": ",".join(batch), "key": api_key, "hl": "ru"},
            )
            quota += 1
            if r.status_code != 200:
                raise RuntimeError(f"Data API HTTP {r.status_code}: {r.text[:200]}")
            seen = set()
            for it in r.json().get("items", []):
                sn = it.get("snippet", {})
                st = it.get("statistics", {})
                cd = it.get("contentDetails", {})
                vid = it["id"]
                seen.add(vid)
                rr = cd.get("regionRestriction") or {}
                avail_ru = None
                if rr.get("blocked"):
                    avail_ru = "RU" not in rr["blocked"]
                elif rr.get("allowed"):
                    avail_ru = "RU" in rr["allowed"]
                out.append(VideoDetails(
                    video_id=vid, status="ok", source="api",
                    title=sn.get("title"), description=sn.get("description"),
                    channel_id=sn.get("channelId"), channel_name=sn.get("channelTitle"),
                    duration_s=parse_duration(cd.get("duration")),
                    published_at=(sn.get("publishedAt") or "").replace(".000Z", "Z") or None,
                    category=CATEGORY_NAMES.get(str(sn.get("categoryId")), sn.get("categoryId")),
                    keywords=list(sn.get("tags") or []),
                    views=int(st["viewCount"]) if st.get("viewCount") else None,
                    likes=int(st["likeCount"]) if st.get("likeCount") else None,
                    comments=int(st["commentCount"]) if st.get("commentCount") else None,
                    avail_ru=avail_ru,
                ))
            for vid in batch:
                if vid not in seen:
                    out.append(VideoDetails(video_id=vid, status="removed", source="api", error="нет в ответе API"))
    return out, quota


def fetch_top_comments(api_key: str, video_id: str, n: int = 3, timeout: float = 20.0) -> list[str]:
    """Верхние комментарии (по релевантности): закреплённый комментарий с названием обычно первый. 1 единица квоты."""
    with httpx.Client(timeout=timeout) as c:
        r = c.get("https://www.googleapis.com/youtube/v3/commentThreads",
                  params={"part": "snippet", "videoId": video_id, "order": "relevance", "maxResults": n, "textFormat": "plainText", "key": api_key})
    if r.status_code == 403 and "commentsDisabled" in r.text:
        return []
    if r.status_code != 200:
        raise RuntimeError(f"commentThreads HTTP {r.status_code}: {r.text[:160]}")
    out = []
    for it in r.json().get("items", []):
        top = (((it.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {})
        text = (top.get("textDisplay") or "").strip()
        if text:
            out.append(text[:300])
    return out
