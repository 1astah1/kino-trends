"""Криптопримитивы: пароли (argon2id), токены сессий и API, HMAC, шифрование секретов (Fernet).

docs/ARCHITECTURE.md §4.2 (`users.password_hash` argon2id, `sessions.token_hash` = sha256(token),
`api_tokens`), §5.1 (cookie `as_session`, double-submit `as_csrf`, `Authorization: Bearer ast_…`),
§8.3 (`AS_SECRETS_KEY` / `AS_SECRETS_KEY_FILE`, MultiFernet для ротации).

Бизнес-правила (блокировка после 10 неудач, лимиты) живут в `services/auth.py` (M1-B);
здесь только примитивы без обращения к БД.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from autoshorts.core.errors import ConfigError

if TYPE_CHECKING:
    from autoshorts.core.config import EnvSettings

# ---------------------------------------------------------------- пароли (argon2id)

PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 256

# Параметры argon2-cffi по умолчанию = RFC 9106 «low memory»: argon2id, t=3, m=64 МиБ, p=4.
_hasher = PasswordHasher()
_DUMMY_HASH: str | None = None


def configure_password_hasher(
    *, time_cost: int = 3, memory_cost: int = 65536, parallelism: int = 4
) -> PasswordHasher:
    """Сменить параметры argon2id (тесты ставят дешёвые). Возвращает прежний хешер."""
    global _hasher, _DUMMY_HASH
    previous = _hasher
    _hasher = PasswordHasher(time_cost=time_cost, memory_cost=memory_cost, parallelism=parallelism)
    _DUMMY_HASH = None
    return previous


def restore_password_hasher(hasher: PasswordHasher) -> None:
    global _hasher, _DUMMY_HASH
    _hasher = hasher
    _DUMMY_HASH = None


def validate_password(password: str) -> None:
    """Минимальная политика пароля. ValueError с русским текстом."""
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Пароль короче {PASSWORD_MIN_LENGTH} символов")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ValueError(f"Пароль длиннее {PASSWORD_MAX_LENGTH} символов")
    if password.strip() != password:
        raise ValueError("Пароль не должен начинаться или заканчиваться пробелом")


def hash_password(password: str) -> str:
    """argon2id-хеш в PHC-формате (`$argon2id$v=19$m=…`)."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """True, если пароль подходит. Кривой хеш — False, без исключения."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """Параметры хеша устарели — после успешного входа стоит перехешировать."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def dummy_verify(password: str) -> None:
    """Проверка против фиктивного хеша: вход с несуществующим логином тратит столько же времени."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))
    verify_password(_DUMMY_HASH, password)


# ---------------------------------------------------------------- токены

SESSION_TOKEN_BYTES = 32
CSRF_TOKEN_BYTES = 24
API_TOKEN_PREFIX = "ast_"
API_TOKEN_DISPLAY_LEN = 12  # «ast_» + 8 символов — видно в списке токенов
API_TOKEN_RE = re.compile(r"ast_[A-Za-z0-9_-]{32,64}")


def new_token(nbytes: int = SESSION_TOKEN_BYTES) -> str:
    """Случайный URL-safe токен (base64url без `=`)."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> bytes:
    """sha256(token) — так токены сессий и API хранятся в БД (BYTEA)."""
    return hashlib.sha256(token.encode("utf-8")).digest()


def new_session_token() -> tuple[str, bytes]:
    """(значение для cookie `as_session`, token_hash для `sessions`)."""
    token = new_token(SESSION_TOKEN_BYTES)
    return token, hash_token(token)


def new_csrf_token() -> str:
    """Значение cookie `as_csrf` и заголовка `X-CSRF-Token` (double-submit)."""
    return new_token(CSRF_TOKEN_BYTES)


@dataclass(frozen=True)
class IssuedApiToken:
    token: str  # показывается пользователю один раз
    prefix: str  # хранится открыто, `api_tokens.prefix`
    token_hash: bytes  # `api_tokens.token_hash`


def new_api_token() -> IssuedApiToken:
    """Машинный токен `ast_<43 символа>` для `Authorization: Bearer`."""
    token = API_TOKEN_PREFIX + new_token(32)
    return IssuedApiToken(token=token, prefix=token[:API_TOKEN_DISPLAY_LEN], token_hash=hash_token(token))


def is_api_token(value: str) -> bool:
    return bool(API_TOKEN_RE.fullmatch(value))


def constant_time_equals(a: str | bytes | None, b: str | bytes | None) -> bool:
    """Сравнение без утечки по времени (CSRF, подписи). None никогда не равен ничему."""
    if a is None or b is None:
        return False
    if isinstance(a, str):
        a = a.encode("utf-8")
    if isinstance(b, str):
        b = b.encode("utf-8")
    return hmac.compare_digest(a, b)


# ---------------------------------------------------------------- HMAC и обезличивание


