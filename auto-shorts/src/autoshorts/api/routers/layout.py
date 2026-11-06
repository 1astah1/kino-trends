"""Раскладка ролика для редактора приёмки (§6.3.7): где баннер, субтитры, заголовок и тезисы.

* `GET /jobs/{job_id}/layout` — канва, safe zone площадки, авто-раскладка и раскладка с текущим
  переопределением (`author_layer.layout`), пересечения, что было в последнем рендере.
* `PUT /jobs/{job_id}/layout` — сохранить `LayoutOverride`; с `rerender=true` ролик с приёмки уходит
  на доработку со стадии `render` (та же механика, что решение «на доработку»), упавший — в retry.
  409 `job_state_conflict` у одобренного/опубликованного/закрытого ролика и пока идёт рендер.

Контракт — `api/schemas/clipping.py` (JobLayout, LayoutPut), логика — `services/clipping.py`
(`job_layout_view`, `put_job_layout`), геометрия — `clipping/layout_override.py`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from autoshorts.api.deps import OperatorDep, ViewerDep, audit_actor
from autoshorts.api.schemas.clipping import (
    JobCaptions,
    JobCaptionsPut,
    JobChannel,
    JobChannelPut,
    JobLayout,
    LayoutPut,
)

router = APIRouter(tags=["clipping"])


def _connection():
    from autoshorts.db.pool import connection

    return connection()


def _caller(request: Request, principal: Any) -> Any:
    from autoshorts.services.jobs import Caller

    return Caller.from_principal(principal, audit_actor(request, principal))


@router.get(
    "/jobs/{job_id}/layout",
    name="layout_get",
    response_model=JobLayout,
    summary="Раскладка ролика: баннер и текстовые зоны на канве 1080×1920",
)
def get_job_layout(job_id: int, principal: ViewerDep):
    from autoshorts.services import clipping as svc

    with _connection() as conn:
        return svc.job_layout_view(conn, job_id)


@router.put(
    "/jobs/{job_id}/layout",
    name="layout_put",
    response_model=JobLayout,
    summary="Сохранить ручную раскладку (rerender=true — перерендер со стадии render; 409 у одобренного)",
)
def put_job_layout(job_id: int, body: LayoutPut, principal: OperatorDep, request: Request):
    from autoshorts.services import clipping as svc

    with _connection() as conn:
        return svc.put_job_layout(conn, job_id, body, _caller(request, principal))


@router.get(
    "/jobs/{job_id}/captions",
    name="captions_get",
    response_model=JobCaptions,
    summary="Субтитры ролика: включены ли",
)
def get_job_captions(job_id: int, principal: ViewerDep):
    from autoshorts.services import clipping as svc

    with _connection() as conn:
        return svc.job_captions_view(conn, job_id)


@router.get(
    "/jobs/{job_id}/channel",
    name="channel_get",
    response_model=JobChannel,
    summary="Канал ролика и можно ли его сменить",
)
def get_job_channel(job_id: int, principal: ViewerDep):
    from autoshorts.services import clipping as svc

    with _connection() as conn:
        return svc.job_channel_view(conn, job_id)


@router.put(
    "/jobs/{job_id}/channel",
    name="channel_put",
    response_model=JobChannel,
    summary="Сменить канал ролика в приёмке: баннер и оформление нового канала, перерендер (409 вне приёмки)",
)
def put_job_channel(job_id: int, body: JobChannelPut, principal: OperatorDep, request: Request):
    from autoshorts.services import clipping as svc

    with _connection() as conn:
        return svc.put_job_channel(conn, job_id, body, _caller(request, principal))


@router.put(
    "/jobs/{job_id}/captions",
    name="captions_put",
    response_model=JobCaptions,
    summary="Субтитры одной галочкой (rerender=true — перерендер со стадии render; 409 у одобренного)",
)
def put_job_captions(job_id: int, body: JobCaptionsPut, principal: OperatorDep, request: Request):
    from autoshorts.services import clipping as svc

    with _connection() as conn:
        return svc.put_job_captions(conn, job_id, body, _caller(request, principal))
