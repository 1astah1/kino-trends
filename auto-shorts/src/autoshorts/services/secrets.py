"""Секреты (docs/ARCHITECTURE.md §8.3): таблица `secrets` (Fernet), затем env с тем же именем.

    from autoshorts.services.secrets import get_secret
    key = get_secret("FAL_API_KEY")        # str | None; кеш в процессе 60 с

Порядок чтения: БД → переменная процесса → файл `.env` проекта (текущий `.env` продолжает
работать) → None. API значения не отдаёт никогда; в аудит пишется только факт записи.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from autoshorts.core.config import ENV_FILE as PROJECT_ENV_FILE
from autoshorts.core.errors import ConfigError, NotFound, NotReady, ValidationFailed
from autoshorts.core.security import SecretCipher, last4
from autoshorts.repos import secrets as secrets_repo
from autoshorts.services import audit
from autoshorts.services.audit import Actor

log = logging.getLogger(__name__)

# Имена из §8.3 без шаблонов (<CLUSTER>, <PRODUCT>, <CHANNEL> появляются по ссылкам из таблиц).
KNOWN_SECRETS: tuple[str, ...] = (
    "ASR_CLOUD_API_KEY",
    "FAL_API_KEY",
    "GEMINI_API_KEY",
    "GIGACHAT_AUTH_KEY",
    "LLM_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "VK_ACCESS_TOKEN",
    "YANDEX_SPEECHKIT_API_KEY",
)

CACHE_TTL_SEC = 60.0
ENV_FILE: Path | None = PROJECT_ENV_FILE  # тесты подменяют, чтобы не читать настоящий .env

_lock = threading.Lock()
_cache: dict[str, tuple[float, str | None]] = {}
_dotenv_cache: tuple[Path | None, float, dict[str, str]] | None = None


def invalidate_cache(name: str | None = None) -> None:
    with _lock:
        if name is None:
            _cache.clear()
        else:
            _cache.pop(name, None)


def _dotenv_values() -> dict[str, str]:
    global _dotenv_cache
    path = ENV_FILE
    if path is None or not path.is_file():
        return {}
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    cached = _dotenv_cache
    if cached is not None and cached[0] == path and cached[1] == mtime:
        return cached[2]
    from dotenv import dotenv_values

    values = {k: v for k, v in dotenv_values(path).items() if v}
    _dotenv_cache = (path, mtime, values)
    return values


def env_value(name: str) -> str | None:
    """Значение из окружения процесса или `.env` проекта (пустое = нет)."""
    value = os.environ.get(name)
    if value:
        return value
    return _dotenv_values().get(name) or None


def _cipher() -> SecretCipher:
    try:
        return SecretCipher.from_env()
    except ConfigError as exc:
        raise NotReady(
            "Не задан ключ шифрования секретов: AS_SECRETS_KEY или AS_SECRETS_KEY_FILE",
            details={"reason": "secrets_key_missing"},
        ) from exc


def _read_db(conn: Any, name: str) -> str | None:
    row = secrets_repo.get_ciphertext(conn, name)
    if row is None:
        return None
    try:
        return SecretCipher.from_env().decrypt(bytes(row["ciphertext"]))
    except ConfigError as exc:
        log.error("secret %s cannot be decrypted (key_id=%s): %s", name, row["key_id"], exc)
        return None


def get_secret(name: str, *, conn: Any | None = None, max_age: float = CACHE_TTL_SEC) -> str | None:
    """Значение секрета: БД, затем env, затем None. Кеш 60 с."""
    now = time.monotonic()
    cached = _cache.get(name)
    if cached is not None and now - cached[0] < max_age:
        return cached[1]
    if conn is not None:
        value = _read_db(conn, name)
    else:
        from autoshorts.db.pool import connection

        with connection() as own:
            value = _read_db(own, name)
    if value is None:
        value = env_value(name)
    with _lock:
        _cache[name] = (now, value)
    return value


def status_map(conn: Any) -> dict[str, dict[str, Any]]:
    """Имя → {source, last4, updated_at} без расшифровки (для списков секретов и провайдеров)."""
    out: dict[str, dict[str, Any]] = {}
    for row in secrets_repo.list_rows(conn):
        out[row["name"]] = {"source": "db", "last4": row["last4"], "updated_at": row["updated_at"]}
    return out


def is_set(name: str | None, db_status: dict[str, dict[str, Any]]) -> bool:
    if not name:
        return False
    return name in db_status or env_value(name) is not None


# ---------------------------------------------------------------- API


def list_secrets() -> list[dict[str, Any]]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        db_status = status_map(conn)
        refs: dict[str, list[str]] = {}
        for row in secrets_repo.references(conn):
            refs.setdefault(row["secret_name"], [])
            if row["ref"] not in refs[row["secret_name"]]:
                refs[row["secret_name"]].append(row["ref"])
    names = sorted(set(KNOWN_SECRETS) | set(refs) | set(db_status))
    items = []
    for name in names:
        db = db_status.get(name)
        env = None if db is not None else env_value(name)
        items.append(
            {
                "name": name,
                "is_set": db is not None or env is not None,
                "source": "db" if db is not None else ("env" if env is not None else None),
                "last4": db["last4"] if db is not None else last4(env),
                "updated_at": db["updated_at"] if db is not None else None,
                "used_by": refs.get(name, []),
            }
        )
    return items


def set_secret(name: str, value: str, actor: Actor) -> None:
    from autoshorts.db.pool import connection

    value = value.strip()
    if not value:
        raise ValidationFailed(
            "Пустое значение секрета",
            details={"errors": [{"loc": ["body", "value"], "msg": "Пустое значение", "type": "value_error"}]},
        )
    ciphertext, key_id = _cipher().encrypt(value)
    with connection() as conn:
        created = secrets_repo.upsert(
            conn,
            name=name,
            ciphertext=ciphertext,
            key_id=key_id,
            last4=last4(value),
            updated_by=actor.user_id,
        )
        audit.record(
            conn,
            actor,
            "secret.set",
            "secret",
            name,
            summary=f"секрет {name} {'записан' if created else 'заменён'} (значение не логируется)",
            diff={"created": created, "key_id": key_id},
        )
    invalidate_cache(name)


def delete_secret(name: str, actor: Actor) -> None:
    from autoshorts.db.pool import connection

    with connection() as conn:
        if not secrets_repo.delete(conn, name):
            raise NotFound("secret", f"Секрет {name} не хранится в БД")
        audit.record(conn, actor, "secret.delete", "secret", name, summary=f"секрет {name} удалён из БД")
    invalidate_cache(name)
