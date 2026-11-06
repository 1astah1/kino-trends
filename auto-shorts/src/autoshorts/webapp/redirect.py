"""
Редирект-сервер домена — первичный приёмник воронки (ТЗ §6: "домен + бот параллельно").
Лендинг разветвляет на два пути: открыть в Telegram / продолжить на сайте.
Метка не теряется ни на одной ветке — обе сходятся в attribution_events по одному tag.

M0-хотфикс (ARCHITECTURE §3.3 п.2, §5.3). В M3 модуль заменяется на public/app.py.
- Статические маршруты (/health, /robots.txt) объявлены ДО catch-all /{tag}, иначе они
  попадают в атрибуцию.
- Метка обязана пройти ^[A-Za-z0-9_-]{2,64}$, иначе 404: кириллица и мусор не пишутся.
- В HTML метка попадает только через html.escape, в JS — через data-атрибут и JSON.parse
  (json.dumps + html.escape). Сам скрипт статичен и закреплён в CSP по sha256.
- HEAD-запросы и боты-превьюеры (TelegramBot, facebookexternalhit и т. п.) получают лендинг,
  но в attribution_events не пишутся.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import re
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from autoshorts import db
from autoshorts.config import SETTINGS
from autoshorts.pipeline.attribution import build_telegram_deeplink

logger = logging.getLogger("autoshorts.redirect")

app = FastAPI(title="Auto Shorts — Attribution Redirect", docs_url=None, redoc_url=None, openapi_url=None)

TAG_RE = re.compile(r"[A-Za-z0-9_-]{2,64}")

# Подстроки User-Agent (без учёта регистра), по которым запрос считается ботом или превьюером
# ссылок (ARCHITECTURE §5.3). Такие переходы не пишутся в attribution_events.
BOT_UA_MARKERS: tuple[str, ...] = tuple(m.lower() for m in (
    "bot", "crawler", "spider", "preview",
    "TelegramBot", "vkShare", "facebookexternalhit", "WhatsApp", "Discordbot", "Slackbot",
    "Googlebot", "YandexBot", "curl", "python-httpx",
))

_LANDING_SCRIPT = (
    "(function(){try{"
    "var el=document.getElementById('as-landing');"
    # Метка кладётся в localStorage при первом заходе — доживает до регистрации/оплаты (ТЗ §6)
    "localStorage.setItem('as_attribution_tag',JSON.parse(el.getAttribute('data-tag')));"
    "}catch(e){}})();"
)
_LANDING_SCRIPT_HASH = base64.b64encode(hashlib.sha256(_LANDING_SCRIPT.encode("utf-8")).digest()).decode("ascii")

LANDING_HEADERS = {
    "Content-Security-Policy": (
        f"default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-{_LANDING_SCRIPT_HASH}'; "
        "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

_LANDING_TEMPLATE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>umka.chat</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #1a1926; color: #ece9f5;
          display: flex; flex-direction: column; align-items: center; justify-content: center;
          min-height: 100vh; margin: 0; padding: 24px; text-align: center; }}
  h1 {{ font-size: 22px; margin-bottom: 8px; }}
  p {{ color: #a39fc0; margin-bottom: 28px; }}
  a.btn {{ display: block; width: 100%; max-width: 320px; padding: 16px; margin-bottom: 12px;
           border-radius: 10px; text-decoration: none; font-weight: 600; font-size: 16px; }}
  .primary {{ background: #4b3fa8; color: white; }}
  .secondary {{ background: #2a2840; color: #ece9f5; border: 1px solid #3f3f58; }}
</style></head>
<body id="as-landing" data-tag="{tag_json_attr}">
  <h1>Бесплатный расклад ждёт тебя</h1>
  <p>Выбери, как удобнее продолжить</p>
  <a class="btn primary" href="{telegram_url_attr}">Открыть в Telegram</a>
  <a class="btn secondary" href="{site_url_attr}">Продолжить на сайте</a>
  <script>{script}</script>
</body></html>"""


def is_valid_tag(tag: str) -> bool:
    return TAG_RE.fullmatch(tag) is not None


def is_bot_user_agent(user_agent: str | None) -> bool:
    """Боты-превьюеры и скрипты. Пустой User-Agent живые браузеры не шлют — тоже бот."""
    ua = (user_agent or "").strip().lower()
    if not ua:
        return True
    return any(marker in ua for marker in BOT_UA_MARKERS)


def render_landing(tag: str) -> str:
    """HTML лендинга. Метка экранируется в любом случае, даже если валидация её пропустила."""
    telegram_url = build_telegram_deeplink(quote(tag, safe=""))
    site_url = f"https://umka.chat/?utm_source=redirect&utm_medium=shorts&utm_campaign={quote(tag, safe='')}"
    return _LANDING_TEMPLATE.format(
        tag_json_attr=html.escape(json.dumps(tag), quote=True),
        telegram_url_attr=html.escape(telegram_url, quote=True),
        site_url_attr=html.escape(site_url, quote=True),
        script=_LANDING_SCRIPT,
    )


def _hash_ip(ip: str) -> str:
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def _log_click(tag: str, request: Request) -> None:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO attribution_events (tag, source, route, ip_hash, user_agent)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                tag,
                tag.split("_")[0] if "_" in tag else "unknown",
                "domain",
                _hash_ip(request.client.host if request.client else "unknown"),
                request.headers.get("user-agent", "")[:512],
            ),
        )


# --- Статические маршруты: строго ДО catch-all /{tag} ---

@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "domain": SETTINGS.redirect_domain}


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots() -> str:
    return "User-agent: *\nDisallow: /\n"


# --- Catch-all: метка атрибуции ---

@app.api_route("/{tag}", methods=["GET", "HEAD"], response_class=HTMLResponse)
def redirect_landing(tag: str, request: Request) -> HTMLResponse:
    # Синхронный обработчик: FastAPI уводит его в threadpool, блокирующий psycopg не держит event loop.
    if not is_valid_tag(tag):
        raise HTTPException(status_code=404)

    if request.method != "HEAD" and not is_bot_user_agent(request.headers.get("user-agent")):
        try:
            _log_click(tag, request)
        except Exception:
            # Сорванный переход человека хуже потерянной строки статистики: лендинг отдаётся и без БД.
            logger.exception("attribution_events: не удалось записать переход tag=%s", tag)

    return HTMLResponse(render_landing(tag), headers=LANDING_HEADERS)
