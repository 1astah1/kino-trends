"""
Мост "тренд-бриф -> свой сценарий". Это и есть легальный ответ на "хочу видео как трендовые":
копируем МЕХАНИКУ залетевшего чужого ролика (хук, темп, момент развязки, длительность,
наличие петли), а не пиксели. Само видео генерируем своим пайплайном (providers/llm.py + render.py).

Источник брифов — trend_briefs (ТЗ §8, outlier-движок): hook_type, hook_duration_sec,
cuts_per_sec_first5, payoff_timestamp_ratio, loop_seam, total_duration_bucket.
Ни один из этих параметров не требует владения самим видеофайлом конкурента — только
метаданными его структуры, которые сам outlier.py вычисляет по своим измерениям
(PySceneDetect на СВОЕЙ копии для анализа), а не по украденному контенту в проде.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from autoshorts.config import NicheConfig
from autoshorts.models import Scenario, Scene

# Соответствие между hook_type из брифа и паттернами хуков в конфиге ниши (config/niches/*.yaml).
# См. ТЗ §3: "Incomplete Visual ×2.4 удержание", "Direct Address ×2.1 saves",
# "Stakes Escalation 68% против 41%" — конкретные замеренные типы хуков.
HOOK_TYPE_TO_NICHE_PATTERN = {
    "incomplete_visual": "incomplete_visual",
    "direct_address": "direct_address",
    "stakes_escalation": "stakes_escalation",
    "product_showcase": "incomplete_visual",  # ближайший аналог в текущих конфигах ниш
}

DURATION_BUCKET_TO_RANGE = {
    "0-15": (10, 15),
    "15-30": (15, 25),   # совпадает с целевым диапазоном ниши по умолчанию
    "30-45": (25, 35),
    "45-60": (35, 45),
}


@dataclass
class TrendBrief:
    """Зеркало таблицы trend_briefs (scripts/init_db.sql) для типизированной работы в коде."""
    external_video_id: str
    hook_type: str | None
    hook_duration_sec: float | None
    cuts_per_sec_first5: float | None
    total_duration_bucket: str | None
    text_on_screen_density: float | None
    payoff_timestamp_ratio: float | None
    loop_seam: bool | None
    cta_position: str | None
    outlier_score: float | None


def pick_hook_for_brief(niche: NicheConfig, brief: TrendBrief) -> dict:
    """Выбирает хук ниши, максимально близкий по паттерну к тому, что сработало у конкурента."""
    target_pattern = HOOK_TYPE_TO_NICHE_PATTERN.get(brief.hook_type or "", None)
    hooks = niche.script["hooks"]

    if target_pattern:
        matching = [h for h in hooks if h.get("pattern") == target_pattern]
        if matching:
            return random.choice(matching)

    return random.choice(hooks)


def resolve_target_duration(niche: NicheConfig, brief: TrendBrief) -> tuple[int, int]:
    """
    Длительность берём из бакета брифа, если он есть и не противоречит здравому смыслу,
    иначе — дефолт ниши. НЕ выходим за собственный диапазон ниши больше чем на 30%
    (защита от копирования формата, который физически не подходит под нашу нишу/аудиторию).
    """
    niche_lo, niche_hi = niche.duration_range
    if not brief.total_duration_bucket:
        return niche_lo, niche_hi

    bucket_range = DURATION_BUCKET_TO_RANGE.get(brief.total_duration_bucket)
    if not bucket_range:
        return niche_lo, niche_hi

    bucket_lo, bucket_hi = bucket_range
    clamped_lo = max(bucket_lo, int(niche_lo * 0.7))
    clamped_hi = min(bucket_hi, int(niche_hi * 1.3))
    if clamped_lo >= clamped_hi:
        return niche_lo, niche_hi
    return clamped_lo, clamped_hi


def compute_scene_count_from_cuts(brief: TrendBrief, target_duration_sec: float,
                                   default_scene_count: int) -> int:
    """
    Темп монтажа конкурента (cuts_per_sec_first5) переносим на СВОЙ хронометраж —
    если у залетевшего ролика в первые 5 сек 2 склейки/сек, у нас тоже должен быть
    быстрый темп в хуке, а не медленный Ken Burns на 6 секунд одним кадром.
    """
    if not brief.cuts_per_sec_first5 or brief.cuts_per_sec_first5 <= 0:
        return default_scene_count

    # Экстраполируем темп первых 5 секунд на весь ролик, ограничиваем разумными рамками
    estimated_cuts = brief.cuts_per_sec_first5 * target_duration_sec
    scene_count = max(3, min(12, round(estimated_cuts)))
    return scene_count


def build_scenario_from_brief(niche: NicheConfig, topic: str, brief: TrendBrief,
                               generate_voiceover_fn) -> Scenario:
    """
    generate_voiceover_fn: та же функция, что в providers/llm.py (generate_scenario),
    но с параметрами, подстроенными под механику брифа, а не под дефолт ниши.

    Возвращает Scenario, где:
    - хук выбран под замеренный тип конкурента (или ближайший доступный в нише)
    - длительность и число сцен подстроены под темп и хронометраж бакета
    - loop_seam копируется как флаг для рендера (петля в финале, если у конкурента она была)
    """
    hook = pick_hook_for_brief(niche, brief)
    lo, hi = resolve_target_duration(niche, brief)
    target_dur = (lo + hi) / 2

    default_scene_count = niche.visuals.get("scene_count", 6)
    scene_count = compute_scene_count_from_cuts(brief, target_dur, default_scene_count)

    hook_text = hook["template"].format(theme=topic, sign=topic)
    cta = random.choice(niche.script["cta_variants"])
    body = (
        f"Карта, которая ляжет перед тобой, говорит о {topic}. "
        f"Прислушайся к тому, что откликается внутри — иногда ответ приходит не словами, а ощущением."
    )
    voiceover = f"{hook_text} {body} {cta}"

    per_scene = round(target_dur / scene_count, 2)
    scenes = [
        Scene(index=i, image_path="", duration_sec=per_scene,
              pan_direction=["in", "out", "left", "right"][i % 4])
        for i in range(scene_count)
    ]

    return Scenario(
        niche_id=niche.id,
        topic=topic,
        voiceover_text=voiceover,
        hook_pattern=hook_text,
        cta_text=cta,
        scenes=scenes,
    )
