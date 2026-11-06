"""Метрики публикаций: лестница опроса, коллекторы, ручной ввод, свежесть, квоты (§6.5). Владелец — M4-A.

Цепочки коллекторов (`schemas.metrics.COLLECTOR_CHAINS`, решения владельца на M4):

* YouTube — `yt_data`: YouTube Data API `videos.list` по ключу куста (`clusters.youtube_api_key_secret`,
  иначе секрет `YOUTUBE_API_KEY`), пачки по 50, 1 единица за пачку, учёт в `api_quota_usage`
  (сутки квоты — America/Los_Angeles). Жёсткий стоп `yt_quota_hard_units` (8000): дальше опрос
  откладывается до сброса квоты, коллектор `paused`, алерт `quota_80`. Без ключа коллектор
  `disabled/no_api_key`, публикации ждут ручного ввода (`manual_due_at`).
* VK — `vk_api` (если задан секрет `VK_ACCESS_TOKEN`) → `ytdlp` (метаданные, не чаще раза в 3 с,
  не больше `VK_MAX_PER_RUN` за прогон) → ручной ввод.

Лестница: `metrics_poll_ladder_hours` (1…168 ч), дальше раз в сутки до 30 дней и раз в неделю до
90 (`poll_schedule_hours`); `next_poll_at` — ближайшая точка после «сейчас», `poll_step` — её номер.
Значения на ровный возраст (`views_24h/72h/7d/14d/30d`) — линейная интерполяция между соседними
снимками, без экстраполяции (`interpolate_views`), пересчитываются после каждого снимка.

Статусы YouTube (`collectors.youtube_data.interpret_video`): ролик пропал из ответа →
`removed_by_platform` + алерт `publication_removed`; `rejectionReason=uploaderAccountSuspended` →
критический алерт канала `channel_sanction_signal` и запись в `channel_incidents`; `claim/copyright`
и региональные ограничения → `has_claim`. Снятый ролик перепроверяется раз в сутки (до 90 дней):
вернулся — статус восстанавливается, алерт закрывается.

Ошибки опроса: `poll_failures`/`last_poll_error` публикации, пауза 15 мин × 2^n (до 6 ч); с
`FAILING_AFTER`-й ошибки подряд публикация ждёт ручного ввода и опрашивается по лестнице.
Состояние коллектора — `collector_state` (`failing` + алерт `collector_failing` с
`FAILING_AFTER`-го неудачного прогона подряд).

Сеть — вне транзакций: публикации «арендуются» (`repos.metrics.claim_due`) короткой транзакцией,
результат пишется следующей. Для других потоков:

    from autoshorts.services.metrics import freshness_map, publication_freshness
    freshness_map(conn, [pub_id, ...])     # {id: PublicationFreshness-словарь} — для Publication.freshness
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from autoshorts.api.schemas.metrics import (
    COLLECTOR_REASON_LABELS,
    METRICS_LADDER_DAILY_UNTIL_DAYS,
    METRICS_LADDER_WEEKLY_UNTIL_DAYS,
    METRICS_OVERDUE_GRACE_MIN,
    YOUTUBE_API_KEY_SECRET_DEFAULT,
    YT_DAILY_QUOTA_UNITS,
    YT_QUOTA_TIMEZONE,
    YT_VIDEOS_LIST_BATCH,
    YT_VIDEOS_LIST_COST_UNITS,
    CollectorReason,
    CollectorState,
    MetricsRejectReason,
)
from autoshorts.collectors.http import CollectorError, ErrorKind
from autoshorts.domain.enums import AlertCode, AlertSeverity, MetricsSource, Platform, PublicationStatus
from autoshorts.repos import metrics as repo
from autoshorts.repos import quota as quota_repo

log = logging.getLogger(__name__)

Row = dict[str, Any]

__all__ = [
    "AGE_POINTS",
    "FAILING_AFTER",
    "POLL_LEASE_SEC",
    "VK_MAX_PER_RUN",
    "PollDeps",
    "PollReport",
    "channel_snapshot_view",
    "collect_channel_stats",
    "collector_statuses",
    "create_manual",
    "delete_manual",
    "freshness_map",
    "interpolate_views",
    "latest_source_map",
    "manual_due",
    "next_poll",
    "poll_metrics",
    "poll_schedule_hours",
    "primary_collector",
    "publication_freshness",
    "quota_usage",
    "recompute_publication",
]

# ---------------------------------------------------------------- константы

POLL_LEASE_SEC = 900
"""Аренда публикации на время прогона: упавший прогон вернёт её в опрос через 15 минут."""
YT_MAX_PER_RUN = 1000
VK_MAX_PER_RUN = 20
"""yt-dlp по одному ролику с паузой 3 с: за прогон — не больше 20 клипов, остальные ждут следующего."""
FAILING_AFTER = 3
BACKOFF_BASE_MIN = 15
BACKOFF_MAX_HOURS = 6
REMOVED_RECHECK_HOURS = 24
PUBLISHED_AT_TOLERANCE = timedelta(minutes=5)
DUE_EPSILON = timedelta(minutes=2)
MANUAL_FUTURE_TOLERANCE = timedelta(minutes=5)
LARGE_JUMP_FACTOR = 20
QUOTA_DEFER_SPREAD_MIN = 10
VK_TOKEN_SECRET = "VK_ACCESS_TOKEN"
VK_API_QUOTA = "vk_api"
YT_QUOTA_API = "youtube_data"
DEFAULT_BUCKET = "default"

AGE_POINTS: dict[str, int] = {
    "views_24h": 24,
    "views_72h": 72,
    "views_7d": 7 * 24,
    "views_14d": 14 * 24,
    "views_30d": 30 * 24,
}
"""Колонки `publications` со значением на ровный возраст (часы)."""

_ERROR_REASON: dict[ErrorKind, CollectorReason] = {
    ErrorKind.NETWORK: CollectorReason.NETWORK_ERROR,
    ErrorKind.HTTP: CollectorReason.HTTP_ERROR,
    ErrorKind.BAD_RESPONSE: CollectorReason.HTTP_ERROR,
    ErrorKind.RATE_LIMITED: CollectorReason.HTTP_ERROR,
    ErrorKind.AUTH: CollectorReason.AUTH_ERROR,
    ErrorKind.QUOTA: CollectorReason.QUOTA_EXHAUSTED,
    ErrorKind.TOOL_MISSING: CollectorReason.TOOL_MISSING,
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def primary_collector(platform: str) -> str:
    """Голова цепочки площадки: youtube → yt_data, vk → ytdlp."""
    return MetricsSource.YT_DATA.value if platform == Platform.YOUTUBE.value else MetricsSource.YTDLP.value


# ---------------------------------------------------------------- лестница и интерполяция


def poll_schedule_hours(ladder: Sequence[int | float]) -> list[float]:
    """Точки опроса в часах от публикации: лестница, затем раз в сутки до 30 дней, раз в неделю до 90."""
    points = sorted({float(h) for h in ladder if float(h) > 0})
    daily_until = float(METRICS_LADDER_DAILY_UNTIL_DAYS * 24)
    weekly_until = float(METRICS_LADDER_WEEKLY_UNTIL_DAYS * 24)
    last = points[-1] if points else 0.0
    h = last + 24.0
    while h <= daily_until:
        points.append(h)
        h += 24.0
    last = points[-1] if points else 0.0
    h = last + 168.0
    while h <= weekly_until:
        points.append(h)
        h += 168.0
    if not points or points[-1] < weekly_until:
        points.append(weekly_until)
    return sorted(set(points))


def next_poll(
    published_at: datetime, now: datetime, schedule: Sequence[float]
) -> tuple[int, datetime] | None:
    """(номер точки, время) ближайшего опроса строго после `now` (+2 мин); None — окно 90 дней прошло."""
    horizon = now + DUE_EPSILON
    for idx, hours in enumerate(schedule):
        at = published_at + timedelta(hours=hours)
        if at > horizon:
            return idx, at
    return None


def interpolate_views(points: Iterable[tuple[float, float]], age_hours: float) -> int | None:
    """Просмотры на ровный возраст: линейно между соседними снимками, без экстраполяции.

    `points` — (возраст в часах, просмотры). Снимок ровно на этом возрасте — его значение; раньше
    первого или позже последнего снимка — None. Несколько снимков одного возраста — берётся последний.
    """
    by_age: dict[float, float] = {}
    for age, views in points:
        if age is None or views is None:
            continue
        by_age[float(age)] = float(views)
    ordered = sorted(by_age.items())
    if not ordered:
        return None
    target = float(age_hours)
    if target < ordered[0][0] or target > ordered[-1][0]:
        return None
    for (a0, v0), (a1, v1) in zip(ordered, ordered[1:], strict=False):
        if a0 <= target <= a1:
            if a1 == a0:
                return int(round(v1))
            return int(round(v0 + (v1 - v0) * (target - a0) / (a1 - a0)))
    return int(round(ordered[-1][1])) if ordered[-1][0] == target else None


def _age_hours(captured_at: datetime, published_at: datetime) -> float:
    return max(0.0, (captured_at - published_at).total_seconds() / 3600.0)


def _published(row: Row) -> datetime:
    return row.get("published_at") or row["created_at"]


def recompute_publication(conn: Any, publication_id: int, published_at: datetime) -> dict[str, Any]:
    """`latest_*`, `last_polled_at` и `views_24h…30d` из всех снимков публикации."""
    snaps = repo.snapshots(conn, publication_id)
    fields: dict[str, Any] = {
        "latest_views": None,
        "latest_likes": None,
        "latest_comments": None,
        "latest_engaged_views": None,
        "last_polled_at": snaps[-1]["captured_at"] if snaps else None,
    }
    for snap in snaps:  # по времени: последнее непустое значение каждого поля
        for src, dst in (
            ("views", "latest_views"),
            ("likes", "latest_likes"),
            ("comments", "latest_comments"),
            ("engaged_views", "latest_engaged_views"),
        ):
            if snap.get(src) is not None:
                fields[dst] = int(snap[src])
    points = [
        (_age_hours(s["captured_at"], published_at), s["views"]) for s in snaps if s.get("views") is not None
    ]
    for column, hours in AGE_POINTS.items():
        fields[column] = interpolate_views(points, hours)
    repo.update_publication(conn, publication_id, fields)
    return fields


def _event(conn: Any, publication_id: int) -> None:
    from autoshorts.db.notify import publish_event
    from autoshorts.domain.events import EventType

    publish_event(conn, EventType.PUBLICATION_METRICS, {"publication_id": int(publication_id)})


# ---------------------------------------------------------------- отчёт прогона


@dataclass
class PubOutcome:
    publication_id: int
    platform: str
    outcome: (
        str  # snapshot | removed | restored | missing | deferred | manual_due | failed | released | error
    )
    source: str | None = None
    views_before: int | None = None
    views: int | None = None
    status: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class PollReport:
    outcomes: list[PubOutcome] = field(default_factory=list)
    api_calls: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    collectors: dict[str, str] = field(default_factory=dict)  # "yt_data:default" -> state
    thumbnails: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, outcome: PubOutcome) -> None:
        with self.lock:
            self.outcomes.append(outcome)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for o in self.outcomes:
            out[o.outcome] += 1
        return dict(out)

    def as_dict(self, *, max_items: int = 50) -> dict[str, Any]:
        return {
            "publications": len(self.outcomes),
            "outcomes": self.counts(),
            "api_calls": dict(self.api_calls),
            "collectors": dict(self.collectors),
            "thumbnails": self.thumbnails,
            "items": [o.as_dict() for o in self.outcomes[:max_items]],
        }


@dataclass
class PollDeps:
    """Подмены для тестов и CLI: транспорт httpx, yt-dlp, сон, чтение секретов."""

    transport: Any = None
    ytdlp: Any = None
    sleep: Callable[[float], None] = time.sleep
    secret: Callable[[str, Any], str | None] | None = None
    thumbnails: bool = False
    media: Any = None


def _get_secret(name: str, conn: Any, deps: PollDeps) -> str | None:
    if deps.secret is not None:
        return deps.secret(name, conn)
    from autoshorts.services.secrets import get_secret

    try:
        return get_secret(name, conn=conn)
    except Exception:  # noqa: BLE001 - нет ключа шифрования и т. п.: коллектор просто без ключа
        log.warning("secret %s is unreadable", name, exc_info=True)
        return None


# ---------------------------------------------------------------- исходы публикации


def _schedule_fields(
    row: Row, now: datetime, schedule: Sequence[float], *, removed: bool = False
) -> dict[str, Any]:
    nxt = next_poll(_published(row), now, schedule)
    if nxt is None:
        return {"next_poll_at": None, "poll_step": len(schedule)}
    step, at = nxt
    if removed:
        at = max(at, now + timedelta(hours=REMOVED_RECHECK_HOURS))
    return {"next_poll_at": at, "poll_step": min(step, 32767)}


def _arm_manual(row: Row, now: datetime) -> datetime:
    """С какого момента ждём ручного ввода: уже ждёт — не сдвигаем, иначе срок опроса (не позже now)."""
    if row.get("manual_due_at") is not None:
        return row["manual_due_at"]
    due = row.get("due_at")
    return min(due, now) if due is not None else now


def _safe_apply(
    conn: Any,
    row: Row,
    apply: Callable[[], Any],
    now: datetime,
    schedule: Sequence[float],
    report: PollReport,
    *,
    source: str,
) -> bool:
    """Записать результат одной публикации в savepoint. Сбой обработки (ошибка в данных или коде) не валит
    пачку: публикация получает ошибку опроса и паузу, как при сбое площадки."""
    try:
        with conn.transaction():
            apply()
        return True
    except Exception as exc:  # noqa: BLE001 - одна публикация не валит прогон
        log.exception("metrics: apply failed for publication %s", row["id"])
        with conn.transaction():
            _poll_failed(
                conn,
                row,
                now,
                schedule,
                report,
                f"сбой обработки: {type(exc).__name__}: {exc}",
                source=source,
            )
        return False


def _fallback_manual(
    conn: Any, row: Row, now: datetime, schedule: Sequence[float], report: PollReport, *, reason: str
) -> None:
    """Коллектор не может (нет ключа, нет yt-dlp, ручной режим): ждём ручного ввода, лестница идёт дальше."""
    fields = {"manual_due_at": _arm_manual(row, now), **_schedule_fields(row, now, schedule)}
    repo.update_publication(conn, int(row["id"]), fields)
    report.add(PubOutcome(int(row["id"]), row["platform"], "manual_due", status=row["status"], error=reason))


def _poll_failed(
    conn: Any,
    row: Row,
    now: datetime,
    schedule: Sequence[float],
    report: PollReport,
    error: str,
    *,
    source: str,
) -> None:
    """Ошибка опроса публикации: пауза с ростом; с FAILING_AFTER-й подряд — ручной ввод и лестница."""
    failures = int(row.get("poll_failures") or 0) + 1
    fields: dict[str, Any] = {"poll_failures": min(failures, 32767), "last_poll_error": error[:2000]}
    if failures >= FAILING_AFTER:
        fields["manual_due_at"] = _arm_manual(row, now)
        fields.update(_schedule_fields(row, now, schedule))
        if fields["next_poll_at"] is not None:
            fields["next_poll_at"] = min(fields["next_poll_at"], now + timedelta(hours=BACKOFF_MAX_HOURS * 4))
    else:
        delay = min(BACKOFF_BASE_MIN * (2 ** (failures - 1)), BACKOFF_MAX_HOURS * 60)
        fields["next_poll_at"] = now + timedelta(minutes=delay)
    repo.update_publication(conn, int(row["id"]), fields)
    report.add(
        PubOutcome(
            int(row["id"]), row["platform"], "failed", source=source, status=row["status"], error=error[:300]
        )
    )


def _defer(conn: Any, row: Row, until: datetime, report: PollReport, *, reason: str) -> None:
    at = until + timedelta(minutes=int(row["id"]) % QUOTA_DEFER_SPREAD_MIN)
    repo.update_publication(conn, int(row["id"]), {"next_poll_at": at})
    report.add(PubOutcome(int(row["id"]), row["platform"], "deferred", status=row["status"], error=reason))


def _pub_title(row: Row) -> str:
    return f"{row.get('video_uid') or row['id']} ({row['platform']} {row.get('platform_video_id')})"


def _alert_removed(conn: Any, row: Row, reason: str, code: str | None) -> None:
    from autoshorts.services.alerts import open_alert

    open_alert(
        conn,
        code=AlertCode.PUBLICATION_REMOVED.value,
        severity=AlertSeverity.WARNING,
        dedup_key=f"publication_removed:{row['id']}",
        title=f"Ролик снят площадкой: {_pub_title(row)}",
        details={
            "publication_id": int(row["id"]),
            "job_id": int(row["job_id"]),
            "channel_id": row["channel_id"],
            "platform": row["platform"],
            "platform_video_id": row.get("platform_video_id"),
            "reason": reason,
            "code": code,
        },
    )


def _alert_sanction(conn: Any, row: Row, rejection: str, label: str) -> None:
    from autoshorts.services.alerts import open_alert

    alert, created = open_alert(
        conn,
        code=AlertCode.CHANNEL_SANCTION_SIGNAL.value,
        severity=AlertSeverity.CRITICAL,
        dedup_key=f"channel_sanction_signal:{row['channel_id']}",
        title=f"Канал {row['channel_id']}: {label} — YouTube отклонил ролик {row.get('platform_video_id')}",
        details={
            "channel_id": row["channel_id"],
            "publication_id": int(row["id"]),
            "platform_video_id": row.get("platform_video_id"),
            "rejection_reason": rejection,
        },
    )
    if created:
        repo.insert_channel_incident(
            conn,
            channel_id=row["channel_id"],
            kind="sanction",
            details={
                "source": "yt_data",
                "rejection_reason": rejection,
                "publication_id": int(row["id"]),
                "alert_id": int(alert["id"]),
            },
            note=f"YouTube Data API: {label} ({rejection})",
        )


def _resolve(conn: Any, dedup_key: str) -> None:
    from autoshorts.services.alerts import resolve_alert

    resolve_alert(conn, dedup_key)


def _apply_youtube(
    conn: Any,
    row: Row,
    obs: Any,
    now: datetime,
    schedule: Sequence[float],
    report: PollReport,
) -> PubOutcome:
    """Результат `videos.list` для одной публикации: снимок, статус, алерты, следующий опрос."""
    from autoshorts.collectors.youtube_data import REJECTION_LABELS, interpret_video

    pid = int(row["id"])
    verdict = interpret_video(obs)
    fields: dict[str, Any] = {}
    published_at = _published(row)
    if (
        obs is not None
        and obs.published_at is not None
        and abs(obs.published_at - published_at) > PUBLISHED_AT_TOLERANCE
    ):
        published_at = obs.published_at
        fields["published_at"] = published_at
        repo.recompute_ages(conn, pid, published_at)
    ref = str(row.get("platform_channel_ref") or "")
    if obs is not None and obs.channel_id and ref.startswith("UC"):
        verify = "ok" if obs.channel_id == ref else "channel_mismatch"
        if verify != row.get("verify_status"):
            fields["verify_status"] = verify
            fields["verify_detail"] = (
                f"YouTube Data API: канал ролика {obs.channel_id}"
                + ("" if verify == "ok" else f", а канал — {ref}")
            )[:500]
            fields["verified_at"] = now
    snapshot = None
    if obs is not None and obs.views is not None:
        snapshot = repo.insert_snapshot(
            conn,
            publication_id=pid,
            captured_at=now,
            age_hours=_age_hours(now, published_at),
            views=obs.views,
            likes=obs.likes,
            comments=obs.comments,
            source=MetricsSource.YT_DATA.value,
            raw=obs.raw(),
        )
    status = row["status"]
    outcome = "snapshot" if snapshot is not None else "no_data"
    if obs is None:
        if status in (PublicationStatus.LIVE.value, PublicationStatus.UNLISTED.value):
            status = PublicationStatus.REMOVED_BY_PLATFORM.value
            fields["status"] = status
            fields["status_reason"] = verdict.reason
            _alert_removed(conn, row, verdict.reason or "", verdict.code)
            outcome = "removed"
        else:
            outcome = "missing"
    elif verdict.removed:
        if (
            status != PublicationStatus.REMOVED_BY_PLATFORM.value
            or row.get("status_reason") != verdict.reason
        ):
            fields["status"] = PublicationStatus.REMOVED_BY_PLATFORM.value
            fields["status_reason"] = verdict.reason
        status = PublicationStatus.REMOVED_BY_PLATFORM.value
        if verdict.claim and not row.get("has_claim"):
            fields["has_claim"] = True
        _alert_removed(conn, row, verdict.reason or "", verdict.code)
        if verdict.sanction:
            rejection = obs.rejection_reason or ""
            _alert_sanction(conn, row, rejection, REJECTION_LABELS.get(rejection, rejection))
        outcome = "removed"
    else:
        new_status = verdict.status.value if verdict.status is not None else status
        if status == PublicationStatus.REMOVED_BY_PLATFORM.value:
            fields["status"] = new_status
            fields["status_reason"] = verdict.reason
            _resolve(conn, f"publication_removed:{pid}")
            outcome = "restored"
        elif new_status != status:
            fields["status"] = new_status
            fields["status_reason"] = verdict.reason
        elif verdict.reason and row.get("status_reason") != verdict.reason:
            fields["status_reason"] = verdict.reason
        status = new_status
        if verdict.claim and not row.get("has_claim"):
            fields["has_claim"] = True
    fields.update(
        {
            "poll_failures": 0,
            "last_poll_error": None,
            "metrics_collector": MetricsSource.YT_DATA.value,
        }
    )
    if snapshot is not None:
        fields["manual_due_at"] = None
    removed = status == PublicationStatus.REMOVED_BY_PLATFORM.value
    fields.update(_schedule_fields({**row, "published_at": published_at}, now, schedule, removed=removed))
    repo.update_publication(conn, pid, fields)
    views = None
    if snapshot is not None:
        views = recompute_publication(conn, pid, published_at)["latest_views"]
    _event(conn, pid)
    result = PubOutcome(
        pid,
        row["platform"],
        outcome,
        source=MetricsSource.YT_DATA.value,
        views_before=row.get("latest_views"),
        views=views if views is not None else row.get("latest_views"),
        status=status,
    )
    report.add(result)
    return result


def _apply_vk(
    conn: Any, row: Row, obs: Any, now: datetime, schedule: Sequence[float], report: PollReport
) -> None:
    pid = int(row["id"])
    published_at = _published(row)
    repo.insert_snapshot(
        conn,
        publication_id=pid,
        captured_at=now,
        age_hours=_age_hours(now, published_at),
        views=obs.views,
        likes=obs.likes,
        comments=obs.comments,
        shares=obs.shares,
        source=obs.source,
        raw=obs.raw or None,
    )
    partial = obs.views is None  # yt-dlp: лайки и комментарии есть, просмотров площадка не отдала
    fields: dict[str, Any] = {
        "poll_failures": 0,
        "last_poll_error": None,
        "manual_due_at": _arm_manual(row, now) if partial else None,
        "metrics_collector": obs.source,
    }
    outcome = "manual_due" if partial else "snapshot"
    status = row["status"]
    if status == PublicationStatus.REMOVED_BY_PLATFORM.value:
        fields["status"] = PublicationStatus.LIVE.value
        fields["status_reason"] = None
        status = PublicationStatus.LIVE.value
        _resolve(conn, f"publication_removed:{pid}")
        outcome = "restored"
    fields.update(_schedule_fields(row, now, schedule))
    repo.update_publication(conn, pid, fields)
    views = recompute_publication(conn, pid, published_at)["latest_views"]
    _event(conn, pid)
    report.add(
        PubOutcome(
            pid,
            row["platform"],
            outcome,
            source=obs.source,
            views_before=row.get("latest_views"),
            views=views,
            status=status,
        )
    )


def _vk_failed(
    conn: Any, row: Row, obs: Any, now: datetime, schedule: Sequence[float], report: PollReport
) -> None:
    """Неудача yt-dlp: «удалён» два раза подряд → removed_by_platform + алерт, иначе пауза/ручной ввод."""
    error = obs.error or "нет данных"
    gone_before = str(row.get("last_poll_error") or "").startswith("gone:")
    if (
        obs.gone
        and gone_before
        and row["status"] in (PublicationStatus.LIVE.value, PublicationStatus.UNLISTED.value)
    ):
        reason = f"VK: ролик недоступен ({error})"[:500]
        fields = {
            "status": PublicationStatus.REMOVED_BY_PLATFORM.value,
            "status_reason": reason,
            "poll_failures": min(int(row.get("poll_failures") or 0) + 1, 32767),
            "last_poll_error": f"gone: {error}"[:2000],
            **_schedule_fields(row, now, schedule, removed=True),
        }
        repo.update_publication(conn, int(row["id"]), fields)
        _alert_removed(conn, row, reason, "gone")
        _event(conn, int(row["id"]))
        report.add(
            PubOutcome(
                int(row["id"]),
                row["platform"],
                "removed",
                source=obs.source,
                status=fields["status"],
                error=error[:300],
            )
        )
        return
    _poll_failed(
        conn, row, now, schedule, report, (f"gone: {error}" if obs.gone else error), source=obs.source
    )


# ---------------------------------------------------------------- состояние коллектора и алерты


def _reason_message(reason: CollectorReason, extra: str | None = None) -> str:
    base = COLLECTOR_REASON_LABELS.get(reason, reason.value)
    return f"{base}. {extra}" if extra else base


def _collector_failed(
    conn: Any, collector: str, bucket: str, exc_kind: ErrorKind, error: str, report: PollReport
) -> None:
    from autoshorts.services.alerts import open_alert

    reason = _ERROR_REASON.get(exc_kind, CollectorReason.HTTP_ERROR)
    state = repo.state_failure(
        conn,
        collector,
        bucket,
        reason=reason.value,
        message=_reason_message(reason),
        error=error,
        failing_after=FAILING_AFTER,
    )
    report.collectors[f"{collector}:{bucket}"] = state["state"]
    if state["state"] == CollectorState.FAILING.value:
        open_alert(
            conn,
            code=AlertCode.COLLECTOR_FAILING.value,
            severity=AlertSeverity.WARNING,
            dedup_key=f"collector_failing:{collector}:{bucket}",
            title=f"Коллектор {collector} ({bucket}) не работает: "
            f"{COLLECTOR_REASON_LABELS.get(reason, reason.value)}",
            details={
                "collector": collector,
                "cluster_id": bucket,
                "reason": reason.value,
                "consecutive_failures": int(state["consecutive_failures"]),
                "last_error": error[:500],
            },
        )


def _collector_ok(
    conn: Any,
    collector: str,
    bucket: str,
    report: PollReport,
    *,
    reason: CollectorReason | None = None,
    message: str | None = None,
) -> None:
    state = CollectorState.DEGRADED.value if reason is not None else CollectorState.OK.value
    repo.state_ok(
        conn, collector, bucket, state=state, reason=reason.value if reason else None, message=message
    )
    report.collectors[f"{collector}:{bucket}"] = state
    _resolve(conn, f"collector_failing:{collector}:{bucket}")


def _quota_alert(
    conn: Any, bucket: str, units: int, hard: int, limit: int, *, exhausted: bool, reset_at: datetime
) -> None:
    from autoshorts.services.alerts import open_alert

    what = "исчерпана до сброса" if exhausted else f"израсходована на {round(units / limit * 100)}%"
    open_alert(
        conn,
        code=AlertCode.QUOTA_80.value,
        severity=AlertSeverity.WARNING,
        dedup_key=f"quota_80:{YT_QUOTA_API}:{bucket}",
        title=f"Квота YouTube Data API ({bucket}) {what}: {units} из {limit} единиц",
        details={
            "api": YT_QUOTA_API,
            "cluster_id": bucket,
            "units": units,
            "hard_stop": hard,
            "limit": limit,
            "exhausted": exhausted,
            "reset_at": reset_at.isoformat(),
        },
    )


# ---------------------------------------------------------------- опрос: YouTube


def _youtube_secret_name(key_secret: str | None) -> str:
    return key_secret or YOUTUBE_API_KEY_SECRET_DEFAULT


def _poll_youtube_bucket(
    bucket: str,
    key_secret: str | None,
    rows: list[Row],
    settings: Any,
    deps: PollDeps,
    report: PollReport,
    handled: set[int],
    now: datetime,
    schedule: Sequence[float],
    cancel: threading.Event | None,
    thumbs: list[tuple[str, str, str]],
) -> None:
    from autoshorts.collectors.http import make_client
    from autoshorts.collectors.youtube_data import VIDEO_ID_RE, YouTubeDataClient, chunks
    from autoshorts.db.pool import connection

    collector = MetricsSource.YT_DATA.value
    secret_name = _youtube_secret_name(key_secret)
    with connection() as conn:
        # кривой ID (legacy, ручная правка) не должен ронять всю пачку из 50: ошибка — только ему
        bad = [r for r in rows if not VIDEO_ID_RE.match(str(r["platform_video_id"]))]
        for row in bad:
            error = f"некорректный ID ролика YouTube: {str(row['platform_video_id'])[:40]!r}"
            _poll_failed(conn, row, now, schedule, report, error, source=collector)
            handled.add(int(row["id"]))
        rows = [r for r in rows if int(r["id"]) not in handled]
        if not rows:
            return
        key = _get_secret(secret_name, conn, deps)
        clk = quota_repo.clock(conn, YT_QUOTA_TIMEZONE)
        states = {(s["collector"], s["cluster_id"]): s for s in repo.list_states(conn)}
        if not key:
            message = (
                f"Нет ключа YouTube Data API: секрет {secret_name} не задан. "
                "Метрики YouTube ждут ручного ввода."
            )
            repo.state_set(
                conn,
                collector,
                bucket,
                state=CollectorState.DISABLED.value,
                reason=CollectorReason.NO_API_KEY.value,
                message=message,
            )
            report.collectors[f"{collector}:{bucket}"] = CollectorState.DISABLED.value
            for row in rows:
                _fallback_manual(conn, row, now, schedule, report, reason=CollectorReason.NO_API_KEY.value)
                handled.add(int(row["id"]))
            return
        paused = states.get((collector, bucket))
        if (
            paused
            and paused["state"] == CollectorState.PAUSED.value
            and paused.get("paused_until")
            and paused["paused_until"] > clk["now"]
        ):
            for row in rows:
                _defer(
                    conn, row, paused["paused_until"], report, reason=CollectorReason.QUOTA_EXHAUSTED.value
                )
                handled.add(int(row["id"]))
            report.collectors[f"{collector}:{bucket}"] = CollectorState.PAUSED.value
            return
    hard = int(settings.yt_quota_hard_units)
    limit = YT_DAILY_QUOTA_UNITS
    soft_units = math.ceil(limit * int(settings.yt_quota_soft_pct) / 100)
    units_now: int | None = None
    ok_batches = 0
    failures: list[CollectorError] = []
    exhausted = False
    batches = list(chunks(rows, YT_VIDEOS_LIST_BATCH))  # type: ignore[arg-type]
    with make_client(proxy=settings.collectors_proxy_url, transport=deps.transport) as client:
        api = YouTubeDataClient(key, client, sleep=deps.sleep)
        for index, batch in enumerate(batches):
            if cancel is not None and cancel.is_set():
                break
            with connection() as conn:
                total = quota_repo.try_consume(
                    conn,
                    api=YT_QUOTA_API,
                    cluster_id=bucket,
                    units=YT_VIDEOS_LIST_COST_UNITS,
                    hard_limit=hard,
                    tz=YT_QUOTA_TIMEZONE,
                )
                if total is None:
                    exhausted = True
                    for rest in batches[index:]:
                        for row in rest:
                            _defer(
                                conn,
                                row,
                                clk["reset_at"],
                                report,
                                reason=CollectorReason.QUOTA_EXHAUSTED.value,
                            )
                            handled.add(int(row["id"]))
                    break
            units_now = total
            report.api_calls["youtube_data.videos_list"] += 1
            try:
                res = api.videos_list([str(r["platform_video_id"]) for r in batch])
            except (CollectorError, ValueError) as exc:
                err = (
                    exc
                    if isinstance(exc, CollectorError)
                    else CollectorError(str(exc), kind=ErrorKind.BAD_RESPONSE)
                )
                with connection() as conn:
                    extra = max(0, err.http_attempts - 1)
                    units_now = quota_repo.add(
                        conn,
                        api=YT_QUOTA_API,
                        cluster_id=bucket,
                        tz=YT_QUOTA_TIMEZONE,
                        units=extra * YT_VIDEOS_LIST_COST_UNITS,
                        calls=extra,
                        errors=1,
                    )
                    if err.kind == ErrorKind.QUOTA:
                        exhausted = True
                        units_now = quota_repo.exhaust(
                            conn, api=YT_QUOTA_API, cluster_id=bucket, tz=YT_QUOTA_TIMEZONE, floor_units=hard
                        )
                        for rest in batches[index:]:
                            for row in rest:
                                _defer(
                                    conn,
                                    row,
                                    clk["reset_at"],
                                    report,
                                    reason=CollectorReason.QUOTA_EXHAUSTED.value,
                                )
                                handled.add(int(row["id"]))
                        break
                    failures.append(err)
                    stop = err.kind == ErrorKind.AUTH
                    for rest in batches[index:] if stop else [batch]:
                        for row in rest:
                            _poll_failed(conn, row, now, schedule, report, err.message, source=collector)
                            handled.add(int(row["id"]))
                if stop:
                    break
                continue
            if res.http_attempts > 1:
                extra = res.http_attempts - 1
                with connection() as conn:
                    units_now = quota_repo.add(
                        conn,
                        api=YT_QUOTA_API,
                        cluster_id=bucket,
                        tz=YT_QUOTA_TIMEZONE,
                        units=extra * YT_VIDEOS_LIST_COST_UNITS,
                        calls=extra,
                    )
            with connection() as conn:
                for row in batch:
                    vid = str(row["platform_video_id"])
                    obs = res.found.get(vid)
                    applied = _safe_apply(
                        conn,
                        row,
                        lambda row=row, obs=obs: _apply_youtube(conn, row, obs, now, schedule, report),
                        now,
                        schedule,
                        report,
                        source=collector,
                    )
                    handled.add(int(row["id"]))
                    if applied and deps.thumbnails and obs is not None and obs.thumbnail_url:
                        thumbs.append(("youtube", vid, obs.thumbnail_url))
            ok_batches += 1
    with connection() as conn:
        if exhausted:
            repo.state_set(
                conn,
                collector,
                bucket,
                state=CollectorState.PAUSED.value,
                reason=CollectorReason.QUOTA_EXHAUSTED.value,
                message=_reason_message(
                    CollectorReason.QUOTA_EXHAUSTED,
                    f"Опрос отложен до сброса квоты ({clk['reset_at']:%Y-%m-%d %H:%M} UTC)",
                ),
                paused_until=clk["reset_at"],
            )
            report.collectors[f"{collector}:{bucket}"] = CollectorState.PAUSED.value
            _quota_alert(
                conn, bucket, units_now or hard, hard, limit, exhausted=True, reset_at=clk["reset_at"]
            )
        elif failures and not ok_batches:
            last = failures[-1]
            _collector_failed(conn, collector, bucket, last.kind, last.message, report)
        elif failures:
            last = failures[-1]
            reason = _ERROR_REASON.get(last.kind, CollectorReason.HTTP_ERROR)
            _collector_ok(
                conn,
                collector,
                bucket,
                report,
                reason=reason,
                message=_reason_message(reason, last.message[:200]),
            )
        elif ok_batches:
            if units_now is not None and units_now >= soft_units:
                _collector_ok(
                    conn,
                    collector,
                    bucket,
                    report,
                    reason=CollectorReason.QUOTA_SOFT_LIMIT,
                    message=_reason_message(
                        CollectorReason.QUOTA_SOFT_LIMIT, f"{units_now} из {limit} единиц"
                    ),
                )
                _quota_alert(conn, bucket, units_now, hard, limit, exhausted=False, reset_at=clk["reset_at"])
            else:
                _collector_ok(conn, collector, bucket, report)
                _resolve(conn, f"quota_80:{YT_QUOTA_API}:{bucket}")


# ---------------------------------------------------------------- опрос: VK


def _poll_vk(
    rows: list[Row],
    settings: Any,
    deps: PollDeps,
    report: PollReport,
    handled: set[int],
    now: datetime,
    schedule: Sequence[float],
    cancel: threading.Event | None,
    thumbs: list[tuple[str, str, str]],
) -> None:
    from autoshorts.collectors.http import make_client
    from autoshorts.collectors.vk_video import (
        VK_VIDEO_GET_BATCH,
        VK_VIDEO_ID_RE,
        VkApiClient,
        YtdlpMetaFetcher,
        vk_clip_url,
    )
    from autoshorts.collectors.youtube_data import chunks
    from autoshorts.db.pool import connection

    remaining = list(rows)
    with connection() as conn:
        token = _get_secret(VK_TOKEN_SECRET, conn, deps)
    if token and remaining:
        found: dict[str, Any] = {}
        failed: CollectorError | None = None
        with make_client(proxy=settings.collectors_proxy_url, transport=deps.transport) as client:
            api = VkApiClient(token, client, sleep=deps.sleep)
            api_ids = [str(r["platform_video_id"]) for r in remaining]
            for batch in chunks([v for v in api_ids if VK_VIDEO_ID_RE.match(v)], VK_VIDEO_GET_BATCH):
                try:
                    found.update(api.video_get(batch))
                except (CollectorError, ValueError) as exc:
                    failed = (
                        exc
                        if isinstance(exc, CollectorError)
                        else CollectorError(str(exc), kind=ErrorKind.BAD_RESPONSE)
                    )
                    break
        report.api_calls["vk_api.video_get"] += api.calls
        with connection() as conn:
            quota_repo.add(
                conn,
                api=VK_API_QUOTA,
                cluster_id=DEFAULT_BUCKET,
                tz=YT_QUOTA_TIMEZONE,
                units=api.calls,
                calls=api.calls,
                errors=1 if failed else 0,
            )
            if failed is not None and not found:
                _collector_failed(
                    conn, MetricsSource.VK_API.value, DEFAULT_BUCKET, failed.kind, failed.message, report
                )
            else:
                _collector_ok(conn, MetricsSource.VK_API.value, DEFAULT_BUCKET, report)
            left: list[Row] = []
            for row in remaining:
                obs = found.get(str(row["platform_video_id"]))
                if obs is None or obs.views is None:
                    left.append(row)
                    continue
                applied = _safe_apply(
                    conn,
                    row,
                    lambda row=row, obs=obs: _apply_vk(conn, row, obs, now, schedule, report),
                    now,
                    schedule,
                    report,
                    source=MetricsSource.VK_API.value,
                )
                handled.add(int(row["id"]))
                if applied and deps.thumbnails and obs.thumbnail_url:
                    thumbs.append(("vk", str(row["platform_video_id"]), obs.thumbnail_url))
        remaining = left
    if not remaining:
        return
    fetcher = deps.ytdlp or YtdlpMetaFetcher(proxy=settings.collectors_proxy_url)
    collector = MetricsSource.YTDLP.value
    if not fetcher.available():
        with connection() as conn:
            repo.state_set(
                conn,
                collector,
                DEFAULT_BUCKET,
                state=CollectorState.DISABLED.value,
                reason=CollectorReason.TOOL_MISSING.value,
                message=_reason_message(CollectorReason.TOOL_MISSING, "Метрики VK ждут ручного ввода."),
            )
            report.collectors[f"{collector}:{DEFAULT_BUCKET}"] = CollectorState.DISABLED.value
            for row in remaining:
                _fallback_manual(conn, row, now, schedule, report, reason=CollectorReason.TOOL_MISSING.value)
                handled.add(int(row["id"]))
        return
    ok = 0
    partial = 0
    last_error: str | None = None
    for row in remaining:
        if cancel is not None and cancel.is_set():
            break
        vid = str(row["platform_video_id"])
        obs = fetcher.fetch(vid, vk_clip_url(vid, row.get("canonical_url")))
        report.api_calls["ytdlp.extract_info"] += 1
        with connection() as conn:
            if obs.partial:
                if _safe_apply(
                    conn,
                    row,
                    lambda row=row, obs=obs: _apply_vk(conn, row, obs, now, schedule, report),
                    now,
                    schedule,
                    report,
                    source=collector,
                ):
                    partial += 1
            elif obs.found:
                if _safe_apply(
                    conn,
                    row,
                    lambda row=row, obs=obs: _apply_vk(conn, row, obs, now, schedule, report),
                    now,
                    schedule,
                    report,
                    source=collector,
                ):
                    ok += 1
                    if deps.thumbnails and obs.thumbnail_url:
                        thumbs.append(("vk", vid, obs.thumbnail_url))
            else:
                last_error = obs.error or "нет данных"
                _safe_apply(
                    conn,
                    row,
                    lambda row=row, obs=obs: _vk_failed(conn, row, obs, now, schedule, report),
                    now,
                    schedule,
                    report,
                    source=collector,
                )
        handled.add(int(row["id"]))
    with connection() as conn:
        if last_error is not None and ok == 0:
            _collector_failed(conn, collector, DEFAULT_BUCKET, ErrorKind.HTTP, last_error, report)
        elif last_error is not None:
            _collector_ok(
                conn,
                collector,
                DEFAULT_BUCKET,
                report,
                reason=CollectorReason.HTTP_ERROR,
                message=_reason_message(CollectorReason.HTTP_ERROR, last_error[:200]),
            )
        elif partial and not ok:
            _collector_ok(
                conn,
                collector,
                DEFAULT_BUCKET,
                report,
                reason=CollectorReason.MANUAL_ONLY,
                message="yt-dlp не отдаёт просмотры клипов VK: лайки и комментарии собираются, "
                "просмотры — ручным вводом",
            )
        elif ok:
            _collector_ok(conn, collector, DEFAULT_BUCKET, report)


# ---------------------------------------------------------------- опрос: вход


def _fetch_thumbnails(
    thumbs: list[tuple[str, str, str]], settings: Any, deps: PollDeps, report: PollReport
) -> None:
    from autoshorts.collectors.http import make_client
    from autoshorts.collectors.thumbnails import fetch_thumbnail
    from autoshorts.core.storage import get_layout

    media = deps.media or get_layout()
    with make_client(proxy=settings.collectors_proxy_url, transport=deps.transport) as client:
        for platform, vid, url in thumbs:
            try:
                res = fetch_thumbnail(client, url, media.thumb_path(platform, vid, create_dir=True))
            except Exception:  # noqa: BLE001 - обложка необязательна
                log.warning("thumbnail %s/%s failed", platform, vid, exc_info=True)
                continue
            if res.saved:
                report.thumbnails += 1


def poll_metrics(
    *,
    settings: Any = None,
    publication_ids: Sequence[int] | None = None,
    platform: str | None = None,
    deps: PollDeps | None = None,
    cancel: threading.Event | None = None,
    now: datetime | None = None,
    progress: Callable[[float], None] | None = None,
) -> PollReport:
    """Один прогон коллекторов: созревшие публикации (или указанные — вне расписания).

    Сеть вне транзакций. Каждая взятая публикация получает новый `next_poll_at`: по лестнице,
    пауза после ошибки, сброс квоты или возврат в расписание при отмене.
    """
    from autoshorts.db.pool import connection

    deps = deps or PollDeps()
    report = PollReport()
    ids = [int(i) for i in publication_ids] if publication_ids else None
    with connection() as conn:
        if settings is None:
            from autoshorts.services.settings import get_runtime_settings

            settings = get_runtime_settings(conn)
        rows: list[Row] = []
        if platform in (None, Platform.YOUTUBE.value):
            rows += repo.claim_due(
                conn,
                lease_sec=POLL_LEASE_SEC,
                limit=YT_MAX_PER_RUN,
                platform=Platform.YOUTUBE.value,
                publication_ids=ids,
            )
        if platform in (None, Platform.VK.value):
            rows += repo.claim_due(
                conn,
                lease_sec=POLL_LEASE_SEC,
                limit=len(ids) if ids else VK_MAX_PER_RUN,
                platform=Platform.VK.value,
                publication_ids=ids,
            )
    now = now or _utcnow()
    schedule = poll_schedule_hours(settings.metrics_poll_ladder_hours)
    handled: set[int] = set()
    thumbs: list[tuple[str, str, str]] = []
    try:
        manual_rows = [
            r for r in rows if r.get("metrics_collector") == MetricsSource.MANUAL.value and not ids
        ]
        if manual_rows:
            with connection() as conn:
                for row in manual_rows:
                    _fallback_manual(
                        conn, row, now, schedule, report, reason=CollectorReason.MANUAL_ONLY.value
                    )
                    handled.add(int(row["id"]))
        auto = [r for r in rows if int(r["id"]) not in handled]
        buckets: dict[tuple[str, str | None], list[Row]] = defaultdict(list)
        vk_rows: list[Row] = []
        for row in auto:
            if row["platform"] == Platform.YOUTUBE.value:
                buckets[(row["quota_bucket"], row.get("youtube_api_key_secret"))].append(row)
            else:
                vk_rows.append(row)
        total = max(1, len(auto))
        for (bucket, key_secret), group in sorted(buckets.items(), key=lambda kv: kv[0][0]):
            if cancel is not None and cancel.is_set():
                break
            _poll_youtube_bucket(
                bucket, key_secret, group, settings, deps, report, handled, now, schedule, cancel, thumbs
            )
            if progress:
                progress(min(0.95, len(handled) / total))
        if vk_rows and not (cancel is not None and cancel.is_set()):
            _poll_vk(vk_rows, settings, deps, report, handled, now, schedule, cancel, thumbs)
        if thumbs:
            _fetch_thumbnails(thumbs, settings, deps, report)
    finally:
        unhandled = [r for r in rows if int(r["id"]) not in handled]
        if unhandled:
            try:
                with connection() as conn:
                    for row in unhandled:
                        repo.release(conn, int(row["id"]), row.get("due_at") or now)
                        report.add(
                            PubOutcome(int(row["id"]), row["platform"], "released", status=row["status"])
                        )
            except Exception:  # noqa: BLE001 - аренда истечёт сама
                log.warning("metrics: release of %d publications failed", len(unhandled), exc_info=True)
    return report


# ---------------------------------------------------------------- снимки каналов


def collect_channel_stats(
    *, settings: Any = None, deps: PollDeps | None = None, cancel: threading.Event | None = None
) -> dict[str, Any]:
    """Задача `channel_stats`: `channels.list` по кустам (1 единица за 50 каналов) и VK `groups.getById`."""
    from autoshorts.collectors.http import make_client
    from autoshorts.collectors.youtube_data import CHANNEL_ID_RE, YouTubeDataClient, chunks
    from autoshorts.db.pool import connection
    from autoshorts.services.alerts import open_alert

    deps = deps or PollDeps()
    out: dict[str, Any] = {
        "youtube": {"snapshots": 0, "missing": [], "skipped": []},
        "vk": {"snapshots": 0, "skipped": None},
    }
    with connection() as conn:
        if settings is None:
            from autoshorts.services.settings import get_runtime_settings

            settings = get_runtime_settings(conn)
        yt_channels = [
            c
            for c in repo.channels_for_stats(conn, Platform.YOUTUBE.value)
            if CHANNEL_ID_RE.match(str(c["platform_channel_ref"]))
        ]
        vk_channels = repo.channels_for_stats(conn, Platform.VK.value)
    groups: dict[tuple[str, str | None], list[Row]] = defaultdict(list)
    for ch in yt_channels:
        groups[(ch["bucket"], ch.get("youtube_api_key_secret"))].append(ch)
    hard = int(settings.yt_quota_hard_units)
    for (bucket, key_secret), channels in sorted(groups.items(), key=lambda kv: kv[0][0]):
        with connection() as conn:
            key = _get_secret(_youtube_secret_name(key_secret), conn, deps)
        if not key:
            out["youtube"]["skipped"].append(
                {"cluster_id": bucket, "reason": CollectorReason.NO_API_KEY.value}
            )
            continue
        with make_client(proxy=settings.collectors_proxy_url, transport=deps.transport) as client:
            api = YouTubeDataClient(key, client, sleep=deps.sleep)
            for batch in chunks(channels, YT_VIDEOS_LIST_BATCH):  # type: ignore[arg-type]
                if cancel is not None and cancel.is_set():
                    break
                with connection() as conn:
                    if (
                        quota_repo.try_consume(
                            conn,
                            api=YT_QUOTA_API,
                            cluster_id=bucket,
                            units=1,
                            hard_limit=hard,
                            tz=YT_QUOTA_TIMEZONE,
                        )
                        is None
                    ):
                        out["youtube"]["skipped"].append(
                            {"cluster_id": bucket, "reason": CollectorReason.QUOTA_EXHAUSTED.value}
                        )
                        break
                try:
                    res = api.channels_list([str(c["platform_channel_ref"]) for c in batch])
                except CollectorError as exc:
                    with connection() as conn:
                        quota_repo.add(
                            conn, api=YT_QUOTA_API, cluster_id=bucket, tz=YT_QUOTA_TIMEZONE, errors=1
                        )
                    out["youtube"]["skipped"].append(
                        {"cluster_id": bucket, "reason": exc.kind.value, "error": exc.message}
                    )
                    break
                with connection() as conn:
                    for ch in batch:
                        obs = res.found.get(str(ch["platform_channel_ref"]))
                        if obs is None:
                            out["youtube"]["missing"].append(ch["id"])
                            open_alert(
                                conn,
                                code=AlertCode.CHANNEL_SANCTION_SIGNAL.value,
                                severity=AlertSeverity.WARNING,
                                dedup_key=f"channel_missing:{ch['id']}",
                                title=f"Канал {ch['id']} не найден в YouTube Data API: "
                                "удалён, заблокирован или неверный ID",
                                details={
                                    "channel_id": ch["id"],
                                    "platform_channel_ref": ch["platform_channel_ref"],
                                },
                            )
                            continue
                        _resolve(conn, f"channel_missing:{ch['id']}")
                        repo.insert_channel_snapshot(
                            conn,
                            channel_id=ch["id"],
                            subscribers=obs.subscribers,
                            total_views=obs.total_views,
                            video_count=obs.video_count,
                            source=MetricsSource.YT_DATA.value,
                        )
                        if obs.subscribers is not None:
                            repo.update_channel_subscribers(conn, ch["id"], obs.subscribers)
                        out["youtube"]["snapshots"] += 1
    if vk_channels:
        from autoshorts.collectors.vk_video import VkApiClient
        from autoshorts.domain.url_parse import normalize_vk_owner

        with connection() as conn:
            token = _get_secret(VK_TOKEN_SECRET, conn, deps)
        owners: dict[int, Row] = {}
        for ch in vk_channels:
            owner = normalize_vk_owner(ch.get("platform_channel_ref"))
            if owner and owner.startswith("-") and owner[1:].isdigit():
                owners[int(owner[1:])] = ch
        if not token:
            out["vk"]["skipped"] = CollectorReason.NOT_CONFIGURED.value
        elif owners:
            try:
                with make_client(proxy=settings.collectors_proxy_url, transport=deps.transport) as client:
                    api = VkApiClient(token, client, sleep=deps.sleep)
                    found = api.groups_members(sorted(owners))
                with connection() as conn:
                    quota_repo.add(
                        conn,
                        api=VK_API_QUOTA,
                        cluster_id=DEFAULT_BUCKET,
                        tz=YT_QUOTA_TIMEZONE,
                        units=api.calls,
                        calls=api.calls,
                    )
                    for gid, obs in found.items():
                        ch = owners.get(gid)
                        if ch is None:
                            continue
                        repo.insert_channel_snapshot(
                            conn,
                            channel_id=ch["id"],
                            subscribers=obs.members,
                            total_views=None,
                            video_count=None,
                            source=MetricsSource.VK_API.value,
                        )
                        if obs.members is not None:
                            repo.update_channel_subscribers(conn, ch["id"], obs.members)
                        out["vk"]["snapshots"] += 1
            except CollectorError as exc:
                out["vk"]["skipped"] = exc.kind.value
    return out


# ---------------------------------------------------------------- свежесть и статусы коллекторов


def _state_index(conn: Any) -> dict[tuple[str, str], Row]:
    return {(s["collector"], s["cluster_id"]): s for s in repo.list_states(conn)}


def freshness_map(
    conn: Any,
    publication_ids: Sequence[int],
    *,
    now: datetime | None = None,
    states: dict[tuple[str, str], Row] | None = None,
) -> dict[int, dict[str, Any]]:
    """`PublicationFreshness` для пачки публикаций (для `Publication.freshness` в M3-A и карточки ролика)."""
    rows = repo.freshness_rows(conn, list(publication_ids))
    if not rows:
        return {}
    states = states if states is not None else _state_index(conn)
    now = now or _utcnow()
    grace = timedelta(minutes=METRICS_OVERDUE_GRACE_MIN)
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        collector = r.get("metrics_collector") or primary_collector(r["platform"])
        if collector == MetricsSource.MANUAL.value:
            c_state, c_reason = CollectorState.IDLE.value, CollectorReason.MANUAL_ONLY.value
        else:
            bucket = r["bucket"] if collector == MetricsSource.YT_DATA.value else DEFAULT_BUCKET
            st = states.get((collector, bucket))
            c_state, c_reason = (st["state"], st["reason"]) if st else (None, None)
        last = r.get("last_captured_at")
        nxt = r.get("next_poll_at")
        manual_due_at = r.get("manual_due_at")
        out[int(r["id"])] = {
            "collector": collector,
            "collector_state": c_state,
            "collector_reason": c_reason,
            "last_polled_at": r.get("last_auto_at"),
            "last_captured_at": last,
            "next_poll_at": nxt,
            "poll_step": int(r.get("poll_step") or 0),
            "metrics_age_min": int((now - last).total_seconds() // 60) if last else None,
            "stale": bool(nxt is not None and nxt < now - grace),
            "manual_due": bool(manual_due_at is not None and manual_due_at <= now),
            "manual_due_at": manual_due_at,
            "poll_failures": int(r.get("poll_failures") or 0),
            "last_poll_error": r.get("last_poll_error"),
        }
    return out


def publication_freshness(conn: Any, publication_id: int) -> dict[str, Any] | None:
    return freshness_map(conn, [publication_id]).get(int(publication_id))


def latest_source_map(conn: Any, publication_ids: Sequence[int]) -> dict[int, str]:
    """{id: источник последнего снимка с просмотрами} — для `PublicationLatest.source` (M3-A)."""
    return {int(r["publication_id"]): r["source"] for r in repo.latest_sources(conn, list(publication_ids))}


def _secret_is_set(name: str, db_status: dict[str, Any]) -> bool:
    from autoshorts.services.secrets import is_set

    return is_set(name, db_status)


def collector_statuses(conn: Any, *, deps: PollDeps | None = None) -> list[dict[str, Any]]:
    """`CollectorStatus` по всем известным коллекторам и вёдрам ключей (для /quota, Обзора, Настроек)."""
    from autoshorts.collectors.vk_video import ytdlp_available
    from autoshorts.services.secrets import status_map

    states = _state_index(conn)
    counts = {(r["collector"], r["bucket"]): r for r in repo.collector_counts(conn)}
    manual = repo.manual_counts(conn)
    db_status = status_map(conn)
    # ведра — по текущей привязке каналов к кустам; строка состояния куста, который отказался от своего
    # ключа, устаревает и не показывается
    yt_secrets = {b["bucket"]: _youtube_secret_name(b["key_secret"]) for b in repo.youtube_buckets(conn)}
    if not yt_secrets:
        yt_secrets[DEFAULT_BUCKET] = YOUTUBE_API_KEY_SECRET_DEFAULT
    vk_token_set = _secret_is_set(VK_TOKEN_SECRET, db_status)
    keys: list[tuple[str, str]] = [(MetricsSource.YT_DATA.value, b) for b in sorted(yt_secrets)]
    if vk_token_set or (MetricsSource.VK_API.value, DEFAULT_BUCKET) in states:
        keys.append((MetricsSource.VK_API.value, DEFAULT_BUCKET))
    keys += [(MetricsSource.YTDLP.value, DEFAULT_BUCKET), (MetricsSource.MANUAL.value, DEFAULT_BUCKET)]
    for key in states:
        if key not in keys and key[0] != MetricsSource.YT_DATA.value:
            keys.append(key)
    items: list[dict[str, Any]] = []
    tool = None
    for collector, bucket in keys:
        st = states.get((collector, bucket))
        key_secret: str | None = None
        key_set: bool | None = None
        if collector == MetricsSource.YT_DATA.value:
            key_secret = yt_secrets.get(bucket, YOUTUBE_API_KEY_SECRET_DEFAULT)
            key_set = _secret_is_set(key_secret, db_status)
        elif collector == MetricsSource.VK_API.value:
            key_secret, key_set = VK_TOKEN_SECRET, vk_token_set
        state = st["state"] if st else CollectorState.IDLE.value
        reason = st["reason"] if st else None
        message = st["message"] if st else None
        if collector == MetricsSource.YT_DATA.value:
            if not key_set:
                state, reason = CollectorState.DISABLED.value, CollectorReason.NO_API_KEY.value
                message = f"Нет ключа YouTube Data API: задайте секрет {key_secret}. До этого — ручной ввод."
            elif st and st["reason"] == CollectorReason.NO_API_KEY.value:
                state, reason, message = (
                    CollectorState.IDLE.value,
                    None,
                    "Ключ задан: коллектор включится при следующем опросе",
                )
        elif collector == MetricsSource.VK_API.value and not key_set:
            state, reason = CollectorState.DISABLED.value, CollectorReason.NOT_CONFIGURED.value
            message = f"Не задан токен VK ({VK_TOKEN_SECRET}): VK собирается через yt-dlp и вручную"
        elif collector == MetricsSource.YTDLP.value:
            if tool is None:
                tool = ytdlp_available() if deps is None or deps.ytdlp is None else deps.ytdlp.available()
            if not tool:
                state, reason = CollectorState.DISABLED.value, CollectorReason.TOOL_MISSING.value
                message = _reason_message(CollectorReason.TOOL_MISSING)
            elif st and st["reason"] == CollectorReason.TOOL_MISSING.value:
                state, reason, message = CollectorState.IDLE.value, None, None
        elif collector == MetricsSource.MANUAL.value:
            state, reason = CollectorState.IDLE.value, CollectorReason.MANUAL_ONLY.value
            message = message or "Ручной ввод: VK без yt-dlp, YouTube без ключа, долгие ошибки коллекторов"
        if collector == MetricsSource.MANUAL.value:
            total, due = int(manual["total"] or 0), int(manual["due"] or 0)
        else:
            cnt = counts.get((collector, bucket))
            total, due = (int(cnt["total"]), int(cnt["due"])) if cnt else (0, 0)
        items.append(
            {
                "collector": collector,
                "cluster_id": bucket,
                "state": state,
                "reason": reason,
                "message": message,
                "key_secret": key_secret,
                "key_set": key_set,
                "last_run_at": st["last_run_at"] if st else None,
                "last_ok_at": st["last_ok_at"] if st else None,
                "last_error_at": st["last_error_at"] if st else None,
                "last_error": st["last_error"] if st else None,
                "consecutive_failures": int(st["consecutive_failures"]) if st else 0,
                "paused_until": st["paused_until"] if st else None,
                "publications_total": total,
                "publications_due": due,
            }
        )
    return items


# ---------------------------------------------------------------- квоты (GET /quota)


def _quota_day_view(row: Row, settings: Any) -> dict[str, Any]:
    yt = row["api"] == YT_QUOTA_API
    limit = YT_DAILY_QUOTA_UNITS if yt else None
    units = int(row.get("units") or 0)
    return {
        "api": row["api"],
        "cluster_id": row["cluster_id"],
        "quota_day": row["quota_day"],
        "units": units,
        "calls": int(row.get("calls") or 0),
        "errors": int(row.get("errors") or 0),
        "limit": limit,
        "hard_stop": int(settings.yt_quota_hard_units) if yt else None,
        "soft_pct": int(settings.yt_quota_soft_pct) if yt else None,
        "pct": round(units / limit * 100, 2) if limit else None,
        "updated_at": row.get("updated_at"),
    }


def quota_usage(
    conn: Any,
    *,
    date_from: date | None,
    date_to: date | None,
    apis: Sequence[str] | None,
    cluster_ids: Sequence[str] | None,
    settings: Any = None,
) -> dict[str, Any]:
    from autoshorts.api.schemas.quota import QUOTA_DEFAULT_DAYS, QUOTA_MAX_DAYS
    from autoshorts.core.errors import ValidationFailed

    if settings is None:
        from autoshorts.services.settings import get_runtime_settings

        settings = get_runtime_settings(conn)
    clk = quota_repo.clock(conn, YT_QUOTA_TIMEZONE)
    today: date = clk["quota_day"]
    d_to = date_to or (max(today, date_from) if date_from else today)
    d_from = date_from or (d_to - timedelta(days=QUOTA_DEFAULT_DAYS - 1))
    if d_from > d_to or (d_to - d_from).days + 1 > QUOTA_MAX_DAYS:
        raise ValidationFailed(
            "Неверный период квот",
            details={
                "errors": [
                    {
                        "loc": ["query", "date_from"],
                        "msg": f"период от 1 до {QUOTA_MAX_DAYS} дней, date_from не позже date_to",
                        "type": "date_range",
                    }
                ]
            },
        )
    apis = [str(a) for a in apis] if apis else None
    cluster_ids = [str(c) for c in cluster_ids] if cluster_ids else None
    rows = quota_repo.list_usage(conn, date_from=d_from, date_to=d_to, apis=apis, cluster_ids=cluster_ids)
    items = [_quota_day_view(r, settings) for r in rows]
    today_rows = {
        (r["api"], r["cluster_id"]): r for r in quota_repo.list_usage(conn, date_from=today, date_to=today)
    }
    combos: set[tuple[str, str]] = set(today_rows)
    for b in repo.youtube_buckets(conn):
        combos.add((YT_QUOTA_API, b["bucket"]))
    if not any(api == YT_QUOTA_API for api, _ in combos):
        combos.add((YT_QUOTA_API, DEFAULT_BUCKET))
    today_items = []
    for api, bucket in sorted(combos):
        if apis and api not in apis:
            continue
        if cluster_ids and bucket not in cluster_ids:
            continue
        base = today_rows.get((api, bucket)) or {
            "api": api,
            "cluster_id": bucket,
            "quota_day": today,
            "units": 0,
            "calls": 0,
            "errors": 0,
            "updated_at": None,
        }
        today_items.append(_quota_day_view(base, settings))
    return {
        "date_from": d_from,
        "date_to": d_to,
        "items": items,
        "today": today_items,
        "reset_at": clk["reset_at"],
        "collectors": collector_statuses(conn),
    }


# ---------------------------------------------------------------- ручной ввод


def _rejected(reason: MetricsRejectReason, message: str, **details: Any) -> Exception:
    from autoshorts.core.errors import AppError

    return AppError(message, code="metrics_rejected", status=422, details={"reason": reason.value, **details})


def snapshot_view(row: Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "publication_id": int(row["publication_id"]),
        "captured_at": row["captured_at"],
        "age_hours": float(row["age_hours"]) if row.get("age_hours") is not None else None,
        "views": row.get("views"),
        "likes": row.get("likes"),
        "comments": row.get("comments"),
        "shares": row.get("shares"),
        "engaged_views": row.get("engaged_views"),
        "source": row["source"],
        "created_by": row.get("created_by"),
        "note": row.get("note"),
    }


def _aware(value: datetime, tz: str) -> datetime:
    """Наивное время из формы — в бизнес-поясе (человек вводит московское время)."""
    if value.tzinfo is None:
        from zoneinfo import ZoneInfo

        return value.replace(tzinfo=ZoneInfo(tz)).astimezone(UTC)
    return value.astimezone(UTC)


def create_manual(
    conn: Any, publication_id: int, body: Any, actor: Any, *, now: datetime | None = None
) -> dict[str, Any]:
    """`POST /publications/{id}/metrics/manual`: снимок `source=manual`, пересчёт публикации."""
    from autoshorts.core.errors import NotFound
    from autoshorts.services import audit
    from autoshorts.services.settings import get_runtime_settings

    row = repo.publication_for_manual(conn, publication_id)
    if row is None:
        raise NotFound("publication", "Публикация не найдена")
    if row.get("deleted_at") is not None or row["status"] == PublicationStatus.DELETED_BY_US.value:
        raise _rejected(
            MetricsRejectReason.PUBLICATION_DELETED, "Публикация удалена: метрики к ней не принимаются"
        )
    now = now or _utcnow()
    tz = get_runtime_settings(conn).business_timezone
    captured = _aware(body.captured_at, tz) if body.captured_at is not None else now
    if captured > now + MANUAL_FUTURE_TOLERANCE:
        raise _rejected(
            MetricsRejectReason.IN_FUTURE, "Время снимка в будущем", captured_at=captured.isoformat()
        )
    published_at = _published(row)
    if captured < published_at:
        raise _rejected(
            MetricsRejectReason.BEFORE_PUBLICATION,
            "Время снимка раньше публикации",
            captured_at=captured.isoformat(),
            published_at=published_at.isoformat(),
        )
    prev = None
    newest = True  # ручной снимок станет последним — ответ на ожидание ручного ввода
    for snap in repo.snapshots(conn, publication_id):
        if snap["captured_at"] <= captured and snap.get("views") is not None:
            prev = snap
        if snap["captured_at"] > captured:
            newest = False
    warnings: list[str] = []
    if prev is not None:
        if body.views < int(prev["views"]):
            warnings.append("views_decreased")
        elif int(prev["views"]) > 0 and body.views > LARGE_JUMP_FACTOR * int(prev["views"]):
            warnings.append("large_jump")
    user_id = getattr(actor, "user_id", None)
    snap = repo.insert_snapshot(
        conn,
        publication_id=publication_id,
        captured_at=captured,
        age_hours=_age_hours(captured, published_at),
        views=body.views,
        likes=body.likes,
        comments=body.comments,
        shares=body.shares,
        source=MetricsSource.MANUAL.value,
        created_by=user_id,
        note=body.note,
    )
    if row.get("manual_due_at") is not None and newest:
        repo.update_publication(conn, publication_id, {"manual_due_at": None})
    recompute_publication(conn, publication_id, published_at)
    audit.record(
        conn,
        actor,
        "publication.metrics_manual",
        "publication",
        publication_id,
        summary=f"ручной снимок: {body.views} просмотров на {captured:%Y-%m-%d %H:%M} UTC",
        diff={
            "snapshot_id": int(snap["id"]),
            "views": body.views,
            "captured_at": captured.isoformat(),
            "warnings": warnings,
        },
    )
    _event(conn, publication_id)
    return {
        "snapshot": snapshot_view(snap),
        "freshness": freshness_map(conn, [publication_id], now=now)[int(publication_id)],
        "warnings": warnings,
    }


def delete_manual(conn: Any, publication_id: int, snapshot_id: int, actor: Any) -> None:
    """`DELETE /publications/{id}/metrics/{snapshot_id}`: только ручной снимок (иначе 409)."""
    from autoshorts.core.errors import Conflict, NotFound
    from autoshorts.services import audit

    row = repo.publication_for_manual(conn, publication_id)
    if row is None:
        raise NotFound("publication", "Публикация не найдена")
    snap = repo.get_snapshot(conn, snapshot_id, for_update=True)
    if snap is None or int(snap["publication_id"]) != int(publication_id):
        raise NotFound("snapshot", "Снимок метрик не найден")
    if snap["source"] != MetricsSource.MANUAL.value:
        raise Conflict(
            "Удалять можно только ручной снимок: снимки коллекторов не удаляются",
            code="metrics_snapshot_not_manual",
            details={"snapshot_id": int(snapshot_id), "source": snap["source"]},
        )
    repo.delete_snapshot(conn, snapshot_id)
    recompute_publication(conn, publication_id, _published(row))
    audit.record(
        conn,
        actor,
        "publication.metrics_manual_delete",
        "publication",
        publication_id,
        summary=f"удалён ручной снимок {snapshot_id}",
        diff={
            "snapshot_id": int(snapshot_id),
            "views": snap.get("views"),
            "captured_at": snap["captured_at"].isoformat(),
        },
    )
    _event(conn, publication_id)


def manual_due(
    conn: Any,
    *,
    platforms: Sequence[str] | None,
    channel_ids: Sequence[str] | None,
    limit: int,
    offset: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """`GET /metrics/manual-due`: публикации, ждущие ручного ввода, самые просроченные первыми."""
    now = now or _utcnow()
    rows, total = repo.manual_due_page(
        conn, platforms=platforms, channel_ids=channel_ids, limit=limit, offset=offset
    )
    states = _state_index(conn)
    items = []
    for r in rows:
        collector = r.get("metrics_collector") or primary_collector(r["platform"])
        if collector == MetricsSource.MANUAL.value:
            reason = CollectorReason.MANUAL_ONLY.value
        else:
            st = states.get(
                (collector, r["bucket"] if collector == MetricsSource.YT_DATA.value else DEFAULT_BUCKET)
            )
            reason = st["reason"] if st else None
        published = r.get("published_at") or r["created_at"]
        items.append(
            {
                "publication_id": int(r["publication_id"]),
                "job_id": int(r["job_id"]),
                "video_uid": r["video_uid"],
                "platform": r["platform"],
                "url": r.get("url"),
                "channel": {
                    "id": r["channel_id"],
                    "title": r.get("channel_title"),
                    "platform": r["platform"],
                },
                "status": r["status"],
                "published_at": r.get("published_at"),
                "age_hours": round(_age_hours(now, published), 1),
                "manual_due_at": r["manual_due_at"],
                "last_captured_at": r.get("last_captured_at"),
                "last_views": r.get("last_views"),
                "collector_reason": reason,
            }
        )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def channel_snapshot_view(conn: Any, channel_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Последний `channel_snapshots` по каналам (для `Channel.stats` в M2-A/M4-B)."""
    return {
        r["channel_id"]: {
            "captured_at": r["captured_at"],
            "source": r["source"],
            "subscribers": r["subscribers"],
            "total_views": r["total_views"],
            "video_count": r["video_count"],
        }
        for r in repo.latest_channel_snapshots(conn, channel_ids)
    }
