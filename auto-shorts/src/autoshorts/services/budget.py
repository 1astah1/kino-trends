"""Бюджет платных вызовов (docs/ARCHITECTURE.md §6.1 «Бюджет», §5.2 `/budget`).

Одна строка `budget_state` (id=1). Бизнес-день считается часами БД в `business_timezone`
(`(now() AT TIME ZONE tz)::date`), а не `date.today()` процесса.

Для исполнителя стадий (M1-C / M2) — `TaskBudgetGate` (протокол `stages.base.BudgetGate`):

    gate = budget.TaskBudgetGate(task_id=task["id"], job_id=task["job_id"])
    r = gate.reserve(0.02, provider="fal_flux_schnell")   # нет денег/пауза → ResourceDeferred
    ... платный вызов ...
    gate.settle(r, 0.018, step_name="image", model="fal-ai/flux/schnell", units=1, latency_ms=900)
    # вызов упал до списания денег: gate.release(r)

`settle` одной транзакцией пишет `generation_events`, переносит резерв в расход и обновляет
кеш `jobs.cost_usd`. Низкоуровневые `reserve(conn, …)` / `settle(conn, …)` — для своих транзакций.

Каждый резерв гейта — строка `budget_reservations` (миграция 0016): задача, её попытка
(`tasks.attempts`), воркер (`tasks.locked_by`), бизнес-день. Закрывается строка только условным
`UPDATE … WHERE id = … AND status = 'open' RETURNING`, и `reserved_usd` уменьшается, только если
строка вернулась и её день равен `budget_state.day` — поэтому гонка воркер/reaper не снимает
резерв дважды. Воркер создаёт гейт на каждое исполнение задачи (`ctx.budget`) и после
обработчика при любом исходе (исключение, retry, отмена, остановка, потеря аренды) снимает всё,
что осталось открытым: `gate.release_open()` (`release_reason='task_ended'`). Резерв процесса,
убитого SIGKILL/OOM, снимает reaper (`reap_orphan_reservations`, `release_reason='worker_lost'`):
задача уже не running, её взял другой воркер или это другая попытка, либо резерв без задачи
(синхронный вызов API) старше часа. Вызов, досчитанный после снятия резерва, всё равно пишет
расход в `spent_today_usd`, а в закрытую строку — `actual_usd` и событие.

Низкоуровневый `reserve(conn, …)` пишет строку только с `provider=` (так резервирует гейт, он же
закрывает строку по id); без него — старый резерв суммой, который закрывает `settle(conn, …)` по
`estimate_usd` (и уходит сменой дня).

`reserve(conn, …)` не бросает исключений (возвращает `Reservation`), чтобы пауза фиксировалась
даже когда вызывающий откатывает свою транзакцию; `reserve_or_defer` коммитит и бросает
`ResourceDeferred('budget_exhausted')` уже снаружи транзакции.

Пауза: автоматическая (`pause_reason = 'budget_exhausted'`) снимается сменой бизнес-дня или
увеличением лимита; ручная (`POST /budget/pause`, причина — текст оператора) держится до
`POST /budget/resume`, в том числе через полночь.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from autoshorts.api.schemas.budget import BudgetReleaseReason, BudgetReservationStatus
from autoshorts.core.errors import ResourceDeferred
from autoshorts.core.timeutil import utcnow, zone
from autoshorts.db.notify import publish_event
from autoshorts.domain.enums import AlertCode, AlertSeverity, DeferReason
from autoshorts.domain.events import EventType
from autoshorts.repos import budget as budget_repo
from autoshorts.services import alerts, audit
from autoshorts.services.audit import Actor
from autoshorts.services.settings import business_range, get_runtime_settings

log = logging.getLogger(__name__)

AUTO_PAUSE_REASON = str(DeferReason.BUDGET_EXHAUSTED)
NEXT_DAY_MARGIN_SEC = 300  # задача откладывается до начала следующего бизнес-дня + 5 мин
MANUAL_PAUSE_RETRY_SEC = 300  # при ручной паузе воркер заглядывает раз в 5 мин
PAUSED_ALERT_KEY = "budget_paused"
ZERO = Decimal("0")
RESERVATIONS_RETAIN_DAYS = 14  # закрытые строки budget_reservations старше — удаляет reaper
ORPHAN_BATCH = 500  # сколько сирот reaper снимает за один проход


@dataclass(frozen=True)
class Reservation:
    ok: bool
    budget: dict[str, Any]
    retry_after_sec: float | None = None
    reason: str | None = None
    reservation_id: int | None = None  # строка budget_reservations (резерв с provider=)


def _dec(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def is_auto_pause(reason: str | None) -> bool:
    return reason is None or reason == AUTO_PAUSE_REASON


def _tz(conn: Any) -> str:
    return get_runtime_settings(conn).business_timezone


def _load(conn: Any, tz: str, *, for_update: bool) -> dict[str, Any]:
    state = budget_repo.get_state(conn, tz=tz, for_update=for_update)
    if state is None:
        raise RuntimeError("В budget_state нет строки id=1 — бюджетный предохранитель не настроен")
    return state


def _virtual_rollover(state: dict[str, Any]) -> dict[str, Any]:
    """Как будет выглядеть строка в новом бизнес-дне (без записи)."""
    if state["day"] == state["business_day"]:
        return dict(state)
    rolled = dict(state)
    rolled.update(spent_today_usd=ZERO, reserved_usd=ZERO, day=state["business_day"], alerted_at=None)
    if is_auto_pause(state["pause_reason"]):
        rolled.update(paused=False, pause_reason=None)
    return rolled


def _rollover(conn: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Сменился бизнес-день — обнулить расход и резерв, снять автоматическую паузу (с записью)."""
    if state["day"] == state["business_day"]:
        return state
    rolled = _virtual_rollover(state)
    budget_repo.update(
        conn,
        {
            "spent_today_usd": ZERO,
            "reserved_usd": ZERO,
            "day": rolled["day"],
            "alerted_at": None,
            "paused": rolled["paused"],
            "pause_reason": rolled["pause_reason"],
        },
    )
    if state["paused"] and not rolled["paused"]:
        alerts.resolve_alert(conn, PAUSED_ALERT_KEY)
    return rolled


