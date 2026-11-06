"""Возобновляемая загрузка tus 1.0.0 (docs/ARCHITECTURE.md §5.2 «Загрузки», §6.3.1).

Реализация — M5-A. Эндпоинты (тег `uploads`), все под `operator`:

* `OPTIONS /uploads/tus` → 204 `Tus-Version: 1.0.0`, `Tus-Extension: creation,termination,expiration`,
  `Tus-Max-Size`;
* `POST /uploads/tus` (`Upload-Length`, `Upload-Metadata` base64: filename, filetype, purpose)
  → 201 `Location`, `Upload-Expires` · 413 · 507;
* `HEAD /uploads/tus/{upload_id}` → 200 `Upload-Offset`, `Upload-Length`, `Cache-Control: no-store`
  · 404 · 410;
* `PATCH /uploads/tus/{upload_id}` (`application/offset+octet-stream`, `Upload-Offset`, тело ≤ 16 МБ)
  → 204 `Upload-Offset` · 409 · 410 · 413 · 415;
* `DELETE /uploads/tus/{upload_id}` → 204 (termination);
* `GET /uploads/{upload_id}` → `UploadInfo` (JSON для UI: статус, получено байт).

Правила протокола:
* каждый запрос, кроме OPTIONS, несёт `Tus-Resumable: 1.0.0`, иначе 412 `tus_version_unsupported`;
  каждый ответ — `Tus-Resumable: 1.0.0`;
* чанк PATCH — не больше `TUS_CHUNK_MAX_BYTES` (16 МБ: nginx `client_max_body_size 17m`), иначе 413;
  клиент (tus-js-client) ставит `chunkSize: 16 * 1024 * 1024`;
* `Upload-Offset` ≠ уже получено → 409 `upload_offset_mismatch` (`details.offset`);
* истёкшая сессия (`Upload-Expires`, 24 ч с последнего PATCH) → 410 `upload_expired`;
* `Upload-Length` > `upload_max_gb` (6 ГБ) → 413; свободного диска меньше длины + `disk_hard_min_gb` → 507;
* `Location` — относительный URL `/api/v1/uploads/tus/<upload_id>` (32 hex);
* после последнего байта статус `complete`, `POST /sources/from-upload` переводит его в `consumed`.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime

from pydantic import Field

from autoshorts.api.schemas.common import ApiModel
from autoshorts.domain.enums import UploadPurpose, UploadStatus

__all__ = [
    "TUS_CHUNK_MAX_BYTES",
    "TUS_CONTENT_TYPE",
    "TUS_EXTENSIONS",
    "TUS_EXPIRES_HOURS",
    "TUS_MAX_SIZE_BYTES",
    "TUS_METADATA_REQUIRED",
    "TUS_VERSION",
    "UPLOAD_ID_PATTERN",
    "UploadInfo",
    "decode_upload_metadata",
    "encode_upload_metadata",
]

TUS_VERSION = "1.0.0"
TUS_EXTENSIONS: tuple[str, ...] = ("creation", "termination", "expiration")
TUS_CONTENT_TYPE = "application/offset+octet-stream"
TUS_CHUNK_MAX_BYTES = 16 * 1024 * 1024
"""Максимальный чанк PATCH (решение ведущего M5: 16 МБ вместо 32 МБ из §5.2)."""
TUS_MAX_SIZE_BYTES = 6 * 1024**3
"""`Tus-Max-Size` по умолчанию = settings.upload_max_gb (6 ГБ); сервер отдаёт текущее значение."""
TUS_EXPIRES_HOURS = 24
TUS_METADATA_REQUIRED: tuple[str, ...] = ("filename", "purpose")
"""Обязательные ключи `Upload-Metadata`; `filetype` желателен. Остальные ключи уходят в uploads.metadata."""
UPLOAD_ID_PATTERN = r"^[a-f0-9]{32}$"

TUS_HEADERS = {
    "resumable": "Tus-Resumable",
    "version": "Tus-Version",
    "extension": "Tus-Extension",
    "max_size": "Tus-Max-Size",
    "length": "Upload-Length",
    "offset": "Upload-Offset",
    "metadata": "Upload-Metadata",
    "expires": "Upload-Expires",
}


class UploadInfo(ApiModel):
    """Состояние загрузки для UI (tus-клиенту хватает HEAD)."""

    id: str = Field(pattern=UPLOAD_ID_PATTERN)
    purpose: UploadPurpose
    filename: str
    filetype: str | None
    length_bytes: int
    received_bytes: int
    status: UploadStatus
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    completed_at: datetime | None


def decode_upload_metadata(header: str | None) -> dict[str, str]:
    """Разбор `Upload-Metadata`: пары `key base64value` через запятую (tus 1.0.0).

    Ключ — латиница без пробелов и запятых; значение — base64 UTF-8 (может отсутствовать → "").
    Ошибка формата — ValueError (API отвечает 400 bad_request).
    """
    out: dict[str, str] = {}
    if not header or not header.strip():
        return out
    for pair in header.split(","):
        parts = pair.strip().split(" ")
        if not parts or not parts[0] or len(parts) > 2:
            raise ValueError(f"bad Upload-Metadata pair: {pair!r}")
        key = parts[0]
        if not key.isascii() or not key.replace("_", "").replace("-", "").replace(".", "").isalnum():
            raise ValueError(f"bad Upload-Metadata key: {key!r}")
        if key in out:
            raise ValueError(f"duplicate Upload-Metadata key: {key!r}")
        value = ""
        if len(parts) == 2:
            try:
                value = base64.b64decode(parts[1], validate=True).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError) as exc:
                raise ValueError(f"bad base64 in Upload-Metadata for {key!r}") from exc
        out[key] = value
    return out


def encode_upload_metadata(values: dict[str, str]) -> str:
    """Обратная операция (тесты, эталон для клиента)."""
    return ",".join(
        f"{k} {base64.b64encode(v.encode('utf-8')).decode('ascii')}" if v else k for k, v in values.items()
    )
