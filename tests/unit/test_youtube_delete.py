"""`publishers.youtube.delete_video`: 204 и 404 — удалено, 403/5xx — PublishError."""

from __future__ import annotations

import httpx
import pytest

from autoshorts.publishers import youtube as yt


def _client(status: int, body: dict | None = None) -> tuple[httpx.Client, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body) if body is not None else httpx.Response(status)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_delete_video_ok_and_missing() -> None:
    for status in (204, 404):
        client, seen = _client(status)
        yt.delete_video(client, "tok", "abc123")
        assert seen[0].method == "DELETE" and seen[0].url.params["id"] == "abc123"
        assert seen[0].headers["authorization"] == "Bearer tok"


def test_delete_video_forbidden_raises() -> None:
    client, _ = _client(403, {"error": {"errors": [{"reason": "insufficientPermissions"}], "message": "no"}})
    with pytest.raises(yt.PublishError) as exc:
        yt.delete_video(client, "tok", "abc123")
    assert exc.value.status == 403
    with pytest.raises(yt.PublishError):
        yt.delete_video(client, "tok", "")
