"""Справочники для UI (docs/ARCHITECTURE.md §5.2 «мета»): enum с русскими подписями,
причины отказа с горячими клавишами, пресеты мёртвых зон."""

from __future__ import annotations

from pydantic import Field, create_model

from autoshorts.api.schemas.common import ApiModel
from autoshorts.domain.enums import ENUM_REGISTRY, RejectReason
from autoshorts.domain.settings_schema import CanvasSize, DeadZone, Rect, SafeZonePreset

__all__ = [
    "CanvasSize",
    "DeadZone",
    "EnumOption",
    "MetaEnums",
    "Rect",
    "RejectReasonItem",
    "RejectReasonList",
    "SafeZoneList",
    "SafeZonePreset",
]


class EnumOption(ApiModel):
    code: str
    label: str


# `{job_status: [{code, label}], job_stage: [...], ...}` — ключи из domain.enums.ENUM_REGISTRY
MetaEnums = create_model(  # type: ignore[call-overload]
    "MetaEnums",
    __base__=ApiModel,
    __doc__="Все словари enum с русскими подписями; ключи — имена словарей (латиница snake_case).",
    **{name: (list[EnumOption], ...) for name in ENUM_REGISTRY},
)


class RejectReasonItem(ApiModel):
    code: RejectReason
    label: str
    hotkey: str | None = Field(description="Клавиша после X на экране приёмки: 1–9, 0")


class RejectReasonList(ApiModel):
    items: list[RejectReasonItem]


class SafeZoneList(ApiModel):
    items: list[SafeZonePreset]
