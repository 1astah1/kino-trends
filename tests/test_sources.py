"""Поиск исходников: эвристика, проверка моделью, сохранение."""
import os
from pathlib import Path

from kinotrends import db, sources
from kinotrends.llm import LLM, Usage

TMP = Path(os.environ["KT_VAR"])


class FakeLLM(LLM):
    def __init__(self, answer):
        super().__init__("http://fake", "k", "fake")
        self.answer = answer

    def ask_json(self, system, user, image=None, max_tokens=700):
        return self.answer, Usage(50, 10, 0.0001)


def test_heuristic_filters_junk_and_rewards_official():
    full = {"duration_s": 83 * 60, "title": "Джентльмены удачи (FullHD, 1971)", "channel": "Киноконцерн «Мосфильм»", "views": 1000}
    junk = {"duration_s": 5 * 60, "title": "Джентльмены удачи: актёры тогда и сейчас", "channel": "X", "views": 1}
    trailer = {"duration_s": 90 * 60, "title": "Джентльмены удачи трейлер", "channel": "X", "views": 1}
    other = {"duration_s": 90 * 60, "title": "Старики-разбойники", "channel": "Мосфильм", "views": 1}
    assert sources.heuristic(full, "Джентльмены удачи", "film") > sources.heuristic(other, "Джентльмены удачи", "film") > 0
    assert sources.heuristic(junk, "Джентльмены удачи", "film") == -1
    assert sources.heuristic(trailer, "Джентльмены удачи", "film") == -1
    ep = {"duration_s": 47 * 60, "title": "Зимородок 1 серия русский дубляж", "channel": "Посмотри Турецкую драму", "views": 5}
    assert sources.heuristic(ep, "Зимородок", "series") > 0
    assert sources.heuristic({"duration_s": 47 * 60, "title": "Зимородок 1 серия", "channel": "x", "views": 1}, "Зимородок", "film") == -1


def test_find_sources_with_fake_search_and_llm(monkeypatch):
    c = db.connect(TMP / "sources.sqlite")
    db.init_db(c)
    # отечественный фильм: «оригинал» у него и есть русская речь (иначе нужен дубляж или озвучка)
    c.execute("INSERT OR IGNORE INTO titles(id, label_ru, year, kind, sitelinks, countries, updated_at) VALUES ('Q9','Брат 2',2000,'film',10,'[\"Россия\"]',?)", (db.now_iso(),))
    c.commit()
    fake_results = [
        {"video_id": "full1", "title": "Брат 2 | фильм | Балабанов", "channel": "Кинокомпания «СТВ»", "duration_s": 122 * 60, "views": 61000, "url": "https://www.youtube.com/watch?v=full1"},
        {"video_id": "mom1", "title": "Брат-2 | Лучшие моменты", "channel": "Кинокомпания «СТВ»", "duration_s": 8 * 60, "views": 3000, "url": "https://www.youtube.com/watch?v=mom1"},
        {"video_id": "conc", "title": "БРАТ-2. Живьем в Олимпийском", "channel": "Live Concert", "duration_s": 78 * 60, "views": 564, "url": "https://www.youtube.com/watch?v=conc"},
    ]
    monkeypatch.setattr(sources, "_search", lambda q, n=10: fake_results)
    monkeypatch.setattr(sources, "_search_rutube", lambda q, n=10: [{"video_id": "rt_abc", "title": "Брат 2 (2000) фильм", "channel": "Кино на Rutube", "duration_s": 121 * 60, "views": 500, "url": "https://rutube.ru/video/abc/"}])
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    llm = FakeLLM({"items": [{"id": "full1", "is_full": True, "type": "film", "language": "оригинал", "trust": 0.95, "note": "официальный канал"},
                             {"id": "conc", "is_full": False, "type": "other", "trust": 0.1, "note": "концерт"}], "best": ["full1"]})
    res = sources.find_sources(c, llm, {"title_id": "Q9", "label": "Брат 2", "label_en": None, "year": 2000, "kind": "film"}, log=lambda *a, **k: None)
    assert res[0]["video_id"] == "full1" and res[0]["verified"] == 1 and res[0]["language"] == "оригинал"
    assert all(r["video_id"] != "mom1" for r in res)  # «лучшие моменты» отфильтрованы до модели
    assert sources.best_source(c, "Q9")["video_id"] == "full1"
    assert any(r["video_id"] == "rt_abc" for r in res)  # кандидат с Rutube тоже попал в список
    assert sources.checked_at(c, "Q9")
    # повторная проверка не нужна, пока свежая
    assert sources.ensure_for_titles(c, llm, [{"title_id": "Q9", "label": "Брат 2", "label_en": None, "year": 2000, "kind": "film"}], log=lambda *a, **k: None) == 0


