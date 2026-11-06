"""VK Клипы: `vk_api` (если задан токен) → метаданные yt-dlp → ручной ввод (docs/ARCHITECTURE.md §6.5).

Решение владельца на M4: VK-метрики сначала через yt-dlp и вручную. Токен VK (`VK_ACCESS_TOKEN`,
право `video`) — необязательная первая ступень: `video.get?videos=<owner_id>_<id>,…` отдаёт
`views`, `likes.count`, `comments`, `reposts.count` пачкой до 100 роликов за вызов.

Проверено 27.09.2026 на публичном клипе: экстрактор VK в yt-dlp 2026.08.19 отдаёт лайки, комментарии,
длительность и время, но **не отдаёт просмотры** клипов. Такой ответ — `partial=True`: снимок без
просмотров пишется, а просмотры ждут ручного ввода (решение владельца «VK — вручную и через yt-dlp»).

yt-dlp — только метаданные (`extract_info(download=False, process=False)`), по одному ролику и не
чаще раза в `min_interval_sec` (площадку не долбим). Ошибка с признаками удаления («removed»,
«удалён», «не найден») помечается `gone=True`: решение о `removed_by_platform` принимает
`services.metrics` по двум таким ответам подряд. Нет yt-dlp в окружении — `ErrorKind.TOOL_MISSING`.
"""

from __future__ import annotations

import importlib.util
import logging
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from autoshorts.collectors.http import (
    DEFAULT_RETRIES,
    CollectorError,
    ErrorKind,
    json_body,
    redact,
    request,
)

__all__ = [
    "VK_API_BASE",
    "VK_API_VERSION",
    "VK_VIDEO_GET_BATCH",
    "VK_YTDLP_MIN_INTERVAL_SEC",
    "VkApiClient",
    "VkClipObservation",
    "VkGroupObservation",
    "YtdlpMetaFetcher",
    "is_gone_message",
    "vk_clip_url",
    "ytdlp_available",
]

log = logging.getLogger(__name__)

VK_API_BASE = "https://api.vk.com/method"
VK_API_VERSION = "5.199"
VK_VIDEO_GET_BATCH = 100
VK_YTDLP_MIN_INTERVAL_SEC = 3.0
VK_VIDEO_ID_RE = re.compile(r"^-?\d{1,20}_\d{1,20}$")

VK_AUTH_ERRORS = frozenset({5, 15, 17, 27, 28, 200, 204})
VK_RATE_ERRORS = frozenset({6, 9, 29})

_GONE_PATTERNS = (
    "has been removed",
    "was removed",
    "been deleted",
    "was deleted",
    "not found",
    "does not exist",
    "no longer available",
    "удален",
    "удалён",
    "не найден",
    "заблокирован",
    "изъят",
)


def is_gone_message(text: str | None) -> bool:
    """Текст ошибки похож на «ролик удалён или не найден»."""
    low = (text or "").lower()
    return any(p in low for p in _GONE_PATTERNS)


def vk_clip_url(platform_video_id: str, canonical_url: str | None = None) -> str:
    """Ссылка на клип для yt-dlp: каноническая из регистрации, иначе `https://vk.com/clip<owner>_<id>`."""
    if canonical_url and canonical_url.startswith("https://"):
        return canonical_url
    return f"https://vk.com/clip{platform_video_id}"


@dataclass
class VkClipObservation:
    video_id: str  # "<owner_id>_<id>", как publications.platform_video_id
    found: bool
    source: str  # vk_api | ytdlp
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    gone: bool = False
    partial: bool = False  # ролик на месте, лайки/комментарии есть, а просмотров площадка не отдала
    error: str | None = None
    error_kind: ErrorKind | None = None
    thumbnail_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _int(value: Any) -> int | None:
    if isinstance(value, dict):
        value = value.get("count")
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- vk_api


@dataclass
class VkGroupObservation:
    group_id: int
    members: int | None


