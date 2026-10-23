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


# ---------- популярные видео: чарты, поиск по лицензии, каналы ----------
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
PARTS_FULL = "snippet,contentDetails,statistics,status,topicDetails"
CATEGORY_RU = {
    "1": "кино и анимация", "2": "авто", "10": "музыка", "15": "животные", "17": "спорт", "19": "путешествия", "20": "игры",
    "22": "люди и блоги", "23": "юмор", "24": "развлечения", "25": "новости", "26": "как сделать и стиль", "27": "образование",
    "28": "наука и техника", "29": "общество",
}


def parse_item(it: dict) -> dict:
    """Полная карточка ролика из ответа videos.list в плоский словарь."""
    sn = it.get("snippet") or {}
    st = it.get("statistics") or {}
    cd = it.get("contentDetails") or {}
    ss = it.get("status") or {}
    td = it.get("topicDetails") or {}
    thumbs = sn.get("thumbnails") or {}
    thumb = (thumbs.get("maxres") or thumbs.get("standard") or thumbs.get("high") or thumbs.get("medium") or {}).get("url")
    hint = (sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or "").strip().lower()
    rr = cd.get("regionRestriction") or {}
    blocked_ru = ("RU" in (rr.get("blocked") or [])) if rr.get("blocked") else (("RU" not in rr["allowed"]) if rr.get("allowed") else False)
    cat = str(sn.get("categoryId") or "")
    topics = [u.rsplit("/", 1)[-1].replace("_", " ") for u in (td.get("topicCategories") or [])]
    return {
        "video_id": it["id"], "title": sn.get("title"), "description": sn.get("description"),
        "channel_id": sn.get("channelId"), "channel_name": sn.get("channelTitle"),
        "duration_s": parse_duration(cd.get("duration")), "published_at": (sn.get("publishedAt") or "").replace(".000Z", "Z") or None,
        "category_id": cat, "category": CATEGORY_RU.get(cat) or CATEGORY_NAMES.get(cat) or cat, "tags": list(sn.get("tags") or []),
        "lang_hint": hint[:2] if hint else None,
        "views": int(st["viewCount"]) if st.get("viewCount") else None, "likes": int(st["likeCount"]) if st.get("likeCount") else None,
        "comments": int(st["commentCount"]) if st.get("commentCount") else None,
        "license": ss.get("license"), "licensed_content": 1 if cd.get("licensedContent") else 0, "embeddable": 1 if ss.get("embeddable") else 0,
        "made_for_kids": 1 if ss.get("madeForKids") else 0, "caption": 1 if str(cd.get("caption")).lower() == "true" else 0,
        "definition": cd.get("definition"), "topics": topics, "thumb": thumb, "blocked_ru": 1 if blocked_ru else 0,
    }


def _check(r: httpx.Response, what: str) -> dict:
    if r.status_code != 200:
        raise RuntimeError(f"{what} HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def most_popular(api_key: str, region: str, category_id: str | None = None, pages: int = 4, timeout: float = 30.0) -> tuple[list[dict], int]:
    """Чарт «самое популярное» по стране (и категории): до 200 роликов, 1 единица квоты за страницу.
    Если чарта для категории нет, YouTube отвечает 404: возвращаем то, что успели собрать."""
    items: list[dict] = []
    quota = 0
    token = None
    with httpx.Client(timeout=timeout) as c:
        for p in range(max(1, pages)):
            params = {"part": PARTS_FULL, "chart": "mostPopular", "regionCode": region, "maxResults": 50, "key": api_key, "hl": "ru"}
            if category_id:
                params["videoCategoryId"] = str(category_id)
            if token:
                params["pageToken"] = token
            r = c.get(VIDEOS_URL, params=params)
            quota += 1
            if r.status_code == 404 or (r.status_code == 400 and "chart" in r.text.lower()):
                break
            data = _check(r, f"chart {region}/{category_id or 'all'}")
            for i, it in enumerate(data.get("items", [])):
                d = parse_item(it)
                d["rank"] = p * 50 + i + 1
                items.append(d)
            token = data.get("nextPageToken")
            if not token:
                break
    return items, quota


def videos_raw(api_key: str, video_ids: list[str], timeout: float = 30.0) -> tuple[list[dict], int]:
    """Полные карточки по списку id: 1 единица квоты на 50 роликов. Отсутствующие в ответе ролики удалены или закрыты."""
    out: list[dict] = []
    quota = 0
    with httpx.Client(timeout=timeout) as c:
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i + 50]
            r = c.get(VIDEOS_URL, params={"part": PARTS_FULL, "id": ",".join(batch), "key": api_key, "hl": "ru"})
            quota += 1
            for it in _check(r, "videos.list").get("items", []):
                out.append(parse_item(it))
    return out, quota


def search_ids(api_key: str, q: str, region: str = "RU", published_after: str | None = None, cc: bool = True, duration: str | None = None,
               order: str = "viewCount", pages: int = 1, lang: str = "ru", timeout: float = 30.0) -> tuple[list[str], int]:
    """search.list: 100 единиц квоты за страницу. cc=True ограничивает лицензией Creative Commons."""
    ids: list[str] = []
    quota = 0
    token = None
    with httpx.Client(timeout=timeout) as c:
        for _ in range(max(1, pages)):
            params = {"part": "id", "type": "video", "q": q, "regionCode": region, "relevanceLanguage": lang, "order": order, "maxResults": 50,
                      "safeSearch": "none", "key": api_key}
            if cc:
                params["videoLicense"] = "creativeCommon"
            if published_after:
                params["publishedAfter"] = published_after
            if duration:
                params["videoDuration"] = duration
            if token:
                params["pageToken"] = token
            r = c.get(SEARCH_URL, params=params)
            quota += 100
            data = _check(r, "search.list")
            ids += [it["id"]["videoId"] for it in data.get("items", []) if (it.get("id") or {}).get("videoId")]
            token = data.get("nextPageToken")
            if not token:
                break
    return ids, quota


def channels_subs(api_key: str, channel_ids: list[str], timeout: float = 30.0) -> tuple[dict[str, dict], int]:
    """Подписчики и число роликов канала: 1 единица квоты на 50 каналов."""
    out: dict[str, dict] = {}
    quota = 0
    with httpx.Client(timeout=timeout) as c:
        for i in range(0, len(channel_ids), 50):
            batch = channel_ids[i:i + 50]
            r = c.get(CHANNELS_URL, params={"part": "snippet,statistics", "id": ",".join(batch), "key": api_key})
            quota += 1
            for it in _check(r, "channels.list").get("items", []):
                st = it.get("statistics") or {}
                out[it["id"]] = {"title": (it.get("snippet") or {}).get("title"), "subs": int(st["subscriberCount"]) if st.get("subscriberCount") else None,
                                 "videos": int(st["videoCount"]) if st.get("videoCount") else None, "country": (it.get("snippet") or {}).get("country")}
    return out, quota
