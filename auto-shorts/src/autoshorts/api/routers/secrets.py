"""Секреты (без значений). Только admin. Владелец — M1-B."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Request, Response

from autoshorts.api.deps import AdminDep, audit_actor
from autoshorts.api.schemas.common import SECRET_NAME_PATTERN
from autoshorts.api.schemas.secrets import SecretList, SecretWrite
from autoshorts.services import secrets as secrets_service

router = APIRouter(tags=["secrets"])

SecretNamePath = Annotated[
    str, Path(pattern=SECRET_NAME_PATTERN, description="Имя секрета, напр. FAL_API_KEY")
]


@router.get("/secrets", name="list", response_model=SecretList, summary="Секреты: заданы или нет, откуда")
def list_secrets(principal: AdminDep):
    return {"items": secrets_service.list_secrets()}


@router.put(
    "/secrets/{secret_name}", name="set", status_code=204, response_class=Response, summary="Записать секрет"
)
def set_secret(secret_name: SecretNamePath, body: SecretWrite, principal: AdminDep, request: Request):
    secrets_service.set_secret(secret_name, body.value, audit_actor(request, principal))
    return Response(status_code=204)


@router.delete(
    "/secrets/{secret_name}",
    name="delete",
    status_code=204,
    response_class=Response,
    summary="Удалить секрет",
)
def delete_secret(secret_name: SecretNamePath, principal: AdminDep, request: Request):
    secrets_service.delete_secret(secret_name, audit_actor(request, principal))
    return Response(status_code=204)
