"""SQL коллекторов метрик (миграции 0006, 0014, 0017; docs/ARCHITECTURE.md §6.5). Владелец — M4-A.

Только SQL, соединение передаёт вызывающий:

* выборка публикаций к опросу с «арендой» (`claim_due`): `FOR UPDATE SKIP LOCKED` + сдвиг
  `next_poll_at` на время аренды — параллельный прогон (плановый и внеочередной) не возьмёт ту же
  публикацию; упавший прогон вернёт её в опрос сам, когда аренда истечёт;
* снимки `metrics_snapshots`, поля метрик публикации (`latest_*`, `views_24h…30d`, лестница, ошибки,
  ожидание ручного ввода), пересчёт возраста снимков при уточнении `published_at`;
* состояние коллекторов `collector_state` и счётчики публикаций по коллектору и «ведру» ключа;
* очередь ручного ввода (`manual_due_at <= now()`), снимки каналов `channel_snapshots`.

«Ведро» (`quota_bucket`) — куст со своим ключом YouTube (`clusters.youtube_api_key_secret`) или
`default` для общего ключа: по нему считаются квота и состояние коллектора `yt_data`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

Row = dict[str, Any]

__all__ = [
    "PUBLICATION_METRIC_COLUMNS",
    "channels_for_stats",
    "claim_due",
    "collector_counts",
    "delete_snapshot",
    "freshness_rows",
    "get_snapshot",
    "insert_channel_incident",
    "insert_channel_snapshot",
    "insert_snapshot",
    "latest_channel_snapshots",
    "latest_sources",
    "list_states",
    "manual_counts",
    "manual_due_page",
    "poll_row",
    "publication_for_manual",
    "recompute_ages",
    "release",
    "snapshots",
    "state_failure",
    "state_ok",
    "state_set",
    "update_channel_subscribers",
    "update_publication",
    "youtube_buckets",
]

BUCKET_SQL = "CASE WHEN cl.youtube_api_key_secret IS NOT NULL THEN cl.id ELSE 'default' END"
_POLL_COLUMNS = f"""
    p.id, p.job_id, p.channel_id, p.platform, p.platform_video_id, p.url, p.canonical_url,
    p.status, p.status_reason, p.has_claim, p.verify_status, p.published_at, p.created_at,
    p.metrics_collector, p.poll_step, p.poll_failures, p.last_poll_error, p.manual_due_at,
    p.next_poll_at, p.latest_views, p.deleted_at,
    c.cluster, c.platform_channel_ref, c.title AS channel_title,
    cl.youtube_api_key_secret,
    CASE WHEN p.platform = 'youtube' THEN {BUCKET_SQL} ELSE 'default' END AS quota_bucket,
    j.video_uid
"""
_POLL_FROM = """
    FROM publications p
    JOIN channels c ON c.id = p.channel_id
    LEFT JOIN clusters cl ON cl.id = c.cluster
    JOIN jobs j ON j.id = p.job_id
