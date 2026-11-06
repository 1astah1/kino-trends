"""Конфигурация окружения (docs/ARCHITECTURE.md §3.2, §8.3).

Только инфраструктура из env, всё остальное — настройки в БД (`domain/settings_schema.py`)
и секреты (таблица `secrets`). Имена переменных — латиница:

    APP_ENV DATABASE_URL MEDIA_ROOT MODELS_DIR BACKUP_DIR AS_SECRETS_KEY AS_SECRETS_KEY_FILE
    REDIRECT_SALT SESSION_COOKIE_SECURE ADMIN_BASE_URL PUBLIC_BASE_URL RESOURCE_PROFILE
    LOG_LEVEL LOG_FORMAT

Источники по убыванию приоритета: переменные процесса, файл `<корень проекта>/.env`, дефолты.
Модуль **ничего не создаёт на диске при импорте** (в отличие от legacy `autoshorts/config.py`):
каталоги создают те, кто в них пишет (`core/storage.py`, раннер миграций).

    from autoshorts.core.config import get_env
    env = get_env()          # кешируется; в тестах — reload_env() после monkeypatch.setenv
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from autoshorts.domain.enums import ResourceProfile

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"

DEFAULT_DATABASE_URL = "postgresql://autoshorts:autoshorts@localhost:5432/autoshorts"

# Синонимы APP_ENV приводятся к коротким значениям; «prod» — единственное боевое.
APP_ENV_ALIASES = {"production": "prod", "development": "dev", "staging": "stage", "testing": "test"}
PROD_ENV = "prod"
APP_ENV_RE = re.compile(r"[a-z][a-z0-9_-]{1,15}")

LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")


class EnvSettings(BaseSettings):
    """Переменные окружения процесса. Неизменяемы; секретные поля — `SecretStr`."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
        case_sensitive=False,
    )

    app_env: str = "dev"
    database_url: str = DEFAULT_DATABASE_URL
    media_root: Path = PROJECT_ROOT / "var" / "media"
    models_dir: Path = PROJECT_ROOT / "var" / "models"
    backup_dir: Path = PROJECT_ROOT / "var" / "backups"
    as_secrets_key: SecretStr | None = None
    as_secrets_key_file: Path | None = None
    redirect_salt: SecretStr | None = None
    session_cookie_secure: bool | None = None
    admin_base_url: str = "http://127.0.0.1:18301"
    public_base_url: str = "http://127.0.0.1:18302"
    resource_profile: ResourceProfile = ResourceProfile.SHARED_DEV
    log_level: str = "INFO"
    log_format: Literal["json", "text"] | None = None

    @field_validator("app_env", mode="before")
    @classmethod
    def _normalize_app_env(cls, value: object) -> object:
        if isinstance(value, str):
            v = value.strip().lower()
            return APP_ENV_ALIASES.get(v, v)
        return value

    @field_validator("app_env")
    @classmethod
    def _latin_app_env(cls, value: str) -> str:
        if not APP_ENV_RE.fullmatch(value):
            raise ValueError("APP_ENV: латинское слово, например dev, test, stage, prod")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        if isinstance(value, str):
            v = value.strip().upper()
            if v == "WARN":
                v = "WARNING"
            if v not in LOG_LEVELS:
                raise ValueError(f"LOG_LEVEL: одно из {', '.join(LOG_LEVELS)}")
            return v
        return value

    @field_validator("log_format", mode="before")
    @classmethod
    def _normalize_log_format(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("media_root", "models_dir", "backup_dir", "as_secrets_key_file")
    @classmethod
    def _absolute_path(cls, value: Path | None) -> Path | None:
        """Относительные пути считаются от корня проекта, а не от текущего каталога процесса."""
        if value is None:
            return None
        value = value.expanduser()
        return value if value.is_absolute() else (PROJECT_ROOT / value)

    # ------------------------------------------------------------ производные значения

    @property
    def is_prod(self) -> bool:
        return self.app_env == PROD_ENV

    @property
    def cookie_secure(self) -> bool:
        """`Secure` у cookie сессии: по умолчанию включён только в prod (§5.1)."""
        if self.session_cookie_secure is None:
            return self.is_prod
        return self.session_cookie_secure

    @property
    def effective_log_format(self) -> Literal["json", "text"]:
        if self.log_format is not None:
            return self.log_format
        return "json" if self.is_prod else "text"

    @property
    def redacted_database_url(self) -> str:
        return redact_conninfo(self.database_url)


def redact_conninfo(conninfo: str) -> str:
    """Строка подключения без пароля — для логов и вывода CLI."""
    try:
        params = conninfo_to_dict(conninfo)
    except Exception:  # noqa: BLE001 - кривая строка не должна ронять логирование
        return "<unparseable DATABASE_URL>"
    if "password" in params:
        params["password"] = "***"
    return make_conninfo(**params)


def database_name(conninfo: str) -> str | None:
    """Имя БД из строки подключения (или None, если не задано)."""
    try:
        return conninfo_to_dict(conninfo).get("dbname")
    except Exception:  # noqa: BLE001
        return None


@lru_cache(maxsize=1)
def get_env() -> EnvSettings:
    """Настройки окружения процесса (кешируются). Ошибка валидации — `pydantic.ValidationError`."""
    return EnvSettings()


def reload_env() -> EnvSettings:
    """Сбросить кеш и перечитать окружение (тесты, `check-config`)."""
    get_env.cache_clear()
    return get_env()
