"""Конфиг ниши (`niches.config`, `niche_versions.config`; форма `config/niches/*.yaml`).

Один конфиг прошивает все стадии ролика: сценарий, визуал, голос, субтитры, музыку, длительность,
обложку, хештеги (`hashtags.pool` вместо `_NICHE_HASHTAG_POOLS` в коде) и промпты LLM
(`prompts.system`, `prompts.body_hint` — тело про таро перестаёт быть зашитым в код), §3.2.

Правила:
* ключи — латиница snake_case, тексты — по-русски; неизвестный ключ — ошибка (опечатка не
  теряется молча), путь ошибки в стиле `script.hooks[2].template`;
* YAML → схема → YAML без потерь: храним и экспортируем `model_dump(exclude_unset=True)` —
  только то, что было задано, значения по умолчанию подставляет сама модель при чтении;
* `id`/`label` в конфиге необязательны: у ниши они живут в строке `niches`, при экспорте
  подставляются оттуда.

Модуль чистый: не читает файлы и не ходит в БД.
"""

from __future__ import annotations

import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

SLUG_PATTERN = r"^[a-z0-9][a-z0-9_-]{1,39}$"
CODE_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"
HEX_COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"
HASHTAG_PATTERN = r"^#[^\s#]{1,60}$"

__all__ = [
    "CaptionsSection",
    "ConfigIssue",
    "DurationSection",
    "HashtagsSection",
    "HookTemplate",
    "MusicSection",
    "NicheConfig",
    "PromptsSection",
    "ScriptSection",
    "ThumbnailSection",
    "VisualsSection",
    "VoiceSection",
    "config_warnings",
    "dump_config",
    "dump_yaml",
    "issue_path",
    "load_yaml",
    "validate_config",
]


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HookTemplate(_Section):
    pattern: str = Field(pattern=CODE_PATTERN, description="incomplete_visual, direct_address, …")
    template: str = Field(min_length=1, max_length=400, description="Текст хука, можно с {theme}")


class ScriptSection(_Section):
    format: str = Field("generic", pattern=CODE_PATTERN, description="pick_a_card | card_of_day | …")
    tone: str = Field("", max_length=300)
    pacing: str = Field("medium", pattern=CODE_PATTERN)
    word_count: tuple[int, int] = (55, 80)
    hooks: list[HookTemplate] = Field(default_factory=list, max_length=50)
    cta_variants: list[str] = Field(default_factory=list, max_length=50)
    forbidden_phrases: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("word_count")
    @classmethod
    def _word_count(cls, v: tuple[int, int]) -> tuple[int, int]:
        lo, hi = v
        if not (5 <= lo <= hi <= 1000):
            raise ValueError("word_count must satisfy 5 <= min <= max <= 1000")
        return v

    @field_validator("cta_variants", "forbidden_phrases")
    @classmethod
    def _non_empty_items(cls, v: list[str]) -> list[str]:
        if any(not str(item).strip() for item in v):
            raise ValueError("empty strings are not allowed")
        return v


class VisualsSection(_Section):
    style: str = Field("", max_length=600)
    mood: str = Field("", max_length=300)
    subjects: list[str] = Field(default_factory=list, max_length=50)
    avoid: list[str] = Field(default_factory=list, max_length=50)
    motion: Literal["crop_pan", "prerendered_loop"] = "crop_pan"
    scene_count: int = Field(6, ge=1, le=20)


class VoiceSection(_Section):
    pace: str = Field("", max_length=100)
    energy: str = Field("", max_length=300)
    suggested_providers: dict[str, str] = Field(default_factory=dict)

    @field_validator("suggested_providers")
    @classmethod
    def _latin_keys(cls, v: dict[str, str]) -> dict[str, str]:
        bad = [k for k in v if not re.fullmatch(CODE_PATTERN, k)]
        if bad:
            raise ValueError(f"keys must be latin snake_case: {bad}")
        return v


