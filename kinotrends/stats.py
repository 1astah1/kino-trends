"""Статистика для интерфейса: фильмы, интересы, каналы, ролики, обзор.

Фильтры одни на всё: period (7|30|90|all), min_conf, min_views, lang. Период считается по дате публикации,
а если она ещё не получена, по дате первого появления в выдаче. Прирост просмотров считается только
по точным замерам (страница ролика, RSS, Data API): цифры со страниц хэштегов и каналов округлены.
"""
from __future__ import annotations

import csv
import io
import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from .db import loads
from .text import duration_bucket

PERIODS = {"7": 7, "30": 30, "90": 90, "all": None}
KIND_RU = {"film": "фильм", "series": "сериал", "animated": "мультфильм", "animated_series": "мультсериал",
           "anime": "аниме", "tv_show": "телешоу", "franchise": "франшиза"}
FEATURE_RU = {
    "question": "вопрос в заголовке", "emoji": "эмодзи в заголовке", "names_title": "название фильма в тексте",
    "hidden_title": "«название в комментариях»", "caps": "КАПС", "pipe": "разделитель |", "exclaim": "восклицание",
    "has_shorts_tag": "#shorts",
}
EXACT_SRC = ("page", "api", "rss")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Filters:
    def __init__(self, period: str = "30", min_conf: float = 0.6, min_views: int = 0, lang: str = "all") -> None:
        self.period = period if period in PERIODS else "30"
        self.days = PERIODS[self.period]
        self.min_conf = min_conf
        self.min_views = min_views
        self.lang = lang if lang in ("all", "ru", "en", "uk", "kk", "other") else "all"

    @property
    def since(self) -> str:
        return _iso(_now() - timedelta(days=self.days)) if self.days else "0000"

    def where(self, recognized: bool = True) -> tuple[str, list]:
        conds = ["v.status = 'ok'", "COALESCE(v.published_at, v.first_seen_at) >= ?", "COALESCE(v.views, 0) >= ?"]
        params: list = [self.since, self.min_views]
        if recognized:
            conds.append("v.title_id IS NOT NULL AND (v.recog_manual = 1 OR v.recog_conf >= ?)")
            params.append(self.min_conf)
        if self.lang != "all":
            conds.append("v.lang = ?")
            params.append(self.lang)
        return " AND ".join(conds), params


def _median(xs: list) -> float:
    xs = [x for x in xs if x is not None]
    return float(statistics.median(xs)) if xs else 0.0


def _pct(xs: list, p: float) -> float:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return 0.0
    k = (len(xs) - 1) * p
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return float(xs[int(k)])
    return float(xs[f] + (xs[c] - xs[f]) * (k - f))


def _age_days(pub: str | None, first_seen: str) -> float:
    s = pub or first_seen
    try:
        dt = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return 1.0
    return max(1.0, (_now() - dt).total_seconds() / 86400)


def _rank_pct(values: dict, key_fn=lambda v: v) -> dict:
    """Процентильный ранг 0..1 по ключу."""
    items = sorted(values.items(), key=lambda kv: key_fn(kv[1]))
    n = len(items)
    out = {}
    for i, (k, _) in enumerate(items):
        out[k] = (i + 1) / n if n else 0
    return out


def _base_rows(conn: sqlite3.Connection, f: Filters, recognized: bool = True, extra: str = "", extra_params: list | None = None) -> list[sqlite3.Row]:
    where, params = f.where(recognized)
    sql = f"""SELECT v.video_id, v.title, v.channel_id, COALESCE(c.name, v.channel_name) AS channel_name, v.duration_s,
                     v.published_at, v.first_seen_at, v.views, v.likes, v.comments, v.lang, v.hashtags, v.features, v.season, v.episode,
                     v.title_id, v.recog_conf, v.recog_method, v.recog_evidence, v.recog_manual, v.thumb, v.details_at,
                     t.label_ru, t.label_en, t.year, t.kind, t.genres, t.countries, t.franchise_id, t.franchise_label, t.kp_id
              FROM videos v LEFT JOIN titles t ON t.id = v.title_id LEFT JOIN channels c ON c.channel_id = v.channel_id
              WHERE {where} {extra}"""
    return conn.execute(sql, params + (extra_params or [])).fetchall()


