"""API приёмки (M2-C): решения, 409 при гонке веба и бота, undo ≤ 10 мин, очередь, контекст (§5.2, §6.2)."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from tests.api.test_jobs_support import (
    BANNER_PLAN,
    LAYERS,
    add_artifact,
    err,
    job_row,
    job_tasks,
    make_job,
    seed_catalog,
)


@pytest.fixture
def catalog(db: Any) -> dict[str, Any]:
    return seed_catalog(db)


def _decide(user: Any, job_id: int, decision: str, expected: str = "pending_review", **kw: Any) -> Any:
    return user.post(f"/jobs/{job_id}/review", json={"decision": decision, "expected_status": expected, **kw})


# ---------------------------------------------------------------- решения


def test_approve_records_decision_and_enqueues_package(login_as: Any, catalog: dict, db: Any) -> None:
    job = make_job(db, review_sent_at=None)
    op = login_as("operator")
    r = _decide(op, job["id"], "approve", comment="Хорошо")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    (rev,) = body["reviews"]
    assert (
        rev["decision"] == "approve" and rev["decided_via"] == "web" and rev["decided_by_name"] == "Operator1"
    )
    assert rev["comment"] == "Хорошо"
    (pkg,) = job_tasks(db, job["id"], kind="package_build")
    assert (
        pkg["status"] == "queued"
        and pkg["stage"] == "package"
        and pkg["dedup_key"] == f"job:{job['id']}:package:0"
    )
    audit = db.execute(
        "SELECT diff FROM audit_log WHERE action = 'job.review' AND entity_id = %s", (str(job["id"]),)
    ).fetchone()
    assert audit["diff"]["status_from"] == "pending_review" and audit["diff"]["status_to"] == "approved"


def test_decision_validation(login_as: Any, catalog: dict, db: Any) -> None:
    job = make_job(db, status="qc_failed", qc_failed_checks=["loudness"])
    op = login_as("operator")
    r = _decide(op, job["id"], "approve", "qc_failed")
    assert r.status_code == 422 and "override_qc" in err(r)["details"]["errors"][0]["msg"]
    r = _decide(op, job["id"], "reject", "qc_failed")
    assert r.status_code == 422 and err(r)["details"]["errors"][0]["type"] == "reject_reason_required"
    r = _decide(op, job["id"], "reject", "qc_failed", reject_reason="bogus")
    assert r.status_code == 422
    r = _decide(op, job["id"], "rework", "qc_failed", rework_from_stage="clip_analyze")
    assert r.status_code == 422
    r = _decide(op, job["id"], "undo", "qc_failed")
    assert r.status_code == 422  # undo — отдельный эндпоинт
    assert _decide(op, 424242, "approve").status_code == 404
    viewer = login_as("viewer")
    assert _decide(viewer, job["id"], "reject", "qc_failed", reject_reason="drugoe").status_code == 403
    assert job_row(db, job["id"])["status"] == "qc_failed"


def test_reject_from_qc_failed_and_undo_returns_to_qc_failed(login_as: Any, catalog: dict, db: Any) -> None:
    job = make_job(db, status="qc_failed", qc_failed_checks=["loudness"])
    op = login_as("operator")
    r = _decide(op, job["id"], "reject", "qc_failed", reject_reason="zvuk_problema")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert job_row(db, job["id"])["reject_reason"] == "zvuk_problema"
    r = op.post(f"/jobs/{job['id']}/review/undo")
    assert r.status_code == 200, r.text
    row = job_row(db, job["id"])
    assert row["status"] == "qc_failed" and row["reject_reason"] is None
    assert [d["decision"] for d in r.json()["reviews"]] == ["reject", "undo"]


def test_override_qc_admin_only(login_as: Any, catalog: dict, db: Any) -> None:
    job = make_job(db, status="qc_failed", qc_failed_checks=["loudness"])
    r = _decide(login_as("operator"), job["id"], "override_qc", "qc_failed")
    assert r.status_code == 403
    admin = login_as("admin")
    r = _decide(admin, job["id"], "override_qc", "qc_failed", comment="громкость ок на слух")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    r = admin.post(f"/jobs/{job['id']}/review/undo")
    assert r.status_code == 200 and r.json()["status"] == "qc_failed"
    assert [t["status"] for t in job_tasks(db, job["id"], kind="package_build")] == ["cancelled"]


def test_rework_auto_restart_and_undo_restores(login_as: Any, catalog: dict, db: Any, app_env: Any) -> None:
    job = make_job(db, attempt_count=0, review_sent_at="2026-09-27T00:00:00Z")
    script = add_artifact(db, app_env.media_root, job, "script_json", stage="script")
    voice = add_artifact(db, app_env.media_root, job, "voice", stage="voice")
    final = add_artifact(db, app_env.media_root, job, "final", stage="render")
    op = login_as("operator")
    r = _decide(
        op, job["id"], "rework", rework_from_stage="voice", reject_reason="zvuk_problema", comment="голос"
    )
    assert r.status_code == 200, r.text
    row = job_row(db, job["id"])
    assert (row["status"], row["stage"], row["attempt_count"], row["rework_from_stage"]) == (
        "processing",
        "voice",
        1,
        "voice",
    )
    assert row["review_sent_at"] is None
    (task,) = job_tasks(db, job["id"], kind="job_stage")
    assert task["stage"] == "voice" and task["dedup_key"] == f"job:{job['id']}:voice:1"
    live = {a["id"] for a in db.execute("SELECT id FROM artifacts WHERE deleted_at IS NULL").fetchall()}
    assert script["id"] in live and voice["id"] not in live and final["id"] not in live

    r = op.post(f"/jobs/{job['id']}/review/undo")
    assert r.status_code == 200, r.text
    row = job_row(db, job["id"])
    assert (row["status"], row["stage"], row["attempt_count"], row["rework_from_stage"]) == (
        "pending_review",
        "qc",
        0,
        None,
    )
    assert (
        db.execute("SELECT status FROM tasks WHERE id = %s", (task["id"],)).fetchone()["status"]
        == "cancelled"
    )
    live = {a["id"] for a in db.execute("SELECT id FROM artifacts WHERE deleted_at IS NULL").fetchall()}
    assert {script["id"], voice["id"], final["id"]} <= live


def test_undo_rework_refused_once_restart_runs(login_as: Any, catalog: dict, db: Any) -> None:
    job = make_job(db)
    op = login_as("operator")
    assert _decide(op, job["id"], "rework").status_code == 200
    db.execute("UPDATE tasks SET status = 'running', locked_by = 'w' WHERE job_id = %s", (job["id"],))
    r = op.post(f"/jobs/{job['id']}/review/undo")
    assert r.status_code == 409 and err(r)["details"]["reason"] == "rework_in_progress"


def test_rework_without_restart_then_start(login_as: Any, catalog: dict, db: Any) -> None:
    job = make_job(db, kind="generate")
    op = login_as("operator")
    r = _decide(op, job["id"], "rework", auto_restart=False, comment="поменять тему")
    assert r.status_code == 200 and r.json()["status"] == "rework" and r.json()["stage"] == "script"
    assert job_tasks(db, job["id"]) == []
    assert op.patch(f"/jobs/{job['id']}", json={"topic": "новая"}).status_code == 200
    r = op.post(f"/jobs/{job['id']}/start")
    assert r.status_code == 200 and r.json()["status"] == "processing" and r.json()["stage"] == "script"
    (task,) = job_tasks(db, job["id"])
    assert task["dedup_key"] == f"job:{job['id']}:script:1"


def test_undo_refused_after_separate_start(login_as: Any, catalog: dict, db: Any, app_env: Any) -> None:
    """Rework без перезапуска + отдельный «Старт»: артефакты помечены временем старта, не решения —
    undo не должен вернуть ролик на приёмку без превью и финала."""
    job = make_job(db, attempt_count=0)
    final = add_artifact(db, app_env.media_root, job, "final", stage="render")
    op = login_as("operator")
    assert _decide(op, job["id"], "rework", rework_from_stage="voice", auto_restart=False).status_code == 200
    r = op.post(f"/jobs/{job['id']}/start")
    assert r.status_code == 200 and r.json()["status"] == "processing", r.text
    art = db.execute("SELECT deleted_at FROM artifacts WHERE id = %s", (final["id"],)).fetchone()
    assert art["deleted_at"] is not None

    r = op.post(f"/jobs/{job['id']}/review/undo")
    assert r.status_code == 409 and err(r)["details"]["reason"] == "status_changed", r.text
    row = job_row(db, job["id"])
    assert row["status"] == "processing" and row["stage"] == "voice"
    (task,) = job_tasks(db, job["id"], kind="job_stage")
    assert task["status"] == "queued"


def test_undo_rules(login_as: Any, catalog: dict, db: Any) -> None:
    op = login_as("operator")
    fresh = make_job(db)
    r = op.post(f"/jobs/{fresh['id']}/review/undo")
    assert r.status_code == 409 and err(r)["details"]["reason"] == "nothing_to_undo"

    old = make_job(db)
    assert _decide(op, old["id"], "approve").status_code == 200
    db.execute(
        "UPDATE review_decisions SET created_at = now() - interval '11 minutes' WHERE job_id = %s",
        (old["id"],),
    )
    r = op.post(f"/jobs/{old['id']}/review/undo")
    assert r.status_code == 409 and err(r)["details"]["reason"] == "undo_window_expired"

    downloaded = make_job(db)
    assert _decide(op, downloaded["id"], "approve").status_code == 200
    art = db.execute(
        "INSERT INTO artifacts (job_id, kind, path, input_hash) "
        "VALUES (%s, 'final', 'jobs/x/final.mp4', 'h') RETURNING id",
        (downloaded["id"],),
    ).fetchone()["id"]
    link = db.execute(
        "INSERT INTO tracking_links (code, product_id, level, job_id) "
        "VALUES ('k7m2q', 'umka', 'package', %s) RETURNING id",
        (downloaded["id"],),
    ).fetchone()["id"]
    db.execute(
        """INSERT INTO upload_packages (job_id, channel_id, platform, status, title, description,
                                        tracking_link_id, final_artifact_id, downloaded_at)
           VALUES (%s, 'taro_main', 'youtube', 'downloaded', 't', 'd', %s, %s, now())""",
        (downloaded["id"], link, art),
    )
    r = op.post(f"/jobs/{downloaded['id']}/review/undo")
    assert r.status_code == 409 and err(r)["details"]["reason"] == "package_downloaded"

    ready = make_job(db)
    assert _decide(op, ready["id"], "approve").status_code == 200
    art2 = db.execute(
        "INSERT INTO artifacts (job_id, kind, path, input_hash) "
        "VALUES (%s, 'final', 'jobs/y/final.mp4', 'h') RETURNING id",
        (ready["id"],),
    ).fetchone()["id"]
    link2 = db.execute(
        "INSERT INTO tracking_links (code, product_id, level, job_id) "
        "VALUES ('k7m2r', 'umka', 'package', %s) RETURNING id",
        (ready["id"],),
    ).fetchone()["id"]
    pkg = db.execute(
        """INSERT INTO upload_packages (job_id, channel_id, platform, status, title, description,
                                        tracking_link_id, final_artifact_id)
           VALUES (%s, 'taro_main', 'youtube', 'ready', 't', 'd', %s, %s) RETURNING id""",
        (ready["id"], link2, art2),
    ).fetchone()["id"]
    detail = op.get(f"/jobs/{ready['id']}").json()
    assert detail["package"] == {"id": pkg, "status": "ready", "code": "k7m2r"}
    r = op.post(f"/jobs/{ready['id']}/review/undo")
    assert r.status_code == 200 and r.json()["status"] == "pending_review"
    assert (
        db.execute("SELECT status FROM upload_packages WHERE id = %s", (pkg,)).fetchone()["status"]
        == "superseded"
    )
    r = op.post(f"/jobs/{ready['id']}/review/undo")
    assert r.status_code == 409 and err(r)["details"]["reason"] == "nothing_to_undo"


def test_stale_expected_status_conflict_names_winner(login_as: Any, catalog: dict, db: Any) -> None:
    job = make_job(db)
    first = login_as("operator", "anna")
    second = login_as("operator", "boris")
    assert _decide(first, job["id"], "approve").status_code == 200
    r = _decide(second, job["id"], "reject", reject_reason="drugoe")
    assert r.status_code == 409
    e = err(r)
    assert e["code"] == "job_state_conflict"
    assert e["details"]["decided_by"] == "Anna" and e["details"]["decided_via"] == "web"
    assert e["details"]["status"] == "approved" and e["details"]["decision"] == "approve"


@pytest.mark.parametrize("round_", range(3))
def test_concurrent_approve_and_reject_exactly_one_409(
    login_as: Any, catalog: dict, db: Any, round_: int
) -> None:
    job = make_job(db)
    a = login_as("operator", f"web{round_}a")
    b = login_as("operator", f"web{round_}b")
    barrier = threading.Barrier(2)
    results: dict[str, Any] = {}

    def go(name: str, user: Any, decision: str, **kw: Any) -> None:
        barrier.wait()
        results[name] = _decide(user, job["id"], decision, **kw)

    threads = [
        threading.Thread(target=go, args=("approve", a, "approve")),
        threading.Thread(target=go, args=("reject", b, "reject"), kwargs={"reject_reason": "hook_slabyy"}),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    codes = sorted(r.status_code for r in results.values())
    assert codes == [200, 409], {k: (v.status_code, v.text[:200]) for k, v in results.items()}
    loser = next(r for r in results.values() if r.status_code == 409)
    winner = next(r for r in results.values() if r.status_code == 200)
    assert err(loser)["details"]["status"] == winner.json()["status"]
    decisions = db.execute("SELECT decision FROM review_decisions WHERE job_id = %s", (job["id"],)).fetchall()
    assert len(decisions) == 1
    assert job_row(db, job["id"])["status"] in ("approved", "rejected")


# ---------------------------------------------------------------- очередь и контекст


def test_review_queue_with_flags(login_as: Any, catalog: dict, db: Any, app_env: Any) -> None:
    a = make_job(
        db,
        status="pending_review",
        duration_sec=21.4,
        config_snapshot={"niche": {"config": {"duration_target_sec": [15, 25]}}},
    )
    b = make_job(
        db,
        status="qc_failed",
        channel_id="taro_vk",
        duration_sec=64.0,
        qc_failed_checks=["forbidden_phrase"],
        qc_warnings=[
            {"code": "library_similarity", "detail": "похож на a9f0", "job_id": a["id"], "similarity": 0.91}
        ],
        stage_payload={"forbidden_hits": [{"phrase": "гарантия", "start": 0, "end": 8}], "tts_mock": True},
    )
    make_job(db, status="approved")
    add_artifact(db, app_env.media_root, a, "poster")
    viewer = login_as("viewer")
    items = viewer.get("/review-queue").json()["items"]
    assert [i["job_id"] for i in items] == [a["id"], b["id"]]
    first = items[0]
    assert first["poster_url"].startswith(f"/api/v1/jobs/{a['id']}/media/poster?v=") and first["preview_url"] is None
    assert first["flags"] == [] and first["attempt_count"] == 0 and first["ready_at"]
    flags = {f["code"]: f for f in items[1]["flags"]}
    assert (
        flags["forbidden_phrase"]["severity"] == "error" and flags["forbidden_phrase"]["detail"] == "гарантия"
    )
    assert flags["duration_dead_zone"]["severity"] == "error"  # VK > 60 с
    assert flags["library_similarity"]["severity"] == "warn" and flags["tts_mock"]["severity"] == "warn"
    only_vk = viewer.get("/review-queue", params={"channel_id": "taro_vk"}).json()["items"]
    assert [i["job_id"] for i in only_vk] == [b["id"]]


def test_review_context(login_as: Any, catalog: dict, db: Any, app_env: Any) -> None:
    parent = make_job(db, status="rejected")
    admin = login_as("admin")
    db.execute(
        "INSERT INTO review_decisions (job_id, decision, reject_reason, comment, decided_via) "
        "VALUES (%s, 'reject', 'hook_slabyy', 'скучно', 'web')",
        (parent["id"],),
    )
    job = make_job(
        db,
        parent_job_id=parent["id"],
        banner_variant_id=catalog["variant_a"],
        campaign_id=catalog["campaign_id"],
        attempt_count=1,
        cost_usd=0.0512,
        config_snapshot={"banner": {"layers": LAYERS}},
        qc_warnings=[
            {"code": "library_similarity", "detail": "похож", "job_id": parent["id"], "similarity": 0.91}
        ],
        stage_payload={
            "script": {"voiceover_text": "Три карты", "cta_text": "Жми", "hook_pattern": "incomplete_visual"},
            "words": [{"word": "Три", "start_sec": 0.0, "end_sec": 0.31}],
            "scenes": [{"duration_sec": 1.2}, {"duration_sec": 3.4}, {"duration_sec": 3.5}],
            "banner_plan": BANNER_PLAN,
            "qc": {
                "checks": [{"code": "loudness", "ok": True, "measured": "-14.2 LUFS", "threshold": "-14±1.5"}]
            },
        },
    )
    db.execute(
        "INSERT INTO review_decisions (job_id, decision, rework_from_stage, comment, decided_via) "
        "VALUES (%s, 'rework', 'script', 'другой хук', 'telegram')",
        (job["id"],),
    )
    add_artifact(db, app_env.media_root, job, "preview")
    ctx = admin.get(f"/jobs/{job['id']}/review-context").json()
    assert ctx["job"]["attempt_count"] == 1 and ctx["job"]["banner_variant_id"] == catalog["variant_a"]
    assert "/media/preview?v=" in ctx["media"]["preview_url"] and ctx["media"]["duration_sec"] == 21.4
    assert ctx["script"] == {
        "topic": "отношения",
        "hook_pattern": "incomplete_visual",
        "voiceover_text": "Три карты",
        "cta_text": "Жми",
        "words": [{"word": "Три", "start_sec": 0.0, "end_sec": 0.31}],
    }
    banner = ctx["banner"]
    assert banner["variant_id"] == catalog["variant_a"] and banner["variant_code"] == "A"
    assert banner["creative_sha256s"] == ["a" * 64] and banner["fallback_used"] is False
    assert banner["windows"][0] == {"group": "persistent", "start_sec": 0.0, "end_sec": 21.4}
    assert {"group": "with_creative", "start_sec": 8.6, "end_sec": 13.6} in banner["windows"]
    boxes = {b["layer_id"]: b for b in banner["boxes"]}
    assert boxes["creative"] == {
        "layer_id": "creative",
        "kind": "creative",
        "x1": 65,
        "y1": 1120,
        "x2": 890,
        "y2": 1310,
    }
    assert boxes["code"]["kind"] == "text_code" and boxes["micro_brand"]["kind"] == "micro_brand"
    assert banner["captions_zone"] == {"x1": 60, "y1": 700, "x2": 1020, "y2": 1100}
    assert banner["warnings"][0]["code"] == "creative_in_bottom_dead_zone"
    assert ctx["scene_cuts_sec"] == [1.2, 4.6]
    assert ctx["qc"] == {
        "passed": True,
        "checks": [{"code": "loudness", "ok": True, "measured": "-14.2 LUFS", "threshold": "-14±1.5"}],
    }
    flags = {f["code"] for f in ctx["flags"]}
    assert {"banner_in_dead_zone", "library_similarity"} <= flags
    assert ctx["similarity"] == {"nearest_job_id": parent["id"], "score": 0.91, "method": "dhash"}
    assert ctx["cost_usd"] == 0.0512
    assert ctx["previous_attempts"] == [
        {"job_id": job["id"], "decision": "rework", "reject_reason": None, "comment": "другой хук"},
        {"job_id": parent["id"], "decision": "reject", "reject_reason": "hook_slabyy", "comment": "скучно"},
    ]


def test_review_context_without_plan_and_legacy_payload(login_as: Any, catalog: dict, db: Any) -> None:
    job = make_job(
        db,
        stage_payload={
            "voiceover_text": "legacy текст",
            "words": [{"word": "x", "start_sec": 0, "end_sec": 1}],
        },
    )
    ctx = login_as("viewer").get(f"/jobs/{job['id']}/review-context").json()
    assert ctx["banner"] == {
        "variant_id": None,
        "variant_code": None,
        "creative_sha256s": [],
        "fallback_used": False,
        "windows": [],
        "boxes": [],
        "captions_zone": None,
        "warnings": [],
        "layout_manual": False,
        "captions_overlap": [],
    }
    assert ctx["script"]["voiceover_text"] == "legacy текст" and ctx["qc"] == {"passed": True, "checks": []}
    assert ctx["similarity"] is None and ctx["previous_attempts"] == [] and ctx["scene_cuts_sec"] == []


def test_decision_publishes_sse_events(login_as: Any, catalog: dict, db: Any, app_env: Any) -> None:
    import json

    import psycopg

    job = make_job(db)
    other = make_job(db, status="qc_failed", qc_failed_checks=["loudness"])
    listener = psycopg.connect(app_env.database_url, autocommit=True)
    try:
        listener.execute("LISTEN as_events")
        assert _decide(login_as("operator"), job["id"], "approve").status_code == 200
        events = [json.loads(n.payload) for n in listener.notifies(timeout=2.0, stop_after=2)]
    finally:
        listener.close()
    by_type = {e["event"]: e["data"] for e in events}
    assert by_type["job_status"] == {"job_id": job["id"], "status": "approved", "stage": "qc"}
    assert by_type["review_queue"] == {"count": 1}
    assert other["id"]
