"""OAuth 2.0 Google для своих YouTube-каналов (docs/ARCHITECTURE.md §6.5, M4-D; автозалив 0019).

Только сеть и разбор ответов, без БД. Клиент OAuth — из секрета куста
(`clusters.youtube_oauth_client_secret`) или общего `YOUTUBE_OAUTH_CLIENT`: JSON, скачанный из
Google Cloud Console (`{"web": {...}}`), или плоский `{"client_id": …, "client_secret": …}`.

    creds = parse_client(get_secret("YOUTUBE_OAUTH_CLIENT"))
    url = authorize_url(creds, redirect_uri=..., state=...)
    tokens = exchange_code(client, creds, code=..., redirect_uri=...)   # refresh_token — один раз
    access = refresh(client, creds, tokens.refresh_token).access_token
    mine = my_channels(client, access)                                    # 1 единица квоты

`state` подписан и зашифрован Fernet-ключами секретов (`SecretCipher`): внутри канал, пользователь
и срок жизни. Колбэк проверяет, что его открыл тот же пользователь, что начинал подключение.
"""

from __future__ import annotations

import json
import secrets as pysecrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from autoshorts.collectors.http import CollectorError, ErrorKind, json_body, request

__all__ = [
    "AUTH_URL",
    "SCOPE_ANALYTICS",
    "SCOPE_MANAGE",
    "SCOPE_READONLY",
    "SCOPE_UPLOAD",
    "SCOPES_UPLOAD",
    "STATE_TTL_SEC",
    "TOKEN_URL",
    "ClientCreds",
    "OAuthState",
    "TokenSet",
    "authorize_url",
    "exchange_code",
    "my_channels",
    "open_state",
    "parse_client",
    "refresh",
    "seal_state",
]

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 — адрес, не секрет
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

SCOPE_UPLOAD = "https://www.googleapis.com/auth/youtube.upload"
SCOPE_READONLY = "https://www.googleapis.com/auth/youtube.readonly"
# force-ssl покрывает upload и даёт videos.delete/update: нужен, чтобы снимать ролики с площадки из консоли
SCOPE_MANAGE = "https://www.googleapis.com/auth/youtube.force-ssl"
# досмотры, «смотрели / пролистали», источники трафика (YouTube Analytics API): по ним учится подбор и оформление
SCOPE_ANALYTICS = "https://www.googleapis.com/auth/yt-analytics.readonly"
SCOPES_UPLOAD = (SCOPE_UPLOAD, SCOPE_READONLY, SCOPE_MANAGE, SCOPE_ANALYTICS)

STATE_TTL_SEC = 15 * 60


@dataclass(frozen=True)
class ClientCreds:
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    expires_in: int
    refresh_token: str | None
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class OAuthState:
    channel_id: str
    user_id: int | None
    expires_at: float


def parse_client(raw: str | None) -> ClientCreds | None:
    """Секрет клиента OAuth → `ClientCreds`; пусто или не тот формат — None."""
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    for key in ("web", "installed"):
        if isinstance(data.get(key), dict):
            data = data[key]
            break
    cid, csec = data.get("client_id"), data.get("client_secret")
    if not isinstance(cid, str) or not isinstance(csec, str) or not cid or not csec:
        return None
    return ClientCreds(cid.strip(), csec.strip())


