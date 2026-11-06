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
# Уверенность распознавания по словам: у нормальной озвучки средняя 0,94–0,98 и неуверенных (< 0,5) слов ≤ 2 %,
# у перевёрнутой (обход Content ID, «Мегамозг» 29.10) — 0,58 и 35 %: распознавание выдаёт несуществующие слова
MIN_ASR_CONFIDENCE = 0.85
MAX_LOW_CONF_SHARE = 0.12

SYSTEM_CHECK = (
    "Ты проверяешь исходник для канала нарезок фильмов и сериалов на русском. Тебе дают название произведения и расшифровку "
    "звука видео — отрывки из разных мест. Реши, это само произведение (фильм или серия: диалоги и реплики персонажей) "
    "на русском языке (дубляж, закадровая озвучка или отечественный оригинал)? Не годятся: стрим или реакция с чатом и "
    "приветствиями зрителей, обзор, пересказ, аудиокнига или чтение книги, подкаст, трейлер, сборник нарезок, другое произведение, "
    "катсцены из видеоигры («игрофильм»), фанатский или сгенерированный фильм, речь не на русском, бессвязная речь — набор "
    "несуществующих слов и обрывков слогов (перевёрнутая или искажённая звуковая дорожка). Смотри и на название ролика "
    "с каналом. Отвечай только JSON: "
    '{"is_work": true, "russian": true, "what": "film|episode|stream|review|audiobook|podcast|trailer|other_work|other", '
    '"confidence": 0.9, "reason": "коротко, до 12 слов"}'
)

SYSTEM_PICK = (
    "Ты продюсер канала нарезок фильмов и сериалов для YouTube Shorts в России. Тебе дают сцены произведения, которые сейчас "
    "режут и смотрят в Shorts (тренд), и моменты, найденные в самом фильме (id, название, почему, кусок реплик). Выбери моменты, "
    "которые совпадают с трендовыми сценами или так же цепляют. Главное — ЗАКОНЧЕННАЯ МИНИ-ИСТОРИЯ: зритель с первых "
    "секунд понимает ситуацию, дальше нарастание, а в конце развязка — панчлайн, ответ, решение, разоблачение или "
    "эмоциональный пик; ради неё досматривают. Среди законченных — сначала смешное (шутка с развязкой, перепалка, "
    "нелепость), затем конфликт и сильные реплики. Не бери обрывки без развязки, скучные и непонятные без контекста. "
    "Отвечай только JSON: "
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


def speech_quality(words: list[dict] | None) -> dict:
    """Осмысленна ли речь по уверенности распознавания. ok None — данных нет (старая расшифровка без p)."""
    ps = [float(w["p"]) for w in (words or []) if isinstance(w, dict) and isinstance(w.get("p"), (int, float))]
    if len(ps) < 200:
        return {"ok": None}
    avg = sum(ps) / len(ps)
    low = sum(1 for p in ps if p < 0.5) / len(ps)
    ok = avg >= MIN_ASR_CONFIDENCE and low <= MAX_LOW_CONF_SHARE
    out = {"ok": ok, "asr_conf": round(avg, 3), "low_share": round(low, 3)}
    if not ok:
        out["reason"] = (f"озвучка неразборчива — похоже на перевёрнутую или искажённую дорожку "
                         f"(уверенность распознавания {avg:.2f}, неуверенных слов {low:.0%})")
    return out


def check_transcript(llm: LLM | None, title: str, year, kind: str | None, sentences: list[str], duration_sec: float | None = None,
                     video_hint: str | None = None, words: list[dict] | None = None) -> dict:
    """{"ok": True|False|None, "reason": ..., ...}. None — модель не ответила, проверить позже."""
    text = " ".join(sentences)
    if len(text) < MIN_SPEECH_CHARS:
        return {"ok": False, "reason": "в видео почти нет речи", "cyrillic": None}
    sq = speech_quality(words)
    if sq.get("ok") is False:
        return {"ok": False, "reason": sq["reason"], "what": "garbled", **{k: sq[k] for k in ("asr_conf", "low_share")}}
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
            + (f"Видео: {video_hint}\n" if video_hint else "")
            + "Отрывки расшифровки:\n" + "\n".join(f"{i + 1}) {p}" for i, p in enumerate(parts)))
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


