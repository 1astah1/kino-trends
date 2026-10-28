"""Рекомендации «что резать сегодня»: спрос, предложение, свежесть, план нарезки; дайджест и тревоги для Telegram."""
from __future__ import annotations

import html
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime

from . import rights
from .db import dumps, get_meta, get_setting, loads, set_meta
from .recognize import GENERIC_TAGS
from .stats import FEATURE_RU, Filters, _base_rows, _median, films
from .text import duration_bucket

WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def _fmt(n: float | int | None) -> str:
    if n is None:
        return "—"
    a = abs(n)
    if a >= 1e6:
        return f"{n / 1e6:.1f}".replace(".", ",").replace(",0", "") + " млн"
    if a >= 1e3:
        return f"{round(n / 1e3)} тыс."
    return str(round(n))


def _plan(vids: list[sqlite3.Row], global_time: str) -> dict:
    """План нарезки по роликам произведения: длительность, хук, время, хэштеги, референсы."""
    views = [(r["views"] or 0) for r in vids]
    top_cut = sorted(views)[-max(1, len(views) // 3):] if views else []
    top_min = min(top_cut) if top_cut else 0
    # длительность: корзина с лучшей медианой среди корзин с ≥2 роликами
    buckets: dict[str, list[int]] = defaultdict(list)
    for r in vids:
        if r["duration_s"]:
            buckets[duration_bucket(r["duration_s"])].append(r["views"] or 0)
    dur = None
    cands = [(k, _median(v)) for k, v in buckets.items() if len(v) >= 2]
    if cands:
        dur = max(cands, key=lambda x: x[1])[0]
    elif buckets:
        dur = max(buckets.items(), key=lambda kv: _median(kv[1]))[0]
    # хук: признак с лучшей медианой среди встречающихся ≥2 раз
    feats: dict[str, list[int]] = defaultdict(list)
    for r in vids:
        fe = loads(r["features"], None) or {}
        for k, val in fe.items():
            if k in FEATURE_RU and val:
                feats[FEATURE_RU[k]].append(r["views"] or 0)
    hook = None
    fc = [(k, _median(v)) for k, v in feats.items() if len(v) >= 2]
    if fc:
        hook = max(fc, key=lambda x: x[1])[0]
    # время: самые частые день и час у верхней трети роликов
    best_time = None
    tops = [r for r in vids if r["published_at"] and (r["views"] or 0) >= top_min]
    if len(tops) >= 3:
        wd = Counter(); hr = Counter()
        for r in tops:
            dt = datetime.strptime(r["published_at"][:19], "%Y-%m-%dT%H:%M:%S")
            wd[WD[dt.weekday()]] += 1
            hr[(dt.hour + 3) % 24] += 1
        h = hr.most_common(1)[0][0]
        best_time = f"{wd.most_common(1)[0][0]}, {h:02d}:00–{(h + 2) % 24:02d}:00 МСК"
    # хэштеги
    tags = Counter()
    for r in vids:
        for t in loads(r["hashtags"], [])[:10]:
            if t not in GENERIC_TAGS or t in ("фильм", "кино", "сериал", "моменты"):
                tags[t] += 1
    refs = sorted(vids, key=lambda r: -(r["views"] or 0))[:3]
    return {
        "duration": dur, "hook": hook, "best_time": best_time or global_time, "best_time_is_global": best_time is None,
        "hashtags": [t for t, _ in tags.most_common(6)],
        "refs": [{"video_id": r["video_id"], "title": r["title"], "views": r["views"], "channel_name": r["channel_name"],
                  "url": f"https://www.youtube.com/shorts/{r['video_id']}"} for r in refs],
    }


def _global_time(rows: list[sqlite3.Row]) -> str:
    wd = Counter(); hr = Counter()
    for r in rows:
        if r["published_at"] and (r["views"] or 0) > 0:
            dt = datetime.strptime(r["published_at"][:19], "%Y-%m-%dT%H:%M:%S")
            wd[WD[dt.weekday()]] += r["views"] or 0
            hr[(dt.hour + 3) % 24] += r["views"] or 0
    if not hr:
        return "сб–вс, 19:00–21:00 МСК"
    h = hr.most_common(1)[0][0]
    return f"{wd.most_common(1)[0][0]}, {h:02d}:00–{(h + 2) % 24:02d}:00 МСК"


def today(conn: sqlite3.Connection, f: Filters, limit: int = 10) -> dict:
    data = films(conn, f, limit=max(400, limit), sort="score")["items"]
    rows = _base_rows(conn, f)
    by_title: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        by_title[r["title_id"]].append(r)
    gtime = _global_time(rows)
    medians = [g["views_median"] for g in data] or [0]
    med_p75 = sorted(medians)[int(len(medians) * 0.75)] if medians else 0
    insights = {r["title_id"]: loads(r["data"], None) for r in conn.execute("SELECT title_id, data FROM title_insights")}
    items = []
    for g in data[:max(150, limit)]:
        vids = by_title.get(g["title_id"], [])
        why = [f"{_fmt(g['vpd_sum'])} просмотров в день по {g['n_shorts']} роликам, медиана {_fmt(g['views_median'])}"]
        supply = 0
        if g["growth7_measured"] and g["growth7"] > 0:
            why.append(f"+{_fmt(g['growth7'])} просмотров за 7 дней по точным замерам")
        if g["n_fresh7"]:
            why.append(f"{g['n_fresh7']} новых роликов за неделю, тема живая")
        if g["n_channels"] <= 2:
            why.append(f"режут всего {g['n_channels']} канал{'' if g['n_channels'] == 1 else 'а'}: ниша свободна")
            supply = 20
        elif g["n_channels"] <= 5:
            why.append(f"{g['n_channels']} каналов: конкуренция умеренная")
            supply = 10
        else:
            why.append(f"уже {g['n_channels']} каналов: нужен свой угол подачи")
            supply = -10
        if g["views_median"] >= med_p75 and g["n_shorts"] >= 2:
            why.append("медиана просмотров в верхней четверти: заходит стабильно, а не одним вирусным роликом")
        plan = _plan(vids, gtime)
        ins = insights.get(g["title_id"])
        momentum = 10 if g["n_fresh7"] >= 2 else (5 if g["n_fresh7"] else 0)
        interest_bonus = 0
        if g.get("outlier_share") is not None and g["outlier_rated"] >= 2 and g["outlier_share"] >= 0.3:
            why.append(f"ролики по теме бьют норму своих каналов: {g['outliers']} из {g['outlier_rated']} набрали в 3+ раза больше обычного для канала (максимум ×{g['outlier_max']:.0f})")
            interest_bonus += 8
        if g.get("small_channel_median") and g["small_channel_n"] >= 2 and g["small_channel_median"] >= 100_000:
            why.append(f"тема вытягивает даже маленькие каналы: медиана {_fmt(g['small_channel_median'])} у каналов до 100 тыс. подписчиков")
            interest_bonus += 6
        if g.get("wiki_ratio") and g["wiki_ratio"] >= 1.5:
            why.append(f"интерес к произведению растёт: статья в Википедии читается в {g['wiki_ratio']:.1f} раза чаще, чем месяц назад".replace(".", ","))
            interest_bonus += 8
        if g.get("trends_ratio") and g["trends_ratio"] >= 1.5:
            why.append(f"поисковый интерес в России вырос в {g['trends_ratio']:.1f} раза за неделю (Google Trends)".replace(".", ","))
            interest_bonus += 6
        # доказательность: один чужой ролик — слабое свидетельство, несколько роликов у разных каналов — сильное
        if g["n_shorts"] == 1:
            evidence = -15 if (g["views_max"] or 0) < 1_000_000 else -5
            why.append("пока один ролик по теме: доказательство слабое" if (g["views_max"] or 0) < 1_000_000 else "один ролик, но уже за миллион")
        elif g["n_shorts"] >= 3 and g["n_channels"] >= 2:
            evidence = 8
        else:
            evidence = 0
        opportunity = round(g["score"] * 0.7 + supply + momentum + evidence + interest_bonus + (5 if g["views_median"] >= med_p75 else 0))
        items.append({
            "title_id": g["title_id"], "label": g["label"], "year": g["year"], "kind_ru": g["kind_ru"], "genres": g["genres"][:3],
            "score": g["score"], "opportunity": opportunity, "n_shorts": g["n_shorts"], "n_channels": g["n_channels"], "n_fresh7": g["n_fresh7"],
            "views_sum": g["views_sum"], "views_median": g["views_median"], "vpd_sum": g["vpd_sum"], "growth7": g["growth7"],
            "why": why, "plan": plan, "outlier_share": g.get("outlier_share"), "outlier_max": g.get("outlier_max"), "small_channel_median": g.get("small_channel_median"), "wiki_7d": g.get("wiki_7d"), "wiki_ratio": g.get("wiki_ratio"), "trends_7d": g.get("trends_7d"), "trends_ratio": g.get("trends_ratio"),
            "scenes": [{"name": s["name"], "views": s["views"], "n": s["n"]} for s in (ins or {}).get("scenes", [])[:3]] if ins else [],
            "hooks": (ins or {}).get("hooks", [])[:3] if ins else [],
        })
    items.sort(key=lambda x: (-x["opportunity"], -x["vpd_sum"]))
    top = items[:limit]
    lv = rights.levels(conn, [it["title_id"] for it in top])
    for it in top:
        it["rights"] = lv[it["title_id"]]
    return {"items": top, "global_time": gtime, "period": f.period, "rights_policy": get_setting(conn, "rights_policy")}


def digest_text(rec: dict, base_url: str = "") -> str:
    items = rec["items"][:8]
    lines = [f"🎬 <b>Что резать сегодня</b> (ролики за {rec['period']} дн.)"]
    for i, it in enumerate(items, 1):
        head = f"{i}. <b>{html.escape(it['label'])}</b>" + (f" ({it['year']})" if it["year"] else "") + f" · {it['kind_ru']} · балл {it['opportunity']}"
        rl = (it.get("rights") or {}).get("level")
        if rl == "caution":
            head += " · ⚠ права: с оговорками"
        elif rl == "block":
            head += " · ✕ права: не проходит"
        lines.append(head)
        lines.append("   " + "; ".join(html.escape(w) for w in it["why"][:3]))
        p = it["plan"]
        bits = []
        if p.get("duration"):
            bits.append(f"длительность {p['duration']}")
        if p.get("hook"):
            bits.append(f"хук: {p['hook']}")
        bits.append(f"время: {p['best_time']}")
        if p.get("hashtags"):
            bits.append(" ".join("#" + t for t in p["hashtags"][:4]))
        lines.append("   План: " + html.escape("; ".join(bits)))
        if it.get("scenes"):
            lines.append("   Сцены: " + html.escape("; ".join(s["name"] for s in it["scenes"][:2])))
    if base_url:
        lines.append(f'\n<a href="{html.escape(base_url)}#/cut">Подробности в консоли</a>')
    text = "\n".join(lines)
    return text[:3900]


def alerts(conn: sqlite3.Connection, limit: int = 15) -> str | None:
    """Новые произведения в топе по скорости за 7 дней по сравнению с прошлым прогоном."""
    f = Filters("7")
    top = films(conn, f, limit=limit, sort="vpd")["items"]
    ids = [g["title_id"] for g in top]
    prev = set(loads(get_meta(conn, "alert_top_ids", "[]"), []))
    set_meta(conn, "alert_top_ids", dumps(ids))
    conn.commit()
    if not prev:
        return None
    new = [g for g in top if g["title_id"] not in prev and g["n_shorts"] >= 2]
    if not new:
        return None
    lines = ["🚀 <b>Взлетает на Shorts</b>"]
    for g in new[:6]:
        lines.append(f"• <b>{html.escape(g['label'])}</b>" + (f" ({g['year']})" if g["year"] else "") +
                     f": {_fmt(g['vpd_sum'])} просм./день, {g['n_shorts']} роликов за 7 дн., {g['n_channels']} кан.")
    return "\n".join(lines)


def _interest_block(conn: sqlite3.Connection, title_id: str) -> dict:
    from .interest import describe, series, summary
    sm = summary(conn, [title_id]).get(title_id, {})
    return {"summary": sm, "lines": describe(sm), "series": series(conn, title_id)}


def brief(conn: sqlite3.Connection, f: Filters, title_id: str) -> dict | None:
    """Бриф для конвейера нарезки: что это, почему, какие сцены, какой хук, длительность, время, хэштеги, референсы."""
    from .neuro import get_explain, get_insights
    from .sources import get_sources
    from .stats import film_detail

    d = film_detail(conn, f, title_id)
    if d is None:
        return None
    srcs = get_sources(conn, title_id)
    expl = get_explain(conn, title_id)
    yt_src = next((x for x in srcs if x["verified"] and "youtube.com" in x["url"]), None)
    any_src = next((x for x in srcs if x["verified"]), None)  # непроверенный исходник в работу не идёт
    main_src = yt_src or any_src
    alt_src = next((x for x in srcs if x["verified"] and x is not main_src), None)
    from .files import status as dl_status
    dl = dl_status(conn, main_src["video_id"]) if main_src else None
    bucket_bounds = {"до 15 с": (8, 15), "16–30 с": (16, 30), "31–45 с": (31, 45), "46–60 с": (46, 60), "61–90 с": (61, 90), "91–180 с": (91, 180)}
    rows = _base_rows(conn, f, extra="AND v.title_id = ?", extra_params=[title_id])
    plan = _plan(rows, _global_time(_base_rows(conn, f)))
    rec = next((it for it in today(conn, f, limit=400)["items"] if it["title_id"] == title_id), None)
    ins = get_insights(conn, title_id)
    sched = [dict(r) for r in conn.execute("SELECT id, day, slot, status FROM schedule WHERE title_id = ? ORDER BY day DESC LIMIT 5", (title_id,))]
    rt = rights.evaluate(conn, title_id) or rights.levels(conn, [title_id])[title_id]
    rights_ok = rights.allowed(rt["level"], get_setting(conn, "rights_policy"))
    clip_min, clip_max = bucket_bounds.get(plan.get("duration") or "", (30, 60))
    if rt.get("clip_max_s"):
        clip_max = min(clip_max, rt["clip_max_s"])  # с заявкой Content ID шортс дольше 60 с блокируется
        clip_min = min(clip_min, clip_max)
    return {
        "rights": {"level": rt["level"], "level_ru": rt.get("level_ru"), "risk": rt.get("risk"), "reasons": rt.get("reasons", []), "rule": rt.get("rule"),
                   "clip_max_s": rt.get("clip_max_s"), "holder": rt.get("holder"), "holder_policy": rt.get("holder_policy"), "passes": rights_ok},
        "title": d["title"], "stats": d["stats"], "why": rec["why"] if rec else [], "opportunity": rec["opportunity"] if rec else None,
        "plan": plan, "scenes": (ins or {}).get("scenes", []), "hooks": (ins or {}).get("hooks", []), "advice": (ins or {}).get("advice", []),
        "insights_at": (ins or {}).get("generated_at"), "top_channels": d["channels"][:5], "hashtags_all": d["hashtags"][:10],
        "seasons": d.get("seasons", []), "schedule": sched,
        "interest": _interest_block(conn, title_id),
        "explain": expl["text"] if expl else None, "explain_at": expl["generated_at"] if expl else None,
        "kinopoisk_url": f"https://www.kinopoisk.ru/{'series' if d['title']['kind'] in ('series', 'animated_series', 'anime', 'tv_show') else 'film'}/{d['title']['kp_id']}/" if d["title"].get("kp_id") else None,
        "sources": srcs,
        "source": main_src, "source_alt": alt_src,
        "references": [{"url": v["url"], "title": v["title"], "views": v["views"], "duration_s": v["duration_s"], "channel": v["channel_name"]} for v in d["videos"][:8]],
        "deliverable": {
            "title": d["title"]["label"], "year": d["title"]["year"], "kind": d["title"]["kind"],
            "source_url": next((x["url"] for x in srcs if x["verified"]), None),
            "source_label": next((x["label"] for x in srcs if x["verified"]), None),
            "scenes": [{"name": sc["name"], "what_to_cut": sc["what_to_cut"], "quotes": sc.get("quotes", []), "where": sc.get("where", ""),
                        "reference_urls": [t["url"] for t in sc.get("top", [])]} for sc in (ins or {}).get("scenes", [])],
            "hooks": (ins or {}).get("hooks", []), "duration": plan.get("duration"), "hashtags": plan.get("hashtags"), "best_time": plan.get("best_time"),
            "rights_level": rt["level"], "rights_ok": rights_ok, "clip_max_s": clip_max,
        },
        "cutter": {
            "source_url": main_src["url"] if main_src else None,
            "source_kind": ("youtube" if main_src and "youtube.com" in main_src["url"] else "rutube" if main_src and "rutube.ru" in main_src["url"] else None),
            "direct_file_url": dl["file_url"] if dl and dl.get("file_url") else None,
            "alt_url": alt_src["url"] if alt_src else None,
            "hint": cutter_hint(ins, expl),
            "clip_min_s": clip_min,
            "clip_max_s": clip_max,
            "rights_ok": rights_ok,
            "rights_level": rt["level"],
            "rights_note": "; ".join(rt.get("reasons", [])[:2]),
        },
    }


def cutter_hint(ins: dict | None, expl: dict | None) -> str:
    """Текст в поле «Что искать» нарезчика: сцены, реплики, чего избегать."""
    parts = []
    for sc in (ins or {}).get("scenes", [])[:5]:
        line = sc["name"]
        if sc.get("what_to_cut"):
            line += f": {sc['what_to_cut']}"
        if sc.get("quotes"):
            line += " Реплики: " + "; ".join(f"«{q}»" for q in sc["quotes"][:2])
        parts.append(line)
    if (ins or {}).get("advice"):
        parts.append("Подача: " + " ".join((ins or {}).get("advice", [])[:2]))
    if not parts and expl and expl.get("text"):
        parts.append(expl["text"].split("\n")[0])
    return "\n".join(parts)[:1500]
