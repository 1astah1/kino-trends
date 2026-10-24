"""Исходники для нарезки: полный фильм или серии на YouTube, найденные поиском и проверенные нейросетью.

Поиск идёт через yt-dlp (страницы поиска YouTube отдаются с сервера свободно). Кандидаты фильтруются
по длительности и мусорным словам, оцениваются эвристикой (официальный канал, совпадение названия,
длительность), затем модель решает, что из этого действительно полный фильм или серия, какая серия
и на каком языке. Скачивание не делается: ссылка уходит в конвейер нарезки.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import httpx
import yt_dlp
from rapidfuzz import fuzz

from .db import dumps, loads, now_iso
from .llm import LLM, LLMError, record_usage
from .text import norm

JUNK = re.compile(r"трейлер|тизер|обзор|реакци|разбор|пересказ|краткое содержание|за \d+ минут|тогда и сейчас|концерт|интервью|лучшие моменты|"
                  r"нарезк|момент|ost\b|саундтрек|песня|клип|актеры|актёры|как снимали|фильм о фильме|пародия|ремикс|trailer|review|recap|reaction|"
                  r"behind the scenes|подкаст|стрим|прохождение|игра\b", re.IGNORECASE)
UNSUITABLE = re.compile(r"тифлокоммент|сурдоперевод|audio description", re.IGNORECASE)
OFFICIAL = re.compile(r"мосфильм|mosfilm|ств\b|кинокомпания|start\b|wink|стс\b|тнт\b|нтв\b|россия 1|россия-1|первый канал|ленфильм|горького|"
                      r"star media|кинопоиск|kinopoisk|okko|\bиви\b|ivi\b|premier|more\.tv|дом кино|dom kino|русские сериалы|сериалы нтв|"
                      r"central partnership|централ партнершип|киностудия|одесская киностудия|беларусьфильм|киноконцерн|телеканал|tv-3|тв-3|"
                      r"пятница|ю\b|домашний|рен тв|рен-тв|че\b|мир\b|звезда|карусель|союзмультфильм|soyuzmultfilm|crunchyroll|wakanim|"
                      r"netflix|hbo|amazon|paramount|disney|sony pictures|warner|universal|lionsgate|mgm|studiocanal|gaumont|pathé", re.IGNORECASE)

SYSTEM_VERIFY = (
    "Ты редактор видеотеки. Тебе дают название произведения и список найденных на YouTube видео (заголовок, канал, длительность, просмотры). "
    "Определи для каждого, является ли оно полным фильмом или полной серией именно этого произведения (а не трейлером, пересказом, обзором, "
    "нарезкой, другим фильмом с похожим названием), какая это серия и сезон, на каком языке (русский дубляж, оригинал, другой). "
    'Отвечай только JSON: {"items": [{"id": "youtube id", "is_full": true, "type": "film|episode|pack|other", "season": 1, "episode": 1, '
    '"language": "русский дубляж", "trust": 0.9, "note": "коротко"}], "best": ["id лучшего источника", "следующий"]}. '
    "Лучший источник: официальный канал правообладателя, русская озвучка, нормальная длительность, без обрезаний. Поле note не длиннее 8 слов."
)


class _L:
    def debug(self, m): pass
    def info(self, m): pass
    def warning(self, m): pass
    def error(self, m): pass


def _search(query: str, n: int = 10) -> list[dict]:
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True, "ignoreerrors": True, "socket_timeout": 30,
            "extractor_args": {"youtube": {"lang": ["ru"]}}, "geo_bypass_country": "RU", "logger": _L()}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(f"ytsearch{n}:{query}", download=False)
    out = []
    for e in (info or {}).get("entries") or []:
        if not e or not e.get("id"):
            continue
        out.append({"video_id": e["id"], "title": e.get("title") or "", "channel": e.get("channel") or e.get("uploader") or "",
                    "duration_s": int(e.get("duration") or 0), "views": e.get("view_count"), "url": f"https://www.youtube.com/watch?v={e['id']}"})
    return out


def _search_rutube(query: str, n: int = 10) -> list[dict]:
    """Публичный поиск Rutube: там официально лежат многие русские сериалы и фильмы."""
    with httpx.Client(timeout=20, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0"}) as c:
        r = c.get("https://rutube.ru/api/search/video/", params={"query": query, "format": "json"})
    if r.status_code != 200:
        return []
    out = []
    for it in (r.json().get("results") or [])[:n]:
        vid = it.get("id")
        if not vid:
            continue
        out.append({"video_id": f"rt_{vid}", "title": it.get("title") or "", "channel": (it.get("author") or {}).get("name") or "",
                    "duration_s": int(it.get("duration") or 0), "views": it.get("hits"), "url": it.get("video_url") or f"https://rutube.ru/video/{vid}/"})
    return out


def queries_for(label: str, year: int | None, kind: str, label_en: str | None) -> list[str]:
    y = f" {year}" if year else ""
    if kind in ("series", "animated_series", "anime", "tv_show"):
        return [f"{label} 1 серия", f"{label} все серии", f"{label} 1 сезон 1 серия русская озвучка"]
    qs = [f"{label}{y} фильм полностью", f"{label} фильм{y}"]
    if label_en and norm(label_en) != norm(label):
        qs.append(f"{label_en}{y} full movie на русском")
    return qs


def heuristic(cand: dict, label: str, kind: str) -> float:
    d = cand["duration_s"]
    is_series = kind in ("series", "animated_series", "anime", "tv_show")
    if is_series:
        if d < 900:
            return -1
        fit = 1.0 if d <= 9000 else 0.6
    else:
        if d < 3000:
            return -1
        fit = 1.0 if 3600 <= d <= 11000 else 0.5
    if JUNK.search(cand["title"]):
        return -1
    name = fuzz.partial_ratio(norm(label), norm(cand["title"])) / 100.0
    official = 1.0 if OFFICIAL.search(cand["channel"] or "") else 0.0
    penalty = 2.0 if re.search(r"тифлокоммент|сурдоперевод|с субтитрами для|audio description", (cand["title"] or "") + " " + (cand["channel"] or ""), re.IGNORECASE) else 0.0
    return round(fit * 2 + name * 3 + official * 2.5 - penalty, 2)


def title_names(conn: sqlite3.Connection, title_id: str, label: str, label_en: str | None) -> list[str]:
    names = [n for n in (label, label_en) if n]
    names += [r[0] for r in conn.execute("SELECT alias FROM title_aliases WHERE title_id = ? LIMIT 30", (title_id,))]
    return [norm(n) for n in names if n and len(norm(n)) >= 3]


def name_matches(names: list[str], video_title: str) -> bool:
    vt = norm(video_title)
    return any(fuzz.partial_ratio(n, vt) >= 60 for n in names)


def find_sources(conn: sqlite3.Connection, llm: LLM | None, title: dict, log=print) -> list[dict]:
    """Ищет, оценивает, при наличии модели проверяет и сохраняет источники произведения."""
    label = title["label"]
    kind = title.get("kind") or "film"
    seen: dict[str, dict] = {}
    qs = queries_for(label, title.get("year"), kind, title.get("label_en"))
    for q in qs:
        try:
            for c in _search(q, 10):
                if c["video_id"] not in seen:
                    seen[c["video_id"]] = c
        except Exception as e:  # noqa: BLE001
            log(f"поиск исходников «{q}»: {e}", "warn")
        time.sleep(0.3)
    rt_queries = [qs[0]]
    if kind in ("series", "animated_series", "anime", "tv_show"):
        rt_queries.append(f"{label} все серии")
    else:
        rt_queries.append(f"{label}{' ' + str(title.get('year')) if title.get('year') else ''}")
    for rq in rt_queries:
        try:
            for c in _search_rutube(rq, 10):
                if c["video_id"] not in seen:
                    seen[c["video_id"]] = c
        except Exception as e:  # noqa: BLE001
            log(f"поиск на Rutube «{rq}»: {e}", "warn")
        time.sleep(0.3)
    cands = []
    for c in seen.values():
        sc = heuristic(c, label, kind)
        if sc > 0:
            c["score"] = sc
            cands.append(c)
    cands.sort(key=lambda c: -c["score"])
    cands = cands[:12]
    verdicts: dict[str, dict] = {}
    best_ids: list[str] = []
    if cands and llm and llm.configured:
        lines = [f"- id={c['video_id']} | {'Rutube' if c['video_id'].startswith('rt_') else 'YouTube'} | {c['duration_s'] // 60} мин | канал: {c['channel']} | просмотров: {c['views'] or '?'} | {c['title'][:110]}" for c in cands]
        user = f"Произведение: {label} ({title.get('year') or 'год неизвестен'}, {kind}{', оригинал: ' + title['label_en'] if title.get('label_en') else ''}).\nНайдено:\n" + "\n".join(lines)
        try:
            try:
                data, usage = llm.ask_json(SYSTEM_VERIFY, user, max_tokens=1800)
            except LLMError:
                # ответ обрезался или не разобрался: повторяем на шести лучших кандидатах
                short = "\n".join(lines[:6])
                user2 = user.split("Найдено:\n")[0] + "Найдено:\n" + short
                data, usage = llm.ask_json(SYSTEM_VERIFY, user2, max_tokens=1200)
            record_usage(conn, "sources", llm.model, usage, ok=True, note=title["title_id"])
            for it in data.get("items") or []:
                if it.get("id"):
                    verdicts[str(it["id"])] = it
            best_ids = [str(x) for x in (data.get("best") or [])]
        except LLMError as e:
            record_usage(conn, "sources", llm.model, None, ok=False, note=str(e)[:200])
            log(f"проверка исходников моделью не удалась: {e}", "warn")
    ts = now_iso()
    conn.execute("DELETE FROM title_sources WHERE title_id = ?", (title["title_id"],))
    names = title_names(conn, title["title_id"], label, title.get("label_en"))
    rows = []
    for c in cands:
        v = verdicts.get(c["video_id"], {})
        note_l = str(v.get("note") or "").lower()
        name_ok = name_matches(names, c["title"]) or float(v.get("trust") or 0) >= 0.9
        verified = 1 if v.get("is_full") and float(v.get("trust") or 0) >= 0.6 and name_ok and "не совпада" not in note_l and "другой" not in note_l else 0
        if UNSUITABLE.search(c["title"] or ""):
            verified = 0
            v = dict(v, note=((v.get("note") or "") + " · версия с тифлокомментариями, для нарезки не годится").strip(" ·"))
        bonus = (3 if c["video_id"] in best_ids[:1] else 1.5 if c["video_id"] in best_ids else 0) + (float(v.get("trust") or 0) * 2 if verified else -2 if v else 0)
        rows.append((title["title_id"], c["video_id"], c["url"], c["title"], c["channel"], c["duration_s"], c["views"],
                     v.get("type") or ("episode" if kind in ("series", "animated_series", "anime", "tv_show") else "film"),
                     v.get("season") if isinstance(v.get("season"), int) else None, v.get("episode") if isinstance(v.get("episode"), int) else None,
                     v.get("language"), float(v.get("trust") or 0) if v else None, verified, (v.get("note") or "")[:200] if v else "не проверено моделью",
                     round(c["score"] + bonus, 2), ts))
    conn.executemany("""INSERT OR REPLACE INTO title_sources(title_id, video_id, url, label, channel, duration_s, views, kind, season, episode, language, trust, verified, note, score, found_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (f"sources_checked:{title['title_id']}", ts))
    conn.commit()
    return get_sources(conn, title["title_id"])


