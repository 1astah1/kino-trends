"""SQL артефактов стадий `artifacts` (docs/ARCHITECTURE.md §4.2 0004, §5.5 `StageResult.artifacts`).

`path` — относительно MEDIA_ROOT (`core.storage.MediaLayout.relpath`). Живой артефакт
уникален по `(job_id, kind, idx, input_hash)` среди неудалённых: повторная стадия с теми же
входами переиспользует файл (кеш по `input_hash`), rework помечает артефакты `deleted_at`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

Row = dict[str, Any]

ARTIFACT_COLUMNS = (
    "id, job_id, source_id, kind, idx, path, bytes, sha256, input_hash, stage, is_master, created_at,"
    " deleted_at"
)


def insert(
    conn: Any,
    *,
    kind: str,
    path: str,
    input_hash: str,
    job_id: int | None = None,
    source_id: int | None = None,
    idx: int = 0,
    bytes_: int | None = None,
    sha256: str | None = None,
    stage: str | None = None,
    is_master: bool = False,
) -> Row:
    """Вставить артефакт; для job — upsert по живому ключу (тот же вход → обновить путь/размер)."""
    params = {
        "job_id": job_id,
        "source_id": source_id,
        "kind": kind,
        "idx": int(idx),
        "path": path,
        "bytes": bytes_,
        "sha256": sha256,
        "input_hash": input_hash,
        "stage": stage,
        "is_master": is_master,
    }
    if job_id is not None:
        return conn.execute(
            f"""
            INSERT INTO artifacts (job_id, source_id, kind, idx, path, bytes, sha256, input_hash, stage,
                                   is_master)
            VALUES (%(job_id)s, %(source_id)s, %(kind)s, %(idx)s, %(path)s, %(bytes)s, %(sha256)s,
                    %(input_hash)s, %(stage)s, %(is_master)s)
            ON CONFLICT (job_id, kind, idx, input_hash) WHERE deleted_at IS NULL AND job_id IS NOT NULL
            DO UPDATE SET path = EXCLUDED.path, bytes = EXCLUDED.bytes, sha256 = EXCLUDED.sha256,
                          stage = EXCLUDED.stage, is_master = EXCLUDED.is_master OR artifacts.is_master
            RETURNING {ARTIFACT_COLUMNS}
            """,
            params,
        ).fetchone()
    return conn.execute(
        f"""
        INSERT INTO artifacts (job_id, source_id, kind, idx, path, bytes, sha256, input_hash, stage,
                                   is_master)
        VALUES (NULL, %(source_id)s, %(kind)s, %(idx)s, %(path)s, %(bytes)s, %(sha256)s,
                %(input_hash)s, %(stage)s, %(is_master)s)
        RETURNING {ARTIFACT_COLUMNS}
        """,
        params,
    ).fetchone()


def find_live(conn: Any, job_id: int, kind: str, input_hash: str, idx: int = 0) -> Row | None:
    """Кеш стадии: живой артефакт с тем же входом."""
    return conn.execute(
        f"""
        SELECT {ARTIFACT_COLUMNS} FROM artifacts
        WHERE job_id = %(job_id)s AND kind = %(kind)s AND idx = %(idx)s AND input_hash = %(h)s
          AND deleted_at IS NULL
        """,
        {"job_id": job_id, "kind": kind, "idx": int(idx), "h": input_hash},
    ).fetchone()


def list_for_job(
    conn: Any, job_id: int, *, kinds: Sequence[str] | None = None, include_deleted: bool = False
) -> list[Row]:
    where = ["job_id = %(job_id)s"]
    params: dict[str, Any] = {"job_id": job_id}
    if not include_deleted:
        where.append("deleted_at IS NULL")
    if kinds:
        where.append("kind = ANY(%(kinds)s)")
        params["kinds"] = list(kinds)
    return conn.execute(
        f"SELECT {ARTIFACT_COLUMNS} FROM artifacts WHERE {' AND '.join(where)} ORDER BY kind, idx, id",
        params,
    ).fetchall()


def list_for_source(conn: Any, source_id: int, *, include_deleted: bool = False) -> list[Row]:
    deleted = "" if include_deleted else " AND deleted_at IS NULL"
    return conn.execute(
        f"SELECT {ARTIFACT_COLUMNS} FROM artifacts WHERE source_id = %(sid)s{deleted} ORDER BY kind, idx, id",
        {"sid": source_id},
    ).fetchall()


def latest(conn: Any, job_id: int, kind: str, idx: int = 0) -> Row | None:
    return conn.execute(
        f"""
        SELECT {ARTIFACT_COLUMNS} FROM artifacts
        WHERE job_id = %(job_id)s AND kind = %(kind)s AND idx = %(idx)s AND deleted_at IS NULL
        ORDER BY id DESC LIMIT 1
        """,
        {"job_id": job_id, "kind": kind, "idx": int(idx)},
    ).fetchone()


def soft_delete_stages(conn: Any, job_id: int, stages: Sequence[str]) -> int:
    """Rework: артефакты стадий ≥ from_stage получают deleted_at (файлы убирает cleanup)."""
    cur = conn.execute(
        """
        UPDATE artifacts SET deleted_at = now()
        WHERE job_id = %(job_id)s AND stage = ANY(%(stages)s) AND deleted_at IS NULL AND NOT is_master
        """,
        {"job_id": job_id, "stages": list(stages)},
    )
    return cur.rowcount


def soft_delete(conn: Any, artifact_ids: Sequence[int]) -> int:
    cur = conn.execute(
        """
        UPDATE artifacts SET deleted_at = now()
        WHERE id = ANY(%(ids)s) AND deleted_at IS NULL AND NOT is_master
        """,
        {"ids": list(artifact_ids)},
    )
    return cur.rowcount


def list_deleted(conn: Any, *, limit: int = 1000) -> list[Row]:
    """Помеченные удалёнными — кандидаты на удаление файлов в cleanup."""
    return conn.execute(
        f"""
        SELECT {ARTIFACT_COLUMNS} FROM artifacts
        WHERE deleted_at IS NOT NULL AND NOT is_master
        ORDER BY deleted_at LIMIT %(limit)s
        """,
        {"limit": int(limit)},
    ).fetchall()
