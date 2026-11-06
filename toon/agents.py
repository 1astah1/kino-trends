"""Агенты «Кат-сцены»: сценарист пишет серию, начальник принимает или возвращает с правками (до TOON_ROUNDS кругов).
Монтажёр и приёмка готового видео — следующим этапом (toon/montage.py, toon/review.py).

Модели — разные, чтобы начальник не хвалил «свою» работу: сценарист toon_writer_model (по умолчанию Claude Sonnet 5.5),
начальник toon_chief_model (Gemini 3.8 Flash — он же потом смотрит готовое видео)."""

from __future__ import annotations

import json
import random
import re
import sqlite3

import httpx

from kinotrends.db import now_iso
from kinotrends.llm import LLM, LLMError, parse_json, record_usage

from . import bible, series, store

WRITER_MODEL = "anthropic/claude-sonnet-5.5"
CHIEF_MODEL = "google/gemini-3.8-flash"
ROUNDS = 3                 # сценарий + до двух доработок
WPS = 2.3                  # слов в секунду у русской озвучки (Gemini TTS с интонацией, замер 06.11)
MIN_S, MAX_S = 30, 55

SCRIPT_SCHEMA = """{
  "drafts": {"hooks": ["5 коротких вариантов первой реплики"], "twists": ["3 варианта поворота"]},
  "title": "заголовок для YouTube до 60 знаков: интрига, без лжи",
  "format": "ключ формата",
  "logline": "суть серии одним предложением",
  "set": "локация из списка",
  "cast": ["ключи героев серии"],
  "lines": [{"who": "ключ героя", "text": "реплика ≤ 12 слов", "emotion": "из словаря", "action": "действие из словаря", "shot": "план из словаря", "prop": "предмет из словаря или пусто", "tv": "надпись на экране ТВ (≤ 4 слов) или пусто"}],
  "twist_at": 0, "punchline_at": 0,
  "loop": "чем конец рифмуется с началом",
  "guest": "кто гость, если есть, иначе пусто",
  "description": "2–3 предложения для описания ролика",
  "hashtags": ["#кино", "#игры"],
  "facts_used": ["факт из переданного источника дословно по смыслу, если формат behind"],
  "bpn": false
}"""

CHIEF_SCHEMA = """{
  "scores": {"hook": 0, "escalation": 0, "twist": 0, "punchline": 0, "loop": 0, "voices": 0, "adult": 0, "clarity": 0, "originality": 0},
  "flags": ["kids|law|ip|facts|too_long|too_short|repeat|weak_hook|explained_joke"],
  "verdict": "approve|revise|reject",
  "best_line": "лучшая реплика",
  "notes": ["конкретная правка: номер реплики → что сделать"],
  "summary": "два предложения: почему досмотрят или почему пролистают"
}"""


def _llm(settings: dict, role: str) -> LLM:
    model = settings.get(f"toon_{role}_model") or (WRITER_MODEL if role == "writer" else CHIEF_MODEL)
    return LLM(settings.get("llm_base_url") or "https://openrouter.ai/api/v1", settings.get("llm_api_key") or "", model, timeout=180)


def seconds(script: dict) -> float:
    """Длительность по числу слов плюс паузы между репликами и немая реакция в конце."""
    words = sum(len(re.findall(r"\w+", ln.get("text") or "")) for ln in script.get("lines") or [])
    return round(words / WPS + 0.35 * len(script.get("lines") or []) + 1.0, 1)


def wiki_fact(title: str) -> dict | None:
    """Вводная статьи ru.wikipedia (до 2500 знаков) — источник для формата «за кадром»."""
    try:
        with httpx.Client(timeout=20, headers={"User-Agent": "kino-trends/0.2 (+https://github.com/1astah1/kino-trends)"}) as c:
            r = c.get("https://ru.wikipedia.org/w/api.php", params={"action": "query", "prop": "extracts", "explaintext": 1,
                                                                    "redirects": 1, "titles": title, "format": "json"})
            pages = (r.json().get("query") or {}).get("pages") or {}
    except (httpx.HTTPError, ValueError):
        return None
    for p in pages.values():
        text = (p.get("extract") or "").strip()
        if text:
            keep = [s for s in re.split(r"\n+", text) if any(w in s.lower() for w in ("съём", "сним", "бюджет", "разработ", "актёр", "роль", "сценар", "режисс", "кассов", "интересн", "факт"))]
            return {"title": p.get("title"), "url": f"https://ru.wikipedia.org/wiki/{p.get('title', '').replace(' ', '_')}",
                    "text": ("\n".join(keep) or text)[:2500]}
    return None


