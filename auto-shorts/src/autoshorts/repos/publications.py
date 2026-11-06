"""SQL публикаций `publications` и их метрик (docs/ARCHITECTURE.md §4.2, §5.4). Владелец — M3-A.

Только SQL. Одна площадка — один ролик в одном канале: `uq_pub_platform_video` (ID площадки) и
`uq_pub_job_platform` (ролик на площадке) среди неудалённых. Удаление ошибочной регистрации —
мягкое (`deleted_at`), алиас `attribution_tag` при этом освобождается.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from psycopg import sql

Row = dict[str, Any]

PUB_COLUMNS = (
    "id",
    "job_id",
    "channel_id",
    "platform",
    "platform_video_id",
    "master_file_path",
    "title",
    "description",
    "hashtags",
    "published_at",
    "attribution_tag",
    "created_at",
    "package_id",
    "url",
    "canonical_url",
    "platform_kind",
    "status",
    "status_reason",
    "has_claim",
    "verify_status",
    "verify_detail",
    "verified_at",
    "product_id",
    "campaign_id",
    "banner_variant_id",
    "registered_by",
    "next_poll_at",
    "poll_step",
    "last_polled_at",
    "latest_views",
    "latest_likes",
    "latest_comments",
    "latest_engaged_views",
    "retention_3s",
    "avg_pct_viewed",
    "views_24h",
    "views_72h",
    "views_7d",
    "deleted_at",
)
_PUB = ", ".join(f"p.{c}" for c in PUB_COLUMNS)
_SELECT = f"""
    SELECT {_PUB},
           j.video_uid, j.kind AS job_kind, j.niche_id, j.cost_usd AS job_cost_usd, j.originality_verdict,
           j.duration_sec,
           c.title AS channel_title,
           bv.code AS variant_code, bv.name AS variant_name
    FROM publications p
    JOIN jobs j ON j.id = p.job_id
    JOIN channels c ON c.id = p.channel_id
    LEFT JOIN banner_variants bv ON bv.id = p.banner_variant_id
