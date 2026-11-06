"""Задачи очереди (docs/ARCHITECTURE.md §5.2 «Ролики, приёмка, задачи», §6.1). Владелец — M1-C.

Права (§5.1): чтение — viewer и выше, `retry`/`cancel` — operator и выше (CSRF проверяет
зависимость). Зависимости прав берутся из `autoshorts.api.deps.require(Role)` (M1-B); если слой
доступа не импортируется, эндпоинты отвечают 401 (закрыто по умолчанию). `require_viewer` /
`require_operator` / `require_admin` — объекты зависимостей этого модуля (их же используют
`workers.py` и `schedules.py`, их подменяют тесты через `app.dependency_overrides`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import mimetypes
import os
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from autoshorts.api.routers import EventStreamResponse
from autoshorts.api.schemas.common import LimitQuery, OffsetQuery, binary_responses, json_error_responses
from autoshorts.api.schemas.tasks import Task, TaskDetail, TaskPage
from autoshorts.core.errors import AppError, NotFound
from autoshorts.domain.enums import ResourceClass, TaskStatus

log = logging.getLogger(__name__)

LOG_TAIL_LINES = 200
LOG_STREAM_POLL_SEC = 0.5
LOG_STREAM_STATUS_SEC = 2.0
LOG_STREAM_PING_SEC = 15.0
LOG_STREAM_MAX_SEC = 3600.0


# ---------------------------------------------------------------- доступ


def _auth_unavailable() -> None:
    raise HTTPException(
        status_code=401,
        detail={
            "code": "not_authenticated",
            "message": "Нужен вход (слой доступа api/deps.py не подключён)",
            "details": {},
        },
    )


def _role_dependency(role: str) -> Callable[..., Any]:
    """Зависимость «роль не ниже `role`» (+ CSRF на небезопасных методах) из `api.deps.require`
    (M1-B). Если слоя доступа нет или он не импортируется — отказ 401 (закрыто по умолчанию)."""
    try:
        from autoshorts.api import deps
        from autoshorts.domain.enums import Role
    except Exception:  # noqa: BLE001 - любой сбой импорта слоя доступа = закрыто
        log.warning("autoshorts.api.deps is unavailable; task endpoints are closed", exc_info=True)
        return _auth_unavailable
    factory = getattr(deps, "require", None)
    if factory is None:
        log.warning("autoshorts.api.deps has no require(); task endpoints are closed")
        return _auth_unavailable
    return factory(Role(role))


require_viewer = _role_dependency("viewer")
require_operator = _role_dependency("operator")
require_admin = _role_dependency("admin")


# ---------------------------------------------------------------- помощники


@contextlib.contextmanager
def app_errors() -> Iterator[None]:
    """AppError → HTTPException с телом §5.1 (работает и до общего обработчика M1-B)."""
    try:
        yield
    except AppError as exc:
        raise HTTPException(
            status_code=exc.status, detail={"code": exc.code, "message": exc.message, "details": exc.details}
        ) from exc


def audit(
    conn: Any,
    request: Request,
    action: str,
    entity_type: str,
    entity_id: str | int | None,
    *,
    summary: str | None = None,
    diff: dict[str, Any] | None = None,
) -> None:
    """Запись в `audit_log` в той же транзакции (`services.audit`, M1-B). Нет слоя аудита — пропуск."""
    try:
        from autoshorts.api.deps import audit_actor
        from autoshorts.services.audit import record
    except ImportError:
        log.warning("audit layer unavailable, %s not recorded", action)
        return
    actor = audit_actor(request, getattr(request.state, "principal", None))
    record(conn, actor, action, entity_type, entity_id, summary=summary, diff=diff)


def task_out(row: dict[str, Any]) -> Task:
    data = dict(row)
    if data.get("progress") is not None:
        data["progress"] = float(data["progress"])
    data["payload"] = data.get("payload") or {}
    return Task.model_validate(data)


def _load_task(task_id: int) -> dict[str, Any]:
    from autoshorts.db.pool import connection
    from autoshorts.services import tasks as svc

    with app_errors(), connection() as conn:
        return svc.get_task(conn, task_id)


# ---------------------------------------------------------------- эндпоинты

router = APIRouter(tags=["tasks"])


@router.get(
    "/tasks", name="list", response_model=TaskPage, summary="Задачи", dependencies=[Depends(require_viewer)]
)
def list_tasks(
    status: list[TaskStatus] | None = Query(default=None),
    kind: list[str] | None = Query(default=None),
    resource_class: ResourceClass | None = None,
    job_id: int | None = None,
    source_id: int | None = None,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
):
    from autoshorts.db.pool import connection
    from autoshorts.services import tasks as svc

    with app_errors(), connection() as conn:
        rows, total = svc.list_tasks(
            conn,
            statuses=[s.value for s in status] if status else None,
            kinds=kind or None,
            resource_class=resource_class.value if resource_class else None,
            job_id=job_id,
            source_id=source_id,
            limit=limit,
            offset=offset,
        )
    return TaskPage(items=[task_out(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get(
    "/tasks/{task_id}",
    name="get",
    response_model=TaskDetail,
    summary="Задача и хвост лога",
    dependencies=[Depends(require_viewer)],
)
def get_task(task_id: int):
    from autoshorts.services import tasks as svc

    row = _load_task(task_id)
    return TaskDetail(task=task_out(row), log_tail=svc.task_log_tail(task_id, LOG_TAIL_LINES))


def _sse(event: str | None, data: str) -> str:
    lines = data.splitlines() or [""]
    head = f"event: {event}\n" if event else ""
    return head + "".join(f"data: {line}\n" for line in lines) + "\n"


async def _log_stream(task_id: int, *, start_lines: int = LOG_TAIL_LINES) -> AsyncIterator[str]:
    """Хвост лога, затем новые строки по мере записи; конец — когда задача завершена и файл дочитан."""
    from autoshorts.core.storage import tail_lines
    from autoshorts.services import tasks as svc

    path = svc.task_log_path(task_id)
    for line in await run_in_threadpool(tail_lines, path, start_lines):
        yield _sse(None, line)
    try:
        position = path.stat().st_size
    except OSError:
        position = 0
    buffer = b""
    loop = asyncio.get_running_loop()
    started = last_status = last_ping = loop.time()
    finished = False
    while True:
        await asyncio.sleep(LOG_STREAM_POLL_SEC)
        now = loop.time()
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size < position:  # файл пересоздан
            position, buffer = 0, b""
        if size > position:
            with open(path, "rb") as fh:
                fh.seek(position)
                chunk = fh.read(min(size - position, 1024 * 1024))
            position += len(chunk)
            buffer += chunk
            *complete, buffer = buffer.split(b"\n")
            for raw in complete:
                yield _sse(None, raw.decode("utf-8", errors="replace").rstrip("\r"))
            continue
        if finished:
            if buffer:
                yield _sse(None, buffer.decode("utf-8", errors="replace"))
            yield _sse("end", json.dumps({"task_id": task_id}))
            return
        if now - last_status >= LOG_STREAM_STATUS_SEC:
            last_status = now
            try:
                row = await run_in_threadpool(_load_task, task_id)
                finished = row["status"] in ("succeeded", "failed", "cancelled")
            except HTTPException:
                finished = True
        if now - last_ping >= LOG_STREAM_PING_SEC:
            last_ping = now
            yield _sse("ping", "{}")
        if now - started >= LOG_STREAM_MAX_SEC:
            return


@router.get(
    "/tasks/{task_id}/log/stream",
    name="log_stream",
    response_class=EventStreamResponse,
    responses={
        200: {"description": "Строки лога (SSE, `data:` = строка)", "content": {"text/event-stream": {}}},
        **json_error_responses(),
    },
    summary="Живой лог задачи (SSE)",
    dependencies=[Depends(require_viewer)],
)
def task_log_stream(task_id: int):
    _load_task(task_id)  # 404 до открытия потока
    return EventStreamResponse(
        _log_stream(task_id),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/tasks/{task_id}/result-file",
    name="result_file",
    response_class=Response,
    responses=binary_responses("application/octet-stream", "audio/mpeg", description="Файл-результат задачи"),
    summary="Файл-результат (voice-sample и т. п.)",
    dependencies=[Depends(require_viewer)],
)
def task_result_file(task_id: int):
    from autoshorts.core.config import get_env
    from autoshorts.core.storage import UnsafePathError, get_layout, x_accel_path

    row = _load_task(task_id)
    result = row.get("result") or {}
    rel = result.get("file") if isinstance(result, dict) else None
    with app_errors():
        if row["status"] != "succeeded" or not rel:
            raise NotFound("task_result", "У задачи нет файла-результата")
        try:
            path = get_layout().resolve(str(rel))
        except UnsafePathError as exc:
            raise NotFound("task_result", "Файл-результат недоступен") from exc
        if not path.is_file():
            raise NotFound("task_result", "Файл-результат удалён")
    media_type = str(
        result.get("media_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    )
    filename = str(result.get("filename") or path.name)
    if not filename.isascii() or any(c in filename for c in '"\\\r\n/'):
        filename = path.name
    disposition = f'attachment; filename="{filename}"'
    if get_env().is_prod:
        # nginx отдаёт файл сам (Range, sendfile), §5.1 / §8.5
        return Response(
            status_code=200,
            media_type=media_type,
            headers={
                "X-Accel-Redirect": x_accel_path(get_layout().relpath(path)),
                "Content-Disposition": disposition,
                "Cache-Control": "private, max-age=0",
            },
        )
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Content-Disposition": disposition, "Cache-Control": "private, max-age=0"},
        stat_result=os.stat(path),
    )


@router.post(
    "/tasks/{task_id}/retry",
    name="retry",
    response_model=Task,
    summary="Повторить задачу",
    dependencies=[Depends(require_operator)],
)
def retry_task(task_id: int, request: Request):
    from autoshorts.db.pool import connection
    from autoshorts.services import tasks as svc

    with app_errors(), connection() as conn:
        row = svc.retry_task(conn, task_id)
        audit(
            conn,
            request,
            "task.retry",
            "task",
            task_id,
            summary=f"{row['kind']} {row.get('stage') or ''}".strip(),
        )
        return task_out(row)


@router.post(
    "/tasks/{task_id}/cancel",
    name="cancel",
    response_model=Task,
    summary="Отменить задачу",
    dependencies=[Depends(require_operator)],
)
def cancel_task(task_id: int, request: Request):
    from autoshorts.db.pool import connection
    from autoshorts.services import tasks as svc

    with app_errors(), connection() as conn:
        row = svc.request_cancel(conn, task_id)
        audit(
            conn,
            request,
            "task.cancel",
            "task",
            task_id,
            summary=f"{row['kind']} {row.get('stage') or ''}".strip(),
            diff={"status": row["status"], "cancel_requested": row["cancel_requested"]},
        )
        return task_out(row)
