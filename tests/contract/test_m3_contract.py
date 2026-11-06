"""Контракт M3 (поток M5M3-0): пакеты, публикации, трекинг, приём конверсий с HMAC.

Сверяется: эталонная подпись приёма (§5.3) и её проверка, тело приёма (лимит 100 событий, код в
алфавите start-параметра Telegram, время с поясом), коды трекинга (латиница, без кириллицы и
разметки), алфавит пакетного кода, имя секрета вебхука, таблица эндпоинтов M3.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any

import pytest
from pydantic import ValidationError

from autoshorts.api.schemas import conversions as CV
from autoshorts.api.schemas import packages as P
from autoshorts.api.schemas import product_integration as PI
from autoshorts.api.schemas import publications as PUB
from autoshorts.api.schemas import tracking as TR
from autoshorts.api.schemas.common import SECRET_NAME_PATTERN, SLUG_PATTERN

SPEC_EVENT = {
    "event_id": "umka-pay-99812",
    "type": "first_payment",
    "occurred_at": "2026-09-26T10:00:00Z",
    "tag": "k7m2q_l",
    "user_ref": "tg:123456",
    "amount_minor": 49000,
    "currency": "RUB",
    "meta": {},
}


# ---------------------------------------------------------------- подпись приёма


def test_signature_matches_spec_formula() -> None:
    secret, ts = "s3cr3t", 1790000000
    body = json.dumps({"events": [SPEC_EVENT]}, separators=(",", ":")).encode("utf-8")
    expected = "sha256=" + hmac.new(b"s3cr3t", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    assert CV.ingest_signature(secret, ts, body) == expected
    assert CV.ingest_signature(secret.encode(), str(ts), body) == expected
    assert CV.verify_ingest_signature(secret, ts, body, expected)
    assert CV.verify_ingest_signature(secret, ts, body, f"  {expected} ")
    assert not CV.verify_ingest_signature(secret, ts, body + b" ", expected)
    assert not CV.verify_ingest_signature(secret, ts + 1, body, expected)
    assert not CV.verify_ingest_signature("other", ts, body, expected)
    assert not CV.verify_ingest_signature(secret, ts, body, None)
    assert not CV.verify_ingest_signature(secret, ts, body, "sha256=подпись")
    assert not CV.verify_ingest_signature(secret, "not-a-number", body, expected)


def test_ingest_limits_are_spec_values() -> None:
    assert CV.INGEST_PATH == "/ingest/v1/events"
    assert (CV.INGEST_MAX_SKEW_SEC, CV.INGEST_MAX_EVENTS, CV.INGEST_MAX_BODY_BYTES) == (300, 100, 256 * 1024)
    assert CV.INGEST_RATE_LIMIT_PER_SEC == 20
    assert [CV.INGEST_HEADER_PRODUCT, CV.INGEST_HEADER_TIMESTAMP, CV.INGEST_HEADER_SIGNATURE] == [
        "X-AS-Product",
        "X-AS-Timestamp",
        "X-AS-Signature",
    ]


# ---------------------------------------------------------------- тело приёма


def test_spec_example_body_validates() -> None:
    req = CV.IngestRequest.model_validate({"events": [SPEC_EVENT]})
    ev = req.events[0]
    assert ev.type == "first_payment" and ev.tag == "k7m2q_l" and ev.amount_minor == 49000
    resp = CV.IngestResponse.model_validate(
        {
            "accepted": 1,
            "duplicates": 0,
            "unknown_tag": 0,
            "results": [{"event_id": "umka-pay-99812", "status": "accepted"}],
        }
    )
    assert resp.invalid == 0


def test_event_count_limit() -> None:
    events = [{**SPEC_EVENT, "event_id": f"e{i}"} for i in range(CV.INGEST_MAX_EVENTS)]
    assert len(CV.IngestRequest.model_validate({"events": events}).events) == 100
    with pytest.raises(ValidationError):
        CV.IngestRequest.model_validate({"events": [*events, {**SPEC_EVENT, "event_id": "e100"}]})
    with pytest.raises(ValidationError):
        CV.IngestRequest.model_validate({"events": []})


@pytest.mark.parametrize(
    "patch",
    [
        {"tag": "код_l"},  # кириллица в start-параметре невозможна
        {"tag": "<script>"},
        {"tag": "a" * 65},
        {"event_id": "with space"},
        {"event_id": "x" * 129},
        {"occurred_at": "2026-09-26T10:00:00"},  # без пояса
        {"type": "purchase"},
        {"currency": "rub"},
        {"amount_minor": -1},
        {"meta": {"blob": "x" * 5000}},
        {"unknown_key": 1},
    ],
)
def test_bad_event_rejected(patch: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CV.IngestEvent.model_validate({**SPEC_EVENT, **patch})


# ---------------------------------------------------------------- коды трекинга и секрет


@pytest.mark.parametrize(
    ("code", "ok"),
    [
        ("k7m2q", True),
        ("yt_dQw4w9WgXcQ", True),
        ("vk_-123_456", True),
        ("x", False),
        ("код", False),
        ("<script>", False),
        ("a" * 65, False),
        ("with space", False),
    ],
)
def test_tracking_code_pattern(code: str, ok: bool) -> None:
    assert bool(re.fullmatch(TR.TRACKING_CODE_PATTERN, code)) is ok
    if not ok:
        with pytest.raises(ValidationError):
            TR.TrackingLinkCreate.model_validate({"product_id": "umka", "level": "manual", "code": code})


def test_package_code_alphabet() -> None:
    alphabet = TR.PACKAGE_CODE_ALPHABET
    assert len(alphabet) == len(set(alphabet)) == 30
    assert not set("0o1liu") & set(alphabet)
    assert alphabet == alphabet.lower() and alphabet.isascii()
    assert TR.PACKAGE_CODE_LENGTH == 5 and TR.LANDING_SUFFIX == "_l"


@pytest.mark.parametrize("product_id", ["umka", "vpn-bot", "gamekeys_tma", "a1"])
def test_webhook_secret_name(product_id: str) -> None:
    assert re.fullmatch(SLUG_PATTERN, product_id)
    assert re.fullmatch(SECRET_NAME_PATTERN, PI.webhook_secret_name(product_id))


def test_tracking_link_create_levels() -> None:
    assert {m.value for m in TR.ManualTrackingLevel} == {"manual", "channel", "campaign"}
    with pytest.raises(ValidationError):  # package/publication создаёт система
        TR.TrackingLinkCreate.model_validate({"product_id": "umka", "level": "package"})
    with pytest.raises(ValidationError):  # только https
        TR.TrackingLinkCreate.model_validate(
            {"product_id": "umka", "level": "manual", "target_override_url": "http://x.ru/"}
        )


def test_package_patch_hashtags() -> None:
    ok = P.PackagePatch.model_validate({"hashtags": ["#таро", "#расклад"]})
    assert ok.hashtags == ["#таро", "#расклад"]
    for bad in (["таро"], ["#два слова"], ["#"]):
        with pytest.raises(ValidationError):
            P.PackagePatch.model_validate({"hashtags": bad})
    assert set(P.PLATFORM_TEXT_LIMITS) == {"youtube", "vk"}


def test_parse_url_request_limits() -> None:
    with pytest.raises(ValidationError):
        PUB.ParseUrlRequest.model_validate({"url": "x"})
    PUB.ParseUrlRequest.model_validate({"url": "https://www.youtube.com/shorts/dQw4w9WgXcQ"})


# ---------------------------------------------------------------- эндпоинты M3


M3_ENDPOINTS = {
    ("GET", "/api/v1/packages"): "packages_list",
    ("GET", "/api/v1/packages/{package_id}"): "packages_get",
    ("GET", "/api/v1/jobs/{job_id}/package"): "packages_job_package_get",
    ("POST", "/api/v1/jobs/{job_id}/package"): "packages_job_package_rebuild",
    ("PATCH", "/api/v1/packages/{package_id}"): "packages_update",
    ("PATCH", "/api/v1/packages/{package_id}/checklist"): "packages_checklist_update",
    ("POST", "/api/v1/packages/{package_id}/mark-uploaded"): "packages_mark_uploaded",
    ("GET", "/api/v1/packages/{package_id}/video"): "packages_video",
    ("GET", "/api/v1/packages/{package_id}/text"): "packages_text",
    ("GET", "/api/v1/packages/{package_id}/zip"): "packages_zip",
    ("POST", "/api/v1/publications/parse-url"): "publications_parse_url",
    ("POST", "/api/v1/publications"): "publications_create",
    ("GET", "/api/v1/publications"): "publications_list",
    ("GET", "/api/v1/publications/{publication_id}"): "publications_get",
    ("PATCH", "/api/v1/publications/{publication_id}"): "publications_update",
    ("DELETE", "/api/v1/publications/{publication_id}"): "publications_delete",
    ("POST", "/api/v1/publications/{publication_id}/reverify"): "publications_reverify",
    ("POST", "/api/v1/publications/{publication_id}/refresh-metrics"): "publications_refresh_metrics",
    ("GET", "/api/v1/publications/{publication_id}/metrics"): "publications_metrics",
    ("GET", "/api/v1/publications/{publication_id}/retention"): "publications_retention",
    ("GET", "/api/v1/publications/{publication_id}/traffic"): "publications_traffic",
    ("GET", "/api/v1/tracking-links"): "tracking_links_list",
    ("POST", "/api/v1/tracking-links"): "tracking_links_create",
    ("GET", "/api/v1/tracking-links/{link_id}"): "tracking_links_get",
    ("PATCH", "/api/v1/tracking-links/{link_id}"): "tracking_links_update",
    ("GET", "/api/v1/tracking-links/{link_id}/stats"): "tracking_links_stats",
    ("POST", "/api/v1/channels/{channel_id}/tracking-link"): "tracking_channel_link_create",
    ("GET", "/api/v1/clicks"): "tracking_clicks_list",
    ("GET", "/api/v1/conversions"): "tracking_conversions_list",
    ("POST", "/api/v1/products/{product_id}/webhook-secret/rotate"): "integration_webhook_secret_rotate",
    ("GET", "/api/v1/products/{product_id}/integration"): "integration_get",
    ("POST", "/api/v1/products/{product_id}/test-conversion"): "integration_test_conversion",
}


def test_m3_endpoints_present(openapi_spec: dict[str, Any]) -> None:
    ops = {
        (method.upper(), path): op["operationId"]
        for path, item in openapi_spec["paths"].items()
        for method, op in item.items()
    }
    missing = {k: v for k, v in M3_ENDPOINTS.items() if ops.get(k) != v}
    assert not missing, missing


def test_publication_create_accepts_idempotency_key(openapi_spec: dict[str, Any]) -> None:
    params = openapi_spec["paths"]["/api/v1/publications"]["post"].get("parameters", [])
    assert any(p["name"] == "Idempotency-Key" and p["in"] == "header" for p in params)


def test_public_ingest_is_not_under_api_prefix(openapi_spec: dict[str, Any]) -> None:
    # приём конверсий живёт на хосте редиректора (public/*, M3-B), не в /api/v1
    assert not any("ingest" in path for path in openapi_spec["paths"])
