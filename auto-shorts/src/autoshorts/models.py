"""Доменные модели конвейера. См. ТЗ §2 (архитектура), §9 (generation_events)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "queued"
    SCRIPT_READY = "script_ready"
    VOICED = "voiced"
    RENDERED = "rendered"
    QC_PASSED = "qc_passed"
    QC_FAILED = "qc_failed"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REWORK = "rework"
    REJECTED = "rejected"
    READY_TO_PUBLISH = "ready_to_publish"
    PUBLISHED = "published"


# Фиксированный список причин отказа — без него через полгода нельзя ответить,
# что ломается чаще всего (ТЗ §10).
REJECT_REASONS = [
    "hook_slabyy",              # хук не цепляет за 1.5 сек
    "vizual_artefakty",         # брак генерации (руки, текст, искажения)
    "subtitry_rassinhron",      # рассинхрон после рендера
    "banner_perekryvaet",       # баннер попал на смысловой элемент
    "zvuk_problema",            # клиппинг / тишина / громкость
    "zapreshchennaya_fraza",    # словарный фильтр пропустил
    "dublikat",                 # похоже на уже опубликованное (signature/pHash)
    "ne_ta_dlitelnost",         # вне целевого диапазона / плохой loop seam
    "tehnicheskiy_sboy",         # стадия падала max_attempts раз подряд (см. worker.py MAX_ATTEMPTS)
    "drugoe",
]

MAX_STAGE_ATTEMPTS = 3  # см. ТЗ §9: "платные шаги — максимум 2 попытки", здесь общий потолок ретраев


@dataclass
class Scene:
    index: int
    image_path: str
    duration_sec: float
    pan_direction: str = "in"   # in | out | left | right — для crop-based Ken Burns


@dataclass
class WordTiming:
    word: str
    start_sec: float
    end_sec: float


@dataclass
class Scenario:
    niche_id: str
    topic: str
    voiceover_text: str
    hook_pattern: str
    cta_text: str
    scenes: list[Scene] = field(default_factory=list)


@dataclass
class GenerationEvent:
    job_id: int
    step_name: str
    provider: str
    model: str | None
    units: float | None
    unit_price_usd: float | None
    cost_usd: float
    latency_ms: int | None = None
    http_status: int | None = None


@dataclass
class QCResult:
    passed: bool
    checks: dict[str, bool]
    details: dict[str, str]

    @property
    def failed_checks(self) -> list[str]:
        return [k for k, v in self.checks.items() if not v]