def _write(conn: sqlite3.Connection, settings: dict, key: str, fmt: str, seed: str, fact: dict | None, recent: list[str],
           prev: dict | None = None, notes: list[str] | None = None) -> dict:
    llm = _llm(settings, "writer")
    system = (series.text(key) + "\n\nТЫ — ГЛАВНЫЙ СЦЕНАРИСТ сериала. Пишешь серию так, чтобы её досмотрели до конца и пересмотрели. "
              "В поле drafts коротко перечисли 5 вариантов хука и 3 поворота, выбери самые неожиданные и по ним пиши серию. "
              "Ответ — ТОЛЬКО один JSON-объект без текста до и после; внутри строк кавычки только «ёлочки», не \". Схема:\n"
              + SCRIPT_SCHEMA)
    task = [f"Формат серии: {fmt} — {series.S[key]['formats'].get(fmt, fmt)}", f"Повод/идея: {seed}"]
    if fact:
        task.append(f"ИСТОЧНИК ФАКТОВ (Википедия, «{fact['title']}»), бери факты ТОЛЬКО отсюда:\n{fact['text']}")
    if recent:
        task.append("Последние серии (не повторять ситуацию и финал):\n- " + "\n- ".join(recent[-20:]))
    if prev and notes:
        task.append("Прошлая версия:\n" + json.dumps(prev, ensure_ascii=False) + "\n\nПРАВКИ НАЧАЛЬНИКА (выполнить все):\n- " + "\n- ".join(notes))
    task.append(f"Длина: {MIN_S}–{MAX_S} секунд озвучки (≈{int(MIN_S * WPS)}–{int((MAX_S - 6) * WPS)} слов во всех репликах).")
    text, usage = llm.chat([{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(task)}],
                           max_tokens=7000, temperature=0.9)
    record_usage(conn, "toon.writer", llm.model, usage)
    try:
        script = parse_json(text)
    except LLMError:
        script = _repair(conn, settings, text)
    script["format"] = fmt
    return script


def _repair(conn: sqlite3.Connection, settings: dict, text: str) -> dict:
    """Сценарист иногда ломает JSON (прямые кавычки в репликах) — дешёвая модель чинит синтаксис, не меняя слов."""
    if not text.strip():
        raise LLMError("пустой ответ модели")
    llm = _llm(settings, "chief")
    fixed, usage = llm.chat([{"role": "user", "content": "Исправь синтаксис JSON: экранируй кавычки внутри строк, закрой скобки. Не меняй ни слова "
                              "в значениях. Ответ — только исправленный JSON.\n\n" + text}], max_tokens=8000, temperature=0)
    record_usage(conn, "toon.json_fix", llm.model, usage)
    return parse_json(fixed)


def _review(conn: sqlite3.Connection, settings: dict, key: str, script: dict, fact: dict | None, recent: list[str]) -> dict:
    llm = _llm(settings, "chief")
    system = (series.text(key) + "\n\nТЫ — НАЧАЛЬНИК (шоураннер). Принимаешь сценарии перед монтажом. Ты строгий: пропускаешь только то, "
              "что зритель досмотрит в ленте Shorts, где он листает за полсекунды. Оцени каждую шкалу 0–10 честно (7 — крепко, "
              "9–10 — редкость). Флаги ставь при любом нарушении запретов. verdict: approve — если hook, twist и punchline ≥ 7, "
              "среднее ≥ 7.5 и нет флагов; reject — если идея мертва и правками не спасти; иначе revise с конкретными правками "
              "по номерам реплик. Ответ — только JSON по схеме:\n" + CHIEF_SCHEMA)
    user = [f"Сценарий (оценка длины по словам: {seconds(script)} с, норма {MIN_S}–{MAX_S}):\n" + json.dumps(script, ensure_ascii=False)]
    if fact:
        user.append(f"Источник фактов (всё, что не отсюда, — флаг facts):\n{fact['text']}")
    if recent:
        user.append("Последние серии (повтор — флаг repeat):\n- " + "\n- ".join(recent[-20:]))
    text, usage = llm.chat([{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(user)}],
                           max_tokens=1500, temperature=0.2)
    record_usage(conn, "toon.chief", llm.model, usage)
    rev = parse_json(text)
    sc = rev.get("scores") or {}
    flags = [f for f in (rev.get("flags") or []) if f]
    s = seconds(script)
    if s > MAX_S + 4:
        flags.append("too_long")
    if s < MIN_S - 6:
        flags.append("too_short")
    rev["flags"] = sorted(set(flags))
    vals = [float(v) for v in sc.values() if isinstance(v, (int, float))]
    rev["avg"] = round(sum(vals) / len(vals), 2) if vals else 0
    ok = rev["avg"] >= 7.5 and min(float(sc.get(k) or 0) for k in ("hook", "twist", "punchline")) >= 7 and not rev["flags"]
    if rev.get("verdict") == "approve" and not ok:
        rev["verdict"] = "revise"  # начальник поставил «да», но цифры и флаги против — правила важнее
    return rev


