"""Отбор кандидатов БЕЗ LLM — эвристика (запасной путь, когда нет ключа или LLM недоступен).

Это НЕ оценка модели: кандидаты помечаются `origin="heuristic"`, а `reason` начинается с
`HEURISTIC_LABEL`. Подоценки 0..10 строятся из простых сигналов, чтобы работала та же формула
§6.3.4:

* хук — вопрос в первом предложении, «цепляющие» слова (почему, секрет, ошибка, никто…),
  цифры, короткая первая фраза; штраф, если фраза начинается с продолжения («и», «а», «поэтому»,
  «он»…) — такой клип без контекста непонятен (`needs_context=True`);
* развязка — после вопроса есть ответ, клип заканчивается на `.`/`!`, итоговые слова;
* эмоция — восклицания и громкость относительно медианы источника (если есть огибающая);
* ниша — пересечение с ключевыми словами ниши; новизна — цифры, «оказывается», «на самом деле».

Длина: для каждого предложения-старта перебираются концы по границам предложений в
[min_clip_sec, max_clip_sec], выбирается ближайший к target_clip_sec с концом на точке.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from autoshorts.clipping.features import EnergyTrack
from autoshorts.clipping.schemas import HOOK_TEXT_MAX, TITLE_MAX, HookType
from autoshorts.clipping.scoring import ClipCandidate, NicheProfile
from autoshorts.clipping.transcript import Sentence, Transcript

HEURISTIC_LABEL = "[эвристика без LLM]"
MIN_QUESTION_WORDS = 4  # короче — отклик («Аа?») или обрывок ASR, не вопрос-хук

_CONTINUATION = re.compile(
    r"^(и|а|но|да|поэтому|потому|так\s+вот|вот|это|этот|эта|эти|он|она|оно|они|его|ее|её|их|тогда|также|"
    r"тоже|еще|ещё|причем|причём|кстати|значит|то\s+есть|зато|хотя|ну|and|but|so|then|it|this|that|they|he|she)"
    r"(?!\w)",
    re.IGNORECASE,
)
_HOOK_WORDS = re.compile(
    r"(?<!\w)(почему|зачем|как\s|секрет|никто|никогда|главн|ошибк|правд|представь|представьте|внимание|"
    r"смотри|запомни|важно|факт|на\s+самом\s+деле|вот\s+что|дело\s+в\s+том|оказывается|признаюсь|"
    r"самый|самая|самое|why|how|secret|never|nobody|mistake|truth)",
    re.IGNORECASE,
)
_PAYOFF_WORDS = re.compile(
    r"(?<!\w)(поэтому|итог|вывод|значит|главное|в\s+общем|короче|в\s+итоге|так\s+что|вот\s+почему|"
    r"so|that's\s+why|bottom\s+line)",
    re.IGNORECASE,
)
_NOVELTY_WORDS = re.compile(
    r"(?<!\w)(впервые|никто\s+не|мало\s+кто|оказывается|на\s+самом\s+деле|неожиданно|surprising|actually)",
    re.IGNORECASE,
)
_DIRECT = re.compile(r"(?<!\w)(ты|вы|тебе|вам|тебя|вас|твой|ваш|you|your)(?!\w)", re.IGNORECASE)
_STORY = re.compile(r"(?<!\w)(однажды|как-то\s+раз|помню|была\s+история|once|i\s+remember)", re.IGNORECASE)
_DIGIT = re.compile(r"\d")


def _clamp(x: float) -> float:
    return max(0.0, min(10.0, x))


def _starts_with_continuation(text: str) -> bool:
    return bool(_CONTINUATION.match(text.strip().lstrip("«\"'(—– -")))


def _hook_type(first: str) -> str:
    low = first.lower()
    if first.rstrip().rstrip("»\"')").endswith("?"):
        return HookType.QUESTION.value
    if "ошибк" in low or "mistake" in low:
        return HookType.MISTAKE_WARNING.value
    if low.lstrip("«\"' ").startswith(("как ", "how ")):
        return HookType.HOW_TO.value
    if _STORY.search(first):
        return HookType.STORY.value
    if _DIGIT.search(first) or "оказывается" in low:
        return HookType.SURPRISING_FACT.value
    if "на самом деле" in low or "все думают" in low:
        return HookType.CONTRARIAN.value
    if _DIRECT.search(first):
        return HookType.DIRECT_ADDRESS.value
    if first.rstrip().endswith("!"):
        return HookType.EMOTIONAL.value
    return HookType.OTHER.value


def _niche_hits(text: str, niche: NicheProfile | None) -> int:
    if not niche or not niche.keywords:
        return 0
    low = text.lower().replace("ё", "е")
    hits = 0
    for kw in niche.keywords:
        stem = kw.lower().replace("ё", "е").strip()
        stem = stem[: max(4, len(stem) - 2)]  # грубая основа: «созвездия» → «созвезд»
        if stem and stem in low:
            hits += 1
    return hits


def pseudo_scores(
    sents: Sequence[Sentence],
    *,
    energy_rel: float | None = None,
    niche: NicheProfile | None = None,
    scene: bool = False,
) -> tuple[dict[str, float], bool, list[str]]:
    """Подоценки 0..10 для фрагмента из предложений, флаг needs_context и причины (для reason).

    `scene=True` — режим диалогов кино: реплики-ответы с местоимений в начале — норма, ценятся короткие
    хлёсткие фразы, восклицания и вопросы-клиффхэнгеры в конце, громкость весит больше."""
    if scene:
        return _scene_pseudo_scores(sents, energy_rel=energy_rel, niche=niche)
    first = sents[0].text
    last = sents[-1].text
    body = " ".join(s.text for s in sents)
    reasons: list[str] = []
    cont = _starts_with_continuation(first)
    n_first = len(first.split())
    # «Аа?», «И мы так?», «наш тизер?» — реплика-отклик или обрывок ASR, а не вопрос-хук
    is_q = first.rstrip().rstrip("»\"')").endswith("?") and n_first >= MIN_QUESTION_WORDS

    hook = 4.0
    if is_q:
        hook += 3
        reasons.append("вопрос в первой фразе")
    hook_words = len(_HOOK_WORDS.findall(first))
    if hook_words:
        hook += min(2, hook_words) * 1.0
        reasons.append("цепляющие слова")
    if _DIGIT.search(first):
        hook += 1
    if n_first <= 12:
        hook += 1
    if cont:
        hook -= 3
        reasons.append("начало с продолжения мысли")

    standalone = 7.0 - (4.0 if cont else 0.0)

    payoff = 4.0
    answered = is_q and any(not s.text.rstrip().endswith("?") for s in sents[1:])
    if answered:
        payoff += 3
        reasons.append("вопрос → ответ")
    if last.rstrip().rstrip("»\"')").endswith((".", "!", "…")):
        payoff += 2
    elif last.rstrip().endswith("?"):
        payoff -= 2
    if _PAYOFF_WORDS.search(" ".join(s.text for s in sents[-2:])):
        payoff += 1
        reasons.append("есть вывод")

    emotion = 4.0 + min(3, body.count("!"))
    if energy_rel is not None:
        emotion += max(-3.0, min(3.0, (energy_rel - 1.0) * 5.0))
        if energy_rel > 1.15:
            reasons.append("громче среднего")

    niche_fit = 5.0 + min(4, _niche_hits(body, niche))
    novelty = 4.0 + (2 if _DIGIT.search(body) else 0) + (1 if _NOVELTY_WORDS.search(body) else 0)

    scores = {
        "hook": _clamp(hook),
        "standalone": _clamp(standalone),
        "payoff": _clamp(payoff),
        "emotion": _clamp(emotion),
        "niche_fit": _clamp(niche_fit),
        "novelty": _clamp(novelty),
    }
    return scores, cont, reasons


def _scene_pseudo_scores(
    sents: Sequence[Sentence], *, energy_rel: float | None, niche: NicheProfile | None
) -> tuple[dict[str, float], bool, list[str]]:
    first = sents[0].text
    last = sents[-1].text.rstrip()
    body = " ".join(s.text for s in sents)
    reasons: list[str] = []
    cont = _starts_with_continuation(first)
    n_first = len(first.split())
    n_last = len(last.split())
    ends = last.rstrip("»\"')")

    hook = 4.5
    if n_first <= 8:
        hook += 1.0
        reasons.append("короткая первая реплика")
    if first.rstrip().rstrip("»\"')").endswith(("!", "?")):
        hook += 1.5
    if _DIGIT.search(first):
        hook += 0.5
    if cont:
        hook -= 1.5  # в диалоге ответ с «а», «но», «он» — обычное дело, но сцену лучше начинать не с него
    if len(sents) >= 3:
        hook += 0.5
        reasons.append("обмен репликами")

    standalone = 6.5 - (2.0 if cont else 0.0)

    payoff = 4.0
    if ends.endswith("!"):
        payoff += 2.0
        reasons.append("восклицание в конце")
    elif ends.endswith("?"):
        payoff += 1.0  # вопрос в конце — клиффхэнгер
        reasons.append("вопрос-клиффхэнгер")
    elif ends.endswith((".", "…")):
        payoff += 1.5
    if n_last <= 7:
        payoff += 1.0
        reasons.append("короткая финальная реплика")
    if _PAYOFF_WORDS.search(last):
        payoff += 0.5

    emotion = 4.0 + min(3.0, 0.8 * (body.count("!") + 0.5 * body.count("?")))
    if energy_rel is not None:
        emotion += max(-3.0, min(3.5, (energy_rel - 1.0) * 7.0))
        if energy_rel > 1.15:
            reasons.append("громче среднего")

    niche_fit = 5.0 + min(4, _niche_hits(body, niche))
    novelty = 4.0 + (1 if _DIGIT.search(body) else 0) + (1 if _NOVELTY_WORDS.search(body) else 0)
    scores = {
        "hook": _clamp(hook),
        "standalone": _clamp(standalone),
        "payoff": _clamp(payoff),
        "emotion": _clamp(emotion),
        "niche_fit": _clamp(niche_fit),
        "novelty": _clamp(novelty),
    }
    return scores, cont, reasons


def _trim(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _best_end(
    sents: Sequence[Sentence], i: int, *, target: float, min_sec: float, max_sec: float
) -> int | None:
    best_j, best_key = None, None
    j = i
    while j < len(sents):
        dur = sents[j].e - sents[i].s
        if dur > max_sec + 1e-6:
            break
        if dur >= min_sec - 1e-6:
            terminal = sents[j].text.rstrip().rstrip("»\"')").endswith((".", "!", "…", "?"))
            key = (not terminal, abs(dur - target))
            if best_key is None or key < best_key:
                best_j, best_key = j, key
        j += 1
    return best_j


def heuristic_candidates(
    transcript: Transcript,
    *,
    target_clip_sec: float = 30.0,
    min_clip_sec: float = 15.0,
    max_clip_sec: float = 58.0,
    energy: EnergyTrack | None = None,
    niche: NicheProfile | None = None,
    min_hook: float = 5.0,
    scene: bool = False,
) -> list[ClipCandidate]:
    """Сырые кандидаты (по одному на предложение-старт с достаточным хуком), ещё без снаппинга и итоговой
    оценки. Их доводит `selection.finalize_candidates` (снаппинг, признаки, скоринг, фильтр, дедуп)."""
    sents = transcript.sentences
    out: list[ClipCandidate] = []
    for i, st in enumerate(sents):
        j = _best_end(sents, i, target=target_clip_sec, min_sec=min_clip_sec, max_sec=max_clip_sec)
        if j is None:
            continue
        chunk = sents[i : j + 1]
        e_rel = energy.relative(st.s, sents[j].e) if energy is not None else None
        scores, cont, reasons = pseudo_scores(chunk, energy_rel=e_rel, niche=niche, scene=scene)
        if scores["hook"] < (min_hook - 1.0 if scene else min_hook):
            continue
        reason = HEURISTIC_LABEL + " " + (", ".join(reasons) if reasons else "ровный фрагмент по длине")
        out.append(
            ClipCandidate(
                start_sid=st.sid,
                end_sid=sents[j].sid,
                hook_sid=st.sid,
                start_sec=st.s,
                end_sec=sents[j].e,
                title=_trim(st.text.rstrip(" .…"), TITLE_MAX),
                hook_text=_trim(st.text, HOOK_TEXT_MAX),
                hook_type=_hook_type(st.text),
                reason=_trim(reason, 300),
                llm_scores=scores,
                needs_context=cont,
                content_flags=[],
                origin="heuristic",
                transcript_excerpt=" ".join(s.text for s in chunk),
                raw_start_sec=st.s,
                raw_end_sec=sents[j].e,
            )
        )
    return out


__all__ = ["HEURISTIC_LABEL", "heuristic_candidates", "pseudo_scores"]
