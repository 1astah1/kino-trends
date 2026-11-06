"""Баннер M2-A (решение владельца: баннер — загруженный креатив).

Загрузка креатива (статика сразу, анимация — задачей), дедупликация, отказы 413/415/422 с
`details.asset_error`, медиа, удаление с 409 `creative_in_use`; вариант из креатива со слоями по
умолчанию, 409 `version_conflict`, проверка (`creative_in_bottom_dead_zone`), «Кадр из рендера»
(PNG, креатив виден в окне импульса и не виден на хуке), шаблоны и YAML, safe zones, шрифты.
"""

from __future__ import annotations

import io
import logging
import threading
from pathlib import Path
from typing import Any

import pytest
import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
RED = (230, 20, 20)


def _png(w: int = 800, h: int = 200, color: tuple[int, int, int] = RED, alpha: bool = False) -> bytes:
    buf = io.BytesIO()
    im = Image.new("RGBA" if alpha else "RGB", (w, h), (*color, 255) if alpha else color)
    if alpha:  # прозрачная полоса сверху — настоящая альфа
        im.paste((0, 0, 0, 0), (0, 0, w, max(1, h // 10)))
    im.save(buf, format="PNG")
    return buf.getvalue()


def _gif(w: int = 200, h: int = 80, frames: int = 4) -> bytes:
    buf = io.BytesIO()
    ims = [Image.new("RGB", (w, h), (40 * i, 200 - 40 * i, 90)) for i in range(frames)]
    ims[0].save(buf, format="GIF", save_all=True, append_images=ims[1:], duration=120, loop=0)
    return buf.getvalue()


def _upload(
    user: Any, data: bytes, filename: str = "banner.png", mime: str = "image/png", **form: str
) -> Any:
    return user.post("/banner-creatives", files={"file": (filename, data, mime)}, data=form or None)


def _campaign(user: Any, **extra: Any) -> dict[str, Any]:
    resp = user.post("/campaigns", json={"product_id": "umka", "name": "Осень", **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _run_normalize_task(db: Any, task_id: int) -> dict[str, Any] | None:
    from autoshorts.services.banner_creatives import normalize_creative_task
    from autoshorts.tasks.registry import TaskContext

    task = db.execute("SELECT * FROM tasks WHERE id = %s", (task_id,)).fetchone()
    task = {**task, "attempts": 1}
    ctx = TaskContext(
        task=task,
        worker_id="test",
        cancel=threading.Event(),
        progress=lambda _p: None,
        log=logging.getLogger("test"),
        settings=None,  # type: ignore[arg-type]
        media=None,  # type: ignore[arg-type]
        log_path=Path("/dev/null"),
    )
    return normalize_creative_task(ctx)


# ---------------------------------------------------------------- креативы


def test_static_upload_dedupe_media_and_delete(login_as, db) -> None:
    op = login_as("operator")
    data = _png(alpha=True)
    resp = _upload(op, data, "Осенний баннер.png", name="Осень")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    creative = body["creative"]
    assert body["deduplicated"] is False
    assert (
        creative["status"] == "ready" and creative["kind"] == "static" and creative["source_format"] == "png"
    )
    assert (creative["width"], creative["height"]) == (800, 200) and creative["has_alpha"] is True
    assert creative["name"] == "Осень" and creative["used_by_variants"] == 0
    sha = creative["sha256"]
    assert creative["thumb_url"] == f"/api/v1/banner-creatives/{sha}/media/thumb"

    again = _upload(op, data, "copy.png")
    assert again.status_code == 201 and again.json()["deduplicated"] is True
    assert again.json()["creative"]["name"] == "Осень"

    for kind, mime in (("file", "image/png"), ("thumb", "image/png"), ("first_frame", "image/png")):
        media = op.get(f"/banner-creatives/{sha}/media/{kind}")
        assert media.status_code == 200, kind
        assert media.headers["content-type"].startswith(mime)
        Image.open(io.BytesIO(media.content)).verify()

    listed = op.get(
        "/banner-creatives", params={"q": "осен", "status": ["ready"], "sort": "-created_at"}
    ).json()
    assert listed["total"] == 1 and listed["items"][0]["sha256"] == sha
    assert op.get("/banner-creatives", params={"kind": "animated"}).json()["total"] == 0
    assert op.get("/banner-creatives", params={"sort": "evil"}).status_code == 422

    patched = op.patch(
        f"/banner-creatives/{sha}", json={"name": "Осень v2", "notes": "для A/B", "product_id": "umka"}
    )
    assert patched.status_code == 200 and patched.json()["product_id"] == "umka"
    assert op.patch(f"/banner-creatives/{sha}", json={"product_id": "nope"}).status_code == 422

    campaign = _campaign(op)
    variant = op.post(
        f"/campaigns/{campaign['id']}/variants", json={"code": "A", "name": "A", "creative_sha256": sha}
    )
    assert variant.status_code == 201, variant.text
    in_use = op.delete(f"/banner-creatives/{sha}")
    assert in_use.status_code == 409
    assert in_use.json()["error"]["code"] == "creative_in_use"
    assert in_use.json()["error"]["details"]["variant_ids"] == [variant.json()["id"]]
    assert op.get(f"/banner-creatives/{sha}").json()["used_by_variants"] == 1

    # вариант переключили на плашку — креатив можно удалить; повторная загрузка его возвращает
    layers = [la for la in variant.json()["layers"] if la["kind"] != "creative"]
    for la in layers:
        if la["kind"] == "fallback_plate":
            la["enabled"] = True
    assert op.patch(f"/banner-variants/{variant.json()['id']}", json={"layers": layers}).status_code == 200
    assert op.delete(f"/banner-creatives/{sha}").status_code == 204
    assert op.get(f"/banner-creatives/{sha}").status_code == 404
    revived = _upload(op, data)
    assert revived.json()["deduplicated"] is True and revived.json()["creative"]["sha256"] == sha
    assert op.get(f"/banner-creatives/{sha}").status_code == 200

    viewer = login_as("viewer")
    assert viewer.get(f"/banner-creatives/{sha}/media/thumb").status_code == 200
    assert _upload(viewer, _png(color=(1, 2, 3))).status_code == 403


def test_upload_rejections_carry_asset_error(login_as, monkeypatch) -> None:
    from autoshorts.services import banner_creatives

    op = login_as("operator")
    svg = _upload(op, b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', "x.svg", "image/svg+xml")
    assert svg.status_code == 415
    assert svg.json()["error"]["code"] == "unsupported_media_type"
    assert svg.json()["error"]["details"]["asset_error"] == "unsupported_format"

    tiny = _upload(op, _png(8, 8))
    assert tiny.status_code == 422 and tiny.json()["error"]["code"] == "creative_rejected"
    assert tiny.json()["error"]["details"]["asset_error"] == "dimensions_too_small"

    truncated = _upload(op, _png()[:60])
    assert truncated.status_code == 422
    assert truncated.json()["error"]["details"]["asset_error"] in {"corrupt_file", "unsupported_format"}

    empty = _upload(op, b"")
    assert empty.status_code == 422 and empty.json()["error"]["details"]["asset_error"] == "empty_file"

    monkeypatch.setattr(banner_creatives, "MAX_UPLOAD_BYTES", 1000)
    big = _upload(op, _png(900, 900, (10, 20, 30)))
    assert big.status_code == 413
    assert big.json()["error"]["code"] == "payload_too_large"
    assert big.json()["error"]["details"]["asset_error"] == "file_too_large"
    assert op.get("/banner-creatives").json()["total"] == 0


def test_animated_upload_goes_through_heavy_task(login_as, db, app_env) -> None:
    op = login_as("operator")
    resp = _upload(op, _gif(), "anim.gif", "image/gif")
    assert resp.status_code == 201, resp.text
    creative = resp.json()["creative"]
    assert creative["status"] == "processing" and creative["kind"] == "animated"
    assert creative["source_format"] == "gif" and creative["file_url"] is None
    task = db.execute("SELECT * FROM tasks WHERE id = %s", (creative["task_id"],)).fetchone()
    assert task["kind"] == "banner_creative_normalize" and task["resource_class"] == "heavy"
    assert task["payload"] == {"sha256": creative["sha256"]}
    upload = Path(app_env.media_root) / f"uploads/creative_{creative['sha256']}.gif"
    assert upload.is_file()

    # повторная загрузка, пока задача в очереди, — тот же креатив, вторая задача не ставится
    again = _upload(op, _gif(), "anim.gif", "image/gif")
    assert again.json()["deduplicated"] is True and again.json()["creative"]["task_id"] == creative["task_id"]
    assert db.execute("SELECT count(*) AS n FROM tasks").fetchone()["n"] == 1

    result = _run_normalize_task(db, creative["task_id"])
    assert result is not None and result["status"] == "ready"
    ready = op.get(f"/banner-creatives/{creative['sha256']}").json()
    assert ready["status"] == "ready" and ready["normalized_mime"] == "video/webm"
    assert ready["duration_sec"] == pytest.approx(0.48, abs=0.1) and ready["frame_count"] >= 4
    assert not upload.exists()
    media = op.get(f"/banner-creatives/{creative['sha256']}/media/file")
    assert media.status_code == 200 and media.headers["content-type"].startswith("video/webm")


def test_failed_normalization_marks_creative(login_as, db, app_env) -> None:
    op = login_as("operator")
    creative = _upload(op, _gif(), "anim.gif", "image/gif").json()["creative"]
    upload = Path(app_env.media_root) / f"uploads/creative_{creative['sha256']}.gif"
    upload.write_bytes(b"GIF89a" + b"\x00" * 10)  # файл испортился до нормализации
    from autoshorts.core.errors import PermanentError

    with pytest.raises(PermanentError):
        _run_normalize_task(db, creative["task_id"])
    failed = op.get(f"/banner-creatives/{creative['sha256']}").json()
    assert failed["status"] == "failed" and failed["error_code"] in {"corrupt_file", "normalize_failed"}
    campaign = _campaign(op)
    bad = op.post(
        f"/campaigns/{campaign['id']}/variants",
        json={"code": "A", "name": "A", "creative_sha256": creative["sha256"]},
    )
    assert bad.status_code == 422


# ---------------------------------------------------------------- варианты, проверка, кадр


def test_variant_from_creative_validate_and_conflict(login_as) -> None:
    op = login_as("operator")
    sha = _upload(op, _png()).json()["creative"]["sha256"]
    campaign = _campaign(op)
    created = op.post(
        f"/campaigns/{campaign['id']}/variants", json={"code": "A", "name": "Красный", "creative_sha256": sha}
    )
    assert created.status_code == 201, created.text
    v = created.json()
    kinds = {la["id"]: la for la in v["layers"]}
    assert kinds["micro_brand"]["text"] == "umka.chat"
    assert kinds["creative"]["creative_sha256"] == sha and kinds["fallback_plate"]["enabled"] is False
    assert v["status"] == "draft" and v["version"] == 1
    assert v["effective_code_url_template"] == "u.umka.chat/{code}" and v["code_url_template"] is None
    assert [c["sha256"] for c in v["creatives"]] == [sha]
    assert op.get(f"/banner-variants/{v['id']}").json()["id"] == v["id"]

    ok = op.post("/banner-variants/validate", json={"variant_id": v["id"], "duration_sec": 21.4})
    assert ok.status_code == 200, ok.text
    res = ok.json()
    assert res["ok"] is True and res["profile_ids"] == ["youtube_shorts", "common_organic"]
    assert res["fallback_used"] is False and res["missing_creatives"] == []
    assert {w["group"] for w in res["windows"]} == {"persistent", "with_creative"}
    creative_pl = next(p for p in res["placements"] if p["layer_id"] == "creative")
    assert creative_pl["scale"] == pytest.approx(0.95, abs=0.01)

    # креатив ниже y=1500 → предупреждение о нижней мёртвой зоне
    draft_layers = v["layers"]
    for la in draft_layers:
        if la["id"] == "creative":
            la["slot"]["y"] = 1520
    draft = {
        "code": "A",
        "name": "низ",
        "layers": draft_layers,
        "timing": v["timing"],
        "ad_marking": v["ad_marking"],
    }
    low = op.post("/banner-variants/validate", json={"draft": draft, "campaign_id": campaign["id"]}).json()
    codes = {w["code"] for w in low["warnings"]}
    assert "creative_in_bottom_dead_zone" in codes

    vk = op.post(
        "/banner-variants/validate", json={"draft": draft, "platform": "vk", "profile_ids": ["vk_clips"]}
    )
    assert vk.json()["profile_ids"] == ["vk_clips"]
    assert op.post("/banner-variants/validate", json={"duration_sec": 10}).status_code == 422
    assert (
        op.post(
            "/banner-variants/validate", json={"variant_id": v["id"], "profile_ids": ["nope_zone"]}
        ).status_code
        == 422
    )

    saved = op.patch(f"/banner-variants/{v['id']}", json={"layers": draft_layers, "expected_version": 1})
    assert saved.status_code == 200 and saved.json()["version"] == 2
    stale = op.patch(f"/banner-variants/{v['id']}", json={"name": "x", "expected_version": 1})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "version_conflict"
    assert stale.json()["error"]["details"]["current_version"] == 2

    missing = [{**draft_layers[1], "creative_sha256": "f" * 64}]
    bad = op.patch(f"/banner-variants/{v['id']}", json={"layers": missing})
    assert bad.status_code == 422
    assert bad.json()["error"]["details"]["errors"][0]["loc"] == ["body", "layers", 0, "creative_sha256"]
    font = [dict(draft_layers[0], style={**draft_layers[0]["style"], "font_file": "Nope.ttf"})]
    assert op.patch(f"/banner-variants/{v['id']}", json={"layers": font}).status_code == 422

    dup = op.post(f"/banner-variants/{v['id']}/duplicate", json={"code": "B"})
    assert (
        dup.status_code == 201 and dup.json()["name"] == "Красный (копия)" and dup.json()["status"] == "draft"
    )
    assert op.post(f"/banner-variants/{v['id']}/duplicate", json={"code": "B"}).status_code == 422
    listed = op.get(f"/campaigns/{campaign['id']}/variants").json()["items"]
    assert [x["code"] for x in listed] == ["A", "B"]


def test_activation_blocked_by_validation_errors(login_as) -> None:
    op = login_as("operator")
    campaign = _campaign(op)
    v = op.post(
        f"/campaigns/{campaign['id']}/variants",
        json={"code": "A", "name": "Маркировка", "ad_marking": {"required": True}},
    ).json()
    assert {la["id"]: la for la in v["layers"]}["erid"]["enabled"] is True
    denied = op.post(f"/banner-variants/{v['id']}/status", json={"status": "active"})
    assert denied.status_code == 422
    assert denied.json()["error"]["details"]["warnings"][0]["code"] == "erid_missing_when_required"
    # создание сразу в ротации проходит ту же проверку, что активация
    direct = op.post(
        f"/campaigns/{campaign['id']}/variants",
        json={"code": "Z", "name": "Сразу", "status": "active", "ad_marking": {"required": True}},
    )
    assert direct.status_code == 422
    assert direct.json()["error"]["details"]["warnings"][0]["code"] == "erid_missing_when_required"
    assert [x["code"] for x in op.get(f"/campaigns/{campaign['id']}/variants").json()["items"]] == ["A"]
    ok_direct = op.post(
        f"/campaigns/{campaign['id']}/variants", json={"code": "Y", "name": "Сразу", "status": "active"}
    )
    assert ok_direct.status_code == 201 and ok_direct.json()["status"] == "active", ok_direct.text
    fixed = op.patch(
        f"/banner-variants/{v['id']}", json={"ad_marking": {"required": True, "erid": "2SDnjeXyZ1"}}
    )
    assert fixed.status_code == 200
    active = op.post(f"/banner-variants/{v['id']}/status", json={"status": "active"})
    assert active.status_code == 200 and active.json()["status"] == "active"
    # у активного варианта нельзя сохранить сломанную маркировку
    broken = op.patch(f"/banner-variants/{v['id']}", json={"ad_marking": {"required": True}})
    assert broken.status_code == 422


def _pixel(png: bytes, x: int, y: int) -> tuple[int, int, int]:
    im = Image.open(io.BytesIO(png)).convert("RGB")
    return im.getpixel((x, y))


def test_preview_frame_shows_creative_in_impulse_only(login_as) -> None:
    op = login_as("operator")
    sha = _upload(op, _png()).json()["creative"]["sha256"]
    campaign = _campaign(op)
    v = op.post(
        f"/campaigns/{campaign['id']}/variants", json={"code": "A", "name": "A", "creative_sha256": sha}
    ).json()

    impulse = op.post("/banner-variants/preview", json={"variant_id": v["id"], "duration_sec": 21.4})
    assert impulse.status_code == 200, impulse.text
    assert impulse.headers["content-type"] == "image/png"
    t = float(impulse.headers["x-preview-t-sec"])
    assert 8.5 < t < 13.7  # середина импульса на 40–65 % ролика 21,4 с
    im = Image.open(io.BytesIO(impulse.content))
    assert im.size == (540, 960)
    # креатив 800×200 в слоте 825×190 снизу по центру: центр ≈ (477, 1215) на канве → (238, 607) на 540×960
    r, g, b = _pixel(impulse.content, 238, 607)
    assert r > 180 and g < 90 and b < 90, (r, g, b)

    hook = op.post(
        "/banner-variants/preview",
        json={"variant_id": v["id"], "duration_sec": 21.4, "moment": "at_time", "t_sec": 1.0},
    )
    assert hook.status_code == 200 and float(hook.headers["x-preview-t-sec"]) == pytest.approx(1.0)
    r, g, b = _pixel(hook.content, 238, 607)
    assert not (r > 180 and g < 90), (r, g, b)

    final = op.post(
        "/banner-variants/preview",
        json={
            "variant_id": v["id"],
            "duration_sec": 21.4,
            "moment": "final_repeat",
            "size": "full",
            "background": "checker",
        },
    )
    assert final.status_code == 200
    assert Image.open(io.BytesIO(final.content)).size == (1080, 1920)
    assert float(final.headers["x-preview-t-sec"]) > 18.9
    r, g, b = _pixel(final.content, 477, 1215)
    assert r > 180 and g < 90 and b < 90

    cached = op.post("/banner-variants/preview", json={"variant_id": v["id"], "duration_sec": 21.4})
    assert cached.content == impulse.content
    assert (
        op.post("/banner-variants/preview", json={"variant_id": v["id"], "background": "job:999"}).status_code
        == 404
    )


def test_preview_of_draft_with_animated_creative(login_as, db) -> None:
    op = login_as("operator")
    anim = _upload(op, _gif(), "anim.gif", "image/gif").json()["creative"]
    _run_normalize_task(db, anim["task_id"])
    campaign = _campaign(op)
    v = op.post(
        f"/campaigns/{campaign['id']}/variants",
        json={"code": "A", "name": "A", "creative_sha256": anim["sha256"]},
    ).json()
    draft = {k: v[k] for k in ("code", "name", "layers", "timing", "ad_marking")}
    resp = op.post(
        "/banner-variants/preview", json={"draft": draft, "duration_sec": 12, "background": "black"}
    )
    assert resp.status_code == 200, resp.text
    assert Image.open(io.BytesIO(resp.content)).size == (540, 960)
    val = op.post("/banner-variants/validate", json={"draft": draft, "duration_sec": 12}).json()
    assert val["fallback_used"] is False


# ---------------------------------------------------------------- шаблоны


def test_templates_yaml_roundtrip_validate_preview(login_as) -> None:
    op = login_as("operator")
    text = (ROOT / "config" / "banner.yaml").read_text(encoding="utf-8")
    imported = op.post(
        "/banner-templates/import-yaml",
        files={"file": ("banner.yaml", text.encode(), "text/yaml")},
        data={"name": "default"},
    )
    assert imported.status_code == 201, imported.text
    tpl = imported.json()
    layers = {la["id"]: la for la in tpl["layout"]["layers"]}
    assert layers["micro_brand"]["slot"]["y"] >= 192  # legacy y=56 вписан в safe zone
    assert layers["fallback_plate"]["style"]["max_font_px"] == 66
    assert (
        tpl["layout"]["timing"]["impulse_min_sec"] == 3.0
        and tpl["layout"]["timing"]["impulse_duration_sec"] == 4.0
    )

    exported = op.get(f"/banner-templates/{tpl['id']}/export-yaml")
    assert exported.status_code == 200
    data = yaml.safe_load(exported.text)
    assert data["layer_1_micro_brand"]["text"] == "umka.chat" and "variant" in data
    reimported = op.post(
        "/banner-templates/import-yaml",
        files={"file": ("export.yaml", exported.content, "text/yaml")},
        data={"name": "copy"},
    )
    assert reimported.status_code == 201 and reimported.json()["layout"] == tpl["layout"]
    assert (
        op.post(
            "/banner-templates/import-yaml",
            files={"file": ("x.yaml", text.encode(), "text/yaml")},
            data={"name": "default"},
        ).status_code
        == 422
    )

    bomb = ['a0: &a0 "lol"'] + [f"a{i}: &a{i} [{', '.join([f'*a{i - 1}'] * 10)}]" for i in range(1, 9)]
    bomb_text = "\n".join([*bomb, "variant:", "  name: *a8"]).encode()
    r = op.post(
        "/banner-templates/import-yaml",
        files={"file": ("bomb.yaml", bomb_text, "text/yaml")},
        data={"name": "bomb"},
    )
    assert r.status_code == 422 and r.json()["error"]["details"]["errors"][0]["type"] == "yaml_invalid"

    val = op.post(f"/banner-templates/{tpl['id']}/validate", json={"duration_sec": 20})
    assert val.status_code == 200 and val.json()["fallback_used"] is True
    png = op.post(f"/banner-templates/{tpl['id']}/preview", json={"duration_sec": 20})
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"

    patched = op.patch(f"/banner-templates/{tpl['id']}", json={"name": "основной", "expected_version": 1})
    assert patched.status_code == 200 and patched.json()["version"] == 2
    assert (
        op.patch(
            f"/banner-templates/{tpl['id']}", json={"status": "archived", "expected_version": 1}
        ).status_code
        == 409
    )
    dup = op.post(f"/banner-templates/{tpl['id']}/duplicate")
    assert dup.status_code == 201 and dup.json()["name"] == "основной (копия)"
    assert op.get("/banner-templates").json()["total"] == 3

    # кампания с шаблоном: новый вариант с креативом встаёт в зону оффера шаблона
    sha = _upload(op, _png()).json()["creative"]["sha256"]
    campaign = _campaign(op, banner_template_id=tpl["id"])
    v = op.post(
        f"/campaigns/{campaign['id']}/variants", json={"code": "A", "name": "A", "creative_sha256": sha}
    ).json()
    by_id = {la["id"]: la for la in v["layers"]}
    assert by_id["creative"]["creative_sha256"] == sha
    assert by_id["creative"]["slot"]["y"] == layers["fallback_plate"]["slot"]["y"]
    assert by_id["fallback_plate"]["enabled"] is False


# ---------------------------------------------------------------- справочники


def test_safe_zones_and_fonts(login_as) -> None:
    viewer = login_as("viewer")
    cat = viewer.get("/banner-safe-zones").json()
    assert cat["canvas"] == {"w": 1080, "h": 1920} and cat["default_profile"] == "common_organic"
    assert {p["platform"]: p["profile_id"] for p in cat["platform_profiles"]} == {
        "youtube": "youtube_shorts",
        "vk": "vk_clips",
    }
    common = next(p for p in cat["items"] if p["id"] == "common_organic")
    assert common["safe"] == {"x1": 65, "y1": 192, "x2": 895, "y2": 1380}
    assert {z["id"] for z in common["dead_zones"]} >= {"top_bar", "bottom_ui", "right_rail"}

    fonts = viewer.get("/banner-fonts").json()["items"]
    names = {f["name"] for f in fonts}
    assert {"Montserrat-Bold.ttf", "Montserrat-SemiBold.ttf"} <= names
    assert all(f["cyrillic_ok"] for f in fonts)
    ttf = viewer.get("/banner-fonts/Montserrat-Bold.ttf")
    assert ttf.status_code == 200 and ttf.headers["content-type"] == "font/ttf"
    assert viewer.get("/banner-fonts/Nope.ttf").status_code == 404
