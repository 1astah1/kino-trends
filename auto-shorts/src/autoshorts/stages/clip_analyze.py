"""Стадия clip-job `clip_analyze` (heavy, §6.3.6): рефрейм 16:9 → 9:16 на прокси.

PySceneDetect на прокси в окне клипа (с запасом под cold open перед клипом), лица YuNet, стратегия
на шот `track`/`center`/`fit_blur` (`clipping.reframe.plan_reframe`). Команды `sendcmd` зависят
от итоговой раскладки EDL (вставки, петля), поэтому здесь сохраняется план целиком
(`reframe_plan.json`, артефакт `crop_cmd` idx 0), а `crop_NNN.cmd` пишет стадия render.
Заодно фиксируется диапазон предложений клипа (`jobs.clip_start_sid/clip_end_sid`).
"""

from __future__ import annotations

import json

from autoshorts.stages.base import NewArtifact, StageContext, StageResult, input_hash, register_stage


@register_stage("clip", "clip_analyze", "heavy", 2, False)
def clip_analyze(ctx: StageContext) -> StageResult:
    from autoshorts.clipping.reframe import plan_reframe
    from autoshorts.db.pool import connection
    from autoshorts.repos import clips as clips_repo
    from autoshorts.services import clipping

    job = ctx.job
    payload = job.get("stage_payload") or {}
    with connection() as conn:
        src = clipping.require_source(conn, int(job["source_id"]))
        sctx = clipping.source_context(conn, src)
        tr = clipping.load_transcript(sctx)
        state = clips_repo.get_clip_state(conn, int(job["id"])) or {}
        s_sid, e_sid = clipping._clip_range_sids(conn, job, state, tr)
    source = clipping.clip_source(sctx, payload.get("clip_section"))
    start, end = float(job["clip_start_sec"]), float(job["clip_end_sec"])
    lo, hi = source.media_range or (source.src_offset, source.src_offset + source.duration)
    a = max(lo, start - min(clipping.PREROLL_PAD_SEC, max(0.0, start - lo)))
    b = min(hi, end + 1.0) if hi > end else end
    mode = str(job.get("reframe_mode") or "auto")
    ctx.check_cancelled()
    clipping.wait_memory_or_defer()
    ctx.progress(0.1)
    # канал без рамки (кино-эдит, кадр на весь экран): вшитые чёрные полосы широкоэкранного кино не берём в кадр
    box = None
    with connection() as conn:
        framed = clipping.resolve_frame_style(conn, job) is not None
    if not framed:
        from autoshorts.clipping import frame_style as fstyle

        try:
            box = fstyle.detect_content_box(source.render_path, a - source.src_offset, b - source.src_offset,
                                            int(source.width), int(source.height))
        except Exception:  # noqa: BLE001 — без полос ролик всё равно соберётся
            box = None
    plan = plan_reframe(
        source.analysis_path,
        a,
        b,
        src_w=source.width,
        src_h=source.height,
        fps=30.0,
        mode=mode,
        content_box=box,
    )
    ctx.check_cancelled()
    out_dir = ctx.workdir / f"analyze_{int(ctx.task['id'])}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "reframe_plan.json"
    data = clipping.reframe_plan_to_json(plan)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    ctx.progress(1.0)
    summary = plan.to_dict()
    return StageResult(
        artifacts=[
            NewArtifact(
                "crop_cmd",
                path,
                input_hash(ctx.video_uid, "clip_analyze", str(source.render_path), a, b, mode),
                idx=0,
            )
        ],
        payload_patch={
            "clip_reframe": {
                "path": clipping._layout().relpath(path),
                "range": [round(a, 3), round(b, 3)],
                "modes": sorted({s["mode"] for s in summary.get("shots") or []}),
                "scene_cuts": summary.get("scene_cuts") or [],
                "warnings": list(plan.warnings),
                "render_size": [source.width, source.height],
                "sids": [s_sid, e_sid],
            }
        },
    )
