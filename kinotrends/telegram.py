"""Отправка сообщений в Telegram через Bot API."""
from __future__ import annotations

import httpx


def send(token: str, chat_id: str, text: str, timeout: float = 20.0) -> tuple[bool, str]:
    if not token or not chat_id:
        return False, "не задан токен или chat_id"
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.post(f"https://api.telegram.org/bot{token}/sendMessage",
                       json={"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True})
        data = r.json()
        return bool(data.get("ok")), "" if data.get("ok") else str(data.get("description"))[:200]
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]


def check(token: str, timeout: float = 15.0) -> tuple[bool, str]:
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.get(f"https://api.telegram.org/bot{token}/getMe")
        d = r.json()
        return bool(d.get("ok")), (d.get("result") or {}).get("username") or str(d.get("description"))[:100]
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]
