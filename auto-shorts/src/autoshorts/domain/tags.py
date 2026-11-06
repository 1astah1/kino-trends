"""Коды атрибуции: пакетные, алиасы площадок, суффикс лендинга (docs/ARCHITECTURE.md §5.3, §6.6).

Все коды живут в одной таблице `tracking_links.code` и подчиняются алфавиту start-параметра Telegram
`^[A-Za-z0-9_-]{2,64}$` (payload `/start` и `startapp` ≤ 64 байт): кириллица невозможна по построению,
иначе ссылка ломается молча.

* **Пакетный код** — 5 символов из `PACKAGE_CODE_ALPHABET` (Crockford base32 в нижнем регистре без
  `0/o/1/l/i/u`): его печатают в кадре и набирают руками, поэтому похожих глифов в нём нет.
* **Алиас площадки** — `yt_<video_id>` (11 символов YouTube) или `vk_<owner>_<id>` (`vk_-123_456`),
  создаётся при регистрации публикации. Алиасы чувствительны к регистру (ID YouTube — тоже).
* **Суффикс `_l`** — start-параметр бота с лендинга (`<code>_l`): `via_landing=true`, без двойного
  счёта посетителей. В коде ссылки суффикс не хранится, поэтому свои коды на `_l` не заканчиваются.

Поиск кода зрителя (`lookup_candidates`): точное совпадение, затем для кодов длины 5 — нижний регистр
и исправление опечаток (`TYPO_FIXES`: Crockford-свёртка похожих глифов, `u→v`). Алиасы `yt_`/`vk_`
ищутся только точно.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable

from autoshorts.api.schemas.tracking import (
    LANDING_SUFFIX,
    PACKAGE_CODE_ALPHABET,
    PACKAGE_CODE_LENGTH,
    TRACKING_CODE_PATTERN,
)

__all__ = [
    "ALIAS_PREFIXES",
    "LANDING_SUFFIX",
    "PACKAGE_CODE_ALPHABET",
    "PACKAGE_CODE_LENGTH",
    "RESERVED_CODES",
    "TRACKING_CODE_RE",
    "TYPO_FIXES",
    "CodeError",
    "fold_package_code",
    "generate_package_code",
    "is_alias",
    "is_package_code",
    "is_valid_code",
    "lookup_candidates",
    "manual_code_problem",
    "split_landing_suffix",
    "unique_code",
    "vk_alias",
    "with_landing_suffix",
    "youtube_alias",
]

TRACKING_CODE_RE = re.compile(TRACKING_CODE_PATTERN)
TELEGRAM_PAYLOAD_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PACKAGE_CODE_RE = re.compile(rf"^[{PACKAGE_CODE_ALPHABET}]{{{PACKAGE_CODE_LENGTH}}}$")
YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
VK_OWNER_RE = re.compile(r"^-?[1-9]\d{0,19}$")
VK_ID_RE = re.compile(r"^[1-9]\d{0,19}$")

ALIAS_PREFIXES = ("yt_", "vk_")
# Пути публичного хоста, объявленные раньше catch-all `/{code}` (§5.3): такой код никогда не откроется
RESERVED_CODES = frozenset({"health", "go", "ingest", "robots", "favicon", "static", "api"})

# Crockford base32 сворачивает o→0, i/l→1; в нашем алфавите нет ни 0/1, ни o/i/l — поэтому такие символы
# в коде зрителя ведут в «неизвестный код», а не в чужой ролик. u исключена из Crockford как
# «неприличная»; набранную вместо v (похожи в узких шрифтах) исправляем.
TYPO_FIXES = str.maketrans({"o": "0", "i": "1", "l": "1", "u": "v"})


class CodeError(ValueError):
    """Код не проходит алфавит или правила (`code` — машинная причина латиницей)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- проверки


def is_valid_code(code: str | None) -> bool:
    """Алфавит start-параметра Telegram, 2–64 символа."""
    return bool(code) and TRACKING_CODE_RE.fullmatch(code) is not None


