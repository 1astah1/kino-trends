"""SQL нарезки: кандидаты, итог отбора источника, авторский слой и оригинальность clip-job (M5-B).

Только SQL: функции принимают открытое соединение (строки — dict) и не управляют транзакциями.
Таблицы — 0005 и 0012/0013 (контракт M5M3-0): `clip_candidates`, `sources.selection`,
`source_rights`, `transcripts`, колонки `jobs.clip_start_sid/clip_end_sid/author_script/
author_script_version/author_voice/originality/originality_verdict`.

Колонки нарезки в `jobs` пишет только этот модуль (общий `repos/jobs.py` их не знает): стадии
clip-job пишут их с условием «ролик всё ещё на этой стадии», API — с оптимистичной версией.
Параметры внутри CASE и интервальной арифметики — только через CAST (§1.5).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

Row = dict[str, Any]

CANDIDATE_COLUMNS: tuple[str, ...] = (
    "id",
    "source_id",
    "start_sid",
    "end_sid",
    "hook_sid",
    "start_sec",
    "end_sec",
    "title",
    "hook_text",
    "hook_type",
    "reason",
    "transcript_excerpt",
    "llm_scores",
    "features",
    "trend_prior",
    "final_score",
    "content_flags",
    "needs_context",
    "status",
    "created_by_kind",
    "operator_edits",
    "created_at",
    "updated_at",
    "job_id",
    "origin",
    "unblocked_flags",
    "start_word_idx",
    "end_word_idx",
    "snap",
    "notes",
    "superseded_at",
    "updated_by",
)
_C = ", ".join(f"cc.{c}" for c in CANDIDATE_COLUMNS)
_SELECT_CANDIDATE = f"""
    SELECT {_C}, j.status AS job_status
    FROM clip_candidates cc LEFT JOIN jobs j ON j.id = cc.job_id
