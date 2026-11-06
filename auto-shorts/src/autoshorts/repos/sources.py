"""Таблицы `sources`, `source_rights` и запись кандидатов отбора (миграции 0005, 0012).

Только SQL: функции принимают открытое соединение (строки — dict) и не управляют транзакциями.
Пути файлов в колонках (`original_path`, `proxy_path`, …) — относительно MEDIA_ROOT.
Параметры внутри CASE и интервальной арифметики — только через CAST (§1.5).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

Row = dict[str, Any]

SOURCE_COLUMNS = (
    "s.id, s.title, s.origin, s.origin_url, s.upload_id, s.status, s.error, s.language, s.niche_id,"
    " s.duration_sec, s.width, s.height, s.fps, s.video_codec, s.has_audio, s.file_bytes, s.sha256,"
    " s.original_path, s.proxy_path, s.audio_path, s.peaks_path, s.storyboard, s.scenes,"
    " s.watermark_suspect, s.watermark_detail, s.options, s.original_retain_until, s.proxy_retain_until,"
    " s.created_by, s.created_at, s.updated_at, s.deleted_at, s.media_dir, s.probe, s.platform_meta,"
    " s.selection"
)
RIGHTS_COLUMNS = (
    "source_id, rights_type, owner_name, owner_channel_url, proof_note, proof_file_path, allowlist_status,"
    " attribution_text, license, license_url, valid_until, warnings, recorded_by, recorded_at, updated_at"
)
JSON_COLUMNS = frozenset(
    {"storyboard", "scenes", "watermark_detail", "options", "probe", "platform_meta", "selection"}
)
UPDATABLE_COLUMNS = frozenset(
    {
        "title", "status", "error", "language", "niche_id", "duration_sec", "width", "height", "fps",
        "video_codec", "has_audio", "file_bytes", "sha256", "original_path", "proxy_path", "audio_path",
        "peaks_path", "storyboard", "scenes", "watermark_suspect", "watermark_detail", "options",
        "original_retain_until", "proxy_retain_until", "media_dir", "probe", "platform_meta", "selection",
        "origin_url",
    }
)  # fmt: skip
SORTABLE: dict[str, str] = {
    "created_at": "s.created_at",
    "updated_at": "s.updated_at",
    "title": "s.title",
    "duration_sec": "s.duration_sec",
    "status": "s.status",
    "id": "s.id",
}
SOURCE_TASK_KINDS = (
    "source_download",
    "source_prepare",
    "source_transcribe_chunk",
    "source_transcribe_cloud",
    "source_select",
    "source_scenes",
)


def _wrap(column: str, value: Any) -> Any:
    return Jsonb(value) if column in JSON_COLUMNS and value is not None else value


# ---------------------------------------------------------------- источники


def insert(conn: Any, fields: Mapping[str, Any]) -> Row:
    allowed = UPDATABLE_COLUMNS | {"origin", "upload_id", "created_by"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unknown sources columns: {sorted(unknown)}")
    cols = list(fields)
    query = sql.SQL("INSERT INTO sources ({}) VALUES ({}) RETURNING id").format(
        sql.SQL(", ").join(sql.Identifier(c) for c in cols),
        sql.SQL(", ").join(sql.Placeholder(c) for c in cols),
    )
    new_id = conn.execute(query, {c: _wrap(c, fields[c]) for c in cols}).fetchone()["id"]
    row = get(conn, int(new_id))
    assert row is not None
    return row


def get(conn: Any, source_id: int, *, for_update: bool = False, include_deleted: bool = False) -> Row | None:
    where = "" if include_deleted else " AND s.deleted_at IS NULL"
    return conn.execute(
        f"SELECT {SOURCE_COLUMNS} FROM sources s WHERE s.id = %(id)s{where}"
        + (" FOR UPDATE OF s" if for_update else ""),
        {"id": int(source_id)},
    ).fetchone()


def update(conn: Any, source_id: int, fields: Mapping[str, Any]) -> Row | None:
    if not fields:
        return get(conn, source_id)
    unknown = set(fields) - UPDATABLE_COLUMNS
    if unknown:
        raise ValueError(f"unknown sources columns: {sorted(unknown)}")
    sets = sql.SQL(", ").join(
        sql.SQL("{} = {}").format(sql.Identifier(c), sql.Placeholder(c)) for c in fields
    )
    params = {c: _wrap(c, v) for c, v in fields.items()}
    params["_id"] = int(source_id)
    conn.execute(sql.SQL("UPDATE sources SET {}, updated_at = now() WHERE id = %(_id)s").format(sets), params)
    return get(conn, source_id, include_deleted=True)


def set_status(
    conn: Any,
    source_id: int,
    status: str,
    *,
    error: str | None = None,
    only_from: Sequence[str] | None = None,
) -> Row | None:
    """Статус источника (+ ошибка). `only_from` — переход только из этих статусов (иначе None)."""
    where = " AND status = ANY(%(from)s)" if only_from else ""
    row = conn.execute(
        f"""
        UPDATE sources SET status = %(status)s, error = %(error)s, updated_at = now()
        WHERE id = %(id)s AND deleted_at IS NULL{where}
        RETURNING id, status
        """,
        {"id": int(source_id), "status": status, "error": error, "from": list(only_from or [])},
    ).fetchone()
    return row


def soft_delete(conn: Any, source_id: int) -> bool:
    row = conn.execute(
        """
        UPDATE sources SET deleted_at = now(), status = 'archived', updated_at = now()
        WHERE id = %(id)s AND deleted_at IS NULL RETURNING id
        """,
        {"id": int(source_id)},
    ).fetchone()
    return row is not None


def media_dir_taken(conn: Any, media_dir: str) -> int | None:
    row = conn.execute("SELECT id FROM sources WHERE media_dir = %(d)s", {"d": media_dir}).fetchone()
    return int(row["id"]) if row else None


_LIST_EXTRA = """
  r.rights_type,
  (SELECT count(*) FROM clip_candidates c
    WHERE c.source_id = s.id AND c.superseded_at IS NULL) AS candidates_count,
  (SELECT count(*) FROM jobs j WHERE j.source_id = s.id) AS jobs_count,
  at.kind AS active_kind, at.status AS active_status, at.progress AS active_progress,
  at.resource_class AS active_class,
  CASE WHEN at.status = 'queued' THEN (
    SELECT count(*) + 1 FROM tasks q
    WHERE q.status = 'queued' AND q.resource_class = at.resource_class AND q.id <> at.id
      AND (q.priority, q.run_after, q.id) < (at.priority, at.run_after, at.id)
  ) END AS queue_position