def is_package_code(code: str | None) -> bool:
    return bool(code) and PACKAGE_CODE_RE.fullmatch(code) is not None


def is_alias(code: str | None) -> bool:
    return bool(code) and code.startswith(ALIAS_PREFIXES)


def manual_code_problem(code: str) -> str | None:
    """Почему оператор не может взять такой код (или None): алфавит, служебные пути, `_l`, алиасы."""
    if not is_valid_code(code):
        return "code_alphabet"
    if code.lower() in RESERVED_CODES:
        return "code_reserved"
    if code.endswith(LANDING_SUFFIX):
        return "code_landing_suffix"
    if is_alias(code.lower()):
        return "code_alias_prefix"
    if len(code) + len(LANDING_SUFFIX) > 64:
        return "code_too_long"
    return None


# ---------------------------------------------------------------- генерация


def generate_package_code(rng: Callable[[str], str] | None = None) -> str:
    """Случайный пакетный код: 5 символов из 30 (≈ 24 млн вариантов)."""
    choice = rng or secrets.choice
    return "".join(choice(PACKAGE_CODE_ALPHABET) for _ in range(PACKAGE_CODE_LENGTH))


def unique_code(is_taken: Callable[[str], bool], *, attempts: int = 30) -> str:
    """Пакетный код, которого ещё нет (`is_taken(code)` смотрит в tracking_links без учёта регистра)."""
    for _ in range(max(1, attempts)):
        code = generate_package_code()
        if not is_taken(code):
            return code
    raise RuntimeError("could not allocate a unique tracking code")


def youtube_alias(video_id: str) -> str:
    """`yt_dQw4w9WgXcQ`."""
    if not YOUTUBE_ID_RE.fullmatch(video_id or ""):
        raise CodeError("youtube_video_id", f"invalid YouTube video id: {video_id!r}")
    return f"yt_{video_id}"


def vk_alias(owner_id: str | int, video_id: str | int) -> str:
    """`vk_-123456_456239017` (владелец-сообщество со знаком минус) или `vk_123_456`."""
    owner, vid = str(owner_id), str(video_id)
    if not VK_OWNER_RE.fullmatch(owner) or not VK_ID_RE.fullmatch(vid):
        raise CodeError("vk_video_id", f"invalid VK owner/video id: {owner!r}_{vid!r}")
    code = f"vk_{owner}_{vid}"
    if not is_valid_code(code):
        raise CodeError("vk_video_id", "VK alias does not fit the Telegram start alphabet")
    return code


# ---------------------------------------------------------------- лендинг и поиск


def with_landing_suffix(code: str) -> str:
    """`k7m2q` → `k7m2q_l` (start-параметр кнопки лендинга). Длина payload Telegram ≤ 64."""
    payload = f"{code}{LANDING_SUFFIX}"
    if not TELEGRAM_PAYLOAD_RE.fullmatch(payload):
        raise CodeError("code_alphabet", f"code does not fit Telegram start payload: {code!r}")
    return payload


def split_landing_suffix(tag: str) -> tuple[str, bool]:
    """`k7m2q_l` → (`k7m2q`, True); `k7m2q` → (`k7m2q`, False). Пустой код не отрезается."""
    if tag.endswith(LANDING_SUFFIX) and len(tag) > len(LANDING_SUFFIX):
        return tag[: -len(LANDING_SUFFIX)], True
    return tag, False


def fold_package_code(code: str) -> str:
    """Нижний регистр и исправление опечаток для кода из 5 символов (§5.3)."""
    return code.lower().translate(TYPO_FIXES)


def lookup_candidates(code: str) -> list[str]:
    """Ключи поиска по порядку: точно, затем (только для длины 5, не алиасы) нижний регистр и
    исправленные опечатки. Невалидный код — пустой список (редиректор отвечает 404)."""
    if not is_valid_code(code):
        return []
    out = [code]
    if len(code) == PACKAGE_CODE_LENGTH and not is_alias(code.lower()):
        for variant in (code.lower(), fold_package_code(code)):
            if variant not in out and is_valid_code(variant):
                out.append(variant)
    return out