class CaptionsSection(_Section):
    style: str = Field("karaoke_word_highlight", pattern=CODE_PATTERN)
    highlight_color: str = Field("#F2C14E", pattern=HEX_COLOR_PATTERN)
    base_color: str = Field("#FFFFFF", pattern=HEX_COLOR_PATTERN)
    font_weight: str = Field("bold", pattern=CODE_PATTERN)
    position: str = Field("lower_third", pattern=CODE_PATTERN)


class MusicSection(_Section):
    mood: str = Field("", max_length=300)
    energy: str = Field("", max_length=100)


class DurationSection(_Section):
    target_sec: tuple[int, int] = (15, 25)
    loop_seam: bool = True

    @field_validator("target_sec")
    @classmethod
    def _range(cls, v: tuple[int, int]) -> tuple[int, int]:
        lo, hi = v
        if not (3 <= lo <= hi <= 180):
            raise ValueError("target_sec must satisfy 3 <= min <= max <= 180")
        return v


class ThumbnailSection(_Section):
    style: str = Field("", max_length=600)
    text_position: str = Field("top", pattern=CODE_PATTERN)


class HashtagsSection(_Section):
    """Пул хештегов ниши: в пакет заливки берётся `per_video` случайных + все `fixed`."""

    pool: list[str] = Field(default_factory=list, max_length=100)
    per_video: int = Field(3, ge=0, le=15)
    fixed: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("pool", "fixed")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        bad = [t for t in v if not re.fullmatch(HASHTAG_PATTERN, t)]
        if bad:
            raise ValueError(f"hashtags must look like #слово without spaces: {bad}")
        return v


class SelectionSection(_Section):
    """Отбор моментов. `talk` — говорящая голова (хук → мысль → вывод), `scene` — сцены кино и сериалов:
    законченные диалоги с развязкой или клиффхэнгером, границы по сменам сцены, кадры в уточняющем проходе."""

    mode: Literal["talk", "scene"] = "talk"
    min_payoff: float | None = Field(None, ge=0, le=10, description="Ворота законченности (подоценка payoff)")
    min_emotion: float | None = Field(None, ge=0, le=10, description="Ворота накала (подоценка emotion)")
    frames_in_detail: bool = Field(True, description="Прикладывать кадры сцены к уточняющему запросу LLM")


class PromptsSection(_Section):
    """Промпты LLM ниши. Пусто — общий промпт стадии `generate_script`."""

    system: str | None = Field(None, max_length=8000)
    body_hint: str | None = Field(None, max_length=4000)
    title_hint: str | None = Field(None, max_length=2000)


class NicheConfig(_Section):
    """Полный конфиг ниши. Все секции необязательны: пропущенная берёт значения по умолчанию."""

    id: str | None = Field(None, pattern=SLUG_PATTERN)
    label: str | None = Field(None, min_length=1, max_length=100)
    script: ScriptSection = Field(default_factory=ScriptSection)
    visuals: VisualsSection = Field(default_factory=VisualsSection)
    voice: VoiceSection = Field(default_factory=VoiceSection)
    captions: CaptionsSection = Field(default_factory=CaptionsSection)
    music: MusicSection = Field(default_factory=MusicSection)
    duration: DurationSection = Field(default_factory=DurationSection)
    thumbnail: ThumbnailSection = Field(default_factory=ThumbnailSection)
    hashtags: HashtagsSection = Field(default_factory=HashtagsSection)
    prompts: PromptsSection = Field(default_factory=PromptsSection)
    selection: SelectionSection = Field(default_factory=SelectionSection)

    @model_validator(mode="after")
    def _forbidden_not_in_templates(self) -> NicheConfig:
        # хук или CTA ниши, который сам нарушает словарный фильтр, — ошибка конфига
        phrases = [p.lower() for p in self.script.forbidden_phrases]
        texts = [h.template for h in self.script.hooks] + list(self.script.cta_variants)
        for text in texts:
            low = text.lower()
            hit = next((p for p in phrases if p and p in low), None)
            if hit is not None:
                raise ValueError(f"hook/cta text contains forbidden phrase {hit!r}")
        return self

    @property
    def forbidden_phrases(self) -> list[str]:
        return list(self.script.forbidden_phrases)

    @property
    def duration_range(self) -> tuple[int, int]:
        return self.duration.target_sec


