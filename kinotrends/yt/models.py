from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class VideoDetails:
    video_id: str
    status: str = "ok"               # ok | removed | bot | ratelimit | error | not_short
    title: str | None = None
    description: str | None = None
    channel_id: str | None = None
    channel_name: str | None = None
    duration_s: int | None = None
    published_at: str | None = None  # ISO UTC
    category: str | None = None
    keywords: list[str] = field(default_factory=list)
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    avail_ru: bool | None = None
    is_short: bool | None = None
    source: str = "page"
    error: str | None = None
