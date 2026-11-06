"""Рантайм-настройки (docs/ARCHITECTURE.md §8.3): дефолты в `domain/settings_schema.py`, в БД —
только переопределения (таблица `settings`).

Для остальных процессов и потоков:

    from autoshorts.services.settings import get_runtime_settings
    rs = get_runtime_settings()            # RuntimeSettings; кеш в процессе 15 с
    rs.business_timezone, rs.allow_mock_in_prod, rs.disk_hard_min_gb, ...

Битое переопределение в БД (неизвестный ключ, значение не проходит схему) пропускается
с предупреждением в лог — одна плохая строка не должна ронять API и воркеры.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime
from typing import Any

from pydantic import ValidationError

from autoshorts.core.errors import ValidationFailed
from autoshorts.core.timeutil import date_range_utc
from autoshorts.domain.settings_schema import RuntimeSettings, setting_specs, validate_setting_value
from autoshorts.repos import settings as settings_repo
from autoshorts.services import audit
from autoshorts.services.audit import Actor

log = logging.getLogger(__name__)

CACHE_TTL_SEC = 15.0

_lock = threading.Lock()
_cache: tuple[float, RuntimeSettings] | None = None


def invalidate_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def _valid_overrides(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for row in rows:
        try:
            out[row["key"]] = validate_setting_value(row["key"], row["value"])
        except ValueError as exc:
            log.warning("setting override %s ignored: %s", row["key"], str(exc).splitlines()[0])
    return out


def load_runtime_settings(conn: Any) -> RuntimeSettings:
    """Дефолты + переопределения из БД (без кеша)."""
    overrides = _valid_overrides(settings_repo.list_overrides(conn))
    try:
        return RuntimeSettings.from_overrides(overrides)
    except ValidationError as exc:  # несовместимые между собой значения — лучше дефолты, чем падение
        log.warning("setting overrides rejected as a whole: %s", str(exc).splitlines()[0])
        return RuntimeSettings()


def get_runtime_settings(
    conn: Any | None = None, *, max_age: float = CACHE_TTL_SEC, fallback_to_defaults: bool = False
) -> RuntimeSettings:
    """Текущие настройки. `conn` — использовать чужое соединение (внутри транзакции).

    `fallback_to_defaults=True` — при недоступной БД вернуть дефолты (для /health/ready).
    """
    global _cache
    now = time.monotonic()
    cached = _cache
    if cached is not None and now - cached[0] < max_age:
        return cached[1]
    try:
        if conn is not None:
            rs = load_runtime_settings(conn)
        else:
            from autoshorts.db.pool import connection

            with connection() as own:
                rs = load_runtime_settings(own)
    except Exception:
        if fallback_to_defaults:
            return cached[1] if cached is not None else RuntimeSettings()
        raise
    with _lock:
        _cache = (now, rs)
    return rs


def business_range(
    date_from: date | None, date_to: date | None, tz: str
) -> tuple[datetime | None, datetime | None]:
    """`date_from/date_to` включительно в бизнес-поясе → полуинтервал UTC; from > to — 422."""
    try:
        return date_range_utc(date_from, date_to, tz)
    except ValueError as exc:
        raise ValidationFailed(
            "date_from позже date_to",
            details={"errors": [{"loc": ["query", "date_from"], "msg": str(exc), "type": "date_range"}]},
        ) from exc


# ---------------------------------------------------------------- API


def _items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {r["key"]: r for r in rows}
    valid = _valid_overrides(rows)
    items = []
    for key, spec in setting_specs().items():
        row = by_key.get(key)
        overridden = key in valid
        items.append(
            {
                "key": key,
                "value": valid[key] if overridden else spec.default,
                "default": spec.default,
                "description": spec.description,
                "group": spec.group,
                "is_overridden": overridden,
                "updated_at": row["updated_at"] if row is not None else None,
            }
        )
    return items


def list_settings() -> list[dict[str, Any]]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        return _items(settings_repo.list_overrides(conn))


def _check_consistency(values: RuntimeSettings) -> list[dict[str, Any]]:
    errors = []
    if not values.disk_emergency_gb < values.disk_hard_min_gb < values.disk_soft_min_gb:
        errors.append(
            {
                "loc": ["body", "disk_hard_min_gb"],
                "msg": "Пороги диска должны идти по возрастанию: disk_emergency_gb < disk_hard_min_gb "
                "< disk_soft_min_gb",
                "type": "disk_thresholds_order",
            }
        )
    if values.outlier_attention >= values.outlier_format_works:
        errors.append(
            {
                "loc": ["body", "outlier_attention"],
                "msg": "outlier_attention должен быть меньше outlier_format_works",
                "type": "outlier_thresholds_order",
            }
        )
    return errors


def patch_settings(patch: dict[str, Any], actor: Actor) -> list[dict[str, Any]]:
    """`{key: value}`; `null` — сбросить переопределение к дефолту. Всё или ничего (422)."""
    from autoshorts.db.pool import connection

    specs = setting_specs()
    errors: list[dict[str, Any]] = []
    normalized: dict[str, Any] = {}
    for key, value in patch.items():
        if key not in specs:
            errors.append({"loc": ["body", key], "msg": "Неизвестная настройка", "type": "unknown_setting"})
            continue
        if value is None:
            normalized[key] = None
            continue
        try:
            normalized[key] = validate_setting_value(key, value)
        except ValueError as exc:
            errors.append({"loc": ["body", key], "msg": _short(str(exc)), "type": "value_error"})
    if errors:
        raise ValidationFailed("Настройки не прошли проверку", details={"errors": errors})

    with connection() as conn:
        settings_repo.lock_table(conn)
        rows = settings_repo.list_overrides(conn)
        current = _valid_overrides(rows)
        merged = {k: v for k, v in current.items() if normalized.get(k, v) is not None}
        merged.update({k: v for k, v in normalized.items() if v is not None})
        try:
            values = RuntimeSettings.from_overrides(merged)
        except ValidationError as exc:
            raise ValidationFailed(
                "Настройки не прошли проверку",
                details={"errors": [{"loc": ["body"], "msg": _short(str(exc)), "type": "value_error"}]},
            ) from exc
        errors = _check_consistency(values)
        if errors:
            raise ValidationFailed("Настройки противоречат друг другу", details={"errors": errors})

        diff: dict[str, Any] = {}
        for key, value in normalized.items():
            before = current.get(key)
            if value is None:
                if settings_repo.delete(conn, key):
                    diff[key] = {"from": before, "to": None}
            elif before != value or key not in current:
                settings_repo.upsert(conn, key, value, updated_by=actor.user_id)
                diff[key] = {"from": before, "to": value}
        if diff:
            audit.record(
                conn,
                actor,
                "settings.update",
                "settings",
                None,
                summary="изменены настройки: " + ", ".join(sorted(diff)),
                diff=diff,
            )
        items = _items(settings_repo.list_overrides(conn))
    invalidate_cache()
    return items


def _short(message: str) -> str:
    lines = [ln.strip() for ln in message.splitlines() if ln.strip()]
    return lines[-1] if lines else message