def _growth(conn: sqlite3.Connection, video_ids: list[str], days: int) -> dict[str, int]:
    """Прирост просмотров за окно по точным замерам: последний замер минус первый замер внутри окна."""
    if not video_ids:
        return {}
    since = _iso(_now() - timedelta(days=days))
    out: dict[str, int] = {}
    chunk = 800
    for i in range(0, len(video_ids), chunk):
        ids = video_ids[i:i + chunk]
        q = ",".join("?" * len(ids))
        rows = conn.execute(
            f"""SELECT video_id, MIN(ts) AS t0, MAX(ts) AS t1 FROM video_stats
                WHERE ts >= ? AND src IN ('page','api','rss') AND video_id IN ({q}) GROUP BY video_id HAVING COUNT(*) >= 2""",
            [since, *ids]).fetchall()
        for r in rows:
            v0 = conn.execute("SELECT views FROM video_stats WHERE video_id = ? AND ts = ?", (r["video_id"], r["t0"])).fetchone()
            v1 = conn.execute("SELECT views FROM video_stats WHERE video_id = ? AND ts = ?", (r["video_id"], r["t1"])).fetchone()
            if v0 and v1 and v0[0] is not None and v1[0] is not None:
                out[r["video_id"]] = max(0, v1[0] - v0[0])
    return out


def channel_baselines(conn: sqlite3.Connection, f: Filters) -> tuple[dict[str, float], dict[str, int | None]]:
    """Медиана просмотров по каждому каналу за период (все ролики канала с деталями) и подписчики."""
    where, params = f.where(recognized=False)
    by_ch: dict[str, list[int]] = defaultdict(list)
    for r in conn.execute(f"SELECT v.channel_id, v.views FROM videos v WHERE {where} AND v.channel_id IS NOT NULL AND v.views IS NOT NULL", params):
        by_ch[r["channel_id"]].append(r["views"])
    medians = {cid: _median(vs) for cid, vs in by_ch.items() if len(vs) >= 3}
    followers = {r["channel_id"]: r["followers"] for r in conn.execute("SELECT channel_id, followers FROM channels")}
    return medians, followers


def outlier_x(views: int | None, channel_id: str | None, medians: dict[str, float]) -> float | None:
    """Во сколько раз ролик обошёл норму своего канала (медиану его роликов за период)."""
    if not views or not channel_id or channel_id not in medians or medians[channel_id] <= 0:
        return None
    return round(views / medians[channel_id], 1)


def _video_dict(r: sqlite3.Row) -> dict:
    age = _age_days(r["published_at"], r["first_seen_at"])
    return {
        "video_id": r["video_id"], "title": r["title"], "channel_id": r["channel_id"], "channel_name": r["channel_name"],
        "duration_s": r["duration_s"], "published_at": r["published_at"], "first_seen_at": r["first_seen_at"],
        "views": r["views"], "likes": r["likes"], "comments": r["comments"], "lang": r["lang"], "thumb": r["thumb"],
        "age_days": round(age, 1), "vpd": round((r["views"] or 0) / age), "has_details": bool(r["details_at"]),
        "title_id": r["title_id"], "title_label": r["label_ru"] or r["label_en"], "title_year": r["year"], "kind": r["kind"],
        "recog_conf": r["recog_conf"], "recog_method": r["recog_method"], "recog_evidence": r["recog_evidence"],
        "recog_manual": bool(r["recog_manual"]), "hashtags": loads(r["hashtags"], []),
        "season": r["season"] if "season" in r.keys() else None, "episode": r["episode"] if "episode" in r.keys() else None,
        "url": f"https://www.youtube.com/shorts/{r['video_id']}",
    }


# ---------- фильмы ----------
# группы типов для фильтра: сериалы (включая мультсериалы, аниме и шоу) и анимация
KIND_GROUPS = {"series": {"series", "animated_series", "anime", "tv_show"}, "animated": {"animated", "animated_series", "anime"}, "film": {"film"}}


