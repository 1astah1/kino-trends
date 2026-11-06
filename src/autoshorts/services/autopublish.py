"""Автозалив своих роликов на свои YouTube-каналы через Data API (решение владельца 2026-09-27, 0019).

Ручной путь не меняется: пакет → оператор заливает → вставляет ссылку. Автозалив включается двумя
способами:

* у канала `publishing_plan.mode = auto`: задача `autopublish_tick` раз в минуту ставит каждый новый
  пакет канала (`status = ready`, файл не скачан, «залил вручную» не нажато) в ближайший свободный
  слот расписания канала;
* у пакета кнопкой «Залить сейчас» или «Запланировать на …» (`POST /packages/{id}/autopublish`).

Созревший слот → задача `autopublish_upload`:
1. гейты: права, оригинальность, erid, Content ID, подключённый канал;
2. квота куста (`api_quota_usage`, youtube_data);
3. resumable upload: сессия и id ролика сохраняются сразу, повтор продолжает с места и не заливает
   дважды;
4. регистрация публикации тем же `services.publications.register`, что у ручной ссылки.

Канал подключается по OAuth (`/channels/{id}/oauth/youtube/start` → Google → `/oauth/youtube/callback`).
Refresh-токен лежит в `secrets` (Fernet) под именем `YT_OAUTH_<КАНАЛ>`, ссылка — `channel_oauth`.

Слоты: `publishing_plan.slots` — локальное время `HH:MM` в `publishing_plan.timezone` (по умолчанию
`business_timezone`); без слотов — `videos_per_day` (по умолчанию 3) равномерно с 09:00 до 21:00.
`videos_per_day` ограничивает и число роликов в сутки вместе с ручными публикациями канала.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from autoshorts.api.schemas.metrics import YT_QUOTA_TIMEZONE
from autoshorts.collectors import oauth_google
from autoshorts.collectors.http import CollectorError, ErrorKind, make_client
from autoshorts.core.errors import AppError, Conflict, NotFound, ValidationFailed
from autoshorts.domain.enums import AlertCode, AlertSeverity, AutopublishState, PublishMode, VideoPrivacy
from autoshorts.publishers import youtube as yt
from autoshorts.repos import channels as channels_repo
from autoshorts.repos import clusters as clusters_repo
from autoshorts.repos import packages as packages_repo
from autoshorts.repos import quota as quota_repo
from autoshorts.services import alerts as alerts_service
from autoshorts.services import audit
from autoshorts.services.audit import SYSTEM, Actor

log = logging.getLogger(__name__)

Row = dict[str, Any]

API = "youtube_data"
OAUTH_CLIENT_SECRET_DEFAULT = "YOUTUBE_OAUTH_CLIENT"
CHANNELS_LIST_UNITS = 1
CALLBACK_PATH = "/api/v1/oauth/youtube/callback"
DEFAULT_VIDEOS_PER_DAY = 3
DEFAULT_CATEGORY_ID = "22"  # People & Blogs; кино-каналу ставить "1" (Film & Animation) в publishing_plan
DAY_START, DAY_END = 9, 21  # окно равномерной раскладки без явных слотов, локальные часы
MIN_LEAD = timedelta(minutes=2)
SLOT_SEARCH_DAYS = 60
UPLOAD_TIMEOUT_SEC = 120.0
QUOTA_RESET_PAD = timedelta(minutes=5)
UPLOAD_LIMIT_DELAY = timedelta(hours=24)
AUTO_BATCH = 50
DUE_BATCH = 20
ACTIVE_CHANNEL_STATUSES = ("active", "warming")
LIVE = packages_repo.LIVE_STATUSES
SHORTS_MAX_SEC = 180.0


# ---------------------------------------------------------------- план канала


@dataclass(frozen=True)
class Plan:
    mode: str
    slots: tuple[dtime, ...]
    timezone: str
    privacy: str
    videos_per_day: int | None
    category_id: str = DEFAULT_CATEGORY_ID
    language: str | None = None
    # нарезок чужого видео (jobs.kind=clip) в сутки: канал, где их большинство, YouTube с 01.10.2026 считает
    # агрегатором и режет показы целиком (docs/SHORTS_RULES.md §2); остальные слоты — под своё
    clips_per_day: int | None = None


def _parse_slot(value: Any) -> dtime | None:
    try:
        hh, mm = str(value).strip().split(":")
        return dtime(int(hh), int(mm))
    except (ValueError, TypeError):
        return None


def even_slots(n: int) -> tuple[dtime, ...]:
    """n слотов равномерно с 09:00 до 21:00 (один — в полдень), с точностью до 5 минут."""
    n = max(1, int(n))
    if n == 1:
        return (dtime(12, 0),)
    span = (DAY_END - DAY_START) * 60
    out = []
    for i in range(n):
        minutes = DAY_START * 60 + round(span * i / (n - 1) / 5) * 5
        out.append(dtime(min(23, minutes // 60), minutes % 60))
    return tuple(sorted(set(out)))


def plan_of(channel: Row, default_tz: str) -> Plan:
    raw = channel.get("publishing_plan") if isinstance(channel.get("publishing_plan"), dict) else {}
    per_day = raw.get("videos_per_day")
    per_day = int(per_day) if isinstance(per_day, int) and per_day >= 0 else None
    slots = tuple(sorted({s for s in (_parse_slot(v) for v in raw.get("slots") or []) if s is not None}))
    if not slots:
        slots = even_slots(per_day or DEFAULT_VIDEOS_PER_DAY)
    tz = str(raw.get("timezone") or default_tz)
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        tz = default_tz
    mode = raw.get("mode") if raw.get("mode") in {m.value for m in PublishMode} else PublishMode.MANUAL.value
    privacy = raw.get("privacy") if raw.get("privacy") in {p.value for p in VideoPrivacy} else "public"
    category = str(raw.get("category_id") or "")
    language = raw.get("language") if isinstance(raw.get("language"), str) and raw.get("language") else None
    cpd = raw.get("clips_per_day")
    clips_per_day = int(cpd) if isinstance(cpd, int) and cpd >= 0 else None
    return Plan(
        mode=mode,
        slots=slots,
        timezone=tz,
        privacy=privacy,
        videos_per_day=per_day,
        category_id=category if category.isdigit() else DEFAULT_CATEGORY_ID,
        language=language,
        clips_per_day=clips_per_day,
    )


def _business_tz(conn: Any) -> str:
    from autoshorts.services import packages as packages_service

    return packages_service.business_tz(conn)


def _taken(conn: Any, channel_id: str, since: datetime) -> list[datetime]:
    """Занятые моменты канала: слоты автозалива и ручные публикации."""
    rows = conn.execute(
        """
        SELECT autopublish_at AS at FROM upload_packages
         WHERE channel_id = %(ch)s AND autopublish_at >= %(since)s
           AND autopublish_state IN ('scheduled','uploading','done')
        UNION ALL
        SELECT published_at FROM publications
         WHERE channel_id = %(ch)s AND published_at >= %(since)s AND deleted_at IS NULL
           AND NOT EXISTS (SELECT 1 FROM upload_packages up
                            WHERE up.id = publications.package_id AND up.autopublish_state = 'done')
        """,
        {"ch": channel_id, "since": since},
    ).fetchall()
    return [r["at"] for r in rows if r["at"] is not None]


def _is_clip_package(conn: Any, pkg: Row) -> bool:
    row = conn.execute("SELECT kind FROM jobs WHERE id = %(id)s", {"id": pkg.get("job_id")}).fetchone()
    return bool(row) and row["kind"] == "clip"


def _taken_clips(conn: Any, channel_id: str, since: datetime) -> list[datetime]:
    """Занятые канальные слоты нарезок (jobs.kind=clip): в плане автозалива и уже опубликованные."""
    rows = conn.execute(
        """
        SELECT up.autopublish_at AS at FROM upload_packages up JOIN jobs j ON j.id = up.job_id
         WHERE up.channel_id = %(ch)s AND up.autopublish_at >= %(since)s AND j.kind = 'clip'
           AND up.autopublish_state IN ('scheduled','uploading','done')
        UNION ALL
        SELECT p.published_at FROM publications p JOIN jobs j ON j.id = p.job_id
         WHERE p.channel_id = %(ch)s AND p.published_at >= %(since)s AND p.deleted_at IS NULL AND j.kind = 'clip'
           AND NOT EXISTS (SELECT 1 FROM upload_packages up
                            WHERE up.id = p.package_id AND up.autopublish_state = 'done')
        """,
        {"ch": channel_id, "since": since},
    ).fetchall()
    return [r["at"] for r in rows if r["at"] is not None]


def next_slot(
    plan: Plan, taken: list[datetime], *, now: datetime, taken_clips: list[datetime] | None = None, is_clip: bool = False
) -> datetime:
    """Ближайший свободный слот не раньше now + 2 мин; лимит `videos_per_day` на местные сутки, у нарезки
    (`is_clip`) ещё и `clips_per_day` по `taken_clips`."""
    tz = ZoneInfo(plan.timezone)
    local_today = now.astimezone(tz).date()
    per_day: dict[Any, int] = {}
    for t in taken:
        d = t.astimezone(tz).date()
        per_day[d] = per_day.get(d, 0) + 1
    clips_day: dict[Any, int] = {}
    for t in taken_clips or ():
        d = t.astimezone(tz).date()
        clips_day[d] = clips_day.get(d, 0) + 1
    for offset in range(SLOT_SEARCH_DAYS):
        day = local_today + timedelta(days=offset)
        if plan.videos_per_day is not None and per_day.get(day, 0) >= plan.videos_per_day:
            continue
        if is_clip and plan.clips_per_day is not None and clips_day.get(day, 0) >= plan.clips_per_day:
            continue
        for slot in plan.slots:
            cand = datetime.combine(day, slot, tzinfo=tz).astimezone(UTC)
            if cand < now + MIN_LEAD:
                continue
            if any(abs((cand - t).total_seconds()) < 60 for t in taken):
                continue
            return cand
    raise Conflict(
        "Нет свободного слота на 60 дней вперёд: увеличьте videos_per_day или добавьте слоты",
        code="autopublish_no_slot",
    )


# ---------------------------------------------------------------- OAuth канала


def _cipher() -> Any:
    from autoshorts.core.security import SecretCipher

    return SecretCipher.from_env()


def _secret(name: str | None, conn: Any, *, fresh: bool = False) -> str | None:
    from autoshorts.services.secrets import get_secret

    if not name:
        return None
    return get_secret(name, conn=conn, max_age=0 if fresh else 60.0)


def token_secret_name(channel_id: str) -> str:
    return ("YT_OAUTH_" + channel_id.upper().replace("-", "_"))[:64]


def redirect_uri() -> str:
    from autoshorts.core.config import get_env

    return get_env().admin_base_url.rstrip("/") + CALLBACK_PATH


def client_secret_name(cluster: Row | None) -> str:
    return (cluster or {}).get("youtube_oauth_client_secret") or OAUTH_CLIENT_SECRET_DEFAULT


def quota_bucket(cluster: Row | None) -> str:
    """Квота заливки — проекта Google, чей OAuth-клиент: свой у куста → id куста, общий → default."""
    if cluster and cluster.get("youtube_oauth_client_secret"):
        return str(cluster["id"])
    return "default"


def _client_creds(conn: Any, cluster: Row | None) -> oauth_google.ClientCreds | None:
    return oauth_google.parse_client(_secret(client_secret_name(cluster), conn))


def _youtube_channel(conn: Any, channel_id: str, *, for_update: bool = False) -> Row:
    channel = channels_repo.get(conn, channel_id, for_update=for_update)
    if channel is None:
        raise NotFound("channel", "Канал не найден")
    if channel["platform"] != "youtube":
        raise AppError(
            "Автозалив пока только для YouTube: VK Клипы идут вторым контуром",
            code="autopublish_platform_unsupported",
            status=422,
            details={"platform": channel["platform"]},
        )
    return channel


def oauth_row(conn: Any, channel_id: str) -> Row | None:
    return conn.execute(
        """
        SELECT channel_id, refresh_token_secret, scopes, granted_at, last_ok_at, last_error,
               google_channel_id, google_channel_title, granted_by
        FROM channel_oauth WHERE channel_id = %(ch)s
        """,
        {"ch": channel_id},
    ).fetchone()


def can_upload(oauth: Row | None) -> bool:
    return bool(oauth) and oauth_google.SCOPE_UPLOAD in list(oauth.get("scopes") or [])


def oauth_status(conn: Any, channel_id: str) -> dict[str, Any]:
    channel = channels_repo.get(conn, channel_id)
    if channel is None:
        raise NotFound("channel", "Канал не найден")
    cluster = clusters_repo.get(conn, channel["cluster"])
    oauth = oauth_row(conn, channel_id)
    plan = plan_of(channel, _business_tz(conn))
    return {
        "channel_id": channel_id,
        "platform": channel["platform"],
        "client_configured": _client_creds(conn, cluster) is not None,
        "client_secret_name": client_secret_name(cluster),
        "redirect_uri": redirect_uri(),
        "connected": oauth is not None,
        "can_upload": can_upload(oauth),
        "scopes": list((oauth or {}).get("scopes") or []),
        "granted_at": (oauth or {}).get("granted_at"),
        "last_ok_at": (oauth or {}).get("last_ok_at"),
        "last_error": (oauth or {}).get("last_error"),
        "google_channel_id": (oauth or {}).get("google_channel_id"),
        "google_channel_title": (oauth or {}).get("google_channel_title"),
        "mode": plan.mode,
        "slots": [s.strftime("%H:%M") for s in plan.slots],
        "timezone": plan.timezone,
        "privacy": plan.privacy,
        "videos_per_day": plan.videos_per_day,
        "category_id": plan.category_id,
        "language": plan.language,
    }


def start_oauth(conn: Any, channel_id: str, user_id: int | None) -> dict[str, Any]:
    channel = _youtube_channel(conn, channel_id)
    cluster = clusters_repo.get(conn, channel["cluster"])
    creds = _client_creds(conn, cluster)
    if creds is None:
        raise AppError(
            f"Нет OAuth-клиента Google: запишите в секрет {client_secret_name(cluster)} JSON клиента "
            "из Google Cloud Console (тип Web application)",
            code="oauth_client_missing",
            status=409,
            details={"secret_name": client_secret_name(cluster), "redirect_uri": redirect_uri()},
        )
    state = oauth_google.seal_state(_cipher(), channel_id=channel_id, user_id=user_id)
    return {
        "authorize_url": oauth_google.authorize_url(creds, redirect_uri=redirect_uri(), state=state),
        "redirect_uri": redirect_uri(),
    }


def _oauth_failed(message: str, code: str, **details: Any) -> AppError:
    return AppError(message, code=code, status=422, details=details)


def finish_oauth(
    code: str, state: str, actor: Actor, *, settings: Any, transport: Any = None
) -> dict[str, Any]:
    """Колбэк Google: код → токены → чей канал → секрет и `channel_oauth`. Возвращает `{channel_id, …}`."""
    from autoshorts.db.pool import connection
    from autoshorts.services.secrets import set_secret

    opened = oauth_google.open_state(_cipher(), state)
    if opened is None:
        raise AppError(
            "Ссылка подключения устарела или испорчена — начните заново",
            code="oauth_state_invalid",
            status=400,
        )
    if opened.user_id is not None and opened.user_id != actor.user_id:
        raise AppError("Подключение начал другой пользователь", code="oauth_state_foreign", status=403)
    with connection() as conn:
        channel = _youtube_channel(conn, opened.channel_id)
        cluster = clusters_repo.get(conn, channel["cluster"])
        creds = _client_creds(conn, cluster)
    if creds is None:
        raise AppError("OAuth-клиент Google пропал из секретов", code="oauth_client_missing", status=409)
    try:
        with make_client(proxy=settings.collectors_proxy_url, transport=transport) as client:
            tokens = oauth_google.exchange_code(client, creds, code=code, redirect_uri=redirect_uri())
            mine = oauth_google.my_channels(client, tokens.access_token)
    except CollectorError as exc:
        raise _oauth_failed(
            f"Google отказал: {exc.message}", "oauth_exchange_failed", reason=exc.reason
        ) from exc
    if not tokens.refresh_token:
        raise _oauth_failed(
            "Google не выдал refresh-токен. Уберите доступ приложения на myaccount.google.com/permissions "
            "и подключите канал ещё раз",
            "oauth_no_refresh_token",
        )
    if tokens.scopes and oauth_google.SCOPE_UPLOAD not in tokens.scopes:
        raise _oauth_failed(
            "На экране Google не отмечено право «Управлять видео на YouTube» — подключите ещё раз и отметьте его",
            "oauth_scope_missing",
            scopes=list(tokens.scopes),
        )
    ref = channel.get("platform_channel_ref")
    ids = [c["id"] for c in mine]
    if ref and ref not in ids:
        raise _oauth_failed(
            "Вход выполнен не в тот канал: выберите на экране Google канал этой карточки",
            "oauth_channel_mismatch",
            expected=ref,
            got=ids,
        )
    if not ids:
        raise _oauth_failed("У этого аккаунта Google нет YouTube-канала", "oauth_no_channel")
    picked = next((c for c in mine if c["id"] == ref), None) or (mine[0] if len(mine) == 1 else None)
    if picked is None:
        raise _oauth_failed(
            "Токен выдан на несколько каналов: укажите UC-id в карточке канала и подключите снова",
            "oauth_channel_ambiguous",
            got=ids,
        )
    secret_name = token_secret_name(opened.channel_id)
    set_secret(secret_name, tokens.refresh_token, actor)
    with connection() as conn:
        quota_repo.add(
            conn,
            api=API,
            cluster_id=quota_bucket(cluster),
            tz=YT_QUOTA_TIMEZONE,
            units=CHANNELS_LIST_UNITS,
            calls=1,
        )
        conn.execute(
            """
            INSERT INTO channel_oauth (channel_id, refresh_token_secret, scopes, granted_at, last_ok_at,
                                       last_error, google_channel_id, google_channel_title, granted_by)
            VALUES (%(ch)s, %(secret)s, %(scopes)s, now(), now(), NULL, %(gid)s, %(gtitle)s, %(by)s)
            ON CONFLICT (channel_id) DO UPDATE
              SET refresh_token_secret = EXCLUDED.refresh_token_secret, scopes = EXCLUDED.scopes,
                  granted_at = now(), last_ok_at = now(), last_error = NULL,
                  google_channel_id = EXCLUDED.google_channel_id,
                  google_channel_title = EXCLUDED.google_channel_title, granted_by = EXCLUDED.granted_by
            """,
            {
                "ch": opened.channel_id,
                "secret": secret_name,
                "scopes": list(tokens.scopes or oauth_google.SCOPES_UPLOAD),
                "gid": picked["id"],
                "gtitle": picked["title"] or None,
                "by": actor.user_id,
            },
        )
        if not ref:
            channels_repo.update(conn, opened.channel_id, {"platform_channel_ref": picked["id"]})
        audit.record(
            conn,
            actor,
            "channel.oauth_connect",
            "channel",
            opened.channel_id,
            summary=f"YouTube подключён: {picked['title'] or picked['id']}",
            diff={"google_channel_id": picked["id"], "scopes": list(tokens.scopes)},
        )
        alerts_service.resolve_alert(conn, f"autopublish:auth:{opened.channel_id}")
    return {"channel_id": opened.channel_id, "google_channel_id": picked["id"], "title": picked["title"]}


def disconnect(conn: Any, channel_id: str, actor: Actor) -> None:
    from autoshorts.repos import secrets as secrets_repo
    from autoshorts.services.secrets import invalidate_cache

    oauth = oauth_row(conn, channel_id)
    if oauth is None:
        raise NotFound("channel_oauth", "Канал не подключён")
    conn.execute("DELETE FROM channel_oauth WHERE channel_id = %(ch)s", {"ch": channel_id})
    secrets_repo.delete(conn, oauth["refresh_token_secret"])
    invalidate_cache(oauth["refresh_token_secret"])
    audit.record(conn, actor, "channel.oauth_disconnect", "channel", channel_id, summary="YouTube отключён")


# ---------------------------------------------------------------- гейты


def gate_problems(conn: Any, pkg: Row, *, oauth: Row | None = None) -> list[dict[str, str]]:
    """Что мешает залить пакет автоматически. Пустой список — можно."""
    from autoshorts.services import packages as packages_service

    problems: list[dict[str, str]] = []

    def add(code: str, message: str) -> None:
        problems.append({"code": code, "message": message})

    if pkg["platform"] != "youtube":
        add("platform_unsupported", "Автозалив пока только для YouTube")
    if pkg["status"] not in LIVE:
        add("package_not_live", "Пакет уже опубликован или заменён")
    if pkg.get("marked_uploaded_at") is not None:
        add("marked_uploaded", "Оператор отметил, что залил этот ролик вручную")
    if not can_upload(oauth if oauth is not None else oauth_row(conn, pkg["channel_id"])):
        add("channel_not_connected", "Канал не подключён к YouTube (карточка канала → «Подключить YouTube»)")
    for item in pkg.get("checklist") or []:
        if (
            isinstance(item, dict)
            and item.get("code") == "content_id"
            and item.get("required")
            and not item.get("done")
        ):
            add("content_id_unchecked", "Лицензионный источник: отметьте в чек-листе проверку Content ID")
    token = packages_service.required_erid_token(conn, pkg)
    if token is not None and token not in (pkg.get("description") or ""):
        add("erid_missing", "В описании нет строки erid, а ролик с маркировкой рекламы")
    if pkg["status"] in LIVE:
        try:
            packages_service.check_ready(conn, packages_service.gather(conn, int(pkg["job_id"])))
        except AppError as exc:
            reason = (exc.details or {}).get("reason") if isinstance(exc.details, dict) else None
            add(str(reason or exc.code), exc.message)
    return problems


# ---------------------------------------------------------------- очередь


def _require_pkg(conn: Any, package_id: int, *, for_update: bool = False) -> Row:
    pkg = packages_repo.get(conn, package_id, for_update=for_update)
    if pkg is None:
        raise NotFound("package", "Пакет не найден")
    return pkg


def schedule(
    conn: Any,
    package_id: int,
    actor: Actor,
    *,
    at: datetime | None = None,
    now_: bool = False,
    privacy: str | None = None,
    now: datetime | None = None,
) -> Row:
    """Поставить пакет в очередь автозалива: на время `at`, сейчас (`now_`) или в ближайший слот."""
    from autoshorts.services import packages as packages_service

    now = now or datetime.now(UTC)
    pkg = _require_pkg(conn, package_id, for_update=True)
    state = pkg.get("autopublish_state")
    if state == AutopublishState.UPLOADING.value:
        raise Conflict("Ролик уже заливается", code="autopublish_in_progress")
    if state == AutopublishState.DONE.value:
        raise Conflict("Ролик уже залит автоматически", code="autopublish_done")
    problems = gate_problems(conn, pkg)
    if problems:
        raise AppError(
            "Автозалив невозможен: " + "; ".join(p["message"] for p in problems),
            code="autopublish_blocked",
            status=409,
            details={"problems": problems, "package_id": package_id},
        )
    channel = _youtube_channel(conn, pkg["channel_id"])
    plan = plan_of(channel, _business_tz(conn))
    if privacy is not None and privacy not in {p.value for p in VideoPrivacy}:
        raise ValidationFailed("Неизвестная приватность", details={"privacy": privacy})
    if now_:
        when = now
    elif at is not None:
        when = max(at.astimezone(UTC), now)
    else:
        taken = _taken(conn, pkg["channel_id"], now - timedelta(days=2))
        own = pkg.get("autopublish_at")
        if own is not None and state == AutopublishState.SCHEDULED.value:
            taken = [t for t in taken if t != own]
        is_clip, taken_clips = False, None
        if plan.clips_per_day is not None:
            is_clip = _is_clip_package(conn, pkg)
            taken_clips = [t for t in _taken_clips(conn, pkg["channel_id"], now - timedelta(days=2)) if t != own]
        when = next_slot(plan, taken, now=now, taken_clips=taken_clips, is_clip=is_clip)
    after = packages_repo.update(
        conn,
        package_id,
        {
            "autopublish_state": AutopublishState.SCHEDULED.value,
            "autopublish_at": when,
            "autopublish_privacy": privacy or plan.privacy,
            "autopublish_attempts": 0,
            "autopublish_error": None,
            "autopublish_by": actor.user_id,
        },
        allowed_statuses=LIVE,
    )
    if after is None:
        raise Conflict("Пакет уже опубликован или заменён", code="package_not_ready")
    audit.record(
        conn,
        actor,
        "package.autopublish_schedule",
        "package",
        package_id,
        summary=f"автозалив {pkg['tracking_code']}: {when.isoformat(timespec='minutes')}",
        diff={"at": when.isoformat(), "privacy": privacy or plan.privacy, "now": now_},
    )
    alerts_service.resolve_alert(conn, f"autopublish:gate:{package_id}")
    if now_:
        enqueue_upload(conn, package_id)
    packages_service.publish_counts(conn)
    return after


def cancel(conn: Any, package_id: int, actor: Actor) -> Row:
    pkg = _require_pkg(conn, package_id, for_update=True)
    state = pkg.get("autopublish_state")
    if state == AutopublishState.UPLOADING.value:
        raise Conflict("Ролик уже заливается — отменить нельзя", code="autopublish_in_progress")
    if state not in (AutopublishState.SCHEDULED.value, AutopublishState.FAILED.value):
        raise Conflict("Пакет не стоит в очереди автозалива", code="autopublish_not_scheduled")
    after = packages_repo.update(
        conn, package_id, {"autopublish_state": AutopublishState.CANCELLED.value, "autopublish_session": None}
    )
    audit.record(
        conn,
        actor,
        "package.autopublish_cancel",
        "package",
        package_id,
        summary=f"автозалив {pkg['tracking_code']} отменён",
    )
    return after or pkg


def enqueue_upload(conn: Any, package_id: int) -> None:
    from autoshorts.services.tasks import enqueue

    enqueue(
        conn, "autopublish_upload", payload={"package_id": package_id}, dedup_key=f"autopublish:{package_id}"
    )


def _gate_alert(conn: Any, pkg: Row, problems: list[dict[str, str]]) -> None:
    alerts_service.open_alert(
        conn,
        code=AlertCode.AUTOPUBLISH_FAILED,
        severity=AlertSeverity.WARNING,
        dedup_key=f"autopublish:gate:{pkg['id']}",
        title=f"Автозалив {pkg['tracking_code']} ждёт: " + "; ".join(p["message"] for p in problems)[:300],
        details={"package_id": int(pkg["id"]), "channel_id": pkg["channel_id"], "problems": problems},
    )


def tick(*, now: datetime | None = None) -> dict[str, Any]:
    """Раз в минуту: новые пакеты auto-каналов → слоты; созревшие слоты → задачи заливки."""
    from autoshorts.db.pool import connection

    now = now or datetime.now(UTC)
    scheduled = waiting = queued = 0
    with connection() as conn:
        fresh = conn.execute(
            """
            SELECT p.id FROM upload_packages p JOIN channels c ON c.id = p.channel_id
             WHERE p.status = 'ready' AND p.autopublish_state IS NULL AND p.platform = 'youtube'
               AND p.downloaded_at IS NULL AND p.marked_uploaded_at IS NULL
               AND c.publishing_plan->>'mode' = 'auto' AND c.status = ANY(%(statuses)s)
             ORDER BY p.created_at, p.id LIMIT %(limit)s
            """,
            {"statuses": list(ACTIVE_CHANNEL_STATUSES), "limit": AUTO_BATCH},
        ).fetchall()
    for row in fresh:
        with connection() as conn:
            try:
                with conn.transaction():
                    schedule(conn, int(row["id"]), SYSTEM, now=now)
                scheduled += 1
            except AppError as exc:
                problems = (exc.details or {}).get("problems") if isinstance(exc.details, dict) else None
                pkg = packages_repo.get(conn, int(row["id"]))
                if pkg is not None:
                    _gate_alert(conn, pkg, problems or [{"code": exc.code, "message": exc.message}])
                waiting += 1
    with connection() as conn:
        revived = conn.execute(
            """
            UPDATE upload_packages p
               SET autopublish_state = 'scheduled', autopublish_at = %(now)s,
                   autopublish_error = 'Заливка прервалась — повтор с места обрыва', updated_at = now()
             WHERE p.autopublish_state = 'uploading' AND p.updated_at < %(now)s - interval '30 minutes'
               AND NOT EXISTS (SELECT 1 FROM tasks t
                                WHERE t.dedup_key = 'autopublish:' || p.id
                                  AND t.status IN ('queued','running'))
            RETURNING p.id
            """,
            {"now": now},
        ).fetchall()
        due = conn.execute(
            """
            SELECT id FROM upload_packages
             WHERE autopublish_state = 'scheduled' AND autopublish_at <= %(now)s
             ORDER BY autopublish_at, id LIMIT %(limit)s
             FOR UPDATE SKIP LOCKED
            """,
            {"now": now, "limit": DUE_BATCH},
        ).fetchall()
        for row in due:
            enqueue_upload(conn, int(row["id"]))
            queued += 1
    return {"scheduled": scheduled, "waiting": waiting, "queued": queued, "revived": len(revived)}


# ---------------------------------------------------------------- заливка


@dataclass
class UploadDeps:
    transport: Any = None
    sleep: Callable[[float], None] | None = None
    now: Callable[[], datetime] = lambda: datetime.now(UTC)


class Deferred(Exception):
    """Заливка отложена до `until` (квота, лимит канала)."""

    def __init__(self, until: datetime, reason: str) -> None:
        self.until = until
        self.reason = reason
        super().__init__(reason)


def _set(package_id: int, fields: dict[str, Any]) -> None:
    from autoshorts.db.pool import connection

    with connection() as conn:
        packages_repo.update(conn, package_id, fields)


def _fail(
    package_id: int, message: str, *, code: AlertCode = AlertCode.AUTOPUBLISH_FAILED, dedup: str | None = None
) -> None:
    from autoshorts.db.pool import connection

    with connection() as conn:
        pkg = packages_repo.update(
            conn,
            package_id,
            {"autopublish_state": AutopublishState.FAILED.value, "autopublish_error": message[:2000]},
        )
        alerts_service.open_alert(
            conn,
            code=code,
            severity=AlertSeverity.WARNING,
            dedup_key=dedup or f"autopublish:fail:{package_id}",
            title=f"Автозалив {(pkg or {}).get('tracking_code', package_id)}: {message}"[:300],
            details={"package_id": package_id, "channel_id": (pkg or {}).get("channel_id")},
        )


def _oauth_error(channel_id: str, message: str) -> None:
    from autoshorts.db.pool import connection

    with connection() as conn:
        conn.execute(
            "UPDATE channel_oauth SET last_error = %(e)s WHERE channel_id = %(ch)s",
            {"e": message[:2000], "ch": channel_id},
        )


@dataclass
class _Prepared:
    pkg: Row
    channel: Row
    cluster: Row | None
    creds: oauth_google.ClientCreds
    refresh_token: str
    path: Path
    size: int
    meta: yt.VideoMeta
    bucket: str


def _prepare(package_id: int, settings: Any, now: datetime) -> _Prepared | dict[str, Any]:
    from autoshorts.core.storage import get_layout
    from autoshorts.db.pool import connection
    from autoshorts.services import packages as packages_service

    with connection() as conn:
        pkg = packages_repo.get(conn, package_id, for_update=True)
        if pkg is None:
            return {"skipped": "not_found"}
        state = pkg.get("autopublish_state")
        if state not in (AutopublishState.SCHEDULED.value, AutopublishState.UPLOADING.value):
            return {"skipped": state or "not_scheduled"}
        if pkg.get("autopublish_at") and pkg["autopublish_at"] > now + timedelta(seconds=30):
            return {"skipped": "not_due"}
        if pkg["status"] not in LIVE and not pkg.get("autopublish_video_id"):
            packages_repo.update(
                conn,
                package_id,
                {
                    "autopublish_state": AutopublishState.CANCELLED.value,
                    "autopublish_error": "Пакет опубликован или заменён",
                },
            )
            return {"skipped": "package_not_live"}
        oauth = oauth_row(conn, pkg["channel_id"])
        if not pkg.get("autopublish_video_id"):
            problems = gate_problems(conn, pkg, oauth=oauth)
            if problems:
                packages_repo.update(
                    conn,
                    package_id,
                    {
                        "autopublish_state": AutopublishState.FAILED.value,
                        "autopublish_error": "; ".join(p["message"] for p in problems)[:2000],
                    },
                )
                _gate_alert(conn, pkg, problems)
                return {"failed": "gate", "problems": problems}
        channel = channels_repo.get(conn, pkg["channel_id"])
        cluster = clusters_repo.get(conn, channel["cluster"]) if channel else None
        creds = _client_creds(conn, cluster)
        refresh_token = _secret((oauth or {}).get("refresh_token_secret"), conn, fresh=True)
        if creds is None or not refresh_token:
            packages_repo.update(
                conn,
                package_id,
                {
                    "autopublish_state": AutopublishState.FAILED.value,
                    "autopublish_error": "Нет OAuth-клиента или токена канала",
                },
            )
            return {"failed": "oauth_missing"}
        rel = packages_service.video_relpath(pkg, conn)
        path = get_layout().resolve(rel)
        if not path.is_file():
            packages_repo.update(
                conn,
                package_id,
                {
                    "autopublish_state": AutopublishState.FAILED.value,
                    "autopublish_error": f"Нет файла ролика: {rel}",
                },
            )
            return {"failed": "no_file"}
        packages_repo.update(
            conn,
            package_id,
            {
                "autopublish_state": AutopublishState.UPLOADING.value,
                "autopublish_attempts": int(pkg.get("autopublish_attempts") or 0) + 1,
            },
        )
    plan = plan_of(channel, "UTC")  # пояс здесь не нужен, только категория и язык
    # хэштеги — в конец описания (чек-лист ручной заливки это делал руками), YouTube читает их оттуда
    description = str(pkg["description"] or "").rstrip()
    tags = [t for t in (pkg.get("hashtags") or ()) if isinstance(t, str) and t.startswith("#")]
    missing = [t for t in tags if t.lower() not in description.lower()]
    if missing:
        description = (description + "\n\n" + " ".join(missing))[:5000]
    meta = yt.VideoMeta(
        title=pkg["title"],
        description=description,
        tags=tuple(pkg.get("hashtags") or ()),
        privacy=pkg.get("autopublish_privacy") or "public",
        synthetic=bool(pkg.get("ai_disclosure_required")),
        category_id=plan.category_id,
        language=plan.language,
    )
    return _Prepared(
        pkg, channel, cluster, creds, refresh_token, path, path.stat().st_size, meta, quota_bucket(cluster)
    )


def _consume_upload_quota(prep: _Prepared, settings: Any) -> None:
    from autoshorts.db.pool import connection

    with connection() as conn:
        total = quota_repo.try_consume(
            conn,
            api=API,
            cluster_id=prep.bucket,
            units=int(settings.yt_upload_units),
            hard_limit=int(settings.yt_quota_hard_units),
            tz=YT_QUOTA_TIMEZONE,
        )
        if total is None:
            clk = quota_repo.clock(conn, YT_QUOTA_TIMEZONE)
            raise Deferred(clk["reset_at"] + QUOTA_RESET_PAD, "quota")


def _video_url(video_id: str, duration: Any) -> str:
    if duration is None or float(duration) <= SHORTS_MAX_SEC:
        return f"https://www.youtube.com/shorts/{video_id}"
    return f"https://www.youtube.com/watch?v={video_id}"


def _register(prep: _Prepared, video_id: str, video: Row | None, now: datetime) -> dict[str, Any]:
    from autoshorts.db.pool import connection
    from autoshorts.repos import publications as publications_repo
    from autoshorts.services import publications as publications_service
    from autoshorts.services.jobs import SYSTEM_CALLER

    package_id = int(prep.pkg["id"])
    url = _video_url(video_id, prep.pkg.get("duration_sec"))
    got_privacy = ((video or {}).get("status") or {}).get("privacyStatus") if video else None
    with connection() as conn:
        pub, _links = publications_service.register(conn, package_id, url, now, SYSTEM_CALLER)
        if got_privacy in ("private", "unlisted"):
            pub = publications_repo.update(conn, int(pub["id"]), {"status": got_privacy}) or pub
        packages_repo.update(
            conn,
            package_id,
            {
                "autopublish_state": AutopublishState.DONE.value,
                "autopublish_error": None,
                "autopublish_session": None,
            },
        )
        conn.execute(
            "UPDATE channel_oauth SET last_ok_at = now(), last_error = NULL WHERE channel_id = %(ch)s",
            {"ch": prep.pkg["channel_id"]},
        )
        alerts_service.resolve_alert(conn, f"autopublish:fail:{package_id}")
        wanted = prep.meta.privacy
        if got_privacy and got_privacy != wanted and wanted == "public":
            alerts_service.open_alert(
                conn,
                code=AlertCode.AUTOPUBLISH_PRIVATE_LOCK,
                severity=AlertSeverity.CRITICAL,
                dedup_key=f"autopublish:private_lock:{prep.bucket}",
                title="YouTube оставил залитый ролик приватным: проект Google без аудита API. "
                "Подайте форму YouTube API Services Audit",
                details={
                    "package_id": package_id,
                    "video_id": video_id,
                    "privacy": got_privacy,
                    "quota_bucket": prep.bucket,
                },
            )
    return {"publication_id": int(pub["id"]), "video_id": video_id, "url": url, "privacy": got_privacy}


def run_upload(
    package_id: int,
    *,
    settings: Any,
    last_attempt: bool,
    progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    deps: UploadDeps | None = None,
) -> dict[str, Any]:
    """Залить пакет и зарегистрировать публикацию. Сбой сети без `last_attempt` — `RetryableError`."""
    from autoshorts.core.errors import Cancelled, RetryableError

    deps = deps or UploadDeps()
    now = deps.now()
    prep = _prepare(package_id, settings, now)
    if isinstance(prep, dict):
        return prep
    pkg = prep.pkg
    sleep_kw = {"sleep": deps.sleep} if deps.sleep is not None else {}
    video_id = pkg.get("autopublish_video_id")
    video: Row | None = None
    try:
        if not video_id:
            with make_client(
                proxy=settings.collectors_proxy_url, transport=deps.transport, timeout=UPLOAD_TIMEOUT_SEC
            ) as client:
                try:
                    token = oauth_google.refresh(client, prep.creds, prep.refresh_token).access_token
                except CollectorError as exc:
                    if exc.kind == ErrorKind.AUTH:
                        raise yt.PublishError(
                            exc.message, kind=yt.PublishErrorKind.AUTH, reason=exc.reason
                        ) from exc
                    raise yt.PublishError(exc.message, kind=yt.PublishErrorKind.NETWORK) from exc
                session = pkg.get("autopublish_session")
                start = 0
                if session:
                    try:
                        start, video = yt.session_status(client, token, session, prep.size)
                    except yt.PublishError as exc:
                        if exc.kind != yt.PublishErrorKind.SESSION_EXPIRED:
                            raise
                        session, start = None, 0
                if video is None:
                    if not session:
                        _consume_upload_quota(prep, settings)
                        session = yt.start_session(client, token, prep.meta, prep.size, **sleep_kw)
                        _set(package_id, {"autopublish_session": session})
                    video = yt.upload(
                        client,
                        token,
                        session,
                        prep.path,
                        prep.size,
                        start=start,
                        progress=progress,
                        cancelled=cancelled,
                        **sleep_kw,
                    )
            video_id = str(video["id"])
            _set(package_id, {"autopublish_video_id": video_id, "autopublish_session": None})
    except Deferred as exc:
        _set(
            package_id,
            {
                "autopublish_state": AutopublishState.SCHEDULED.value,
                "autopublish_at": exc.until,
                "autopublish_error": "Квота YouTube на сутки исчерпана — перенесено на сброс",
            },
        )
        return {"deferred": exc.reason, "until": exc.until.isoformat()}
    except yt.PublishError as exc:
        if exc.reason == "cancelled":
            _set(package_id, {"autopublish_state": AutopublishState.SCHEDULED.value, "autopublish_at": now})
            raise Cancelled("автозалив прерван") from exc
        return _handle_error(
            prep,
            exc,
            now=now,
            last_attempt=last_attempt,
            retry=RetryableError,
            hard_units=int(settings.yt_quota_hard_units),
        )
    try:
        return _register(prep, video_id, video, now)
    except AppError as exc:
        _fail(
            package_id,
            f"Залит как {video_id}, но публикация не зарегистрирована: {exc.message}. "
            f"Вставьте ссылку {_video_url(video_id, pkg.get('duration_sec'))} вручную",
        )
        return {"failed": "register", "video_id": video_id, "error": exc.code}


def _handle_error(
    prep: _Prepared,
    exc: yt.PublishError,
    *,
    now: datetime,
    last_attempt: bool,
    retry: type[Exception],
    hard_units: int,
) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    package_id = int(prep.pkg["id"])
    kind = exc.kind
    if kind == yt.PublishErrorKind.QUOTA:
        with connection() as conn:
            quota_repo.exhaust(
                conn, api=API, cluster_id=prep.bucket, tz=YT_QUOTA_TIMEZONE, floor_units=hard_units
            )
            clk = quota_repo.clock(conn, YT_QUOTA_TIMEZONE)
        until = clk["reset_at"] + QUOTA_RESET_PAD
        _set(
            package_id,
            {
                "autopublish_state": AutopublishState.SCHEDULED.value,
                "autopublish_at": until,
                "autopublish_error": "YouTube: квота проекта исчерпана — перенесено на сброс",
            },
        )
        return {"deferred": "quota", "until": until.isoformat()}
    if kind == yt.PublishErrorKind.UPLOAD_LIMIT:
        until = now + UPLOAD_LIMIT_DELAY
        _set(
            package_id,
            {
                "autopublish_state": AutopublishState.SCHEDULED.value,
                "autopublish_at": until,
                "autopublish_session": None,
                "autopublish_error": "YouTube: канал упёрся в суточный лимит заливок — перенесено на сутки",
            },
        )
        return {"deferred": "upload_limit", "until": until.isoformat()}
    if kind == yt.PublishErrorKind.AUTH:
        _oauth_error(prep.pkg["channel_id"], exc.message)
        _fail(
            package_id,
            "Токен канала отозван или протух — подключите YouTube в карточке канала заново",
            code=AlertCode.AUTOPUBLISH_AUTH,
            dedup=f"autopublish:auth:{prep.pkg['channel_id']}",
        )
        return {"failed": "auth"}
    if kind == yt.PublishErrorKind.SESSION_EXPIRED:
        _set(package_id, {"autopublish_session": None})
    if exc.retryable and not last_attempt:
        _set(package_id, {"autopublish_error": exc.message})
        raise retry(exc.message)
    _fail(package_id, exc.message)
    return {"failed": kind.value, "reason": exc.reason}


# ---------------------------------------------------------------- представление


def view(row: Row) -> dict[str, Any] | None:
    """Блок `autopublish` пакета для API; None — пакет в автозалив не ставили."""
    state = row.get("autopublish_state")
    if state is None:
        return None
    vid = row.get("autopublish_video_id")
    return {
        "state": state,
        "at": row.get("autopublish_at"),
        "privacy": row.get("autopublish_privacy"),
        "attempts": int(row.get("autopublish_attempts") or 0),
        "error": row.get("autopublish_error"),
        "video_id": vid,
        "video_url": _video_url(vid, row.get("duration_sec")) if vid else None,
    }


def queue(
    conn: Any, *, channel_id: str | None = None, states: list[str] | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT p.id AS package_id, p.job_id, p.channel_id, c.title AS channel_title, p.title,
               p.autopublish_state, p.autopublish_at, p.autopublish_privacy, p.autopublish_attempts,
               p.autopublish_error, p.autopublish_video_id, j.video_uid, j.duration_sec, tl.code AS tracking_code
          FROM upload_packages p
          JOIN channels c ON c.id = p.channel_id
          JOIN jobs j ON j.id = p.job_id
          JOIN tracking_links tl ON tl.id = p.tracking_link_id
         WHERE p.autopublish_state IS NOT NULL
           AND (CAST(%(ch)s AS text) IS NULL OR p.channel_id = %(ch)s)
           AND (CAST(%(st)s AS text[]) IS NULL OR p.autopublish_state = ANY(CAST(%(st)s AS text[])))
         ORDER BY CASE p.autopublish_state WHEN 'uploading' THEN 0 WHEN 'failed' THEN 1
                                           WHEN 'scheduled' THEN 2 ELSE 3 END,
                  CASE WHEN p.autopublish_state IN ('scheduled','uploading') THEN p.autopublish_at END ASC,
                  p.autopublish_at DESC NULLS LAST, p.id DESC
         LIMIT %(limit)s
        """,
        {"ch": channel_id, "st": states or None, "limit": limit},
    ).fetchall()
    out = []
    for r in rows:
        vid = r["autopublish_video_id"]
        out.append(
            {
                "package_id": int(r["package_id"]),
                "job_id": int(r["job_id"]),
                "video_uid": str(r["video_uid"]),
                "tracking_code": r["tracking_code"],
                "channel_id": r["channel_id"],
                "channel_title": r["channel_title"],
                "title": r["title"],
                "state": r["autopublish_state"],
                "at": r["autopublish_at"],
                "privacy": r["autopublish_privacy"],
                "attempts": int(r["autopublish_attempts"] or 0),
                "error": r["autopublish_error"],
                "video_id": vid,
                "video_url": _video_url(vid, r["duration_sec"]) if vid else None,
            }
        )
    return out
