#!/usr/bin/env python3
"""Смоук развёрнутого Auto Shorts (docs/ARCHITECTURE.md §8.5 п. 7, критерии M1 и M3 в §10.2).

Проверяет здоровье api и public, выключенные /docs, формат ошибок, вход, CSRF, SSE, заголовки
nginx и — на стенде — сквозной сценарий M3 (`stand_*`): ролик → приёмка → пакет → регистрация
ссылки → клик по коду на публичном хосте → подписанный HMAC `bot_start` → карточка публикации
показывает 1 посетителя и 1 старт.
Только стандартная библиотека: запускается и из .venv на VPS, и с машины оператора.

Режимы:
  --local          api и public напрямую (по умолчанию 127.0.0.1:18300 и 127.0.0.1:18302), без nginx.
                   Сценарий M3 — только с --flow
  --stand          стенд за nginx: плюс статика, CSP, закрытость /_protected_media/, HEAD лендинга
                   и сценарий M3 (отключается --no-flow). Проверки под логином обязательны (§1.5):
                   без учётки прогон считается проваленным
  --prod-readonly  прод после выкладки: только чтение. Единственная запись — сессия входа и выхода

Сценарий M3 пишет данные (одобряет ролик, регистрирует публикацию со случайной ссылкой, пишет клик
и конверсию), поэтому ролик выбирается явно:
  --job-id N             готовый ролик в pending_review (или уже approved без публикации)
  --clip-candidate N     новый clip-job из кандидата нарезки; ждём pending_review (рендер на стенде)
  --channel ID [--topic] новый generate-job в канале (--mock-providers — провайдеры mock)
Учётке нужна роль operator; секрет вебхука продукта — AS_SMOKE_WEBHOOK_SECRET или
--rotate-webhook-secret (admin; прежний секрет ещё 24 ч принимается). --cleanup (admin) в конце
мягко удаляет тестовую публикацию (ролик снова approved) — нужно там, где коллекторы опрашивают
площадку: ссылка выдуманная.

Примеры:
  .venv/bin/python scripts/e2e_smoke.py --local
  AS_SMOKE_LOGIN=smoke AS_SMOKE_PASSWORD=... .venv/bin/python scripts/e2e_smoke.py --stand \\
      --admin-url https://admin.stand.example --public-url https://u.stand.example --job-id 42
  # локальный стенд scripts/stand.sh (порты 18300/18301/18302) или свой набор портов:
  AS_SMOKE_LOGIN=admin AS_SMOKE_PASSWORD=... .venv/bin/python scripts/e2e_smoke.py --local --flow \\
      --api-port 18310 --public-port 18312 --job-id 7 --rotate-webhook-secret --cleanup
  .venv/bin/python scripts/e2e_smoke.py --prod-readonly --json

Адреса: --admin-url / AS_SMOKE_ADMIN_URL (admin-хост, за ним /api/), --public-url / AS_SMOKE_PUBLIC_URL,
или для --local порты --api-port / --public-port / --web-port на --host (по умолчанию 127.0.0.1).
Учётка: --login или AS_SMOKE_LOGIN (запасное имя E2E_LOGIN, как у Playwright); пароль только из
окружения AS_SMOKE_PASSWORD (E2E_PASSWORD) — в аргументах он попал бы в историю и в ps.
Без сценария M3 лучше отдельный пользователь с ролью viewer.

Коды выхода: 0 — провалов нет (пропуски допустимы), 1 — есть провалы, 2 — ошибка вызова.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.cookiejar
import json
import os
import re
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

API_PREFIX = "/api/v1"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_API_PORT = 18300
DEFAULT_PUBLIC_PORT = 18302
DEFAULT_LOCAL_API = f"http://{DEFAULT_HOST}:{DEFAULT_API_PORT}"
DEFAULT_LOCAL_PUBLIC = f"http://{DEFAULT_HOST}:{DEFAULT_PUBLIC_PORT}"
USER_AGENT = "autoshorts-e2e-smoke/1 (bot)"  # «bot» в UA: редиректор не считает запрос кликом (§5.3)
# Клик зрителя по коду с плашки: обычный мобильный браузер, иначе botfilter не посчитает переход
VIEWER_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/18.0 Mobile/15E148 Safari/604.1"
)
# Приём конверсий (api/schemas/conversions.py; здесь копия, скрипт без зависимостей от пакета)
INGEST_PATH = "/ingest/v1/events"
INGEST_SIGNATURE_PREFIX = "sha256="
JOB_READY_STATUSES = ("pending_review", "approved")
JOB_DEAD_STATUSES = ("qc_failed", "failed", "cancelled", "rejected", "published", "draft")
PACKAGE_READY_STATUSES = ("ready", "downloaded")
MAX_BODY_BYTES = 2 * 1024 * 1024
SSE_TIMEOUT_SEC = 20.0  # ping раз в 15 с (§5.1)

EXPECTED_ADMIN_CSP_PARTS = ("default-src 'self'", "frame-ancestors 'none'", "connect-src 'self'")
EXTERNAL_REF_RE = re.compile(
    r"""\b(?:src|href|srcset|poster|action|data)\s*=\s*["']\s*((?:https?:)?//[^"'\s>]+)""", re.I
)

PASS, FAIL, SKIP = "pass", "fail", "skip"


