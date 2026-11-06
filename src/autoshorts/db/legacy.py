"""
Слой БД. Очередь на SELECT ... FOR UPDATE SKIP LOCKED (ТЗ §9) —
никакого Celery/Temporal при единицах задач в час.

LEGACY (M1-A): бывший модуль `src/autoshorts/db.py`, перенесён сюда без изменений логики,
когда `autoshorts.db` стал пакетом. Старый код обращается к нему как раньше
(`from autoshorts import db; db.enqueue_job(...)`) — `db/__init__.py` пробрасывает имена сюда.
Работает только со схемой до миграций 0003/0004 (колонка `jobs.niche`); новый код идёт через
`autoshorts.db.pool` и `repos/*`. Удаляется вместе со старым конвейером (M7, §3.2).
"""
# ruff: noqa: E501, UP035  (перенесённый код оставлен байт-в-байт, чтобы история читалась)
from __future__ import annotations

import contextlib
import json
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from autoshorts.config import SETTINGS


@contextlib.contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(SETTINGS.database_url, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def enqueue_job(video_uid: str, channel_id: str, niche: str, template_version: str) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO jobs (video_uid, channel_id, niche, template_version, status)
            VALUES (%s, %s, %s, %s, 'queued')
            RETURNING id
            """,
            (video_uid, channel_id, niche, template_version),
        )
        return cur.fetchone()["id"]


def claim_next_job(worker_id: str, from_status: str) -> dict[str, Any] | None:
    """Атомарно забирает одну задачу в заданном статусе. SKIP LOCKED — параллельные воркеры не толкаются."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id FROM jobs
            WHERE status = %s
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            (from_status,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        job_id = row["id"]
        cur.execute(
            """
            UPDATE jobs SET locked_by = %s, locked_at = now(), updated_at = now()
            WHERE id = %s
            RETURNING *
            """,
            (worker_id, job_id),
        )
        return cur.fetchone()


def increment_attempt_count(job_id: int) -> int:
    """Возвращает attempt_count ПОСЛЕ инкремента — используется process_one_cycle для лимита ретраев."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET attempt_count = attempt_count + 1 WHERE id = %s RETURNING attempt_count",
            (job_id,),
        )
        return cur.fetchone()["attempt_count"]


def update_job_status(job_id: int, status: str, stage_payload_patch: dict | None = None,
                       reject_reason: str | None = None) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        if stage_payload_patch:
            cur.execute(
                """
                UPDATE jobs
                SET status = %s,
                    stage_payload = stage_payload || %s::jsonb,
                    reject_reason = COALESCE(%s, reject_reason),
                    locked_by = NULL, locked_at = NULL,
                    updated_at = now()
                WHERE id = %s
                """,
                (status, json.dumps(stage_payload_patch), reject_reason, job_id),
            )
        else:
            cur.execute(
                """
                UPDATE jobs
                SET status = %s, reject_reason = COALESCE(%s, reject_reason),
                    locked_by = NULL, locked_at = NULL, updated_at = now()
                WHERE id = %s
                """,
                (status, reject_reason, job_id),
            )


def record_generation_event(job_id: int, step_name: str, provider: str, model: str | None,
                             units: float | None, unit_price_usd: float | None, cost_usd: float,
                             latency_ms: int | None = None, http_status: int | None = None) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO generation_events
                (job_id, step_name, provider, model, units, unit_price_usd, cost_usd, latency_ms, http_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (job_id, step_name, provider, model, units, unit_price_usd, cost_usd, latency_ms, http_status),
        )


def get_published_video_cost(job_id: int) -> float:
    """Цена ОПУБЛИКОВАННОГО ролика — единственная честная метрика (ТЗ §9)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COALESCE(SUM(cost_usd), 0) AS total FROM generation_events WHERE job_id = %s", (job_id,))
        return float(cur.fetchone()["total"])


def claim_jobs_for_review(limit: int = 10) -> list[dict]:
    """
    Задачи, дошедшие до pending_review, но карточка в Telegram ещё не отправлена.

    ВАЖНО: НЕ проставляет review_sent_at здесь — только читает кандидатов. Отметка ставится
    отдельным вызовом mark_review_sent() ПОСЛЕ реально успешной отправки (см. review_poll_loop
    в bot/telegram_bot.py). Если проставлять отметку до отправки и сама отправка упадёт
    (например, TELEGRAM_REVIEW_CHAT_ID ещё не настроен на первом запуске), задача навсегда
    останется помеченной 'отправлено', хотя карточка так и не ушла — поймано при подготовке
    деплоя, до того как это стало реальной проблемой в проде.

    Продакшен разворачивается ОДНИМ инстансом бота (systemd, не реплицируется), поэтому
    гонка при параллельном опросе теоретическая, не практическая — жертвовать надёжностью
    ретраев ради защиты от несуществующего сценария не имеет смысла.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'pending_review' AND review_sent_at IS NULL
            ORDER BY created_at
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()


def mark_review_sent(job_id: int) -> None:
    """Вызывать ТОЛЬКО после успешной отправки карточки в Telegram — см. claim_jobs_for_review."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE jobs SET review_sent_at = now() WHERE id = %s", (job_id,))


# Бизнес-день бюджета (ARCHITECTURE §3.2, §4: budget_state.day — бизнес-день Europe/Moscow).
# Считается часами БД, а не date.today() процесса: TZ машины воркера на стыке суток даёт другой день,
# и сброс spent_today_usd срабатывал не тогда или не срабатывал вовсе.
BUSINESS_TIMEZONE = "Europe/Moscow"


def check_and_reserve_budget(estimated_cost_usd: float) -> bool:
    """Бюджетный предохранитель — читается ПЕРЕД каждым платным вызовом (ТЗ §9)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT daily_limit_usd, spent_today_usd, paused, day,
                   (now() AT TIME ZONE CAST(%(tz)s AS text))::date AS business_day
            FROM budget_state
            WHERE id = 1
            FOR UPDATE
            """,
            {"tz": BUSINESS_TIMEZONE},
        )
        state = cur.fetchone()
        if state is None:
            raise RuntimeError("В budget_state нет строки id=1 — бюджетный предохранитель не настроен")

        if state["day"] != state["business_day"]:
            cur.execute(
                "UPDATE budget_state SET spent_today_usd = 0, paused = false, day = %(day)s WHERE id = 1",
                {"day": state["business_day"]},
            )
            state["spent_today_usd"] = 0
            state["paused"] = False

        if state["paused"]:
            return False

        projected = float(state["spent_today_usd"]) + estimated_cost_usd
        if projected > float(state["daily_limit_usd"]):
            cur.execute("UPDATE budget_state SET paused = true WHERE id = 1")
            return False

        cur.execute(
            "UPDATE budget_state SET spent_today_usd = spent_today_usd + %(cost)s WHERE id = 1",
            {"cost": estimated_cost_usd},
        )
        return True
