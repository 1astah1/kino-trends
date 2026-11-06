"""Справочники для UI (viewer+). Владелец — M1-B.

Данные: domain.enums (ENUM_REGISTRY, LABELS), настройка `safe_zone_presets`.
Горячие клавиши причин отказа (экран приёмки, `X` + цифра) пока живут здесь: `domain/reject_reasons.py`
из §3.1 ещё не создан (владелец контрактов); форма ответа от этого не зависит.
"""

from __future__ import annotations

from fastapi import APIRouter

from autoshorts.api.deps import ViewerDep
from autoshorts.api.schemas.meta import MetaEnums, RejectReasonList, SafeZoneList
from autoshorts.domain.enums import ENUM_REGISTRY, RejectReason, enum_options, label

router = APIRouter(tags=["meta"])

# Клавиша после X на экране приёмки (§7.3). Причины трека нарезки без клавиши — редкие.
REJECT_REASON_HOTKEYS: dict[RejectReason, str | None] = {
    RejectReason.HOOK_SLABYY: "1",
    RejectReason.VIZUAL_ARTEFAKTY: "2",
    RejectReason.SUBTITRY_RASSINHRON: "3",
    RejectReason.BANNER_PEREKRYVAET: "4",
    RejectReason.ZVUK_PROBLEMA: "5",
    RejectReason.ZAPRESHCHENNAYA_FRAZA: "6",
    RejectReason.DUBLIKAT: "7",
    RejectReason.NE_TA_DLITELNOST: "8",
    RejectReason.DRUGOE: "9",
    RejectReason.AVTORSKIY_SLOY_SLABYY: None,
    RejectReason.PRAVA_SOMNITELNY: None,
    RejectReason.POVTOR_SHABLONA: "0",
}


@router.get("/meta/enums", name="enums", response_model=MetaEnums, summary="Все enum с русскими подписями")
def enums(principal: ViewerDep):
    return {name: enum_options(enum_cls) for name, enum_cls in ENUM_REGISTRY.items()}


@router.get(
    "/meta/reject-reasons", name="reject_reasons", response_model=RejectReasonList, summary="Причины отказа"
)
def reject_reasons(principal: ViewerDep):
    return {
        "items": [
            {"code": reason, "label": label(reason), "hotkey": REJECT_REASON_HOTKEYS.get(reason)}
            for reason in RejectReason
        ]
    }


@router.get("/meta/safe-zones", name="safe_zones", response_model=SafeZoneList, summary="Пресеты мёртвых зон")
def safe_zones(principal: ViewerDep):
    from autoshorts.services.settings import get_runtime_settings

    presets = get_runtime_settings().safe_zone_presets
    return {"items": [p.model_dump(mode="json") for p in presets]}
