"""Публичный редиректор (M3-B, `public/app.py`): /health без клика, бот по UA → is_bot, неизвестный код →
лендинг продукта по умолчанию, CSP, `<script>` → 404, /go/*, прямые режимы, опечатки в коде."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

BROWSER = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129 Mobile"


@pytest.fixture
def public(app_env: Any, db: Any) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from autoshorts.public import app as public_app
    from autoshorts.services import settings

    settings.invalidate_cache()
    public_app.products.clear()
    with TestClient(public_app.create_app(), headers={"User-Agent": BROWSER}) as c:
        yield c
    public_app.products.clear()


@pytest.fixture
def links(db: Any) -> dict[str, Any]:
    from tests.api.test_jobs_support import make_job
    from tests.api.test_packages_support import seed_m3

    m3 = seed_m3(db)
    job = make_job(db, status="published", banner_variant_id=m3["variant_a"], campaign_id=m3["campaign_id"])
    link = db.execute(
        """INSERT INTO tracking_links (code, product_id, level, job_id, channel_id, campaign_id,
                                       banner_variant_id, platform)
           VALUES ('k7m2q', 'umka', 'package', %s, 'taro_main', %s, %s, 'youtube') RETURNING *""",
        (job["id"], m3["campaign_id"], m3["variant_a"]),
    ).fetchone()
    return {"job": job, "link": link, **m3}


def clicks(db: Any) -> list[dict[str, Any]]:
    return db.execute("SELECT * FROM clicks ORDER BY id").fetchall()


def test_health_and_robots_do_not_log_clicks(public: Any, db: Any) -> None:
    resp = public.get("/health")
    assert resp.status_code == 200 and resp.json() == {"status": "ok"}
    robots = public.get("/robots.txt")
    assert robots.status_code == 200 and "Disallow: /" in robots.text
    assert clicks(db) == []


def test_landing_logs_click_with_dimensions_and_csp(public: Any, db: Any, links: dict[str, Any]) -> None:
    from autoshorts.public.landing import LANDING_SCRIPT, script_hash

    resp = public.get("/k7m2q")
    assert resp.status_code == 200
    csp = resp.headers["content-security-policy"]
    assert "default-src 'none'" in csp and f"'{script_hash(LANDING_SCRIPT)}'" in csp
    assert "style-src 'unsafe-inline'" in csp and "img-src 'self' data:" in csp
    assert resp.headers["x-content-type-options"] == "nosniff" and resp.headers["cache-control"] == "no-store"
    html = resp.text
    # без внешних ресурсов: ни src=, ни <link>, ссылки только на свой /go/*
    assert "src=" not in html and "<link" not in html
    assert re.findall(r'href="([^"]+)"', html) == ["/go/k7m2q/tg", "/go/k7m2q/site"]
    assert 'data-code="&#34;k7m2q&#34;"' in html
    assert "Бесплатный расклад ждёт тебя" in html  # products.landing
    [c] = clicks(db)
    link = links["link"]
    assert c["route"] == "landing" and c["is_bot"] is False and c["code"] == "k7m2q"
    assert c["tracking_link_id"] == link["id"] and c["job_id"] == links["job"]["id"]
    assert c["product_id"] == "umka" and c["channel_id"] == "taro_main" and c["platform"] == "youtube"
    assert c["banner_variant_id"] == links["variant_a"] and c["campaign_id"] == links["campaign_id"]
    assert re.fullmatch(r"[0-9a-f]{32}", c["visitor_key"])
    assert c["user_agent"] == BROWSER


def test_bot_user_agent_and_head_are_marked(public: Any, db: Any, links: dict[str, Any]) -> None:
    assert public.get("/k7m2q", headers={"User-Agent": "TelegramBot (like TwitterBot)"}).status_code == 200
    assert public.get("/k7m2q", headers={"User-Agent": "facebookexternalhit/1.1"}).status_code == 200
    assert public.get("/k7m2q", headers={"User-Agent": ""}).status_code == 200
    assert public.head("/k7m2q").status_code == 200
    rows = clicks(db)
    assert len(rows) == 4 and all(r["is_bot"] for r in rows)


def test_unknown_code_gets_default_product_landing(public: Any, db: Any, links: dict[str, Any]) -> None:
    resp = public.get("/zzzzz")
    assert resp.status_code == 200 and "content-security-policy" in resp.headers
    assert 'data-code="null"' in resp.text
    [c] = clicks(db)
    assert c["route"] == "unknown_code" and c["tracking_link_id"] is None and c["product_id"] == "umka"
    # переход из такого лендинга — к боту без кода
    tg = public.get("/go/zzzzz/tg")
    assert tg.status_code == 200 and 'href="tg://resolve?domain=umka_bot"' in tg.text


def test_inactive_link_is_unknown(public: Any, db: Any, links: dict[str, Any]) -> None:
    db.execute("UPDATE tracking_links SET is_active = false WHERE code = 'k7m2q'")
    public.get("/k7m2q")
    assert clicks(db)[0]["route"] == "unknown_code"


@pytest.mark.parametrize("path", ["/%3Cscript%3E", "/<script>", "/a", "/" + "x" * 65, "/код", "/k7m2q.php"])
def test_invalid_codes_are_404_without_click(public: Any, db: Any, links: dict[str, Any], path: str) -> None:
    resp = public.get(path)
    assert resp.status_code == 404
    assert "<script>" not in resp.text
    assert clicks(db) == []


def test_typo_in_package_code_is_fixed(public: Any, db: Any, links: dict[str, Any]) -> None:
    assert public.get("/K7M2Q").status_code == 200
    [c] = clicks(db)
    assert c["tracking_link_id"] == links["link"]["id"] and c["code"] == "k7m2q"


def test_go_telegram_with_fallback(public: Any, db: Any, links: dict[str, Any]) -> None:
    from autoshorts.public.landing import TG_SCRIPT, script_hash

    resp = public.get("/go/k7m2q/tg")
    assert resp.status_code == 200
    assert f"'{script_hash(TG_SCRIPT)}'" in resp.headers["content-security-policy"]
    html = resp.text
    assert "tg://resolve?domain=umka_bot&amp;start=k7m2q_l" in html
    assert "https://telegram.me/umka_bot?start=k7m2q_l" in html
    assert 'http-equiv="refresh"' in html and "1500" in TG_SCRIPT
    [c] = clicks(db)
    assert c["route"] == "to_telegram" and c["tracking_link_id"] == links["link"]["id"]


def test_go_telegram_https_style_redirects(public: Any, db: Any, links: dict[str, Any]) -> None:
    db.execute("UPDATE products SET telegram_link_style = 'https_tme' WHERE id = 'umka'")
    resp = public.get("/go/k7m2q/tg", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://telegram.me/umka_bot?start=k7m2q_l"


def test_go_telegram_tma_uses_startapp(public: Any, db: Any, links: dict[str, Any]) -> None:
    db.execute(
        "UPDATE products SET kind = 'telegram_mini_app', tma_short_name = 'shop', "
        "telegram_link_style = 'https_tme' WHERE id = 'umka'"
    )
    resp = public.get("/go/k7m2q/tg", follow_redirects=False)
    assert resp.headers["location"] == "https://telegram.me/umka_bot/shop?startapp=k7m2q_l"


def test_go_site_redirects_with_utm(public: Any, db: Any, links: dict[str, Any]) -> None:
    resp = public.get("/go/k7m2q/site", follow_redirects=False)
    assert resp.status_code == 302
    loc = urlsplit(resp.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == "https://umka.chat/"
    q = parse_qs(loc.query)
    assert q == {
        "utm_source": ["yt"],
        "utm_medium": ["shorts"],
        "utm_campaign": ["k7m2q"],
        "utm_content": ["A"],
    }
    [c] = clicks(db)
    assert c["route"] == "to_site"


def test_direct_modes(public: Any, db: Any, links: dict[str, Any]) -> None:
    from autoshorts.public import app as public_app

    db.execute("UPDATE products SET redirect_mode = 'direct_site' WHERE id = 'umka'")
    public_app.products.clear()
    resp = public.get("/k7m2q", follow_redirects=False)
    assert resp.status_code == 302 and "utm_campaign=k7m2q" in resp.headers["location"]
    db.execute(
        "UPDATE products SET redirect_mode = 'direct_telegram', telegram_link_style = 'https_tme' "
        "WHERE id = 'umka'"
    )
    public_app.products.clear()
    resp = public.get("/k7m2q", follow_redirects=False)
    assert resp.status_code == 302 and resp.headers["location"] == "https://telegram.me/umka_bot?start=k7m2q"
    assert [c["route"] for c in clicks(db)] == ["direct", "direct"]


def test_visitor_key_is_stable_per_day_and_ua() -> None:
    from autoshorts.public.app import visitor_key

    a = visitor_key("1.2.3.4", "UA", day="2026-09-27")
    assert a == visitor_key("1.2.3.4", "UA", day="2026-09-27")
    assert a != visitor_key("1.2.3.4", "UA2", day="2026-09-27")
    assert a != visitor_key("1.2.3.4", "UA", day="2026-09-28")
    assert "1.2.3.4" not in a and len(a) == 32


def test_landing_survives_database_outage(
    public: Any, db: Any, links: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoshorts.public import app as public_app

    assert public.get("/k7m2q").status_code == 200  # продукт в кеше

    def broken(code: str) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(public_app, "_resolve", broken)
    resp = public.get("/k7m2q")
    assert resp.status_code == 200 and "Открыть в Telegram" in resp.text
