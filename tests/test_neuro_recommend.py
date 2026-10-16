"""Нейро-модули с подменённой моделью, рекомендации, календарь, сезоны."""
import os
from pathlib import Path

import pytest

from kinotrends import db, neuro, recommend, schedule, stats
from kinotrends.llm import LLM, Usage, image_mime, parse_json
from kinotrends.recognize import Recognizer
from kinotrends.text import norm, season_episode

TMP = Path(os.environ["KT_VAR"])


class FakeLLM(LLM):
    def __init__(self, answers):
        super().__init__("http://fake", "k", "fake-model")
        self.answers = list(answers)
        self.calls = []

    def ask_json(self, system, user, image=None, max_tokens=700):
        self.calls.append((user, image is not None))
        return self.answers.pop(0), Usage(100, 20, 0.0001)


@pytest.fixture(scope="module")
def conn():
    c = db.connect(TMP / "neuro.sqlite")
    db.init_db(c)
    ts = db.now_iso()
    c.execute("INSERT OR IGNORE INTO titles(id, label_ru, label_en, year, kind, sitelinks, genres, countries, updated_at) VALUES ('Q1','Брат 2','Brother 2',2000,'film',40,'[\"боевик\"]','[\"Россия\"]',?)", (ts,))
    c.execute("INSERT OR IGNORE INTO titles(id, label_ru, label_en, year, kind, sitelinks, genres, countries, updated_at) VALUES ('Q2','Зимородок','Yali Capkini',2022,'series',12,'[\"драма\"]','[\"Турция\"]',?)", (ts,))
    for tid, label in (("Q1", "Брат 2"), ("Q2", "Зимородок")):
        c.execute("INSERT OR IGNORE INTO title_aliases(title_id, alias, norm, lang) VALUES (?,?,?,'ru')", (tid, label, norm(label)))
    for i in range(8):
        tid = "Q1" if i % 2 == 0 else "Q2"
        c.execute("""INSERT OR IGNORE INTO videos(video_id, title, channel_id, channel_name, duration_s, published_at, first_seen_at, last_seen_at, details_at,
                     views, lang, hashtags, features, title_id, recog_conf, recog_method, status, thumb)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ok',NULL)""",
                  (f"v{i}", f"Момент {i} #фильм", f"UC{i % 3}", f"Канал {i % 3}", 20 + i * 5, ts, ts, ts, ts, 100000 * (i + 1), "ru", '["фильм"]',
                   '{"question": false, "emoji": true}', tid, 0.9, "cue"))
    c.execute("""INSERT OR IGNORE INTO videos(video_id, title, channel_id, channel_name, duration_s, published_at, first_seen_at, last_seen_at, details_at, views, lang, hashtags, status)
                 VALUES ('u1','Он всех спас 😱','UC0','Канал 0',30,?,?,?,?,900000,'ru','["кино"]','ok')""", (ts, ts, ts, ts))
    c.commit()
    return c


def test_parse_and_mime():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert image_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert image_mime(b"\xff\xd8\xff\xe0") == "image/jpeg"


def test_season_episode():
    assert season_episode("Волчонок 3 сезон 7 серия") == (3, 7)
    assert season_episode("Сериал s02e05") == (2, 5)
    assert season_episode("просто момент") == (None, None)


def test_neuro_maps_and_applies(conn):
    rec = Recognizer()
    rec.load(conn, min_sitelinks=0)
    llm = FakeLLM([{"is_film": True, "title_ru": "Зимородок", "year": 2022, "kind": "series", "confidence": 0.9, "clues": "актёры"}])
    v = conn.execute("SELECT * FROM videos WHERE video_id = 'u1'").fetchone()
    res = neuro.recognize_one(conn, rec, llm, v)
    assert res["title_id"] == "Q2" and res["how"] == "exact"
    assert "Этот канал раньше резал" in llm.calls[0][0]
    neuro.apply_result(conn, "u1", res, "fake")
    row = conn.execute("SELECT title_id, recog_conf, recog_method FROM videos WHERE video_id = 'u1'").fetchone()
    assert row["title_id"] == "Q2" and row["recog_method"] == "neuro" and row["recog_conf"] == 0.9
    # неизвестное произведение с высокой уверенностью создаётся
    ans = {"is_film": True, "title_ru": "Новая дорама", "title_original": "New Drama", "year": 2024, "kind": "series", "confidence": 0.85}
    tid, how = neuro.map_answer(conn, rec, ans, create=True)
    assert how == "created" and tid.startswith("neu_")
    assert conn.execute("SELECT source FROM titles WHERE id = ?", (tid,)).fetchone()[0] == "neuro"
    # не фильм
    assert neuro.map_answer(conn, rec, {"is_film": False, "confidence": 0.9}, create=True) == (None, "not_film")


def test_insights(conn):
    llm = FakeLLM([{"scenes": [{"name": "Данила в баре", "what_to_cut": "сцена с диалогом", "video_ids": ["v0", "v2"]}], "hooks": ["Он не ожидал"], "advice": ["до 30 с"]}])
    f = stats.Filters("30")
    d = stats.film_detail(conn, f, "Q1")
    ins = neuro.title_insights(conn, llm, "Q1", d["videos"], d["title"])
    assert ins["scenes"][0]["n"] == 2 and ins["scenes"][0]["views"] == 100000 + 300000
    assert neuro.get_insights(conn, "Q1")["hooks"] == ["Он не ожидал"]


def test_recommend_schedule_brief(conn):
    f = stats.Filters("30", lang="ru")
    rec = recommend.today(conn, f, limit=5)
    assert rec["items"] and rec["items"][0]["why"] and "plan" in rec["items"][0]
    text = recommend.digest_text(rec, base_url="https://x/")
    assert "Что резать сегодня" in text and "https://x/" in text
    v = schedule.build(conn, f, days=3, per_day=1, cooldown=10)
    future = [d for d in v["days"] if not d["is_past"]]
    assert future[0]["items"] and future[1]["items"]
    assert future[0]["items"][0]["title_id"] != future[1]["items"][0]["title_id"]  # не повторяем
    item = future[0]["items"][0]
    schedule.set_status(conn, item["id"], "done")
    schedule.replace(conn, future[1]["items"][0]["id"], "Q1", f)
    v2 = schedule.build(conn, f, days=3, per_day=1, cooldown=10, rebuild=True)
    f2 = [d for d in v2["days"] if not d["is_past"]]
    assert f2[0]["items"][0]["status"] == "done"
    assert f2[1]["items"][0]["locked"] and f2[1]["items"][0]["title_id"] == "Q1"
    assert schedule.digest_lines(conn)
    b = recommend.brief(conn, f, "Q1")
    assert b["title"]["label"] == "Брат 2" and b["plan"]["refs"] and b["scenes"]
    assert recommend.brief(conn, f, "nope") is None
