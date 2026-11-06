"""Исключения приложения (docs/ARCHITECTURE.md §5.1 «Ошибки», §5.5).

Две семьи:

* `AppError` и наследники — ошибки, которые API превращает в ответ
  `{"error": {"code", "message", "details"}}` с HTTP-статусом из `status`. Код — латиница
  snake_case из справочника `api.schemas.common.ERROR_CODES`, сообщение — по-русски.
* `TaskError` и наследники — сигналы исполнителю очереди (§5.5): `RetryableError`,
  `PermanentError`, `ResourceDeferred` (не тратит попытку), `Cancelled`.

Модуль не зависит от FastAPI: обработчик в `api/app.py` вызывает `AppError.to_payload()`.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Базовая ошибка с HTTP-статусом и машинным кодом."""

    status: int = 500
    code: str = "internal_error"
    default_message: str = "Внутренняя ошибка"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status
        self.details: dict[str, Any] = dict(details or {})
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        """Тело ответа в едином формате §5.1."""
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}

    def __repr__(self) -> str:
        return f"{type(self).__name__}(status={self.status}, code={self.code!r}, message={self.message!r})"


class BadRequest(AppError):
    status = 400
    code = "bad_request"
    default_message = "Некорректный запрос"


class NotAuthenticated(AppError):
    status = 401
    code = "not_authenticated"
    default_message = "Нужен вход"


class Forbidden(AppError):
    status = 403
    code = "forbidden"
    default_message = "Недостаточно прав"


class CsrfFailed(Forbidden):
    code = "csrf_failed"
    default_message = "Не совпал CSRF-токен"


class NotFound(AppError):
    """404 с кодом `<entity>_not_found` (`NotFound("job")` → `job_not_found`)."""

    status = 404
    code = "not_found"
    default_message = "Не найдено"

    def __init__(
        self,
        entity: str = "",
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.entity = entity
        super().__init__(
            message,
            code=f"{entity}_not_found" if entity else None,
            details=details,
        )


class Conflict(AppError):
    status = 409
    code = "conflict"
    default_message = "Конфликт состояния"


class JobStateConflict(Conflict):
    code = "job_state_conflict"
    default_message = "Ролик в неподходящем статусе для этого действия"


class VersionConflict(Conflict):
    code = "version_conflict"
    default_message = "Запись изменилась, обновите страницу"


class TrendsNotConfigured(Conflict):
    """Мост к Kino-trends не настроен: пустая настройка `kino_trends_url`."""

    code = "trends_not_configured"
    default_message = "Kino-trends не настроен: укажите kino_trends_url в настройках"


class TrendsUnreachable(AppError):
    """Kino-trends не ответил или ответил не JSON/5xx."""

    status = 502
    code = "trends_unreachable"
    default_message = "Kino-trends недоступен"


class PayloadTooLarge(AppError):
    status = 413
    code = "payload_too_large"
    default_message = "Слишком большой запрос"


class UnsupportedMediaType(AppError):
    status = 415
    code = "unsupported_media_type"
    default_message = "Неподдерживаемый тип содержимого"


class ValidationFailed(AppError):
    """422. Для ошибок полей — `details={"errors": [{loc, msg, type}]}`."""

    status = 422
    code = "validation_error"
    default_message = "Ошибка проверки данных"


class BudgetPaused(AppError):
    status = 423
    code = "budget_paused"
    default_message = "Бюджет на сегодня исчерпан, платные вызовы на паузе"


class RateLimited(AppError):
    status = 429
    code = "rate_limited"
    default_message = "Слишком много запросов, попробуйте позже"


class NotReady(AppError):
    status = 503
    code = "not_ready"
    default_message = "Сервис ещё не готов"


class MigrationsPending(NotReady):
    """Схема БД отстаёт от файлов миграций: процесс не стартует, /health/ready → 503 (§4.1)."""

    code = "migrations_pending"
    default_message = "Не применены миграции БД"


class InsufficientStorage(AppError):
    status = 507
    code = "insufficient_storage"
    default_message = "Недостаточно места на диске"


class ConfigError(RuntimeError):
    """Окружение (env) неполно или противоречиво. Процесс не должен стартовать."""


# ---------------------------------------------------------------- исполнение задач (§5.5, §6.1)


class TaskError(Exception):
    """База сигналов обработчика задачи."""


class RetryableError(TaskError):
    """Временный сбой: задача вернётся в очередь, попытка засчитывается."""

    def __init__(self, message: str = "", *, retry_after_sec: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_sec = retry_after_sec


class PermanentError(TaskError):
    """Повтор бессмыслен: задача сразу падает в failed."""

    def __init__(self, message: str = "", *, error_class: str | None = None) -> None:
        super().__init__(message)
        self.error_class = error_class


class ResourceDeferred(TaskError):
    """Нет ресурсов (диск, память, нагрузка, бюджет, квота): отложить, попытку НЕ тратить.

    `reason` — значение `domain.enums.DeferReason` (disk_low | load_high | mem_low |
    budget_exhausted | quota_exhausted).
    """

    def __init__(self, reason: str, *, retry_after_sec: float = 60.0, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason
        self.retry_after_sec = retry_after_sec


class Cancelled(TaskError):
    """Оператор отменил задачу (`tasks.cancel_requested`)."""
