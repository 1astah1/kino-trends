"""Публикации: разбор ссылки, регистрация, реестр, проверка принадлежности (§5.2, §5.4, §6.5, §6.6).
Владелец — M3-A.

Публикация ручная: оператор заливает файл пакета сам и вставляет ссылку. Регистрация — одна
транзакция (`register`):

1. ссылка разбирается (`domain/url_parse.py`, 25+ форм YouTube и VK), площадка ссылки обязана
   совпасть с площадкой канала пакета — иначе 422 `platform_mismatch`;
2. тот же ролик площадки или второй ролик того же job на площадке → 409 `publication_exists`;
3. «на одной площадке один ролик идёт ровно в один канал» (§0.1 п. 15): если родня ролика (клоны и
   адаптации по `parent_job_id`, нарезка из того же кандидата) уже опубликована на этой площадке в
   ДРУГОМ канале → 409 `same_platform_crosspost_forbidden`;
4. мастер копируется в `masters/YYYY/MM/<video_uid>.mp4` (жёсткая ссылка или копия; уборка его не
   трогает), публикация вставляется с текстами пакета, алиас `yt_<id>` / `vk_<o>_<id>` → ссылка
   `level=publication`, пакетная ссылка получает `publication_id`, пакет и ролик → `published`.

Проверка принадлежности каналу (`verify`): VK — владелец клипа из ссылки против
`channels.platform_channel_ref` сразу при регистрации; YouTube — задача `publication_verify` (oEmbed:
автор ролика против `channels.url`), без совпадения — `channel_mismatch`, без данных — `unverifiable`.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from autoshorts.core.errors import AppError, Conflict, JobStateConflict, NotFound, RetryableError
from autoshorts.domain.url_parse import UnsupportedUrl, normalize_vk_owner, parse_video_url
from autoshorts.repos import packages as packages_repo
from autoshorts.repos import publications as publications_repo
from autoshorts.repos import tracking as tracking_repo
from autoshorts.services import audit
from autoshorts.services import tracking as tracking_service
from autoshorts.services.audit import Actor

log = logging.getLogger(__name__)

Row = dict[str, Any]

VERIFY_TASK_KIND = "publication_verify"
METRICS_TASK_KIND = "metrics_poll"
OEMBED_URL = "https://www.youtube.com/oembed"
OEMBED_TIMEOUT_SEC = 10.0
SHORTS_MAX_SEC = 180.0


# ---------------------------------------------------------------- разбор ссылки


def unsupported(exc: UnsupportedUrl) -> AppError:
    return AppError(str(exc), code="unsupported_url", status=422, details={"reason": exc.reason})


def parse_url(url: str) -> dict[str, Any]:
    try:
        return parse_video_url(url).to_dict()
    except UnsupportedUrl as exc:
        raise unsupported(exc) from exc


# ---------------------------------------------------------------- мастер-архив


def archive_master(final_rel: str, video_uid: str, when: datetime) -> str:
    """Копия финала в `masters/YYYY/MM/<uid>.mp4` (жёсткая ссылка, иначе копия через tmp + rename)."""
    from autoshorts.core.storage import get_layout

    media = get_layout()
    src = media.resolve(final_rel)
    if not src.is_file():
        raise Conflict(
            "Финальный файл ролика пропал с диска",
            code="package_not_ready",
            details={"reason": "final_file_missing", "path": final_rel},
        )
    dst = media.master_path(video_uid, when, create=True)
    if not dst.exists():
        try:
            os.link(src, dst)
        except OSError:
            tmp = dst.with_name(dst.name + ".part")
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
    return media.relpath(dst)


# ---------------------------------------------------------------- регистрация


def _job_row(conn: Any, job_id: int) -> Row:
    from autoshorts.repos import jobs as jobs_repo

    job = jobs_repo.get(conn, job_id, for_update=True)
    if job is None:
        raise NotFound("job", "Ролик не найден")
    return job


def _crosspost_conflict(conn: Any, job: Row, platform: str, channel_id: str) -> Row | None:
    family = set(publications_repo.job_family(conn, int(job["id"])))
    family.update(publications_repo.same_candidate_jobs(conn, int(job["id"])))
    family.discard(int(job["id"]))
    for pub in publications_repo.live_for_jobs_on_platform(conn, sorted(family), platform):
        if pub["channel_id"] != channel_id:
            return pub
    return None


def _first_poll(conn: Any, published_at: datetime) -> datetime:
    from autoshorts.services.settings import get_runtime_settings

    try:
        ladder = get_runtime_settings(conn).metrics_poll_ladder_hours
        first = int(ladder[0]) if ladder else 1
    except Exception:  # noqa: BLE001
        first = 1
    return published_at + timedelta(hours=first)


def register(
    conn: Any, package_id: int, url: str, published_at: datetime | None, caller: Any
) -> tuple[Row, list[Row]]:
    """Регистрация публикации по ссылке. Возвращает (публикация, [пакетная ссылка, алиас])."""
    from autoshorts.repos import jobs as jobs_repo
    from autoshorts.services import jobs as jobs_service
    from autoshorts.services import packages as packages_service

    pkg = packages_repo.get(conn, package_id, for_update=True)
    if pkg is None:
        raise NotFound("package", "Пакет не найден")
    try:
        parsed = parse_video_url(url)
    except UnsupportedUrl as exc:
        raise unsupported(exc) from exc
    if parsed.platform != pkg["platform"]:
        raise AppError(
            f"Ссылка на {parsed.platform}, а канал пакета — {pkg['platform']}",
            code="platform_mismatch",
            status=422,
            details={
                "url_platform": parsed.platform,
                "channel_platform": pkg["platform"],
                "channel_id": pkg["channel_id"],
            },
        )
    existing = publications_repo.find_by_video(conn, parsed.platform, parsed.platform_video_id)
    if existing is not None:
        raise Conflict(
            "Этот ролик площадки уже зарегистрирован",
            code="publication_exists",
            details={"publication_id": int(existing["id"]), "job_id": int(existing["job_id"])},
        )
    if pkg["status"] == "superseded":
        raise Conflict(
            "Пакет заменён более новым — регистрируйте по текущему пакету",
            code="package_not_ready",
            details={"reason": "package_superseded", "package_id": package_id},
        )
    job = _job_row(conn, int(pkg["job_id"]))
    same_job = publications_repo.live_for_jobs_on_platform(conn, [int(job["id"])], parsed.platform)
    if same_job or job["status"] == "published":
        raise Conflict(
            "Ролик уже зарегистрирован на этой площадке",
            code="publication_exists",
            details={
                "publication_id": int(same_job[0]["id"]) if same_job else None,
                "job_id": int(job["id"]),
            },
        )
    if job["status"] != "approved":
        raise JobStateConflict(
            "Зарегистрировать публикацию можно только одобренному ролику",
            details={"job_id": int(job["id"]), "status": job["status"]},
        )
    other = _crosspost_conflict(conn, job, parsed.platform, pkg["channel_id"])
    if other is not None:
        raise Conflict(
            "На одной площадке ролик идёт только в один канал: он уже опубликован в другом канале",
            code="same_platform_crosspost_forbidden",
            details={
                "publication_id": int(other["id"]),
                "job_id": int(other["job_id"]),
                "channel_id": other["channel_id"],
                "platform": parsed.platform,
            },
        )
    when = published_at or datetime.now(UTC)
    master_rel = archive_master(pkg["final_path"], str(pkg["video_uid"]), when)
    kind = parsed.platform_kind
    if parsed.platform == "youtube" and kind == "video":
        dur = job.get("duration_sec")
        if dur is None or float(dur) <= SHORTS_MAX_SEC:
            kind = "short"  # вертикаль ≤ 3 мин на YouTube — это Short, как бы ни выглядела ссылка
    canonical = parsed.canonical_url
    if parsed.platform == "youtube" and kind == "short":
        canonical = f"https://www.youtube.com/shorts/{parsed.platform_video_id}"
    product_id = str(
        job.get("product_id") or pkg.get("link_product_id") or tracking_service.default_product_id(conn)
    )
    pub = publications_repo.insert(
        conn,
        {
            "job_id": int(job["id"]),
            "channel_id": pkg["channel_id"],
            "platform": parsed.platform,
            "platform_video_id": parsed.platform_video_id,
            "platform_kind": kind,
            "url": url.strip(),
            "canonical_url": canonical,
            "master_file_path": master_rel,
            "title": pkg["title"],
            "description": pkg["description"],
            "hashtags": list(pkg.get("hashtags") or []),
            "published_at": when,
            "attribution_tag": parsed.alias_code,
            "status": "live",
            "verify_status": "pending",
            "product_id": product_id,
            "campaign_id": job.get("campaign_id"),
            "banner_variant_id": job.get("banner_variant_id"),
            "registered_by": caller.user_id,
            "package_id": package_id,
            "next_poll_at": _first_poll(conn, when),
        },
    )
    alias_link = tracking_service.ensure_alias_link(
        conn, parsed.alias_code, publication=pub, product_id=product_id
    )
    package_link = tracking_repo.update(
        conn, int(pkg["tracking_link_id"]), {"publication_id": int(pub["id"]), "platform": parsed.platform}
    )
    items = packages_service.tick_checklist(pkg.get("checklist"), "published", caller.user_id)
    packages_repo.update(conn, package_id, {"status": "published", "checklist": items})
    after = jobs_repo.update(conn, int(job["id"]), {"status": "published"}, allowed_statuses=("approved",))
    if after is None:
        raise JobStateConflict(details={"job_id": int(job["id"])})
    jobs_service.audit_job(
        conn,
        caller,
        "job.publish",
        after,
        before=job,
        summary=f"{after['video_uid']}: {parsed.platform} {parsed.platform_video_id}",
        diff={
            "publication_id": int(pub["id"]),
            "platform": parsed.platform,
            "platform_video_id": parsed.platform_video_id,
        },
    )
    jobs_service.after_transition(conn, job, after)
    packages_service.publish_counts(conn)
    if parsed.platform == "vk":
        pub = apply_verification(conn, pub, *verify_vk(pub, _channel(conn, pub["channel_id"])))
    else:
        enqueue_verify(conn, int(pub["id"]))
    links = [row for row in (package_link, alias_link) if row is not None]
    return pub, links


# ---------------------------------------------------------------- проверка принадлежности


def _channel(conn: Any, channel_id: str) -> Row:
    return conn.execute(
        "SELECT id, platform, title, url, platform_channel_ref, profile FROM channels WHERE id = %(id)s",
        {"id": channel_id},
    ).fetchone() or {"id": channel_id}


def verify_vk(pub: Row, channel: Row) -> tuple[str, str]:
    owner = str(pub.get("platform_video_id") or "").split("_")[0]
    expected = normalize_vk_owner(channel.get("platform_channel_ref")) or normalize_vk_owner(
        channel.get("url")
    )
    if not expected:
        return "unverifiable", f"У канала не указан ID сообщества VK; владелец ролика {owner}"
    if owner == expected:
        return "ok", f"владелец {owner} совпадает с каналом"
    return "channel_mismatch", f"владелец ролика {owner}, а канал — {expected}"


def _yt_path(url: str | None) -> str | None:
    if not url:
        return None
    try:
        path = urlsplit(url if "://" in url else "https://" + url).path.rstrip("/").lower()
    except ValueError:
        return None
    return path or None


def verify_youtube(pub: Row, channel: Row, *, client: Any = None) -> tuple[str, str]:
    """oEmbed YouTube (без ключа API): автор ролика против `channels.url`."""
    import httpx

    video_url = f"https://www.youtube.com/watch?v={pub['platform_video_id']}"
    own = client is None
    if own:
        from autoshorts.services.settings import get_runtime_settings

        proxy = None
        try:
            proxy = get_runtime_settings().collectors_proxy_url
        except Exception:  # noqa: BLE001
            proxy = None
        client = httpx.Client(timeout=OEMBED_TIMEOUT_SEC, proxy=proxy, follow_redirects=True)
    try:
        resp = client.get(OEMBED_URL, params={"url": video_url, "format": "json"})
    except httpx.HTTPError as exc:
        raise RetryableError(f"oEmbed: {type(exc).__name__}") from exc
    finally:
        if own:
            client.close()
    if resp.status_code == 404:
        return "not_found", "YouTube не нашёл ролик (удалён, приватный или ещё обрабатывается)"
    if resp.status_code in (401, 403):
        return "unverifiable", f"oEmbed HTTP {resp.status_code}: ролик скрыт или встраивание запрещено"
    if resp.status_code == 429 or resp.status_code >= 500:
        raise RetryableError(f"oEmbed HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        return "unverifiable", "oEmbed: ответ не JSON"
    author_url = str(data.get("author_url") or "")
    author = str(data.get("author_name") or "")
    got = _yt_path(author_url)
    expected = _yt_path(channel.get("url"))
    handle = ((channel.get("profile") or {}) if isinstance(channel.get("profile"), dict) else {}).get(
        "handle"
    )
    candidates = {p for p in (expected, f"/{handle.lower().lstrip('/')}" if handle else None) if p}
    ref = str(channel.get("platform_channel_ref") or "")
    if ref.startswith("UC"):
        candidates.add(f"/channel/{ref.lower()}")
    if got and got in candidates:
        return "ok", f"автор {author} ({author_url})"
    if got and expected and (expected.startswith("/@") == got.startswith("/@")):
        return "channel_mismatch", f"автор ролика {author} ({author_url}), а канал — {channel.get('url')}"
    return "unverifiable", f"автор ролика {author} ({author_url}); у канала нет сопоставимой ссылки"


def apply_verification(conn: Any, pub: Row, status: str, detail: str) -> Row:
    return publications_repo.update(
        conn,
        int(pub["id"]),
        {"verify_status": status, "verify_detail": detail[:500], "verified_at": datetime.now(UTC)},
    )


def enqueue_verify(conn: Any, publication_id: int) -> int:
    from autoshorts.services.tasks import enqueue

    res = enqueue(
        conn,
        VERIFY_TASK_KIND,
        resource_class="light",
        max_attempts=3,
        priority=80,
        payload={"publication_id": publication_id},
        dedup_key=f"pub:{publication_id}:verify",
    )
    return int(res.task["id"])


def verify_task(publication_id: int) -> dict[str, Any]:
    """Обработчик `publication_verify`: сеть — вне транзакции, запись — короткой транзакцией."""
    from autoshorts.db.pool import connection

    with connection() as conn:
        pub = publications_repo.get(conn, publication_id)
        if pub is None:
            return {"publication_id": publication_id, "skipped": "not_found"}
        channel = _channel(conn, pub["channel_id"])
    status, detail = verify_vk(pub, channel) if pub["platform"] == "vk" else verify_youtube(pub, channel)
    with connection() as conn:
        apply_verification(conn, pub, status, detail)
    return {"publication_id": publication_id, "verify_status": status}


# ---------------------------------------------------------------- ответ API


def _num(v: Any) -> int:
    return int(v or 0)


def _f(v: Any) -> float | None:
    return float(v) if v is not None else None


def traffic_totals(conn: Any, publication_id: int) -> dict[str, Any]:
    from autoshorts.repos import clicks as clicks_repo
    from autoshorts.repos import conversions as conversions_repo

    ct = clicks_repo.totals(conn, publication_id=publication_id)
    vt = conversions_repo.totals(conn, publication_id=publication_id)
    return {
        "landing_views": _num(ct["landing_views"]),
        "unique_visitors": _num(ct["unique_visitors"]),
        "to_telegram": _num(ct["to_telegram"]),
        "to_site": _num(ct["to_site"]),
        "bot_starts": _num(vt["bot_starts"]),
        "signups": _num(vt["signups"]),
        "first_payments": _num(vt["first_payments"]),
        "revenue_minor": _num(vt["revenue_minor"]),
        "currency": (vt["currency"] or "").strip() or None,
        "_direct_bot_starts": _num(vt["direct_bot_starts"]),
    }


def publication_view(conn: Any, row: Row) -> dict[str, Any]:
    pid = int(row["id"])
    traffic = traffic_totals(conn, pid)
    direct = traffic.pop("_direct_bot_starts")
    visitors = traffic["unique_visitors"] + direct  # §6.6: без двойного счёта стартов с лендинга
    views = row.get("latest_engaged_views") or row.get("latest_views")
    job_cost = float(row.get("job_cost_usd") or 0)
    variant = None
    if row.get("banner_variant_id") is not None and row.get("variant_code") is not None:
        variant = {
            "id": int(row["banner_variant_id"]),
            "code": row["variant_code"],
            "name": row["variant_name"],
        }
    verdict = (
        "d7"
        if row.get("views_7d") is not None
        else "h72"
        if row.get("views_72h") is not None
        else ("h24" if row.get("views_24h") is not None else None)
    )
    return {
        "id": pid,
        "job_id": int(row["job_id"]),
        "package_id": row.get("package_id"),
        "video_uid": row["video_uid"],
        "kind": row["job_kind"],
        "platform": row["platform"],
        "platform_video_id": row.get("platform_video_id"),
        "platform_kind": row.get("platform_kind"),
        "url": row.get("url"),
        "canonical_url": row.get("canonical_url"),
        "status": row["status"],
        "status_reason": row.get("status_reason"),
        "verify_status": row["verify_status"],
        "verify_detail": row.get("verify_detail"),
        "verified_at": row.get("verified_at"),
        "has_claim": bool(row.get("has_claim")),
        "channel": {"id": row["channel_id"], "title": row.get("channel_title"), "platform": row["platform"]},
        "niche_id": row.get("niche_id"),
        "product_id": row.get("product_id"),
        "campaign_id": row.get("campaign_id"),
        "banner_variant": variant,
        "tracking": {"codes": [link["code"] for link in tracking_repo.links_for_publication(conn, pid)]},
        "latest": {
            "captured_at": row.get("last_polled_at"),
            "views": row.get("latest_views"),
            "likes": row.get("latest_likes"),
            "comments": row.get("latest_comments"),
            "engaged_views": row.get("latest_engaged_views"),
            "retention_3s": _f(row.get("retention_3s")),
            "avg_pct_viewed": _f(row.get("avg_pct_viewed")),
        },
        "age_views": {"h24": row.get("views_24h"), "h72": row.get("views_72h"), "d7": row.get("views_7d")},
        "verdict": verdict,
        "traffic": traffic,
        "unit_economics": {
            "job_cost_usd": job_cost,
            "ctr_per_1k_views": round(visitors / views * 1000, 4) if views else None,
            "cost_per_visitor_usd": round(job_cost / visitors, 6) if visitors else None,
        },
        "originality_verdict": row.get("originality_verdict") if row.get("job_kind") == "clip" else None,
        "next_poll_at": row.get("next_poll_at"),
        "published_at": row.get("published_at"),
        "created_at": row["created_at"],
    }


def remove_remote(conn: Any, publication_id: int, caller: Any, *, client: Any = None) -> dict[str, Any]:
    """Снять ролик с YouTube: `videos.delete` токеном канала → статус `deleted_by_us`.
    Нужен scope youtube.force-ssl у подключения канала; иначе 409 `oauth_scope_missing`
    с подсказкой переподключить канал."""
    import httpx

    from autoshorts.collectors import oauth_google
    from autoshorts.collectors.http import CollectorError
    from autoshorts.publishers import youtube as yt
    from autoshorts.repos import clusters as clusters_repo
    from autoshorts.services import autopublish

    pub = require(conn, publication_id, for_update=True)
    if pub["platform"] != "youtube":
        raise AppError("Снять с площадки можно только ролик YouTube", code="platform_unsupported", status=422)
    if pub["status"] == "deleted_by_us":
        return publication_view(conn, pub)
    vid = pub.get("platform_video_id")
    if not vid:
        raise Conflict("У публикации нет id ролика на площадке", code="publication_no_video_id")
    from autoshorts.repos import channels as channels_repo

    channel = channels_repo.get(conn, pub["channel_id"])  # полная строка: нужен кластер для OAuth-клиента
    if channel is None:
        raise NotFound("channel", "Канал не найден")
    oauth = autopublish.oauth_row(conn, channel["id"])
    scopes = list((oauth or {}).get("scopes") or [])
    if not oauth or oauth_google.SCOPE_MANAGE not in scopes:
        raise Conflict(
            "Подключение YouTube без права удалять ролики: переподключите канал "
            "(кнопка «Подключить YouTube»), новое подключение запросит это право",
            code="oauth_scope_missing",
            details={"channel_id": channel["id"], "scopes": scopes, "required": oauth_google.SCOPE_MANAGE},
        )
    cluster = clusters_repo.get(conn, channel["cluster"])
    creds = autopublish._client_creds(conn, cluster)
    refresh_token = autopublish._secret(oauth.get("refresh_token_secret"), conn, fresh=True)
    if creds is None or not refresh_token:
        raise Conflict("Нет OAuth-клиента или токена канала", code="oauth_missing")
    http = client or httpx.Client(timeout=30.0)
    try:
        token = oauth_google.refresh(http, creds, refresh_token).access_token
        yt.delete_video(http, token, str(vid))
    except yt.PublishError as exc:
        raise AppError(
            f"YouTube не удалил ролик: {exc.message}",
            code="remote_delete_failed",
            status=502,
            details={"kind": exc.kind.value, "status": exc.status, "reason": exc.reason},
        ) from exc
    except CollectorError as exc:
        raise AppError(f"YouTube не ответил: {exc}", code="remote_delete_failed", status=502) from exc
    finally:
        if client is None:
            http.close()
    fields = {"status": "deleted_by_us", "status_reason": "снят оператором через API YouTube"}
    row = publications_repo.update(conn, publication_id, fields)
    actor = getattr(caller, "actor", caller)
    summary = f"Ролик {vid} удалён с YouTube"
    audit.record(conn, actor, "publication.remove_remote", "publication", publication_id, summary=summary)
    return publication_view(conn, row or pub)


def require(conn: Any, publication_id: int, *, for_update: bool = False) -> Row:
    row = publications_repo.get(conn, publication_id, for_update=for_update)
    if row is None:
        raise NotFound("publication", "Публикация не найдена")
    return row


def list_publications(conn: Any, **filters: Any) -> dict[str, Any]:
    from autoshorts.services.settings import business_range, get_runtime_settings

    tz = get_runtime_settings(conn).business_timezone
    since, until = business_range(filters.pop("date_from"), filters.pop("date_to"), tz)
    rows, total = publications_repo.list_page(conn, since=since, until=until, **filters)
    return {
        "items": [publication_view(conn, r) for r in rows],
        "total": total,
        "limit": filters["limit"],
        "offset": filters["offset"],
    }


def patch(conn: Any, publication_id: int, changes: dict[str, Any], actor: Actor) -> Row:
    before = require(conn, publication_id, for_update=True)
    fields = {
        k: v for k, v in changes.items() if k in ("status", "status_reason", "has_claim", "published_at")
    }
    for key in ("status", "has_claim", "published_at"):
        if key in fields and fields[key] is None:
            fields.pop(key)
    if "status" in fields:
        fields["status"] = str(fields["status"])
    after = publications_repo.update(conn, publication_id, fields)
    diff = {k: {"from": str(before.get(k)), "to": str(v)} for k, v in fields.items() if before.get(k) != v}
    if diff:
        audit.record(
            conn,
            actor,
            "publication.update",
            "publication",
            publication_id,
            summary=f"публикация {publication_id}",
            diff=diff,
        )
    return after


def delete(conn: Any, publication_id: int, caller: Any) -> None:
    """Мягкое удаление ошибочной регистрации: ролик → approved, пакет → ready, алиас выключен."""
    from autoshorts.repos import jobs as jobs_repo
    from autoshorts.services import jobs as jobs_service
    from autoshorts.services import packages as packages_service

    pub = require(conn, publication_id, for_update=True)
    publications_repo.update(conn, publication_id, {"deleted_at": datetime.now(UTC), "attribution_tag": None})
    tracking_repo.detach_publication(conn, publication_id)
    job = jobs_repo.get(conn, int(pub["job_id"]), for_update=True)
    if job is not None and job["status"] == "published":
        if not publications_repo.live_for_job(conn, int(job["id"])):
            after = jobs_repo.update(
                conn, int(job["id"]), {"status": "approved"}, allowed_statuses=("published",)
            )
            if after is not None:
                jobs_service.audit_job(
                    conn, caller, "job.unpublish", after, before=job, diff={"publication_id": publication_id}
                )
                jobs_service.after_transition(conn, job, after)
    if pub.get("package_id") is not None:
        pkg = packages_repo.get(conn, int(pub["package_id"]), for_update=True)
        if (
            pkg is not None
            and pkg["status"] == "published"
            and packages_repo.live_for_job(conn, int(pkg["job_id"])) is None
        ):
            items = [dict(i) for i in (pkg.get("checklist") or []) if isinstance(i, dict)]
            for i in items:
                if i.get("code") == "published":
                    i.update({"done": False, "done_at": None, "done_by": None})
            packages_repo.update(conn, int(pkg["id"]), {"status": "ready", "checklist": items})
    audit.record(
        conn,
        caller.actor if hasattr(caller, "actor") else caller,
        "publication.delete",
        "publication",
        publication_id,
        summary=f"ошибочная регистрация {pub['platform']} {pub.get('platform_video_id')} удалена",
        diff={"job_id": int(pub["job_id"]), "url": pub.get("url")},
    )
    packages_service.publish_counts(conn)


def refresh_metrics(conn: Any, publication_id: int) -> int:
    from autoshorts.services.tasks import enqueue

    require(conn, publication_id)
    publications_repo.update(conn, publication_id, {"next_poll_at": datetime.now(UTC)})
    res = enqueue(
        conn,
        METRICS_TASK_KIND,
        payload={"publication_id": publication_id, "manual": True},
        dedup_key=f"pub:{publication_id}:metrics",
    )
    return int(res.task["id"])


def metrics(conn: Any, publication_id: int, granularity: str) -> dict[str, Any]:
    from autoshorts.services.settings import get_runtime_settings

    pub = require(conn, publication_id)
    tz = get_runtime_settings(conn).business_timezone
    rows = (
        publications_repo.metrics_daily(conn, publication_id, tz)
        if granularity == "day"
        else publications_repo.metrics(conn, publication_id)
    )
    published = pub.get("published_at") or pub["created_at"]
    points = []
    for r in rows:
        age = r.get("age_hours")
        if age is None:
            age = max(0.0, (r["captured_at"] - published).total_seconds() / 3600.0)
        points.append(
            {
                "t": r["captured_at"],
                "age_hours": round(float(age), 3),
                "views": r.get("views"),
                "likes": r.get("likes"),
                "comments": r.get("comments"),
                "engaged_views": r.get("engaged_views"),
                "source": r.get("source") or "yt_data",
            }
        )
    return {"publication_id": publication_id, "granularity": granularity, "points": points}


def retention(conn: Any, publication_id: int) -> dict[str, Any]:
    pub = require(conn, publication_id)
    row = publications_repo.latest_retention(conn, publication_id)
    duration = _f(pub.get("duration_sec"))
    if row is None:
        return {
            "captured_at": None,
            "duration_sec": duration,
            "retention_3s": None,
            "failure_class": None,
            "points": [],
        }
    points = []
    for p in row.get("points") or []:
        if isinstance(p, list | tuple) and len(p) >= 2:
            points.append(
                {
                    "ratio": float(p[0]),
                    "audience_watch_ratio": float(p[1]),
                    "relative": float(p[2]) if len(p) > 2 and p[2] is not None else None,
                }
            )
    return {
        "captured_at": row["captured_at"],
        "duration_sec": duration,
        "retention_3s": _f(row.get("retention_3s")),
        "failure_class": row.get("failure_class"),
        "points": points,
    }


def traffic(conn: Any, publication_id: int, granularity: str) -> dict[str, Any]:
    from autoshorts.services.settings import get_runtime_settings

    require(conn, publication_id)
    tz = get_runtime_settings(conn).business_timezone
    return {
        "publication_id": publication_id,
        "granularity": granularity,
        "points": tracking_service.traffic_series(
            conn, granularity=granularity, tz=tz, publication_id=publication_id
        ),
    }


def master_path(row: Row) -> Path:
    from autoshorts.core.storage import get_layout

    return get_layout().resolve(row["master_file_path"])


__all__ = [
    "apply_verification",
    "archive_master",
    "delete",
    "enqueue_verify",
    "list_publications",
    "metrics",
    "parse_url",
    "patch",
    "publication_view",
    "refresh_metrics",
    "register",
    "require",
    "retention",
    "traffic",
    "verify_task",
    "verify_vk",
    "verify_youtube",
]
