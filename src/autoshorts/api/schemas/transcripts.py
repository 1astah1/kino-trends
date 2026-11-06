"""Транскрипт источника для студии нарезки (§5.2 `GET /sources/{source_id}/transcript`, §6.3.2).

Формат — компактный контракт движка `clipping/transcript.py` (`Transcript.to_dict()`), ключи короткие,
потому что слов в часе — десятки тысяч: слово `{w, s, e, sid[, p]}`, предложение
`{sid, s, e, text, w0, w1}` (`w0..w1` — индексы слов включительно; `sid` с 0 подряд).
Время — секунды от начала источника, 3 знака.

Правка границ кандидата «кликом по слову» передаёт индексы слов (`start_word_idx`, `end_word_idx`)
этого списка — см. `api/schemas/clipping.py::ClipCandidatePatch`.

`?format=srt|vtt` отдаёт текст (`text/plain; charset=utf-8`), по предложениям.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from autoshorts.api.schemas.common import ApiModel

__all__ = ["SourceTranscript", "TranscriptFormat", "TranscriptSentence", "TranscriptWord"]


class TranscriptFormat(StrEnum):
    JSON = "json"
    SRT = "srt"
    VTT = "vtt"


class TranscriptWord(ApiModel):
    w: str = Field(description="Слово с пунктуацией")
    s: float = Field(description="Начало, с")
    e: float = Field(description="Конец, с")
    sid: int = Field(description="Предложение")
    p: float | None = Field(default=None, description="Уверенность ASR, если есть")


class TranscriptSentence(ApiModel):
    sid: int
    s: float
    e: float
    text: str
    w0: int = Field(description="Индекс первого слова")
    w1: int = Field(description="Индекс последнего слова (включительно)")


class SourceTranscript(ApiModel):
    source_id: int
    language: str
    duration_sec: float
    provider_key: str
    model: str | None
    words: list[TranscriptWord]
    sentences: list[TranscriptSentence]
