"""M0: хотфикс редиректора (ARCHITECTURE §3.3 п.2, §5.3, §10.2 M0). Без живого Postgres: БД подменяется."""
import base64
import contextlib
import hashlib
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from autoshorts import db  # noqa: E402
from autoshorts.webapp import redirect  # noqa: E402

BROWSER_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36"
)
_SCRIPT_RE = re.compile(r"<script[^>]*>(.*?)</script>", re.S | re.I)


class _Recorder:
    def __init__(self, fail=False):
        self.statements: list[tuple[str, tuple]] = []
        self.fail = fail

    @property
    def inserts(self):
        return [(sql, params) for sql, params in self.statements if "INSERT" in sql.upper()]

    @contextlib.contextmanager
    def get_conn(self):
        if self.fail:
            raise RuntimeError("БД недоступна")
        rec = self

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=()):
                rec.statements.append((sql, tuple(params)))

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return _Cur()

        yield _Conn()


@pytest.fixture
def rec(monkeypatch):
    r = _Recorder()
    monkeypatch.setattr(db, "get_conn", r.get_conn)
    return r


@pytest.fixture
def client():
    return TestClient(redirect.app)


def test_health_is_json_and_not_logged(client, rec):
    resp = client.get("/health", headers={"User-Agent": BROWSER_UA})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["status"] == "ok"
    assert rec.statements == []


def test_static_routes_declared_before_catch_all():
    paths = [getattr(r, "path", None) for r in redirect.app.routes]
    assert paths.index("/health") < paths.index("/{tag}")
    assert paths.index("/robots.txt") < paths.index("/{tag}")


def test_robots_txt_not_logged(client, rec):
    resp = client.get("/robots.txt", headers={"User-Agent": BROWSER_UA})
    assert resp.status_code == 200
    assert "Disallow: /" in resp.text
    assert rec.statements == []


@pytest.mark.parametrize("raw_path", [
    "/%3Cscript%3E",                            # <script>
    "/%3Cscript%3Ealert(1)%3C%2Fscript%3E",
    "/yt_%22%3E%3Cimg%20src%3Dx%3E",            # yt_"><img src=x>
    "/%D1%82%D0%B5%D1%81%D1%82",               # кириллица
    "/yt_%D0%B0%D0%B1%D0%B2",                   # yt_ + кириллица
    "/a",                                       # короче 2 символов
    "/" + "a" * 65,                             # длиннее 64
    "/yt.dQw4w9WgXcQ",
    "/favicon.ico",
    "/yt_abc%0A",                               # перевод строки в конце
])
def test_invalid_tag_is_404_and_not_logged(client, rec, raw_path):
    resp = client.get(raw_path, headers={"User-Agent": BROWSER_UA})
    assert resp.status_code == 404
    assert rec.statements == []
    assert "<script>" not in resp.text


def test_valid_tag_landing_has_no_raw_tag_in_js(client, rec):
    tag = "yt_dQw4w9WgXcQ"
    resp = client.get(f"/{tag}", headers={"User-Agent": BROWSER_UA})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")

    scripts = _SCRIPT_RE.findall(resp.text)
    assert scripts, "на лендинге должен быть скрипт записи метки в localStorage"
    for body in scripts:
        assert tag not in body
    # Метка передаётся в JS через data-атрибут: json.dumps + html.escape
    assert 'data-tag="&quot;yt_dQw4w9WgXcQ&quot;"' in resp.text
    assert "JSON.parse" in scripts[0]

    # Ровно одна запись перехода с этой меткой
    assert len(rec.inserts) == 1
    _sql, params = rec.inserts[0]
    assert params[0] == tag
    assert params[1] == "yt"
    assert params[2] == "domain"


def test_landing_csp_pins_inline_script(client, rec):
    resp = client.get("/yt_dQw4w9WgXcQ", headers={"User-Agent": BROWSER_UA})
    csp = resp.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    body = _SCRIPT_RE.findall(resp.text)[0]
    digest = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()
    assert f"'sha256-{digest}'" in csp
    assert "http" not in csp  # никаких внешних источников


@pytest.mark.parametrize("ua", [
    "TelegramBot (like TwitterBot)",
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "WhatsApp/2.23.20.0",
    "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)",
    "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)",
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)",
    "vkShare; +http://vk.com/dev/Share",
    "curl/8.5.0",
    "python-httpx/0.28.1",
    "",
])
def test_bot_user_agent_is_not_logged(client, rec, ua):
    resp = client.get("/yt_dQw4w9WgXcQ", headers={"User-Agent": ua})
    assert resp.status_code == 200  # превьюер всё равно получает лендинг
    assert rec.inserts == []


def test_head_request_is_not_logged(client, rec):
    resp = client.head("/yt_dQw4w9WgXcQ", headers={"User-Agent": BROWSER_UA})
    assert resp.status_code == 200
    assert rec.inserts == []


def test_landing_survives_db_failure(client, monkeypatch):
    broken = _Recorder(fail=True)
    monkeypatch.setattr(db, "get_conn", broken.get_conn)
    resp = client.get("/yt_dQw4w9WgXcQ", headers={"User-Agent": BROWSER_UA})
    assert resp.status_code == 200
    assert "Открыть в Telegram" in resp.text


def test_render_landing_escapes_even_unvalidated_tag():
    """Вторая линия обороны: даже если метка обошла регулярку, разметка не ломается."""
    evil = "x\"'></script><script>alert(1)</script>"
    page = redirect.render_landing(evil)
    assert "<script>alert(1)" not in page
    assert "</script><script>" not in page.replace(f"<script>{redirect._LANDING_SCRIPT}</script>", "")
    for body in _SCRIPT_RE.findall(page):
        assert "alert" not in body
    assert "\"'>" not in page


def test_bot_detector_passes_regular_browsers():
    assert not redirect.is_bot_user_agent(BROWSER_UA)
    assert not redirect.is_bot_user_agent(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
    )
    assert redirect.is_bot_user_agent("TelegramBot (like TwitterBot)")