def ingest_signature(secret: str, timestamp: int, raw_body: bytes) -> str:
    """`sha256=<hex(HMAC_SHA256(secret, f"{ts}." + body))>` (`api/schemas/conversions.ingest_signature`)."""
    msg = f"{int(timestamp)}.".encode("ascii") + raw_body
    return INGEST_SIGNATURE_PREFIX + hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def fake_video_url(platform: str, channel_ref: str | None = None) -> str:
    """Выдуманная, но валидная ссылка площадки канала: каждый прогон — новый ролик (иначе 409)."""
    if platform == "vk":
        owner = channel_ref if channel_ref and re.fullmatch(r"-?[1-9]\d{0,18}", channel_ref) else "-1"
        return f"https://vk.com/clip{owner}_{secrets.randbelow(10**9) + 1}"
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
    video_id = "S" + "".join(secrets.choice(alphabet) for _ in range(10))
    return f"https://www.youtube.com/shorts/{video_id}"


class SmokeError(Exception):
    """Ожидаемый провал проверки: печатается одной строкой."""


# ───────────────────────────────────────────────────────────── HTTP


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes = b""

    def header(self, name: str) -> str:
        return self.headers.get(name.lower(), "")

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except ValueError as exc:
            raise SmokeError(f"ответ не JSON (HTTP {self.status}): {self.text()[:120]!r}") from exc


