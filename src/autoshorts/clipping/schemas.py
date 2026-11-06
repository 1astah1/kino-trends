"""Схемы ответа LLM для отбора хайлайтов (docs/ARCHITECTURE.md §6.3.3).

LLM получает пронумерованные предложения `[sid] (мм:сс) текст` и возвращает диапазоны sid.
Ключи строго по спецификации:

    start_sid, end_sid, hook_sid, title(≤90), hook_text(≤140), why(≤300), hook_type(enum),
    scores{hook, standalone, payoff, emotion, niche_fit, novelty: 0..10}, needs_context,
    content_flags(enum)

Валидация терпима к мелким огрехам модели, чтобы из-за одного лишнего символа не терять
весь ответ: длинные строки обрезаются, оценки зажимаются в 0..10, неизвестный `hook_type`
становится `other`, неизвестные флаги отбрасываются. Жёсткие ошибки (нет обязательного
ключа, sid не число, JSON битый) валидацию не проходят — тогда `llm_client.chat_json` делает
repair-запрос.

`wire_schema(model)` строит JSON Schema для `response_format=json_schema` (strict): все
ссылки раскрыты, `additionalProperties: false`, все поля обязательны.
"""

from __future__ import annotations

import copy
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TITLE_MAX = 90
HOOK_TEXT_MAX = 140
WHY_MAX = 300
SCORE_KEYS = ("hook", "standalone", "payoff", "emotion", "niche_fit", "novelty")


class HookType(StrEnum):
    """Тип хука в первом предложении клипа. Пересекается с `hook_type` брифов трендоскаута."""

    QUESTION = "question"  # вопрос зрителю или риторический
    BOLD_CLAIM = "bold_claim"  # смелое или спорное утверждение
    SURPRISING_FACT = "surprising_fact"  # неожиданный факт, цифра
    STORY = "story"  # начало истории: «однажды…», «я как-то…»
    CONTRARIAN = "contrarian"  # «все думают X, а на самом деле Y»
    HOW_TO = "how_to"  # «как сделать…», инструкция
    MISTAKE_WARNING = "mistake_warning"  # «главная ошибка…», предупреждение
    DIRECT_ADDRESS = "direct_address"  # «если ты…», обращение к зрителю
    STAKES_ESCALATION = "stakes_escalation"  # ставки растут: «и тут всё пошло не так»
    EMOTIONAL = "emotional"  # сильная эмоция, смех, признание
    HUMOR = "humor"  # шутка, панчлайн
    OTHER = "other"


class ContentFlag(StrEnum):
    """Флаги содержания от LLM. `BLOCKING_FLAGS` блокируют кандидата до разблокировки оператором."""

    MEDICAL_CLAIM = "medical_claim"  # обещание лечения, совет вместо врача
    GUARANTEE_CLAIM = "guarantee_claim"  # гарантия результата, «100%», «точно сбудется»
    FINANCIAL_ADVICE = "financial_advice"  # инвестиционные советы, обещания дохода
    LEGAL_ADVICE = "legal_advice"
    PROFANITY = "profanity"  # мат
    POLITICS = "politics"
    VIOLENCE = "violence"
    SEXUAL = "sexual"
    HATE = "hate"  # оскорбления групп
    DANGEROUS_ACT = "dangerous_act"  # опасные трюки, самоповреждение
    PERSONAL_DATA = "personal_data"  # телефоны, адреса, имена частных лиц
    ADVERTISING = "advertising"  # чужая реклама или интеграция внутри фрагмента


BLOCKING_FLAGS: frozenset[str] = frozenset({ContentFlag.MEDICAL_CLAIM, ContentFlag.GUARANTEE_CLAIM})
# Флаг, который ставит словарный фильтр ниши (не LLM): тоже блокирует.
FORBIDDEN_PHRASE_FLAG = "forbidden_phrase"


def _clip_str(value: Any, limit: int) -> Any:
    if value is None:
        return ""
    if isinstance(value, str):
        value = " ".join(value.split())
        if len(value) > limit:
            cut = value[: limit - 1].rstrip()
            return cut + "…"
    return value


