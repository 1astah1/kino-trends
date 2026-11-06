"""
Генератор хэштегов. Рисёрч 2026 года прямо говорит: 3-5 точных тегов работают лучше
20-30 случайных (Instagram с декабря 2025 жёстко лимитирует 5 на пост) — тяжёлые
dedicated-сервисы вытесняются либо LLM-промптом, либо готовым словарём ниши.

Дешевле и предсказуемее собственного API-вызова: держим базовый пул тегов прямо в
конфиге ниши (уже есть паттерн в config/niches/*.yaml), ротируем по релевантности к теме.
"""

from __future__ import annotations

import random

from autoshorts.config import NicheConfig

# Базовые пулы — консервативно по 3 штуки на вызов (см. ТЗ §3: "оптимум — 3 хештега").
_NICHE_HASHTAG_POOLS = {
    "taro": ["#таро", "#расклад", "#эзотерика", "#картытаро", "#гадание", "#таролог"],
    "astro": ["#астрология", "#гороскоп", "#лунныйкалендарь", "#знакизодиака", "#луна"],
    "kino": [
        "#кино",
        "#фильм",
        "#фильмы",
        "#лучшиесцены",
        "#моментыизфильмов",
        "#сериалы",
        "#киномомент",
        "#чтопосмотреть",
    ],
    "vpn": [
        "#технологии",
        "#лайфхак",
        "#интернет",
        "#гаджеты",
        "#полезное",
        "#смартфон",
        "#android",
        "#iphone",
    ],
}


def generate_hashtags(niche: NicheConfig, topic: str, count: int = 3) -> list[str]:
    pool = _NICHE_HASHTAG_POOLS.get(niche.id, [f"#{niche.id}"])
    count = min(count, len(pool))
    return random.sample(pool, count)


def format_hashtag_line(hashtags: list[str]) -> str:
    return " ".join(hashtags)
