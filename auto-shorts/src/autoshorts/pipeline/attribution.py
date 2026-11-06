"""
Атрибуция ролик -> переход -> регистрация (ТЗ §6).
Ключевое ограничение: кириллица в start-параметре Telegram ломается молча — не пропускать её никогда.
YouTube video ID — 11 символов из [A-Za-z0-9_-], идеально укладывается в 64-байтовый лимит.
"""
from __future__ import annotations

import re

from autoshorts.config import SETTINGS

_TG_START_PARAM_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def build_attribution_tag(platform: str, platform_video_id: str) -> str:
    """'yt_dQw4w9WgXcQ' / 'vk_123456789'. Валидирует алфавит ДО того, как метка попадёт в ссылку."""
    prefix = {"youtube": "yt", "vk": "vk"}.get(platform, platform[:2])
    tag = f"{prefix}_{platform_video_id}"
    if not _TG_START_PARAM_RE.match(tag):
        raise ValueError(
            f"Метка атрибуции '{tag}' не проходит по алфавиту Telegram start-параметра "
            f"([A-Za-z0-9_-], до 64 байт) — кириллица или спецсимволы ломают ссылку молча."
        )
    return tag


def build_telegram_deeplink(attribution_tag: str) -> str:
    return f"https://t.me/{SETTINGS.telegram_bot_username}?start={attribution_tag}"


def build_domain_redirect_url(attribution_tag: str) -> str:
    """Домен-редиректор — первичный приёмник (ТЗ §6, воронка «домен + бот параллельно»)."""
    return f"https://{SETTINGS.redirect_domain}/{attribution_tag}"


def build_utm_landing_url(base_landing_url: str, source: str, medium: str, attribution_tag: str) -> str:
    return f"{base_landing_url}?utm_source={source}&utm_medium={medium}&utm_campaign={attribution_tag}"
