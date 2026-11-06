"""HTML публичного контура: лендинг `/{code}`, переход в Telegram `/go/{code}/tg`, 404 (§5.3).

* Jinja2 с autoescape, CSS инлайн, **без внешних ресурсов** (у зрителей из РФ чужие CDN не грузятся).
* Скрипты статичны и закреплены в CSP по sha256; код ролика попадает в JS только через
  `data-*`-атрибут и `JSON.parse` (атрибут экранирует Jinja). Лендинг кладёт код в `localStorage`
  (`as_code`) для веб-ветки продукта.
* Шаблоны лежат в модуле, а не в файлах: пакет ставится без package-data для HTML.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any

from jinja2 import DictLoader, Environment, select_autoescape

LANDING_SCRIPT = (
    "(function(){try{var el=document.getElementById('as-landing');"
    "var c=JSON.parse(el.getAttribute('data-code'));"
    "if(c){localStorage.setItem('as_code',c);localStorage.setItem('as_code_at',String(Date.now()));}"
    "}catch(e){}})();"
)
TG_SCRIPT = (
    "(function(){var el=document.getElementById('as-tg');"
    "var app=JSON.parse(el.getAttribute('data-app'));var web=JSON.parse(el.getAttribute('data-web'));"
    "try{window.location.href=app;}catch(e){}"
    "setTimeout(function(){window.location.replace(web);},1500);})();"
)
TG_FALLBACK_DELAY_MS = 1500
TG_META_REFRESH_SEC = 3  # без JS: запасная https-ссылка через 3 с


def script_hash(script: str) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode("ascii")


def csp(script: str | None) -> str:
    script_src = f"'{script_hash(script)}'" if script else "'none'"
    return (
        f"default-src 'none'; style-src 'unsafe-inline'; script-src {script_src}; img-src 'self' data:; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )


LANDING_CSP = csp(LANDING_SCRIPT)
TG_CSP = csp(TG_SCRIPT)
PLAIN_CSP = csp(None)

COMMON_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow",
}

_BASE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
{% block head %}{% endblock %}
<title>{{ page_title }}</title>
<style>
  :root { --accent: {{ accent }}; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; background: #15141f;
         color: #ece9f5; display: flex; flex-direction: column; align-items: center; justify-content: center;
         min-height: 100vh; margin: 0; padding: 24px 16px; text-align: center; }
  h1 { font-size: 22px; line-height: 1.3; margin: 0 0 8px; }
  p { color: #a9a4c6; margin: 0 0 28px; font-size: 16px; line-height: 1.45; }
  a.btn { display: block; width: 100%; max-width: 340px; padding: 16px; margin: 0 auto 12px;
          border-radius: 12px; text-decoration: none; font-weight: 600; font-size: 17px; }
  .primary { background: var(--accent); color: #fff; }
  .secondary { background: #262438; color: #ece9f5; border: 1px solid #3c3a55; }
  .domain { margin-top: 20px; font-size: 13px; color: #6f6a8c; }
</style></head>
<body{% block body_attrs %}{% endblock %}>
{% block body %}{% endblock %}
</body></html>"""

_LANDING = """{% extends "base.html" %}
{% block body_attrs %} id="as-landing" data-code="{{ code_json }}"{% endblock %}
{% block body %}
  <h1>{{ title }}</h1>
  {% if subtitle %}<p>{{ subtitle }}</p>{% endif %}
  {% if telegram_href %}<a class="btn primary" href="{{ telegram_href }}">{{ button_telegram }}</a>{% endif %}
  {% if site_href %}<a class="btn {{ 'secondary' if telegram_href else 'primary' }}"
     href="{{ site_href }}">{{ button_site }}</a>{% endif %}
  {% if domain %}<div class="domain">{{ domain }}</div>{% endif %}
  <script>{{ script | safe }}</script>
{% endblock %}"""

