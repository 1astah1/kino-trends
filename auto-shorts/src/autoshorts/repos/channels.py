"""Таблицы `channels` и `channel_incidents` (миграции 0001, 0003) + сводки для здоровья канала."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

Row = dict[str, Any]

# Дубль профиля (§1.3, «разные профили против детекта сетки»): та же площадка и тот же профиль
# без пустых значений; пустой профиль (всё по умолчанию) — дубль только внутри одной ниши.
_DUPLICATES = """
    COALESCE((
        SELECT array_agg(o.id ORDER BY o.id) FROM channels o
        WHERE o.id <> c.id AND o.platform = c.platform
          AND o.status NOT IN ('terminated', 'retired')
          AND jsonb_strip_nulls(o.profile) = jsonb_strip_nulls(c.profile)
          AND (o.niche_id = c.niche_id OR jsonb_strip_nulls(c.profile) <> '{}'::jsonb)
    ), '{}'::text[]) AS profile_duplicate_of
"""
COLUMNS = (
    "c.id, c.platform, c.niche_id, c.cluster, c.title, c.platform_channel_ref, c.url, c.status,"
    " c.replaced_by, c.default_product_id, c.profile, c.publishing_plan, c.advanced_features,"
    " c.subscribers, c.notes, c.created_at, c.updated_at," + _DUPLICATES
)
JSON_COLUMNS = frozenset({"profile", "publishing_plan"})
EDITABLE_COLUMNS = frozenset(
    {
        "niche_id",
        "cluster",
        "title",
        "platform_channel_ref",
        "url",
        "status",
        "replaced_by",
        "default_product_id",
        "profile",
        "publishing_plan",
        "advanced_features",
        "subscribers",
        "notes",
    }
)
INCIDENT_COLUMNS = "id, channel_id, kind, occurred_at, details, note, created_by"


def _params(fields: Mapping[str, Any]) -> dict[str, Any]:
    return {k: (Jsonb(v) if k in JSON_COLUMNS and v is not None else v) for k, v in fields.items()}


def get(conn: Any, channel_id: str, *, for_update: bool = False) -> Row | None:
    if for_update:
        conn.execute("SELECT 1 FROM channels WHERE id = %(id)s FOR UPDATE", {"id": channel_id})
    return conn.execute(
        f"SELECT {COLUMNS} FROM channels c WHERE c.id = %(id)s", {"id": channel_id}
    ).fetchone()


def list_page(
    conn: Any,
    *,
    platform: str | None,
    statuses: Sequence[str] | None,
    niche_id: str | None,
    cluster_id: str | None,
    limit: int,
    offset: int,
) -> tuple[list[Row], int]:
    conds: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if platform:
        conds.append("c.platform = %(platform)s")
        params["platform"] = platform
    if statuses:
        conds.append("c.status = ANY(%(statuses)s)")
        params["statuses"] = list(statuses)
    if niche_id:
        conds.append("c.niche_id = %(niche_id)s")
        params["niche_id"] = niche_id
    if cluster_id:
        conds.append("c.cluster = %(cluster_id)s")
        params["cluster_id"] = cluster_id
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    total = conn.execute(f"SELECT count(*) AS n FROM channels c {where}", params).fetchone()["n"]
    rows = conn.execute(
        f"SELECT {COLUMNS} FROM channels c {where} ORDER BY c.id LIMIT %(limit)s OFFSET %(offset)s", params
    ).fetchall()
    return rows, int(total)


def insert(conn: Any, fields: Mapping[str, Any]) -> Row:
    unknown = set(fields) - EDITABLE_COLUMNS - {"id", "platform"}
    if unknown:
        raise ValueError(f"unknown channels columns: {sorted(unknown)}")
    names = sorted(fields)
    conn.execute(
        sql.SQL("INSERT INTO channels ({}) VALUES ({})").format(
            sql.SQL(", ").join(sql.Identifier(n) for n in names),
            sql.SQL(", ").join(sql.Placeholder(n) for n in names),
        ),
        _params(fields),
    )
    row = get(conn, str(fields["id"]))
    assert row is not None
    return row


def update(conn: Any, channel_id: str, fields: Mapping[str, Any]) -> Row | None:
    unknown = set(fields) - EDITABLE_COLUMNS
    if unknown:
        raise ValueError(f"cannot update channels columns: {sorted(unknown)}")
    if fields:
        assignments = sql.SQL(", ").join(
            sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder(k)) for k in sorted(fields)
        )
        conn.execute(
            sql.SQL("UPDATE channels SET {}, updated_at = now() WHERE id = %(pk)s").format(assignments),
            {**_params(fields), "pk": channel_id},
        )
    return get(conn, channel_id)


# ---------------------------------------------------------------- инциденты


def list_incidents(conn: Any, channel_id: str, *, limit: int = 200) -> list[Row]:
    return conn.execute(
        f"""
        SELECT {INCIDENT_COLUMNS} FROM channel_incidents
        WHERE channel_id = %(id)s ORDER BY occurred_at DESC, id DESC LIMIT %(limit)s
        """,
        {"id": channel_id, "limit": limit},
    ).fetchall()


def insert_incident(
    conn: Any,
    *,
    channel_id: str,
    kind: str,
    note: str | None,
    details: dict[str, Any],
    occurred_at: Any,
    created_by: int | None,
) -> Row:
    return conn.execute(
        f"""
        INSERT INTO channel_incidents (channel_id, kind, occurred_at, details, note, created_by)
        VALUES (%(channel_id)s, %(kind)s, COALESCE(%(occurred_at)s, now()), %(details)s, %(note)s, %(by)s)
        RETURNING {INCIDENT_COLUMNS}
        """,
        {
            "channel_id": channel_id,
            "kind": kind,
            "occurred_at": occurred_at,
            "details": Jsonb(details),
            "note": note,
            "by": created_by,
        },
    ).fetchone()


# ---------------------------------------------------------------- здоровье


def recent_publications(conn: Any, channel_id: str, *, limit: int = 10) -> list[Row]:
    return conn.execute(
        """
        SELECT id AS publication_id, published_at, status, latest_views AS views
        FROM publications
        WHERE channel_id = %(id)s AND deleted_at IS NULL
        ORDER BY COALESCE(published_at, created_at) DESC, id DESC
        LIMIT %(limit)s
        """,
        {"id": channel_id, "limit": limit},
    ).fetchall()


def health_numbers(conn: Any, channel_id: str) -> Row:
    """Удалённые площадкой за 30 дней, медианы просмотров 7/7 дней, доля ленты Shorts."""
    return conn.execute(
        """
        SELECT
          (SELECT count(*) FROM publications p
            WHERE p.channel_id = %(id)s AND p.deleted_at IS NULL AND p.status = 'removed_by_platform'
              AND COALESCE(p.published_at, p.created_at) >= now() - interval '30 days') AS removed_count_30d,
          (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY p.latest_views) FROM publications p
            WHERE p.channel_id = %(id)s AND p.deleted_at IS NULL AND p.latest_views IS NOT NULL
              AND p.published_at >= now() - interval '7 days') AS median_7d,
          (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY p.latest_views) FROM publications p
            WHERE p.channel_id = %(id)s AND p.deleted_at IS NULL AND p.latest_views IS NOT NULL
              AND p.published_at >= now() - interval '14 days'
              AND p.published_at < now() - interval '7 days') AS median_prev_7d,
          (SELECT avg(s.shorts_feed_share) FROM metrics_snapshots s
             JOIN publications p ON p.id = s.publication_id
            WHERE p.channel_id = %(id)s AND s.shorts_feed_share IS NOT NULL
              AND s.captured_at >= now() - interval '30 days') AS shorts_feed_share
        """,
        {"id": channel_id},
    ).fetchone()


def views_by_recency(conn: Any, channel_id: str, *, limit: int = 60) -> list[int]:
    rows = conn.execute(
        """
        SELECT latest_views FROM publications
        WHERE channel_id = %(id)s AND deleted_at IS NULL
          AND latest_views IS NOT NULL AND published_at IS NOT NULL
        ORDER BY published_at DESC LIMIT %(limit)s
        """,
        {"id": channel_id, "limit": limit},
    ).fetchall()
    return [int(r["latest_views"]) for r in rows]


def recent_incident_kinds(conn: Any, channel_id: str, *, days: int = 30) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT kind FROM channel_incidents
        WHERE channel_id = %(id)s AND occurred_at >= now() - make_interval(days => CAST(%(days)s AS int))
        ORDER BY kind
        """,
        {"id": channel_id, "days": days},
    ).fetchall()
    return [r["kind"] for r in rows]
