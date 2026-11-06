"""Общие типы API (docs/ARCHITECTURE.md §5.1): ошибки, пагинация, идентификаторы, служебные ответы."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# ---------------------------------------------------------------- базовые модели


class ApiModel(BaseModel):
    """База ответов API."""

    model_config = ConfigDict(from_attributes=True)


class RequestModel(BaseModel):
    """База тел запросов: неизвестные ключи — 422, чтобы опечатки не терялись молча."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- идентификаторы

SLUG_PATTERN = r"^[a-z0-9][a-z0-9_-]{1,39}$"
Slug = Annotated[str, StringConstraints(pattern=SLUG_PATTERN)]
"""product_id, channel_id, niche_id, cluster_id."""

SECRET_NAME_PATTERN = r"^[A-Z][A-Z0-9_]{2,63}$"
SecretName = Annotated[str, StringConstraints(pattern=SECRET_NAME_PATTERN)]

PROVIDER_KEY_PATTERN = r"^[a-z0-9_]{2,63}$"
ProviderKey = Annotated[str, StringConstraints(pattern=PROVIDER_KEY_PATTERN)]

SETTING_KEY_PATTERN = r"^[a-z][a-z0-9_]{2,63}$"
LOGIN_PATTERN = r"^[a-z0-9][a-z0-9_.-]{2,31}$"
CURRENCY_PATTERN = r"^[A-Z]{3}$"
Currency = Annotated[str, StringConstraints(pattern=CURRENCY_PATTERN)]

# ---------------------------------------------------------------- ошибки

ERROR_CODES: dict[int, tuple[str, ...]] = {
    400: ("bad_request",),
    401: (
        "not_authenticated",
        # публичный POST /ingest/v1/events (§5.3): подпись HMAC, окно ±300 с, неизвестный X-AS-Product
        "invalid_signature",
        "stale_timestamp",
    ),
    403: ("forbidden", "csrf_failed"),
    404: ("<entity>_not_found",),  # job_not_found, product_not_found, upload_not_found, …
    409: (
        "job_state_conflict",
        "version_conflict",
        "publication_exists",
        "same_platform_crosspost_forbidden",
        "creative_in_use",  # details.variant_ids
        "clip_candidate_in_use",
        "source_not_ready",
        "idempotency_in_progress",
        # M5/M3
        "upload_offset_mismatch",  # tus PATCH: Upload-Offset ≠ получено; details.offset
        "upload_not_complete",  # POST /sources/from-upload до последнего байта
        "upload_consumed",  # загрузка уже стала источником
        "clip_candidate_blocked",  # флаги medical_claim | guarantee_claim | forbidden_phrase не сняты
        "author_script_blocked",  # в авторском тексте ошибка (запрет, ссылка, заглушка в prod)
        "package_not_ready",  # нет финального файла или ролик не одобрен
        "tracking_code_taken",
        # M4
        "metrics_snapshot_not_manual",  # удалять можно только ручной снимок метрик
        "trends_not_configured",  # мост к Kino-trends: пустая настройка kino_trends_url
    ),
    410: ("upload_expired",),  # tus: сессия истекла (Upload-Expires)
    412: ("tus_version_unsupported",),  # tus: нет Tus-Resumable: 1.0.0
    # креатив (details.asset_error), чанк tus > 16 МБ, ingest > 256 КБ или > 100 событий
    413: ("payload_too_large",),
    415: ("unsupported_media_type",),  # креатив, tus без application/offset+octet-stream, голос автора
    422: (
        "validation_error",  # отказ по правам источника: details.errors[].type = "rights_rejected"
        "unsupported_url",
        "platform_mismatch",
        "font_no_cyrillic",
        "creative_rejected",  # details.asset_error = CreativeRejectCode
        "source_rejected",  # details.reason: too_long | too_big | no_audio | no_video | probe_failed
        "rights_rejected",  # details.codes: license_incompatible | attribution_required | rights_expired | …
        # M4: ручной ввод метрик; details.reason = MetricsRejectReason (before_publication | in_future | …)
        "metrics_rejected",
    ),
    423: ("budget_paused",),
    429: ("rate_limited",),
    501: ("not_implemented",),
    502: ("trends_unreachable",),  # Kino-trends не ответил / ответил 5xx или не JSON
    503: ("not_ready", "migrations_pending"),
    507: ("insufficient_storage",),
}
"""Справочник кодов ошибок (§5.1). `code` в ответе — строка, 404 строятся как `<entity>_not_found`."""


class ValidationErrorItem(ApiModel):
    loc: list[str | int]
    msg: str
    type: str


class ErrorBody(ApiModel):
    code: str = Field(description="Машинный код ошибки, латиница snake_case", examples=["job_not_found"])
    message: str = Field(description="Сообщение для человека (по-русски)")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Подробности; для 422 — `errors: [{loc, msg, type}]`",
    )


class ErrorResponse(ApiModel):
    """Единый формат ошибки: `{"error": {"code", "message", "details"}}`."""

    error: ErrorBody


# ---------------------------------------------------------------- пагинация и параметры


class PageMeta(ApiModel):
    total: int
    limit: int
    offset: int


class JournalMeta(ApiModel):
    next_before_id: int | None = Field(
        description="Передать как `before_id`, чтобы получить следующую страницу"
    )


LimitQuery = Annotated[int, Query(ge=1, le=200, description="Размер страницы, ≤ 200")]
OffsetQuery = Annotated[int, Query(ge=0, description="Смещение")]
BeforeIdQuery = Annotated[int | None, Query(ge=1, description="Курсор журнала: id строго меньше")]
DateFromQuery = Annotated[date | None, Query(description="YYYY-MM-DD включительно, business_timezone")]
DateToQuery = Annotated[date | None, Query(description="YYYY-MM-DD включительно, business_timezone")]
SortQuery = Annotated[
    str | None,
    Query(pattern=r"^-?[a-z0-9_]+$", description="Поле сортировки из белого списка, `-` — по убыванию"),
]

# ---------------------------------------------------------------- служебные ответы


class TaskAccepted(ApiModel):
    """202: работа поставлена в очередь."""

    task_id: int


class OkResponse(ApiModel):
    ok: bool = True


ERROR_RESPONSE_REF = "#/components/schemas/ErrorResponse"


def json_error_responses() -> dict[int | str, dict[str, Any]]:
    """4XX/5XX как JSON `ErrorResponse` для эндпоинтов с не-JSON ответом (файл, YAML, PNG, SSE).

    Без этого FastAPI приписывает схему ошибки медиатипу эндпоинта (`text/yaml`, `image/png`).
    """
    content = {"application/json": {"schema": {"$ref": ERROR_RESPONSE_REF}}}
    return {
        "4XX": {"description": "Ошибка запроса: `{error: {code, message, details}}`", "content": content},
        "5XX": {"description": "Ошибка сервера или 501 not_implemented", "content": content},
    }


def binary_responses(*media_types: str, description: str = "Файл") -> dict[int | str, dict[str, Any]]:
    """`responses=` для эндпоинтов, отдающих файл (Range); ставится вместе с `response_class=Response`."""
    return {
        200: {
            "description": description,
            "content": {mt: {"schema": {"type": "string", "format": "binary"}} for mt in media_types},
        },
        **json_error_responses(),
    }


def text_responses(media_type: str, description: str) -> dict[int | str, dict[str, Any]]:
    return {
        200: {"description": description, "content": {media_type: {"schema": {"type": "string"}}}},
        **json_error_responses(),
    }
