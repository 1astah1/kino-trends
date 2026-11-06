"""Сервер tus 1.0.0: создание, смещение, приём чанков, прерывание (docs/ARCHITECTURE.md §5.2, §6.3.1, §8.2).

Контракт — `api/schemas/uploads.py`. Расширения: creation, termination, expiration.

* POST: `Upload-Length` ≤ лимита назначения (source — `upload_max_gb`, asset — 512 МБ, rights_doc —
  50 МБ), иначе 413; дисковый сторож (`core.resources.ensure_upload_space`) учитывает длину новой
  загрузки и недополученные байты живых сессий — меньше `disk_soft_min_gb` после записи → 507.
  Файл `MEDIA_ROOT/uploads/<id>.part` создаётся сразу (пустой).
* PATCH: тело читается потоком из `request.stream()`, пишется `os.pwrite` по смещению блоками по
  1 МБ (запись и fsync — в пуле потоков), чанк ≤ 16 МБ. Параллельные PATCH одной загрузки
  исключены `flock` на файле: второй получает 409 и продолжает по HEAD. Обрыв клиента посреди
  чанка не теряет принятое: смещение фиксируется по фактически записанным байтам.
* Последний байт: файл обрезается ровно до `Upload-Length`, переименовывается в
  `uploads/<id>.<ext>` (уборка `.part` старше суток его не трогает), статус `complete`.
* Срок сессии — 24 ч с последнего чанка (`Upload-Expires`); истёкшая → 410 `upload_expired`,
  `.part` удаляется. Прерванная (DELETE) → 404.
* Доступ: владелец загрузки или admin, чужая загрузка неотличима от несуществующей (404).
"""

from __future__ import annotations

import contextlib
import email.utils
import errno
import fcntl
import logging
import os
import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autoshorts.api.schemas.uploads import (
    TUS_CHUNK_MAX_BYTES,
    TUS_CONTENT_TYPE,
    TUS_EXPIRES_HOURS,
    TUS_EXTENSIONS,
    TUS_METADATA_REQUIRED,
    TUS_VERSION,
    decode_upload_metadata,
)
from autoshorts.core.errors import (
    AppError,
    BadRequest,
    Conflict,
    InsufficientStorage,
    NotFound,
    PayloadTooLarge,
    UnsupportedMediaType,
)
from autoshorts.core.storage import get_layout
from autoshorts.domain.enums import Role, UploadPurpose, UploadStatus
from autoshorts.repos import uploads as uploads_repo

log = logging.getLogger(__name__)

