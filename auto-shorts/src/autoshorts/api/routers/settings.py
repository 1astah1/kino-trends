"""Рантайм-настройки: чтение operator+, изменение admin. Владелец — M1-B."""

from __future__ import annotations

from fastapi import APIRouter, Request

from autoshorts.api.deps import AdminDep, OperatorDep, audit_actor
from autoshorts.api.schemas.settings import SettingsList, SettingsPatch
from autoshorts.services import settings as settings_service

router = APIRouter(tags=["settings"])


@router.get("/settings", name="list", response_model=SettingsList, summary="Настройки с дефолтами")
def list_settings(principal: OperatorDep):
    return {"items": settings_service.list_settings()}


@router.patch("/settings", name="update", response_model=SettingsList, summary="Изменить настройки (admin)")
def update_settings(body: SettingsPatch, principal: AdminDep, request: Request):
    return {"items": settings_service.patch_settings(body.root, audit_actor(request, principal))}
