"""Вебхук продукта: ротация секрета, инструкция интеграции, тестовое событие (§5.2, §6.6). Владелец — M3-B.

Контракт — `api/schemas/product_integration.py`, тело приёма — `api/schemas/conversions.py`.
Заглушки контракта M5M3-0 отвечают 501; M3-B заменяет тела, не меняя пути, имена и схемы.
Ротация — admin (это секрет; значение отдаётся один раз, в аудит — без значения).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Request

from autoshorts.api.deps import AdminDep, OperatorDep, ViewerDep, audit_actor, request_id
from autoshorts.api.schemas.common import SLUG_PATTERN
from autoshorts.api.schemas.product_integration import (
    ConversionTestRequest,
    ConversionTestResult,
    ProductIntegration,
    WebhookSecretRotate,
    WebhookSecretRotated,
)

router = APIRouter(tags=["integration"])

ProductIdPath = Annotated[str, Path(pattern=SLUG_PATTERN)]


def _connection() -> Any:
    from autoshorts.db.pool import connection

    return connection()


def _svc() -> Any:
    from autoshorts.services import ingest

    return ingest


@router.post(
    "/products/{product_id}/webhook-secret/rotate",
    name="webhook_secret_rotate",
    response_model=WebhookSecretRotated,
    summary="Новый HMAC-секрет (admin; показывается один раз)",
)
def rotate_webhook_secret(
    product_id: ProductIdPath, body: WebhookSecretRotate, principal: AdminDep, request: Request
):
    with _connection() as conn:
        return _svc().rotate_secret(conn, product_id, body.grace_hours, audit_actor(request, principal))


@router.get(
    "/products/{product_id}/integration",
    name="get",
    response_model=ProductIntegration,
    summary="Инструкция интеграции и последние запросы приёма",
)
def get_integration(product_id: ProductIdPath, principal: ViewerDep):
    with _connection() as conn:
        return _svc().integration_view(conn, product_id)


@router.post(
    "/products/{product_id}/test-conversion",
    name="test_conversion",
    response_model=ConversionTestResult,
    summary="Тестовое событие через публичный ingest (в статистику не идёт)",
)
def send_test_conversion(
    product_id: ProductIdPath, body: ConversionTestRequest, principal: OperatorDep, request: Request
):
    with _connection() as conn:
        return _svc().send_test_conversion(
            conn,
            product_id,
            body.event_type.value,
            body.tag,
            audit_actor(request, principal),
            request_id=request_id(request),
        )
