"""Эталонный отправитель конверсий продукта в Auto Shorts (docs/ARCHITECTURE.md §5.3, §6.6).

Только stdlib (hmac, json, urllib): модуль копируется в репозиторий продукта как есть. Вкладка
«Вебхук» продукта (`GET /products/{id}/integration`) отдаёт этот же код с подставленными URL и ID
(`python_snippet`) и Node-вариант (`node_snippet`).

    from emitter_snippet import send_events
    send_events(
        "https://u.umka.chat/ingest/v1/events", "umka", SECRET,
        [{"event_id": "umka-pay-99812", "type": "first_payment", "occurred_at": "2026-09-26T10:00:00Z",
          "tag": "k7m2q_l", "user_ref": "tg:123456", "amount_minor": 49000, "currency": "RUB"}],
    )

Что шлют продукты: бот на `/start <param>` — `bot_start` с `tag=<param>`; TMA — `tma_open` со
`start_param`; сайт при первом заходе кладёт `utm_campaign` в localStorage и шлёт его в `tag` с
`signup` и `first_payment`; хранимый код пользователя (first-touch, окно 30 дней) подставляется во все
последующие события. `event_id` уникален в продукте: повтор даёт `duplicate`, а не второй платёж.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------- эталон (копируется в продукт)

_REFERENCE = '''import hashlib, hmac, json, time, urllib.error, urllib.request

INGEST_URL = "{ingest_url}"
PRODUCT_ID = "{product_id}"


def send_events(secret: str, events: list[dict], *, timeout: float = 10.0) -> dict:
    """POST событий с подписью HMAC-SHA256. 200 — {{accepted, duplicates, unknown_tag, invalid, results}}."""
    body = json.dumps({{"events": events}}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode("utf-8"), ts.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(INGEST_URL, data=body, method="POST", headers={{
        "Content-Type": "application/json",
        "X-AS-Product": PRODUCT_ID,
        "X-AS-Timestamp": ts,
        "X-AS-Signature": "sha256=" + sig,
    }})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # 401 подпись/время, 413 размер, 429 частота (Retry-After)
        raise RuntimeError(f"ingest HTTP {{exc.code}}: {{exc.read()[:300]!r}}") from exc


# пример: бот получил /start k7m2q_l
# send_events(SECRET, [{{"event_id": "bot-start-123456", "type": "bot_start",
#                       "occurred_at": "2026-09-26T10:00:00Z", "tag": "k7m2q_l", "user_ref": "tg:123456"}}])
'''

_NODE = """import crypto from "node:crypto";

const INGEST_URL = "{ingest_url}";
const PRODUCT_ID = "{product_id}";

export async function sendEvents(secret, events) {{
  const body = JSON.stringify({{ events }});
  const ts = String(Math.floor(Date.now() / 1000));
  const sig = crypto.createHmac("sha256", secret).update(ts + "." + body, "utf8").digest("hex");
  const res = await fetch(INGEST_URL, {{
    method: "POST",
    headers: {{
      "Content-Type": "application/json",
      "X-AS-Product": PRODUCT_ID,
      "X-AS-Timestamp": ts,
      "X-AS-Signature": "sha256=" + sig,
    }},
    body,
  }});
  if (!res.ok) throw new Error(`ingest HTTP ${{res.status}}: ${{(await res.text()).slice(0, 300)}}`);
  return res.json(); // {{accepted, duplicates, unknown_tag, invalid, results}}
}}

// пример: бот получил /start k7m2q_l
// await sendEvents(SECRET, [{{ event_id: "bot-start-123456", type: "bot_start",
//   occurred_at: new Date().toISOString(), tag: "k7m2q_l", user_ref: "tg:123456" }}]);
"""


def _safe(value: str) -> str:
    """URL и ID продукта в строковом литерале: без кавычек, обратных слешей и переводов строк."""
    return "".join(c for c in str(value) if c not in '"\\\n\r`$')


def python_snippet(ingest_url: str, product_id: str) -> str:
    return _REFERENCE.format(ingest_url=_safe(ingest_url), product_id=_safe(product_id))


def node_snippet(ingest_url: str, product_id: str) -> str:
    return _NODE.format(ingest_url=_safe(ingest_url), product_id=_safe(product_id))


# ---------------------------------------------------------------- тот же код как функция


def sign(secret: str, timestamp: int | str, body: bytes) -> str:
    """Значение `X-AS-Signature`: `sha256=<hex(HMAC_SHA256(secret, f"{ts}." + body))>`."""
    msg = str(int(timestamp)).encode("ascii") + b"." + body
    return "sha256=" + hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def build_request(
    ingest_url: str, product_id: str, secret: str, events: list[dict], *, timestamp: int | None = None
) -> urllib.request.Request:
    body = json.dumps({"events": events}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ts = int(time.time()) if timestamp is None else int(timestamp)
    return urllib.request.Request(
        ingest_url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-AS-Product": product_id,
            "X-AS-Timestamp": str(ts),
            "X-AS-Signature": sign(secret, ts, body),
        },
    )


def send_events(
    ingest_url: str, product_id: str, secret: str, events: list[dict], *, timeout: float = 10.0
) -> dict:
    req = build_request(ingest_url, product_id, secret, events)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URL задаёт продукт
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"ingest HTTP {exc.code}: {exc.read()[:300]!r}") from exc


__all__ = ["build_request", "node_snippet", "python_snippet", "send_events", "sign"]