def test_banned_source_stays_unverified_after_recheck(tmp_path):
    from kinotrends import db, sources
    c = db.connect(tmp_path / "bans.sqlite")
    db.init_db(c)
    c.execute("INSERT INTO titles(id, label_ru, kind, updated_at) VALUES ('Q886', 'Симпсоны', 'animated_series', '2026-10-29T00:00:00Z')")
    c.execute("INSERT INTO title_sources(title_id, video_id, url, label, channel, kind, language, trust, verified, note, score, found_at) "
              "VALUES ('Q886', 'rt_game', 'https://rutube.ru/video/game/', 'симпсоны 1 серия', 'drags1', 'episode', 'ru', 0.9, 1, '', 5, '2026-10-29T00:00:00Z')")
    c.commit()
    sources.mark_bad(c, "Q886", "rt_game", "запись игры, не сериал")
    assert sources.banned_sources(c) == {"rt_game": "запись игры, не сериал"}
    c.execute("UPDATE title_sources SET verified = 1 WHERE video_id = 'rt_game'")  # как будто кто-то вернул отметку
    c.commit()
    sources.recheck_verified(c)
    assert c.execute("SELECT verified FROM title_sources WHERE video_id = 'rt_game'").fetchone()[0] == 0


def test_soviet_titles_detected(tmp_path):
    from kinotrends import db, sources
    c = db.connect(tmp_path / "soviet.sqlite")
    db.init_db(c)
    rows = [("Q1", "Афоня", 1975, '["СССР"]', None, None), ("Q2", "ДМБ", 2000, '["Россия"]', None, None),
            ("Q3", "Ну, погоди!", 1969, "[]", "Ну, погоди!", None), ("Q4", "Том и Джерри", 1940, "[]", "Tom and Jerry", "Tom and Jerry"),
            ("Q5", "Сваты", None, '["Украина"]', None, None)]
    for tid, label, year, countries, orig, en in rows:
        c.execute("INSERT INTO titles(id, label_ru, year, kind, countries, original_title, label_en, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                  (tid, label, year, "film", countries, orig, en, "2026-10-29T00:00:00Z"))
    c.commit()
    assert sources.soviet_titles(c, ["Q1", "Q2", "Q3", "Q4", "Q5"]) == {"Q1", "Q3"}


def test_runtime_mismatch_catches_same_name_other_version():
    """«Отверженные» 2012 г. — 158 мин; чёрно-белая одноимённая версия на 109 мин — не тот фильм и не тот год."""
    c = db.connect(TMP / "runtime.sqlite")
    db.init_db(c)
    sources._ensure_runtime_cols(c)
    c.execute("INSERT OR REPLACE INTO titles(id, label_ru, kind, year, updated_at, runtime_min) VALUES ('Q28234', 'Отверженные', 'film', 2012, 'now', '[158]')")
    c.execute("INSERT OR REPLACE INTO titles(id, label_ru, kind, updated_at, runtime_min) VALUES ('Qser', 'Сериал', 'series', 'now', '[45]')")
    c.execute("INSERT OR REPLACE INTO titles(id, label_ru, kind, updated_at, runtime_min) VALUES ('Qnone', 'Без данных', 'film', 'now', '[]')")
    c.commit()
    assert "109 мин" in sources.runtime_mismatch(c, "Q28234", "film", 6560) and "158" in sources.runtime_mismatch(c, "Q28234", "film", 6560)
    assert sources.runtime_mismatch(c, "Q28234", "film", 158 * 60 * 0.96) is None  # PAL-ускорение 4 %
    assert sources.runtime_mismatch(c, "Q28234", "film", 150 * 60) is None
    assert sources.runtime_mismatch(c, "Qser", "series", 20 * 60) is None  # серии не сверяются
    assert sources.runtime_mismatch(c, "Qnone", "film", 30 * 60) is None  # длина неизвестна — не мешаем
    assert sources.runtime_min(c, "Q28234", fetch=False) == [158]


def test_runtime_catches_namesake_adaptation(tmp_path):
    """01.11: под «Гордостью и предубеждением» 2005 (127 мин) пришла версия 1940 (117 мин) — в допуске 15 %,
    но длина файла точно как у одноимённой экранизации."""
    from kinotrends import db, sources

    c = db.connect(tmp_path / "rt.sqlite")
    db.init_db(c)
    sources.runtime_min(c, "Qnone", fetch=False)  # колонка runtime_min
    for tid, year, rt in (("Q2005", 2005, "[127]"), ("Q1940", 1940, "[117]")):
        c.execute("INSERT INTO titles(id, label_ru, year, kind, runtime_min, updated_at) VALUES (?, 'Гордость и предубеждение', ?, 'film', ?, 'now')",
                  (tid, year, rt))
    c.commit()
    assert "1940" in sources.runtime_mismatch(c, "Q2005", "film", 118 * 60, fetch=False)
    assert sources.runtime_mismatch(c, "Q2005", "film", 128 * 60, fetch=False) is None
    assert sources.runtime_mismatch(c, "Q2005", "film", 123 * 60, fetch=False) is None  # PAL-ускорение своей версии