def view(state: dict[str, Any]) -> dict[str, Any]:
    """Строка → схема `Budget`."""
    return {
        "day": state["day"],
        "daily_limit_usd": float(state["daily_limit_usd"]),
        "spent_today_usd": float(state["spent_today_usd"]),
        "reserved_usd": float(state["reserved_usd"]),
        "paused": bool(state["paused"]),
        "pause_reason": state["pause_reason"],
        "alert_threshold_pct": int(state["alert_threshold_pct"]),
    }


def _publish(conn: Any, state: dict[str, Any]) -> None:
    publish_event(
        conn,
        EventType.BUDGET,
        {
            "spent_usd": float(state["spent_today_usd"]),
            "reserved_usd": float(state["reserved_usd"]),
            "limit_usd": float(state["daily_limit_usd"]),
            "paused": bool(state["paused"]),
        },
    )


def seconds_until_next_day(tz: str, *, margin_sec: float = NEXT_DAY_MARGIN_SEC) -> float:
    tzi = zone(tz)
    now_local = utcnow().astimezone(tzi)
    next_midnight = datetime.combine(now_local.date() + timedelta(days=1), time.min, tzinfo=tzi)
    return max((next_midnight - now_local).total_seconds(), 0.0) + margin_sec


def _check_threshold(conn: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Порог `alert_threshold_pct` открывает алерт `budget_80` (раз в бизнес-день)."""
    limit = _dec(state["daily_limit_usd"])
    if limit <= 0 or state["alerted_at"] is not None:
        return state
    used = _dec(state["spent_today_usd"]) + _dec(state["reserved_usd"])
    if used * 100 < limit * int(state["alert_threshold_pct"]):
        return state
    alerts.open_alert(
        conn,
        code=str(AlertCode.BUDGET_80),
        severity=AlertSeverity.WARNING,
        dedup_key=f"budget_80:{state['day'].isoformat()}",
        title=f"Бюджет дня израсходован на {int(state['alert_threshold_pct'])}% и больше",
        details={
            "day": state["day"].isoformat(),
            "spent_usd": float(state["spent_today_usd"]),
            "reserved_usd": float(state["reserved_usd"]),
            "limit_usd": float(limit),
        },
    )
    budget_repo.update(conn, {"alerted_at": utcnow()})
    return {**state, "alerted_at": utcnow()}


# ---------------------------------------------------------------- для воркеров


def reserve(
    conn: Any,
    amount_usd: float | Decimal,
    *,
    task_id: int | None = None,
    provider: str | None = None,
    job_id: int | None = None,
    source_id: int | None = None,
    trend_brief_id: int | None = None,
) -> Reservation:
    """Зарезервировать сумму под платный вызов. Не бросает: смотрите `Reservation.ok`.

    С `provider` резерв пишется строкой `budget_reservations` (id — `Reservation.reservation_id`),
    закрывать его нужно `settle(conn, …, reservation_id=…)`. Без `provider` — только сумма в
    `reserved_usd` (как до 0016).
    """
    amount = _dec(amount_usd)
    if amount < 0:
        raise ValueError("amount_usd < 0")
    tz = _tz(conn)
    state = _rollover(conn, _load(conn, tz, for_update=True))
    if state["paused"]:
        retry = seconds_until_next_day(tz) if is_auto_pause(state["pause_reason"]) else MANUAL_PAUSE_RETRY_SEC
        return Reservation(False, view(state), retry, AUTO_PAUSE_REASON)
    used = _dec(state["spent_today_usd"]) + _dec(state["reserved_usd"])
    if used + amount > _dec(state["daily_limit_usd"]):
        budget_repo.update(conn, {"paused": True, "pause_reason": AUTO_PAUSE_REASON})
        state = {**state, "paused": True, "pause_reason": AUTO_PAUSE_REASON}
        alerts.open_alert(
            conn,
            code=str(AlertCode.BUDGET_PAUSED),
            severity=AlertSeverity.CRITICAL,
            dedup_key=PAUSED_ALERT_KEY,
            title="Бюджет дня исчерпан: платные шаги на паузе до следующего дня",
            details={
                "day": state["day"].isoformat(),
                "limit_usd": float(state["daily_limit_usd"]),
                "used_usd": float(used),
                "requested_usd": float(amount),
                "task_id": task_id,
            },
        )
        _publish(conn, state)
        return Reservation(False, view(state), seconds_until_next_day(tz), AUTO_PAUSE_REASON)
    reserved = _dec(state["reserved_usd"]) + amount
    budget_repo.update(conn, {"reserved_usd": reserved})
    reservation_id = None
    if provider is not None:
        row = budget_repo.insert_reservation(
            conn,
            amount_usd=amount,
            provider=provider,
            business_day=state["day"],
            task_id=task_id,
            job_id=job_id,
            source_id=source_id,
            trend_brief_id=trend_brief_id,
        )
        reservation_id = int(row["id"])
    state = _check_threshold(conn, {**state, "reserved_usd": reserved})
    _publish(conn, state)
    return Reservation(True, view(state), reservation_id=reservation_id)


def _reserve_tx(amount_usd: float | Decimal, **kwargs: Any) -> Reservation:
    """`reserve` в своей транзакции; денег нет или пауза — `ResourceDeferred('budget_exhausted')`
    (уже после коммита: пауза и алерт записываются, даже когда резерв не выдан)."""
    from autoshorts.db.pool import connection

    with connection() as conn:
        result = reserve(conn, amount_usd, **kwargs)
    if not result.ok:
        raise ResourceDeferred(
            AUTO_PAUSE_REASON,
            retry_after_sec=float(result.retry_after_sec or MANUAL_PAUSE_RETRY_SEC),
            message="Бюджет дня исчерпан или платные шаги на паузе",
        )
    return result


def reserve_or_defer(
    amount_usd: float | Decimal,
    *,
    task_id: int | None = None,
    provider: str | None = None,
    job_id: int | None = None,
    source_id: int | None = None,
    trend_brief_id: int | None = None,
) -> dict[str, Any]:
    """`reserve` в своей транзакции; денег нет или пауза — `ResourceDeferred('budget_exhausted')`.

    Возвращает бюджет (схема `Budget`); id строки резерва (при `provider=`) — в `reservation_id`.
    """
    result = _reserve_tx(
        amount_usd,
        task_id=task_id,
        provider=provider,
        job_id=job_id,
        source_id=source_id,
        trend_brief_id=trend_brief_id,
    )
    if result.reservation_id is None:
        return result.budget
    return {**result.budget, "reservation_id": result.reservation_id}


def _close_row(
    conn: Any,
    state: dict[str, Any],
    reservation_id: int,
    *,
    actual: Decimal,
    generation_event_id: int | None,
    release_reason: str | None,
) -> Decimal:
    """Закрыть строку резерва (под `budget_state FOR UPDATE`). Сколько вычесть из `reserved_usd`:
    сумму строки, если закрыли именно мы и резерв сегодняшний; иначе 0."""
    if release_reason is not None:
        row = budget_repo.close_reservation(
            conn,
            reservation_id,
            status=str(BudgetReservationStatus.RELEASED),
            release_reason=release_reason,
            actual_usd=actual if actual > 0 else None,
            generation_event_id=generation_event_id,
        )
    else:
        row = budget_repo.close_reservation(
            conn,
            reservation_id,
            status=str(BudgetReservationStatus.SETTLED),
            actual_usd=max(actual, ZERO),
            generation_event_id=generation_event_id,
        )
    if row is None:
        # уже закрыт (reaper, release_open, повтор): резерв не трогаем, факт расхода дописываем
        if release_reason is None or actual > 0:
            budget_repo.annotate_closed_reservation(
                conn, reservation_id, actual_usd=max(actual, ZERO), generation_event_id=generation_event_id
            )
        return ZERO
    return _dec(row["amount_usd"]) if row["business_day"] == state["day"] else ZERO


def settle(
    conn: Any,
    *,
    estimate_usd: float | Decimal,
    actual_usd: float | Decimal,
    reserved_on: date | None = None,
    reservation_id: int | None = None,
    generation_event_id: int | None = None,
    release_reason: str | None = None,
) -> dict[str, Any]:
    """После платного вызова, в транзакции с `generation_event`: reserved −= estimate; spent += actual.

    Резерв, сделанный в прошлый бизнес-день (`reserved_on`), уже обнулён сменой дня — из резерва
    ничего не вычитается, а расход идёт в новый день. Без `reserved_on` днём резерва считается
    день, записанный в `budget_state` до смены.

    С `reservation_id` (строка 0016) судьбу резерва решает строка: `estimate_usd`/`reserved_on` не
    используются, вычитается сумма строки, только если её закрыл этот вызов и её `business_day`
    равен текущему дню. `release_reason` — снять без расхода (`status='released'`), иначе
    `status='settled'` с `actual_usd` и `generation_event_id`.
    """
    estimate, actual = _dec(estimate_usd), _dec(actual_usd)
    tz = _tz(conn)
    state = _load(conn, tz, for_update=True)
    stored_day = state["day"]
    state = _rollover(conn, state)
    if reservation_id is not None:
        subtract = _close_row(
            conn,
            state,
            int(reservation_id),
            actual=actual,
            generation_event_id=generation_event_id,
            release_reason=release_reason,
        )
    else:
        same_day = (reserved_on if reserved_on is not None else stored_day) == state["day"]
        subtract = estimate if same_day else ZERO
    reserved = max(_dec(state["reserved_usd"]) - subtract, ZERO)
    spent = _dec(state["spent_today_usd"]) + max(actual, ZERO)
    budget_repo.update(conn, {"reserved_usd": reserved, "spent_today_usd": spent})
    state = _check_threshold(conn, {**state, "reserved_usd": reserved, "spent_today_usd": spent})
    _publish(conn, state)
    return view(state)


@dataclass(frozen=True)
class BudgetReservation:
    """Резерв под один платный вызов (возвращает `TaskBudgetGate.reserve`).

    `id` — строка `budget_reservations`; None — резерв суммой без строки (собран вручную).
    """

    amount_usd: Decimal
    provider: str
    day: date
    id: int | None = None


@dataclass
class TaskBudgetGate:
    """Бюджет-предохранитель задачи для `StageContext.budget` (протокол `stages.base.BudgetGate`)."""

    task_id: int | None = None
    job_id: int | None = None
    source_id: int | None = None
    trend_brief_id: int | None = None
    # выданные резервы: [reservation, open]; сравнение по идентичности объекта
    _issued: list[list[Any]] = field(default_factory=list, init=False, repr=False, compare=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    def reserve(self, amount_usd: float | Decimal, *, provider: str) -> BudgetReservation:
        """Зарезервировать сумму; лимит исчерпан или пауза — `ResourceDeferred('budget_exhausted')`."""
        result = _reserve_tx(
            amount_usd,
            task_id=self.task_id,
            provider=provider,
            job_id=self.job_id,
            source_id=self.source_id,
            trend_brief_id=self.trend_brief_id,
        )
        reservation = BudgetReservation(
            _dec(amount_usd), provider, result.budget["day"], result.reservation_id
        )
        with self._lock:
            self._issued.append([reservation, True])
        return reservation

    def _close(self, reservation: BudgetReservation) -> bool:
        """Отметить резерв закрытым в памяти гейта. False — его уже сняли (`release_open`).

        Для резерва со строкой (`id`) это только учёт гейта: вычитать или нет, решает условный
        UPDATE строки. Для резерва без строки False значит «вычитать нечего»; резерв не из этого
        гейта считается открытым (как раньше)."""
        with self._lock:
            for entry in self._issued:
                if entry[0] is reservation:
                    was_open = bool(entry[1])
                    entry[1] = False
                    return was_open
        return True

    def _reopen(self, reservation: BudgetReservation) -> None:
        """Транзакция settle/release не прошла — резерв снова открыт (его снимет `release_open`)."""
        with self._lock:
            for entry in self._issued:
                if entry[0] is reservation:
                    entry[1] = True
                    return

    @property
    def open_reservations(self) -> list[BudgetReservation]:
        with self._lock:
            return [entry[0] for entry in self._issued if entry[1]]

    def release_open(self) -> int:
        """Снять без расхода все резервы гейта, не закрытые `settle`/`release`. Возвращает их число."""
        with self._lock:
            pending = [entry[0] for entry in self._issued if entry[1]]
            for entry in self._issued:
                entry[1] = False
        if not pending:
            return 0
        from autoshorts.db.pool import connection

        try:
            with connection() as conn:
                for reservation in pending:
                    settle(
                        conn,
                        estimate_usd=reservation.amount_usd,
                        actual_usd=ZERO,
                        reserved_on=reservation.day,
                        reservation_id=reservation.id,
                        release_reason=str(BudgetReleaseReason.TASK_ENDED) if reservation.id else None,
                    )
        except BaseException:
            for reservation in pending:
                self._reopen(reservation)
            raise
        return len(pending)

    def settle(
        self, reservation: BudgetReservation, actual_usd: float | Decimal, **event: Any
    ) -> dict[str, Any]:
        """Одной транзакцией: строка `generation_events` + резерв → расход + кеш `jobs.cost_usd`.

        `event`: step_name (обязательно), model, units, unit_price_usd, latency_ms, http_status,
        status ('ok' | 'error'), error, cpu_ms.
        """
        from autoshorts.db.pool import connection

        fields = {k: v for k, v in event.items() if v is not None}
        unknown = set(fields) - (
            set(budget_repo.GENEV_INSERTABLE)
            - {"job_id", "task_id", "source_id", "trend_brief_id", "provider", "cost_usd"}
        )
        if unknown:
            raise TypeError(f"settle(): неизвестные поля события {sorted(unknown)}")
        if not fields.get("step_name"):
            raise TypeError("settle(): нужен step_name")
        actual = max(_dec(actual_usd), ZERO)
        subject = {
            "job_id": self.job_id,
            "task_id": self.task_id,
            "source_id": self.source_id,
            "trend_brief_id": self.trend_brief_id,
        }
        was_open = self._close(reservation)
        estimate = reservation.amount_usd if was_open else ZERO
        try:
            with connection() as conn:
                event_id = budget_repo.insert_generation_event(
                    conn,
                    {
                        **{k: v for k, v in subject.items() if v is not None},
                        **fields,
                        "provider": reservation.provider,
                        "cost_usd": actual,
                    },
                )
                result = settle(
                    conn,
                    estimate_usd=estimate,
                    actual_usd=actual,
                    reserved_on=reservation.day,
                    reservation_id=reservation.id,
                    generation_event_id=event_id,
                )
                if self.job_id is not None:
                    budget_repo.refresh_job_cost(conn, self.job_id)
        except BaseException:
            if was_open:
                self._reopen(reservation)
            raise
        return result

    def release(self, reservation: BudgetReservation) -> dict[str, Any]:
        """Снять резерв без расхода (вызов не состоялся и денег не стоил)."""
        from autoshorts.db.pool import connection

        was_open = self._close(reservation)
        estimate = reservation.amount_usd if was_open else ZERO
        try:
            with connection() as conn:
                return settle(
                    conn,
                    estimate_usd=estimate,
                    actual_usd=ZERO,
                    reserved_on=reservation.day,
                    reservation_id=reservation.id,
                    release_reason=str(BudgetReleaseReason.CALL_FAILED) if reservation.id else None,
                )
        except BaseException:
            if was_open:
                self._reopen(reservation)
            raise


# ---------------------------------------------------------------- reaper


def reap_orphan_reservations(conn: Any, *, limit: int = ORPHAN_BATCH) -> dict[str, Any]:
    """Снять открытые резервы без живого хозяина (`v_budget_orphan_reservations`).

    Под `budget_state FOR UPDATE` (тот же порядок блокировок, что у reserve/settle): каждая строка
    закрывается условным UPDATE с `release_reason='worker_lost'`; из `reserved_usd` вычитаются только
    сегодняшние строки, закрытые этим проходом. Если автоматическая пауза стояла из-за таких
    фантомных резервов и теперь расход + резерв ниже лимита, пауза снимается (как при увеличении
    лимита в `update_budget`). Ручная пауза не трогается.
    """
    tz = _tz(conn)
    state = _rollover(conn, _load(conn, tz, for_update=True))
    orphans = budget_repo.list_orphan_reservations(conn, limit=limit)
    released: list[dict[str, Any]] = []
    subtract = ZERO
    total = ZERO
    for orphan in orphans:
        row = budget_repo.close_reservation(
            conn,
            int(orphan["id"]),
            status=str(BudgetReservationStatus.RELEASED),
            release_reason=str(BudgetReleaseReason.WORKER_LOST),
        )
        if row is None:  # закрыл кто-то ещё между выборкой и UPDATE
            continue
        amount = _dec(row["amount_usd"])
        total += amount
        today = row["business_day"] == state["day"]
        if today:
            subtract += amount
        released.append(
            {
                "id": int(row["id"]),
                "task_id": row["task_id"],
                "worker_id": row["worker_id"],
                "amount_usd": float(amount),
                "orphan_reason": orphan["orphan_reason"],
                "today": today,
            }
        )
    unpaused = False
    if subtract > 0:
        reserved = max(_dec(state["reserved_usd"]) - subtract, ZERO)
        changes: dict[str, Any] = {"reserved_usd": reserved}
        state = {**state, "reserved_usd": reserved}
        used = _dec(state["spent_today_usd"]) + reserved
        if state["paused"] and is_auto_pause(state["pause_reason"]) and used < _dec(state["daily_limit_usd"]):
            changes.update(paused=False, pause_reason=None)
            state.update(paused=False, pause_reason=None)
            alerts.resolve_alert(conn, PAUSED_ALERT_KEY)
            unpaused = True
        budget_repo.update(conn, changes)
        _publish(conn, state)
    if released:
        log.warning(
            "budget reaper: released %s orphan reservation(s), %.6f USD (%.6f from today's reserve)%s",
            len(released),
            total,
            subtract,
            ", auto-pause lifted" if unpaused else "",
        )
    by_reason: dict[str, int] = {}
    for item in released:
        by_reason[item["orphan_reason"]] = by_reason.get(item["orphan_reason"], 0) + 1
    return {
        "released": len(released),
        "released_usd": float(total),
        "subtracted_usd": float(subtract),
        "by_reason": by_reason,
        "unpaused": unpaused,
        "items": released[:50],
    }


def purge_closed_reservations(conn: Any, *, older_than_days: int = RESERVATIONS_RETAIN_DAYS) -> int:
    """Удалить закрытые резервы старше `older_than_days` (открытые остаются всегда)."""
    return budget_repo.delete_closed_reservations(conn, older_than_days=older_than_days)


# ---------------------------------------------------------------- API


def get_budget(conn: Any | None = None) -> dict[str, Any]:
    """Бюджет текущего бизнес-дня (смена дня показывается, но не записывается)."""
    if conn is not None:
        return view(_virtual_rollover(_load(conn, _tz(conn), for_update=False)))
    from autoshorts.db.pool import connection

    with connection() as own:
        return get_budget(own)


def update_budget(
    *, daily_limit_usd: float | None, alert_threshold_pct: int | None, actor: Actor
) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        state = _rollover(conn, _load(conn, _tz(conn), for_update=True))
        changes: dict[str, Any] = {}
        diff: dict[str, Any] = {}
        if daily_limit_usd is not None and _dec(daily_limit_usd) != _dec(state["daily_limit_usd"]):
            changes["daily_limit_usd"] = _dec(daily_limit_usd)
            diff["daily_limit_usd"] = {"from": float(state["daily_limit_usd"]), "to": float(daily_limit_usd)}
        if alert_threshold_pct is not None and alert_threshold_pct != int(state["alert_threshold_pct"]):
            changes["alert_threshold_pct"] = alert_threshold_pct
            changes["alerted_at"] = None  # новый порог — алерт может сработать снова
            diff["alert_threshold_pct"] = {
                "from": int(state["alert_threshold_pct"]),
                "to": alert_threshold_pct,
            }
        if not changes:
            return view(state)
        state = {**state, **changes}
        used = _dec(state["spent_today_usd"]) + _dec(state["reserved_usd"])
        if state["paused"] and is_auto_pause(state["pause_reason"]) and used < _dec(state["daily_limit_usd"]):
            changes.update(paused=False, pause_reason=None)
            state.update(paused=False, pause_reason=None)
            alerts.resolve_alert(conn, PAUSED_ALERT_KEY)
        budget_repo.update(conn, changes)
        audit.record(
            conn, actor, "budget.update", "budget", "1", summary="изменены лимиты бюджета", diff=diff
        )
        state = _check_threshold(conn, state)
        _publish(conn, state)
        return view(state)


def pause(reason: str, actor: Actor) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    reason = reason.strip()
    with connection() as conn:
        state = _rollover(conn, _load(conn, _tz(conn), for_update=True))
        budget_repo.update(conn, {"paused": True, "pause_reason": reason})
        state = {**state, "paused": True, "pause_reason": reason}
        audit.record(conn, actor, "budget.pause", "budget", "1", summary=f"ручная пауза: {reason}")
        _publish(conn, state)
        return view(state)


def resume(actor: Actor) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        state = _rollover(conn, _load(conn, _tz(conn), for_update=True))
        was = state["pause_reason"] if state["paused"] else None
        budget_repo.update(conn, {"paused": False, "pause_reason": None})
        state = {**state, "paused": False, "pause_reason": None}
        alerts.resolve_alert(conn, PAUSED_ALERT_KEY)
        audit.record(
            conn,
            actor,
            "budget.resume",
            "budget",
            "1",
            summary="пауза снята" + (f" (была: {was})" if was else " (паузы не было)"),
        )
        _publish(conn, state)
        return view(state)


def list_generation_events(
    *,
    job_id: int | None = None,
    source_id: int | None = None,
    provider: str | None = None,
    step_name: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    before_id: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        t_from, t_to = business_range(date_from, date_to, _tz(conn))
        return budget_repo.list_generation_events(
            conn,
            job_id=job_id,
            source_id=source_id,
            provider=provider,
            step_name=step_name,
            created_from=t_from,
            created_to=t_to,
            before_id=before_id,
            limit=limit,
        )
