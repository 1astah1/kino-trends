"""Интерес аудитории к произведению независимо от наших шортсов.

Два источника без ключей:
  wiki    — ежедневные просмотры статьи русской Википедии (Wikimedia Pageviews API), статья берётся из Wikidata (sitelink ruwiki);
  trends  — Google Trends по России (pytrends, лимиты жёсткие: батчи по 5 запросов, паузы, при 429 останавливаемся).
Хранится в title_interest(title_id, source, day, value). Сводка: среднее за 7 дней против предыдущих 30 и отношение.
"""
from __future__ import annotations

import sqlite3
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import httpx

from .config import WIKIDATA_UA
from .db import get_meta, now_iso, set_meta
from .stats import KIND_RU

WIKI_HEADERS = {"User-Agent": "kino-trends/0.1 (research tool; contact: owner)"}


def resolve_ruwiki(conn: sqlite3.Connection, title_ids: list[str]) -> dict[str, str | None]:
    """Название статьи ruwiki по Wikidata, с кэшем в titles.ruwiki (пустая строка = статьи нет)."""
    out: dict[str, str | None] = {}
    todo = []
    for tid in title_ids:
        r = conn.execute("SELECT ruwiki FROM titles WHERE id = ?", (tid,)).fetchone()
        if r is None:
            out[tid] = None
        elif r["ruwiki"] is not None:
            out[tid] = r["ruwiki"] or None
        elif tid.startswith("Q"):
            todo.append(tid)
        else:
            out[tid] = None
    with httpx.Client(timeout=30, headers={"User-Agent": WIKIDATA_UA}) as c:
        for i in range(0, len(todo), 50):
            batch = todo[i:i + 50]
            try:
                r = c.get("https://www.wikidata.org/w/api.php", params={"action": "wbgetentities", "ids": "|".join(batch), "props": "sitelinks",
                                                                        "sitefilter": "ruwiki", "format": "json"})
                ents = r.json().get("entities", {}) if r.status_code == 200 else {}
            except Exception:  # noqa: BLE001
                ents = {}
            for tid in batch:
                title = ((ents.get(tid) or {}).get("sitelinks") or {}).get("ruwiki", {}).get("title")
                conn.execute("UPDATE titles SET ruwiki = ? WHERE id = ?", (title or "", tid))
                out[tid] = title
            conn.commit()
    return out


def fetch_wiki_views(article: str, days: int = 60) -> list[tuple[str, int]]:
    end = datetime.now(timezone.utc) - timedelta(days=1)
    start = end - timedelta(days=days)
    url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/ru.wikipedia/all-access/user/"
           + urllib.parse.quote(article.replace(" ", "_"), safe="") + f"/daily/{start:%Y%m%d}/{end:%Y%m%d}")
    with httpx.Client(timeout=30, headers=WIKI_HEADERS) as c:
        for attempt in range(4):
            r = c.get(url)
            if r.status_code == 200:
                return [(it["timestamp"][:8], int(it["views"])) for it in r.json().get("items", [])]
            if r.status_code == 404:
                return []
            time.sleep(8 * (attempt + 1))
    raise RuntimeError(f"pageviews HTTP {r.status_code}")


def update_wiki(conn: sqlite3.Connection, title_ids: list[str], max_titles: int = 200, max_age_hours: int = 20, log=print) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pending = [t for t in title_ids if get_meta(conn, f"interest_wiki:{t}", "") < cutoff][:max_titles]
    if not pending:
        return 0
    articles = resolve_ruwiki(conn, pending)
    n = 0
    for tid in pending:
        art = articles.get(tid)
        set_meta(conn, f"interest_wiki:{tid}", now_iso())
        conn.commit()
        if not art:
            continue
        try:
            rows = fetch_wiki_views(art)
        except Exception as e:  # noqa: BLE001
            log(f"Википедия «{art}»: {e}", "warn")
            continue
        conn.executemany("INSERT OR REPLACE INTO title_interest(title_id, source, day, value) VALUES (?, 'wiki', ?, ?)",
                         [(tid, f"{d[:4]}-{d[4:6]}-{d[6:8]}", v) for d, v in rows])
        conn.commit()
        n += 1
        time.sleep(1.0)
    conn.commit()
    return n


