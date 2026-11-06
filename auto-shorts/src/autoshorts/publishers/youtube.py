"""Заливка ролика на свой YouTube-канал: `videos.insert`, resumable upload (автозалив 0019).

Только сеть и разбор ответов, без БД. Порядок:

    url = start_session(client, token, meta, size)             # POST …?uploadType=resumable → Location
    video = upload(client, token, url, path, size)              # PUT кусками по 8 МиБ, 308 → дальше
    # после обрыва: offset, done = session_status(client, token, url, size) и upload(..., start=offset)

Сессия живёт около недели: её URL сохраняется в пакете, и повтор задачи продолжает с места. Ролик
вертикальный и не длиннее 3 минут — YouTube сам относит его к Shorts, отдельного флага в API нет.
Ошибки — `PublishError` с классом: что повторять, что ждать до сброса квоты, что чинить человеку.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx

from autoshorts.collectors.http import redact

__all__ = [
    "delete_video",
    "CHUNK_BYTES",
    "UPLOAD_URL",
    "PublishError",
    "PublishErrorKind",
    "VideoMeta",
    "clean_description",
    "clean_tags",
    "clean_title",
    "request_body",
    "session_status",
    "start_session",
    "upload",
]

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
CHUNK_BYTES = 8 * 1024 * 1024  # кратно 256 КиБ, как требует resumable upload
TITLE_MAX = 100
DESCRIPTION_MAX_BYTES = 5000
TAGS_MAX_CHARS = 500
MAX_CHUNK_FAILURES = 5
BACKOFF_MAX_SEC = 30.0


class PublishErrorKind(StrEnum):
    AUTH = "auth"  # токен отозван или протух: переподключить канал
    QUOTA = "quota"  # квота проекта на сутки кончилась: ждать сброса (полночь PT)
    UPLOAD_LIMIT = "upload_limit"  # канал упёрся в суточный лимит заливок YouTube
    INVALID = "invalid"  # площадка не приняла метаданные: править пакет
    FORBIDDEN = "forbidden"  # канал не может заливать (нет канала, бан, не подтверждён)
    SESSION_EXPIRED = "session_expired"  # resumable-сессия умерла: начать заново
    NETWORK = "network"
    HTTP = "http"  # 5xx/429 после повторов
    BAD_RESPONSE = "bad_response"


RETRYABLE = frozenset({PublishErrorKind.NETWORK, PublishErrorKind.HTTP, PublishErrorKind.SESSION_EXPIRED,
                       PublishErrorKind.BAD_RESPONSE})


class PublishError(Exception):
    def __init__(
        self, message: str, *, kind: PublishErrorKind | str, status: int | None = None, reason: str | None = None
    ) -> None:
        self.message = redact(message)[:500]
        self.kind = PublishErrorKind(kind)
        self.status = status
        self.reason = reason
        super().__init__(self.message)

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE

    def __repr__(self) -> str:
        return f"PublishError(kind={self.kind.value!r}, status={self.status}, reason={self.reason!r})"


@dataclass(frozen=True)
class VideoMeta:
    title: str
    description: str
    tags: tuple[str, ...] = ()
    privacy: str = "public"
    synthetic: bool = False  # status.containsSyntheticMedia: реалистичный ИИ-контент
    category_id: str = "22"  # People & Blogs
    language: str | None = None
    made_for_kids: bool = False
    extra_status: dict[str, Any] = field(default_factory=dict)


_ANGLE = re.compile(r"[<>]")
_SPACES = re.compile(r"[ \t]+")


def clean_title(title: str) -> str:
    """Заголовок для YouTube: без `<` и `>` (площадка их не принимает), одна строка, ≤ 100 символов."""
    text = _SPACES.sub(" ", _ANGLE.sub("", str(title or "")).replace("\n", " ")).strip()
    if len(text) > TITLE_MAX:
        text = text[: TITLE_MAX - 1].rstrip() + "…"
    if not text:
        raise PublishError("Пустой заголовок ролика", kind=PublishErrorKind.INVALID, reason="empty_title")
    return text


def clean_description(description: str) -> str:
    """Описание без `<` и `>`, не больше 5000 байт UTF-8 (обрезка по границе символа)."""
    text = _ANGLE.sub("", str(description or "")).strip()
    raw = text.encode("utf-8")
    if len(raw) <= DESCRIPTION_MAX_BYTES:
        return text
    return raw[:DESCRIPTION_MAX_BYTES].decode("utf-8", errors="ignore").rstrip()


def clean_tags(tags: Any) -> list[str]:
    """Теги из хэштегов пакета: без `#`, без дублей; сумма не больше 500 символов по правилу YouTube
    (тег с пробелом считается в кавычках, между тегами запятая)."""
    out: list[str] = []
    seen: set[str] = set()
    total = 0
    for raw in tags or ():
        tag = _ANGLE.sub("", str(raw)).strip().lstrip("#").replace(",", " ").strip()
        tag = _SPACES.sub(" ", tag)
        if not tag or tag.lower() in seen:
            continue
        cost = len(tag) + (2 if " " in tag else 0) + (1 if out else 0)
        if total + cost > TAGS_MAX_CHARS:
            break
        out.append(tag)
        seen.add(tag.lower())
        total += cost
    return out


def request_body(meta: VideoMeta) -> dict[str, Any]:
    snippet: dict[str, Any] = {
        "title": clean_title(meta.title),
        "description": clean_description(meta.description),
        "categoryId": meta.category_id,
    }
    tags = clean_tags(meta.tags)
    if tags:
        snippet["tags"] = tags
    if meta.language:
        snippet["defaultLanguage"] = meta.language
        snippet["defaultAudioLanguage"] = meta.language
    status: dict[str, Any] = {
        "privacyStatus": meta.privacy,
        "selfDeclaredMadeForKids": bool(meta.made_for_kids),
        "containsSyntheticMedia": bool(meta.synthetic),
        "embeddable": True,
        **meta.extra_status,
    }
    return {"snippet": snippet, "status": status}


# ---------------------------------------------------------------- ошибки


def _reason(body: Any) -> tuple[str | None, str]:
    if not isinstance(body, dict):
        return None, ""
    err = body.get("error")
    if isinstance(err, str):  # ответы OAuth
        return err, str(body.get("error_description") or "")
    if not isinstance(err, dict):
        return None, ""
    reasons = [e.get("reason") for e in err.get("errors") or [] if isinstance(e, dict)]
    return (str(reasons[0]) if reasons and reasons[0] else None), str(err.get("message") or "")


def error_from(resp: httpx.Response, where: str) -> PublishError:
    try:
        body = resp.json()
    except ValueError:
        body = None
    reason, message = _reason(body)
    status = resp.status_code
    text = f"YouTube {where}: HTTP {status} {reason or ''} {message}".strip()
    if status == 401:
        kind = PublishErrorKind.AUTH
    elif reason in ("quotaExceeded", "dailyLimitExceeded"):
        kind = PublishErrorKind.QUOTA
    elif reason == "uploadLimitExceeded":
        kind = PublishErrorKind.UPLOAD_LIMIT
    elif status in (404, 410) and where == "upload":
        kind = PublishErrorKind.SESSION_EXPIRED
    elif status == 403:
        kind = PublishErrorKind.QUOTA if reason in ("rateLimitExceeded", "userRateLimitExceeded") else \
            PublishErrorKind.FORBIDDEN
    elif status == 400:
        kind = PublishErrorKind.INVALID
    elif status == 429 or status >= 500:
        kind = PublishErrorKind.HTTP
    else:
        kind = PublishErrorKind.BAD_RESPONSE
    return PublishError(text, kind=kind, status=status, reason=reason)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _backoff(attempt: int) -> float:
    return min(BACKOFF_MAX_SEC, 1.0 * (2**attempt)) + random.uniform(0, 0.5)  # noqa: S311


# ---------------------------------------------------------------- сессия


def start_session(
    client: httpx.Client,
    access_token: str,
    meta: VideoMeta,
    size: int,
    *,
    content_type: str = "video/mp4",
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Открыть resumable-сессию. Возвращает её URL (заголовок `Location`)."""
    body = request_body(meta)
    headers = {
        **_auth(access_token),
        "X-Upload-Content-Length": str(size),
        "X-Upload-Content-Type": content_type,
    }
    for attempt in range(4):
        try:
            resp = client.post(
                UPLOAD_URL,
                params={"uploadType": "resumable", "part": "snippet,status"},
                headers=headers,
                json=body,
            )
        except httpx.TransportError as exc:
            if attempt < 3:
                sleep(_backoff(attempt))
                continue
            raise PublishError(f"сеть недоступна: {type(exc).__name__}", kind=PublishErrorKind.NETWORK) from exc
        if resp.status_code == 200 and resp.headers.get("Location"):
            return str(resp.headers["Location"])
        err = error_from(resp, "session")
        if err.kind == PublishErrorKind.HTTP and attempt < 3:
            sleep(_backoff(attempt))
            continue
        if resp.status_code == 200:
            raise PublishError("YouTube не вернул адрес сессии заливки", kind=PublishErrorKind.BAD_RESPONSE)
        raise err
    raise AssertionError("unreachable")  # pragma: no cover


