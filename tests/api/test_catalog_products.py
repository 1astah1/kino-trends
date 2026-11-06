"""Каталог M2-A: продукты, кусты, каналы (профиль, дубли, здоровье, инциденты), ассеты (шрифты, картинки)."""

from __future__ import annotations

import io
import secrets
import shutil
from pathlib import Path
from typing import Any

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def _seed_niche_cluster(db: Any, niche: str = "taro", cluster: str = "main") -> None:
    db.execute("INSERT INTO niches (id, label) VALUES (%s, %s) ON CONFLICT DO NOTHING", (niche, niche))
    db.execute("INSERT INTO clusters (id, title) VALUES (%s, %s) ON CONFLICT DO NOTHING", (cluster, cluster))


# ---------------------------------------------------------------- продукты


def test_products_crud_and_stats(login_as, db) -> None:
    op = login_as("operator")
    listed = op.get("/products").json()
    umka = next(p for p in listed["items"] if p["id"] == "umka")
    assert umka["brand"]["display_domain"] == "umka.chat" and umka["stats_7d"]["clicks"] == 0

    created = op.post(
        "/products",
        json={
            "id": "vpn_bot",
            "name": "VPN-бот",
            "kind": "telegram_bot",
            "redirect_host": "u.vpn.example",
            "telegram_bot_username": "vpn_example_bot",
            "brand": {"display_domain": "vpn.example"},
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["brand"] == {"display_domain": "vpn.example", "primary_color": None, "logo_asset_id": None}
    assert body["webhook_secret_set"] is False and body["stats_7d"] is None

    dup = op.post("/products", json={"id": "vpn_bot", "name": "x", "kind": "other"})
    assert dup.status_code == 422 and dup.json()["error"]["details"]["errors"][0]["loc"] == ["body", "id"]

    patched = op.patch("/products/vpn_bot", json={"brand": {"primary_color": "#112233"}, "name": "VPN"})
    assert patched.status_code == 200
    assert patched.json()["brand"]["display_domain"] == "vpn.example"  # частичный PATCH сливает brand
    assert patched.json()["brand"]["primary_color"] == "#112233"
    assert op.patch("/products/vpn_bot", json={"name": None}).status_code == 422

    db.execute(
        "INSERT INTO clicks (code, route, product_id, visitor_key)"
        " VALUES ('abc12', 'landing', 'vpn_bot', 'v1'),"
        " ('abc12', 'landing', 'vpn_bot', 'v1'), ('abc12', 'to_site', 'vpn_bot', 'v2')"
    )
    db.execute(
        "INSERT INTO conversions"
        " (product_id, external_event_id, event_type, occurred_at, amount_minor, currency)"
        " VALUES ('vpn_bot', 'e1', 'bot_start', now(), NULL, NULL),"
        " ('vpn_bot', 'e2', 'first_payment', now(), 49000, 'RUB')"
    )
    stats = next(p for p in op.get("/products").json()["items"] if p["id"] == "vpn_bot")["stats_7d"]
    assert stats == {
        "clicks": 3,
        "unique_visitors": 2,
        "bot_starts": 1,
        "first_payments": 1,
        "revenue_minor": 49000,
        "currency": "RUB",
    }

    archived = op.post("/products/vpn_bot/archive")
    assert archived.status_code == 200 and archived.json()["status"] == "archived"
    assert op.get("/products", params={"status": "archived"}).json()["total"] == 1
    assert op.get("/products/nope").json()["error"]["code"] == "product_not_found"

    viewer = login_as("viewer")
    assert viewer.get("/products/umka").status_code == 200
    assert viewer.post("/products/umka/archive").status_code == 403


# ---------------------------------------------------------------- кусты


def test_clusters(login_as, db) -> None:
    op = login_as("operator")
    created = op.post("/clusters", json={"id": "cluster_a", "title": "Куст А"})
    assert created.status_code == 201 and created.json()["channels_count"] == 0
    assert op.post("/clusters", json={"id": "cluster_a", "title": "x"}).status_code == 422
    patched = op.patch("/clusters/cluster_a", json={"youtube_api_key_secret": "YOUTUBE_API_KEY_A"})
    assert patched.json()["youtube_api_key_secret"] == "YOUTUBE_API_KEY_A"
    db.execute("INSERT INTO niches (id, label) VALUES ('taro', 'taro')")
    db.execute(
        "INSERT INTO channels (id, niche_id, platform, cluster)"
        " VALUES ('ch1', 'taro', 'youtube', 'cluster_a')"
    )
    assert op.get("/clusters").json()["items"][0]["channels_count"] == 1
    assert op.get("/clusters/none").status_code == 404


# ---------------------------------------------------------------- каналы


def test_channels_profile_duplicates_and_errors(login_as, db) -> None:
    _seed_niche_cluster(db)
    op = login_as("operator")
    base = {"platform": "youtube", "niche_id": "taro", "cluster": "main"}
    a = op.post(
        "/channels", json={**base, "id": "taro_a", "title": "Таро А", "profile": {"tts_voice": "Dmitry"}}
    )
    assert a.status_code == 201, a.text
    assert a.json()["profile"]["tts_voice"] == "Dmitry" and a.json()["profile_duplicate_of"] == []

    b = op.post("/channels", json={**base, "id": "taro_b", "profile": {"tts_voice": "Dmitry"}})
    assert b.json()["profile_duplicate_of"] == ["taro_a"]
    assert op.get("/channels/taro_a").json()["profile_duplicate_of"] == ["taro_b"]

    # другой профиль — предупреждения нет; VK с тем же профилем — другая площадка, не дубль
    fixed = op.patch("/channels/taro_b", json={"profile": {"tts_voice": "Svetlana", "seed_family": "b"}})
    assert fixed.status_code == 200 and fixed.json()["profile_duplicate_of"] == []
    assert fixed.json()["profile"] == {
        "tts_provider": None,
        "tts_voice": "Svetlana",
        "banner_template_id": None,
        "visual_style_suffix": None,
        "seed_family": "b",
    }
    vk = op.post(
        "/channels", json={**base, "platform": "vk", "id": "taro_vk", "profile": {"tts_voice": "Dmitry"}}
    )
    assert vk.json()["profile_duplicate_of"] == []

    bad_niche = op.post("/channels", json={**base, "id": "x_channel", "niche_id": "nope"})
    assert bad_niche.status_code == 422
    assert bad_niche.json()["error"]["details"]["errors"][0]["loc"] == ["body", "niche_id"]
    bad_cluster = op.post("/channels", json={**base, "id": "x_channel", "cluster": "nope"})
    assert bad_cluster.json()["error"]["details"]["errors"][0]["loc"] == ["body", "cluster"]
    bad_tts = op.post("/channels", json={**base, "id": "x_channel", "profile": {"tts_provider": "nope"}})
    assert bad_tts.json()["error"]["details"]["errors"][0]["loc"] == ["body", "profile", "tts_provider"]
    ok_tts = op.post("/channels", json={**base, "id": "x_channel", "profile": {"tts_provider": "edge_tts"}})
    assert ok_tts.status_code == 201
    assert op.post("/channels", json={**base, "id": "taro_a"}).status_code == 422
    assert op.patch("/channels/taro_a", json={"replaced_by": "taro_a"}).status_code == 422

    filtered = op.get("/channels", params={"platform": "youtube", "status": ["active"], "niche_id": "taro"})
    assert filtered.json()["total"] == 3
    assert op.get("/channels", params={"cluster_id": "nope"}).json()["total"] == 0


def test_channel_incidents_and_health(login_as, db) -> None:
    _seed_niche_cluster(db)
    op = login_as("operator")
    op.post("/channels", json={"id": "taro_a", "platform": "youtube", "niche_id": "taro", "cluster": "main"})
    inc = op.post("/channels/taro_a/incidents", json={"kind": "reach_drop", "note": "просмотры упали"})
    assert inc.status_code == 201 and inc.json()["kind"] == "reach_drop"
    assert op.get("/channels/taro_a/incidents").json()["items"][0]["note"] == "просмотры упали"

    # 10 публикаций, новые первыми: две последние сильно ниже p25 канала
    for i, views in enumerate([5, 4, 100, 120, 90, 110, 130, 95, 105, 115]):
        job_id = db.execute(
            "INSERT INTO jobs (video_uid, channel_id, niche_id, template_version)"
            " VALUES (%s, 'taro_a', 'taro', 'v') RETURNING id",
            (f"uid{i}",),
        ).fetchone()["id"]
        db.execute(
            "INSERT INTO publications (job_id, channel_id, platform, master_file_path, published_at,"
            " latest_views, platform_video_id)"
            " VALUES (%s, 'taro_a', 'youtube', 'm', now() - make_interval(hours => %s), %s, %s)",
            (job_id, i + 1, views, f"vid{i}"),
        )
    db.execute("UPDATE publications SET status = 'removed_by_platform' WHERE platform_video_id = 'vid9'")
    health = op.get("/channels/taro_a/health").json()
    assert health["status"] == "active"
    assert health["removed_count_30d"] == 1
    assert "reach_drop" in health["sanction_signals"] and "removed_by_platform" in health["sanction_signals"]
    assert health["below_p25_streak"] == 2
    assert len(health["last_publications"]) == 10


# ---------------------------------------------------------------- ассеты


def _upload_row(db: Any, media_root: Path, user_id: int, data: bytes, filename: str) -> str:
    upload_id = secrets.token_hex(16)
    rel = f"uploads/{upload_id}.part"
    (media_root / "uploads").mkdir(parents=True, exist_ok=True)
    (media_root / rel).write_bytes(data)
    db.execute(
        "INSERT INTO uploads"
        " (id, user_id, purpose, filename, length_bytes, received_bytes, path, status, expires_at)"
        " VALUES (%s, %s, 'asset', %s, %s, %s, %s, 'complete', now() + interval '1 day')",
        (upload_id, user_id, filename, len(data), len(data), rel),
    )
    return upload_id


def _latin_only_font(path: Path) -> None:
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef", "A"])
    fb.setupCharacterMap({ord("A"): "A"})
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((500, 700))
    pen.lineTo((1000, 0))
    pen.closePath()
    glyph = pen.glyph()
    fb.setupGlyf({".notdef": glyph, "A": glyph})
    fb.setupHorizontalMetrics({".notdef": (1000, 0), "A": (1000, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "LatinOnly", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.save(str(path))


def test_assets_font_and_image(login_as, db, app_env, tmp_path) -> None:
    op = login_as("operator")
    media_root = Path(app_env.media_root)
    user_id = op.user["id"]

    font_bytes = (ROOT / "config" / "fonts" / "Montserrat-Bold.ttf").read_bytes()
    up = _upload_row(db, media_root, user_id, font_bytes, "Montserrat-Bold.ttf")
    created = op.post(
        "/assets",
        json={"upload_id": up, "kind": "font", "name": "Montserrat Bold", "license_note": "OFL 1.1"},
    )
    assert created.status_code == 201, created.text
    asset = created.json()
    assert (
        asset["mime"] == "font/ttf"
        and asset["meta"]["cyrillic_ok"] is True
        and asset["meta"]["missing"] == []
    )
    assert db.execute("SELECT status FROM uploads WHERE id = %s", (up,)).fetchone()["status"] == "consumed"
    got = op.get(asset["file_url"].removeprefix("/api/v1"))
    assert got.status_code == 200 and got.content == font_bytes

    latin = tmp_path / "latin.ttf"
    _latin_only_font(latin)
    up2 = _upload_row(db, media_root, user_id, latin.read_bytes(), "latin.ttf")
    rejected = op.post(
        "/assets", json={"upload_id": up2, "kind": "font", "name": "Latin", "license_note": "own"}
    )
    assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "font_no_cyrillic"
    assert "Ж" in rejected.json()["error"]["details"]["missing"]

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "red").save(buf, format="PNG")
    up3 = _upload_row(db, media_root, user_id, buf.getvalue(), "bg.png")
    wrong_kind = op.post(
        "/assets", json={"upload_id": up3, "kind": "font", "name": "bg", "license_note": "own"}
    )
    assert wrong_kind.status_code == 422
    image = op.post("/assets", json={"upload_id": up3, "kind": "image", "name": "Фон", "license_note": "own"})
    assert image.status_code == 201 and image.json()["mime"] == "image/png"

    kinds = {a["kind"] for a in op.get("/assets").json()["items"]}
    assert kinds == {"font", "image"}
    assert op.get("/assets", params={"kind": "font"}).json()["items"][0]["name"] == "Montserrat Bold"
    assert op.delete(f"/assets/{asset['id']}").status_code == 204
    assert op.get(f"/assets/{asset['id']}/file").status_code == 404
    assert op.delete(f"/assets/{asset['id']}").status_code == 404
    assert (
        op.post(
            "/assets", json={"upload_id": up, "kind": "font", "name": "x", "license_note": "own"}
        ).status_code
        == 422
    )
    shutil.rmtree(media_root / "assets", ignore_errors=True)
