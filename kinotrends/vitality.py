"""Жизнь каналов: когда канал «умер» (ролики заливаются, а YouTube их не показывает) и когда ожил.

Владелец 06.11.2026: «нужно фиксировать, когда акк у нас умер — видосы заливаются, а не улетают, по нулям».
Каждый ролик оценивается по просмотрам за первые сутки (v24: снимки pub_views, между ними — по прямой):
  ноль  — v24 ≤ vital_zero_views (20): YouTube не дал ролику даже первого круга показов;
  живой — v24 ≥ vital_alive_views (100).
Канал УМЕР, если у него уже были живые ролики, а затем vital_dead_streak (4) зрелых роликов подряд — нули;
момент смерти — выход первого нуля этой серии. ОЖИЛ — первый живой ролик после смерти. Канал, у которого живых
роликов не было ни разу, «не взлетал». Ролики, снятые или заблокированные за права, серию не рвут и не продолжают:
это другая причина. События пишутся в channel_events один раз (повторный проход их не дублирует)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .db import get_meta, now_iso, set_meta

MATURE_H = 24.0
ZERO_VIEWS = 20
ALIVE_VIEWS = 100
DEAD_STREAK = 4

KIND_RU = {"died": "умер", "revived": "ожил", "note": "заметка"}
STATUS_RU = {"alive": "жив", "dead": "умер", "dying": "умирает", "never": "не взлетал", "weak": "слабый", "new": "мало данных"}


def init(conn: sqlite3.Connection) -> None:
    conn.executescript("""
CREATE TABLE IF NOT EXISTS channel_events(
  id INTEGER PRIMARY KEY, channel_id TEXT NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL, detected_at TEXT NOT NULL,
  title TEXT, evidence TEXT, UNIQUE(channel_id, kind, at));