def authorize_url(
    creds: ClientCreds, *, redirect_uri: str, state: str, scopes: tuple[str, ...] = SCOPES_UPLOAD
) -> str:
    """Ссылка на экран согласия. `access_type=offline` + `prompt=consent` — чтобы пришёл refresh_token."""
    query = {
        "client_id": creds.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent select_account",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(query)}"


def _token_error(resp: httpx.Response, attempts: int) -> CollectorError:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    code = body.get("error") if isinstance(body, dict) else None
    text = body.get("error_description") if isinstance(body, dict) else None
    kind = ErrorKind.AUTH if resp.status_code in (400, 401, 403) else ErrorKind.HTTP
    return CollectorError(
        f"Google OAuth: HTTP {resp.status_code} {code or ''} {text or ''}".strip(),
        kind=kind,
        status=resp.status_code,
        reason=str(code) if code else None,
        http_attempts=attempts,
    )


def _tokens(body: Any, attempts: int) -> TokenSet:
    if not isinstance(body, dict) or not isinstance(body.get("access_token"), str):
        raise CollectorError("Google OAuth: в ответе нет access_token", kind=ErrorKind.BAD_RESPONSE,
                             http_attempts=attempts)
    scope = body.get("scope")
    return TokenSet(
        access_token=body["access_token"],
        expires_in=int(body.get("expires_in") or 3600),
        refresh_token=body.get("refresh_token") if isinstance(body.get("refresh_token"), str) else None,
        scopes=tuple(str(scope).split()) if scope else (),
    )


def exchange_code(client: httpx.Client, creds: ClientCreds, *, code: str, redirect_uri: str) -> TokenSet:
    res = request(
        client,
        "POST",
        TOKEN_URL,
        data={
            "code": code,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    if res.response.status_code != 200:
        raise _token_error(res.response, res.http_attempts)
    return _tokens(json_body(res.response, http_attempts=res.http_attempts), res.http_attempts)


def refresh(client: httpx.Client, creds: ClientCreds, refresh_token: str) -> TokenSet:
    """Новый access_token. Отозванный или протухший refresh_token — `CollectorError(kind=auth,
    reason=invalid_grant)`: канал надо подключить заново."""
    res = request(
        client,
        "POST",
        TOKEN_URL,
        data={
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    if res.response.status_code != 200:
        raise _token_error(res.response, res.http_attempts)
    return _tokens(json_body(res.response, http_attempts=res.http_attempts), res.http_attempts)


def my_channels(client: httpx.Client, access_token: str) -> list[dict[str, str]]:
    """Каналы, к которым выдан токен: `[{id: "UC…", title}]` (`channels.list?mine=true`, 1 единица)."""
    res = request(
        client,
        "GET",
        CHANNELS_URL,
        params={"part": "snippet", "mine": "true", "maxResults": 50},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    body = json_body(res.response, http_attempts=res.http_attempts)
    if res.response.status_code in (401, 403):
        raise CollectorError(
            f"YouTube channels.list: HTTP {res.response.status_code}",
            kind=ErrorKind.AUTH,
            status=res.response.status_code,
            http_attempts=res.http_attempts,
        )
    if res.response.status_code != 200 or not isinstance(body, dict):
        raise CollectorError(
            f"YouTube channels.list: HTTP {res.response.status_code}",
            kind=ErrorKind.HTTP,
            status=res.response.status_code,
            http_attempts=res.http_attempts,
        )
    out = []
    for item in body.get("items") or []:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            title = (item.get("snippet") or {}).get("title") if isinstance(item.get("snippet"), dict) else None
            out.append({"id": item["id"], "title": str(title or "")})
    return out


# ---------------------------------------------------------------- state


def seal_state(cipher: Any, *, channel_id: str, user_id: int | None, ttl_sec: int = STATE_TTL_SEC) -> str:
    """Зашифрованный `state` для ссылки согласия (Fernet-токен, URL-safe)."""
    payload = {
        "c": channel_id,
        "u": user_id,
        "e": int(time.time()) + int(ttl_sec),
        "n": pysecrets.token_urlsafe(8),
    }
    token, _key_id = cipher.encrypt(json.dumps(payload, separators=(",", ":")))
    return token.decode("ascii")


def open_state(cipher: Any, state: str) -> OAuthState | None:
    """Расшифровать `state`; чужой, испорченный или просроченный — None."""
    try:
        data = json.loads(cipher.decrypt(state.encode("ascii")))
    except Exception:  # noqa: BLE001 — любой мусор в state = отказ
        return None
    if not isinstance(data, dict) or not isinstance(data.get("c"), str):
        return None
    expires = float(data.get("e") or 0)
    if expires < time.time():
        return None
    user = data.get("u")
    return OAuthState(channel_id=data["c"], user_id=int(user) if user is not None else None, expires_at=expires)
