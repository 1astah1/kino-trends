"""Логирование: JSON в prod, текст в dev, вырезание секретов (docs/ARCHITECTURE.md §8.3).

«Логгер вырезает `*key*`, `*token*`, `*secret*`, `password`»: фильтр на обработчике маскирует

* поля `extra=` и ключи словарей-аргументов с такими именами (рекурсивно);
* в тексте сообщения — `NAME_KEY=…`, `password: …`, `Bearer …`, `ast_…`, пароль в URL БД.

Контекст (request_id, job_id, task_id, worker_id) кладётся в contextvar через `log_context(...)`
и попадает в каждую запись — удобно для middleware API и исполнителя задач.

    from autoshorts.core.logging import configure_logging, get_logger, log_context
    configure_logging()                   # один раз при старте процесса
    log = get_logger(__name__)
    with log_context(task_id=42):
        log.info("старт", extra={"stage": "render"})
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import re
import sys
from collections.abc import Iterator, Mapping, MutableMapping
from typing import Any, Literal, TextIO

REDACTED = "***"

# Имена полей, чьи значения нельзя писать в лог (§8.3). Проверка по подстроке, без учёта регистра.
SENSITIVE_NAME_RE = re.compile(r"key|token|secret|passw|authorization|cookie|credential", re.IGNORECASE)
# Имена, которые попадают под шаблон, но секретов не содержат.
SAFE_NAMES = frozenset(
    {
        "dedup_key",
        "idempotency_key_present",
        "key_id",
        "setting_key",
        "provider_key",
        "cache_key",
        "sort_key",
        "token_prefix",
        "prefix",
        "keys_count",
        "max_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    }
)

_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # postgresql://user:password@host → пароль
    (re.compile(r"(?P<head>\b[a-z][a-z0-9+.-]*://[^:/@\s]+:)(?P<secret>[^@\s]+)(?P<tail>@)", re.I), "url"),
    # Authorization: Bearer xxx / Bearer xxx
    (re.compile(r"(?P<head>\bBearer\s+)(?P<secret>[A-Za-z0-9._~+/=-]+)", re.I), "bearer"),
    # машинные токены API
    (re.compile(r"(?P<head>)(?P<secret>\bast_[A-Za-z0-9_-]{8,})"), "api_token"),
    # FAL_API_KEY=..., "password": "...", token: ...
    (
        re.compile(
            r"(?P<head>[\"']?\b[A-Za-z0-9_]*(?:key|token|secret|passw(?:or)?d?)[A-Za-z0-9_]*\b[\"']?\s*[=:]\s*[\"']?)"
            r"(?P<secret>[^\s\"',;&}]+)",
            re.I,
        ),
        "assignment",
    ),
)

_STANDARD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
    | {"message", "asctime", "taskName", "color_message"}
)

_context: contextvars.ContextVar[Mapping[str, Any] | None] = contextvars.ContextVar(
    "autoshorts_log_context", default=None
)


def is_sensitive_name(name: str) -> bool:
    return name not in SAFE_NAMES and bool(SENSITIVE_NAME_RE.search(name))


def redact_text(text: str) -> str:
    """Замаскировать похожие на секреты фрагменты в произвольной строке."""
    for pattern, _kind in _TEXT_PATTERNS:
        text = pattern.sub(lambda m: f"{m.group('head')}{REDACTED}{m.groupdict().get('tail') or ''}", text)
    return text


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Копия структуры, где значения под «секретными» ключами заменены на `***`.

    Годится и для логов, и для `audit_log.diff` («значения секретов вырезаются до записи», §4.2).
    """
    if _depth > 8:
        return value
    if isinstance(value, Mapping):
        return {
            k: (
                REDACTED
                if isinstance(k, str) and is_sensitive_name(k) and v is not None
                else redact(v, _depth=_depth + 1)
            )
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        items = [redact(v, _depth=_depth + 1) for v in value]
        return type(value)(items) if isinstance(value, tuple) else items
    if isinstance(value, str):
        return redact_text(value)
    return value


class RedactingFilter(logging.Filter):
    """Маскирует секреты в записи. Ставится на обработчик, поэтому видит записи всех логгеров."""

    def filter(self, record: logging.LogRecord) -> bool:
        for name in list(record.__dict__):
            if name in _STANDARD_ATTRS:
                continue
            if is_sensitive_name(name):
                if record.__dict__[name] is not None:
                    record.__dict__[name] = REDACTED
            else:
                record.__dict__[name] = redact(record.__dict__[name])
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if record.args:
            if isinstance(record.args, Mapping):
                record.args = redact(dict(record.args))
            elif isinstance(record.args, tuple):
                record.args = tuple(redact(a) for a in record.args)
        return True


class ContextFilter(logging.Filter):
    """Добавляет в запись поля из `log_context(...)` (не перетирая явные `extra=`)."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, val in (_context.get() or {}).items():
            if key not in record.__dict__:
                record.__dict__[key] = val
        return True


@contextlib.contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Временно добавить поля ко всем записям текущего потока/таски."""
    token = _context.set({**(_context.get() or {}), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def bind_context(**fields: Any) -> contextvars.Token[Mapping[str, Any] | None]:
    """Добавить поля без блока `with`; вернуть токен для `reset_context`."""
    return _context.set({**(_context.get() or {}), **fields})


def reset_context(token: contextvars.Token[Mapping[str, Any] | None]) -> None:
    _context.reset(token)


def current_context() -> dict[str, Any]:
    return dict(_context.get() or {})


class BoundLogger(logging.LoggerAdapter):
    """LoggerAdapter, который **сливает** свои поля с `extra=` вызова (а не заменяет их)."""

    def process(self, msg: Any, kwargs: MutableMapping[str, Any]) -> tuple[Any, MutableMapping[str, Any]]:
        kwargs["extra"] = {**(self.extra or {}), **(kwargs.get("extra") or {})}
        return msg, kwargs


def get_logger(name: str, **fields: Any) -> logging.Logger | BoundLogger:
    """Логгер модуля; с полями — `BoundLogger` (так строится `StageContext.log`, §5.5)."""
    logger = logging.getLogger(name)
    return BoundLogger(logger, fields) if fields else logger


class _TextFormatter(logging.Formatter):
    """Текст для dev: `время уровень логгер: сообщение [k=v …]`."""

    def formatException(self, ei: Any) -> str:  # noqa: N802 - имя из logging.Formatter
        return redact_text(super().formatException(ei))

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS}
        if extras:
            tail = " ".join(f"{k}={v}" for k, v in sorted(extras.items()))
            first, sep, rest = base.partition("\n")
            return f"{first} [{tail}]{sep}{rest}"
        return base


def _json_formatter() -> logging.Formatter:
    from pythonjsonlogger.json import JsonFormatter

    class _RedactingJsonFormatter(JsonFormatter):
        def formatException(self, ei: Any) -> Any:  # noqa: N802
            return redact(super().formatException(ei))

    return _RedactingJsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "ts", "levelname": "level", "name": "logger"},
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        json_ensure_ascii=False,
    )


