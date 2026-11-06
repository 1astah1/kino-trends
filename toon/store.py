"""Серии и журнал решений агентов (sqlite Кино-трендов, var/ — вне git)."""

from __future__ import annotations

import json
import sqlite3

from kinotrends.db import now_iso


def init(conn: sqlite3.Connection) -> None:
    conn.executescript("""
CREATE TABLE IF NOT EXISTS toon_episodes(
  id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, status TEXT NOT NULL, format TEXT, seed TEXT,
  fact TEXT, title TEXT, logline TEXT, script TEXT, review TEXT, video TEXT, package_id INTEGER, channel TEXT);
CREATE TABLE IF NOT EXISTS toon_events(
  id INTEGER PRIMARY KEY, episode_id INTEGER NOT NULL, ts TEXT NOT NULL, agent TEXT, action TEXT, detail TEXT, data TEXT);
""")
    conn.commit()


def create(conn: sqlite3.Connection, fmt: str, seed: str, fact: dict | None) -> int:
    ts = now_iso()
    cur = conn.execute("INSERT INTO toon_episodes(created_at, updated_at, status, format, seed, fact) VALUES (?, ?, 'writing', ?, ?, ?)",
                       (ts, ts, fmt, seed, json.dumps(fact, ensure_ascii=False) if fact else None))
    conn.commit()
    return int(cur.lastrowid)


def save(conn: sqlite3.Connection, ep: int, **cols) -> None:
    if "script" in cols and cols["script"] is not None:
        sc = cols["script"]
        cols.setdefault("title", sc.get("title"))
        cols.setdefault("logline", sc.get("logline"))
    for k in ("script", "review"):
        if k in cols and not isinstance(cols[k], str):
            cols[k] = json.dumps(cols[k], ensure_ascii=False) if cols[k] is not None else None
    cols["updated_at"] = now_iso()
    sets = ", ".join(f"{k} = ?" for k in cols)
    conn.execute(f"UPDATE toon_episodes SET {sets} WHERE id = ?", (*cols.values(), ep))  # noqa: S608 — ключи свои
    conn.commit()


def event(conn: sqlite3.Connection, ep: int, agent: str, action: str, detail: str = "", data=None) -> None:
    conn.execute("INSERT INTO toon_events(episode_id, ts, agent, action, detail, data) VALUES (?, ?, ?, ?, ?, ?)",
                 (ep, now_iso(), agent, action, detail, json.dumps(data, ensure_ascii=False) if data is not None else None))
    conn.commit()


def recent_loglines(conn: sqlite3.Connection, n: int = 30) -> list[str]:
    rows = conn.execute("SELECT title, logline FROM toon_episodes WHERE logline IS NOT NULL ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    return [f"«{r[0]}» — {r[1]}" for r in reversed(rows)]


def recent_formats(conn: sqlite3.Connection, n: int = 12) -> list[str]:
    return [r[0] for r in conn.execute("SELECT format FROM toon_episodes ORDER BY id DESC LIMIT ?", (n,))]


def episode(conn: sqlite3.Connection, ep: int) -> dict:
    conn.row_factory = sqlite3.Row
    r = dict(conn.execute("SELECT * FROM toon_episodes WHERE id = ?", (ep,)).fetchone())
    for k in ("script", "review", "fact"):
        r[k] = json.loads(r[k]) if r.get(k) else None
    r["events"] = [dict(e) for e in conn.execute("SELECT ts, agent, action, detail FROM toon_events WHERE episode_id = ? ORDER BY id", (ep,))]
    return r
