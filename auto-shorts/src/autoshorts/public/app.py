"""Публичный контур: редиректор, лендинг, `/go/*`, приём конверсий (docs/ARCHITECTURE.md §5.3, §6.6).

Запуск: `uvicorn autoshorts.public.app:app --host 127.0.0.1 --port 18302 --workers 1 --proxy-headers
--forwarded-allow-ips 127.0.0.1` (deploy/systemd/autoshorts-public.service, хост `u.<домен>`).

Маршруты (статические объявлены **до** catch-all `/{code}`):

| метод | путь | поведение |
|---|---|---|
| GET | /health | `{"status":"ok"}`, без БД и без клика |
| GET | /robots.txt | `Disallow: /` |
| POST | /ingest/v1/events | приём конверсий с HMAC (`services/ingest.process`) |
| GET, HEAD | /go/{code}/tg | клик `to_telegram` → `tg://resolve…start=<code>_l`, https через 1,5 с |
| GET, HEAD | /go/{code}/site | клик `to_site` → 302 на лендинг продукта с utm |
| GET, HEAD | /{code} | см. ниже |

`/{code}`: код `^[A-Za-z0-9_-]{2,64}$`, иначе 404; дальше по `redirect_mode` продукта лендинг или прямой
302; неизвестный код — клик `unknown_code` и лендинг продукта по умолчанию (`redirect_default_product`).

* HEAD и боты по UA пишутся с `is_bot=true` (`public/botfilter.py`) и в статистику не идут.
* `visitor_key = hmac_sha256(REDIRECT_SALT, f"{day}|{ip}|{ua}")[:32]`, сырой IP не хранится.
* Лендинг — Jinja2 с autoescape, CSS инлайн, CSP `default-src 'none'` + sha256 скрипта, без внешних
  ресурсов (`public/landing.py`).
* Сорванный переход человека хуже потерянной строки статистики: без БД лендинг отдаётся по кешу
  продукта, клик теряется с предупреждением в логе.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import logging
import secrets as pysecrets
import threading
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from autoshorts.api.schemas.conversions import (
    INGEST_HEADER_PRODUCT,
    INGEST_HEADER_SIGNATURE,
    INGEST_HEADER_TIMESTAMP,
    INGEST_MAX_BODY_BYTES,
    INGEST_PATH,
)
from autoshorts.domain import tags
from autoshorts.public import landing
from autoshorts.public.botfilter import is_bot_request

log = logging.getLogger("autoshorts.public")

Row = dict[str, Any]

PRODUCT_CACHE_TTL_SEC = 30.0
PRODUCT_CACHE_STALE_SEC = 3600.0  # без БД лендинг живёт на кеше до часа
POOL_MAX_SIZE = 4
ROBOTS_TXT = "User-agent: *\nDisallow: /\n"


# ---------------------------------------------------------------- кеш продуктов


class ProductCache:
    """Продукты по id: TTL 30 с; при недоступной БД — последнее известное значение до часа."""

    def __init__(self, ttl: float = PRODUCT_CACHE_TTL_SEC, stale: float = PRODUCT_CACHE_STALE_SEC) -> None:
        self.ttl = ttl
        self.stale = stale
        self._rows: dict[str, tuple[float, Row | None]] = {}
        self._lock = threading.Lock()

    def get(self, product_id: str, loader: Any) -> Row | None:
        now = time.monotonic()
        with self._lock:
            cached = self._rows.get(product_id)
        if cached is not None and now - cached[0] < self.ttl:
            return cached[1]
        try:
            row = loader(product_id)
        except Exception:
            if cached is not None and now - cached[0] < self.stale:
                log.warning("product %s: database unavailable, using cached row", product_id)
                return cached[1]
            raise
        with self._lock:
            self._rows[product_id] = (now, row)
        return row

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()


products = ProductCache()


# ---------------------------------------------------------------- БД


def _connection() -> Any:
    from autoshorts.db.pool import connection

    return connection()


def _load_product(product_id: str) -> Row | None:
    from autoshorts.services.tracking import product_for

    with _connection() as conn:
        return product_for(conn, product_id)


def _settings() -> Any:
    from autoshorts.services.settings import get_runtime_settings

    return get_runtime_settings(fallback_to_defaults=True)


def _default_product() -> Row | None:
    return products.get(_settings().redirect_default_product, _load_product)


def _resolve(code: str) -> tuple[Row | None, str | None]:
    """(активная ссылка или None, код варианта баннера для utm_content)."""
    from autoshorts.services.tracking import resolve_code

    with _connection() as conn:
        link = resolve_code(conn, code)
        variant_code = None
        if link is not None and link.get("banner_variant_id") is not None:
            row = conn.execute(
                "SELECT code FROM banner_variants WHERE id = %(id)s", {"id": link["banner_variant_id"]}
            ).fetchone()
            variant_code = row["code"] if row else None
    return link, variant_code


# ---------------------------------------------------------------- клики


_fallback_salt = pysecrets.token_bytes(32)
_salt_warned = False


def _salt() -> bytes:
    global _salt_warned
    from autoshorts.core.config import get_env

    env = get_env()
    if env.redirect_salt is not None and env.redirect_salt.get_secret_value():
        return env.redirect_salt.get_secret_value().encode("utf-8")
    if not _salt_warned:
        log.warning("REDIRECT_SALT is not set: visitor_key uses a per-process salt (unique visitors drift)")
        _salt_warned = True
    return _fallback_salt


def business_day(now: datetime | None = None) -> str:
    tz = _settings().business_timezone
    try:
        zone = ZoneInfo(tz)
    except Exception:  # noqa: BLE001
        zone = ZoneInfo("Europe/Moscow")
    return (now or datetime.now(UTC)).astimezone(zone).date().isoformat()


def visitor_key(ip: str | None, user_agent: str | None, *, day: str | None = None) -> str:
    """`hmac_sha256(REDIRECT_SALT, f"{day}|{ip}|{ua}")[:32]` (§5.3)."""
    msg = f"{day or business_day()}|{ip or ''}|{user_agent or ''}".encode()
    return hmac.new(_salt(), msg, hashlib.sha256).hexdigest()[:32]


def _referer_host(request: Request) -> str | None:
    ref = request.headers.get("referer")
    if not ref:
        return None
    try:
        host = urlsplit(ref).hostname
    except ValueError:
        return None
    return host[:255] if host and host.isascii() else None


def _log_click(request: Request, *, code: str, route: str, link: Row | None, product_id: str | None) -> None:
    from autoshorts.repos import clicks as clicks_repo

    ua = request.headers.get("user-agent")
    ip = request.client.host if request.client else None
    try:
        with _connection() as conn:
            clicks_repo.insert(
                conn,
                code=code,
                route=route,
                visitor_key=visitor_key(ip, ua),
                is_bot=is_bot_request(request.method, ua),
                link=link,
                product_id=product_id,
                user_agent=ua,
                referer_host=_referer_host(request),
            )
    except Exception:  # noqa: BLE001 - переход зрителя важнее строки статистики
        log.exception("clicks: could not log %s click for code=%s", route, code)


# ---------------------------------------------------------------- ответы


def _html(body: str, *, status: int = 200, csp: str = landing.LANDING_CSP) -> HTMLResponse:
    return HTMLResponse(
        body, status_code=status, headers={**landing.COMMON_HEADERS, "Content-Security-Policy": csp}
    )


def not_found() -> HTMLResponse:
    return _html(landing.render_not_found(), status=404, csp=landing.PLAIN_CSP)


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=302, headers=dict(landing.COMMON_HEADERS))


def _unavailable() -> HTMLResponse:
    body = (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Скоро вернёмся</title></head>"
        '<body style="font-family:system-ui,sans-serif;margin:3rem auto;max-width:28rem;padding:0 1rem;'
        'text-align:center"><h1 style="font-size:1.4rem">Ссылка временно недоступна</h1>'
        "<p>Обновите страницу через минуту.</p></body></html>"
    )
    resp = _html(body, status=503, csp=landing.PLAIN_CSP)
    resp.headers["Retry-After"] = "30"
    return resp


def _telegram_target(product: Row | None, payload: str | None) -> tuple[str | None, str | None]:
    from autoshorts.services.tracking import telegram_urls

    if payload is None:
        bot = (product or {}).get("telegram_bot_username")
        if not bot:
            return None, None
        return f"tg://resolve?domain={bot}", f"https://telegram.me/{bot}"
    return telegram_urls(product, payload)


def _telegram_response(product: Row | None, payload: str | None) -> Response | None:
    app_url, web_url = _telegram_target(product, payload)
    if web_url is None:
        return None
    style = (product or {}).get("telegram_link_style") or "tg_scheme_with_fallback"
    if style == "https_tme" or app_url is None:
        return _redirect(web_url)
    return _html(landing.render_tg(product, app_url=app_url, web_url=web_url), csp=landing.TG_CSP)


def _site_target(
    product: Row | None, link: Row | None, code: str | None, variant_code: str | None
) -> str | None:
    from autoshorts.services.tracking import site_url

    return site_url(
        product,
        code,
        platform=(link or {}).get("platform"),
        variant_code=variant_code,
        override=(link or {}).get("target_override_url"),
    )


# ---------------------------------------------------------------- приложение


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from autoshorts.core.logging import configure_logging
    from autoshorts.db.pool import close_pool, init_pool

    configure_logging()
    try:
        init_pool(application_name="autoshorts-public", max_size=POOL_MAX_SIZE, timeout=5.0, wait=False)
    except Exception as exc:  # noqa: BLE001 - БД может подняться позже
        log.error("database pool init failed: %s", exc)
    try:
        yield
    finally:
        await anyio.to_thread.run_sync(close_pool)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Auto Shorts public",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        swagger_ui_oauth2_redirect_url=None,
        lifespan=lifespan,
    )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if request.url.path == INGEST_PATH:
            return JSONResponse(
                {
                    "error": {
                        "code": "method_not_allowed" if exc.status_code == 405 else "not_found",
                        "message": str(exc.detail),
                        "details": {},
                    }
                },
                status_code=exc.status_code,
            )
        if exc.status_code == 405:
            return PlainTextResponse("Method Not Allowed", status_code=405, headers={"Allow": "GET, HEAD"})
        return not_found()

    # --- статические маршруты: строго ДО catch-all /{code}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/robots.txt", response_class=PlainTextResponse)
    async def robots() -> PlainTextResponse:
        return PlainTextResponse(ROBOTS_TXT, headers={"Cache-Control": "public, max-age=86400"})

    @app.post(INGEST_PATH)
    async def ingest(request: Request) -> Response:
        return await ingest_endpoint(request)

    @app.api_route("/go/{code}/tg", methods=["GET", "HEAD"])
    def go_telegram(code: str, request: Request) -> Response:
        return go(code, "tg", request)

    @app.api_route("/go/{code}/site", methods=["GET", "HEAD"])
    def go_site(code: str, request: Request) -> Response:
        return go(code, "site", request)

    @app.api_route("/{code}", methods=["GET", "HEAD"])
    def redirect(code: str, request: Request) -> Response:
        return landing_or_redirect(code, request)

    return app


# ---------------------------------------------------------------- обработчики


def landing_or_redirect(code: str, request: Request) -> Response:
    if not tags.is_valid_code(code):
        return not_found()
    try:
        link, variant_code = _resolve(code)
    except Exception:  # noqa: BLE001 - БД лежит: лендинг продукта по умолчанию из кеша
        log.exception("redirect: code lookup failed for %s", code)
        try:
            product = _default_product()
        except Exception:  # noqa: BLE001
            return _unavailable()
        return _landing(product, code=None, link_code=code)
    if link is None:
        try:
            product = _default_product()
        except Exception:  # noqa: BLE001
            return _unavailable()
        _log_click(request, code=code, route="unknown_code", link=None, product_id=(product or {}).get("id"))
        return _landing(product, code=None, link_code=code)
    try:
        product = products.get(link["product_id"], _load_product)
    except Exception:  # noqa: BLE001
        return _unavailable()
    mode = (product or {}).get("redirect_mode") or "landing_split"
    real_code = link["code"]
    if mode == "direct_site":
        target = _site_target(product, link, real_code, variant_code)
        if target:
            _log_click(request, code=real_code, route="direct", link=link, product_id=link["product_id"])
            return _redirect(target)
    elif mode == "direct_telegram":
        resp = _telegram_response(product, real_code)
        if resp is not None:
            _log_click(request, code=real_code, route="direct", link=link, product_id=link["product_id"])
            return resp
    _log_click(request, code=real_code, route="landing", link=link, product_id=link["product_id"])
    return _landing(product, code=real_code, link_code=real_code)


def _landing(product: Row | None, *, code: str | None, link_code: str) -> Response:
    """Лендинг: кнопки ведут на свои `/go/<code>/…` (там пишется клик и строится ссылка)."""
    has_tg = bool((product or {}).get("telegram_bot_username"))
    has_site = bool((product or {}).get("landing_url"))
    base = f"/go/{quote(link_code, safe='')}"
    html = landing.render_landing(
        product,
        code=code,
        telegram_href=f"{base}/tg" if has_tg else None,
        site_href=f"{base}/site" if has_site else None,
    )
    return _html(html)


def go(code: str, target: str, request: Request) -> Response:
    if not tags.is_valid_code(code):
        return not_found()
    try:
        link, variant_code = _resolve(code)
        product = products.get(link["product_id"], _load_product) if link else _default_product()
    except Exception:  # noqa: BLE001
        log.exception("go: lookup failed for %s", code)
        return _unavailable()
    real_code = link["code"] if link else None
    route = "to_telegram" if target == "tg" else "to_site"
    if target == "tg":
        payload = tags.with_landing_suffix(real_code) if real_code else None
        resp = _telegram_response(product, payload)
    else:
        url = _site_target(product, link, real_code, variant_code)
        resp = _redirect(url) if url else None
    if resp is None:
        return not_found()
    _log_click(request, code=real_code or code, route=route, link=link, product_id=(product or {}).get("id"))
    return resp


async def _read_body(request: Request, limit: int) -> tuple[bytes | None, int]:
    """Тело с ограничением: больше `limit` — (None, прочитано); дальше не читаем."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        return None, int(declared)
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            return None, len(buf)
    return bytes(buf), len(buf)


