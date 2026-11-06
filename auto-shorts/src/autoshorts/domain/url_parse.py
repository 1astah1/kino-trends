"""Разбор ссылки на опубликованный ролик YouTube или VK (docs/ARCHITECTURE.md §5.2 `parse-url`, §6.6).

Публикация ручная: оператор заливает сам и вставляет ссылку. Здесь из ссылки получается ID ролика
площадки, вид (short | clip | video), каноническая ссылка и алиас атрибуции (`domain/tags.py`).
Фронт повторяет правила в `web/lib/platform-url.ts`; при расхождении прав этот модуль.

YouTube (ID — ровно 11 символов `[A-Za-z0-9_-]`):
    youtube.com/shorts/ID · youtube.com/watch?v=ID · youtu.be/ID · youtube.com/embed/ID ·
    youtube-nocookie.com/embed/ID · youtube.com/live/ID · youtube.com/v/ID · youtube.com/e/ID ·
    studio.youtube.com/video/ID/edit · youtube.com/attribution_link?u=/watch%3Fv%3DID ·
    хосты www./m./music., со схемой и без, с хвостами ?si= &t= #…
VK (ID — `<owner>_<id>`, владелец-сообщество с минусом):
    vk.com/clip-1_2 · vk.com/video-1_2 · vk.ru/… · m.vk.com/… · vkvideo.ru/… ·
    vk.com/clips-1?z=clip-1_2 · vk.com/video?z=video-1_2%2Fpl_… · vk.com/wall-1_5?z=clip-1_2 ·
    vk.com/video_ext.php?oid=-1&id=2&hash=…

Отказ — `UnsupportedUrl` (API: 422 `unsupported_url`): не-ASCII (кириллица), пробелы, чужой хост
(включая `youtube.com.evil.example`), не ролик (канал, плейлист), неверная длина ID.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from urllib.parse import parse_qs, unquote, urlsplit

from autoshorts.domain.tags import CodeError, vk_alias, youtube_alias

__all__ = [
    "ParsedVideoUrl",
    "UnsupportedUrl",
    "canonical_url",
    "normalize_vk_owner",
    "parse_video_url",
]

URL_MAX = 2000
YOUTUBE_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
VK_REF_RE = re.compile(r"(clip|video)(-?[1-9]\d{0,19})_([1-9]\d{0,19})")
VK_PATH_RE = re.compile(r"^/(clip|video)(-?[1-9]\d{0,19})_([1-9]\d{0,19})/?$")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_BARE_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d)")  # javascript:, data:, но не host:443

YOUTUBE_HOSTS = frozenset({"youtube.com", "youtu.be", "youtube-nocookie.com", "studio.youtube.com"})
VK_HOSTS = frozenset({"vk.com", "vk.ru", "vkvideo.ru"})
_HOST_PREFIXES = ("www.", "m.", "music.", "mobile.")
_YT_PATH_ID = frozenset({"shorts", "embed", "live", "v", "e"})


class UnsupportedUrl(ValueError):
    """Ссылка не распознана. `reason` — машинная причина латиницей (details.reason в ответе)."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ParsedVideoUrl:
    platform: str  # youtube | vk
    platform_video_id: str  # 11 символов YouTube | "<owner>_<id>" VK
    platform_kind: str  # short | clip | video
    canonical_url: str
    alias_code: str
    owner_ref: str | None = None  # VK: владелец клипа (-<group_id> или <user_id>)

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


def _host(netloc: str) -> str:
    host = netloc.rsplit("@", 1)[-1].split(":", 1)[0].lower().rstrip(".")
    for prefix in _HOST_PREFIXES:
        if host.startswith(prefix):
            host = host[len(prefix) :]
            break
    return host


def canonical_url(platform: str, platform_video_id: str, platform_kind: str) -> str:
    if platform == "youtube":
        if platform_kind == "short":
            return f"https://www.youtube.com/shorts/{platform_video_id}"
        return f"https://www.youtube.com/watch?v={platform_video_id}"
    prefix = "clip" if platform_kind == "clip" else "video"
    return f"https://vk.com/{prefix}{platform_video_id}"


def _youtube(host: str, path: str, query: str) -> ParsedVideoUrl:
    segs = [s for s in path.split("/") if s]
    qs = parse_qs(query, keep_blank_values=False)
    video_id: str | None = None
    kind = "video"
    if host == "youtu.be":
        video_id = segs[0] if segs else None
    elif host == "studio.youtube.com":
        # studio.youtube.com/video/ID/edit — ссылка из Творческой студии
        if len(segs) >= 2 and segs[0] == "video":
            video_id = segs[1]
    elif segs and segs[0] == "watch":
        video_id = (qs.get("v") or [None])[0] or (segs[1] if len(segs) > 1 else None)
    elif segs and segs[0] == "attribution_link":
        inner = unquote((qs.get("u") or [""])[0])
        inner_qs = parse_qs(urlsplit(inner).query)
        video_id = (inner_qs.get("v") or [None])[0]
    elif len(segs) >= 2 and segs[0] in _YT_PATH_ID:
        video_id = segs[1]
        if segs[0] == "shorts":
            kind = "short"
    if not video_id:
        raise UnsupportedUrl("not_a_video", "Это ссылка YouTube, но не на ролик (канал, плейлист, поиск)")
    if not YOUTUBE_ID_RE.fullmatch(video_id):
        raise UnsupportedUrl("bad_video_id", "ID ролика YouTube — ровно 11 символов A–Z, a–z, 0–9, _ и -")
    return ParsedVideoUrl(
        platform="youtube",
        platform_video_id=video_id,
        platform_kind=kind,
        canonical_url=canonical_url("youtube", video_id, kind),
        alias_code=youtube_alias(video_id),
    )


