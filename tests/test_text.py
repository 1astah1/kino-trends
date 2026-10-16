from kinotrends.text import detect_lang, duration_bucket, hashtags, norm, squash, strip_year_season, title_features, year_hint


def test_norm_basic():
    assert norm("Джентльмены, (2019)!") == "джентльмены 2019"
    assert norm("Ёжик в тумане") == "ежик в тумане"
    assert norm("  The   Orville ") == "the orville"
    assert squash("Гарри Поттер") == "гарриποттер".replace("ποттер", "поттер")


def test_hashtags_and_lang():
    assert hashtags("Момент #Фильм #кино #Shorts", "#сериал") == ["фильм", "кино", "shorts", "сериал"]
    assert detect_lang("Он всех спас #movie") == "ru"
    assert detect_lang("He forced me down #фильм") == "en"
    assert detect_lang("Найкращий серіал") == "uk"
    assert detect_lang("Қазақша фильм") == "kk"
    assert detect_lang("😂🔥") == "other"


def test_year_and_season():
    assert year_hint("Орвилл (2017)") == 2017
    assert year_hint("без года") is None
    assert strip_year_season("Волчонок 3 сезон (2013)") == "Волчонок"


def test_duration_bucket_and_features():
    assert duration_bucket(10) == "до 15 с"
    assert duration_bucket(60) == "46–60 с"
    assert duration_bucket(None) == "неизвестно"
    f = title_features("Что это за фильм? 😂 Название в комментариях | #фильм #shorts", None)
    assert f["question"] and f["emoji"] and f["hidden_title"] and f["pipe"] and f["has_shorts_tag"]
    assert f["n_hashtags"] == 2
