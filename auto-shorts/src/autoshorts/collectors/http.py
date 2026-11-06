"""Общий HTTP коллекторов (docs/ARCHITECTURE.md §6.5): таймаут 15 с, 3 ретрая, прокси на провайдера.

Все вызовы площадок идут через `request()`: сетевые ошибки, 429 и 5xx повторяются с экспоненциальной
паузой (и `Retry-After`, если он есть), остальные ответы возвращаются как есть — их толкует модуль
площадки. Число запросов, дошедших до сервера (`http_attempts`), нужно для учёта квоты: YouTube
списывает единицы и за неудачные вызовы.

    from autoshorts.collectors.http import make_client, request
    with make_client(proxy=settings.collectors_proxy_url) as client:
        res = request(client, "GET", url, params={"id": "...", "key": key})

Ключи API и токены в текстах ошибок вырезаются (`redact`): текст ошибки уходит в БД
(`collector_state.last_error`, `publications.last_poll_error`) и в логи.

Для тестов и ручных проверок без сети: `transport_override` (модульная переменная) подставляется
во все клиенты, которые создаются без явного `transport` — например, `httpx.MockTransport`.
"""

from __future__ import annotations

import json
import random
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx

__all__ = [
    "BACKOFF_BASE_SEC",
    "BACKOFF_MAX_SEC",
    "DEFAULT_RETRIES",
    "DEFAULT_TIMEOUT_SEC",
    "RETRY_STATUSES",
    "CollectorError",
    "ErrorKind",
    "HttpResult",
    "json_body",
    "make_client",
    "redact",
    "request",
]

DEFAULT_TIMEOUT_SEC = 15.0
DEFAULT_RETRIES = 3
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
BACKOFF_BASE_SEC = 0.5
BACKOFF_MAX_SEC = 8.0
USER_AGENT = "autoshorts-collectors/1.0"
MAX_ERROR_TEXT = 500

transport_override: httpx.BaseTransport | None = None
"""Транспорт для всех клиентов без явного `transport` (тесты, `collectors-once` без сети)."""


class ErrorKind(StrEnum):
    """Класс ошибки коллектора; `services.metrics` переводит его в `CollectorReason`."""

    NETWORK = "network"  # таймаут, DNS, отказ соединения, прокси
    HTTP = "http"  # 5xx/429 после ретраев или неожиданный 4xx
    AUTH = "auth"  # ключ или токен отклонён площадкой
    QUOTA = "quota"  # суточная квота исчерпана (YouTube quotaExceeded)
    RATE_LIMITED = "rate_limited"  # площадка просит сбавить темп
    BAD_RESPONSE = "bad_response"  # не JSON или неожиданная форма ответа
    TOOL_MISSING = "tool_missing"  # нет yt-dlp


class CollectorError(Exception):
    """Ошибка вызова площадки. `http_attempts` — сколько запросов дошло до сервера (для квоты)."""

    def __init__(
        self,
        message: str,
        *,
        kind: ErrorKind | str,
        status: int | None = None,
        reason: str | None = None,
        http_attempts: int = 0,
    ) -> None:
        self.message = redact(message)[:MAX_ERROR_TEXT]
        self.kind = ErrorKind(kind)
        self.status = status
        self.reason = reason
        self.http_attempts = http_attempts
        super().__init__(self.message)

    def __repr__(self) -> str:
        return f"CollectorError(kind={self.kind.value!r}, status={self.status}, reason={self.reason!r})"


_SECRET_PARAM = re.compile(r"(?i)\b((?:key|access_token|token|api_key)=)[^&\s'\"]+")


def redact(text: str) -> str:
    """Вырезать значения `key=…`, `access_token=…` из URL и текстов ошибок."""
    return _SECRET_PARAM.sub(r"\1***", str(text))


def make_client(
    *,
    proxy: str | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    headers: Mapping[str, str] | None = None,
) -> httpx.Client:
    """Клиент коллектора: таймаут 15 с, редиректы, прокси `collectors_proxy_url` (если задан)."""
    kwargs: dict[str, Any] = {
        "timeout": httpx.Timeout(timeout),
        "follow_redirects": True,
        "headers": {"User-Agent": USER_AGENT, **dict(headers or {})},
    }
    chosen = transport if transport is not None else transport_override
    if chosen is not None:
        kwargs["transport"] = chosen
    elif proxy:
        kwargs["proxy"] = proxy
    return httpx.Client(**kwargs)


@dataclass
class HttpResult:
    response: httpx.Response
    http_attempts: int


def _backoff(attempt: int) -> float:
    return min(BACKOFF_MAX_SEC, BACKOFF_BASE_SEC * (2**attempt)) + random.uniform(0, 0.25)  # noqa: S311


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, min(BACKOFF_MAX_SEC, float(value)))
    except ValueError:
        return None


def request(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    json_data: Any = None,
    data: Mapping[str, Any] | None = None,
    retries: int = DEFAULT_RETRIES,
    retry_statuses: frozenset[int] = RETRY_STATUSES,
    sleep: Callable[[float], None] = time.sleep,
) -> HttpResult:
    """Запрос с ретраями. Сеть не ответила после всех попыток — `CollectorError(kind=network)`.

    429/5xx повторяются; если и последняя попытка вернула такой статус, ответ возвращается —
    толкование за модулем площадки. Остальные статусы возвращаются сразу. `headers`, `json_data`, `data` —
    для OAuth и публикаторов (`collectors.oauth_google`, `publishers.youtube`).
    """
    attempts = 0
    for i in range(max(0, retries) + 1):
        last = i >= retries
        try:
            resp = client.request(
                method,
                url,
                params=dict(params) if params is not None else None,
                headers=dict(headers) if headers is not None else None,
                json=json_data,
                data=dict(data) if data is not None else None,
            )
        except httpx.TransportError as exc:  # таймауты, DNS, соединение, прокси
            if not last:
                sleep(_backoff(i))
                continue
            raise CollectorError(
                f"сеть недоступна: {type(exc).__name__}: {exc}",
                kind=ErrorKind.NETWORK,
                http_attempts=attempts,
            ) from exc
        attempts += 1
        if resp.status_code in retry_statuses and not last:
            sleep(_retry_after(resp) or _backoff(i))
            continue
        return HttpResult(resp, attempts)
    raise AssertionError("unreachable")  # pragma: no cover


def json_body(resp: httpx.Response, *, http_attempts: int = 0) -> Any:
    """Тело ответа как JSON, иначе `CollectorError(kind=bad_response)`."""
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise CollectorError(
            f"ответ не JSON (HTTP {resp.status_code})",
            kind=ErrorKind.BAD_RESPONSE,
            status=resp.status_code,
            http_attempts=http_attempts,
        ) from exc
