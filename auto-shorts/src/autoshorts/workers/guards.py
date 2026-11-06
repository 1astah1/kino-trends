"""Защиты тяжёлого воркера (docs/ARCHITECTURE.md §0 «Ресурсы», §6.1, §8.1).

* `HeavySlot` — ровно один тяжёлый процесс: flock на хосте (`/run/lock/autoshorts-heavy.lock`)
  **и** `pg_try_advisory_lock(771001)` на выделенном соединении (второй контейнер или хост с той
  же БД). Держится всё время жизни воркера; если соединение с блокировкой оборвалось, слот
  считается потерянным и перезахватывается.
* `Admission` — допуск перед взятием тяжёлой задачи по MemAvailable, PSI, load1 и диску; пороги
  из настроек (`core.resources.admission_policy`).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg

from autoshorts.core.resources import (
    AdmissionDecision,
    FileLock,
    admission_policy,
    default_heavy_lock_path,
    evaluate_heavy_admission,
    take_snapshot,
)

log = logging.getLogger(__name__)

HEAVY_ADVISORY_LOCK_KEY = 771001


class HeavySlot:
    def __init__(
        self,
        conninfo: str,
        *,
        lock_path: Path | None = None,
        advisory_key: int = HEAVY_ADVISORY_LOCK_KEY,
        application_name: str = "autoshorts-heavy-slot",
    ) -> None:
        self.conninfo = conninfo
        self.file_lock = FileLock(lock_path or default_heavy_lock_path())
        self.advisory_key = advisory_key
        self.application_name = application_name
        self._conn: psycopg.Connection[Any] | None = None
        self._mutex = threading.Lock()

    @property
    def held(self) -> bool:
        return self.file_lock.held and self._conn is not None and not self._conn.closed

    def try_acquire(self) -> bool:
        """Захватить слот (без ожидания). False — его держит другой живой тяжёлый воркер."""
        with self._mutex:
            if self.held:
                return True
            self._release_locked()
            if not self.file_lock.try_acquire():
                return False
            try:
                conn = psycopg.connect(
                    self.conninfo, autocommit=True, application_name=self.application_name, connect_timeout=5
                )
            except psycopg.Error as exc:
                log.warning("heavy slot: cannot connect for advisory lock: %s", exc)
                self.file_lock.release()
                return False
            try:
                got = conn.execute("SELECT pg_try_advisory_lock(%(k)s)", {"k": self.advisory_key}).fetchone()[
                    0
                ]
            except psycopg.Error as exc:
                log.warning("heavy slot: advisory lock query failed: %s", exc)
                got = False
            if not got:
                conn.close()
                self.file_lock.release()
                return False
            self._conn = conn
            log.info("heavy slot acquired (flock %s, advisory %s)", self.file_lock.path, self.advisory_key)
            return True

    def check(self) -> bool:
        """Жив ли слот: соединение с advisory lock отвечает. Нет — слот отпускается целиком."""
        with self._mutex:
            if self._conn is None or self._conn.closed:
                self._release_locked()
                return False
            try:
                self._conn.execute("SELECT 1")
                return True
            except psycopg.Error as exc:
                log.warning("heavy slot lost (advisory lock connection): %s", exc)
                self._release_locked()
                return False

    def _release_locked(self) -> None:
        if self._conn is not None:
            try:
                if not self._conn.closed:
                    self._conn.execute("SELECT pg_advisory_unlock(%(k)s)", {"k": self.advisory_key})
            except psycopg.Error:
                pass
            finally:
                try:
                    self._conn.close()
                finally:
                    self._conn = None
        self.file_lock.release()

    def release(self) -> None:
        with self._mutex:
            self._release_locked()


class Admission:
    """Решение допуска по текущему снимку ресурсов и настройкам (из кеша настроек воркера)."""

    def __init__(self, media_root: Path, settings_provider: Callable[[], Any]) -> None:
        self.media_root = Path(media_root)
        self.settings_provider = settings_provider

    def evaluate(self) -> AdmissionDecision:
        settings = self.settings_provider()
        policy = admission_policy(settings)
        snapshot = take_snapshot(self.media_root, media_usage=policy.media_max_gb is not None)
        return evaluate_heavy_admission(snapshot, policy)