def _next_offset(resp: httpx.Response) -> int:
    """308 Resume Incomplete: `Range: bytes=0-N` → следующий байт N+1; без Range — ничего не принято."""
    value = resp.headers.get("Range") or ""
    m = re.match(r"bytes=0-(\d+)$", value.strip())
    return int(m.group(1)) + 1 if m else 0


def _video(resp: httpx.Response) -> dict[str, Any]:
    try:
        body = resp.json()
    except ValueError as exc:
        raise PublishError("YouTube: ответ заливки не JSON", kind=PublishErrorKind.BAD_RESPONSE) from exc
    if not isinstance(body, dict) or not isinstance(body.get("id"), str):
        raise PublishError("YouTube: в ответе заливки нет id ролика", kind=PublishErrorKind.BAD_RESPONSE)
    return body


def session_status(
    client: httpx.Client, access_token: str, session_url: str, size: int
) -> tuple[int, dict[str, Any] | None]:
    """Сколько байт сессия уже приняла; если заливка завершена — (size, ролик)."""
    try:
        resp = client.put(
            session_url, headers={**_auth(access_token), "Content-Range": f"bytes */{size}", "Content-Length": "0"}
        )
    except httpx.TransportError as exc:
        raise PublishError(f"сеть недоступна: {type(exc).__name__}", kind=PublishErrorKind.NETWORK) from exc
    if resp.status_code in (200, 201):
        return size, _video(resp)
    if resp.status_code == 308:
        return _next_offset(resp), None
    raise error_from(resp, "upload")


