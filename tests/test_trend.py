"""Тренд рынка: массовость важнее одного вирусного ролика, ложные совпадения по слову отсеиваются."""
import os
from pathlib import Path

from kinotrends import db, trend
from tests.test_content_check import FakeLLM

TMP = Path(os.environ["KT_VAR"])


def test_many_channels_beat_one_viral_short():
    one_viral = {"kind": "film", "vpd_sum": 260000, "n_channels": 1, "n_fresh7": 2}
    market_hit = {"kind": "series", "vpd_sum": 25000, "n_channels": 7, "n_fresh7": 30}
    assert not trend.is_market_trend(one_viral) and trend.is_market_trend(market_hit)
    assert trend.trend_score(market_hit) > trend.trend_score({**one_viral, "n_channels": 3})


def test_validate_counts_answers_itself_and_caches():
    c = db.connect(TMP / "trend.sqlite")
    db.init_db(c)
    ts = db.now_iso()
    c.execute("INSERT OR IGNORE INTO titles(id, label_ru, year, kind, sitelinks, updated_at) VALUES ('Qconcert', 'Концерт', 2009, 'film', 5, ?)", (ts,))
    for i, t in enumerate(["Dua Lipa Live #concert", "Летний концерт на ВДНХ #концерт", "Концерт Матранга", "Le Concert 2009 сцена со скрипкой"]):
        c.execute("INSERT INTO videos(video_id, title, views, title_id, first_seen_at, last_seen_at) VALUES (?, ?, ?, 'Qconcert', ?, ?)", (f"v{i}", t, 1000 - i, ts, ts))
    c.commit()
    llm = FakeLLM({"items": [{"n": 1, "about": False}, {"n": 2, "about": False}, {"n": 3, "about": False}, {"n": 4, "about": True}]})
    v = trend.validate(c, llm, "Qconcert", "Концерт", 2009, "Le Concert")
    assert v["ok"] is False and v["share"] == 0.25 and "Le Concert" in llm.calls[0]
    assert trend.validate(c, FakeLLM(fail=True), "Qconcert", "Концерт")["ok"] is False  # из кэша, без модели
    assert trend.validate(c, FakeLLM(fail=True), "Qother", "Другое")["ok"] is None


def test_this_years_film_is_not_a_cuttable_trend():
    hype = {"kind": "film", "year": 2026, "vpd_sum": 260000, "n_channels": 5, "n_fresh7": 9}
    assert not trend.is_market_trend(hype, this_year=2026)
    assert trend.is_market_trend({**hype, "kind": "series"}, this_year=2026)


def test_game_movie_is_junk_source():
    from kinotrends import sources
    assert sources.JUNK.search("Мстители: Судный день — Игрофильм (все катсцены)")
    assert not sources.verdict_ok({"is_full": True, "trust": 0.9, "language": "русский дубляж", "note": "игрофильм на русском"},
                                  ["мстители судныи день"], {"title": "Мстители: Судный день полный фильм"}, russian_made=False)


def test_funny_means_comedy_but_not_kids():
    assert trend.is_funny({"label": "Гриффины", "genres": ["комедийный телесериал", "сатира"]})
    assert trend.is_funny({"label": "Бриллиантовая рука", "genres": ["буффонада", "криминальная комедия"]})
    assert not trend.is_funny({"label": "Барбоскины", "genres": ["комедийный телесериал"]})
    assert not trend.is_funny({"label": "Смешарики", "genres": ["детский телевизионный сериал", "комедийный"]})
    assert not trend.is_funny({"label": "Великая Отечественная", "genres": ["документальный сериал"]})
