"""Продукты — то, что продвигают ролики (docs/ARCHITECTURE.md §5.2 «Продукты», таблица products).

Вебхук-часть (секрет, интеграция, тестовая конверсия) — поток M3-B.

Общие помощники каталога (ошибка на поле формы, ответ продукта) живут здесь и переиспользуются
сервисами кустов, ниш, каналов и кампаний.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import psycopg

from autoshorts.core.errors import NotFound, ValidationFailed
from autoshorts.repos import products as products_repo
from autoshorts.services import audit
from autoshorts.services.audit import Actor

ProductRow = dict[str, Any]

BRAND_KEYS = ("display_domain", "primary_color", "logo_asset_id")
LANDING_KEYS = ("title", "subtitle", "button_telegram", "button_site")


# ---------------------------------------------------------------- общие помощники каталога


def field_error(field: str | Sequence[str | int], msg: str, type_: str = "value_error") -> ValidationFailed:
    """422 `validation_error` с ошибкой на поле тела (форма подсвечивает его)."""
    loc = [field] if isinstance(field, str) else list(field)
    return ValidationFailed(msg, details={"errors": [{"loc": ["body", *loc], "msg": msg, "type": type_}]})


def constraint_of(exc: psycopg.Error) -> str:
    return getattr(getattr(exc, "diag", None), "constraint_name", "") or ""


def _pick(data: Any, keys: Sequence[str]) -> dict[str, Any]:
    src = data if isinstance(data, dict) else {}
    return {k: src.get(k) for k in keys}


# ---------------------------------------------------------------- ответ


def to_api(
    row: ProductRow, *, secrets_set: set[str] | None = None, stats: dict[str, Any] | None = None
) -> dict:
    secret = row.get("webhook_secret_name")
    utm = row.get("utm_defaults") or {}
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "status": row["status"],
        "landing_url": row["landing_url"],
        "redirect_host": row["redirect_host"],
        "redirect_mode": row["redirect_mode"],
        "telegram_bot_username": row["telegram_bot_username"],
        "tma_short_name": row["tma_short_name"],
        "telegram_link_style": row["telegram_link_style"],
        "utm_defaults": {str(k): str(v) for k, v in utm.items()} if isinstance(utm, dict) else {},
        "brand": _pick(row.get("brand"), BRAND_KEYS),
        "landing": _pick(row.get("landing"), LANDING_KEYS),
        "webhook_secret_set": bool(secret and secrets_set is not None and secret in secrets_set),
        "stats_7d": (
            {
                "clicks": int(stats["clicks"]),
                "unique_visitors": int(stats["unique_visitors"]),
                "bot_starts": int(stats["bot_starts"]),
                "first_payments": int(stats["first_payments"]),
                "revenue_minor": int(stats["revenue_minor"]),
                "currency": (stats["currency"] or "").strip() or None,
            }
            if stats is not None
            else None
        ),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _api_one(conn: Any, row: ProductRow) -> dict[str, Any]:
    secrets_set = products_repo.existing_secrets(conn, [row.get("webhook_secret_name") or ""])
    return to_api(row, secrets_set=secrets_set)


def display_domain(product: ProductRow | None) -> str | None:
    """Домен в кадре: brand.display_domain → redirect_host → хост landing_url."""
    if not product:
        return None
    brand = product.get("brand") or {}
    if isinstance(brand, dict) and brand.get("display_domain"):
        return str(brand["display_domain"])
    if product.get("redirect_host"):
        return str(product["redirect_host"])
    landing = product.get("landing_url") or ""
    if "://" in landing:
        return landing.split("://", 1)[1].split("/", 1)[0] or None
    return None


def code_url_template(product: ProductRow | None) -> str:
    """Как печатать код ролика по умолчанию: `<хост редиректора>/{code}` (§6.6).

    Хост — тот же, что у ссылок пакета (`services.tracking.public_host`: `redirect_host`, иначе
    `PUBLIC_BASE_URL`). Раньше без `redirect_host` в кадр шёл домен бренда или лендинга: зритель
    набирал адрес мимо редиректора — ни клика, ни старта `_l`, атрибуция самого заметного кода
    терялась. Голый домен без кода (атрибуция `direct`) печатает слой домена, не этот шаблон.
    """
    from autoshorts.services.tracking import public_host

    host = public_host(product).strip().rstrip("/")
    return f"{host}/{{code}}" if host else "{code}"


# ---------------------------------------------------------------- операции


def require(conn: Any, product_id: str, *, for_update: bool = False) -> ProductRow:
    row = products_repo.get(conn, product_id, for_update=for_update)
    if row is None:
        raise NotFound("product", "Продукт не найден")
    return row


def list_products(*, status: str | None, limit: int, offset: int) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        rows, total = products_repo.list_page(conn, status=status, limit=limit, offset=offset)
        stats = products_repo.stats_7d(conn, [r["id"] for r in rows])
        secrets_set = products_repo.existing_secrets(conn, [r.get("webhook_secret_name") or "" for r in rows])
    items = [to_api(r, secrets_set=secrets_set, stats=stats.get(r["id"])) for r in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def get_product(product_id: str) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        return _api_one(conn, require(conn, product_id))


def create_product(fields: dict[str, Any], actor: Actor) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    fields = {k: (str(v) if hasattr(v, "value") else v) for k, v in fields.items()}
    try:
        with connection() as conn:
            row = products_repo.insert(conn, fields)
            audit.record(
                conn,
                actor,
                "product.create",
                "product",
                row["id"],
                summary=f"создан продукт {row['id']}",
                diff={"id": row["id"], "name": row["name"], "kind": row["kind"]},
            )
            return _api_one(conn, row)
    except psycopg.errors.UniqueViolation as exc:
        raise field_error("id", "Продукт с таким id уже есть", "unique") from exc


def update_product(product_id: str, changes: dict[str, Any], actor: Actor) -> dict[str, Any]:
    """`changes` — только переданные поля; brand/landing сливаются с текущими (частичный PATCH)."""
    from autoshorts.db.pool import connection

    for key in ("name", "kind", "redirect_mode", "telegram_link_style"):
        if key in changes and changes[key] is None:
            raise field_error(key, "Поле не может быть пустым", "none")
    with connection() as conn:
        current = require(conn, product_id, for_update=True)
        fields: dict[str, Any] = {}
        for key, value in changes.items():
            if key in ("brand", "landing"):
                base = current.get(key) if isinstance(current.get(key), dict) else {}
                fields[key] = {**base, **(value or {})}
            elif key == "utm_defaults":
                fields[key] = dict(value or {})
            else:
                fields[key] = str(value) if hasattr(value, "value") else value
        row = products_repo.update(conn, product_id, fields)
        assert row is not None
        audit.record(
            conn,
            actor,
            "product.update",
            "product",
            product_id,
            summary=f"правка продукта {product_id}",
            diff={k: fields[k] for k in sorted(fields)},
        )
        return _api_one(conn, row)


def archive_product(product_id: str, actor: Actor) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        require(conn, product_id, for_update=True)
        row = products_repo.update(conn, product_id, {"status": "archived"})
        assert row is not None
        audit.record(conn, actor, "product.archive", "product", product_id, summary="продукт в архиве")
        return _api_one(conn, row)
