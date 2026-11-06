"""Пакет БД: пул соединений, раннер миграций, LISTEN/NOTIFY (docs/ARCHITECTURE.md §3.1, §4.1).

Модули: `pool.py` (psycopg-pool), `migrate.py` (раннер SQL-миграций), `notify.py`
(LISTEN/NOTIFY), `migrations/*.sql` (владелец — поток контрактов), `legacy.py` (старый `db.py`).

Совместимость со старым кодом. Раньше `autoshorts.db` был модулем, и воркер, оркестратор, бот,
редиректор и дашборд пишут `from autoshorts import db; db.enqueue_job(...)` или
`from autoshorts.db import get_conn`. Эти имена по-прежнему доступны здесь: они **лениво**
пробрасываются в `autoshorts.db.legacy`, поэтому `import autoshorts.db.migrate` не тянет legacy
`autoshorts.config` (тот создаёт каталоги и читает .env при импорте).

Присваивание legacy-имени пакету (`monkeypatch.setattr(db, "SETTINGS", ...)` в тестах M0)
переносится в `legacy`, иначе функции legacy видели бы старое значение: `get_conn` читает
`SETTINGS` из globals своего модуля, и тест бюджета ушёл бы в dev-базу из .env.
"""

from __future__ import annotations

import importlib
import sys
import types
from typing import Any

LEGACY_EXPORTS: frozenset[str] = frozenset(
    {
        "SETTINGS",
        "BUSINESS_TIMEZONE",
        "get_conn",
        "enqueue_job",
        "claim_next_job",
        "increment_attempt_count",
        "update_job_status",
        "record_generation_event",
        "get_published_video_cost",
        "claim_jobs_for_review",
        "mark_review_sent",
        "check_and_reserve_budget",
    }
)

__all__ = sorted(LEGACY_EXPORTS)


def _legacy() -> types.ModuleType:
    return importlib.import_module("autoshorts.db.legacy")


def __getattr__(name: str) -> Any:
    if name in LEGACY_EXPORTS:
        return getattr(_legacy(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | LEGACY_EXPORTS)


class _DbPackage(types.ModuleType):
    """Модуль-пакет, который переносит запись legacy-имён в `autoshorts.db.legacy`."""

    def __setattr__(self, name: str, value: Any) -> None:
        if name in LEGACY_EXPORTS:
            setattr(_legacy(), name, value)
            return
        super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        if name in LEGACY_EXPORTS:
            delattr(_legacy(), name)
            return
        super().__delattr__(name)


sys.modules[__name__].__class__ = _DbPackage
