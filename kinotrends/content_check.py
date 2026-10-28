"""Проверка исходника по содержанию и выбор моментов по сценам из трендов.

Метаданные (заголовок, канал, длительность) не отличают фильм от стрима с ним, аудиокниги или английской
версии — это видно только по звуку. После загрузки в auto-shorts берём расшифровку и проверяем:
1) речь русская — доля кириллицы (распознавание на стенде русское, английская речь выходит латиницей);
2) модель по отрывкам из разных мест решает, само ли это произведение с диалогами персонажей.

Второе: какие моменты резать. Сайт знает, какие сцены произведения режут в Shorts сейчас (разбор «сцены»);
модель сопоставляет их с моментами, которые нашёл auto-shorts, и выбирает совпадающие.
"""
from __future__ import annotations

import json
import re

from .llm import LLM, LLMError

MIN_CYRILLIC = 0.85
# Аудиокнига: авторская речь («сказал», «спросил»…) — в кино ~0,4 на 1000 слов, в «Форресте Гампе»-романе 8
NARRATION = re.compile(r"\b(сказал[аио]?|спросил[аио]?|ответил[аио]?|подумал[аио]?|произн[её]с(ла)?|воскликнул[аи]?|прошептал[аи]?|пробормотал[аи]?)\b",
                       re.IGNORECASE)
MAX_NARRATION_PER_1000 = 4.0
BOOK_MARKERS = re.compile(r"\bроман\b|перевод с|\bглава\s+\w+|аудиокниг|\bчитает\b|\bкнига\s+(перв|втор)", re.IGNORECASE)
# Русской речи слишком мало на минуту — звук на другом языке, а русская озвучка тихая или её нет
# (в «Брестской крепости» ~18 слов в минуту, в корейском сериале с невнятной озвучкой ~4)
MIN_WORDS_PER_MIN = 8.0
BAD_WHAT = {"stream", "review", "audiobook", "podcast", "trailer", "other_work", "other", "reaction", "compilation"}
MIN_SPEECH_CHARS = 400

SYSTEM_CHECK = (
    "Ты проверяешь исходник для канала нарезок фильмов и сериалов на русском. Тебе дают название произведения и расшифровку "
    "звука видео — отрывки из разных мест. Реши, это само произведение (фильм или серия: диалоги и реплики персонажей) "
    "на русском языке (дубляж, закадровая озвучка или отечественный оригинал)? Не годятся: стрим или реакция с чатом и "
    "приветствиями зрителей, обзор, пересказ, аудиокнига или чтение книги, подкаст, трейлер, сборник нарезок, другое произведение, "
    "речь не на русском. Отвечай только JSON: "
    '{"is_work": true, "russian": true, "what": "film|episode|stream|review|audiobook|podcast|trailer|other_work|other", '
    '"confidence": 0.9, "reason": "коротко, до 12 слов"}'
)

SYSTEM_PICK = (
    "Ты продюсер канала нарезок фильмов и сериалов для YouTube Shorts в России. Тебе дают сцены произведения, которые сейчас "
    "режут и смотрят в Shorts (тренд), и моменты, найденные в самом фильме (id, название, почему, кусок реплик). Выбери моменты, "
    "которые совпадают с трендовыми сценами или так же цепляют: законченная мини-история, конфликт, сильная реплика, эмоция. "
    "Не бери скучные, обрывочные и непонятные без контекста. Отвечай только JSON: "
    '{"picked": [{"id": 12, "scene": "название трендовой сцены или пусто", "why": "до 10 слов"}]} — в порядке силы, не больше N.'
)


