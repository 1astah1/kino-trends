"""YouTube Data API v3 по ключу API куста (docs/ARCHITECTURE.md §6.5, решение владельца на M4).

* `videos.list?part=snippet,statistics,status,contentDetails&id=<≤50>&key=…` — 1 единица квоты за
  вызов при любом наборе part (калькулятор квоты YouTube). Больше 50 id за вызов API не принимает —
  клиент режет пачки сам (`chunks`) и отказывает пачке длиннее `YT_VIDEOS_LIST_BATCH`.
* `channels.list?part=statistics&id=<≤50>` — снимок канала (подписчики, просмотры, число роликов),
  тоже 1 единица.

Ролик, которого нет в ответе, API по ключу не отличает: удалён, снят площадкой или стал приватным
(приватные ролики по ключу не видны). `interpret_video` возвращает вердикт для `publications.status`:
пропал → `removed_by_platform`; `uploadStatus=rejected` → снят с причиной (`rejectionReason`),
`uploaderAccountSuspended`/`uploaderAccountClosed` — сигнал санкции канала (критический алерт);
`claim`/`copyright`/`trademark` и `regionRestriction` — признак претензии (`has_claim`).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from autoshorts.api.schemas.metrics import YT_VIDEOS_LIST_BATCH, YT_VIDEOS_LIST_COST_UNITS
from autoshorts.collectors.http import (
    DEFAULT_RETRIES,
    CollectorError,
    ErrorKind,
    json_body,
    request,
)
from autoshorts.domain.enums import PublicationStatus

__all__ = [
    "API_BASE",
    "CHANNELS_LIST_COST_UNITS",
    "CLAIM_REASONS",
    "REJECTION_LABELS",
    "SANCTION_REASONS",
    "VIDEO_PARTS",
    "ChannelObservation",
    "ChannelsListResult",
    "VideoObservation",
    "VideoVerdict",
    "VideosListResult",
    "YouTubeDataClient",
    "chunks",
    "classify_error",
    "interpret_video",
    "parse_channel",
    "parse_video",
]

API_BASE = "https://www.googleapis.com/youtube/v3"
VIDEO_PARTS = "snippet,statistics,status,contentDetails"
CHANNEL_PARTS = "statistics"
CHANNELS_LIST_COST_UNITS = 1
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")

REJECTION_LABELS: dict[str, str] = {
    "claim": "заявка правообладателя (Content ID)",
    "copyright": "нарушение авторских прав",
    "duplicate": "дубликат",
    "inappropriate": "недопустимое содержание",
    "legal": "юридическая причина",
    "length": "превышена длина",
    "termsOfUse": "нарушение условий использования",
    "trademark": "товарный знак",
    "uploaderAccountClosed": "аккаунт автора закрыт",
    "uploaderAccountSuspended": "аккаунт автора заблокирован",
}
FAILURE_LABELS: dict[str, str] = {
    "codec": "неподдерживаемый кодек",
    "conversion": "ошибка обработки",
    "emptyFile": "пустой файл",
    "invalidFile": "повреждённый файл",
    "tooSmall": "слишком маленький файл",
    "uploadAborted": "загрузка прервана",
}
SANCTION_REASONS = frozenset({"uploaderAccountSuspended", "uploaderAccountClosed"})
CLAIM_REASONS = frozenset({"claim", "copyright", "trademark"})

QUOTA_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded", "dailyLimitExceededUnreg"})
RATE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})
AUTH_REASONS = frozenset(
    {
        "keyInvalid",
        "keyExpired",
        "accessNotConfigured",
        "ipRefererBlocked",
        "forbidden",
        "API_KEY_INVALID",
        "API_KEY_EXPIRED",
        "SERVICE_DISABLED",
        "API_KEY_SERVICE_BLOCKED",
        "API_KEY_HTTP_REFERRER_BLOCKED",
        "API_KEY_IP_ADDRESS_BLOCKED",
    }
)


def chunks(items: Sequence[str], size: int = YT_VIDEOS_LIST_BATCH) -> Iterator[list[str]]:
    """Пачки по `size` (≤ 50 для videos.list)."""
    for i in range(0, len(items), size):
        yield list(items[i : i + size])


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------- разбор ответов


@dataclass(frozen=True)
class VideoObservation:
    """Ролик из ответа `videos.list` (только то, что нужно коллектору)."""

    video_id: str
    views: int | None
    likes: int | None
    comments: int | None
    privacy_status: str | None
    upload_status: str | None
    rejection_reason: str | None
    failure_reason: str | None
    region_blocked: tuple[str, ...] = ()
    region_allowed: tuple[str, ...] | None = None
    published_at: datetime | None = None
    channel_id: str | None = None
    thumbnail_url: str | None = None
    duration: str | None = None
    made_for_kids: bool | None = None

    def raw(self) -> dict[str, Any]:
        """Компактный след для `metrics_snapshots.raw` (без описаний и тегов)."""
        out: dict[str, Any] = {
            "privacy_status": self.privacy_status,
            "upload_status": self.upload_status,
        }
        if self.rejection_reason:
            out["rejection_reason"] = self.rejection_reason
        if self.failure_reason:
            out["failure_reason"] = self.failure_reason
        if self.region_blocked:
            out["region_blocked"] = list(self.region_blocked)
        if self.region_allowed is not None:
            out["region_allowed"] = list(self.region_allowed)
        if self.channel_id:
            out["channel_id"] = self.channel_id
        if self.published_at:
            out["published_at"] = self.published_at.isoformat()
        if self.made_for_kids is not None:
            out["made_for_kids"] = self.made_for_kids
        return out


def parse_video(item: dict[str, Any]) -> VideoObservation:
    snippet = item.get("snippet") or {}
    stats = item.get("statistics") or {}
    status = item.get("status") or {}
    details = item.get("contentDetails") or {}
    restriction = details.get("regionRestriction") or {}
    thumbs = snippet.get("thumbnails") or {}
    thumb = None
    for size in ("maxres", "standard", "high", "medium", "default"):
        url = (thumbs.get(size) or {}).get("url")
        if url:
            thumb = str(url)
            break
    allowed = restriction.get("allowed")
    return VideoObservation(
        video_id=str(item.get("id") or ""),
        views=_int(stats.get("viewCount")),
        likes=_int(stats.get("likeCount")),
        comments=_int(stats.get("commentCount")),
        privacy_status=status.get("privacyStatus"),
        upload_status=status.get("uploadStatus"),
        rejection_reason=status.get("rejectionReason"),
        failure_reason=status.get("failureReason"),
        region_blocked=tuple(str(c) for c in restriction.get("blocked") or ()),
        region_allowed=tuple(str(c) for c in allowed) if isinstance(allowed, list) else None,
        published_at=_dt(snippet.get("publishedAt")),
        channel_id=snippet.get("channelId"),
        thumbnail_url=thumb,
        duration=details.get("duration"),
        made_for_kids=status.get("madeForKids"),
    )


@dataclass(frozen=True)
class ChannelObservation:
    channel_id: str
    subscribers: int | None
    total_views: int | None
    video_count: int | None
    hidden_subscribers: bool = False

    def raw(self) -> dict[str, Any]:
        return {"hidden_subscribers": self.hidden_subscribers}


def parse_channel(item: dict[str, Any]) -> ChannelObservation:
    stats = item.get("statistics") or {}
    hidden = bool(stats.get("hiddenSubscriberCount"))
    return ChannelObservation(
        channel_id=str(item.get("id") or ""),
        subscribers=None if hidden else _int(stats.get("subscriberCount")),
        total_views=_int(stats.get("viewCount")),
        video_count=_int(stats.get("videoCount")),
        hidden_subscribers=hidden,
    )


# ---------------------------------------------------------------- толкование статуса


@dataclass(frozen=True)
class VideoVerdict:
    """Что делать с публикацией по ответу API."""

    status: PublicationStatus | None  # новый publications.status; None — не менять
    reason: str | None  # status_reason по-русски
    removed: bool = False  # снят или недоступен → алерт publication_removed
    sanction: bool = False  # аккаунт автора заблокирован/закрыт → критический алерт канала
    claim: bool = False  # признак претензии правообладателя → has_claim
    code: str | None = None  # машинная причина: missing | rejectionReason | failed:<reason> | deleted


MISSING_REASON = "Ролик пропал из ответа YouTube Data API: удалён, снят площадкой или стал приватным"


def interpret_video(obs: VideoObservation | None) -> VideoVerdict:
    """Вердикт по ролику; `None` — ролика нет в ответе `videos.list`."""
    if obs is None:
        return VideoVerdict(
            PublicationStatus.REMOVED_BY_PLATFORM, MISSING_REASON, removed=True, code="missing"
        )
    upload = obs.upload_status or ""
    if upload == "rejected":
        why = obs.rejection_reason or "unknown"
        label = REJECTION_LABELS.get(why, why)
        return VideoVerdict(
            PublicationStatus.REMOVED_BY_PLATFORM,
            f"YouTube отклонил ролик: {label} ({why})",
            removed=True,
            sanction=why in SANCTION_REASONS,
            claim=why in CLAIM_REASONS,
            code=why,
        )
    if upload == "failed":
        why = obs.failure_reason or "unknown"
        return VideoVerdict(
            PublicationStatus.REMOVED_BY_PLATFORM,
            f"YouTube не обработал ролик: {FAILURE_LABELS.get(why, why)} ({why})",
            removed=True,
            code=f"failed:{why}",
        )
    if upload == "deleted":
        return VideoVerdict(
            PublicationStatus.REMOVED_BY_PLATFORM, "Ролик удалён на YouTube", removed=True, code="deleted"
        )
    claim = bool(obs.region_blocked) or obs.region_allowed is not None
    privacy = obs.privacy_status or "public"
    status = {
        "public": PublicationStatus.LIVE,
        "unlisted": PublicationStatus.UNLISTED,
        "private": PublicationStatus.PRIVATE,
    }.get(privacy, PublicationStatus.LIVE)
    reason = None
    if claim:
        blocked = ", ".join(obs.region_blocked[:10])
        reason = f"Ограничен по регионам{': ' + blocked if blocked else ''} (часто — заявка правообладателя)"
    return VideoVerdict(status, reason, claim=claim)


# ---------------------------------------------------------------- ошибки API


def classify_error(resp: httpx.Response, *, http_attempts: int = 0) -> CollectorError:
    """Ошибка Google API → `CollectorError` с понятным классом (квота, ключ, темп, HTTP)."""
    status = resp.status_code
    reasons: list[str] = []
    message = ""
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        err = body["error"]
        message = str(err.get("message") or "")
        for item in err.get("errors") or []:
            if isinstance(item, dict) and item.get("reason"):
                reasons.append(str(item["reason"]))
        for item in err.get("details") or []:
            if isinstance(item, dict) and item.get("reason"):
                reasons.append(str(item["reason"]))
    reason = reasons[0] if reasons else None
    found = set(reasons)
    text = (
        f"YouTube Data API HTTP {status}"
        + (f" {reason}" if reason else "")
        + (f": {message}" if message else "")
    )
    if found & QUOTA_REASONS:
        kind = ErrorKind.QUOTA
        reason = next(r for r in reasons if r in QUOTA_REASONS)
    elif found & RATE_REASONS or status == 429:
        kind = ErrorKind.RATE_LIMITED
    elif found & AUTH_REASONS or status == 401 or "API key" in message:
        kind = ErrorKind.AUTH
        reason = next((r for r in reasons if r in AUTH_REASONS), reason)
    elif status == 403:
        kind = ErrorKind.AUTH
    else:
        kind = ErrorKind.HTTP
    return CollectorError(text, kind=kind, status=status, reason=reason, http_attempts=http_attempts)


# ---------------------------------------------------------------- клиент


@dataclass
class VideosListResult:
    found: dict[str, VideoObservation]
    missing: list[str]
    http_attempts: int
    units: int = field(default=0)


@dataclass
class ChannelsListResult:
    found: dict[str, ChannelObservation]
    missing: list[str]
    http_attempts: int
    units: int = field(default=0)


class YouTubeDataClient:
    """Клиент YouTube Data API v3 на готовом `httpx.Client` (`collectors.http.make_client`)."""

    def __init__(
        self,
        api_key: str,
        client: httpx.Client,
        *,
        base_url: str = API_BASE,
        retries: int = DEFAULT_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("YouTube Data API key is empty")
        self.api_key = api_key
        self.client = client
        self.base_url = base_url.rstrip("/")
        self.retries = retries
        self.sleep = sleep

    def _get(self, path: str, params: dict[str, Any]) -> tuple[dict[str, Any], int]:
        res = request(
            self.client,
            "GET",
            f"{self.base_url}/{path}",
            params={**params, "key": self.api_key},
            retries=self.retries,
            sleep=self.sleep,
        )
        if res.response.status_code != 200:
            raise classify_error(res.response, http_attempts=res.http_attempts)
        body = json_body(res.response, http_attempts=res.http_attempts)
        if not isinstance(body, dict) or not isinstance(body.get("items", []), list):
            raise CollectorError(
                "YouTube Data API: неожиданная форма ответа",
                kind=ErrorKind.BAD_RESPONSE,
                status=200,
                http_attempts=res.http_attempts,
            )
        return body, res.http_attempts

    def videos_list(self, video_ids: Sequence[str]) -> VideosListResult:
        """Один вызов `videos.list` на ≤ 50 id. Пропавшие из ответа — в `missing`."""
        ids = _dedupe(video_ids)
        if not ids:
            return VideosListResult({}, [], 0, 0)
        if len(ids) > YT_VIDEOS_LIST_BATCH:
            raise ValueError(f"videos.list accepts at most {YT_VIDEOS_LIST_BATCH} ids, got {len(ids)}")
        bad = [v for v in ids if not VIDEO_ID_RE.match(v)]
        if bad:
            raise ValueError(f"invalid YouTube video ids: {bad[:5]}")
        body, attempts = self._get(
            "videos",
            {"part": VIDEO_PARTS, "id": ",".join(ids), "maxResults": YT_VIDEOS_LIST_BATCH},
        )
        found: dict[str, VideoObservation] = {}
        for item in body.get("items") or []:
            if isinstance(item, dict):
                obs = parse_video(item)
                if obs.video_id in ids:
                    found[obs.video_id] = obs
        missing = [v for v in ids if v not in found]
        return VideosListResult(found, missing, attempts, attempts * YT_VIDEOS_LIST_COST_UNITS)

    def channels_list(self, channel_ids: Sequence[str]) -> ChannelsListResult:
        """Один вызов `channels.list?part=statistics` на ≤ 50 id каналов (UC…)."""
        ids = _dedupe(channel_ids)
        if not ids:
            return ChannelsListResult({}, [], 0, 0)
        if len(ids) > YT_VIDEOS_LIST_BATCH:
            raise ValueError(f"channels.list accepts at most {YT_VIDEOS_LIST_BATCH} ids, got {len(ids)}")
        bad = [c for c in ids if not CHANNEL_ID_RE.match(c)]
        if bad:
            raise ValueError(f"invalid YouTube channel ids: {bad[:5]}")
        body, attempts = self._get(
            "channels",
            {"part": CHANNEL_PARTS, "id": ",".join(ids), "maxResults": YT_VIDEOS_LIST_BATCH},
        )
        found: dict[str, ChannelObservation] = {}
        for item in body.get("items") or []:
            if isinstance(item, dict):
                obs = parse_channel(item)
                if obs.channel_id in ids:
                    found[obs.channel_id] = obs
        missing = [c for c in ids if c not in found]
        return ChannelsListResult(found, missing, attempts, attempts * CHANNELS_LIST_COST_UNITS)
