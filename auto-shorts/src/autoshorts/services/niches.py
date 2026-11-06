"""Ниши: конфиг с версиями, проверка, YAML-импорт и экспорт (§5.2 «Ниши», §3.2 «config.py»).

* Каждое сохранение конфига — новая строка `niche_versions` (история, откат), `niches.config` —
  текущая версия, `niches.current_version` — её номер (`expected_version` в PUT, иначе 409).
* Конфиг проверяется `domain.niche_schema.NicheConfig`; ошибки — 422 с путями
  (`details.config_errors=[{path,msg}]` + стандартные `details.errors` для формы).
* Ниша со статусом `needs_import` (появилась из legacy-каналов в миграции 0003) при первом
  импорте YAML получает версию 1 и становится `active`.
"""

from __future__ import annotations

from typing import Any, Literal

import psycopg

from autoshorts.core.errors import NotFound, ValidationFailed, VersionConflict
from autoshorts.domain.enums import NicheStatus, NicheVersionSource
from autoshorts.domain.niche_schema import (
    ConfigIssue,
    config_warnings,
    dump_config,
    dump_yaml,
    load_yaml,
    validate_config,
)
from autoshorts.repos import niches as niches_repo
from autoshorts.services import audit
from autoshorts.services.audit import Actor
from autoshorts.services.products import field_error

ImportMode = Literal["create", "replace", "auto"]


# ---------------------------------------------------------------- проверка


def _config_error(
    issues: list[ConfigIssue], *, base_loc: tuple[str, ...] = ("body", "config")
) -> ValidationFailed:
    errors = [
        {
            "loc": [*base_loc, *[p for p in i.path.replace("[", ".").replace("]", "").split(".") if p]],
            "msg": i.msg,
            "type": "config_invalid",
        }
        for i in issues
    ]
    return ValidationFailed(
        "Конфиг ниши не прошёл проверку",
        details={"errors": errors, "config_errors": [i.model_dump() for i in issues]},
    )


def check_config(
    niche_id: str, config: dict[str, Any], *, base_loc: tuple[str, ...] = ("body", "config")
) -> dict:
    """Проверить конфиг ниши и вернуть нормализованный JSON для БД (только заданные поля)."""
    cfg, issues = validate_config(config)
    if cfg is None:
        raise _config_error(issues, base_loc=base_loc)
    if cfg.id is not None and cfg.id != niche_id:
        raise _config_error(
            [ConfigIssue(path="id", msg=f"id в конфиге ({cfg.id}) не совпадает с нишей")], base_loc=base_loc
        )
    return dump_config(cfg)


def validate_niche_config(niche_id: str, config: dict[str, Any]) -> dict[str, Any]:
    """`POST /niches/{id}/validate`: без сохранения, ошибки и предупреждения по путям."""
    cfg, issues = validate_config(config)
    if cfg is not None and cfg.id is not None and cfg.id != niche_id:
        issues = [ConfigIssue(path="id", msg=f"id в конфиге ({cfg.id}) не совпадает с нишей")]
        cfg = None
    warnings = config_warnings(cfg) if cfg is not None else []
    return {
        "ok": not issues,
        "errors": [i.model_dump() for i in issues],
        "warnings": [w.model_dump() for w in warnings],
    }


# ---------------------------------------------------------------- чтение


def _require(conn: Any, niche_id: str, *, for_update: bool = False) -> dict[str, Any]:
    row = niches_repo.get(conn, niche_id, for_update=for_update)
    if row is None:
        raise NotFound("niche", "Ниша не найдена")
    return row


def _row_brief(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in ("id", "label", "status", "current_version", "used_by_channels", "updated_at")}


def list_niches(*, status: str | None, limit: int, offset: int) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        rows, total = niches_repo.list_page(conn, status=status, limit=limit, offset=offset)
    return {"items": [_row_brief(r) for r in rows], "total": total, "limit": limit, "offset": offset}


def get_niche(niche_id: str) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        return _require(conn, niche_id)


def list_versions(niche_id: str) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        _require(conn, niche_id)
        return {"items": niches_repo.list_versions(conn, niche_id)}


def get_version(niche_id: str, version: int) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        _require(conn, niche_id)
        row = niches_repo.get_version(conn, niche_id, version)
    if row is None:
        raise NotFound("niche_version", "Версия ниши не найдена")
    return row


def export_yaml(niche_id: str) -> str:
    from autoshorts.db.pool import connection

    with connection() as conn:
        row = _require(conn, niche_id)
    return dump_yaml(row["config"] or {}, niche_id=row["id"], label=row["label"])


# ---------------------------------------------------------------- запись


def _new_version(
    conn: Any,
    current: dict[str, Any],
    config: dict[str, Any],
    *,
    source: NicheVersionSource,
    actor: Actor,
    label: str | None = None,
) -> dict[str, Any]:
    version = int(current["current_version"]) + 1
    niches_repo.insert_version(
        conn,
        niche_id=current["id"],
        version=version,
        config=config,
        source=str(source),
        created_by=actor.user_id,
    )
    status = str(NicheStatus.ACTIVE) if current["status"] == NicheStatus.NEEDS_IMPORT else None
    row = niches_repo.set_config(
        conn, current["id"], config=config, version=version, status=status, label=label
    )
    assert row is not None
    audit.record(
        conn,
        actor,
        "niche.version",
        "niche",
        current["id"],
        summary=f"ниша {current['id']}: версия {version} ({source})",
        diff={"version": version, "source": str(source)},
    )
    return row


