"""Стадия clip-job `qc` (heavy, §6.3.9): QC одним проходом + отчёт об оригинальности (OWNER-DECISIONS п. 5).

* `clipping.qc_clip.run_clip_qc` — длительность, тишина, громкость, клиппинг, чёрное, зависания
  (кроме `expected_static` стоп-кадров вставок);
* `clipping.originality.build_originality_report` — author_share (§6.3.8), голос против надписей,
  свой голос и текст, качество и повтор реплик, повтор момента источника (Jaccard по sid прошлых
  clip-job), похожесть на свою библиотеку (dHash роликов канала), чужой водяной знак, права;
* гейт: при чужих правах (`rights_type <> 'own'`, или для своих при
  `clip_author_share_gate_for_own`) провал отчёта → `qc_failed` с кодом `author_share` (или
  `library_similarity` для повтора момента, `forbidden_phrase`, `foreign_watermark`); для своего
  источника доля только показывается — ролик идёт на приёмку с пометкой в `qc_warnings`.

Отчёт пишется в `jobs.originality` (+ итог `originality_verdict`) и показывается на приёмке.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from autoshorts.core.errors import PermanentError
from autoshorts.stages.base import NewArtifact, StageContext, StageResult, input_hash, register_stage

# провал отчёта при чужих правах → код QC (словарь qc_check §4.3). `originality` — провал отчёта
# без своего кода QC (заглушка вместо текста, повтор своих реплик, права): в `QcCheck` его пока
# нет (запрос владельцу domain/enums), статус ролика всё равно qc_failed, причина — в отчёте.
ORIGINALITY_QC_CODE = "originality"
ORIGINALITY_FAIL_QC = {
    "author_share": "author_share",
    "voice_balance": "author_share",
    "author_text": ORIGINALITY_QC_CODE,
    "author_text_repetition": ORIGINALITY_QC_CODE,
    "repeat_moment": "library_similarity",
    "library_similarity": "library_similarity",
    "foreign_watermark": "foreign_watermark",
    "rights": ORIGINALITY_QC_CODE,
}


def qc_outcome(
    report: Any, qc: Any, *, gate_applies: bool, author_blocked: bool, min_share: float
) -> tuple[list[str], list[dict[str, Any]]]:
    """(провалы QC, предупреждения) по итогам прохода ffmpeg и отчёта об оригинальности."""
    failed = list(dict.fromkeys(qc.failed_checks))
    if gate_applies and report.verdict == "fail":
        for code in report.fails:
            mapped = ORIGINALITY_FAIL_QC.get(code)
            if code == "author_text" and author_blocked:
                mapped = "forbidden_phrase"
            if mapped and mapped not in failed:
                failed.append(mapped)
    warnings: list[dict[str, Any]] = []
    for c in qc.checks:
        if not c.ok and c.severity == "warn":
            warnings.append({"code": c.code, "detail": f"{c.measured}; {c.detail}".strip("; ")})
    share = report.summary.get("author_share")
    if not gate_applies and isinstance(share, int | float) and share + 1e-9 < min_share:
        warnings.append(
            {
                "code": "author_share",
                "detail": f"{share:.2f} < {min_share:.2f}: свой источник, гейт не применяется (пометка)",
            }
        )
    if not gate_applies and report.verdict == "fail":
        codes = ", ".join(report.fails)
        warnings.append(
            {"code": "author_share", "detail": f"отчёт об оригинальности: стоп ({codes}) — см. отчёт"}
        )
    return failed, warnings


@register_stage("clip", "qc", "heavy", 2, False)
def clip_qc(ctx: StageContext) -> StageResult:
    from autoshorts.clipping.edl import EDL
    from autoshorts.clipping.originality import build_originality_report, library_similarity_for
    from autoshorts.clipping.produce import originality_qc_checks
    from autoshorts.clipping.qc_clip import run_clip_qc
    from autoshorts.db.pool import connection
    from autoshorts.pipeline import similarity as sim
    from autoshorts.repos import artifacts as artifacts_repo
    from autoshorts.repos import clips as clips_repo
    from autoshorts.services import clipping

    job = ctx.job
    job_id = int(job["id"])
    payload = dict(job.get("stage_payload") or {})
    layout = clipping._layout()
    with connection() as conn:
        final_art = artifacts_repo.latest(conn, job_id, "final")
        edl_art = artifacts_repo.latest(conn, job_id, "edl_json")
        state = clips_repo.get_clip_state(conn, job_id) or {}
        src = clipping.require_source(conn, int(job["source_id"]))
        sctx = clipping.source_context(conn, src)
        tr = clipping.load_transcript(sctx)
        previous, texts, library = clipping.originality_history(
            conn, source_id=int(src["id"]), channel_id=job.get("channel_id"), job_id=job_id
        )
    if final_art is None or edl_art is None:
        raise PermanentError(
            "Нет финального файла или EDL: стадия render не прошла", error_class="render_missing"
        )
    final = layout.resolve(final_art["path"])
    edl = EDL.load(layout.resolve(edl_art["path"]))
    data = state.get("author_script")
    if not isinstance(data, dict):
        raise PermanentError("Нет авторского текста ролика", error_class="author_script_missing")
    author = clipping.author_result_from_dict(data)
    from autoshorts.clipping.author.script import is_unedited_template

    author_mode = author.mode
    if author_mode == "operator" and is_unedited_template(author.script):
        author_mode = "heuristic"  # шаблон без LLM, сохранённый как «текст оператора» без правки
    settings = ctx.settings
    min_share = float(settings.clip_author_share_min)
    with connection() as conn:
        prof = clipping.channel_profile(conn, job)
    require_voice = bool(prof.get("author_voice", True))
    if prof.get("author_share_min") is not None:
        # порог доли автора задаёт канал (канал без нейроозвучки: решение владельца, риск охвата — его)
        min_share = float(prof["author_share_min"])
    gate_for_own = bool(settings.clip_author_share_gate_for_own)
    rights = clipping.rights_of(sctx)
    cv = payload.get("clip_voice") if isinstance(payload.get("clip_voice"), dict) else {}
    kinds = list(cv.get("kinds") or (edl.meta or {}).get("voice_kinds") or [])
    ctx.check_cancelled()
    clipping.wait_memory_or_defer()
    share = edl.author_share()
    lib_sim, lib_note, fp = (None, "своих роликов канала для сравнения пока нет", None)
    if library:
        lib_sim, lib_note, fp = library_similarity_for(final, library)
    if fp is None:
        try:
            fp = sim.fingerprint_video(final)
        except Exception:  # noqa: BLE001 - без отпечатка просто не будет похожести в будущем
            fp = None
    ctx.progress(0.2)
    report = build_originality_report(
        share=share,
        rights=rights,
        rights_warnings=clipping.rights_warnings_of(sctx),
        watermark=clipping.watermark_of(sctx),
        edl=edl,
        transcript=tr,
        previous_clips=previous,
        library_similarity=lib_sim,
        library_note=lib_note,
        author_mode=author_mode,
        operator_edited=author_mode == "operator",
        script_issues=[i.to_dict() for i in author.issues],
        author_parts=author.script.vo_parts(),
        previous_author_texts=texts,
        voice_kinds=kinds,
        min_share=min_share,
        gate_for_own=gate_for_own,
        require_voice=require_voice,
    )
    extra = originality_qc_checks(report, share, author, min_share=min_share)
    wd = ctx.workdir / f"qc_{int(ctx.task['id'])}"
    wd.mkdir(parents=True, exist_ok=True)
    qc = run_clip_qc(
        final,
        expected_static=edl.expected_static,
        extra_checks=extra,
        workdir=wd,
        threads=int(getattr(settings, "ffmpeg_threads", 2) or 2),
        cancel=ctx.cancel,
    )
    ctx.progress(0.9)
    gate_applies = bool(report.summary.get("gate_applies"))
    failed, warnings = qc_outcome(
        report, qc, gate_applies=gate_applies, author_blocked=author.blocked, min_share=min_share
    )
    if lib_sim:
        top = max(lib_sim, key=lambda x: x[1])
        for w in warnings:
            if w["code"] == "library_similarity" and isinstance(top[0], int):
                w.update({"job_id": top[0], "similarity": round(float(top[1]), 4)})
    computed_at = datetime.now(UTC).isoformat(timespec="seconds")
    rep = {**report.to_dict(), "stage": "final", "computed_at": computed_at, "qc_failed_checks": failed}
    with connection() as conn:
        clips_repo.set_originality(conn, job_id, rep, report.verdict, guard_stage="qc")
    out = wd / "qc.json"
    out.write_text(
        json.dumps({"qc": qc.to_dict(), "originality": rep}, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8",
    )
    patch: dict[str, Any] = {
        "qc": {
            "checks": [
                {"code": c.code, "ok": c.ok, "measured": c.measured, "threshold": c.threshold}
                for c in qc.checks
            ],
            "stats": {
                k: v for k, v in qc.stats.items() if k in ("duration", "integrated_lufs", "true_peak_dbfs")
            },
        },
        "originality": {"verdict": report.verdict, "fails": report.fails, "warns": report.warns},
    }
    if lib_sim:
        top = max(lib_sim, key=lambda x: x[1])
        patch["similarity"] = {
            "score": round(float(top[1]), 4),
            "nearest_job_id": top[0] if isinstance(top[0], int) else None,
        }
    fields: dict[str, Any] = {
        "qc_failed_checks": failed,
        "qc_warnings": warnings,
        "author_share": round(float(share.value), 3),
        "duration_sec": round(float(qc.stats.get("duration") or edl.duration), 2),
    }
    if fp is not None:
        fields["fingerprint"] = fp.to_json()
    ctx.progress(1.0)
    return StageResult(
        artifacts=[NewArtifact("qc_report", out, input_hash(ctx.video_uid, "qc", int(ctx.task["id"])))],
        job_fields=fields,
        payload_patch=patch,
    )
