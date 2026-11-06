"""Каркас уборки §8.2: план, исполнение, превью, постановка, аварийный режим."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from autoshorts.core.storage import MediaLayout
from autoshorts.db.pool import connection
from autoshorts.domain.settings_schema import RuntimeSettings
from autoshorts.repos import artifacts as artifacts_repo
from autoshorts.services import tasks as svc
from tests.worker.conftest import enqueue_raw, make_job, make_runner, run_in_thread, task_row, wait_until

DAY = 86400


def age(path: Path, seconds: float) -> None:
    t = time.time() - seconds
    os.utime(path, (t, t))


def test_plan_execute_preview(queue_env: Any, conn: Any) -> None:
    media = MediaLayout(Path(queue_env.media_root))
    media.ensure_tree()
    old_tmp = media.tmp_dir() / "work-old"
    old_tmp.mkdir()
    (old_tmp / "x.bin").write_bytes(b"x" * 100)
    age(old_tmp, 2 * DAY)
    fresh_tmp = media.tmp_dir() / "work-new"
    fresh_tmp.mkdir()
    old_log = media.task_log_path(1)
    old_log.write_text("old")
    age(old_log, 20 * DAY)
    new_log = media.task_log_path(2)
    new_log.write_text("new")
    part = media.upload_part("abandoned")
    part.write_bytes(b"p" * 10)
    age(part, 2 * DAY)

    job_id = make_job(conn)
    jd = media.job_dir("tjob1", create=True)
    for name in ("old_scene.png", "final.mp4", "rewritten.mp4"):
        (jd / name).write_bytes(b"f" * 50)
        age(jd / name, 3600)
    master = media.master_path("tjob1", __import__("datetime").date(2026, 9, 1), create=True)
    master.write_bytes(b"m")
    age(master, 3600)
    dead = artifacts_repo.insert(
        conn,
        job_id=job_id,
        kind="scene_image",
        path="jobs/tjob1/old_scene.png",
        input_hash="a",
        stage="visuals",
    )
    artifacts_repo.insert(
        conn, job_id=job_id, kind="final", path="jobs/tjob1/final.mp4", input_hash="old", stage="render"
    )
    artifacts_repo.insert(
        conn, job_id=job_id, kind="final", path="jobs/tjob1/rewritten.mp4", input_hash="r", stage="render"
    )
    artifacts_repo.insert(
        conn, job_id=job_id, kind="final", path=media.relpath(master), input_hash="m", stage="render"
    )
    assert artifacts_repo.soft_delete_stages(conn, job_id, ["visuals", "render"]) == 4
    # новый живой артефакт на том же пути (rework перезаписал final.mp4) — файл не трогать
    artifacts_repo.insert(
        conn, job_id=job_id, kind="final", path="jobs/tjob1/final.mp4", input_hash="new", stage="render"
    )
    # файл переписан после пометки — не трогать
    (jd / "rewritten.mp4").write_bytes(b"new")

    preview = svc.cleanup_preview(media)
    items = {i["category"]: i for i in preview["items"]}
    assert set(items) == set(svc.CLEANUP_CATEGORIES)
    assert (items["tmp"]["files"], items["tmp"]["bytes"]) == (1, 100)
    assert items["task_logs"]["files"] == 1 and items["tus_parts"]["files"] == 1
    assert (items["work_artifacts"]["files"], items["work_artifacts"]["bytes"]) == (1, 50)
    assert items["proxies"]["files"] == 0

    with connection() as c:
        plan = svc.plan_cleanup(c, media)
    assert {p.artifact_id for p in plan if p.category == "work_artifacts"} == {dead["id"]}
    removed = {i["category"]: i["files"] for i in svc.execute_cleanup(plan)}
    assert removed["tmp"] == 1 and removed["task_logs"] == 1 and removed["work_artifacts"] == 1
    assert not old_tmp.exists() and fresh_tmp.exists() and not old_log.exists() and new_log.exists()
    assert not part.exists() and not (jd / "old_scene.png").exists()
    assert (jd / "final.mp4").exists() and (jd / "rewritten.mp4").exists() and master.exists()


def test_enqueue_cleanup_dedup(queue_env: Any, conn: Any) -> None:
    with connection() as c:
        a = svc.enqueue_cleanup(c)
        b = svc.enqueue_cleanup(c)
        e = svc.enqueue_cleanup(c, emergency=True)
    assert a.created and not b.created and a.task["id"] == b.task["id"]
    assert (a.task["kind"], a.task["resource_class"], a.task["dedup_key"]) == (
        "cleanup",
        "light",
        "cleanup:manual",
    )
    assert e.created and e.task["payload"] == {"emergency": True} and e.task["priority"] == 150


def test_cleanup_task_runs_in_worker(queue_env: Any, conn: Any) -> None:
    media = MediaLayout(Path(queue_env.media_root))
    old = media.tmp_dir(create=True) / "stale.bin"
    old.write_bytes(b"s" * 7)
    age(old, 2 * DAY)
    with connection() as c:
        t = svc.enqueue_cleanup(c).task
    runner = make_runner(queue_env)
    with run_in_thread(runner):
        wait_until(lambda: task_row(conn, t["id"])["status"] == "succeeded")
    result = task_row(conn, t["id"])["result"]
    assert {i["category"]: i["bytes"] for i in result["removed"]}["tmp"] == 7
    assert not old.exists() and result["disk_free_gb"] > 0


def test_heavy_worker_queues_emergency_cleanup_when_disk_critical(queue_env: Any, conn: Any) -> None:
    h = enqueue_raw(conn, "wtest_heavy_record", "heavy")
    huge = 10.0**7  # ГБ: любой реальный диск «меньше аварийного порога»
    settings = RuntimeSettings.from_overrides(
        {"disk_soft_min_gb": huge, "disk_hard_min_gb": huge, "disk_emergency_gb": huge}
    )
    runner = make_runner(
        queue_env,
        "heavy",
        admission=True,
        settings=settings,
        heavy_lock_path=Path(queue_env.media_root) / "h.lock",
    )
    with run_in_thread(runner):
        row = wait_until(
            lambda: conn.execute("SELECT * FROM tasks WHERE dedup_key = 'cleanup:emergency'").fetchone()
        )
        wait_until(lambda: task_row(conn, h["id"])["defer_reason"] == "disk_low")
    assert row["payload"] == {"emergency": True} and row["resource_class"] == "light"
    assert task_row(conn, h["id"])["status"] == "queued" and task_row(conn, h["id"])["attempts"] == 0


def test_retention_dead_jobs_and_spent_sources(queue_env: Any, conn: Any) -> None:
    """Сроки хранения: папка отклонённого ролика уходит, папка, на которую ссылается живой клон, остаётся;
    у отработанного источника уходят видео и звук, расшифровка остаётся; источник с роликом в работе не трогаем."""
    media = MediaLayout(Path(queue_env.media_root))
    media.ensure_tree()
    settings = RuntimeSettings(rejected_jobs_retain_days=1, original_retain_days=2)

    def src(title: str) -> int:
        row = conn.execute(
            "INSERT INTO sources (title, origin, origin_url, status) VALUES (%s, 'url', 'https://x.test/v', 'ready')"
            " RETURNING id",
            (title,),
        ).fetchone()
        return int(row["id"])

    spent, busy = src("отработан"), src("в работе")
    dead = make_job(conn, status="rejected", stage="qc", uid="deaduid1")
    shared = make_job(conn, status="cancelled", stage="qc", uid="shareduid")
    clone = make_job(conn, status="pending_review", stage="qc", uid="cloneuid")
    live = make_job(conn, status="processing", stage="render", uid="liveuid")
    conn.execute("UPDATE jobs SET source_id = %s WHERE id = ANY(%s)", (spent, [dead, shared]))
    conn.execute("UPDATE jobs SET source_id = %s WHERE id = %s", (busy, live))
    conn.execute("UPDATE jobs SET source_id = %s WHERE id = %s", (spent, clone))
    conn.execute("UPDATE jobs SET status = 'published' WHERE id = %s", (clone,))
    conn.execute("UPDATE jobs SET updated_at = now() - interval '3 days'")
    conn.execute("UPDATE sources SET updated_at = now() - interval '3 days'")
    for uid in ("deaduid1", "shareduid", "liveuid"):
        jd = media.job_dir(uid, create=True)
        (jd / "final.mp4").write_bytes(b"f" * 100)
    # живой клон держит файл из папки отменённого родителя
    artifacts_repo.insert(conn, job_id=clone, kind="final", path="jobs/shareduid/final.mp4", input_hash="h", stage="render")
    for sid in (spent, busy):
        sd = media.source_dir(sid, create=True)
        for name in ("original_video.mp4", "proxy_540p.mp4", "audio.m4a", "transcript.json.gz"):
            (sd / name).write_bytes(b"s" * 10)

    plan = svc.plan_cleanup(conn, media, settings=settings)
    paths = {str(c.path) for c in plan}
    assert str(media.jobs_dir() / "deaduid1") in paths
    assert str(media.jobs_dir() / "shareduid") not in paths
    assert str(media.jobs_dir() / "liveuid") not in paths
    spent_dir, busy_dir = media.source_dir(spent), media.source_dir(busy)
    assert {str(spent_dir / n) for n in ("original_video.mp4", "proxy_540p.mp4", "audio.m4a")} <= paths
    assert str(spent_dir / "transcript.json.gz") not in paths
    assert not any(p.startswith(str(busy_dir)) for p in paths)

    svc.execute_cleanup(plan)
    assert not (media.jobs_dir() / "deaduid1").exists()
    assert (media.jobs_dir() / "shareduid" / "final.mp4").exists()
    assert (spent_dir / "transcript.json.gz").exists() and not (spent_dir / "original_video.mp4").exists()

    # свежий отказ (моложе срока) не трогаем
    conn.execute("UPDATE jobs SET updated_at = now()")
    assert not any("deaduid" in str(c.path) for c in svc.plan_cleanup(conn, media, settings=settings))
