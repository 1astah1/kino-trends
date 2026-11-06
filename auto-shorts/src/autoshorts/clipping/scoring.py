"""Скоринг кандидатов, дедупликация и словарный фильтр ниши (docs/ARCHITECTURE.md §6.3.3–6.3.4).

Формула (веса в `ScoringConfig`, их можно переопределить из настроек):

    llm     = (0.30·hook + 0.20·standalone + 0.20·payoff + 0.10·emotion + 0.15·niche_fit + 0.05·novelty) / 10
    feat    = mean(rate_fit(speech_rate 2.3–3.5 wps), 1 − 3·dead_air_ratio,
                   first_word_latency < 0.3, ends_on_terminal_punct)
    len_fit = треугольник вокруг target_clip_sec (30 ± 15 с), 0 за жёстким потолком (VK 58 с)
    trend   = доля hook_type среди брифов ниши за 30 дней, взвешенная по outlier_score
              (нормируется на самую частую долю: лидер = 1; без данных — нейтральные 0,5)
    final   = 0.55·llm + 0.20·feat + 0.15·len_fit + 0.10·trend ; needs_context → ×0.7

`dedupe_overlapping(0.5)`: жадно по убыванию final; кандидат выбрасывается, если перекрывается
с уже принятым больше чем на 50 % более короткого из двух, или если final < 0.8 и ближе 3 минут
(по центрам) к уже принятому. Заблокированные кандидаты идут после незаблокированных и не
вытесняют их.

Блокировка: флаги `medical_claim`, `guarantee_claim` (от LLM или словарной страховки) и
`forbidden_phrase` (словарь ниши). Заблокированный кандидат остаётся в списке с
`blocked=True`, пока оператор явно не снимет флаг (`unblock_flags` в PATCH /clip-candidates).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Literal

from autoshorts.clipping.features import ClipFeatures, EnergyTrack, compute_features
from autoshorts.clipping.schemas import BLOCKING_FLAGS, FORBIDDEN_PHRASE_FLAG, SCORE_KEYS
from autoshorts.clipping.transcript import Word

Origin = Literal["llm", "mock", "heuristic", "manual"]

DEFAULT_LLM_WEIGHTS: dict[str, float] = {
    "hook": 0.30,
    "standalone": 0.20,
    "payoff": 0.20,
    "emotion": 0.10,
    "niche_fit": 0.15,
    "novelty": 0.05,
}
DEFAULT_FINAL_WEIGHTS: dict[str, float] = {"llm": 0.55, "feat": 0.20, "len_fit": 0.15, "trend": 0.10}
# Режим «сцена» (кино, сериалы, диалоги): главное — законченность (payoff) и накал (emotion), а не хук-вопрос.
SCENE_LLM_WEIGHTS: dict[str, float] = {
    "hook": 0.20,
    "standalone": 0.15,
    "payoff": 0.25,
    "emotion": 0.25,
    "niche_fit": 0.10,
    "novelty": 0.05,
}
SCENE_SCORING_DEFAULTS: dict[str, Any] = {
    "llm_weights": SCENE_LLM_WEIGHTS,
    # в кино паузы и медленная речь — норма, а не «мёртвый воздух»
    "rate_lo": 1.0,
    "rate_hi": 3.8,
    "dead_air_k": 1.5,
    "latency_max": 0.6,
    "needs_context_mult": 0.6,
    # разные сцены фильма не должны быть соседями по времени
    "spacing_sec": 240.0,
    # ворота законченности: без развязки/клиффхэнгера и без эмоции сцена не идёт даже в кандидаты
    "min_payoff": 5.0,
    "min_emotion": 4.0,
}
_REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------- настройки


@dataclass(slots=True)
class ScoringConfig:
    llm_weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_LLM_WEIGHTS))
    final_weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_FINAL_WEIGHTS))
    needs_context_mult: float = 0.7
    rate_lo: float = 2.3
    rate_hi: float = 3.5
    rate_falloff: float = 1.0  # за пределами диапазона rate_fit линейно падает до 0 за 1 wps
    dead_air_k: float = 3.0
    latency_max: float = 0.3
    target_clip_sec: float = 30.0
    len_tolerance_sec: float = 15.0
    hard_max_sec: float | None = 58.0  # VK жёстко ≤ 58 с
    trend_neutral: float = 0.5
    dedupe_overlap: float = 0.5
    spacing_sec: float = 180.0
    spacing_exempt_score: float = 0.8
    # ворота по подоценкам LLM/эвристики (None — выключено); режим «сцена» включает их пресетом
    min_payoff: float | None = None
    min_emotion: float | None = None

    def gate_reason(self, scores: Mapping[str, float]) -> str | None:
        """Почему кандидат не проходит ворота законченности (или None)."""
        if self.min_payoff is not None and float(scores.get("payoff", 0.0)) < self.min_payoff:
            return "weak_payoff"
        if self.min_emotion is not None and float(scores.get("emotion", 0.0)) < self.min_emotion:
            return "weak_emotion"
        return None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> ScoringConfig:
        """Настройки из словаря (settings): неизвестные ключи игнорируются, веса сливаются."""
        cfg = cls()
        if not data:
            return cfg
        names = {f.name for f in fields(cls)}
        for key, val in data.items():
            if key not in names or val is None:
                continue
            if key in ("llm_weights", "final_weights"):
                merged = dict(getattr(cfg, key))
                merged.update({k: float(v) for k, v in dict(val).items()})
                setattr(cfg, key, merged)
            else:
                setattr(cfg, key, val)
        return cfg


# ---------------------------------------------------------------------- кандидат


@dataclass(slots=True)
class ClipCandidate:
    """Кандидат клипа. Поля `to_row()` совпадают с таблицей clip_candidates (§4.2, 0005)."""

    start_sid: int | None
    end_sid: int | None
    hook_sid: int | None
    start_sec: float
    end_sec: float
    title: str = ""
    hook_text: str = ""
    hook_type: str = "other"
    reason: str = ""
    llm_scores: dict[str, float] = field(default_factory=dict)
    needs_context: bool = False
    content_flags: list[str] = field(default_factory=list)
    origin: Origin = "llm"
    transcript_excerpt: str = ""
    features: dict[str, Any] = field(default_factory=dict)
    trend_prior: float | None = None
    final_score: float | None = None
    score_parts: dict[str, float] = field(default_factory=dict)
    forbidden_hits: list[dict[str, Any]] = field(default_factory=list)
    unblocked_flags: list[str] = field(default_factory=list)
    detail_status: Literal["none", "refined", "rejected", "error"] = "none"
    raw_start_sec: float | None = None  # границы предложений до снаппинга
    raw_end_sec: float | None = None
    keep_segments: list[tuple[float, float]] | None = None  # план tighten_pauses (если включён)

    @property
    def duration(self) -> float:
        return self.end_sec - self.start_sec

    @property
    def block_reasons(self) -> list[str]:
        blocking = set(BLOCKING_FLAGS) | {FORBIDDEN_PHRASE_FLAG}
        return [f for f in self.content_flags if f in blocking and f not in self.unblocked_flags]

    @property
    def blocked(self) -> bool:
        return bool(self.block_reasons)

    def to_row(self) -> dict[str, Any]:
        """Колонки clip_candidates. `forbidden_hits` и детали скоринга лежат в features."""
        feats = dict(self.features)
        if self.score_parts:
            feats["score_parts"] = {k: round(v, 4) for k, v in self.score_parts.items()}
        if self.forbidden_hits:
            feats["forbidden_hits"] = self.forbidden_hits
        if self.keep_segments is not None:
            feats["keep_segments"] = [[round(a, 3), round(b, 3)] for a, b in self.keep_segments]
        feats["origin"] = self.origin
        feats["detail_status"] = self.detail_status
        return {
            "start_sid": self.start_sid,
            "end_sid": self.end_sid,
            "hook_sid": self.hook_sid,
            "start_sec": round(self.start_sec, 2),
            "end_sec": round(self.end_sec, 2),
            "title": self.title,
            "hook_text": self.hook_text,
            "hook_type": self.hook_type,
            "reason": self.reason,
            "transcript_excerpt": self.transcript_excerpt,
            "llm_scores": {k: round(float(v), 2) for k, v in self.llm_scores.items()},
            "features": feats,
            "trend_prior": None if self.trend_prior is None else round(self.trend_prior, 3),
            "final_score": None if self.final_score is None else round(self.final_score, 3),
            "content_flags": list(self.content_flags),
            "needs_context": self.needs_context,
            "status": "proposed",
            "created_by_kind": "manual" if self.origin == "manual" else "auto",
        }

    def to_dict(self) -> dict[str, Any]:
        """Для API и отладки: строка БД плюс длительность, блокировка и происхождение."""
        row = self.to_row()
        row.update(
            {
                "duration_sec": round(self.duration, 2),
                "blocked": self.blocked,
                "block_reasons": self.block_reasons,
                "origin": self.origin,
            }
        )
        return row


# ---------------------------------------------------------------------- компоненты формулы


def llm_component(scores: Mapping[str, float], weights: Mapping[str, float] | None = None) -> float:
    w = weights or DEFAULT_LLM_WEIGHTS
    total = sum(w.get(k, 0.0) * float(scores.get(k, 0.0)) for k in SCORE_KEYS)
    norm = sum(w.get(k, 0.0) for k in SCORE_KEYS) or 1.0
    # веса по умолчанию в сумме дают 1.0; при кастомных весах нормируем, чтобы остаться в 0..1
    return max(0.0, min(1.0, total / norm / 10.0))


def rate_fit(wps: float, lo: float = 2.3, hi: float = 3.5, falloff: float = 1.0) -> float:
    if lo <= wps <= hi:
        return 1.0
    dist = lo - wps if wps < lo else wps - hi
    return max(0.0, 1.0 - dist / falloff) if falloff > 0 else 0.0


def feat_component(f: ClipFeatures | Mapping[str, Any], cfg: ScoringConfig | None = None) -> float:
    cfg = cfg or ScoringConfig()
    get = f.get if isinstance(f, Mapping) else lambda k, d=None: getattr(f, k, d)
    wps = float(get("speech_rate_wps", 0.0) or 0.0)
    dead_raw = get("dead_air_ratio", None)
    dead = 1.0 if dead_raw is None else float(dead_raw)
    lat = get("first_word_latency", None)
    lat = float("inf") if lat is None else float(lat)
    term = bool(get("ends_on_terminal_punct", False))
    parts = [
        rate_fit(wps, cfg.rate_lo, cfg.rate_hi, cfg.rate_falloff),
        max(0.0, min(1.0, 1.0 - cfg.dead_air_k * dead)),
        1.0 if lat < cfg.latency_max else 0.0,
        1.0 if term else 0.0,
    ]
    return sum(parts) / len(parts)


def len_fit(
    duration: float, target: float = 30.0, tolerance: float = 15.0, hard_max: float | None = 58.0
) -> float:
    if duration <= 0:
        return 0.0
    if hard_max is not None and duration > hard_max + 1e-6:
        return 0.0
    if tolerance <= 0:
        return 1.0 if abs(duration - target) < 1e-6 else 0.0
    return max(0.0, 1.0 - abs(duration - target) / tolerance)


def trend_shares_from_briefs(briefs: Iterable[tuple[str | None, float | None]]) -> dict[str, float]:
    """Доли hook_type среди брифов (hook_type, outlier_score), взвешенные по outlier_score.

    Нормируются на максимальную долю: самый частый тип = 1.0. Пустой вход — пустой словарь.
    """
    acc: dict[str, float] = {}
    for hook_type, outlier in briefs:
        if not hook_type:
            continue
        w = float(outlier) if outlier is not None and float(outlier) > 0 else 1.0
        acc[hook_type] = acc.get(hook_type, 0.0) + w
    top = max(acc.values(), default=0.0)
    return {k: v / top for k, v in acc.items()} if top > 0 else {}


def trend_prior(hook_type: str, shares: Mapping[str, float] | None, neutral: float = 0.5) -> float:
    if not shares:
        return neutral
    return max(0.0, min(1.0, float(shares.get(hook_type, 0.0))))


def final_score(
    llm: float,
    feat: float,
    lenfit: float,
    trend: float,
    *,
    needs_context: bool,
    cfg: ScoringConfig | None = None,
) -> float:
    cfg = cfg or ScoringConfig()
    w = cfg.final_weights
    score = w["llm"] * llm + w["feat"] * feat + w["len_fit"] * lenfit + w["trend"] * trend
    if needs_context:
        score *= cfg.needs_context_mult
    return max(0.0, min(1.0, score))


def score_candidate(
    cand: ClipCandidate,
    words: Sequence[Word],
    *,
    cfg: ScoringConfig | None = None,
    trend_shares: Mapping[str, float] | None = None,
    energy: EnergyTrack | None = None,
    effective_duration: float | None = None,
) -> ClipCandidate:
    """Признаки, итоговая оценка; меняет и возвращает cand.

    Признаки считаются на границах ДО снаппинга (`raw_start_sec/raw_end_sec`, для LLM это
    границы предложений): скоринг §6.3.4 идёт раньше снаппинга §6.3.5, а подход в тишину до
    0,35 с иначе всегда проваливал бы тест `first_word_latency < 0.3`. `len_fit` считается по
    фактической длительности клипа: после снаппинга или `effective_duration` (джамп-каты).
    """
    cfg = cfg or ScoringConfig()
    fs = cand.raw_start_sec if cand.raw_start_sec is not None else cand.start_sec
    fe = cand.raw_end_sec if cand.raw_end_sec is not None else cand.end_sec
    feats = compute_features(words, fs, fe, energy=energy)
    dur = cand.duration if effective_duration is None else effective_duration
    parts = {
        "llm": llm_component(cand.llm_scores, cfg.llm_weights),
        "feat": feat_component(feats, cfg),
        "len_fit": len_fit(dur, cfg.target_clip_sec, cfg.len_tolerance_sec, cfg.hard_max_sec),
        "trend": trend_prior(cand.hook_type, trend_shares, cfg.trend_neutral),
    }
    cand.features = {**cand.features, **feats.to_dict(), "duration_sec": round(cand.duration, 3)}
    if effective_duration is not None:
        cand.features["effective_duration_sec"] = round(effective_duration, 3)
    cand.trend_prior = parts["trend"]
    cand.score_parts = parts
    cand.final_score = final_score(
        parts["llm"],
        parts["feat"],
        parts["len_fit"],
        parts["trend"],
        needs_context=cand.needs_context,
        cfg=cfg,
    )
    return cand


# ---------------------------------------------------------------------- дедупликация


def overlap_ratio(a0: float, a1: float, b0: float, b1: float) -> float:
    """Пересечение / длина более короткого отрезка (0..1)."""
    inter = min(a1, b1) - max(a0, b0)
    if inter <= 0:
        return 0.0
    shorter = min(a1 - a0, b1 - b0)
    return inter / shorter if shorter > 0 else 0.0


def dedupe_overlapping(
    cands: Sequence[ClipCandidate],
    threshold: float = 0.5,
    *,
    spacing_sec: float = 180.0,
    exempt_score: float = 0.8,
    dropped: list[tuple[ClipCandidate, str]] | None = None,
) -> list[ClipCandidate]:
    """Жадная дедупликация: перекрытие > threshold и не больше одного слабого кандидата на 3 минуты."""
    order = sorted(cands, key=lambda c: (c.blocked, -(c.final_score or 0.0), c.start_sec))
    kept: list[ClipCandidate] = []
    for c in order:
        reason = None
        for k in kept:
            if overlap_ratio(c.start_sec, c.end_sec, k.start_sec, k.end_sec) > threshold:
                reason = "overlap"
                break
        if reason is None and (c.final_score or 0.0) < exempt_score and spacing_sec > 0:
            center = (c.start_sec + c.end_sec) / 2
            for k in kept:
                if abs(center - (k.start_sec + k.end_sec) / 2) < spacing_sec:
                    reason = "spacing"
                    break
        if reason is None:
            kept.append(c)
        elif dropped is not None:
            dropped.append((c, reason))
    return kept


# ---------------------------------------------------------------------- ниша и словарный фильтр


@dataclass(slots=True)
class NicheProfile:
    """Что отбору нужно знать о нише: описание для LLM, словарь запретов, ключевые слова."""

    id: str = "generic"
    label: str = ""
    description: str = ""
    forbidden_phrases: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    # секция `selection` ниши: режим отбора («talk» — говорящая голова, «scene» — сцены кино/диалоги)
    selection_mode: str = "talk"
    min_payoff: float | None = None
    min_emotion: float | None = None
    frames_in_detail: bool = True


def load_niche_profile(niche: str | Path, *, niches_dir: str | Path | None = None) -> NicheProfile:
    """NicheProfile из config/niches/<id>.yaml (или прямого пути к YAML)."""
    import yaml  # лениво

    path = Path(niche)
    if path.suffix not in (".yaml", ".yml"):
        base = Path(niches_dir) if niches_dir else _REPO_ROOT / "config" / "niches"
        path = base / f"{niche}.yaml"
    data = yaml.safe_load(path.read_text("utf-8")) or {}
    script = data.get("script") or {}
    visuals = data.get("visuals") or {}
    forbidden = data.get("forbidden_phrases") or script.get("forbidden_phrases") or []
    desc_parts = [str(data.get("label") or "")]
    if script.get("tone"):
        desc_parts.append(f"Тон: {script['tone']}")
    sel = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    return NicheProfile(
        id=str(data.get("id") or path.stem),
        label=str(data.get("label") or ""),
        description=". ".join(p for p in desc_parts if p),
        forbidden_phrases=[str(p) for p in forbidden],
        keywords=[str(s) for s in (visuals.get("subjects") or [])],
        selection_mode=str(sel.get("mode") or "talk"),
        min_payoff=None if sel.get("min_payoff") is None else float(sel["min_payoff"]),
        min_emotion=None if sel.get("min_emotion") is None else float(sel["min_emotion"]),
        frames_in_detail=bool(sel.get("frames_in_detail", True)),
    )


def _norm(text: str) -> str:
    # 1:1 по символам (позиции совпадают с исходником): регистр и ё → е
    out = []
    for ch in text:
        low = ch.lower()
        out.append(low if len(low) == 1 else ch)
    return "".join(out).replace("ё", "е")


def _phrase_regex(phrase: str) -> re.Pattern[str] | None:
    tokens = _norm(phrase).split()
    if not tokens:
        return None
    body = r"\s+".join(re.escape(t) for t in tokens)
    left = r"(?<!\w)" if re.match(r"\w", tokens[0]) else ""
    # Правой границы нет намеренно: «вылечит» ловит «вылечить», «излечит» — «излечится».
    return re.compile(left + body)


def find_forbidden(text: str, phrases: Iterable[str]) -> list[dict[str, Any]]:
    """Совпадения словаря ниши: [{phrase, start, end}] (позиции в исходном тексте)."""
    low = _norm(text or "")
    hits: list[dict[str, Any]] = []
    for phrase in phrases:
        rx = _phrase_regex(phrase)
        if rx is None:
            continue
        for m in rx.finditer(low):
            hits.append({"phrase": phrase, "start": m.start(), "end": m.end()})
    hits.sort(key=lambda h: (h["start"], h["end"]))
    return hits


_FLAG_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "medical_claim": [
        re.compile(r"(?<!\w)(вылеч|излеч|исцел)\w*"),
        re.compile(r"(?<!\w)не\s+нужен\s+врач"),
        re.compile(r"(?<!\w)(cure[sd]?|heals?)(?!\w)"),
    ],
    "guarantee_claim": [
        re.compile(r"(?<!\w)гарантир\w*"),
        re.compile(r"(?<!\w)100\s?%"),
        re.compile(r"(?<!\w)(точно|обязательно)\s+сбуд\w*"),
        re.compile(r"(?<!\w)guarantee[sd]?(?!\w)"),
    ],
}


def detect_content_flags(text: str) -> list[str]:
    """Словарная страховка medical/guarantee claims. Применяется ко всем кандидатам (и к LLM):
    флаги модели можно сбить инструкцией внутри транскрипта."""
    low = _norm(text or "")
    return [flag for flag, pats in _FLAG_PATTERNS.items() if any(p.search(low) for p in pats)]


def apply_niche_filter(
    cand: ClipCandidate, niche: NicheProfile | None, *, keyword_flags: bool = False
) -> ClipCandidate:
    """Словарь ниши по тексту клипа и по нашим заголовку и хуку. Совпадение блокирует кандидата.

    `keyword_flags=True` — добавить словарные medical/guarantee-флаги поверх флагов LLM
    (`selection.finalize_candidates` включает это для всех кандидатов).
    """
    hits: list[dict[str, Any]] = []
    phrases = niche.forbidden_phrases if niche else []
    for where, text in (
        ("transcript", cand.transcript_excerpt),
        ("title", cand.title),
        ("hook_text", cand.hook_text),
    ):
        for h in find_forbidden(text, phrases):
            hits.append({**h, "where": where})
    cand.forbidden_hits = hits
    flags = [f for f in cand.content_flags if f != FORBIDDEN_PHRASE_FLAG]
    if hits:
        flags.append(FORBIDDEN_PHRASE_FLAG)
    if keyword_flags:
        text = " ".join((cand.transcript_excerpt, cand.title, cand.hook_text))
        for f in detect_content_flags(text):
            if f not in flags:
                flags.append(f)
    cand.content_flags = flags
    return cand


def copy_candidate(cand: ClipCandidate, **changes: Any) -> ClipCandidate:
    return replace(cand, **changes)


__all__ = [
    "DEFAULT_FINAL_WEIGHTS",
    "DEFAULT_LLM_WEIGHTS",
    "SCENE_LLM_WEIGHTS",
    "SCENE_SCORING_DEFAULTS",
    "ClipCandidate",
    "NicheProfile",
    "ScoringConfig",
    "apply_niche_filter",
    "copy_candidate",
    "dedupe_overlapping",
    "detect_content_flags",
    "feat_component",
    "final_score",
    "find_forbidden",
    "len_fit",
    "llm_component",
    "load_niche_profile",
    "overlap_ratio",
    "rate_fit",
    "score_candidate",
    "trend_prior",
    "trend_shares_from_briefs",
]
