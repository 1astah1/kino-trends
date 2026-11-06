"""Приём конверсий `POST /ingest/v1/events` (M3-B): неверная подпись → 401, ts старше 300 с → 401,
повтор event_id → duplicates=1, неизвестный tag → unknown_tag=1, 101 событие → 413, тело > 256 КБ → 413,
частота → 429, ротация секрета с окном прежнего, журнал ingest_requests, эталонный отправитель."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from autoshorts.api.schemas.conversions import INGEST_PATH, ingest_signature


@pytest.fixture
def public(app_env: Any, db: Any) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from autoshorts.public import app as public_app
    from autoshorts.services import ingest, secrets, settings

    settings.invalidate_cache()
    secrets.invalidate_cache()
    ingest.rate_limiter.reset()
    with TestClient(public_app.create_app()) as c:
        yield c
    ingest.rate_limiter.reset()


@pytest.fixture
def secret(public: Any, db: Any) -> str:
    """Секрет вебхука umka (через ротацию) и пакетная ссылка k7m2q ролика."""
    from tests.api.test_jobs_support import make_job
    from tests.api.test_packages_support import seed_m3

    seed_m3(db)
    job = make_job(db, status="published")
    db.execute(
        """INSERT INTO tracking_links (code, product_id, level, job_id, channel_id, platform)
           VALUES ('k7m2q', 'umka', 'package', %s, 'taro_main', 'youtube')""",
        (job["id"],),
    )
    return _rotate("umka", 24)


def _rotate(product_id: str, grace_hours: int) -> str:
    from autoshorts.db.pool import connection
    from autoshorts.services.audit import SYSTEM
    from autoshorts.services.ingest import rotate_secret

    with connection() as conn:
        return rotate_secret(conn, product_id, grace_hours, SYSTEM)["secret"]


def event(event_id: str = "umka-pay-1", **kw: Any) -> dict[str, Any]:
    ev = {
        "event_id": event_id,
        "type": "bot_start",
        "occurred_at": "2026-09-26T10:00:00Z",
        "tag": "k7m2q_l",
        "user_ref": "tg:123456",
    }
    ev.update(kw)
    return ev


def send(
    client: Any,
    secret: str,
    events: Any,
    *,
    ts: int | None = None,
    product: str = "umka",
    signature: str | None = None,
    raw: bytes | None = None,
) -> Any:
    body = (
        raw
        if raw is not None
        else json.dumps(events if isinstance(events, dict) else {"events": events}).encode()
    )
    ts = int(time.time()) if ts is None else ts
    sig = signature if signature is not None else ingest_signature(secret, ts, body)
    return client.post(
        INGEST_PATH,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-AS-Product": product,
            "X-AS-Timestamp": str(ts),
            "X-AS-Signature": sig,
        },
    )


def ingest_log(db: Any) -> list[dict[str, Any]]:
    return db.execute("SELECT * FROM ingest_requests ORDER BY id").fetchall()


def test_accepts_signed_event_and_attributes_it(public: Any, db: Any, secret: str) -> None:
    resp = send(public, secret, [event(amount_minor=49000, currency="RUB", meta={"plan": "pro"})])
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "accepted": 1,
        "duplicates": 0,
        "unknown_tag": 0,
        "invalid": 0,
        "results": [{"event_id": "umka-pay-1", "status": "accepted", "error": None}],
    }
    row = db.execute("SELECT * FROM conversions").fetchone()
    link = db.execute("SELECT * FROM tracking_links WHERE code = 'k7m2q'").fetchone()
    assert row["tracking_link_id"] == link["id"] and row["job_id"] == link["job_id"]
    assert row["channel_id"] == "taro_main" and row["platform"] == "youtube"
    assert row["via_landing"] is True and row["tag"] == "k7m2q_l"
    assert row["user_ref_hash"] == hashlib.sha256(b"umka|tg:123456").hexdigest()
    assert row["amount_minor"] == 49000 and row["currency"] == "RUB" and row["meta"] == {"plan": "pro"}
    assert row["is_test"] is False
    [log] = ingest_log(db)
    assert log["outcome"] == "accepted" and log["http_status"] == 200 and log["accepted"] == 1
    assert log["product_id"] == "umka" and log["events_total"] == 1


def test_duplicate_event_id(public: Any, db: Any, secret: str) -> None:
    assert send(public, secret, [event()]).json()["accepted"] == 1
    again = send(public, secret, [event()]).json()
    assert again["duplicates"] == 1 and again["accepted"] == 0
    assert again["results"][0]["status"] == "duplicate"
    assert db.execute("SELECT count(*) AS n FROM conversions").fetchone()["n"] == 1


def test_unknown_tag_is_stored_as_direct(public: Any, db: Any, secret: str) -> None:
    body = send(public, secret, [event("e-unknown", tag="nosuchcode")]).json()
    assert body["unknown_tag"] == 1 and body["accepted"] == 0
    row = db.execute("SELECT * FROM conversions").fetchone()
    assert row["tracking_link_id"] is None and row["tag"] == "nosuchcode"
    # без тега — прямой трафик продукта, это не «неизвестный тег»
    body = send(public, secret, [event("e-direct", tag=None)]).json()
    assert body["accepted"] == 1


def test_tag_of_other_product_is_unknown(public: Any, db: Any, secret: str) -> None:
    db.execute("INSERT INTO products (id, name, kind) VALUES ('vpn', 'VPN', 'telegram_bot')")
    db.execute("INSERT INTO tracking_links (code, product_id, level) VALUES ('vpncode', 'vpn', 'manual')")
    assert send(public, secret, [event(tag="vpncode")]).json()["unknown_tag"] == 1


def test_bad_signature_is_401(public: Any, db: Any, secret: str) -> None:
    resp = send(public, secret, [event()], signature="sha256=" + "0" * 64)
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "invalid_signature"
    wrong_secret = send(public, "not-the-secret", [event()])
    assert wrong_secret.status_code == 401
    no_sig = public.post(INGEST_PATH, content=b'{"events":[]}', headers={"X-AS-Product": "umka"})
    assert no_sig.status_code == 401
    assert db.execute("SELECT count(*) AS n FROM conversions").fetchone()["n"] == 0
    outcomes = [r["outcome"] for r in ingest_log(db)]
    assert outcomes == ["invalid_signature"] * 3


def test_unknown_product_is_401_without_hint(public: Any, db: Any, secret: str) -> None:
    resp = send(public, secret, [event()], product="nosuch")
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "invalid_signature"
    [log] = ingest_log(db)
    assert (
        log["outcome"] == "unknown_product"
        and log["product_id"] is None
        and log["product_header"] == "nosuch"
    )


def test_stale_timestamp_is_401(public: Any, db: Any, secret: str) -> None:
    old = int(time.time()) - 301
    resp = send(public, secret, [event()], ts=old)
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "stale_timestamp"
    future = send(public, secret, [event()], ts=int(time.time()) + 400)
    assert future.status_code == 401
    ok = send(public, secret, [event()], ts=int(time.time()) - 250)
    assert ok.status_code == 200
    assert [r["outcome"] for r in ingest_log(db)] == ["stale_timestamp", "stale_timestamp", "accepted"]


def test_too_many_events_is_413(public: Any, db: Any, secret: str) -> None:
    resp = send(public, secret, [event(f"e{i}") for i in range(101)])
    assert resp.status_code == 413 and resp.json()["error"]["code"] == "payload_too_large"
    assert db.execute("SELECT count(*) AS n FROM conversions").fetchone()["n"] == 0
    assert ingest_log(db)[0]["outcome"] == "payload_too_large"
    ok = send(public, secret, [event(f"e{i}") for i in range(100)])
    assert ok.status_code == 200 and ok.json()["accepted"] == 100


def test_body_over_256k_is_413(public: Any, db: Any, secret: str) -> None:
    big = json.dumps({"events": [event(meta={"x": "a" * 3000}) for _ in range(90)]}).encode()
    assert len(big) > 256 * 1024
    resp = send(public, secret, None, raw=big)
    assert resp.status_code == 413
    assert ingest_log(db)[0]["outcome"] == "payload_too_large"


def test_invalid_event_does_not_fail_batch(public: Any, db: Any, secret: str) -> None:
    body = send(
        public,
        secret,
        [
            event("good-1"),
            {"event_id": "bad-1", "type": "nonsense", "occurred_at": "2026-09-26T10:00:00Z"},
            {"event_id": "bad-2", "type": "signup", "occurred_at": "2026-09-26T10:00:00"},  # без пояса
            {"event_id": "bad 3", "type": "signup", "occurred_at": "2026-09-26T10:00:00Z"},
            event("bad-4", tag="код"),
        ],
    ).json()
    assert body["accepted"] == 1 and body["invalid"] == 4
    statuses = {r["event_id"]: r["status"] for r in body["results"]}
    assert statuses["good-1"] == "accepted" and statuses["bad-1"] == "invalid"
    assert all(r["error"] for r in body["results"] if r["status"] == "invalid")


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b'{"events": []}',
        b'{"events": "x"}',
        b'{"other": 1}',
        b"[1,2]",
        b'{"events": [], "x": 1}',
    ],
)
def test_bad_body_is_422(public: Any, db: Any, secret: str, raw: bytes) -> None:
    resp = send(public, secret, None, raw=raw)
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "validation_error"
    assert ingest_log(db)[0]["outcome"] == "invalid_body"


def test_rate_limit_is_429(public: Any, db: Any, secret: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from autoshorts.services import ingest

    monkeypatch.setattr(ingest, "rate_limiter", ingest.RateLimiter(limit_per_sec=2))
    codes = [send(public, secret, [event(f"r{i}")]).status_code for i in range(3)]
    assert codes == [200, 200, 429]
    resp = send(public, secret, [event("r9")])
    assert resp.status_code == 429 and int(resp.headers["retry-after"]) >= 1
    assert ingest_log(db)[-1]["outcome"] == "rate_limited"


def test_rate_limiter_window() -> None:
    from autoshorts.services.ingest import RateLimiter

    rl = RateLimiter(limit_per_sec=20)
    assert all(rl.allow("umka", now=100.0 + i / 100)[0] for i in range(20))
    ok, retry = rl.allow("umka", now=100.5)
    assert not ok and 0 < retry <= 1.0
    assert rl.allow("vpn", now=100.5)[0]  # у каждого продукта своё окно
    assert rl.allow("umka", now=101.3)[0]


def test_rotation_keeps_previous_secret_for_grace(public: Any, db: Any, secret: str) -> None:
    new = _rotate("umka", 24)
    assert new != secret
    assert send(public, new, [event("rot-1")]).status_code == 200
    assert send(public, secret, [event("rot-2")]).status_code == 200  # прежний — ещё сутки
    db.execute("UPDATE products SET webhook_prev_valid_until = now() - interval '1 minute' WHERE id = 'umka'")
    assert send(public, secret, [event("rot-3")]).status_code == 401
    newest = _rotate("umka", 0)
    assert send(public, new, [event("rot-4")]).status_code == 401  # grace 0 — прежний сразу нет
    assert send(public, newest, [event("rot-5")]).status_code == 200
    row = db.execute("SELECT * FROM products WHERE id = 'umka'").fetchone()
    assert row["webhook_secret_name"] == "PRODUCT_WEBHOOK_UMKA" and row["webhook_prev_secret_name"] is None


def test_emitter_snippet_is_accepted(public: Any, db: Any, secret: str) -> None:
    from autoshorts.public.emitter_snippet import build_request, node_snippet, python_snippet

    req = build_request("http://testserver" + INGEST_PATH, "umka", secret, [event("snip-1")])
    headers = {k: v for k, v in req.header_items()}
    resp = public.post(INGEST_PATH, content=req.data, headers=headers)
    assert resp.status_code == 200 and resp.json()["accepted"] == 1
    code = python_snippet("https://u.umka.chat/ingest/v1/events", "umka")
    compile(code, "emitter_snippet", "exec")
    assert 'INGEST_URL = "https://u.umka.chat/ingest/v1/events"' in code
    assert "sha256=" in node_snippet("https://u.umka.chat/ingest/v1/events", "umka")
    # продукт не может подсунуть кавычку в сниппет
    assert '"' not in python_snippet('x"y', "umka").split("INGEST_URL = ")[1].split("\n")[0][1:-1]


def test_process_uses_injected_clock(app_env: Any, db: Any, secret: str) -> None:
    from autoshorts.db.pool import connection
    from autoshorts.services.ingest import IngestRaw, RateLimiter, process

    body = json.dumps({"events": [event("clock-1")]}).encode()
    ts = int(datetime(2026, 9, 26, 10, 0, tzinfo=UTC).timestamp())
    raw = IngestRaw("umka", str(ts), ingest_signature(secret, ts, body), body, len(body))
    with connection() as conn:
        late = process(conn, raw, now=datetime(2026, 9, 26, 10, 6, tzinfo=UTC), limiter=RateLimiter())
        ok = process(
            conn,
            raw,
            now=datetime(2026, 9, 26, 10, 4, tzinfo=UTC) + timedelta(seconds=59),
            limiter=RateLimiter(),
        )
    assert late.status == 401 and late.outcome == "stale_timestamp"
    assert ok.status == 200 and ok.body["accepted"] == 1
