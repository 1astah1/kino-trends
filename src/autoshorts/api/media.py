"""Отдача медиафайлов из `MEDIA_ROOT` (docs/ARCHITECTURE.md §5.1 «Медиа», §8.5 nginx).

    from autoshorts.api.media import media_response
    return media_response("jobs/a1b2c3/final.mp4", filename="taro_main_2026-09-26_k7m2q.mp4",
                          download=True, media_type="video/mp4")

- В prod (`APP_ENV=prod`) API не читает файл: отвечает пустым телом с
  `X-Accel-Redirect: /_protected_media/<relpath>`, а nginx отдаёт файл сам (Range, sendfile).
  `location /_protected_media/ { internal; alias <MEDIA_ROOT>/; }` — alias обязан совпадать с MEDIA_ROOT.
- В dev — `FileResponse` Starlette с Range (206/416), ETag и Last-Modified.
- Путь только относительный, внутри MEDIA_ROOT, ASCII (X-Accel), без `..` — иначе 404.
- Имя файла для `Content-Disposition` — латиница (кириллица и прочее транслитерируется в `_`),
  плюс `filename*` в UTF-8 для исходного имени.
"""

from __future__ import annotations

import mimetypes
import re
import unicodedata
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from starlette.responses import FileResponse, Response

from autoshorts.core.config import get_env
from autoshorts.core.errors import NotFound

X_ACCEL_PREFIX = "/_protected_media/"
DEFAULT_CACHE_CONTROL = "private, max-age=3600"
_SAFE_REL_RE = re.compile(r"[A-Za-z0-9._/-]+")
_FILENAME_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_relpath(relpath: str | PurePosixPath) -> str:
    """Проверить относительный путь внутри MEDIA_ROOT. Нарушение — 404 `media_not_found`."""
    text = str(relpath).replace("\\", "/")
    parts = PurePosixPath(text).parts
    if (
        not text
        or text.startswith("/")
        or not _SAFE_REL_RE.fullmatch(text)
        or any(p in ("..", ".") for p in parts)
    ):
        raise NotFound("media", "Файл не найден")
    return "/".join(parts)


_RU_LOWER = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}  # fmt: skip
_RU_TRANSLIT = str.maketrans(_RU_LOWER | {k.upper(): v.capitalize() for k, v in _RU_LOWER.items()})


def ascii_filename(name: str, *, default: str = "file") -> str:
    """Латинское имя для Content-Disposition: `Таро выпуск 1.mp4` → `Taro_vypusk_1.mp4`."""
    normalized = unicodedata.normalize("NFKD", name.translate(_RU_TRANSLIT))
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = _FILENAME_UNSAFE_RE.sub("_", ascii_only).strip("._")
    return cleaned[:150] or default


def content_disposition(filename: str, *, download: bool) -> str:
    kind = "attachment" if download else "inline"
    fallback = ascii_filename(filename)
    if fallback == filename:
        return f'{kind}; filename="{fallback}"'
    return f"{kind}; filename=\"{fallback}\"; filename*=utf-8''{quote(filename, safe='')}"


def resolve_media_path(relpath: str, media_root: Path | None = None) -> Path:
    root = (media_root or get_env().media_root).resolve()
    path = (root / safe_relpath(relpath)).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise NotFound("media", "Файл не найден")
    return path


def media_response(
    relpath: str,
    *,
    filename: str | None = None,
    download: bool = False,
    media_type: str | None = None,
    cache_control: str = DEFAULT_CACHE_CONTROL,
    x_accel: bool | None = None,
    media_root: Path | None = None,
) -> Response:
    """Ответ с файлом `MEDIA_ROOT/<relpath>`. `x_accel=None` — по окружению (prod → X-Accel)."""
    env = get_env()
    path = resolve_media_path(relpath, media_root)
    rel = path.relative_to((media_root or env.media_root).resolve()).as_posix()
    name = filename or path.name
    mtype = media_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
    headers = {
        "Content-Disposition": content_disposition(name, download=download),
        "Cache-Control": cache_control,
        "X-Content-Type-Options": "nosniff",
    }
    use_accel = env.is_prod if x_accel is None else x_accel
    if use_accel:
        headers["X-Accel-Redirect"] = X_ACCEL_PREFIX + quote(rel, safe="/")
        return Response(status_code=200, headers=headers, media_type=mtype)
    return FileResponse(path, media_type=mtype, headers=headers, stat_result=path.stat())
