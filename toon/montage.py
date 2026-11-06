"""Агент-монтажёр: реплики с реальной длиной звука → монтажный план (планы, движения камеры, реакции слушающих, склейки
внутри длинных реплик) → таймлайн для рендера на маке + субтитры кусками по 1–3 слова.

Ответ модели проверяется по словарю сериала; что не прошло — заменяется правилами (монтаж по правилам — запасной)."""

from __future__ import annotations

import json
import re
import sqlite3

from kinotrends.llm import LLM, LLMError, parse_json, record_usage

from . import series

MODEL = "google/gemini-3.8-flash"
GAP = 0.14            # пауза между репликами, с
LEAD = 0.2            # тишина перед первой репликой
TAIL = 1.1            # немая реакция после панчлайна
SPLIT_OVER = 2.7      # реплику длиннее — режем на два плана

SCHEMA = """{"set": "локация из словаря",
 "segments": [{"line": 0, "shot": "план", "move": "static|push|drift|shake", "react": {"ключ слушающего": "эмоция"},
   "react_action": {"ключ слушающего": "действие"}, "split": null или {"at": 0.5, "shot": "второй план", "move": "..."}}],
 "hl_lines": [номера реплик, где субтитры жёлтые — панчлайн и самые сильные]}"""


def _vocab(key: str) -> dict:
    s = series.S[key]
    return {"sets": s["sets"], "shots": s["shots"], "actions": s["actions"], "emotions": series.EMOTIONS, "cast": list(s["cast"])}


def _ask(conn: sqlite3.Connection, settings: dict, key: str, script: dict, durs: list[float]) -> dict:
    llm = LLM(settings.get("llm_base_url") or "https://openrouter.ai/api/v1", settings.get("llm_api_key") or "",
              settings.get("toon_montage_model") or MODEL, timeout=120)
    v = _vocab(key)
    lines = [{"i": i, "who": ln.get("who"), "text": ln.get("text"), "emotion": ln.get("emotion"), "action": ln.get("action"),
              "shot_hint": ln.get("shot"), "sec": d} for i, (ln, d) in enumerate(zip(script.get("lines") or [], durs))]
    system = ("Ты — монтажёр вертикальных мультфильмов для ленты Shorts. Задача — удержание: смена плана каждые 1,5–2,7 с, "
              "крупный план говорящего на сильных репликах, «двое»/«через плечо» на перепалке, «наезд» с движением push на панчлайне, "
              "shake — на шоке/ударе, общий — редко (завязка, поворот). Слушающим ставь реакцию — их лица в кадре. "
              f"Используй ТОЛЬКО словарь: {json.dumps(v, ensure_ascii=False)}. Ответ — только JSON по схеме:\n{SCHEMA}")
    user = (f"Сериал «{series.S[key]['title']}». Поворот на реплике {script.get('twist_at')}, панчлайн на {script.get('punchline_at')}.\n"
            f"Реплики с длительностью звука:\n{json.dumps(lines, ensure_ascii=False)}")
    text, usage = llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens=2500, temperature=0.3)
    record_usage(conn, "toon.montage", llm.model, usage)
    return parse_json(text)


def _rule(key: str, script: dict, i: int, ln: dict, n: int) -> dict:
    """Монтаж по правилам — если модель ошиблась или недоступна."""
    v = _vocab(key)
    who = ln.get("who")
    close = next((s for s in v["shots"] if s.startswith("крупно") and series.S[key]["cast"].get(who, {}).get("name", "") in s), None)
    hint = ln.get("shot") if ln.get("shot") in v["shots"] else None
    if i == script.get("punchline_at") or i == n - 1:
        return {"shot": close or hint or v["shots"][0], "move": "push"}
    if i == script.get("twist_at"):
        return {"shot": "общий" if "общий" in v["shots"] else (hint or v["shots"][0]), "move": "shake" if ln.get("emotion") in ("шок", "злость", "паника") else "static"}
    if i % 3 == 2 and "двое" in v["shots"]:
        return {"shot": "двое", "move": "drift"}
    return {"shot": hint or close or v["shots"][0], "move": "drift"}


def _chunks(text: str) -> list[str]:
    words = re.findall(r"\S+", text)
    out, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= 3 or re.search(r"[.!?…,:;—]$", w) and len(cur) >= 2:
            out.append(" ".join(cur)); cur = []
    if cur:
        out.append(" ".join(cur))
    return out


