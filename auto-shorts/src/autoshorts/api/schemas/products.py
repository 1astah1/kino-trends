"""Продукты — то, что продвигают ролики (docs/ARCHITECTURE.md §5.2, таблица products).

Вебхук-часть (rotate, integration, test-conversion) — контракт M3 (`product_integration`).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from autoshorts.api.schemas.common import ApiModel, Currency, PageMeta, RequestModel, Slug
from autoshorts.domain.enums import ProductKind, ProductStatus, RedirectMode, TelegramLinkStyle

TG_BOT_USERNAME_PATTERN = r"^[A-Za-z0-9_]{5,32}$"
TMA_SHORT_NAME_PATTERN = r"^[A-Za-z0-9_]{3,64}$"
HOST_PATTERN = r"^[a-z0-9.-]{3,253}$"


class ProductBrand(RequestModel):
    display_domain: str | None = Field(
        default=None, pattern=HOST_PATTERN, description="Домен в кадре: umka.chat"
    )
    primary_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    logo_asset_id: int | None = None


class ProductLanding(RequestModel):
    title: str | None = Field(default=None, max_length=120)
    subtitle: str | None = Field(default=None, max_length=200)
    button_telegram: str | None = Field(default=None, max_length=60)
    button_site: str | None = Field(default=None, max_length=60)


class ProductStats7d(ApiModel):
    clicks: int
    unique_visitors: int
    bot_starts: int
    first_payments: int
    revenue_minor: int
    currency: Currency | None


class Product(ApiModel):
    id: str
    name: str
    kind: ProductKind
    status: ProductStatus
    landing_url: str | None
    redirect_host: str | None
    redirect_mode: RedirectMode
    telegram_bot_username: str | None
    tma_short_name: str | None
    telegram_link_style: TelegramLinkStyle
    utm_defaults: dict[str, str]
    brand: ProductBrand
    landing: ProductLanding
    webhook_secret_set: bool = Field(description="Секрет вебхука задан (значение не отдаётся)")
    stats_7d: ProductStats7d | None = Field(description="Только в списке")
    created_at: datetime
    updated_at: datetime


class ProductPage(PageMeta):
    items: list[Product]


class ProductCreate(RequestModel):
    id: Slug
    name: str = Field(min_length=1, max_length=100)
    kind: ProductKind
    landing_url: str | None = Field(default=None, max_length=2000)
    redirect_host: str | None = Field(default=None, pattern=HOST_PATTERN)
    redirect_mode: RedirectMode = RedirectMode.LANDING_SPLIT
    telegram_bot_username: str | None = Field(default=None, pattern=TG_BOT_USERNAME_PATTERN)
    tma_short_name: str | None = Field(default=None, pattern=TMA_SHORT_NAME_PATTERN)
    telegram_link_style: TelegramLinkStyle = TelegramLinkStyle.TG_SCHEME_WITH_FALLBACK
    utm_defaults: dict[str, str] = Field(default_factory=dict)
    brand: ProductBrand = Field(default_factory=ProductBrand)
    landing: ProductLanding = Field(default_factory=ProductLanding)


class ProductPatch(RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    kind: ProductKind | None = None
    landing_url: str | None = Field(default=None, max_length=2000)
    redirect_host: str | None = Field(default=None, pattern=HOST_PATTERN)
    redirect_mode: RedirectMode | None = None
    telegram_bot_username: str | None = Field(default=None, pattern=TG_BOT_USERNAME_PATTERN)
    tma_short_name: str | None = Field(default=None, pattern=TMA_SHORT_NAME_PATTERN)
    telegram_link_style: TelegramLinkStyle | None = None
    utm_defaults: dict[str, str] | None = None
    brand: ProductBrand | None = None
    landing: ProductLanding | None = None