def films(conn: sqlite3.Connection, f: Filters, limit: int = 100, sort: str = "score", kind: str = "all",
          genre: str | None = None, q: str | None = None, offset: int = 0) -> dict:
    rows = _base_rows(conn, f)
    fresh7 = _iso(_now() - timedelta(days=7))
    medians, followers = channel_baselines(conn, f)
    groups: dict[str, dict] = {}
    for r in rows:
        tid = r["title_id"]
        if kind != "all" and r["kind"] not in KIND_GROUPS.get(kind, {kind}):
            continue
        genres = loads(r["genres"], [])
        if genre and genre not in genres:
            continue
        label = r["label_ru"] or r["label_en"] or tid
        if q and q.lower() not in (label or "").lower() and q.lower() not in (r["label_en"] or "").lower():
            continue
        g = groups.setdefault(tid, {
            "title_id": tid, "label": label, "label_en": r["label_en"], "year": r["year"], "kind": r["kind"],
            "kind_ru": KIND_RU.get(r["kind"], r["kind"]), "genres": genres, "countries": loads(r["countries"], []),
            "franchise": r["franchise_label"], "kp_id": r["kp_id"],
            "n_shorts": 0, "n_fresh7": 0, "channels": set(), "views": [], "durations": [], "likes": 0, "views_with_likes": 0,
            "vpd_sum": 0.0, "video_ids": [], "manual": 0, "views_max": 0, "top_video": None, "last_pub": None,
            "outliers": 0, "outlier_rated": 0, "outlier_max": 0.0, "small_views": [],
        })
        views = r["views"] or 0
        age = _age_days(r["published_at"], r["first_seen_at"])
        g["n_shorts"] += 1
        if (r["published_at"] or r["first_seen_at"]) >= fresh7:
            g["n_fresh7"] += 1
        if r["channel_id"]:
            g["channels"].add(r["channel_id"])
        g["views"].append(views)
        g["vpd_sum"] += views / age
        if r["duration_s"]:
            g["durations"].append(r["duration_s"])
        if r["likes"] is not None:
            g["likes"] += r["likes"]
            g["views_with_likes"] += views
        g["video_ids"].append(r["video_id"])
        g["manual"] += int(bool(r["recog_manual"]))
        ox = outlier_x(views, r["channel_id"], medians)
        if ox is not None:
            g["outlier_rated"] += 1
            g["outlier_max"] = max(g["outlier_max"], ox)
            if ox >= 3:
                g["outliers"] += 1
        fol = followers.get(r["channel_id"])
        if fol is not None and fol < 100_000:
            g["small_views"].append(views)
        if views >= g["views_max"]:
            g["views_max"] = views
            g["top_video"] = {"video_id": r["video_id"], "title": r["title"], "views": views, "channel_name": r["channel_name"], "thumb": r["thumb"]}
        pub = r["published_at"] or r["first_seen_at"]
        if not g["last_pub"] or pub > g["last_pub"]:
            g["last_pub"] = pub
    all_ids = [vid for g in groups.values() for vid in g["video_ids"]]
    growth7 = _growth(conn, all_ids, 7)
    for g in groups.values():
        vs = g["views"]
        g["views_sum"] = sum(vs)
        g["views_median"] = round(_median(vs))
        g["views_p90"] = round(_pct(vs, 0.9))
        g["n_channels"] = len(g["channels"])
        g["dur_avg"] = round(statistics.mean(g["durations"])) if g["durations"] else None
        g["like_rate"] = round(g["likes"] / g["views_with_likes"] * 100, 2) if g["views_with_likes"] else None
        g["vpd_sum"] = round(g["vpd_sum"])
        g["growth7"] = sum(growth7.get(v, 0) for v in g["video_ids"])
        g["growth7_measured"] = sum(1 for v in g["video_ids"] if v in growth7)
        g["outlier_share"] = round(g["outliers"] / g["outlier_rated"], 2) if g["outlier_rated"] else None
        g["small_channel_median"] = round(_median(g["small_views"])) if len(g["small_views"]) >= 2 else None
        g["small_channel_n"] = len(g["small_views"])
        del g["channels"], g["views"], g["durations"], g["likes"], g["views_with_likes"], g["video_ids"], g["small_views"]
    if groups:
        r_vpd = _rank_pct({k: g["vpd_sum"] for k, g in groups.items()})
        r_fresh = _rank_pct({k: g["n_fresh7"] for k, g in groups.items()})
        r_med = _rank_pct({k: g["views_median"] for k, g in groups.items()})
        r_ch = _rank_pct({k: g["n_channels"] for k, g in groups.items()})
        med_p75 = _pct([g["views_median"] for g in groups.values()], 0.75)
        for k, g in groups.items():
            g["score"] = round(100 * (0.45 * r_vpd[k] + 0.25 * r_fresh[k] + 0.2 * r_med[k] + 0.1 * r_ch[k]))
            g["opportunity"] = g["views_median"] >= med_p75 and g["n_channels"] <= 3 and g["n_shorts"] >= 2
    try:
        from .interest import summary as _interest_summary
        isum = _interest_summary(conn, list(groups.keys()))
    except Exception:  # noqa: BLE001
        isum = {}
    for k, g in groups.items():
        sm = isum.get(k, {})
        w, t = sm.get("wiki"), sm.get("trends")
        g["wiki_7d"] = w["last7"] if w else None
        g["wiki_ratio"] = w["ratio"] if w else None
        g["trends_7d"] = t["last7"] if t else None
        g["trends_ratio"] = t["ratio"] if t else None
    keys = {"score": lambda g: (-g["score"], -g["vpd_sum"]), "views": lambda g: -g["views_sum"], "median": lambda g: -g["views_median"],
            "wiki": lambda g: -(g["wiki_7d"] or 0), "interest": lambda g: -(g["wiki_ratio"] or 0),
            "outliers": lambda g: (-(g["outlier_share"] or 0), -g["outlier_max"]), "small": lambda g: -(g["small_channel_median"] or 0),
            "shorts": lambda g: -g["n_shorts"], "vpd": lambda g: -g["vpd_sum"], "growth": lambda g: -g["growth7"],
            "channels": lambda g: -g["n_channels"], "fresh": lambda g: (-g["n_fresh7"], -g["vpd_sum"]), "label": lambda g: (g["label"] or "")}
    ordered = sorted(groups.values(), key=keys.get(sort, keys["score"]))
    return {"total": len(ordered), "items": ordered[offset:offset + limit]}