"""
_SNAPSHOT_COLUMNS = (
    "id, publication_id, captured_at, age_hours, views, likes, comments, shares, engaged_views, source,"
    " created_by, note"
)

PUBLICATION_METRIC_COLUMNS = frozenset(
    {
        "latest_views",
        "latest_likes",
        "latest_comments",
        "latest_engaged_views",
        "last_polled_at",
        "views_24h",
        "views_72h",
        "views_7d",
        "views_14d",
        "views_30d",
        "next_poll_at",
        "poll_step",
        "poll_failures",
        "last_poll_error",
        "manual_due_at",
        "metrics_collector",
        "status",
        "status_reason",
        "has_claim",
        "published_at",
        "verify_status",
        "verify_detail",
        "verified_at",
    }
)

# ---------------------------------------------------------------- опрос


def claim_due(
    conn: Any,
    *,
    lease_sec: int,
    limit: int,
    platform: str | None = None,
    publication_ids: Sequence[int] | None = None,
) -> list[Row]:
    """Взять публикации к опросу и сдвинуть им `next_poll_at` на `lease_sec` (аренда).

    Без `publication_ids` — созревшие (`next_poll_at <= now()`), самые давние первыми; с ними —
    именно эти, независимо от расписания (внеочередной опрос). `due_at` — прежний `next_poll_at`.
    """
    params: dict[str, Any] = {
        "lease": int(lease_sec),
        "limit": int(limit),
        "platform": platform,
        "ids": list(publication_ids) if publication_ids is not None else None,
    }
    if publication_ids is not None:
        pick = """
            SELECT p.id, p.next_poll_at AS due_at FROM publications p
            WHERE p.id = ANY(CAST(%(ids)s AS bigint[]))
              AND p.deleted_at IS NULL AND p.status <> 'deleted_by_us' AND p.platform_video_id IS NOT NULL
              AND (CAST(%(platform)s AS text) IS NULL OR p.platform = CAST(%(platform)s AS text))
            ORDER BY p.id
            LIMIT CAST(%(limit)s AS int)
            FOR UPDATE OF p SKIP LOCKED
        """
    else:
        pick = """
            SELECT p.id, p.next_poll_at AS due_at FROM publications p
            WHERE p.deleted_at IS NULL AND p.status <> 'deleted_by_us' AND p.platform_video_id IS NOT NULL
              AND p.next_poll_at IS NOT NULL AND p.next_poll_at <= now()
              AND (CAST(%(platform)s AS text) IS NULL OR p.platform = CAST(%(platform)s AS text))
            ORDER BY p.next_poll_at, p.id
            LIMIT CAST(%(limit)s AS int)
            FOR UPDATE OF p SKIP LOCKED
        """
    return conn.execute(
        f"""
        WITH due AS ({pick}),
        leased AS (
            UPDATE publications p
            SET next_poll_at = now() + make_interval(secs => CAST(%(lease)s AS int))
            FROM due WHERE p.id = due.id
            RETURNING p.id, due.due_at
        )
        SELECT {_POLL_COLUMNS}, leased.due_at
        {_POLL_FROM}
        JOIN leased ON leased.id = p.id
        ORDER BY p.id
        """,
        params,
    ).fetchall()


def release(conn: Any, publication_id: int, next_poll_at: datetime | None) -> None:
    """Вернуть публикацию в расписание (не опрошена: отмена, лимит прогона)."""
    conn.execute(
        "UPDATE publications SET next_poll_at = %(at)s WHERE id = %(id)s",
        {"id": publication_id, "at": next_poll_at},
    )


def poll_row(
    conn: Any, publication_id: int, *, include_deleted: bool = False, for_update: bool = False
) -> Row | None:
    deleted = "" if include_deleted else " AND p.deleted_at IS NULL"
    lock = " FOR UPDATE OF p" if for_update else ""
    return conn.execute(
        f"SELECT {_POLL_COLUMNS} {_POLL_FROM} WHERE p.id = %(id)s{deleted}{lock}", {"id": publication_id}
    ).fetchone()


def publication_for_manual(conn: Any, publication_id: int) -> Row | None:
    """Публикация под ручной ввод (с удалёнными — чтобы ответить 422 publication_deleted)."""
    return poll_row(conn, publication_id, include_deleted=True, for_update=True)


def update_publication(conn: Any, publication_id: int, fields: dict[str, Any]) -> None:
    bad = set(fields) - PUBLICATION_METRIC_COLUMNS
    if bad:
        raise ValueError(f"publications: columns not updatable by collectors: {sorted(bad)}")
    if not fields:
        return
    sets = sql.SQL(", ").join(
        sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder(k)) for k in sorted(fields)
    )
    conn.execute(
        sql.SQL("UPDATE publications SET {sets} WHERE id = %(_id)s").format(sets=sets),
        {**fields, "_id": publication_id},
    )


def recompute_ages(conn: Any, publication_id: int, published_at: datetime) -> None:
    """После уточнения `published_at` — пересчитать `age_hours` всех снимков публикации."""
    conn.execute(
        """
        UPDATE metrics_snapshots
        SET age_hours = round(
              CAST(GREATEST(0, extract(epoch FROM captured_at - %(pub)s) / 3600.0) AS numeric), 3)
        WHERE publication_id = %(id)s
        """,
        {"id": publication_id, "pub": published_at},
    )


# ---------------------------------------------------------------- снимки


def insert_snapshot(
    conn: Any,
    *,
    publication_id: int,
    captured_at: datetime,
    age_hours: float | None,
    views: int | None,
    likes: int | None = None,
    comments: int | None = None,
    shares: int | None = None,
    engaged_views: int | None = None,
    source: str,
    created_by: int | None = None,
    note: str | None = None,
    raw: dict[str, Any] | None = None,
) -> Row:
    return conn.execute(
        f"""
        INSERT INTO metrics_snapshots (publication_id, captured_at, age_hours, views, likes, comments, shares,
                                       engaged_views, source, created_by, note, raw)
        VALUES (%(pid)s, %(at)s, %(age)s, %(views)s, %(likes)s, %(comments)s, %(shares)s, %(engaged)s,
                %(source)s, %(by)s, %(note)s, %(raw)s)
        RETURNING {_SNAPSHOT_COLUMNS}
        """,
        {
            "pid": publication_id,
            "at": captured_at,
            "age": round(age_hours, 3) if age_hours is not None else None,
            "views": views,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "engaged": engaged_views,
            "source": source,
            "by": created_by,
            "note": note,
            "raw": Jsonb(raw) if raw is not None else None,
        },
    ).fetchone()


def snapshots(conn: Any, publication_id: int) -> list[Row]:
    return conn.execute(
        f"SELECT {_SNAPSHOT_COLUMNS} FROM metrics_snapshots WHERE publication_id = %(id)s"
        " ORDER BY captured_at, id",
        {"id": publication_id},
    ).fetchall()


def latest_sources(conn: Any, publication_ids: Sequence[int]) -> list[Row]:
    """Источник последнего снимка с просмотрами по публикациям (`PublicationLatest.source`)."""
    if not publication_ids:
        return []
    return conn.execute(
        """
        SELECT DISTINCT ON (publication_id) publication_id, source, captured_at
        FROM metrics_snapshots
        WHERE publication_id = ANY(CAST(%(ids)s AS bigint[])) AND views IS NOT NULL
        ORDER BY publication_id, captured_at DESC, id DESC
        """,
        {"ids": list(publication_ids)},
    ).fetchall()


def get_snapshot(conn: Any, snapshot_id: int, *, for_update: bool = False) -> Row | None:
    return conn.execute(
        f"SELECT {_SNAPSHOT_COLUMNS} FROM metrics_snapshots WHERE id = %(id)s"
        + (" FOR UPDATE" if for_update else ""),
        {"id": snapshot_id},
    ).fetchone()


def delete_snapshot(conn: Any, snapshot_id: int) -> bool:
    return (
        conn.execute(
            "DELETE FROM metrics_snapshots WHERE id = %(id)s RETURNING id", {"id": snapshot_id}
        ).fetchone()
        is not None
    )


# ---------------------------------------------------------------- состояние коллекторов

_STATE_COLUMNS = (
    "collector, cluster_id, state, reason, message, last_run_at, last_ok_at, last_error_at, last_error,"
    " consecutive_failures, paused_until, updated_at"
)


def list_states(conn: Any) -> list[Row]:
    return conn.execute(
        f"SELECT {_STATE_COLUMNS} FROM collector_state ORDER BY collector, cluster_id"
    ).fetchall()


def state_ok(
    conn: Any,
    collector: str,
    cluster_id: str,
    *,
    state: str = "ok",
    reason: str | None = None,
    message: str | None = None,
) -> Row:
    """Успешный прогон: `last_ok_at`, счётчик ошибок в ноль, пауза снята."""
    return conn.execute(
        f"""
        INSERT INTO collector_state (collector, cluster_id, state, reason, message, last_run_at, last_ok_at,
                                     consecutive_failures, paused_until, updated_at)
        VALUES (%(c)s, %(cl)s, %(state)s, %(reason)s, %(msg)s, now(), now(), 0, NULL, now())
        ON CONFLICT (collector, cluster_id) DO UPDATE
          SET state = EXCLUDED.state, reason = EXCLUDED.reason, message = EXCLUDED.message,
              last_run_at = now(), last_ok_at = now(), consecutive_failures = 0, paused_until = NULL,
              updated_at = now()
        RETURNING {_STATE_COLUMNS}
        """,
        {
            "c": collector,
            "cl": cluster_id,
            "state": state,
            "reason": reason,
            "msg": (message or None) and message[:500],
        },
    ).fetchone()


def state_set(
    conn: Any,
    collector: str,
    cluster_id: str,
    *,
    state: str,
    reason: str | None,
    message: str | None,
    paused_until: datetime | None = None,
    touch_run: bool = True,
) -> Row:
    """Состояние без исхода прогона (выключен, пауза квоты, ждёт работы)."""
    return conn.execute(
        f"""
        INSERT INTO collector_state (collector, cluster_id, state, reason, message, last_run_at, paused_until,
                                     updated_at)
        VALUES (%(c)s, %(cl)s, %(state)s, %(reason)s, %(msg)s,
                CASE WHEN %(touch)s THEN now() END, %(until)s, now())
        ON CONFLICT (collector, cluster_id) DO UPDATE
          SET state = EXCLUDED.state, reason = EXCLUDED.reason, message = EXCLUDED.message,
              last_run_at = COALESCE(EXCLUDED.last_run_at, collector_state.last_run_at),
              paused_until = EXCLUDED.paused_until, updated_at = now()
        RETURNING {_STATE_COLUMNS}
        """,
        {
            "c": collector,
            "cl": cluster_id,
            "state": state,
            "reason": reason,
            "msg": (message or None) and message[:500],
            "until": paused_until,
            "touch": bool(touch_run),
        },
    ).fetchone()


def state_failure(
    conn: Any,
    collector: str,
    cluster_id: str,
    *,
    reason: str,
    message: str | None,
    error: str,
    failing_after: int,
) -> Row:
    """Неудачный прогон: +1 к ошибкам подряд; `failing` с `failing_after`-й, до того `degraded`."""
    return conn.execute(
        f"""
        INSERT INTO collector_state (collector, cluster_id, state, reason, message, last_run_at,
                                     last_error_at, last_error, consecutive_failures, updated_at)
        VALUES (%(c)s, %(cl)s,
                CASE WHEN 1 >= CAST(%(after)s AS int) THEN 'failing' ELSE 'degraded' END,
                %(reason)s, %(msg)s, now(), now(), %(err)s, 1, now())
        ON CONFLICT (collector, cluster_id) DO UPDATE
          SET consecutive_failures = collector_state.consecutive_failures + 1,
              state = CASE WHEN collector_state.consecutive_failures + 1 >= CAST(%(after)s AS int)
                           THEN 'failing' ELSE 'degraded' END,
              reason = EXCLUDED.reason, message = EXCLUDED.message, last_run_at = now(),
              last_error_at = now(), last_error = EXCLUDED.last_error, paused_until = NULL, updated_at = now()
        RETURNING {_STATE_COLUMNS}
        """,
        {
            "c": collector,
            "cl": cluster_id,
            "reason": reason,
            "msg": (message or None) and message[:500],
            "err": error[:2000],
            "after": int(failing_after),
        },
    ).fetchone()


def collector_counts(conn: Any) -> list[Row]:
    """Публикации по (коллектор, ведро): всего и с наступившим `next_poll_at`."""
    return conn.execute(
        f"""
        SELECT COALESCE(p.metrics_collector,
                        CASE p.platform WHEN 'youtube' THEN 'yt_data' ELSE 'ytdlp' END) AS collector,
               CASE WHEN p.platform = 'youtube' THEN {BUCKET_SQL} ELSE 'default' END AS bucket,
               count(*) AS total,
               count(*) FILTER (WHERE p.next_poll_at IS NOT NULL AND p.next_poll_at <= now()) AS due
        FROM publications p
        JOIN channels c ON c.id = p.channel_id
        LEFT JOIN clusters cl ON cl.id = c.cluster
        WHERE p.deleted_at IS NULL AND p.status <> 'deleted_by_us' AND p.platform_video_id IS NOT NULL
        GROUP BY 1, 2
        """
    ).fetchall()


def manual_counts(conn: Any) -> Row:
    return conn.execute(
        """
        SELECT count(*) FILTER (WHERE manual_due_at IS NOT NULL OR metrics_collector = 'manual') AS total,
               count(*) FILTER (WHERE manual_due_at IS NOT NULL AND manual_due_at <= now()) AS due
        FROM publications
        WHERE deleted_at IS NULL AND status NOT IN ('deleted_by_us', 'removed_by_platform')
        """
    ).fetchone()


def youtube_buckets(conn: Any) -> list[Row]:
    """Ведра ключей YouTube, в которых есть каналы: (bucket, key_secret — имя секрета куста или NULL)."""
    return conn.execute(
        f"""
        SELECT DISTINCT {BUCKET_SQL} AS bucket, cl.youtube_api_key_secret AS key_secret
        FROM channels c LEFT JOIN clusters cl ON cl.id = c.cluster
        WHERE c.platform = 'youtube'
        ORDER BY 1
        """
    ).fetchall()


# ---------------------------------------------------------------- свежесть и ручной ввод


def freshness_rows(conn: Any, publication_ids: Sequence[int]) -> list[Row]:
    if not publication_ids:
        return []
    return conn.execute(
        f"""
        SELECT p.id, p.platform, p.metrics_collector, p.next_poll_at, p.poll_step, p.poll_failures,
               p.last_poll_error, p.manual_due_at, p.status,
               CASE WHEN p.platform = 'youtube' THEN {BUCKET_SQL} ELSE 'default' END AS bucket,
               s.last_captured_at, s.last_auto_at
        FROM publications p
        JOIN channels c ON c.id = p.channel_id
        LEFT JOIN clusters cl ON cl.id = c.cluster
        LEFT JOIN LATERAL (
            SELECT max(ms.captured_at) AS last_captured_at,
                   max(ms.captured_at) FILTER (WHERE ms.source <> 'manual') AS last_auto_at
            FROM metrics_snapshots ms WHERE ms.publication_id = p.id
        ) s ON true
        WHERE p.id = ANY(CAST(%(ids)s AS bigint[]))
        """,
        {"ids": list(publication_ids)},
    ).fetchall()


def manual_due_page(
    conn: Any,
    *,
    platforms: Sequence[str] | None,
    channel_ids: Sequence[str] | None,
    limit: int,
    offset: int,
) -> tuple[list[Row], int]:
    where = """
        p.deleted_at IS NULL AND p.manual_due_at IS NOT NULL AND p.manual_due_at <= now()
        AND p.status NOT IN ('deleted_by_us', 'removed_by_platform')
        AND (CAST(%(platforms)s AS text[]) IS NULL OR p.platform = ANY(CAST(%(platforms)s AS text[])))
        AND (CAST(%(channels)s AS text[]) IS NULL OR p.channel_id = ANY(CAST(%(channels)s AS text[])))
    """
    params = {
        "platforms": list(platforms) if platforms else None,
        "channels": list(channel_ids) if channel_ids else None,
        "limit": int(limit),
        "offset": int(offset),
    }
    total = conn.execute(f"SELECT count(*) AS n FROM publications p WHERE {where}", params).fetchone()["n"]
    rows = conn.execute(
        f"""
        SELECT p.id AS publication_id, p.job_id, j.video_uid, p.platform,
               COALESCE(p.canonical_url, p.url) AS url, p.channel_id, c.title AS channel_title,
               p.status, p.published_at, p.created_at, p.manual_due_at, p.metrics_collector,
               CASE WHEN p.platform = 'youtube' THEN {BUCKET_SQL} ELSE 'default' END AS bucket,
               ls.captured_at AS last_captured_at, ls.views AS last_views
        FROM publications p
        JOIN jobs j ON j.id = p.job_id
        JOIN channels c ON c.id = p.channel_id
        LEFT JOIN clusters cl ON cl.id = c.cluster
        LEFT JOIN LATERAL (
            SELECT s.captured_at, s.views FROM metrics_snapshots s
            WHERE s.publication_id = p.id ORDER BY s.captured_at DESC, s.id DESC LIMIT 1
        ) ls ON true
        WHERE {where}
        ORDER BY p.manual_due_at, p.id
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    ).fetchall()
    return rows, int(total)


