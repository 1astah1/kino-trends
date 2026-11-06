"""Библиотека загруженных креативов баннера (решение владельца 2026-09-26, OWNER-DECISIONS п. 1).

    upload_creative(fileobj, filename=..., name=..., product_id=..., actor=...)
      → {"creative": {...}, "deduplicated": bool}

* Тело ≤ 50 МБ (`banner_assets.DEFAULT_LIMITS.max_animated_bytes`, = `MAX_CREATIVE_UPLOAD_BYTES`
  API) пишется во временный файл `MEDIA_ROOT/tmp`, проверяется `banner_validate.validate_upload`
  (сигнатура, лимиты по заголовку) и адресуется sha256 исходных байтов.
* Статика (PNG/JPG/WebP) нормализуется сразу `banner_assets.ingest_creative` → 201 `ready`.
* Анимация (GIF, APNG, анимированный WebP, WebM, MP4/MOV) → исходник в
  `MEDIA_ROOT/uploads/creative_<sha>.<ext>`, тяжёлая задача `banner_creative_normalize`
  (VP9 WebM с альфой) → 201 `processing` + `task_id`; готовность — `task_progress` SSE и GET.
* Отказ файла: 413 `payload_too_large` (больше лимита), 415 `unsupported_media_type` (не тот
  формат), 422 `creative_rejected` (битый, мелкий, длинный, …); во всех `details.asset_error` —
  `CreativeRejectCode` (= `BannerAssetError.code`).
* Удаление мягкое; пока на креатив ссылаются варианты — 409 `creative_in_use` (`details.variant_ids`).
  Файлы удалённого креатива остаются до уборки (cleanup), повторная загрузка возвращает его.

Обработчик задачи регистрируется здесь (`@register_task`); воркер подхватывает его модулем
`tasks/banner_creatives.py` (реэкспорт) или флагом `--import autoshorts.services.banner_creatives`.
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any, BinaryIO

from psycopg import sql

from autoshorts.core.errors import (
    AppError,
    Conflict,
    NotFound,
    PayloadTooLarge,
    PermanentError,
    RetryableError,
    UnsupportedMediaType,
    ValidationFailed,
)
from autoshorts.core.storage import get_layout
from autoshorts.domain.banner_schema import BannerCreative as DomainCreative
from autoshorts.pipeline.banner_assets import (
    DEFAULT_LIMITS,
    NORMALIZER_VERSION,
    BannerAssetError,
    creative_dir,
    ingest_creative,
    sha256_of,
    sniff_file,
)
from autoshorts.pipeline.banner_validate import validate_upload
from autoshorts.repos import banner_creatives as creatives_repo
from autoshorts.repos import products as products_repo
from autoshorts.services import audit
from autoshorts.services.audit import Actor
from autoshorts.services.products import field_error
from autoshorts.tasks.registry import TaskContext, register_task

log = logging.getLogger(__name__)

TASK_KIND = "banner_creative_normalize"
TASK_MAX_ATTEMPTS = 2
MAX_UPLOAD_BYTES = DEFAULT_LIMITS.max_animated_bytes
CHUNK = 1024 * 1024
URL_PREFIX = "/api/v1/banner-creatives"

_EXT = {
    "png": "png",
    "jpeg": "jpg",
    "webp": "webp",
    "gif": "gif",
    "apng": "png",
    "webp_animated": "webp",
    "webm": "webm",
    "mp4": "mp4",
    "mov": "mov",
}
_MEDIA = {"file": "normalized_path", "thumb": "thumb_path", "first_frame": "first_frame_path"}


class CreativeRejected(AppError):
    status = 422
    code = "creative_rejected"
    default_message = "Файл креатива не принят"


class CreativeInUse(Conflict):
    code = "creative_in_use"
    default_message = "Креатив используется в вариантах баннера"


# ---------------------------------------------------------------- ответы


def media_url(sha: str, kind: str) -> str:
    return f"{URL_PREFIX}/{sha}/media/{kind}"


def to_api(row: dict[str, Any]) -> dict[str, Any]:
    ready = row["status"] == "ready"
    sha = row["sha256"]
    return {
        "sha256": sha,
        "name": row["name"],
        "status": row["status"],
        "error_code": row["error_code"],
        "error_message": row["error_message"],
        "kind": row["kind"],
        "source_format": row["source_format"],
        "source_bytes": int(row["source_bytes"]),
        "source_width": row["source_width"],
        "source_height": row["source_height"],
        "width": row["width"],
        "height": row["height"],
        "has_alpha": row["has_alpha"],
        "duration_sec": float(row["duration_sec"]) if row["duration_sec"] is not None else None,
        "fps": float(row["fps"]) if row["fps"] is not None else None,
        "frame_count": row["frame_count"],
        "loop": row["loop"],
        "normalized_mime": row["normalized_mime"],
        "normalized_bytes": row["normalized_bytes"],
        "file_url": media_url(sha, "file") if ready else None,
        "thumb_url": media_url(sha, "thumb") if ready else None,
        "first_frame_url": media_url(sha, "first_frame") if ready else None,
        "product_id": row["product_id"],
        "notes": row["notes"],
        "used_by_variants": int(row.get("used_by_variants") or 0),
        "task_id": row["task_id"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def brief(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "sha256": row["sha256"],
        "name": row["name"],
        "kind": row["kind"],
        "status": row["status"],
        "width": row["width"],
        "height": row["height"],
        "thumb_url": media_url(row["sha256"], "thumb") if row["status"] == "ready" else None,
    }


def to_domain(row: dict[str, Any]) -> DomainCreative | None:
    """Готовый креатив из строки БД → доменная модель для banner_place/validate/overlay."""
    if row["status"] != "ready" or row.get("deleted_at") is not None:
        return None
    return DomainCreative(
        sha256=row["sha256"],
        kind=row["kind"],
        source_format=row["source_format"],
        width=int(row["width"]),
        height=int(row["height"]),
        source_width=int(row["source_width"] or row["width"]),
        source_height=int(row["source_height"] or row["height"]),
        has_alpha=bool(row["has_alpha"]),
        duration_sec=float(row["duration_sec"] or 0),
        fps=float(row["fps"] or 0),
        frame_count=int(row["frame_count"] or 1),
        loop=bool(row["loop"]),
        source_bytes=int(row["source_bytes"]),
        normalized_path=row["normalized_path"],
        normalized_mime=row["normalized_mime"],
        thumb_path=row["thumb_path"],
        first_frame_path=row["first_frame_path"],
        normalizer_version=int(row["normalizer_version"] or NORMALIZER_VERSION),
    )


def domain_creatives(conn: Any, shas: Sequence[str]) -> dict[str, DomainCreative]:
    """sha → готовый креатив (удалённые, в обработке и сбойные не попадают — их заменит плашка)."""
    out: dict[str, DomainCreative] = {}
    for sha, row in creatives_repo.get_many(conn, shas).items():
        creative = to_domain(row)
        if creative is not None:
            out[sha] = creative
    return out


# ---------------------------------------------------------------- чтение и правка


def _require(conn: Any, sha: str, *, for_update: bool = False) -> dict[str, Any]:
    row = creatives_repo.get(conn, sha, for_update=for_update)
    if row is None:
        raise NotFound("creative", "Креатив не найден")
    return row


def _order(sort: str | None) -> sql.Composable:
    """`?sort=-created_at` по белому списку; второй ключ — sha256 (у таблицы нет id)."""
    value = sort or "-created_at"
    desc = value.startswith("-")
    column = creatives_repo.SORTABLE.get(value.lstrip("-"))
    if column is None:
        raise ValidationFailed(
            "Недопустимая сортировка",
            details={
                "errors": [
                    {
                        "loc": ["query", "sort"],
                        "msg": f"Допустимо: {', '.join(sorted(creatives_repo.SORTABLE))} (с '-' — убыв.)",
                        "type": "sort_not_allowed",
                    }
                ]
            },
        )
    direction = sql.SQL("DESC" if desc else "ASC")
    return sql.SQL("ORDER BY {} {}, b.sha256 {}").format(
        sql.Identifier(*column.split(".")), direction, direction
    )


def list_creatives(
    *,
    kind: str | None,
    statuses: Sequence[str] | None,
    product_id: str | None,
    q: str | None,
    sort: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    order = _order(sort)
    with connection() as conn:
        rows, total = creatives_repo.list_page(
            conn,
            kind=kind,
            statuses=statuses,
            product_id=product_id,
            q=(q or "").strip() or None,
            order=order,
            limit=limit,
            offset=offset,
        )
    return {"items": [to_api(r) for r in rows], "total": total, "limit": limit, "offset": offset}


def get_creative(sha: str) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        return to_api(_require(conn, sha))


def _check_product(conn: Any, product_id: str | None) -> None:
    if product_id and products_repo.get(conn, product_id) is None:
        raise field_error("product_id", "Продукт не найден", "not_found")


def update_creative(sha: str, changes: dict[str, Any], actor: Actor) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    if "name" in changes and not (changes["name"] or "").strip():
        raise field_error("name", "Имя не может быть пустым", "none")
    fields = {k: (v.strip() if isinstance(v, str) and k == "name" else v) for k, v in changes.items()}
    with connection() as conn:
        _require(conn, sha, for_update=True)
        _check_product(conn, fields.get("product_id"))
        creatives_repo.update_meta(conn, sha, fields)
        audit.record(conn, actor, "banner_creative.update", "banner_creative", sha, diff=fields)
        return to_api(_require(conn, sha))


def delete_creative(sha: str, actor: Actor) -> None:
    from autoshorts.db.pool import connection

    with connection() as conn:
        row = _require(conn, sha, for_update=True)
        variant_ids = creatives_repo.variants_using(conn, sha)
        if variant_ids:
            raise CreativeInUse(
                "Креатив стоит в вариантах баннера — сначала замените его там",
                details={"variant_ids": variant_ids},
            )
        creatives_repo.soft_delete(conn, sha)
        audit.record(
            conn,
            actor,
            "banner_creative.delete",
            "banner_creative",
            sha,
            summary=f"креатив «{row['name']}» удалён",
        )


def creative_media(sha: str, media_kind: str) -> tuple[str, str, str]:
    """(relpath, mime, filename) медиа готового креатива; иначе 404."""
    from autoshorts.db.pool import connection

    with connection() as conn:
        row = _require(conn, sha)
    if row["status"] != "ready":
        raise NotFound("creative_media", "Креатив ещё не готов")
    rel = row[_MEDIA[media_kind]]
    mime = row["normalized_mime"] if media_kind == "file" else "image/png"
    ext = "webm" if mime == "video/webm" else "png"
    return rel, mime, f"creative_{sha[:12]}_{media_kind}.{ext}"


# ---------------------------------------------------------------- загрузка


def _spool(fileobj: BinaryIO, dst: Path, limit: int) -> int:
    total = 0
    with open(dst, "wb") as out:
        while chunk := fileobj.read(CHUNK):
            total += len(chunk)
            if total > limit:
                raise PayloadTooLarge(
                    f"Файл больше {limit // (1024 * 1024)} МБ",
                    details={"asset_error": "file_too_large", "limit_bytes": limit},
                )
            out.write(chunk)
    return total


def _reject(code: str, message: str, details: dict[str, Any] | None = None) -> AppError:
    extra = {"asset_error": code, **{k: v for k, v in (details or {}).items() if k != "asset_error"}}
    if code == "file_too_large":
        return PayloadTooLarge(message, details=extra)
    if code == "unsupported_format":
        return UnsupportedMediaType(message, details=extra)
    return CreativeRejected(message, details=extra)


def _check_upload(path: Path) -> None:
    for w in validate_upload(path):
        if w.severity.value == "error":
            code = str(w.details.get("asset_error") or "corrupt_file")
            raise _reject(code, w.message, dict(w.details))


def _files_ok(row: dict[str, Any]) -> bool:
    root = get_layout().root
    return all(row.get(k) and (root / row[k]).is_file() for k in _MEDIA.values())


def _ingest_notes(sha: str) -> list[str]:
    import json

    path = creative_dir(get_layout().root, sha) / "notes.json"
    try:
        return list(json.loads(path.read_text(encoding="utf-8")).get("notes") or [])
    except (OSError, ValueError, AttributeError):
        return []


def _ready_fields(creative: DomainCreative) -> dict[str, Any]:
    root = get_layout().root
    normalized = root / creative.normalized_path
    return {
        "kind": str(creative.kind),
        "source_format": str(creative.source_format),
        "source_bytes": creative.source_bytes,
        "source_width": creative.source_width,
        "source_height": creative.source_height,
        "width": creative.width,
        "height": creative.height,
        "has_alpha": creative.has_alpha,
        "duration_sec": round(creative.duration_sec, 3),
        "fps": round(creative.fps, 3),
        "frame_count": creative.frame_count,
        "loop": creative.loop,
        "normalized_path": creative.normalized_path,
        "normalized_mime": creative.normalized_mime,
        "normalized_bytes": normalized.stat().st_size if normalized.is_file() else None,
        "thumb_path": creative.thumb_path,
        "first_frame_path": creative.first_frame_path,
        "normalizer_version": creative.normalizer_version,
    }


def _task_alive(conn: Any, task_id: int | None) -> bool:
    if task_id is None:
        return False
    row = conn.execute("SELECT status FROM tasks WHERE id = %(id)s", {"id": task_id}).fetchone()
    return row is not None and row["status"] in ("queued", "running")


def _default_name(filename: str | None, sha: str) -> str:
    base = os.path.basename((filename or "").replace("\\", "/")).strip()
    return (base or f"creative_{sha[:8]}")[:200]


def upload_creative(
    fileobj: BinaryIO,
    *,
    filename: str | None,
    name: str | None,
    product_id: str | None,
    actor: Actor,
    declared_size: int | None = None,
) -> dict[str, Any]:
    """Принять загруженный файл креатива → `{creative, deduplicated}` (см. docstring модуля)."""
    from autoshorts.db.pool import connection

    if declared_size is not None and declared_size > MAX_UPLOAD_BYTES:
        raise _reject(
            "file_too_large",
            f"Файл больше {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ",
            {"limit_bytes": MAX_UPLOAD_BYTES, "size_bytes": declared_size},
        )
    layout = get_layout()
    tmp_dir = layout.tmp_dir(create=True)
    tmp = tmp_dir / f"creative-{uuid.uuid4().hex}.upload"
    try:
        size = _spool(fileobj, tmp, MAX_UPLOAD_BYTES)
        if size == 0:
            raise _reject("empty_file", "Файл пустой")
        _check_upload(tmp)
        sniff = sniff_file(tmp)
        sha = sha256_of(tmp)
        title = (name or "").strip() or _default_name(filename, sha)
        with connection() as conn:
            _check_product(conn, product_id)
            existing = creatives_repo.get(conn, sha, include_deleted=True)
        if existing is not None and existing["status"] == "ready" and _files_ok(existing):
            return _dedupe(sha, existing, title if name else None, product_id, actor)
        if not sniff.animated:
            return _ingest_static(tmp, sha, title, product_id, filename, existing, actor)
        return _queue_animated(tmp, sha, sniff, size, title, product_id, filename, existing, actor)
    finally:
        tmp.unlink(missing_ok=True)


def _dedupe(
    sha: str, existing: dict[str, Any], name: str | None, product_id: str | None, actor: Actor
) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        if existing.get("deleted_at") is not None:
            creatives_repo.revive(conn, sha, name=name, product_id=product_id)
            audit.record(
                conn,
                actor,
                "banner_creative.restore",
                "banner_creative",
                sha,
                summary="креатив загружен снова",
            )
        row = creatives_repo.get(conn, sha)
    assert row is not None
    return {"creative": to_api(row), "deduplicated": True}


def _ingest_static(
    tmp: Path,
    sha: str,
    title: str,
    product_id: str | None,
    filename: str | None,
    existing: dict[str, Any] | None,
    actor: Actor,
) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    try:
        creative = ingest_creative(tmp, get_layout().root, force=existing is not None)
    except BannerAssetError as exc:
        raise _reject(exc.code, exc.message, {k: v for k, v in exc.details.items() if _jsonable(v)}) from None
    fields = _ready_fields(creative)
    meta = {"original_filename": filename, "notes": _ingest_notes(sha)}
    with connection() as conn:
        if existing is None:
            inserted = creatives_repo.insert(
                conn,
                {
                    **fields,
                    "sha256": sha,
                    "name": title,
                    "status": "ready",
                    "meta": meta,
                    "product_id": product_id,
                    "created_by": actor.user_id,
                },
            )
            if not inserted:  # гонка одинаковых загрузок: запись уже есть
                creatives_repo.set_ready(conn, sha, fields, meta=meta)
        else:
            creatives_repo.set_ready(conn, sha, fields, meta=meta)
            if existing.get("deleted_at") is not None:
                creatives_repo.revive(conn, sha, name=title, product_id=product_id)
        audit.record(
            conn,
            actor,
            "banner_creative.upload",
            "banner_creative",
            sha,
            summary=f"креатив «{title}» ({creative.source_format}, {creative.width}×{creative.height})",
            diff={"kind": "static", "source_bytes": creative.source_bytes},
        )
        row = creatives_repo.get(conn, sha)
    assert row is not None
    return {"creative": to_api(row), "deduplicated": existing is not None and existing["status"] == "ready"}


def _jsonable(v: Any) -> bool:
    return isinstance(v, str | int | float | bool) or v is None


def _queue_animated(
    tmp: Path,
    sha: str,
    sniff: Any,
    size: int,
    title: str,
    product_id: str | None,
    filename: str | None,
    existing: dict[str, Any] | None,
    actor: Actor,
) -> dict[str, Any]:
    from autoshorts.db.pool import connection
    from autoshorts.services.tasks import enqueue

    layout = get_layout()
    fmt = str(sniff.source_format)
    rel = f"uploads/creative_{sha}.{_EXT.get(fmt, 'bin')}"
    with connection() as conn:
        row = creatives_repo.get(conn, sha, include_deleted=True, for_update=True)
        if row is not None and row["status"] == "processing" and _task_alive(conn, row["task_id"]):
            if row.get("deleted_at") is not None:
                creatives_repo.revive(conn, sha, name=title, product_id=product_id)
            return {"creative": to_api(creatives_repo.get(conn, sha)), "deduplicated": True}
        dst = layout.root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tmp, dst)
        os.chmod(dst, 0o640)
        if row is None:
            creatives_repo.insert(
                conn,
                {
                    "sha256": sha,
                    "name": title,
                    "status": "processing",
                    "kind": "animated",
                    "source_format": fmt,
                    "source_bytes": size,
                    "source_width": sniff.width,
                    "source_height": sniff.height,
                    "upload_path": rel,
                    "meta": {"original_filename": filename},
                    "product_id": product_id,
                    "created_by": actor.user_id,
                },
            )
        else:
            creatives_repo.set_processing(conn, sha, upload_path=rel)
            if row.get("deleted_at") is not None:
                creatives_repo.revive(conn, sha, name=title, product_id=product_id)
        queued = enqueue(
            conn,
            TASK_KIND,
            resource_class="heavy",
            max_attempts=TASK_MAX_ATTEMPTS,
            payload={"sha256": sha},
            dedup_key=f"banner_creative:{sha}",
        )
        creatives_repo.set_task(conn, sha, int(queued.task["id"]))
        audit.record(
            conn,
            actor,
            "banner_creative.upload",
            "banner_creative",
            sha,
            summary=f"креатив «{title}» ({fmt}) — нормализация в очереди",
            diff={"kind": "animated", "source_bytes": size, "task_id": int(queued.task["id"])},
        )
        out = creatives_repo.get(conn, sha)
    assert out is not None
    return {"creative": to_api(out), "deduplicated": False}


# ---------------------------------------------------------------- задача нормализации анимации


def _finish_failed(sha: str, code: str, message: str, upload: Path | None) -> None:
    from autoshorts.db.pool import connection

    with connection() as conn:
        creatives_repo.set_failed(conn, sha, error_code=code, error_message=message)
    if upload is not None:
        upload.unlink(missing_ok=True)


@register_task(TASK_KIND, "heavy", max_attempts=TASK_MAX_ATTEMPTS, priority=60)
def normalize_creative_task(ctx: TaskContext) -> dict[str, Any] | None:
    """Нормализовать анимированный креатив (VP9 WebM, альфа) и отметить его `ready` / `failed`."""
    from autoshorts.db.pool import connection

    sha = str(ctx.payload.get("sha256") or "")
    with connection() as conn:
        row = creatives_repo.get(conn, sha, include_deleted=True)
    if row is None or row["status"] != "processing" or not row["upload_path"]:
        return {"sha256": sha, "skipped": True}
    layout = get_layout()
    upload = layout.resolve(row["upload_path"])
    if not upload.is_file():
        _finish_failed(sha, "normalize_failed", "Исходный файл пропал до нормализации", None)
        raise PermanentError("creative upload file is missing", error_class="normalize_failed")
    ctx.progress(0.05)
    ctx.check_cancelled()
    try:
        creative = ingest_creative(upload, layout.root, force=True)
    except BannerAssetError as exc:
        _finish_failed(sha, exc.code, exc.message, upload)
        raise PermanentError(f"{exc.code}: {exc.message}", error_class=exc.code) from None
    except Exception as exc:  # noqa: BLE001 - ffmpeg/Pillow: повтор, на последней попытке — failed
        if ctx.attempt >= int(ctx.task.get("max_attempts") or TASK_MAX_ATTEMPTS):
            _finish_failed(sha, "normalize_failed", f"Нормализация не удалась: {exc}"[:500], upload)
            raise PermanentError(str(exc), error_class="normalize_failed") from None
        raise RetryableError(str(exc)) from None
    fields = _ready_fields(creative)
    with connection() as conn:
        creatives_repo.set_ready(conn, sha, fields, meta={"notes": _ingest_notes(sha)})
    upload.unlink(missing_ok=True)
    ctx.progress(1.0)
    return {"sha256": sha, "status": "ready", "duration_sec": fields["duration_sec"]}
