"""Задачи M3-A: сборка пакета «К заливке» (`package_build`) и проверка публикации (`publication_verify`).

Это не стадии ролика (`job_stage`), а отдельные виды задач (§6.1): модуль лежит в `stages/`, потому что
`tasks.registry.load_all()` импортирует его автообнаружением (§10.1 п. 2), и воркер берёт их задачи.

`package_build` (light, 3 попытки, приоритет 10) ставит приёмка после approve
(`services/review._enqueue_package`). Фазы:
1. чтение ролика и черновик текстов вне транзакции: LLM через бюджет задачи (`ctx.budget`,
   `generation_events.step_name='package_texts'`) или шаблон без LLM;
2. запись пакета — в транзакции успеха задачи (`ctx.on_commit`): код, описание с атрибуцией, чек-лист.
Ролик уже не одобрен (отмена решения) — задача тихо завершается; живой пакет на тот же финальный
файл уже есть — повтор ничего не делает. Отказ по правам или оригинальности — `PermanentError` и
алерт `package_blocked` (ролик одобрен, а пакета нет); удачная сборка алерт закрывает.

`publication_verify` (light, 3 попытки) — принадлежность ролика каналу: VK — по владельцу в ссылке,
YouTube — oEmbed (`services/publications.verify`).
"""

from __future__ import annotations

import logging
from typing import Any

from autoshorts.core.errors import AppError, PermanentError
from autoshorts.tasks.registry import TaskContext, register_task

log = logging.getLogger(__name__)

PACKAGE_TASK_KIND = "package_build"
VERIFY_TASK_KIND = "publication_verify"


def _connection() -> Any:
    from autoshorts.db.pool import connection

    return connection()


def _permanent(exc: AppError) -> PermanentError:
    reason = exc.details.get("reason") if isinstance(exc.details, dict) else None
    return PermanentError(f"{exc.code}: {exc.message}", error_class=str(reason or exc.code))


BLOCK_REASONS_RU = {
    "originality_fail": "отчёт об оригинальности провален (чужие права)",
    "originality_stale": "права источника поменялись после QC — нужен повторный QC",
    "originality_missing": "нет отчёта об оригинальности — нужен повторный QC",
    "erid_missing": "вариант требует маркировку рекламы, а erid не задан",
    "no_final": "нет финального файла",
    "rights_rejected": "права на источник не позволяют заливать",
}


def alert_key(job_id: int) -> str:
    return f"package_blocked:{int(job_id)}"


def open_blocked_alert(job: dict[str, Any], exc: AppError) -> None:
    """Одобренный ролик без пакета — алерт (колокольчик, «Пакеты → Проблемы»).

    Задача падает PermanentError, а ролик остаётся `approved`: без алерта след есть только в
    таймлайне Конвейера. Своя транзакция: транзакция задачи откатывается вместе с ошибкой.
    """
    from autoshorts.domain.enums import AlertCode, AlertSeverity
    from autoshorts.services.alerts import open_alert

    details = exc.details if isinstance(exc.details, dict) else {}
    reason = str(details.get("reason") or exc.code)
    job_id = int(job["id"])
    uid = str(job.get("video_uid") or job_id)
    try:
        with _connection() as conn:
            open_alert(
                conn,
                code=str(AlertCode.PACKAGE_BLOCKED),
                severity=AlertSeverity.WARNING,
                dedup_key=alert_key(job_id),
                title=f"Пакет не собран: {uid} — {BLOCK_REASONS_RU.get(reason, exc.message)}",
                details={
                    "job_id": job_id,
                    "video_uid": uid,
                    "reason": reason,
                    "error_code": exc.code,
                    "message": exc.message,
                    "fails": [str(f) for f in details.get("fails") or []],
                },
            )
    except Exception:  # noqa: BLE001 - алерт не должен менять исход задачи
        log.warning("job %s: package_blocked alert not opened", job_id, exc_info=True)


@register_task(PACKAGE_TASK_KIND, "light", max_attempts=3)
def package_build(ctx: TaskContext) -> dict[str, Any]:
    from autoshorts.repos import artifacts as artifacts_repo
    from autoshorts.repos import packages as packages_repo
    from autoshorts.services import packages as packages_service
    from autoshorts.services.audit import SYSTEM

    job_id = ctx.job_id or ctx.payload.get("job_id")
    if job_id is None:
        raise PermanentError("package_build without job_id", error_class="bad_payload")
    job_id = int(job_id)
    with _connection() as conn:
        try:
            inp = packages_service.gather(conn, job_id)
        except AppError as exc:
            raise _permanent(exc) from exc
        if inp.job["status"] != "approved":
            return {"job_id": job_id, "skipped": "job_not_approved", "status": inp.job["status"]}
        live = packages_repo.live_for_job(conn, job_id)
        final = artifacts_repo.latest(conn, job_id, "final")
        if live is not None and final is not None and int(live["final_artifact_id"]) == int(final["id"]):
            return {"job_id": job_id, "skipped": "package_exists", "package_id": int(live["id"])}
        try:
            packages_service.check_ready(conn, inp)
        except AppError as exc:
            blocked = exc
        else:
            blocked = None
    if blocked is not None:
        open_blocked_alert(inp.job, blocked)
        raise _permanent(blocked) from blocked
    ctx.check_cancelled()
    regenerate = not (inp.previous is not None and inp.previous.get("texts_mode") == "operator")
    draft = packages_service.draft_texts(inp, regenerate=regenerate, budget=ctx.budget)
    ctx.progress(0.8)

    def write(conn: Any, _task_row: dict[str, Any]) -> None:
        try:
            packages_service.write_package(conn, job_id, draft, SYSTEM, reason="build")
        except AppError as exc:
            raise _permanent(exc) from exc

    ctx.on_commit(write)
    return {"job_id": job_id, "texts_mode": draft.mode, "texts_note": draft.note, "cost_usd": draft.cost_usd}


@register_task(VERIFY_TASK_KIND, "light", max_attempts=3, priority=80)
def publication_verify(ctx: TaskContext) -> dict[str, Any]:
    from autoshorts.services import publications as publications_service

    pub_id = ctx.payload.get("publication_id")
    if pub_id is None:
        raise PermanentError("publication_verify without publication_id", error_class="bad_payload")
    return publications_service.verify_task(int(pub_id))


__all__ = ["PACKAGE_TASK_KIND", "VERIFY_TASK_KIND", "package_build", "publication_verify"]