class VkApiClient:
    """VK API (`video.get`, `groups.getById`) по токену; ошибки VK — `CollectorError`."""

    def __init__(
        self,
        token: str,
        client: httpx.Client,
        *,
        base_url: str = VK_API_BASE,
        version: str = VK_API_VERSION,
        retries: int = DEFAULT_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not token:
            raise ValueError("VK token is empty")
        self.token = token
        self.client = client
        self.base_url = base_url.rstrip("/")
        self.version = version
        self.retries = retries
        self.sleep = sleep
        self.calls = 0

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        res = request(
            self.client,
            "GET",
            f"{self.base_url}/{method}",
            params={**params, "access_token": self.token, "v": self.version},
            retries=self.retries,
            sleep=self.sleep,
        )
        self.calls += res.http_attempts
        if res.response.status_code != 200:
            raise CollectorError(
                f"VK API HTTP {res.response.status_code}",
                kind=ErrorKind.HTTP,
                status=res.response.status_code,
                http_attempts=res.http_attempts,
            )
        body = json_body(res.response, http_attempts=res.http_attempts)
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            err = body["error"]
            code = _int(err.get("error_code")) or 0
            msg = str(err.get("error_msg") or "")
            kind = (
                ErrorKind.AUTH
                if code in VK_AUTH_ERRORS
                else ErrorKind.RATE_LIMITED
                if code in VK_RATE_ERRORS
                else ErrorKind.HTTP
            )
            raise CollectorError(
                f"VK API error {code}: {msg}",
                kind=kind,
                status=200,
                reason=str(code),
                http_attempts=res.http_attempts,
            )
        if not isinstance(body, dict) or "response" not in body:
            raise CollectorError(
                "VK API: неожиданная форма ответа",
                kind=ErrorKind.BAD_RESPONSE,
                http_attempts=res.http_attempts,
            )
        return body["response"]

    def video_get(self, video_ids: Sequence[str]) -> dict[str, VkClipObservation]:
        """`video.get` пачкой ≤ 100. Ответ — только найденные ролики (ключ — `<owner>_<id>`)."""
        ids = list(dict.fromkeys(video_ids))
        if len(ids) > VK_VIDEO_GET_BATCH:
            raise ValueError(f"video.get batch is limited to {VK_VIDEO_GET_BATCH}")
        bad = [v for v in ids if not VK_VIDEO_ID_RE.match(v)]
        if bad:
            raise ValueError(f"invalid VK video ids: {bad[:5]}")
        if not ids:
            return {}
        resp = self._call("video.get", {"videos": ",".join(ids), "count": len(ids)})
        items = resp.get("items") if isinstance(resp, dict) else None
        out: dict[str, VkClipObservation] = {}
        for item in items or []:
            if not isinstance(item, dict):
                continue
            vid = f"{item.get('owner_id')}_{item.get('id')}"
            if vid not in ids:
                continue
            thumb = None
            images = item.get("image") or []
            if isinstance(images, list) and images:
                best = max(
                    (i for i in images if isinstance(i, dict)),
                    key=lambda i: i.get("width") or 0,
                    default=None,
                )
                thumb = (best or {}).get("url")
            out[vid] = VkClipObservation(
                video_id=vid,
                found=True,
                source="vk_api",
                views=_int(item.get("views")),
                likes=_int(item.get("likes")),
                comments=_int(item.get("comments")),
                shares=_int(item.get("reposts")),
                thumbnail_url=thumb,
                raw={"date": item.get("date"), "duration": item.get("duration")},
            )
        return out

    def groups_members(self, group_ids: Sequence[int]) -> dict[int, VkGroupObservation]:
        """`groups.getById(fields=members_count)` для снимков сообществ."""
        ids = [abs(int(g)) for g in dict.fromkeys(group_ids)]
        if not ids:
            return {}
        resp = self._call(
            "groups.getById", {"group_ids": ",".join(str(i) for i in ids), "fields": "members_count"}
        )
        groups = resp.get("groups") if isinstance(resp, dict) else resp
        out: dict[int, VkGroupObservation] = {}
        for item in groups or []:
            if isinstance(item, dict) and item.get("id") is not None:
                gid = int(item["id"])
                out[gid] = VkGroupObservation(gid, _int(item.get("members_count")))
        return out


# ---------------------------------------------------------------- yt-dlp


def ytdlp_available() -> bool:
    return importlib.util.find_spec("yt_dlp") is not None


class _QuietLogger:
    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)


class YtdlpMetaFetcher:
    """Метаданные клипа через yt-dlp с паузой между вызовами (по умолчанию 3 с)."""

    source = "ytdlp"

    def __init__(
        self,
        *,
        proxy: str | None = None,
        min_interval_sec: float = VK_YTDLP_MIN_INTERVAL_SEC,
        timeout_sec: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        extract: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.proxy = proxy
        self.min_interval_sec = max(0.0, float(min_interval_sec))
        self.timeout_sec = timeout_sec
        self.clock = clock
        self.sleep = sleep
        self._extract_override = extract
        self._last: float | None = None
        self._lock = threading.Lock()
        self.calls = 0

    def available(self) -> bool:
        return self._extract_override is not None or ytdlp_available()

    def _pace(self) -> None:
        with self._lock:
            now = self.clock()
            if self._last is not None:
                wait = self.min_interval_sec - (now - self._last)
                if wait > 0:
                    self.sleep(wait)
            self._last = self.clock()

    def _extract(self, url: str) -> dict[str, Any]:
        if self._extract_override is not None:
            return self._extract_override(url)
        import yt_dlp

        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": self.timeout_sec,
            "extractor_retries": 1,
            "cachedir": False,
            "logger": _QuietLogger(),
        }
        if self.proxy:
            opts["proxy"] = self.proxy
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False, process=False)
        return dict(info or {})

    def fetch(self, video_id: str, url: str) -> VkClipObservation:
        if not self.available():
            return VkClipObservation(
                video_id, False, self.source, error="yt-dlp не установлен", error_kind=ErrorKind.TOOL_MISSING
            )
        self._pace()
        self.calls += 1
        try:
            info = self._extract(url)
        except Exception as exc:  # noqa: BLE001 - yt-dlp бросает DownloadError/ExtractorError и сетевые
            text = redact(str(exc)).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
            text = re.sub(r"^ERROR:\s*", "", text)[:500]
            return VkClipObservation(
                video_id,
                False,
                self.source,
                gone=is_gone_message(text),
                error=f"yt-dlp: {text}",
                error_kind=ErrorKind.HTTP,
            )
        views = _int(info.get("view_count"))
        obs = VkClipObservation(
            video_id=video_id,
            found=views is not None,
            source=self.source,
            views=views,
            likes=_int(info.get("like_count")),
            comments=_int(info.get("comment_count")),
            shares=_int(info.get("repost_count")),
            thumbnail_url=info.get("thumbnail") if isinstance(info.get("thumbnail"), str) else None,
            raw={
                k: info.get(k)
                for k in ("extractor", "availability", "timestamp", "duration")
                if info.get(k) is not None
            },
        )
        if views is None:
            obs.error = "yt-dlp: площадка не отдала число просмотров"
            obs.error_kind = ErrorKind.BAD_RESPONSE
            obs.partial = obs.likes is not None or obs.comments is not None
        return obs
