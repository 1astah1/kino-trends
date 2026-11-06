"""Трекинг-ссылки: коды, URL для зрителя, статистика (docs/ARCHITECTURE.md §5.2, §5.3, §6.6). Владелец — M3-B.

Функции с `conn` работают в транзакции вызывающего:

    with connection() as conn:
        link = create_link(conn, fields, actor)
        return link_view(conn, link)

* Пакетная ссылка ролика (`ensure_package_link`) — переиспользует код, который заранее
  зарезервировал рендер нарезки (код уже в кадре!), иначе выдаёт новый 5-символьный код.
* Алиас площадки (`ensure_alias_link`) — `yt_<id>` / `vk_<owner>_<id>` при регистрации публикации;
  ссылка, выключенная мягким удалением прежней регистрации, включается снова.
* Ручные, канальные и кампанийные ссылки — из API (`create_link`, `channel_link`).
* `resolve_code` — поиск кода зрителя для редиректора (`public/*`): точно, затем нижний регистр и
  опечатки для 5-символьных кодов; выключенная ссылка = неизвестный код.

URL для зрителя строятся из продукта: `https://<redirect_host>/<code>`, Telegram — `tg://resolve`
и `https://telegram.me/…` (t.me в РФ ненадёжен), TMA — `startapp`, сайт — `utm_campaign=<code>`.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from autoshorts.core.errors import Conflict, NotFound, ValidationFailed
from autoshorts.domain import tags
from autoshorts.repos import clicks as clicks_repo
from autoshorts.repos import conversions as conversions_repo
from autoshorts.repos import tracking as tracking_repo
from autoshorts.services import audit
from autoshorts.services.audit import Actor

log = logging.getLogger(__name__)

Row = dict[str, Any]

PLATFORM_UTM_SOURCE = {"youtube": "yt", "vk": "vk"}
UTM_MEDIUM = "shorts"
CHANNEL_LINK_LABEL = "Ссылка канала (шапка, «Подписаться»)"


# ---------------------------------------------------------------- ошибки


def _invalid(field: str, message: str, kind: str) -> ValidationFailed:
    return ValidationFailed(
        message, details={"errors": [{"loc": ["body", field], "msg": message, "type": kind}]}
    )


def _code_taken(code: str) -> Conflict:
    return Conflict("Такой код уже занят", code="tracking_code_taken", details={"code": code})


# ---------------------------------------------------------------- URL для зрителя


def public_host(product: Row | None) -> str:
    """Хост редиректора: `products.redirect_host`, иначе `PUBLIC_BASE_URL` (dev)."""
    if product and product.get("redirect_host"):
        return str(product["redirect_host"]).strip().rstrip("/")
    from autoshorts.core.config import get_env

    base = get_env().public_base_url.rstrip("/")
    return base.split("://", 1)[-1]


def public_base(product: Row | None) -> str:
    if product and product.get("redirect_host"):
        return "https://" + public_host(product)
    from autoshorts.core.config import get_env

    return get_env().public_base_url.rstrip("/")


def redirect_url(product: Row | None, code: str) -> str:
    return f"{public_base(product)}/{quote(code, safe='')}"


def is_tma(product: Row | None) -> bool:
    return bool(product and product.get("tma_short_name") and product.get("kind") == "telegram_mini_app")


def telegram_urls(product: Row | None, payload: str) -> tuple[str | None, str | None]:
    """(tg://…, https://telegram.me/…) для start-параметра `payload`; нет бота — (None, None)."""
    bot = (product or {}).get("telegram_bot_username")
    if not bot:
        return None, None
    p = quote(payload, safe="")
    if is_tma(product):
        app = product["tma_short_name"]
        return (
            f"tg://resolve?domain={bot}&appname={app}&startapp={p}",
            f"https://telegram.me/{bot}/{app}?startapp={p}",
        )
    return f"tg://resolve?domain={bot}&start={p}", f"https://telegram.me/{bot}?start={p}"


def tma_url(product: Row | None, payload: str) -> str | None:
    if not is_tma(product) or not product.get("telegram_bot_username"):
        return None
    bot, app = product["telegram_bot_username"], product["tma_short_name"]
    return f"https://t.me/{bot}/{app}?startapp={quote(payload, safe='')}"


def site_url(
    product: Row | None,
    code: str | None,
    *,
    platform: str | None = None,
    variant_code: str | None = None,
    override: str | None = None,
) -> str | None:
    """Лендинг продукта с utm: `utm_source=<yt|vk>&utm_medium=shorts&utm_campaign=<code>&utm_content=<A>`."""
    base = override or (product or {}).get("landing_url")
    if not base:
        return None
    parts = urlsplit(base)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    defaults = (product or {}).get("utm_defaults") or {}
    if isinstance(defaults, dict):
        for k, v in defaults.items():
            query.setdefault(str(k), str(v))
    query["utm_source"] = PLATFORM_UTM_SOURCE.get(platform or "", query.get("utm_source") or "redirect")
    query["utm_medium"] = UTM_MEDIUM
    if code:
        query["utm_campaign"] = code
    if variant_code:
        query["utm_content"] = variant_code
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", urlencode(query), parts.fragment))


def product_for(conn: Any, product_id: str | None) -> Row | None:
    if not product_id:
        return None
    return conn.execute(
        """
        SELECT id, name, kind, status, landing_url, redirect_host, redirect_mode, telegram_bot_username,
               tma_short_name, telegram_link_style, utm_defaults, brand, landing
        FROM products WHERE id = %(id)s
        """,
        {"id": product_id},
    ).fetchone()


def default_product_id(conn: Any) -> str:
    from autoshorts.services.settings import get_runtime_settings

    try:
        return get_runtime_settings(conn).redirect_default_product
    except Exception:  # noqa: BLE001 - нет настроек — дефолт схемы
        return "umka"


# ---------------------------------------------------------------- ответ API


def link_view(
    conn: Any, link: Row, *, product: Row | None = None, with_clicks: bool = False
) -> dict[str, Any]:
    product = product or product_for(conn, link["product_id"])
    code = link["code"]
    tg, tg_https = telegram_urls(product, code)
    return {
        "id": int(link["id"]),
        "code": code,
        "product_id": link["product_id"],
        "level": link["level"],
        "job_id": link.get("job_id"),
        "publication_id": link.get("publication_id"),
        "channel_id": link.get("channel_id"),
        "campaign_id": link.get("campaign_id"),
        "banner_variant_id": link.get("banner_variant_id"),
        "platform": link.get("platform"),
        "label": link.get("label"),
        "target_override_url": link.get("target_override_url"),
        "is_active": bool(link["is_active"]),
        "redirect_url": redirect_url(product, code),
        "telegram_url": tg,
        "telegram_https_url": tg_https,
        "tma_url": tma_url(product, code),
        "clicks_7d": int(link["clicks_7d"]) if with_clicks and link.get("clicks_7d") is not None else None,
        "created_by": link.get("created_by"),
        "created_at": link["created_at"],
        "updated_at": link["updated_at"],
    }


def list_links(conn: Any, **filters: Any) -> dict[str, Any]:
    rows, total = tracking_repo.list_page(conn, **filters)
    products: dict[str, Row | None] = {}
    items = []
    for row in rows:
        pid = row["product_id"]
        if pid not in products:
            products[pid] = product_for(conn, pid)
        items.append(link_view(conn, row, product=products[pid], with_clicks=True))
    return {"items": items, "total": total, "limit": filters["limit"], "offset": filters["offset"]}


def require_link(conn: Any, link_id: int, *, for_update: bool = False) -> Row:
    row = tracking_repo.get(conn, link_id, for_update=for_update)
    if row is None:
        raise NotFound("tracking_link", "Ссылка не найдена")
    return row


# ---------------------------------------------------------------- создание


def _insert_with_code(conn: Any, fields: dict[str, Any], code: str | None) -> Row:
    """Вставить с заданным кодом (занят → 409) или с новым сгенерированным."""
    if code:
        if tracking_repo.code_taken(conn, code):
            raise _code_taken(code)
        row = tracking_repo.insert(conn, {**fields, "code": code})
        if row is None:
            raise _code_taken(code)
        return row
    for _ in range(30):
        candidate = tags.unique_code(lambda c: tracking_repo.code_taken(conn, c))
        row = tracking_repo.insert(conn, {**fields, "code": candidate})
        if row is not None:
            return row
    raise RuntimeError("could not allocate a unique tracking code")


def create_link(conn: Any, body: Any, actor: Actor) -> Row:
    """POST /tracking-links: manual | channel | campaign (пакеты и алиасы создаёт система)."""
    level = str(body.level)
    product = product_for(conn, body.product_id)
    if product is None:
        raise NotFound("product", "Продукт не найден")
    fields: dict[str, Any] = {
        "product_id": body.product_id,
        "level": level,
        "label": body.label,
        "target_override_url": body.target_override_url,
        "created_by": actor.user_id,
    }
    if level == "channel":
        if not body.channel_id:
            raise _invalid("channel_id", "Для ссылки канала нужен channel_id", "channel_required")
        ch = conn.execute(
            "SELECT id, platform FROM channels WHERE id = %(id)s", {"id": body.channel_id}
        ).fetchone()
        if ch is None:
            raise NotFound("channel", "Канал не найден")
        fields.update({"channel_id": ch["id"], "platform": ch["platform"]})
    elif body.channel_id:
        raise _invalid("channel_id", "channel_id — только для ссылки канала", "channel_not_allowed")
    if level == "campaign":
        if body.campaign_id is None:
            raise _invalid("campaign_id", "Для ссылки кампании нужен campaign_id", "campaign_required")
        camp = conn.execute(
            "SELECT id, product_id FROM campaigns WHERE id = %(id)s", {"id": body.campaign_id}
        ).fetchone()
        if camp is None:
            raise NotFound("campaign", "Кампания не найдена")
        if camp["product_id"] != body.product_id:
            raise _invalid("campaign_id", "Кампания другого продукта", "campaign_product_mismatch")
        fields["campaign_id"] = int(camp["id"])
    elif body.campaign_id is not None:
        raise _invalid("campaign_id", "campaign_id — только для ссылки кампании", "campaign_not_allowed")
    if body.code:
        problem = tags.manual_code_problem(body.code)
        if problem:
            messages = {
                "code_reserved": "Код совпадает со служебным путём редиректора",
                "code_landing_suffix": "Код не может заканчиваться на _l (так помечается переход с лендинга)",
                "code_alias_prefix": "Префиксы yt_ и vk_ — только для алиасов площадок",
                "code_too_long": "Код слишком длинный: вместе с _l не больше 64 символов",
            }
            raise _invalid("code", messages.get(problem, "Код — латиница, цифры, _ и -"), problem)
    row = _insert_with_code(conn, fields, body.code)
    audit.record(
        conn,
        actor,
        "tracking_link.create",
        "tracking_link",
        int(row["id"]),
        summary=f"ссылка {row['code']} ({level}) для {body.product_id}",
        diff={
            "code": row["code"],
            "level": level,
            "channel_id": fields.get("channel_id"),
            "campaign_id": fields.get("campaign_id"),
        },
    )
    return row


def update_link(conn: Any, link_id: int, changes: dict[str, Any], actor: Actor) -> Row:
    before = require_link(conn, link_id, for_update=True)
    allowed = {k: v for k, v in changes.items() if k in ("label", "is_active", "target_override_url")}
    if "is_active" in allowed and allowed["is_active"] is None:
        allowed.pop("is_active")
    row = tracking_repo.update(conn, link_id, allowed)
    diff = {k: {"from": before.get(k), "to": v} for k, v in allowed.items() if before.get(k) != v}
    if diff:
        audit.record(
            conn,
            actor,
            "tracking_link.update",
            "tracking_link",
            link_id,
            summary=f"ссылка {before['code']}",
            diff=diff,
        )
    return row


def channel_link(conn: Any, channel_id: str, product_id: str | None, label: str | None, actor: Actor) -> Row:
    """Ссылка канала (шапка, «Подписаться»): существующая активная или новая."""
    ch = conn.execute(
        "SELECT id, platform, default_product_id, title FROM channels WHERE id = %(id)s", {"id": channel_id}
    ).fetchone()
    if ch is None:
        raise NotFound("channel", "Канал не найден")
    pid = product_id or ch.get("default_product_id") or default_product_id(conn)
    if product_for(conn, pid) is None:
        raise NotFound("product", "Продукт не найден")
    existing = tracking_repo.channel_link(conn, channel_id, pid)
    if existing is not None:
        return existing
    row = _insert_with_code(
        conn,
        {
            "product_id": pid,
            "level": "channel",
            "channel_id": channel_id,
            "platform": ch["platform"],
            "label": label or CHANNEL_LINK_LABEL,
            "created_by": actor.user_id,
        },
        None,
    )
    audit.record(
        conn,
        actor,
        "tracking_link.create",
        "tracking_link",
        int(row["id"]),
        summary=f"ссылка канала {channel_id}: {row['code']}",
        diff={"code": row["code"], "level": "channel", "channel_id": channel_id, "product_id": pid},
    )
    return row


def ensure_package_link(conn: Any, job: Row, *, product_id: str) -> Row:
    """Пакетная ссылка ролика: зарезервированная рендером (код уже в кадре) или новая."""
    existing = tracking_repo.package_link_for_job(conn, int(job["id"]))
    if existing is not None:
        return existing
    return _insert_with_code(
        conn,
        {
            "product_id": product_id,
            "level": "package",
            "job_id": int(job["id"]),
            "channel_id": job.get("channel_id"),
            "campaign_id": job.get("campaign_id"),
            "banner_variant_id": job.get("banner_variant_id"),
            "platform": job.get("platform") or job.get("channel_platform"),
        },
        None,
    )


def ensure_alias_link(conn: Any, alias: str, *, publication: Row, product_id: str) -> Row:
    """Алиас площадки `yt_<id>` / `vk_<o>_<id>` → публикация (выключенный прежний — включается)."""
    dims = {
        "publication_id": int(publication["id"]),
        "job_id": int(publication["job_id"]),
        "channel_id": publication["channel_id"],
        "campaign_id": publication.get("campaign_id"),
        "banner_variant_id": publication.get("banner_variant_id"),
        "platform": publication["platform"],
    }
    existing = tracking_repo.get_by_code(conn, alias)
    if existing is not None:
        if existing["is_active"] and existing.get("publication_id") not in (None, publication["id"]):
            raise Conflict(
                "Алиас площадки уже привязан к другой публикации",
                code="publication_exists",
                details={"publication_id": existing.get("publication_id")},
            )
        return tracking_repo.update(conn, int(existing["id"]), {**dims, "is_active": True})
    row = tracking_repo.insert(
        conn, {"code": alias, "product_id": product_id, "level": "publication", **dims}
    )
    if row is None:  # гонка: вставили между SELECT и INSERT
        raise Conflict("Алиас площадки уже занят", code="publication_exists", details={"code": alias})
    return row


# ---------------------------------------------------------------- поиск кода зрителя


def resolve_code(conn: Any, code: str) -> Row | None:
    """Ссылка по коду зрителя (активная) или None. Невалидный код — None (редиректор уже ответил 404)."""
    row = tracking_repo.find_first(conn, tags.lookup_candidates(code))
    if row is None or not row["is_active"]:
        return None
    return row


# ---------------------------------------------------------------- статистика


def _num(v: Any) -> int:
    return int(v or 0)


def link_stats(
    conn: Any, link_id: int, *, date_from: date | None, date_to: date | None, granularity: str
) -> dict[str, Any]:
    from autoshorts.services.settings import business_range, get_runtime_settings

    require_link(conn, link_id)
    tz = get_runtime_settings(conn).business_timezone
    since, until = business_range(date_from, date_to, tz)
    ct = clicks_repo.totals(conn, tracking_link_id=link_id, since=since, until=until)
    vt = conversions_repo.totals(conn, tracking_link_id=link_id, since=since, until=until)
    return {
        "link_id": link_id,
        "granularity": granularity,
        "totals": {
            "clicks": _num(ct["clicks"]),
            "unique_visitors": _num(ct["unique_visitors"]),
            "bot_clicks": _num(ct["bot_clicks"]),
            "to_telegram": _num(ct["to_telegram"]),
            "to_site": _num(ct["to_site"]),
            "bot_starts": _num(vt["bot_starts"]),
            "signups": _num(vt["signups"]),
            "first_payments": _num(vt["first_payments"]),
            "revenue_minor": _num(vt["revenue_minor"]),
            "currency": (vt["currency"] or "").strip() or None,
        },
        "series": traffic_series(
            conn, granularity=granularity, tz=tz, since=since, until=until, tracking_link_id=link_id
        ),
    }


def traffic_series(
    conn: Any, *, granularity: str, tz: str, since: Any = None, until: Any = None, **scope: Any
) -> list[dict[str, Any]]:
    """Клики и конверсии одним рядом (`TrafficPoint`), интервалы — объединение обоих источников."""
    points: dict[Any, dict[str, Any]] = {}

    def point(t: Any) -> dict[str, Any]:
        return points.setdefault(
            t,
            {
                "t": t,
                "clicks": 0,
                "unique_visitors": 0,
                "to_telegram": 0,
                "to_site": 0,
                "bot_starts": 0,
                "signups": 0,
                "first_payments": 0,
                "revenue_minor": 0,
            },
        )

    for r in clicks_repo.series(conn, granularity=granularity, tz=tz, since=since, until=until, **scope):
        p = point(r["t"])
        for k in ("clicks", "unique_visitors", "to_telegram", "to_site"):
            p[k] = _num(r[k])
    for r in conversions_repo.series(conn, granularity=granularity, tz=tz, since=since, until=until, **scope):
        p = point(r["t"])
        for k in ("bot_starts", "signups", "first_payments", "revenue_minor"):
            p[k] = _num(r[k])
    return [points[t] for t in sorted(points)]


# ---------------------------------------------------------------- журналы


def attribution_level(row: Row) -> str:
    """exact — ролик (код пакета, алиас); channel — ссылка канала; direct — продукт без ролика."""
    if row.get("publication_id") is not None or row.get("job_id") is not None:
        return "exact"
    if row.get("channel_id") is not None or row.get("link_level") == "channel":
        return "channel"
    return "direct"


def conversion_view(row: Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "product_id": row["product_id"],
        "external_event_id": row["external_event_id"],
        "event_type": row["event_type"],
        "occurred_at": row["occurred_at"],
        "received_at": row["received_at"],
        "tag": row.get("tag"),
        "via_landing": bool(row["via_landing"]),
        "attribution_level": attribution_level(row),
        "tracking_link_id": row.get("tracking_link_id"),
        "publication_id": row.get("publication_id"),
        "job_id": row.get("job_id"),
        "channel_id": row.get("channel_id"),
        "campaign_id": row.get("campaign_id"),
        "banner_variant_id": row.get("banner_variant_id"),
        "platform": row.get("platform"),
        "amount_minor": row.get("amount_minor"),
        "currency": (row.get("currency") or "").strip() or None,
        "meta": row.get("meta") or {},
        "is_test": bool(row["is_test"]),
    }


def journal_page(items: list[Row], limit: int) -> int | None:
    return int(items[-1]["id"]) if len(items) >= limit and items else None


__all__ = [
    "attribution_level",
    "channel_link",
    "conversion_view",
    "create_link",
    "default_product_id",
    "ensure_alias_link",
    "ensure_package_link",
    "journal_page",
    "link_stats",
    "link_view",
    "list_links",
    "product_for",
    "public_base",
    "redirect_url",
    "require_link",
    "resolve_code",
    "site_url",
    "telegram_urls",
    "tma_url",
    "traffic_series",
    "update_link",
]
