"""Приём конверсий по HMAC и вебхук-часть продукта (docs/ARCHITECTURE.md §5.3, §6.6). Владелец — M3-B.

`process(raw)` — вся логика публичного `POST /ingest/v1/events` без HTTP: её вызывают публичное
приложение (`public/app.py`) и тестовое событие вкладки «Вебхук» (`send_test_conversion`, тот же путь
с `is_test=True`). Порядок проверок (контракт `api/schemas/conversions.py`):

1. тело ≤ 256 КБ → иначе 413 `payload_too_large`;
2. продукт известен и подпись `sha256=HMAC(secret, f"{ts}." + body)` верна текущим секретом или
   прежним до `webhook_prev_valid_until` → иначе 401 `invalid_signature` (неизвестный продукт — тоже
   401, без подсказки);
3. `|now − ts| ≤ 300 с` → иначе 401 `stale_timestamp`;
4. ≤ 20 запросов/с на продукт → иначе 429 `rate_limited` (`Retry-After`);
5. в `events` ≤ 100 → иначе 413 (до разбора событий);
6. тело — объект `{"events": [...]}` → иначе 422 `validation_error`.

События разбираются по одному: плохое получает `invalid` и не валит пачку; повтор `event_id` →
`duplicate`; `tag` с `_l` → `via_landing`; неизвестный `tag` → событие сохраняется как прямой
трафик продукта (`unknown_tag`). Каждый запрос пишется в `ingest_requests` (тело не храним).
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets as pysecrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from pydantic import ValidationError

from autoshorts.api.schemas.conversions import (
    INGEST_HEADER_PRODUCT,
    INGEST_HEADER_SIGNATURE,
    INGEST_HEADER_TIMESTAMP,
    INGEST_MAX_BODY_BYTES,
    INGEST_MAX_EVENTS,
    INGEST_MAX_SKEW_SEC,
    INGEST_PATH,
    INGEST_RATE_LIMIT_PER_SEC,
    IngestEvent,
    IngestOutcome,
    ingest_signature,
    verify_ingest_signature,
)
from autoshorts.api.schemas.product_integration import webhook_secret_name
from autoshorts.core.errors import NotFound, NotReady
from autoshorts.domain import tags
from autoshorts.domain.enums import ConversionEventType
from autoshorts.repos import conversions as conversions_repo
from autoshorts.repos import tracking as tracking_repo
from autoshorts.services import audit
from autoshorts.services.audit import Actor

log = logging.getLogger(__name__)

Row = dict[str, Any]

PREV_SUFFIX = "_PREV"
SLUG_RE_TEXT = r"^[a-z0-9][a-z0-9_-]{1,39}$"


# ---------------------------------------------------------------- ограничение частоты


class RateLimiter:
    """Скользящее окно 1 с на ключ (продукт). Процесс публичного контура один (`--workers 1`)."""

    def __init__(self, limit_per_sec: int = INGEST_RATE_LIMIT_PER_SEC, window_sec: float = 1.0) -> None:
        self.limit = int(limit_per_sec)
        self.window = float(window_sec)
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> tuple[bool, float]:
        """(пускать ли, через сколько секунд повторить)."""
        t = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and t - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False, max(0.05, self.window - (t - q[0]))
            q.append(t)
            if len(self._hits) > 1000:  # не копим мусор неизвестных ключей
                for k in [k for k, v in self._hits.items() if not v or t - v[-1] >= self.window][:500]:
                    self._hits.pop(k, None)
            return True, 0.0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = RateLimiter()


# ---------------------------------------------------------------- результат


@dataclass
class IngestResult:
    """HTTP-ответ приёма: статус, тело (ответ или ошибка), заголовки, исход для журнала."""

    status: int
    body: dict[str, Any]
    outcome: str
    headers: dict[str, str] = field(default_factory=dict)
    request_log_id: int | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200


def _error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


# ---------------------------------------------------------------- секреты продукта


def _cipher() -> Any:
    from autoshorts.core.errors import ConfigError
    from autoshorts.core.security import SecretCipher

    try:
        return SecretCipher.from_env()
    except ConfigError as exc:
        raise NotReady(
            "Не задан ключ шифрования секретов: AS_SECRETS_KEY или AS_SECRETS_KEY_FILE",
            details={"reason": "secrets_key_missing"},
        ) from exc


def _secret_value(conn: Any, name: str | None) -> str | None:
    """Значение секрета без кеша процесса (после ротации старое значение не должно жить 60 с)."""
    if not name:
        return None
    from autoshorts.services.secrets import get_secret

    try:
        return get_secret(name, conn=conn, max_age=0)
    except Exception:  # noqa: BLE001 - нет ключа шифрования: секрет продукта недоступен
        log.warning("webhook secret %s unavailable", name, exc_info=True)
        return None


def product_secrets(conn: Any, product: Row, now: datetime) -> list[str]:
    """Действующие секреты: текущий и прежний до `webhook_prev_valid_until`."""
    out: list[str] = []
    cur = _secret_value(conn, product.get("webhook_secret_name"))
    if cur:
        out.append(cur)
    until = product.get("webhook_prev_valid_until")
    if product.get("webhook_prev_secret_name") and until is not None and until > now:
        prev = _secret_value(conn, product["webhook_prev_secret_name"])
        if prev and prev not in out:
            out.append(prev)
    return out


def rotate_secret(conn: Any, product_id: str, grace_hours: int, actor: Actor) -> dict[str, Any]:
    """Новый секрет (показывается один раз); прежний принимается ещё `grace_hours`."""
    from autoshorts.core.security import last4
    from autoshorts.repos import secrets as secrets_repo
    from autoshorts.services.secrets import invalidate_cache

    product = conversions_repo.product_webhook(conn, product_id, for_update=True)
    if product is None:
        raise NotFound("product", "Продукт не найден")
    now = datetime.now(UTC)
    name = webhook_secret_name(product_id)
    prev_name: str | None = None
    prev_until: datetime | None = None
    current = _secret_value(conn, product.get("webhook_secret_name"))
    cipher = _cipher()
    if current and grace_hours > 0:
        prev_name = name + PREV_SUFFIX
        ct, key_id = cipher.encrypt(current)
        secrets_repo.upsert(
            conn, name=prev_name, ciphertext=ct, key_id=key_id, last4=last4(current), updated_by=actor.user_id
        )
        prev_until = now + timedelta(hours=int(grace_hours))
    value = pysecrets.token_urlsafe(32)
    ct, key_id = cipher.encrypt(value)
    secrets_repo.upsert(
        conn, name=name, ciphertext=ct, key_id=key_id, last4=last4(value), updated_by=actor.user_id
    )
    row = conversions_repo.set_product_webhook(
        conn,
        product_id,
        secret_name=name,
        rotated_at=now,
        prev_secret_name=prev_name,
        prev_valid_until=prev_until,
    )
    audit.record(
        conn,
        actor,
        "product.webhook_secret_rotate",
        "product",
        product_id,
        summary=f"новый секрет вебхука {product_id} (значение не логируется)",
        diff={
            "secret_name": name,
            "previous_valid_until": prev_until.isoformat() if prev_until else None,
            "grace_hours": int(grace_hours),
        },
        safe_keys=frozenset({"secret_name", "previous_valid_until", "grace_hours"}),
    )
    invalidate_cache(name)
    if prev_name:
        invalidate_cache(prev_name)
    return {
        "product_id": product_id,
        "secret": value,
        "secret_name": name,
        "rotated_at": row["webhook_secret_rotated_at"],
        "previous_valid_until": row["webhook_prev_valid_until"],
    }


# ---------------------------------------------------------------- события


def hash_user_ref(product_id: str, user_ref: str | None) -> str | None:
    if not user_ref:
        return None
    return hashlib.sha256(f"{product_id}|{user_ref}".encode()).hexdigest()


def _event_error(exc: ValidationError) -> str:
    first = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(str(p) for p in first.get("loc", ()))
    kind = str(first.get("type") or "invalid")
    return f"{loc}:{kind}"[:200] if loc else kind[:200]


def _resolve_tag(conn: Any, product_id: str, tag: str | None) -> tuple[Row | None, bool, bool]:
    """(ссылка, via_landing, неизвестный ли тег). Ссылка другого продукта — неизвестный тег."""
    if not tag:
        return None, False, False
    code, via_landing = tags.split_landing_suffix(tag)
    link = tracking_repo.find_first(conn, tags.lookup_candidates(code))
    if link is None or link["product_id"] != product_id:
        return None, via_landing, True
    return link, via_landing, False


def _store_events(conn: Any, product_id: str, raw_events: list[Any], *, is_test: bool) -> dict[str, Any]:
    counts = {"accepted": 0, "duplicates": 0, "unknown_tag": 0, "invalid": 0}
    results: list[dict[str, Any]] = []
    for raw in raw_events:
        event_id = raw.get("event_id") if isinstance(raw, dict) else None
        shown_id = event_id if isinstance(event_id, str) and event_id.isascii() else ""
        try:
            ev = IngestEvent.model_validate(raw)
        except ValidationError as exc:
            counts["invalid"] += 1
            results.append({"event_id": shown_id[:128], "status": "invalid", "error": _event_error(exc)})
            continue
        link, via_landing, unknown = _resolve_tag(conn, product_id, ev.tag)
        dims = link or {}
        try:
            with conn.transaction():  # точка сохранения: отказ БД по одному событию не валит пачку
                row = conversions_repo.insert_event(
                    conn,
                    {
                        "product_id": product_id,
                        "external_event_id": ev.event_id,
                        "event_type": ConversionEventType(ev.type).value,
                        "occurred_at": ev.occurred_at,
                        "tag": ev.tag,
                        "via_landing": via_landing,
                        "tracking_link_id": dims.get("id"),
                        "publication_id": dims.get("publication_id"),
                        "job_id": dims.get("job_id"),
                        "channel_id": dims.get("channel_id"),
                        "campaign_id": dims.get("campaign_id"),
                        "banner_variant_id": dims.get("banner_variant_id"),
                        "platform": dims.get("platform"),
                        "user_ref_hash": hash_user_ref(product_id, ev.user_ref),
                        "amount_minor": ev.amount_minor,
                        "currency": ev.currency,
                        "meta": ev.meta,
                        "is_test": is_test,
                    },
                )
        except psycopg.Error as exc:
            counts["invalid"] += 1
            log.info("ingest event %s rejected by DB: %s", ev.event_id, type(exc).__name__)
            results.append({"event_id": ev.event_id, "status": "invalid", "error": "db_rejected"})
            continue
        if row is None:
            counts["duplicates"] += 1
            status = "duplicate"
        elif unknown:
            counts["unknown_tag"] += 1
            status = "unknown_tag"
        else:
            counts["accepted"] += 1
            status = "accepted"
        results.append({"event_id": ev.event_id, "status": status, "error": None})
    return {**counts, "results": results}


# ---------------------------------------------------------------- приём


@dataclass
class IngestRaw:
    """Сырые данные HTTP-запроса приёма."""

    product_header: str | None
    timestamp_header: str | None
    signature_header: str | None
    body: bytes | None  # None — тело больше лимита (дочитывать не стали)
    body_bytes: int
    request_id: str | None = None


def _log_request(
    conn: Any,
    raw: IngestRaw,
    *,
    product_id: str | None,
    outcome: str,
    status: int,
    is_test: bool,
    detail: str | None = None,
    counts: dict[str, Any] | None = None,
) -> int | None:
    try:
        with conn.transaction():
            row = conversions_repo.insert_ingest_request(
                conn,
                {
                    "product_id": product_id,
                    "product_header": raw.product_header,
                    "outcome": outcome,
                    "http_status": status,
                    "events_total": (counts or {}).get("events_total", 0),
                    "accepted": (counts or {}).get("accepted", 0),
                    "duplicates": (counts or {}).get("duplicates", 0),
                    "unknown_tag": (counts or {}).get("unknown_tag", 0),
                    "invalid": (counts or {}).get("invalid", 0),
                    "is_test": is_test,
                    "body_bytes": raw.body_bytes,
                    "detail": detail,
                    "request_id": raw.request_id,
                },
            )
        return int(row["id"])
    except Exception:  # noqa: BLE001 - журнал не должен ронять приём
        log.warning("ingest_requests: could not log request", exc_info=True)
        return None


def _parse_ts(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip()
    if not text or len(text) > 12 or not text.lstrip("-").isdigit():
        return None
    return int(text)


def process(
    conn: Any,
    raw: IngestRaw,
    *,
    is_test: bool = False,
    now: datetime | None = None,
    limiter: RateLimiter | None = None,
) -> IngestResult:
    """Обработать запрос приёма. `conn` — autocommit-соединение или транзакция вызывающего."""
    now = now or datetime.now(UTC)
    limiter = limiter or rate_limiter
    header = (raw.product_header or "").strip()

    def reject(
        status: int,
        code: str,
        message: str,
        outcome: str,
        *,
        product_id: str | None = None,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> IngestResult:
        log_id = _log_request(
            conn,
            raw,
            product_id=product_id,
            outcome=outcome,
            status=status,
            is_test=is_test,
            detail=detail or code,
        )
        return IngestResult(status, _error_body(code, message, details), outcome, headers or {}, log_id)

    # 1. размер тела
    if raw.body is None or raw.body_bytes > INGEST_MAX_BODY_BYTES:
        return reject(
            413,
            "payload_too_large",
            "Тело запроса больше 256 КБ",
            IngestOutcome.PAYLOAD_TOO_LARGE.value,
            details={"max_bytes": INGEST_MAX_BODY_BYTES},
        )
    # 2. продукт и подпись
    import re

    product = None
    if header and re.fullmatch(SLUG_RE_TEXT, header):
        product = conversions_repo.product_webhook(conn, header)
    if product is None:
        return reject(
            401,
            "invalid_signature",
            "Подпись не прошла проверку",
            IngestOutcome.UNKNOWN_PRODUCT.value,
            detail="unknown_product",
        )
    product_id = str(product["id"])
    ts = _parse_ts(raw.timestamp_header)
    keys = product_secrets(conn, product, now)
    signed = ts is not None and any(
        verify_ingest_signature(k, ts, raw.body, raw.signature_header) for k in keys
    )
    if not signed:
        detail = (
            "secret_not_set" if not keys else ("bad_timestamp_header" if ts is None else "signature_mismatch")
        )
        return reject(
            401,
            "invalid_signature",
            "Подпись не прошла проверку",
            IngestOutcome.INVALID_SIGNATURE.value,
            product_id=product_id,
            detail=detail,
        )
    # 3. окно времени
    skew = abs(now.timestamp() - ts)
    if skew > INGEST_MAX_SKEW_SEC:
        return reject(
            401,
            "stale_timestamp",
            "Метка времени вне окна ±300 с",
            IngestOutcome.STALE_TIMESTAMP.value,
            product_id=product_id,
            detail=f"skew_sec={int(skew)}",
            details={"max_skew_sec": INGEST_MAX_SKEW_SEC},
        )
    # 4. частота
    allowed, retry = limiter.allow(product_id)
    if not allowed:
        retry_s = max(1, int(retry + 0.999))
        return reject(
            429,
            "rate_limited",
            "Слишком много запросов, повторите позже",
            IngestOutcome.RATE_LIMITED.value,
            product_id=product_id,
            headers={"Retry-After": str(retry_s)},
            details={"retry_after_sec": retry_s, "limit_per_sec": INGEST_RATE_LIMIT_PER_SEC},
        )
    # 5–6. тело
    try:
        data = json.loads(raw.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return reject(
            422,
            "validation_error",
            "Тело — не JSON",
            IngestOutcome.INVALID_BODY.value,
            product_id=product_id,
            detail="json_decode",
            details={"errors": [{"loc": ["body"], "msg": "invalid JSON", "type": "json_invalid"}]},
        )
    events = data.get("events") if isinstance(data, dict) else None
    if isinstance(events, list) and len(events) > INGEST_MAX_EVENTS:
        return reject(
            413,
            "payload_too_large",
            f"Больше {INGEST_MAX_EVENTS} событий в запросе",
            IngestOutcome.PAYLOAD_TOO_LARGE.value,
            product_id=product_id,
            detail=f"events={len(events)}",
            details={"max_events": INGEST_MAX_EVENTS},
        )
    extra = set(data) - {"events"} if isinstance(data, dict) else set()
    if not isinstance(events, list) or not events or extra:
        msg = 'Нужен объект {"events": [...]} с 1–100 событиями'
        err_type = "extra_forbidden" if extra else "events_required"
        return reject(
            422,
            "validation_error",
            msg,
            IngestOutcome.INVALID_BODY.value,
            product_id=product_id,
            detail=err_type,
            details={"errors": [{"loc": ["body", "events"], "msg": msg, "type": err_type}]},
        )
    stored = _store_events(conn, product_id, events, is_test=is_test)
    counts = {k: stored[k] for k in ("accepted", "duplicates", "unknown_tag", "invalid")}
    log_id = _log_request(
        conn,
        raw,
        product_id=product_id,
        outcome=IngestOutcome.ACCEPTED.value,
        status=200,
        is_test=is_test,
        counts={**counts, "events_total": len(events)},
    )
    return IngestResult(
        200, {**counts, "results": stored["results"]}, IngestOutcome.ACCEPTED.value, {}, log_id
    )


# ---------------------------------------------------------------- вкладка «Вебхук»


def integration_view(conn: Any, product_id: str) -> dict[str, Any]:
    from autoshorts.public.emitter_snippet import node_snippet, python_snippet
    from autoshorts.services import tracking as tracking_service

    product = conversions_repo.product_webhook(conn, product_id)
    if product is None:
        raise NotFound("product", "Продукт не найден")
    full = tracking_service.product_for(conn, product_id)
    ingest_url = tracking_service.public_base(full) + INGEST_PATH
    sample = tracking_repo.any_product_link(conn, product_id)
    code = sample["code"] if sample else "k7m2q"
    example = {
        "events": [
            {
                "event_id": f"{product_id}-pay-99812",
                "type": "first_payment",
                "occurred_at": "2026-09-26T10:00:00Z",
                "tag": tags.with_landing_suffix(code),
                "user_ref": "tg:123456",
                "amount_minor": 49000,
                "currency": "RUB",
                "meta": {},
            }
        ]
    }
    deeplinks: list[dict[str, Any]] = []
    tg, tg_https = tracking_service.telegram_urls(full, code)
    if tg_https:
        if tracking_service.is_tma(full):
            deeplinks.append(
                {
                    "kind": "tma_startapp",
                    "url": tg_https,
                    "description": "Mini App получает start_param=<код> и шлёт tma_open",
                }
            )
        else:
            deeplinks.append(
                {
                    "kind": "bot_start",
                    "url": tg_https,
                    "description": "Бот получает /start <код> и шлёт bot_start с tag=<код>",
                }
            )
        _tg_l, tg_https_l = tracking_service.telegram_urls(full, tags.with_landing_suffix(code))
        deeplinks.append(
            {
                "kind": "bot_start_landing",
                "url": tg_https_l or "",
                "description": "С лендинга приходит <код>_l: via_landing, без двойного счёта",
            }
        )
    site = tracking_service.site_url(full, code, platform="youtube")
    if site:
        deeplinks.append(
            {
                "kind": "site_utm",
                "url": site,
                "description": "Сайт кладёт utm_campaign в localStorage и шлёт его в tag",
            }
        )
    secret_set = bool(_secret_value(conn, product.get("webhook_secret_name")))
    recent = [
        {
            "id": int(r["id"]),
            "received_at": r["received_at"],
            "outcome": r["outcome"],
            "http_status": int(r["http_status"]),
            "events_total": int(r["events_total"]),
            "accepted": int(r["accepted"]),
            "duplicates": int(r["duplicates"]),
            "unknown_tag": int(r["unknown_tag"]),
            "invalid": int(r["invalid"]),
            "is_test": bool(r["is_test"]),
            "detail": r.get("detail"),
        }
        for r in conversions_repo.recent_ingest_requests(conn, product_id, limit=20)
    ]
    now = datetime.now(UTC)
    prev_until = product.get("webhook_prev_valid_until")
    return {
        "product_id": product_id,
        "ingest_url": ingest_url,
        "headers": [
            {"name": INGEST_HEADER_PRODUCT, "value": product_id, "description": "ID продукта"},
            {"name": INGEST_HEADER_TIMESTAMP, "value": "<unix>", "description": "Секунды UTC, окно ±300 с"},
            {
                "name": INGEST_HEADER_SIGNATURE,
                "value": "sha256=<hex>",
                "description": 'HMAC-SHA256(secret, f"{ts}." + тело), hex',
            },
            {"name": "Content-Type", "value": "application/json", "description": "UTF-8"},
        ],
        "signature_spec": (
            'sha256=hex(HMAC_SHA256(secret, f"{ts}." + raw_body)); сравнение hmac.compare_digest'
        ),
        "max_skew_sec": INGEST_MAX_SKEW_SEC,
        "max_events": INGEST_MAX_EVENTS,
        "max_body_bytes": INGEST_MAX_BODY_BYTES,
        "rate_limit_per_sec": INGEST_RATE_LIMIT_PER_SEC,
        "event_types": [t.value for t in ConversionEventType],
        "example_payload": example,
        "deeplink_examples": deeplinks,
        "emitter_snippets": {
            "python": python_snippet(ingest_url, product_id),
            "node": node_snippet(ingest_url, product_id),
        },
        "secret_set": secret_set,
        "secret_rotated_at": product.get("webhook_secret_rotated_at"),
        "previous_secret_valid_until": prev_until if prev_until is not None and prev_until > now else None,
        "last_event_at": conversions_repo.last_event_at(conn, product_id),
        "recent_requests": recent,
    }


def send_test_conversion(
    conn: Any,
    product_id: str,
    event_type: str,
    tag: str | None,
    actor: Actor,
    *,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Настоящий подписанный запрос тем же путём приёма (`process`) с `is_test` — в статистику не идёт."""
    product = conversions_repo.product_webhook(conn, product_id)
    if product is None:
        raise NotFound("product", "Продукт не найден")
    if tag is None:
        sample = tracking_repo.any_product_link(conn, product_id)
        tag = sample["code"] if sample else None
    event_id = f"test-{pysecrets.token_hex(8)}"
    event: dict[str, Any] = {
        "event_id": event_id,
        "type": ConversionEventType(event_type).value,
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "user_ref": f"test:{actor.user_id or 0}",
        "meta": {"test": True},
    }
    if tag:
        event["tag"] = tag
    body = json.dumps({"events": [event]}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ts = int(time.time())
    secret = _secret_value(conn, product.get("webhook_secret_name"))
    signature = ingest_signature(secret, ts, body) if secret else ""
    raw = IngestRaw(product_id, str(ts), signature, body, len(body), request_id=request_id)
    # тестовый запрос не отнимает у продукта окно частоты
    result = process(conn, raw, is_test=True, limiter=RateLimiter(limit_per_sec=1_000_000))
    audit.record(
        conn,
        actor,
        "product.test_conversion",
        "product",
        product_id,
        summary=f"тестовое событие {event_type} → HTTP {result.status}",
        diff={"event_id": event_id, "http_status": result.status, "outcome": result.outcome},
    )
    error_code = None if result.ok else str((result.body.get("error") or {}).get("code") or result.outcome)
    return {
        "ok": result.ok,
        "http_status": result.status,
        "event_id": event_id,
        "error_code": error_code,
        "response": result.body if result.ok else None,
        "request_id": request_id,
    }


__all__ = [
    "IngestRaw",
    "IngestResult",
    "RateLimiter",
    "hash_user_ref",
    "integration_view",
    "process",
    "product_secrets",
    "rate_limiter",
    "rotate_secret",
    "send_test_conversion",
]
