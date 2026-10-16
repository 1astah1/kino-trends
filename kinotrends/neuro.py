"""Нейро-часть: опознание произведения по кадру и тексту, сцены и хуки по произведению."""
from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import Counter

from rapidfuzz import fuzz

from .db import dumps, loads, now_iso
from .llm import LLM, LLMError, Usage, record_usage, usage_today
from .recognize import Recognizer
from .text import norm, squash
from .thumbs import get_thumb

KINDS = {"film", "series", "animated", "animated_series", "anime", "tv_show"}

SYSTEM_RECOGNIZE = (
    "Ты киноэксперт. По кадру из вертикального ролика YouTube Shorts, его заголовку, описанию, хэштегам и каналу "
    "определи, из какого фильма, сериала, мультфильма, аниме или телешоу взят фрагмент. Учитывай актёров, декорации, "
    "стиль съёмки, субтитры и подсказки в тексте. Отвечай только JSON без пояснений вида "
    '{"is_film": true, "title_ru": "название по-русски", "title_original": "оригинальное название", "year": 2010, '
    '"kind": "film|series|animated|animated_series|anime|tv_show", "confidence": 0.0, "clues": "по каким признакам узнал, коротко"}. '
    "is_film=false, если это не фрагмент произведения (блогер, мем, игра, новости, реклама). Не выдумывай: если не уверен, ставь confidence ниже 0.5."
)

SYSTEM_INSIGHTS = (
    "Ты аналитик вирусных нарезок фильмов и сериалов для YouTube Shorts. Отвечай только JSON без пояснений."
)


def _strip_known(text: str, evidence: str | None) -> str:
    if not text or not evidence:
        return text or ""
    ev = evidence.lstrip("#").strip()
    if len(ev) < 3:
        return text
    out = re.sub(re.escape(ev), "…", text, flags=re.IGNORECASE)
    sq = squash(ev)
    if sq:
        out = re.sub(r"#\w+", lambda m: "" if squash(m.group(0)) == sq else m.group(0), out)
    return out


def build_user_prompt(v: sqlite3.Row | dict, channel_titles: list[str], hints: list[str], hide_evidence: str | None = None) -> str:
    title = _strip_known(v["title"] or "", hide_evidence)
    desc = _strip_known((v["description"] or "")[:400], hide_evidence)
    tags = loads(v["hashtags"], [])
    if hide_evidence:
        tags = [t for t in tags if squash(t) != squash(hide_evidence)]
    parts = [f"Заголовок: {title}"]
    if desc.strip():
        parts.append(f"Описание: {desc}")
    if tags:
        parts.append("Хэштеги: " + " ".join("#" + t for t in tags[:12]))
    if v["channel_name"]:
        parts.append(f"Канал: {v['channel_name']}")
    if channel_titles:
        parts.append("Этот канал раньше резал: " + "; ".join(channel_titles[:6]))
    if hints:
        parts.append("Словарь нашёл похожие названия (могут быть ложными): " + "; ".join(hints[:5]))
    if v["duration_s"]:
        parts.append(f"Длительность ролика: {v['duration_s']} с")
    parts.append("На картинке кадр из ролика.")
    return "\n".join(parts)


def map_answer(conn: sqlite3.Connection, rec: Recognizer, ans: dict, create: bool = True) -> tuple[str | None, str]:
    """Сопоставляет ответ модели со словарём. Возвращает (title_id, как_сопоставили)."""
    if not ans.get("is_film") or not (ans.get("title_ru") or ans.get("title_original")):
        return None, "not_film"
    year = ans.get("year") if isinstance(ans.get("year"), int) else None
    for q in (ans.get("title_ru"), ans.get("title_original")):
        if not q or len(str(q)) < 2:
            continue
        q = str(q)
        nz = norm(q)
        # точное совпадение по псевдонимам
        ids = list(rec.norm_index.get(nz, ()))
        if ids:
            tid, _ = rec._pick(ids, year)
            return tid, "exact"
        row = conn.execute("SELECT id, year FROM titles WHERE lower(label_ru) = ? OR lower(label_en) = ? ORDER BY sitelinks DESC LIMIT 1",
                           (q.lower(), q.lower())).fetchone()
        if row:
            return row["id"], "exact_db"
        for tid, score in rec.search(q, limit=6):
            meta = rec.meta.get(tid)
            if not meta:
                continue
            label = meta[3] or ""
            ratio = fuzz.ratio(nz, norm(label))
            ty = meta[0]
            year_ok = year is None or ty is None or abs(ty - year) <= 1
            if ratio >= 88 and year_ok:
                return tid, "fuzzy"
    if create and float(ans.get("confidence") or 0) >= 0.7:
        label = str(ans.get("title_ru") or ans.get("title_original")).strip()[:120]
        kind = ans.get("kind") if ans.get("kind") in KINDS else "film"
        tid = f"neu_{int(time.time() * 1000):x}"
        conn.execute("INSERT INTO titles(id, label_ru, label_en, year, kind, sitelinks, genres, countries, is_manual, source, updated_at) VALUES (?,?,?,?,?,0,'[]','[]',0,'neuro',?)",
                     (tid, label, (ans.get("title_original") or None), year, kind, now_iso()))
        for a in (label, ans.get("title_original")):
            if a:
                conn.execute("INSERT OR IGNORE INTO title_aliases(title_id, alias, norm, lang, source) VALUES (?,?,?,?,'neuro')", (tid, a, norm(a), "ru"))
                rec.add_alias(tid, a)
        with rec.lock:
            rec.meta[tid] = (year, 0, kind, label)
        conn.commit()
        return tid, "created"
    return None, "unmatched"


