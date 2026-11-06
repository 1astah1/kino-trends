"""SSE `/api/v1/events/stream`: LISTEN as_events → кадры, ping, авторизация, живой uvicorn."""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import pytest

from tests.api.conftest import API

E2E_PORT = 18300  # порт API из §0.1; занят — живой тест пропускается


def test_stream_requires_auth(client) -> None:
    resp = client.get(f"{API}/events/stream")
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["error"]["code"] == "not_authenticated"


def _publish(url: str, event: str, data: dict) -> None:
    import psycopg

    from autoshorts.db.notify import publish_event

    with psycopg.connect(url, autocommit=True) as conn:
        publish_event(conn, event, data)


def _raw_notify(url: str, payload: str) -> None:
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("SELECT pg_notify('as_events', %s)", (payload,))


def test_broadcaster_frames(app_env) -> None:
    from autoshorts.api.sse import EventBroadcaster, event_frames

    url = app_env.database_url

    async def scenario() -> list[str]:
        broadcaster = EventBroadcaster(url)
        frames = event_frames(broadcaster, ping_interval=0.2)
        got = [await frames.__anext__(), await frames.__anext__()]
        await asyncio.wait_for(broadcaster.connected.wait(), 5)
        assert broadcaster.subscribers == 1
        await asyncio.to_thread(_raw_notify, url, "not json at all")
        await asyncio.to_thread(
            _publish, url, "job_status", {"job_id": 7, "status": "pending_review", "stage": "qc"}
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            frame = await asyncio.wait_for(frames.__anext__(), 5)
            got.append(frame)
            if frame.startswith("event: job_status"):
                break
        await broadcaster.close()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(frames.__anext__(), 5)
        assert broadcaster.subscribers == 0
        return got

    frames = asyncio.run(scenario())
    assert frames[0] == "retry: 5000\n\n"
    assert frames[1] == "event: ping\ndata: {}\n\n"
    assert frames[-1] == 'event: job_status\ndata: {"job_id":7,"status":"pending_review","stage":"qc"}\n\n'
    assert all(f.startswith(("retry:", "event: ping", "event: job_status")) for f in frames)  # мусор пропущен


def test_slow_subscriber_drops_oldest() -> None:
    from autoshorts.api import sse

    sub = sse.Subscription()
    for i in range(sse.QUEUE_MAX + 5):
        sub.put(str(i))
    assert sub.dropped == 5
    assert sub.queue.get_nowait() == "5"


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def test_live_uvicorn_stream(app_env, fast_password_hasher) -> None:
    """Настоящий uvicorn на 127.0.0.1:18300: вход по cookie, поток, событие из NOTIFY, выход."""
    import httpx
    import uvicorn

    from autoshorts.api.app import create_app
    from autoshorts.services import auth
    from autoshorts.services.audit import SYSTEM
    from autoshorts.services.users import create_user

    if not _port_free(E2E_PORT):
        pytest.skip(f"порт {E2E_PORT} занят")
    auth.login_ip_limiter.reset()
    app = create_app()
    app.state.sse_ping_interval = 0.5
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=E2E_PORT,
            log_level="warning",
            lifespan="on",
            timeout_graceful_shutdown=2,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started, "uvicorn не стартовал"
        create_user(
            login="sse_admin",
            display_name="SSE",
            role="admin",
            password="sse-password-12",
            tg_user_id=None,
            actor=SYSTEM,
        )
        base = f"http://127.0.0.1:{E2E_PORT}"
        with httpx.Client(base_url=base, timeout=httpx.Timeout(10.0, read=5.0)) as http:
            assert http.get(f"{API}/health/ready").status_code == 200
            login = http.post(f"{API}/auth/login", json={"login": "sse_admin", "password": "sse-password-12"})
            assert login.status_code == 200
            csrf = login.json()["csrf_token"]
            lines: list[str] = []
            with http.stream("GET", f"{API}/events/stream", headers={"Accept": "text/event-stream"}) as resp:
                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith("text/event-stream")
                assert resp.headers["x-accel-buffering"] == "no"
                published = False
                for line in resp.iter_lines():
                    lines.append(line)
                    if line == "event: ping" and not published:
                        # слушатель мог ещё не подписаться: публикуем, когда он точно на связи
                        while not app.state.events.connected or not app.state.events.connected.is_set():
                            time.sleep(0.05)
                        _publish(app_env.database_url, "review_queue", {"count": 3})
                        published = True
                    if line.startswith("data: ") and lines[-2:-1] == ["event: review_queue"]:
                        break
            assert "event: review_queue" in lines and 'data: {"count":3}' in lines
            assert http.post(f"{API}/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
            assert http.get(f"{API}/auth/me").status_code == 401
    finally:
        server.should_exit = True
        thread.join(timeout=15)
    assert not thread.is_alive()