""")
    conn.commit()


def _age_h(published_at: str, now: datetime) -> float:
    t = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (now - t).total_seconds() / 3600


def v24(snaps: list[tuple[float, int]], age_now: float, views_now: int | None) -> tuple[int | None, bool]:
    """(просмотры к 24 ч, точно ли). snaps — [(возраст ч, просмотры)]. Моложе суток — (None, False).
    Без снимка до суток: если и сейчас ≤ порога нуля — это верхняя граница (ноль точно), иначе приблизительно."""
    if age_now < MATURE_H:
        return None, False
    pts = sorted(p for p in snaps if p[1] is not None)
    if views_now is not None and (not pts or age_now > pts[-1][0]):
        pts.append((age_now, int(views_now)))
    if not pts:
        return None, False
    before = [p for p in pts if p[0] <= MATURE_H]
    after = [p for p in pts if p[0] >= MATURE_H]
    if before and after:
        (a0, v0), (a1, v1) = before[-1], after[0]
        if a1 == a0:
            return int(v1), True
        return int(round(v0 + (v1 - v0) * (MATURE_H - a0) / (a1 - a0))), True
    v = after[0][1] if after else before[-1][1]
    return int(v), v <= ZERO_VIEWS


def timeline(rows: list[dict], zero: int = ZERO_VIEWS, alive: int = ALIVE_VIEWS, streak: int = DEAD_STREAK) -> dict:
    """rows — зрелые ролики канала по времени выхода: {video_id, published_at, v24, title, blocked}.
    → {status, since, events: [{kind, at, title, evidence}], zero_run, last_alive}."""
    events: list[dict] = []
    ever_alive = False
    dead = False
    run: list[dict] = []
    last_alive: dict | None = None
    first: dict | None = None
    counted = zeros = 0
    for r in rows:
        if r.get("blocked") or r.get("v24") is None:
            continue
        first = first or r
        v = int(r["v24"])
        counted += 1
        zeros += v <= zero
        if v >= alive:
            if dead:
                events.append({"kind": "revived", "at": r["published_at"], "title": r.get("title"),
                               "evidence": {"video_id": r["video_id"], "v24": v, "zeros_before": len(run)}})
            dead, ever_alive, last_alive, run = False, True, r, []
        elif v <= zero:
            run.append(r)
            if len(run) == streak and ever_alive and not dead:
                dead = True
                events.append({"kind": "died", "at": run[0]["published_at"], "title": run[0].get("title"),
                               "evidence": {"zeros": [{"video_id": x["video_id"], "v24": int(x["v24"]), "title": x.get("title"),
                                                       "published_at": x["published_at"]} for x in run],
                                            "last_alive": last_alive and {"video_id": last_alive["video_id"], "v24": int(last_alive["v24"]),
                                                                          "title": last_alive.get("title"), "published_at": last_alive["published_at"]}}})
        else:
            run = []  # между нулём и живым: показы есть, серия нулей прервана
    if first is None:
        status, since = "new", None
    elif dead:
        status, since = "dead", next(e["at"] for e in reversed(events) if e["kind"] == "died")
    elif not ever_alive:
        # живых не было: мало роликов — рано судить; нулей набралось на серию — «не взлетал»; иначе слабый
        status = "new" if counted < streak else "never" if zeros >= streak else "weak"
        since = first["published_at"]
    elif len(run) >= 2:
        status, since = "dying", run[0]["published_at"]
    else:
        status, since = "alive", last_alive and last_alive["published_at"]
    return {"status": status, "since": since, "events": events, "zero_run": len(run),
            "last_alive": last_alive and {"video_id": last_alive["video_id"], "v24": int(last_alive["v24"]), "published_at": last_alive["published_at"],
                                          "title": last_alive.get("title")}}


def _rows(conn: sqlite3.Connection, now: datetime) -> dict[str, list[dict]]:
    snaps: dict[str, list[tuple[float, int]]] = {}
    for s in conn.execute("SELECT video_id, age_h, views FROM pub_views"):
        snaps.setdefault(s["video_id"], []).append((float(s["age_h"] or 0), s["views"]))
    by: dict[str, list[dict]] = {}
    for o in conn.execute("SELECT video_id, channel_id, channel_title, title_label, published_at, views, status, block "
                          "FROM pub_outcomes WHERE channel_id IS NOT NULL AND published_at IS NOT NULL"):
        from .outcomes import _iso
        pub = _iso(o["published_at"])
        val, _exact = v24(snaps.get(o["video_id"], []), _age_h(pub, now), o["views"])
        by.setdefault(o["channel_id"], []).append({
            "video_id": o["video_id"], "published_at": pub, "v24": val, "title": o["title_label"], "channel_title": o["channel_title"],
            "blocked": bool(o["block"])})
    for rs in by.values():
        rs.sort(key=lambda r: r["published_at"])
    return by


def run(conn: sqlite3.Connection, settings: dict | None = None, log=print, now: datetime | None = None,
        names: dict[str, str] | None = None) -> dict:
    """Пересчитать жизнь каналов, записать новые события. → {channels: {id: …}, new: [события]}."""
    from . import outcomes
    outcomes.init(conn)
    init(conn)
    st = settings or {}
    zero = int(st.get("vital_zero_views") or ZERO_VIEWS)
    alive = int(st.get("vital_alive_views") or ALIVE_VIEWS)
    streak = int(st.get("vital_dead_streak") or DEAD_STREAK)
    now = now or datetime.now(timezone.utc)
    channels: dict[str, dict] = {}
    new: list[dict] = []
    for ch, rows in _rows(conn, now).items():
        t = timeline(rows, zero, alive, streak)
        t["title"] = (names or {}).get(ch) or next((r["channel_title"] for r in reversed(rows) if r.get("channel_title")), ch)
        t["recent"] = [{"video_id": r["video_id"], "v24": r["v24"], "published_at": r["published_at"], "blocked": r["blocked"]}
                       for r in rows if r["v24"] is not None][-16:]
        channels[ch] = t
        for e in t["events"]:
            cur = conn.execute("INSERT OR IGNORE INTO channel_events(channel_id, kind, at, detected_at, title, evidence) VALUES (?, ?, ?, ?, ?, ?)",
                               (ch, e["kind"], e["at"], now_iso(), e.get("title"), json.dumps(e["evidence"], ensure_ascii=False)))
            if cur.rowcount:
                new.append({"channel_id": ch, "channel": t["title"], **e})
                log(f"жизнь каналов: {t['title']} {KIND_RU[e['kind']]} — {e['at'][:16].replace('T', ' ')} UTC")
    set_meta(conn, "vitality", json.dumps({"at": now_iso(), "channels": channels, "thresholds": {"zero": zero, "alive": alive, "streak": streak}},
                                          ensure_ascii=False))
    conn.commit()
    return {"channels": channels, "new": new}


def note(conn: sqlite3.Connection, channel_id: str, at: str, text: str) -> None:
    """Заметка на шкале канала (смена формата, ручное действие) — чтобы смерть было с чем сопоставить."""
    init(conn)
    conn.execute("INSERT OR IGNORE INTO channel_events(channel_id, kind, at, detected_at, title, evidence) VALUES (?, 'note', ?, ?, ?, '{}')",
                 (channel_id, at, now_iso(), text))
    conn.commit()


def state(conn: sqlite3.Connection) -> dict:
    """Последний расчёт + история событий — для «Итогов» и доктора."""
    init(conn)
    cur = json.loads(get_meta(conn, "vitality", "") or "{}")
    cur["events"] = [dict(r, evidence=json.loads(r["evidence"] or "{}")) for r in
                     conn.execute("SELECT channel_id, kind, at, detected_at, title, evidence FROM channel_events ORDER BY at DESC LIMIT 200")]
    return cur


def checks(conn: sqlite3.Connection) -> list:
    """Проверки для доктора: умерший канал — fail (инцидент и уведомление), умирающий — warn."""
    from .doctor import Check
    s = state(conn)
    out = []
    for ch, t in sorted((s.get("channels") or {}).items()):
        since = (t.get("since") or "")[:16].replace("T", " ")
        st = t.get("status")
        level = {"dead": "fail", "dying": "warn", "never": "warn"}.get(st, "ok")
        la = t.get("last_alive") or {}
        detail = {"dead": f"с {since} UTC живых роликов нет; последний живой — {(la.get('published_at') or '')[:10]}, {la.get('v24')} просм. за сутки",
                  "dying": f"{t.get('zero_run')} нуля подряд с {since} UTC",
                  "never": "живых роликов не было ни разу"}.get(st, "")
        out.append(Check(f"vital.{ch}", "Каналы", f"Жизнь канала {t.get('title') or ch}", level, STATUS_RU.get(st, st or "?"), detail))
    return out
