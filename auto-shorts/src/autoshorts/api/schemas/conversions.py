"""Конверсии: журнал в API и публичный приём `POST /ingest/v1/events` с HMAC (§5.2, §5.3, §6.6).

Реализация — M3-B: журнал `GET /conversions` (`api/routers/conversions.py`, тег `tracking`), приём —
публичное приложение редиректора (`public/*`, хост `u.<домен>`, без `/api/v1`). Здесь — контракт
приёма: заголовки, подпись, лимиты, тело и ответ, плюс эталонные функции подписи (stdlib), которыми
пользуются сервер, `public/emitter_snippet.py`, тестовое событие и тесты.

**Приём.** Заголовки `X-AS-Product: <product_id>`, `X-AS-Timestamp: <unix, секунды>`,
`X-AS-Signature: sha256=<hex(HMAC_SHA256(secret, f"{ts}." + raw_body))>`. Проверки по порядку:
тело ≤ 256 КБ (иначе 413 `payload_too_large`); продукт известен и подпись верна текущим секретом
или прежним до `webhook_prev_valid_until` (иначе 401 `invalid_signature`, неизвестный продукт тоже
401 — без подсказки); `|now − ts| ≤ 300 с` (иначе 401 `stale_timestamp`); ≤ 20 запросов/с на продукт
(иначе 429 `rate_limited` c `Retry-After`); в `events` не больше 100 (иначе 413 до разбора событий);
разбор JSON и `IngestRequest` (иначе 422 `validation_error`). Каждый запрос пишется в
`ingest_requests` (исход, счётчики; тело не храним).

**События** разбираются по одному: плохое событие получает `status=invalid` и не валит пачку.
Идемпотентность — `ON CONFLICT (product_id, external_event_id) DO NOTHING` → `duplicate`.
Суффикс `_l` у `tag` снимается и даёт `via_landing=true`; `tag` разрешается в `tracking_links`,
измерения (публикация, ролик, канал, кампания, вариант, площадка) копируются в строку; неизвестный
`tag` → событие сохраняется как прямой трафик продукта со статусом `unknown_tag`. `user_ref`
хешируется (`sha256(product|user_ref)`), PII не храним.

Журнал: `GET /conversions`
`?product_id&tracking_link_id&publication_id&event_type&include_test&before_id&limit`
→ `ConversionJournal`. Тестовые события (`is_test`, из вкладки «Вебхук») в статистику не идут.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator
from pydantic_core import PydanticCustomError

from autoshorts.api.schemas.common import ApiModel, Currency, JournalMeta, RequestModel
from autoshorts.api.schemas.tracking import AttributionLevel
from autoshorts.domain.enums import ConversionEventType, Platform

__all__ = [
    "INGEST_HEADER_PRODUCT",
    "INGEST_HEADER_SIGNATURE",
    "INGEST_HEADER_TIMESTAMP",
    "INGEST_MAX_BODY_BYTES",
    "INGEST_MAX_EVENTS",
    "INGEST_MAX_SKEW_SEC",
    "INGEST_PATH",
    "INGEST_RATE_LIMIT_PER_SEC",
    "INGEST_SIGNATURE_PREFIX",
    "Conversion",
    "ConversionJournal",
    "IngestEvent",
    "IngestEventStatus",
    "IngestOutcome",
    "IngestRequest",
    "IngestResponse",
    "IngestResult",
    "ingest_signature",
    "verify_ingest_signature",
]

INGEST_PATH = "/ingest/v1/events"
INGEST_HEADER_PRODUCT = "X-AS-Product"
INGEST_HEADER_TIMESTAMP = "X-AS-Timestamp"
INGEST_HEADER_SIGNATURE = "X-AS-Signature"
INGEST_SIGNATURE_PREFIX = "sha256="
INGEST_MAX_SKEW_SEC = 300
INGEST_MAX_BODY_BYTES = 256 * 1024
INGEST_MAX_EVENTS = 100
INGEST_RATE_LIMIT_PER_SEC = 20
EVENT_ID_PATTERN = r"^[\x21-\x7e]{1,128}$"
"""event_id продукта: печатный ASCII без пробелов, ≤ 128 (CHECK conversions.external_event_id)."""
INGEST_TAG_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
"""Как пришло из start-параметра Telegram / utm_campaign (с возможным суффиксом `_l`)."""


# ---------------------------------------------------------------- подпись


def ingest_signature(secret: str | bytes, timestamp: int | str, raw_body: bytes) -> str:
    """`sha256=<hex(HMAC_SHA256(secret, f"{ts}." + raw_body))>` — значение заголовка X-AS-Signature."""
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    msg = f"{int(timestamp)}.".encode("ascii") + raw_body
    return INGEST_SIGNATURE_PREFIX + hmac.new(key, msg, hashlib.sha256).hexdigest()


def verify_ingest_signature(
    secret: str | bytes, timestamp: int | str, raw_body: bytes, header_value: str | None
) -> bool:
    """Сравнение постоянного времени (`hmac.compare_digest`); окно времени проверяется отдельно."""
    if not header_value:
        return False
    try:
        expected = ingest_signature(secret, timestamp, raw_body)
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(expected.encode("ascii"), header_value.strip().encode("ascii", "replace"))


# ---------------------------------------------------------------- тело и ответ


class IngestEvent(RequestModel):
    event_id: str = Field(pattern=EVENT_ID_PATTERN, description="Уникален в пределах продукта")
    type: ConversionEventType
    occurred_at: datetime = Field(description="ISO 8601 с часовым поясом")
    tag: str | None = Field(
        default=None, pattern=INGEST_TAG_PATTERN, description="Код из start-параметра / utm_campaign"
    )
    user_ref: str | None = Field(default=None, max_length=256, description="Хешируется, не хранится")
    amount_minor: int | None = Field(
        default=None, ge=0, le=10**12, description="Сумма в минимальных единицах валюты"
    )
    currency: Currency | None = None
    meta: dict[str, Any] = Field(default_factory=dict, description="≤ 4 КБ в JSON")

    @field_validator("occurred_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise PydanticCustomError("timezone_required", "occurred_at без часового пояса")
        return v

    @field_validator("meta")
    @classmethod
    def _meta_size(cls, v: dict[str, Any]) -> dict[str, Any]:
        import json

        if len(json.dumps(v, ensure_ascii=False, default=str).encode("utf-8")) > 4096:
            raise PydanticCustomError("meta_too_large", "meta больше 4 КБ")
        return v


class IngestRequest(RequestModel):
    events: list[IngestEvent] = Field(min_length=1, max_length=INGEST_MAX_EVENTS)


class IngestEventStatus(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    UNKNOWN_TAG = "unknown_tag"
    INVALID = "invalid"


class IngestResult(ApiModel):
    event_id: str
    status: IngestEventStatus
    error: str | None = Field(default=None, description="Для invalid: что не так (латиница)")


class IngestResponse(ApiModel):
    """200: счётчики не пересекаются; `unknown_tag` — сохранено как прямой трафик продукта."""

    accepted: int
    duplicates: int
    unknown_tag: int
    invalid: int = 0
    results: list[IngestResult]


class IngestOutcome(StrEnum):
    """Исход запроса в журнале `ingest_requests.outcome`."""

    ACCEPTED = "accepted"
    INVALID_SIGNATURE = "invalid_signature"
    STALE_TIMESTAMP = "stale_timestamp"
    UNKNOWN_PRODUCT = "unknown_product"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    RATE_LIMITED = "rate_limited"
    INVALID_BODY = "invalid_body"


# ---------------------------------------------------------------- журнал в API


class Conversion(ApiModel):
    id: int
    product_id: str
    external_event_id: str
    event_type: ConversionEventType
    occurred_at: datetime
    received_at: datetime
    tag: str | None
    via_landing: bool
    attribution_level: AttributionLevel
    tracking_link_id: int | None
    publication_id: int | None
    job_id: int | None
    channel_id: str | None
    campaign_id: int | None
    banner_variant_id: int | None
    platform: Platform | None
    amount_minor: int | None
    currency: Currency | None
    meta: dict[str, Any]
    is_test: bool


class ConversionJournal(JournalMeta):
    items: list[Conversion]
