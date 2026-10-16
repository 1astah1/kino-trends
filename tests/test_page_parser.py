from pathlib import Path

from kinotrends.yt.page import parse_page

FIX = Path(__file__).parent / "fixtures" / "shorts_page.html"


def test_parse_real_page():
    d = parse_page("-VuE032aCzQ", FIX.read_text(encoding="utf-8"), "https://www.youtube.com/shorts/-VuE032aCzQ")
    assert d.status == "ok"
    assert d.title.startswith("Тот самый друг")
    assert d.duration_s == 20
    assert d.published_at == "2025-04-09T10:45:00Z"
    assert d.views == 59681984
    assert d.channel_id == "UCD3yYQZw3jCTqarQaKpPygA"
    assert d.channel_name == "МайорКрафт"
    assert d.category == "Film & Animation"
    assert d.avail_ru is True
    assert d.is_short is True


def test_parse_login_wall():
    html = '<html><script>var ytInitialPlayerResponse = {"playabilityStatus":{"status":"LOGIN_REQUIRED","reason":"Войдите в аккаунт, чтобы подтвердить, что вы не бот"}};</script></html>'
    d = parse_page("x", html)
    assert d.status == "bot"


def test_parse_removed():
    html = '<html><script>var ytInitialPlayerResponse = {"playabilityStatus":{"status":"ERROR","reason":"Видео недоступно"}};</script></html>'
    assert parse_page("x", html).status == "removed"


def test_parse_garbage():
    assert parse_page("x", "<html>nothing</html>").status == "error"