def delete_video(client: httpx.Client, access_token: str, video_id: str) -> None:
    """Удалить ролик с YouTube (`videos.delete`; нужно право youtube.force-ssl, одного youtube.upload мало).
    204 — удалён, 404 — его уже нет (считаем удалённым); остальное — `PublishError` как у заливки."""
    vid = str(video_id or "").strip()
    if not vid:
        raise PublishError("Пустой id ролика", kind=PublishErrorKind.INVALID, reason="empty_video_id")
    try:
        resp = client.delete(VIDEOS_URL, params={"id": vid}, headers=_auth(access_token))
    except httpx.HTTPError as exc:
        raise PublishError(f"сеть: {exc}", kind=PublishErrorKind.NETWORK) from exc
    if resp.status_code in (204, 404):
        return
    raise error_from(resp, "delete")


def upload(
    client: httpx.Client,
    access_token: str,
    session_url: str,
    path: Path,
    size: int,
    *,
    start: int = 0,
    chunk_bytes: int = CHUNK_BYTES,
    progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Догрузить файл в сессию с байта `start`. Возвращает ресурс ролика (`id`, `status`, …).

    Сетевой сбой и 5xx: пауза, запрос статуса сессии, продолжение с принятого байта; после
    `MAX_CHUNK_FAILURES` подряд — `PublishError(network|http)`, повтор задачи продолжит с места.
    """
    if chunk_bytes <= 0 or chunk_bytes % (256 * 1024):
        raise ValueError("chunk_bytes должен быть кратен 256 КиБ")
    offset = max(0, int(start))
    failures = 0
    with path.open("rb") as fh:
        while True:
            if cancelled is not None and cancelled():
                raise PublishError("заливка отменена", kind=PublishErrorKind.NETWORK, reason="cancelled")
            fh.seek(offset)
            data = fh.read(chunk_bytes)
            end = offset + len(data) - 1
            headers = {**_auth(access_token), "Content-Length": str(len(data))}
            headers["Content-Range"] = f"bytes {offset}-{end}/{size}" if data else f"bytes */{size}"
            try:
                resp = client.put(session_url, headers=headers, content=data)
            except httpx.TransportError as exc:
                failures += 1
                if failures > MAX_CHUNK_FAILURES:
                    raise PublishError(
                        f"сеть недоступна: {type(exc).__name__}", kind=PublishErrorKind.NETWORK
                    ) from exc
                sleep(_backoff(failures))
                offset, done = session_status(client, access_token, session_url, size)
                if done is not None:
                    return done
                continue
            if resp.status_code in (200, 201):
                if progress is not None:
                    progress(1.0)
                return _video(resp)
            if resp.status_code == 308:
                failures = 0
                offset = _next_offset(resp)
                if progress is not None and size:
                    progress(min(0.99, offset / size))
                continue
            err = error_from(resp, "upload")
            if err.kind == PublishErrorKind.HTTP:
                failures += 1
                if failures > MAX_CHUNK_FAILURES:
                    raise err
                sleep(_backoff(failures))
                offset, done = session_status(client, access_token, session_url, size)
                if done is not None:
                    return done
                continue
            raise err
