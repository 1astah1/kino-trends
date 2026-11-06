"""Ролик целиком через новую очередь (M2-C, критерий M2 `test_jobs_flow`): POST /jobs → воркеры
в тесте → pending_review с артефактами → приёмка; параллельные approve и reject → ровно один 409.

Стадии — тестовые заглушки (`@register_stage` подменён на время теста), исполнитель задач
`job_stage` — настоящий `services.jobs.run_stage_task`, воркеры — настоящий `workers.runner.Runner`.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_jobs_support import BANNER_PLAN, err, job_row, job_tasks, seed_catalog

API = "/api/v1"


@pytest.fixture
def catalog(db: Any) -> dict[str, Any]:
    return seed_catalog(db)


# ---------------------------------------------------------------- тестовые стадии


def _write(path: Path, data: bytes | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    return path


def _stage_handlers() -> dict[tuple[str, str], Any]:
    from autoshorts.stages.base import NewArtifact, StageResult, input_hash

    def script(ctx: Any) -> StageResult:
        text = f"Три карты про {ctx.job.get('topic') or 'жизнь'}"
        p = _write(ctx.workdir / "script.json", json.dumps({"voiceover_text": text}, ensure_ascii=False))
        return StageResult(
            artifacts=[NewArtifact("script_json", p, input_hash(ctx.video_uid, "script"))],
            payload_patch={"script": {"hook_text": "Три карты", "voiceover_text": text, "cta_text": "Жми"}},
        )

    def voice(ctx: Any) -> StageResult:
        a = _write(ctx.workdir / "voice.mp3", b"ID3 fake")
        w = _write(
            ctx.workdir / "words.json",
            json.dumps([{"word": "Три", "start_sec": 0.0, "end_sec": 0.3}], ensure_ascii=False),
        )
        return StageResult(
            artifacts=[
                NewArtifact("voice", a, input_hash(ctx.video_uid, "voice")),
                NewArtifact("words_json", w, input_hash(ctx.video_uid, "words")),
            ],
            payload_patch={"voice": {"provider": "edge_tts"}},
        )

    def visuals(ctx: Any) -> StageResult:
        arts = [
            NewArtifact("scene_image", _write(ctx.workdir / f"scene_{i}.png", b"png"), f"s{i}", idx=i)
            for i in range(2)
        ]
        return StageResult(
            artifacts=arts,
            payload_patch={"scenes": [{"prompt_en": "moon", "seed": 1, "duration_sec": 10.7}] * 2},
        )

    def render(ctx: Any) -> StageResult:
        ctx.progress(0.5)
        arts = [
            NewArtifact("final", _write(ctx.workdir / "final.mp4", b"\x00" * 64), "f"),
            NewArtifact("preview", _write(ctx.workdir / "preview.mp4", b"\x00" * 32), "p"),
            NewArtifact("poster", _write(ctx.workdir / "poster.jpg", b"jpg"), "q"),
        ]
        return StageResult(
            artifacts=arts, job_fields={"duration_sec": 21.4}, payload_patch={"banner_plan": BANNER_PLAN}
        )

    def qc(ctx: Any) -> StageResult:
        fail = ctx.job.get("topic") == "fail-once" and int(ctx.job.get("attempt_count") or 0) == 0
        checks = [{"code": "loudness", "ok": not fail, "measured": "-14.2 LUFS", "threshold": "-14±1.5"}]
        report = _write(ctx.workdir / "qc.json", json.dumps(checks))
        return StageResult(
            artifacts=[NewArtifact("qc_report", report, "qc")],
            job_fields={"qc_failed_checks": ["loudness"] if fail else []},
            payload_patch={"qc": {"checks": checks}},
        )

    return {
        ("generate", "script"): script,
        ("generate", "voice"): voice,
        ("generate", "visuals"): visuals,
        ("generate", "render"): render,
        ("generate", "qc"): qc,
    }


@pytest.fixture
def pipeline(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Тестовые стадии generate + исполнитель `job_stage` = `services.jobs.run_stage_task`."""
    from autoshorts.services.jobs import run_stage_task
    from autoshorts.stages import base as stage_base
    from autoshorts.tasks import registry

    specs = dict(stage_base._stages)
    for (kind, stage), handler in _stage_handlers().items():
        d = stage_base.STAGE_DEFAULTS[stage]
        specs[(kind, stage)] = stage_base.StageSpec(
            kind, stage, d.resource_class, d.max_attempts, False, d.priority, handler
        )
    monkeypatch.setattr(stage_base, "_stages", specs)
    tasks = dict(registry._registry)
    tasks["job_stage"] = registry.TaskSpec(
        kind="job_stage",
        resource_class="light",
        max_attempts=3,
        paid=False,
        priority=50,
        handler=run_stage_task,
        module=run_stage_task.__module__,
    )
    monkeypatch.setattr(registry, "_registry", tasks)
    return specs