class Transport(Protocol):
    """То, чем смоук ходит в сеть. Подменяется в тестах (например, на Starlette TestClient).

    `raw_body` — байты как есть (подписанный HMAC запрос приёма конверсий), `json_body` — объект.
    """

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        raw_body: bytes | None = None,
        stream: bool = False,
        timeout: float | None = None,
    ) -> Response: ...

    def cookie(self, name: str) -> str | None: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Редиректы не выполняются: смоуку важен сам ответ (301 на https, 302 редиректора)."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class UrllibTransport:
    def __init__(self, *, timeout: float, insecure: bool = False) -> None:
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        context = ssl.create_default_context()
        if insecure:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        self.opener = urllib.request.build_opener(
            _NoRedirect(),
            urllib.request.HTTPCookieProcessor(self.jar),
            urllib.request.HTTPSHandler(context=context),
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        raw_body: bytes | None = None,
        stream: bool = False,
        timeout: float | None = None,
    ) -> Response:
        data = raw_body
        all_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            all_headers["Content-Type"] = "application/json"
        all_headers.update(headers or {})
        req = urllib.request.Request(url, data=data, method=method, headers=all_headers)
        try:
            resp = self.opener.open(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as exc:  # 3xx/4xx/5xx — тоже ответ
            resp = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise SmokeError(f"нет ответа от {url}: {reason}") from exc
        try:
            status = resp.status if resp.status is not None else resp.code
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            body = b"" if (stream or method == "HEAD") else resp.read(MAX_BODY_BYTES)
        finally:
            resp.close()
        return Response(status=status, headers=hdrs, body=body)

    def cookie(self, name: str) -> str | None:
        for item in self.jar:
            if item.name == name:
                return item.value
        return None


# ───────────────────────────────────────────────────────────── проверки


@dataclass
class Result:
    check: str
    status: str
    detail: str
    duration_ms: int = 0


@dataclass
class Config:
    mode: str
    api_url: str
    public_url: str
    web_url: str | None
    login: str | None
    password: str | None = field(repr=False, default=None)
    require_login: bool = True
    probe_code: str = "smoke_probe"
    # сценарий M3 (stand_*)
    flow: bool = False
    job_id: int | None = None
    clip_candidate_id: int | None = None
    channel_id: str | None = None
    topic: str | None = None
    mock_providers: bool = False
    publication_url: str | None = None
    webhook_secret: str | None = field(repr=False, default=None)
    rotate_webhook_secret: bool = False
    cleanup: bool = False
    job_timeout_sec: float = 1800.0
    package_timeout_sec: float = 300.0
    poll_interval_sec: float = 3.0


@dataclass
class FlowState:
    """Что сценарий M3 уже создал: следующие шаги берут отсюда, отчёт показывает ссылки."""

    job: dict[str, Any] | None = None
    approved: bool = False
    package: dict[str, Any] | None = None
    publication: dict[str, Any] | None = None
    code: str | None = None
    clicked: bool = False
    bot_start_sent: bool = False


class Smoke:
    def __init__(self, cfg: Config, transport: Transport) -> None:
        self.cfg = cfg
        self.http = transport
        self.results: list[Result] = []
        self.csrf_token: str | None = None
        self.logged_in = False
        self.flow = FlowState()
        self.sleep: Callable[[float], None] = time.sleep
        self.clock: Callable[[], float] = time.monotonic

    # -- инфраструктура

    def check(self, name: str, fn: Callable[[], str | None], *, skip: str | None = None) -> None:
        if skip:
            self.results.append(Result(name, SKIP, skip))
            return
        started = time.monotonic()
        try:
            detail = fn() or "ok"
            status = PASS
        except SmokeError as exc:
            status, detail = FAIL, str(exc)
        except Exception as exc:  # noqa: BLE001 - смоук не должен падать на одной проверке
            status, detail = FAIL, f"{type(exc).__name__}: {exc}"
        self.results.append(Result(name, status, detail, int((time.monotonic() - started) * 1000)))

    def api(self, path: str) -> str:
        return self.cfg.api_url.rstrip("/") + API_PREFIX + path

    def get(self, url: str, **kwargs: Any) -> Response:
        return self.http.request("GET", url, **kwargs)

    def send(
        self, method: str, path: str, body: Any = None, *, idempotent: bool = False, **headers: str
    ) -> Response:
        """Запрос к API под сессией: X-CSRF-Token (double-submit, §5.1) и, если нужно, Idempotency-Key."""
        hdrs = {"X-CSRF-Token": self.csrf_token or "", **headers}
        if idempotent:
            hdrs["Idempotency-Key"] = f"smoke-{uuid.uuid4().hex}"
        return self.http.request(method, self.api(path), headers=hdrs, json_body=body)

    def public(self, path: str) -> str:
        return self.cfg.public_url.rstrip("/") + path

    @staticmethod
    def expect_status(resp: Response, *expected: int) -> None:
        if resp.status not in expected:
            want = " или ".join(str(s) for s in expected)
            raise SmokeError(f"HTTP {resp.status}, ожидался {want}: {resp.text()[:160]!r}")

    @staticmethod
    def error_code(resp: Response) -> str:
        body = resp.json()
        try:
            return str(body["error"]["code"])
        except (KeyError, TypeError) as exc:
            raise SmokeError(f"ответ не в формате ошибок §5.1: {resp.text()[:160]!r}") from exc

    # -- api

    def c_api_health(self) -> str:
        resp = self.get(self.api("/health"))
        self.expect_status(resp, 200)
        if resp.json().get("status") != "ok":
            raise SmokeError(f"status != ok: {resp.text()[:120]!r}")
        return "живость ok"

    def c_api_ready(self) -> str:
        resp = self.get(self.api("/health/ready"))
        if resp.status == 503:
            body = resp.json()
            checks = body.get("checks") if isinstance(body, dict) else None
            bad = [k for k, v in (checks or {}).items() if not (isinstance(v, dict) and v.get("ok"))]
            raise SmokeError(f"не готов (503): {', '.join(bad) or resp.text()[:120]}")
        self.expect_status(resp, 200)
        body = resp.json()
        if body.get("status") != "ok":
            raise SmokeError(f"status={body.get('status')!r}")
        return "db, migrations, media_writable, disk — ok"

    def c_api_docs_disabled(self) -> str:
        base = self.cfg.api_url.rstrip("/")
        for path in ("/docs", "/redoc"):
            resp = self.get(base + path)
            if resp.status != 404:
                raise SmokeError(f"{path} → HTTP {resp.status}, ожидался 404 (Swagger грузится с CDN, §5.1)")
        return "/docs и /redoc → 404"

    def c_api_openapi(self) -> str:
        resp = self.get(self.api("/openapi.json"))
        self.expect_status(resp, 200)
        spec = resp.json()
        paths = list((spec.get("paths") or {}).keys())
        if "openapi" not in spec or not paths:
            raise SmokeError("в ответе нет openapi/paths")
        non_latin = [p for p in paths if not p.isascii()]
        if non_latin:
            raise SmokeError(f"кириллица в путях API: {non_latin[:3]}")
        foreign = [p for p in paths if not p.startswith(API_PREFIX)]
        if foreign:
            raise SmokeError(f"пути вне {API_PREFIX}: {foreign[:3]}")
        return f"{len(paths)} путей, все латиницей"

    def c_api_error_format(self) -> str:
        resp = self.get(self.api("/smoke-route-that-does-not-exist"))
        self.expect_status(resp, 404)
        code = self.error_code(resp)
        if not code.endswith("not_found"):
            raise SmokeError(f"code={code!r}, ожидался *_not_found")
        return f"404 {code}"

    def c_api_auth_required(self) -> str:
        resp = self.get(self.api("/auth/me"))
        self.expect_status(resp, 401)
        code = self.error_code(resp)
        if code != "not_authenticated":
            raise SmokeError(f"code={code!r}, ожидался not_authenticated")
        return "без сессии → 401 not_authenticated"

    # -- admin-хост за nginx

    def c_web_index(self) -> str:
        assert self.cfg.web_url
        resp = self.get(self.cfg.web_url.rstrip("/") + "/")
        self.expect_status(resp, 200)
        if "text/html" not in resp.header("content-type"):
            raise SmokeError(f"Content-Type {resp.header('content-type')!r}, ожидался text/html")
        csp = resp.header("content-security-policy")
        missing = [part for part in EXPECTED_ADMIN_CSP_PARTS if part not in csp]
        if missing:
            raise SmokeError(f"CSP без {missing} (§7.7): {csp!r}")
        return "оболочка отдаётся, CSP на месте"

    def c_web_no_external(self) -> str:
        assert self.cfg.web_url
        allowed = {urllib.parse.urlsplit(self.cfg.web_url).hostname or "", "www.w3.org"}
        # свои домены — тем же списком, что у web/scripts/check-no-external.mjs
        allowed |= {h.strip().lower() for h in os.environ.get("AS_ALLOWED_HOSTS", "").split(",") if h.strip()}
        html = self.get(self.cfg.web_url.rstrip("/") + "/").text()
        external = sorted(
            {
                ref
                for ref in EXTERNAL_REF_RE.findall(html)
                if (
                    urllib.parse.urlsplit(ref if ":" in ref.split("//")[0] else "https:" + ref).hostname or ""
                )
                not in allowed
            }
        )
        if external:
            raise SmokeError(f"внешние ресурсы в оболочке (§7.7): {external[:5]}")
        return "внешних src/href нет"

    def c_web_security_headers(self) -> str:
        assert self.cfg.web_url
        resp = self.get(self.cfg.web_url.rstrip("/") + "/")
        problems = []
        if resp.header("x-content-type-options").lower() != "nosniff":
            problems.append("X-Content-Type-Options")
        if self.cfg.web_url.startswith("https://") and not resp.header("strict-transport-security"):
            problems.append("Strict-Transport-Security")
        if problems:
            raise SmokeError(f"нет заголовков: {', '.join(problems)}")
        return "nosniff" + (", HSTS" if self.cfg.web_url.startswith("https://") else "")

    def c_web_protected_media_internal(self) -> str:
        assert self.cfg.web_url
        resp = self.get(self.cfg.web_url.rstrip("/") + "/_protected_media/")
        if resp.status != 404:
            raise SmokeError(f"/_protected_media/ снаружи → HTTP {resp.status}, должен быть internal (404)")
        return "медиа снаружи закрыты"

    # -- публичный контур

    def c_public_health(self) -> str:
        resp = self.get(self.cfg.public_url.rstrip("/") + "/health")
        self.expect_status(resp, 200)
        if resp.json().get("status") != "ok":
            raise SmokeError(f"status != ok: {resp.text()[:120]!r}")
        return "живость ok"

    def c_public_robots(self) -> str:
        resp = self.get(self.cfg.public_url.rstrip("/") + "/robots.txt")
        self.expect_status(resp, 200)
        if "Disallow: /" not in resp.text():
            raise SmokeError("в robots.txt нет «Disallow: /»")
        return "Disallow: /"

    def c_public_bad_code(self) -> str:
        resp = self.get(self.cfg.public_url.rstrip("/") + "/%3Cscript%3E")
        self.expect_status(resp, 404)
        if "<script>" in resp.text():
            raise SmokeError("сырой <script> отражён в ответе (XSS, §3.3 п. 2)")
        return "мусорный код → 404 без отражения"

    def c_public_landing_head(self) -> str:
        url = self.cfg.public_url.rstrip("/") + "/" + urllib.parse.quote(self.cfg.probe_code)
        resp = self.http.request("HEAD", url)
        self.expect_status(resp, 200, 302, 404)
        if resp.status == 200:
            csp = resp.header("content-security-policy")
            if "default-src 'none'" not in csp:
                raise SmokeError(f"у лендинга нет CSP default-src 'none' (§5.3): {csp!r}")
            return "лендинг с CSP"
        return f"HTTP {resp.status}"

    # -- под логином

    def c_auth_login(self) -> str:
        resp = self.http.request(
            "POST",
            self.api("/auth/login"),
            json_body={"login": self.cfg.login, "password": self.cfg.password},
        )
        if resp.status in (401, 429):
            raise SmokeError(f"вход отклонён: HTTP {resp.status} {self.error_code(resp)}")
        self.expect_status(resp, 200)
        body = resp.json()
        self.csrf_token = body.get("csrf_token")
        if not self.csrf_token:
            raise SmokeError("в ответе входа нет csrf_token")
        if not self.http.cookie("as_session"):
            raise SmokeError("сервер не выставил cookie as_session")
        self.logged_in = True
        return f"вход как {self.cfg.login}"

    def c_auth_me(self) -> str:
        resp = self.get(self.api("/auth/me"))
        self.expect_status(resp, 200)
        user = resp.json().get("user") or {}
        if user.get("login") != self.cfg.login:
            raise SmokeError(f"/auth/me вернул {user.get('login')!r}")
        return f"роль {user.get('role')}"

    def c_auth_csrf_enforced(self) -> str:
        resp = self.http.request("POST", self.api("/auth/logout"))
        if resp.status in (200, 204):
            self.logged_in = False
            raise SmokeError("выход без X-CSRF-Token принят — double-submit не работает (§5.1)")
        self.expect_status(resp, 403)
        code = self.error_code(resp)
        if code != "csrf_failed":
            raise SmokeError(f"code={code!r}, ожидался csrf_failed")
        return "без X-CSRF-Token → 403 csrf_failed"

    def c_system_status(self) -> str:
        resp = self.get(self.api("/system/status"))
        self.expect_status(resp, 200)
        body = resp.json()
        workers = body.get("workers")
        if not isinstance(workers, list):
            raise SmokeError("в /system/status нет списка workers")
        alive = sum(
            1 for w in workers if isinstance(w, dict) and w.get("state") not in (None, "dead", "stopped")
        )
        return f"воркеров {len(workers)}, живых {alive}"

    def c_sse_stream(self) -> str:
        resp = self.http.request(
            "GET",
            self.api("/events/stream"),
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=SSE_TIMEOUT_SEC,
        )
        self.expect_status(resp, 200)
        ctype = resp.header("content-type")
        if "text/event-stream" not in ctype:
            raise SmokeError(f"Content-Type {ctype!r}, ожидался text/event-stream")
        return "поток открывается"

    def c_auth_logout(self) -> str:
        headers = {"X-CSRF-Token": self.csrf_token or ""}
        resp = self.http.request("POST", self.api("/auth/logout"), headers=headers)
        self.expect_status(resp, 204, 200)
        self.logged_in = False
        after = self.get(self.api("/auth/me"))
        if after.status != 401:
            raise SmokeError(f"после выхода /auth/me → HTTP {after.status}, ожидался 401")
        return "сессия закрыта"

    # -- сценарий стенда (M3, §10.2): ролик → приёмка → пакет → ссылка → клик → bot_start → карточка

    def _wait(self, what: str, timeout: float, probe: Callable[[], Any]) -> Any:
        """Опрос `probe()` до непустого ответа; `probe` сам бросает SmokeError на безнадёжном состоянии."""
        deadline = self.clock() + timeout
        while True:
            value = probe()
            if value:
                return value
            if self.clock() >= deadline:
                raise SmokeError(f"{what}: не дождались за {int(timeout)} с")
            self.sleep(self.cfg.poll_interval_sec)

    def _job(self, job_id: int) -> dict[str, Any]:
        resp = self.get(self.api(f"/jobs/{job_id}"))
        if resp.status == 404:
            raise SmokeError(f"ролик #{job_id} не найден")
        self.expect_status(resp, 200)
        return resp.json()

    def _wait_job_ready(self, job_id: int) -> dict[str, Any]:
        last: dict[str, Any] = {}

        def probe() -> dict[str, Any] | None:
            job = self._job(job_id)
            last.update(job)
            status = job.get("status")
            if status in JOB_READY_STATUSES:
                return job
            if status in JOB_DEAD_STATUSES:
                reason = job.get("error_class") or job.get("error") or ""
                raise SmokeError(
                    f"ролик #{job_id} в статусе {status} (стадия {job.get('stage')}) {reason}".strip()
                )
            return None

        try:
            return self._wait("ролик до pending_review", self.cfg.job_timeout_sec, probe)
        except SmokeError as exc:
            if last and "не дождались" in str(exc):
                raise SmokeError(
                    f"{exc}; последний статус {last.get('status')}, стадия {last.get('stage')}"
                ) from exc
            raise

    def _create_job(self) -> int:
        cfg = self.cfg
        body: dict[str, Any]
        if cfg.clip_candidate_id is not None:
            body = {"kind": "clip", "clip_candidate_id": cfg.clip_candidate_id}
        else:
            body = {"kind": "generate", "channel_id": cfg.channel_id, "start": True}
            if cfg.topic:
                body["topic"] = cfg.topic
            if cfg.mock_providers:
                body["options"] = {"llm_provider": "mock", "tts_provider": "mock", "image_provider": "mock"}
        resp = self.send("POST", "/jobs", body, idempotent=True)
        if resp.status in (409, 422, 423):
            raise SmokeError(f"POST /jobs отклонён: HTTP {resp.status} {self.error_code(resp)}")
        self.expect_status(resp, 201)
        return int(resp.json()["id"])

    def c_stand_job(self) -> str:
        cfg = self.cfg
        started = self.clock()
        if cfg.job_id is not None:
            job_id, how = cfg.job_id, "готовый"
        elif cfg.clip_candidate_id is not None or cfg.channel_id:
            job_id = self._create_job()
            how = "новый clip-job" if cfg.clip_candidate_id is not None else "новый generate-job"
        else:
            raise SmokeError(
                "не указан ролик для сценария: --job-id, --clip-candidate или --channel "
                "(сценарий одобряет ролик и регистрирует публикацию — ролик выбирается явно)"
            )
        job = self._wait_job_ready(job_id)
        self.flow.job = job
        waited = int(self.clock() - started)
        channel = (job.get("channel") or {}).get("id") or job.get("channel_id")
        return f"{how} ролик #{job_id} ({job.get('kind')}, канал {channel}): {job['status']} за {waited} с"

    def c_stand_approve(self) -> str:
        job = self.flow.job or {}
        job_id = int(job["id"])
        if job.get("status") == "approved":
            self.flow.approved = True
            return f"ролик #{job_id} уже одобрен"
        resp = self.send(
            "POST",
            f"/jobs/{job_id}/review",
            {"decision": "approve", "expected_status": "pending_review", "comment": "e2e smoke"},
        )
        if resp.status in (403, 409):
            raise SmokeError(f"одобрение отклонено: HTTP {resp.status} {self.error_code(resp)}")
        self.expect_status(resp, 200)
        after = resp.json()
        if after.get("status") != "approved":
            raise SmokeError(f"после approve статус {after.get('status')!r}")
        self.flow.job = after
        self.flow.approved = True
        return f"ролик #{job_id} одобрен"

    def c_stand_package(self) -> str:
        job_id = int((self.flow.job or {})["id"])

        def probe() -> dict[str, Any] | None:
            resp = self.get(self.api(f"/jobs/{job_id}/package"))
            if resp.status == 404:
                return None  # задача package_build ещё не отработала
            self.expect_status(resp, 200)
            pkg = resp.json()
            if pkg.get("status") == "published" or pkg.get("publication"):
                raise SmokeError(f"пакет #{pkg.get('id')} уже опубликован — нужен ролик без публикации")
            return pkg if pkg.get("status") in PACKAGE_READY_STATUSES else None

        pkg = self._wait("пакет к заливке", self.cfg.package_timeout_sec, probe)
        code = str((pkg.get("tracking") or {}).get("code") or "")
        if not re.fullmatch(r"[0-9a-z]{4,16}", code):
            raise SmokeError(f"у пакета нет кода атрибуции: {pkg.get('tracking')!r}")
        description = (pkg.get("texts") or {}).get("description") or ""
        if code not in description:
            raise SmokeError(f"кода {code} нет в описании пакета")
        self.flow.package = pkg
        self.flow.code = code
        return f"пакет #{pkg['id']} ({pkg['status']}), код {code}, площадка {pkg['channel']['platform']}"

    def c_stand_publication(self) -> str:
        pkg = self.flow.package or {}
        channel = pkg.get("channel") or {}
        url = self.cfg.publication_url or fake_video_url(
            str(channel.get("platform") or "youtube"), channel.get("platform_channel_ref")
        )
        resp = self.send("POST", "/publications", {"package_id": pkg["id"], "url": url}, idempotent=True)
        if resp.status in (409, 422):
            raise SmokeError(f"регистрация {url} отклонена: HTTP {resp.status} {self.error_code(resp)}")
        self.expect_status(resp, 201)
        pub = resp.json()["publication"]
        if pub.get("status") != "live":
            raise SmokeError(f"публикация #{pub.get('id')} в статусе {pub.get('status')!r}, ожидался live")
        self.flow.publication = pub
        return f"публикация #{pub['id']} {pub.get('canonical_url') or url}, продукт {pub.get('product_id')}"

    def c_stand_click(self) -> str:
        code = self.flow.code or ""
        viewer = {"User-Agent": VIEWER_UA, "Accept": "text/html"}
        # зритель набрал домен/код с плашки (верхний регистр — как с экрана, код нечувствителен к нему)
        resp = self.http.request("GET", self.public("/" + code.upper()), headers=viewer)
        self.expect_status(resp, 200, 302)
        if resp.status == 200 and "default-src 'none'" not in resp.header("content-security-policy"):
            raise SmokeError(
                f"у лендинга нет CSP default-src 'none' (§5.3): {resp.header('content-security-policy')!r}"
            )
        landing = "лендинг" if resp.status == 200 else f"редирект на {resp.header('location')[:60]}"
        self.flow.clicked = True
        # кнопка «Открыть в Telegram» на лендинге
        tg = self.http.request("GET", self.public(f"/go/{code}/tg"), headers=viewer)
        if tg.status == 404:
            return f"{landing}; у продукта нет Telegram-бота — переход в бота не проверялся"
        self.expect_status(tg, 200, 302)
        target = tg.header("location") + " " + tg.text()
        if f"start={code}_l" not in target:
            raise SmokeError(f"/go/{code}/tg не передаёт start={code}_l боту")
        return f"{landing}; /go/{code}/tg → start={code}_l"

    def _webhook_secret(self, product_id: str) -> tuple[str, str]:
        if self.cfg.webhook_secret:
            return self.cfg.webhook_secret, "секрет из AS_SMOKE_WEBHOOK_SECRET"
        if not self.cfg.rotate_webhook_secret:
            raise SmokeError(
                "нет секрета вебхука: задайте AS_SMOKE_WEBHOOK_SECRET или --rotate-webhook-secret (admin)"
            )
        resp = self.send("POST", f"/products/{product_id}/webhook-secret/rotate", {"grace_hours": 24})
        if resp.status == 403:
            raise SmokeError("--rotate-webhook-secret: нужна роль admin")
        self.expect_status(resp, 200)
        return str(resp.json()["secret"]), "секрет перевыпущен (прежний принимается ещё 24 ч)"

    def c_stand_bot_start(self) -> str:
        pub = self.flow.publication or {}
        product_id = str(pub.get("product_id") or "")
        if not product_id:
            raise SmokeError("у публикации нет продукта — некуда слать конверсию")
        secret, how = self._webhook_secret(product_id)
        event_id = f"smoke-{uuid.uuid4().hex}"
        body = json.dumps(
            {
                "events": [
                    {
                        "event_id": event_id,
                        "type": "bot_start",
                        "occurred_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "tag": f"{self.flow.code}_l",  # пришёл через лендинг
                        "user_ref": f"smoke:{uuid.uuid4().hex[:16]}",
                    }
                ]
            },
            separators=(",", ":"),
        ).encode("utf-8")
        ts = int(time.time())
        resp = self.http.request(
            "POST",
            self.public(INGEST_PATH),
            headers={
                "X-AS-Product": product_id,
                "X-AS-Timestamp": str(ts),
                "X-AS-Signature": ingest_signature(secret, ts, body),
                "Content-Type": "application/json",
            },
            raw_body=body,
        )
        if resp.status in (401, 403, 413, 429):
            raise SmokeError(f"приём отклонил событие: HTTP {resp.status} {self.error_code(resp)}")
        self.expect_status(resp, 200)
        out = resp.json()
        if out.get("accepted") != 1:
            raise SmokeError(f"событие не принято: {resp.text()[:200]!r}")
        self.flow.bot_start_sent = True
        return f"bot_start {event_id} принят ({how})"

    def c_stand_publication_stats(self) -> str:
        pub_id = int((self.flow.publication or {})["id"])
        last: dict[str, Any] = {}

        def probe() -> dict[str, Any] | None:
            resp = self.get(self.api(f"/publications/{pub_id}"))
            self.expect_status(resp, 200)
            traffic = resp.json().get("traffic") or {}
            last.update(traffic)
            return traffic if traffic.get("bot_starts") and traffic.get("unique_visitors") else None

        try:
            traffic = self._wait("клик и старт в карточке публикации", 20.0, probe)
        except SmokeError as exc:
            raise SmokeError(f"{exc}: traffic={last!r}") from exc
        if traffic.get("unique_visitors") != 1 or traffic.get("bot_starts") != 1:
            raise SmokeError(f"ожидались 1 посетитель и 1 старт: traffic={traffic!r}")
        return (
            f"публикация #{pub_id}: просмотров лендинга {traffic.get('landing_views')}, "
            f"посетителей {traffic['unique_visitors']}, в Telegram {traffic.get('to_telegram')}, "
            f"стартов {traffic['bot_starts']}"
        )

    def c_stand_cleanup(self) -> str:
        pub_id = int((self.flow.publication or {})["id"])
        resp = self.send("DELETE", f"/publications/{pub_id}")
        if resp.status == 403:
            raise SmokeError("--cleanup: мягкое удаление публикации только для admin")
        self.expect_status(resp, 204, 200)
        return f"публикация #{pub_id} мягко удалена, ролик снова approved"

    def run_stand_flow(self) -> None:
        """Шаги сценария M3 по порядку; после первого провала остальные пропускаются."""
        steps: list[tuple[str, Callable[[], str | None], Callable[[], bool]]] = [
            ("stand_job", self.c_stand_job, lambda: True),
            ("stand_approve", self.c_stand_approve, lambda: self.flow.job is not None),
            ("stand_package", self.c_stand_package, lambda: self.flow.approved),
            ("stand_publication", self.c_stand_publication, lambda: self.flow.package is not None),
            ("stand_click", self.c_stand_click, lambda: self.flow.publication is not None),
            ("stand_bot_start", self.c_stand_bot_start, lambda: self.flow.clicked),
            ("stand_publication_stats", self.c_stand_publication_stats, lambda: self.flow.bot_start_sent),
        ]
        failed_at: str | None = None
        for name, fn, ready in steps:
            if failed_at is None and not ready():
                failed_at = "предыдущий шаг"
            self.check(name, fn, skip=f"сценарий остановлен: провал {failed_at}" if failed_at else None)
            if failed_at is None and self.results[-1].status == FAIL:
                failed_at = name
        if self.cfg.cleanup:
            self.check(
                "stand_cleanup",
                self.c_stand_cleanup,
                skip=None if self.flow.publication is not None else "публикация не создана",
            )

    # -- порядок

    def run(self) -> list[Result]:
        cfg = self.cfg
        self.check("api_health", self.c_api_health)
        self.check("api_ready", self.c_api_ready)
        self.check("api_docs_disabled", self.c_api_docs_disabled)
        self.check("api_openapi", self.c_api_openapi)
        self.check("api_error_format", self.c_api_error_format)
        self.check("api_auth_required", self.c_api_auth_required)

        no_web = (
            None
            if cfg.web_url
            else "admin-хост не задан (--admin-url), статика и заголовки nginx не проверялись"
        )
        self.check("web_index", self.c_web_index, skip=no_web)
        self.check("web_no_external", self.c_web_no_external, skip=no_web)
        self.check("web_security_headers", self.c_web_security_headers, skip=no_web)
        self.check("web_protected_media_internal", self.c_web_protected_media_internal, skip=no_web)

        self.check("public_health", self.c_public_health)
        self.check("public_robots", self.c_public_robots)
        self.check("public_bad_code", self.c_public_bad_code)
        self.check(
            "public_landing_head",
            self.c_public_landing_head,
            skip="режим prod-readonly: запрос к лендингу не делается"
            if cfg.mode == "prod-readonly"
            else None,
        )

        if not (cfg.login and cfg.password):
            reason = "нет учётки: задайте AS_SMOKE_LOGIN и AS_SMOKE_PASSWORD"
            if cfg.require_login:
                self.results.append(
                    Result("auth_login", FAIL, reason + " — проверки под логином обязательны (§1.5)")
                )
            else:
                self.results.append(Result("auth_login", SKIP, reason + ": прогон без входа неполный"))
        else:
            self.check("auth_login", self.c_auth_login)
            after_login = None if self.logged_in else "вход не удался"
            self.check("auth_me", self.c_auth_me, skip=after_login)
            self.check("auth_csrf_enforced", self.c_auth_csrf_enforced, skip=after_login)
            after_login = None if self.logged_in else "сессии нет"
            self.check("system_status", self.c_system_status, skip=after_login)
            self.check("sse_stream", self.c_sse_stream, skip=after_login)
            if cfg.flow:
                if self.logged_in:
                    self.run_stand_flow()
                else:
                    self.results.append(
                        Result("stand_job", FAIL, "сценарий M3 требует входа (роль operator)")
                    )
            self.check("auth_logout", self.c_auth_logout, skip=None if self.logged_in else "сессии нет")

        if not cfg.flow:
            reason = {
                "prod-readonly": "режим prod-readonly: сценарий M3 пишет данные и на проде не запускается",
                "local": "сценарий M3 не запрошен (--flow)",
            }.get(cfg.mode, "сценарий M3 выключен (--no-flow)")
            self.results.append(Result("stand_flow", SKIP, reason))
        elif not (cfg.login and cfg.password):
            self.results.append(
                Result("stand_job", FAIL, "сценарий M3 требует входа: AS_SMOKE_LOGIN/AS_SMOKE_PASSWORD")
            )
        return self.results


# ───────────────────────────────────────────────────────────── CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="e2e_smoke.py",
        description="Смоук развёрнутого Auto Shorts (§8.5). Выход: 0 — ok, 1 — есть провалы, 2 — вызов.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--local", dest="mode", action="store_const", const="local", help="api и public напрямую"
    )
    mode.add_argument("--stand", dest="mode", action="store_const", const="stand", help="стенд за nginx")
    mode.add_argument(
        "--prod-readonly",
        dest="mode",
        action="store_const",
        const="prod-readonly",
        help="прод, только чтение",
    )
    parser.add_argument(
        "--admin-url", default=os.environ.get("AS_SMOKE_ADMIN_URL"), help="admin-хост (nginx)"
    )
    parser.add_argument(
        "--api-url", default=os.environ.get("AS_SMOKE_API_URL"), help="api напрямую (--local)"
    )
    parser.add_argument(
        "--public-url", default=os.environ.get("AS_SMOKE_PUBLIC_URL"), help="хост редиректора"
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="--local: хост для --*-port (127.0.0.1)")
    parser.add_argument("--api-port", type=int, default=None, help=f"--local: порт api ({DEFAULT_API_PORT})")
    parser.add_argument(
        "--public-port", type=int, default=None, help=f"--local: порт редиректора ({DEFAULT_PUBLIC_PORT})"
    )
    parser.add_argument("--web-port", type=int, default=None, help="--local: порт статики веба (18301)")
    flow = parser.add_argument_group("сценарий M3 (stand_*: пишет данные)")
    flow.add_argument("--flow", dest="flow", action="store_true", default=None, help="включить (--local)")
    flow.add_argument("--no-flow", dest="flow", action="store_false", help="выключить (--stand)")
    flow.add_argument("--job-id", type=int, default=None, help="готовый ролик в pending_review/approved")
    flow.add_argument("--clip-candidate", type=int, default=None, help="создать clip-job из кандидата")
    flow.add_argument("--channel", default=None, help="создать generate-job в канале")
    flow.add_argument("--topic", default=None, help="тема generate-job")
    flow.add_argument("--mock-providers", action="store_true", help="generate-job на провайдерах mock")
    flow.add_argument("--publication-url", default=None, help="ссылка площадки (по умолчанию случайная)")
    flow.add_argument(
        "--rotate-webhook-secret",
        action="store_true",
        help="перевыпустить секрет вебхука продукта (admin), если нет AS_SMOKE_WEBHOOK_SECRET",
    )
    flow.add_argument("--cleanup", action="store_true", help="в конце мягко удалить публикацию (admin)")
    flow.add_argument("--job-timeout", type=float, default=1800.0, help="ожидание pending_review, с")
    flow.add_argument("--package-timeout", type=float, default=300.0, help="ожидание пакета, с")
    flow.add_argument("--poll-interval", type=float, default=3.0, help="шаг опроса, с")
    parser.add_argument(
        "--login", default=os.environ.get("AS_SMOKE_LOGIN") or os.environ.get("E2E_LOGIN"), help="логин"
    )
    parser.add_argument("--no-login", action="store_true", help="разрешить прогон без входа (неполный)")
    parser.add_argument("--probe-code", default="smoke_probe", help="код для HEAD лендинга (стенд)")
    parser.add_argument("--timeout", type=float, default=10.0, help="таймаут запроса, с")
    parser.add_argument("--insecure", action="store_true", help="не проверять TLS (самоподписанный стенд)")
    parser.add_argument("--json", action="store_true", help="вывод JSON")
    parser.set_defaults(mode="local")
    return parser


