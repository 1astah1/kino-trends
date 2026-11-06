"""Ролики: создание, переходы статусов, стадии, чтение для API и бота (docs/ARCHITECTURE.md §5.2, §6.2).

Функции с `conn` работают в транзакции вызывающего (NOTIFY уходит на COMMIT):

    with connection() as conn:
        job = create_job(conn, spec_from_request(body), caller)
        return job_view(conn, job)

Статусы (§6.2):

    draft ──start──► queued ──(взятие первой задачи)──► processing
    processing ──(qc ok)──► pending_review          processing ──(qc fail)──► qc_failed
    processing ──(задача упала окончательно)──► failed ──retry(from_stage)──► processing
    rework ──start──► processing                    любой нетерминальный ──cancel──► cancelled
    approved ──(регистрация публикации)──► published

Решения приёмки (approve / rework / reject / override_qc / undo) — `services/review.py`.

Каждый переход — оптимистичный `UPDATE … WHERE status = ANY(allowed)`: ноль строк означает 409
`job_state_conflict` (веб и бот нажали одновременно). Переход публикует SSE `job_status` (и
`review_queue`, если изменилась очередь приёмки) и пишет аудит `job.<verb>` с `status_from/status_to`.

Стадии подключаются без правки этого модуля: `@register_stage("clip", "author_script", …)` в
`stages/<name>.py` (§5.5). Исполнитель задач `job_stage` — `run_stage_task(ctx)`: регистрирует его
модуль очереди (`register_task("job_stage", …)(run_stage_task)`), он переводит ролик
queued → processing, вызывает обработчик стадии и в транзакции успеха задачи фиксирует
`StageResult` (`commit_stage_result`: артефакты, патч ролика, следующая стадия или итог QC).
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from psycopg import errors as pg_errors

from autoshorts.core.errors import (
    AppError,
    BudgetPaused,
    Conflict,
    Forbidden,
    JobStateConflict,
    NotFound,
    PermanentError,
    ValidationFailed,
)
from autoshorts.db.notify import publish_event
from autoshorts.domain.enums import (
    CLIP_STAGES,
    GENERATE_STAGES,
    ArtifactKind,
    DecidedVia,
    JobKind,
    JobStage,
    JobStatus,
    Platform,
    QcCheck,
    Role,
)
from autoshorts.domain.events import EventType
from autoshorts.repos import artifacts as artifacts_repo
from autoshorts.repos import jobs as jobs_repo
from autoshorts.repos import reviews as reviews_repo
from autoshorts.services import audit
from autoshorts.services.audit import SYSTEM, Actor

if TYPE_CHECKING:
    from autoshorts.core.storage import MediaLayout
    from autoshorts.stages.base import StageResult
    from autoshorts.tasks.registry import TaskContext

log = logging.getLogger(__name__)

Row = dict[str, Any]

API_PREFIX = "/api/v1"
TEMPLATE_VERSION = "v1"
VIDEO_UID_BYTES = 6  # 12 hex-символов, как у legacy (`uuid4().hex[:12]`)
MAX_BATCH_TOPICS = 20
WORDS_FILE_MAX_BYTES = 4 * 1024 * 1024
SIMILAR_MIN_SCORE = 0.80
SIMILAR_LIMIT = 10
SIMILAR_LIBRARY = 500
CLIP_MIN_SEC = 3.0
CLIP_MAX_SEC = 180.0  # YouTube Shorts до 3 мин; VK — жёстко 60 с (ниже)
PLATFORM_MAX_DURATION_SEC = {Platform.YOUTUBE.value: 180.0, Platform.VK.value: 60.0}

STAGE_ORDER: dict[str, tuple[str, ...]] = {
    JobKind.GENERATE.value: tuple(s.value for s in GENERATE_STAGES),
    JobKind.CLIP.value: tuple(s.value for s in CLIP_STAGES),
}
# rework по умолчанию: генерация — с начала, нарезка — с авторского слоя (анализ кадра детерминирован)
DEFAULT_REWORK_STAGE = {
    JobKind.GENERATE.value: JobStage.SCRIPT.value,
    JobKind.CLIP.value: JobStage.AUTHOR_SCRIPT.value,
}

TERMINAL_STATUSES = (JobStatus.PUBLISHED.value, JobStatus.REJECTED.value, JobStatus.CANCELLED.value)
CANCELLABLE_STATUSES = tuple(s.value for s in JobStatus if s.value not in TERMINAL_STATUSES)
EDITABLE_STATUSES = (JobStatus.DRAFT.value, JobStatus.QUEUED.value, JobStatus.REWORK.value)
STARTABLE_STATUSES = (JobStatus.DRAFT.value, JobStatus.REWORK.value)
RETRYABLE_STATUSES = (JobStatus.FAILED.value,)
REVIEW_STATUSES = (JobStatus.PENDING_REVIEW.value, JobStatus.QC_FAILED.value)
ADAPTABLE_STATUSES = (JobStatus.APPROVED.value, JobStatus.PUBLISHED.value)
SCENE_REGEN_STATUSES = (
    JobStatus.PENDING_REVIEW.value,
    JobStatus.QC_FAILED.value,
    JobStatus.REWORK.value,
    JobStatus.FAILED.value,
)
# статус ролика, при котором кандидат нарезки считается свободным (его ролик не живой)
DEAD_JOB_STATUSES = (JobStatus.CANCELLED.value, JobStatus.REJECTED.value, JobStatus.FAILED.value)

# `job_fields` из StageResult, которые стадия может менять (§5.5)
STAGE_JOB_FIELDS = frozenset(
    {
        "duration_sec",
        "qc_failed_checks",
        "qc_warnings",
        "author_share",
        "fingerprint",
        "topic",
        "niche_version",
        "banner_variant_id",
        "campaign_id",
        "product_id",
    }
)
# `GET /jobs/{id}/media/{media_kind}` → (вид артефакта, idx) по порядку предпочтения
MEDIA_ARTIFACTS: dict[str, tuple[tuple[str, int], ...]] = {
    "preview": ((ArtifactKind.PREVIEW.value, 0),),
    "final": ((ArtifactKind.FINAL.value, 0),),
    "poster": ((ArtifactKind.POSTER.value, 0),),
    "voice": ((ArtifactKind.VOICE.value, 0), (ArtifactKind.AUTHOR_VOICE.value, 0)),
    "banner_impulse": ((ArtifactKind.BANNER_PNG.value, 0),),
    "banner_final": ((ArtifactKind.BANNER_PNG.value, 1),),
}
MEDIA_DEFAULT_TYPES = {
    "preview": "video/mp4",
    "final": "video/mp4",
    "poster": "image/jpeg",
    "voice": "audio/mpeg",
    "banner_impulse": "image/png",
    "banner_final": "image/png",
}

JOB_SORT_FIELDS: dict[str, str] = {
    "created_at": "created_at",
    "updated_at": "updated_at",
    "priority": "priority",
    "duration_sec": "duration_sec",
    "cost_usd": "cost_usd",
    "status": "status",
    "id": "id",
}


# ---------------------------------------------------------------- кто действует


@dataclass(frozen=True)
class Caller:
    """Кто меняет ролик: человек из веба/API, оператор из Telegram или система (воркер)."""

    actor: Actor
    user_id: int | None
    role: Role
    display_name: str | None = None
    via: DecidedVia = DecidedVia.WEB

    @classmethod
    def from_principal(cls, principal: Any, actor: Actor) -> Caller:
        via = DecidedVia.WEB if getattr(principal, "is_session", False) else DecidedVia.API
        return cls(
            actor=actor,
            user_id=principal.user_id,
            role=Role(principal.role),
            display_name=principal.display_name,
            via=via,
        )

    def at_least(self, role: Role) -> bool:
        order = {Role.VIEWER: 0, Role.OPERATOR: 1, Role.ADMIN: 2}
        return order[self.role] >= order[role]


SYSTEM_CALLER = Caller(actor=SYSTEM, user_id=None, role=Role.ADMIN, display_name="system", via=DecidedVia.API)


# ---------------------------------------------------------------- стадии


def stages_of(kind: str) -> tuple[str, ...]:
    return STAGE_ORDER[JobKind(kind).value]


def first_stage(kind: str, *, source_origin: str | None = None) -> str:
    """generate → script; clip → clip_fetch (источник по URL) или clip_analyze (загрузка)."""
    if JobKind(kind) == JobKind.CLIP:
        return JobStage.CLIP_FETCH.value if source_origin == "url" else JobStage.CLIP_ANALYZE.value
    return stages_of(kind)[0]


def next_stage(kind: str, stage: str) -> str | None:
    """Следующая стадия трека; после последней (qc) — None: ролик уходит на приёмку."""
    order = stages_of(kind)
    if stage not in order:
        return None
    i = order.index(stage)
    return order[i + 1] if i + 1 < len(order) else None


def stages_from(kind: str, stage: str) -> list[str]:
    """Стадия и все следующие за ней (артефакты этих стадий удаляются при rework)."""
    order = stages_of(kind)
    if stage not in order:
        return [stage]
    return list(order[order.index(stage) :])


def _check_stage_for_kind(kind: str, stage: str, field_name: str) -> str:
    if stage not in stages_of(kind):
        raise _invalid(
            field_name,
            f"Стадия {stage} не относится к ролику вида {kind}: {', '.join(stages_of(kind))}",
            "stage_not_in_track",
        )
    return stage


def _stage_or_none(stage: Any) -> str | None:
    return stage if stage in JobStage._value2member_map_ else None


# ---------------------------------------------------------------- ошибки и события


def _invalid(
    field_name: str, message: str, kind: str = "value_error", loc_root: str = "body"
) -> ValidationFailed:
    return ValidationFailed(
        message,
        details={"errors": [{"loc": [loc_root, *field_name.split(".")], "msg": message, "type": kind}]},
    )


def _conflict(job: Row, message: str | None = None, **details: Any) -> JobStateConflict:
    return JobStateConflict(message, details={"job_id": int(job["id"]), "status": job["status"], **details})


def _require_job(conn: Any, job_id: int, *, for_update: bool = False) -> Row:
    job = jobs_repo.get(conn, job_id, for_update=for_update)
    if job is None:
        raise NotFound("job", "Ролик не найден")
    return job


def publish_job_status(conn: Any, job: Row) -> None:
    publish_event(
        conn,
        EventType.JOB_STATUS,
        {"job_id": int(job["id"]), "status": job["status"], "stage": _stage_or_none(job.get("stage"))},
    )


def publish_review_queue(conn: Any) -> int:
    count = jobs_repo.review_queue_count(conn)
    publish_event(conn, EventType.REVIEW_QUEUE, {"count": count})
    return count


def after_transition(conn: Any, before: Row | None, after: Row) -> None:
    """SSE после смены статуса/стадии: job_status всегда, review_queue — если задета очередь."""
    publish_job_status(conn, after)
    was = before["status"] if before else None
    if was in REVIEW_STATUSES or after["status"] in REVIEW_STATUSES:
        publish_review_queue(conn)


def audit_job(
    conn: Any,
    caller: Caller,
    action: str,
    job: Row,
    *,
    before: Row | None = None,
    summary: str | None = None,
    diff: dict[str, Any] | None = None,
) -> None:
    data: dict[str, Any] = dict(diff or {})
    if before is not None and before["status"] != job["status"]:
        data.setdefault("status_from", before["status"])
        data.setdefault("status_to", job["status"])
    audit.record(
        conn,
        caller.actor,
        action,
        "job",
        int(job["id"]),
        summary=summary or f"{job['video_uid']}: {job['status']}",
        diff=data or None,
    )


def _budget_paused(conn: Any) -> bool:
    from autoshorts.services import budget as budget_service

    try:
        return bool(budget_service.get_budget(conn).get("paused"))
    except Exception:  # noqa: BLE001 - нет строки бюджета (пустая база) — не пауза
        log.warning("budget state unavailable", exc_info=True)
        return False


def _needs_paid_steps(kind: str, author_layer: dict[str, Any] | None) -> bool:
    """Будут ли платные шаги: генерация — всегда (LLM, картинки); нарезка — черновик LLM."""
    if kind == JobKind.GENERATE.value:
        return True
    mode = (author_layer or {}).get("mode", "llm_draft")
    return mode == "llm_draft"


def _check_budget(conn: Any, kind: str, author_layer: dict[str, Any] | None) -> None:
    if _needs_paid_steps(kind, author_layer) and _budget_paused(conn):
        raise BudgetPaused(details={"kind": kind})


# ---------------------------------------------------------------- создание


@dataclass
class JobSpec:
    """Что создать: поля ролика до проверок и снимков (из `JobCreate`, клона или бота)."""

    kind: str
    channel_id: str
    topic: str | None = None
    trend_brief_id: int | None = None
    campaign_id: int | None = None
    banner_variant_id: int | None = None
    scheduled_for: datetime | None = None
    priority: int = 100
    options: dict[str, Any] = field(default_factory=dict)
    start: bool = True
    # нарезка
    clip_candidate_id: int | None = None
    source_id: int | None = None
    start_sec: float | None = None
    end_sec: float | None = None
    author_layer: dict[str, Any] | None = None
    reframe_mode: str | None = None
    # служебное
    parent_job_id: int | None = None
    allow_used_candidate: bool = False
    niche_id: str | None = None  # adapt: ниша родителя
    stage_payload: dict[str, Any] = field(default_factory=dict)


def spec_from_request(body: Any) -> JobSpec:
    """`JobCreateGenerate | JobCreateClip` (или RootModel `JobCreate`) → JobSpec."""
    data = getattr(body, "root", body)
    kind = str(data.kind)
    if kind == JobKind.GENERATE.value:
        return JobSpec(
            kind=kind,
            channel_id=data.channel_id,
            topic=data.topic,
            trend_brief_id=data.trend_brief_id,
            campaign_id=data.campaign_id,
            banner_variant_id=data.banner_variant_id,
            scheduled_for=data.scheduled_for,
            priority=data.priority,
            options=data.options.model_dump(mode="json", exclude_none=True) if data.options else {},
            start=data.start,
        )
    src = data.source
    return JobSpec(
        kind=kind,
        channel_id=data.channel_id,
        campaign_id=data.campaign_id,
        banner_variant_id=data.banner_variant_id,
        priority=data.priority,
        start=data.start,
        clip_candidate_id=data.clip_candidate_id,
        source_id=src.source_id if src else None,
        start_sec=src.start_sec if src else None,
        end_sec=src.end_sec if src else None,
        author_layer=data.author_layer.model_dump(mode="json") if data.author_layer else None,
        reframe_mode=str(data.reframe.mode) if data.reframe else None,
    )


def _new_video_uid(conn: Any) -> str:
    for _ in range(10):
        uid = secrets.token_hex(VIDEO_UID_BYTES)
        if not jobs_repo.video_uid_exists(conn, uid):
            return uid
    raise RuntimeError("could not allocate a unique video_uid")


def _variant_snapshot(variant: Row) -> dict[str, Any]:
    return {
        "variant_id": int(variant["id"]),
        "campaign_id": int(variant["campaign_id"]),
        "code": variant["code"],
        "name": variant["name"],
        "version": variant["version"],
        "layers": variant["layers"] or [],
        "timing": variant["timing"] or {},
        "ad_marking": variant["ad_marking"] or {},
        "code_url_template": variant["code_url_template"],
        "voice_cta_text": variant["voice_cta_text"],
    }


def channel_default_variant(conn: Any, channel: Row) -> int | None:
    """Вариант баннера канала по умолчанию: `profile.banner_variant_id`, иначе — первого канала с ним."""
    profile = channel.get("profile") if isinstance(channel.get("profile"), dict) else {}
    candidates: list[Any] = [(profile or {}).get("banner_variant_id")]
    row = conn.execute(
        "SELECT profile->>'banner_variant_id' AS v FROM channels WHERE profile ? 'banner_variant_id'"
        " AND status NOT IN ('terminated', 'retired') ORDER BY created_at LIMIT 1"
    ).fetchone()
    candidates.append(row["v"] if row else None)
    for value in candidates:
        try:
            vid = int(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            continue
        if vid is None:
            continue
        variant = jobs_repo.get_variant(conn, vid)
        if variant is not None and variant["status"] != "archived":
            return vid
    return None


def _resolve_banner(
    conn: Any, *, campaign_id: int | None, banner_variant_id: int | None, channel: Row
) -> tuple[int | None, int | None, str | None, dict[str, Any] | None]:
    """(campaign_id, banner_variant_id, product_id, снимок варианта) по выбору оператора.

    Вариант без кампании — кампания берётся из варианта. Ничего не выбрано — продукт канала;
    вариант назначит `services.campaigns.assign_variant` (M2-A) до стадии render.
    """
    product_id = channel.get("default_product_id")
    snapshot = None
    if banner_variant_id is None and campaign_id is None and channel.get("id"):
        # оператор баннер не выбирал — баннер канала (profile.banner_variant_id), у нового канала без
        # своего — баннер первого канала, где он задан (решение 29.10: «на всех роликах наш баннер»)
        banner_variant_id = channel_default_variant(conn, channel)
    if banner_variant_id is not None:
        variant = jobs_repo.get_variant(conn, banner_variant_id)
        if variant is None:
            raise NotFound("banner_variant", "Вариант баннера не найден")
        if campaign_id is not None and int(variant["campaign_id"]) != int(campaign_id):
            raise _invalid("banner_variant_id", "Вариант не из этой кампании", "variant_campaign_mismatch")
        if variant["status"] == "archived":
            raise _invalid("banner_variant_id", "Вариант в архиве", "variant_archived")
        if variant["status"] != "active":
            # черновик или пауза не проходили проверку активации — ручной выбор проверяется здесь
            from autoshorts.services.campaigns import variant_errors

            errors = variant_errors(conn, int(banner_variant_id))
            if errors:
                labels = "; ".join(sorted({w.message or w.code.value for w in errors}))
                raise _invalid(
                    "banner_variant_id", f"У варианта есть ошибки баннера: {labels}", "variant_invalid"
                )
        campaign_id = int(variant["campaign_id"])
        product_id = variant["product_id"]
        snapshot = _variant_snapshot(variant)
    elif campaign_id is not None:
        campaign = jobs_repo.get_campaign(conn, campaign_id)
        if campaign is None:
            raise NotFound("campaign", "Кампания не найдена")
        product_id = campaign["product_id"]
    return campaign_id, banner_variant_id, product_id, snapshot


def _resolve_clip(conn: Any, spec: JobSpec, channel: Row) -> tuple[Row, dict[str, Any]]:
    """Источник и диапазон клипа (кандидат или ручной `source`), проверки прав и длины."""
    if (spec.clip_candidate_id is None) == (spec.source_id is None):
        raise _invalid("clip_candidate_id", "Нужен ровно один из clip_candidate_id или source", "clip_ref")
    fields: dict[str, Any] = {}
    if spec.clip_candidate_id is not None:
        cand = jobs_repo.get_candidate(conn, spec.clip_candidate_id, for_update=True)
        if cand is None:
            raise NotFound("clip_candidate", "Кандидат нарезки не найден")
        if cand["status"] == "dismissed":
            raise _invalid("clip_candidate_id", "Кандидат отброшен", "candidate_dismissed")
        if (
            not spec.allow_used_candidate
            and cand["job_id"] is not None
            and cand["job_status"] not in DEAD_JOB_STATUSES
        ):
            raise Conflict(
                "Кандидат уже в работе",
                code="clip_candidate_in_use",
                details={"clip_candidate_id": int(cand["id"]), "job_id": int(cand["job_id"])},
            )
        source_id = int(cand["source_id"])
        start = float(cand["start_sec"]) if spec.start_sec is None else float(spec.start_sec)
        end = float(cand["end_sec"]) if spec.end_sec is None else float(spec.end_sec)
        fields["clip_candidate_id"] = int(cand["id"])
        if spec.topic is None and cand.get("title"):
            fields["topic"] = cand["title"]
    else:
        source_id = int(spec.source_id)  # type: ignore[arg-type]
        start, end = float(spec.start_sec or 0.0), float(spec.end_sec or 0.0)
    source = jobs_repo.get_source(conn, source_id)
    if source is None or source.get("deleted_at") is not None:
        raise NotFound("source", "Источник не найден")
    if source["status"] != "ready":
        raise Conflict(
            "Источник ещё не готов к нарезке",
            code="source_not_ready",
            details={"source_id": source_id, "status": source["status"]},
        )
    if source.get("rights_source_id") is None:
        raise _invalid("source.source_id", "У источника не записаны права", "rights_required")
    valid_until = source.get("valid_until")
    if valid_until is not None and valid_until < datetime.now(UTC).date():
        raise _invalid("source.source_id", f"Права на источник истекли {valid_until}", "rights_expired")
    duration = end - start
    if duration < CLIP_MIN_SEC or duration > CLIP_MAX_SEC:
        raise _invalid(
            "source.end_sec",
            f"Длина клипа {duration:.1f} с вне {CLIP_MIN_SEC:g}–{CLIP_MAX_SEC:g} с",
            "clip_length",
        )
    limit = PLATFORM_MAX_DURATION_SEC.get(channel["platform"])
    if limit is not None and duration > limit:
        raise _invalid(
            "source.end_sec", f"Для {channel['platform']} клип не длиннее {limit:g} с", "clip_length_platform"
        )
    src_duration = source.get("duration_sec")
    if src_duration is not None and end > float(src_duration) + 0.05:
        raise _invalid("source.end_sec", "Конец клипа за пределами источника", "clip_out_of_source")
    fields.update(
        {
            "source_id": source_id,
            "clip_start_sec": round(start, 2),
            "clip_end_sec": round(end, 2),
            "reframe_mode": spec.reframe_mode or "auto",
            "author_layer": spec.author_layer or {"mode": "llm_draft", "voice": "tts", "captions": "both"},
        }
    )
    return source, fields


def create_job(conn: Any, spec: JobSpec, caller: Caller) -> Row:
    """Создать ролик (draft или queued с первой задачей). 404/409/422/423 — по §5.2."""
    kind = JobKind(spec.kind).value
    channel = jobs_repo.get_channel(conn, spec.channel_id)
    if channel is None:
        raise NotFound("channel", "Канал не найден")
    if channel["status"] in ("terminated", "retired"):
        raise _invalid("channel_id", "Канал выведен из работы", "channel_inactive")
    niche_id = spec.niche_id or channel["niche_id"]
    niche = jobs_repo.get_niche(conn, niche_id)
    if niche is None:
        raise NotFound("niche", "Ниша не найдена")
    if niche["status"] == "archived":
        raise _invalid("channel_id", "Ниша канала в архиве", "niche_archived")
    if spec.trend_brief_id is not None and jobs_repo.get_trend_brief(conn, spec.trend_brief_id) is None:
        raise NotFound("trend_brief", "Бриф не найден")
    campaign_id, variant_id, product_id, banner = _resolve_banner(
        conn, campaign_id=spec.campaign_id, banner_variant_id=spec.banner_variant_id, channel=channel
    )
    fields: dict[str, Any] = {}
    source: Row | None = None
    if kind == JobKind.CLIP.value:
        source, clip_fields = _resolve_clip(conn, spec, channel)
        fields.update(clip_fields)
    if spec.start:
        _check_budget(conn, kind, fields.get("author_layer"))

    snapshot: dict[str, Any] = {
        "niche": {"id": niche_id, "version": niche["current_version"], "config": niche["config"] or {}},
        "channel": {
            "id": channel["id"],
            "platform": channel["platform"],
            "profile": channel["profile"] or {},
        },
        "banner": banner,
        "options": spec.options or {},
    }
    fields.update(
        {
            "video_uid": _new_video_uid(conn),
            "kind": kind,
            "status": JobStatus.QUEUED.value if spec.start else JobStatus.DRAFT.value,
            "stage": None,
            "channel_id": channel["id"],
            "niche_id": niche_id,
            "niche_version": niche["current_version"],
            "product_id": product_id,
            "campaign_id": campaign_id,
            "banner_variant_id": variant_id,
            "trend_brief_id": spec.trend_brief_id,
            "template_version": TEMPLATE_VERSION,
            "config_snapshot": snapshot,
            "stage_payload": dict(spec.stage_payload or {}),
            "priority": int(spec.priority),
            "scheduled_for": spec.scheduled_for,
            "parent_job_id": spec.parent_job_id,
            "created_by": caller.user_id,
        }
    )
    if spec.topic is not None or "topic" not in fields:
        fields["topic"] = spec.topic
    try:
        job = jobs_repo.insert(conn, fields)
    except pg_errors.ForeignKeyViolation as exc:  # гонка с удалением справочника
        raise _invalid("channel_id", "Связанная запись не найдена", "foreign_key") from exc
    if job.get("clip_candidate_id") is not None and not spec.allow_used_candidate:
        jobs_repo.link_candidate(conn, int(job["clip_candidate_id"]), int(job["id"]))
    if kind == JobKind.CLIP.value:
        from autoshorts.services.clipping import on_clip_job_created

        on_clip_job_created(conn, job)  # sid клипа, авторский слой родителя (M5-B)
    if spec.start:
        stage = first_stage(kind, source_origin=source["origin"] if source else None)
        job = _enqueue_stage(conn, job, stage, run_after=spec.scheduled_for, set_status=None)
    audit_job(
        conn,
        caller,
        "job.create",
        job,
        summary=f"{job['video_uid']}: {kind} {channel['id']}",
        diff={"status_to": job["status"], "parent_job_id": spec.parent_job_id},
    )
    publish_job_status(conn, job)
    return job


def create_batch(conn: Any, base: JobSpec, topics: Sequence[str], caller: Caller) -> list[Row]:
    if not topics or len(topics) > MAX_BATCH_TOPICS:
        raise _invalid("topics", f"От 1 до {MAX_BATCH_TOPICS} тем", "batch_size")
    out: list[Row] = []
    for topic in topics:
        spec = JobSpec(**{**base.__dict__, "topic": topic.strip() or None})
        out.append(create_job(conn, spec, caller))
    return out


def _enqueue_stage(
    conn: Any,
    job: Row,
    stage: str,
    *,
    run_after: datetime | None = None,
    set_status: str | None = JobStatus.PROCESSING.value,
    payload: dict[str, Any] | None = None,
) -> Row:
    """Поставить задачу стадии (dedup `job:<id>:<stage>:<attempt_count>`) и записать стадию ролика."""
    from autoshorts.services.tasks import enqueue_job_stage

    enqueue_job_stage(
        conn,
        int(job["id"]),
        stage,
        job_kind=job["kind"],
        rev=int(job.get("attempt_count") or 0),
        payload=payload,
        run_after=run_after,
        priority=stage_task_priority(job, stage),
    )
    fields: dict[str, Any] = {"stage": stage}
    if set_status is not None:
        fields["status"] = set_status
    updated = jobs_repo.update(conn, int(job["id"]), fields)
    return updated or job


def stage_task_priority(job: Row, stage: str) -> int:
    """Приоритет задачи стадии: база §6.1 (render 30, script 50, …) + смещение от `jobs.priority`.

    `jobs.priority` 100 (по умолчанию) смещения не даёт; каждые 10 пунктов — ±1 к задаче
    (0 → −10, 1000 → +90). Меньше — раньше.
    """
    from autoshorts.stages.base import stage_task_defaults

    base = stage_task_defaults(stage, job["kind"]).priority
    offset = (int(job.get("priority") if job.get("priority") is not None else 100) - 100) // 10
    return max(0, min(base + offset, 32767))


def cancel_active_tasks(conn: Any, job_id: int, *, kinds: Sequence[str] | None = None) -> list[int]:
    from autoshorts.services.tasks import request_cancel

    done: list[int] = []
    for task in jobs_repo.active_tasks(conn, job_id, kinds=kinds):
        try:
            request_cancel(conn, int(task["id"]))
            done.append(int(task["id"]))
        except AppError:  # задача успела завершиться
            continue
    return done


# ---------------------------------------------------------------- управление


def patch_job(conn: Any, job_id: int, changes: dict[str, Any], caller: Caller) -> Row:
    """Правка до старта: только draft | queued | rework (§5.2). Иначе 409."""
    job = _require_job(conn, job_id, for_update=True)
    if job["status"] not in EDITABLE_STATUSES:
        raise _conflict(job, "Править можно только черновик, ролик в очереди или на доработке")
    fields: dict[str, Any] = {}
    merge: dict[str, Any] = {}
    drop: list[str] = []
    if "topic" in changes:
        fields["topic"] = changes["topic"]
    if "priority" in changes and changes["priority"] is not None:
        fields["priority"] = int(changes["priority"])
    if "script_override" in changes:
        if job["kind"] != JobKind.GENERATE.value:
            raise _invalid("script_override", "Сценарий задаётся только у генерации", "kind_mismatch")
        if changes["script_override"] is None:
            drop.append("script_override")
        else:
            merge["script_override"] = changes["script_override"]
    if "author_layer" in changes:
        if job["kind"] != JobKind.CLIP.value:
            raise _invalid("author_layer", "Авторский слой задаётся только у нарезки", "kind_mismatch")
        fields["author_layer"] = changes["author_layer"]
    if "banner_variant_id" in changes:
        channel = jobs_repo.get_channel(conn, job["channel_id"]) or {}
        campaign_id, variant_id, product_id, banner = _resolve_banner(
            conn,
            campaign_id=None,
            banner_variant_id=changes["banner_variant_id"],
            channel={"default_product_id": job.get("product_id") or channel.get("default_product_id")},
        )
        fields.update({"banner_variant_id": variant_id})
        if variant_id is not None:
            fields.update({"campaign_id": campaign_id, "product_id": product_id})
        snapshot = dict(job.get("config_snapshot") or {})
        snapshot["banner"] = banner
        fields["config_snapshot"] = snapshot
    if not fields and not merge and not drop:
        return job
    updated = jobs_repo.update(
        conn,
        job_id,
        fields,
        allowed_statuses=EDITABLE_STATUSES,
        merge_payload=merge or None,
        drop_payload_keys=drop,
    )
    if updated is None:
        raise _conflict(_require_job(conn, job_id))
    if "priority" in fields:
        for task in jobs_repo.active_tasks(conn, job_id, kinds=["job_stage"]):
            if task["status"] == "queued" and task.get("stage"):
                jobs_repo.set_task_priority(
                    conn, int(task["id"]), stage_task_priority(updated, task["stage"])
                )
    audit_job(
        conn,
        caller,
        "job.update",
        updated,
        summary=f"{updated['video_uid']}: правка до старта",
        diff={"fields": sorted({*fields, *merge, *drop} - {"config_snapshot"})},
    )
    return updated


def start_job(conn: Any, job_id: int, caller: Caller) -> Row:
    """draft → queued (первая стадия); rework → processing (со стадии rework_from_stage)."""
    job = _require_job(conn, job_id, for_update=True)
    if job["status"] not in STARTABLE_STATUSES:
        raise _conflict(job, "Запустить можно черновик или ролик на доработке")
    _check_budget(conn, job["kind"], job.get("author_layer"))
    if job["status"] == JobStatus.DRAFT.value:
        source = jobs_repo.get_source(conn, int(job["source_id"])) if job.get("source_id") else None
        stage = first_stage(job["kind"], source_origin=source["origin"] if source else None)
        moved = jobs_repo.update(
            conn, job_id, {"status": JobStatus.QUEUED.value}, allowed_statuses=(JobStatus.DRAFT.value,)
        )
        if moved is None:
            raise _conflict(_require_job(conn, job_id))
        after = _enqueue_stage(conn, moved, stage, run_after=job.get("scheduled_for"), set_status=None)
    else:
        stage = job.get("rework_from_stage") or job.get("stage") or DEFAULT_REWORK_STAGE[job["kind"]]
        after = restart_from_stage(conn, job, stage, allowed=(JobStatus.REWORK.value,))
    audit_job(conn, caller, "job.start", after, before=job)
    after_transition(conn, job, after)
    return after


def restart_from_stage(
    conn: Any,
    job: Row,
    stage: str,
    *,
    allowed: Sequence[str],
    fields: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> Row:
    """Перезапуск со стадии: артефакты стадий ≥ stage → deleted_at, ролик → processing, задача стадии.

    Оптимистично по `allowed`; ноль строк — 409. Вызывающий пишет аудит и события.
    """
    stage = _check_stage_for_kind(job["kind"], stage, "from_stage")
    changed = {
        "status": JobStatus.PROCESSING.value,
        "stage": stage,
        "error": None,
        "error_class": None,
        "review_sent_at": None,
        **(fields or {}),
    }
    moved = jobs_repo.update(conn, int(job["id"]), changed, allowed_statuses=allowed)
    if moved is None:
        raise _conflict(_require_job(conn, int(job["id"])))
    return restart_tasks(conn, moved, stage, payload=payload)


def restart_tasks(conn: Any, job: Row, stage: str, *, payload: dict[str, Any] | None = None) -> Row:
    """Ролик уже переведён в processing на `stage`: артефакты стадий ≥ stage → deleted_at
    (в той же транзакции: `deleted_at` = время решения, по нему undo их вернёт), задача стадии."""
    artifacts_repo.soft_delete_stages(conn, int(job["id"]), stages_from(job["kind"], stage))
    return _enqueue_stage(conn, job, stage, set_status=None, payload=payload)


def cancel_job(conn: Any, job_id: int, caller: Caller) -> Row:
    """Любой нетерминальный статус → cancelled; активные задачи ролика отменяются."""
    job = _require_job(conn, job_id, for_update=True)
    if job["status"] not in CANCELLABLE_STATUSES:
        raise _conflict(job, "Ролик уже в конечном статусе")
    after = jobs_repo.update(
        conn, job_id, {"status": JobStatus.CANCELLED.value}, allowed_statuses=CANCELLABLE_STATUSES
    )
    if after is None:
        raise _conflict(_require_job(conn, job_id))
    cancelled = cancel_active_tasks(conn, job_id)
    jobs_repo.supersede_ready_packages(conn, job_id, include_downloaded=True)
    audit_job(conn, caller, "job.cancel", after, before=job, diff={"tasks_cancelled": cancelled})
    after_transition(conn, job, after)
    return after


def retry_job(conn: Any, job_id: int, from_stage: str | None, caller: Caller) -> Row:
    """failed → processing с `from_stage` (по умолчанию — стадия, на которой упал)."""
    job = _require_job(conn, job_id, for_update=True)
    if job["status"] not in RETRYABLE_STATUSES:
        raise _conflict(job, "Перезапуск со стадии — только для упавшего ролика")
    stage = from_stage or job.get("stage") or first_stage(job["kind"])
    after = restart_from_stage(conn, job, stage, allowed=RETRYABLE_STATUSES)
    audit_job(conn, caller, "job.retry", after, before=job, diff={"from_stage": stage})
    after_transition(conn, job, after)
    return after


def clone_job(conn: Any, job_id: int, overrides: dict[str, Any], caller: Caller) -> Row:
    """Клон с правками: новый ролик с `parent_job_id` (по умолчанию черновик)."""
    parent = _require_job(conn, job_id)
    snapshot = parent.get("config_snapshot") or {}
    options = dict(snapshot.get("options") or {})
    if overrides.get("options"):
        options.update({k: v for k, v in overrides["options"].items() if v is not None})
    channel_id = overrides.get("channel_id") or parent["channel_id"]
    variant_id = (
        overrides["banner_variant_id"] if "banner_variant_id" in overrides else parent["banner_variant_id"]
    )
    campaign_id = overrides["campaign_id"] if "campaign_id" in overrides else parent["campaign_id"]
    if "banner_variant_id" in overrides and "campaign_id" not in overrides and variant_id is not None:
        campaign_id = None  # кампания возьмётся из нового варианта
    spec = JobSpec(
        kind=parent["kind"],
        channel_id=channel_id,
        topic=overrides["topic"] if overrides.get("topic") is not None else parent.get("topic"),
        trend_brief_id=parent.get("trend_brief_id"),
        campaign_id=campaign_id,
        banner_variant_id=variant_id,
        priority=int(overrides["priority"])
        if overrides.get("priority") is not None
        else int(parent["priority"]),
        options=options,
        start=bool(overrides.get("start", False)),
        parent_job_id=int(parent["id"]),
    )
    # клон живёт в нише родителя, а не в нише канала: иначе ролик из ниши vpn станет «кино»
    spec.niche_id = parent.get("niche_id") or spec.niche_id
    if parent["kind"] == JobKind.CLIP.value:
        # у клипа главнее ниша источника: так клон чинит ролик, которому досталась ниша канала
        from autoshorts.services.clipping import _source_niche

        spec.niche_id = _source_niche(conn, parent.get("source_id")) or spec.niche_id
        spec.clip_candidate_id = parent.get("clip_candidate_id")
        spec.source_id = None if spec.clip_candidate_id else parent.get("source_id")
        spec.start_sec = float(parent["clip_start_sec"]) if parent.get("clip_start_sec") is not None else None
        spec.end_sec = float(parent["clip_end_sec"]) if parent.get("clip_end_sec") is not None else None
        spec.author_layer = overrides.get("author_layer") or parent.get("author_layer")
        spec.reframe_mode = parent.get("reframe_mode")
        spec.allow_used_candidate = True
    elif overrides.get("author_layer"):
        raise _invalid("overrides.author_layer", "Авторский слой — только у нарезки", "kind_mismatch")
    return create_job(conn, spec, caller)


def adapt_job(conn: Any, job_id: int, channel_id: str, caller: Caller) -> Row:
    """Перерендер под другую площадку (VK): дочерний ролик сразу со стадии render (§6.2)."""
    parent = _require_job(conn, job_id)
    if parent["status"] not in ADAPTABLE_STATUSES:
        raise _conflict(parent, "Адаптировать можно одобренный или опубликованный ролик")
    target = jobs_repo.get_channel(conn, channel_id)
    if target is None:
        raise NotFound("channel", "Канал не найден")
    if target["platform"] == parent["channel_platform"]:
        raise AppError(
            "Адаптация нужна под другую площадку",
            code="platform_mismatch",
            status=422,
            details={"platform": target["platform"]},
        )
    payload = {
        k: v
        for k, v in (parent.get("stage_payload") or {}).items()
        if k not in ("banner_plan", "qc", "render", "fingerprint")
    }
    spec = JobSpec(
        kind=parent["kind"],
        channel_id=target["id"],
        topic=parent.get("topic"),
        trend_brief_id=parent.get("trend_brief_id"),
        campaign_id=parent.get("campaign_id"),
        banner_variant_id=parent.get("banner_variant_id"),
        priority=int(parent["priority"]),
        options=dict((parent.get("config_snapshot") or {}).get("options") or {}),
        start=False,
        parent_job_id=int(parent["id"]),
        niche_id=parent["niche_id"],
        stage_payload=payload,
    )
    if parent["kind"] == JobKind.CLIP.value:
        spec.clip_candidate_id = parent.get("clip_candidate_id")
        spec.source_id = None if spec.clip_candidate_id else parent.get("source_id")
        spec.start_sec = float(parent["clip_start_sec"]) if parent.get("clip_start_sec") is not None else None
        spec.end_sec = float(parent["clip_end_sec"]) if parent.get("clip_end_sec") is not None else None
        spec.author_layer = parent.get("author_layer")
        spec.reframe_mode = parent.get("reframe_mode")
        spec.allow_used_candidate = True
    child = create_job(conn, spec, caller)
    order = stages_of(parent["kind"])
    before_render = list(order[: order.index(JobStage.RENDER.value)])
    jobs_repo.copy_artifacts(conn, int(parent["id"]), int(child["id"]), before_render)
    after = _enqueue_stage(conn, child, JobStage.RENDER.value)
    audit_job(conn, caller, "job.adapt", after, before=child, diff={"parent_job_id": int(parent["id"])})
    publish_job_status(conn, after)
    return after


def regenerate_scene(conn: Any, job_id: int, scene_idx: int, prompt: str | None, caller: Caller) -> Row:
    """Перегенерация одной сцены (платно): ролик → processing со стадии visuals → render → qc."""
    job = _require_job(conn, job_id, for_update=True)
    if job["kind"] != JobKind.GENERATE.value:
        raise _invalid("scene_idx", "Сцены есть только у генерации", "kind_mismatch", loc_root="path")
    if job["status"] not in SCENE_REGEN_STATUSES:
        raise _conflict(job, "Сцену можно перегенерировать после рендера (приёмка, доработка, сбой)")
    scenes = scene_list(conn, job)
    if not any(s["idx"] == scene_idx for s in scenes):
        raise NotFound("scene", "Сцена не найдена")
    if _budget_paused(conn):
        raise BudgetPaused(details={"kind": job["kind"]})
    payload = {"scene_idx": int(scene_idx)}
    if prompt:
        payload["prompt"] = prompt
    after = restart_from_stage(
        conn,
        job,
        JobStage.VISUALS.value,
        allowed=SCENE_REGEN_STATUSES,
        payload=payload,
    )
    # сама картинка сцены остаётся до перегенерации: стадия visuals заменит только её
    conn.execute(
        """
        UPDATE artifacts SET deleted_at = NULL
        WHERE job_id = %(id)s AND kind = 'scene_image' AND stage = 'visuals' AND idx <> %(idx)s
          AND deleted_at = now()
        """,
        {"id": job_id, "idx": int(scene_idx)},
    )
    audit_job(conn, caller, "job.scene_regenerate", after, before=job, diff={"scene_idx": scene_idx})
    after_transition(conn, job, after)
    return after


def latest_stage_task_id(conn: Any, job_id: int, stage: str) -> int | None:
    for task in reversed(jobs_repo.tasks_for_job(conn, job_id)):
        if task["kind"] == "job_stage" and task["stage"] == stage:
            return int(task["id"])
    return None


# ---------------------------------------------------------------- стадии: исполнение и фиксация


def begin_stage(conn: Any, job_id: int, stage: str) -> Row | None:
    """Воркер взял задачу стадии: queued → processing. None — ролик не ждёт эту стадию (отменён,
    ушёл на доработку, перезапущен с другой стадии) — задачу надо завершить без работы."""
    job = jobs_repo.get(conn, job_id, for_update=True)
    if job is None:
        return None
    if job["status"] == JobStatus.QUEUED.value:
        after = jobs_repo.update(
            conn,
            job_id,
            {"status": JobStatus.PROCESSING.value, "stage": stage},
            allowed_statuses=(JobStatus.QUEUED.value,),
        )
        if after is not None:
            audit_job(conn, SYSTEM_CALLER, "job.processing", after, before=job)
            publish_job_status(conn, after)
        return after
    if job["status"] == JobStatus.PROCESSING.value and job.get("stage") == stage:
        return job
    return None


def _artifact_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _clean_stage_fields(raw: dict[str, Any]) -> dict[str, Any]:
    bad = set(raw) - STAGE_JOB_FIELDS
    if bad:
        raise PermanentError(
            f"stage returned unknown job_fields: {sorted(bad)}", error_class="bad_stage_result"
        )
    out = dict(raw)
    if "qc_failed_checks" in out:
        out["qc_failed_checks"] = [str(c) for c in out["qc_failed_checks"] or []]
    return out


def commit_stage_result(
    conn: Any, job_id: int, task_row: Row, result: StageResult, *, media: MediaLayout
) -> Row | None:
    """Фиксация стадии в транзакции успеха задачи (§6.1): артефакты, патч ролика, следующая стадия
    или итог QC (pending_review | qc_failed), события. Ролик уже не ждёт стадию — None (итог
    отброшен: решение приёмки или отмена опередили воркер)."""
    stage = str(task_row.get("stage"))
    job = jobs_repo.get(conn, job_id, for_update=True)
    if job is None or job["status"] != JobStatus.PROCESSING.value or job.get("stage") != stage:
        log.info(
            "job %s: stage %s result dropped (job is %s/%s)",
            job_id,
            stage,
            job["status"] if job else None,
            job.get("stage") if job else None,
        )
        return None
    for art in result.artifacts:
        path = Path(art.path)
        rel = media.relpath(path)
        size = _artifact_size(media.resolve(rel))
        artifacts_repo.insert(
            conn,
            kind=art.kind,
            path=rel,
            input_hash=art.input_hash,
            job_id=job_id,
            idx=art.idx,
            bytes_=size,
            stage=stage,
            is_master=art.is_master,
        )
    fields = _clean_stage_fields(result.job_fields)
    nxt = None if result.job_status else (result.next_stage or next_stage(job["kind"], stage))
    if nxt is not None:
        _check_stage_for_kind(job["kind"], nxt, "next_stage")
    if nxt is None:
        # последняя стадия (QC) — источник истины о провалах: не вернула список — провалов нет
        failed = list(fields.get("qc_failed_checks") or [])
        fields["qc_failed_checks"] = failed
        fields.setdefault("qc_warnings", [])
        status = result.job_status or (
            JobStatus.QC_FAILED.value if failed else JobStatus.PENDING_REVIEW.value
        )
        fields.update({"status": status, "review_sent_at": None, "error": None, "error_class": None})
    after = jobs_repo.update(conn, job_id, fields, merge_payload=result.payload_patch or None)
    assert after is not None
    if nxt is not None:
        after = _enqueue_stage(conn, after, nxt, set_status=None)
    else:
        audit_job(conn, SYSTEM_CALLER, "job.stage_done", after, before=job, diff={"stage": stage})
        if after["status"] == JobStatus.QC_FAILED.value and job["kind"] == JobKind.CLIP.value:
            # провалена только длина — ролик сам перерезается по целым фразам и рендерится заново
            from autoshorts.services import clipping

            fixed = clipping.autofix_qc(conn, after)
            if fixed is not None:
                audit_job(conn, SYSTEM_CALLER, "job.autofix", fixed, before=after, diff={"failed": after.get("qc_failed_checks")})
                after = fixed
    after_transition(conn, job, after)
    return after


def run_stage_task(ctx: TaskContext) -> dict[str, Any]:
    """Исполнитель задач `job_stage`: регистрируется модулем очереди (`tasks/*.py`) как

        register_task("job_stage", "light", max_attempts=3)(run_stage_task)

    Класс/попытки конкретной задачи берутся из колонок `tasks` (их ставит `enqueue_job_stage`).
    Провайдеры стадии — `ctx.extras["providers"]` (реестр M2-B), бюджет — `ctx.budget`.
    """
    from autoshorts.db.pool import connection
    from autoshorts.stages.base import StageContext, StageResult, get_stage

    job_id, stage = ctx.job_id, ctx.stage
    if job_id is None or not stage:
        raise PermanentError("job_stage task without job_id/stage", error_class="bad_task")
    with connection() as conn:
        job = begin_stage(conn, int(job_id), str(stage))
    if job is None:
        ctx.log.info("job %s no longer waits for stage %s, skipping", job_id, stage)
        return {"job_id": job_id, "stage": stage, "skipped": True}
    spec = get_stage(job["kind"], str(stage))
    if spec is None:
        raise PermanentError(
            f"Стадия {job['kind']}:{stage} не зарегистрирована", error_class="stage_not_registered"
        )
    workdir = ctx.media.job_dir(str(job["video_uid"]), create=True)
    stage_ctx = StageContext(
        job=dict(job),
        task=dict(ctx.task),
        workdir=workdir,
        settings=ctx.settings,
        providers=ctx.extras.get("providers"),
        budget=ctx.budget,
        cancel=ctx.cancel,
        progress=ctx.progress,
        log=ctx.log,
        log_path=ctx.log_path,
    )
    result = spec.handler(stage_ctx)
    if not isinstance(result, StageResult):
        raise PermanentError(
            f"stage {stage} returned {type(result).__name__}", error_class="bad_stage_result"
        )
    media = ctx.media
    ctx.on_commit(lambda conn, row: commit_stage_result(conn, int(job_id), row, result, media=media))
    return {"job_id": int(job_id), "stage": stage, "artifacts": len(result.artifacts)}


# ---------------------------------------------------------------- представления для API


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def media_url(job_id: int, kind: str) -> str:
    return f"{API_PREFIX}/jobs/{int(job_id)}/media/{kind}"


def media_urls(conn: Any, job_id: int) -> dict[str, str | None]:
    """Адреса медиа ролика с версией файла (`?v=<id артефакта>`): адрес без версии постоянный, а браузер
    держит его в кэше час — после перерендера в приёмке играл старый ролик (29.10)."""
    latest: dict[str, int] = {}
    for a in artifacts_repo.list_for_job(conn, job_id, kinds=["preview", "final", "poster"]):
        latest[a["kind"]] = max(latest.get(a["kind"], 0), int(a["id"]))

    def url(kind: str) -> str | None:
        return f"{media_url(job_id, kind)}?v={latest[kind]}" if kind in latest else None

    return {"preview_url": url("preview"), "final_url": url("final"), "poster_url": url("poster")}


def _valid_qc_code(code: Any) -> bool:
    return isinstance(code, str) and code in QcCheck._value2member_map_


def qc_view(job: Row) -> dict[str, Any] | None:
    payload = job.get("stage_payload") or {}
    qc = payload.get("qc") if isinstance(payload.get("qc"), dict) else {}
    checks = []
    for c in qc.get("checks") or []:
        if isinstance(c, dict) and _valid_qc_code(c.get("code")):
            checks.append(
                {
                    "code": c["code"],
                    "ok": bool(c.get("ok")),
                    "measured": None if c.get("measured") is None else str(c.get("measured")),
                    "threshold": None if c.get("threshold") is None else str(c.get("threshold")),
                }
            )
    failed = [c for c in job.get("qc_failed_checks") or [] if _valid_qc_code(c)]
    warnings = []
    for w in job.get("qc_warnings") or []:
        if isinstance(w, dict) and _valid_qc_code(w.get("code")):
            warnings.append(
                {
                    "code": w["code"],
                    "detail": None if w.get("detail") is None else str(w.get("detail")),
                    "job_id": w.get("job_id") if isinstance(w.get("job_id"), int) else None,
                    "similarity": _f(w.get("similarity"))
                    if isinstance(w.get("similarity"), int | float)
                    else None,
                }
            )
    post_qc = job["status"] in (*REVIEW_STATUSES, "approved", "published", "rejected")
    if not checks and not failed and not warnings and not post_qc:
        return None
    return {
        "passed": not failed and job["status"] != JobStatus.QC_FAILED.value,
        "failed_checks": failed,
        "checks": checks,
        "warnings": warnings,
    }


def script_view(job: Row) -> dict[str, Any] | None:
    payload = job.get("stage_payload") or {}
    script = payload.get("script") if isinstance(payload.get("script"), dict) else {}
    keys = ("hook_text", "body_text", "cta_text", "voice_cta_text", "voiceover_text")
    out = {k: script.get(k) if isinstance(script.get(k), str) else None for k in keys}
    if out["voiceover_text"] is None and isinstance(payload.get("voiceover_text"), str):
        out["voiceover_text"] = payload["voiceover_text"]  # legacy stage_payload
    if all(v is None for v in out.values()):
        return None
    return out


def _word(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    word = item.get("word", item.get("w"))
    start = item.get("start_sec", item.get("start", item.get("s")))
    end = item.get("end_sec", item.get("end", item.get("e")))
    if not isinstance(word, str) or not isinstance(start, int | float) or not isinstance(end, int | float):
        return None
    return {"word": word, "start_sec": float(start), "end_sec": float(end)}


def words_of(conn: Any, job: Row, media: MediaLayout | None = None) -> list[dict[str, Any]]:
    """Слова с таймингами: `stage_payload.words`, иначе артефакт `words_json` (список или `{words}`)."""
    payload = job.get("stage_payload") or {}
    raw: Any = payload.get("words")
    if not isinstance(raw, list):
        raw = None
        art = artifacts_repo.latest(conn, int(job["id"]), ArtifactKind.WORDS_JSON.value)
        if art is not None:
            try:
                from autoshorts.core.storage import get_layout

                path = (media or get_layout()).resolve(art["path"])
                if path.is_file() and path.stat().st_size <= WORDS_FILE_MAX_BYTES:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    raw = data.get("words") if isinstance(data, dict) else data
            except (OSError, ValueError):
                log.warning("job %s: words_json unreadable", job["id"], exc_info=True)
    out = [w for w in (_word(x) for x in raw or []) if w is not None]
    return out


def _stage_infos(tasks: Sequence[Row]) -> list[dict[str, Any]]:
    latest: dict[str, Row] = {}
    for t in tasks:
        if t["kind"] == "job_stage" and t["stage"] in JobStage._value2member_map_:
            latest[t["stage"]] = t
    out = []
    for stage, t in latest.items():
        out.append(
            {
                "stage": stage,
                "status": t["status"],
                "task_id": int(t["id"]),
                "attempts": int(t["attempts"]),
                "duration_ms": _duration_ms(t),
                "cost_usd": float(t.get("cost_usd") or 0),
                "error": t.get("last_error") if t["status"] in ("failed", "cancelled") else None,
            }
        )
    order = {s: i for i, s in enumerate(JobStage)}
    out.sort(key=lambda x: order.get(JobStage(x["stage"]), 99))
    return out


def _duration_ms(task: Row) -> int | None:
    s, e = task.get("started_at"), task.get("finished_at")
    if s is None or e is None:
        return None
    return max(int((e - s).total_seconds() * 1000), 0)


def review_decision_view(d: Row) -> dict[str, Any]:
    return {
        "id": int(d["id"]),
        "decision": d["decision"],
        "reject_reason": d.get("reject_reason"),
        "rework_from_stage": _stage_or_none(d.get("rework_from_stage")),
        "comment": d.get("comment"),
        "decided_by": d.get("decided_by"),
        "decided_by_name": d.get("decided_by_name"),
        "decided_via": d["decided_via"],
        "created_at": d["created_at"],
    }


def job_view(conn: Any, job: Row, *, include_words: bool = False) -> dict[str, Any]:
    """Схема `Job` (§5.4)."""
    job_id = int(job["id"])
    tasks = jobs_repo.tasks_for_job(conn, job_id)
    packages = jobs_repo.packages_for_job(conn, job_id)
    live_pkg = next((p for p in packages if p["status"] != "superseded"), None)
    source = None
    if job.get("source_id") is not None:
        src = jobs_repo.source_ref(conn, job)
        source = {
            "source_id": int(job["source_id"]),
            "title": src["title"] if src else "",
            "clip_candidate_id": job.get("clip_candidate_id"),
            "start_sec": _f(job.get("clip_start_sec")),
            "end_sec": _f(job.get("clip_end_sec")),
        }
    variant = None
    if job.get("banner_variant_id") is not None and job.get("variant_code") is not None:
        variant = {
            "id": int(job["banner_variant_id"]),
            "code": job["variant_code"],
            "name": job["variant_name"],
        }
    out = {
        "id": job_id,
        "video_uid": job["video_uid"],
        "kind": job["kind"],
        "status": job["status"],
        "stage": _stage_or_none(job.get("stage")),
        "channel": {
            "id": job["channel_id"],
            "title": job.get("channel_title"),
            "platform": job["channel_platform"],
        },
        "niche_id": job["niche_id"],
        "niche_version": job.get("niche_version"),
        "product_id": job.get("product_id"),
        "campaign_id": job.get("campaign_id"),
        "banner_variant": variant,
        "topic": job.get("topic"),
        "priority": int(job["priority"]),
        "scheduled_for": job.get("scheduled_for"),
        "duration_sec": _f(job.get("duration_sec")),
        "cost_usd": float(job.get("cost_usd") or 0),
        "author_share": _f(job.get("author_share")),
        "qc": qc_view(job),
        "script": script_view(job),
        "words": words_of(conn, job) if include_words else None,
        "stages": _stage_infos(tasks),
        "media": media_urls(conn, job_id),
        "reviews": [review_decision_view(d) for d in reviews_repo.list_for_job(conn, job_id)],
        "package": (
            {"id": int(live_pkg["id"]), "status": live_pkg["status"], "code": live_pkg.get("code") or ""}
            if live_pkg
            else None
        ),
        "publications": [
            {"id": int(p["id"]), "platform": p["platform"], "url": p.get("url"), "status": p["status"]}
            for p in jobs_repo.publications_for_job(conn, job_id)
        ],
        "source": source,
        "parent_job_id": job.get("parent_job_id"),
        "error_class": job.get("error_class"),
        "error": job.get("error"),
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }
    if job["kind"] == JobKind.CLIP.value:
        from autoshorts.services.clipping import job_view_extras

        out.update(job_view_extras(conn, job))  # author_script, originality (M5-B)
    return out


def job_row_view(job: Row, progress: float | None) -> dict[str, Any]:
    """Схема `JobRow` (строка списка)."""
    return {
        "id": int(job["id"]),
        "video_uid": job["video_uid"],
        "kind": job["kind"],
        "status": job["status"],
        "stage": _stage_or_none(job.get("stage")),
        "channel_id": job["channel_id"],
        "niche_id": job["niche_id"],
        "product_id": job.get("product_id"),
        "campaign_id": job.get("campaign_id"),
        "banner_variant_id": job.get("banner_variant_id"),
        "topic": job.get("topic"),
        "priority": int(job["priority"]),
        "progress": progress,
        "duration_sec": _f(job.get("duration_sec")),
        "cost_usd": float(job.get("cost_usd") or 0),
        "author_share": _f(job.get("author_share")),
        "error": job.get("error"),
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }


def list_jobs(
    conn: Any,
    *,
    kind: str | None = None,
    statuses: Sequence[str] | None = None,
    stages: Sequence[str] | None = None,
    channel_id: str | None = None,
    niche_id: str | None = None,
    product_id: str | None = None,
    campaign_id: int | None = None,
    banner_variant_id: int | None = None,
    source_id: int | None = None,
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    from autoshorts.api.pagination import order_by
    from autoshorts.services.settings import business_range, get_runtime_settings

    t_from, t_to = (None, None)
    if date_from or date_to:
        t_from, t_to = business_range(date_from, date_to, get_runtime_settings(conn).business_timezone)
    rows, total = jobs_repo.list_jobs(
        conn,
        kind=kind,
        statuses=statuses,
        stages=stages,
        channel_id=channel_id,
        niche_id=niche_id,
        product_id=product_id,
        campaign_id=campaign_id,
        banner_variant_id=banner_variant_id,
        source_id=source_id,
        q=q.strip() if q else None,
        created_from=t_from,
        created_to=t_to,
        order=order_by(sort, JOB_SORT_FIELDS, "-created_at"),
        limit=limit,
        offset=offset,
    )
    progress = jobs_repo.current_progress(conn, [int(r["id"]) for r in rows])
    items = [job_row_view(r, progress.get(int(r["id"]))) for r in rows]
    if any(i["kind"] == JobKind.CLIP.value for i in items):
        from autoshorts.services.clipping import attach_originality_verdicts

        attach_originality_verdicts(conn, items)  # originality_verdict нарезки (M5-B)
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def stage_counts(conn: Any) -> list[dict[str, Any]]:
    return [
        {"status": r["status"], "stage": _stage_or_none(r["stage"]), "count": int(r["count"])}
        for r in jobs_repo.stage_counts(conn)
        if r["status"] in JobStatus._value2member_map_
    ]


def get_job(conn: Any, job_id: int) -> Row:
    return _require_job(conn, job_id)


def task_timeline(conn: Any, job_id: int) -> list[dict[str, Any]]:
    _require_job(conn, job_id)
    out = []
    for t in jobs_repo.tasks_for_job(conn, job_id):
        out.append(
            {
                "stage": _stage_or_none(t.get("stage")),
                "status": t["status"],
                "task_id": int(t["id"]),
                "attempts": int(t["attempts"]),
                "started_at": t.get("started_at"),
                "finished_at": t.get("finished_at"),
                "duration_ms": _duration_ms(t),
                "cost_usd": float(t.get("cost_usd") or 0),
                "error": t.get("last_error"),
                "progress": _f(t.get("progress")),
            }
        )
    return out


def history(conn: Any, job_id: int) -> list[dict[str, Any]]:
    """Переходы, решения, задачи и правки ролика по времени (§5.2 `/jobs/{id}/history`)."""
    _require_job(conn, job_id)
    items: list[dict[str, Any]] = []
    for a in jobs_repo.audit_for_job(conn, job_id):
        diff = a.get("diff") or {}
        by = a.get("actor_name") or (a["actor_kind"] if a["actor_kind"] != "user" else None)
        if a["action"] == "job.review":
            continue  # решения — из review_decisions (там же канал и причина)
        if isinstance(diff, dict) and "status_to" in diff:
            items.append(
                {
                    "at": a["occurred_at"],
                    "kind": "status",
                    "from": diff.get("status_from"),
                    "to": diff.get("status_to"),
                    "by": by,
                    "via": "telegram" if a["actor_kind"] == "bot" else None,
                    "comment": a.get("summary"),
                }
            )
        elif a["action"] == "job.update":
            items.append(
                {
                    "at": a["occurred_at"],
                    "kind": "edit",
                    "from": None,
                    "to": None,
                    "by": by,
                    "via": None,
                    "comment": ", ".join(diff.get("fields") or [])
                    if isinstance(diff, dict)
                    else a.get("summary"),
                }
            )
    for d in reviews_repo.list_for_job(conn, job_id):
        items.append(
            {
                "at": d["created_at"],
                "kind": "review",
                "from": None,
                "to": d["decision"],
                "by": d.get("decided_by_name") or ("bot" if d["decided_via"] == "telegram" else None),
                "via": d["decided_via"],
                "comment": d.get("comment") or d.get("reject_reason"),
            }
        )
    for t in jobs_repo.tasks_for_job(conn, job_id):
        items.append(
            {
                "at": t.get("finished_at") or t.get("started_at") or t["created_at"],
                "kind": "task",
                "from": t.get("stage") or t["kind"],
                "to": t["status"],
                "by": "system",
                "via": None,
                "comment": t.get("error_class"),
            }
        )
    items.sort(key=lambda x: x["at"])
    return items


def similar_jobs(conn: Any, job_id: int) -> list[dict[str, Any]]:
    """Похожие свои ролики по dHash (`jobs.fingerprint`, §6.2 qc): топ-10 не ниже 0,80."""
    from autoshorts.pipeline.similarity import similarity

    job = _require_job(conn, job_id)
    fp = _fingerprint(job.get("fingerprint"))
    if fp is None:
        return []
    hits: list[tuple[Row, float]] = []
    for other in jobs_repo.fingerprints_for_similarity(conn, job_id, channel_id=None, limit=SIMILAR_LIBRARY):
        ofp = _fingerprint(other.get("fingerprint"))
        if ofp is None:
            continue
        score = similarity(fp, ofp)
        if score >= SIMILAR_MIN_SCORE:
            hits.append((other, score))
    hits.sort(key=lambda h: h[1], reverse=True)
    return [
        {"job_id": int(o["id"]), "video_uid": o["video_uid"], "similarity": s, "channel_id": o["channel_id"]}
        for o, s in hits[:SIMILAR_LIMIT]
    ]


def _fingerprint(raw: Any) -> Any:
    from autoshorts.pipeline.similarity import VideoFingerprint

    try:
        if isinstance(raw, dict) and raw.get("dhash"):
            return VideoFingerprint.from_json(raw)
        if isinstance(raw, list) and raw:
            return VideoFingerprint.from_hex([str(x) for x in raw])
    except (TypeError, ValueError):
        return None
    return None


def scene_list(conn: Any, job: Row) -> list[dict[str, Any]]:
    """Сцены генерации: `stage_payload.scenes` + картинки `scene_image` (idx = номер сцены)."""
    job_id = int(job["id"])
    payload = job.get("stage_payload") or {}
    raw = payload.get("scenes") if isinstance(payload.get("scenes"), list) else []
    images = {
        int(a["idx"])
        for a in artifacts_repo.list_for_job(conn, job_id, kinds=[ArtifactKind.SCENE_IMAGE.value])
    }
    indices = set(images) | set(range(len(raw)))
    out = []
    for idx in sorted(indices):
        item = raw[idx] if idx < len(raw) and isinstance(raw[idx], dict) else {}
        prompt = item.get("prompt_en") or item.get("prompt")
        seed = item.get("seed")
        duration = item.get("duration_sec")
        out.append(
            {
                "idx": idx,
                "prompt": prompt if isinstance(prompt, str) else None,
                "image_url": f"{API_PREFIX}/jobs/{job_id}/scenes/{idx}/image" if idx in images else None,
                "seed": seed if isinstance(seed, int) else None,
                "duration_sec": float(duration) if isinstance(duration, int | float) else None,
            }
        )
    return out


@dataclass(frozen=True)
class MediaFile:
    relpath: str
    filename: str
    media_type: str


def media_file(conn: Any, job_id: int, media_kind: str) -> MediaFile:
    job = _require_job(conn, job_id)
    for kind, idx in MEDIA_ARTIFACTS.get(media_kind, ()):
        art = artifacts_repo.latest(conn, job_id, kind, idx)
        if art is not None:
            ext = Path(art["path"]).suffix.lower() or ".bin"
            mtype = mimetypes.guess_type(f"x{ext}")[0] or MEDIA_DEFAULT_TYPES.get(
                media_kind, "application/octet-stream"
            )
            return MediaFile(art["path"], f"{job['video_uid']}_{media_kind}{ext}", mtype)
    raise NotFound("media", "Файла ещё нет")


def scene_image_file(conn: Any, job_id: int, scene_idx: int) -> MediaFile:
    job = _require_job(conn, job_id)
    art = artifacts_repo.latest(conn, job_id, ArtifactKind.SCENE_IMAGE.value, int(scene_idx))
    if art is None:
        raise NotFound("scene", "Картинки сцены нет")
    ext = Path(art["path"]).suffix.lower() or ".png"
    mtype = mimetypes.guess_type(f"x{ext}")[0] or "image/png"
    return MediaFile(art["path"], f"{job['video_uid']}_scene{int(scene_idx)}{ext}", mtype)


# ---------------------------------------------------------------- ID ролика на площадке (/videoid бота)

YOUTUBE_VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
VK_OWNER_VIDEO_RE = re.compile(r"-?\d{1,20}_\d{1,20}")
_VK_PATH_RE = re.compile(r"/(?:clip|video)(-?\d{1,20}_\d{1,20})(?:$|[/?#])")
_YT_HOSTS = frozenset({"youtube.com", "youtu.be", "youtube-nocookie.com"})
_VK_HOSTS = frozenset({"vk.com", "vk.ru", "vkvideo.ru"})
ATTRIBUTION_TAG_RE = re.compile(r"[A-Za-z0-9_-]{2,64}")


@dataclass(frozen=True)
class PlatformVideoRef:
    platform: str
    video_id: str
    url: str
    platform_kind: str

    @property
    def attribution_tag(self) -> str:
        return f"{'yt' if self.platform == Platform.YOUTUBE.value else 'vk'}_{self.video_id}"


def _platform_error(platform: str) -> ValidationFailed:
    fmt = (
        "11 символов [A-Za-z0-9_-] или ссылка youtube.com/shorts/…"
        if platform == Platform.YOUTUBE.value
        else "owner_video вида -123456_456239017 или ссылка vk.com/clip-…"
    )
    return ValidationFailed(
        f"Неверный ID ролика {platform}: нужен {fmt}",
        details={"errors": [{"loc": ["body", "platform_video_id"], "msg": fmt, "type": "platform_video_id"}]},
    )


def _host(netloc: str) -> str:
    host = netloc.lower().split("@")[-1].split(":")[0]
    for prefix in ("www.", "m.", "music."):
        if host.startswith(prefix):
            host = host[len(prefix) :]
    return host


def parse_platform_video_ref(platform: str, text: str) -> PlatformVideoRef:
    """ID ролика или ссылка → проверенный ID площадки канала (§3.3 п. 5: /videoid).

    YouTube — `^[A-Za-z0-9_-]{11}$`; VK — owner_video `-?\\d+_\\d+`. Ссылка на другую площадку —
    422 `platform_mismatch`, чужой хост — 422 `unsupported_url`, кириллица и мусор — 422.
    """
    platform = Platform(platform).value
    value = (text or "").strip()
    if not value or not value.isascii() or any(c.isspace() for c in value):
        raise _platform_error(platform)
    video_id: str | None = None
    if "/" in value or value.lower().startswith(("http:", "https:")):
        parts = urlsplit(value if "://" in value else f"https://{value}")
        host = _host(parts.netloc)
        detected = (
            Platform.YOUTUBE.value if host in _YT_HOSTS else Platform.VK.value if host in _VK_HOSTS else None
        )
        if detected is None:
            raise AppError(
                "Ссылка не на YouTube или VK", code="unsupported_url", status=422, details={"host": host}
            )
        if detected != platform:
            raise AppError(
                f"Ссылка на {detected}, а канал ролика — {platform}",
                code="platform_mismatch",
                status=422,
                details={"url_platform": detected, "channel_platform": platform},
            )
        if platform == Platform.YOUTUBE.value:
            segs = [s for s in parts.path.split("/") if s]
            if host == "youtu.be" and segs:
                video_id = segs[0]
            elif len(segs) >= 2 and segs[0] in ("shorts", "embed", "live", "v"):
                video_id = segs[1]
            else:
                video_id = (parse_qs(parts.query).get("v") or [None])[0]
        else:
            m = _VK_PATH_RE.search(parts.path + "/")
            if m is None:
                z = (parse_qs(parts.query).get("z") or [""])[0]
                m = _VK_PATH_RE.search("/" + z.split("/")[0] + "/")
            video_id = m.group(1) if m else None
    else:
        video_id = value
        if platform == Platform.VK.value:
            for prefix in ("clip", "video"):
                if video_id.startswith(prefix):
                    video_id = video_id[len(prefix) :]
    if platform == Platform.YOUTUBE.value:
        if not video_id or not YOUTUBE_VIDEO_ID_RE.fullmatch(video_id):
            raise _platform_error(platform)
        return PlatformVideoRef(platform, video_id, f"https://www.youtube.com/shorts/{video_id}", "short")
    if not video_id or not VK_OWNER_VIDEO_RE.fullmatch(video_id):
        raise _platform_error(platform)
    return PlatformVideoRef(platform, video_id, f"https://vk.com/clip{video_id}", "clip")


def find_job_ref(conn: Any, ref: str) -> Row:
    """Ролик по `video_uid`, коду пакета или числовому id."""
    value = (ref or "").strip()
    if not value or not value.isascii():
        raise NotFound("job", "Ролик не найден")
    job = jobs_repo.get_by_video_uid(conn, value) or jobs_repo.get_by_package_code(conn, value)
    if job is None and value.isdigit():
        job = jobs_repo.get(conn, int(value))
    if job is None:
        raise NotFound("job", "Ролик не найден")
    return job


def bind_platform_video(conn: Any, job_ref: str, raw_video: str, caller: Caller) -> dict[str, Any]:
    """Регистрация ручной заливки по ID/ссылке (мост `/videoid` бота до `services.publications`, M3-A).

    Площадка берётся из канала ролика; формат ID проверяется. Ролик approved → published,
    живой пакет → published. 409 `publication_exists` — ID уже привязан или у ролика уже есть
    публикация на этой площадке.

    Есть живой пакет → регистрация идёт через `services.publications.register` (как в вебе: запрет
    кросспоста, мастер в masters/, алиас yt_/vk_, проверка принадлежности). Нарезку без живого пакета
    мост не публикует: атрибуция и гейт оригинальности живут в пакете (409 `package_not_ready`).
    """
    found = find_job_ref(conn, job_ref)
    job = _require_job(conn, int(found["id"]), for_update=True)
    platform = job["channel_platform"]
    ref = parse_platform_video_ref(platform, raw_video)
    if job["status"] != JobStatus.APPROVED.value:
        if job["status"] == JobStatus.PUBLISHED.value:
            raise Conflict(
                "Ролик уже опубликован", code="publication_exists", details={"job_id": int(job["id"])}
            )
        raise _conflict(job, "Привязать ID можно только одобренному ролику")
    existing = jobs_repo.find_publication(conn, platform, ref.video_id)
    if existing is not None or any(
        p["platform"] == platform for p in jobs_repo.publications_for_job(conn, int(job["id"]))
    ):
        raise Conflict(
            "Этот ролик площадки уже привязан",
            code="publication_exists",
            details={"publication_id": existing["id"] if existing else None, "job_id": int(job["id"])},
        )
    tag = ref.attribution_tag
    if not ATTRIBUTION_TAG_RE.fullmatch(tag):
        raise _platform_error(platform)
    final = artifacts_repo.latest(conn, int(job["id"]), ArtifactKind.FINAL.value)
    live_pkg = next(
        (
            p
            for p in jobs_repo.packages_for_job(conn, int(job["id"]))
            if p["status"] in ("ready", "downloaded")
        ),
        None,
    )
    if live_pkg is not None:
        from autoshorts.services import publications as publications_service

        pub, _links = publications_service.register(conn, int(live_pkg["id"]), ref.url, None, caller)
        return {
            "job": _require_job(conn, int(job["id"])),
            "publication_id": int(pub["id"]),
            "platform": platform,
            "platform_video_id": ref.video_id,
            "url": ref.url,
            "attribution_tag": pub.get("attribution_tag") or tag,
        }
    if job.get("kind") == "clip":
        raise Conflict(
            "Сначала соберите пакет: для нарезки в нём атрибуция и проверка оригинальности",
            code="package_not_ready",
            details={"reason": "no_live_package", "job_id": int(job["id"])},
        )
    try:
        with conn.transaction():
            pub = jobs_repo.insert_publication(
                conn,
                {
                    "job_id": int(job["id"]),
                    "channel_id": job["channel_id"],
                    "platform": platform,
                    "platform_video_id": ref.video_id,
                    "platform_kind": ref.platform_kind,
                    "url": ref.url,
                    "canonical_url": ref.url,
                    "master_file_path": final["path"] if final else "",
                    "attribution_tag": tag,
                    "status": "live",
                    "verify_status": "pending",
                    "product_id": job.get("product_id"),
                    "campaign_id": job.get("campaign_id"),
                    "banner_variant_id": job.get("banner_variant_id"),
                    "registered_by": caller.user_id,
                    "package_id": int(live_pkg["id"]) if live_pkg else None,
                    "published_at": datetime.now(UTC),
                },
            )
    except pg_errors.UniqueViolation as exc:
        raise Conflict("Этот ролик площадки уже привязан", code="publication_exists") from exc
    after = jobs_repo.update(
        conn,
        int(job["id"]),
        {"status": JobStatus.PUBLISHED.value},
        allowed_statuses=(JobStatus.APPROVED.value,),
    )
    if after is None:
        raise _conflict(_require_job(conn, int(job["id"])))
    jobs_repo.mark_packages_published(conn, int(job["id"]))
    audit_job(
        conn,
        caller,
        "job.publish",
        after,
        before=job,
        summary=f"{after['video_uid']}: {platform} {ref.video_id}",
        diff={"publication_id": int(pub["id"]), "platform": platform, "platform_video_id": ref.video_id},
    )
    after_transition(conn, job, after)
    return {
        "job": after,
        "publication_id": int(pub["id"]),
        "platform": platform,
        "platform_video_id": ref.video_id,
        "url": ref.url,
        "attribution_tag": tag,
    }


# ---------------------------------------------------------------- проверки прав в сервисе


def require_role(caller: Caller, role: Role) -> None:
    if not caller.at_least(role):
        raise Forbidden("Недостаточно прав", details={"required_role": str(role)})


__all__ = [
    "Caller",
    "JobSpec",
    "MediaFile",
    "PlatformVideoRef",
    "SYSTEM_CALLER",
    "adapt_job",
    "begin_stage",
    "bind_platform_video",
    "cancel_job",
    "clone_job",
    "commit_stage_result",
    "create_batch",
    "create_job",
    "first_stage",
    "job_view",
    "list_jobs",
    "next_stage",
    "parse_platform_video_ref",
    "patch_job",
    "regenerate_scene",
    "restart_from_stage",
    "restart_tasks",
    "retry_job",
    "run_stage_task",
    "spec_from_request",
    "start_job",
]