def _b(value: str | bytes) -> bytes:
    return value.encode("utf-8") if isinstance(value, str) else value


def hmac_sha256(key: str | bytes, message: str | bytes) -> bytes:
    return hmac.new(_b(key), _b(message), hashlib.sha256).digest()


def hmac_sha256_hex(key: str | bytes, message: str | bytes) -> str:
    return hmac.new(_b(key), _b(message), hashlib.sha256).hexdigest()


def ip_hash(ip: str, salt: str | bytes) -> str:
    """Обезличенный IP для `sessions.ip_hash`, `audit_log.ip_hash`: hmac(salt, ip)[:32]."""
    return hmac_sha256_hex(salt, ip)[:32]


def visitor_key(salt: str | bytes, day: date | str, ip: str, user_agent: str) -> str:
    """`clicks.visitor_key` = hmac(REDIRECT_SALT, day|ip|ua)[:32] — сырой IP не храним (§4.2, 0006)."""
    day_s = day.isoformat() if isinstance(day, date) else day
    return hmac_sha256_hex(salt, f"{day_s}|{ip}|{user_agent}")[:32]


# ---------------------------------------------------------------- шифрование секретов (Fernet)


def generate_fernet_key() -> str:
    """Новый ключ для `AS_SECRETS_KEY` (base64url, 32 байта)."""
    return Fernet.generate_key().decode("ascii")


def parse_fernet_keys(raw: str) -> list[bytes]:
    """Ключи через запятую, пробел или перевод строки; первый — основной (шифрует), остальные — для чтения.

    Строки, начинающиеся с `#`, пропускаются (удобно для файла `AS_SECRETS_KEY_FILE`).
    """
    lines = [ln for ln in raw.splitlines() if not ln.strip().startswith("#")]
    keys = [k.encode("ascii") for k in re.split(r"[\s,]+", "\n".join(lines)) if k]
    for i, key in enumerate(keys):
        try:
            Fernet(key)
        except (ValueError, TypeError) as exc:
            raise ConfigError(f"AS_SECRETS_KEY: ключ №{i + 1} не является ключом Fernet") from exc
    return keys


def load_secret_keys(env: EnvSettings | None = None) -> list[bytes]:
    """Ключи из `AS_SECRETS_KEY` или файла `AS_SECRETS_KEY_FILE`. Нет ключей — ConfigError."""
    if env is None:
        from autoshorts.core.config import get_env

        env = get_env()
    raw = env.as_secrets_key.get_secret_value() if env.as_secrets_key else ""
    if not raw and env.as_secrets_key_file:
        path = Path(env.as_secrets_key_file)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"AS_SECRETS_KEY_FILE не читается: {path}") from exc
    keys = parse_fernet_keys(raw) if raw else []
    if not keys:
        raise ConfigError("Не задан ключ шифрования секретов: AS_SECRETS_KEY или AS_SECRETS_KEY_FILE")
    return keys


def secret_key_id(key: bytes) -> str:
    """Короткий отпечаток ключа для `secrets.key_id` (видно, каким ключом зашифровано)."""
    return hashlib.sha256(key).hexdigest()[:8]


class SecretCipher:
    """Шифрование значений таблицы `secrets`. Первый ключ шифрует, все — расшифровывают."""

    def __init__(self, keys: list[bytes]) -> None:
        if not keys:
            raise ConfigError("SecretCipher: пустой список ключей")
        self._keys = list(keys)
        self._multi = MultiFernet([Fernet(k) for k in self._keys])
        self.primary_key_id = secret_key_id(self._keys[0])

    @classmethod
    def from_env(cls, env: EnvSettings | None = None) -> SecretCipher:
        return cls(load_secret_keys(env))

    def encrypt(self, plaintext: str) -> tuple[bytes, str]:
        """(ciphertext, key_id)."""
        return self._multi.encrypt(plaintext.encode("utf-8")), self.primary_key_id

    def decrypt(self, ciphertext: bytes) -> str:
        """Расшифровать; чужой/испорченный шифртекст — ConfigError (ключ потерян или подменён)."""
        try:
            return self._multi.decrypt(bytes(ciphertext)).decode("utf-8")
        except InvalidToken as exc:
            raise ConfigError("Секрет не расшифровывается текущими ключами AS_SECRETS_KEY") from exc

    def rotate(self, ciphertext: bytes) -> tuple[bytes, str]:
        """Перешифровать основным ключом (после добавления нового ключа первым в списке)."""
        try:
            return self._multi.rotate(bytes(ciphertext)), self.primary_key_id
        except InvalidToken as exc:
            raise ConfigError("Секрет не расшифровывается текущими ключами AS_SECRETS_KEY") from exc


def last4(value: str | None) -> str | None:
    """Последние 4 символа для показа в UI (`secrets.last4`); короткие значения не раскрываем."""
    if not value or len(value) < 8:
        return None
    return value[-4:]
