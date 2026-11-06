"""Пользователи (admin): создание, правка, последний admin, отключение, сброс пароля, аудит."""

from __future__ import annotations

from tests.api.conftest import API, ApiUser


def test_create_and_list_users(login_as, db) -> None:
    admin = login_as("admin")
    resp = admin.post(
        "/users",
        json={
            "login": "anna.k",
            "display_name": "Анна",
            "role": "operator",
            "password": "anna-password-1",
            "tg_user_id": 123456,
        },
    )
    assert resp.status_code == 201, resp.text
    user = resp.json()
    assert user["login"] == "anna.k" and user["role"] == "operator" and user["tg_user_id"] == 123456
    assert user["is_active"] is True and user["locked_until"] is None
    assert "password_hash" not in user

    logins = [u["login"] for u in admin.get("/users").json()["items"]]
    assert logins == ["admin1", "anna.k"]

    audit = db.execute(
        "SELECT action, diff FROM audit_log WHERE action = 'user.create' ORDER BY id DESC"
    ).fetchone()
    assert audit["diff"]["login"] == "anna.k"
    assert "password" not in audit["diff"]

    stored = db.execute("SELECT password_hash FROM users WHERE login = 'anna.k'").fetchone()["password_hash"]
    assert stored.startswith("$argon2id$")


def test_create_validation(login_as) -> None:
    admin = login_as("admin")
    bad_login = admin.post(
        "/users",
        json={"login": "Анна", "display_name": "x", "role": "viewer", "password": "long-enough-pass"},
    )
    assert bad_login.status_code == 422
    short = admin.post(
        "/users", json={"login": "anna", "display_name": "x", "role": "viewer", "password": "short"}
    )
    assert short.status_code == 422
    admin.post(
        "/users",
        json={
            "login": "anna",
            "display_name": "x",
            "role": "viewer",
            "password": "long-enough-pass",
            "tg_user_id": 42,
        },
    )
    dup = admin.post(
        "/users",
        json={"login": "anna", "display_name": "y", "role": "viewer", "password": "long-enough-pass"},
    )
    assert dup.status_code == 422
    assert dup.json()["error"]["details"]["errors"][0]["loc"] == ["body", "login"]
    dup_tg = admin.post(
        "/users",
        json={
            "login": "boris",
            "display_name": "y",
            "role": "viewer",
            "password": "long-enough-pass",
            "tg_user_id": 42,
        },
    )
    assert dup_tg.status_code == 422
    assert dup_tg.json()["error"]["details"]["errors"][0]["loc"] == ["body", "tg_user_id"]
    extra = admin.post(
        "/users",
        json={
            "login": "vera",
            "display_name": "y",
            "role": "viewer",
            "password": "long-enough-pass",
            "is_admin": True,
        },
    )
    assert extra.status_code == 422  # неизвестные ключи тела — ошибка, а не молчание


def test_update_user_fields(login_as, db) -> None:
    admin = login_as("admin")
    op = login_as("operator")
    resp = admin.patch(f"/users/{op.user['id']}", json={"display_name": "Оператор", "tg_user_id": 777})
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Оператор" and resp.json()["tg_user_id"] == 777
    cleared = admin.patch(f"/users/{op.user['id']}", json={"tg_user_id": None})
    assert cleared.json()["tg_user_id"] is None
    null_name = admin.patch(f"/users/{op.user['id']}", json={"display_name": None})
    assert null_name.status_code == 422
    missing = admin.patch("/users/999999", json={"display_name": "x"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "user_not_found"
    diffs = db.execute("SELECT diff FROM audit_log WHERE action = 'user.update' ORDER BY id").fetchall()
    assert diffs[0]["diff"]["tg_user_id"] == {"from": None, "to": 777}


def test_last_admin_is_protected(login_as) -> None:
    admin = login_as("admin")
    me = admin.user["id"]
    demote = admin.patch(f"/users/{me}", json={"role": "operator"})
    assert demote.status_code == 409 and demote.json()["error"]["code"] == "last_admin"
    disable = admin.patch(f"/users/{me}", json={"is_active": False})
    assert disable.status_code == 409

    second = login_as("admin", "admin2")
    assert admin.patch(f"/users/{second.user['id']}", json={"role": "operator"}).status_code == 200
    # теперь admin1 снова единственный
    assert admin.patch(f"/users/{me}", json={"role": "viewer"}).status_code == 409


def test_deactivation_revokes_sessions_and_tokens(login_as, client, db) -> None:
    admin = login_as("admin")
    other_admin = login_as("admin", "admin2")
    token = other_admin.post("/api-tokens", json={"name": "t", "scopes": ["read"]}).json()["token"]
    resp = admin.patch(f"/users/{other_admin.user['id']}", json={"is_active": False})
    assert resp.status_code == 200 and resp.json()["is_active"] is False
    assert other_admin.get("/auth/me").status_code == 401
    fresh = client.__class__(client.app)
    assert fresh.get(f"{API}/budget", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    relogin = fresh.post(f"{API}/auth/login", json={"login": "admin2", "password": other_admin.password})
    assert relogin.status_code == 401


def test_reset_password_revokes_sessions(login_as) -> None:
    admin = login_as("admin")
    op = login_as("operator")
    resp = admin.post(f"/users/{op.user['id']}/reset-password", json={"new_password": "reset-password-99"})
    assert resp.status_code == 204
    assert op.get("/auth/me").status_code == 401
    ApiUser(op.client, op.user, "reset-password-99").login()
    assert (
        admin.post("/users/999999/reset-password", json={"new_password": "reset-password-99"}).status_code
        == 404
    )