"""
_ACTIVE_TASK_JOIN = """
LEFT JOIN source_rights r ON r.source_id = s.id
LEFT JOIN LATERAL (
  SELECT t.id, t.kind, t.status, t.progress, t.resource_class, t.priority, t.run_after
  FROM tasks t WHERE t.source_id = s.id AND t.status IN ('queued', 'running')
  ORDER BY (t.status = 'running') DESC, t.id DESC LIMIT 1
) at ON true
"""


def list_page(
    conn: Any,
    *,
    statuses: Sequence[str] | None,
    origin: str | None,
    niche_id: str | None,
    q: str | None,
    order: sql.Composable,
    limit: int,
    offset: int,
) -> tuple[list[Row], int]:
    where = ["s.deleted_at IS NULL"]
    params: dict[str, Any] = {"limit": int(limit), "offset": int(offset)}
    if statuses:
        where.append("s.status = ANY(%(statuses)s)")
        params["statuses"] = list(statuses)
    if origin:
        where.append("s.origin = %(origin)s")
        params["origin"] = origin
    if niche_id:
        where.append("s.niche_id = %(niche_id)s")
        params["niche_id"] = niche_id
    if q:
        where.append("s.title ILIKE %(q)s")
        params["q"] = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    where_sql = " AND ".join(where)
    total = conn.execute(f"SELECT count(*) AS n FROM sources s WHERE {where_sql}", params).fetchone()["n"]
    query = (
        sql.SQL(
            f"SELECT {SOURCE_COLUMNS}, {_LIST_EXTRA} FROM sources s {_ACTIVE_TASK_JOIN} WHERE {where_sql} "
        )
        + order
        + sql.SQL(" LIMIT %(limit)s OFFSET %(offset)s")
    )
    rows = conn.execute(query, params).fetchall()
    return rows, int(total)


def counts(conn: Any, source_id: int) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT
          (SELECT count(*) FROM clip_candidates c WHERE c.source_id = %(id)s AND c.superseded_at IS NULL)
            AS candidates_count,
          (SELECT count(*) FROM jobs j WHERE j.source_id = %(id)s) AS jobs_count,
          (SELECT count(*) FROM jobs j WHERE j.source_id = %(id)s
             AND j.status IN ('queued', 'processing')) AS active_jobs
        """,
        {"id": int(source_id)},
    ).fetchone()
    return {k: int(v) for k, v in row.items()}