# типы произведений, для которых ищем исходники (franchise — зонтик без своего видео)
SOURCE_KINDS = {"film", "series", "animated", "animated_series", "anime", "tv_show"}


def top_candidates(conn: sqlite3.Connection, limit: int = 30, period: str = "30", lang: str = "ru") -> list[dict]:
    """Произведения, где найденный исходник сразу расширит выбор конвейера: самые популярные в Shorts,
    кино/сериалы/анимация, права не «нельзя», проверенного исходника ещё нет. Порядок — по score."""
    from . import rights, stats
    from .stats import Filters

    out = stats.films(conn, Filters(period, lang=lang), limit=400, sort="score")
    ids = [it["title_id"] for it in out["items"]]
    levels = rights.levels(conn, ids) if ids else {}
    picked: list[dict] = []
    for it in out["items"]:
        if it.get("kind") not in SOURCE_KINDS:
            continue
        if ((levels.get(it["title_id"]) or {}).get("level") or "unknown") == "block":
            continue
        if any(x.get("verified") for x in get_sources(conn, it["title_id"])):
            continue
        picked.append({"title_id": it["title_id"], "label": it["label"], "label_en": it.get("label_en"), "year": it.get("year"), "kind": it["kind"]})
        if len(picked) >= limit:
            break
    return picked


