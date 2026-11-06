"""ASGI-middleware API (docs/ARCHITECTURE.md §5.1, §8.5 «Логи»). Чистый ASGI — не ломает SSE-потоки.

`RequestContextMiddleware`:
- `X-Request-ID`: берётся входящий (его ставит nginx, `$request_id`) или генерируется; кладётся
  в `request.state.request_id`, в контекст логов и в заголовок ответа;
- заголовки ответа `/api/`: `Cache-Control: no-store` (если эндпоинт не поставил свой),
  `X-Content-Type-Options: nosniff`;
- строка лога на запрос: метод, путь, статус, `duration_ms` (кроме `/health`);
- ворота миграций: пока схема БД отстаёт от файлов миграций (§4.1), все `/api/v1/*`, кроме
  `/health`, `/health/ready` и `openapi.json`, отвечают 503 `migrations_pending`.

`UploadGuard` — multipart-загрузки (креатив баннера, импорт YAML): лимит тела и вход проверяются
до чтения тела (подробности — в docstring класса).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from autoshorts.core.errors import AppError, MigrationsPending, PayloadTooLarge
from autoshorts.core.ids import new_request_id
from autoshorts.core.logging import bind_context, reset_context

log = logging.getLogger("autoshorts.api.access")

REQUEST_ID_HEADER = "x-request-id"
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{8,64}")
SCHEMA_RECHECK_SEC = 15.0


class SchemaGate:
    """Состояние схемы БД для ворот миграций. `None` — ещё не проверяли (не блокирует)."""

    def __init__(self, exempt_paths: frozenset[str]) -> None:
        self.exempt_paths = exempt_paths
        self.state: Any | None = None
        self.last_error: str | None = None
        self.checked_at: float | None = None

    @property
    def blocked(self) -> bool:
        return self.state is not None and not self.state.ok

    def check(self) -> Any | None:
        """Синхронно сверить `schema_migrations` с файлами (в пуле потоков)."""
        from autoshorts.db.migrate import schema_state
        from autoshorts.db.pool import connection

        try:
            with connection(timeout=5) as conn:
                self.state = schema_state(conn)
                self.last_error = None
        except Exception as exc:  # noqa: BLE001 - БД недоступна: не блокируем, /health/ready покажет
            self.last_error = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        self.checked_at = time.monotonic()
        return self.state

    async def refresh(self) -> Any | None:
        return await asyncio.to_thread(self.check)

    async def watch(self, interval: float = SCHEMA_RECHECK_SEC) -> None:
        """Пока миграции не применены (или БД не отвечала) — перепроверять раз в `interval`."""
        while self.state is None or not self.state.ok:
            await asyncio.sleep(interval)
            await self.refresh()
            if self.state is not None and self.state.ok:
                log.info("schema is current (version %s), migrations gate opened", self.state.current)

    def error_body(self) -> bytes:
        state = self.state
        exc = MigrationsPending(
            f"Схема БД на версии {state.current}, файлы до {state.latest}: "
            "выполните autoshorts-admin migrate",
            details={
                "current_version": state.current,
                "latest_version": state.latest,
                "pending": list(state.pending),
            },
        )
        return json.dumps(exc.to_payload(), ensure_ascii=False).encode("utf-8")


class RequestContextMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        api_prefix: str = "/api/",
        gate: SchemaGate | None = None,
        quiet_paths: frozenset[str] = frozenset(),
    ) -> None:
        self.app = app
        self.api_prefix = api_prefix
        self.gate = gate
        self.quiet_paths = quiet_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        incoming = headers.get(REQUEST_ID_HEADER)
        request_id = incoming if incoming and _REQUEST_ID_RE.fullmatch(incoming) else new_request_id()
        scope.setdefault("state", {})["request_id"] = request_id
        path: str = scope.get("path", "")
        is_api = path.startswith(self.api_prefix)
        status_code = 500
        started = time.monotonic()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                h = MutableHeaders(scope=message)
                h["X-Request-ID"] = request_id
                if is_api:
                    if "cache-control" not in h:
                        h["Cache-Control"] = "no-store"
                    if "x-content-type-options" not in h:
                        h["X-Content-Type-Options"] = "nosniff"
            await send(message)

        token = bind_context(request_id=request_id)
        try:
            if self.gate is not None and is_api and self.gate.blocked and path not in self.gate.exempt_paths:
                body = self.gate.error_body()
                await send_wrapper(
                    {
                        "type": "http.response.start",
                        "status": 503,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                            (b"retry-after", b"30"),
                        ],
                    }
                )
                await send_wrapper({"type": "http.response.body", "body": body})
                return
            await self.app(scope, receive, send_wrapper)
        finally:
            if path not in self.quiet_paths:
                duration_ms = int((time.monotonic() - started) * 1000)
                log.info(
                    "%s %s %s %dms",
                    scope.get("method", "?"),
                    path,
                    status_code,
                    duration_ms,
                    extra={
                        "method": scope.get("method"),
                        "path": path,
                        "status": status_code,
                        "duration_ms": duration_ms,
                    },
                )
            reset_context(token)


MULTIPART_DEFAULT_LIMIT = 1024 * 1024
"""Лимит multipart-тела по умолчанию: импорт YAML ниши и шаблона баннера — до 256 КБ."""
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class UploadGuard:
    """multipart/form-data под `/api/`: лимит тела и вход проверяются до чтения тела.

    FastAPI разбирает форму (Starlette складывает файл во временный файл системного /tmp) раньше,
    чем выполняются зависимости `OperatorDep` и проверки размера сервиса. Без этой обёртки
    анонимный запрос успевает записать на диск весь файл и только потом получает 401. Здесь:

    * `Content-Length` больше лимита пути → 413 `payload_too_large` сразу, тело не читается;
    * вход, CSRF и роль (`deps.require`, как у небезопасных методов) → 401 / 403 без чтения тела;
      найденный принципал кладётся в `request.state`, зависимость роутера берёт его оттуда;
    * тело без `Content-Length` (chunked) считается по мере чтения и обрывается 413 на лимите.

    `limits` — точный путь → лимит в байтах; `pattern_limits` — пары (регулярное выражение пути,
    лимит) для путей с параметрами (`/jobs/{id}/author-voice`), проверяются после точных; остальные
    multipart-пути — `default_limit`. `limit_details` ищется по точному пути, затем по шаблону.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        api_prefix: str = "/api/",
        limits: Mapping[str, int] | None = None,
        default_limit: int = MULTIPART_DEFAULT_LIMIT,
        limit_details: Mapping[str, Mapping[str, Any]] | None = None,
        pattern_limits: Sequence[tuple[str, int]] | None = None,
    ) -> None:
        self.app = app
        self.api_prefix = api_prefix
        self.limits = dict(limits or {})
        self.default_limit = int(default_limit)
        self.limit_details = {k: dict(v) for k, v in (limit_details or {}).items()}
        self.pattern_limits = [(re.compile(rx), rx, int(n)) for rx, n in (pattern_limits or ())]

    def _limit_for(self, path: str) -> tuple[int, str]:
        """Лимит пути и ключ для `limit_details`: точный путь, затем первый совпавший шаблон."""
        if path in self.limits:
            return self.limits[path], path
        for rx, key, limit in self.pattern_limits:
            if rx.fullmatch(path):
                return limit, key
        return self.default_limit, path

    def _too_large(self, path: str, limit: int, size: int | None) -> PayloadTooLarge:
        details: dict[str, Any] = {**self.limit_details.get(path, {}), "limit_bytes": limit}
        if size is not None:
            details["size_bytes"] = size
        return PayloadTooLarge(f"Запрос больше {max(1, limit // (1024 * 1024))} МБ", details=details)

    @staticmethod
    async def _reply(scope: Scope, receive: Receive, send: Send, exc: AppError) -> None:
        from starlette.responses import JSONResponse

        await JSONResponse(exc.to_payload(), status_code=exc.status)(scope, receive, send)

    @staticmethod
    async def _authenticate(scope: Scope) -> AppError | None:
        from autoshorts.api.deps import require
        from autoshorts.domain.enums import Role

        try:
            await run_in_threadpool(require(Role.VIEWER), Request(scope))
        except AppError as exc:
            return exc
        except Exception:  # noqa: BLE001 - БД недоступна и т. п.: ответит обычный путь запроса
            log.warning("upload guard: auth check failed, deferring to route", exc_info=True)
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path: str = scope.get("path", "") if scope["type"] == "http" else ""
        if (
            scope["type"] != "http"
            or str(scope.get("method", "")).upper() not in _UNSAFE_METHODS
            or not path.startswith(self.api_prefix)
        ):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if not headers.get("content-type", "").lower().startswith("multipart/"):
            await self.app(scope, receive, send)
            return
        limit, limit_key = self._limit_for(path)
        raw_length = headers.get("content-length")
        declared = int(raw_length) if raw_length and raw_length.strip().isdigit() else None
        if declared is not None and declared > limit:
            await self._reply(scope, receive, send, self._too_large(limit_key, limit, declared))
            return
        denied = await self._authenticate(scope)
        if denied is not None:
            await self._reply(scope, receive, send, denied)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exc = self._too_large(limit_key, limit, None)
                    # FastAPI пробрасывает HTTPException из разбора тела как есть → обработчик app.py
                    raise HTTPException(
                        413, detail={"code": exc.code, "message": exc.message, "details": exc.details}
                    )
            return message

        await self.app(scope, limited_receive, send)
