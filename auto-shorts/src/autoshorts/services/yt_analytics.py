"""YouTube Analytics по подключённым каналам: досмотры и удержание наших роликов.

Нужно право `yt-analytics.readonly` у подключения канала (с 29.10.2026 «Подключить YouTube» его запрашивает,
старые подключения — переподключить) и включённый YouTube Analytics API в проекте Google Cloud клиента.
Analytics отдаёт данные с задержкой до 1–2 суток, поэтому канал опрашивается не чаще `MIN_GAP_H`, ролики — за
`WINDOW_DAYS` дней.

Пишет в публикацию `avg_pct_viewed` (средний % досмотра), `latest_engaged_views` (просмотры без мгновенного
пролистывания), `retention_3s` (доля зрителей на 3-й секунде); кривую удержания (100 точек) — в `retention_curves`.
По ним кино-тренды (`outcomes`) отличают «не показали» от «показали, но пролистали».

    from autoshorts.services import analytics
    analytics.collect()   # {"channels": [...], "skipped": [...]}
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from autoshorts.collectors import oauth_google

log = logging.getLogger(__name__)

REPORTS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
METRICS = ("views", "engagedViews", "averageViewDuration", "averageViewPercentage", "likes", "shares", "subscribersGained")
WINDOW_DAYS = 30
MIN_GAP_H = 3.0
CURVE_MIN_AGE_H = 24.0  # раньше кривая удержания пустая
CURVE_EVERY_H = 24.0
HOOK_DROP_AT = 0.6  # на 3-й секунде осталось меньше 60 % — крючок не держит

_LAST_RUN: dict[str, datetime] = {}  # канал → последний удачный опрос (в памяти процесса API: после рестарта опросит сразу)


class AnalyticsError(Exception):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def _query(client: httpx.Client, token: str, params: dict[str, str]) -> list[dict[str, Any]]:
    resp = client.get(REPORTS_URL, params={"ids": "channel==MINE", **params}, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code != 200:
        try:
            err = resp.json().get("error") or {}
        except ValueError:
            err = {}
        reasons = {str(e.get("reason")) for e in err.get("errors") or [] if isinstance(e, dict)}
        text = str(err.get("message") or resp.text[:200])
        if resp.status_code == 403 and ("accessNotConfigured" in reasons or "SERVICE_DISABLED" in text or "has not been used" in text):
            raise AnalyticsError("api_disabled", "в проекте Google Cloud не включён YouTube Analytics API")
        if resp.status_code in (401, 403) and ("insufficientPermissions" in reasons or "scope" in text.lower()):
            raise AnalyticsError("no_scope", "у подключения нет права на статистику: переподключить канал")
        raise AnalyticsError("http", f"YouTube Analytics {resp.status_code}: {text}")
    body = resp.json()
    cols = [h["name"] for h in body.get("columnHeaders") or []]
    return [dict(zip(cols, row, strict=False)) for row in body.get("rows") or []]


def video_stats(client: httpx.Client, token: str, video_ids: list[str], start: str, end: str) -> dict[str, dict[str, Any]]:
    """Сводка по роликам за период: {video_id: {views, engagedViews, averageViewPercentage, …}}."""
    out: dict[str, dict[str, Any]] = {}
    metrics = list(METRICS)
    for i in range(0, len(video_ids), 200):
        chunk = video_ids[i : i + 200]
        params = {"startDate": start, "endDate": end, "dimensions": "video", "filters": "video==" + ",".join(chunk), "maxResults": "200"}
        try:
            rows = _query(client, token, {**params, "metrics": ",".join(metrics)})
        except AnalyticsError as exc:
            if exc.reason != "http" or "engagedViews" not in metrics:
                raise
            metrics.remove("engagedViews")  # метрика появилась в 2025-м; где её нет — без неё
            rows = _query(client, token, {**params, "metrics": ",".join(metrics)})
        for r in rows:
            out[str(r.pop("video"))] = r
    return out


def retention_curve(client: httpx.Client, token: str, video_id: str, start: str, end: str) -> list[list[float | None]]:
    """Кривая удержания: [[доля ролика, доля зрителей, относительно похожих роликов], …] (до 100 точек)."""
    rows = _query(
        client,
        token,
        {
            "startDate": start,
            "endDate": end,
            "dimensions": "elapsedVideoTimeRatio",
            "metrics": "audienceWatchRatio,relativeRetentionPerformance",
            "filters": f"video=={video_id}",
            "sort": "elapsedVideoTimeRatio",
        },
    )
    return [
        [float(r["elapsedVideoTimeRatio"]), float(r["audienceWatchRatio"]),
         None if r.get("relativeRetentionPerformance") is None else float(r["relativeRetentionPerformance"])]
        for r in rows
    ]


def watch_ratio_at(points: list[list[float | None]], ratio: float) -> float | None:
    """Доля зрителей в момент `ratio` (линейно между соседними точками кривой)."""
    pts = [(float(p[0]), float(p[1])) for p in points if p[0] is not None and p[1] is not None]  # type: ignore[arg-type]
    if not pts:
        return None
    if ratio <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
        if x0 <= ratio <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (ratio - x0) / (x1 - x0)
    return pts[-1][1]


def failure_class(points: list[list[float | None]], retention_3s: float | None) -> str:
    if retention_3s is not None and retention_3s < HOOK_DROP_AT:
        return "hook_drop"
    mid = watch_ratio_at(points, 0.5)
    if mid is not None and mid < 0.35:
        return "middle_sag"
    end = watch_ratio_at(points, 1.0)
    if end is not None and end < 0.25:
        return "no_loop_lift"
    return "none"


def _channels(conn: Any) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT o.channel_id, o.scopes, o.refresh_token_secret, c.cluster
            FROM channel_oauth o JOIN channels c ON c.id = o.channel_id
            WHERE c.platform = 'youtube' AND c.status = 'active' AND o.refresh_token_secret IS NOT NULL
            ORDER BY o.channel_id
            """
        ).fetchall()
    ]