def update_trends(conn: sqlite3.Connection, titles: list[dict], max_titles: int = 40, max_age_hours: int = 20, log=print) -> int:
    """titles: [{title_id, label, kind}]. Запрос «название фильм|сериал», Россия, 90 дней."""
    try:
        from pytrends.request import TrendReq
    except ImportError:
        log("pytrends не установлен, Google Trends пропущен", "warn")
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pending = [t for t in titles if get_meta(conn, f"interest_trends:{t['title_id']}", "") < cutoff][:max_titles]
    if not pending:
        return 0
    n = 0
    try:
        tr = TrendReq(hl="ru-RU", tz=180, timeout=(10, 25))
    except Exception as e:  # noqa: BLE001
        log(f"Google Trends недоступен: {e}", "warn")
        return 0
    for i in range(0, len(pending), 5):
        batch = pending[i:i + 5]
        kws = []
        for t in batch:
            suffix = "сериал" if t.get("kind") in ("series", "animated_series", "anime", "tv_show") else "фильм"
            kws.append(f"{t['label']} {suffix}"[:100])
        try:
            tr.build_payload(kws, timeframe="today 3-m", geo="RU")
            df = tr.interest_over_time()
        except Exception as e:  # noqa: BLE001
            log(f"Google Trends: {str(e)[:120]}, останавливаюсь на этом прогоне", "warn")
            break
        for t, kw in zip(batch, kws):
            set_meta(conn, f"interest_trends:{t['title_id']}", now_iso())
            conn.commit()
            if df is None or df.empty or kw not in df.columns:
                continue
            rows = [(t["title_id"], idx.strftime("%Y-%m-%d"), float(val)) for idx, val in df[kw].items()]
            conn.executemany("INSERT OR REPLACE INTO title_interest(title_id, source, day, value) VALUES (?, 'trends', ?, ?)", rows)
            n += 1
        conn.commit()
        time.sleep(4)
    return n


def summary(conn: sqlite3.Connection, title_ids: list[str]) -> dict[str, dict]:
    """Для каждого произведения: среднее за последние 7 дней, за предыдущие 30, отношение; ряды для графиков."""
    if not title_ids:
        return {}
    out: dict[str, dict] = {}
    today = datetime.now(timezone.utc).date()
    d7 = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    d37 = (today - timedelta(days=37)).strftime("%Y-%m-%d")
    chunk = 500
    for i in range(0, len(title_ids), chunk):
        ids = title_ids[i:i + chunk]
        q = ",".join("?" * len(ids))
        for r in conn.execute(f"""SELECT title_id, source,
                                         AVG(CASE WHEN day >= ? THEN value END) AS last7,
                                         AVG(CASE WHEN day >= ? AND day < ? THEN value END) AS prev30,
                                         COUNT(*) AS n
                                  FROM title_interest WHERE title_id IN ({q}) GROUP BY title_id, source""", [d7, d37, d7, *ids]):
            d = out.setdefault(r["title_id"], {})
            last7, prev30 = r["last7"], r["prev30"]
            ratio = round(last7 / prev30, 2) if last7 is not None and prev30 and prev30 > 0 else None
            d[r["source"]] = {"last7": round(last7, 1) if last7 is not None else None, "prev30": round(prev30, 1) if prev30 is not None else None,
                              "ratio": ratio, "n": r["n"]}
    return out


def series(conn: sqlite3.Connection, title_id: str, days: int = 90) -> dict[str, list[dict]]:
    since = (datetime.now(timezone.utc).date() - timedelta(days=days)).strftime("%Y-%m-%d")
    out: dict[str, list[dict]] = {"wiki": [], "trends": []}
    for r in conn.execute("SELECT source, day, value FROM title_interest WHERE title_id = ? AND day >= ? ORDER BY day", (title_id, since)):
        out.setdefault(r["source"], []).append({"day": r["day"], "value": r["value"]})
    return out


def describe(sm: dict | None) -> list[str]:
    """Человеческие строки для причин и карточек."""
    if not sm:
        return []
    lines = []
    w = sm.get("wiki")
    if w and w.get("last7") is not None:
        s = f"Википедия: {round(w['last7']):,} просмотров статьи в день".replace(",", " ")
        if w.get("ratio"):
            s += f", ×{w['ratio']:.1f} к прошлому месяцу".replace(".", ",")
        lines.append(s)
    t = sm.get("trends")
    if t and t.get("last7") is not None:
        s = f"Google Trends (Россия): {round(t['last7'])} из 100"
        if t.get("ratio"):
            s += f", ×{t['ratio']:.1f} за неделю".replace(".", ",")
        lines.append(s)
    return lines