# ---------------------------------------------------------------- каналы


def channels_for_stats(conn: Any, platform: str) -> list[Row]:
    """Каналы площадки с ID площадки (кроме retired/terminated) для задачи `channel_stats`."""
    return conn.execute(
        f"""
        SELECT c.id, c.platform, c.platform_channel_ref, c.status, c.cluster, c.subscribers,
               cl.youtube_api_key_secret,
               CASE WHEN c.platform = 'youtube' THEN {BUCKET_SQL} ELSE 'default' END AS bucket
        FROM channels c LEFT JOIN clusters cl ON cl.id = c.cluster
        WHERE c.platform = %(platform)s AND c.platform_channel_ref IS NOT NULL
          AND c.status NOT IN ('retired', 'terminated')
        ORDER BY c.id
        """,
        {"platform": platform},
    ).fetchall()


def insert_channel_snapshot(
    conn: Any,
    *,
    channel_id: str,
    subscribers: int | None,
    total_views: int | None,
    video_count: int | None,
    source: str,
) -> Row | None:
    return conn.execute(
        """
        INSERT INTO channel_snapshots (channel_id, captured_at, subscribers, total_views, video_count, source)
        VALUES (%(id)s, now(), %(subs)s, %(views)s, %(videos)s, %(source)s)
        ON CONFLICT (channel_id, captured_at) DO NOTHING
        RETURNING channel_id, captured_at, subscribers, total_views, video_count, source
        """,
        {
            "id": channel_id,
            "subs": subscribers,
            "views": total_views,
            "videos": video_count,
            "source": source,
        },
    ).fetchone()


