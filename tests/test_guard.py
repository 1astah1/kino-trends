"""Сторож правил выкладки: повторы, один фильм — один канал, план 4 в день, голодный канал получает фильм."""
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kinotrends import db, guard, pipeline


@pytest.fixture()
def conn():
    c = db.connect(Path(os.environ["KT_VAR"]) / "guard.sqlite")
    db.init_db(c)
    yield c
    c.close()


def _r(pid, ch, title, st="scheduled", at="2026-11-02T08:00:00Z", pub=None, sha="taxi", a=0.0, b=40.0, jid=None, jst="approved"):
    return {"pid": pid, "jid": jid or pid + 1000, "kind": "clip", "ch": ch, "st": st, "jst": jst, "at_utc": at,
            "pub_utc": pub, "title": title, "sha": sha, "a": a, "b": b}


def test_dedupe_scene_title_and_film_channel():
    rows = [
        _r(1, "emerald", "Угадаешь французский боевик 90-х по предложению?", st="done", pub="2026-10-30T12:00:00Z", a=2234, b=2273),
        _r(2, "emerald", "Он предложил ему выбор: стать копом или пешеходом", a=2234, b=2273),          # та же сцена — снять
        _r(3, "emerald", "Почему «Такси» чуть не остался без студии?", at="2026-11-01T12:00:00Z", a=3303, b=3336),
        _r(4, "emerald", "Почему «Такси» чуть не осталось без студии? 🎬", at="2026-11-07T08:00:00Z", a=192, b=229),  # тот же факт
        _r(5, "emerald", "Он решил проучить наглых таксистов", a=3806, b=3852),                          # своё — оставить
        _r(6, "kadr-dnya", "Король предложил стать христианином", sha="vik", a=1561, b=1608, jid=429),
        _r(7, "emerald", "Он предложил стать христианином ради мира", sha="vik2", a=1561, b=1608, jid=409),  # фильм не на своём канале
    ]
    film_of = {429: "Qvik", 409: "Qvik"}
    homes = guard.film_homes(rows, film_of, {"Qvik": "kadr-dnya"})
    out = {r["pid"]: why for r, why in guard.plan_dedupe(rows, film_of, homes)}
    assert set(out) == {2, 4, 7}
    assert "сцена" in out[2] and "заголовок" in out[4] and "kadr-dnya" in out[7]


def test_published_and_publishing_are_never_cancelled():
    rows = [_r(1, "bpn-shorts", "Сцена", st="done", pub="2026-10-30T12:00:00Z"),
            _r(2, "emerald", "Сцена", st="done", pub="2026-11-01T07:00:00Z"),
            _r(3, "emerald", "Сцена", st="publishing")]
    assert guard.plan_dedupe(rows, {}, {}) == []


def test_fill_counts_today_tomorrow_and_idle():
    now = datetime(2026, 11, 1, 11, 0, tzinfo=timezone.utc)  # 14:00 МСК
    plan = {"slots": ["10:00", "14:00", "18:00", "22:00"], "videos_per_day": 4, "timezone": "Europe/Moscow"}
    channels = [{"id": "bpn-shorts", "plan": plan, "connected": True}, {"id": "kadr-2", "plan": plan, "connected": False},
                {"id": "myhostbox-2", "plan": plan, "connected": True}]
    rows = [_r(1, "bpn-shorts", "a", st="done", pub="2026-11-01T07:00:00Z"),
            _r(2, "bpn-shorts", "b", at="2026-11-01T15:00:00Z"),
            _r(3, "bpn-shorts", "c", at="2026-11-01T19:00:00Z"),
            _r(4, "bpn-shorts", "d", at="2026-11-02T07:00:00Z"),
            _r(5, "bpn-shorts", "e", st=None, at=None)]
    f = guard.fill(rows, channels, now)
    assert "kadr-2" not in f  # не подключён — роликов туда не будет, считать нечего
    assert f["bpn-shorts"] == {"today": 3, "need_today": 3, "tomorrow": 1, "per_day": 4, "idle": 1, "deficit": 2}
    assert f["myhostbox-2"]["deficit"] == 2 + 4  # сегодня осталось 2 слота (18, 22) + завтра 4


def test_hungry_channel_gets_next_film(conn):
    targets = [("emerald", 6), ("bpn-shorts", 4), ("kadr-dnya", 3), ("myhostbox-2", None)]
    conn.execute("INSERT OR REPLACE INTO titles(id, label_ru, kind, genres, updated_at) VALUES ('Qh_action', 'Боевик', 'film', '[\"боевик\"]', 'now')")
    conn.execute("DELETE FROM pipeline_items")
    conn.commit()
    pick = lambda: pipeline._film_channel(conn, {"ref_id": "Qh_action"}, targets, {})[0]  # noqa: E731
    assert pick() == "emerald"  # по теме
    db.set_meta(conn, "pipeline_hungry", pipeline.dumps({"at": db.now_iso(), "deficit": {"myhostbox-2": 6, "bpn-shorts": 1}}))
    assert pick() == "myhostbox-2"  # голодный канал — первым, тема не важна
    # для канала уже режется фильм (≈5 роликов) — дефицит 6 почти закрыт, снова решает тема
    iid = conn.execute("INSERT INTO pipeline_items(kind, ref_id, label, status, metrics, created_at, updated_at) "
                       "VALUES ('film', 'Qother', 'x', 'cutting', '{\"channel\": \"myhostbox-2\"}', 'now', 'now')").lastrowid
    conn.commit()
    assert pick() == "emerald"
    conn.execute("DELETE FROM pipeline_items WHERE id = ?", (iid,))
    db.set_meta(conn, "pipeline_hungry", pipeline.dumps({"at": "2026-01-01T00:00:00Z", "deficit": {"myhostbox-2": 6}}))
    assert pick() == "emerald"  # устаревшая оценка не управляет


def test_too_long_clip_is_cancelled():
    r = dict(_r(1, "emerald", "Длинная сцена"), dur=62.0)
    out = guard.plan_dedupe([r, _r(2, "emerald", "Обычная", sha="x")], {}, {})
    assert [x["pid"] for x, _ in out] == [1] and "Content ID" in out[0][1]


def test_orphans_reopen_published_item_with_idle_clips():
    rows = [_r(237, "myhostbox-2", "Крампус 2", st=None, at=None), _r(238, "myhostbox-2", "Крампус 3", st=None, at=None),
            _r(236, "myhostbox-2", "Крампус 1", st="done", pub="2026-11-01T15:00:00Z"),
            _r(96, "emerald", "Джентльмены", st=None, at=None)]
    items = [{"id": 214, "status": "published", "as_packages": [236, 237, 238], "last_error": None},
             {"id": 95, "status": "published", "as_packages": [96], "last_error": "снято владельцем 29.10"},
             {"id": 9, "status": "published", "as_packages": [11], "last_error": ""}]
    assert guard.orphans(rows, items) == {214: 2}

