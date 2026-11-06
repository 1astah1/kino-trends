"""Кусты изоляции каналов (ТЗ §10; §5.2 `/clusters`): свой Cloud-проект и квота YouTube на куст."""

from __future__ import annotations

from typing import Any

import psycopg

from autoshorts.core.errors import NotFound
from autoshorts.repos import clusters as clusters_repo
from autoshorts.services import audit
from autoshorts.services.audit import Actor
from autoshorts.services.products import field_error


def _require(conn: Any, cluster_id: str) -> dict[str, Any]:
    row = clusters_repo.get(conn, cluster_id)
    if row is None:
        raise NotFound("cluster", "Куст не найден")
    return row


def list_clusters() -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        return {"items": clusters_repo.list_all(conn)}


def get_cluster(cluster_id: str) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        return _require(conn, cluster_id)


def create_cluster(*, cluster_id: str, title: str, notes: str | None, actor: Actor) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    try:
        with connection() as conn:
            row = clusters_repo.insert(conn, cluster_id=cluster_id, title=title.strip(), notes=notes)
            audit.record(
                conn, actor, "cluster.create", "cluster", cluster_id, summary=f"создан куст {cluster_id}"
            )
            return row
    except psycopg.errors.UniqueViolation as exc:
        raise field_error("id", "Куст с таким id уже есть", "unique") from exc


def update_cluster(cluster_id: str, changes: dict[str, Any], actor: Actor) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    if "title" in changes and changes["title"] is None:
        raise field_error("title", "Поле не может быть пустым", "none")
    with connection() as conn:
        _require(conn, cluster_id)
        row = clusters_repo.update(conn, cluster_id, changes)
        assert row is not None
        audit.record(
            conn,
            actor,
            "cluster.update",
            "cluster",
            cluster_id,
            summary=f"правка куста {cluster_id}",
            diff=dict(changes),
            safe_keys=frozenset({"youtube_api_key_secret", "youtube_oauth_client_secret"}),
        )
        return row
