"""
Агрегаты для веб-дашборда (ТЗ: "инструмент должен быть на сайте, чтобы следить, как всё работает").
Отдельный модуль от db.py — там транзакционные операции очереди, здесь read-only отчётность.
Все запросы читают ту же схему (scripts/init_db.sql), никакой отдельной аналитической БД не нужно
при текущем объёме (5-10 роликов/день, ТЗ §9: "единицы задач в час").
"""
from __future__ import annotations

from autoshorts.db import get_conn


def queue_by_status() -> list[dict]:
    """Сколько задач в очереди на каждой стадии — первое, что нужно увидеть при заходе."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM jobs
            GROUP BY status
            ORDER BY
                CASE status
                    WHEN 'queued' THEN 1 WHEN 'script_ready' THEN 2 WHEN 'voiced' THEN 3
                    WHEN 'rendered' THEN 4 WHEN 'qc_passed' THEN 5 WHEN 'qc_failed' THEN 6
                    WHEN 'pending_review' THEN 7 WHEN 'approved' THEN 8 WHEN 'rework' THEN 9
                    WHEN 'rejected' THEN 10 WHEN 'ready_to_publish' THEN 11 WHEN 'published' THEN 12
                    ELSE 99
                END
            """
        )
        return cur.fetchall()


def budget_status() -> dict:
    """Бюджетный предохранитель (ТЗ §9) — расход за сегодня против дневного лимита."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT daily_limit_usd, spent_today_usd, day, paused FROM budget_state WHERE id = 1")
        return cur.fetchone() or {}


def qc_pass_rate(last_n: int = 100) -> dict:
    """Процент прохождения preflight-QC на последних N задачах, дошедших до этой стадии."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE status IN ('qc_passed','pending_review','approved',
                                                    'ready_to_publish','published','rework')) AS passed,
                COUNT(*) FILTER (WHERE status = 'qc_failed') AS failed
            FROM (
                SELECT status FROM jobs
                WHERE status IN ('qc_passed','qc_failed','pending_review','approved',
                                  'ready_to_publish','published','rework','rejected')
                ORDER BY updated_at DESC LIMIT %s
            ) recent
            """,
            (last_n,),
        )
        row = cur.fetchone()
        passed, failed = row["passed"] or 0, row["failed"] or 0
        total = passed + failed
        return {"passed": passed, "failed": failed, "total": total,
                "pass_rate_pct": round(100 * passed / total, 1) if total else None}


def reject_reason_breakdown(last_n: int = 100) -> list[dict]:
    """Частотность причин отказа — ТЗ §10: 'без этого через полгода нельзя ответить, что ломается чаще'."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT reject_reason, COUNT(*) AS count
            FROM (SELECT reject_reason FROM jobs WHERE reject_reason IS NOT NULL
                  ORDER BY updated_at DESC LIMIT %s) recent
            GROUP BY reject_reason ORDER BY count DESC
            """,
            (last_n,),
        )
        return cur.fetchall()


def recent_jobs(limit: int = 30) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT j.id, j.video_uid, j.channel_id, j.niche, j.status, j.reject_reason,
                   j.created_at, j.updated_at,
                   COALESCE((SELECT SUM(cost_usd) FROM generation_events WHERE job_id = j.id), 0) AS cost_usd
            FROM jobs j
            ORDER BY j.updated_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()


def job_detail(job_id: int) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
        job = cur.fetchone()
        if not job:
            return None
        cur.execute(
            "SELECT * FROM generation_events WHERE job_id = %s ORDER BY created_at", (job_id,)
        )
        job["generation_events"] = cur.fetchall()
        cur.execute("SELECT * FROM publications WHERE job_id = %s", (job_id,))
        job["publications"] = cur.fetchall()
        return job


def cost_summary(days: int = 7) -> list[dict]:
    """Расход по дням за последнюю неделю — цена ОПУБЛИКОВАННОГО ролика важнее цены генерации (ТЗ §9)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT DATE(created_at) AS day, COUNT(DISTINCT job_id) AS jobs_touched,
                   SUM(cost_usd) AS total_cost_usd
            FROM generation_events
            WHERE created_at >= now() - (%s || ' days')::interval
            GROUP BY DATE(created_at)
            ORDER BY day DESC
            """,
            (days,),
        )
        return cur.fetchall()


def channels_overview() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.niche, c.platform, c.cluster,
                   COUNT(j.id) AS total_jobs,
                   COUNT(j.id) FILTER (WHERE j.status = 'published') AS published_count,
                   COUNT(p.id) AS publications_count
            FROM channels c
            LEFT JOIN jobs j ON j.channel_id = c.id
            LEFT JOIN publications p ON p.channel_id = c.id
            GROUP BY c.id, c.niche, c.platform, c.cluster
            ORDER BY c.id
            """
        )
        return cur.fetchall()


def recent_publications(limit: int = 30) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.*, j.niche, j.video_uid
            FROM publications p
            JOIN jobs j ON j.id = p.job_id
            ORDER BY p.published_at DESC NULLS LAST, p.created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()
