"""Ассеты: шрифты, логотипы, картинки, музыка, b-roll, эталоны водяных знаков (§5.2 `/assets`).

Файл приходит tus-загрузкой (`uploads`, purpose=asset, status=complete), переносится в
`MEDIA_ROOT/assets/<kind>/<sha256>.<ext>` (контентная адресация), загрузка помечается consumed.
Шрифт проверяется fontTools: без кириллицы — 422 `font_no_cyrillic` (служебный текст баннера и
субтитры — по-русски). Тип файла определяется по сигнатуре, не по имени.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from autoshorts.core.errors import AppError, NotFound
from autoshorts.core.storage import get_layout, sha256_file
from autoshorts.repos import assets as assets_repo
from autoshorts.services import audit
from autoshorts.services.audit import Actor
from autoshorts.services.products import field_error

ASSETS_DIR = "assets"
CYRILLIC_REQUIRED = [chr(c) for c in range(0x0410, 0x0450)] + ["Ё", "ё"]
IMAGE_KINDS = frozenset({"image", "logo", "watermark_ref"})

_SIGNATURES: tuple[tuple[bytes, int, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", 0, "image/png", "png"),
    (b"\xff\xd8\xff", 0, "image/jpeg", "jpg"),
    (b"GIF8", 0, "image/gif", "gif"),
    (b"OTTO", 0, "font/otf", "otf"),
    (b"\x00\x01\x00\x00", 0, "font/ttf", "ttf"),
    (b"true", 0, "font/ttf", "ttf"),
    (b"wOFF", 0, "font/woff", "woff"),
    (b"wOF2", 0, "font/woff2", "woff2"),
    (b"ID3", 0, "audio/mpeg", "mp3"),
    (b"OggS", 0, "audio/ogg", "ogg"),
    (b"fLaC", 0, "audio/flac", "flac"),
    (b"\x1a\x45\xdf\xa3", 0, "video/webm", "webm"),
)
_ALLOWED_MIME = {
    "font": {"font/ttf", "font/otf"},
    "logo": {"image/png", "image/jpeg", "image/webp", "image/gif"},
    "image": {"image/png", "image/jpeg", "image/webp", "image/gif"},
    "watermark_ref": {"image/png", "image/jpeg", "image/webp"},
    "music": {"audio/mpeg", "audio/ogg", "audio/flac", "audio/wav", "audio/mp4"},
    "broll": {"video/mp4", "video/webm", "video/quicktime"},
}


class FontNoCyrillic(AppError):
    status = 422
    code = "font_no_cyrillic"
    default_message = "В шрифте нет кириллицы"


def sniff_mime(path: Path) -> tuple[str, str]:
    """(mime, расширение) по сигнатуре; неизвестное — application/octet-stream."""
    with open(path, "rb") as f:
        head = f.read(64)
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp", "webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav", "wav"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"M4A ", b"M4B "):
            return "audio/mp4", "m4a"
        if brand == b"qt  ":
            return "video/quicktime", "mov"
        return "video/mp4", "mp4"
    if len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return "audio/mpeg", "mp3"
    for magic, offset, mime, ext in _SIGNATURES:
        if head[offset : offset + len(magic)] == magic:
            return mime, ext
    return "application/octet-stream", "bin"


def font_meta(path: Path) -> dict[str, Any]:
    """Семейство, начертание и покрытие кириллицы (fontTools). Битый шрифт → ValueError."""
    from fontTools.ttLib import TTFont

    try:
        with TTFont(str(path), lazy=True) as font:
            cmap = font.getBestCmap() or {}
            name = font["name"]
            family = name.getBestFamilyName() or path.stem
            style = name.getBestSubFamilyName() or "Regular"
    except Exception as exc:  # noqa: BLE001 - fontTools бросает что угодно на битом файле
        raise ValueError(f"font is not readable: {exc}") from None
    missing = [ch for ch in CYRILLIC_REQUIRED if ord(ch) not in cmap]
    return {"cyrillic_ok": not missing, "missing": missing, "family": str(family), "style": str(style)}


def to_api(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "name": row["name"],
        "bytes": int(row["bytes"]),
        "mime": row["mime"],
        "license_note": row["license_note"],
        "meta": row["meta"] or {},
        "file_url": f"/api/v1/assets/{row['id']}/file",
        "created_at": row["created_at"],
    }


def list_assets(kind: str | None) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        return {"items": [to_api(r) for r in assets_repo.list_live(conn, kind=kind)]}


def require_asset(conn: Any, asset_id: int) -> dict[str, Any]:
    row = assets_repo.get(conn, asset_id)
    if row is None:
        raise NotFound("asset", "Ассет не найден")
    return row


def create_asset(*, upload_id: str, kind: str, name: str, license_note: str, actor: Actor) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    layout = get_layout()
    with connection() as conn:
        upload = assets_repo.get_upload(conn, upload_id, for_update=True)
        if upload is None:
            raise field_error("upload_id", "Загрузка не найдена", "not_found")
        if upload["status"] != "complete" or upload["purpose"] != "asset":
            raise field_error("upload_id", "Загрузка не завершена или не для ассета", "upload_state")
        try:
            src = layout.resolve(upload["path"])
        except ValueError:
            raise field_error("upload_id", "Файл загрузки недоступен", "upload_state") from None
        if not src.is_file():
            raise field_error("upload_id", "Файл загрузки не найден на диске", "upload_state")
        mime, ext = sniff_mime(src)
        if mime not in _ALLOWED_MIME.get(kind, set()):
            raise field_error("kind", f"Файл {mime} не подходит для ассета {kind}", "mime_mismatch")
        meta: dict[str, Any] = {"original_filename": upload["filename"]}
        if kind == "font":
            try:
                info = font_meta(src)
            except ValueError:
                raise field_error("upload_id", "Шрифт не читается", "font_invalid") from None
            if not info["cyrillic_ok"]:
                raise FontNoCyrillic(
                    "В шрифте нет кириллицы: служебный текст и субтитры будут квадратиками",
                    details={"missing": info["missing"][:40]},
                )
            meta.update(info)
        digest = sha256_file(src)
        rel = f"{ASSETS_DIR}/{kind}/{digest}.{ext}"
        dst = layout.root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            tmp = dst.with_name(f".{dst.name}.tmp")
            shutil.copyfile(src, tmp)
            os.chmod(tmp, 0o640)
            os.replace(tmp, dst)
        row = assets_repo.insert(
            conn,
            kind=kind,
            name=name.strip(),
            path=rel,
            bytes_=dst.stat().st_size,
            mime=mime,
            license_note=license_note.strip(),
            meta=meta,
            created_by=actor.user_id,
        )
        assets_repo.consume_upload(conn, upload_id)
        audit.record(
            conn,
            actor,
            "asset.create",
            "asset",
            row["id"],
            summary=f"ассет {kind}: {row['name']}",
            diff={"kind": kind, "mime": mime, "bytes": row["bytes"]},
        )
    src.unlink(missing_ok=True)
    return to_api(row)


def asset_file(asset_id: int) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        return require_asset(conn, asset_id)


def delete_asset(asset_id: int, actor: Actor) -> None:
    from autoshorts.db.pool import connection

    with connection() as conn:
        if not assets_repo.soft_delete(conn, asset_id):
            raise NotFound("asset", "Ассет не найден")
        audit.record(conn, actor, "asset.delete", "asset", asset_id, summary="ассет удалён")