"""
CANDIDATE_WRITABLE: frozenset[str] = frozenset(
    c for c in CANDIDATE_COLUMNS if c not in ("id", "created_at", "updated_at")
)
CANDIDATE_JSONB: frozenset[str] = frozenset({"llm_scores", "features", "operator_edits", "snap"})
CANDIDATE_ARRAYS: frozenset[str] = frozenset({"content_flags", "unblocked_flags"})

# статусы ролика, при которых кандидат и момент источника считаются «живыми» (не отброшенными)
DEAD_JOB_STATUSES = ("cancelled", "rejected", "failed")


def _cand_param(column: str, value: Any) -> Any:
    if column in CANDIDATE_JSONB and value is not None:
        return Jsonb(value)
    if column in CANDIDATE_ARRAYS and value is not None:
        return list(value)
    return value


def _check_candidate_columns(fields: Iterable[str]) -> None:
    bad = set(fields) - CANDIDATE_WRITABLE
    if bad:
        raise ValueError(f"clip_candidates: columns not writable: {sorted(bad)}")


# ---------------------------------------------------------------- кандидаты


def list_candidates(
    conn: Any,
    source_id: int,
    *,
    statuses: Sequence[str] | None = None,
    include_superseded: bool = False,
) -> list[Row]:
    where = ["cc.source_id = %(sid)s"]
    params: dict[str, Any] = {"sid": source_id}
    if not include_superseded:
        where.append("cc.superseded_at IS NULL")
    if statuses:
        where.append("cc.status = ANY(%(statuses)s)")
        params["statuses"] = list(statuses)
    return conn.execute(
        f"""{_SELECT_CANDIDATE}
        WHERE {" AND ".join(where)}
        ORDER BY (cc.superseded_at IS NOT NULL), cc.final_score DESC NULLS LAST, cc.start_sec, cc.id
        """,
        params,
    ).fetchall()


def get_candidate(conn: Any, candidate_id: int, *, for_update: bool = False) -> Row | None:
    lock = " FOR UPDATE OF cc" if for_update else ""
    return conn.execute(f"{_SELECT_CANDIDATE} WHERE cc.id = %(id)s{lock}", {"id": candidate_id}).fetchone()


def insert_candidate(conn: Any, fields: dict[str, Any]) -> Row:
    _check_candidate_columns(fields)
    names = sorted(fields)
    query = sql.SQL("INSERT INTO clip_candidates ({cols}) VALUES ({vals}) RETURNING id").format(
        cols=sql.SQL(", ").join(sql.Identifier(n) for n in names),
        vals=sql.SQL(", ").join(sql.Placeholder(n) for n in names),
    )
    row = conn.execute(query, {n: _cand_param(n, fields[n]) for n in names}).fetchone()
    out = get_candidate(conn, int(row["id"]))
    assert out is not None
    return out


def update_candidate(conn: Any, candidate_id: int, fields: dict[str, Any]) -> Row | None:
    _check_candidate_columns(fields)
    if not fields:
        return get_candidate(conn, candidate_id)
    names = sorted(fields)
    query = sql.SQL(
        "UPDATE clip_candidates SET {sets}, updated_at = now() WHERE id = %(id)s RETURNING id"
    ).format(
        sets=sql.SQL(", ").join(
            sql.SQL("{} = {}").format(sql.Identifier(n), sql.Placeholder(n)) for n in names
        )
    )
    params = {n: _cand_param(n, fields[n]) for n in names}
    params["id"] = candidate_id
    if conn.execute(query, params).fetchone() is None:
        return None
    return get_candidate(conn, candidate_id)


def supersede_auto_candidates(conn: Any, source_id: int) -> int:
    """Повторный отбор: прежние автоматические кандидаты без живого clip-job → superseded_at.

    Ручные и уже взятые в работу (у ролика не отменённый статус) остаются в списке.
    """
    cur = conn.execute(
        """
        UPDATE clip_candidates cc SET superseded_at = now(), updated_at = now()
        WHERE cc.source_id = %(sid)s AND cc.superseded_at IS NULL AND cc.created_by_kind = 'auto'
          AND (cc.job_id IS NULL OR EXISTS (
                SELECT 1 FROM jobs j WHERE j.id = cc.job_id AND j.status = ANY(%(dead)s)))
        """,
        {"sid": source_id, "dead": list(DEAD_JOB_STATUSES)},
    )
    return cur.rowcount


# ---------------------------------------------------------------- источник


def get_source(conn: Any, source_id: int, *, for_update: bool = False) -> Row | None:
    """Источник с записью прав (всё, что нужно нарезке)."""
    lock = " FOR UPDATE OF s" if for_update else ""
    return conn.execute(
        f"""
        SELECT s.id, s.title, s.origin, s.origin_url, s.status, s.error, s.language, s.niche_id,
               s.duration_sec, s.width, s.height, s.fps, s.has_audio, s.original_path, s.proxy_path,
               s.audio_path, s.peaks_path, s.watermark_suspect, s.watermark_detail, s.options,
               s.media_dir, s.probe, s.platform_meta, s.selection, s.deleted_at, s.created_at,
               s.updated_at,
               r.source_id AS rights_source_id, r.rights_type, r.owner_name, r.owner_channel_url,
               r.proof_note, r.allowlist_status, r.attribution_text, r.valid_until, r.license,
               r.license_url, r.warnings AS rights_warnings
        FROM sources s LEFT JOIN source_rights r ON r.source_id = s.id
        WHERE s.id = %(id)s{lock}
        """,
        {"id": source_id},
    ).fetchone()


def current_transcript(conn: Any, source_id: int) -> Row | None:
    return conn.execute(
        """
        SELECT id, source_id, provider_key, model, language, path, words_count, sentences_count,
               duration_sec, created_at
        FROM transcripts WHERE source_id = %(sid)s AND is_current
        ORDER BY id DESC LIMIT 1
        """,
        {"sid": source_id},
    ).fetchone()


def set_source_selection(
    conn: Any, source_id: int, selection: dict[str, Any], *, mark_ready: bool
) -> str | None:
    """Итог отбора в `sources.selection`; источник в статусе selecting становится ready. → статус."""
    row = conn.execute(
        """
        UPDATE sources SET selection = %(sel)s,
               status = CASE WHEN CAST(%(ready)s AS boolean) AND status = 'selecting' THEN 'ready'
                             ELSE status END,
               error = CASE WHEN CAST(%(ready)s AS boolean) AND status = 'selecting' THEN NULL
                            ELSE error END,
               updated_at = now()
        WHERE id = %(id)s
        RETURNING status
        """,
        {"id": source_id, "sel": Jsonb(selection), "ready": bool(mark_ready)},
    ).fetchone()
    return row["status"] if row else None


def merge_source_options(conn: Any, source_id: int, options: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE sources SET options = options || %(o)s, updated_at = now() WHERE id = %(id)s",
        {"id": source_id, "o": Jsonb(options)},
    )


def niche_row(conn: Any, niche_id: str) -> Row | None:
    return conn.execute(
        "SELECT id, label, status, config FROM niches WHERE id = %(id)s", {"id": niche_id}
    ).fetchone()


# ---------------------------------------------------------------- колонки нарезки в jobs


CLIP_JOB_COLUMNS = (
    "id, kind, status, stage, video_uid, channel_id, source_id, clip_candidate_id, clip_start_sec,"
    " clip_end_sec, parent_job_id, author_layer, clip_start_sid, clip_end_sid, author_script,"
    " author_script_version, author_voice, originality, originality_verdict, fingerprint, updated_at"
)


def get_clip_state(conn: Any, job_id: int, *, for_update: bool = False) -> Row | None:
    lock = " FOR UPDATE" if for_update else ""
    return conn.execute(
        f"SELECT {CLIP_JOB_COLUMNS} FROM jobs WHERE id = %(id)s{lock}", {"id": job_id}
    ).fetchone()


def set_clip_sids(conn: Any, job_id: int, start_sid: int | None, end_sid: int | None) -> None:
    conn.execute(
        """
        UPDATE jobs SET clip_start_sid = %(s)s, clip_end_sid = %(e)s, updated_at = now()
        WHERE id = %(id)s
        """,
        {"id": job_id, "s": start_sid, "e": end_sid},
    )


def set_author_script(
    conn: Any,
    job_id: int,
    data: dict[str, Any] | None,
    *,
    expected_version: int | None = None,
    guard_stage: str | None = None,
) -> Row | None:
    """Записать сценарий и поднять версию. None — версия не совпала или ролик ушёл со стадии."""
    return conn.execute(
        """
        UPDATE jobs SET author_script = %(data)s, author_script_version = author_script_version + 1,
               updated_at = now()
        WHERE id = %(id)s
          AND (CAST(%(ver)s AS int) IS NULL OR author_script_version = CAST(%(ver)s AS int))
          AND (CAST(%(stage)s AS text) IS NULL
               OR (status = 'processing' AND stage = CAST(%(stage)s AS text)))
        RETURNING id, author_script, author_script_version
        """,
        {
            "id": job_id,
            "data": Jsonb(data) if data is not None else None,
            "ver": expected_version,
            "stage": guard_stage,
        },
    ).fetchone()


def set_author_voice_segment(
    conn: Any, job_id: int, segment: str, data: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Одна реплика в `jobs.author_voice.segments` (остальные не трогаются); None — удалить."""
    if data is None:
        row = conn.execute(
            """
            UPDATE jobs SET author_voice = jsonb_set(
                   COALESCE(author_voice, '{}'::jsonb), '{segments}',
                   COALESCE(author_voice->'segments', '{}'::jsonb) - CAST(%(seg)s AS text)),
                   updated_at = now()
            WHERE id = %(id)s RETURNING author_voice
            """,
            {"id": job_id, "seg": segment},
        ).fetchone()
    else:
        row = conn.execute(
            """
            UPDATE jobs SET author_voice = jsonb_set(
                   COALESCE(author_voice, '{}'::jsonb), '{segments}',
                   COALESCE(author_voice->'segments', '{}'::jsonb)
                     || jsonb_build_object(CAST(%(seg)s AS text), CAST(%(data)s AS jsonb))),
                   updated_at = now()
            WHERE id = %(id)s RETURNING author_voice
            """,
            {"id": job_id, "seg": segment, "data": Jsonb(data)},
        ).fetchone()
    return row["author_voice"] if row else None