def film_detail(conn: sqlite3.Connection, f: Filters, title_id: str) -> dict | None:
    t = conn.execute("SELECT * FROM titles WHERE id = ?", (title_id,)).fetchone()
    if not t:
        return None
    rows = _base_rows(conn, f, recognized=True, extra="AND v.title_id = ?", extra_params=[title_id])
    medians, followers = channel_baselines(conn, f)
    vids = []
    for r in rows:
        v = _video_dict(r)
        v["outlier_x"] = outlier_x(r["views"], r["channel_id"], medians)
        v["channel_followers"] = followers.get(r["channel_id"])
        vids.append(v)
    growth7 = _growth(conn, [v["video_id"] for v in vids], 7)
    for v in vids:
        v["growth7"] = growth7.get(v["video_id"])
    vids.sort(key=lambda v: -(v["views"] or 0))
    views = [v["views"] or 0 for v in vids]
    channels: dict[str, dict] = {}
    tags: Counter = Counter()
    durs: Counter = Counter()
    daily: dict[str, dict] = defaultdict(lambda: {"n": 0, "views": 0})
    for v in vids:
        if v["channel_id"]:
            c = channels.setdefault(v["channel_id"], {"channel_id": v["channel_id"], "name": v["channel_name"], "n": 0, "views": 0})
            c["n"] += 1
            c["views"] += v["views"] or 0
        for h in v["hashtags"]:
            tags[h] += 1
        durs[duration_bucket(v["duration_s"])] += 1
        day = (v["published_at"] or v["first_seen_at"])[:10]
        daily[day]["n"] += 1
        daily[day]["views"] += v["views"] or 0
    aliases = [r[0] for r in conn.execute("SELECT alias FROM title_aliases WHERE title_id = ? ORDER BY source DESC, lang LIMIT 12", (title_id,))]
    seasons: dict[int, dict] = defaultdict(lambda: {"n": 0, "views": 0})
    for v in vids:
        if v.get("season"):
            seasons[v["season"]]["n"] += 1
            seasons[v["season"]]["views"] += v["views"] or 0
    return {
        "title": {"title_id": t["id"], "label": t["label_ru"] or t["label_en"], "label_en": t["label_en"], "original_title": t["original_title"],
                  "year": t["year"], "kind": t["kind"], "kind_ru": KIND_RU.get(t["kind"], t["kind"]), "genres": loads(t["genres"], []),
                  "countries": loads(t["countries"], []), "kp_id": t["kp_id"], "imdb_id": t["imdb_id"], "franchise": t["franchise_label"],
                  "sitelinks": t["sitelinks"], "is_manual": bool(t["is_manual"]), "aliases": aliases},
        "stats": {"n_shorts": len(vids), "n_channels": len(channels), "views_sum": sum(views), "views_median": round(_median(views)),
                  "views_p90": round(_pct(views, 0.9)), "views_max": max(views) if views else 0,
                  "vpd_sum": round(sum(v["vpd"] for v in vids)), "growth7": sum(growth7.values()),
                  "dur_avg": round(statistics.mean([v["duration_s"] for v in vids if v["duration_s"]])) if any(v["duration_s"] for v in vids) else None},
        "channels": sorted(channels.values(), key=lambda c: -c["views"])[:15],
        "hashtags": [{"tag": k, "n": n} for k, n in tags.most_common(15)],
        "durations": [{"bucket": k, "n": n} for k, n in durs.most_common()],
        "daily": [{"day": d, **x} for d, x in sorted(daily.items())],
        "seasons": [{"season": k, **x} for k, x in sorted(seasons.items())],
        "videos": vids[:200],
    }


