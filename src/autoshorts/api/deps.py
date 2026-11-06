"""Зависимости FastAPI: кто делает запрос, роли, CSRF, контекст аудита (docs/ARCHITECTURE.md §5.1).

Использование в роутере (любой поток):

    from autoshorts.api.deps import AdminDep, OperatorDep, ViewerDep, audit_actor

    @router.post("/things", name="create")
    def create(body: ThingCreate, principal: OperatorDep, request: Request):
        actor = audit_actor(request, principal)
        ...

- `ViewerDep` / `OperatorDep` / `AdminDep` — минимальная роль (admin ≥ operator ≥ viewer).
- Небезопасный метод (POST/PUT/PATCH/DELETE): по cookie-сессии нужен `X-CSRF-Token`
  (= cookie `as_csrf` = токен сессии), иначе 403 `csrf_failed`; по API-токену — всегда 403
  (токены только читают); `viewer` на небезопасных методах — 403, кроме своих настроек входа
  (`SessionDep`: выход, смена пароля, отзыв своих сессий).
- API-токен читает `/api/v1` только со scope `read`; токен без него (например, только `ingest`)
  получает 403 на любом эндпоинте. Приём конверсий идёт через публичный `/ingest/v1/events`
  с HMAC продукта (§0.1 п. 10), поэтому scope `ingest` операторского API не открывает.
- `SessionDep` — только cookie-сессия (токеном нельзя, 403); `AdminSessionDep` — admin из веб-сессии
  (пользователи, выпуск API-токенов).

Зависимости читают `Request` напрямую и не объявляют Header/Cookie-параметры — поэтому
OpenAPI-контракт не меняется.
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import Depends, Request

from autoshorts.core.errors import CsrfFailed, Forbidden, NotAuthenticated
from autoshorts.core.security import constant_time_equals
from autoshorts.domain.enums import ActorKind, ApiTokenScope, Role
from autoshorts.services import auth as auth_service
from autoshorts.services.audit import Actor
from autoshorts.services.auth import Principal

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_BEARER_RE = re.compile(r"Bearer\s+(\S+)", re.IGNORECASE)

__all__ = [
    "AdminDep",
    "AdminSessionDep",
    "OperatorDep",
    "Principal",
    "SessionDep",
    "ViewerDep",
    "audit_actor",
    "client_ip",
    "get_principal",
    "request_id",
    "require",
]


def client_ip(request: Request) -> str | None:
    """IP клиента. За nginx его подставляет uvicorn `--proxy-headers` из X-Forwarded-For."""
    return request.client.host if request.client else None


def request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _bearer(request: Request) -> str | None:
    """Значение `Authorization: Bearer …`. Другие схемы (Basic от nginx) игнорируются."""
    header = request.headers.get("authorization")
    if not header or not header.strip().lower().startswith("bearer"):
        return None
    m = _BEARER_RE.fullmatch(header.strip())
    return m.group(1) if m else ""


def get_principal(request: Request) -> Principal:
    """Principal по Bearer-токену или cookie `as_session` (кешируется на время запроса). Нет — 401."""
    cached = getattr(request.state, "principal", None)
    if cached is not None:
        return cached
    token = _bearer(request)
    if token is not None:
        principal = auth_service.authenticate_api_token(token)
        if principal is None:
            raise NotAuthenticated("Неверный или отозванный API-токен")
    else:
        principal = auth_service.authenticate_session(request.cookies.get(auth_service.SESSION_COOKIE))
        if principal is None:
            raise NotAuthenticated()
    request.state.principal = principal
    return principal


def _check_csrf(request: Request, principal: Principal) -> None:
    header = request.headers.get(auth_service.CSRF_HEADER)
    cookie = request.cookies.get(auth_service.CSRF_COOKIE)
    if not header or not constant_time_equals(header, principal.csrf_token):
        raise CsrfFailed("Нет или неверный заголовок X-CSRF-Token")
    if cookie is not None and not constant_time_equals(header, cookie):
        raise CsrfFailed("X-CSRF-Token не совпадает с cookie as_csrf")


def require(min_role: Role = Role.VIEWER, *, session_only: bool = False, viewer_may_write: bool = False):
    """Фабрика зависимости: аутентификация + роль + CSRF для небезопасных методов."""

    def dependency(request: Request) -> Principal:
        principal = get_principal(request)
        unsafe = request.method.upper() in UNSAFE_METHODS
        if principal.kind == ActorKind.API_TOKEN:
            if session_only:
                raise Forbidden("Действие доступно только из веб-сессии, не по API-токену")
            if ApiTokenScope.READ.value not in principal.scopes:
                raise Forbidden(
                    "У API-токена нет scope read", details={"required_scope": ApiTokenScope.READ.value}
                )
            if unsafe:
                raise Forbidden("API-токен даёт только чтение")
        elif unsafe:
            _check_csrf(request, principal)
            if principal.role == Role.VIEWER and not viewer_may_write:
                raise Forbidden("Роль viewer — только просмотр", details={"required_role": "operator"})
        if not principal.at_least(min_role):
            raise Forbidden("Недостаточно прав", details={"required_role": str(min_role)})
        return principal

    return dependency


ViewerDep = Annotated[Principal, Depends(require(Role.VIEWER))]
OperatorDep = Annotated[Principal, Depends(require(Role.OPERATOR))]
AdminDep = Annotated[Principal, Depends(require(Role.ADMIN))]
SessionDep = Annotated[Principal, Depends(require(Role.VIEWER, session_only=True, viewer_may_write=True))]
AdminSessionDep = Annotated[Principal, Depends(require(Role.ADMIN, session_only=True))]


def audit_actor(request: Request, principal: Principal | None = None) -> Actor:
    """Actor для `services.audit.record`: пользователь/токен + request_id + обезличенный IP."""
    ip_h = auth_service.hash_client_ip(client_ip(request))
    if principal is None:
        return Actor(kind=ActorKind.USER, user_id=None, request_id=request_id(request), ip_hash=ip_h)
    return principal.actor(request_id=request_id(request), ip_hash=ip_h)