def set_originality(
    conn: Any, job_id: int, report: dict[str, Any] | None, verdict: str | None, *, guard_stage: str | None
) -> bool:
    row = conn.execute(
        """
        UPDATE jobs SET originality = %(r)s, originality_verdict = %(v)s, updated_at = now()
        WHERE id = %(id)s
          AND (CAST(%(stage)s AS text) IS NULL
               OR (status = 'processing' AND stage = CAST(%(stage)s AS text)))
        RETURNING id
        """,
        {
            "id": job_id,
            "r": Jsonb(report) if report is not None else None,
            "v": verdict,
            "stage": guard_stage,
        },
    ).fetchone()
    return row is not None


def copy_author_layer(conn: Any, parent_id: int, child_id: int) -> None:
    """Клон и адаптация: сценарий автора и записи голоса родителя переходят ребёнку."""
    conn.execute(
        """
        UPDATE jobs c SET author_script = p.author_script,
               author_script_version = CASE WHEN p.author_script IS NULL THEN c.author_script_version
                                            ELSE c.author_script_version + 1 END,
               author_voice = p.author_voice,
               clip_start_sid = COALESCE(c.clip_start_sid, p.clip_start_sid),
               clip_end_sid = COALESCE(c.clip_end_sid, p.clip_end_sid),
               updated_at = now()
        FROM jobs p
        WHERE c.id = %(child)s AND p.id = %(parent)s AND c.author_script IS NULL
        """,
        {"parent": parent_id, "child": child_id},
    )