def update_channel_subscribers(conn: Any, channel_id: str, subscribers: int) -> None:
    """Кеш `channels.subscribers` (без `updated_at`: это не правка карточки человеком)."""
    conn.execute(
        """
        UPDATE channels SET subscribers = CAST(%(s)s AS bigint)
        WHERE id = %(id)s AND subscribers IS DISTINCT FROM CAST(%(s)s AS bigint)
        """,
        {"id": channel_id, "s": subscribers},
    )


def latest_channel_snapshots(conn: Any, channel_ids: Sequence[str]) -> list[Row]:
    if not channel_ids:
        return []
    return conn.execute(
        """
        SELECT DISTINCT ON (channel_id) channel_id, captured_at, subscribers, total_views, video_count, source
        FROM channel_snapshots WHERE channel_id = ANY(CAST(%(ids)s AS text[]))
        ORDER BY channel_id, captured_at DESC
        """,
        {"ids": list(channel_ids)},
    ).fetchall()


def insert_channel_incident(
    conn: Any, *, channel_id: str, kind: str, details: dict[str, Any], note: str | None = None
) -> int:
    row = conn.execute(
        """
        INSERT INTO channel_incidents (channel_id, kind, details, note)
        VALUES (%(id)s, %(kind)s, %(details)s, %(note)s) RETURNING id
        """,
        {"id": channel_id, "kind": kind, "details": Jsonb(details), "note": note},
    ).fetchone()
    return int(row["id"])
