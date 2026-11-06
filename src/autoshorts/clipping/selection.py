"""Отбор кандидатов целиком: LLM (или mock) либо эвристика → снаппинг → признаки → скоринг →
словарный фильтр ниши → дедупликация → лимит (docs/ARCHITECTURE.md §6.3.3–6.3.5).

Точка входа для стадии `source_select` и для `scripts/clip_local.py`:

    select_clips(transcript, llm=LLMClient | None, niche=NicheProfile | None, config=SelectionConfig())
        → SelectionResult(candidates, mode, label, llm_events, cost_usd, …)

Режимы (поле `mode`, в `label` — человекочитаемая пометка для UI и логов):
    "llm"        — настоящий LLM (LLMClient.from_env или переданный клиент)
    "mock"       — детерминированная заглушка (`llm_select.make_mock_client`), НЕ модель
    "heuristic"  — без LLM вообще (`heuristic_select`), кандидаты помечены `[эвристика без LLM]`

Если `llm=None`, берётся эвристика. Если LLM упал на всех окнах, `fallback_to_heuristic=True`
(по умолчанию) переключает на эвристику и пишет предупреждение; деньги неудачных вызовов всё
равно попадают в `llm_events`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from autoshorts.clipping.features import EnergyTrack
from autoshorts.clipping.heuristic_select import HEURISTIC_LABEL, heuristic_candidates
from autoshorts.clipping.llm_client import LLMClient, LLMError
from autoshorts.clipping.llm_select import FrameProvider, LLMSelectConfig, detail_pass, window_pass
from autoshorts.clipping.scoring import (
    SCENE_SCORING_DEFAULTS,
    ClipCandidate,
    NicheProfile,
    ScoringConfig,
    apply_niche_filter,
    dedupe_overlapping,
    score_candidate,
)
from autoshorts.clipping.snapping import snap_clip_to_words, tighten_pauses
from autoshorts.clipping.transcript import Transcript
from autoshorts.clipping.windows import Markers

SelectMode = Literal["llm", "mock", "heuristic"]


@dataclass(slots=True)
class SelectionConfig:
    # talk — говорящая голова; scene — сцены кино/диалоги: другой промпт, веса, ворота законченности,
    # границы по сменам сцены и кадры в уточняющем проходе (docs: режим задаёт секция `selection` ниши)
    mode: str = "talk"
    target_clip_sec: float = 30.0
    min_clip_sec: float = 15.0
    max_clip_sec: float = 58.0
    target_count: int = 6  # сколько клипов нужно; шортлист detail-прохода = 2 × target_count, ≤ 12
    max_detail: int = 12
    max_candidates: int = 12  # сколько кандидатов показываем оператору
    detail_pass: bool = True
    detail_rejected_mult: float = 0.85
    # снаппинг (§6.3.5)
    snap_search_window: float = 1.5
    snap_max_lead: float = 0.35
    snap_max_tail: float = 0.45
    # джамп-каты: по умолчанию выключены (для музыки их включать нельзя)
    tighten_pauses: bool = False
    tighten_max_gap: float = 0.45
    tighten_keep: float = 0.15
    fallback_to_heuristic: bool = True
    # смены сцены по видео (секунды, по возрастанию): маркеры в промпте и снаппинг границ на склейки
    scene_cuts: list[float] = field(default_factory=list)
    scene_cut_lead: float = 1.2
    scene_cut_tail: float = 1.6
    llm: LLMSelectConfig = field(default_factory=LLMSelectConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)

    def __post_init__(self) -> None:
        # одна длина клипа на все модули
        self.llm.target_clip_sec = self.target_clip_sec
        self.llm.min_clip_sec = self.min_clip_sec
        self.llm.max_clip_sec = self.max_clip_sec
        self.llm.mode = self.mode
        self.scoring.target_clip_sec = self.target_clip_sec
        if self.scoring.hard_max_sec is None or self.scoring.hard_max_sec > self.max_clip_sec:
            self.scoring.hard_max_sec = self.max_clip_sec
        self.scene_cuts = sorted(float(c) for c in self.scene_cuts)

    @classmethod
    def from_options(
        cls,
        options: Mapping[str, Any] | None,
        scoring: Mapping[str, Any] | None = None,
        niche: NicheProfile | None = None,
    ) -> SelectionConfig:
        """Из `sources.options` ({target_clip_sec, max_candidates, …}), весов скоринга из настроек и
        секции `selection` ниши: режим «scene» подменяет пресет скоринга (веса, паузы, ворота), а явные
        значения из настроек и ниши ложатся поверх пресета."""
        opts = dict(options or {})
        kwargs: dict[str, Any] = {}
        for key in (
            "target_clip_sec",
            "min_clip_sec",
            "max_clip_sec",
            "max_candidates",
            "target_count",
            "detail_pass",
            "tighten_pauses",
        ):
            if opts.get(key) is not None:
                kwargs[key] = opts[key]
        mode = str(opts.get("mode") or getattr(niche, "selection_mode", None) or "talk")
        if mode not in ("talk", "scene"):
            mode = "talk"
        merged: dict[str, Any] = dict(SCENE_SCORING_DEFAULTS) if mode == "scene" else {}
        merged.update({k: v for k, v in dict(scoring or {}).items() if v is not None})
        if niche is not None:
            if getattr(niche, "min_payoff", None) is not None:
                merged["min_payoff"] = niche.min_payoff
            if getattr(niche, "min_emotion", None) is not None:
                merged["min_emotion"] = niche.min_emotion
        return cls(mode=mode, scoring=ScoringConfig.from_mapping(merged), **kwargs)


@dataclass(slots=True)
class SelectionResult:
    candidates: list[ClipCandidate]
    mode: SelectMode
    label: str
    llm_events: list[dict[str, Any]] = field(default_factory=list)
    n_requests: int = 0
    cost_usd: float = 0.0
    llm_latency_ms: int = 0
    wall_ms: int = 0
    shortlist_size: int = 0
    dropped: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "label": self.label,
            "n_candidates": len(self.candidates),
            "n_blocked": sum(1 for c in self.candidates if c.blocked),
            "n_requests": self.n_requests,
            "cost_usd": round(self.cost_usd, 6),
            "llm_latency_ms": self.llm_latency_ms,
            "wall_ms": self.wall_ms,
            "shortlist_size": self.shortlist_size,
            "warnings": self.warnings,
            "dropped": self.dropped,
            "candidates": [c.to_dict() for c in self.candidates],
            "llm_events": self.llm_events,
        }


# ---------------------------------------------------------------------- доводка кандидатов


def finalize_candidates(
    cands: Sequence[ClipCandidate],
    transcript: Transcript,
    *,
    config: SelectionConfig,
    niche: NicheProfile | None = None,
    trend_shares: Mapping[str, float] | None = None,
    energy: EnergyTrack | None = None,
) -> list[ClipCandidate]:
    """Снаппинг → (джамп-каты) → признаки и оценка → словарный фильтр. Без дедупликации."""
    words = transcript.words
    out: list[ClipCandidate] = []
    for c in cands:
        raw_s = c.raw_start_sec if c.raw_start_sec is not None else c.start_sec
        raw_e = c.raw_end_sec if c.raw_end_sec is not None else c.end_sec
        snap = snap_clip_to_words(
            words,
            raw_s,
            raw_e,
            search_window=config.snap_search_window,
            max_lead=config.snap_max_lead,
            max_tail=config.snap_max_tail,
            duration=transcript.duration or None,
            scene_cuts=config.scene_cuts,
            cut_lead=config.scene_cut_lead,
            cut_tail=config.scene_cut_tail,
            max_duration=config.max_clip_sec,
        )
        c.start_sec, c.end_sec = snap.start, snap.end
        c.raw_start_sec, c.raw_end_sec = raw_s, raw_e
        eff = None
        if config.tighten_pauses:
            plan = tighten_pauses(
                words, c.start_sec, c.end_sec, max_gap=config.tighten_max_gap, keep=config.tighten_keep
            )
            c.keep_segments = plan.segments
            eff = plan.duration
        score_candidate(
            c, words, cfg=config.scoring, trend_shares=trend_shares, energy=energy, effective_duration=eff
        )
        if c.detail_status == "rejected" and c.final_score is not None:
            c.final_score *= config.detail_rejected_mult
        # Словарные medical/guarantee-флаги — для ВСЕХ кандидатов, не только эвристики и mock:
        # суждение LLM о флагах можно сбить текстом самого транскрипта (чужие данные в промпте).
        apply_niche_filter(c, niche, keyword_flags=True)
        out.append(c)
    return out


def apply_gates(
    cands: Sequence[ClipCandidate], config: SelectionConfig, dropped: list[dict[str, Any]]
) -> list[ClipCandidate]:
    """Ворота законченности (min_payoff/min_emotion): обрывок без развязки и без эмоции в кандидаты не идёт.
    Ручные кандидаты оператора ворота не трогают."""
    kept: list[ClipCandidate] = []
    for c in cands:
        why = None if c.origin == "manual" else config.scoring.gate_reason(c.llm_scores)
        if why is None:
            kept.append(c)
            continue
        dropped.append(
            {
                "start_sec": c.start_sec,
                "end_sec": c.end_sec,
                "final_score": c.final_score,
                "reason": why,
                "origin": c.origin,
                "scores": dict(c.llm_scores),
            }
        )
    return kept


def _rank(
    cands: Sequence[ClipCandidate], config: SelectionConfig, dropped: list[dict[str, Any]], limit: int
) -> list[ClipCandidate]:
    sc = config.scoring
    lost: list[tuple[ClipCandidate, str]] = []
    kept = dedupe_overlapping(
        cands,
        sc.dedupe_overlap,
        spacing_sec=sc.spacing_sec,
        exempt_score=sc.spacing_exempt_score,
        dropped=lost,
    )
    for c, why in lost:
        dropped.append(
            {
                "start_sec": c.start_sec,
                "end_sec": c.end_sec,
                "final_score": c.final_score,
                "reason": why,
                "origin": c.origin,
            }
        )
    for c in kept[limit:]:
        dropped.append(
            {
                "start_sec": c.start_sec,
                "end_sec": c.end_sec,
                "final_score": c.final_score,
                "reason": "limit",
                "origin": c.origin,
            }
        )
    return kept[:limit]


# ---------------------------------------------------------------------- точка входа


def select_clips(
    transcript: Transcript,
    *,
    llm: LLMClient | None = None,
    niche: NicheProfile | None = None,
    config: SelectionConfig | None = None,
    trend_shares: Mapping[str, float] | None = None,
    energy: EnergyTrack | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    frames: FrameProvider | None = None,
) -> SelectionResult:
    """Полный отбор. `on_event(event)` вызывается сразу после каждого вызова LLM (успех или
    ошибка) — стадия пишет по нему `generation_events` отдельной транзакцией (§6.1).
    `frames(start, end)` — кадры фрагмента для уточняющего прохода (режим «сцена»)."""
    config = config or SelectionConfig()
    markers = (
        Markers(scene_cuts=tuple(config.scene_cuts), energy=energy)
        if (config.scene_cuts or energy is not None)
        else None
    )
    t0 = time.monotonic()
    dropped: list[dict[str, Any]] = []
    warnings: list[str] = []
    events: list[dict[str, Any]] = []
    n_req = 0
    shortlist_n = 0
    final: list[ClipCandidate] | None = None
    mode: SelectMode = "heuristic"
    label = f"ЭВРИСТИКА без LLM {HEURISTIC_LABEL}"

    if llm is not None and transcript.sentences:
        mode = "mock" if llm.is_mock else "llm"
        label = (
            "MOCK: детерминированная заглушка, не модель"
            if llm.is_mock
            else f"LLM {llm.model} ({llm.base_url})"
        )
        try:
            wp = window_pass(transcript, llm, cfg=config.llm, niche=niche, on_event=on_event, markers=markers)
            events += wp.events
            warnings += wp.warnings
            n_req += wp.n_requests
            prepared = finalize_candidates(
                wp.candidates,
                transcript,
                config=config,
                niche=niche,
                trend_shares=trend_shares,
                energy=energy,
            )
            prepared = apply_gates(prepared, config, dropped)
            shortlist_cap = min(2 * config.target_count, config.max_detail)
            shortlist = _rank(prepared, config, dropped, shortlist_cap)
            shortlist_n = len(shortlist)
            if config.detail_pass and shortlist:
                dp = detail_pass(
                    transcript,
                    llm,
                    shortlist,
                    cfg=config.llm,
                    niche=niche,
                    on_event=on_event,
                    markers=markers,
                    frames=frames,
                )
                events += dp.events
                warnings += dp.warnings
                n_req += dp.n_requests
                shortlist = finalize_candidates(
                    dp.candidates,
                    transcript,
                    config=config,
                    niche=niche,
                    trend_shares=trend_shares,
                    energy=energy,
                )
                shortlist = apply_gates(shortlist, config, dropped)
            final = _rank(shortlist, config, dropped, config.max_candidates)
        except LLMError as exc:
            events += list(getattr(exc, "events", []) or [])
            if not config.fallback_to_heuristic:
                raise
            warnings.append(f"LLM failed ({type(exc).__name__}: {exc}); fell back to heuristic")
            mode = "heuristic"
            label = f"ЭВРИСТИКА без LLM {HEURISTIC_LABEL} (LLM недоступен)"
            final = None

    if final is None:
        raw = heuristic_candidates(
            transcript,
            target_clip_sec=config.target_clip_sec,
            min_clip_sec=config.min_clip_sec,
            max_clip_sec=config.max_clip_sec,
            energy=energy,
            niche=niche,
            scene=config.mode == "scene",
        )
        prepared = finalize_candidates(
            raw, transcript, config=config, niche=niche, trend_shares=trend_shares, energy=energy
        )
        prepared = apply_gates(prepared, config, dropped)
        final = _rank(prepared, config, dropped, config.max_candidates)
        shortlist_n = len(prepared)

    final.sort(key=lambda c: (c.blocked, -(c.final_score or 0.0)))
    return SelectionResult(
        candidates=final,
        mode=mode,
        label=label,
        llm_events=events,
        n_requests=n_req,
        cost_usd=sum(float(e.get("cost_usd") or 0.0) for e in events),
        llm_latency_ms=sum(int(e.get("latency_ms") or 0) for e in events),
        wall_ms=int((time.monotonic() - t0) * 1000),
        shortlist_size=shortlist_n,
        dropped=dropped,
        warnings=warnings,
    )


__all__ = [
    "SelectMode",
    "SelectionConfig",
    "SelectionResult",
    "apply_gates",
    "finalize_candidates",
    "select_clips",
]