# ---------- интересы ----------
def interests(conn: sqlite3.Connection, f: Filters) -> dict:
    rows = _base_rows(conn, f)

    def agg(key_fn):
        buckets: dict[str, dict] = {}
        for r in rows:
            keys = key_fn(r)
            if not keys:
                continue
            for k in keys:
                b = buckets.setdefault(k, {"key": k, "n": 0, "views": [], "titles": set(), "channels": set()})
                b["n"] += 1
                b["views"].append(r["views"] or 0)
                b["titles"].add(r["title_id"])
                if r["channel_id"]:
                    b["channels"].add(r["channel_id"])
        out = []
        for b in buckets.values():
            out.append({"key": b["key"], "n": b["n"], "views_sum": sum(b["views"]), "views_median": round(_median(b["views"])),
                        "views_p75": round(_pct(b["views"], 0.75)), "n_titles": len(b["titles"]), "n_channels": len(b["channels"])})
        return sorted(out, key=lambda x: -x["views_sum"])

    def decade(r):
        y = r["year"]
        return [f"{(y // 10) * 10}-е"] if y else ["год неизвестен"]

    def country(r):
        cs = loads(r["countries"], [])
        return cs[:2] if cs else ["страна неизвестна"]

    def feature_keys(r):
        fe = loads(r["features"], None)
        if not fe:
            return []
        return [FEATURE_RU[k] for k, v in fe.items() if k in FEATURE_RU and v]

    def hashtag_keys(r):
        return ["#" + h for h in loads(r["hashtags"], [])[:8]]

    def weekday(r):
        p = r["published_at"]
        if not p:
            return []
        dt = datetime.strptime(p[:19], "%Y-%m-%dT%H:%M:%S")
        return [["пн", "вт", "ср", "чт", "пт", "сб", "вс"][dt.weekday()]]

    def hour(r):
        p = r["published_at"]
        if not p:
            return []
        h = (int(p[11:13]) + 3) % 24  # московское время
        return [f"{h:02d}:00"]

    all_views = [r["views"] or 0 for r in rows]
    return {
        "total": {"n": len(rows), "views_sum": sum(all_views), "views_median": round(_median(all_views))},
        "genres": agg(lambda r: loads(r["genres"], []) or ["жанр неизвестен"])[:20],
        "kinds": agg(lambda r: [KIND_RU.get(r["kind"], r["kind"])]),
        "countries": agg(country)[:15],
        "decades": sorted(agg(decade), key=lambda x: x["key"]),
        "langs": agg(lambda r: [r["lang"] or "неизвестно"]),
        "durations": sorted(agg(lambda r: [duration_bucket(r["duration_s"])]), key=lambda x: ["до 15 с", "16–30 с", "31–45 с", "46–60 с", "61–90 с", "91–180 с", "более 3 мин", "неизвестно"].index(x["key"])),
        "features": agg(feature_keys),
        "hashtags": agg(hashtag_keys)[:25],
        "weekdays": sorted(agg(weekday), key=lambda x: ["пн", "вт", "ср", "чт", "пт", "сб", "вс"].index(x["key"])),
        "hours": sorted(agg(hour), key=lambda x: x["key"]),
    }