MIN_SOURCE_SIDE = 360  # меньше по короткой стороне — мыло (29.10: «Мегамозг» 368×208)
BAD_VISUAL_KIND = {"game", "reaction", "stream", "slideshow", "trailer", "compilation", "other"}

SYSTEM_FRAMES = (
    "Ты проверяешь видео-исходник для канала нарезок фильмов и сериалов. Тебе дают название произведения и лист раскадровки: "
    "сетка мелких кадров из подряд идущих моментов видео. Реши по картинке: это сами кадры этого произведения "
    "(фильма, серии, мультфильма, аниме) в нормальном виде? Не годятся: запись видеоигры или геймплей (3D-графика игры, "
    "интерфейс, прицел, мини-карта, полоски здоровья) — даже по мотивам того же мультфильма; реакция или стрим (лицо "
    "ведущего, чат, вебкамера, кадр в кадре); слайд-шоу из картинок; трейлер или нарезка разных произведений; зеркально "
    "отражённое видео (перевёрнутые буквы и цифры, зеркальные надписи); картинка занимает малую часть кадра (маленькое окно "
    "на чёрном или на узорном фоне); сильный посторонний водяной знак или рамка поверх; совсем мутное изображение. Чёрные "
    "полосы сверху и снизу у широкоэкранного фильма — нормально. Отвечай только JSON: "
    '{"is_work": true, "kind": "film|episode|game|reaction|stream|slideshow|trailer|compilation|other", "mirrored": false, '
    '"small_picture": false, "overlay": false, "blurry": false, "confidence": 0.9, "reason": "коротко, до 12 слов"}'
)


def check_resolution(width, height) -> dict:
    """Мыльные перезаливки (240p, 368×208) в нарезку не берём."""
    try:
        w, h = int(width or 0), int(height or 0)
    except (TypeError, ValueError):
        return {"ok": None}
    if not w or not h:
        return {"ok": None}
    if min(w, h) < MIN_SOURCE_SIDE:
        return {"ok": False, "reason": f"низкое качество видео {w}×{h}: мыльная перезаливка"}
    return {"ok": True, "size": f"{w}×{h}"}


def check_frames(llm: LLM | None, title: str, year, kind: str | None, sheet: bytes) -> dict:
    """Проверка по кадрам (лист раскадровки): игра, реакция, зеркало, окно на фоне, мыло. None — модель не ответила."""
    if llm is None or not llm.configured:
        return {"ok": True, "reason": "кадры не проверены: модель не настроена"}
    user = f"Произведение: {title} ({year or 'год неизвестен'}, {kind or 'фильм'}). Лист раскадровки приложен."
    try:
        data, _usage = llm.ask_json(SYSTEM_FRAMES, user, image=sheet, max_tokens=250)
    except LLMError as e:
        msg = str(e)
        if "PROHIBITED" in msg or "blocked" in msg.lower() or "SAFETY" in msg:
            # фильтр модели не пускает кадры (насилие, откровенные сцены) — не держим фильм: звук и разрешение проверены
            return {"ok": True, "reason": "кадры не проверены: модель отказалась смотреть (её фильтр)", "blocked": True}
        return {"ok": None, "reason": f"модель не ответила: {msg[:120]}"}
    kind_v = str(data.get("kind") or "")
    conf = float(data.get("confidence") or 0)
    flags = [name for name, key in (("зеркальное видео", "mirrored"), ("картинка в малом окне", "small_picture"),
                                     ("посторонняя рамка или знак", "overlay"), ("мутное изображение", "blurry")) if data.get(key)]
    bad = (not data.get("is_work")) or kind_v in BAD_VISUAL_KIND or bool(flags)
    ok = not (bad and conf >= 0.6)
    reason = str(data.get("reason") or "")[:160]
    if not ok and not reason:
        reason = {"game": "это запись игры", "reaction": "это реакция", "stream": "это стрим", "slideshow": "это слайд-шоу",
                  "trailer": "это трейлер", "compilation": "это нарезка"}.get(kind_v, ", ".join(flags) or "кадры не из произведения")
    return {"ok": ok, "reason": reason, "kind": kind_v, "flags": flags, "confidence": conf}


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
