"""Продукты (M2-A): список со сводкой за 7 дней, карточка, создание, правка, архив.

Вебхук-часть (`/webhook-secret/rotate`, `/integration`, `/test-conversion`) — M3-B (`product_integration.py`).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel

from autoshorts.api.deps import OperatorDep, ViewerDep, audit_actor
from autoshorts.api.schemas.common import SLUG_PATTERN, LimitQuery, OffsetQuery
from autoshorts.api.schemas.products import Product, ProductCreate, ProductPage, ProductPatch
from autoshorts.domain.enums import ProductStatus
from autoshorts.services import products as products_service

router = APIRouter(tags=["products"])

ProductIdPath = Annotated[str, Path(pattern=SLUG_PATTERN)]


def _changes(body: BaseModel) -> dict[str, Any]:
    """Только переданные поля; вложенные модели — тоже только переданные ключи."""
    out: dict[str, Any] = {}
    for name in body.model_fields_set:
        value = getattr(body, name)
        out[name] = (
            value.model_dump(mode="json", exclude_unset=True) if isinstance(value, BaseModel) else value
        )
    return out


@router.get("/products", name="list", response_model=ProductPage, summary="Продукты (+ stats_7d)")
def list_products(
    principal: ViewerDep,
    status: ProductStatus | None = None,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
):
    return products_service.list_products(status=status, limit=limit, offset=offset)


@router.post("/products", name="create", status_code=201, response_model=Product, summary="Создать продукт")
def create_product(body: ProductCreate, principal: OperatorDep, request: Request):
    fields = body.model_dump(mode="json")
    fields["brand"] = body.brand.model_dump(mode="json", exclude_none=True)
    fields["landing"] = body.landing.model_dump(mode="json", exclude_none=True)
    return products_service.create_product(fields, audit_actor(request, principal))


@router.get("/products/{product_id}", name="get", response_model=Product, summary="Карточка продукта")
def get_product(product_id: ProductIdPath, principal: ViewerDep):
    return products_service.get_product(product_id)


@router.patch("/products/{product_id}", name="update", response_model=Product, summary="Правка продукта")
def update_product(product_id: ProductIdPath, body: ProductPatch, principal: OperatorDep, request: Request):
    return products_service.update_product(product_id, _changes(body), audit_actor(request, principal))


@router.post("/products/{product_id}/archive", name="archive", response_model=Product, summary="В архив")
def archive_product(product_id: ProductIdPath, principal: OperatorDep, request: Request):
    return products_service.archive_product(product_id, audit_actor(request, principal))