def _vk_match(text: str) -> re.Match[str] | None:
    m = VK_REF_RE.search(text)
    if m is None:
        return None
    # границы: перед clip/video не буква, после id не цифра (pl_…, %2F — ок)
    before = text[m.start() - 1] if m.start() > 0 else "/"
    after = text[m.end()] if m.end() < len(text) else "/"
    if before.isalnum() or after.isdigit():
        return None
    return m


def _vk(path: str, query: str) -> ParsedVideoUrl:
    qs = parse_qs(query, keep_blank_values=False)
    m: re.Match[str] | None = None
    # 1) z=clip-1_2 / z=video-1_2%2Fpl_… (ленты клипов, стены, каталоги) — главнее пути
    z = unquote((qs.get("z") or [""])[0])
    if z:
        m = _vk_match(z.split("/")[0])
    # 2) /clip-1_2, /video-1_2
    if m is None:
        m = VK_PATH_RE.match(path)
    kind: str
    if m is not None:
        kind_raw, owner, vid = m.group(1), m.group(2), m.group(3)
        kind = "clip" if kind_raw == "clip" else "video"
    elif path.rstrip("/") == "/video_ext.php":
        # 3) плеер для вставки: video_ext.php?oid=-1&id=2&hash=…
        owner = (qs.get("oid") or [""])[0]
        vid = (qs.get("id") or [""])[0]
        if not re.fullmatch(r"-?[1-9]\d{0,19}", owner) or not re.fullmatch(r"[1-9]\d{0,19}", vid):
            raise UnsupportedUrl("bad_video_id", "В ссылке плеера VK нет oid и id ролика")
        kind = "video"
    else:
        raise UnsupportedUrl("not_a_video", "Это ссылка VK, но не на клип или видео")
    video_id = f"{owner}_{vid}"
    try:
        alias = vk_alias(owner, vid)
    except CodeError as exc:
        raise UnsupportedUrl("bad_video_id", "Неверный ID ролика VK") from exc
    return ParsedVideoUrl(
        platform="vk",
        platform_video_id=video_id,
        platform_kind=kind,
        canonical_url=canonical_url("vk", video_id, kind),
        alias_code=alias,
        owner_ref=owner,
    )


def parse_video_url(url: str) -> ParsedVideoUrl:
    """Ссылка на ролик → платформа, ID, вид, каноническая ссылка, алиас. Иначе `UnsupportedUrl`."""
    text = (url or "").strip()
    if not text:
        raise UnsupportedUrl("empty", "Пустая ссылка")
    if len(text) > URL_MAX:
        raise UnsupportedUrl("too_long", "Ссылка длиннее 2000 символов")
    if not text.isascii():
        raise UnsupportedUrl("non_ascii", "В ссылке кириллица или другие не-ASCII символы")
    if any(c.isspace() or ord(c) < 0x20 for c in text):
        raise UnsupportedUrl("whitespace", "В ссылке пробелы или управляющие символы")
    if not _SCHEME_RE.match(text):
        if _BARE_SCHEME_RE.match(text):
            raise UnsupportedUrl("bad_scheme", "Нужна ссылка http(s)")
        if text.startswith("//"):
            text = "https:" + text
        else:
            text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https"):
        raise UnsupportedUrl("bad_scheme", "Нужна ссылка http(s)")
    host = _host(parts.netloc)
    if not host:
        raise UnsupportedUrl("no_host", "В ссылке нет домена")
    if host in YOUTUBE_HOSTS:
        return _youtube(host, parts.path, parts.query)
    if host in VK_HOSTS:
        return _vk(parts.path, parts.query)
    raise UnsupportedUrl("unsupported_host", f"Ссылка не на YouTube или VK: {host}")


def normalize_vk_owner(ref: str | None) -> str | None:
    """Ссылка/ID сообщества VK из профиля канала → владелец клипа: `club123`, `public123`, `-123`,
    `https://vk.com/club123` → `-123`; `id45` / `45` → `45`. Короткое имя (`vk.com/umka`) — None:
    без API VK его не сопоставить."""
    if not ref:
        return None
    text = ref.strip()
    if "/" in text:
        path = urlsplit(text if "://" in text else "https://" + text).path
        segs = [s for s in path.split("/") if s]
        text = segs[-1] if segs else ""
    m = re.fullmatch(r"(club|public|event)(\d{1,20})", text)
    if m:
        return f"-{m.group(2)}"
    m = re.fullmatch(r"id(\d{1,20})", text)
    if m:
        return m.group(1)
    if re.fullmatch(r"-?\d{1,20}", text):
        return text
    return None
