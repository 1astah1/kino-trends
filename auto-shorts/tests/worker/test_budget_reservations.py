"""Постоянные резервы бюджета (миграция 0016): гейт пишет, закрывает и снимает строки, reaper снимает
резервы задач, чей воркер пропал, и ни одна гонка не вычитает резерв дважды."""

from __future__ import annotations

import threading
import time
from decimal import Decimal
from typing import Any

import pytest

from autoshorts.db.pool import connection
from autoshorts.services import budget
from autoshorts.services import tasks as tasks_svc
from tests.worker.conftest import enqueue_raw, make_job, task_row


class _FakeCtx:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {}


def _state(conn: Any) -> dict[str, Any]:
    return conn.execute(
        "SELECT day, spent_today_usd, reserved_usd, paused, pause_reason, daily_limit_usd FROM budget_state"
    ).fetchone()


def _reserved(conn: Any) -> Decimal:
    return Decimal(str(_state(conn)["reserved_usd"]))


def _row(conn: Any, rid: int) -> dict[str, Any]:
    return conn.execute("SELECT * FROM budget_reservations WHERE id = %s", (rid,)).fetchone()


def _running_task(conn: Any, *, worker: str = "w1", job_id: int | None = None) -> dict[str, Any]:
    """Задача, взятая воркером `worker` так, как её берёт очередь (attempts += 1, locked_by)."""
    cols: dict[str, Any] = {"job_id": job_id} if job_id is not None else {}
    t = enqueue_raw(conn, "wtest_record", **cols)
    with connection() as c:
        row = tasks_svc.claim(c, "light", worker, ["wtest_record"])
    assert row is not None and row["id"] == t["id"]
    return row


def _reap() -> dict[str, Any]:
    from autoshorts.tasks.registry import get_task_spec, load_all

    load_all()
    return get_task_spec("task_reaper").handler(_FakeCtx())


def _invariant(conn: Any) -> None:
    """reserved_usd = сумма открытых сегодняшних строк (у тестов нет резервов без строки)."""
    with connection() as c:
        total = budget.budget_repo.open_reserved_total(c, _state(conn)["day"])
    assert Decimal(str(total)) == _reserved(conn)


# ---------------------------------------------------------------- гейт


def test_gate_reserve_writes_row_with_attempt_and_worker(queue_env: Any, conn: Any) -> None:
    job_id = make_job(conn)
    task = _running_task(conn, worker="w-alpha", job_id=job_id)
    conn.execute("UPDATE tasks SET attempts = 2 WHERE id = %s", (task["id"],))
    gate = budget.TaskBudgetGate(task_id=task["id"], job_id=job_id)
    r = gate.reserve(0.25, provider="openai_compat")
    assert r.id is not None
    row = _row(conn, r.id)
    assert (row["task_id"], row["task_attempt"], row["worker_id"], row["job_id"]) == (
        task["id"],
        2,
        "w-alpha",
        job_id,
    )
    assert row["status"] == "open" and row["closed_at"] is None and row["provider"] == "openai_compat"
    assert row["business_day"] == _state(conn)["day"] == r.day
    assert Decimal(str(row["amount_usd"])) == Decimal("0.25") == _reserved(conn)
    _invariant(conn)


