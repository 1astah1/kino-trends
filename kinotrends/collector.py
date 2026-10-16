"""Прогон сбора: хэштеги → каналы → детали роликов → распознавание → автослежение за каналами.

Один прогон работает в одном потоке со своим подключением к SQLite. Состояние прогона (фаза, журнал)
лежит в STATE и читается API для интерфейса. Отмена — через STATE.cancel.
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timedelta, timezone

from . import db, files, interest, neuro, recommend, schedule, sources, telegram
from .db import dumps, loads, now_iso
from .llm import llm_from_settings
from .recognize import GENERIC_TAGS, Recognizer, result_json
from .stats import Filters
from .text import detect_lang, hashtags, season_episode, title_features
from .yt import dataapi, flat, page, rss
from .yt.models import VideoDetails


NONFILM_TAGS = {
    "shorts", "short", "shortvideo", "shortsvideo", "youtubeshorts", "шортс", "шорт", "film", "movie", "movies", "films", "video", "видео",
    "мемы", "мем", "прикол", "приколы", "юмор", "смех", "рекомендации", "рек", "реки", "viral", "trending", "тренды", "тренд", "fyp", "foryou",
    "семья", "любовь", "жизнь", "мотивация", "цитаты", "эмоции", "music", "музыка", "песня", "танец", "dance", "funny", "comedy", "humor",
    "lol", "meme", "memes", "подписывайся", "подпишись", "лайк", "интересно", "интересное", "факты", "факт", "момент", "моменты", "edit",
    "эдит", "топ", "лучшее", "жиза", "ржака", "ржач", "угар", "смешно", "вайб", "vibe", "эстетика", "aesthetic", "красиво", "кадры",
}


class RunState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.run_id: int | None = None
        self.trigger: str | None = None
        self.started_at: str | None = None
        self.phase = ""
        self.progress = ""
        self.cancel = threading.Event()
        self.log_tail: deque[dict] = deque(maxlen=300)
        self.stats: dict = {}

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "running": self.running,
                "run_id": self.run_id,
                "trigger": self.trigger,
                "started_at": self.started_at,
                "phase": self.phase,
                "progress": self.progress,
                "stats": dict(self.stats),
                "log": list(self.log_tail)[-80:],
            }


STATE = RunState()


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Collector:
    def __init__(self, conn: sqlite3.Connection, recognizer: Recognizer, trigger: str = "manual", state: RunState = STATE) -> None:
        self.conn = conn
        self.rec = recognizer
        self.trigger = trigger
        self.state = state
        self.run_id: int | None = None
        self.stats: dict = {
            "sources_ok": 0, "sources_err": 0, "discovered": 0, "new_videos": 0, "snapshots": 0,
            "details_ok": 0, "details_removed": 0, "details_blocked": 0, "details_error": 0, "details_source": "page",
            "rss_ok": 0, "rss_err": 0, "recognized_new": 0, "recognized_checked": 0, "auto_channels_added": 0,
            "api_quota_used": 0, "aborted": False, "duration_s": 0,
            "neuro_checked": 0, "neuro_recognized": 0, "neuro_cost_usd": 0.0, "auto_hashtags_added": 0, "alerts_sent": 0,
            "comments_fetched": 0, "interest_wiki": 0, "interest_trends": 0,
        }
        self.settings = db.all_settings(conn)

    # ---------- служебное ----------
    def _write(self, sql: str, params: tuple, attempts: int = 8) -> None:
        """Запись с повторами: соседние процессы могут держать блокировку дольше busy_timeout."""
        for i in range(attempts):
            try:
                self.conn.execute(sql, params)
                self.conn.commit()
                return
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower() or i == attempts - 1:
                    raise
                time.sleep(10)

    def log(self, msg: str, level: str = "info") -> None:
        ts = now_iso()
        with self.state.lock:
            self.state.log_tail.append({"ts": ts, "level": level, "msg": msg})
        if self.run_id is not None:
            try:
                self._write("INSERT INTO run_log(run_id, ts, level, msg) VALUES (?,?,?,?)", (self.run_id, ts, level, msg), attempts=3)
            except sqlite3.OperationalError:
                pass

    def phase(self, name: str, progress: str = "") -> None:
        with self.state.lock:
            self.state.phase = name
            self.state.progress = progress
            self.state.stats = dict(self.stats)

    def cancelled(self) -> bool:
        return self.state.cancel.is_set()

    def setting_int(self, key: str) -> int:
        try:
            return int(self.settings.get(key) or db.DEFAULT_SETTINGS[key])
        except ValueError:
            return int(db.DEFAULT_SETTINGS[key])

    # ---------- запись роликов ----------
    def seen(self, e: dict, ts: str, src: str, source_id: int | None, rank: int) -> bool:
        """Фиксирует, что ролик встретился в выдаче. Возвращает True, если ролик новый."""
        conn = self.conn
        cur = conn.execute(
            """INSERT OR IGNORE INTO videos(video_id, title, thumb, channel_id, channel_name, first_seen_at, last_seen_at, views, stats_at, status)
               VALUES (?,?,?,?,?,?,?,?,?, 'ok')""",
            (e["video_id"], e.get("title"), e.get("thumb"), e.get("channel_id"), e.get("channel"), ts, ts, e.get("views"), ts if e.get("views") is not None else None),
        )
        is_new = cur.rowcount == 1
        if not is_new:
            conn.execute(
                """UPDATE videos SET last_seen_at = ?, title = COALESCE(title, ?), thumb = COALESCE(thumb, ?),
                   channel_id = COALESCE(channel_id, ?), channel_name = COALESCE(channel_name, ?),
                   views = CASE WHEN ? IS NOT NULL THEN ? ELSE views END,
                   stats_at = CASE WHEN ? IS NOT NULL THEN ? ELSE stats_at END
                   WHERE video_id = ?""",
                (ts, e.get("title"), e.get("thumb"), e.get("channel_id"), e.get("channel"),
                 e.get("views"), e.get("views"), e.get("views"), ts, e["video_id"]),
            )
        if e.get("views") is not None:
            conn.execute("INSERT OR REPLACE INTO video_stats(video_id, ts, views, likes, comments, src) VALUES (?,?,?,?,?,?)",
                         (e["video_id"], ts, e["views"], e.get("likes"), None, src))
            self.stats["snapshots"] += 1
        if source_id is not None:
            conn.execute(
                """INSERT INTO video_sources(video_id, source_id, first_seen_at, last_seen_at, last_rank) VALUES (?,?,?,?,?)
                   ON CONFLICT(video_id, source_id) DO UPDATE SET last_seen_at = excluded.last_seen_at, last_rank = excluded.last_rank""",
                (e["video_id"], source_id, ts, ts, rank),
            )
        self.stats["discovered"] += 1
        if is_new:
            self.stats["new_videos"] += 1
        return is_new

    def upsert_channel(self, channel_id: str | None, name: str | None, ts: str, handle: str | None = None, followers: int | None = None) -> None:
        if not channel_id:
            return
        self.conn.execute(
            """INSERT INTO channels(channel_id, name, handle, followers, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?)
               ON CONFLICT(channel_id) DO UPDATE SET name = COALESCE(excluded.name, channels.name), handle = COALESCE(excluded.handle, channels.handle),
                 followers = COALESCE(excluded.followers, channels.followers), last_seen_at = excluded.last_seen_at""",
            (channel_id, name, handle, followers, ts, ts),
        )

    def apply_details(self, d: VideoDetails, ts: str) -> None:
        conn = self.conn
        if d.status in ("bot", "ratelimit", "error"):
            conn.execute("UPDATE videos SET details_fail = details_fail + 1 WHERE video_id = ?", (d.video_id,))
            return
        if d.status == "removed":
            conn.execute("UPDATE videos SET status = 'removed', details_at = ?, details_source = ? WHERE video_id = ?", (ts, d.source, d.video_id))
            return
        status = "not_short" if d.is_short is False or d.status == "not_short" else "ok"
        tags = hashtags(d.title, d.description)
        lang = detect_lang(d.title, d.description)
        feats = title_features(d.title, d.description)
        season, episode = season_episode(d.title, d.description)
        conn.execute(
            """UPDATE videos SET title = COALESCE(?, title), description = ?, channel_id = COALESCE(?, channel_id),
                 channel_name = COALESCE(?, channel_name), duration_s = COALESCE(?, duration_s),
                 published_at = COALESCE(?, published_at), category = COALESCE(?, category), keywords = ?, hashtags = ?,
                 lang = ?, avail_ru = COALESCE(?, avail_ru), status = ?, details_at = ?, details_source = ?, details_fail = 0,
                 views = COALESCE(?, views), likes = COALESCE(?, likes), comments = COALESCE(?, comments),
                 stats_at = CASE WHEN ? IS NOT NULL THEN ? ELSE stats_at END, features = ?, season = ?, episode = ?
               WHERE video_id = ?""",
            (d.title, d.description, d.channel_id, d.channel_name, d.duration_s, d.published_at, d.category,
             dumps(d.keywords), dumps(tags), lang, None if d.avail_ru is None else int(d.avail_ru), status, ts, d.source,
             d.views, d.likes, d.comments, d.views, ts, dumps(feats), season, episode, d.video_id),
        )
        if d.views is not None:
            conn.execute("INSERT OR REPLACE INTO video_stats(video_id, ts, views, likes, comments, src) VALUES (?,?,?,?,?,?)",
                         (d.video_id, ts, d.views, d.likes, d.comments, d.source))
            self.stats["snapshots"] += 1
        self.upsert_channel(d.channel_id, d.channel_name, ts)

    # ---------- фазы ----------
    def collect_hashtags(self) -> None:
        depth = self.setting_int("hashtag_depth")
        rows = self.conn.execute("SELECT * FROM sources WHERE kind = 'hashtag' AND enabled = 1 ORDER BY id").fetchall()
        for i, s in enumerate(rows, 1):
            if self.cancelled():
                return
            self.phase("хэштеги", f"{i}/{len(rows)} #{s['value']}")
            ts = now_iso()
            try:
                res = flat.hashtag_shorts(s["value"], depth)
            except Exception as e:  # noqa: BLE001
                self.stats["sources_err"] += 1
                self.conn.execute("UPDATE sources SET last_run_at = ?, last_error = ? WHERE id = ?", (ts, str(e)[:300], s["id"]))
                self.conn.commit()
                self.log(f"#{s['value']}: ошибка {e}", "error")
                continue
            new = 0
            for rank, e in enumerate(res["entries"], 1):
                if e.get("is_short") is False:
                    continue
                if self.seen(e, ts, "hashtag", s["id"], rank):
                    new += 1
            self.conn.execute("UPDATE sources SET last_run_at = ?, last_count = ?, last_new = ?, last_error = NULL WHERE id = ?",
                              (ts, len(res["entries"]), new, s["id"]))
            self.conn.commit()
            self.stats["sources_ok"] += 1
            self.log(f"#{s['value']}: {len(res['entries'])} роликов, новых {new}")

    def collect_channels(self) -> None:
        depth = self.setting_int("channel_depth")
        rows = self.conn.execute("SELECT * FROM sources WHERE kind = 'channel' AND enabled = 1 ORDER BY auto, id").fetchall()
        for i, s in enumerate(rows, 1):
            if self.cancelled():
                return
            self.phase("каналы", f"{i}/{len(rows)} {s['label'] or s['value']}")
            ts = now_iso()
            try:
                res = flat.channel_shorts(s["value"], depth)
            except Exception as e:  # noqa: BLE001
                self.stats["sources_err"] += 1
                self.conn.execute("UPDATE sources SET last_run_at = ?, last_error = ? WHERE id = ?", (ts, str(e)[:300], s["id"]))
                self.conn.commit()
                self.log(f"канал {s['value']}: ошибка {e}", "error")
                continue
            ch = res.get("channel") or {}
            cid = ch.get("channel_id") or s["value"]
            self.upsert_channel(cid, ch.get("name"), ts, ch.get("handle"), ch.get("followers"))
            if ch.get("name") and not s["label"]:
                self.conn.execute("UPDATE sources SET label = ? WHERE id = ?", (ch["name"], s["id"]))
            new = 0
            for rank, e in enumerate(res["entries"], 1):
                e.setdefault("channel_id", cid)
                e["channel_id"] = e.get("channel_id") or cid
                e["channel"] = e.get("channel") or ch.get("name")
                if self.seen(e, ts, "channel", s["id"], rank):
                    new += 1
            self.conn.execute("UPDATE channels SET shorts_checked_at = ? WHERE channel_id = ?", (ts, cid))
            self.conn.execute("UPDATE sources SET last_run_at = ?, last_count = ?, last_new = ?, last_error = NULL WHERE id = ?",
                              (ts, len(res["entries"]), new, s["id"]))
            self.conn.commit()
            self.stats["sources_ok"] += 1
            # RSS: точные даты и лайки последних 15 роликов
            try:
                feed = rss.fetch_feed(cid)
                for item in feed:
                    e = {"video_id": item["video_id"], "title": item["title"], "views": item["views"], "likes": item["likes"],
                         "channel_id": cid, "channel": ch.get("name")}
                    self.seen(e, ts, "rss", None, 0)
                    self.conn.execute(
                        "UPDATE videos SET published_at = COALESCE(published_at, ?), likes = COALESCE(?, likes), description = COALESCE(description, ?) WHERE video_id = ?",
                        (item["published_at"], item["likes"], item["description"], item["video_id"]),
                    )
                self.conn.execute("UPDATE channels SET rss_checked_at = ? WHERE channel_id = ?", (ts, cid))
                self.conn.commit()
                self.stats["rss_ok"] += 1
            except Exception as e:  # noqa: BLE001
                self.stats["rss_err"] += 1
                self.log(f"RSS {cid}: {e}", "warn")
            self.log(f"канал {ch.get('name') or cid}: {len(res['entries'])} шортсов, новых {new}")

    def collect_searches(self) -> None:
        """Поиск шортсов по названиям произведений из топа и календаря: «<название> #shorts»."""
        f = Filters("30", lang="ru")
        cap = self.setting_int("search_titles_max")
        top = recommend.today(self.conn, f, limit=cap)["items"]
        labels = {t["title_id"]: t["label"] for t in top}
        for d in schedule.view(self.conn, days=self.setting_int("schedule_days"), back=0)["days"]:
            for it in d["items"]:
                if it["title_id"] and it["label"] and it["title_id"] not in labels:
                    labels[it["title_id"]] = it["label"]
        ts = now_iso()
        for tid, label in list(labels.items())[:cap]:
            q = f"{label} #shorts"
            self.conn.execute("INSERT OR IGNORE INTO sources(kind, value, label, enabled, auto, added_at) VALUES ('search', ?, ?, 1, 1, ?)", (q, f"поиск: {label}", ts))
        self.conn.commit()
        rows = self.conn.execute("SELECT * FROM sources WHERE kind = 'search' AND enabled = 1 ORDER BY COALESCE(last_run_at, '') LIMIT ?", (cap,)).fetchall()
        for i, srow in enumerate(rows, 1):
            if self.cancelled():
                return
            self.phase("поиск по названиям", f"{i}/{len(rows)} {srow['label']}")
            ts = now_iso()
            try:
                res = flat.search_shorts(srow["value"], 30)
            except Exception as e:  # noqa: BLE001
                self.stats["sources_err"] += 1
                self.conn.execute("UPDATE sources SET last_run_at = ?, last_error = ? WHERE id = ?", (ts, str(e)[:300], srow["id"]))
                self.conn.commit()
                continue
            new = sum(1 for rank, e in enumerate(res["entries"], 1) if self.seen(e, ts, "search", srow["id"], rank))
            self.conn.execute("UPDATE sources SET last_run_at = ?, last_count = ?, last_new = ?, last_error = NULL WHERE id = ?", (ts, len(res["entries"]), new, srow["id"]))
            self.conn.commit()
            self.stats["sources_ok"] += 1
        if rows:
            self.log(f"поиск по названиям: {len(rows)} запросов")

    def title_hashtags(self) -> None:
        """Хэштеги самих произведений (#рикиморти, #словопацана): по уликам распознавания берём в источники."""
        cap = self.setting_int("title_tags_max")
        have = self.conn.execute("SELECT COUNT(*) FROM sources WHERE kind = 'hashtag' AND auto = 1 AND label LIKE 'тег произведения:%'").fetchone()[0]
        if have >= cap:
            return
        known = {r[0] for r in self.conn.execute("SELECT value FROM sources WHERE kind = 'hashtag'")}
        rows = self.conn.execute("""SELECT lower(substr(recog_evidence, 2)) AS tag, COUNT(*) AS n, COALESCE(t.label_ru, t.label_en) AS label
                                    FROM videos v JOIN titles t ON t.id = v.title_id
                                    WHERE v.recog_method = 'hashtag' AND v.recog_conf >= 0.7 AND v.recog_evidence LIKE '#%'
                                    GROUP BY tag HAVING n >= 3 ORDER BY n DESC LIMIT ?""", (cap,)).fetchall()
        ts = now_iso()
        added = 0
        for r in rows:
            tag = r["tag"]
            if tag in known or len(tag) < 5 or not re.search(r"[а-яёa-z]", tag):
                continue
            self.conn.execute("INSERT OR IGNORE INTO sources(kind, value, label, enabled, auto, added_at) VALUES ('hashtag', ?, ?, 1, 1, ?)", (tag, f"тег произведения: {r['label']}", ts))
            added += 1
            if have + added >= cap:
                break
        self.conn.commit()
        if added:
            self.stats["auto_hashtags_added"] += added
            self.log(f"хэштеги произведений добавлены в сбор: {added}")

    def detail_queue(self) -> list[str]:
        budget = self.setting_int("detail_budget")
        refresh_days = self.setting_int("refresh_days")
        since = _iso(datetime.now(timezone.utc) - timedelta(days=refresh_days))
        stale = _iso(datetime.now(timezone.utc) - timedelta(hours=12))
        fresh = [r[0] for r in self.conn.execute(
            """SELECT video_id FROM videos WHERE details_at IS NULL AND details_fail < 3 AND status = 'ok'
               ORDER BY COALESCE(views, 0) DESC LIMIT ?""", (budget,))]
        rest = max(0, budget - len(fresh))
        refresh: list[str] = []
        if rest:
            refresh = [r[0] for r in self.conn.execute(
                """SELECT video_id FROM videos WHERE details_at IS NOT NULL AND status = 'ok' AND published_at >= ?
                     AND (stats_at IS NULL OR stats_at < ?)
                   ORDER BY COALESCE(views, 0) DESC LIMIT ?""", (since, stale, min(rest, budget // 3 or 1)))]
        return fresh + refresh

    def collect_details(self) -> None:
        queue = self.detail_queue()
        if not queue:
            return
        api_key = (self.settings.get("youtube_api_key") or "").strip()
        ts = now_iso()
        done = 0
        if api_key:
            self.stats["details_source"] = "api"
            self.phase("детали (Data API)", f"0/{len(queue)}")
            try:
                for i in range(0, len(queue), 50):
                    if self.cancelled():
                        return
                    batch = queue[i:i + 50]
                    details, quota = dataapi.fetch_videos(api_key, batch)
                    self.stats["api_quota_used"] += quota
                    for d in details:
                        self.apply_details(d, ts)
                        self.stats["details_ok" if d.status == "ok" else "details_removed"] += 1
                    self.conn.commit()
                    done += len(batch)
                    self.phase("детали (Data API)", f"{done}/{len(queue)}")
                self.log(f"детали через Data API: {done} роликов, квота {self.stats['api_quota_used']}")
                return
            except Exception as e:  # noqa: BLE001
                self.log(f"Data API не сработал ({e}), перехожу на страницы", "warn")
                queue = queue[done:]
        self.stats["details_source"] = "page"
        conc = max(1, min(8, self.setting_int("detail_concurrency")))
        self.phase("детали (страницы)", f"0/{len(queue)}")
        counter = {"n": 0}

        def on_result(d: VideoDetails) -> None:
            self.apply_details(d, ts)
            counter["n"] += 1
            if d.status == "ok" or d.status == "not_short":
                self.stats["details_ok"] += 1
            elif d.status == "removed":
                self.stats["details_removed"] += 1
            elif d.status in ("bot", "ratelimit"):
                self.stats["details_blocked"] += 1
            else:
                self.stats["details_error"] += 1
            if counter["n"] % 25 == 0:
                self.conn.commit()
                self.phase("детали (страницы)", f"{counter['n']}/{len(queue)}")

        res = asyncio.run(page.fetch_many(queue, concurrency=conc, on_result=on_result, should_stop=self.cancelled))
        self.conn.commit()
        if res.get("aborted"):
            self.stats["aborted"] = True
            self.log("YouTube начал резать запросы к страницам роликов, выгрузка деталей остановлена до следующего прогона", "warn")
        self.log(f"детали со страниц: ок {res['ok']}, удалено {res['removed']}, блок {res['blocked']}, ошибок {res['error']}")

    def recognize_pending(self, force: bool = False) -> None:
        self.phase("распознавание")
        imported = db.get_meta(self.conn, "dict_imported_at", "")
        if force:
            where = "recog_manual = 0 AND title IS NOT NULL"
        else:
            where = "recog_manual = 0 AND title IS NOT NULL AND (recog_at IS NULL OR ((title_id IS NULL OR recog_conf < 0.8) AND recog_at < ?))"
        params = () if force else (imported or "9999",)
        rows = self.conn.execute(
            f"""SELECT v.video_id, v.title, v.description, v.keywords, v.comments_text, c.default_title_id
                FROM videos v LEFT JOIN channels c ON c.channel_id = v.channel_id WHERE {where}""", params).fetchall()
        ts = now_iso()
        n_new = 0
        for i, r in enumerate(rows, 1):
            if self.cancelled():
                break
            desc = r["description"] or ""
            if r["comments_text"]:
                desc = (desc + "\nКомментарий: " + r["comments_text"])[:2500]
            m = self.rec.recognize(r["title"], desc, loads(r["keywords"], []), r["default_title_id"])
            self.conn.execute(
                """UPDATE videos SET title_id = ?, recog_conf = ?, recog_method = ?, recog_evidence = ?, recog_at = ?, recog_candidates = ?
                   WHERE video_id = ? AND recog_manual = 0""",
                (m.title_id, m.conf if m.title_id else None, m.method, m.evidence, ts, result_json(m), r["video_id"]),
            )
            if m.title_id:
                n_new += 1
            if i % 500 == 0:
                self.conn.commit()
                self.phase("распознавание", f"{i}/{len(rows)}")
        self.conn.commit()
        self.stats["recognized_checked"] += len(rows)
        self.stats["recognized_new"] += n_new
        self.log(f"распознавание: проверено {len(rows)}, опознано {n_new}")

    def promote_channels(self) -> None:
        """Каналы, у которых уже есть несколько опознанных киношортсов, берём в слежение (вкладка Shorts + RSS)."""
        self.phase("автослежение за каналами")
        min_shorts = self.setting_int("auto_channel_min_shorts")
        cap = self.setting_int("auto_channels_max")
        have = self.conn.execute("SELECT COUNT(*) FROM sources WHERE kind = 'channel'").fetchone()[0]
        room = max(0, cap - have)
        if room == 0:
            return
        rows = self.conn.execute(
            """SELECT v.channel_id, COALESCE(c.name, v.channel_name) AS name, COUNT(*) AS n, SUM(COALESCE(v.views, 0)) AS views
               FROM videos v LEFT JOIN channels c ON c.channel_id = v.channel_id
               WHERE v.channel_id IS NOT NULL AND v.title_id IS NOT NULL AND v.recog_conf >= 0.6 AND v.status = 'ok'
                 AND v.channel_id NOT IN (SELECT value FROM sources WHERE kind = 'channel')
               GROUP BY v.channel_id HAVING n >= ? ORDER BY views DESC LIMIT ?""", (min_shorts, room)).fetchall()
        ts = now_iso()
        for r in rows:
            self.conn.execute("INSERT OR IGNORE INTO sources(kind, value, label, enabled, auto, added_at) VALUES ('channel', ?, ?, 1, 1, ?)",
                              (r["channel_id"], r["name"], ts))
            self.stats["auto_channels_added"] += 1
        self.conn.commit()
        if rows:
            self.log(f"в слежение добавлено каналов: {len(rows)}")

    def comments_phase(self) -> None:
        """С ключом Data API: верхние комментарии неопознанных роликов (там часто пишут название), потом повторное распознавание."""
        api_key = (self.settings.get("youtube_api_key") or "").strip()
        if not api_key:
            return
        cap = self.setting_int("comments_per_run")
        rows = self.conn.execute("""SELECT video_id FROM videos WHERE details_at IS NOT NULL AND status = 'ok' AND title_id IS NULL AND recog_manual = 0
                                    AND comments_text IS NULL AND lang IN ('ru','uk','kk') ORDER BY COALESCE(views,0) DESC LIMIT ?""", (cap,)).fetchall()
        if not rows:
            return
        self.phase("комментарии YouTube", f"0/{len(rows)}")
        n = 0
        for i, r in enumerate(rows, 1):
            if self.cancelled():
                break
            try:
                texts = dataapi.fetch_top_comments(api_key, r["video_id"])
            except Exception as e:  # noqa: BLE001
                self.log(f"комментарии: {e}", "warn")
                break
            self.conn.execute("UPDATE videos SET comments_text = ?, recog_at = NULL WHERE video_id = ?", (" | ".join(texts) if texts else "", r["video_id"]))
            self.stats["api_quota_used"] += 1
            n += 1
            if i % 50 == 0:
                self.conn.commit()
                self.phase("комментарии YouTube", f"{i}/{len(rows)}")
        self.conn.commit()
        self.stats["comments_fetched"] += n
        self.log(f"комментарии получены для {n} роликов")

    def interest_phase(self) -> None:
        """Просмотры статей Википедии и Google Trends для произведений из топа и календаря."""
        self.phase("интерес аудитории")
        f = Filters("30", lang="ru")
        top = recommend.today(self.conn, f, limit=120)["items"]
        ids = [t["title_id"] for t in top]
        for d in schedule.view(self.conn, days=self.setting_int("schedule_days"), back=0)["days"]:
            for it in d["items"]:
                if it["title_id"] and it["title_id"] not in ids:
                    ids.append(it["title_id"])
        try:
            self.stats["interest_wiki"] += interest.update_wiki(self.conn, ids, max_titles=self.setting_int("interest_wiki_max"), log=self.log)
        except Exception as e:  # noqa: BLE001
            self.log(f"Википедия: {e}", "warn")
        try:
            meta = {t["title_id"]: t for t in top}
            titles = [{"title_id": i, "label": meta[i]["label"] if i in meta else (self.conn.execute("SELECT label_ru FROM titles WHERE id = ?", (i,)).fetchone() or [""])[0],
                       "kind": (self.conn.execute("SELECT kind FROM titles WHERE id = ?", (i,)).fetchone() or ["film"])[0]} for i in ids[:60]]
            self.stats["interest_trends"] += interest.update_trends(self.conn, titles, max_titles=self.setting_int("interest_trends_max"), log=self.log)
        except Exception as e:  # noqa: BLE001
            self.log(f"Google Trends: {e}", "warn")
        self.log(f"интерес: Википедия обновлена для {self.stats['interest_wiki']}, Google Trends для {self.stats['interest_trends']}")

    def neuro_phase(self) -> None:
        llm = llm_from_settings(self.settings)
        if not llm.configured or self.settings.get("neuro_enabled", "1") != "1":
            return
        self.phase("нейро-опознание по кадру")
        res = neuro.run_batch(self.conn, self.rec, llm, self.settings, log=self.log, should_stop=self.cancelled)
        self.stats["neuro_checked"] += res["checked"]
        self.stats["neuro_recognized"] += res["recognized"]
        self.stats["neuro_cost_usd"] = round(self.stats["neuro_cost_usd"] + res["cost_usd"], 4)
        if res["checked"]:
            self.log(f"нейро: проверено {res['checked']}, опознано {res['recognized']}, не фильм {res['not_film']}, не уверена {res['unsure']}, новых произведений {res['created']}, {res['cost_usd']:.3f} $")

    def auto_hashtags(self) -> None:
        """Хэштеги, под которыми много опознанных киношортсов, берём в источники."""
        self.phase("автодобор хэштегов")
        cap = self.setting_int("auto_hashtags_max")
        have = self.conn.execute("SELECT COUNT(*) FROM sources WHERE kind = 'hashtag' AND auto = 1").fetchone()[0]
        if have >= cap:
            return
        known = {r[0] for r in self.conn.execute("SELECT value FROM sources WHERE kind = 'hashtag'")}
        total: dict[str, int] = {}
        rec: dict[str, int] = {}
        for r in self.conn.execute("SELECT hashtags, recog_conf FROM videos WHERE details_at IS NOT NULL AND lang = 'ru' AND status = 'ok'"):
            for t in loads(r["hashtags"], [])[:10]:
                total[t] = total.get(t, 0) + 1
                if (r["recog_conf"] or 0) >= 0.6:
                    rec[t] = rec.get(t, 0) + 1
        cands = []
        for t, n in total.items():
            if t in known or n < 12 or len(t) < 4 or t.isdigit() or t in NONFILM_TAGS or not re.search(r"[а-яё]", t):
                continue
            share = rec.get(t, 0) / n
            if share >= 0.5:
                cands.append((share * n, t, n, rec.get(t, 0)))
        cands.sort(reverse=True)
        ts = now_iso()
        for _, t, n, k in cands[: cap - have]:
            self.conn.execute("INSERT OR IGNORE INTO sources(kind, value, label, enabled, auto, added_at) VALUES ('hashtag', ?, ?, 1, 1, ?)", (t, "#" + t, ts))
            self.stats["auto_hashtags_added"] += 1
            self.log(f"новый хэштег в сборе: #{t} ({k} опознанных из {n})")
        self.conn.commit()

    def prepare_files(self) -> None:
        """Прямые mp4 для сегодняшних и завтрашних слотов, где исходник качается (Rutube)."""
        self.phase("файлы исходников на сегодня и завтра")
        files.init(self.conn)
        remote = files.storage(self.conn)["remote"]
        v = schedule.view(self.conn, days=2, back=0)
        n = 0
        for d in v["days"]:
            for it in d["items"]:
                if not it["title_id"] or it["status"] == "skipped":
                    continue
                cands = [c for c in (it.get("source"), it.get("source_alt")) if c and (remote or "rutube.ru" in c["url"])]
                src = cands[0] if cands else None
                if not src:
                    continue
                st = files.status(self.conn, src["video_id"])
                if st and st["status"] in ("done", "running"):
                    continue
                res = files.request(self.conn, it["title_id"], src["video_id"], src["url"])
                if res["ok"]:
                    n += 1
                    # загрузки идут по одной (блокировка внутри files), ждём завершения, чтобы не копить очередь
                    for _ in range(360):
                        time.sleep(5)
                        st = files.status(self.conn, src["video_id"])
                        if not st or st["status"] != "running":
                            break
                else:
                    self.log(f"файл для «{it['label']}»: {res['error']}", "warn")
        if n:
            self.log(f"подготовлено файлов исходников: {n}")
        self.stats["files_prepared"] = n

    def notify_phase(self) -> None:
        """Календарь на неделю вперёд и тревоги в Telegram."""
        self.phase("календарь и тревоги")
        f = Filters("30", lang="ru")
        try:
            schedule.build(self.conn, f, days=self.setting_int("schedule_days"), per_day=self.setting_int("schedule_per_day"),
                           cooldown=self.setting_int("schedule_cooldown_days"))
        except Exception as e:  # noqa: BLE001
            self.log(f"календарь не построился: {e}", "warn")
        try:
            self.prepare_schedule_titles()
        except Exception as e:  # noqa: BLE001
            self.log(f"исходники для календаря: {e}", "warn")
        try:
            self.prepare_files()
        except Exception as e:  # noqa: BLE001
            self.log(f"файлы исходников: {e}", "warn")
        try:
            text = recommend.alerts(self.conn)
        except Exception as e:  # noqa: BLE001
            self.log(f"тревоги не посчитались: {e}", "warn")
            return
        if text and self.settings.get("alerts_enabled", "1") == "1":
            ok, err = telegram.send(self.settings.get("telegram_bot_token", ""), self.settings.get("telegram_chat_id", ""), text)
            self.conn.execute("INSERT INTO notifications(ts, kind, text, ok) VALUES (?,?,?,?)", (now_iso(), "alert", text, int(ok)))
            self.conn.commit()
            if ok:
                self.stats["alerts_sent"] += 1
            else:
                self.log(f"тревога не отправлена: {err}", "warn")

    def prepare_schedule_titles(self) -> None:
        """Для произведений в календаре на неделю: исходники на YouTube, объяснение «почему», сцены."""
        self.phase("исходники и объяснения для календаря")
        llm = llm_from_settings(self.settings)
        v = schedule.view(self.conn, days=self.setting_int("schedule_days"), back=0)
        f = Filters("30", lang="ru")
        recs = {r["title_id"]: r for r in recommend.today(self.conn, f, limit=200)["items"]}
        titles = []
        for d in v["days"]:
            for it in d["items"]:
                if it["title_id"] and it["status"] != "skipped" and it["title_id"] not in {t["title_id"] for t in titles}:
                    t = self.conn.execute("SELECT id, label_ru, label_en, year, kind FROM titles WHERE id = ?", (it["title_id"],)).fetchone()
                    if t:
                        titles.append({"title_id": t["id"], "label": t["label_ru"] or t["label_en"], "label_en": t["label_en"], "year": t["year"], "kind": t["kind"]})
        n = sources.ensure_for_titles(self.conn, llm if llm.configured else None, titles, max_titles=32, log=self.log)
        self.stats["sources_found_for"] = n
        if not llm.configured:
            return
        from .stats import film_detail
        for t in titles[:32]:
            if self.cancelled():
                break
            item = recs.get(t["title_id"])
            if item and not neuro.get_explain(self.conn, t["title_id"]):
                try:
                    neuro.explain_title(self.conn, llm, item, item["plan"].get("refs", []), sources.get_sources(self.conn, t["title_id"]))
                except Exception as e:  # noqa: BLE001
                    self.log(f"объяснение для «{t['label']}»: {e}", "warn")
            if not neuro.get_insights(self.conn, t["title_id"]):
                d = film_detail(self.conn, Filters("all"), t["title_id"])
                if d and len(d["videos"]) >= 2:
                    try:
                        neuro.title_insights(self.conn, llm, t["title_id"], d["videos"], d["title"])
                    except Exception as e:  # noqa: BLE001
                        self.log(f"сцены для «{t['label']}»: {e}", "warn")

    # ---------- прогон ----------
    def run(self) -> dict:
        t0 = time.time()
        with self.state.lock:
            if self.state.running:
                raise RuntimeError("прогон уже идёт")
            self.state.running = True
            self.state.cancel.clear()
            self.state.trigger = self.trigger
            self.state.started_at = now_iso()
            self.state.log_tail.clear()
            self.state.stats = {}
        cur = self.conn.execute("INSERT INTO runs(started_at, status, trigger) VALUES (?, 'running', ?)", (self.state.started_at, self.trigger))
        self.run_id = cur.lastrowid
        self.conn.commit()
        with self.state.lock:
            self.state.run_id = self.run_id
        status = "ok"
        error = None
        try:
            self.log(f"старт прогона #{self.run_id} ({self.trigger}); словарь: {self.rec.size} произведений")
            self.conn.execute("PRAGMA busy_timeout=180000")
            self.collect_hashtags()
            if not self.cancelled():
                self.collect_channels()
            if not self.cancelled():
                self.collect_searches()
            if not self.cancelled():
                self.collect_details()
            if not self.cancelled():
                self.comments_phase()
            if not self.cancelled():
                self.recognize_pending()
            if not self.cancelled():
                self.neuro_phase()
            if not self.cancelled():
                self.promote_channels()
            if not self.cancelled():
                self.auto_hashtags()
            if not self.cancelled():
                self.title_hashtags()
            if not self.cancelled():
                self.interest_phase()
            if not self.cancelled():
                self.notify_phase()
            if self.cancelled():
                status = "cancelled"
                self.log("прогон остановлен оператором", "warn")
        except Exception as e:  # noqa: BLE001
            status = "error"
            error = f"{e}\n{traceback.format_exc()[-1500:]}"
            self.log(f"ошибка прогона: {e}", "error")
        finally:
            self.stats["duration_s"] = round(time.time() - t0)
            try:
                self._write("UPDATE runs SET finished_at = ?, status = ?, stats = ?, error = ? WHERE id = ?",
                            (now_iso(), status, dumps(self.stats), error, self.run_id))
            except sqlite3.OperationalError as e:
                error = (error or "") + f"\nне удалось записать итог: {e}"
            self.log(f"финиш: {status}, {self.stats['duration_s']} с; новых роликов {self.stats['new_videos']}, деталей {self.stats['details_ok']}, опознано {self.stats['recognized_new']}")
            with self.state.lock:
                self.state.running = False
                self.state.phase = "готово" if status == "ok" else status
                self.state.progress = ""
                self.state.stats = dict(self.stats)
        return {"run_id": self.run_id, "status": status, "stats": self.stats}
