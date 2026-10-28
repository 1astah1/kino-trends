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
