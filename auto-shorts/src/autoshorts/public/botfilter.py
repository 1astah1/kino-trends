"""Боты, превьюеры ссылок и скрипты (docs/ARCHITECTURE.md §5.3).

HEAD-запросы и User-Agent с маркерами ниже пишутся в `clicks` с `is_bot=true` и в статистику не
идут. Пустой User-Agent живые браузеры не шлют — тоже бот.
"""

from __future__ import annotations

BOT_UA_MARKERS: tuple[str, ...] = tuple(
    m.lower()
    for m in (
        "bot",
        "crawler",
        "spider",
        "preview",
        "TelegramBot",
        "vkShare",
        "facebookexternalhit",
        "WhatsApp",
        "Discordbot",
        "Slackbot",
        "Googlebot",
        "YandexBot",
        "curl",
        "python-httpx",
        "python-requests",
        "wget",
        "Go-http-client",
        "okhttp",
        "headless",
        "lighthouse",
        "embedly",
        "skypeuripreview",
        "outbrain",
        "pinterest",
    )
)


def is_bot_user_agent(user_agent: str | None) -> bool:
    ua = (user_agent or "").strip().lower()
    if not ua:
        return True
    return any(marker in ua for marker in BOT_UA_MARKERS)


def is_bot_request(method: str, user_agent: str | None) -> bool:
    """HEAD (проверка ссылки мессенджером) или бот по UA."""
    return method.upper() == "HEAD" or is_bot_user_agent(user_agent)


__all__ = ["BOT_UA_MARKERS", "is_bot_request", "is_bot_user_agent"]