def originality_verdicts(conn: Any, job_ids: Sequence[int]) -> dict[int, str | None]:
    if not job_ids:
        return {}
    rows = conn.execute(
        "SELECT id, originality_verdict FROM jobs WHERE id = ANY(%(ids)s)", {"ids": list(job_ids)}
    ).fetchall()
    return {int(r["id"]): r["originality_verdict"] for r in rows}


# ---------------------------------------------------------------- история для отчёта об оригинальности


def source_clip_jobs(conn: Any, source_id: int, *, exclude_job_id: int | None = None) -> list[Row]:
    """Живые clip-job источника (повтор момента): диапазон sid и родство (клоны, адаптации)."""
    return conn.execute(
        """
        SELECT id, video_uid, status, parent_job_id, clip_start_sid, clip_end_sid, clip_start_sec,
               clip_end_sec, channel_id
        FROM jobs
        WHERE source_id = %(sid)s AND kind = 'clip' AND deleted_at IS NULL
          AND status <> ALL(%(dead)s)
          AND (CAST(%(ex)s AS bigint) IS NULL OR id <> CAST(%(ex)s AS bigint))
        ORDER BY id
        """,
        {"sid": source_id, "ex": exclude_job_id, "dead": list(DEAD_JOB_STATUSES)},
    ).fetchall()


def channel_author_scripts(
    conn: Any, channel_id: str, *, exclude_job_id: int | None = None, limit: int = 200
) -> list[Row]:
    """Реплики автора прошлых роликов канала (повтор своих реплик)."""
    return conn.execute(
        """
        SELECT id, video_uid, parent_job_id, author_script FROM jobs
        WHERE channel_id = %(ch)s AND kind = 'clip' AND author_script IS NOT NULL
          AND deleted_at IS NULL AND status <> ALL(%(dead)s)
          AND (CAST(%(ex)s AS bigint) IS NULL OR id <> CAST(%(ex)s AS bigint))
        ORDER BY created_at DESC LIMIT %(limit)s
        """,
        {"ch": channel_id, "ex": exclude_job_id, "limit": int(limit), "dead": list(DEAD_JOB_STATUSES)},
    ).fetchall()


def channel_fingerprints(
    conn: Any, channel_id: str, *, exclude_job_id: int | None = None, limit: int = 200
) -> list[Row]:
    """dHash последних роликов канала (похожесть на свою библиотеку, §6.3.9)."""
    return conn.execute(
        """
        SELECT id, video_uid, parent_job_id, fingerprint FROM jobs
        WHERE channel_id = %(ch)s AND fingerprint IS NOT NULL AND deleted_at IS NULL
          AND (CAST(%(ex)s AS bigint) IS NULL OR id <> CAST(%(ex)s AS bigint))
        ORDER BY created_at DESC LIMIT %(limit)s
        """,
        {"ch": channel_id, "ex": exclude_job_id, "limit": int(limit)},
    ).fetchall()