# ---------- каналы ----------
def channels(conn: sqlite3.Connection, f: Filters, limit: int = 100, sort: str = "views", q: str | None = None, offset: int = 0) -> dict:
    rows = _base_rows(conn, f, recognized=False)
    groups: dict[str, dict] = {}
    for r in rows:
        cid = r["channel_id"]
        if not cid:
            continue
        g = groups.setdefault(cid, {"channel_id": cid, "name": r["channel_name"], "n_shorts": 0, "n_recognized": 0, "views": [],
                                    "titles": Counter(), "title_labels": {}, "durations": [], "pubs": [], "likes": 0, "views_with_likes": 0})
        g["n_shorts"] += 1
        g["views"].append(r["views"] or 0)
        rec = r["title_id"] and (r["recog_manual"] or (r["recog_conf"] or 0) >= f.min_conf)
        if rec:
            g["n_recognized"] += 1
            g["titles"][r["title_id"]] += 1
            g["title_labels"][r["title_id"]] = r["label_ru"] or r["label_en"]
        if r["duration_s"]:
            g["durations"].append(r["duration_s"])
        if r["published_at"]:
            g["pubs"].append(r["published_at"])
        if r["likes"] is not None:
            g["likes"] += r["likes"]
            g["views_with_likes"] += r["views"] or 0
    meta = {r["channel_id"]: r for r in conn.execute("SELECT channel_id, followers, handle, default_title_id FROM channels")}
    tracked = {r[0] for r in conn.execute("SELECT value FROM sources WHERE kind = 'channel' AND enabled = 1")}
    out = []
    for cid, g in groups.items():
        if q and q.lower() not in (g["name"] or "").lower():
            continue
        m = meta.get(cid)
        pubs = sorted(g["pubs"])
        cadence = None
        if len(pubs) >= 3:
            d0 = datetime.strptime(pubs[0][:19], "%Y-%m-%dT%H:%M:%S")
            d1 = datetime.strptime(pubs[-1][:19], "%Y-%m-%dT%H:%M:%S")
            span = max(1.0, (d1 - d0).total_seconds() / 86400)
            cadence = round(len(pubs) / span * 7, 1)
        top = g["titles"].most_common(3)
        out.append({
            "channel_id": cid, "name": g["name"], "handle": m["handle"] if m else None, "followers": m["followers"] if m else None,
            "tracked": cid in tracked, "default_title_id": m["default_title_id"] if m else None,
            "n_shorts": g["n_shorts"], "n_recognized": g["n_recognized"], "views_sum": sum(g["views"]), "views_median": round(_median(g["views"])),
            "views_max": max(g["views"]) if g["views"] else 0, "dur_avg": round(statistics.mean(g["durations"])) if g["durations"] else None,
            "per_week": cadence, "n_titles": len(g["titles"]),
            "top_titles": [{"title_id": t, "label": g["title_labels"][t], "n": n} for t, n in top],
            "like_rate": round(g["likes"] / g["views_with_likes"] * 100, 2) if g["views_with_likes"] else None,
            "url": f"https://www.youtube.com/channel/{cid}/shorts",
        })
    keys = {"views": lambda c: -c["views_sum"], "median": lambda c: -c["views_median"], "shorts": lambda c: -c["n_shorts"],
            "recognized": lambda c: -c["n_recognized"], "followers": lambda c: -(c["followers"] or 0), "name": lambda c: (c["name"] or "")}
    out.sort(key=keys.get(sort, keys["views"]))
    return {"total": len(out), "items": out[offset:offset + limit]}


def channel_detail(conn: sqlite3.Connection, f: Filters, channel_id: str) -> dict | None:
    ch = conn.execute("SELECT * FROM channels WHERE channel_id = ?", (channel_id,)).fetchone()
    rows = _base_rows(conn, f, recognized=False, extra="AND v.channel_id = ?", extra_params=[channel_id])
    if not ch and not rows:
        return None
    vids = [_video_dict(r) for r in rows]
    vids.sort(key=lambda v: -(v["views"] or 0))
    titles: Counter = Counter()
    labels = {}
    for v in vids:
        if v["title_id"] and (v["recog_manual"] or (v["recog_conf"] or 0) >= f.min_conf):
            titles[v["title_id"]] += 1
            labels[v["title_id"]] = v["title_label"]
    views = [v["views"] or 0 for v in vids]
    tracked = conn.execute("SELECT id, enabled FROM sources WHERE kind = 'channel' AND value = ?", (channel_id,)).fetchone()
    return {
        "channel": {"channel_id": channel_id, "name": ch["name"] if ch else (vids[0]["channel_name"] if vids else None),
                    "handle": ch["handle"] if ch else None, "followers": ch["followers"] if ch else None,
                    "default_title_id": ch["default_title_id"] if ch else None, "note": ch["note"] if ch else None,
                    "tracked": bool(tracked and tracked["enabled"]), "source_id": tracked["id"] if tracked else None,
                    "url": f"https://www.youtube.com/channel/{channel_id}/shorts"},
        "stats": {"n_shorts": len(vids), "n_recognized": sum(titles.values()), "views_sum": sum(views), "views_median": round(_median(views)),
                  "views_max": max(views) if views else 0},
        "titles": [{"title_id": t, "label": labels[t], "n": n} for t, n in titles.most_common(20)],
        "videos": vids[:200],
    }


