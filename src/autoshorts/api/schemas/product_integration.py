"""Вебхук продукта: секрет HMAC, инструкция интеграции, тестовое событие (§5.2 «Продукты…», §6.6).

Реализация — M3-B (`api/routers/product_integration.py`), тег `integration` (карточка продукта —
тег `products`, M2-A). Экран: вкладка «Вебхук» продукта (M3-C, `web/components/products/webhook-tab.tsx`).

* `POST /products/{product_id}/webhook-secret/rotate` `WebhookSecretRotate` → `WebhookSecretRotated`
  (секрет отдаётся один раз; прежний принимается ещё `grace_hours`; аудит без значения);
* `GET /products/{product_id}/integration` → `ProductIntegration` (URL приёма, заголовки, спецификация
  подписи, пример тела, deeplink-примеры, сниппеты Python/Node, последние запросы приёма);
* `POST /products/{product_id}/test-conversion` `ConversionTestRequest` → `ConversionTestResult`
  (настоящий подписанный запрос в публичный `/ingest/v1/events` с `is_test`; в статистику не идёт).

Секрет хранится в `secrets` под именем `PRODUCT_WEBHOOK_<ID>` (латиница, верхний регистр,
`-` → `_`), имя — в `products.webhook_secret_name`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from autoshorts.api.schemas.common import ApiModel, RequestModel
from autoshorts.api.schemas.conversions import IngestOutcome, IngestResponse
from autoshorts.domain.enums import ConversionEventType

__all__ = [
    "DeeplinkExample",
    "DeeplinkKind",
    "EmitterSnippets",
    "IngestRequestLogItem",
    "IntegrationHeader",
    "ProductIntegration",
    "ConversionTestRequest",
    "ConversionTestResult",
    "WebhookSecretRotate",
    "WebhookSecretRotated",
    "webhook_secret_name",
]


def webhook_secret_name(product_id: str) -> str:
    """`umka` → `PRODUCT_WEBHOOK_UMKA`, `vpn-bot` → `PRODUCT_WEBHOOK_VPN_BOT` (SECRET_NAME_PATTERN)."""
    return "PRODUCT_WEBHOOK_" + product_id.upper().replace("-", "_")


class WebhookSecretRotate(RequestModel):
    grace_hours: int = Field(
        default=24, ge=0, le=168, description="Сколько часов ещё принимать прежний секрет (0 — сразу нет)"
    )


class WebhookSecretRotated(ApiModel):
    product_id: str
    secret: str = Field(description="Показывается один раз; ≥ 32 байт энтропии, base64url")
    secret_name: str
    rotated_at: datetime
    previous_valid_until: datetime | None


class IntegrationHeader(ApiModel):
    name: str = Field(description="X-AS-Product | X-AS-Timestamp | X-AS-Signature | Content-Type")
    value: str = Field(description="Значение или шаблон: `umka`, `<unix>`, `sha256=<hex>`")
    description: str


class DeeplinkKind(StrEnum):
    BOT_START = "bot_start"
    BOT_START_LANDING = "bot_start_landing"
    TMA_STARTAPP = "tma_startapp"
    SITE_UTM = "site_utm"


class DeeplinkExample(ApiModel):
    kind: DeeplinkKind
    url: str
    description: str


class EmitterSnippets(ApiModel):
    python: str = Field(description="stdlib: hmac, json, urllib (эталон public/emitter_snippet.py)")
    node: str = Field(description="node:crypto + fetch")


class IngestRequestLogItem(ApiModel):
    """Строка `ingest_requests`: вкладка показывает и отказы (подпись, время, размер)."""

    id: int
    received_at: datetime
    outcome: IngestOutcome
    http_status: int
    events_total: int
    accepted: int
    duplicates: int
    unknown_tag: int
    invalid: int
    is_test: bool
    detail: str | None


class ProductIntegration(ApiModel):
    product_id: str
    ingest_url: str = Field(description="https://<redirect_host>/ingest/v1/events")
    headers: list[IntegrationHeader]
    signature_spec: str = Field(description='HMAC_SHA256(secret, f"{ts}." + raw_body), hex, префикс sha256=')
    max_skew_sec: int
    max_events: int
    max_body_bytes: int
    rate_limit_per_sec: int
    event_types: list[ConversionEventType]
    example_payload: dict = Field(description="Тело IngestRequest с одним событием")
    deeplink_examples: list[DeeplinkExample]
    emitter_snippets: EmitterSnippets
    secret_set: bool
    secret_rotated_at: datetime | None
    previous_secret_valid_until: datetime | None
    last_event_at: datetime | None = Field(description="Последнее принятое событие (не тестовое)")
    recent_requests: list[IngestRequestLogItem] = Field(description="Последние 20 запросов приёма")


class ConversionTestRequest(RequestModel):
    event_type: ConversionEventType = ConversionEventType.BOT_START
    tag: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$", description="Пусто — канальный или тестовый код"
    )


class ConversionTestResult(ApiModel):
    ok: bool
    http_status: int
    event_id: str
    error_code: str | None = Field(description="invalid_signature | stale_timestamp | … при отказе")
    response: IngestResponse | None
    request_id: str | None
