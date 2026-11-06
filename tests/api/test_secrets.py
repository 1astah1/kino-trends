"""Секреты: шифрование Fernet, значения не отдаются и не пишутся в аудит, порядок БД → env."""

from __future__ import annotations


def _by_name(resp) -> dict:
    return {s["name"]: s for s in resp.json()["items"]}


def test_list_known_and_referenced(login_as) -> None:
    admin = login_as("admin")
    items = _by_name(admin.get("/secrets"))
    assert {"FAL_API_KEY", "LLM_API_KEY", "TELEGRAM_BOT_TOKEN"} <= set(items)
    assert items["FAL_API_KEY"] == {
        "name": "FAL_API_KEY",
        "is_set": False,
        "source": None,
        "last4": None,
        "updated_at": None,
        "used_by": ["fal_flux_schnell", "fal_minimax_speech"],
    }
    assert items["LLM_API_KEY"]["used_by"] == ["openai_compat"]


def test_set_encrypts_and_never_returns_value(login_as, db) -> None:
    from autoshorts.services.secrets import get_secret

    admin = login_as("admin")
    value = "sk-very-secret-value-9f2a"
    resp = admin.put("/secrets/LLM_API_KEY", json={"value": f"  {value}\n"})
    assert resp.status_code == 204
    row = db.execute(
        "SELECT ciphertext, key_id, last4, updated_by FROM secrets WHERE name = 'LLM_API_KEY'"
    ).fetchone()
    assert value.encode() not in bytes(row["ciphertext"])
    assert row["last4"] == "9f2a" and row["updated_by"] == admin.user["id"] and len(row["key_id"]) == 8

    listed = admin.get("/secrets")
    assert value not in listed.text
    item = _by_name(listed)["LLM_API_KEY"]
    assert item["is_set"] and item["source"] == "db" and item["last4"] == "9f2a" and item["updated_at"]

    assert get_secret("LLM_API_KEY", max_age=0) == value
    audit = db.execute("SELECT summary, diff FROM audit_log WHERE action = 'secret.set'").fetchone()
    assert value not in str(audit) and audit["diff"]["created"] is True


def test_env_fallback_and_delete(login_as, monkeypatch) -> None:
    from autoshorts.services.secrets import get_secret, invalidate_cache

    admin = login_as("admin")
    monkeypatch.setenv("VK_ACCESS_TOKEN", "vk-env-token-abcd")
    invalidate_cache()
    item = _by_name(admin.get("/secrets"))["VK_ACCESS_TOKEN"]
    assert item["source"] == "env" and item["last4"] == "abcd"
    assert get_secret("VK_ACCESS_TOKEN") == "vk-env-token-abcd"

    admin.put("/secrets/VK_ACCESS_TOKEN", json={"value": "vk-db-token-wxyz"})
    assert get_secret("VK_ACCESS_TOKEN") == "vk-db-token-wxyz"  # БД важнее env, кеш сброшен
    assert admin.delete("/secrets/VK_ACCESS_TOKEN").status_code == 204
    assert get_secret("VK_ACCESS_TOKEN") == "vk-env-token-abcd"
    again = admin.delete("/secrets/VK_ACCESS_TOKEN")
    assert again.status_code == 404 and again.json()["error"]["code"] == "secret_not_found"


def test_dotenv_file_fallback(login_as, monkeypatch, tmp_path) -> None:
    from autoshorts.services import secrets

    env_file = tmp_path / ".env"
    env_file.write_text("GEMINI_API_KEY=gm-from-dotenv-1234\nEMPTY_ONE=\n")
    monkeypatch.setattr(secrets, "ENV_FILE", env_file)
    secrets.invalidate_cache()
    assert secrets.get_secret("GEMINI_API_KEY") == "gm-from-dotenv-1234"
    assert secrets.env_value("EMPTY_ONE") is None


def test_name_validation_and_empty_value(login_as) -> None:
    admin = login_as("admin")
    assert admin.put("/secrets/lower_case", json={"value": "x"}).status_code == 422
    assert admin.put("/secrets/FAL_API_KEY", json={"value": "   "}).status_code == 422
    assert admin.put("/secrets/FAL_API_KEY", json={"value": ""}).status_code == 422


def test_missing_encryption_key_is_503(login_as, monkeypatch) -> None:
    from autoshorts.core.config import reload_env

    admin = login_as("admin")
    monkeypatch.delenv("AS_SECRETS_KEY")
    reload_env()
    resp = admin.put("/secrets/FAL_API_KEY", json={"value": "some-value-123"})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "not_ready"


def test_secrets_are_admin_only(login_as) -> None:
    op = login_as("operator")
    assert op.get("/secrets").status_code == 403
    assert op.put("/secrets/FAL_API_KEY", json={"value": "x" * 10}).status_code == 403