_TG = """{% extends "base.html" %}
{% block head %}<meta http-equiv="refresh" content="{{ refresh_sec }};url={{ web_url }}">{% endblock %}
{% block body_attrs %} id="as-tg" data-app="{{ app_json }}" data-web="{{ web_json }}"{% endblock %}
{% block body %}
  <h1>Открываем Telegram…</h1>
  <p>Если ничего не произошло, нажмите кнопку.</p>
  <a class="btn primary" href="{{ web_url }}">{{ button_telegram }}</a>
  <a class="btn secondary" href="{{ app_url }}">Открыть в приложении</a>
  <script>{{ script | safe }}</script>
{% endblock %}"""

_NOT_FOUND = """{% extends "base.html" %}
{% block body %}
  <h1>Ссылка не найдена</h1>
  <p>Проверьте адрес: код ролика — латинские буквы и цифры.</p>
{% endblock %}"""

_env = Environment(
    loader=DictLoader({"base.html": _BASE, "landing.html": _LANDING, "tg.html": _TG, "404.html": _NOT_FOUND}),
    autoescape=select_autoescape(default=True, default_for_string=True),
    auto_reload=False,
)

DEFAULT_LANDING = {
    "title": "Продолжим?",
    "subtitle": "Выбери, как удобнее продолжить",
    "button_telegram": "Открыть в Telegram",
    "button_site": "Продолжить на сайте",
}
DEFAULT_ACCENT = "#4b3fa8"
_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?")


def _accent(product: dict[str, Any] | None) -> str:
    brand = (product or {}).get("brand") or {}
    color = brand.get("primary_color") if isinstance(brand, dict) else None
    return color if isinstance(color, str) and _COLOR_RE.fullmatch(color) else DEFAULT_ACCENT


def _texts(product: dict[str, Any] | None) -> dict[str, str]:
    landing = (product or {}).get("landing") or {}
    out = dict(DEFAULT_LANDING)
    if isinstance(landing, dict):
        for key in out:
            val = landing.get(key)
            if isinstance(val, str) and val.strip():
                out[key] = val.strip()[:300]
    return out


def display_domain(product: dict[str, Any] | None) -> str:
    brand = (product or {}).get("brand") or {}
    if isinstance(brand, dict) and brand.get("display_domain"):
        return str(brand["display_domain"])[:100]
    landing = (product or {}).get("landing_url") or ""
    if "://" in landing:
        return landing.split("://", 1)[1].split("/", 1)[0][:100]
    return str((product or {}).get("name") or "")[:100]


def render_landing(
    product: dict[str, Any] | None, *, code: str | None, telegram_href: str | None, site_href: str | None
) -> str:
    texts = _texts(product)
    domain = display_domain(product)
    return _env.get_template("landing.html").render(
        page_title=domain or texts["title"],
        accent=_accent(product),
        code_json=json.dumps(code),
        title=texts["title"],
        subtitle=texts["subtitle"],
        button_telegram=texts["button_telegram"],
        button_site=texts["button_site"],
        telegram_href=telegram_href,
        site_href=site_href,
        domain=domain,
        script=LANDING_SCRIPT,
    )


def render_tg(product: dict[str, Any] | None, *, app_url: str, web_url: str) -> str:
    texts = _texts(product)
    return _env.get_template("tg.html").render(
        page_title="Telegram",
        accent=_accent(product),
        app_json=json.dumps(app_url),
        web_json=json.dumps(web_url),
        app_url=app_url,
        web_url=web_url,
        refresh_sec=TG_META_REFRESH_SEC,
        button_telegram=texts["button_telegram"],
        script=TG_SCRIPT,
    )


def render_not_found() -> str:
    return _env.get_template("404.html").render(page_title="Не найдено", accent=DEFAULT_ACCENT)


__all__ = [
    "COMMON_HEADERS",
    "LANDING_CSP",
    "LANDING_SCRIPT",
    "PLAIN_CSP",
    "TG_CSP",
    "TG_SCRIPT",
    "csp",
    "display_domain",
    "render_landing",
    "render_not_found",
    "render_tg",
    "script_hash",
]