"""
INSERTABLE = frozenset(PUB_COLUMNS) - {"id", "created_at"}
UPDATABLE = frozenset(
    {
        "status",
        "status_reason",
        "has_claim",
        "published_at",
        "verify_status",
        "verify_detail",
        "verified_at",
        "next_poll_at",
        "deleted_at",
        "attribution_tag",
    }
)
SORTS = {
    "published_at": "p.published_at",
    "created_at": "p.created_at",
    "latest_views": "p.latest_views",
    "views_72h": "p.views_72h",
    "id": "p.id",
}


def get(
    conn: Any, publication_id: int, *, for_update: bool = False, include_deleted: bool = False
) -> Row | None:
    deleted = "" if include_deleted else " AND p.deleted_at IS NULL"
    lock = " FOR UPDATE OF p" if for_update else ""
    return conn.execute(f"{_SELECT} WHERE p.id = %(id)s{deleted}{lock}", {"id": publication_id}).fetchone()


def find_by_video(conn: Any, platform: str, platform_video_id: str) -> Row | None:
    return conn.execute(
        """
        SELECT id, job_id, channel_id, package_id FROM publications
        WHERE platform = %(p)s AND platform_video_id = %(v)s AND deleted_at IS NULL
        """,
        {"p": platform, "v": platform_video_id},
    ).fetchone()


def live_for_jobs_on_platform(conn: Any, job_ids: Sequence[int], platform: str) -> list[Row]:
    if not job_ids:
        return []
    return conn.execute(
        """
        SELECT id, job_id, channel_id, platform_video_id, url FROM publications
        WHERE job_id = ANY(%(ids)s) AND platform = %(p)s AND deleted_at IS NULL
        ORDER BY id
        """,
        {"ids": list(job_ids), "p": platform},
    ).fetchall()


def live_for_job(conn: Any, job_id: int) -> list[Row]:
    return conn.execute(
        "SELECT id, platform, channel_id FROM publications WHERE job_id = %(id)s AND deleted_at IS NULL",
        {"id": job_id},
    ).fetchall()


def job_family(conn: Any, job_id: int, *, depth: int = 10) -> list[int]:
    """Ролик и его родня по `parent_job_id` (клоны, адаптации) в обе стороны — «тот же ролик»."""
    rows = conn.execute(
        """
        WITH RECURSIVE up(id, parent_job_id, d) AS (
            SELECT id, parent_job_id, 0 FROM jobs WHERE id = %(id)s
          UNION
            SELECT j.id, j.parent_job_id, up.d + 1 FROM jobs j JOIN up ON j.id = up.parent_job_id
            WHERE up.d < %(depth)s
        ),
        root AS (SELECT id FROM up ORDER BY d DESC LIMIT 1),
        down(id, d) AS (
            SELECT id, 0 FROM root
          UNION
            SELECT j.id, down.d + 1 FROM jobs j JOIN down ON j.parent_job_id = down.id
            WHERE down.d < %(depth)s
        )
        SELECT DISTINCT id FROM down
        """,
        {"id": job_id, "depth": int(depth)},
    ).fetchall()
    ids = {int(r["id"]) for r in rows}
    ids.add(int(job_id))
    return sorted(ids)


def same_candidate_jobs(conn: Any, job_id: int) -> list[int]:
    """Нарезка: ролики из того же кандидата (тот же момент источника)."""
    rows = conn.execute(
        """
        SELECT o.id FROM jobs j JOIN jobs o ON o.clip_candidate_id = j.clip_candidate_id
        WHERE j.id = %(id)s AND j.clip_candidate_id IS NOT NULL AND o.deleted_at IS NULL
        """,
        {"id": job_id},
    ).fetchall()
    return [int(r["id"]) for r in rows]


def insert(conn: Any, fields: dict[str, Any]) -> Row:
    bad = set(fields) - INSERTABLE
    if bad:
        raise ValueError(f"publications: columns not insertable: {sorted(bad)}")
    names = sorted(fields)
    params = {n: (list(fields[n]) if n == "hashtags" and fields[n] is not None else fields[n]) for n in names}
    row = conn.execute(
        sql.SQL("INSERT INTO publications ({cols}) VALUES ({vals}) RETURNING id").format(
            cols=sql.SQL(", ").join(sql.Identifier(n) for n in names),
            vals=sql.SQL(", ").join(sql.Placeholder(n) for n in names),
        ),
        params,
    ).fetchone()
    return get(conn, int(row["id"]))


def update(conn: Any, publication_id: int, fields: dict[str, Any]) -> Row | None:
    bad = set(fields) - UPDATABLE
    if bad:
        raise ValueError(f"publications: columns not updatable: {sorted(bad)}")
    if not fields:
        return get(conn, publication_id, include_deleted=True)
    sets = sql.SQL(", ").join(
        sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder(k)) for k in sorted(fields)
    )
    row = conn.execute(
        sql.SQL("UPDATE publications SET {sets} WHERE id = %(_id)s RETURNING id").format(sets=sets),
        {**fields, "_id": publication_id},
    ).fetchone()
    return get(conn, int(row["id"]), include_deleted=True) if row else None


def list_page(
    conn: Any,
    *,
    platform: str | None,
    channel_id: str | None,
    niche_id: str | None,
    product_id: str | None,
    campaign_id: int | None,
    banner_variant_id: int | None,
    kind: str | None,
    statuses: Sequence[str] | None,
    verify_statuses: Sequence[str] | None,
    since: datetime | None,
    until: datetime | None,
    sort: str,
    limit: int,
    offset: int,
) -> tuple[list[Row], int]:
    where = ["p.deleted_at IS NULL"]
    params: dict[str, Any] = {"limit": int(limit), "offset": int(offset)}
    simple = {
        "p.platform": platform,
        "p.channel_id": channel_id,
        "j.niche_id": niche_id,
        "p.product_id": product_id,
        "p.campaign_id": campaign_id,
        "p.banner_variant_id": banner_variant_id,
        "j.kind": kind,
    }
    for i, (col, value) in enumerate(simple.items()):
        if value is not None and value != "":
            where.append(f"{col} = %(f{i})s")
            params[f"f{i}"] = value
    if statuses:
        where.append("p.status = ANY(%(statuses)s)")
        params["statuses"] = list(statuses)
    if verify_statuses:
        where.append("p.verify_status = ANY(%(vstatuses)s)")
        params["vstatuses"] = list(verify_statuses)
    if since is not None:
        where.append("COALESCE(p.published_at, p.created_at) >= %(since)s")
        params["since"] = since
    if until is not None:
        where.append("COALESCE(p.published_at, p.created_at) < %(until)s")
        params["until"] = until
    desc = sort.startswith("-")
    column = SORTS.get(sort.lstrip("-"), "p.published_at")
    order = f"{column} {'DESC' if desc else 'ASC'} NULLS LAST, p.id {'DESC' if desc else 'ASC'}"
    cond = " AND ".join(where)
    total = conn.execute(
        f"SELECT count(*) AS n FROM publications p JOIN jobs j ON j.id = p.job_id WHERE {cond}", params
    ).fetchone()["n"]
    rows = conn.execute(
        f"{_SELECT} WHERE {cond} ORDER BY {order} LIMIT %(limit)s OFFSET %(offset)s", params
    ).fetchall()
    return rows, int(total)


def metrics(conn: Any, publication_id: int) -> list[Row]:
    return conn.execute(
        """
        SELECT captured_at, age_hours, views, likes, comments, engaged_views, source
        FROM metrics_snapshots WHERE publication_id = %(id)s
        ORDER BY captured_at
        """,
        {"id": publication_id},
    ).fetchall()


def metrics_daily(conn: Any, publication_id: int, tz: str) -> list[Row]:
    """Последний снимок каждого бизнес-дня."""
    return conn.execute(
        """
        SELECT DISTINCT ON ((captured_at AT TIME ZONE %(tz)s)::date)
               captured_at, age_hours, views, likes, comments, engaged_views, source
        FROM metrics_snapshots WHERE publication_id = %(id)s
        ORDER BY (captured_at AT TIME ZONE %(tz)s)::date, captured_at DESC
        """,
        {"id": publication_id, "tz": tz},
    ).fetchall()


def latest_retention(conn: Any, publication_id: int) -> Row | None:
    return conn.execute(
        """
        SELECT captured_at, points, retention_3s, failure_class FROM retention_curves
        WHERE publication_id = %(id)s ORDER BY captured_at DESC, id DESC LIMIT 1
        """,
        {"id": publication_id},
    ).fetchone()


__all__ = [
    "PUB_COLUMNS",
    "SORTS",
    "find_by_video",
    "get",
    "insert",
    "job_family",
    "latest_retention",
    "list_page",
    "live_for_job",
    "live_for_jobs_on_platform",
    "metrics",
    "metrics_daily",
    "same_candidate_jobs",
    "update",
]