# ---------- ролики ----------
def shorts(conn: sqlite3.Connection, f: Filters, state: str = "all", q: str | None = None, title_id: str | None = None,
           channel_id: str | None = None, sort: str = "views", limit: int = 50, offset: int = 0) -> dict:
    where, params = f.where(recognized=False)
    conds = [where]
    if state == "recognized":
        conds.append("v.title_id IS NOT NULL AND (v.recog_manual = 1 OR v.recog_conf >= ?)")
        params.append(f.min_conf)
    elif state == "unrecognized":
        conds.append("v.title_id IS NULL AND v.details_at IS NOT NULL")
    elif state == "lowconf":
        conds.append("v.title_id IS NOT NULL AND v.recog_manual = 0 AND v.recog_conf < ?")
        params.append(f.min_conf)
    elif state == "nodetails":
        conds.append("v.details_at IS NULL")
    if q:
        conds.append("(v.title LIKE ? OR v.description LIKE ? OR v.channel_name LIKE ?)")
        like = f"%{q}%"
        params += [like, like, like]
    if title_id:
        conds.append("v.title_id = ?")
        params.append(title_id)
    if channel_id:
        conds.append("v.channel_id = ?")
        params.append(channel_id)
    order = {"views": "COALESCE(v.views,0) DESC", "date": "COALESCE(v.published_at, v.first_seen_at) DESC", "conf": "v.recog_conf DESC",
             "vpd": "COALESCE(v.views,0) / MAX(1.0, julianday('now') - julianday(COALESCE(v.published_at, v.first_seen_at))) DESC",
             "seen": "v.first_seen_at DESC"}.get(sort, "COALESCE(v.views,0) DESC")
    w = " AND ".join(conds)
    total = conn.execute(f"SELECT COUNT(*) FROM videos v WHERE {w}", params).fetchone()[0]
    rows = conn.execute(
        f"""SELECT v.video_id, v.title, v.channel_id, COALESCE(c.name, v.channel_name) AS channel_name, v.duration_s, v.published_at,
                   v.first_seen_at, v.views, v.likes, v.comments, v.lang, v.hashtags, v.title_id, v.recog_conf, v.recog_method,
                   v.recog_evidence, v.recog_manual, v.thumb, v.details_at, v.recog_candidates, v.description,
                   t.label_ru, t.label_en, t.year, t.kind
            FROM videos v LEFT JOIN titles t ON t.id = v.title_id LEFT JOIN channels c ON c.channel_id = v.channel_id
            WHERE {w} ORDER BY {order} LIMIT ? OFFSET ?""", params + [limit, offset]).fetchall()
    medians, followers = channel_baselines(conn, f)
    items = []
    for r in rows:
        d = _video_dict(r)
        d["candidates"] = loads(r["recog_candidates"], {}).get("candidates", [])
        d["description"] = (r["description"] or "")[:300]
        d["outlier_x"] = outlier_x(r["views"], r["channel_id"], medians)
        d["channel_followers"] = followers.get(r["channel_id"])
        items.append(d)
    return {"total": total, "items": items}


