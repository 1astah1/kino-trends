"""Дымовые тесты статистики и API на временной базе."""
import os
from pathlib import Path

import pytest

from kinotrends import db, stats
from kinotrends.text import norm

TMP = Path(os.environ["KT_VAR"])


@pytest.fixture(scope="module")
def conn():
    c = db.connect(TMP / "test.sqlite")
    db.init_db(c)
    ts = db.now_iso()
    c.execute("INSERT OR IGNORE INTO titles(id, label_ru, label_en, year, kind, sitelinks, genres, countries, updated_at) VALUES ('Q1','Орвилл','The Orville',2017,'series',40,'[\"комедия\",\"фантастика\"]','[\"США\"]',?)", (ts,))
    c.execute("INSERT OR IGNORE INTO title_aliases(title_id, alias, norm, lang) VALUES ('Q1','Орвилл',?, 'ru')", (norm("Орвилл"),))
    c.execute("INSERT OR IGNORE INTO channels(channel_id, name, followers, first_seen_at, last_seen_at) VALUES ('UC1','Канал',1000,?,?)", (ts, ts))
    for i in range(5):
        vid = f"vid{i}"
        c.execute("""INSERT OR IGNORE INTO videos(video_id, title, channel_id, channel_name, duration_s, published_at, first_seen_at, last_seen_at, details_at,
                     views, likes, lang, hashtags, features, title_id, recog_conf, recog_method, recog_evidence, status)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ok')""",
                  (vid, f"Ролик {i} Название: Орвилл", "UC1", "Канал", 30 + i, ts, ts, ts, ts, 1000 * (i + 1), 50, "ru", '["фильм"]',
                   '{"question": true, "emoji": false}', "Q1" if i < 4 else None, 0.95 if i < 4 else None, "cue" if i < 4 else None, "Орвилл" if i < 4 else None))
        c.execute("INSERT OR IGNORE INTO video_stats(video_id, ts, views, src) VALUES (?,?,?,'page')", (vid, ts, 1000 * (i + 1)))
    c.commit()
    return c


def test_films_and_detail(conn):
    f = stats.Filters("30")
    res = stats.films(conn, f)
    assert res["total"] == 1
    g = res["items"][0]
    assert g["n_shorts"] == 4 and g["n_channels"] == 1 and g["views_sum"] == 10000 and g["score"] >= 0
    d = stats.film_detail(conn, f, "Q1")
    assert d["stats"]["n_shorts"] == 4 and len(d["videos"]) == 4 and d["channels"][0]["channel_id"] == "UC1"
    assert stats.film_detail(conn, f, "nope") is None


def test_interests_channels_shorts_overview(conn):
    f = stats.Filters("30")
    i = stats.interests(conn, f)
    assert i["total"]["n"] == 4 and i["genres"][0]["key"] in ("комедия", "фантастика")
    ch = stats.channels(conn, f)
    assert ch["items"][0]["n_shorts"] == 5 and ch["items"][0]["n_recognized"] == 4
    cd = stats.channel_detail(conn, f, "UC1")
    assert cd["stats"]["n_shorts"] == 5
    sh = stats.shorts(conn, f, state="unrecognized")
    assert sh["total"] == 1
    sh2 = stats.shorts(conn, f, q="Орвилл", sort="vpd")
    assert sh2["total"] == 5
    o = stats.overview(conn, f)
    assert o["period"]["n_recognized"] == 4 and o["totals"]["videos"] == 5
    assert "Орвилл" in stats.films_csv(conn, f) and "vid0" in stats.shorts_csv(conn, f)


def test_api_smoke(conn):
    from fastapi.testclient import TestClient

    from kinotrends import api

    db.set_setting(conn, "schedule_hours", "0")
    api.REC.load(conn)
    with TestClient(api.app) as client:
        for path in ("/api/overview", "/api/films", "/api/films/Q1", "/api/interests", "/api/channels", "/api/channels/UC1",
                     "/api/shorts?state=all", "/api/sources", "/api/settings", "/api/runs", "/api/dict/status", "/api/health",
                     "/api/titles/search?q=орв", "/api/export/films.csv", "/api/export/shorts.csv", "/"):
            r = client.get(path)
            assert r.status_code == 200, path
        assert client.get("/api/titles/search?q=орв").json()["items"][0]["title_id"] == "Q1"
        t = client.post("/api/titles", json={"label_ru": "Мой сериал", "kind": "series", "year": 2024}).json()
        assert t["title_id"].startswith("man_")
        r = client.post("/api/videos/vid4/assign", json={"title_id": t["title_id"], "learn": True}).json()
        assert r["title_id"] == t["title_id"]
        r = client.post("/api/videos/vid4/assign", json={"title_id": None, "ban": "Орвилл"}).json()
        assert r["title_id"] is None
        assert client.get("/api/aliases/bans").json()["items"][0]["norm"] == "орвилл"
        assert client.delete("/api/aliases/bans/Орвилл").status_code == 200
        r = client.post("/api/videos/vid4/assign", json={"reset": True}).json()
        assert "title_id" in r
        s = client.post("/api/sources", json={"kind": "hashtag", "value": "#ТестТег"}).json()
        assert s["value"] == "тесттег"
        assert client.patch(f"/api/sources/{s['id']}", json={"enabled": False}).json()["enabled"] == 0
        assert client.delete(f"/api/sources/{s['id']}").status_code == 200
        assert client.put("/api/settings", json={"schedule_hours": "0"}).status_code == 200
        assert client.put("/api/settings", json={"schedule_hours": "abc"}).status_code == 400
        assert client.get("/api/films/nope").status_code == 404
