"""`UploadGuard`: multipart отсекается по размеру и входу до чтения тела (api/middleware.py).

Большая часть — чистый ASGI без БД: внутреннее приложение-заглушка и счётчик чтений `receive`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from starlette.exceptions import HTTPException

from autoshorts.api.middleware import UploadGuard

PATH = "/api/v1/banner-creatives"
MULTIPART = "multipart/form-data; boundary=xyz"


class Inner:
    """Внутреннее приложение: дочитывает тело и отвечает 201."""

    def __init__(self) -> None:
        self.called = False
        self.body = b""

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.called = True
        while True:
            message = await receive()
            self.body += message.get("body", b"")
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 201, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _scope(headers: dict[str, str], *, method: str = "POST", path: str = PATH) -> dict[str, Any]:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "http_version": "1.1",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("203.0.113.5", 5555),
    }


def _run(guard: UploadGuard, scope: dict[str, Any], chunks: list[bytes]) -> tuple[int | None, Any, int]:
    """→ (статус ответа, JSON тела или None, сколько раз читали `receive`)."""
    sent: list[dict[str, Any]] = []
    reads = 0
    pending = list(chunks)

    async def receive() -> dict[str, Any]:
        nonlocal reads
        reads += 1
        if pending:
            body = pending.pop(0)
            return {"type": "http.request", "body": body, "more_body": bool(pending)}
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    asyncio.run(guard(scope, receive, send))
    status = next((m["status"] for m in sent if m["type"] == "http.response.start"), None)
    raw = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        payload = None
    return status, payload, reads


def _guard(inner: Inner, limit: int = 51 * 1024 * 1024) -> UploadGuard:
    return UploadGuard(
        inner,
        api_prefix="/api/",
        limits={PATH: limit},
        limit_details={PATH: {"asset_error": "file_too_large"}},
    )


@pytest.fixture
def operator_session(monkeypatch: pytest.MonkeyPatch) -> str:
    """Сессия `sess` оператора с CSRF-токеном `tok` без БД."""
    from autoshorts.domain.enums import ActorKind, Role
    from autoshorts.services import auth
    from autoshorts.services.auth import Principal

    principal = Principal(
        kind=ActorKind.USER,
        user_id=7,
        login="op",
        display_name="Op",
        role=Role.OPERATOR,
        session_id=1,
        csrf_token="tok",
    )
    monkeypatch.setattr(auth, "authenticate_session", lambda token: principal if token == "sess" else None)
    return "as_session=sess; as_csrf=tok"


def test_oversized_content_length_rejected_before_body_and_auth() -> None:
    inner = Inner()
    status, payload, reads = _run(
        _guard(inner), _scope({"content-type": MULTIPART, "content-length": str(60 * 1024 * 1024)}), [b"x"]
    )
    assert status == 413 and reads == 0 and not inner.called
    assert payload["error"]["code"] == "payload_too_large"
    assert payload["error"]["details"]["asset_error"] == "file_too_large"
    assert payload["error"]["details"]["size_bytes"] == 60 * 1024 * 1024


def test_anonymous_multipart_rejected_before_body() -> None:
    inner = Inner()
    status, payload, reads = _run(
        _guard(inner), _scope({"content-type": MULTIPART, "content-length": "5000000"}), [b"x" * 100]
    )
    assert status == 401 and payload["error"]["code"] == "not_authenticated"
    assert reads == 0 and not inner.called

    bearer = _scope({"content-type": MULTIPART, "content-length": "10", "authorization": "Bearer nope"})
    status, _, reads = _run(_guard(inner), bearer, [b"x"])
    assert status == 401 and reads == 0 and not inner.called


def test_missing_csrf_rejected_before_body(operator_session: str) -> None:
    inner = Inner()
    scope = _scope({"content-type": MULTIPART, "content-length": "10", "cookie": operator_session})
    status, payload, reads = _run(_guard(inner), scope, [b"x" * 10])
    assert status == 403 and payload["error"]["code"] == "csrf_failed"
    assert reads == 0 and not inner.called


def test_authenticated_upload_passes_and_caches_principal(operator_session: str) -> None:
    inner = Inner()
    scope = _scope(
        {
            "content-type": MULTIPART,
            "content-length": "20",
            "cookie": operator_session,
            "x-csrf-token": "tok",
        }
    )
    status, _, _ = _run(_guard(inner), scope, [b"a" * 10, b"b" * 10])
    assert status == 201 and inner.called and inner.body == b"a" * 10 + b"b" * 10
    assert scope["state"]["principal"].user_id == 7


def test_chunked_body_is_cut_at_limit(operator_session: str) -> None:
    inner = Inner()
    scope = _scope({"content-type": MULTIPART, "cookie": operator_session, "x-csrf-token": "tok"})
    with pytest.raises(HTTPException) as info:
        _run(_guard(inner, limit=100), scope, [b"a" * 60, b"b" * 60, b"c" * 60])
    assert info.value.status_code == 413
    assert info.value.detail["code"] == "payload_too_large"
    assert len(inner.body) == 60  # третий кусок уже не читали


def test_other_requests_pass_through() -> None:
    inner = Inner()
    status, _, _ = _run(_guard(inner), _scope({"content-type": "application/json"}), [b"{}"])
    assert status == 201 and inner.called
    inner = Inner()
    status, _, _ = _run(_guard(inner), _scope({"content-type": MULTIPART}, method="GET"), [b""])
    assert status == 201 and inner.called
    inner = Inner()
    status, _, _ = _run(_guard(inner), _scope({"content-type": MULTIPART}, path="/ingest/v1/x"), [b""])
    assert status == 201 and inner.called


def test_app_rejects_anonymous_creative_upload(client: Any) -> None:
    resp = client.post(f"{PATH}", files={"file": ("a.png", b"x" * 2048, "image/png")})
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "not_authenticated"
