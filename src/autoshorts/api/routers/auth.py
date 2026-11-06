"""Auth: вход, выход, текущий пользователь, сессии (docs/ARCHITECTURE.md §5.1, §5.2). Владелец — M1-B.

Cookie: `as_session` (HttpOnly, SameSite=Lax, Secure в prod, 30 дней) и `as_csrf` (читается из JS,
для double-submit `X-CSRF-Token`). `GET /auth/me` обновляет `as_csrf`, чтобы cookie и токен
сессии не расходились.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from autoshorts.api.deps import SessionDep, ViewerDep, audit_actor, client_ip, request_id
from autoshorts.api.schemas.auth import AuthState, ChangePasswordRequest, LoginRequest, SessionList
from autoshorts.core.config import get_env
from autoshorts.services import auth as auth_service
from autoshorts.services.auth import Principal

router = APIRouter(tags=["auth"])

COOKIE_MAX_AGE = auth_service.SESSION_ABSOLUTE_DAYS * 24 * 3600


def set_auth_cookies(response: Response, session_token: str | None, csrf_token: str) -> None:
    secure = get_env().cookie_secure
    if session_token is not None:
        response.set_cookie(
            auth_service.SESSION_COOKIE,
            session_token,
            max_age=COOKIE_MAX_AGE,
            path="/",
            secure=secure,
            httponly=True,
            samesite="lax",
        )
    response.set_cookie(
        auth_service.CSRF_COOKIE,
        csrf_token,
        max_age=COOKIE_MAX_AGE,
        path="/",
        secure=secure,
        httponly=False,
        samesite="lax",
    )


def clear_auth_cookies(response: Response) -> None:
    secure = get_env().cookie_secure
    for name, httponly in ((auth_service.SESSION_COOKIE, True), (auth_service.CSRF_COOKIE, False)):
        response.delete_cookie(name, path="/", secure=secure, httponly=httponly, samesite="lax")


def _auth_state(principal: Principal) -> dict:
    return {"user": principal.user_dict(), "csrf_token": principal.csrf_token or ""}


@router.post(
    "/auth/login", name="login", response_model=AuthState, summary="Вход (ставит cookie as_session, as_csrf)"
)
def login(body: LoginRequest, request: Request, response: Response):
    result = auth_service.login(
        body.login,
        body.password,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        request_id=request_id(request),
    )
    set_auth_cookies(response, result.session_token, result.csrf_token)
    return _auth_state(result.principal)


@router.post("/auth/logout", name="logout", status_code=204, response_class=Response, summary="Выход")
def logout(principal: SessionDep, request: Request):
    auth_service.logout(principal, audit_actor(request, principal))
    response = Response(status_code=204)
    clear_auth_cookies(response)
    return response


@router.get("/auth/me", name="me", response_model=AuthState, summary="Текущий пользователь и CSRF-токен")
def me(principal: ViewerDep, response: Response):
    if principal.is_session and principal.csrf_token:
        set_auth_cookies(response, None, principal.csrf_token)
    return _auth_state(principal)


@router.post(
    "/auth/change-password",
    name="change_password",
    status_code=204,
    response_class=Response,
    summary="Смена пароля (остальные сессии отзываются)",
)
def change_password(body: ChangePasswordRequest, principal: SessionDep, request: Request):
    auth_service.change_password(
        principal, body.current_password, body.new_password, audit_actor(request, principal)
    )
    return Response(status_code=204)


@router.get("/auth/sessions", name="sessions_list", response_model=SessionList, summary="Мои сессии")
def list_sessions(principal: SessionDep):
    return {"items": auth_service.list_sessions(principal)}


@router.delete(
    "/auth/sessions/{session_id}",
    name="sessions_revoke",
    status_code=204,
    response_class=Response,
    summary="Отозвать сессию",
)
def revoke_session(session_id: int, principal: SessionDep, request: Request):
    current = auth_service.revoke_session(principal, session_id, audit_actor(request, principal))
    response = Response(status_code=204)
    if current:
        clear_auth_cookies(response)
    return response
