"""Популярные видео: разбор карточки API, оценка чистоты, выборки, подборка."""
import os
from pathlib import Path

import pytest

from kinotrends import db, popular
from kinotrends.yt import dataapi

TMP = Path(os.environ["KT_VAR"])


def api_item(vid="abc123def45", cat="20", lic="youtube", licensed=True, lang="ru", dur="PT1M30S", title="Ролик", channel="Канал"):
    return {"id": vid, "snippet": {"title": title, "description": "описание", "channelId": "UC" + vid, "channelTitle": channel, "categoryId": cat,
                                   "publishedAt": "2026-10-20T10:00:00Z", "tags": ["игры", "roblox"], "defaultAudioLanguage": lang,
                                   "thumbnails": {"high": {"url": "https://i.ytimg.com/vi/x/hqdefault.jpg"}}},
            "contentDetails": {"duration": dur, "licensedContent": licensed, "caption": "false", "definition": "hd"},
            "statistics": {"viewCount": "500000", "likeCount": "20000", "commentCount": "300"},
            "status": {"license": lic, "embeddable": True, "madeForKids": False},
            "topicDetails": {"topicCategories": ["https://en.wikipedia.org/wiki/Video_game_culture"]}}


def test_parse_item():
    d = dataapi.parse_item(api_item())
    assert d["video_id"] == "abc123def45" and d["duration_s"] == 90 and d["category"] == "игры" and d["lang_hint"] == "ru"
    assert d["licensed_content"] == 1 and d["license"] == "youtube" and d["views"] == 500000 and d["topics"] == ["Video game culture"]


def base(**kw):
    v = {"license": "youtube", "licensed_content": 0, "made_for_kids": 0, "channel_name": "Вася", "category_id": "22", "lang": "ru", "duration_s": 50,
         "topics": [], "analysis": None, "frames": None, "growth": 0, "vpd": 0, "regions": {}, "manual_level": None, "blocked_ru": 0}
    v.update(kw)
    return v


def test_assess():
    assert popular.assess(base())["level"] == "unknown"
    a = popular.assess(base(license="creativeCommon", category_id="20", topics=["Video game culture"], growth=150000, regions={"RU": 3, "KZ": 9, "BY": 12},
                            analysis={"reuse_fit": 0.8, "audience_fit": 0.9, "audience_why": "геймеры"}, frames={"clean": True, "free_zones": ["низ"]}))
    assert a["level"] == "clean" and a["score"] >= 90 and a["reasons"][0].startswith("наша аудитория")
    b = popular.assess(base(licensed_content=1, channel_name="Первый канал", category_id="25", analysis={"reuse_fit": 0.3, "audience_fit": 0.2}, frames={"watermark": True}))
    assert b["level"] == "no" and any("Первый канал" in r for r in b["reasons"])
    c = popular.assess(base(analysis={"reuse_fit": 0.7, "audience_fit": 0.7}))  # без кадров выше «проверить» не поднимается
    assert c["level"] == "check"
    d = popular.assess(base(analysis={"reuse_fit": 0.9, "audience_fit": 0.9}, frames={"watermark": True, "watermark_where": "угол"}, license="creativeCommon"))
    assert d["level"] in ("check", "no") and any("водяной знак" in r for r in d["reasons"])
    e = popular.assess(base(manual_level="no", analysis={"reuse_fit": 0.9, "audience_fit": 0.9}, frames={"clean": True}))
    assert e["level"] == "no" and e["auto_level"] == "clean"
    f = popular.assess(base(lang="en", analysis={"reuse_fit": 0.7, "audience_fit": 0.6}))
    assert any("язык ролика" in r for r in f["reasons"])


def test_cfg_parses_lists():
    c = popular.cfg({"pop_regions": "ru, kz", "pop_categories": "20,28", "pop_pages": "x", "pop_lang": "ru"})
    assert c["regions"] == ["RU", "KZ"] and c["categories"] == ["20", "28"] and c["pages"] == 4 and c["audience"]


