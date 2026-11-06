"""SQL трекинг-ссылок `tracking_links` (docs/ARCHITECTURE.md §4.2 0006/0015, §6.6). Владелец — M3-B.

Только SQL: функции принимают открытое соединение и не управляют транзакциями. Код ссылки уникален
(`UNIQUE (code)`), для кодов зрителя поиск идёт по списку кандидатов (`domain/tags.lookup_candidates`).
Пакетную ссылку ролика (`level='package'`) может заранее зарезервировать рендер нарезки (M5-B), её
переиспользует `package_build`; алиас площадки (`level='publication'`) создаёт регистрация публикации.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from psycopg import sql

Row = dict[str, Any]

COLUMNS = (
    "id",
    "code",
    "product_id",
    "level",
    "job_id",
    "publication_id",
    "channel_id",
    "campaign_id",
    "banner_variant_id",
    "platform",
    "target_override_url",
    "label",
    "is_active",
    "created_by",
    "created_at",
    "updated_at",
)
_TL = ", ".join(f"tl.{c}" for c in COLUMNS)
INSERTABLE = frozenset(COLUMNS) - {"id", "created_at", "updated_at"}
UPDATABLE = frozenset(
    {
        "label",
        "is_active",
        "target_override_url",
        "publication_id",
        "job_id",
        "channel_id",
        "campaign_id",
        "banner_variant_id",
        "platform",
    }
)


def get(conn: Any, link_id: int, *, for_update: bool = False) -> Row | None:
    lock = " FOR UPDATE" if for_update else ""
    return conn.execute(
        f"SELECT {_TL} FROM tracking_links tl WHERE tl.id = %(id)s{lock}", {"id": link_id}
    ).fetchone()


def get_by_code(conn: Any, code: str) -> Row | None:
    return conn.execute(f"SELECT {_TL} FROM tracking_links tl WHERE tl.code = %(c)s", {"c": code}).fetchone()


def find_first(conn: Any, codes: Sequence[str]) -> Row | None:
    """Первая существующая ссылка из списка кандидатов (порядок списка = приоритет)."""
    if not codes:
        return None
    return conn.execute(
        f"""
        SELECT {_TL} FROM tracking_links tl
        WHERE tl.code = ANY(%(codes)s)
        ORDER BY array_position(%(codes)s, tl.code)
        LIMIT 1
        """,
        {"codes": list(codes)},
    ).fetchone()


def code_taken(conn: Any, code: str) -> bool:
    """Занят ли код без учёта регистра (пакетные коды зритель набирает в любом регистре)."""
    row = conn.execute(
        "SELECT 1 AS x FROM tracking_links WHERE lower(code) = lower(%(c)s) LIMIT 1", {"c": code}
    ).fetchone()
    return row is not None


def insert(conn: Any, fields: dict[str, Any]) -> Row | None:
    """Вставить ссылку; занятый код → None (вызывающий берёт другой или отвечает 409)."""
    bad = set(fields) - INSERTABLE
    if bad:
        raise ValueError(f"tracking_links: columns not insertable: {sorted(bad)}")
    names = sorted(fields)
    query = sql.SQL(
        "INSERT INTO tracking_links ({cols}) VALUES ({vals}) ON CONFLICT (code) DO NOTHING RETURNING {ret}"
    ).format(
        cols=sql.SQL(", ").join(sql.Identifier(n) for n in names),
        vals=sql.SQL(", ").join(sql.Placeholder(n) for n in names),
        ret=sql.SQL(", ").join(sql.Identifier(c) for c in COLUMNS),
    )
    return conn.execute(query, fields).fetchone()


def update(conn: Any, link_id: int, fields: dict[str, Any]) -> Row | None:
    bad = set(fields) - UPDATABLE
    if bad:
        raise ValueError(f"tracking_links: columns not updatable: {sorted(bad)}")
    if not fields:
        return get(conn, link_id)
    sets = sql.SQL(", ").join(
        sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder(k)) for k in sorted(fields)
    )
    query = sql.SQL(
        "UPDATE tracking_links SET {sets}, updated_at = now() WHERE id = %(_id)s RETURNING {ret}"
    ).format(sets=sets, ret=sql.SQL(", ").join(sql.Identifier(c) for c in COLUMNS))
    return conn.execute(query, {**fields, "_id": link_id}).fetchone()


def package_link_for_job(conn: Any, job_id: int) -> Row | None:
    """Пакетная ссылка ролика (её мог заранее зарезервировать рендер нарезки)."""
    return conn.execute(
        f"""
        SELECT {_TL} FROM tracking_links tl
        WHERE tl.job_id = %(id)s AND tl.level = 'package' AND tl.is_active
        ORDER BY tl.id LIMIT 1
        """,
        {"id": job_id},
    ).fetchone()


def links_for_publication(conn: Any, publication_id: int) -> list[Row]:
    return conn.execute(
        f"""
        SELECT {_TL} FROM tracking_links tl
        WHERE tl.publication_id = %(id)s
        ORDER BY CASE tl.level WHEN 'package' THEN 0 WHEN 'publication' THEN 1 ELSE 2 END, tl.id
        """,
        {"id": publication_id},
    ).fetchall()


def detach_publication(conn: Any, publication_id: int) -> None:
    """Мягкое удаление публикации: алиас выключается, пакетная ссылка отвязывается."""
    conn.execute(
        """
        UPDATE tracking_links SET is_active = false, updated_at = now()
        WHERE publication_id = %(id)s AND level = 'publication'
        """,
        {"id": publication_id},
    )
    conn.execute(
        """
        UPDATE tracking_links SET publication_id = NULL, updated_at = now()
        WHERE publication_id = %(id)s AND level <> 'publication'
        """,
        {"id": publication_id},
    )


def channel_link(conn: Any, channel_id: str, product_id: str) -> Row | None:
    return conn.execute(
        f"""
        SELECT {_TL} FROM tracking_links tl
        WHERE tl.channel_id = %(ch)s AND tl.product_id = %(p)s AND tl.level = 'channel' AND tl.is_active
        ORDER BY tl.id LIMIT 1
        """,
        {"ch": channel_id, "p": product_id},
    ).fetchone()


def any_product_link(conn: Any, product_id: str) -> Row | None:
    """Пример кода продукта для инструкции интеграции и тестового события: канальный, иначе любой."""
    return conn.execute(
        f"""
        SELECT {_TL} FROM tracking_links tl
        WHERE tl.product_id = %(p)s AND tl.is_active
        ORDER BY CASE tl.level WHEN 'channel' THEN 0 WHEN 'package' THEN 1 ELSE 2 END, tl.id DESC
        LIMIT 1
        """,
        {"p": product_id},
    ).fetchone()


def list_page(
    conn: Any,
    *,
    product_id: str | None,
    levels: Sequence[str] | None,
    channel_id: str | None,
    campaign_id: int | None,
    is_active: bool | None,
    q: str | None,
    limit: int,
    offset: int,
) -> tuple[list[Row], int]:
    where = ["true"]
    params: dict[str, Any] = {"limit": int(limit), "offset": int(offset)}
    if product_id:
        where.append("tl.product_id = %(product_id)s")
        params["product_id"] = product_id
    if levels:
        where.append("tl.level = ANY(%(levels)s)")
        params["levels"] = list(levels)
    if channel_id:
        where.append("tl.channel_id = %(channel_id)s")
        params["channel_id"] = channel_id
    if campaign_id is not None:
        where.append("tl.campaign_id = %(campaign_id)s")
        params["campaign_id"] = int(campaign_id)
    if is_active is not None:
        where.append("tl.is_active = %(is_active)s")
        params["is_active"] = bool(is_active)
    if q:
        where.append("(tl.code ILIKE %(q)s OR tl.label ILIKE %(q)s)")
        params["q"] = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    cond = " AND ".join(where)
    total = conn.execute(f"SELECT count(*) AS n FROM tracking_links tl WHERE {cond}", params).fetchone()["n"]
    rows = conn.execute(
        f"""
        SELECT {_TL},
               (SELECT count(*) FROM clicks c
                 WHERE c.tracking_link_id = tl.id AND NOT c.is_bot
                   AND c.occurred_at >= now() - make_interval(days => 7)) AS clicks_7d
        FROM tracking_links tl WHERE {cond}
        ORDER BY tl.id DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    ).fetchall()
    return rows, int(total)


__all__ = [
    "COLUMNS",
    "any_product_link",
    "channel_link",
    "code_taken",
    "detach_publication",
    "find_first",
    "get",
    "get_by_code",
    "insert",
    "links_for_publication",
    "list_page",
    "package_link_for_job",
    "update",
]