def _create(
    conn: Any, niche_id: str, label: str, config: dict[str, Any], *, source: NicheVersionSource, actor: Actor
) -> dict[str, Any]:
    niches_repo.insert(
        conn, niche_id=niche_id, label=label, status=str(NicheStatus.ACTIVE), config=config, current_version=1
    )
    niches_repo.insert_version(
        conn, niche_id=niche_id, version=1, config=config, source=str(source), created_by=actor.user_id
    )
    audit.record(
        conn, actor, "niche.create", "niche", niche_id, summary=f"создана ниша {niche_id} ({source})"
    )
    row = niches_repo.get(conn, niche_id)
    assert row is not None
    return row


def create_niche(
    *, niche_id: str, label: str, config: dict[str, Any], clone_from: str | None, actor: Actor
) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    try:
        with connection() as conn:
            if clone_from:
                src = niches_repo.get(conn, clone_from)
                if src is None:
                    raise field_error("clone_from", "Ниша-образец не найдена", "not_found")
                config = {k: v for k, v in (src["config"] or {}).items() if k not in ("id", "label")}
            normalized = check_config(niche_id, config)
            existing = niches_repo.get(conn, niche_id, for_update=True)
            if existing is not None:
                if existing["status"] != NicheStatus.NEEDS_IMPORT:
                    raise field_error("id", "Ниша с таким id уже есть", "unique")
                return _new_version(
                    conn, existing, normalized, source=NicheVersionSource.UI, actor=actor, label=label.strip()
                )
            return _create(
                conn, niche_id, label.strip(), normalized, source=NicheVersionSource.UI, actor=actor
            )
    except psycopg.errors.UniqueViolation as exc:
        raise field_error("id", "Ниша с таким id уже есть", "unique") from exc


def put_version(niche_id: str, config: dict[str, Any], expected_version: int, actor: Actor) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    normalized = check_config(niche_id, config)
    with connection() as conn:
        current = _require(conn, niche_id, for_update=True)
        if int(current["current_version"]) != int(expected_version):
            raise VersionConflict(
                "Нишу уже изменили, обновите страницу",
                details={"current_version": int(current["current_version"])},
            )
        return _new_version(conn, current, normalized, source=NicheVersionSource.UI, actor=actor)


def update_meta(niche_id: str, changes: dict[str, Any], actor: Actor) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    for key in ("label", "status"):
        if key in changes and changes[key] is None:
            raise field_error(key, "Поле не может быть пустым", "none")
    fields = {k: (str(v) if k == "status" else str(v).strip()) for k, v in changes.items()}
    with connection() as conn:
        _require(conn, niche_id, for_update=True)
        row = niches_repo.update_meta(conn, niche_id, fields)
        assert row is not None
        audit.record(
            conn, actor, "niche.update", "niche", niche_id, summary=f"правка ниши {niche_id}", diff=fields
        )
        return row


def import_config(
    data: dict[str, Any], *, mode: ImportMode, actor: Actor, loc: tuple[str, ...] = ("body", "file")
) -> tuple[dict[str, Any], str]:
    """Импорт конфига формы YAML. → (строка ниши, действие: created | updated | unchanged).

    `create` — только новая ниша (или заготовка `needs_import`); `replace` и `auto` — новая
    версия, если конфиг отличается от текущего, иначе ничего не меняется.
    """
    from autoshorts.db.pool import connection

    niche_id = data.get("id")
    if not isinstance(niche_id, str) or not niche_id:
        raise field_error(list(loc[1:]) or "file", "В YAML нет поля id", "missing")
    raw_label = data.get("label")
    if raw_label is not None and not isinstance(raw_label, str):
        raise field_error([*loc[1:], "label"], "label должен быть строкой", "string_type")
    label = (raw_label or niche_id).strip()
    normalized = check_config(niche_id, data, base_loc=loc)
    try:
        with connection() as conn:
            existing = niches_repo.get(conn, niche_id, for_update=True)
            if existing is None:
                return _create(
                    conn, niche_id, label, normalized, source=NicheVersionSource.YAML_IMPORT, actor=actor
                ), "created"
            if existing["status"] == NicheStatus.NEEDS_IMPORT:
                return _new_version(
                    conn,
                    existing,
                    normalized,
                    source=NicheVersionSource.YAML_IMPORT,
                    actor=actor,
                    label=label,
                ), "created"
            if mode == "create":
                raise field_error(
                    list(loc[1:]) or "file", f"Ниша {niche_id} уже есть: нужен mode=replace", "unique"
                )
            if (existing["config"] or {}) == normalized and existing["label"] == label:
                return existing, "unchanged"
            return _new_version(
                conn, existing, normalized, source=NicheVersionSource.YAML_IMPORT, actor=actor, label=label
            ), "updated"
    except psycopg.errors.UniqueViolation as exc:
        raise field_error("file", f"Ниша {niche_id} уже есть", "unique") from exc


def import_yaml(text: str, *, mode: ImportMode, actor: Actor) -> dict[str, Any]:
    try:
        data = load_yaml(text)
    except ValueError as exc:
        raise field_error("file", str(exc), "yaml_invalid") from None
    row, _ = import_config(data, mode=mode, actor=actor)
    return row
