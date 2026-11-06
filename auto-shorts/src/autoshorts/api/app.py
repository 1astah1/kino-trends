"""Фабрика FastAPI-приложения `/api/v1` (docs/ARCHITECTURE.md §2.2, §5.1, §10.1 п. 2).

Запуск: `uvicorn autoshorts.api.app:app --host 127.0.0.1 --port 18300 --workers 1`.

- Роутеры подхватываются автоматически: каждый модуль `autoshorts.api.routers.*` с атрибутом
  `router` (APIRouter) подключается с префиксом `/api/v1`, в алфавитном порядке имён модулей.
- `/docs` и `/redoc` выключены всегда: Swagger UI и ReDoc грузятся с cdn.jsdelivr.net, а у оператора
  из РФ внешние CDN недоступны (§5.1, §7.7). Схема для кодогенерации: `GET /api/v1/openapi.json`.
- operationId = `<tag>_<name>` (латиница), ошибки 4XX/5XX описаны единой схемой `ErrorResponse`.
- Ошибки: `core.errors.AppError` → `{"error": {code, message, details}}` со статусом исключения;
  422 валидации — `validation_error` с `details.errors=[{loc,msg,type}]`; недоступная БД — 503
  `not_ready`; необработанное — 500 `internal_error` (с `request_id` в details).
- Старт (lifespan): логирование (`core.logging`), пул БД (не блокирует старт, если БД лежит),
  сверка схемы с файлами миграций. Отстаёт — API не падает, но бизнес-эндпоинты отвечают 503
  `migrations_pending`, `/health/ready` — 503; после `autoshorts-admin migrate` ворота открываются
  сами (перепроверка раз в 15 с).
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import pkgutil
from collections.abc import AsyncIterator, Iterator
from importlib.metadata import PackageNotFoundError, version

import psycopg
from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from psycopg_pool import PoolTimeout
from starlette.exceptions import HTTPException as StarletteHTTPException

from autoshorts.api.middleware import RequestContextMiddleware, SchemaGate, UploadGuard
from autoshorts.api.schemas.banners import MAX_CREATIVE_UPLOAD_BYTES
from autoshorts.api.schemas.clipping import AUTHOR_VOICE_MAX_BYTES
from autoshorts.api.schemas.common import ErrorResponse
from autoshorts.api.sse import EventBroadcaster
from autoshorts.core.errors import AppError

API_PREFIX = "/api/v1"
OPENAPI_URL = f"{API_PREFIX}/openapi.json"
HEALTH_PATHS = frozenset({f"{API_PREFIX}/health", f"{API_PREFIX}/health/ready"})
CREATIVE_UPLOAD_PATH = f"{API_PREFIX}/banner-creatives"
CREATIVE_UPLOAD_LIMIT = MAX_CREATIVE_UPLOAD_BYTES + 1024 * 1024
# Запись голоса автора (multipart, до 20 МБ): путь с параметром, поэтому лимит по шаблону.
AUTHOR_VOICE_UPLOAD_PATTERN = rf"{API_PREFIX}/jobs/\d+/author-voice"
AUTHOR_VOICE_UPLOAD_LIMIT = AUTHOR_VOICE_MAX_BYTES + 1024 * 1024
"""Тело multipart креатива: файл ≤ 50 МБ + заголовки частей и поля формы (nginx: 51m)."""
POOL_MAX_SIZE = 8
POOL_TIMEOUT_SEC = 10.0

log = logging.getLogger(__name__)

COMMON_RESPONSES: dict[int | str, dict] = {
    "4XX": {"model": ErrorResponse, "description": "Ошибка запроса: `{error: {code, message, details}}`"},
    "5XX": {"model": ErrorResponse, "description": "Ошибка сервера или 501 not_implemented"},
}

_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "not_authenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    423: "locked",
    429: "rate_limited",
    501: "not_implemented",
    503: "not_ready",
    507: "insufficient_storage",
}


def _app_version() -> str:
    try:
        return version("autoshorts")
    except PackageNotFoundError:
        return "0.0.0"


def operation_id(route: APIRoute) -> str:
    """`<tag>_<name>`: `jobs_list`, `auth_login`. Без тега — только имя."""
    tag = str(route.tags[0]) if route.tags else ""
    return f"{tag}_{route.name}" if tag else route.name


def discover_routers() -> Iterator[tuple[str, APIRouter]]:
    """Модули `autoshorts.api.routers.*` с атрибутом `router`, в алфавитном порядке."""
    import autoshorts.api.routers as pkg

    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{pkg.__name__}.{info.name}")
        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            yield info.name, router


def _error(
    status_code: int,
    code: str,
    message: str,
    details: dict | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details or {}}},
        headers=headers,
    )


def _retry_after(details: dict) -> dict[str, str] | None:
    value = details.get("retry_after_sec") if isinstance(details, dict) else None
    if isinstance(value, int | float) and value > 0:
        return {"Retry-After": str(int(value))}
    return None


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_request: Request, exc: AppError) -> JSONResponse:
        if exc.status >= 500:
            log.warning("app error %s: %s", exc.code, exc.message)
        return JSONResponse(
            status_code=exc.status, content=exc.to_payload(), headers=_retry_after(exc.details)
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "loc": [str(p) if not isinstance(p, int) else p for p in e.get("loc", ())],
                "msg": str(e.get("msg", "")),
                "type": str(e.get("type", "")),
            }
            for e in exc.errors()
        ]
        return _error(422, "validation_error", "Ошибка валидации запроса", {"errors": errors})

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            return _error(
                exc.status_code, str(detail["code"]), str(detail.get("message", "")), detail.get("details")
            )
        code = _STATUS_CODES.get(exc.status_code, "error")
        message = detail if isinstance(detail, str) else code
        return _error(exc.status_code, code, message, headers=dict(exc.headers) if exc.headers else None)

    @app.exception_handler(PoolTimeout)
    @app.exception_handler(psycopg.OperationalError)
    async def _db_unavailable(_request: Request, exc: Exception) -> JSONResponse:
        log.warning("database unavailable: %s", str(exc).splitlines()[0] if str(exc) else type(exc).__name__)
        return _error(
            503,
            "not_ready",
            "База данных недоступна",
            {"reason": "db_unavailable"},
            headers={"Retry-After": "10"},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        rid = getattr(request.state, "request_id", None)
        log.exception("unhandled error in %s %s", request.method, request.url.path)
        return _error(500, "internal_error", "Внутренняя ошибка", {"request_id": rid})


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from autoshorts.core.logging import configure_logging
    from autoshorts.db.pool import close_pool, init_pool

    configure_logging()
    try:
        init_pool(
            application_name="autoshorts-api", max_size=POOL_MAX_SIZE, timeout=POOL_TIMEOUT_SEC, wait=False
        )
    except Exception as exc:  # noqa: BLE001 - БД может подняться позже; /health/ready скажет
        log.error("database pool init failed: %s", exc)
    gate: SchemaGate = app.state.schema_gate
    state = await gate.refresh()
    if state is None:
        log.error("schema check failed (database unavailable?): %s", gate.last_error)
    elif not state.ok:
        log.error(
            "database schema is behind migrations (current %s, latest %s, pending %s): "
            "API answers 503 migrations_pending until `autoshorts-admin migrate`",
            state.current,
            state.latest,
            list(state.pending),
        )
    watcher = (
        asyncio.create_task(gate.watch(), name="autoshorts-schema-gate")
        if not gate.state or not gate.state.ok
        else None
    )
    try:
        yield
    finally:
        if watcher is not None:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await watcher
        await app.state.events.close()
        await asyncio.to_thread(close_pool)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Auto Shorts API",
        version=_app_version(),
        openapi_url=OPENAPI_URL,
        docs_url=None,
        redoc_url=None,
        swagger_ui_oauth2_redirect_url=None,
        generate_unique_id_function=operation_id,
        separate_input_output_schemas=False,
        lifespan=lifespan,
    )
    gate = SchemaGate(exempt_paths=HEALTH_PATHS | {OPENAPI_URL})
    app.state.schema_gate = gate
    app.state.events = EventBroadcaster()
    app.state.sse_ping_interval = None  # None — SSE_PING_INTERVAL_SEC (15 с); тесты ставят меньше
    _install_error_handlers(app)
    # Порядок: последний добавленный — внешний. RequestContext снаружи (request_id, лог, ворота миграций),
    # UploadGuard внутри него: multipart отсекается по размеру и входу до чтения тела.
    app.add_middleware(
        UploadGuard,
        api_prefix="/api/",
        limits={CREATIVE_UPLOAD_PATH: CREATIVE_UPLOAD_LIMIT},
        pattern_limits=[(AUTHOR_VOICE_UPLOAD_PATTERN, AUTHOR_VOICE_UPLOAD_LIMIT)],
        limit_details={
            CREATIVE_UPLOAD_PATH: {"asset_error": "file_too_large"},
            AUTHOR_VOICE_UPLOAD_PATTERN: {"asset_error": "file_too_large"},
        },
    )
    app.add_middleware(RequestContextMiddleware, api_prefix="/api/", gate=gate, quiet_paths=HEALTH_PATHS)
    for name, router in discover_routers():
        app.include_router(router, prefix=API_PREFIX, responses=COMMON_RESPONSES)
        log.debug("router %s included", name)
    return app


app = create_app()
