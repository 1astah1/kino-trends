"""Календарь нарезки: раскладывает рекомендации по дням. Сегодня этот сериал, завтра тот фильм.

Правила: берём произведения по убыванию «возможности», не повторяем одно и то же раньше cooldown,
чередуем типы (после сериала фильм, после фильма сериал или мультфильм), ручные правки и отметки
«сделано/пропустить» при пересборке не трогаем.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from . import recommend, rights
from .db import dumps, get_setting, loads, now_iso
from .stats import KIND_RU, Filters

MSK = timezone(timedelta(hours=3))
WD = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def today_msk() -> str:
    return datetime.now(MSK).strftime("%Y-%m-%d")


def _days(start: str, n: int) -> list[str]:
    d0 = datetime.strptime(start, "%Y-%m-%d")
    return [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


def build(conn: sqlite3.Connection, f: Filters, days: int = 7, per_day: int = 2, cooldown: int = 10, start: str | None = None,
          rebuild: bool = False) -> dict:
    """Достраивает календарь на days дней вперёд. rebuild=True пересобирает незафиксированные слоты заново."""
    start = start or today_msk()
    day_list = _days(start, days)
    ts = now_iso()
    recs = recommend.today(conn, f, limit=80)["items"]
    pool = {r["title_id"]: r for r in recs}
    since = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=cooldown)).strftime("%Y-%m-%d")
    # проверка по авторским правам: не прошедшие политику уходят из пула и из незафиксированных слотов впереди
    policy = get_setting(conn, "rights_policy")
    future_ids = [r[0] for r in conn.execute("SELECT DISTINCT title_id FROM schedule WHERE title_id IS NOT NULL AND day >= ?", (start,))]
    lv = rights.levels(conn, list(pool) + future_ids)
    blocked = {tid for tid, r in lv.items() if not rights.allowed(r["level"], policy)}
    if blocked:
        conn.execute(f"DELETE FROM schedule WHERE day >= ? AND locked = 0 AND status = 'planned' AND title_id IN ({','.join('?' * len(blocked))})", (start, *blocked))
        pool = {k: v for k, v in pool.items() if k not in blocked}
    if rebuild:
        conn.execute("DELETE FROM schedule WHERE day >= ? AND locked = 0 AND status = 'planned'", (start,))
    existing = {(r["day"], r["slot"]): r for r in conn.execute("SELECT * FROM schedule WHERE day >= ?", (since,))}
    used: dict[str, str] = {}  # title_id -> день последнего использования
    for (day, _slot), r in existing.items():
        if r["title_id"] and r["status"] != "skipped":
            used[r["title_id"]] = max(used.get(r["title_id"], ""), day)
    prev_kind: str | None = None
    for day in day_list:
        for slot in range(per_day):
            row = existing.get((day, slot))
            if row and (row["locked"] or row["status"] != "planned" or row["title_id"]):
                prev_kind = pool.get(row["title_id"], {}).get("kind_ru") if row["title_id"] else prev_kind
                continue
            best = None
            best_score = -1e9
            for tid, r in pool.items():
                last = used.get(tid)
                if last and (datetime.strptime(day, "%Y-%m-%d") - datetime.strptime(last, "%Y-%m-%d")).days < cooldown:
                    continue
                score = r["opportunity"]
                if prev_kind and r["kind_ru"] == prev_kind:
                    score -= 12  # чередуем типы
                if r["n_fresh7"]:
                    score += 3
                if score > best_score:
                    best, best_score = r, score
            if not best:
                break
            reason = "; ".join(best["why"][:2])
            plan = dumps({"duration": best["plan"].get("duration"), "hook": best["plan"].get("hook"), "best_time": best["plan"].get("best_time"),
                          "hashtags": best["plan"].get("hashtags"), "refs": best["plan"].get("refs"), "scenes": best.get("scenes"), "hooks": best.get("hooks")})
            conn.execute("""INSERT INTO schedule(day, slot, title_id, reason, plan, status, locked, created_at, updated_at) VALUES (?,?,?,?,?,'planned',0,?,?)
                            ON CONFLICT(day, slot) DO UPDATE SET title_id = excluded.title_id, reason = excluded.reason, plan = excluded.plan, updated_at = excluded.updated_at""",
                         (day, slot, best["title_id"], reason, plan, ts, ts))
            used[best["title_id"]] = day
            prev_kind = best["kind_ru"]
    conn.commit()
    return view(conn, start, days)


def view(conn: sqlite3.Connection, start: str | None = None, days: int = 7, back: int = 3) -> dict:
    start = start or today_msk()
    d0 = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=back)).strftime("%Y-%m-%d")
    d1 = (datetime.strptime(start, "%Y-%m-%d") + timedelta(days=days - 1)).strftime("%Y-%m-%d")
    rows = conn.execute("""SELECT s.*, COALESCE(t.label_ru, t.label_en) AS label, t.year, t.kind FROM schedule s LEFT JOIN titles t ON t.id = s.title_id
                           WHERE s.day BETWEEN ? AND ? ORDER BY s.day, s.slot""", (d0, d1)).fetchall()
    by_day: dict[str, list] = {}
    lv = rights.levels(conn, [r["title_id"] for r in rows if r["title_id"]])
    for r in rows:
        src = conn.execute("SELECT video_id, url, label, channel, duration_s, verified, kind, season, episode, language FROM title_sources WHERE title_id = ? ORDER BY verified DESC, score DESC LIMIT 1", (r["title_id"],)).fetchone() if r["title_id"] else None
        alt = None
        if src:
            other = "rutube.ru" if "youtube.com" in src["url"] else "youtube.com"
            alt = conn.execute("SELECT video_id, url, label, channel, duration_s, verified, kind, season, episode, language FROM title_sources WHERE title_id = ? AND verified = 1 AND url LIKE ? ORDER BY score DESC LIMIT 1", (r["title_id"], f"%{other}%")).fetchone()
        ex = conn.execute("SELECT text FROM title_explain WHERE title_id = ?", (r["title_id"],)).fetchone() if r["title_id"] else None
        kp = conn.execute("SELECT kp_id FROM titles WHERE id = ?", (r["title_id"],)).fetchone() if r["title_id"] else None
        by_day.setdefault(r["day"], []).append({
            "id": r["id"], "day": r["day"], "slot": r["slot"], "title_id": r["title_id"], "label": r["label"], "year": r["year"],
            "kind_ru": KIND_RU.get(r["kind"], r["kind"]), "reason": r["reason"], "plan": loads(r["plan"], {}), "status": r["status"], "locked": bool(r["locked"]),
            "source": dict(src) if src else None, "source_alt": dict(alt) if alt else None, "explain": ex["text"] if ex else None,
            "rights": lv.get(r["title_id"]) if r["title_id"] else None,
            "kinopoisk_url": f"https://www.kinopoisk.ru/{'series' if r['kind'] in ('series', 'animated_series', 'anime', 'tv_show') else 'film'}/{kp['kp_id']}/" if kp and kp["kp_id"] else None,
        })
    out = []
    for day in _days(d0, back + days):
        dt = datetime.strptime(day, "%Y-%m-%d")
        out.append({"day": day, "weekday": WD[dt.weekday()], "is_today": day == start, "is_past": day < start, "items": by_day.get(day, [])})
    return {"today": start, "days": out}


def set_status(conn: sqlite3.Connection, item_id: int, status: str) -> None:
    if status not in ("planned", "done", "skipped"):
        raise ValueError("статус: planned|done|skipped")
    conn.execute("UPDATE schedule SET status = ?, updated_at = ? WHERE id = ?", (status, now_iso(), item_id))
    conn.commit()


def replace(conn: sqlite3.Connection, item_id: int, title_id: str | None, f: Filters) -> None:
    """Ручная замена произведения в слоте; слот фиксируется и при пересборке не меняется."""
    reason, plan = "выбрано вручную", "{}"
    if title_id:
        for r in recommend.today(conn, f, limit=200)["items"]:
            if r["title_id"] == title_id:
                reason = "вручную; " + "; ".join(r["why"][:2])
                plan = dumps({"duration": r["plan"].get("duration"), "hook": r["plan"].get("hook"), "best_time": r["plan"].get("best_time"),
                              "hashtags": r["plan"].get("hashtags"), "refs": r["plan"].get("refs")})
                break
    conn.execute("UPDATE schedule SET title_id = ?, reason = ?, plan = ?, locked = 1, status = 'planned', updated_at = ? WHERE id = ?",
                 (title_id, reason, plan, now_iso(), item_id))
    conn.commit()


def add_slot(conn: sqlite3.Connection, day: str, title_id: str, f: Filters) -> int:
    slot = (conn.execute("SELECT COALESCE(MAX(slot), -1) + 1 FROM schedule WHERE day = ?", (day,)).fetchone()[0])
    ts = now_iso()
    cur = conn.execute("INSERT INTO schedule(day, slot, title_id, reason, plan, status, locked, created_at, updated_at) VALUES (?,?,?,?,?,'planned',1,?,?)",
                       (day, slot, title_id, "добавлено вручную", "{}", ts, ts))
    conn.commit()
    replace(conn, cur.lastrowid, title_id, f)
    return cur.lastrowid


def digest_lines(conn: sqlite3.Connection) -> list[str]:
    """Строки для Telegram: сегодня и завтра."""
    v = view(conn, days=2, back=0)
    out = []
    for i, d in enumerate(v["days"][:2]):
        names = [f"{it['label']} ({it['kind_ru']})" for it in d["items"] if it["label"] and it["status"] != "skipped"]
        if names:
            out.append(("Сегодня" if i == 0 else "Завтра") + f" режем: " + "; ".join(names))
    return out
