"""Провайдеры LLM/TTS/картинок/ASR и их конфиги. Чтение viewer+, правка и проверка — admin.
Владелец — M1-B."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Request

from autoshorts.api.deps import AdminDep, ViewerDep, audit_actor
from autoshorts.api.schemas.common import PROVIDER_KEY_PATTERN
from autoshorts.api.schemas.providers import Provider, ProviderList, ProviderTestResult, ProviderUpdate
from autoshorts.services import providers as providers_service

router = APIRouter(tags=["providers"])

ProviderKeyPath = Annotated[str, Path(pattern=PROVIDER_KEY_PATTERN)]


@router.get("/providers", name="list", response_model=ProviderList, summary="Реестр провайдеров и конфиги")
def list_providers(principal: ViewerDep):
    return {"items": providers_service.list_providers()}


@router.put("/providers/{provider_key}", name="update", response_model=Provider, summary="Конфиг провайдера")
def update_provider(
    provider_key: ProviderKeyPath, body: ProviderUpdate, principal: AdminDep, request: Request
):
    return providers_service.update_provider(
        provider_key,
        enabled=body.enabled,
        is_default=body.is_default,
        params=body.params,
        secret_name=body.secret_name,
        actor=audit_actor(request, principal),
    )


@router.post(
    "/providers/{provider_key}/test", name="test", response_model=ProviderTestResult, summary="Проверка связи"
)
def test_provider(provider_key: ProviderKeyPath, principal: AdminDep, request: Request):
    return providers_service.test_provider(provider_key, audit_actor(request, principal))
