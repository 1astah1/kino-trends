"""Трекинг-ссылки, журналы кликов и конверсий, вебхук продукта (M3-B, API `/api/v1`)."""

from __future__ import annotations

from typing import Any

import pytest

from autoshorts.api.schemas.product_integration import ProductIntegration
from autoshorts.api.schemas.tracking import TrackingLink


@pytest.fixture
def catalog(db: Any) -> dict[str, Any]:
    from tests.api.test_packages_support import seed_m3

    return seed_m3(db)


@pytest.fixture
def operator(login_as: Any) -> Any:
    return login_as("operator")


@pytest.fixture
def admin(login_as: Any) -> Any:
    return login_as("admin")


def test_create_manual_campaign_and_channel_links(
    client: Any, db: Any, catalog: dict[str, Any], operator: Any
) -> None:
    manual = operator.post(
        "/tracking-links",
        json={"product_id": "umka", "level": "manual", "code": "promo2026", "label": "Промо"},
    )
    assert manual.status_code == 201, manual.text
    link = TrackingLink.model_validate(manual.json())
    assert link.code == "promo2026" and link.redirect_url == "https://u.umka.chat/promo2026"
    assert (
        link.telegram_url == "tg://resolve?domain=umka_bot&start=promo2026"
        and link.created_by == operator.user["id"]
    )
    generated = operator.post("/tracking-links", json={"product_id": "umka", "level": "manual"}).json()
    assert len(generated["code"]) == 5
    camp = operator.post(
        "/tracking-links",
        json={"product_id": "umka", "level": "campaign", "campaign_id": catalog["campaign_id"]},
    )
    assert camp.status_code == 201 and camp.json()["campaign_id"] == catalog["campaign_id"]
    ch = operator.post(
        "/tracking-links", json={"product_id": "umka", "level": "channel", "channel_id": "taro_vk"}
    )
    assert ch.status_code == 201 and ch.json()["platform"] == "vk"
    # 409 на занятый код (без учёта регистра), 422 на служебные и алиасы
    taken = operator.post(
        "/tracking-links", json={"product_id": "umka", "level": "manual", "code": "PROMO2026"}
    )
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "tracking_code_taken"
    for bad, kind in (
        ("health", "code_reserved"),
        ("promo_l", "code_landing_suffix"),
        ("yt_x1", "code_alias_prefix"),
    ):
        resp = operator.post("/tracking-links", json={"product_id": "umka", "level": "manual", "code": bad})
        assert resp.status_code == 422 and resp.json()["error"]["details"]["errors"][0]["type"] == kind, bad
    assert (
        operator.post(
            "/tracking-links", json={"product_id": "umka", "level": "manual", "code": "код"}
        ).status_code
        == 422
    )
    assert (
        operator.post("/tracking-links", json={"product_id": "umka", "level": "channel"}).status_code == 422
    )
    assert operator.post("/tracking-links", json={"product_id": "nope", "level": "manual"}).status_code == 404
    assert (
        operator.post("/tracking-links", json={"product_id": "umka", "level": "package"}).status_code == 422
    )


def test_list_patch_get_stats(
    client: Any, db: Any, catalog: dict[str, Any], operator: Any, login_as: Any
) -> None:
    link = operator.post(
        "/tracking-links", json={"product_id": "umka", "level": "manual", "code": "promo1"}
    ).json()
    db.execute(
        "INSERT INTO clicks (code, tracking_link_id, route, product_id, visitor_key, is_bot) VALUES "
        "('promo1', %s, 'landing', 'umka', 'a', false), ('promo1', %s, 'to_telegram', 'umka', 'a', false),"
        "('promo1', %s, 'landing', 'umka', 'b', true)",
        (link["id"], link["id"], link["id"]),
    )
    db.execute(
        "INSERT INTO conversions (product_id, external_event_id, event_type, occurred_at, tracking_link_id, "
        "amount_minor, currency) VALUES ('umka', 'p1', 'first_payment', now(), %s, 49000, 'RUB'),"
        "('umka', 's1', 'bot_start', now(), %s, NULL, NULL)",
        (link["id"], link["id"]),
    )
    listing = operator.get("/tracking-links", params={"product_id": "umka", "q": "promo"}).json()
    assert listing["total"] == 1 and listing["items"][0]["clicks_7d"] == 2
    assert operator.get("/tracking-links", params={"level": "channel"}).json()["total"] == 0
    stats = operator.get(f"/tracking-links/{link['id']}/stats").json()
    t = stats["totals"]
    assert (t["clicks"], t["unique_visitors"], t["bot_clicks"], t["to_telegram"]) == (2, 1, 1, 1)
    assert (t["bot_starts"], t["first_payments"], t["revenue_minor"], t["currency"]) == (1, 1, 49000, "RUB")
    assert sum(p["clicks"] for p in stats["series"]) == 2
    patched = operator.patch(f"/tracking-links/{link['id']}", json={"is_active": False, "label": "старое"})
    assert patched.status_code == 200 and patched.json()["is_active"] is False
    assert operator.get(f"/tracking-links/{link['id']}").json()["label"] == "старое"
    assert operator.get("/tracking-links/99999").status_code == 404
    viewer = login_as("viewer")
    assert viewer.get("/tracking-links").status_code == 200
    assert viewer.post("/tracking-links", json={"product_id": "umka", "level": "manual"}).status_code == 403