def _runner(app_env: Any, resource_class: str, tmp_path: Path) -> Any:
    from autoshorts.domain.settings_schema import RuntimeSettings
    from autoshorts.workers.runner import Runner, RunnerConfig

    settings = RuntimeSettings()
    cfg = RunnerConfig(
        resource_class=resource_class,
        conninfo=app_env.database_url,
        poll_interval_sec=0.2,
        heartbeat_interval_sec=0.5,
        progress_interval_sec=0.1,
        worker_heartbeat_sec=0.5,
        standby_retry_sec=0.2,
        shutdown_grace_sec=5.0,
        kill_grace_sec=1.0,
        admission=False,
        scheduler=False,
        media_root=Path(app_env.media_root),
        heavy_lock_path=tmp_path / "heavy.lock",
        worker_id=f"test-{resource_class}",
    )
    return Runner(cfg, settings_provider=lambda: settings)


@contextlib.contextmanager
def workers(app_env: Any, tmp_path: Path) -> Iterator[None]:
    runners = [_runner(app_env, "light", tmp_path), _runner(app_env, "heavy", tmp_path)]
    threads = [threading.Thread(target=r.run, daemon=True) for r in runners]
    for t in threads:
        t.start()
    try:
        yield
    finally:
        for r in runners:
            r.request_stop()
        for t in threads:
            t.join(30)
        assert not any(t.is_alive() for t in threads), "runner did not stop"