def produce_script(conn: sqlite3.Connection, settings: dict, key: str, fmt: str, seed: str, fact: dict | None = None, log=print) -> int:
    """Сценарий через круги «сценарист → начальник». Возвращает id серии (статус script_ok или script_rejected)."""
    store.init(conn)
    recent = store.recent_loglines(conn, key)
    ep = store.create(conn, fmt, seed, fact, key)
    script, rev, prev = None, None, None
    for rnd in range(1, ROUNDS + 1):
        try:
            script = _write(conn, settings, key, fmt, seed, fact, recent, prev, (rev or {}).get("notes") if rev else None)
        except LLMError as e:
            store.event(conn, ep, "сценарист", "ошибка", str(e)[:300])
            continue
        store.event(conn, ep, "сценарист", f"версия {rnd}", f"«{script.get('title')}» — {seconds(script)} с")
        rev = _review(conn, settings, key, script, fact, recent)
        store.event(conn, ep, "начальник", rev.get("verdict", "?"),
                    f"среднее {rev.get('avg')}; флаги: {', '.join(rev['flags']) or 'нет'}; {rev.get('summary', '')}", rev)
        log(f"серия {ep}, круг {rnd}: «{script.get('title')}» → {rev.get('verdict')} ({rev.get('avg')})")
        if rev.get("verdict") == "approve":
            store.save(conn, ep, status="script_ok", script=script, review=rev)
            return ep
        if rev.get("verdict") == "reject":
            break
        prev = script
    store.save(conn, ep, status="script_rejected", script=script, review=rev)
    return ep


def seeds(conn: sqlite3.Connection, n: int = 3) -> list[tuple[str, str, dict | None]]:
    """(формат, повод, факт) для новых серий: «что если» чаще, «за кадром» — по фильму из календаря трендов с Википедией."""
    fmts = ["what_if", "what_if", "kino_logic", "game_logic", "behind", "client", "debate"]
    used = store.recent_formats(conn)
    out: list[tuple[str, str, dict | None]] = []
    # известные фильмы и сериалы со статьёй в ру-Википедии: из 1500 самых «ссылаемых» — случайные 40
    films = [r[0] for r in conn.execute("SELECT ruwiki FROM (SELECT ruwiki FROM titles WHERE ruwiki IS NOT NULL AND kind IN ('film','series') "
                                        "ORDER BY sitelinks DESC LIMIT 1500) ORDER BY RANDOM() LIMIT 40")] if _has(conn, "titles") else []
    ideas = {
        "what_if": ["что если бы NPC помнили всё, что ты с ними делал", "что если бы в жизни была кнопка «пропустить кат-сцену»",
                    "что если бы у фильмов были патчноуты, как у игр", "что если бы герои боевиков платили за разбитые машины",
                    "что если бы жизнь сохранялась только у костров, как в Dark Souls", "что если бы спойлеры были уголовной статьёй"],
        "kino_logic": ["герой в кино всегда находит парковку у входа", "в фильмах хакер взламывает всё за пять секунд",
                       "в кино всегда успевают обезвредить бомбу на последней секунде", "в фильмах никто не говорит «пока» в конце звонка"],
        "game_logic": ["в играх всё можно починить, если постучать", "побочные квесты в реальной жизни",
                       "лут с каждого шкафа", "баффы от еды перед важным разговором"],
        "client": random.sample(bible.GUEST_TYPES, k=len(bible.GUEST_TYPES)),
        "debate": ["что страшнее: хоррор-фильм или хоррор-игра", "кто лучше рассказывает истории — кино или игры",
                   "экранизации игр: проклятие или уже нет"],
    }
    for fmt in sorted(fmts, key=lambda f: used.count(f) + random.random())[:n]:
        if fmt == "behind":
            fact = None
            for film in films:
                fact = wiki_fact(film)
                if fact and len(fact["text"]) > 600:
                    out.append((fmt, f"история съёмок «{fact['title']}»", fact))
                    break
            if fact:
                continue
            fmt = "what_if"
        out.append((fmt, random.choice(ideas[fmt]), None))
    return out


def _has(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())