def family_job_ids(conn: Any, job_id: int) -> set[int]:
    """Родня ролика по parent_job_id — всё дерево клонов и адаптаций того же момента: от корня
    (самого первого ролика) вниз. Клон клона тоже родня деду, иначе он «повторяет момент» сам себя."""
    rows = conn.execute(
        """
        WITH RECURSIVE up AS (
            SELECT id, parent_job_id, 0 AS depth FROM jobs WHERE id = %(id)s
            UNION ALL
            SELECT j.id, j.parent_job_id, up.depth + 1 FROM jobs j JOIN up ON j.id = up.parent_job_id
            WHERE up.depth < 50
        ),
        root AS (SELECT id FROM up ORDER BY depth DESC LIMIT 1),
        down AS (
            SELECT id, 0 AS depth FROM jobs WHERE id = (SELECT id FROM root)
            UNION ALL
            SELECT j.id, down.depth + 1 FROM jobs j JOIN down ON j.parent_job_id = down.id
            WHERE down.depth < 50
        )
        SELECT DISTINCT id FROM down WHERE id <> %(id)s
        """,
        {"id": job_id},
    ).fetchall()
    return {int(r["id"]) for r in rows}


# ---------------------------------------------------------------- задачи и ссылки


def active_task(
    conn: Any, kind: str, *, job_id: int | None = None, source_id: int | None = None
) -> Row | None:
    """Последняя живая задача вида (job_id побочных задач нарезки лежит в payload)."""
    return conn.execute(
        """
        SELECT id, kind, status, payload, created_at FROM tasks
        WHERE kind = %(kind)s AND status IN ('queued','running')
          AND (CAST(%(job)s AS bigint) IS NULL OR payload->>'job_id' = CAST(%(job)s AS text))
          AND (CAST(%(src)s AS bigint) IS NULL OR source_id = CAST(%(src)s AS bigint))
        ORDER BY id DESC LIMIT 1
        """,
        {"kind": kind, "job": job_id, "src": source_id},
    ).fetchone()


def job_tracking_link(conn: Any, job_id: int) -> Row | None:
    return conn.execute(
        """
        SELECT id, code, product_id FROM tracking_links
        WHERE job_id = %(id)s AND level = 'package' AND is_active
        ORDER BY id LIMIT 1
        """,
        {"id": job_id},
    ).fetchone()


def insert_tracking_link(conn: Any, fields: dict[str, Any]) -> Row | None:
    """Код ролика; занятый код → None (вызывающий пробует другой)."""
    names = sorted(fields)
    query = sql.SQL(
        "INSERT INTO tracking_links ({cols}) VALUES ({vals}) ON CONFLICT (code) DO NOTHING RETURNING id, code"
    ).format(
        cols=sql.SQL(", ").join(sql.Identifier(n) for n in names),
        vals=sql.SQL(", ").join(sql.Placeholder(n) for n in names),
    )
    return conn.execute(query, fields).fetchone()


def product_row(conn: Any, product_id: str) -> Row | None:
    return conn.execute("SELECT * FROM products WHERE id = %(id)s", {"id": product_id}).fetchone()


__all__ = [
    "CANDIDATE_COLUMNS",
    "DEAD_JOB_STATUSES",
    "active_task",
    "channel_author_scripts",
    "channel_fingerprints",
    "copy_author_layer",
    "current_transcript",
    "family_job_ids",
    "get_candidate",
    "get_clip_state",
    "get_source",
    "insert_candidate",
    "insert_tracking_link",
    "job_tracking_link",
    "list_candidates",
    "merge_source_options",
    "niche_row",
    "originality_verdicts",
    "product_row",
    "set_author_script",
    "set_author_voice_segment",
    "set_clip_sids",
    "set_originality",
    "set_source_selection",
    "source_clip_jobs",
    "supersede_auto_candidates",
    "update_candidate",
]