# ---------------------------------------------------------------- проверки и (де)сериализация


class ConfigIssue(BaseModel):
    path: str
    msg: str


def issue_path(loc: tuple[Any, ...] | list[Any]) -> str:
    """`('script', 'hooks', 2, 'template')` → `script.hooks[2].template`."""
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += ("." if out else "") + str(part)
    return out or "$"


def validate_config(data: Any) -> tuple[NicheConfig | None, list[ConfigIssue]]:
    """Проверить конфиг. → (модель, []) или (None, ошибки по путям)."""
    if not isinstance(data, dict):
        return None, [ConfigIssue(path="$", msg="config must be a mapping")]
    try:
        return NicheConfig.model_validate(data), []
    except ValidationError as exc:
        issues = []
        for err in exc.errors():
            loc = tuple(p for p in err.get("loc", ()) if p not in ("function-after", "function-before"))
            msg = str(err.get("msg", ""))
            if msg.startswith("Value error, "):
                msg = msg[len("Value error, ") :]
            issues.append(ConfigIssue(path=issue_path(loc), msg=msg))
        return None, issues


def config_warnings(cfg: NicheConfig) -> list[ConfigIssue]:
    """Не ошибки, но стоит поправить: пустой словарный фильтр, нет хуков, нет хештегов."""
    out: list[ConfigIssue] = []
    if not cfg.script.hooks:
        out.append(ConfigIssue(path="script.hooks", msg="Нет шаблонов хука: сценарий пишется без подсказки"))
    if not cfg.script.cta_variants:
        out.append(ConfigIssue(path="script.cta_variants", msg="Нет вариантов CTA"))
    if not cfg.script.forbidden_phrases:
        out.append(
            ConfigIssue(path="script.forbidden_phrases", msg="Словарный фильтр пуст: площадки ловят обещания")
        )
    if not cfg.hashtags.pool and not cfg.hashtags.fixed:
        out.append(ConfigIssue(path="hashtags.pool", msg="Нет хештегов: в пакет пойдёт только #<ниша>"))
    if cfg.hashtags.pool and cfg.hashtags.per_video > len(cfg.hashtags.pool):
        out.append(ConfigIssue(path="hashtags.per_video", msg="per_video больше размера пула: возьмутся все"))
    return out


def dump_config(cfg: NicheConfig) -> dict[str, Any]:
    """JSON для БД и экспорта: только заданные поля (без потерь и без дописанных дефолтов)."""
    return cfg.model_dump(mode="json", exclude_unset=True)


def load_yaml(text: str) -> dict[str, Any]:
    """Разобрать YAML ниши (загрузка оператора: без `*alias`). Не словарь / битый YAML → ValueError."""
    from autoshorts.core.yamlsafe import load_untrusted_yaml

    try:
        data = load_untrusted_yaml(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML не разбирается: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError("YAML ниши должен быть словарём верхнего уровня")
    return data


def dump_yaml(config: dict[str, Any], *, niche_id: str | None = None, label: str | None = None) -> str:
    """Конфиг → YAML формы config/niches/*.yaml (id и label — первыми, из строки ниши)."""
    head: dict[str, Any] = {}
    if niche_id or config.get("id"):
        head["id"] = niche_id or config.get("id")
    if label or config.get("label"):
        head["label"] = label or config.get("label")
    body = {k: v for k, v in config.items() if k not in ("id", "label")}
    return yaml.safe_dump(
        {**head, **body}, allow_unicode=True, sort_keys=False, default_flow_style=None, width=100
    )
