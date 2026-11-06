"""
Генерация сценария. Mock-режим работает без ключей — для локальной обкатки пайплайна.
Реальный вызов — любой OpenAI-совместимый эндпоинт (GigaChat в т.ч.).
"""
from __future__ import annotations

import random
import time

import httpx

from autoshorts.config import NicheConfig, SETTINGS
from autoshorts.models import Scenario, Scene
from autoshorts.providers.registry import resolve_llm


def _mock_generate_text(niche: NicheConfig, topic: str) -> tuple[str, str, str]:
    """Детерминированная заглушка: собирает текст из шаблонов ниши, без сети и без денег."""
    hook = random.choice(niche.script["hooks"])
    cta = random.choice(niche.script["cta_variants"])
    hook_text = hook["template"].format(theme=topic, sign=topic)
    body = (
        f"Карта, которая ляжет перед тобой, говорит о {topic}. "
        f"Прислушайся к тому, что откликается внутри — иногда ответ приходит не словами, а ощущением."
    )
    return hook_text, body, cta


def _contains_forbidden(text: str, forbidden: list[str]) -> list[str]:
    low = text.lower()
    return [p for p in forbidden if p.lower() in low]


def generate_scenario(niche: NicheConfig, topic: str) -> Scenario:
    spec = resolve_llm(SETTINGS.llm_provider)
    t0 = time.monotonic()

    if spec.adapter == "mock" or not SETTINGS.llm_api_key:
        hook_text, body, cta = _mock_generate_text(niche, topic)
    else:
        # OpenAI-совместимый вызов — тот же путь для gigachat и любого другого адаптера.
        prompt = (
            f"Сценарий шортса для ниши «{niche.id}». Тема: {topic}. "
            f"Тон: {niche.script['tone']}. Длина: {niche.script['word_count'][0]}-{niche.script['word_count'][1]} слов. "
            f"НИКОГДА не используй фразы: {', '.join(niche.forbidden_phrases)}. "
            f"Верни только текст озвучки, без разметки."
        )
        resp = httpx.post(
            f"{spec.default_base_url or SETTINGS.llm_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {SETTINGS.llm_api_key}"},
            json={"model": spec.default_model, "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()["choices"][0]["message"]["content"].strip()
        hook_text = body.split(".")[0] + "."
        cta = random.choice(niche.script["cta_variants"])

    voiceover = f"{hook_text} {body} {cta}"

    # Словарный фильтр — см. ТЗ §4. Блокирует до рендера, а не после.
    hit = _contains_forbidden(voiceover, niche.forbidden_phrases)
    if hit:
        raise ValueError(f"Сценарий содержит запрещённые фразы {hit} — перегенерировать")

    scene_count = niche.visuals.get("scene_count", 6)
    target_dur = sum(niche.duration_range) / 2
    per_scene = round(target_dur / scene_count, 2)
    scenes = [
        Scene(index=i, image_path="", duration_sec=per_scene,
              pan_direction=["in", "out", "left", "right"][i % 4])
        for i in range(scene_count)
    ]

    latency_ms = int((time.monotonic() - t0) * 1000)
    return Scenario(
        niche_id=niche.id,
        topic=topic,
        voiceover_text=voiceover,
        hook_pattern=hook_text,
        cta_text=cta,
        scenes=scenes,
    ), latency_ms
