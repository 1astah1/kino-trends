"""Пакеты «К заливке» (§5.2, §5.4 UploadPackage, §7.3 «Пакеты»). Владелец — M3-A.

Контракт — `api/schemas/packages.py`. Заглушки контракта M5M3-0 отвечают 501; M3-A заменяет тела,
не меняя пути, имена и схемы. Пакет собирает стадия `package_build` (light, после approve):
тексты (LLM или шаблон), хэштеги ниши, закреп варианта, чек-лист, код; для нарезки — атрибуция
(чужие права) и итог отчёта об оригинальности. Файл отдаётся через `api/media.media_response`
(Range, в prod `X-Accel-Redirect`), латинское имя `<channel>_<date>_<code>.mp4`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import StreamingResponse

from autoshorts.api.deps import OperatorDep, ViewerDep, audit_actor
from autoshorts.api.schemas.common import (
    SLUG_PATTERN,
    LimitQuery,
    OffsetQuery,
    binary_responses,
    text_responses,
)
from autoshorts.api.schemas.packages import (
    ChecklistPatch,
    PackagePatch,
    PackageRebuild,
    UploadPackage,
    UploadPackagePage,
)
from autoshorts.domain.enums import PackageStatus, Platform

router = APIRouter(tags=["packages"])

SlugQuery = Annotated[str | None, Query(pattern=SLUG_PATTERN)]


def _connection() -> Any:
    from autoshorts.db.pool import connection

    return connection()


def _svc() -> Any:
    from autoshorts.services import packages

    return packages


def _out(conn: Any, row: dict[str, Any]) -> dict[str, Any]:
    return UploadPackage.model_validate(_svc().package_view(conn, row)).model_dump(mode="json")


@router.get("/packages", name="list", response_model=UploadPackagePage, summary="Очередь «К заливке»")
def list_packages(
    principal: ViewerDep,
    status: list[PackageStatus] | None = Query(default=None),
    channel_id: SlugQuery = None,
    platform: Platform | None = None,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
):
    with _connection() as conn:
        return _svc().list_packages(
            conn,
            statuses=[s.value for s in status] if status else None,
            channel_id=channel_id,
            platform=platform.value if platform else None,
            limit=limit,
            offset=offset,
        )


@router.get("/packages/{package_id}", name="get", response_model=UploadPackage, summary="Пакет")
def get_package(package_id: int, principal: ViewerDep):
    with _connection() as conn:
        return _out(conn, _svc().require(conn, package_id))


@router.get(
    "/jobs/{job_id}/package",
    name="job_package_get",
    response_model=UploadPackage,
    summary="Текущий пакет ролика (404 package_not_found)",
)
def get_job_package(job_id: int, principal: ViewerDep):
    with _connection() as conn:
        return _out(conn, _svc().job_package(conn, job_id))


@router.post(
    "/jobs/{job_id}/package",
    name="job_package_rebuild",
    response_model=UploadPackage,
    summary="Пересобрать пакет (409 package_not_ready)",
)
def rebuild_job_package(job_id: int, body: PackageRebuild, principal: OperatorDep, request: Request):
    from autoshorts.services.budget import TaskBudgetGate

    actor = audit_actor(request, principal)
    row = _svc().build_for_job(
        _connection, job_id, actor, regenerate=body.regenerate_texts, budget=TaskBudgetGate(job_id=job_id)
    )
    with _connection() as conn:
        return _out(conn, _svc().require(conn, int(row["id"])))


@router.patch(
    "/packages/{package_id}", name="update", response_model=UploadPackage, summary="Правка текстов пакета"
)
def update_package(package_id: int, body: PackagePatch, principal: OperatorDep, request: Request):
    changes = body.model_dump(exclude_unset=True)
    with _connection() as conn:
        row = _svc().patch_texts(conn, package_id, changes, audit_actor(request, principal))
        return _out(conn, row)


@router.patch(
    "/packages/{package_id}/checklist",
    name="checklist_update",
    response_model=UploadPackage,
    summary="Отметить пункт чек-листа",
)
def update_checklist(package_id: int, body: ChecklistPatch, principal: OperatorDep, request: Request):
    with _connection() as conn:
        row = _svc().set_checklist(
            conn, package_id, body.code.value, body.done, audit_actor(request, principal)
        )
        return _out(conn, row)


@router.post(
    "/packages/{package_id}/mark-uploaded",
    name="mark_uploaded",
    response_model=UploadPackage,
    summary="«Залил, ссылку вставлю позже»",
)
def mark_uploaded(package_id: int, principal: OperatorDep, request: Request):
    with _connection() as conn:
        return _out(conn, _svc().mark_uploaded(conn, package_id, audit_actor(request, principal)))


@router.get(
    "/packages/{package_id}/video",
    name="video",
    response_class=Response,
    responses=binary_responses("video/mp4", description="Финальный файл (attachment, Range)"),
    summary="Скачать ролик (ставит downloaded)",
)
def package_video(package_id: int, principal: ViewerDep):
    from autoshorts.api.media import media_response

    svc = _svc()
    with _connection() as conn:
        row = svc.mark_downloaded(conn, package_id, principal.user_id)
        filename = svc.file_base(row, svc.business_tz(conn)) + ".mp4"
        relpath = svc.video_relpath(row, conn)
    return media_response(relpath, filename=filename, download=True, media_type="video/mp4")


@router.get(
    "/packages/{package_id}/text",
    name="text",
    response_class=Response,
    responses=text_responses("text/plain", "Тексты пакета одним файлом"),
    summary="Тексты одним файлом",
)
def package_text(package_id: int, principal: ViewerDep):
    from autoshorts.api.media import content_disposition

    svc = _svc()
    with _connection() as conn:
        row = svc.require(conn, package_id)
        text = svc.text_file(conn, row)
        filename = svc.file_base(row, svc.business_tz(conn)) + ".txt"
    return Response(
        text.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": content_disposition(filename, download=False),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/packages/{package_id}/zip",
    name="zip",
    response_class=Response,
    responses=binary_responses("application/zip", description="Видео и тексты (ZIP_STORED)"),
    summary="Архив пакета",
)
def package_zip(package_id: int, principal: ViewerDep):
    from autoshorts.api.media import content_disposition, resolve_media_path

    svc = _svc()
    with _connection() as conn:
        row = svc.mark_downloaded(conn, package_id, principal.user_id)
        text = svc.text_file(conn, row)
        base = svc.file_base(row, svc.business_tz(conn))
        relpath = svc.video_relpath(row, conn)
    video = resolve_media_path(relpath)
    return StreamingResponse(
        svc.zip_stream(video, f"{base}.mp4", f"{base}.txt", text),
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition(f"{base}.zip", download=True),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