def test_settle_closes_row_with_actual_and_event(queue_env: Any, conn: Any) -> None:
    job_id = make_job(conn)
    task = _running_task(conn, job_id=job_id)
    gate = budget.TaskBudgetGate(task_id=task["id"], job_id=job_id)
    r = gate.reserve(0.02, provider="fal_flux_schnell")
    after = gate.settle(r, 0.018, step_name="image", model="fal-ai/flux/schnell", units=1)
    assert after["reserved_usd"] == 0.0 and after["spent_today_usd"] == 0.018
    row = _row(conn, r.id)
    ev = conn.execute("SELECT id FROM generation_events ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "settled" and row["closed_at"] is not None and row["release_reason"] is None
    assert Decimal(str(row["actual_usd"])) == Decimal("0.018") and row["generation_event_id"] == ev["id"]
    # повторный settle того же резерва: расход пишется, резерв второй раз не вычитается
    gate.settle(r, 0.001, step_name="image")
    s = _state(conn)
    assert Decimal(str(s["reserved_usd"])) == 0 and Decimal(str(s["spent_today_usd"])) == Decimal("0.019")
    assert _row(conn, r.id)["status"] == "settled"
    _invariant(conn)


def test_release_and_release_open_reasons(queue_env: Any, conn: Any) -> None:
    task = _running_task(conn)
    gate = budget.TaskBudgetGate(task_id=task["id"])
    failed = gate.reserve(0.5, provider="openai_compat")
    left1 = gate.reserve(0.25, provider="openai_compat")
    left2 = gate.reserve(0.125, provider="openai_compat")
    other = budget.TaskBudgetGate(task_id=task["id"]).reserve(1, provider="openai_compat")
    assert _reserved(conn) == Decimal("1.875")

    gate.release(failed)
    gate.release(failed)  # повтор ничего не вычитает
    assert _reserved(conn) == Decimal("1.375")
    row = _row(conn, failed.id)
    assert (row["status"], row["release_reason"], row["actual_usd"]) == ("released", "call_failed", None)

    assert gate.release_open() == 2
    assert _reserved(conn) == Decimal("1")  # чужой резерв на месте
    for r in (left1, left2):
        assert (_row(conn, r.id)["status"], _row(conn, r.id)["release_reason"]) == ("released", "task_ended")
    assert _row(conn, other.id)["status"] == "open"
    _invariant(conn)


def test_failed_transaction_keeps_row_open_and_gate_retries(
    queue_env: Any, conn: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _running_task(conn)
    gate = budget.TaskBudgetGate(task_id=task["id"])
    r = gate.reserve(0.4, provider="openai_compat")

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("db blip")

    monkeypatch.setattr(budget.budget_repo, "close_reservation", boom)
    with pytest.raises(RuntimeError):
        gate.release(r)
    monkeypatch.undo()
    assert _row(conn, r.id)["status"] == "open" and gate.open_reservations == [r]
    assert gate.release_open() == 1 and _reserved(conn) == 0
    assert _row(conn, r.id)["release_reason"] == "task_ended"


def test_low_level_reserve_without_provider_writes_no_row(queue_env: Any, conn: Any) -> None:
    with connection() as c:
        res = budget.reserve(c, 2)
    assert res.ok and res.reservation_id is None
    assert conn.execute("SELECT count(*) AS n FROM budget_reservations").fetchone()["n"] == 0
    with connection() as c:
        budget.settle(c, estimate_usd=2, actual_usd=1.5)
    assert _reserved(conn) == 0 and _reap()["budget_reservations"]["released"] == 0


def test_reserve_or_defer_returns_reservation_id(queue_env: Any, conn: Any) -> None:
    view = budget.reserve_or_defer(0.3, provider="openai_compat")
    assert isinstance(view["reservation_id"], int) and view["reserved_usd"] == 0.3
    assert "reservation_id" not in budget.reserve_or_defer(0.1)


# ---------------------------------------------------------------- reaper


def test_reaper_releases_reservation_of_requeued_task(queue_env: Any, conn: Any) -> None:
    """Воркер пропал: reaper задач возвращает задачу в очередь, в том же проходе снимается резерв."""
    dead = _running_task(conn, worker="dead-worker")
    alive = _running_task(conn, worker="alive-worker")
    r_dead = budget.TaskBudgetGate(task_id=dead["id"]).reserve(0.7, provider="openai_compat")
    r_alive = budget.TaskBudgetGate(task_id=alive["id"]).reserve(0.2, provider="openai_compat")
    conn.execute("UPDATE tasks SET heartbeat_at = now() - interval '10 minutes' WHERE id = %s", (dead["id"],))

    report = _reap()
    assert report["requeued"] == [dead["id"]]
    b = report["budget_reservations"]
    assert b["released"] == 1 and b["by_reason"] == {"task_not_running": 1}
    assert b["subtracted_usd"] == pytest.approx(0.7) and b["purged"] == 0
    assert task_row(conn, dead["id"])["status"] == "queued"
    row = _row(conn, r_dead.id)
    assert (row["status"], row["release_reason"]) == ("released", "worker_lost")
    assert _row(conn, r_alive.id)["status"] == "open" and _reserved(conn) == Decimal("0.2")
    _invariant(conn)
    # второй проход ничего не находит
    assert _reap()["budget_reservations"]["released"] == 0 and _reserved(conn) == Decimal("0.2")


def test_reaper_orphan_reasons(queue_env: Any, conn: Any) -> None:
    conn.execute("UPDATE budget_state SET daily_limit_usd = 100")
    finished = _running_task(conn, worker="w1")
    repicked = _running_task(conn, worker="w1")
    retried = _running_task(conn, worker="w2")
    r_fin = budget.TaskBudgetGate(task_id=finished["id"]).reserve(1, provider="p")
    r_rep = budget.TaskBudgetGate(task_id=repicked["id"]).reserve(2, provider="p")
    r_try = budget.TaskBudgetGate(task_id=retried["id"]).reserve(4, provider="p")
    r_api_old = budget.TaskBudgetGate(job_id=None).reserve(8, provider="p")  # синхронный вызов API
    r_api_new = budget.TaskBudgetGate().reserve(16, provider="p")
    conn.execute(
        "UPDATE tasks SET status = 'succeeded', finished_at = now() WHERE id = %s", (finished["id"],)
    )
    conn.execute("UPDATE tasks SET locked_by = 'w9' WHERE id = %s", (repicked["id"],))
    conn.execute("UPDATE tasks SET attempts = attempts + 1 WHERE id = %s", (retried["id"],))
    conn.execute(
        "UPDATE budget_reservations SET created_at = now() - interval '61 minutes' WHERE id = %s",
        (r_api_old.id,),
    )
    view = {
        r["id"]: r["orphan_reason"]
        for r in conn.execute("SELECT id, orphan_reason FROM v_budget_orphan_reservations").fetchall()
    }
    assert view == {
        r_fin.id: "task_not_running",
        r_rep.id: "task_repicked",
        r_try.id: "task_repicked",
        r_api_old.id: "no_task_stale",
    }
    b = _reap()["budget_reservations"]
    assert b["released"] == 4 and b["released_usd"] == 15.0
    assert _reserved(conn) == 16 and _row(conn, r_api_new.id)["status"] == "open"
    _invariant(conn)


def test_settle_after_reaper_does_not_double_subtract(queue_env: Any, conn: Any) -> None:
    """Воркер с протухшим heartbeat жив и досчитал вызов после того, как reaper снял резерв."""
    job_id = make_job(conn)
    task = _running_task(conn, job_id=job_id)
    gate = budget.TaskBudgetGate(task_id=task["id"], job_id=job_id)
    r = gate.reserve(0.5, provider="openai_compat")
    keep = budget.TaskBudgetGate().reserve(0.25, provider="openai_compat")
    conn.execute("UPDATE tasks SET heartbeat_at = now() - interval '10 minutes' WHERE id = %s", (task["id"],))
    assert _reap()["budget_reservations"]["released"] == 1
    assert _reserved(conn) == Decimal("0.25")

    state = gate.settle(r, 0.3, step_name="script")
    assert state["reserved_usd"] == 0.25 and state["spent_today_usd"] == 0.3
    row = _row(conn, r.id)
    ev = conn.execute("SELECT id FROM generation_events ORDER BY id DESC LIMIT 1").fetchone()
    assert (row["status"], row["release_reason"]) == ("released", "worker_lost")
    assert Decimal(str(row["actual_usd"])) == Decimal("0.3") and row["generation_event_id"] == ev["id"]
    # и снятие незакрытого после обработчика тоже ничего не вычитает
    assert gate.release_open() == 0 and _reserved(conn) == Decimal("0.25")
    assert _row(conn, keep.id)["status"] == "open"


def test_previous_day_orphan_released_without_touching_today(queue_env: Any, conn: Any) -> None:
    old_task = _running_task(conn)
    r_old = budget.TaskBudgetGate(task_id=old_task["id"]).reserve(3, provider="p")
    # полночь: резерв остался вчерашним, смена дня обнулила reserved_usd
    conn.execute("UPDATE budget_reservations SET business_day = business_day - 1 WHERE id = %s", (r_old.id,))
    conn.execute("UPDATE budget_state SET reserved_usd = 0")
    today = budget.TaskBudgetGate().reserve(2, provider="p")
    conn.execute("UPDATE tasks SET status = 'failed', finished_at = now() WHERE id = %s", (old_task["id"],))
    b = _reap()["budget_reservations"]
    assert b["released"] == 1 and b["subtracted_usd"] == 0.0 and b["items"][0]["today"] is False
    assert _reserved(conn) == 2 and _row(conn, today.id)["status"] == "open"


def test_reaper_lifts_auto_pause_caused_by_phantom_reserve(queue_env: Any, conn: Any) -> None:
    conn.execute("UPDATE budget_state SET daily_limit_usd = 15, spent_today_usd = 3")
    victim = _running_task(conn, worker="oom-killed")
    budget.TaskBudgetGate(task_id=victim["id"]).reserve(10, provider="p")
    from autoshorts.core.errors import ResourceDeferred

    with pytest.raises(ResourceDeferred):
        budget.TaskBudgetGate().reserve(4, provider="p")  # 3 + 10 + 4 > 15 → автопауза
    assert _state(conn)["paused"] is True
    alert = conn.execute("SELECT status FROM alerts WHERE dedup_key = 'budget_paused'").fetchone()
    assert alert is not None and alert["status"] == "open"
    conn.execute(
        "UPDATE tasks SET heartbeat_at = now() - interval '10 minutes' WHERE id = %s", (victim["id"],)
    )
    b = _reap()["budget_reservations"]
    s = _state(conn)
    assert b["unpaused"] is True and s["paused"] is False and s["pause_reason"] is None
    assert Decimal(str(s["reserved_usd"])) == 0
    alert = conn.execute("SELECT status FROM alerts WHERE dedup_key = 'budget_paused'").fetchone()
    assert alert["status"] != "open"
    budget.TaskBudgetGate().reserve(4, provider="p")  # снова можно тратить


def test_reaper_keeps_manual_pause(queue_env: Any, conn: Any) -> None:
    victim = _running_task(conn)
    budget.TaskBudgetGate(task_id=victim["id"]).reserve(1, provider="p")
    conn.execute("UPDATE budget_state SET paused = true, pause_reason = 'проверка оператора'")
    conn.execute("UPDATE tasks SET status = 'cancelled', finished_at = now() WHERE id = %s", (victim["id"],))
    b = _reap()["budget_reservations"]
    s = _state(conn)
    assert b["released"] == 1 and b["unpaused"] is False
    assert s["paused"] is True and s["pause_reason"] == "проверка оператора" and _reserved(conn) == 0


def _race_round(conn: Any, i: int, *, settle: bool) -> int:
    """Один раунд гонки: воркер закрывает резерв (settle или release), reaper — одновременно."""
    task = _running_task(conn, worker=f"racer-{i}")
    gate = budget.TaskBudgetGate(task_id=task["id"])
    r = gate.reserve(Decimal("0.5"), provider="p")
    # задачу уже сняли с воркера (он «пропал»), но он жив и досчитывает вызов
    conn.execute(
        "UPDATE tasks SET status = 'cancelled', finished_at = now(), locked_by = NULL WHERE id = %s",
        (task["id"],),
    )
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker_side() -> None:
        try:
            barrier.wait(5)
            if settle:
                gate.settle(r, Decimal("0.1"), step_name="script")
            else:
                gate.release(r)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def reaper_side() -> None:
        try:
            barrier.wait(5)
            with connection() as c:
                budget.reap_orphan_reservations(c)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker_side), threading.Thread(target=reaper_side)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert not errors, errors
    assert r.id is not None
    return r.id


def test_concurrent_settle_and_reaper_subtract_once(queue_env: Any, conn: Any) -> None:
    """Гонка воркера и reaper на одном резерве: сумма вычитается ровно один раз."""
    spent = Decimal("0")
    for i in range(12):
        rid = _race_round(conn, i, settle=bool(i % 2))
        if i % 2:
            spent += Decimal("0.1")
        assert _reserved(conn) == 0, f"round {i}"
        assert _row(conn, rid)["status"] in ("settled", "released")
    s = _state(conn)
    assert Decimal(str(s["spent_today_usd"])) == spent and Decimal(str(s["reserved_usd"])) == 0


def test_purge_closed_reservations(queue_env: Any, conn: Any) -> None:
    task = _running_task(conn)
    gate = budget.TaskBudgetGate(task_id=task["id"])
    old_closed = gate.reserve(0.1, provider="p")
    gate.release(old_closed)
    old_open = gate.reserve(0.2, provider="p")
    new_closed = gate.reserve(0.3, provider="p")
    gate.release(new_closed)
    conn.execute(
        "UPDATE budget_reservations SET created_at = now() - interval '30 days' WHERE id IN (%s, %s)",
        (old_closed.id, old_open.id),
    )
    report = _reap()["budget_reservations"]
    assert report["purged"] == 1
    ids = {r["id"] for r in conn.execute("SELECT id FROM budget_reservations").fetchall()}
    assert ids == {old_open.id, new_closed.id}


def test_reaper_survives_budget_failure(queue_env: Any, conn: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Сбой снятия резервов не мешает возврату задач: он в отчёте, задача всё равно requeued."""
    task = _running_task(conn)
    conn.execute("UPDATE tasks SET heartbeat_at = now() - interval '10 minutes' WHERE id = %s", (task["id"],))

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("view missing")

    monkeypatch.setattr(budget, "reap_orphan_reservations", boom)
    report = _reap()
    assert report["requeued"] == [task["id"]]
    assert "view missing" in report["budget_reservations"]["error"]


def test_killed_worker_process_reservation_released(queue_env: Any, conn: Any, spawn_worker: Any) -> None:
    """Критерий: воркер убит SIGKILL посреди платного шага → reaper возвращает задачу и снимает резерв."""
    t = enqueue_raw(conn, "wtest_budget_hold", payload={"amount": 0.7, "hold_sec": 60}, max_attempts=3)
    proc = spawn_worker("light", "--import", "tests.worker.test_budget_reservations_hold")
    deadline = time.monotonic() + 30
    row = None
    while time.monotonic() < deadline:
        row = conn.execute("SELECT * FROM budget_reservations WHERE task_id = %s", (t["id"],)).fetchone()
        if row is not None:
            break
        assert proc.poll() is None, "worker exited"
        time.sleep(0.1)
    assert row is not None and row["status"] == "open"
    running = task_row(conn, t["id"])
    assert running["status"] == "running" and row["worker_id"] == running["locked_by"]
    assert row["task_attempt"] == running["attempts"] == 1
    assert _reserved(conn) == Decimal("0.7")

    proc.kill()  # SIGKILL: ни finally, ни release_open не выполнятся
    proc.wait(10)
    assert _row(conn, row["id"])["status"] == "open"  # сам резерв никуда не делся
    time.sleep(1.2)
    from autoshorts.tasks.registry import get_task_spec, load_all

    load_all()
    report = get_task_spec("task_reaper").handler(_FakeCtx({"stale_after_sec": 1}))
    assert report["requeued"] == [t["id"]]
    assert report["budget_reservations"]["released"] == 1
    closed = _row(conn, row["id"])
    assert (closed["status"], closed["release_reason"]) == ("released", "worker_lost")
    assert _reserved(conn) == 0 and task_row(conn, t["id"])["status"] == "queued"
