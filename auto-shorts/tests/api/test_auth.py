"""Критерий M1 (§10.2): логин, 403 без X-CSRF-Token, роли, блокировка после 10 неудач.

Плюс: скользящая сессия, лимит попыток с IP, смена пароля, свои сессии, API-токены.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.api.conftest import API, ApiUser


def _cookie_header(resp, name: str) -> str:
    return next(v for v in resp.headers.get_list("set-cookie") if v.startswith(f"{name}="))


# ---------------------------------------------------------------- вход и выход


def test_login_sets_cookies_and_me_returns_user(client, make_user) -> None:
    user = make_user("admin1", "admin")
    resp = client.post(f"{API}/auth/login", json={"login": "Admin1 ", "password": user["password"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"] == {
        "id": user["id"],
        "login": "admin1",
        "display_name": "Admin1",
        "role": "admin",
        "tg_user_id": None,
    }
    session_cookie = _cookie_header(resp, "as_session").lower()
    csrf_cookie = _cookie_header(resp, "as_csrf").lower()
    assert "httponly" in session_cookie and "samesite=lax" in session_cookie and "path=/" in session_cookie
    assert "httponly" not in csrf_cookie  # фронт читает as_csrf из JS
    assert client.cookies.get("as_csrf") == body["csrf_token"]

    me = client.get(f"{API}/auth/me")
    assert me.status_code == 200
    assert me.json() == body
    assert me.headers["cache-control"] == "no-store"
    assert me.headers["x-request-id"]


def test_session_cookie_secure_in_prod(client, make_user, monkeypatch) -> None:
    from autoshorts.core.config import reload_env

    user = make_user("admin1", "admin")
    monkeypatch.setenv("APP_ENV", "prod")
    reload_env()
    resp = client.post(f"{API}/auth/login", json={"login": "admin1", "password": user["password"]})
    assert resp.status_code == 200
    assert "secure" in _cookie_header(resp, "as_session").lower()
    assert "secure" in _cookie_header(resp, "as_csrf").lower()


def test_wrong_password_and_unknown_login_are_401(client, make_user) -> None:
    make_user("admin1", "admin")
    for login, password in (("admin1", "not-the-password"), ("nobody", "whatever-password")):
        resp = client.post(f"{API}/auth/login", json={"login": login, "password": password})
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "not_authenticated"
    assert "as_session" not in client.cookies


def test_inactive_user_cannot_login(client, make_user, db) -> None:
    user = make_user("op1", "operator")
    db.execute("UPDATE users SET is_active = false WHERE id = %s", (user["id"],))
    resp = client.post(f"{API}/auth/login", json={"login": "op1", "password": user["password"]})
    assert resp.status_code == 401


def test_unauthenticated_requests_get_401(client) -> None:
    for method, path in (
        ("GET", "/auth/me"),
        ("GET", "/system/status"),
        ("GET", "/users"),
        ("POST", "/auth/logout"),
    ):
        resp = client.request(method, f"{API}{path}")
        assert resp.status_code == 401, (method, path, resp.text)
        assert resp.json()["error"]["code"] == "not_authenticated"


def test_logout_requires_csrf_then_revokes_session(login_as) -> None:
    admin = login_as("admin")
    no_header = admin.post("/auth/logout", csrf=False)
    assert no_header.status_code == 403
    assert no_header.json()["error"]["code"] == "csrf_failed"
    wrong = admin.post("/auth/logout", headers={"X-CSRF-Token": "wrong-token"})
    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "csrf_failed"
    assert admin.get("/auth/me").status_code == 200  # сессия цела

    ok = admin.post("/auth/logout")
    assert ok.status_code == 204
    assert admin.get("/auth/me").status_code == 401


def test_csrf_header_must_match_cookie(login_as) -> None:
    admin = login_as("admin")
    admin.client.cookies.set("as_csrf", "tampered-cookie-value")
    resp = admin.post("/budget/pause", json={"reason": "проверка"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_failed"


def test_me_refreshes_csrf_cookie(login_as) -> None:
    admin = login_as("admin")
    admin.client.cookies.delete("as_csrf")
    me = admin.get("/auth/me")
    assert me.status_code == 200
    assert admin.client.cookies.get("as_csrf") == me.json()["csrf_token"] == admin.csrf
    assert admin.post("/budget/resume").status_code == 200


def test_mutation_without_csrf_header_is_403_for_every_role(login_as) -> None:
    for role in ("admin", "operator", "viewer"):
        user = login_as(role)
        resp = user.post("/alerts/1/ack", csrf=False)
        assert resp.status_code == 403, role
        assert resp.json()["error"]["code"] == "csrf_failed"


# ---------------------------------------------------------------- роли


@pytest.mark.parametrize(
    ("method", "path", "body", "allowed"),
    [
        ("GET", "/system/status", None, {"admin", "operator", "viewer"}),
        ("GET", "/budget", None, {"admin", "operator", "viewer"}),
        ("GET", "/meta/enums", None, {"admin", "operator", "viewer"}),
        ("GET", "/providers", None, {"admin", "operator", "viewer"}),
        ("GET", "/alerts", None, {"admin", "operator", "viewer"}),
        ("GET", "/settings", None, {"admin", "operator"}),
        ("GET", "/audit-log", None, {"admin", "operator"}),
        ("GET", "/users", None, {"admin"}),
        ("GET", "/secrets", None, {"admin"}),
        ("GET", "/api-tokens", None, {"admin"}),
        ("PATCH", "/settings", {}, {"admin"}),
        ("PATCH", "/budget", {}, {"admin"}),
        ("POST", "/budget/resume", None, {"admin"}),
        ("PUT", "/secrets/FAL_API_KEY", {"value": "x" * 12}, {"admin"}),
    ],
)
def test_role_matrix(login_as, method, path, body, allowed) -> None:
    for role in ("admin", "operator", "viewer"):
        user = login_as(role)
        kwargs = {"json": body} if body is not None else {}
        resp = user.request(method, path, **kwargs)
        if role in allowed:
            assert resp.status_code < 400, (role, method, path, resp.status_code, resp.text)
        else:
            assert resp.status_code == 403, (role, method, path, resp.status_code, resp.text)
            assert resp.json()["error"]["code"] == "forbidden"


def test_viewer_can_manage_own_login_but_not_content(login_as) -> None:
    viewer = login_as("viewer")
    assert viewer.post("/alerts/1/ack").status_code == 403
    assert viewer.get("/auth/sessions").status_code == 200
    assert viewer.post("/auth/logout").status_code == 204


def test_role_change_applies_to_existing_session(login_as, db) -> None:
    op = login_as("operator")
    assert op.get("/users").status_code == 403
    db.execute("UPDATE users SET role = 'admin' WHERE id = %s", (op.user["id"],))
    assert op.get("/users").status_code == 200


# ---------------------------------------------------------------- перебор


@pytest.fixture
def no_ip_limit(monkeypatch):
    from autoshorts.services import auth

    monkeypatch.setattr(auth.login_ip_limiter, "limit", 1000)


def test_lockout_after_ten_failures(client, make_user, db, login_as, no_ip_limit) -> None:
    user = make_user("op1", "operator")
    codes = []
    for _ in range(10):
        resp = client.post(f"{API}/auth/login", json={"login": "op1", "password": "wrong-password-x"})
        codes.append(resp.status_code)
    assert codes[:9] == [401] * 9
    assert codes[9] == 429
    locked = client.post(f"{API}/auth/login", json={"login": "op1", "password": user["password"]})
    assert locked.status_code == 429
    err = locked.json()["error"]
    assert err["code"] == "rate_limited" and err["details"]["locked_until"]
    assert int(locked.headers["retry-after"]) > 0

    row = db.execute("SELECT failed_logins, locked_until FROM users WHERE id = %s", (user["id"],)).fetchone()
    assert row["failed_logins"] == 10 and row["locked_until"] > datetime.now(UTC)
    actions = [
        r["action"]
        for r in db.execute(
            "SELECT action FROM audit_log WHERE entity_type = 'user' AND entity_id = %s ORDER BY id",
            (str(user["id"]),),
        ).fetchall()
    ]
    assert actions.count("auth.login_failed") == 10 and "auth.locked" in actions

    # админ сбрасывает пароль — блокировка снята, счётчик начинается заново
    admin = login_as("admin")
    assert (
        admin.post(
            f"/users/{user['id']}/reset-password", json={"new_password": "brand-new-pass-1"}
        ).status_code
        == 204
    )
    resp = client.post(f"{API}/auth/login", json={"login": "op1", "password": "wrong-password-x"})
    assert resp.status_code == 401
    ok = client.post(f"{API}/auth/login", json={"login": "op1", "password": "brand-new-pass-1"})
    assert ok.status_code == 200
    row = db.execute("SELECT failed_logins, locked_until FROM users WHERE id = %s", (user["id"],)).fetchone()
    assert row["failed_logins"] == 0 and row["locked_until"] is None


def test_lock_expires_and_old_failures_leave_window(client, make_user, db, no_ip_limit) -> None:
    user = make_user("op1", "operator")
    for _ in range(10):
        client.post(f"{API}/auth/login", json={"login": "op1", "password": "wrong-password-x"})
    # прошло 16 минут: блокировка истекла, неудачи вне окна 15 минут
    db.execute("UPDATE users SET locked_until = now() - interval '1 minute' WHERE id = %s", (user["id"],))
    db.execute(
        "UPDATE audit_log SET occurred_at = occurred_at - interval '16 minutes' WHERE entity_id = %s",
        (str(user["id"]),),
    )
    resp = client.post(f"{API}/auth/login", json={"login": "op1", "password": "wrong-password-x"})
    assert resp.status_code == 401
    row = db.execute("SELECT failed_logins FROM users WHERE id = %s", (user["id"],)).fetchone()
    assert row["failed_logins"] == 1
    assert (
        client.post(f"{API}/auth/login", json={"login": "op1", "password": user["password"]}).status_code
        == 200
    )


def test_success_resets_failure_counter(client, make_user, db, no_ip_limit) -> None:
    user = make_user("op1", "operator")
    for _ in range(9):
        client.post(f"{API}/auth/login", json={"login": "op1", "password": "wrong-password-x"})
    assert (
        client.post(f"{API}/auth/login", json={"login": "op1", "password": user["password"]}).status_code
        == 200
    )
    for _ in range(9):
        assert (
            client.post(
                f"{API}/auth/login", json={"login": "op1", "password": "wrong-password-x"}
            ).status_code
            == 401
        )
    assert (
        client.post(f"{API}/auth/login", json={"login": "op1", "password": user["password"]}).status_code
        == 200
    )


def test_ip_rate_limit_five_per_minute(client, make_user) -> None:
    user = make_user("op1", "operator")
    for _ in range(5):
        assert (
            client.post(
                f"{API}/auth/login", json={"login": "op1", "password": "wrong-password-x"}
            ).status_code
            == 401
        )
    resp = client.post(f"{API}/auth/login", json={"login": "op1", "password": user["password"]})
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "rate_limited"
    assert int(resp.headers["retry-after"]) >= 1


# ---------------------------------------------------------------- сессии


def test_idle_and_absolute_expiry(login_as, db) -> None:
    admin = login_as("admin")
    sid = db.execute("SELECT max(id) AS id FROM sessions").fetchone()["id"]
    db.execute("UPDATE sessions SET last_seen_at = now() - interval '6 days' WHERE id = %s", (sid,))
    assert admin.get("/auth/me").status_code == 200  # 6 дней простоя — ещё жива, и продлевается
    seen = db.execute("SELECT last_seen_at FROM sessions WHERE id = %s", (sid,)).fetchone()["last_seen_at"]
    assert seen > datetime.now(UTC) - timedelta(minutes=1)

    db.execute("UPDATE sessions SET last_seen_at = now() - interval '8 days' WHERE id = %s", (sid,))
    assert admin.get("/auth/me").status_code == 401

    admin2 = login_as("admin")
    sid2 = db.execute("SELECT max(id) AS id FROM sessions").fetchone()["id"]
    db.execute("UPDATE sessions SET expires_at = now() - interval '1 second' WHERE id = %s", (sid2,))
    assert admin2.get("/auth/me").status_code == 401


def test_session_expiry_is_30_days(login_as, db) -> None:
    login_as("admin")
    row = db.execute("SELECT created_at, expires_at FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
    assert row["expires_at"] - row["created_at"] == timedelta(days=30)


def test_list_and_revoke_own_sessions(login_as, db) -> None:
    first = login_as("admin", "boss")
    second = login_as("admin", "boss")
    items = first.get("/auth/sessions").json()["items"]
    assert len(items) == 2
    assert sum(1 for s in items if s["current"]) == 1
    other = next(s for s in items if not s["current"])
    assert first.delete(f"/auth/sessions/{other['id']}").status_code == 204
    assert second.get("/auth/me").status_code == 401
    assert first.get("/auth/me").status_code == 200

    stranger = login_as("operator")
    own_id = next(s["id"] for s in first.get("/auth/sessions").json()["items"] if s["current"])
    resp = stranger.delete(f"/auth/sessions/{own_id}")
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "session_not_found"

    resp = first.delete(f"/auth/sessions/{own_id}")
    assert resp.status_code == 204
    assert "as_session" not in first.client.cookies
    assert first.get("/auth/me").status_code == 401


def test_change_password(login_as) -> None:
    main = login_as("operator", "op_main")
    other = login_as("operator", "op_main")
    bad = main.post(
        "/auth/change-password",
        json={"current_password": "wrong-current", "new_password": "a-new-password-12"},
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["details"]["errors"][0]["loc"] == ["body", "current_password"]
    short = main.post(
        "/auth/change-password", json={"current_password": main.password, "new_password": "short"}
    )
    assert short.status_code == 422

    ok = main.post(
        "/auth/change-password", json={"current_password": main.password, "new_password": "a-new-password-12"}
    )
    assert ok.status_code == 204
    assert main.get("/auth/me").status_code == 200
    assert other.get("/auth/me").status_code == 401

    relogin = ApiUser(other.client, other.user, "a-new-password-12")
    relogin.login()
    assert relogin.get("/auth/me").status_code == 200


# ---------------------------------------------------------------- API-токены


def test_api_token_lifecycle(login_as, client) -> None:
    admin = login_as("admin")
    created = admin.post("/api-tokens", json={"name": "grafana", "scopes": ["read"]})
    assert created.status_code == 201, created.text
    token = created.json()["token"]
    assert token.startswith("ast_")

    listed = admin.get("/api-tokens").json()["items"]
    assert listed[0]["name"] == "grafana" and listed[0]["prefix"] == token[:12]
    assert "token" not in listed[0]

    bearer = {"Authorization": f"Bearer {token}"}
    fresh = client.__class__(client.app)
    assert fresh.get(f"{API}/system/status", headers=bearer).status_code == 200
    me = fresh.get(f"{API}/auth/me", headers=bearer).json()
    assert me["user"]["login"] == admin.user["login"] and me["csrf_token"] == ""
    # токен только читает и не даёт админских разделов
    write = fresh.post(f"{API}/budget/resume", headers=bearer)
    assert write.status_code == 403 and write.json()["error"]["code"] == "forbidden"
    assert fresh.get(f"{API}/users", headers=bearer).status_code == 403
    assert fresh.get(f"{API}/secrets", headers=bearer).status_code == 403
    assert fresh.get(f"{API}/settings", headers=bearer).status_code == 200  # operator-уровень

    token_id = created.json()["id"]
    assert admin.delete(f"/api-tokens/{token_id}").status_code == 204
    assert fresh.get(f"{API}/system/status", headers=bearer).status_code == 401
    assert admin.delete("/api-tokens/999999").status_code == 404


def test_api_token_scope_read_required(login_as, client) -> None:
    """Токен без scope `read` (только `ingest`) не читает операторский API."""
    admin = login_as("admin")
    ingest_only = admin.post("/api-tokens", json={"name": "backend", "scopes": ["ingest"]}).json()["token"]
    both = admin.post("/api-tokens", json={"name": "both", "scopes": ["read", "ingest"]}).json()["token"]
    fresh = client.__class__(client.app)
    for path in ("/system/status", "/settings", "/audit-log", "/budget", "/tasks", "/auth/me"):
        resp = fresh.get(f"{API}{path}", headers={"Authorization": f"Bearer {ingest_only}"})
        assert resp.status_code == 403, (path, resp.text)
        assert resp.json()["error"]["details"] == {"required_scope": "read"}
    assert fresh.get(f"{API}/system/status", headers={"Authorization": f"Bearer {both}"}).status_code == 200


def test_api_token_expiry_and_garbage(login_as, client, db) -> None:
    admin = login_as("admin")
    past = admin.post(
        "/api-tokens", json={"name": "old", "scopes": ["read"], "expires_at": "2020-01-01T00:00:00Z"}
    )
    assert past.status_code == 422
    created = admin.post(
        "/api-tokens", json={"name": "soon", "scopes": ["read"], "expires_at": "2099-01-01T00:00:00Z"}
    )
    token = created.json()["token"]
    db.execute(
        "UPDATE api_tokens SET expires_at = now() - interval '1 second' WHERE id = %s",
        (created.json()["id"],),
    )
    fresh = client.__class__(client.app)
    assert fresh.get(f"{API}/budget", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert fresh.get(f"{API}/budget", headers={"Authorization": "Bearer ast_garbage"}).status_code == 401


def test_basic_auth_header_from_proxy_is_ignored(login_as) -> None:
    admin = login_as("admin")
    resp = admin.get("/auth/me", headers={"Authorization": "Basic YWRtaW46cGFzcw=="})
    assert resp.status_code == 200


# ---------------------------------------------------------------- формат ошибок


def test_error_format(client) -> None:
    missing = client.get(f"{API}/no-such-endpoint")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    invalid = client.post(f"{API}/auth/login", json={"login": "x"})
    assert invalid.status_code == 422
    err = invalid.json()["error"]
    assert err["code"] == "validation_error"
    assert {"loc", "msg", "type"} <= set(err["details"]["errors"][0])
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