# ---------------------------------------------------------------- задачи источника


def tasks_for(conn: Any, source_id: int, *, limit: int = 200) -> list[Row]:
    """Задачи источника, новые первыми (+ место в очереди для queued)."""
    return conn.execute(
        """
        SELECT t.id, t.kind, t.status, t.resource_class, t.progress, t.payload, t.defer_reason,
               t.last_error, t.error_class, t.started_at, t.finished_at, t.created_at, t.attempts,
               t.max_attempts, t.result,
               CASE WHEN t.status = 'queued' THEN (
                 SELECT count(*) + 1 FROM tasks q
                 WHERE q.status = 'queued' AND q.resource_class = t.resource_class AND q.id <> t.id
                   AND (q.priority, q.run_after, q.id) < (t.priority, t.run_after, t.id)
               ) END AS queue_position
        FROM tasks t WHERE t.source_id = %(id)s AND t.kind = ANY(%(kinds)s)
        ORDER BY t.id DESC LIMIT %(limit)s
        """,
        {"id": int(source_id), "kinds": list(SOURCE_TASK_KINDS), "limit": int(limit)},
    ).fetchall()


def active_tasks(conn: Any, source_id: int) -> list[Row]:
    return conn.execute(
        "SELECT id, kind, status FROM tasks WHERE source_id = %(id)s AND status IN ('queued', 'running')",
        {"id": int(source_id)},
    ).fetchall()


def running_ahead(conn: Any, kind: str, task_id: int) -> int:
    """Сколько задач `kind` уже бегут «впереди» задачи `task_id`: взяты раньше (started_at), при
    равенстве — с меньшим id. Две задачи, взятые одновременно, не откладывают друг друга по кругу:
    ровно одна из них видит 0."""
    row = conn.execute(
        """
        SELECT count(*) AS n
        FROM tasks o, tasks me
        WHERE me.id = CAST(%(me)s AS bigint) AND o.kind = %(kind)s AND o.status = 'running' AND o.id <> me.id
          AND (COALESCE(o.started_at, '-infinity'::timestamptz), o.id)
              < (COALESCE(me.started_at, 'infinity'::timestamptz), me.id)
        """,
        {"kind": kind, "me": int(task_id)},
    ).fetchone()
    return int(row["n"])


def running_count(conn: Any, kind: str, *, exclude_task_id: int | None = None) -> int:
    row = conn.execute(
        """
        SELECT count(*) AS n FROM tasks
        WHERE kind = %(kind)s AND status = 'running' AND id <> COALESCE(CAST(%(ex)s AS bigint), -1)
        """,
        {"kind": kind, "ex": exclude_task_id},
    ).fetchone()
    return int(row["n"])


# ---------------------------------------------------------------- права


def get_rights(conn: Any, source_id: int) -> Row | None:
    return conn.execute(
        f"SELECT {RIGHTS_COLUMNS} FROM source_rights WHERE source_id = %(id)s", {"id": int(source_id)}
    ).fetchone()