GIB = 1024**3
MIB = 1024**2
WRITE_BLOCK_BYTES = 1 * MIB
PURPOSE_MAX_BYTES: dict[str, int] = {
    UploadPurpose.ASSET.value: 512 * MIB,
    UploadPurpose.RIGHTS_DOC.value: 50 * MIB,
}
"""Потолок `Upload-Length` для назначений, кроме source (у source — настройка `upload_max_gb`)."""
FILENAME_MAX = 255
_EXT_RE = re.compile(r"^[a-z0-9]{1,8}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class TusVersionUnsupported(AppError):
    status = 412
    code = "tus_version_unsupported"
    default_message = "Нужен заголовок Tus-Resumable: 1.0.0"


class UploadExpired(AppError):
    status = 410
    code = "upload_expired"
    default_message = "Загрузка просрочена: начните заново"


class UploadOffsetMismatch(Conflict):
    code = "upload_offset_mismatch"
    default_message = "Смещение чанка не совпадает с полученным: продолжите с Upload-Offset из HEAD"


# ---------------------------------------------------------------- заголовки и проверки


def base_headers() -> dict[str, str]:
    return {"Tus-Resumable": TUS_VERSION}


def http_date(dt: datetime) -> str:
    """RFC 7231 (`Upload-Expires`)."""
    aware = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return email.utils.format_datetime(aware.astimezone(UTC), usegmt=True)


def check_resumable(value: str | None) -> None:
    if (value or "").strip() != TUS_VERSION:
        raise TusVersionUnsupported(details={"tus_version": TUS_VERSION})


def _settings() -> Any:
    from autoshorts.services.settings import get_runtime_settings

    return get_runtime_settings()


def source_max_bytes(settings: Any | None = None) -> int:
    rs = settings or _settings()
    return int(float(rs.upload_max_gb) * GIB)


def max_bytes_for(purpose: str, settings: Any | None = None) -> int:
    if purpose == UploadPurpose.SOURCE.value:
        return source_max_bytes(settings)
    return PURPOSE_MAX_BYTES[purpose]


def options_headers() -> dict[str, str]:
    """OPTIONS: возможности сервера (Tus-Max-Size — текущая настройка upload_max_gb)."""
    return {
        **base_headers(),
        "Tus-Version": TUS_VERSION,
        "Tus-Extension": ",".join(TUS_EXTENSIONS),
        "Tus-Max-Size": str(source_max_bytes()),
    }


def clean_filename(raw: str) -> str:
    """Имя без каталогов и управляющих символов (кириллица сохраняется: это данные)."""
    name = unicodedata.normalize("NFC", raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _CONTROL_RE.sub("", name).strip().strip(".")
    return (name or "upload")[:FILENAME_MAX]


def file_ext(filename: str) -> str:
    suffix = Path(filename).suffix.lower().lstrip(".")
    return suffix if _EXT_RE.match(suffix) else "bin"


def _visible(row: dict[str, Any] | None, principal: Any) -> dict[str, Any]:
    if row is None or (int(row["user_id"]) != int(principal.user_id) and not principal.at_least(Role.ADMIN)):
        raise NotFound("upload", "Загрузка не найдена")
    return row


def _part_path(row: dict[str, Any]) -> Path:
    return get_layout().resolve(row["path"])


def _drop_file(row: dict[str, Any]) -> None:
    with contextlib.suppress(OSError, ValueError):
        _part_path(row).unlink(missing_ok=True)


def _expire(conn: Any, row: dict[str, Any]) -> None:
    uploads_repo.set_status(conn, row["id"], UploadStatus.EXPIRED.value, only_from=("uploading",))
    _drop_file(row)


def _live_row(conn: Any, upload_id: str, principal: Any, *, for_update: bool = False) -> dict[str, Any]:
    """Строка загрузки для HEAD/PATCH: прерванная — 404, истёкшая — 410 (и помечается expired)."""
    row = _visible(uploads_repo.get(conn, upload_id, for_update=for_update), principal)
    status = row["status"]
    if status == UploadStatus.ABORTED.value:
        raise NotFound("upload", "Загрузка прервана")
    if status == UploadStatus.EXPIRED.value:
        raise UploadExpired()
    if status == UploadStatus.UPLOADING.value and row.get("is_expired"):
        _expire(conn, row)
        raise UploadExpired(details={"expired_at": row["expires_at"].isoformat()})
    return row


def offset_headers(row: dict[str, Any]) -> dict[str, str]:
    done = row["status"] in (UploadStatus.COMPLETE.value, UploadStatus.CONSUMED.value)
    headers = {
        **base_headers(),
        "Upload-Offset": str(int(row["length_bytes"] if done else row["received_bytes"])),
        "Upload-Length": str(int(row["length_bytes"])),
        "Cache-Control": "no-store",
    }
    if row["status"] == UploadStatus.UPLOADING.value:
        headers["Upload-Expires"] = http_date(row["expires_at"])
    return headers


# ---------------------------------------------------------------- создание


def _parse_metadata(header: str | None) -> tuple[str, str | None, str, dict[str, str]]:
    try:
        meta = decode_upload_metadata(header)
    except ValueError as exc:
        raise BadRequest(f"Upload-Metadata: {exc}", details={"header": "Upload-Metadata"}) from None
    missing = [k for k in TUS_METADATA_REQUIRED if not meta.get(k)]
    if missing:
        raise BadRequest(
            "В Upload-Metadata нет обязательных ключей: " + ", ".join(missing),
            details={"header": "Upload-Metadata", "missing": missing},
        )
    purpose = meta["purpose"].strip()
    if purpose not in {p.value for p in UploadPurpose}:
        raise BadRequest(
            "Неизвестное назначение загрузки",
            details={
                "header": "Upload-Metadata",
                "purpose": purpose,
                "allowed": [p.value for p in UploadPurpose],
            },
        )
    filetype = (meta.get("filetype") or "").strip()[:200] or None
    extra = {k: v[:1000] for k, v in meta.items() if k not in ("filename", "filetype", "purpose")}
    return clean_filename(meta["filename"]), filetype, purpose, extra


def _check_disk(conn: Any, length: int) -> None:
    from autoshorts.core.resources import admission_policy, ensure_upload_space
    from autoshorts.services.settings import get_runtime_settings

    policy = admission_policy(get_runtime_settings(conn))
    incoming = int(length) + uploads_repo.pending_bytes(conn)
    ensure_upload_space(get_layout().root, policy, incoming)


def create_upload(*, principal: Any, length: int | None, metadata_header: str | None) -> dict[str, Any]:
    """POST /uploads/tus → строка uploads (+ файл `.part`). 400 · 413 · 507."""
    from autoshorts.db.pool import connection

    if length is None:
        raise BadRequest("Нужен заголовок Upload-Length (отложенная длина не поддерживается)")
    filename, filetype, purpose, extra = _parse_metadata(metadata_header)
    limit = max_bytes_for(purpose)
    if length > limit:
        raise PayloadTooLarge(
            f"Файл больше допустимого для загрузки ({limit / GIB:.1f} ГБ)"
            if limit >= GIB
            else f"Файл больше допустимого для загрузки ({limit // MIB} МБ)",
            details={"limit_bytes": limit, "size_bytes": int(length), "purpose": purpose},
        )
    layout = get_layout()
    upload_id = secrets.token_hex(16)
    part = layout.upload_part(upload_id, create_dir=True)
    rel = layout.relpath(part)
    with connection() as conn:
        _check_disk(conn, int(length))
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        os.close(fd)
        try:
            row = uploads_repo.insert(
                conn,
                upload_id=upload_id,
                user_id=int(principal.user_id),
                purpose=purpose,
                filename=filename,
                filetype=filetype,
                length_bytes=int(length),
                path=rel,
                metadata=extra,
                expires_hours=TUS_EXPIRES_HOURS,
            )
            if int(length) == 0:
                row = _complete(conn, row, part)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
    return row


def location(upload_id: str) -> str:
    return f"/api/v1/uploads/tus/{upload_id}"


def create_headers(row: dict[str, Any]) -> dict[str, str]:
    headers = {**base_headers(), "Location": location(row["id"])}
    if row["status"] == UploadStatus.UPLOADING.value:
        headers["Upload-Expires"] = http_date(row["expires_at"])
    headers["Upload-Offset"] = str(int(row["received_bytes"]))
    return headers


# ---------------------------------------------------------------- HEAD / DELETE / GET


def head_upload(upload_id: str, principal: Any) -> dict[str, str]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        row = _live_row(conn, upload_id, principal)
    return offset_headers(row)


def terminate_upload(upload_id: str, principal: Any) -> None:
    """DELETE: сессия → aborted, файл удаляется. Повтор — 204; ставшая источником — 409."""
    from autoshorts.db.pool import connection

    with connection() as conn:
        row = _visible(uploads_repo.get(conn, upload_id, for_update=True), principal)
        if row["status"] == UploadStatus.CONSUMED.value:
            raise Conflict("Загрузка уже стала источником", code="upload_consumed")
        if row["status"] in (UploadStatus.UPLOADING.value, UploadStatus.COMPLETE.value):
            uploads_repo.set_status(conn, upload_id, UploadStatus.ABORTED.value)
    _drop_file(row)


def upload_info(upload_id: str, principal: Any) -> dict[str, Any]:
    """GET /uploads/{upload_id} → UploadInfo (истёкшая сессия помечается expired по дороге)."""
    from autoshorts.db.pool import connection

    with connection() as conn:
        row = _visible(uploads_repo.get(conn, upload_id), principal)
        if row["status"] == UploadStatus.UPLOADING.value and row.get("is_expired"):
            _expire(conn, row)
            row = uploads_repo.get(conn, upload_id) or row
    return {
        "id": row["id"],
        "purpose": row["purpose"],
        "filename": row["filename"],
        "filetype": row["filetype"],
        "length_bytes": int(row["length_bytes"]),
        "received_bytes": int(row["received_bytes"]),
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "expires_at": row["expires_at"],
        "completed_at": row["completed_at"],
    }


# ---------------------------------------------------------------- PATCH


@dataclass
class PatchPlan:
    upload_id: str
    offset: int
    length: int
    max_bytes: int  # сколько байт можно принять в этом чанке
    part: Path
    principal: Any


def begin_patch(
    upload_id: str,
    principal: Any,
    *,
    offset: int | None,
    content_type: str | None,
    content_length: int | None,
) -> PatchPlan | dict[str, Any]:
    """Проверки до чтения тела. Готовая загрузка с верным смещением → её строка (ответ без записи)."""
    from autoshorts.db.pool import connection

    mtype = (content_type or "").split(";", 1)[0].strip().lower()
    if mtype != TUS_CONTENT_TYPE:
        raise UnsupportedMediaType(
            f"Чанк tus передаётся как {TUS_CONTENT_TYPE}", details={"content_type": mtype or None}
        )
    if offset is None:
        raise BadRequest("Нужен заголовок Upload-Offset")
    with connection() as conn:
        row = _live_row(conn, upload_id, principal)
    length = int(row["length_bytes"])
    if row["status"] != UploadStatus.UPLOADING.value:  # complete / consumed
        if offset == length and not content_length:
            return row
        raise UploadOffsetMismatch(details={"offset": length})
    received = int(row["received_bytes"])
    if offset != received:
        raise UploadOffsetMismatch(details={"offset": received})
    remaining = length - received
    allowed = min(TUS_CHUNK_MAX_BYTES, remaining)
    if content_length is not None and content_length > allowed:
        over_length = content_length > remaining
        raise PayloadTooLarge(
            "Чанк выходит за Upload-Length" if over_length else "Чанк больше 16 МБ",
            details={
                "limit_bytes": allowed,
                "size_bytes": int(content_length),
                "chunk_max_bytes": TUS_CHUNK_MAX_BYTES,
            },
        )
    _check_chunk_disk(content_length or allowed)
    return PatchPlan(upload_id, offset, length, allowed, _part_path(row), principal)


def _check_chunk_disk(incoming: int) -> None:
    """Место уже зарезервировано при создании; здесь — только аварийный порог (hard)."""
    from autoshorts.core.resources import admission_policy, disk_free_gb

    policy = admission_policy(_settings())
    free = disk_free_gb(get_layout().root) - incoming / GIB
    if free < policy.disk_hard_min_gb:
        raise InsufficientStorage(
            "Мало места на диске: загрузка приостановлена",
            details={"free_gb": round(free, 2), "min_gb": policy.disk_hard_min_gb},
        )


def _pwrite_all(fd: int, data: bytes, pos: int) -> None:
    view = memoryview(data)
    while view:
        n = os.pwrite(fd, view, pos)
        view = view[n:]
        pos += n


@dataclass
class ChunkOutcome:
    written: int
    too_large: bool = False
    disconnected: bool = False


def _received_now(upload_id: str) -> int | None:
    from autoshorts.db.pool import connection

    with connection() as conn:
        row = uploads_repo.get(conn, upload_id)
    if row is None or row["status"] != UploadStatus.UPLOADING.value:
        return None
    return int(row["received_bytes"])


async def receive_chunk(plan: PatchPlan, stream: Any) -> tuple[ChunkOutcome, dict[str, Any]]:
    """Принять тело PATCH в файл по смещению плана и зафиксировать новое смещение — всё под `flock`
    файла загрузки (параллельный PATCH той же загрузки получает 409). Обрыв клиента — не ошибка:
    фиксируется то, что успело записаться."""
    from starlette.concurrency import run_in_threadpool
    from starlette.requests import ClientDisconnect

    try:
        fd = os.open(plan.part, os.O_WRONLY)
    except FileNotFoundError:
        raise UploadExpired("Файл загрузки пропал: начните заново") from None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                raise UploadOffsetMismatch(
                    "Эта загрузка уже принимает другой чанк", details={"offset": plan.offset, "locked": True}
                ) from None
            raise
        # пока ждали замок, соседний PATCH мог сдвинуть смещение
        current = await run_in_threadpool(_received_now, plan.upload_id)
        if current is None:
            raise NotFound("upload", "Загрузка прервана")
        if current != plan.offset:
            raise UploadOffsetMismatch(details={"offset": current})
        out = ChunkOutcome(0)
        buf = bytearray()
        try:
            async for chunk in stream:
                if not chunk:
                    continue
                if out.written + len(buf) + len(chunk) > plan.max_bytes:
                    out.too_large = True
                    break
                buf += chunk
                if len(buf) >= WRITE_BLOCK_BYTES:
                    data = bytes(buf)
                    buf.clear()
                    await run_in_threadpool(_pwrite_all, fd, data, plan.offset + out.written)
                    out.written += len(data)
        except ClientDisconnect:
            out.disconnected = True
        if buf and not out.too_large:
            data = bytes(buf)
            await run_in_threadpool(_pwrite_all, fd, data, plan.offset + out.written)
            out.written += len(data)
        if out.written and not out.too_large:
            await run_in_threadpool(os.fsync, fd)
        # отвергнутый (слишком большой) чанк не засчитывается целиком: записанное поверх смещения
        # безвредно — следующий PATCH с того же Upload-Offset его перезапишет
        row = await run_in_threadpool(finish_patch, plan, 0 if out.too_large else out.written)
        return out, row
    finally:
        os.close(fd)  # закрытие снимает flock


def _complete(conn: Any, row: dict[str, Any], part: Path) -> dict[str, Any]:
    layout = get_layout()
    final = part.with_name(f"{row['id']}.{file_ext(row['filename'])}")
    with open(part, "r+b") as fh:
        fh.truncate(int(row["length_bytes"]))
    os.replace(part, final)
    done = uploads_repo.mark_complete(conn, row["id"], path=layout.relpath(final))
    return done or row


def finish_patch(plan: PatchPlan, written: int) -> dict[str, Any]:
    """Зафиксировать новое смещение; последний байт → complete. Возвращает строку uploads."""
    from autoshorts.db.pool import connection

    new_offset = plan.offset + int(written)
    with connection() as conn:
        row = uploads_repo.get(conn, plan.upload_id, for_update=True)
        if row is None or row["status"] != UploadStatus.UPLOADING.value:
            raise NotFound("upload", "Загрузка прервана")
        if written:
            row = (
                uploads_repo.record_progress(
                    conn, plan.upload_id, received_bytes=new_offset, expires_hours=TUS_EXPIRES_HOURS
                )
                or row
            )
        if new_offset >= int(row["length_bytes"]):
            row = _complete(conn, row, plan.part)
    return row


def patch_headers(row: dict[str, Any]) -> dict[str, str]:
    headers = offset_headers(row)
    headers.pop("Upload-Length", None)
    headers.pop("Cache-Control", None)
    return headers


# ---------------------------------------------------------------- для источников и документов прав


def take_complete_upload(conn: Any, upload_id: str, principal: Any, *, purpose: str) -> dict[str, Any]:
    """Загрузка, готовая стать источником/документом (FOR UPDATE): 404 · 409 · 422."""
    from autoshorts.core.errors import ValidationFailed

    row = _visible(uploads_repo.get(conn, upload_id, for_update=True), principal)
    status = row["status"]
    if status == UploadStatus.CONSUMED.value:
        raise Conflict("Эта загрузка уже использована", code="upload_consumed")
    if status == UploadStatus.UPLOADING.value:
        if row.get("is_expired"):
            raise UploadExpired()
        raise Conflict(
            "Загрузка ещё не завершена",
            code="upload_not_complete",
            details={"received_bytes": int(row["received_bytes"]), "length_bytes": int(row["length_bytes"])},
        )
    if status == UploadStatus.EXPIRED.value:
        raise UploadExpired()
    if status != UploadStatus.COMPLETE.value:
        raise NotFound("upload", "Загрузка прервана")
    if row["purpose"] != purpose:
        raise ValidationFailed(
            "Загрузка сделана для другого назначения",
            details={
                "errors": [
                    {
                        "loc": ["body", "upload_id"],
                        "msg": f"Нужна загрузка с purpose={purpose}, у этой {row['purpose']}",
                        "type": "upload_purpose",
                    }
                ]
            },
        )
    path = _part_path(row)
    if not path.is_file():
        raise Conflict("Файл загрузки не найден на диске", code="upload_not_complete")
    return row


def upload_path(row: dict[str, Any]) -> Path:
    return _part_path(row)


def expire_stale_uploads() -> int:
    """Истёкшие незавершённые сессии → expired, их `.part` удаляются (для уборки)."""
    from autoshorts.db.pool import connection

    with connection() as conn:
        rows = uploads_repo.expire_stale(conn)
    for row in rows:
        _drop_file(row)
    return len(rows)


__all__ = [
    "ChunkOutcome",
    "PatchPlan",
    "TusVersionUnsupported",
    "UploadExpired",
    "UploadOffsetMismatch",
    "base_headers",
    "begin_patch",
    "check_resumable",
    "create_headers",
    "create_upload",
    "expire_stale_uploads",
    "finish_patch",
    "head_upload",
    "options_headers",
    "patch_headers",
    "receive_chunk",
    "take_complete_upload",
    "terminate_upload",
    "upload_info",
    "upload_path",
]