def get_sources(conn: sqlite3.Connection, title_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM title_sources WHERE title_id = ? ORDER BY verified DESC, score DESC LIMIT 12", (title_id,)).fetchall()
    return [dict(r) for r in rows]


def best_source(conn: sqlite3.Connection, title_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM title_sources WHERE title_id = ? ORDER BY verified DESC, score DESC LIMIT 1", (title_id,)).fetchone()
    return dict(r) if r else None


def checked_at(conn: sqlite3.Connection, title_id: str) -> str | None:
    r = conn.execute("SELECT value FROM meta WHERE key = ?", (f"sources_checked:{title_id}",)).fetchone()
    return r["value"] if r else None


def ensure_for_titles(conn: sqlite3.Connection, llm: LLM | None, titles: list[dict], max_titles: int = 10, max_age_days: int = 14, log=print) -> int:
    """Ищет исходники для произведений, у которых их ещё нет или они старше max_age_days."""
    done = 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for t in titles:
        if done >= max_titles:
            break
        ca = checked_at(conn, t["title_id"])
        if ca and ca >= cutoff:
            continue
        res = find_sources(conn, llm, t, log=log)
        done += 1
        log(f"исходники для «{t['label']}»: найдено {len(res)}, проверенных {sum(1 for r in res if r['verified'])}")
    return done
