"""Каталог M2-A: кампании (CRUD, статус, таргетинг) и назначение варианта ролику в БД."""

from __future__ import annotations

from collections import Counter
from typing import Any


def _variant(user: Any, campaign_id: int, code: str, weight: int, *, activate: bool = True) -> dict[str, Any]:
    resp = user.post(
        f"/campaigns/{campaign_id}/variants", json={"code": code, "name": code, "weight": weight}
    )
    assert resp.status_code == 201, resp.text
    if activate:
        act = user.post(f"/banner-variants/{resp.json()['id']}/status", json={"status": "active"})
        assert act.status_code == 200, act.text
        return act.json()
    return resp.json()


def test_campaign_crud_and_errors(login_as, db) -> None:
    op = login_as("operator")
    created = op.post(
        "/campaigns",
        json={"product_id": "umka", "name": "Осень", "priority": 50, "targeting": {"platforms": ["youtube"]}},
    )
    assert created.status_code == 201, created.text
    c = created.json()
    assert c["status"] == "draft" and c["banner_template_id"] is None and c["variants_count"] == 0
    assert c["targeting"] == {"channel_ids": [], "niche_ids": [], "platforms": ["youtube"], "kinds": []}

    no_product = op.post("/campaigns", json={"product_id": "nope", "name": "x"})
    assert no_product.status_code == 422
    assert no_product.json()["error"]["details"]["errors"][0]["loc"] == ["body", "product_id"]
    no_tpl = op.post("/campaigns", json={"product_id": "umka", "name": "x", "banner_template_id": 999})
    assert no_tpl.json()["error"]["details"]["errors"][0]["loc"] == ["body", "banner_template_id"]
    bad_window = op.post(
        "/campaigns",
        json={
            "product_id": "umka",
            "name": "x",
            "starts_at": "2026-10-10T00:00:00Z",
            "ends_at": "2026-10-01T00:00:00Z",
        },
    )
    assert bad_window.status_code == 422

    patched = op.patch(
        f"/campaigns/{c['id']}", json={"targeting": {"niche_ids": ["taro"]}, "allocation": "round_robin"}
    )
    assert patched.status_code == 200
    assert (
        patched.json()["targeting"]["niche_ids"] == ["taro"] and patched.json()["allocation"] == "round_robin"
    )
    assert op.patch(f"/campaigns/{c['id']}", json={"name": None}).status_code == 422

    _variant(op, c["id"], "A", 50, activate=False)
    active = op.post(f"/campaigns/{c['id']}/status", json={"status": "active"})
    assert (
        active.status_code == 200
        and active.json()["status"] == "active"
        and active.json()["variants_count"] == 1
    )
    assert op.get("/campaigns", params={"status": ["active"], "product_id": "umka"}).json()["total"] == 1
    assert op.get("/campaigns", params={"status": ["ended"]}).json()["total"] == 0
    assert op.get("/campaigns/999").json()["error"]["code"] == "campaign_not_found"
    assert op.get("/campaigns/999/variants").status_code == 404
    assert op.get("/banner-variants/999").json()["error"]["code"] == "banner_variant_not_found"
    dup_code = op.post(f"/campaigns/{c['id']}/variants", json={"code": "A", "name": "again"})
    assert dup_code.status_code == 422

    viewer = login_as("viewer")
    assert viewer.get(f"/campaigns/{c['id']}").status_code == 200
    assert viewer.post(f"/campaigns/{c['id']}/status", json={"status": "paused"}).status_code == 403
    assert (
        db.execute("SELECT count(*) AS n FROM audit_log WHERE entity_type = 'campaign'").fetchone()["n"] >= 3
    )


def test_assign_banner_weighted_and_deterministic(login_as, app_env) -> None:
    from autoshorts.db.pool import connection
    from autoshorts.services.campaigns import assign_banner, pick_campaign, variant_snapshot

    op = login_as("operator")
    yt = op.post(
        "/campaigns",
        json={"product_id": "umka", "name": "YT", "priority": 10, "targeting": {"platforms": ["youtube"]}},
    ).json()
    anywhere = op.post("/campaigns", json={"product_id": "umka", "name": "Все", "priority": 100}).json()
    a = _variant(op, yt["id"], "A", 70)
    b = _variant(op, yt["id"], "B", 30)
    _variant(op, yt["id"], "C", 50, activate=False)  # draft не назначается
    z = _variant(op, anywhere["id"], "Z", 1)
    for cid in (yt["id"], anywhere["id"]):
        assert op.post(f"/campaigns/{cid}/status", json={"status": "active"}).status_code == 200

    with connection() as conn:
        assert pick_campaign(conn, platform="youtube")["id"] == yt["id"]
        assert pick_campaign(conn, platform="vk")["id"] == anywhere["id"]
        counts: Counter[int] = Counter()
        for i in range(2000):
            uid = f"{i:012x}"
            first = assign_banner(conn, video_uid=uid, platform="youtube")
            assert first == assign_banner(conn, video_uid=uid, platform="youtube")
            counts[first["banner_variant_id"]] += 1
        vk = assign_banner(conn, video_uid="000000000001", platform="vk")
        explicit = assign_banner(conn, video_uid="000000000001", campaign_id=anywhere["id"])
        snapshot = variant_snapshot(conn, a["id"])
    assert set(counts) == {a["id"], b["id"]}
    assert 0.64 < counts[a["id"]] / 2000 < 0.76
    assert vk == {"campaign_id": anywhere["id"], "product_id": "umka", "banner_variant_id": z["id"]}
    assert explicit["banner_variant_id"] == z["id"]
    assert snapshot["variant"]["code_url_template"] == "u.umka.chat/{code}"
    assert snapshot["variant"]["layers"][0]["id"] == "micro_brand"

    # manual — не назначается; пауза варианта убирает его из выбора
    op.patch(f"/campaigns/{anywhere['id']}", json={"allocation": "manual"})
    op.post(f"/banner-variants/{b['id']}/status", json={"status": "paused"})
    with connection() as conn:
        assert assign_banner(conn, video_uid="x", platform="vk")["banner_variant_id"] is None
        only_a = {
            assign_banner(conn, video_uid=f"u{i}", platform="youtube")["banner_variant_id"] for i in range(50)
        }
    assert only_a == {a["id"]}
