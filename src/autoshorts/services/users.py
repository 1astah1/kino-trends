"""Пользователи и роли (docs/ARCHITECTURE.md §5.1 «Роли», §5.2 `/users`). Только admin.

Инварианты:
- всегда остаётся хотя бы один активный admin (понизить или отключить последнего нельзя — 409);
- отключение пользователя сразу отзывает его сессии; сброс пароля снимает блокировку входа и
  тоже отзывает сессии;
- занятый логин или `tg_user_id` — 422 с ошибкой на поле (форма в UI подсвечивает его).
"""

from __future__ import annotations

from typing import Any

import psycopg

from autoshorts.core.errors import Conflict, NotFound, ValidationFailed
from autoshorts.core.security import hash_password
from autoshorts.domain.enums import Role
from autoshorts.repos import api_tokens as api_tokens_repo
from autoshorts.repos import sessions as sessions_repo
from autoshorts.repos import users as users_repo
from autoshorts.services import audit
from autoshorts.services.audit import Actor
from autoshorts.services.auth import check_new_password


def _unique_error(exc: psycopg.errors.UniqueViolation) -> ValidationFailed:
    constraint = getattr(exc.diag, "constraint_name", "") or ""
    if "tg_user_id" in constraint:
        field, msg = "tg_user_id", "Этот Telegram ID уже привязан к другому пользователю"
    else:
        field, msg = "login", "Логин уже занят"
    return ValidationFailed(msg, details={"errors": [{"loc": ["body", field], "msg": msg, "type": "unique"}]})


def list_users() -> list[dict[str, Any]]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        return users_repo.list_all(conn)


def create_user(
    *,
    login: str,
    display_name: str,
    role: Role,
    password: str,
    tg_user_id: int | None,
    actor: Actor,
) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    check_new_password(password, "password")
    password_hash = hash_password(password)
    try:
        with connection() as conn:
            row = users_repo.insert(
                conn,
                login=login,
                display_name=display_name.strip(),
                password_hash=password_hash,
                role=str(role),
                tg_user_id=tg_user_id,
            )
            audit.record(
                conn,
                actor,
                "user.create",
                "user",
                row["id"],
                summary=f"создан пользователь {login} ({role})",
                diff={"login": login, "role": str(role), "tg_user_id": tg_user_id},
            )
    except psycopg.errors.UniqueViolation as exc:
        raise _unique_error(exc) from exc
    return row


def update_user(user_id: int, changes: dict[str, Any], actor: Actor) -> dict[str, Any]:
    """`changes` — только переданные поля (`display_name`, `role`, `is_active`, `tg_user_id`)."""
    from autoshorts.db.pool import connection

    fields = {k: (str(v) if k == "role" and v is not None else v) for k, v in changes.items()}
    for key in ("display_name", "role", "is_active"):
        if key in fields and fields[key] is None:
            raise ValidationFailed(
                "Поле не может быть пустым",
                details={
                    "errors": [{"loc": ["body", key], "msg": "Поле не может быть null", "type": "none"}]
                },
            )
    if "display_name" in fields:
        fields["display_name"] = fields["display_name"].strip()
    try:
        with connection() as conn:
            before = users_repo.get(conn, user_id, for_update=True)
            if before is None:
                raise NotFound("user", "Пользователь не найден")
            fields = {k: v for k, v in fields.items() if before[k] != v}
            loses_admin = (
                before["role"] == str(Role.ADMIN)
                and before["is_active"]
                and (
                    fields.get("role", str(Role.ADMIN)) != str(Role.ADMIN) or fields.get("is_active") is False
                )
            )
            if loses_admin and users_repo.count_active_admins(conn, exclude_user_id=user_id) == 0:
                raise Conflict(
                    "Нельзя понизить или отключить последнего администратора",
                    code="last_admin",
                )
            row = users_repo.update_fields(conn, user_id, fields)
            if fields.get("is_active") is False:
                sessions_repo.revoke_all_for_user(conn, user_id)
                api_tokens_repo.revoke_all_for_user(conn, user_id)
            if fields:
                audit.record(
                    conn,
                    actor,
                    "user.update",
                    "user",
                    user_id,
                    summary=f"изменён пользователь {before['login']}: {', '.join(sorted(fields))}",
                    diff={k: {"from": before[k], "to": v} for k, v in fields.items()},
                )
    except psycopg.errors.UniqueViolation as exc:
        raise _unique_error(exc) from exc
    return row


def reset_password(user_id: int, new_password: str, actor: Actor) -> None:
    from autoshorts.db.pool import connection

    check_new_password(new_password)
    password_hash = hash_password(new_password)
    with connection() as conn:
        user = users_repo.get(conn, user_id, for_update=True)
        if user is None:
            raise NotFound("user", "Пользователь не найден")
        users_repo.set_password(conn, user_id, password_hash, clear_lock=True)
        revoked = sessions_repo.revoke_all_for_user(conn, user_id)
        audit.record(
            conn,
            actor,
            "user.password_reset",
            "user",
            user_id,
            summary=f"пароль {user['login']} сброшен администратором, блокировка снята, "
            f"отозвано сессий: {revoked}",
        )