@pytest.fixture(scope="module")
def conn():
    c = db.connect(TMP / "popular.sqlite")
    db.init_db(c)
    return c


def test_upsert_list_pick(conn):
    ts = db.now_iso()
    items = [dataapi.parse_item(api_item("v1aaaaaaaaa", licensed=False, lic="creativeCommon", title="Roblox прикол")), dataapi.parse_item(api_item("v2bbbbbbbbb", cat="10", title="Клип"))]
    items[0]["rank"], items[1]["rank"] = 1, 2
    new, seen = popular.upsert(conn, items, "chart", ts, region="RU")
    assert (new, seen) == (2, 2)
    cat_items = [dataapi.parse_item(api_item("v1aaaaaaaaa", licensed=False, lic="creativeCommon"))]
    cat_items[0]["rank"] = 5
    popular.upsert(conn, cat_items, "chart", ts, region="RU", cat="20")
    popular.upsert(conn, [dataapi.parse_item(api_item("v1aaaaaaaaa", licensed=False, lic="creativeCommon"))], "chart", ts, region="KZ")
    assert popular.derive(conn, ts) == 2
    v = popular.detail(conn, "v1aaaaaaaaa")
    assert v["regions"] == {"RU": 1, "KZ": None} or v["regions"]["RU"] == 1 and "KZ" in v["regions"]
    assert v["cats"] == {"20": 5} and v["is_short"] is None and v["vpd"] > 0
    c = popular.cfg({})
    conn.execute("UPDATE pop_videos SET analysis = ?, frames = ? WHERE video_id = 'v1aaaaaaaaa'",
                 (db.dumps({"reuse_fit": 0.8, "audience_fit": 0.9, "audience_why": "геймеры", "topic": "Roblox"}), db.dumps({"clean": True, "free_zones": ["низ"]})))
    conn.commit()
    a = popular.evaluate(conn, "v1aaaaaaaaa", c)
    assert a["level"] == "clean"
    assert popular.evaluate(conn, "v2bbbbbbbbb", c)["level"] == "no"
    picked, unpicked = popular.auto_pick(conn, c)
    assert (picked, unpicked) == (1, 0)
    lst = popular.list_videos(conn, level="clean")
    assert lst["total"] == 1 and lst["items"][0]["video_id"] == "v1aaaaaaaaa" and lst["picks"] == 1
    assert popular.list_videos(conn, region="KZ")["total"] == 1 and popular.list_videos(conn, category="10")["total"] == 1
    assert popular.list_videos(conn, fit=0.6)["total"] == 1 and popular.list_videos(conn, pick=True)["total"] == 1
    assert popular.list_videos(conn, q="Клип")["items"][0]["video_id"] == "v2bbbbbbbbb"
    p = popular.picks(conn)
    assert p[0]["video_id"] == "v1aaaaaaaaa" and p[0]["audience_fit"] == 0.9 and p[0]["free_zones"] == ["низ"] and p[0]["source_url"].endswith("v1aaaaaaaaa")
    d = popular.set_level(conn, "v1aaaaaaaaa", "no", c)
    assert d["level"] == "no" and d["pick"] == 0
    d = popular.set_level(conn, "v1aaaaaaaaa", None, c)
    assert d["level"] == "clean"
    d = popular.set_pick(conn, "v2bbbbbbbbb", True, "ручной")
    assert d["pick"] == 1 and d["pick_auto"] == 0
    assert popular.auto_pick(conn, c) == (1, 0)  # v1 снова чистый и без ручного запрета: возвращается; ручной v2 не трогаем
    ov = popular.overview(conn)
    assert ov["total"] == 2 and ov["picks"] == 2 and ov["cc"] == 1
    with pytest.raises(ValueError):
        popular.set_level(conn, "v1aaaaaaaaa", "bad", c)
