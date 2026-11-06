"""API-токены для машин (`Authorization: Bearer ast_…`). Выпуск и отзыв — admin из веб-сессии.
Владелец — M1-B.

Токен в `/api/v1` даёт только чтение и не больше прав operator (см. `api/deps.py`).
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from autoshorts.api.deps import AdminSessionDep, audit_actor
from autoshorts.api.schemas.auth import ApiTokenCreate, ApiTokenCreated, ApiTokenList
from autoshorts.services import auth as auth_service

router = APIRouter(tags=["auth"])


@router.get("/api-tokens", name="api_tokens_list", response_model=ApiTokenList, summary="Мои API-токены")
def list_api_tokens(principal: AdminSessionDep):
    return {"items": auth_service.list_api_tokens(principal)}


@router.post(
    "/api-tokens",
    name="api_tokens_create",
    status_code=201,
    response_model=ApiTokenCreated,
    summary="Выпустить токен (значение отдаётся один раз)",
)
def create_api_token(body: ApiTokenCreate, principal: AdminSessionDep, request: Request):
    return auth_service.create_api_token(
        principal,
        name=body.name,
        scopes=body.scopes,
        expires_at=body.expires_at,
        actor=audit_actor(request, principal),
    )


@router.delete(
    "/api-tokens/{token_id}",
    name="api_tokens_revoke",
    status_code=204,
    response_class=Response,
    summary="Отозвать токен",
)
def revoke_api_token(token_id: int, principal: AdminSessionDep, request: Request):
    auth_service.revoke_api_token(principal, token_id, audit_actor(request, principal))
    return Response(status_code=204)