async def ingest_endpoint(request: Request) -> Response:
    from autoshorts.services import ingest as ingest_service

    body, size = await _read_body(request, INGEST_MAX_BODY_BYTES)
    raw = ingest_service.IngestRaw(
        product_header=request.headers.get(INGEST_HEADER_PRODUCT),
        timestamp_header=request.headers.get(INGEST_HEADER_TIMESTAMP),
        signature_header=request.headers.get(INGEST_HEADER_SIGNATURE),
        body=body,
        body_bytes=size,
        request_id=(request.headers.get("x-request-id") or "")[:100] or None,
    )

    def run() -> Any:
        with _connection() as conn:
            return ingest_service.process(conn, raw)

    try:
        result = await anyio.to_thread.run_sync(run)
    except Exception:  # noqa: BLE001 - БД недоступна: продукт повторит позже
        log.exception("ingest: processing failed")
        return JSONResponse(
            {"error": {"code": "not_ready", "message": "Приём событий временно недоступен", "details": {}}},
            status_code=503,
            headers={"Retry-After": "60"},
        )
    return JSONResponse(result.body, status_code=result.status, headers=result.headers or None)


app = create_app()

__all__ = [
    "ProductCache",
    "app",
    "business_day",
    "create_app",
    "go",
    "ingest_endpoint",
    "landing_or_redirect",
    "not_found",
    "products",
    "visitor_key",
]
