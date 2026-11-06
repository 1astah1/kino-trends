"""Вход, сессии, CSRF, API-токены, защита от перебора (docs/ARCHITECTURE.md §5.1 «Аутентификация»).

- Cookie `as_session` (HttpOnly, Secure в prod, SameSite=Lax): в БД только sha256 токена.
  Сессия скользящая: idle 7 дней, абсолютный срок 30 дней.
- CSRF double-submit: небезопасные методы требуют `X-CSRF-Token`, равный cookie `as_csrf`
  (не HttpOnly) и токену сессии в БД. Значение отдают `POST /auth/login` и `GET /auth/me`.
- Машины: `Authorization: Bearer ast_…` (CSRF не нужен). Токен в `/api/v1` даёт только чтение
  (GET/HEAD) и не больше прав `operator` — админские разделы по токену недоступны.
- Перебор: 10 неудач за 15 минут → `users.locked_until` (+15 минут), и не больше 5 попыток входа
  в минуту с одного IP (в памяти процесса; API работает одним процессом, `--workers 1`).
  Неудачи считаются по `audit_log` (`auth.login_failed`), поэтому окно переживает рестарт.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from autoshorts.core.errors import (
    Forbidden,
    NotAuthenticated,
    NotFound,
    RateLimited,
    ValidationFailed,
)
from autoshorts.core.security import (
    dummy_verify,
    hash_password,
    hash_token,
    ip_hash,
    is_api_token,
    new_api_token,
    new_csrf_token,
    new_session_token,
    password_needs_rehash,
    validate_password,
    verify_password,
)
from autoshorts.core.timeutil import ensure_utc, isoformat_z, utcnow
from autoshorts.domain.enums import ActorKind, ApiTokenScope, Role
from autoshorts.repos import api_tokens as api_tokens_repo
from autoshorts.repos import audit as audit_repo
from autoshorts.repos import sessions as sessions_repo
from autoshorts.repos import users as users_repo
from autoshorts.services import audit
from autoshorts.services.audit import Actor

SESSION_COOKIE = "as_session"
CSRF_COOKIE = "as_csrf"
CSRF_HEADER = "X-CSRF-Token"

SESSION_IDLE_SEC = 7 * 24 * 3600
SESSION_ABSOLUTE_DAYS = 30
SESSION_TOUCH_SEC = 60  # last_seen_at обновляется не чаще раза в минуту
TOKEN_TOUCH_SEC = 60

LOGIN_FAILURE_LIMIT = 10
LOGIN_FAILURE_WINDOW_MIN = 15
LOCK_MINUTES = 15
LOGIN_IP_LIMIT = 5
LOGIN_IP_WINDOW_SEC = 60.0

FAIL_ACTION = "auth.login_failed"
# после этих действий счётчик неудач начинается заново
RESET_ACTIONS = ("user.password_reset", "auth.password_change", "user.unlock")

USER_AGENT_MAX = 300
SESSION_TOKEN_MAX = 128

ROLE_RANK: dict[Role, int] = {Role.VIEWER: 0, Role.OPERATOR: 1, Role.ADMIN: 2}
API_TOKEN_MAX_ROLE = Role.OPERATOR

_FALLBACK_IP_SALT = "autoshorts-ip-hash-dev"


# ---------------------------------------------------------------- принципал


@dataclass(frozen=True)
class Principal:
    """Кто делает запрос: пользователь по cookie-сессии или по API-токену."""

    kind: ActorKind  # USER (сессия) | API_TOKEN
    user_id: int
    login: str
    display_name: str
    role: Role
    tg_user_id: int | None = None
    session_id: int | None = None
    csrf_token: str | None = None
    token_id: int | None = None
    scopes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_session(self) -> bool:
        return self.kind == ActorKind.USER and self.session_id is not None

    def at_least(self, role: Role) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[role]

    def user_dict(self) -> dict[str, Any]:
        """Схема `CurrentUser`."""
        return {
            "id": self.user_id,
            "login": self.login,
            "display_name": self.display_name,
            "role": self.role,
            "tg_user_id": self.tg_user_id,
        }

    def actor(self, *, request_id: str | None = None, ip_hash: str | None = None) -> Actor:
        return Actor(kind=self.kind, user_id=self.user_id, request_id=request_id, ip_hash=ip_hash)


def _principal_from_row(row: dict[str, Any], **extra: Any) -> Principal:
    return Principal(
        user_id=int(row["user_id"]),
        login=row["login"],
        display_name=row["display_name"],
        role=Role(row["role"]),
        tg_user_id=row["tg_user_id"],
        **extra,
    )


def hash_client_ip(ip: str | None) -> str | None:
    """Обезличенный IP для сессий и аудита (соль — REDIRECT_SALT, в dev — постоянная)."""
    if not ip:
        return None
    from autoshorts.core.config import get_env

    salt = get_env().redirect_salt
    return ip_hash(ip, salt.get_secret_value() if salt else _FALLBACK_IP_SALT)


# ---------------------------------------------------------------- лимит попыток с IP


class SlidingWindowLimiter:
    """Не больше `limit` событий за `window_sec` на ключ. Потокобезопасно, в памяти процесса."""

    def __init__(self, limit: int, window_sec: float) -> None:
        self.limit = limit
        self.window_sec = window_sec
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> float | None:
        """Засчитать попытку. None — можно; число — через сколько секунд можно снова."""
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] >= self.window_sec:
                q.popleft()
            if len(q) >= self.limit:
                return max(self.window_sec - (now - q[0]), 0.1)
            q.append(now)
            if len(self._hits) > 10_000:  # не копить ключи бесконечно
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] >= self.window_sec]:
                    self._hits.pop(k, None)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


login_ip_limiter = SlidingWindowLimiter(LOGIN_IP_LIMIT, LOGIN_IP_WINDOW_SEC)


# ---------------------------------------------------------------- вход и выход


@dataclass(frozen=True)
class LoginResult:
    principal: Principal
    session_token: str
    csrf_token: str


def _count_failures(conn: Any, user_id: int, last_login_at: datetime | None) -> int:
    return audit_repo.count_login_failures(
        conn,
        user_id,
        window_minutes=LOGIN_FAILURE_WINDOW_MIN,
        since=last_login_at,
        failure_action=FAIL_ACTION,
        reset_actions=RESET_ACTIONS,
    )


def _register_failure(conn: Any, user: dict[str, Any], actor: Actor, where: str) -> dict[str, Any]:
    """Записать неудачную проверку пароля; на 10-й за окно — заблокировать вход."""
    audit.record(conn, actor, FAIL_ACTION, "user", user["id"], summary=f"неверный пароль ({where})")
    failures = _count_failures(conn, user["id"], user["last_login_at"])
    lock = LOCK_MINUTES if failures >= LOGIN_FAILURE_LIMIT else None
    row = users_repo.set_login_failures(conn, user["id"], failures, lock_minutes=lock)
    if lock is not None:
        audit.record(
            conn,
            actor,
            "auth.locked",
            "user",
            user["id"],
            summary=f"вход заблокирован на {LOCK_MINUTES} мин после {failures} неудач",
        )
    return row


def _locked_error(locked_until: datetime) -> RateLimited:
    retry = max(int((ensure_utc(locked_until) - utcnow()).total_seconds()) + 1, 1)
    return RateLimited(
        "Слишком много неудачных попыток: вход временно заблокирован",
        details={"locked_until": isoformat_z(locked_until), "retry_after_sec": retry},
    )


def login(
    login_name: str,
    password: str,
    *,
    ip: str | None,
    user_agent: str | None,
    request_id: str | None = None,
) -> LoginResult:
    """Проверить пароль и открыть сессию. 401 — неверно, 429 — лимит с IP или блокировка."""
    from autoshorts.db.pool import connection

    retry = login_ip_limiter.hit(ip or "unknown")
    if retry is not None:
        raise RateLimited(
            "Слишком много попыток входа, подождите минуту",
            details={"retry_after_sec": int(retry) + 1},
        )
    ip_h = hash_client_ip(ip)
    anon = Actor(kind=ActorKind.USER, user_id=None, request_id=request_id, ip_hash=ip_h)
    normalized = login_name.strip().lower()

    error: Exception | None = None
    result: LoginResult | None = None
    with connection() as conn:
        user = users_repo.get_with_password(conn, login=normalized, for_update=True)
        if user is None or not user["is_active"]:
            dummy_verify(password)
            error = NotAuthenticated("Неверный логин или пароль")
        elif user["locked_until"] is not None and ensure_utc(user["locked_until"]) > utcnow():
            error = _locked_error(user["locked_until"])
        elif not verify_password(user["password_hash"], password):
            row = _register_failure(conn, user, anon, "вход")
            if row["locked_until"] is not None and ensure_utc(row["locked_until"]) > utcnow():
                error = _locked_error(row["locked_until"])
            else:
                error = NotAuthenticated("Неверный логин или пароль")
        else:
            new_hash = hash_password(password) if password_needs_rehash(user["password_hash"]) else None
            users_repo.record_login_success(conn, user["id"], new_password_hash=new_hash)
            token, token_h = new_session_token()
            csrf = new_csrf_token()
            session = sessions_repo.insert(
                conn,
                token_hash=token_h,
                csrf_token=csrf,
                user_id=user["id"],
                absolute_days=SESSION_ABSOLUTE_DAYS,
                ip_hash=ip_h,
                user_agent=(user_agent or "")[:USER_AGENT_MAX] or None,
            )
            principal = _principal_from_row(
                {**user, "user_id": user["id"]},
                kind=ActorKind.USER,
                session_id=session["id"],
                csrf_token=csrf,
            )
            audit.record(
                conn,
                principal.actor(request_id=request_id, ip_hash=ip_h),
                "auth.login",
                "user",
                user["id"],
                summary=f"вход {user['login']}",
            )
            result = LoginResult(principal, token, csrf)
    if error is not None:
        raise error
    assert result is not None
    return result


def authenticate_session(token: str | None) -> Principal | None:
    """Principal по значению cookie `as_session`; None — нет такой живой сессии."""
    if not token or len(token) > SESSION_TOKEN_MAX:
        return None
    from autoshorts.db.pool import connection

    with connection() as conn:
        row = sessions_repo.get_active_by_token_hash(conn, hash_token(token), idle_sec=SESSION_IDLE_SEC)
        if row is None:
            return None
        if float(row["idle_for_sec"]) >= SESSION_TOUCH_SEC:
            sessions_repo.touch(conn, row["session_id"])
    return _principal_from_row(
        row, kind=ActorKind.USER, session_id=int(row["session_id"]), csrf_token=row["csrf_token"]
    )


def authenticate_api_token(token: str | None) -> Principal | None:
    """Principal по `Authorization: Bearer ast_…`; роль не выше operator, только чтение."""
    if not token or not is_api_token(token):
        return None
    from autoshorts.db.pool import connection

    with connection() as conn:
        row = api_tokens_repo.get_active_by_hash(conn, hash_token(token))
        if row is None:
            return None
        if row["last_used_at"] is None or float(row["idle_for_sec"]) >= TOKEN_TOUCH_SEC:
            api_tokens_repo.touch(conn, row["token_id"])
    role = Role(row["role"])
    if ROLE_RANK[role] > ROLE_RANK[API_TOKEN_MAX_ROLE]:
        role = API_TOKEN_MAX_ROLE
    return _principal_from_row(
        {**row, "role": role},
        kind=ActorKind.API_TOKEN,
        token_id=int(row["token_id"]),
        scopes=tuple(row["scopes"] or ()),
    )


def logout(principal: Principal, actor: Actor) -> None:
    from autoshorts.db.pool import connection

    if principal.session_id is None:
        return
    with connection() as conn:
        if sessions_repo.revoke(conn, principal.session_id):
            audit.record(conn, actor, "auth.logout", "session", principal.session_id, summary="выход")


def _field_error(field_name: str, message: str, kind: str) -> ValidationFailed:
    return ValidationFailed(
        message, details={"errors": [{"loc": ["body", field_name], "msg": message, "type": kind}]}
    )


def check_new_password(password: str, field_name: str = "new_password") -> None:
    try:
        validate_password(password)
    except ValueError as exc:
        raise _field_error(field_name, str(exc), "password_policy") from exc


def change_password(principal: Principal, current: str, new: str, actor: Actor) -> int:
    """Сменить свой пароль; остальные сессии отзываются. Возвращает число отозванных сессий."""
    from autoshorts.db.pool import connection

    check_new_password(new)
    if new == current:
        raise _field_error("new_password", "Новый пароль совпадает с текущим", "password_reused")
    new_hash = hash_password(new)
    error: Exception | None = None
    revoked = 0
    with connection() as conn:
        user = users_repo.get_with_password(conn, user_id=principal.user_id, for_update=True)
        if user is None:
            raise NotAuthenticated()
        if not verify_password(user["password_hash"], current):
            _register_failure(conn, user, actor, "смена пароля")
            error = _field_error("current_password", "Неверный текущий пароль", "invalid_password")
        else:
            users_repo.set_password(conn, principal.user_id, new_hash, clear_lock=True)
            revoked = sessions_repo.revoke_all_for_user(
                conn, principal.user_id, except_session_id=principal.session_id
            )
            audit.record(
                conn,
                actor,
                "auth.password_change",
                "user",
                principal.user_id,
                summary=f"пароль сменён, отозвано сессий: {revoked}",
            )
    if error is not None:
        raise error
    return revoked


def list_sessions(principal: Principal) -> list[dict[str, Any]]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        rows = sessions_repo.list_active_for_user(conn, principal.user_id, idle_sec=SESSION_IDLE_SEC)
    return [{**r, "current": r["id"] == principal.session_id} for r in rows]


def revoke_session(principal: Principal, session_id: int, actor: Actor) -> bool:
    """Отозвать свою сессию. True — это текущая сессия (нужно стереть cookie)."""
    from autoshorts.db.pool import connection

    with connection() as conn:
        if not sessions_repo.exists_for_user(conn, session_id, principal.user_id):
            raise NotFound("session", "Сессия не найдена")
        if sessions_repo.revoke(conn, session_id, user_id=principal.user_id):
            audit.record(conn, actor, "auth.session_revoke", "session", session_id, summary="сессия отозвана")
    return session_id == principal.session_id


# ---------------------------------------------------------------- API-токены


def list_api_tokens(principal: Principal) -> list[dict[str, Any]]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        return api_tokens_repo.list_for_user(conn, principal.user_id)


def create_api_token(
    principal: Principal,
    *,
    name: str,
    scopes: list[ApiTokenScope],
    expires_at: datetime | None,
    actor: Actor,
) -> dict[str, Any]:
    """Выпустить токен. Полное значение возвращается один раз, в БД — только sha256."""
    from autoshorts.db.pool import connection

    if expires_at is not None and ensure_utc(expires_at) <= utcnow():
        raise _field_error("expires_at", "Срок действия уже прошёл", "expired")
    clean_scopes = sorted({str(s) for s in scopes})
    issued = new_api_token()
    with connection() as conn:
        row = api_tokens_repo.insert(
            conn,
            user_id=principal.user_id,
            name=name.strip() or "token",
            prefix=issued.prefix,
            token_hash=issued.token_hash,
            scopes=clean_scopes,
            expires_at=ensure_utc(expires_at) if expires_at is not None else None,
        )
        audit.record(
            conn,
            actor,
            "api_token.create",
            "api_token",
            row["id"],
            summary=f"выпущен токен {row['prefix']}… ({', '.join(clean_scopes)})",
            diff={"name": row["name"], "scopes": clean_scopes, "token_prefix": row["prefix"]},
            safe_keys=frozenset({"name", "scopes", "token_prefix"}),
        )
    return {"id": row["id"], "token": issued.token}


def revoke_api_token(principal: Principal, token_id: int, actor: Actor) -> None:
    """Отозвать токен (свой; администратор — любой). Повторный отзыв — без ошибки."""
    from autoshorts.db.pool import connection

    with connection() as conn:
        row = api_tokens_repo.get(conn, token_id)
        if row is None or (row["user_id"] != principal.user_id and not principal.at_least(Role.ADMIN)):
            raise NotFound("api_token", "Токен не найден")
        if api_tokens_repo.revoke(conn, token_id):
            audit.record(
                conn,
                actor,
                "api_token.revoke",
                "api_token",
                token_id,
                summary=f"отозван токен {row['prefix']}…",
            )


def require_role(principal: Principal, role: Role) -> None:
    if not principal.at_least(role):
        raise Forbidden("Недостаточно прав для этого действия", details={"required_role": str(role)})
