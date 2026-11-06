"""Публикации (M3-A): разбор ссылки, регистрация (одна транзакция, мастер в masters/), площадка ≠ канал →
422, второй канал той же площадки → 409 same_platform_crosspost_forbidden, проверка принадлежности,
мягкое удаление, реестр, метрики и трафик."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from autoshorts.api.schemas.publications import PublicationCreated
from tests.api.test_packages_support import approved_job, clip_job, run_package_task, seed_m3

YT = "dQw4w9WgXcQ"


@pytest.fixture
def m3(db: Any) -> dict[str, Any]:
    return seed_m3(db)


@pytest.fixture
def media_root(app_env: Any) -> Path:
    return Path(app_env.media_root)


@pytest.fixture
def operator(login_as: Any) -> Any:
    return login_as("operator")


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    from autoshorts.services import packages

    monkeypatch.setattr(packages, "make_llm_client", lambda: None)


def _packaged(db: Any, media_root: Path, **fields: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    job = approved_job(db, media_root, **fields)
    run_package_task(int(job["id"]))
    pkg = db.execute(
        "SELECT p.*, tl.code FROM upload_packages p JOIN tracking_links tl ON tl.id = p.tracking_link_id "
        "WHERE p.job_id = %s AND p.status = 'ready'",
        (job["id"],),
    ).fetchone()
    return job, pkg


# ---------------------------------------------------------------- разбор


def test_parse_url(client: Any, operator: Any) -> None:
    ok = operator.post("/publications/parse-url", json={"url": f"https://youtube.com/shorts/{YT}?si=x"})
    assert ok.status_code == 200
    assert ok.json() == {
        "platform": "youtube",
        "platform_video_id": YT,
        "platform_kind": "short",
        "canonical_url": f"https://www.youtube.com/shorts/{YT}",
        "alias_code": f"yt_{YT}",
        "owner_ref": None,
    }
    vk = operator.post("/publications/parse-url", json={"url": "https://vk.com/clip-123_456"}).json()
    assert vk["platform"] == "vk" and vk["alias_code"] == "vk_-123_456" and vk["owner_ref"] == "-123"
    for bad in ("https://вк.com/clip-1_2", "https://evil.example/x", "https://youtube.com/@umka"):
        resp = operator.post("/publications/parse-url", json={"url": bad})
        assert resp.status_code == 422 and resp.json()["error"]["code"] == "unsupported_url", bad


# ---------------------------------------------------------------- регистрация


def test_register_youtube_publication(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any
) -> None:
    job, pkg = _packaged(db, media_root, banner_variant_id=m3["variant_a"], campaign_id=m3["campaign_id"])
    resp = operator.post(
        "/publications", json={"package_id": pkg["id"], "url": f"https://youtu.be/{YT}?si=abc"}
    )
    assert resp.status_code == 201, resp.text
    created = PublicationCreated.model_validate(resp.json())
    pub = created.publication
    assert pub.platform.value == "youtube" and pub.platform_video_id == YT
    assert pub.platform_kind.value == "short"  # вертикаль ≤ 3 мин
    assert pub.canonical_url == f"https://www.youtube.com/shorts/{YT}"
    assert pub.status.value == "live" and pub.verify_status.value == "pending"
    assert pub.package_id == pkg["id"] and pub.job_id == job["id"]
    assert pub.banner_variant is not None and pub.banner_variant.code == "A"
    assert pub.tracking.codes == [pkg["code"], f"yt_{YT}"]
    codes = {link.code: link for link in created.tracking_links}
    assert codes[pkg["code"]].level.value == "package" and codes[pkg["code"]].publication_id == pub.id
    alias = codes[f"yt_{YT}"]
    assert (
        alias.level.value == "publication" and alias.publication_id == pub.id and alias.product_id == "umka"
    )
    assert alias.telegram_https_url == f"https://telegram.me/umka_bot?start=yt_{YT}"
    # ролик и пакет опубликованы, пункт чек-листа отмечен
    assert (
        db.execute("SELECT status FROM jobs WHERE id = %s", (job["id"],)).fetchone()["status"] == "published"
    )
    prow = db.execute("SELECT status, checklist FROM upload_packages WHERE id = %s", (pkg["id"],)).fetchone()
    assert prow["status"] == "published"
    assert next(i for i in prow["checklist"] if i["code"] == "published")["done"] is True
    # мастер в masters/YYYY/MM/<uid>.mp4
    row = db.execute("SELECT * FROM publications WHERE id = %s", (pub.id,)).fetchone()
    now = datetime.now(UTC)
    assert row["master_file_path"] == f"masters/{now:%Y}/{now:%m}/{job['video_uid']}.mp4"
    master = media_root / row["master_file_path"]
    assert (
        master.is_file()
        and master.read_bytes() == (media_root / f"jobs/{job['video_uid']}/final_0.mp4").read_bytes()
    )
    assert row["title"] == pkg["title"] and row["attribution_tag"] == f"yt_{YT}"
    assert row["registered_by"] == operator.user["id"] and row["next_poll_at"] > row["published_at"]
    # проверка принадлежности — задачей
    task = db.execute("SELECT * FROM tasks WHERE kind = 'publication_verify'").fetchone()
    assert task["payload"] == {"publication_id": pub.id} and task["resource_class"] == "light"
    # пакет показывает публикацию
    pk = operator.get(f"/packages/{pkg['id']}").json()
    assert pk["status"] == "published" and pk["publication"]["id"] == pub.id
    # повтор той же ссылки → 409
    again = operator.post(
        "/publications", json={"package_id": pkg["id"], "url": f"https://www.youtube.com/watch?v={YT}"}
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "publication_exists"
    # другая ссылка для того же ролика → 409
    other = operator.post(
        "/publications", json={"package_id": pkg["id"], "url": "https://youtu.be/AAAAAAAAAAA"}
    )
    assert other.status_code == 409 and other.json()["error"]["code"] == "publication_exists"


def test_platform_mismatch_is_422(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any
) -> None:
    job, pkg = _packaged(db, media_root)
    resp = operator.post(
        "/publications", json={"package_id": pkg["id"], "url": "https://vk.com/clip-123_456"}
    )
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "platform_mismatch"
    assert err["details"]["url_platform"] == "vk" and err["details"]["channel_platform"] == "youtube"
    assert db.execute("SELECT count(*) AS n FROM publications").fetchone()["n"] == 0
    assert (
        db.execute("SELECT status FROM jobs WHERE id = %s", (job["id"],)).fetchone()["status"] == "approved"
    )
    garbage = operator.post("/publications", json={"package_id": pkg["id"], "url": "просто текст ссылки"})
    assert garbage.status_code == 422 and garbage.json()["error"]["code"] == "unsupported_url"


def test_same_platform_crosspost_is_forbidden(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any
) -> None:
    job, pkg = _packaged(db, media_root)
    assert (
        operator.post(
            "/publications", json={"package_id": pkg["id"], "url": f"https://youtu.be/{YT}"}
        ).status_code
        == 201
    )
    # клон того же ролика во второй YouTube-канал
    clone, clone_pkg = _packaged(db, media_root, channel_id="taro_second", parent_job_id=job["id"])
    resp = operator.post(
        "/publications", json={"package_id": clone_pkg["id"], "url": "https://youtu.be/BBBBBBBBBBB"}
    )
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "same_platform_crosspost_forbidden"
    assert err["details"]["channel_id"] == "taro_main" and err["details"]["job_id"] == job["id"]
    # адаптация под VK (другая площадка) — можно
    vk, vk_pkg = _packaged(db, media_root, channel_id="taro_vk", parent_job_id=job["id"])
    ok = operator.post(
        "/publications", json={"package_id": vk_pkg["id"], "url": "https://vk.com/clip-123_456"}
    )
    assert ok.status_code == 201, ok.text
    # VK проверяется сразу по владельцу из ссылки
    assert ok.json()["publication"]["verify_status"] == "ok"


def test_clip_from_same_candidate_is_crosspost(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any
) -> None:
    first = clip_job(db, media_root, rights_type="own")
    run_package_task(int(first["id"]))
    p1 = db.execute("SELECT id FROM upload_packages WHERE job_id = %s", (first["id"],)).fetchone()
    assert (
        operator.post(
            "/publications", json={"package_id": p1["id"], "url": f"https://youtu.be/{YT}"}
        ).status_code
        == 201
    )
    from tests.api.test_jobs_support import add_artifact, make_job

    second = make_job(
        db,
        kind="clip",
        status="approved",
        channel_id="taro_second",
        source_id=first["source_id"],
        clip_candidate_id=first["candidate_id"],
        clip_start_sec=10,
        clip_end_sec=40,
    )
    add_artifact(db, media_root, second, "final", content=b"x" * 100)
    db.execute(
        "UPDATE source_rights SET rights_type = 'own', attribution_text = NULL WHERE source_id = %s",
        (first["source_id"],),
    )
    run_package_task(int(second["id"]))
    p2 = db.execute("SELECT id FROM upload_packages WHERE job_id = %s", (second["id"],)).fetchone()
    resp = operator.post(
        "/publications", json={"package_id": p2["id"], "url": "https://youtu.be/CCCCCCCCCCC"}
    )
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "same_platform_crosspost_forbidden"


def test_vk_owner_mismatch_is_flagged(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any
) -> None:
    _job, pkg = _packaged(db, media_root, channel_id="taro_vk")
    resp = operator.post("/publications", json={"package_id": pkg["id"], "url": "https://vk.com/video-999_1"})
    assert resp.status_code == 201
    pub = resp.json()["publication"]
    assert pub["verify_status"] == "channel_mismatch" and "-999" in pub["verify_detail"]
    assert pub["platform_kind"] == "video" and pub["tracking"]["codes"][-1] == "vk_-999_1"


def test_idempotency_key_replays(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any
) -> None:
    _job, pkg = _packaged(db, media_root)
    body = {"package_id": pkg["id"], "url": f"https://youtu.be/{YT}"}
    first = operator.post("/publications", json=body, headers={"Idempotency-Key": "reg-1"})
    second = operator.post("/publications", json=body, headers={"Idempotency-Key": "reg-1"})
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert db.execute("SELECT count(*) AS n FROM publications").fetchone()["n"] == 1


def test_register_requires_approved_job_and_live_package(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any
) -> None:
    job, pkg = _packaged(db, media_root)
    db.execute("UPDATE jobs SET status = 'rework' WHERE id = %s", (job["id"],))
    resp = operator.post("/publications", json={"package_id": pkg["id"], "url": f"https://youtu.be/{YT}"})
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "job_state_conflict"
    db.execute("UPDATE upload_packages SET status = 'superseded' WHERE id = %s", (pkg["id"],))
    resp = operator.post("/publications", json={"package_id": pkg["id"], "url": f"https://youtu.be/{YT}"})
    assert resp.status_code == 409 and resp.json()["error"]["details"]["reason"] == "package_superseded"
    assert (
        operator.post("/publications", json={"package_id": 999, "url": f"https://youtu.be/{YT}"}).status_code
        == 404
    )


# ---------------------------------------------------------------- реестр, правка, удаление


def test_list_get_patch_delete(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any, login_as: Any
) -> None:
    job, pkg = _packaged(db, media_root)
    pub = operator.post(
        "/publications", json={"package_id": pkg["id"], "url": f"https://youtu.be/{YT}"}
    ).json()["publication"]
    listing = operator.get("/publications", params={"platform": "youtube", "status": "live"}).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == pub["id"]
    assert operator.get("/publications", params={"platform": "vk"}).json()["total"] == 0
    assert operator.get("/publications", params={"sort": "-latest_views"}).status_code == 200
    got = operator.get(f"/publications/{pub['id']}").json()
    assert got["traffic"]["unique_visitors"] == 0 and got["verdict"] is None
    patched = operator.patch(f"/publications/{pub['id']}", json={"has_claim": True, "status": "unlisted"})
    assert (
        patched.status_code == 200
        and patched.json()["has_claim"] is True
        and patched.json()["status"] == "unlisted"
    )
    # удаление — только admin
    assert operator.delete(f"/publications/{pub['id']}").status_code == 403
    admin = login_as("admin")
    assert admin.delete(f"/publications/{pub['id']}").status_code == 204
    assert operator.get(f"/publications/{pub['id']}").status_code == 404
    assert (
        db.execute("SELECT status FROM jobs WHERE id = %s", (job["id"],)).fetchone()["status"] == "approved"
    )
    prow = db.execute("SELECT status, checklist FROM upload_packages WHERE id = %s", (pkg["id"],)).fetchone()
    assert prow["status"] == "ready"
    alias = db.execute("SELECT * FROM tracking_links WHERE code = %s", (f"yt_{YT}",)).fetchone()
    assert alias["is_active"] is False
    # ту же ссылку можно зарегистрировать заново: алиас включается снова
    again = operator.post("/publications", json={"package_id": pkg["id"], "url": f"https://youtu.be/{YT}"})
    assert again.status_code == 201, again.text
    alias = db.execute("SELECT * FROM tracking_links WHERE code = %s", (f"yt_{YT}",)).fetchone()
    assert alias["is_active"] is True and alias["publication_id"] == again.json()["publication"]["id"]


def test_metrics_retention_traffic_and_tasks(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any
) -> None:
    _job, pkg = _packaged(db, media_root)
    pub = operator.post(
        "/publications", json={"package_id": pkg["id"], "url": f"https://youtu.be/{YT}"}
    ).json()["publication"]
    pid = pub["id"]
    t0 = datetime.now(UTC) - timedelta(hours=30)
    for h, views in ((1, 100), (25, 900)):
        db.execute(
            "INSERT INTO metrics_snapshots (publication_id, captured_at, views, likes, comments, source) "
            "VALUES (%s, %s, %s, 1, 0, 'yt_data')",
            (pid, t0 + timedelta(hours=h), views),
        )
    db.execute("UPDATE publications SET latest_views = 900, views_24h = 800 WHERE id = %s", (pid,))
    m = operator.get(f"/publications/{pid}/metrics").json()
    assert [p["views"] for p in m["points"]] == [100, 900] and m["granularity"] == "raw"
    assert operator.get(f"/publications/{pid}/metrics", params={"granularity": "day"}).status_code == 200
    r = operator.get(f"/publications/{pid}/retention").json()
    assert r["points"] == [] and r["captured_at"] is None
    db.execute(
        "INSERT INTO retention_curves (publication_id, points, retention_3s, failure_class) "
        "VALUES (%s, '[[0.0, 1.0, 0.5], [0.5, 0.62, null]]', 0.8123, 'hook_drop')",
        (pid,),
    )
    r = operator.get(f"/publications/{pid}/retention").json()
    assert r["failure_class"] == "hook_drop" and r["retention_3s"] == pytest.approx(0.8123)
    assert r["points"][1] == {"ratio": 0.5, "audience_watch_ratio": 0.62, "relative": None}
    assert r["duration_sec"] == pytest.approx(21.4)
    link = db.execute("SELECT id FROM tracking_links WHERE code = %s", (pkg["code"],)).fetchone()["id"]
    db.execute(
        "INSERT INTO clicks (code, tracking_link_id, route, publication_id, visitor_key, is_bot) VALUES "
        "(%s, %s, 'landing', %s, 'v1', false), (%s, %s, 'landing', %s, 'v2', true)",
        (pkg["code"], link, pid, pkg["code"], link, pid),
    )
    db.execute(
        "INSERT INTO conversions (product_id, external_event_id, event_type, occurred_at, tracking_link_id, "
        "publication_id, is_test) VALUES ('umka', 'e1', 'bot_start', now(), %s, %s, false), "
        "('umka', 'e2', 'bot_start', now(), %s, %s, true)",
        (link, pid, link, pid),
    )
    tr = operator.get(f"/publications/{pid}/traffic", params={"granularity": "hour"}).json()
    assert sum(p["clicks"] for p in tr["points"]) == 1 and sum(p["bot_starts"] for p in tr["points"]) == 1
    card = operator.get(f"/publications/{pid}").json()
    assert card["traffic"]["landing_views"] == 1 and card["traffic"]["bot_starts"] == 1
    assert card["verdict"] == "h24" and card["latest"]["views"] == 900
    assert card["unit_economics"]["ctr_per_1k_views"] is not None
    rv = operator.post(f"/publications/{pid}/reverify")
    assert rv.status_code == 202 and rv.json()["task_id"] > 0
    rm = operator.post(f"/publications/{pid}/refresh-metrics")
    assert rm.status_code == 202
    task = db.execute("SELECT * FROM tasks WHERE id = %s", (rm.json()["task_id"],)).fetchone()
    assert task["kind"] == "metrics_poll" and task["payload"]["publication_id"] == pid


# ---------------------------------------------------------------- проверка принадлежности YouTube


def _oembed(status: int, author_url: str = "https://www.youtube.com/@taro_main") -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oembed"
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, json={"author_name": "Таро", "author_url": author_url})

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("status", "author_url", "expected"),
    [
        (200, "https://www.youtube.com/@taro_main", "ok"),
        (200, "https://www.youtube.com/@Taro_Main/", "ok"),
        (200, "https://www.youtube.com/@someone_else", "channel_mismatch"),
        (404, "", "not_found"),
        (403, "", "unverifiable"),
    ],
)
def test_verify_youtube_oembed(status: int, author_url: str, expected: str) -> None:
    from autoshorts.services.publications import verify_youtube

    channel = {"id": "taro_main", "url": "https://www.youtube.com/@taro_main", "platform_channel_ref": None}
    got, detail = verify_youtube({"platform_video_id": YT}, channel, client=_oembed(status, author_url))
    assert got == expected, detail


def test_verify_youtube_network_error_is_retryable() -> None:
    from autoshorts.core.errors import RetryableError
    from autoshorts.services.publications import verify_youtube

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(RetryableError):
        verify_youtube(
            {"platform_video_id": YT}, {"url": None}, client=httpx.Client(transport=httpx.MockTransport(boom))
        )


def test_verify_task_updates_publication(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoshorts.services import publications

    _job, pkg = _packaged(db, media_root)
    pid = operator.post(
        "/publications", json={"package_id": pkg["id"], "url": f"https://youtu.be/{YT}"}
    ).json()["publication"]["id"]
    real = publications.verify_youtube
    monkeypatch.setattr(publications, "verify_youtube", lambda pub, ch: real(pub, ch, client=_oembed(200)))
    assert publications.verify_task(pid) == {"publication_id": pid, "verify_status": "ok"}
    row = db.execute("SELECT verify_status, verified_at FROM publications WHERE id = %s", (pid,)).fetchone()
    assert row["verify_status"] == "ok" and row["verified_at"] is not None


def test_package_video_falls_back_to_master(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any
) -> None:
    job, pkg = _packaged(db, media_root)
    assert (
        operator.post(
            "/publications", json={"package_id": pkg["id"], "url": f"https://youtu.be/{YT}"}
        ).status_code
        == 201
    )
    final = media_root / f"jobs/{job['video_uid']}/final_0.mp4"
    data = final.read_bytes()
    final.unlink()  # уборка рабочих файлов
    resp = operator.get(f"/packages/{pkg['id']}/video")
    assert resp.status_code == 200 and resp.content == data


# ---------------------------------------------------------------- снять с площадки


def _remote_setup(monkeypatch: pytest.MonkeyPatch, scopes: list[str], deleted: list[str]) -> None:
    from autoshorts.collectors import oauth_google
    from autoshorts.publishers import youtube as yt
    from autoshorts.services import autopublish

    oauth = {"refresh_token_secret": "s", "scopes": scopes}
    monkeypatch.setattr(
        autopublish, "oauth_row", lambda conn, channel_id: {"channel_id": channel_id, **oauth}
    )
    monkeypatch.setattr(autopublish, "_client_creds", lambda conn, cluster: object())
    monkeypatch.setattr(autopublish, "_secret", lambda name, conn, fresh=False: "refresh-token")
    monkeypatch.setattr(oauth_google, "refresh", lambda client, creds, rt: SimpleNamespace(access_token="at"))
    monkeypatch.setattr(yt, "delete_video", lambda client, token, vid: deleted.append(f"{token}:{vid}"))


def test_remove_remote_requires_manage_scope_then_deletes(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoshorts.collectors import oauth_google

    _job, pkg = _packaged(db, media_root)
    created = operator.post(
        "/publications", json={"package_id": pkg["id"], "url": f"https://youtu.be/{YT}"}
    ).json()
    pub = created.get("publication") or created
    deleted: list[str] = []
    _remote_setup(monkeypatch, [oauth_google.SCOPE_UPLOAD, oauth_google.SCOPE_READONLY], deleted)
    resp = operator.post(f"/publications/{pub['id']}/remove-remote")
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "oauth_scope_missing" and deleted == []

    full = [oauth_google.SCOPE_UPLOAD, oauth_google.SCOPE_READONLY, oauth_google.SCOPE_MANAGE]
    _remote_setup(monkeypatch, full, deleted)
    resp = operator.post(f"/publications/{pub['id']}/remove-remote")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "deleted_by_us" and deleted == [f"at:{YT}"]
    row = db.execute("SELECT status, status_reason FROM publications WHERE id = %s", (pub["id"],)).fetchone()
    assert row["status"] == "deleted_by_us" and "YouTube" in row["status_reason"]
    entry = db.execute(
        "SELECT action, summary FROM audit_log WHERE entity_type = 'publication' "
        "AND action = 'publication.remove_remote' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert entry is not None and YT in entry["summary"]
    # повтор — идемпотентен, на площадку не ходим
    assert operator.post(f"/publications/{pub['id']}/remove-remote").status_code == 200 and len(deleted) == 1
