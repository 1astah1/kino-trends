"""SQL пакетов «К заливке» `upload_packages` (docs/ARCHITECTURE.md §4.2 0006/0014, §5.4). Владелец — M3-A.

Только SQL. Живой пакет ролика один (`uq_pkg_live_per_job`: status ready | downloaded); пересборка
сначала переводит прежний в `superseded`, затем вставляет новый — в одной транзакции.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

Row = dict[str, Any]

PKG_COLUMNS = (
    "id",
    "job_id",
    "channel_id",
    "platform",
    "status",
    "title",
    "description",
    "hashtags",
    "pinned_comment",
    "audio_note",
    "related_video_hint",
    "checklist",
    "tracking_link_id",
    "final_artifact_id",
    "downloaded_at",
    "edited_by",
    "texts_mode",
    "attribution_text",
    "ai_disclosure_required",
    "marked_uploaded_at",
    "marked_uploaded_by",
    "autopublish_state",
    "autopublish_at",
    "autopublish_privacy",
    "autopublish_attempts",
    "autopublish_error",
    "autopublish_session",
    "autopublish_video_id",
    "autopublish_by",
    "created_at",
    "updated_at",
)
_P = ", ".join(f"p.{c}" for c in PKG_COLUMNS)
_SELECT = f"""
    SELECT {_P},
           j.video_uid, j.kind AS job_kind, j.status AS job_status, j.duration_sec, j.originality,
           j.originality_verdict, j.product_id AS job_product_id,
           c.title AS channel_title, c.url AS channel_url, c.platform_channel_ref,
           c.profile AS channel_profile,
           tl.code AS tracking_code, tl.product_id AS link_product_id,
           a.path AS final_path, a.bytes AS final_bytes
    FROM upload_packages p
    JOIN jobs j ON j.id = p.job_id
    JOIN channels c ON c.id = p.channel_id
    JOIN tracking_links tl ON tl.id = p.tracking_link_id
    JOIN artifacts a ON a.id = p.final_artifact_id