def wait_status(db: Any, job_id: int, statuses: set[str], timeout: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    row = job_row(db, job_id)
    while time.monotonic() < deadline:
        row = job_row(db, job_id)
        if row["status"] in statuses:
            return row
        time.sleep(0.1)
    tasks = [(t["stage"], t["status"], t["last_error"]) for t in job_tasks(db, job_id)]
    raise AssertionError(f"job {job_id} stuck in {row['status']}/{row['stage']}: {tasks}")


# ---------------------------------------------------------------- тесты


def test_generate_job_reaches_review_and_is_approved(
    login_as: Any, catalog: dict, db: Any, app_env: Any, pipeline: Any, tmp_path: Path
) -> None:
    op = login_as("operator")
    first = op.post("/jobs", json={"kind": "generate", "channel_id": "taro_main", "topic": "любовь"}).json()
    second = op.post("/jobs", json={"kind": "generate", "channel_id": "taro_main", "topic": "работа"}).json()
    assert first["status"] == "queued"
    with workers(app_env, tmp_path):
        a = wait_status(db, first["id"], {"pending_review", "qc_failed", "failed"})
        b = wait_status(db, second["id"], {"pending_review", "qc_failed", "failed"})
    assert (a["status"], a["stage"]) == ("pending_review", "qc") and b["status"] == "pending_review"
    assert float(a["duration_sec"]) == 21.4

    stages = [(t["stage"], t["status"], t["resource_class"]) for t in job_tasks(db, first["id"])]
    assert stages == [
        ("script", "succeeded", "light"),
        ("voice", "succeeded", "light"),
        ("visuals", "succeeded", "light"),
        ("render", "succeeded", "heavy"),
        ("qc", "succeeded", "heavy"),
    ]
    arts = db.execute(
        "SELECT kind, stage, path, bytes FROM artifacts WHERE job_id = %s AND deleted_at IS NULL",
        (first["id"],),
    ).fetchall()
    kinds = {r["kind"]: r for r in arts}
    assert {"final", "preview", "poster", "voice", "words_json", "script_json", "qc_report"} <= set(kinds)
    assert kinds["final"]["stage"] == "render" and kinds["final"]["bytes"] == 64
    assert kinds["final"]["path"] == f"jobs/{first['video_uid']}/final.mp4"

    detail = op.get(f"/jobs/{first['id']}", params={"include": "words"}).json()
    assert detail["media"]["final_url"] and detail["script"]["hook_text"] == "Три карты"
    assert detail["words"] == [{"word": "Три", "start_sec": 0.0, "end_sec": 0.3}]
    assert [s["stage"] for s in detail["stages"]] == ["script", "voice", "visuals", "render", "qc"]
    assert op.get(f"/jobs/{first['id']}/media/final").content == b"\x00" * 64
    ctx = op.get(f"/jobs/{first['id']}/review-context").json()
    assert ctx["qc"]["passed"] is True and ctx["banner"]["boxes"] and ctx["scene_cuts_sec"] == [10.7]
    queue = op.get("/review-queue").json()["items"]
    assert {i["job_id"] for i in queue} == {first["id"], second["id"]}

    # решение: одобрить; параллельно по второму ролику — approve и reject, ровно один 409
    r = op.post(
        f"/jobs/{first['id']}/review", json={"decision": "approve", "expected_status": "pending_review"}
    )
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert [t["status"] for t in job_tasks(db, first["id"], kind="package_build")] == ["queued"]

    other = login_as("operator", "second_op")
    barrier = threading.Barrier(2)
    out: list[Any] = []

    def decide(user: Any, body: dict[str, Any]) -> None:
        barrier.wait()
        out.append(user.post(f"/jobs/{second['id']}/review", json=body))

    threads = [
        threading.Thread(
            target=decide, args=(op, {"decision": "approve", "expected_status": "pending_review"})
        ),
        threading.Thread(
            target=decide,
            args=(
                other,
                {"decision": "reject", "expected_status": "pending_review", "reject_reason": "dublikat"},
            ),
        ),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(r.status_code for r in out) == [200, 409]
    conflict = next(r for r in out if r.status_code == 409)
    assert err(conflict)["code"] == "job_state_conflict" and err(conflict)["details"]["decided_via"] == "web"


def test_qc_failed_rework_restarts_and_passes(
    login_as: Any, catalog: dict, db: Any, app_env: Any, pipeline: Any, tmp_path: Path
) -> None:
    op = login_as("operator")
    job = op.post("/jobs", json={"kind": "generate", "channel_id": "taro_main", "topic": "fail-once"}).json()
    with workers(app_env, tmp_path):
        row = wait_status(db, job["id"], {"pending_review", "qc_failed", "failed"})
        assert row["status"] == "qc_failed" and row["qc_failed_checks"] == ["loudness"]
        r = op.post(
            f"/jobs/{job['id']}/review",
            json={"decision": "rework", "expected_status": "qc_failed", "rework_from_stage": "render"},
        )
        assert r.status_code == 200 and r.json()["status"] == "processing"
        row = wait_status(db, job["id"], {"pending_review", "qc_failed", "failed"})
    assert row["status"] == "pending_review" and row["attempt_count"] == 1 and row["qc_failed_checks"] == []
    render_tasks = [t for t in job_tasks(db, job["id"]) if t["stage"] == "render"]
    assert [t["dedup_key"] for t in render_tasks] == [
        f"job:{job['id']}:render:0",
        f"job:{job['id']}:render:1",
    ]
    # старые артефакты render/qc помечены, новые живые
    finals = db.execute(
        "SELECT deleted_at FROM artifacts WHERE job_id = %s AND kind = 'final' ORDER BY id", (job["id"],)
    ).fetchall()
    assert len(finals) == 2 and finals[0]["deleted_at"] is not None and finals[1]["deleted_at"] is None


def test_unregistered_stage_fails_job(
    login_as: Any, catalog: dict, db: Any, app_env: Any, pipeline: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    from autoshorts.stages import base as stage_base

    specs = dict(stage_base._stages)
    specs.pop(("generate", "voice"))
    monkeypatch.setattr(stage_base, "_stages", specs)
    op = login_as("operator")
    job = op.post("/jobs", json={"kind": "generate", "channel_id": "taro_main", "topic": "x"}).json()
    with workers(app_env, tmp_path):
        row = wait_status(db, job["id"], {"failed", "pending_review"})
    assert (
        row["status"] == "failed" and row["error_class"] == "stage_not_registered" and row["stage"] == "voice"
    )
    r = op.post(f"/jobs/{job['id']}/retry", json={})
    assert r.status_code == 200 and r.json()["stage"] == "voice"


def test_cancel_while_processing_drops_stage_result(
    login_as: Any, catalog: dict, db: Any, app_env: Any
) -> None:
    """Итог стадии, пришедший после отмены ролика, не двигает его дальше (commit_stage_result → None)."""
    from autoshorts.core.storage import MediaLayout
    from autoshorts.db.pool import connection
    from autoshorts.services import jobs as svc
    from autoshorts.stages.base import StageResult

    op = login_as("operator")
    job = op.post("/jobs", json={"kind": "generate", "channel_id": "taro_main", "topic": "x"}).json()
    with connection() as conn:
        assert svc.begin_stage(conn, job["id"], "script")["status"] == "processing"
    assert op.post(f"/jobs/{job['id']}/cancel").status_code == 200
    (task,) = job_tasks(db, job["id"])
    with connection() as conn:
        out = svc.commit_stage_result(
            conn,
            job["id"],
            task,
            StageResult(payload_patch={"x": 1}),
            media=MediaLayout(Path(app_env.media_root)),
        )
        assert svc.begin_stage(conn, job["id"], "script") is None
    assert out is None and job_row(db, job["id"])["status"] == "cancelled"
    assert "x" not in job_row(db, job["id"])["stage_payload"]