# ---------- обзор ----------
def overview(conn: sqlite3.Connection, f: Filters) -> dict:
    c = conn.execute
    where, params = f.where(recognized=False)
    totals = {
        "videos": c("SELECT COUNT(*) FROM videos").fetchone()[0],
        "videos_period": c(f"SELECT COUNT(*) FROM videos v WHERE {where}", params).fetchone()[0],
        "with_details": c("SELECT COUNT(*) FROM videos WHERE details_at IS NOT NULL").fetchone()[0],
        "no_details": c("SELECT COUNT(*) FROM videos WHERE details_at IS NULL AND status = 'ok'").fetchone()[0],
        "recognized": c("SELECT COUNT(*) FROM videos WHERE title_id IS NOT NULL AND (recog_manual = 1 OR recog_conf >= ?) AND status = 'ok'", (f.min_conf,)).fetchone()[0],
        "lowconf": c("SELECT COUNT(*) FROM videos WHERE title_id IS NOT NULL AND recog_manual = 0 AND recog_conf < ? AND status = 'ok'", (f.min_conf,)).fetchone()[0],
        "unrecognized": c("SELECT COUNT(*) FROM videos WHERE title_id IS NULL AND details_at IS NOT NULL AND status = 'ok'").fetchone()[0],
        "removed": c("SELECT COUNT(*) FROM videos WHERE status = 'removed'").fetchone()[0],
        "titles_seen": c("SELECT COUNT(DISTINCT title_id) FROM videos WHERE title_id IS NOT NULL AND (recog_manual = 1 OR recog_conf >= ?)", (f.min_conf,)).fetchone()[0],
        "titles_dict": c("SELECT COUNT(*) FROM titles").fetchone()[0],
        "aliases_dict": c("SELECT COUNT(*) FROM title_aliases").fetchone()[0],
        "channels": c("SELECT COUNT(*) FROM channels").fetchone()[0],
        "channels_tracked": c("SELECT COUNT(*) FROM sources WHERE kind = 'channel' AND enabled = 1").fetchone()[0],
        "hashtags": c("SELECT COUNT(*) FROM sources WHERE kind = 'hashtag' AND enabled = 1").fetchone()[0],
        "snapshots": c("SELECT COUNT(*) FROM video_stats").fetchone()[0],
        "runs": c("SELECT COUNT(*) FROM runs").fetchone()[0],
    }
    last_run = c("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    top = films(conn, f, limit=10, sort="score")["items"]
    rising = films(conn, f, limit=10, sort="fresh")["items"]
    opp = [g for g in films(conn, f, limit=300, sort="median")["items"] if g.get("opportunity")][:10]
    rec_rows = _base_rows(conn, f)
    all_rows = _base_rows(conn, f, recognized=False)
    rec_views = sum((r["views"] or 0) for r in rec_rows)
    all_views = sum((r["views"] or 0) for r in all_rows)
    # по дням: сколько шортсов опубликовано и сколько просмотров они набрали
    daily: dict[str, dict] = defaultdict(lambda: {"n": 0, "views": 0, "n_all": 0})
    days = f.days or 90
    start = (_now() - timedelta(days=days)).strftime("%Y-%m-%d")
    for r in rec_rows:
        day = (r["published_at"] or r["first_seen_at"])[:10]
        if day >= start:
            daily[day]["n"] += 1
            daily[day]["views"] += r["views"] or 0
    for r in all_rows:
        day = (r["published_at"] or r["first_seen_at"])[:10]
        if day >= start:
            daily[day]["n_all"] += 1
    # полнота: сколько роликов периода вообще удалось опознать
    meth = Counter(r["recog_method"] for r in rec_rows)
    return {
        "totals": totals,
        "period": {"n_all": len(all_rows), "n_recognized": len(rec_rows), "views_all": all_views, "views_recognized": rec_views,
                   "views_median_recognized": round(_median([r["views"] or 0 for r in rec_rows])),
                   "titles": len({r["title_id"] for r in rec_rows}), "channels": len({r["channel_id"] for r in rec_rows if r["channel_id"]}),
                   "methods": dict(meth)},
        "last_run": dict(last_run) | {"stats": loads(last_run["stats"], {})} if last_run else None,
        "top": top, "rising": rising, "opportunities": opp,
        "daily": [{"day": d, **x} for d, x in sorted(daily.items())],
    }


# ---------- экспорт ----------
def films_csv(conn: sqlite3.Connection, f: Filters) -> str:
    data = films(conn, f, limit=100000)["items"]
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["title_id", "Название", "Год", "Тип", "Жанры", "Страны", "Шортсов", "Свежих за 7 дн", "Каналов", "Просмотры всего",
                "Медиана просмотров", "P90", "Макс", "Просмотров в день", "Прирост 7 дн (замеры)", "Лайк-рейт %", "Ср. длительность", "Балл", "Возможность"])
    for g in data:
        w.writerow([g["title_id"], g["label"], g["year"], g["kind_ru"], ", ".join(g["genres"]), ", ".join(g["countries"]), g["n_shorts"], g["n_fresh7"],
                    g["n_channels"], g["views_sum"], g["views_median"], g["views_p90"], g["views_max"], g["vpd_sum"], g["growth7"], g["like_rate"],
                    g["dur_avg"], g["score"], "да" if g.get("opportunity") else ""])
    return buf.getvalue()


def shorts_csv(conn: sqlite3.Connection, f: Filters, state: str = "all") -> str:
    data = shorts(conn, f, state=state, limit=100000)["items"]
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["video_id", "Ссылка", "Заголовок", "Канал", "Длительность", "Опубликован", "Просмотры", "Лайки", "Просм./день", "Язык",
                "Фильм", "Год", "Уверенность", "Метод", "Улика", "Хэштеги"])
    for v in data:
        w.writerow([v["video_id"], v["url"], v["title"], v["channel_name"], v["duration_s"], v["published_at"], v["views"], v["likes"], v["vpd"],
                    v["lang"], v["title_label"], v["title_year"], v["recog_conf"], v["recog_method"], v["recog_evidence"], " ".join(v["hashtags"])])
    return buf.getvalue()