def _publications(conn: Any, channel_id: str, since: datetime) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT p.id, p.platform_video_id, p.published_at, j.duration_sec,
                   (SELECT max(rc.captured_at) FROM retention_curves rc WHERE rc.publication_id = p.id) AS curve_at
            FROM publications p LEFT JOIN jobs j ON j.id = p.job_id
            WHERE p.channel_id = %(ch)s AND p.platform = 'youtube' AND p.platform_video_id IS NOT NULL
              AND p.deleted_at IS NULL AND p.published_at >= %(since)s
            """,
            {"ch": channel_id, "since": since},
        ).fetchall()
    ]


def collect(*, client: httpx.Client | None = None, now: datetime | None = None, force: bool = False) -> dict[str, Any]:
    """Опросить все каналы с правом на статистику. Ошибка одного канала не мешает остальным."""
    from autoshorts.db.pool import connection
    from autoshorts.repos import clusters as clusters_repo
    from autoshorts.services import autopublish

    now = now or datetime.now(UTC)
    out: dict[str, Any] = {"channels": [], "skipped": []}
    with connection() as conn:
        channels = _channels(conn)
    http = client or httpx.Client(timeout=30.0)
    try:
        for ch in channels:
            cid = ch["channel_id"]
            if oauth_google.SCOPE_ANALYTICS not in list(ch.get("scopes") or []):
                out["skipped"].append({"channel_id": cid, "reason": "no_scope", "message": "нет права на статистику: переподключить канал"})
                continue
            last = _LAST_RUN.get(cid)
            if not force and last and now - last < timedelta(hours=MIN_GAP_H):
                out["skipped"].append({"channel_id": cid, "reason": "fresh", "message": f"опрошен {last:%d.%m %H:%M}"})
                continue
            with connection() as conn:
                pubs = _publications(conn, cid, now - timedelta(days=WINDOW_DAYS))
                creds = autopublish._client_creds(conn, clusters_repo.get(conn, ch["cluster"]))
                refresh_token = autopublish._secret(ch["refresh_token_secret"], conn, fresh=True)
            if not pubs:
                out["skipped"].append({"channel_id": cid, "reason": "no_publications", "message": "нет роликов за 30 дней"})
                continue
            if creds is None or not refresh_token:
                out["skipped"].append({"channel_id": cid, "reason": "no_oauth", "message": "нет OAuth-клиента или токена канала"})
                continue
            try:
                token = oauth_google.refresh(http, creds, refresh_token).access_token
                start = (min(p["published_at"] for p in pubs) - timedelta(days=1)).date().isoformat()
                end = now.date().isoformat()
                stats = video_stats(http, token, [str(p["platform_video_id"]) for p in pubs], start, end)
                curves = 0
                with connection() as conn:
                    for p in pubs:
                        s = stats.get(str(p["platform_video_id"]))
                        fields: dict[str, Any] = {}
                        if s:
                            if s.get("averageViewPercentage") is not None:
                                fields["avg_pct_viewed"] = round(float(s["averageViewPercentage"]), 2)
                            if s.get("engagedViews") is not None:
                                fields["latest_engaged_views"] = int(s["engagedViews"])
                        age_h = (now - p["published_at"]).total_seconds() / 3600
                        due = p["curve_at"] is None or now - p["curve_at"] >= timedelta(hours=CURVE_EVERY_H)
                        if s and age_h >= CURVE_MIN_AGE_H and due:
                            points = retention_curve(http, token, str(p["platform_video_id"]), start, end)
                            if points:
                                dur = float(p["duration_sec"] or 0)
                                r3 = watch_ratio_at(points, 3.0 / dur) if dur > 3 else None
                                conn.execute(
                                    "INSERT INTO retention_curves(publication_id, points, retention_3s, failure_class) VALUES (%(p)s, %(pts)s, %(r3)s, %(fc)s)",
                                    {"p": p["id"], "pts": json.dumps(points), "r3": None if r3 is None else round(min(r3, 9.9999), 4),
                                     "fc": failure_class(points, r3)},
                                )
                                if r3 is not None:
                                    fields["retention_3s"] = round(min(r3, 9.9999), 4)
                                curves += 1
                        if fields:
                            sets = ", ".join(f"{k} = %({k})s" for k in sorted(fields))
                            conn.execute(f"UPDATE publications SET {sets} WHERE id = %(_id)s", {**fields, "_id": p["id"]})  # noqa: S608 - ключи свои
                _LAST_RUN[cid] = now
                out["channels"].append({"channel_id": cid, "videos": len(stats), "curves": curves})
            except AnalyticsError as exc:
                out["skipped"].append({"channel_id": cid, "reason": exc.reason, "message": exc.message})
            except Exception as exc:  # noqa: BLE001 - канал с ошибкой не мешает остальным
                log.warning("analytics %s: %s", cid, exc)
                out["skipped"].append({"channel_id": cid, "reason": "error", "message": str(exc)[:200]})
    finally:
        if client is None:
            http.close()
    return out
