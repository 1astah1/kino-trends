"""Грубое сравнение текстов для авторского слоя: «не пересказ ли это транскрипта», повтор шаблона.

Токены — слова из букв и цифр длиной от 3 символов, в нижнем регистре, ё → е, обрезанные до
5 символов (дешёвая замена стеммингу: «объясняет» и «объяснил» совпадают). Для русского этого
хватает, чтобы отличить свою формулировку от цитаты.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_WORD = re.compile(r"[0-9a-zа-я]+", re.IGNORECASE)
STEM_LEN = 5
MIN_TOKEN = 3
_STOP = frozenset(
    {
        "это",
        "что",
        "как",
        "так",
        "для",
        "или",
        "его",
        "она",
        "они",
        "оно",
        "вот",
        "там",
        "тут",
        "все",
        "всё",
        "еще",
        "уже",
        "был",
        "быть",
        "есть",
        "the",
        "and",
        "for",
        "you",
        "that",
        "this",
        "когд",
        "тако",
        "може",
        "очен",
        "прос",
        "чтоб",
        "если",
        "было",
        "были",
        "даже",
    }
)


def stems(text: str) -> list[str]:
    out: list[str] = []
    for m in _WORD.finditer((text or "").lower().replace("ё", "е")):
        tok = m.group(0)
        if len(tok) < MIN_TOKEN:
            continue
        st = tok[:STEM_LEN]
        if tok in _STOP or st in _STOP:
            continue
        out.append(st)
    return out


def coverage(text: str, reference: str | Iterable[str]) -> float:
    """Доля токенов `text`, которые встречаются в `reference` (строка или набор токенов)."""
    toks = stems(text)
    if not toks:
        return 0.0
    ref = set(stems(reference)) if isinstance(reference, str) else set(reference)
    return sum(1 for t in toks if t in ref) / len(toks)


def duplicates_transcript(
    text: str, transcript_text: str, *, threshold: float = 0.7, min_tokens: int = 2
) -> bool:
    """Текст на экране или реплика — почти целиком слова исходника (цитата или пересказ)."""
    toks = stems(text)
    if len(toks) < min_tokens:
        return bool(toks) and coverage(text, transcript_text) >= 1.0
    return coverage(text, transcript_text) >= threshold


SUBSTANTIVE_MIN_TOKENS = 2


def content_tokens(text: str) -> list[str]:
    """Смысловые токены: `stems` без чисел («100», «2026» смысла сами по себе не несут)."""
    return [t for t in stems(text) if not t.isdigit()]


def is_substantive(text: str, *, min_tokens: int = SUBSTANTIVE_MIN_TOKENS) -> bool:
    """Есть ли в тексте хоть какая-то своя формулировка: не меньше `min_tokens` смысловых слов.

    «!!!», «🔥🔥🔥», «Ну и ну?», «Это так», «Разбор» — нет: такой заголовок не авторский вклад.
    """
    return len(content_tokens(text)) >= min_tokens


def author_text_status(text: str, transcript_text: str, *, threshold: float = 0.7) -> tuple[bool, str]:
    """Засчитывается ли экранный текст как авторский: (да/нет, причина).

    Причины отказа: `no_words` (нет своих слов) и `duplicates_transcript` (цитата или пересказ).
    """
    if not is_substantive(text):
        return False, "no_words"
    if duplicates_transcript(text, transcript_text, threshold=threshold):
        return False, "duplicates_transcript"
    return True, ""


def shingles(text: str, n: int = 2) -> set[tuple[str, ...]]:
    toks = stems(text)
    if len(toks) < n:
        return {tuple(toks)} if toks else set()
    return {tuple(toks[i : i + n]) for i in range(len(toks) - n + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def text_similarity(a: str, b: str, n: int = 2) -> float:
    return jaccard(shingles(a, n), shingles(b, n))


__all__ = [
    "SUBSTANTIVE_MIN_TOKENS",
    "author_text_status",
    "content_tokens",
    "coverage",
    "duplicates_transcript",
    "is_substantive",
    "jaccard",
    "shingles",
    "stems",
    "text_similarity",
]
