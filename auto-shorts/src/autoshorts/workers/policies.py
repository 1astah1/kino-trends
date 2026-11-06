"""Классы ошибок и исходы задач (docs/ARCHITECTURE.md §6.1 «Классы ошибок»).

| Исключение | Исход |
|---|---|
| `RetryableError` и неизвестные исключения | `retry` с backoff до `max_attempts`, дальше `fail` |
| `PermanentError` | `fail` сразу |
| `ResourceDeferred` | `defer`: queued, run_after сдвинут, attempts − 1 |
| `Cancelled` / отмена оператором | `cancel` |
| остановка воркера | `requeue`: сразу в очередь без траты попытки |
| аренда потеряна (задачу забрал reaper) | `abandon`: ничего не писать |
"""

from __future__ import annotations

import random
import traceback
from dataclasses import dataclass
from typing import Any, Literal

from autoshorts.core.errors import Cancelled, PermanentError, ResourceDeferred, RetryableError
from autoshorts.core.logging import redact_text
from autoshorts.domain.enums import DeferReason
from autoshorts.services.tasks import error_class_of, retry_backoff_sec

Action = Literal["succeed", "retry", "fail", "defer", "cancel", "requeue", "abandon"]
CancelReason = Literal["user", "shutdown", "lease_lost"]

DEFAULT_DEFER_SEC = 60.0
ERROR_TAIL_CHARS = 4000


@dataclass(frozen=True)
class Outcome:
    action: Action
    delay_sec: float = 0.0
    error_class: str | None = None
    message: str | None = None
    reason: str | None = None  # defer_reason
    result: dict[str, Any] | None = None


def describe_error(exc: BaseException) -> str:
    """Текст для `tasks.last_error`: сообщение + хвост stderr подпроцесса или трейсбек.

    Секреты (`?key=…`, `Bearer …`, пароль в URL) маскируются: текст видят viewer и выше.
    """
    if isinstance(exc, (RetryableError, PermanentError, ResourceDeferred, Cancelled)):
        parts = [f"{type(exc).__name__}: {exc}"]
        stderr_tail = getattr(exc, "stderr_tail", None)
        if stderr_tail:
            parts.append(str(stderr_tail))
        text = "\n".join(parts)
    else:  # неожиданное исключение — трейсбек (его конец — сама ошибка)
        text = "".join(traceback.format_exception(exc)).rstrip()
    text = redact_text(text)
    return text if len(text) <= ERROR_TAIL_CHARS else "…" + text[-(ERROR_TAIL_CHARS - 1) :]


def normalize_defer_reason(reason: str | None) -> str:
    try:
        return DeferReason(str(reason)).value
    except ValueError:
        return DeferReason.LOAD_HIGH.value


def classify(
    exc: BaseException | None,
    task: dict[str, Any],
    *,
    cancel_reason: CancelReason | None = None,
    result: dict[str, Any] | None = None,
    rng: random.Random | None = None,
) -> Outcome:
    """Исход исполнения задачи по исключению (или его отсутствию) и причине отмены."""
    if cancel_reason == "lease_lost":
        return Outcome("abandon", message="lease lost")
    if exc is None:
        return Outcome("succeed", result=result)
    if cancel_reason == "shutdown":
        # ffmpeg убит при остановке воркера — это не вина задачи
        return Outcome("requeue", error_class="worker_shutdown", message=describe_error(exc))
    if isinstance(exc, Cancelled) or cancel_reason == "user":
        return Outcome("cancel", error_class="cancelled", message=str(exc) or "cancelled")
    if isinstance(exc, ResourceDeferred):
        delay = exc.retry_after_sec if exc.retry_after_sec and exc.retry_after_sec > 0 else DEFAULT_DEFER_SEC
        return Outcome("defer", delay_sec=float(delay), reason=normalize_defer_reason(exc.reason))
    message = describe_error(exc)
    if isinstance(exc, PermanentError):
        return Outcome("fail", error_class=error_class_of(exc), message=message)
    attempts = int(task.get("attempts") or 0)
    max_attempts = int(task.get("max_attempts") or 1)
    if attempts >= max_attempts:
        return Outcome("fail", error_class=error_class_of(exc), message=message)
    delay = getattr(exc, "retry_after_sec", None)
    if not delay or delay <= 0:
        delay = retry_backoff_sec(attempts, rng=rng)
    return Outcome("retry", delay_sec=float(delay), error_class=error_class_of(exc), message=message)
