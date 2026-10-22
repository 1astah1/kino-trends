"""Проверка по авторским правам: классификация сносов, расчёт уровня, события, отсев в календаре, разбор страницы."""
import json
import os
from pathlib import Path

import pytest

from kinotrends import db, rights, schedule, stats
from kinotrends.yt.page import parse_page

TMP = Path(os.environ["KT_VAR"])


def test_classify_removed():
    k, who = rights.classify_removed("ERROR: Видео недоступно · Это видео больше недоступно из-за жалобы на нарушение авторских прав, поданной пользователем Universal Pictures.")
    assert k == "copyright" and who == "Universal Pictures"
    k, who = rights.classify_removed("ERROR: Video unavailable · This video is no longer available due to a copyright claim by Toho Co., Ltd")
    assert k == "copyright" and who.startswith("Toho")
    assert rights.classify_removed("ERROR: Это видео недоступно, так как связанный с ним аккаунт YouTube был удалён")[0] == "terminated"
    assert rights.classify_removed("UNPLAYABLE: Это частное видео")[0] == "private"
    assert rights.classify_removed(None) == ("none", None)


def base(**kw):
    sig = {"kind": "film", "rated": 0, "removed_strict": 0, "claimants": [], "age_days": 0, "events": [], "source": None, "llm": None, "manual_level": None}
    sig.update(kw)
    return sig


def test_assess_levels_and_clip_rule():
    assert rights.assess(base())["level"] == "unknown"
    a = rights.assess(base(llm={"holder": "Walt Disney Studios", "holder_policy": "tolerant"}))
    assert a["holder_policy"] == "block" and a["level"] == "caution" and a["clip_max_s"] == 59  # список известных сильнее ответа модели
    a = rights.assess(base(llm={"holder": "Netflix", "holder_policy": "claim"}, rated=30, age_days=60))
    assert a["level"] == "ok" and a["clip_max_s"] == 59
    a = rights.assess(base(llm={"holder": "Мосфильм", "holder_policy": "claim", "public_domain": True}))
    assert a["clip_max_s"] is None and a["level"] == "ok"
    a = rights.assess(base(llm={"holder": "Universal", "holder_policy": "block"}, rated=20, removed_strict=5, claimants=[("Universal Pictures", 5)]))
    assert a["level"] == "block" and any("Universal Pictures" in r for r in a["reasons"])
    a = rights.assess(base(llm={"holder": "Netflix", "holder_policy": "claim"}, events=[{"kind": "strike"}]))
    assert a["level"] == "block" and a["reasons"][0].startswith("наши страйки")
    a = rights.assess(base(llm={"holder": "Universal", "holder_policy": "block"}, manual_level="ok", manual_note="лицензия"))
    assert a["level"] == "ok" and a["auto_level"] == "caution" and "решение оператора" in a["reasons"][0]
    assert rights.assess(base(kind="anime"))["level"] == "caution"
    a = rights.assess(base(llm={"holder": "Star Media", "holder_policy": "claim"}, source={"official": True, "alive": True, "channel": "Star Media"}, rated=25, age_days=40))
    assert a["level"] == "ok" and a["risk"] < 20


def test_allowed():
    assert rights.allowed("caution", "balanced") and not rights.allowed("block", "balanced")
    assert not rights.allowed("caution", "strict") and rights.allowed("ok", "strict")
    assert rights.allowed("block", "off") and rights.allowed("unknown", "balanced")


def test_parse_removed_page_names_claimant():
    pr = {"playabilityStatus": {"status": "ERROR", "reason": "Видео недоступно", "errorScreen": {"playerErrorMessageRenderer": {"subreason": {"runs": [
        {"text": "Это видео больше недоступно из-за жалобы на нарушение авторских прав, поданной пользователем "}, {"text": "Paramount Pictures"}]}}}}}
    html = "<html><script>var ytInitialPlayerResponse = " + json.dumps(pr, ensure_ascii=False) + ";</script></html>"
    d = parse_page("abc", html, "https://www.youtube.com/shorts/abc")
    assert d.status == "removed" and "Paramount Pictures" in d.error
    assert rights.classify_removed(d.error) == ("copyright", "Paramount Pictures")


