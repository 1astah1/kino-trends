"""API роликов (M2-C): создание, права, переходы, 409, клон, адаптация, медиа, история (§5.2, §6.2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from psycopg.types.json import Jsonb

from tests.api.test_jobs_support import (
    LAYERS,
    add_artifact,
    err,
    job_row,
    job_tasks,
    make_job,
    seed_catalog,
)

API = "/api/v1"


@pytest.fixture
def catalog(db: Any) -> dict[str, Any]:
    return seed_catalog(db)


def _gen(**kw: Any) -> dict[str, Any]:
    body = {"kind": "generate", "channel_id": "taro_main", "topic": "отношения", "start": False}
    body.update(kw)
    return body


# ---------------------------------------------------------------- доступ


def test_requires_login_and_operator_for_writes(client: Any, login_as: Any, catalog: dict) -> None:
    assert client.get(f"{API}/jobs").status_code == 401
    viewer = login_as("viewer")
    assert viewer.get("/jobs").status_code == 200
    resp = viewer.post("/jobs", json=_gen())
    assert resp.status_code == 403 and err(resp)["code"] == "forbidden"
    op = login_as("operator")
    resp = op.post("/jobs", json=_gen(), csrf=False)
    assert resp.status_code == 403 and err(resp)["code"] == "csrf_failed"


# ---------------------------------------------------------------- создание


def test_create_draft_and_get(login_as: Any, catalog: dict, db: Any) -> None:
    op = login_as("operator")
    resp = op.post("/jobs", json=_gen(banner_variant_id=catalog["variant_a"]))
    assert resp.status_code == 201, resp.text
    job = resp.json()
    assert job["status"] == "draft" and job["stage"] is None and job["kind"] == "generate"
    assert job["channel"] == {"id": "taro_main", "title": "Таро · основной", "platform": "youtube"}
    assert job["banner_variant"] == {"id": catalog["variant_a"], "code": "A", "name": "Домен + расклад"}
    assert job["campaign_id"] == catalog["campaign_id"] and job["product_id"] == "umka"
    assert job["niche_id"] == "taro" and job["niche_version"] == 3
    assert len(job["video_uid"]) == 12 and job["media"] == {
        "preview_url": None,
        "final_url": None,
        "poster_url": None,
    }
    row = job_row(db, job["id"])
    assert row["config_snapshot"]["banner"]["layers"][0]["creative_sha256"] == "a" * 64
    assert row["config_snapshot"]["niche"]["config"]["duration_target_sec"] == [15, 25]
    assert job_tasks(db, job["id"]) == []
    got = op.get(f"/jobs/{job['id']}").json()
    assert got["id"] == job["id"] and got["words"] is None
    audit = db.execute(
        "SELECT action FROM audit_log WHERE entity_type='job' AND entity_id=%s", (str(job["id"]),)
    )
    assert [a["action"] for a in audit.fetchall()] == ["job.create"]


def test_create_started_enqueues_first_stage(login_as: Any, catalog: dict, db: Any) -> None:
    op = login_as("operator")
    job = op.post("/jobs", json=_gen(start=True)).json()
    assert job["status"] == "queued" and job["stage"] == "script"
    (task,) = job_tasks(db, job["id"])
    assert task["kind"] == "job_stage" and task["stage"] == "script" and task["resource_class"] == "light"
    assert task["dedup_key"] == f"job:{job['id']}:script:0" and task["status"] == "queued"


def test_create_validation_and_lookups(login_as: Any, catalog: dict, db: Any) -> None:
    op = login_as("operator")
    r = op.post("/jobs", json=_gen(channel_id="nope"))
    assert r.status_code == 404 and err(r)["code"] == "channel_not_found"
    r = op.post("/jobs", json=_gen(banner_variant_id=999999))
    assert r.status_code == 404 and err(r)["code"] == "banner_variant_not_found"
    r = op.post(
        "/jobs", json=_gen(banner_variant_id=catalog["variant_a"], campaign_id=catalog["other_campaign_id"])
    )
    assert r.status_code == 422 and err(r)["details"]["errors"][0]["type"] == "variant_campaign_mismatch"
    r = op.post("/jobs", json=_gen(banner_variant_id=catalog["variant_archived"]))
    assert r.status_code == 422
    # черновик с ошибкой баннера (маркировка обязательна, erid нет) вручную не назначается
    draft = db.execute(
        """INSERT INTO banner_variants (campaign_id, code, name, status, weight, layers, ad_marking)
           VALUES (%s, 'D', 'Черновик', 'draft', 50, %s, %s) RETURNING id""",
        (catalog["campaign_id"], Jsonb(LAYERS), Jsonb({"required": True, "erid": None})),
    ).fetchone()["id"]
    r = op.post("/jobs", json=_gen(banner_variant_id=draft))
    assert r.status_code == 422 and err(r)["details"]["errors"][0]["type"] == "variant_invalid", r.text
    db.execute(
        "UPDATE banner_variants SET ad_marking = %s WHERE id = %s",
        (Jsonb({"required": False, "erid": None}), draft),
    )
    r = op.post("/jobs", json=_gen(banner_variant_id=draft))
    assert r.status_code == 201 and r.json()["banner_variant"]["id"] == draft, r.text
    r = op.post("/jobs", json=_gen(campaign_id=catalog["campaign_id"]))
    assert r.status_code == 201 and r.json()["campaign_id"] == catalog["campaign_id"]
    assert r.json()["banner_variant"] is None
    r = op.post("/jobs", json=_gen(trend_brief_id=424242))
    assert r.status_code == 404 and err(r)["code"] == "trend_brief_not_found"
    r = op.post("/jobs", json={"kind": "generate", "channel_id": "Таро"})
    assert r.status_code == 422 and err(r)["code"] == "validation_error"


def test_budget_paused_gives_423(login_as: Any, catalog: dict, db: Any) -> None:
    db.execute("UPDATE budget_state SET paused = true, pause_reason = 'manual'")
    op = login_as("operator")
    r = op.post("/jobs", json=_gen(start=True))
    assert r.status_code == 423 and err(r)["code"] == "budget_paused"
    assert op.post("/jobs", json=_gen(start=False)).status_code == 201  # черновик можно


def test_idempotency_key_returns_same_job(login_as: Any, catalog: dict, db: Any) -> None:
    op = login_as("operator")
    headers = {"Idempotency-Key": "k-123"}
    a = op.post("/jobs", json=_gen(), headers=headers)
    b = op.post("/jobs", json=_gen(), headers=headers)
    assert a.status_code == b.status_code == 201 and a.json()["id"] == b.json()["id"]
    assert db.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 1
    c = op.post("/jobs", json=_gen(), headers={"Idempotency-Key": "k-124"})
    assert c.json()["id"] != a.json()["id"]


def test_batch(login_as: Any, catalog: dict, db: Any) -> None:
    op = login_as("operator")
    r = op.post(
        "/jobs/batch",
        json={"channel_id": "taro_main", "topics": ["любовь", "работа", "деньги"], "start": True},
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [j["topic"] for j in items] == ["любовь", "работа", "деньги"]
    assert all(j["status"] == "queued" for j in items)
    assert db.execute("SELECT count(*) AS n FROM tasks WHERE kind='job_stage'").fetchone()["n"] == 3
    r = op.post("/jobs/batch", json={"channel_id": "taro_main", "topics": [f"t{i}" for i in range(21)]})
    assert r.status_code == 422


# ---------------------------------------------------------------- список и лента


def test_list_filters_sort_and_stage_counts(login_as: Any, catalog: dict, db: Any) -> None:
    a = make_job(db, status="pending_review", topic="любовь и карты", priority=10)
    b = make_job(db, status="processing", stage="render", channel_id="taro_vk", priority=50)
    make_job(db, status="rejected", stage="qc", niche_id="astro", channel_id="astro_yt")
    viewer = login_as("viewer")
    page = viewer.get("/jobs").json()
    assert page["total"] == 3 and page["limit"] == 50 and page["offset"] == 0
    page = viewer.get("/jobs", params={"status": ["pending_review", "processing"], "sort": "priority"}).json()
    assert [j["id"] for j in page["items"]] == [a["id"], b["id"]]
    assert viewer.get("/jobs", params={"channel_id": "taro_vk"}).json()["items"][0]["id"] == b["id"]
    assert viewer.get("/jobs", params={"q": "любовь"}).json()["total"] == 1
    assert viewer.get("/jobs", params={"q": a["video_uid"][:6]}).json()["items"][0]["id"] == a["id"]
    assert viewer.get("/jobs", params={"stage": "render"}).json()["total"] == 1
    r = viewer.get("/jobs", params={"sort": "-nope"})
    assert r.status_code == 422
    counts = {(c["status"], c["stage"]): c["count"] for c in viewer.get("/jobs/stage-counts").json()["items"]}
    assert counts[("pending_review", "qc")] == 1 and counts[("processing", "render")] == 1


def test_list_progress_from_active_task(login_as: Any, catalog: dict, db: Any) -> None:
    job = make_job(db, status="processing", stage="render")
    db.execute(
        "INSERT INTO tasks (kind, resource_class, status, job_id, stage, progress) "
        "VALUES ('job_stage','heavy','running',%s,'render',42.5)",
        (job["id"],),
    )
    item = login_as("viewer").get("/jobs").json()["items"][0]
    assert item["progress"] == 42.5 and item["stage"] == "render"


# ---------------------------------------------------------------- переходы


def test_patch_only_before_start(login_as: Any, catalog: dict, db: Any) -> None:
    op = login_as("operator")
    job = op.post("/jobs", json=_gen()).json()
    r = op.patch(
        f"/jobs/{job['id']}",
        json={
            "topic": "новая тема",
            "priority": 5,
            "banner_variant_id": catalog["variant_b"],
            "script_override": {"hook_text": "Смотри"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["topic"] == "новая тема" and body["priority"] == 5 and body["banner_variant"]["code"] == "B"
    assert job_row(db, job["id"])["stage_payload"]["script_override"] == {"hook_text": "Смотри"}
    r = op.patch(f"/jobs/{job['id']}", json={"author_layer": {"mode": "operator_text"}})
    assert r.status_code == 422  # авторский слой — только у нарезки
    running = make_job(db, status="processing", stage="voice")
    r = op.patch(f"/jobs/{running['id']}", json={"topic": "x"})
    assert r.status_code == 409 and err(r)["code"] == "job_state_conflict"
    assert err(r)["details"]["status"] == "processing"


def test_start_cancel_and_conflicts(login_as: Any, catalog: dict, db: Any) -> None:
    op = login_as("operator")
    job = op.post("/jobs", json=_gen()).json()
    r = op.post(f"/jobs/{job['id']}/start")
    assert r.status_code == 200 and r.json()["status"] == "queued" and r.json()["stage"] == "script"
    r = op.post(f"/jobs/{job['id']}/start")
    assert r.status_code == 409 and err(r)["code"] == "job_state_conflict"
    r = op.post(f"/jobs/{job['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert [t["status"] for t in job_tasks(db, job["id"])] == ["cancelled"]
    r = op.post(f"/jobs/{job['id']}/cancel")
    assert r.status_code == 409
    assert op.post("/jobs/999999/cancel").status_code == 404


def test_cancel_running_task_requests_cancel(login_as: Any, catalog: dict, db: Any) -> None:
    job = make_job(db, status="processing", stage="render")
    t = db.execute(
        "INSERT INTO tasks (kind, resource_class, status, job_id, stage, locked_by) "
        "VALUES ('job_stage','heavy','running',%s,'render','w1') RETURNING id",
        (job["id"],),
    ).fetchone()
    r = login_as("operator").post(f"/jobs/{job['id']}/cancel")
    assert r.status_code == 200
    task = db.execute("SELECT status, cancel_requested FROM tasks WHERE id = %s", (t["id"],)).fetchone()
    assert task["status"] == "running" and task["cancel_requested"] is True


def test_retry_failed_from_stage(login_as: Any, catalog: dict, db: Any, app_env: Any) -> None:
    job = make_job(db, status="failed", stage="visuals", error="fal 500", error_class="retryable_error")
    img = add_artifact(db, app_env.media_root, job, "scene_image", stage="visuals")
    voice = add_artifact(db, app_env.media_root, job, "voice", stage="voice")
    op = login_as("operator")
    r = op.post(f"/jobs/{job['id']}/retry", json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "processing" and r.json()["stage"] == "visuals" and r.json()["error"] is None
    (task,) = job_tasks(db, job["id"])
    assert task["stage"] == "visuals" and task["status"] == "queued"
    live = {a["id"] for a in db.execute("SELECT id FROM artifacts WHERE deleted_at IS NULL").fetchall()}
    assert voice["id"] in live and img["id"] not in live  # стадии ≥ visuals помечены удалёнными
    r = op.post(f"/jobs/{job['id']}/retry", json={})
    assert r.status_code == 409
    other = make_job(db, status="failed", stage="render")
    r = op.post(f"/jobs/{other['id']}/retry", json={"from_stage": "clip_analyze"})
    assert r.status_code == 422


def test_clone(login_as: Any, catalog: dict, db: Any) -> None:
    op = login_as("operator")
    parent = make_job(
        db, status="rejected", banner_variant_id=catalog["variant_a"], campaign_id=catalog["campaign_id"]
    )
    r = op.post(f"/jobs/{parent['id']}/clone", json={"overrides": {"topic": "другая тема"}})
    assert r.status_code == 201, r.text
    child = r.json()
    assert (
        child["parent_job_id"] == parent["id"]
        and child["status"] == "draft"
        and child["topic"] == "другая тема"
    )
    assert child["banner_variant"]["id"] == catalog["variant_a"]
    r = op.post(
        f"/jobs/{parent['id']}/clone",
        json={
            "overrides": {"banner_variant_id": catalog["variant_b"], "start": True, "channel_id": "taro_vk"}
        },
    )
    assert r.status_code == 201
    assert r.json()["status"] == "queued" and r.json()["banner_variant"]["code"] == "B"
    assert r.json()["channel"]["platform"] == "vk"


def test_adapt_to_vk(login_as: Any, catalog: dict, db: Any, app_env: Any) -> None:
    op = login_as("operator")
    parent = make_job(
        db, status="approved", stage_payload={"script": {"voiceover_text": "Текст"}, "banner_plan": {"v": 1}}
    )
    add_artifact(db, app_env.media_root, parent, "voice", stage="voice")
    add_artifact(db, app_env.media_root, parent, "scene_image", stage="visuals")
    add_artifact(db, app_env.media_root, parent, "final", stage="render")
    r = op.post(f"/jobs/{parent['id']}/adapt", json={"channel_id": "taro_main"})
    assert r.status_code == 422 and err(r)["code"] == "platform_mismatch"
    r = op.post(f"/jobs/{parent['id']}/adapt", json={"channel_id": "taro_vk"})
    assert r.status_code == 201, r.text
    child = r.json()
    assert (
        child["parent_job_id"] == parent["id"]
        and child["status"] == "processing"
        and child["stage"] == "render"
    )
    kinds = sorted(
        a["kind"] for a in db.execute("SELECT kind FROM artifacts WHERE job_id = %s", (child["id"],))
    )
    assert kinds == ["scene_image", "voice"]
    payload = job_row(db, child["id"])["stage_payload"]
    assert payload["script"]["voiceover_text"] == "Текст" and "banner_plan" not in payload
    (task,) = job_tasks(db, child["id"])
    assert task["stage"] == "render" and task["resource_class"] == "heavy"
    pending = make_job(db, status="pending_review")
    assert op.post(f"/jobs/{pending['id']}/adapt", json={"channel_id": "taro_vk"}).status_code == 409


# ---------------------------------------------------------------- чтение: деталь, медиа, сцены, история


def test_detail_with_words_qc_reviews_and_media(login_as: Any, catalog: dict, db: Any, app_env: Any) -> None:
    job = make_job(
        db,
        status="qc_failed",
        qc_failed_checks=["loudness", "made_up_check"],
        qc_warnings=[{"code": "library_similarity", "detail": "похож", "job_id": 7, "similarity": 0.93}],
        stage_payload={
            "script": {"hook_text": "Три карты", "voiceover_text": "Три карты скажут всё"},
            "words": [{"word": "Три", "start_sec": 0.0, "end_sec": 0.31}],
            "qc": {
                "checks": [
                    {"code": "loudness", "ok": False, "measured": "-19.2 LUFS", "threshold": "-14±1.5"}
                ]
            },
        },
    )
    add_artifact(db, app_env.media_root, job, "preview", content=b"\x00" * 1000)
    add_artifact(db, app_env.media_root, job, "poster", content=b"jpg")
    viewer = login_as("viewer")
    body = viewer.get(f"/jobs/{job['id']}", params={"include": "words,script"}).json()
    assert body["words"] == [{"word": "Три", "start_sec": 0.0, "end_sec": 0.31}]
    assert body["script"]["hook_text"] == "Три карты" and body["script"]["cta_text"] is None
    assert body["qc"]["passed"] is False and body["qc"]["failed_checks"] == ["loudness"]
    assert body["qc"]["checks"][0]["measured"] == "-19.2 LUFS"
    assert body["qc"]["warnings"][0]["similarity"] == 0.93
    assert body["media"]["preview_url"].startswith(f"/api/v1/jobs/{job['id']}/media/preview?v=")
    assert body["media"]["final_url"] is None
    r = viewer.get(f"/jobs/{job['id']}/media/preview")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4" and len(r.content) == 1000
    r = viewer.get(f"/jobs/{job['id']}/media/preview", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and len(r.content) == 10
    r = viewer.get(f"/jobs/{job['id']}/media/poster", params={"download": 1})
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("attachment;")
    assert job["video_uid"] in r.headers["content-disposition"]
    r = viewer.get(f"/jobs/{job['id']}/media/final")
    assert r.status_code == 404 and err(r)["code"] == "media_not_found"
    assert viewer.get(f"/jobs/{job['id']}/media/bogus").status_code == 422


def test_words_from_artifact_file(login_as: Any, catalog: dict, db: Any, app_env: Any) -> None:
    from tests.api.test_jobs_support import words_json

    job = make_job(db)
    add_artifact(
        db, app_env.media_root, job, "words_json", content=words_json([("Раз", 0.0, 0.2), ("два", 0.2, 0.5)])
    )
    body = login_as("viewer").get(f"/jobs/{job['id']}", params={"include": "words"}).json()
    assert [w["word"] for w in body["words"]] == ["Раз", "два"]


def test_scenes_and_regenerate(login_as: Any, catalog: dict, db: Any, app_env: Any) -> None:
    job = make_job(
        db,
        status="pending_review",
        stage_payload={
            "scenes": [
                {"prompt_en": "moon", "seed": 11, "duration_sec": 3.2},
                {"prompt_en": "sun", "seed": 12},
            ]
        },
    )
    img0 = add_artifact(db, app_env.media_root, job, "scene_image", idx=0, stage="visuals", content=b"png0")
    img1 = add_artifact(db, app_env.media_root, job, "scene_image", idx=1, stage="visuals", content=b"png1")
    fin = add_artifact(db, app_env.media_root, job, "final", stage="render")
    op = login_as("operator")
    scenes = op.get(f"/jobs/{job['id']}/scenes").json()["items"]
    assert scenes[0] == {
        "idx": 0,
        "prompt": "moon",
        "image_url": f"/api/v1/jobs/{job['id']}/scenes/0/image",
        "seed": 11,
        "duration_sec": 3.2,
    }
    r = op.get(f"/jobs/{job['id']}/scenes/1/image")
    assert r.status_code == 200 and r.content == b"png1" and r.headers["content-type"] == "image/png"
    assert op.get(f"/jobs/{job['id']}/scenes/5/image").status_code == 404
    assert op.post(f"/jobs/{job['id']}/scenes/9/regenerate", json={}).status_code == 404
    r = op.post(f"/jobs/{job['id']}/scenes/1/regenerate", json={"prompt": "bright sun"})
    assert r.status_code == 202, r.text
    task = db.execute("SELECT * FROM tasks WHERE id = %s", (r.json()["task_id"],)).fetchone()
    assert task["stage"] == "visuals" and task["payload"] == {"scene_idx": 1, "prompt": "bright sun"}
    row = job_row(db, job["id"])
    assert row["status"] == "processing" and row["stage"] == "visuals"
    live = {a["id"] for a in db.execute("SELECT id FROM artifacts WHERE deleted_at IS NULL").fetchall()}
    assert img0["id"] in live and img1["id"] not in live and fin["id"] not in live
    assert op.post(f"/jobs/{job['id']}/scenes/0/regenerate", json={}).status_code == 409


def test_tasks_history_and_similar(login_as: Any, catalog: dict, db: Any) -> None:
    fp = {"method": "dhash8x8_keyframes_v1", "duration_sec": 20, "dhash": ["00ff00ff00ff00ff"] * 8}
    near = {"method": "dhash8x8_keyframes_v1", "duration_sec": 20, "dhash": ["00ff00ff00ff00fe"] * 8}
    far = {"method": "dhash8x8_keyframes_v1", "duration_sec": 20, "dhash": ["ff00ff00ff00ff00"] * 8}
    op = login_as("operator")
    job = op.post("/jobs", json=_gen(start=True)).json()
    db.execute("UPDATE jobs SET fingerprint = %s WHERE id = %s", (Jsonb(fp), job["id"]))
    twin = make_job(db, fingerprint=near)
    make_job(db, fingerprint=far)
    op.post(f"/jobs/{job['id']}/cancel")
    tasks = op.get(f"/jobs/{job['id']}/tasks").json()["items"]
    assert tasks[0]["stage"] == "script" and tasks[0]["status"] == "cancelled"
    hist = op.get(f"/jobs/{job['id']}/history").json()["items"]
    status_moves = [(h["from"], h["to"]) for h in hist if h["kind"] == "status"]
    assert status_moves == [(None, "queued"), ("queued", "cancelled")]
    assert any(h["kind"] == "task" and h["to"] == "cancelled" for h in hist)
    assert hist[0]["by"] == "Operator1"
    similar = op.get(f"/jobs/{job['id']}/similar").json()["items"]
    assert [s["job_id"] for s in similar] == [twin["id"]] and similar[0]["similarity"] > 0.95


def test_job_not_found(login_as: Any, catalog: dict) -> None:
    viewer = login_as("viewer")
    for path in ("/jobs/424242", "/jobs/424242/tasks", "/jobs/424242/history", "/jobs/424242/scenes"):
        r = viewer.get(path)
        assert r.status_code == 404 and err(r)["code"] == "job_not_found", path


# ---------------------------------------------------------------- нарезка


def _source(db: Any, *, status: str = "ready", rights: bool = True, origin: str = "upload") -> int:
    user = db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
    upload = None
    if origin == "upload":
        upload = db.execute(
            """INSERT INTO uploads (id, user_id, purpose, filename, length_bytes, path, status, expires_at)
               VALUES (md5(random()::text), %s, 'source', 'efir.mp4', 10, 'uploads/x.part', 'consumed',
                       now() + interval '1 day')
               RETURNING id""",
            (user,),
        ).fetchone()["id"]
    sid = db.execute(
        """INSERT INTO sources (title, origin, origin_url, upload_id, status, duration_sec)
           VALUES ('Эфир 12.09', %s, %s, %s, %s, 3600) RETURNING id""",
        (origin, "https://example.org/v" if origin == "url" else None, upload, status),
    ).fetchone()["id"]
    if rights:
        db.execute(
            "INSERT INTO source_rights (source_id, rights_type, owner_name, proof_note) "
            "VALUES (%s, 'own', 'Мы', 'свой эфир')",
            (sid,),
        )
    return sid


def test_clip_job_from_source_range_and_candidate(login_as: Any, catalog: dict, db: Any) -> None:
    op = login_as("operator")
    sid = _source(db)
    body = {
        "kind": "clip",
        "channel_id": "taro_main",
        "source": {"source_id": sid, "start_sec": 1204.3, "end_sec": 1226.9},
        "author_layer": {"mode": "operator_text", "hook_text": "Слушай"},
        "reframe": {"mode": "center"},
        "start": True,
    }
    r = op.post("/jobs", json=body)
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["kind"] == "clip" and job["status"] == "queued" and job["stage"] == "clip_analyze"
    assert job["source"] == {
        "source_id": sid,
        "title": "Эфир 12.09",
        "clip_candidate_id": None,
        "start_sec": 1204.3,
        "end_sec": 1226.9,
    }
    row = job_row(db, job["id"])
    assert row["reframe_mode"] == "center" and row["author_layer"]["hook_text"] == "Слушай"
    (task,) = job_tasks(db, job["id"])
    assert task["stage"] == "clip_analyze" and task["resource_class"] == "heavy"

    cand = db.execute(
        "INSERT INTO clip_candidates (source_id, start_sec, end_sec, title) "
        "VALUES (%s, 10, 32, 'Хук') RETURNING id",
        (sid,),
    ).fetchone()["id"]
    r = op.post(
        "/jobs", json={"kind": "clip", "channel_id": "taro_main", "clip_candidate_id": cand, "start": False}
    )
    assert r.status_code == 201, r.text
    assert r.json()["topic"] == "Хук" and r.json()["source"]["clip_candidate_id"] == cand
    c = db.execute("SELECT status, job_id FROM clip_candidates WHERE id = %s", (cand,)).fetchone()
    assert c["status"] == "used" and c["job_id"] == r.json()["id"]
    r2 = op.post("/jobs", json={"kind": "clip", "channel_id": "taro_main", "clip_candidate_id": cand})
    assert r2.status_code == 409 and err(r2)["code"] == "clip_candidate_in_use"
    op.post(f"/jobs/{r.json()['id']}/cancel")
    r3 = op.post(
        "/jobs", json={"kind": "clip", "channel_id": "taro_main", "clip_candidate_id": cand, "start": False}
    )
    assert r3.status_code == 201  # ролик кандидата отменён — кандидат свободен


def test_clip_job_validation(login_as: Any, catalog: dict, db: Any) -> None:
    op = login_as("operator")
    ready = _source(db)
    not_ready = _source(db, status="transcribing")
    base = {"kind": "clip", "channel_id": "taro_vk", "start": False}
    r = op.post("/jobs", json={**base})
    assert r.status_code == 422  # ни кандидата, ни источника
    r = op.post("/jobs", json={**base, "source": {"source_id": not_ready, "start_sec": 0, "end_sec": 20}})
    assert r.status_code == 409 and err(r)["code"] == "source_not_ready"
    r = op.post("/jobs", json={**base, "source": {"source_id": ready, "start_sec": 0, "end_sec": 75}})
    assert r.status_code == 422 and err(r)["details"]["errors"][0]["type"] == "clip_length_platform"
    r = op.post("/jobs", json={**base, "source": {"source_id": 999999, "start_sec": 0, "end_sec": 20}})
    assert r.status_code == 404 and err(r)["code"] == "source_not_found"
    r = op.post("/jobs", json={**base, "clip_candidate_id": 999999})
    assert r.status_code == 404 and err(r)["code"] == "clip_candidate_not_found"
    url_src = _source(db, origin="url")
    r = op.post(
        "/jobs", json={**base, "start": True, "source": {"source_id": url_src, "start_sec": 0, "end_sec": 30}}
    )
    assert r.status_code == 201 and r.json()["stage"] == "clip_fetch"


def test_viewer_cannot_see_media_without_login(client: Any, catalog: dict, db: Any, app_env: Any) -> None:
    job = make_job(db)
    add_artifact(db, app_env.media_root, job, "preview")
    assert client.get(f"{API}/jobs/{job['id']}/media/preview").status_code == 401
    assert Path(app_env.media_root).is_dir()


def test_job_priority_shifts_stage_task_priority(login_as: Any, catalog: dict, db: Any) -> None:
    op = login_as("operator")
    urgent = op.post("/jobs", json=_gen(start=True, priority=0)).json()
    normal = op.post("/jobs", json=_gen(start=True)).json()
    assert job_tasks(db, urgent["id"])[0]["priority"] == 40  # script 50 − 10
    assert job_tasks(db, normal["id"])[0]["priority"] == 50
    r = op.patch(f"/jobs/{normal['id']}", json={"priority": 300})
    assert r.status_code == 200 and r.json()["priority"] == 300
    assert job_tasks(db, normal["id"])[0]["priority"] == 70