def recognize_one(conn: sqlite3.Connection, rec: Recognizer, llm: LLM, v: sqlite3.Row, hide_evidence: str | None = None,
                  create: bool = True) -> dict:
    """Один ролик: кадр + текст → модель → словарь. Возвращает словарь с ответом и сопоставлением (базу не меняет)."""
    image = get_thumb(v["video_id"], v["thumb"])
    channel_titles = []
    if v["channel_id"]:
        channel_titles = [r[0] for r in conn.execute(
            """SELECT COALESCE(t.label_ru, t.label_en) FROM videos x JOIN titles t ON t.id = x.title_id
               WHERE x.channel_id = ? AND x.recog_conf >= 0.8 AND x.video_id != ? GROUP BY t.id ORDER BY COUNT(*) DESC LIMIT 6""",
            (v["channel_id"], v["video_id"]))]
    hints = []
    alt = loads(v["recog_candidates"], {}) if "recog_candidates" in v.keys() else {}
    for tid, _ in (alt.get("alternatives") or [])[:3]:
        m = rec.meta.get(tid)
        if m and m[3]:
            hints.append(f"{m[3]} ({m[0]})" if m[0] else m[3])
    prompt = build_user_prompt(v, channel_titles, hints, hide_evidence)
    t0 = time.time()
    ans, usage = llm.ask_json(SYSTEM_RECOGNIZE, prompt, image=image, max_tokens=300)
    title_id, how = map_answer(conn, rec, ans, create=create)
    return {"answer": ans, "title_id": title_id, "how": how, "usage": usage, "seconds": round(time.time() - t0, 1), "has_image": image is not None}


def apply_result(conn: sqlite3.Connection, video_id: str, res: dict, model: str) -> None:
    ans = res["answer"]
    conf = float(ans.get("confidence") or 0)
    ts = now_iso()
    payload = dumps({"model": model, "answer": ans, "how": res["how"], "title_id": res["title_id"], "has_image": res["has_image"]})
    if res["title_id"] and conf >= 0.5:
        final = round(min(0.9, conf), 2)
        evidence = f"кадр: {ans.get('title_ru') or ans.get('title_original')}; {str(ans.get('clues') or '')[:90]}"
        conn.execute("""UPDATE videos SET title_id = ?, recog_conf = ?, recog_method = 'neuro', recog_evidence = ?, recog_at = ?, neuro_at = ?, neuro_json = ?
                        WHERE video_id = ? AND recog_manual = 0""", (res["title_id"], final, evidence, ts, ts, payload, video_id))
    else:
        note = "нейро: не фильм" if ans.get("is_film") is False else f"нейро не уверена: {ans.get('title_ru') or '—'}"
        conn.execute("""UPDATE videos SET neuro_at = ?, neuro_json = ?, recog_evidence = CASE WHEN title_id IS NULL THEN ? ELSE recog_evidence END
                        WHERE video_id = ? AND recog_manual = 0""", (ts, payload, note, video_id))
    conn.commit()


