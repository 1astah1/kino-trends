"""Стадия clip-job `render` (heavy, §6.3.7): EDL → одно кодирование 1080×1920 → превью и постер.

Готовые итоги прошлых стадий (сценарий автора, реплики, план рефрейма) идут в
`clipping.produce.produce_clip` без повторной работы: раскладка EDL (cold open, вставки
freeze_zoom/over_ducked, петля outro), `crop_NNN.cmd`, двойные субтитры (Source + Author, свой
заголовок), премикс с приглушением и loudnorm, рендер одним проходом с баннером варианта ролика
(`pipeline/banner_*`: загруженный креатив, безопасные зоны площадки, код ролика из
`tracking_links`). QC и отчёт об оригинальности — следующая стадия `qc`.
Каждый прогон пишет в свой каталог `render_<task_id>`: rework не перезаписывает файлы артефактов,
которые ещё может вернуть undo.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autoshorts.core.errors import PermanentError
from autoshorts.stages.base import NewArtifact, StageContext, StageResult, input_hash, register_stage

PROGRESS_STEPS = {
    "layout": 0.05,
    "clip_analyze": 0.1,
    "captions": 0.15,
    "audio_mix": 0.3,
    "render": 0.9,
    "originality": 0.92,
}


def _preview_and_poster(
    final: Path, wd: Path, *, threads: int, duration: float, ctx: StageContext
) -> tuple[Path, Path]:
    from autoshorts.pipeline.encode_profiles import preview_profile, with_threads
    from autoshorts.pipeline.ffmpeg_exec import run_media

    preview = wd / "preview.mp4"
    poster = wd / "poster.jpg"
    prof = with_threads(preview_profile(), threads)
    run_media(
        ["ffmpeg", "-y", "-i", str(final), "-vf", "scale=540:960:flags=bicubic", *prof.output_args(fps=30.0),
         str(preview)],
        timeout_sec=600,
        threads=threads,
        nice=10,
        cancel=ctx.cancel,
        log_path=ctx.log_path,
        duration_sec=duration,
    )  # fmt: skip
    t = max(0.0, min(1.5, duration / 3.0))
    run_media(
        ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(final), "-frames:v", "1", "-vf", "scale=540:960",
         "-q:v", "3", str(poster)],
        timeout_sec=120,
        threads=threads,
        nice=10,
        cancel=ctx.cancel,
        log_path=ctx.log_path,
    )  # fmt: skip
    return preview, poster


@register_stage("clip", "render", "heavy", 2, False)
def clip_render(ctx: StageContext) -> StageResult:
    from autoshorts.clipping.edl import words_json
    from autoshorts.clipping.produce import ClipJobConfig, produce_clip
    from autoshorts.clipping.source_ingest import min_mem_mb_now
    from autoshorts.db.pool import connection
    from autoshorts.repos import clips as clips_repo
    from autoshorts.services import clipping

    job = ctx.job
    job_id = int(job["id"])
    payload: dict[str, Any] = dict(job.get("stage_payload") or {})
    layout = clipping._layout()
    with connection() as conn:
        state = clips_repo.get_clip_state(conn, job_id) or {}
        src = clipping.require_source(conn, int(job["source_id"]))
        sctx = clipping.source_context(conn, src)
        tr = clipping.load_transcript(sctx)
        s_sid, e_sid = clipping._clip_range_sids(conn, job, state, tr)
        niche = clipping.niche_profile(
            conn, job.get("niche_id") or src.get("niche_id"), product_id=job.get("product_id")
        )
        title, hook = clipping._candidate_texts(conn, job)
        banner = clipping.banner_setup(conn, job)
        if state.get("author_script") is None and job.get("parent_job_id") is not None:
            clips_repo.copy_author_layer(conn, int(job["parent_job_id"]), job_id)
            state = clips_repo.get_clip_state(conn, job_id) or {}
    data = state.get("author_script")
    if not isinstance(data, dict):
        raise PermanentError("Нет авторского текста ролика", error_class="author_script_missing")
    author = clipping.author_result_from_dict(data)
    voice = clipping.voice_result_from_payload(payload)
    if voice is None:
        raise PermanentError(
            "Нет голоса автора: стадия author_voice не прошла", error_class="author_voice_missing"
        )
    missing = [r for r, t in voice.takes.items() if not t.path.is_file()]
    if missing:
        raise PermanentError(
            f"Файлы реплик пропали: {', '.join(missing)} — перезапустите со стадии author_voice",
            error_class="author_voice_missing",
        )
    plan = None
    reframe = payload.get("clip_reframe") if isinstance(payload.get("clip_reframe"), dict) else {}
    if reframe.get("path"):
        try:
            plan_path = layout.resolve(str(reframe["path"]))
            if plan_path.is_file():
                plan = clipping.reframe_plan_from_json(json.loads(plan_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError):
            plan = None
    source = clipping.clip_source(sctx, payload.get("clip_section"))
    settings = ctx.settings
    from autoshorts.db.pool import connection as _conn

    with _conn() as conn:
        frame_style = clipping.resolve_frame_style(conn, job)
        cold_open = clipping.channel_cold_open(conn, job)
        author_voice = clipping.channel_author_voice(conn, job)
        author_text = clipping.channel_author_text(conn, job)
        lag_ad = clipping.channel_lag_ad(conn, job)
        fmt = clipping.channel_format(conn, job)
    cfg = ClipJobConfig(
        tts=clipping.tts_engine(),
        reframe_mode=str(job.get("reframe_mode") or "auto"),
        run_qc=False,
        min_share=float(settings.clip_author_share_min),
        gate_for_own=bool(settings.clip_author_share_gate_for_own),
        min_mem_mb=min_mem_mb_now(),  # порог на момент вызова (AS_DEV_MIN_MEM_MB), не импорт-тайм константа
        mem_wait_sec=clipping.MEM_WAIT_SEC,
        captions_mode=str((job.get("author_layer") or {}).get("captions") or "both"),
        frame_style=frame_style,
    )
    cfg.layout.cold_open = cold_open
    cfg.layout.author_voice = author_voice
    cfg.layout.author_text = author_text
    cfg.layout.lag_ad = lag_ad
    if fmt in ("edit", "facts"):
        # кино-эдит: фраза-история крупно только в первые секунды, дальше кадр чистый (как у растущих кино-каналов)
        cfg.layout.title_mode = "intro"
        cfg.layout.title_intro_sec = 3.0
        cfg.caption_preset = "edit"
    threads = int(getattr(settings, "ffmpeg_threads", 2) or 2)
    cfg.render.threads = threads
    wd = ctx.workdir / f"render_{int(ctx.task['id'])}"

    def progress(step: str, frac: float) -> None:
        if frac >= 1.0 and step in PROGRESS_STEPS:
            ctx.progress(PROGRESS_STEPS[step])

    from autoshorts.clipping.source_ingest import InsufficientMemory
    from autoshorts.core.errors import ResourceDeferred

    try:
        res = produce_clip(
            source,
            tr,
            start=float(job["clip_start_sec"]),
            end=float(job["clip_end_sec"]),
            start_sid=s_sid,
            end_sid=e_sid,
            title=title,
            hook_text=hook,
            workdir=wd,
            niche=niche,
            banner_plan=banner.factory,
            layout_override=clipping.layout_spec_of(job),
            config=cfg,
            progress=progress,
            cancel=ctx.cancel,
            render=True,
            author=author,
            voice=voice,
            reframe_plan=plan,
        )
    except InsufficientMemory as exc:
        raise ResourceDeferred("mem_low", retry_after_sec=120.0, message=str(exc)) from exc
    final = res.final_path
    if final is None or not final.is_file():
        raise PermanentError("Рендер не дал файла", error_class="render_failed")
    duration = float(res.render.duration if res.render else res.edl.duration)
    ctx.check_cancelled()
    preview, poster = _preview_and_poster(final, wd, threads=threads, duration=duration, ctx=ctx)
    # слова обеих дорожек на выходной шкале — для экрана приёмки
    words = [{**w, "track": "source"} for w in words_json(res.edl.source_words(tr.words))] + [
        {**w, "track": "author"} for w in words_json(res.edl.author_words())
    ]
    words.sort(key=lambda w: w["s"])
    words_path = wd / "words.json"
    words_path.write_text(json.dumps({"words": words}, ensure_ascii=False), encoding="utf-8")
    cuts = []
    for c in plan.scene_cuts if plan is not None else reframe.get("scene_cuts") or []:
        t = res.edl.src_to_out(float(c))
        if t is not None:
            cuts.append(round(t, 3))
    h = input_hash(ctx.video_uid, "render", int(ctx.task["id"]))
    artifacts = [
        NewArtifact("final", final, h),
        NewArtifact("preview", preview, h),
        NewArtifact("poster", poster, h),
        NewArtifact("edl_json", wd / "edl.json", h),
        NewArtifact("words_json", words_path, h),
    ]
    if (wd / "captions.ass").is_file():
        artifacts.append(NewArtifact("subtitles_ass", wd / "captions.ass", h))
    patch: dict[str, Any] = {
        "clip_render": {
            "dir": layout.relpath(wd),
            "warnings": list(res.warnings)[:50] + banner.warnings,
            "reframe": res.reframe.get("shots") and sorted({s.get("mode") for s in res.reframe["shots"]}),
            "mix": {k: v for k, v in (res.mix or {}).items() if k in ("loudnorm", "gains", "levels")},
            "author_share_estimate": round(res.author_share, 3),
            "banner": bool(res.render and res.render.banner),
            "layout": {
                "manual": bool(res.edl.meta.get("layout_manual")),
                "zones": res.edl.meta.get("caption_zones"),
                "conflicts": res.edl.meta.get("layout_conflicts") or [],
                "notes": banner.layout_notes,
            },
        },
        "banner_plan": res.edl.banner if isinstance(res.edl.banner, dict) else None,
        "tracking_code": banner.code,
        "scene_cuts_sec": sorted(set(cuts)),
    }
    snapshot = job.get("config_snapshot") or {}
    if banner.layers is not None and not (
        isinstance(snapshot.get("banner"), dict) and snapshot["banner"].get("layers")
    ):
        patch["banner_layers"] = banner.layers
    fields: dict[str, Any] = {"duration_sec": round(duration, 2), **banner.job_fields}
    ctx.progress(1.0)
    return StageResult(artifacts=artifacts, job_fields=fields, payload_patch=patch)