def test_channel_link_is_idempotent(client: Any, db: Any, catalog: dict[str, Any], operator: Any) -> None:
    first = operator.post("/channels/taro_main/tracking-link", json={})
    assert first.status_code == 200, first.text
    again = operator.post("/channels/taro_main/tracking-link", json={}).json()
    assert again["id"] == first.json()["id"] and again["level"] == "channel" and again["product_id"] == "umka"
    assert operator.post("/channels/nope/tracking-link", json={}).status_code == 404


def test_click_and_conversion_journals(client: Any, db: Any, catalog: dict[str, Any], operator: Any) -> None:
    for i in range(5):
        db.execute(
            "INSERT INTO clicks (code, route, product_id, visitor_key, is_bot) "
            "VALUES (%s, 'landing', 'umka', 'v', %s)",
            (f"c{i}", i == 4),
        )
    page = operator.get("/clicks", params={"limit": 2}).json()
    assert [c["code"] for c in page["items"]] == ["c3", "c2"] and page["next_before_id"] == page["items"][-1][
        "id"
    ]
    rest = operator.get("/clicks", params={"before_id": page["next_before_id"], "limit": 10}).json()
    assert [c["code"] for c in rest["items"]] == ["c1", "c0"] and rest["next_before_id"] is None
    assert len(operator.get("/clicks", params={"include_bots": True}).json()["items"]) == 5
    db.execute(
        "INSERT INTO tracking_links (code, product_id, level, channel_id) "
        "VALUES ('chan1', 'umka', 'channel', 'taro_main')"
    )
    tl = db.execute("SELECT id FROM tracking_links WHERE code = 'chan1'").fetchone()["id"]
    db.execute(
        "INSERT INTO conversions (product_id, external_event_id, event_type, occurred_at, tracking_link_id, "
        "channel_id, is_test) VALUES ('umka', 'x1', 'bot_start', now(), %s, 'taro_main', false),"
        "('umka', 'x2', 'bot_start', now(), NULL, NULL, false),"
        "('umka', 'x3', 'bot_start', now(), NULL, NULL, true)",
        (tl,),
    )
    conv = operator.get("/conversions").json()["items"]
    assert [c["external_event_id"] for c in conv] == ["x2", "x1"]
    assert {c["external_event_id"]: c["attribution_level"] for c in conv} == {"x2": "direct", "x1": "channel"}
    assert len(operator.get("/conversions", params={"include_test": True}).json()["items"]) == 3


def test_webhook_rotate_integration_and_test_conversion(
    client: Any, db: Any, catalog: dict[str, Any], operator: Any, admin: Any
) -> None:
    # без секрета тестовое событие честно отвергается подписью
    no_secret = operator.post("/products/umka/test-conversion", json={})
    assert no_secret.status_code == 200 and no_secret.json()["ok"] is False
    assert no_secret.json()["error_code"] == "invalid_signature"
    assert operator.post("/products/umka/webhook-secret/rotate", json={}).status_code == 403
    rotated = admin.post("/products/umka/webhook-secret/rotate", json={"grace_hours": 12})
    assert rotated.status_code == 200, rotated.text
    body = rotated.json()
    assert body["secret_name"] == "PRODUCT_WEBHOOK_UMKA" and len(body["secret"]) >= 40
    assert body["previous_valid_until"] is None  # прежнего секрета не было
    audit = db.execute("SELECT * FROM audit_log WHERE action = 'product.webhook_secret_rotate'").fetchone()
    assert body["secret"] not in str(audit["diff"]) and body["secret"] not in (audit["summary"] or "")
    second = admin.post("/products/umka/webhook-secret/rotate", json={"grace_hours": 12}).json()
    assert second["previous_valid_until"] is not None and second["secret"] != body["secret"]

    db.execute(
        "INSERT INTO tracking_links (code, product_id, level, channel_id) "
        "VALUES ('chn22', 'umka', 'channel', 'taro_main')"
    )
    test = operator.post("/products/umka/test-conversion", json={"event_type": "signup"})
    assert test.status_code == 200, test.text
    res = test.json()
    assert res["ok"] is True and res["http_status"] == 200 and res["response"]["accepted"] == 1
    row = db.execute("SELECT * FROM conversions WHERE external_event_id = %s", (res["event_id"],)).fetchone()
    assert row["is_test"] is True and row["event_type"] == "signup" and row["tag"] == "chn22"

    info = operator.get("/products/umka/integration")
    assert info.status_code == 200, info.text
    integ = ProductIntegration.model_validate(info.json())
    assert integ.ingest_url == "https://u.umka.chat/ingest/v1/events"
    assert integ.secret_set is True and integ.previous_secret_valid_until is not None
    assert integ.max_events == 100 and integ.max_skew_sec == 300 and integ.max_body_bytes == 256 * 1024
    assert {h.name for h in integ.headers} >= {"X-AS-Product", "X-AS-Timestamp", "X-AS-Signature"}
    assert integ.example_payload["events"][0]["tag"] == "chn22_l"
    kinds = {d.kind.value: d.url for d in integ.deeplink_examples}
    assert kinds["bot_start"] == "https://telegram.me/umka_bot?start=chn22"
    assert kinds["bot_start_landing"].endswith("start=chn22_l") and "utm_campaign=chn22" in kinds["site_utm"]
    assert "hmac" in integ.emitter_snippets.python and "createHmac" in integ.emitter_snippets.node
    assert integ.last_event_at is None  # тестовые события не считаются
    outcomes = [r.outcome.value for r in integ.recent_requests]
    assert outcomes[0] == "accepted" and "invalid_signature" in outcomes
    assert integ.recent_requests[0].is_test is True
    assert operator.get("/products/nope/integration").status_code == 404