def pending(conn: sqlite3.Connection, min_views: int, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM videos WHERE details_at IS NOT NULL AND status = 'ok' AND recog_manual = 0 AND neuro_at IS NULL
             AND lang IN ('ru', 'uk', 'kk') AND (title_id IS NULL OR recog_conf < 0.6) AND COALESCE(views, 0) >= ?
           ORDER BY COALESCE(views, 0) DESC LIMIT ?""", (min_views, limit)).fetchall()


def run_batch(conn: sqlite3.Connection, rec: Recognizer, llm: LLM, settings: dict, log=print, should_stop=lambda: False) -> dict:
    """Нейро-опознание очереди с учётом дневного бюджета."""
    stats = {"checked": 0, "recognized": 0, "not_film": 0, "unsure": 0, "errors": 0, "cost_usd": 0.0, "created": 0}
    if not llm.configured or settings.get("neuro_enabled", "1") != "1":
        return stats
    limit = int(settings.get("neuro_per_run") or 300)
    min_views = int(settings.get("neuro_min_views") or 0)
    budget = float(settings.get("neuro_daily_usd") or 3)
    spent = usage_today(conn)["cost_usd"]
    if spent >= budget:
        log(f"нейро: дневной бюджет {budget} $ исчерпан ({spent:.2f} $)", "warn")
        return stats
    queue = pending(conn, min_views, limit)
    for i, v in enumerate(queue, 1):
        if should_stop():
            break
        try:
            res = recognize_one(conn, rec, llm, v)
        except LLMError as e:
            stats["errors"] += 1
            record_usage(conn, "recognize", llm.model, None, ok=False, note=str(e)[:200])
            if stats["errors"] >= 5 and stats["checked"] == 0:
                log(f"нейро: модель не отвечает ({e}), останавливаю", "error")
                break
            continue
        except Exception as e:  # noqa: BLE001
            stats["errors"] += 1
            record_usage(conn, "recognize", llm.model, None, ok=False, note=str(e)[:200])
            continue
        record_usage(conn, "recognize", llm.model, res["usage"], ok=True, note=v["video_id"])
        apply_result(conn, v["video_id"], res, llm.model)
        stats["checked"] += 1
        if res["usage"].cost_usd:
            stats["cost_usd"] += res["usage"].cost_usd
        if res["title_id"] and float(res["answer"].get("confidence") or 0) >= 0.5:
            stats["recognized"] += 1
            if res["how"] == "created":
                stats["created"] += 1
        elif res["answer"].get("is_film") is False:
            stats["not_film"] += 1
        else:
            stats["unsure"] += 1
        if i % 25 == 0:
            spent = usage_today(conn)["cost_usd"]
            log(f"нейро: {i}/{len(queue)}, опознано {stats['recognized']}, потрачено сегодня {spent:.2f} $")
            if spent >= budget:
                log(f"нейро: дневной бюджет {budget} $ исчерпан", "warn")
                break
    stats["cost_usd"] = round(stats["cost_usd"], 4)
    return stats


# ---------- сцены и хуки ----------
def title_insights(conn: sqlite3.Connection, llm: LLM, title_id: str, videos: list[dict], title: dict) -> dict:
    vids = sorted(videos, key=lambda x: -(x.get("views") or 0))[:40]
    lines = [f"{i + 1}. [{v['video_id']}] {v.get('views') or 0} просм., {v.get('duration_s') or '?'} с, канал {v.get('channel_name') or '?'}: {v.get('title') or ''}" for i, v in enumerate(vids)]
    user = (
        f"Произведение: {title.get('label')} ({title.get('year') or 'год неизвестен'}, {title.get('kind_ru') or title.get('kind')}).\n"
        f"Вот {len(vids)} роликов-нарезок с заголовками и просмотрами:\n" + "\n".join(lines) +
        "\n\nЗадача:\n1) Сгруппируй ролики по сценам или моментам произведения. Название сцены должно объяснять монтажёру, что именно резать: персонажи, место, что происходит. "
        "Одна сцена может объединять несколько роликов, в video_ids давай id из квадратных скобок. Для каждой сцены добавь quotes: 1–3 реплики персонажей "
        "из самой сцены произведения на русском (не заголовки роликов и не твои выдумки; если не помнишь дословно, оставь пустой список), по ним монтажёр найдёт место в субтитрах. Если знаешь, где сцена в фильме, "
        "укажи where: «начало», «середина», «финал» или примерную минуту.\n"
        "2) Предложи 5 заголовков-хуков в стиле самых просматриваемых роликов: без названия произведения, до 60 символов, можно эмодзи.\n"
        "3) Дай 3 коротких совета по длительности, подаче и оформлению для этого произведения.\n"
        'Ответ JSON: {"scenes": [{"name": "…", "what_to_cut": "…", "quotes": ["…"], "where": "…", "video_ids": ["…"]}], "hooks": ["…"], "advice": ["…"]}'
    )
    try:
        data, usage = llm.ask_json(SYSTEM_INSIGHTS, user, max_tokens=2400)
    except LLMError:
        # ответ не разобрался (обычно обрезан): повторяем на меньшем списке роликов
        vids = vids[:20]
        lines = lines[:20]
        user = user.split("\n\nЗадача:")[0].rsplit("\n", len(lines))[0] if False else user
        short_user = (f"Произведение: {title.get('label')} ({title.get('year') or 'год неизвестен'}, {title.get('kind_ru') or title.get('kind')}).\n"
                      f"Вот {len(vids)} роликов-нарезок с заголовками и просмотрами:\n" + "\n".join(lines) + "\n\nЗадача:" + user.split("\n\nЗадача:", 1)[1])
        data, usage = llm.ask_json(SYSTEM_INSIGHTS, short_user, max_tokens=2400)
    record_usage(conn, "insights", llm.model, usage, ok=True, note=title_id)
    by_id = {v["video_id"]: v for v in vids}
    scenes = []
    for sc in data.get("scenes") or []:
        ids = [i for i in (sc.get("video_ids") or []) if i in by_id]
        views = sum(by_id[i].get("views") or 0 for i in ids)
        scenes.append({"name": str(sc.get("name") or "")[:120], "what_to_cut": str(sc.get("what_to_cut") or "")[:400], "video_ids": ids,
                       "quotes": [str(q)[:160] for q in (sc.get("quotes") or []) if q][:3], "where": str(sc.get("where") or "")[:60],
                       "n": len(ids), "views": views, "top": [{"video_id": i, "title": by_id[i].get("title"), "views": by_id[i].get("views"), "url": by_id[i].get("url")} for i in ids[:3]]})
    scenes.sort(key=lambda s: -s["views"])
    out = {"generated_at": now_iso(), "model": llm.model, "scenes": scenes, "hooks": [str(h)[:120] for h in (data.get("hooks") or [])][:8],
           "advice": [str(a)[:300] for a in (data.get("advice") or [])][:5], "n_videos": len(vids), "cost_usd": usage.cost_usd}
    conn.execute("INSERT INTO title_insights(title_id, generated_at, model, data) VALUES (?,?,?,?) ON CONFLICT(title_id) DO UPDATE SET generated_at = excluded.generated_at, model = excluded.model, data = excluded.data",
                 (title_id, out["generated_at"], llm.model, dumps(out)))
    conn.commit()
    return out


def get_insights(conn: sqlite3.Connection, title_id: str) -> dict | None:
    r = conn.execute("SELECT data FROM title_insights WHERE title_id = ?", (title_id,)).fetchone()
    return loads(r["data"], None) if r else None


SYSTEM_EXPLAIN = (
    "Ты продюсер канала нарезок фильмов и сериалов для YouTube Shorts в России и СНГ. Объясняй коротко, конкретно и по-человечески, "
    "без воды и без повторения цифр подряд. Отвечай только JSON."
)


def explain_title(conn: sqlite3.Connection, llm: LLM, item: dict, refs: list[dict], sources: list[dict]) -> str:
    """Человеческое объяснение, почему это произведение стоит резать сейчас и как."""
    ref_lines = [f"- {r.get('views') or 0} просм., {r.get('duration_s') or '?'} с: {r.get('title') or ''}" for r in refs[:6]]
    src = sources[0] if sources else None
    user = (
        f"Произведение: {item['label']} ({item.get('year') or '—'}, {item.get('kind_ru') or item.get('kind')}). Жанры: {', '.join(item.get('genres') or []) or '—'}.\n"
        f"Статистика чужих нарезок за 30 дней: {item.get('n_shorts')} роликов у {item.get('n_channels')} каналов, медиана просмотров {item.get('views_median')}, "
        f"просмотров в день суммарно {item.get('vpd_sum')}, новых роликов за неделю {item.get('n_fresh7')}.\n"
        f"Лучшие чужие ролики:\n" + ("\n".join(ref_lines) or "нет") + "\n"
        f"Исходник: {src['label'] + ' (' + (src.get('channel') or '') + ')' if src else 'не найден'}.\n"
        "Напиши JSON {\"why\": \"2–4 предложения: почему это стоит резать сейчас, что именно в этом произведении цепляет зрителя Shorts, "
        "что резать в первую очередь и чего избегать\", \"risk\": \"одна фраза про риск (конкуренция, права, узость аудитории) или пусто\"}"
    )
    data, usage = llm.ask_json(SYSTEM_EXPLAIN, user, max_tokens=400)
    record_usage(conn, "explain", llm.model, usage, ok=True, note=item["title_id"])
    text = str(data.get("why") or "").strip()
    risk = str(data.get("risk") or "").strip()
    if risk:
        text += f"\nРиск: {risk}"
    conn.execute("INSERT INTO title_explain(title_id, generated_at, model, text) VALUES (?,?,?,?) ON CONFLICT(title_id) DO UPDATE SET generated_at = excluded.generated_at, model = excluded.model, text = excluded.text",
                 (item["title_id"], now_iso(), llm.model, text))
    conn.commit()
    return text


def get_explain(conn: sqlite3.Connection, title_id: str) -> dict | None:
    r = conn.execute("SELECT generated_at, model, text FROM title_explain WHERE title_id = ?", (title_id,)).fetchone()
    return dict(r) if r else None
