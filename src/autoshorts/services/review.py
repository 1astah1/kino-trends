"""Приёмка: решения, отмена решения, очередь, контекст экрана, карточки бота (§5.2, §5.4, §6.2).

    pending_review ──approve──► approved ──(package_build, M3-A)──► пакет ready
    pending_review | qc_failed ──rework(from_stage, auto_restart=true)──► processing
    pending_review | qc_failed ──rework(auto_restart=false)──► rework
    pending_review | qc_failed ──reject(reason)──► rejected
    qc_failed ──override_qc (admin, аудит)──► approved
    approved | rejected | rework (| processing после rework с auto_restart) ──undo ≤ 10 мин──►
        pending_review | qc_failed  (rework без перезапуска + отдельный «Старт» — 409 status_changed)

Решение — оптимистичный `UPDATE jobs … WHERE status = expected_status`: два одновременных
решения (веб и Telegram) дают ровно один успех и один 409 `job_state_conflict` с
`details.decided_by`, `details.decided_via` победителя. Веб и бот вызывают одни и те же функции
(`decide`, `undo`), различаются только `Caller.via`.

Что экран приёмки читает из ролика (контракт для стадий render/qc, M2-B и M5):
* `stage_payload.script` — `{hook_text, body_text, cta_text, voice_cta_text, voiceover_text, hook_pattern}`;
  слова — `stage_payload.words` или артефакт `words_json`;
* `stage_payload.banner_plan` — `pipeline.banner_overlay.BannerPlan.to_json()` рендера;
  виды слоёв — из `config_snapshot.banner.layers` (или `stage_payload.banner_layers`);
* `stage_payload.scene_cuts_sec` — склейки (иначе накопленные `scenes[].duration_sec`);
* `stage_payload.qc` — `{checks: [{code, ok, measured, threshold}]}`; провалы и предупреждения —
  `jobs.qc_failed_checks`, `jobs.qc_warnings` (`[{code, detail, job_id?, similarity?}]`);
* `stage_payload.forbidden_hits`, `stage_payload.tts_mock` / `voice.provider`,
  `stage_payload.watermark_suspect` — источники флагов.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from autoshorts.core.errors import Forbidden, JobStateConflict, NotFound, ValidationFailed
from autoshorts.domain.banner_schema import (
    BannerLayerKind,
    BannerValidationCode,
    BannerValidationWarning,
    LayerShow,
)
from autoshorts.domain.enums import (
    ActorKind,
    DecidedVia,
    JobKind,
    JobStage,
    JobStatus,
    Platform,
    ReviewDecision,
    Role,
    WarningSeverity,
)
from autoshorts.repos import jobs as jobs_repo
from autoshorts.repos import reviews as reviews_repo
from autoshorts.services import jobs as jobs_service
from autoshorts.services.audit import Actor
from autoshorts.services.jobs import Caller

log = logging.getLogger(__name__)

Row = dict[str, Any]

UNDO_WINDOW_MIN = 10
PACKAGE_TASK_KIND = "package_build"

ALLOWED_FROM: dict[str, tuple[str, ...]] = {
    ReviewDecision.APPROVE.value: (JobStatus.PENDING_REVIEW.value,),
    ReviewDecision.REWORK.value: (JobStatus.PENDING_REVIEW.value, JobStatus.QC_FAILED.value),
    ReviewDecision.REJECT.value: (JobStatus.PENDING_REVIEW.value, JobStatus.QC_FAILED.value),
    ReviewDecision.OVERRIDE_QC.value: (JobStatus.QC_FAILED.value,),
}
DECISION_LABELS = {
    "approve": "одобрено",
    "rework": "на доработку",
    "reject": "отклонено",
    "override_qc": "принято вопреки QC",
    "undo": "решение отменено",
}
BANNER_DEAD_ZONE_CODES = frozenset(
    {
        BannerValidationCode.CREATIVE_OUTSIDE_SAFE_ZONE.value,
        BannerValidationCode.CREATIVE_IN_BOTTOM_DEAD_ZONE.value,
        BannerValidationCode.MICRO_BRAND_IN_TOP_BAR.value,
        BannerValidationCode.LAYER_OUTSIDE_CANVAS.value,
    }
)
DURATION_TOLERANCE_SEC = 3.0


@dataclass(frozen=True)
class DecisionInput:
    decision: str
    expected_status: str
    reject_reason: str | None = None
    rework_from_stage: str | None = None
    auto_restart: bool = True
    comment: str | None = None

    @classmethod
    def from_request(cls, body: Any) -> DecisionInput:
        return cls(
            decision=str(body.decision),
            expected_status=str(body.expected_status),
            reject_reason=str(body.reject_reason) if body.reject_reason else None,
            rework_from_stage=str(body.rework_from_stage) if body.rework_from_stage else None,
            auto_restart=bool(body.auto_restart),
            comment=body.comment,
        )


def _invalid(field_name: str, message: str, kind: str) -> ValidationFailed:
    return ValidationFailed(
        message, details={"errors": [{"loc": ["body", field_name], "msg": message, "type": kind}]}
    )


def _decided_conflict(conn: Any, job_id: int, message: str | None = None, **extra: Any) -> JobStateConflict:
    """409 с тем, кто и откуда уже решил (для тоста «уже решено в Telegram»)."""
    job = jobs_repo.get(conn, job_id)
    last = reviews_repo.latest(conn, job_id)
    details: dict[str, Any] = {"job_id": job_id, "status": job["status"] if job else None}
    if last is not None:
        details.update(
            {
                "decision": last["decision"],
                "decided_by": last.get("decided_by_name"),
                "decided_by_id": last.get("decided_by"),
                "decided_via": last["decided_via"],
                "decided_at": last["created_at"].isoformat(),
            }
        )
    details.update(extra)
    return JobStateConflict(message or "Решение по ролику уже принято", details=details)


# ---------------------------------------------------------------- решение


def decide(conn: Any, job_id: int, inp: DecisionInput, caller: Caller) -> Row:
    """Решение по ролику. 404, 403 (override_qc не admin), 409 `job_state_conflict`, 422."""
    job = jobs_repo.get(conn, job_id)
    if job is None:
        raise NotFound("job", "Ролик не найден")
    decision = inp.decision
    if decision not in ALLOWED_FROM:
        raise _invalid("decision", "Неизвестное решение", "decision")
    if inp.expected_status not in ALLOWED_FROM[decision]:
        hint = (
            "Ролик с проваленным QC принимает только admin (override_qc)" if decision == "approve" else None
        )
        raise _invalid(
            "expected_status",
            hint or f"Решение {decision} невозможно из {inp.expected_status}",
            "decision_not_allowed",
        )
    if decision == ReviewDecision.REJECT.value and not inp.reject_reason:
        raise _invalid("reject_reason", "Укажите причину отклонения", "reject_reason_required")
    if decision == ReviewDecision.OVERRIDE_QC.value and not caller.at_least(Role.ADMIN):
        raise Forbidden("Принять вопреки QC может только admin", details={"required_role": "admin"})
    if not caller.at_least(Role.OPERATOR):
        raise Forbidden("Роль viewer — только просмотр", details={"required_role": "operator"})
    expected = (inp.expected_status,)
    stage = None
    fields: dict[str, Any] = {"review_comment": inp.comment}
    if decision in (ReviewDecision.APPROVE.value, ReviewDecision.OVERRIDE_QC.value):
        fields["status"] = JobStatus.APPROVED.value
    elif decision == ReviewDecision.REJECT.value:
        fields.update({"status": JobStatus.REJECTED.value, "reject_reason": inp.reject_reason})
    else:
        stage = inp.rework_from_stage or jobs_service.DEFAULT_REWORK_STAGE[job["kind"]]
        if stage not in jobs_service.stages_of(job["kind"]):
            raise _invalid(
                "rework_from_stage",
                f"Стадия {stage} не относится к ролику вида {job['kind']}",
                "stage_not_in_track",
            )
        fields.update(
            {
                "attempt_count": int(job["attempt_count"] or 0) + 1,
                "rework_from_stage": stage,
                "stage": stage,
                "status": JobStatus.PROCESSING.value if inp.auto_restart else JobStatus.REWORK.value,
                "review_sent_at": None,
                "error": None,
                "error_class": None,
            }
        )
    after = jobs_repo.update(conn, job_id, fields, allowed_statuses=expected)
    if after is None:
        raise _decided_conflict(conn, job_id)
    if decision == ReviewDecision.REWORK.value and inp.auto_restart:
        after = jobs_service.restart_tasks(conn, after, stage)  # type: ignore[arg-type]
    row = reviews_repo.insert(
        conn,
        job_id=job_id,
        decision=decision,
        decided_via=caller.via.value,
        decided_by=caller.user_id,
        reject_reason=inp.reject_reason,
        rework_from_stage=stage,
        comment=inp.comment,
    )
    if after["status"] == JobStatus.APPROVED.value:
        _enqueue_package(conn, after)
    jobs_service.audit_job(
        conn,
        caller,
        "job.review",
        after,
        before=job,
        summary=f"{after['video_uid']}: {DECISION_LABELS[decision]}",
        diff={
            "decision_id": int(row["id"]),
            "decision": decision,
            "via": caller.via.value,
            "reject_reason": inp.reject_reason,
            "rework_from_stage": stage,
            "auto_restart": inp.auto_restart if decision == ReviewDecision.REWORK.value else None,
        },
    )
    jobs_service.after_transition(conn, job, after)
    return after


def _enqueue_package(conn: Any, job: Row) -> None:
    """Сборка пакета к заливке (обработчик — M3-A `stages/package_build.py`); одна задача на попытку."""
    from autoshorts.services.tasks import enqueue

    enqueue(
        conn,
        PACKAGE_TASK_KIND,
        job_id=int(job["id"]),
        stage=JobStage.PACKAGE.value,
        payload={"job_id": int(job["id"])},
        dedup_key=f"job:{int(job['id'])}:package:{int(job['attempt_count'] or 0)}",
    )


# ---------------------------------------------------------------- отмена решения


def undo(conn: Any, job_id: int, caller: Caller) -> Row:
    """Отменить последнее решение ≤ 10 мин. 409, если пакет скачан, есть публикация, окно истекло
    или перезапущенная доработка уже пошла (`details.reason`)."""
    if not caller.at_least(Role.OPERATOR):
        raise Forbidden("Роль viewer — только просмотр", details={"required_role": "operator"})
    job = jobs_repo.get(conn, job_id, for_update=True)
    if job is None:
        raise NotFound("job", "Ролик не найден")
    last = reviews_repo.latest(conn, job_id)
    if last is None or last["decision"] == ReviewDecision.UNDO.value:
        raise _decided_conflict(conn, job_id, "Нечего отменять", reason="nothing_to_undo")
    decision = last["decision"]
    expected: tuple[str, ...]
    if decision in (ReviewDecision.APPROVE.value, ReviewDecision.OVERRIDE_QC.value):
        expected = (JobStatus.APPROVED.value,)
    elif decision == ReviewDecision.REJECT.value:
        expected = (JobStatus.REJECTED.value,)
    else:
        expected = (JobStatus.REWORK.value, JobStatus.PROCESSING.value)
    if job["status"] not in expected:
        reason = "published" if job["status"] == JobStatus.PUBLISHED.value else "status_changed"
        raise _decided_conflict(conn, job_id, "Статус ролика уже изменился", reason=reason)
    if not reviews_repo.is_within_minutes(conn, int(last["id"]), UNDO_WINDOW_MIN):
        raise _decided_conflict(
            conn, job_id, f"Отменить можно в течение {UNDO_WINDOW_MIN} минут", reason="undo_window_expired"
        )
    if jobs_repo.publications_for_job(conn, job_id):
        raise _decided_conflict(conn, job_id, "У ролика уже есть публикация", reason="published")
    packages = jobs_repo.packages_for_job(conn, job_id)
    if any(p["downloaded_at"] is not None or p["status"] in ("downloaded", "published") for p in packages):
        raise _decided_conflict(conn, job_id, "Пакет уже скачан", reason="package_downloaded")

    restarted = decision == ReviewDecision.REWORK.value and job["status"] == JobStatus.PROCESSING.value
    if restarted:
        since = jobs_repo.tasks_created_since(conn, job_id, last["created_at"])
        if any(t["status"] in ("running", "succeeded") for t in since):
            raise _decided_conflict(
                conn, job_id, "Доработка уже выполняется — отменить нельзя", reason="rework_in_progress"
            )
        # processing допустим, только если доработку перезапустило само решение (auto_restart): его
        # задача стадии создана в той же транзакции, `created_at` совпадает с временем решения, и
        # артефакты помечены удалёнными тем же временем. Rework без перезапуска + отдельный «Старт»
        # помечает артефакты временем старта — такое решение уже не откатить без потери превью.
        if not any(t["kind"] == "job_stage" and t["created_at"] == last["created_at"] for t in since):
            raise _decided_conflict(
                conn,
                job_id,
                "Ролик уже запустили заново после решения — отменить нельзя",
                reason="status_changed",
            )

    if decision == ReviewDecision.OVERRIDE_QC.value or (job.get("qc_failed_checks") or []):
        back = JobStatus.QC_FAILED.value
    else:
        back = JobStatus.PENDING_REVIEW.value
    fields: dict[str, Any] = {
        "status": back,
        "stage": JobStage.QC.value,
        "review_comment": None,
        "review_sent_at": None,
    }
    if decision == ReviewDecision.REJECT.value:
        fields["reject_reason"] = None
    if decision == ReviewDecision.REWORK.value:
        fields.update(
            {"attempt_count": max(int(job["attempt_count"] or 0) - 1, 0), "rework_from_stage": None}
        )
    after = jobs_repo.update(conn, job_id, fields, allowed_statuses=(job["status"],))
    if after is None:
        raise _decided_conflict(conn, job_id)
    cancelled: list[int] = []
    if restarted:
        cancelled = jobs_service.cancel_active_tasks(conn, job_id, kinds=["job_stage"])
        jobs_repo.restore_artifacts_deleted_at(conn, job_id, last["created_at"])
    if decision in (ReviewDecision.APPROVE.value, ReviewDecision.OVERRIDE_QC.value):
        cancelled += jobs_service.cancel_active_tasks(conn, job_id, kinds=[PACKAGE_TASK_KIND])
        jobs_repo.supersede_ready_packages(conn, job_id)
    row = reviews_repo.insert(
        conn,
        job_id=job_id,
        decision=ReviewDecision.UNDO.value,
        decided_via=caller.via.value,
        decided_by=caller.user_id,
        undoes_id=int(last["id"]),
    )
    jobs_service.audit_job(
        conn,
        caller,
        "job.review_undo",
        after,
        before=job,
        summary=f"{after['video_uid']}: отмена «{DECISION_LABELS.get(decision, decision)}»",
        diff={
            "decision_id": int(row["id"]),
            "undoes_id": int(last["id"]),
            "undone": decision,
            "via": caller.via.value,
            "tasks_cancelled": cancelled,
        },
    )
    jobs_service.after_transition(conn, job, after)
    return after


# ---------------------------------------------------------------- флаги


def _flag(code: str, severity: str, detail: str | None) -> dict[str, Any]:
    return {"code": code, "severity": severity, "detail": detail}


def _duration_range(job: Row) -> tuple[float, float] | None:
    snap = job.get("config_snapshot") or {}
    for raw in (
        (snap.get("options") or {}).get("duration_target_sec"),
        ((snap.get("niche") or {}).get("config") or {}).get("duration_target_sec"),
    ):
        if (
            isinstance(raw, list)
            and len(raw) == 2
            and all(isinstance(x, int | float) for x in raw)
            and raw[0] <= raw[1]
        ):
            return float(raw[0]), float(raw[1])
    return None


def banner_plan_of(job: Row) -> dict[str, Any] | None:
    plan = (job.get("stage_payload") or {}).get("banner_plan")
    return plan if isinstance(plan, dict) else None


def compute_flags(job: Row, settings: Any) -> list[dict[str, Any]]:
    """Флаги экрана приёмки и очереди (`ReviewFlagCode`) из итогов стадий."""
    flags: list[dict[str, Any]] = []
    payload = job.get("stage_payload") or {}
    failed = set(job.get("qc_failed_checks") or [])
    warn: dict[str, dict[str, Any]] = {}
    for w in job.get("qc_warnings") or []:
        if isinstance(w, dict) and isinstance(w.get("code"), str):
            warn.setdefault(w["code"], w)

    hits = payload.get("forbidden_hits")
    if "forbidden_phrase" in failed or (isinstance(hits, list) and hits):
        phrases = [str(h.get("phrase")) for h in hits or [] if isinstance(h, dict) and h.get("phrase")]
        flags.append(_flag("forbidden_phrase", "error", ", ".join(phrases[:5]) or None))

    duration = job.get("duration_sec")
    if duration is not None:
        d = float(duration)
        limit = jobs_service.PLATFORM_MAX_DURATION_SEC.get(job.get("channel_platform") or "")
        rng = _duration_range(job)
        if limit is not None and d > limit:
            flags.append(_flag("duration_dead_zone", "error", f"{d:.1f} с > {limit:g} с для площадки"))
        elif "duration" in failed:
            flags.append(_flag("duration_dead_zone", "error", f"{d:.1f} с"))
        elif rng is not None and not (rng[0] <= d <= rng[1] + DURATION_TOLERANCE_SEC):
            flags.append(_flag("duration_dead_zone", "warn", f"{d:.1f} с вне {rng[0]:g}–{rng[1]:g} с ниши"))

    plan = banner_plan_of(job)
    if plan is not None:
        bad = [
            w
            for w in plan.get("warnings") or []
            if isinstance(w, dict) and w.get("code") in BANNER_DEAD_ZONE_CODES
        ]
        if bad:
            severity = "error" if any(w.get("severity") == "error" for w in bad) else "warn"
            flags.append(_flag("banner_in_dead_zone", severity, ", ".join(sorted({w["code"] for w in bad}))))

    if "library_similarity" in failed or "library_similarity" in warn:
        w = warn.get("library_similarity") or {}
        detail = w.get("detail") or (
            f"похож на ролик {w['job_id']}" if isinstance(w.get("job_id"), int) else None
        )
        flags.append(
            _flag("library_similarity", "error" if "library_similarity" in failed else "warn", detail)
        )

    voice = payload.get("voice") if isinstance(payload.get("voice"), dict) else {}
    provider = str(voice.get("provider") or payload.get("tts_provider") or "")
    if payload.get("tts_mock") is True or provider.startswith("mock"):
        flags.append(_flag("tts_mock", "warn", provider or "mock"))

    share = job.get("author_share")
    if job.get("kind") == JobKind.CLIP.value and (share is not None or "author_share" in failed):
        minimum = float(getattr(settings, "clip_author_share_min", 0.30))
        if "author_share" in failed:
            flags.append(_flag("author_share_low", "error", f"{float(share or 0):.2f} < {minimum:.2f}"))
        elif share is not None and float(share) < minimum:
            flags.append(_flag("author_share_low", "warn", f"{float(share):.2f} < {minimum:.2f}"))

    if "foreign_watermark" in failed or "foreign_watermark" in warn or payload.get("watermark_suspect"):
        severity = "error" if "foreign_watermark" in failed else "warn"
        detail = (warn.get("foreign_watermark") or {}).get("detail")
        flags.append(_flag("foreign_watermark", severity, detail))
    return flags


# ---------------------------------------------------------------- очередь и контекст


def _settings(conn: Any) -> Any:
    from autoshorts.services.settings import get_runtime_settings

    return get_runtime_settings(conn)


def review_queue(conn: Any, *, channel_id: str | None = None) -> list[dict[str, Any]]:
    settings = _settings(conn)
    items = []
    for job in jobs_repo.review_queue(conn, channel_id=channel_id):
        media = jobs_service.media_urls(conn, int(job["id"]))
        items.append(
            {
                "job_id": int(job["id"]),
                "video_uid": job["video_uid"],
                "kind": job["kind"],
                "status": job["status"],
                "channel_id": job["channel_id"],
                "niche_id": job["niche_id"],
                "poster_url": media["poster_url"],
                "preview_url": media["preview_url"],
                "duration_sec": float(job["duration_sec"]) if job.get("duration_sec") is not None else None,
                "attempt_count": int(job["attempt_count"] or 0),
                "flags": compute_flags(job, settings),
                "ready_at": job.get("ready_at"),
            }
        )
    if any(i["kind"] == JobKind.CLIP.value for i in items):
        from autoshorts.services.clipping import attach_originality_verdicts

        attach_originality_verdicts(conn, items, id_key="job_id")  # нарезка (M5-B)
    return items


def _layer_kinds(job: Row) -> dict[str, dict[str, Any]]:
    snap = job.get("config_snapshot") or {}
    layers = (snap.get("banner") or {}).get("layers") if isinstance(snap.get("banner"), dict) else None
    if not isinstance(layers, list):
        layers = (job.get("stage_payload") or {}).get("banner_layers")
    out: dict[str, dict[str, Any]] = {}
    for layer in layers or []:
        if isinstance(layer, dict) and isinstance(layer.get("id"), str):
            out[layer["id"]] = layer
    return out


def _edges(raw: Any) -> dict[str, int] | None:
    if isinstance(raw, dict) and all(isinstance(raw.get(k), int | float) for k in ("x1", "y1", "x2", "y2")):
        return {k: int(raw[k]) for k in ("x1", "y1", "x2", "y2")}
    if isinstance(raw, list | tuple) and len(raw) == 4 and all(isinstance(v, int | float) for v in raw):
        return dict(zip(("x1", "y1", "x2", "y2"), (int(v) for v in raw), strict=True))
    return None


def banner_view(job: Row) -> dict[str, Any]:
    """`ReviewBanner` из `stage_payload.banner_plan` (BannerPlan.to_json() рендера)."""
    plan = banner_plan_of(job) or {}
    layers = _layer_kinds(job)
    kinds = {k.value for k in BannerLayerKind}
    groups = {g.value for g in LayerShow}
    windows = []
    raw_windows = plan.get("windows") if isinstance(plan.get("windows"), dict) else {}
    for group, items in raw_windows.items():
        if group not in groups or not isinstance(items, list):
            continue
        for w in items:
            if (
                isinstance(w, dict)
                and isinstance(w.get("start_sec"), int | float)
                and isinstance(w.get("end_sec"), int | float)
            ):
                windows.append(
                    {"group": group, "start_sec": float(w["start_sec"]), "end_sec": float(w["end_sec"])}
                )
    windows.sort(key=lambda w: (w["start_sec"], w["group"]))
    boxes = []
    shown: set[str] = set()
    raw_rects = plan.get("rects") if isinstance(plan.get("rects"), dict) else {}
    for layer_id, raw in raw_rects.items():
        edges = _edges(raw)
        layer = layers.get(layer_id) or {}
        kind = layer.get("kind") if layer.get("kind") in kinds else (layer_id if layer_id in kinds else None)
        if edges is None or kind is None:
            continue
        shown.add(layer_id)
        boxes.append({"layer_id": layer_id, "kind": kind, **edges})
    warnings = []
    for w in plan.get("warnings") or []:
        try:
            warnings.append(BannerValidationWarning.model_validate(w).model_dump(mode="json"))
        except ValueError:  # неизвестный код из старого плана не роняет экран
            continue
    creatives = [
        str(layers[i]["creative_sha256"])
        for i in shown
        if layers.get(i, {}).get("kind") == BannerLayerKind.CREATIVE.value
        and layers[i].get("creative_sha256")
    ]
    fallback = any(b["kind"] == BannerLayerKind.FALLBACK_PLATE.value for b in boxes) or any(
        w.get("code") == BannerValidationCode.FALLBACK_PLATE_USED.value for w in warnings
    )
    render = (job.get("stage_payload") or {}).get("clip_render")
    render = render if isinstance(render, dict) else {}
    layout = render.get("layout") if isinstance(render.get("layout"), dict) else {}
    overlap: list[str] = []
    for w in render.get("warnings") or []:
        if isinstance(w, str) and w.startswith("captions_overlap_banner"):
            _, _, zones = w.partition(":")
            overlap += [z for z in zones.split(",") if z]
    return {
        "variant_id": job.get("banner_variant_id"),
        "variant_code": job.get("variant_code"),
        "creative_sha256s": sorted(set(creatives)),
        "fallback_used": fallback,
        "windows": windows,
        "boxes": boxes,
        "captions_zone": _edges(plan.get("captions_zone")),
        "warnings": warnings,
        "layout_manual": bool(layout.get("manual")),
        "captions_overlap": sorted(set(overlap)),
    }


def _scene_cuts(job: Row) -> list[float]:
    payload = job.get("stage_payload") or {}
    for key in ("scene_cuts_sec", "scene_cuts"):
        raw = payload.get(key)
        if isinstance(raw, list) and all(isinstance(x, int | float) for x in raw):
            return [round(float(x), 3) for x in raw]
    scenes = payload.get("scenes")
    cuts: list[float] = []
    if isinstance(scenes, list):
        t = 0.0
        for s in scenes[:-1]:
            d = s.get("duration_sec") if isinstance(s, dict) else None
            if not isinstance(d, int | float):
                return []
            t += float(d)
            cuts.append(round(t, 3))
    return cuts


def _similarity(job: Row) -> dict[str, Any] | None:
    for w in job.get("qc_warnings") or []:
        if isinstance(w, dict) and w.get("code") == "library_similarity":
            score = w.get("similarity")
            return {
                "nearest_job_id": w.get("job_id") if isinstance(w.get("job_id"), int) else None,
                "score": float(score) if isinstance(score, int | float) else None,
                "method": "dhash",
            }
    raw = (job.get("stage_payload") or {}).get("similarity")
    if isinstance(raw, dict) and isinstance(raw.get("score"), int | float):
        nearest = raw.get("nearest_job_id")
        return {
            "nearest_job_id": nearest if isinstance(nearest, int) else None,
            "score": float(raw["score"]),
            "method": "dhash",
        }
    return None


def _previous_attempts(conn: Any, job: Row) -> list[dict[str, Any]]:
    """Прошлые решения: доработки/отклонения этого ролика и решения его предков (клонов)."""
    job_id = int(job["id"])
    ids = [job_id, *reviews_repo.ancestor_job_ids(conn, job_id)]
    rows = reviews_repo.list_for_jobs(conn, ids)
    undone = {int(r["undoes_id"]) for r in rows if r.get("undoes_id") is not None}
    out = []
    for r in rows:
        if r["decision"] == ReviewDecision.UNDO.value or int(r["id"]) in undone:
            continue
        if int(r["job_id"]) == job_id and r["decision"] not in ("rework", "reject"):
            continue
        out.append(
            {
                "job_id": int(r["job_id"]),
                "decision": r["decision"],
                "reject_reason": r.get("reject_reason"),
                "comment": r.get("comment"),
            }
        )
    out.reverse()
    return out


def review_context(conn: Any, job_id: int) -> dict[str, Any]:
    """Схема `ReviewContext` (§5.4) — всё для экрана приёмки одним запросом."""
    job = jobs_repo.get(conn, job_id)
    if job is None:
        raise NotFound("job", "Ролик не найден")
    settings = _settings(conn)
    payload = job.get("stage_payload") or {}
    script = payload.get("script") if isinstance(payload.get("script"), dict) else {}
    base_script = jobs_service.script_view(job) or {}
    media = jobs_service.media_urls(conn, job_id)
    qc = jobs_service.qc_view(job)
    hook_pattern = script.get("hook_pattern") or payload.get("hook_pattern")
    out = {
        "job": {
            "id": job_id,
            "video_uid": job["video_uid"],
            "kind": job["kind"],
            "status": job["status"],
            "channel_id": job["channel_id"],
            "niche_id": job["niche_id"],
            "product_id": job.get("product_id"),
            "banner_variant_id": job.get("banner_variant_id"),
            "attempt_count": int(job["attempt_count"] or 0),
        },
        "media": {
            **media,
            "duration_sec": float(job["duration_sec"]) if job.get("duration_sec") is not None else None,
        },
        "script": {
            "topic": job.get("topic")
            or (payload.get("topic") if isinstance(payload.get("topic"), str) else None),
            "hook_pattern": hook_pattern if isinstance(hook_pattern, str) else None,
            "voiceover_text": base_script.get("voiceover_text"),
            "cta_text": base_script.get("cta_text"),
            "words": jobs_service.words_of(conn, job),
        },
        "banner": banner_view(job),
        "scene_cuts_sec": _scene_cuts(job),
        "qc": {"passed": qc["passed"], "checks": qc["checks"]} if qc else None,
        "flags": compute_flags(job, settings),
        "similarity": _similarity(job),
        "cost_usd": float(job.get("cost_usd") or 0),
        "previous_attempts": _previous_attempts(conn, job),
    }
    if job["kind"] == JobKind.CLIP.value:
        from autoshorts.services.clipping import review_extras

        out.update(review_extras(conn, job))  # source, author_script, originality (M5-B)
    return out


# ---------------------------------------------------------------- Telegram: кто жмёт и какие карточки


def reviewer_for_tg(conn: Any, tg_user_id: int | None) -> Caller | None:
    """Caller для кнопок и команд бота: только активный пользователь с привязанным `tg_user_id`."""
    if tg_user_id is None:
        return None
    row = reviews_repo.reviewer_by_tg(conn, int(tg_user_id))
    if row is None:
        return None
    return Caller(
        actor=Actor(kind=ActorKind.BOT, user_id=int(row["id"])),
        user_id=int(row["id"]),
        role=Role(row["role"]),
        display_name=row["display_name"],
        via=DecidedVia.TELEGRAM,
    )


def linked_user_for_tg(conn: Any, tg_user_id: int) -> Row | None:
    """Пользователь, к которому привязан Telegram (для `/link` без аргументов)."""
    return reviews_repo.reviewer_by_tg(conn, int(tg_user_id))


@dataclass(frozen=True)
class ReviewCard:
    job_id: int
    video_uid: str
    status: str
    kind: str
    channel_id: str
    channel_title: str | None
    platform: str
    niche_id: str
    topic: str | None
    duration_sec: float | None
    attempt_count: int
    flags: list[dict[str, Any]]
    failed_checks: list[str]
    voiceover_text: str | None
    preview_path: str | None  # абсолютный путь к превью (для отправки файлом)


def card_for(conn: Any, job: Row, *, settings: Any | None = None) -> ReviewCard:
    from autoshorts.core.storage import get_layout
    from autoshorts.repos import artifacts as artifacts_repo

    preview = artifacts_repo.latest(conn, int(job["id"]), "preview")
    path = None
    if preview is not None:
        try:
            resolved = get_layout().resolve(preview["path"])
            path = str(resolved) if resolved.is_file() else None
        except ValueError:
            path = None
    script = jobs_service.script_view(job) or {}
    return ReviewCard(
        job_id=int(job["id"]),
        video_uid=job["video_uid"],
        status=job["status"],
        kind=job["kind"],
        channel_id=job["channel_id"],
        channel_title=job.get("channel_title"),
        platform=job.get("channel_platform") or Platform.YOUTUBE.value,
        niche_id=job["niche_id"],
        topic=job.get("topic"),
        duration_sec=float(job["duration_sec"]) if job.get("duration_sec") is not None else None,
        attempt_count=int(job["attempt_count"] or 0),
        flags=compute_flags(job, settings or _settings(conn)),
        failed_checks=[str(c) for c in job.get("qc_failed_checks") or []],
        voiceover_text=script.get("voiceover_text"),
        preview_path=path,
    )


def pending_cards(conn: Any, *, limit: int = 10) -> list[ReviewCard]:
    """Ролики на приёмке без отправленной карточки (бот отмечает `mark_card_sent` после отправки)."""
    settings = _settings(conn)
    return [card_for(conn, j, settings=settings) for j in jobs_repo.unsent_review_jobs(conn, limit=limit)]


def mark_card_sent(conn: Any, job_id: int) -> None:
    jobs_repo.set_review_sent(conn, job_id, None)


def job_card(conn: Any, job_id: int) -> ReviewCard:
    job = jobs_repo.get(conn, job_id)
    if job is None:
        raise NotFound("job", "Ролик не найден")
    return card_for(conn, job)


def severity_rank(severity: str) -> int:
    return {WarningSeverity.ERROR.value: 2, WarningSeverity.WARN.value: 1}.get(severity, 0)