@pytest.fixture(scope="module")
def conn():
    c = db.connect(TMP / "rights.sqlite")
    db.init_db(c)
    ts = db.now_iso()
    c.execute("INSERT INTO titles(id, label_ru, label_en, year, kind, sitelinks, genres, countries, updated_at) VALUES ('Q1','Брат 2','Brother 2',2000,'film',40,'[]','[\"Россия\"]',?)", (ts,))
    c.execute("INSERT INTO titles(id, label_ru, label_en, year, kind, sitelinks, genres, countries, updated_at) VALUES ('Q2','Холоп','Kholop',2019,'film',20,'[]','[\"Россия\"]',?)", (ts,))
    for i in range(6):
        removed = i < 1
        c.execute("""INSERT INTO videos(video_id, title, channel_id, duration_s, published_at, first_seen_at, last_seen_at, details_at, views, lang, title_id, recog_conf,
                     recog_method, status, removed_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (f"r{i}", f"Момент {i}", "UC1", 30, ts, ts, ts, ts, 1000, "ru", "Q1", 0.9, "cue", "removed" if removed else "ok",
                   "ERROR: Видео недоступно · жалоба на нарушение авторских прав, поданной пользователем СТВ" if removed else None))
    c.commit()
    return c


def test_evaluate_and_events(conn):
    a = rights.evaluate(conn, "Q1")
    assert a["signals"]["removed_strict"] == 1 and a["signals"]["claimants"][0][0] == "СТВ"
    assert a["level"] == "caution" and 40 <= a["risk"] < 65
    b = rights.add_event(conn, "Q2", "strike", note="тест")
    assert b["level"] == "block"
    lv = rights.levels(conn, ["Q1", "Q2", "Q404"])
    assert lv["Q2"]["level"] == "block" and lv["Q404"]["level"] == "unknown" and lv["Q1"]["clip_max_s"] == 59
    m = rights.set_manual(conn, "Q2", "ok", "лицензия есть")
    assert m["level"] == "ok" and m["auto_level"] == "block"
    assert rights.set_manual(conn, "Q2", None)["level"] == "block"
    ov = rights.overview(conn)
    assert ov["removed"]["copyright"] == 1 and ov["claimants"][0]["name"] == "СТВ" and ov["events"] and ov["blocked"][0]["title_id"] == "Q2"
    with pytest.raises(ValueError):
        rights.add_event(conn, "Q1", "whatever")


def test_schedule_skips_blocked(conn, monkeypatch):
    items = [{"title_id": "Q2", "label": "Холоп", "kind_ru": "фильм", "opportunity": 90, "n_fresh7": 1, "why": ["a"], "plan": {}, "scenes": [], "hooks": []},
             {"title_id": "Q1", "label": "Брат 2", "kind_ru": "фильм", "opportunity": 50, "n_fresh7": 0, "why": ["b"], "plan": {}, "scenes": [], "hooks": []}]
    monkeypatch.setattr(schedule.recommend, "today", lambda c, f, limit=80: {"items": items})
    db.set_setting(conn, "rights_policy", "balanced")
    v = schedule.build(conn, stats.Filters("30"), days=1, per_day=2, cooldown=10, start="2030-01-01")
    day = v["days"][-1]
    assert [it["title_id"] for it in day["items"]] == ["Q1"]  # Q2 со страйком отсеян, Q1 «с оговорками» проходит
    assert day["items"][0]["rights"]["level"] == "caution"
    db.set_setting(conn, "rights_policy", "strict")
    v = schedule.build(conn, stats.Filters("30"), days=1, per_day=2, cooldown=10, start="2030-02-01")
    assert [it["title_id"] for it in v["days"][-1]["items"]] == []  # при strict «с оговорками» тоже не проходит
    db.set_setting(conn, "rights_policy", "off")
    v = schedule.build(conn, stats.Filters("30"), days=1, per_day=2, cooldown=10, start="2030-03-01")
    assert {it["title_id"] for it in v["days"][-1]["items"]} == {"Q1", "Q2"}
    # смена политики на balanced выбрасывает уже стоящий в плане Q2 из незафиксированного слота
    db.set_setting(conn, "rights_policy", "balanced")
    v = schedule.build(conn, stats.Filters("30"), days=1, per_day=2, cooldown=10, start="2030-03-01")
    assert {it["title_id"] for it in v["days"][-1]["items"]} == {"Q1"}
