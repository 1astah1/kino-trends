"""Генераторы идентификаторов (docs/ARCHITECTURE.md §6.6, §4.2).

* Crockford base32 в нижнем регистре: алфавит `0-9a-z` без `i l o u`.
* Коды пакетов (атрибуция): 5 символов того же алфавита **без `0` и `1`** —
  «без `0/o/1/l/i/u`», чтобы код не путали при наборе. 30^5 ≈ 24,3 млн вариантов;
  уникальность проверяет вызывающий (`domain/tags.py`, `services/tracking.py`, M3) по БД.
* Любой код укладывается в алфавит Telegram start-параметра `^[A-Za-z0-9_-]{2,64}$`.
"""

from __future__ import annotations

import os
import re
import secrets
import socket

CROCKFORD_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
PACKAGE_CODE_ALPHABET = "23456789abcdefghjkmnpqrstvwxyz"
PACKAGE_CODE_LENGTH = 5
PACKAGE_CODE_RE = re.compile(r"[2-9a-hjkmnp-tv-z]{5}")
TELEGRAM_START_PARAM_RE = re.compile(r"[A-Za-z0-9_-]{2,64}")

_DECODE_MAP: dict[str, int] = {ch: i for i, ch in enumerate(CROCKFORD_ALPHABET)}
# Crockford: при чтении путаемые буквы приводятся к цифрам.
_DECODE_MAP.update({"o": 0, "i": 1, "l": 1})


def random_code(length: int, alphabet: str = PACKAGE_CODE_ALPHABET) -> str:
    """Криптостойкая случайная строка из алфавита."""
    if length <= 0:
        raise ValueError("length must be positive")
    return "".join(secrets.choice(alphabet) for _ in range(length))


def new_package_code(length: int = PACKAGE_CODE_LENGTH) -> str:
    """Код пакета к заливке: `k7m2q` (5 символов, нижний регистр, без 0/o/1/l/i/u)."""
    return random_code(length, PACKAGE_CODE_ALPHABET)


def is_package_code(value: str) -> bool:
    return bool(PACKAGE_CODE_RE.fullmatch(value))


def is_telegram_start_param(value: str) -> bool:
    return bool(TELEGRAM_START_PARAM_RE.fullmatch(value))


def crockford_encode(number: int, min_length: int = 1) -> str:
    """Неотрицательное целое → Crockford base32 (нижний регистр), дополнение нулями слева."""
    if number < 0:
        raise ValueError("crockford_encode: only non-negative integers")
    out: list[str] = []
    while number:
        number, rem = divmod(number, 32)
        out.append(CROCKFORD_ALPHABET[rem])
    return "".join(reversed(out)).rjust(max(min_length, 1), "0")


def crockford_decode(text: str) -> int:
    """Crockford base32 → целое. Регистр не важен, дефисы пропускаются, `o→0`, `i/l→1`."""
    cleaned = text.replace("-", "").strip().lower()
    if not cleaned:
        raise ValueError("crockford_decode: empty string")
    value = 0
    for ch in cleaned:
        try:
            value = value * 32 + _DECODE_MAP[ch]
        except KeyError:
            raise ValueError(f"crockford_decode: invalid character {ch!r}") from None
    return value


def new_request_id() -> str:
    """Идентификатор запроса для логов и `audit_log.request_id`: 16 символов Crockford (80 бит)."""
    return crockford_encode(secrets.randbits(80), 16)


def new_worker_id(resource_class: str) -> str:
    """`workers.id` в формате `<host>-<pid>-<class>` (§4.2, 0004)."""
    return f"{socket.gethostname()}-{os.getpid()}-{resource_class}"
