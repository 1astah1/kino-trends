"""Пагинация и сортировка (docs/ARCHITECTURE.md §5.1 «Пагинация», «Сортировка и фильтры»).

- Списки: `?limit=50&offset=0` (limit ≤ 200) → `{items, total, limit, offset}` — `page()`.
- Журналы: `?before_id=&limit=` → `{items, next_before_id}` — `journal()`. Запрашивайте из БД
  `limit` строк по убыванию id; если пришло ровно `limit`, `next_before_id` = id последней.
- Сортировка: `?sort=-created_at` по белому списку — `order_by()` возвращает безопасный
  фрагмент `psycopg.sql` (имя колонки берётся из белого списка, не из запроса).

Параметры запроса объявляются алиасами из `api.schemas.common` (`LimitQuery`, `OffsetQuery`,
`BeforeIdQuery`, `SortQuery`), чтобы OpenAPI у всех эндпоинтов был одинаковым.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from psycopg import sql

from autoshorts.core.errors import ValidationFailed

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def clamp_limit(limit: int | None, *, default: int = DEFAULT_LIMIT) -> int:
    if limit is None:
        return default
    return max(1, min(int(limit), MAX_LIMIT))


def page(items: Sequence[Any], *, total: int, limit: int, offset: int) -> dict[str, Any]:
    return {"items": list(items), "total": int(total), "limit": int(limit), "offset": int(offset)}


def journal(items: Sequence[Mapping[str, Any]], *, limit: int, id_key: str = "id") -> dict[str, Any]:
    """`{items, next_before_id}`: курсор есть, только если страница заполнена целиком."""
    rows = list(items)
    next_before = int(rows[-1][id_key]) if rows and len(rows) >= limit else None
    return {"items": rows, "next_before_id": next_before}


def order_by(sort: str | None, allowed: Mapping[str, str], default: str) -> sql.Composable:
    """`sort` = `field` или `-field` из `allowed` (поле API → колонка SQL). Иначе 422.

    Всегда добавляет `id` вторым ключом для стабильного порядка страниц.
    """
    value = sort or default
    desc = value.startswith("-")
    name = value[1:] if desc else value
    column = allowed.get(name)
    if column is None:
        raise ValidationFailed(
            "Недопустимая сортировка",
            details={
                "errors": [
                    {
                        "loc": ["query", "sort"],
                        "msg": f"Допустимо: {', '.join(sorted(allowed))} (с '-' — по убыванию)",
                        "type": "sort_not_allowed",
                    }
                ]
            },
        )
    direction = sql.SQL("DESC") if desc else sql.SQL("ASC")
    parts = [sql.SQL("{} {}").format(sql.Identifier(*column.split(".")), direction)]
    if column != "id":
        parts.append(sql.SQL("id {}").format(direction))
    return sql.SQL("ORDER BY ") + sql.SQL(", ").join(parts)
