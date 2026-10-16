"""Интерес аудитории и комментарии Data API на подменённых ответах."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from kinotrends import db, interest
from kinotrends.yt import dataapi

TMP = Path(os.environ["KT_VAR"])


def test_interest_summary_and_describe():
    c = db.connect(TMP / "interest.sqlite")
    db.init_db(c)
    c.execute("INSERT OR IGNORE INTO titles(id, label_ru, year, kind, sitelinks, updated_at) VALUES ('Q7','Тест',2020,'film',5,?)", (db.now_iso(),))
    today = datetime.now(timezone.utc).date()
    rows = []
    for i in range(1, 40):
        day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        rows.append(("Q7", "wiki", day, 300 if i <= 7 else 100))
    c.executemany("INSERT OR REPLACE INTO title_interest(title_id, source, day, value) VALUES (?,?,?,?)", rows)
    c.commit()
    sm = interest.summary(c, ["Q7"])["Q7"]
    assert sm["wiki"]["last7"] == 300 and sm["wiki"]["prev30"] == 100 and sm["wiki"]["ratio"] == 3.0
    lines = interest.describe(sm)
    assert lines and "Википедия" in lines[0] and "×3,0" in lines[0]
    ser = interest.series(c, "Q7")
    assert len(ser["wiki"]) == 39 and ser["trends"] == []


def test_fetch_wiki_views_parses(monkeypatch):
    class R:
        status_code = 200
        def json(self):
            return {"items": [{"timestamp": "2026100100", "views": 12}, {"timestamp": "2026100200", "views": 15}]}

    class C:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, url): return R()

    monkeypatch.setattr(httpx, "Client", C)
    assert interest.fetch_wiki_views("Титаник (фильм, 1997)") == [("20261001", 12), ("20261002", 15)]


def test_top_comments_parse(monkeypatch):
    class R:
        status_code = 200
        text = ""
        def json(self):
            return {"items": [{"snippet": {"topLevelComment": {"snippet": {"textDisplay": "Название: Орвилл (2017)"}}}},
                              {"snippet": {"topLevelComment": {"snippet": {"textDisplay": "  "}}}}]}

    class C:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, url, params=None): return R()

    monkeypatch.setattr(httpx, "Client", C)
    assert dataapi.fetch_top_comments("key", "abc") == ["Название: Орвилл (2017)"]