class HighlightScores(BaseModel):
    """Подоценки LLM 0..10 (§6.3.4, формула `llm`)."""

    model_config = ConfigDict(extra="ignore")

    hook: float = Field(ge=0, le=10, description="Сила хука в первые 1–3 секунды")
    standalone: float = Field(ge=0, le=10, description="Понятно без предыдущего контекста")
    payoff: float = Field(ge=0, le=10, description="Есть развязка: ответ, вывод, панчлайн")
    emotion: float = Field(ge=0, le=10, description="Эмоциональный заряд")
    niche_fit: float = Field(ge=0, le=10, description="Польза и попадание в нишу канала")
    novelty: float = Field(ge=0, le=10, description="Новизна, неожиданность")

    @field_validator(*SCORE_KEYS, mode="before")
    @classmethod
    def _clamp(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip().replace(",", ".")
            try:
                v = float(v)
            except ValueError:
                return v  # пусть валидатор типа выдаст понятную ошибку
        if isinstance(v, int | float) and not isinstance(v, bool):
            return min(10.0, max(0.0, float(v)))
        return v

    def as_dict(self) -> dict[str, float]:
        return {k: float(getattr(self, k)) for k in SCORE_KEYS}


class HighlightCandidate(BaseModel):
    """Один кандидат от LLM. Диапазон sid включительный: [start_sid, end_sid]."""

    model_config = ConfigDict(extra="ignore")

    start_sid: int = Field(ge=0, description="Первое предложение клипа")
    end_sid: int = Field(ge=0, description="Последнее предложение клипа (включительно)")
    hook_sid: int = Field(ge=0, description="Предложение с главным хуком, обычно start_sid")
    title: str = Field(max_length=TITLE_MAX, description="Свой заголовок ролика, до 90 символов")
    hook_text: str = Field(max_length=HOOK_TEXT_MAX, description="Текст хука для экрана, до 140 символов")
    why: str = Field(max_length=WHY_MAX, description="Почему фрагмент сработает, до 300 символов")
    hook_type: HookType
    scores: HighlightScores
    needs_context: bool = Field(description="true, если без предыдущего контекста непонятно")
    content_flags: list[ContentFlag] = Field(default_factory=list)

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, v: Any) -> Any:
        return _clip_str(v, TITLE_MAX)

    @field_validator("hook_text", mode="before")
    @classmethod
    def _hook_text(cls, v: Any) -> Any:
        return _clip_str(v, HOOK_TEXT_MAX)

    @field_validator("why", mode="before")
    @classmethod
    def _why(cls, v: Any) -> Any:
        return _clip_str(v, WHY_MAX)

    @field_validator("hook_type", mode="before")
    @classmethod
    def _hook_type(cls, v: Any) -> Any:
        if isinstance(v, str):
            key = v.strip().lower().replace("-", "_").replace(" ", "_")
            return key if key in HookType._value2member_map_ else HookType.OTHER.value
        return v

    @field_validator("content_flags", mode="before")
    @classmethod
    def _flags(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        if isinstance(v, list):
            out: list[str] = []
            for item in v:
                if not isinstance(item, str):
                    continue
                key = item.strip().lower().replace("-", "_").replace(" ", "_")
                if key in ContentFlag._value2member_map_ and key not in out:
                    out.append(key)
            return out
        return v

    @field_validator("needs_context", mode="before")
    @classmethod
    def _bool(cls, v: Any) -> Any:
        if isinstance(v, str):
            low = v.strip().lower()
            if low in {"true", "yes", "да", "1"}:
                return True
            if low in {"false", "no", "нет", "0", ""}:
                return False
        return v

    @model_validator(mode="after")
    def _order(self) -> HighlightCandidate:
        if self.end_sid < self.start_sid:
            self.start_sid, self.end_sid = self.end_sid, self.start_sid
        if not (self.start_sid <= self.hook_sid <= self.end_sid):
            self.hook_sid = self.start_sid
        return self


class HighlightResponse(BaseModel):
    """Ответ окна или detail-прохода: список кандидатов (может быть пустым)."""

    model_config = ConfigDict(extra="ignore")

    candidates: list[HighlightCandidate] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _accept_bare_list(cls, data: Any) -> Any:
        # Некоторые модели в json_object/plain возвращают сразу массив или один объект.
        if isinstance(data, list):
            return {"candidates": data}
        if isinstance(data, dict) and "candidates" not in data and "start_sid" in data:
            return {"candidates": [data]}
        return data


# ---------------------------------------------------------------------- JSON Schema для провайдера


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            merged = {k: v for k, v in node.items() if k != "$ref"}
            target = _inline_refs(copy.deepcopy(defs[name]), defs)
            target.update({k: _inline_refs(v, defs) for k, v in merged.items()})
            return target
        return {k: _inline_refs(v, defs) for k, v in node.items() if k not in ("$defs", "default")}
    if isinstance(node, list):
        return [_inline_refs(v, defs) for v in node]
    return node


# strict-режим OpenAI не принимает длины строк; лимиты остаются в description и в pydantic
_UNSUPPORTED_STRICT = ("title", "maxLength", "minLength")


def _strictify(node: Any) -> Any:
    if isinstance(node, dict):
        for key in _UNSUPPORTED_STRICT:
            if not isinstance(node.get(key), dict):
                node.pop(key, None)
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"].keys())
        for v in node.values():
            _strictify(v)
    elif isinstance(node, list):
        for v in node:
            _strictify(v)
    return node


def wire_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema модели для `response_format={"type":"json_schema", ... "strict": true}`."""
    raw = model.model_json_schema()
    defs = raw.get("$defs", {})
    return _strictify(_inline_refs(raw, defs))


__all__ = [
    "BLOCKING_FLAGS",
    "FORBIDDEN_PHRASE_FLAG",
    "HOOK_TEXT_MAX",
    "SCORE_KEYS",
    "TITLE_MAX",
    "WHY_MAX",
    "ContentFlag",
    "HighlightCandidate",
    "HighlightResponse",
    "HighlightScores",
    "HookType",
    "wire_schema",
]
