"""Каналы на площадках, их здоровье и журнал инцидентов (§5.2 «Ниши и каналы», §1.3, ТЗ §10-11).

* У канала свой профиль (голос, шаблон баннера, стиль визуала, seed-семья): одинаковые профили
  на одной площадке — предупреждение `profile_duplicate_of` (разные профили против детекта сетки).
* Ссылки (ниша, куст, продукт по умолчанию, канал-замена, шаблон баннера, провайдер TTS)
  проверяются: ошибка — 422 на поле формы.
* Здоровье считается по реестру публикаций и инцидентам; метрики площадок наполняют M4-коллекторы,
  пока их нет — поля пустые.
"""

from __future__ import annotations

import statistics
from typing import Any

import psycopg

from autoshorts.core.errors import NotFound
from autoshorts.repos import channels as channels_repo
from autoshorts.services import audit
from autoshorts.services.audit import Actor
from autoshorts.services.products import constraint_of, field_error

PROFILE_KEYS = ("tts_provider", "tts_voice", "banner_template_id", "visual_style_suffix", "seed_family")
PLAN_KEYS = ("videos_per_day", "mode", "slots", "timezone", "privacy", "category_id", "language", "clips_per_day")
SANCTION_KINDS = ("sanction", "strike", "freeze", "reach_drop", "claim")

_FK_FIELDS = {
    "fk_channels_niche": "niche_id",
    "fk_channels_cluster": "cluster",
    "channels_default_product_id_fkey": "default_product_id",
    "channels_replaced_by_fkey": "replaced_by",
}
_FK_MESSAGES = {
    "niche_id": "Ниша не найдена",
    "cluster": "Куст не найден",
    "default_product_id": "Продукт не найден",
    "replaced_by": "Канал-замена не найден",
}