def plan(conn: sqlite3.Connection, settings: dict, key: str, script: dict, durs: list[float], envs: list[list[float]],
         fps: int = 30, log=print) -> dict:
    s = series.S[key]
    v = _vocab(key)
    lines = script.get("lines") or []
    try:
        m = _ask(conn, settings, key, script, durs)
    except (LLMError, ValueError) as e:
        log(f"монтажёр: модель не ответила ({e}) — монтаж по правилам")
        m = {}
    by_line = {int(x.get("line", -1)): x for x in (m.get("segments") or []) if isinstance(x, dict)}
    hl = {int(x) for x in (m.get("hl_lines") or []) if str(x).isdigit()} | {script.get("punchline_at")}
    st = m.get("set") if m.get("set") in v["sets"] else (script.get("set") if script.get("set") in v["sets"] else v["sets"][0])
    cast = [k for k in dict.fromkeys(ln.get("who") for ln in lines) if k in s["cast"]]
    segs, caps, t = [], [], LEAD
    total = LEAD + sum(durs) + GAP * (len(durs) - 1) + TAIL
    env_all = {k: [0.0] * (int(total * fps) + 2) for k in cast}
    for i, (ln, d) in enumerate(zip(lines, durs)):
        who = ln.get("who") if ln.get("who") in s["cast"] else cast[0]
        x = by_line.get(i) or {}
        rule = _rule(key, script, i, ln, len(lines))
        shot = x.get("shot") if x.get("shot") in v["shots"] else rule["shot"]
        move = x.get("move") if x.get("move") in ("static", "push", "drift", "shake") else rule["move"]
        react = {k: e for k, e in (x.get("react") or {}).items() if k in cast and k != who and e in series.EMOTIONS}
        ract = {k: a for k, a in (x.get("react_action") or {}).items() if k in cast and k != who and a in v["actions"]}
        others = [k for k in cast if k != who]
        base = {"who": who, "to": others[0] if others else None, "emotion": ln.get("emotion") if ln.get("emotion") in series.EMOTIONS else "спокойно",
                "action": ln.get("action") if ln.get("action") in v["actions"] else "talk", "react": react, "react_action": ract,
                "prop": ln.get("prop") if ln.get("prop") in s["props"] else None, "tv": (ln.get("tv") or None) if s.get("tv") else None,
                "text": ln.get("text")}
        t1 = t + d + (GAP if i < len(lines) - 1 else TAIL)
        sp = x.get("split") if isinstance(x.get("split"), dict) else None
        if d > SPLIT_OVER and not sp:
            sp = {"at": 0.5, "shot": "двое" if "двое" in v["shots"] and shot != "двое" else v["shots"][0], "move": "static"}
        if sp and sp.get("shot") in v["shots"]:
            mid = t + d * min(0.75, max(0.3, float(sp.get("at") or 0.5)))
            segs.append({**base, "t0": round(t, 3), "t1": round(mid, 3), "shot": shot, "move": move})
            segs.append({**base, "t0": round(mid, 3), "t1": round(t1, 3), "shot": sp["shot"], "move": sp.get("move") if sp.get("move") in ("static", "push", "drift", "shake") else "static"})
        else:
            segs.append({**base, "t0": round(t, 3), "t1": round(t1, 3), "shot": shot, "move": move})
        # рот: огибающая реплики в общую дорожку героя
        f0 = int(round(t * fps))
        for j, a in enumerate(envs[i] if i < len(envs) else []):
            if f0 + j < len(env_all[who]):
                env_all[who][f0 + j] = a
        # субтитры кусками, время пропорционально буквам
        parts = _chunks(ln.get("text") or "")
        total_chars = sum(len(p) for p in parts) or 1
        c = t
        for p in parts:
            dd = d * len(p) / total_chars
            caps.append({"t0": round(c, 3), "t1": round(c + dd, 3), "text": p, "hl": i in hl})
            c += dd
        t = t1
    return {"fps": fps, "duration": round(t, 3), "scene": s["scene"], "world": s.get("set_world"), "set": st, "cast": cast,
            "segments": segs, "env": env_all, "captions": caps, "tv": None, "caption_y": s.get("caption_y", 0.69)}