"""
WRITABLE = frozenset(PKG_COLUMNS) - {"id", "created_at", "updated_at"}
LIVE_STATUSES = ("ready", "downloaded")
SORTS = {"created_at": "p.created_at", "updated_at": "p.updated_at", "id": "p.id"}


def _param(column: str, value: Any) -> Any:
    if column == "checklist" and value is not None:
        return Jsonb(value)
    if column == "hashtags" and value is not None:
        return list(value)
    return value


def get(conn: Any, package_id: int, *, for_update: bool = False) -> Row | None:
    lock = " FOR UPDATE OF p" if for_update else ""
    return conn.execute(f"{_SELECT} WHERE p.id = %(id)s{lock}", {"id": package_id}).fetchone()


def current_for_job(conn: Any, job_id: int) -> Row | None:
    """Текущий пакет ролика: живой, иначе последний опубликованный, иначе последний вообще."""
    return conn.execute(
        f"""{_SELECT} WHERE p.job_id = %(id)s
        ORDER BY CASE p.status WHEN 'ready' THEN 0 WHEN 'downloaded' THEN 0
                               WHEN 'published' THEN 1 ELSE 2 END,
                 p.id DESC
        LIMIT 1""",
        {"id": job_id},
    ).fetchone()


def live_for_job(conn: Any, job_id: int, *, for_update: bool = False) -> Row | None:
    lock = " FOR UPDATE OF p" if for_update else ""
    return conn.execute(
        f"{_SELECT} WHERE p.job_id = %(id)s AND p.status IN ('ready','downloaded')"
        f" ORDER BY p.id DESC LIMIT 1{lock}",
        {"id": job_id},
    ).fetchone()


def insert(conn: Any, fields: dict[str, Any]) -> Row:
    bad = set(fields) - WRITABLE
    if bad:
        raise ValueError(f"upload_packages: columns not writable: {sorted(bad)}")
    names = sorted(fields)
    query = sql.SQL("INSERT INTO upload_packages ({cols}) VALUES ({vals}) RETURNING id").format(
        cols=sql.SQL(", ").join(sql.Identifier(n) for n in names),
        vals=sql.SQL(", ").join(sql.Placeholder(n) for n in names),
    )
    row = conn.execute(query, {n: _param(n, fields[n]) for n in names}).fetchone()
    return get(conn, int(row["id"]))


def update(
    conn: Any, package_id: int, fields: dict[str, Any], *, allowed_statuses: Sequence[str] | None = None
) -> Row | None:
    """Патч пакета; `allowed_statuses` — оптимистичная проверка статуса (0 строк → None)."""
    bad = set(fields) - WRITABLE
    if bad:
        raise ValueError(f"upload_packages: columns not writable: {sorted(bad)}")
    sets = [sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder(k)) for k in sorted(fields)]
    sets.append(sql.SQL("updated_at = now()"))
    where = sql.SQL("id = %(_id)s")
    params = {k: _param(k, v) for k, v in fields.items()} | {"_id": package_id}
    if allowed_statuses is not None:
        where = sql.SQL("id = %(_id)s AND status = ANY(%(_allowed)s)")
        params["_allowed"] = list(allowed_statuses)
    row = conn.execute(
        sql.SQL("UPDATE upload_packages SET {sets} WHERE {where} RETURNING id").format(
            sets=sql.SQL(", ").join(sets), where=where
        ),
        params,
    ).fetchone()
    return get(conn, int(row["id"])) if row else None


def live_clip_jobs_for_source(conn: Any, source_id: int) -> list[Row]:
    """Нарезки источника с живым пакетом (ready | downloaded): их тексты и гейт зависят от прав."""
    return conn.execute(
        """
        SELECT DISTINCT j.id, j.status, j.attempt_count
        FROM upload_packages p JOIN jobs j ON j.id = p.job_id
        WHERE j.source_id = %(sid)s AND j.kind = 'clip' AND j.deleted_at IS NULL
          AND p.status IN ('ready','downloaded')
        ORDER BY j.id
        """,
        {"sid": int(source_id)},
    ).fetchall()


def supersede_live(conn: Any, job_id: int) -> list[int]:
    rows = conn.execute(
        """
        UPDATE upload_packages SET status = 'superseded', updated_at = now()
        WHERE job_id = %(id)s AND status IN ('ready','downloaded')
        RETURNING id
        """,
        {"id": job_id},
    ).fetchall()
    return [int(r["id"]) for r in rows]


def mark_downloaded(conn: Any, package_id: int) -> bool:
    """GET видео: ready → downloaded, `downloaded_at` при первом скачивании. True — что-то поменялось."""
    cur = conn.execute(
        """
        UPDATE upload_packages SET
          status = CASE WHEN status = 'ready' THEN 'downloaded' ELSE status END,
          downloaded_at = COALESCE(downloaded_at, now()),
          updated_at = now()
        WHERE id = %(id)s AND (status = 'ready' OR downloaded_at IS NULL)
        """,
        {"id": package_id},
    )
    return cur.rowcount > 0


def list_page(
    conn: Any,
    *,
    statuses: Sequence[str] | None,
    channel_id: str | None,
    platform: str | None,
    limit: int,
    offset: int,
) -> tuple[list[Row], int]:
    where = ["true"]
    params: dict[str, Any] = {"limit": int(limit), "offset": int(offset)}
    if statuses:
        where.append("p.status = ANY(%(statuses)s)")
        params["statuses"] = list(statuses)
    else:
        where.append("p.status <> 'superseded'")
    if channel_id:
        where.append("p.channel_id = %(ch)s")
        params["ch"] = channel_id
    if platform:
        where.append("p.platform = %(platform)s")
        params["platform"] = platform
    cond = " AND ".join(where)
    total = conn.execute(f"SELECT count(*) AS n FROM upload_packages p WHERE {cond}", params).fetchone()["n"]
    rows = conn.execute(
        f"""{_SELECT} WHERE {cond}
        ORDER BY CASE p.status WHEN 'ready' THEN 0 WHEN 'downloaded' THEN 1
                               WHEN 'published' THEN 2 ELSE 3 END,
                 p.id DESC
        LIMIT %(limit)s OFFSET %(offset)s""",
        params,
    ).fetchall()
    return rows, int(total)


def counts(conn: Any) -> dict[str, int]:
    """Для SSE `packages {ready, downloaded}`."""
    row = conn.execute(
        """
        SELECT count(*) FILTER (WHERE status = 'ready') AS ready,
               count(*) FILTER (WHERE status = 'downloaded') AS downloaded
        FROM upload_packages
        """
    ).fetchone()
    return {"ready": int(row["ready"]), "downloaded": int(row["downloaded"])}


# ---------------------------------------------------------------- чтения для сборки


def job_for_package(conn: Any, job_id: int, *, for_update: bool = False) -> Row | None:
    lock = " FOR UPDATE OF j" if for_update else ""
    return conn.execute(
        f"""
        SELECT j.id, j.video_uid, j.kind, j.status, j.channel_id, j.niche_id, j.product_id, j.campaign_id,
               j.banner_variant_id, j.topic, j.duration_sec, j.stage_payload, j.config_snapshot,
               j.source_id, j.clip_candidate_id, j.clip_start_sec, j.clip_end_sec, j.reframe_mode,
               j.author_layer, j.author_share, j.author_script, j.author_voice, j.originality,
               j.originality_verdict, j.attempt_count, j.parent_job_id, j.deleted_at,
               c.platform, c.title AS channel_title, c.url AS channel_url, c.platform_channel_ref,
               c.default_product_id, c.profile AS channel_profile
        FROM jobs j JOIN channels c ON c.id = j.channel_id
        WHERE j.id = %(id)s{lock}
        """,
        {"id": job_id},
    ).fetchone()


def niche(conn: Any, niche_id: str | None) -> Row | None:
    if not niche_id:
        return None
    return conn.execute("SELECT id, label, config FROM niches WHERE id = %(id)s", {"id": niche_id}).fetchone()


def variant(conn: Any, variant_id: int | None) -> Row | None:
    if variant_id is None:
        return None
    return conn.execute(
        """
        SELECT v.id, v.code, v.name, v.ad_marking, v.pinned_comment_template, v.description_cta_template,
               v.code_url_template, v.voice_cta_text, c.product_id
        FROM banner_variants v JOIN campaigns c ON c.id = v.campaign_id
        WHERE v.id = %(id)s
        """,
        {"id": variant_id},
    ).fetchone()


def product(conn: Any, product_id: str | None) -> Row | None:
    if not product_id:
        return None
    return conn.execute(
        """
        SELECT id, name, kind, status, landing_url, redirect_host, redirect_mode, telegram_bot_username,
               tma_short_name, telegram_link_style, utm_defaults, brand, landing
        FROM products WHERE id = %(id)s
        """,
        {"id": product_id},
    ).fetchone()


def source_rights(conn: Any, source_id: int | None) -> Row | None:
    if source_id is None:
        return None
    return conn.execute(
        """
        SELECT s.id AS source_id, s.title AS source_title, s.origin_url, r.rights_type, r.owner_name,
               r.owner_channel_url, r.allowlist_status, r.attribution_text, r.valid_until, r.license,
               r.license_url
        FROM sources s LEFT JOIN source_rights r ON r.source_id = s.id
        WHERE s.id = %(id)s
        """,
        {"id": source_id},
    ).fetchone()


def publication_for_package(conn: Any, package_id: int) -> Row | None:
    return conn.execute(
        """
        SELECT id, url, status, verify_status, published_at FROM publications
        WHERE package_id = %(id)s AND deleted_at IS NULL
        ORDER BY id DESC LIMIT 1
        """,
        {"id": package_id},
    ).fetchone()


__all__ = [
    "LIVE_STATUSES",
    "PKG_COLUMNS",
    "counts",
    "current_for_job",
    "get",
    "insert",
    "job_for_package",
    "list_page",
    "live_clip_jobs_for_source",
    "live_for_job",
    "mark_downloaded",
    "niche",
    "product",
    "publication_for_package",
    "source_rights",
    "supersede_live",
    "update",
    "variant",
]