_NOISY_LOGGERS = {"psycopg.pool": "WARNING", "httpx": "WARNING", "httpcore": "WARNING", "asyncio": "WARNING"}


def configure_logging(
    level: str | int | None = None,
    fmt: Literal["json", "text"] | None = None,
    *,
    stream: TextIO | None = None,
) -> logging.Handler:
    """Настроить корневой логгер один раз при старте процесса.

    Без аргументов берёт `LOG_LEVEL`/`LOG_FORMAT` из `core.config.get_env()` (json в prod, text иначе).
    Повторный вызов заменяет обработчик, установленный этой функцией, а чужие не трогает.
    """
    if level is None or fmt is None:
        try:
            from autoshorts.core.config import get_env

            env = get_env()
            level = level if level is not None else env.log_level
            fmt = fmt if fmt is not None else env.effective_log_format
        except Exception:  # noqa: BLE001 - кривой env не должен лишать процесс логов
            level = level if level is not None else "INFO"
            fmt = fmt if fmt is not None else "text"

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.set_name("autoshorts")
    if fmt == "json":
        handler.setFormatter(_json_formatter())
    else:
        handler.setFormatter(_TextFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handler.addFilter(ContextFilter())
    handler.addFilter(RedactingFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        if existing.get_name() == "autoshorts":
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level if isinstance(level, int) else str(level).upper())
    for name, lvl in _NOISY_LOGGERS.items():
        logging.getLogger(name).setLevel(lvl)
    return handler