def config_from_args(args: argparse.Namespace) -> Config:
    password = os.environ.get("AS_SMOKE_PASSWORD") or os.environ.get("E2E_PASSWORD")
    if args.mode == "local":

        def at(port: int | None) -> str | None:
            return f"http://{args.host}:{port}" if port else None

        api_url = (
            at(args.api_port) or args.api_url or args.admin_url or f"http://{args.host}:{DEFAULT_API_PORT}"
        )
        public_url = at(args.public_port) or args.public_url or f"http://{args.host}:{DEFAULT_PUBLIC_PORT}"
        web_url = at(args.web_port) or args.admin_url
    else:
        if not args.admin_url or not args.public_url:
            raise SystemExit(f"e2e_smoke: для --{args.mode} нужны --admin-url и --public-url")
        api_url = args.api_url or args.admin_url
        public_url = args.public_url
        web_url = args.admin_url
    for url in filter(None, (api_url, public_url, web_url)):
        if not re.match(r"^https?://[^/\s]+", url):
            raise SystemExit(f"e2e_smoke: не URL: {url!r}")
    flow = args.flow if args.flow is not None else args.mode == "stand"
    if args.mode == "prod-readonly" and args.flow:
        raise SystemExit("e2e_smoke: --flow пишет данные и в режиме --prod-readonly запрещён")
    sources = [x for x in (args.job_id, args.clip_candidate, args.channel) if x is not None]
    if len(sources) > 1:
        raise SystemExit("e2e_smoke: укажите что-то одно из --job-id, --clip-candidate, --channel")
    if args.poll_interval <= 0 or args.job_timeout <= 0 or args.package_timeout <= 0:
        raise SystemExit("e2e_smoke: таймауты и шаг опроса должны быть > 0")
    return Config(
        mode=args.mode,
        api_url=api_url,
        public_url=public_url,
        web_url=web_url,
        login=args.login,
        password=password,
        require_login=(args.mode != "local" or bool(flow)) and not args.no_login,
        probe_code=args.probe_code,
        flow=bool(flow) and args.mode != "prod-readonly",
        job_id=args.job_id,
        clip_candidate_id=args.clip_candidate,
        channel_id=args.channel,
        topic=args.topic,
        mock_providers=args.mock_providers,
        publication_url=args.publication_url,
        webhook_secret=os.environ.get("AS_SMOKE_WEBHOOK_SECRET") or None,
        rotate_webhook_secret=args.rotate_webhook_secret,
        cleanup=args.cleanup,
        job_timeout_sec=args.job_timeout,
        package_timeout_sec=args.package_timeout,
        poll_interval_sec=args.poll_interval,
    )


def print_report(cfg: Config, results: list[Result], *, as_json: bool) -> None:
    counts = {s: sum(1 for r in results if r.status == s) for s in (PASS, FAIL, SKIP)}
    if as_json:
        payload = {"mode": cfg.mode, "counts": counts, "results": [asdict(r) for r in results]}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    marks = {PASS: "PASS", FAIL: "FAIL", SKIP: "SKIP"}
    print(f"Auto Shorts smoke: режим {cfg.mode}, api {cfg.api_url}, public {cfg.public_url}")
    width = max(len(r.check) for r in results)
    for r in results:
        timing = f" ({r.duration_ms} мс)" if r.status != SKIP else ""
        print(f"  {marks[r.status]}  {r.check:<{width}}  {r.detail}{timing}")
    print(f"Итого: {counts[PASS]} pass, {counts[FAIL]} fail, {counts[SKIP]} skip")


def main(argv: list[str] | None = None, transport: Transport | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = config_from_args(args)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2
    transport = transport or UrllibTransport(timeout=args.timeout, insecure=args.insecure)
    results = Smoke(cfg, transport).run()
    print_report(cfg, results, as_json=args.json)
    return 1 if any(r.status == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
