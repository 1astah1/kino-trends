"""Планировщик расписаний `schedules` (docs/ARCHITECTURE.md §6.1 «Планировщик»).

Работает внутри `worker-light`. Лидер один на базу: `pg_try_advisory_lock(771002)` на
выделенном соединении (держится, пока жив процесс). Раз в `tick_sec` (10 с) лидер берёт
созревшие расписания (`FOR UPDATE SKIP LOCKED`), ставит задачу с `dedup_key='sched:<name>'`
(активный дубль — новая не ставится) и сдвигает `next_run_at`:

* без якоря — на ближайшее `prev + k·interval` в будущем (пропущенные запуски не догоняются);
* с `anchor_local_time` — на ближайшее это время в `business_timezone` (для интервалов
  длиннее суток — не раньше `now + interval − 1 сутки`).

Расписания видов задач без обработчика в реестре этого процесса пропускаются (сдвигаются без
постановки): задача, которую никто не возьмёт, только копилась бы в очереди.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Any

import psycopg

from autoshorts.core.timeutil import ensure_utc, next_local_time_utc
from autoshorts.db.pool import connection
from autoshorts.repos import schedules as schedules_repo
from autoshorts.services.tasks import enqueue

log = logging.getLogger(__name__)

SCHEDULER_ADVISORY_LOCK_KEY = 771002
DEFAULT_TICK_SEC = 10.0
DAY_SEC = 86400


def next_run_at(schedule: dict[str, Any], now: datetime, tz: str) -> datetime:
    interval = int(schedule["interval_sec"])
    anchor = schedule.get("anchor_local_time")
    now = ensure_utc(now)
    if anchor is not None:
        after = now + timedelta(seconds=max(interval - DAY_SEC, 0))
        return next_local_time_utc(anchor, tz, after=after)
    prev = ensure_utc(schedule["next_run_at"])
    if prev > now:
        return prev + timedelta(seconds=interval)
    k = math.floor((now - prev).total_seconds() / interval) + 1
    return prev + timedelta(seconds=interval * k)


class Scheduler:
    def __init__(
        self,
        conninfo: str,
        *,
        known_kinds: Callable[[], Iterable[str]],
        timezone_provider: Callable[[], str] = lambda: "Europe/Moscow",
        tick_sec: float = DEFAULT_TICK_SEC,
        advisory_key: int = SCHEDULER_ADVISORY_LOCK_KEY,
    ) -> None:
        self.conninfo = conninfo
        self.known_kinds = known_kinds
        self.timezone_provider = timezone_provider
        self.tick_sec = tick_sec
        self.advisory_key = advisory_key
        self._conn: psycopg.Connection[Any] | None = None
        self._last_tick = 0.0
        self._skipped_logged: set[str] = set()
        self._lock = threading.Lock()

    @property
    def is_leader(self) -> bool:
        return self._conn is not None and not self._conn.closed

    def _ensure_leader(self) -> bool:
        if self._conn is not None and not self._conn.closed:
            try:
                self._conn.execute("SELECT 1")
                return True
            except psycopg.Error:
                self._drop()
        try:
            conn = psycopg.connect(
                self.conninfo, autocommit=True, application_name="autoshorts-scheduler", connect_timeout=5
            )
        except psycopg.Error as exc:
            log.warning("scheduler: cannot connect: %s", exc)
            return False
        try:
            got = conn.execute("SELECT pg_try_advisory_lock(%(k)s)", {"k": self.advisory_key}).fetchone()[0]
        except psycopg.Error:
            got = False
        if not got:
            conn.close()
            return False
        self._conn = conn
        log.info("scheduler: this worker is the leader (advisory %s)", self.advisory_key)
        return True

    def _drop(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def close(self) -> None:
        with self._lock:
            if self._conn is not None and not self._conn.closed:
                try:
                    self._conn.execute("SELECT pg_advisory_unlock(%(k)s)", {"k": self.advisory_key})
                except psycopg.Error:
                    pass
            self._drop()

    def maybe_tick(self) -> list[int]:
        now = time.monotonic()
        if now - self._last_tick < self.tick_sec:
            return []
        self._last_tick = now
        try:
            return self.tick()
        except Exception:  # noqa: BLE001 - сбой планировщика не должен ронять воркер
            log.exception("scheduler tick failed")
            return []

    def tick(self, now: datetime | None = None) -> list[int]:
        """Один проход. Возвращает id созданных задач."""
        with self._lock:
            if not self._ensure_leader():
                return []
            created: list[int] = []
            known = set(self.known_kinds())
            tz = self.timezone_provider()
            with connection() as conn:
                db_now = now or conn.execute("SELECT now() AS n").fetchone()["n"]
                for sched in schedules_repo.claim_due(conn):
                    task_id: int | None = None
                    kind = sched["task_kind"]
                    if kind in known:
                        res = enqueue(
                            conn,
                            kind,
                            resource_class=sched["resource_class"],
                            payload=dict(sched.get("payload") or {}),
                            dedup_key=f"sched:{sched['name']}",
                        )
                        task_id = int(res.task["id"])
                        if res.created:
                            created.append(task_id)
                    elif kind not in self._skipped_logged:
                        self._skipped_logged.add(kind)
                        log.info("scheduler: no handler for %s (schedule %s), skipped", kind, sched["name"])
                    schedules_repo.advance(
                        conn, sched["name"], next_run_at=next_run_at(sched, db_now, tz), last_task_id=task_id
                    )
            return created
