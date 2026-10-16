import sqlite3

import pytest

from kinotrends import db
from kinotrends.dictionary.importer import alias_variants
from kinotrends.recognize import Recognizer, extract_candidates
from kinotrends.text import norm

TITLES = [
    ("Q1", "Орвилл", "The Orville", 2017, "series", 40),
    ("Q2", "Мег 2: Бездна", "Meg 2: The Trench", 2023, "film", 35),
    ("Q3", "Американский пирог", "American Pie", 1999, "film", 60),
    ("Q4", "Мумия", "The Mummy", 1999, "film", 70),
    ("Q5", "Мумия", "The Mummy", 2017, "film", 50),
    ("Q6", "Джентльмены", "The Gentlemen", 2019, "film", 55),
    ("Q7", "Гарри Поттер и философский камень", "Harry Potter and the Philosopher's Stone", 2001, "film", 120),
    ("Q8", "Она", "Her", 2013, "film", 40),
    ("Q9", "Волчонок", "Teen Wolf", 2011, "series", 45),
    ("Q10", "Джанго освобождённый", "Django Unchained", 2012, "film", 90),
    ("Q11", "Ералаш", None, 1974, "tv_show", 12),
    ("Q12", "Гладиатор", "Gladiator", 2000, "film", 110),
    ("Q13", "На грани", "The Edge", 1997, "film", 30),
    ("Q14", "История любви", "Love Story", 1970, "film", 45),
    ("Q15", "Герои", "Heroes", 2006, "series", 60),
]


@pytest.fixture()
def rec():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    ts = db.now_iso()
    for tid, ru, en, year, kind, sl in TITLES:
        conn.execute("INSERT INTO titles(id, label_ru, label_en, year, kind, sitelinks, updated_at) VALUES (?,?,?,?,?,?,?)", (tid, ru, en, year, kind, sl, ts))
        for lang, raw in (("ru", ru), ("en", en)):
            for a in alias_variants(raw):
                conn.execute("INSERT OR IGNORE INTO title_aliases(title_id, alias, norm, lang) VALUES (?,?,?,?)", (tid, a, norm(a), lang))
    conn.execute("INSERT OR IGNORE INTO title_aliases(title_id, alias, norm, lang) VALUES ('Q7','Гарри Поттер','гарри поттер','ru')")
    r = Recognizer()
    r.load(conn)
    return r


def test_alias_variants():
    assert "Путешествие на дно моря" in alias_variants("Путешествие на дно моря (телесериал)")
    assert "Orville" in alias_variants("The Orville")
    assert "Мег 2" in alias_variants("Мег 2: Бездна")


def test_cue_with_year(rec):
    m = rec.recognize("Тот самый друг который ест всё Название: Орвилл (2017) #фильм #сериал #моменты", None)
    assert m.title_id == "Q1" and m.method == "cue" and m.conf >= 0.9


def test_pipe_cue(rec):
    m = rec.recognize("Он всех спас💀|Название:Мег 2: Бездна|#сериал #фильм #кино", None)
    assert m.title_id == "Q2" and m.conf >= 0.9


def test_segment(rec):
    m = rec.recognize("Карма настигла пляжных хулиганов 😈 | Американский пирог | #кино", None)
    assert m.title_id == "Q3" and m.method == "segment"


def test_year_disambiguation(rec):
    m = rec.recognize("Бенни знакомится с Имхотепом | Мумия. 1999. Момент из фильма [1080p]", None)
    assert m.title_id == "Q4"
    m2 = rec.recognize("Том Круз бежит | Мумия (2017)", None)
    assert m2.title_id == "Q5"


def test_quotes_and_from(rec):
    assert rec.recognize("Момент из фильма «Джентльмены» 🔥", None).title_id == "Q6"
    assert rec.recognize("Отрывок из фильма 'Джентльмены' Смерть Маэстро", None).title_id == "Q6"


def test_hashtag_squash(rec):
    m = rec.recognize("Когда Снейп злой 😂 #гаррипоттер #фильм", None)
    assert m.title_id == "Q7" and m.method == "hashtag"


def test_generic_single_word_not_matched_in_free_text(rec):
    m = rec.recognize("Она ушла и не вернулась #фильм", None)
    assert m.title_id is None


def test_free_text_multiword(rec):
    m = rec.recognize("Лучшая сцена Джанго освобождённый без названия", None)
    assert m.title_id == "Q10" and m.method == "text"


def test_fuzzy_typo(rec):
    m = rec.recognize("Название: Джентельмены", None)
    assert m.title_id == "Q6" and m.conf >= 0.8


def test_description_cue_and_channel_default(rec):
    m = rec.recognize("Смешной момент 😂", "Фильм: Волчонок\nПодписывайся", None)
    assert m.title_id == "Q9"
    m2 = rec.recognize("Смешной момент 😂", None, None, channel_default="Q11")
    assert m2.title_id == "Q11" and m2.method == "channel"


def test_candidates_cleanup():
    cands = extract_candidates("Доктор Шульц убивает шерифа — Джанго освобождённый (фильм 2012) Сцена", None, None)
    texts = [c.text for c in cands]
    assert "Джанго освобождённый" in texts
    assert all("1080p" not in t for t in texts)


def test_search(rec):
    hits = rec.search("джентл")
    assert hits and hits[0][0] == "Q6"


def test_titlecase_rules(rec):
    # одиночное слово в середине с заглавной — слабое совпадение, ниже порога, но предлагается
    m = rec.recognize("Верность генерала Рима. Фильм Гладиатор. #фильм", None)
    assert m.title_id == "Q12" and m.conf < 0.6
    # то же слово строчными — не предлагается
    assert rec.recognize("он вышел как гладиатор на арену", None).title_id is None
    # предлог + слово строчными — не предлагается
    assert rec.recognize("Он уже не дышал… медик был на грани", None).title_id is None
    # заголовок КАПСОМ не превращает каждое слово в название
    assert rec.recognize("ГЛАДИАТОР ЭТО СИЛА И ЧЕСТЬ", None).title_id is None


def test_generic_hashtag_penalised(rec):
    m = rec.recognize("Он встретил её в 1957-м #любовь #историялюбви", None)
    assert m.title_id is None or m.conf < 0.6


def test_short_generic_hashtag_rejected(rec):
    assert rec.recognize("Генерал поставил на место дочь вора #герои #сериал", None).title_id is None


def test_ban_alias(rec):
    assert rec.recognize("Нашел отца #рикиморти", None).title_id is None  # нет в тестовом словаре
    assert rec.recognize("Смешной момент #гаррипоттер", None).title_id == "Q7"
    rec.ban("#гаррипоттер")
    assert rec.recognize("Смешной момент #гаррипоттер", None).title_id is None