def _pick(data: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    src = data if isinstance(data, dict) else {}
    return {k: src.get(k) for k in keys}


def to_api(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: row[k] for k in row if k not in ("profile", "publishing_plan", "profile_duplicate_of")}
    out["profile"] = _pick(row.get("profile"), PROFILE_KEYS)
    out["publishing_plan"] = _pick(row.get("publishing_plan"), PLAN_KEYS)
    out["profile_duplicate_of"] = list(row.get("profile_duplicate_of") or [])
    return out


def _require(conn: Any, channel_id: str, *, for_update: bool = False) -> dict[str, Any]:
    row = channels_repo.get(conn, channel_id, for_update=for_update)
    if row is None:
        raise NotFound("channel", "Канал не найден")
    return row


def _check_profile(conn: Any, profile: dict[str, Any]) -> None:
    template_id = profile.get("banner_template_id")
    if template_id is not None:
        found = conn.execute(
            "SELECT 1 FROM banner_templates WHERE id = %(id)s", {"id": template_id}
        ).fetchone()
        if found is None:
            raise field_error(["profile", "banner_template_id"], "Шаблон баннера не найден", "not_found")
    provider = profile.get("tts_provider")
    if provider:
        found = conn.execute(
            "SELECT 1 FROM provider_configs WHERE key = %(key)s AND kind = 'tts'", {"key": provider}
        ).fetchone()
        if found is None:
            raise field_error(["profile", "tts_provider"], "Нет такого провайдера озвучки", "not_found")


def _db_error(exc: psycopg.Error) -> Exception:
    name = constraint_of(exc)
    if isinstance(exc, psycopg.errors.ForeignKeyViolation):
        field = _FK_FIELDS.get(name, "body")
        return field_error(field, _FK_MESSAGES.get(field, "Связанная запись не найдена"), "not_found")
    if isinstance(exc, psycopg.errors.UniqueViolation):
        if "ref" in name:
            return field_error("platform_channel_ref", "Этот канал площадки уже заведён", "unique")
        return field_error("id", "Канал с таким id уже есть", "unique")
    return field_error("body", "Данные канала не прошли проверку БД", "db_check")


# ---------------------------------------------------------------- CRUD


def list_channels(**filters: Any) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    limit, offset = filters["limit"], filters["offset"]
    with connection() as conn:
        rows, total = channels_repo.list_page(conn, **filters)
    return {"items": [to_api(r) for r in rows], "total": total, "limit": limit, "offset": offset}


def get_channel(channel_id: str) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        return to_api(_require(conn, channel_id))


def create_channel(fields: dict[str, Any], actor: Actor) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    try:
        with connection() as conn:
            _check_profile(conn, fields.get("profile") or {})
            row = channels_repo.insert(conn, fields)
            audit.record(
                conn,
                actor,
                "channel.create",
                "channel",
                row["id"],
                summary=f"канал {row['id']} ({row['platform']})",
                diff={"id": row["id"], "platform": row["platform"], "niche_id": row["niche_id"]},
            )
            return to_api(row)
    except (
        psycopg.errors.ForeignKeyViolation,
        psycopg.errors.UniqueViolation,
        psycopg.errors.CheckViolation,
    ) as exc:
        raise _db_error(exc) from exc


def update_channel(channel_id: str, changes: dict[str, Any], actor: Actor) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    for key in ("niche_id", "cluster", "status", "profile", "publishing_plan", "advanced_features"):
        if key in changes and changes[key] is None:
            raise field_error(key, "Поле не может быть пустым", "none")
    if changes.get("replaced_by") == channel_id:
        raise field_error("replaced_by", "Канал не может заменять сам себя", "self_reference")
    try:
        with connection() as conn:
            current = _require(conn, channel_id, for_update=True)
            fields = dict(changes)
            if "profile" in fields:
                base = current.get("profile") if isinstance(current.get("profile"), dict) else {}
                fields["profile"] = {**base, **fields["profile"]}
                _check_profile(conn, fields["profile"])
            if "publishing_plan" in fields:
                base = (
                    current.get("publishing_plan") if isinstance(current.get("publishing_plan"), dict) else {}
                )
                fields["publishing_plan"] = {**base, **fields["publishing_plan"]}
            row = channels_repo.update(conn, channel_id, fields)
            assert row is not None
            audit.record(
                conn,
                actor,
                "channel.update",
                "channel",
                channel_id,
                summary=f"правка канала {channel_id}",
                diff={k: fields[k] for k in sorted(fields)},
            )
            return to_api(row)
    except (
        psycopg.errors.ForeignKeyViolation,
        psycopg.errors.UniqueViolation,
        psycopg.errors.CheckViolation,
    ) as exc:
        raise _db_error(exc) from exc


# ---------------------------------------------------------------- инциденты и здоровье


def list_incidents(channel_id: str) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        _require(conn, channel_id)
        return {"items": channels_repo.list_incidents(conn, channel_id)}


def create_incident(channel_id: str, body: dict[str, Any], actor: Actor) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        _require(conn, channel_id)
        row = channels_repo.insert_incident(
            conn,
            channel_id=channel_id,
            kind=str(body["kind"]),
            note=body.get("note"),
            details=body.get("details") or {},
            occurred_at=body.get("occurred_at"),
            created_by=actor.user_id,
        )
        audit.record(
            conn,
            actor,
            "channel.incident",
            "channel",
            channel_id,
            summary=f"инцидент {row['kind']}",
            diff={"incident_id": row["id"], "kind": row["kind"]},
        )
        return row


def _below_p25_streak(views: list[int]) -> int:
    """Сколько последних роликов подряд ниже p25 канала (нужно ≥ 8 роликов для порога)."""
    if len(views) < 8:
        return 0
    p25 = statistics.quantiles(views, n=4, method="inclusive")[0]
    streak = 0
    for v in views:
        if v < p25:
            streak += 1
        else:
            break
    return streak


def channel_health(channel_id: str) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        row = _require(conn, channel_id)
        pubs = channels_repo.recent_publications(conn, channel_id)
        nums = channels_repo.health_numbers(conn, channel_id)
        views = channels_repo.views_by_recency(conn, channel_id)
        kinds = channels_repo.recent_incident_kinds(conn, channel_id)
    signals = [k for k in kinds if k in SANCTION_KINDS]
    if row["status"] in ("sanctioned", "terminated"):
        signals.insert(0, f"status_{row['status']}")
    if int(nums["removed_count_30d"] or 0) > 0:
        signals.append("removed_by_platform")
    m7, prev = nums["median_7d"], nums["median_prev_7d"]
    trend = round(float(m7) / float(prev), 3) if m7 is not None and prev not in (None, 0) else None
    share = nums["shorts_feed_share"]
    return {
        "status": row["status"],
        "last_publications": pubs,
        "removed_count_30d": int(nums["removed_count_30d"] or 0),
        "sanction_signals": signals,
        "views_trend_7d": trend,
        "below_p25_streak": _below_p25_streak(views),
        "shorts_feed_share": round(float(share), 4) if share is not None else None,
    }
