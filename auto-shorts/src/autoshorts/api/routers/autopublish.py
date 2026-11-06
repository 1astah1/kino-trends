"""Автозалив через YouTube Data API (0019, решение владельца 2026-09-27). Логика — `services/autopublish.py`.

* `/channels/{channel_id}/oauth/youtube` — статус подключения, `…/start` — ссылка на экран согласия Google,
  `DELETE` — отключить (admin);
* `/oauth/youtube/callback` — сюда Google возвращает браузер; дальше 302 в карточку канала;
* `/packages/{package_id}/autopublish` — поставить в очередь (сейчас, на время или в ближайший слот)
  и снять с очереди;
* `/autopublish/queue` — очередь «Запланировано»: заливаются, ошибки, в слотах, залитые.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Path, Query, Request, Response
from fastapi.responses import RedirectResponse

from autoshorts.api.deps import AdminDep, OperatorDep, ViewerDep, audit_actor
from autoshorts.api.schemas.autopublish import (
    AutopublishQueue,
    AutopublishRequest,
    OAuthStart,
    YoutubeConnection,
)
from autoshorts.api.schemas.common import SLUG_PATTERN
from autoshorts.api.schemas.packages import UploadPackage
from autoshorts.domain.enums import AutopublishState

router = APIRouter(tags=["autopublish"])

ChannelIdPath = Annotated[str, Path(pattern=SLUG_PATTERN)]


def _connection() -> Any:
    from autoshorts.db.pool import connection

    return connection()


def _svc() -> Any:
    from autoshorts.services import autopublish

    return autopublish


def _package_out(conn: Any, package_id: int) -> dict[str, Any]:
    from autoshorts.services import packages

    row = packages.require(conn, package_id)
    return UploadPackage.model_validate(packages.package_view(conn, row)).model_dump(mode="json")


def _ui(path: str, **query: str) -> str:
    from autoshorts.core.config import get_env

    base = get_env().admin_base_url.rstrip("/")
    return f"{base}{path}?{urlencode(query)}" if query else f"{base}{path}"


@router.get(
    "/channels/{channel_id}/oauth/youtube",
    name="connection",
    response_model=YoutubeConnection,
    summary="Подключение канала к YouTube и план автозалива",
)
def connection_status(channel_id: ChannelIdPath, principal: ViewerDep):
    with _connection() as conn:
        return _svc().oauth_status(conn, channel_id)


@router.get(
    "/channels/{channel_id}/oauth/youtube/start",
    name="oauth_start",
    response_model=OAuthStart,
    summary="Ссылка на экран согласия Google (409 oauth_client_missing)",
)
def oauth_start(channel_id: ChannelIdPath, principal: OperatorDep):
    with _connection() as conn:
        return _svc().start_oauth(conn, channel_id, principal.user_id)


@router.delete(
    "/channels/{channel_id}/oauth/youtube",
    name="oauth_disconnect",
    status_code=204,
    response_class=Response,
    summary="Отключить канал от YouTube (токен удаляется)",
)
def oauth_disconnect(channel_id: ChannelIdPath, principal: AdminDep, request: Request):
    with _connection() as conn:
        _svc().disconnect(conn, channel_id, audit_actor(request, principal))
    return Response(status_code=204)


@router.get(
    "/oauth/youtube/callback",
    name="oauth_callback",
    response_class=RedirectResponse,
    status_code=302,
    summary="Колбэк Google OAuth → 302 в карточку канала",
)
def oauth_callback(
    request: Request,
    principal: OperatorDep,
    code: str | None = Query(default=None, max_length=2048),
    state: str | None = Query(default=None, max_length=4096),
    error: str | None = Query(default=None, max_length=200),
):
    from autoshorts.core.errors import AppError
    from autoshorts.services.settings import get_runtime_settings

    if error or not code or not state:
        return RedirectResponse(_ui("/channels/", oauth="denied", reason=error or "no_code"), status_code=302)
    try:
        done = _svc().finish_oauth(
            code, state, audit_actor(request, principal), settings=get_runtime_settings()
        )
    except AppError as exc:
        return RedirectResponse(
            _ui("/channels/", oauth="error", reason=exc.code, message=exc.message[:300]), status_code=302
        )
    return RedirectResponse(_ui("/channels/", oauth="ok", channel=done["channel_id"]), status_code=302)


@router.post(
    "/packages/{package_id}/autopublish",
    name="schedule",
    response_model=UploadPackage,
    summary="Автозалив пакета: сейчас, на время или в ближайший слот (409 autopublish_blocked)",
)
def schedule(package_id: int, body: AutopublishRequest, principal: OperatorDep, request: Request):
    with _connection() as conn:
        _svc().schedule(
            conn,
            package_id,
            audit_actor(request, principal),
            at=body.at,
            now_=body.now,
            privacy=str(body.privacy) if body.privacy else None,
        )
        return _package_out(conn, package_id)


@router.delete(
    "/packages/{package_id}/autopublish",
    name="cancel",
    response_model=UploadPackage,
    summary="Снять пакет с очереди автозалива (кроме идущей заливки)",
)
def cancel(package_id: int, principal: OperatorDep, request: Request):
    with _connection() as conn:
        _svc().cancel(conn, package_id, audit_actor(request, principal))
        return _package_out(conn, package_id)


@router.get(
    "/autopublish/queue",
    name="queue",
    response_model=AutopublishQueue,
    summary="Очередь автозалива: заливаются, ошибки, в слотах, залитые",
)
def queue(
    principal: ViewerDep,
    channel_id: str | None = Query(default=None, pattern=SLUG_PATTERN),
    state: list[AutopublishState] | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    with _connection() as conn:
        items = _svc().queue(
            conn, channel_id=channel_id, states=[str(s) for s in state] if state else None, limit=limit
        )
    return {"items": items, "generated_at": datetime.now().astimezone()}