def cyrillic_share(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    cyr = sum(1 for ch in letters if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
    return cyr / len(letters)


def excerpts(sentences: list[str], parts: int = 8, per: int = 4, cap: int = 2600) -> list[str]:
    """Отрывки из разных мест расшифровки: начало, середина, конец — чтобы стрим или аудиокнигу было видно везде."""
    clean = [s.strip() for s in sentences if s and s.strip()]
    if not clean:
        return []
    out: list[str] = []
    step = max(1, len(clean) // parts)
    for i in range(0, len(clean), step):
        out.append(" ".join(clean[i:i + per]))
        if len(out) >= parts:
            break
    total, res = 0, []
    for e in out:
        if total + len(e) > cap:
            break
        res.append(e)
        total += len(e)
    return res


def check_transcript(llm: LLM | None, title: str, year, kind: str | None, sentences: list[str], duration_sec: float | None = None) -> dict:
    """{"ok": True|False|None, "reason": ..., ...}. None — модель не ответила, проверить позже."""
    text = " ".join(sentences)
    if len(text) < MIN_SPEECH_CHARS:
        return {"ok": False, "reason": "в видео почти нет речи", "cyrillic": None}
    share = cyrillic_share(text)
    if share < MIN_CYRILLIC:
        return {"ok": False, "reason": f"речь не на русском (кириллица {share:.0%})", "cyrillic": round(share, 3)}
    words = len(text.split())
    if duration_sec and duration_sec >= 600:
        wpm = words / (duration_sec / 60.0)
        if wpm < MIN_WORDS_PER_MIN:
            return {"ok": False, "reason": f"русской речи почти нет ({wpm:.0f} слов в минуту): звук на другом языке или озвучка неразборчива",
                    "cyrillic": round(share, 3)}
    narr = len(NARRATION.findall(text)) / max(1, words) * 1000
    if narr > MAX_NARRATION_PER_1000 or BOOK_MARKERS.search(" ".join(sentences[:20])):
        return {"ok": False, "reason": "это аудиокнига, а не фильм", "what": "audiobook", "cyrillic": round(share, 3)}
    if llm is None or not llm.configured:
        return {"ok": True, "reason": "проверено только по языку: модель не настроена", "cyrillic": round(share, 3)}
    parts = excerpts(sentences)
    user = (f"Произведение: {title} ({year or 'год неизвестен'}, {kind or 'фильм'}).\n"
            "Отрывки расшифровки:\n" + "\n".join(f"{i + 1}) {p}" for i, p in enumerate(parts)))
    try:
        data, _usage = llm.ask_json(SYSTEM_CHECK, user, max_tokens=300)
    except LLMError as e:
        return {"ok": None, "reason": f"модель не ответила: {str(e)[:120]}", "cyrillic": round(share, 3)}
    what = str(data.get("what") or "")
    conf = float(data.get("confidence") or 0)
    # модель пишет и «series», и «movie»: отбраковываем только явный мусор, а не непривычное слово
    ok = bool(data.get("is_work")) and bool(data.get("russian")) and what not in BAD_WHAT and conf >= 0.6
    reason = str(data.get("reason") or "")[:160]
    if not ok and not reason:
        reason = {"stream": "это стрим", "review": "это обзор", "audiobook": "это аудиокнига", "podcast": "это подкаст",
                  "trailer": "это трейлер", "other_work": "это другое произведение"}.get(what, "это не само произведение")
    return {"ok": ok, "reason": reason, "what": what, "confidence": conf, "cyrillic": round(share, 3)}


def pick_by_scenes(llm: LLM | None, title: str, scenes: list[dict], cands: list[dict], n: int) -> list[dict]:
    """Моменты auto-shorts, совпадающие с трендовыми сценами. Без модели или сцен — пусто (берём по баллу)."""
    if llm is None or not llm.configured or not scenes or not cands:
        return []
    sc_lines = []
    for sc in scenes[:8]:
        q = "; ".join((sc.get("quotes") or [])[:2])
        sc_lines.append(f"- {sc.get('name')}: {(sc.get('what_to_cut') or '')[:160]}{' Реплики: ' + q if q else ''}")
    c_lines = [f"- id={c['id']} | {c.get('title') or ''} | {(c.get('reason') or '')[:140]} | {(c.get('transcript_excerpt') or c.get('hook_text') or '')[:220]}"
               for c in cands[:20]]
    user = (f"Произведение: {title}. N={n}.\nТрендовые сцены:\n" + "\n".join(sc_lines) + "\n\nМоменты в фильме:\n" + "\n".join(c_lines))
    try:
        data, _usage = llm.ask_json(SYSTEM_PICK, user, max_tokens=500)
    except LLMError:
        return []
    by_id = {int(c["id"]): c for c in cands}
    out: list[dict] = []
    for it in data.get("picked") or []:
        try:
            cid = int(it.get("id"))
        except (TypeError, ValueError):
            continue
        if cid in by_id and by_id[cid] not in out:
            out.append(by_id[cid])
        if len(out) >= n:
            break
    return out


def dumps_short(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False)[:400]
