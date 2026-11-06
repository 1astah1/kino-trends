"""CLI `autoshorts-admin`: автообнаружение подкоманд, create-user, check-config."""

from __future__ import annotations

import io
import json
import sys
import types

import pytest

from autoshorts import cli
from autoshorts.core.security import verify_password
from autoshorts.db.migrate import latest_version

PASSWORD = "correct horse battery"


def _run(argv: list[str], monkeypatch: pytest.MonkeyPatch, stdin: str = "") -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    return cli.main(argv)


# ---------------------------------------------------------------- обнаружение подкоманд


def test_commands_discovered() -> None:
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")  # noqa: SLF001
    assert {"migrate", "create-user", "check-config"} <= set(sub.choices)


def test_broken_command_module_does_not_break_cli(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    real_import = cli.importlib.import_module
    fake_infos = [types.SimpleNamespace(name="db"), types.SimpleNamespace(name="zz_broken")]
    monkeypatch.setattr(cli.pkgutil, "iter_modules", lambda path: fake_infos)

    def fake_import(name: str):
        if name.endswith("zz_broken"):
            raise ImportError("нет опциональной зависимости")
        return real_import(name)

    monkeypatch.setattr(cli.importlib, "import_module", fake_import)
    modules, broken = cli.discover_command_modules()
    assert [m.__name__ for m in modules] == ["autoshorts.cli.db"]
    assert broken and broken[0][0] == "autoshorts.cli.zz_broken"
    assert cli.main(["check-config", "--no-db", "--json"]) in (0, 1)
    assert "zz_broken" in capsys.readouterr().err


def test_no_command_prints_help(capsys) -> None:
    assert cli.main([]) == 2
    assert "migrate" in capsys.readouterr().out


# ---------------------------------------------------------------- create-user


def test_create_user(app_env, db_conn, fast_password_hasher, monkeypatch, capsys) -> None:
    rc = _run(
        [
            "create-user",
            "--login",
            "owner",
            "--role",
            "admin",
            "--display-name",
            "Владелец",
            "--password-stdin",
        ],
        monkeypatch,
        stdin=PASSWORD + "\n",
    )
    assert rc == 0, capsys.readouterr().err
    user = db_conn.execute("SELECT * FROM users WHERE login = 'owner'").fetchone()
    assert user["role"] == "admin" and user["display_name"] == "Владелец" and user["is_active"]
    assert user["password_hash"].startswith("$argon2id$")
    assert verify_password(user["password_hash"], PASSWORD)
    audit = db_conn.execute("SELECT actor_kind, action, entity_type, entity_id FROM audit_log").fetchall()
    assert audit == [
        {"actor_kind": "system", "action": "user.create", "entity_type": "user", "entity_id": str(user["id"])}
    ]


def test_create_user_duplicate_and_if_not_exists(
    app_env, db_conn, fast_password_hasher, monkeypatch, capsys
) -> None:
    args = ["create-user", "--login", "op1", "--role", "operator", "--password-stdin"]
    assert _run(args, monkeypatch, stdin=PASSWORD + "\n") == 0
    assert _run(args, monkeypatch, stdin=PASSWORD + "\n") == 1
    assert "уже существует" in capsys.readouterr().err
    assert _run([*args, "--if-not-exists"], monkeypatch, stdin="") == 0
    assert db_conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == 1


def test_reset_password_unlocks_and_revokes_sessions(
    app_env, db_conn, fast_password_hasher, monkeypatch
) -> None:
    assert (
        _run(["create-user", "--login", "op2", "--role", "viewer", "--password-stdin"], monkeypatch, PASSWORD)
        == 0
    )
    uid = db_conn.execute("SELECT id FROM users WHERE login = 'op2'").fetchone()["id"]
    db_conn.execute(
        "UPDATE users SET failed_logins = 10, locked_until = now() + interval '1 hour' WHERE id = %s", (uid,)
    )
    db_conn.execute(
        "INSERT INTO sessions (token_hash, csrf_token, user_id, expires_at)"
        " VALUES ('\\x01', 'c', %s, now() + interval '1 day')",
        (uid,),
    )
    new_password = "another long password"
    rc = _run(
        ["create-user", "--login", "op2", "--role", "viewer", "--reset-password", "--password-stdin"],
        monkeypatch,
        new_password + "\n",
    )
    assert rc == 0
    user = db_conn.execute("SELECT * FROM users WHERE id = %s", (uid,)).fetchone()
    assert verify_password(user["password_hash"], new_password) and not verify_password(
        user["password_hash"], PASSWORD
    )
    assert user["failed_logins"] == 0 and user["locked_until"] is None
    assert db_conn.execute("SELECT count(*) AS n FROM sessions WHERE revoked_at IS NULL").fetchone()["n"] == 0
    actions = [r["action"] for r in db_conn.execute("SELECT action FROM audit_log ORDER BY id").fetchall()]
    assert actions == ["user.create", "user.password_reset"]


@pytest.mark.parametrize(
    ("argv", "stdin", "code", "message"),
    [
        (["--login", "Админ", "--role", "admin"], PASSWORD, 2, "логин"),
        (["--login", "ab", "--role", "admin"], PASSWORD, 2, "логин"),
        (["--login", "shorty", "--role", "admin"], "short", 1, "короче"),
    ],
)
def test_create_user_validation(app_env, monkeypatch, capsys, argv, stdin, code, message) -> None:
    assert _run(["create-user", *argv, "--password-stdin"], monkeypatch, stdin) == code
    assert message in capsys.readouterr().err


def test_create_user_without_tty_requires_password_stdin(app_env, monkeypatch, capsys) -> None:
    assert _run(["create-user", "--login", "notty", "--role", "viewer"], monkeypatch, "") == 1
    assert "--password-stdin" in capsys.readouterr().err


def test_create_user_before_migrations(make_database, env_settings, monkeypatch, capsys) -> None:
    url = make_database("cli_nousers")
    rc = _run(
        ["create-user", "--login", "early", "--role", "admin", "--password-stdin", "--database-url", url],
        monkeypatch,
        PASSWORD,
    )
    assert rc == 1 and "migrate" in capsys.readouterr().err


# ---------------------------------------------------------------- check-config


def _checks(capsys) -> dict[str, dict[str, str]]:
    payload = json.loads(capsys.readouterr().out)
    return {c["code"]: c for c in payload["checks"]} | {"_ok": payload["ok"]}


def test_check_config_dev_ok(app_env, monkeypatch, capsys) -> None:
    assert _run(["check-config", "--json"], monkeypatch) == 0
    checks = _checks(capsys)
    assert checks["_ok"] is True
    assert checks["database"]["status"] == "ok"
    assert checks["migrations"]["status"] == "ok"
    assert f"версия {latest_version()}" in checks["migrations"]["message"]
    assert checks["secrets_key"]["status"] == "ok"
    assert checks["media_root"]["status"] == "warn"  # каталога в tmp ещё нет — в dev это не ошибка
    assert "password=autoshorts" not in checks["database"]["message"]


def test_check_config_prod_requires_secrets_and_secure_cookie(env_settings, monkeypatch, capsys) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("AS_SECRETS_KEY")
    monkeypatch.delenv("REDIRECT_SALT")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    assert _run(["check-config", "--json", "--no-db"], monkeypatch) == 1
    checks = _checks(capsys)
    assert checks["app_env"]["message"].startswith("APP_ENV=prod")
    for code in ("secrets_key", "redirect_salt", "session_cookie_secure", "media_root", "backup_dir"):
        assert checks[code]["status"] == "error", code


def test_check_config_reports_bad_env(env_settings, monkeypatch, capsys) -> None:
    monkeypatch.setenv("RESOURCE_PROFILE", "huge")
    assert _run(["check-config", "--no-db"], monkeypatch) == 1
    out = capsys.readouterr().out
    assert "[error] env" in out and "RESOURCE_PROFILE" in out


def test_check_config_unreachable_db(env_settings, monkeypatch, capsys) -> None:
    rc = _run(
        ["check-config", "--json", "--database-url", "postgresql://autoshorts:secretpw@127.0.0.1:1/none"],
        monkeypatch,
    )
    assert rc == 1
    raw = capsys.readouterr().out
    assert "secretpw" not in raw
    assert json.loads(raw)["checks"][1]["code"] == "database"