def upsert_rights(conn: Any, source_id: int, fields: Mapping[str, Any], *, recorded_by: int | None) -> Row:
    """Записать права (вставка или замена целиком). `recorded_by` — кто заменил последним."""
    cols = (
        "rights_type", "owner_name", "owner_channel_url", "proof_note", "proof_file_path",
        "allowlist_status", "attribution_text", "license", "license_url", "valid_until", "warnings",
    )  # fmt: skip
    params = {c: fields.get(c) for c in cols}
    params["warnings"] = list(fields.get("warnings") or [])
    params["source_id"] = int(source_id)
    params["by"] = recorded_by
    return conn.execute(
        f"""
        INSERT INTO source_rights (source_id, {", ".join(cols)}, recorded_by)
        VALUES (%(source_id)s, {", ".join(f"%({c})s" for c in cols)}, %(by)s)
        ON CONFLICT (source_id) DO UPDATE SET
          {", ".join(f"{c} = EXCLUDED.{c}" for c in cols)},
          recorded_by = EXCLUDED.recorded_by, recorded_at = now(), updated_at = now()
        RETURNING {RIGHTS_COLUMNS}
        """,
        params,
    ).fetchone()


def set_rights_warnings(conn: Any, source_id: int, warnings: Sequence[str]) -> None:
    conn.execute(
        "UPDATE source_rights SET warnings = %(w)s WHERE source_id = %(id)s",
        {"id": int(source_id), "w": list(dict.fromkeys(warnings))},
    )


# ---------------------------------------------------------------- кандидаты отбора


CANDIDATE_INSERT_COLUMNS = (
    "source_id", "start_sid", "end_sid", "hook_sid", "start_sec", "end_sec", "title", "hook_text",
    "hook_type", "reason", "transcript_excerpt", "llm_scores", "features", "trend_prior", "final_score",
    "content_flags", "needs_context", "status", "created_by_kind", "origin", "start_word_idx",
    "end_word_idx", "snap",
)  # fmt: skip
_CANDIDATE_JSON = frozenset({"llm_scores", "features", "snap"})


def supersede_auto_candidates(conn: Any, source_id: int) -> int:
    """Повторный отбор: прежние автоматические кандидаты без clip-job уходят в `superseded`.

    Ручные, одобренные и уже взятые в работу (есть job_id или status used) остаются текущими.
    """
    return conn.execute(
        """
        UPDATE clip_candidates SET superseded_at = now(), updated_at = now()
        WHERE source_id = %(id)s AND superseded_at IS NULL AND created_by_kind = 'auto'
          AND job_id IS NULL AND status IN ('proposed', 'dismissed')
        """,
        {"id": int(source_id)},
    ).rowcount


def insert_candidates(conn: Any, source_id: int, rows: Sequence[Mapping[str, Any]]) -> list[int]:
    ids: list[int] = []
    query = sql.SQL("INSERT INTO clip_candidates ({}) VALUES ({}) RETURNING id").format(
        sql.SQL(", ").join(sql.Identifier(c) for c in CANDIDATE_INSERT_COLUMNS),
        sql.SQL(", ").join(sql.Placeholder(c) for c in CANDIDATE_INSERT_COLUMNS),
    )
    for r in rows:
        params = {c: r.get(c) for c in CANDIDATE_INSERT_COLUMNS}
        params["source_id"] = int(source_id)
        for c in _CANDIDATE_JSON:
            params[c] = Jsonb(params[c] if params[c] is not None else {})
        params["content_flags"] = list(params["content_flags"] or [])
        params["needs_context"] = bool(params["needs_context"])
        ids.append(int(conn.execute(query, params).fetchone()["id"]))
    return ids


def current_candidates(conn: Any, source_id: int) -> list[Row]:
    return conn.execute(
        """
        SELECT id, start_sec, end_sec, origin, status, final_score, content_flags, job_id
        FROM clip_candidates WHERE source_id = %(id)s AND superseded_at IS NULL
        ORDER BY final_score DESC NULLS LAST, id
        """,
        {"id": int(source_id)},
    ).fetchall()


__all__ = [
    "SORTABLE",
    "SOURCE_TASK_KINDS",
    "active_tasks",
    "counts",
    "current_candidates",
    "get",
    "get_rights",
    "insert",
    "insert_candidates",
    "list_page",
    "media_dir_taken",
    "running_ahead",
    "running_count",
    "set_rights_warnings",
    "set_status",
    "soft_delete",
    "supersede_auto_candidates",
    "tasks_for",
    "update",
    "upsert_rights",
]
