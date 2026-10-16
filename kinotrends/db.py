"""SQLite: схема, подключение, настройки. Все идентификаторы латиницей, кириллица только в значениях."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import DB_PATH, DEFAULT_SETTINGS

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('hashtag','channel','search')),
  value TEXT NOT NULL,
  label TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  auto INTEGER NOT NULL DEFAULT 0,
  added_at TEXT NOT NULL,
  last_run_at TEXT,
  last_count INTEGER,
  last_new INTEGER,
  last_error TEXT,
  UNIQUE(kind, value)
);

CREATE TABLE IF NOT EXISTS channels(
  channel_id TEXT PRIMARY KEY,
  name TEXT,
  handle TEXT,
  followers INTEGER,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  default_title_id TEXT,
  note TEXT,
  rss_checked_at TEXT,
  shorts_checked_at TEXT
);

CREATE TABLE IF NOT EXISTS titles(
  id TEXT PRIMARY KEY,
  label_ru TEXT,
  label_en TEXT,
  original_title TEXT,
  year INTEGER,
  kind TEXT NOT NULL,
  sitelinks INTEGER NOT NULL DEFAULT 0,
  kp_id TEXT,
  imdb_id TEXT,
  genres TEXT NOT NULL DEFAULT '[]',
  countries TEXT NOT NULL DEFAULT '[]',
  franchise_id TEXT,
  franchise_label TEXT,
  is_manual INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_titles_label ON titles(label_ru);

CREATE TABLE IF NOT EXISTS title_aliases(
  title_id TEXT NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
  alias TEXT NOT NULL,
  norm TEXT NOT NULL,
  lang TEXT NOT NULL DEFAULT 'ru',
  source TEXT NOT NULL DEFAULT 'wikidata',
  PRIMARY KEY(title_id, norm)
);
CREATE INDEX IF NOT EXISTS idx_alias_norm ON title_aliases(norm);

CREATE TABLE IF NOT EXISTS videos(
  video_id TEXT PRIMARY KEY,
  title TEXT,
  description TEXT,
  channel_id TEXT,
  channel_name TEXT,
  duration_s INTEGER,
  published_at TEXT,
  category TEXT,
  keywords TEXT,
  hashtags TEXT,
  lang TEXT,
  avail_ru INTEGER,
  thumb TEXT,
  status TEXT NOT NULL DEFAULT 'ok',
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  details_at TEXT,
  details_source TEXT,
  details_fail INTEGER NOT NULL DEFAULT 0,
  views INTEGER,
  likes INTEGER,
  comments INTEGER,
  stats_at TEXT,
  title_id TEXT REFERENCES titles(id) ON DELETE SET NULL,
  recog_conf REAL,
  recog_method TEXT,
  recog_evidence TEXT,
  recog_manual INTEGER NOT NULL DEFAULT 0,
  recog_at TEXT,
  recog_candidates TEXT,
  features TEXT
);
CREATE INDEX IF NOT EXISTS idx_videos_title ON videos(title_id);
CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_videos_published ON videos(published_at);
CREATE INDEX IF NOT EXISTS idx_videos_details ON videos(details_at);

CREATE TABLE IF NOT EXISTS video_stats(
  video_id TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
  ts TEXT NOT NULL,
  views INTEGER,
  likes INTEGER,
  comments INTEGER,
  src TEXT NOT NULL,
  PRIMARY KEY(video_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_stats_ts ON video_stats(ts);

CREATE TABLE IF NOT EXISTS video_sources(
  video_id TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
  source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  last_rank INTEGER,
  PRIMARY KEY(video_id, source_id)
);

CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  trigger TEXT NOT NULL,
  stats TEXT NOT NULL DEFAULT '{}',
  error TEXT
);

CREATE TABLE IF NOT EXISTS run_log(
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  ts TEXT NOT NULL,
  level TEXT NOT NULL,
  msg TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runlog_run ON run_log(run_id);

CREATE TABLE IF NOT EXISTS alias_bans(
  norm TEXT PRIMARY KEY,
  note TEXT,
  added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_usage(
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  purpose TEXT NOT NULL,
  model TEXT,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  cost_usd REAL,
  ok INTEGER NOT NULL DEFAULT 1,
  note TEXT
);

CREATE TABLE IF NOT EXISTS title_insights(
  title_id TEXT PRIMARY KEY REFERENCES titles(id) ON DELETE CASCADE,
  generated_at TEXT NOT NULL,
  model TEXT,
  data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  ok INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS title_interest(
  title_id TEXT NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  day TEXT NOT NULL,
  value REAL NOT NULL,
  PRIMARY KEY(title_id, source, day)
);

CREATE TABLE IF NOT EXISTS title_sources(
  id INTEGER PRIMARY KEY,
  title_id TEXT NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
  video_id TEXT NOT NULL,
  url TEXT NOT NULL,
  label TEXT,
  channel TEXT,
  duration_s INTEGER,
  views INTEGER,
  kind TEXT,
  season INTEGER,
  episode INTEGER,
  language TEXT,
  trust REAL,
  verified INTEGER NOT NULL DEFAULT 0,
  note TEXT,
  score REAL NOT NULL DEFAULT 0,
  found_at TEXT NOT NULL,
  UNIQUE(title_id, video_id)
);

CREATE TABLE IF NOT EXISTS title_explain(
  title_id TEXT PRIMARY KEY REFERENCES titles(id) ON DELETE CASCADE,
  generated_at TEXT NOT NULL,
  model TEXT,
  text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedule(
  id INTEGER PRIMARY KEY,
  day TEXT NOT NULL,
  slot INTEGER NOT NULL,
  title_id TEXT REFERENCES titles(id) ON DELETE SET NULL,
  reason TEXT,
  plan TEXT,
  status TEXT NOT NULL DEFAULT 'planned',
  locked INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(day, slot)
);

CREATE TABLE IF NOT EXISTS meta(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

# Хэштеги, с которых начинается сбор. Оператор правит список в интерфейсе.
DEFAULT_HASHTAGS = [
    "фильм", "фильмы", "кино", "сериал", "сериалы", "моменты", "моментыизфильмов",
    "киномоменты", "нарезка", "нарезкафильмов", "кинонарезка", "отрывокизфильма",
    "лучшиемоменты", "мультфильм", "аниме", "дорама", "турецкийсериал", "советскоекино",
    "русскоекино", "боевик", "комедия", "ужасы", "фантастика", "кинопоиск",
    "русскиесериалы", "турецкиесериалы", "дорамы", "моментыизсериалов", "сериалмомент", "лучшиесериалы",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=60, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


MIGRATIONS = [
    ("videos", "neuro_at", "TEXT"),
    ("videos", "neuro_json", "TEXT"),
    ("videos", "season", "INTEGER"),
    ("videos", "episode", "INTEGER"),
    ("titles", "source", "TEXT NOT NULL DEFAULT 'wikidata'"),
    ("titles", "poster", "TEXT"),
    ("titles", "ruwiki", "TEXT"),
    ("videos", "comments_text", "TEXT"),
]


def migrate(conn: sqlite3.Connection) -> None:
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'sources'").fetchone()
    if sql and "'search'" not in sql[0]:
        conn.executescript("""
            CREATE TABLE sources_new(
              id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('hashtag','channel','search')), value TEXT NOT NULL, label TEXT,
              enabled INTEGER NOT NULL DEFAULT 1, auto INTEGER NOT NULL DEFAULT 0, added_at TEXT NOT NULL, last_run_at TEXT, last_count INTEGER,
              last_new INTEGER, last_error TEXT, UNIQUE(kind, value));
            INSERT INTO sources_new SELECT id, kind, value, label, enabled, auto, added_at, last_run_at, last_count, last_new, last_error FROM sources;
            DROP TABLE sources;
            ALTER TABLE sources_new RENAME TO sources;
        """)
    for table, col, decl in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    migrate(conn)
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    n = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    if n == 0:
        ts = now_iso()
        conn.executemany(
            "INSERT OR IGNORE INTO sources(kind, value, label, enabled, auto, added_at) VALUES ('hashtag', ?, ?, 1, 0, ?)",
            [(h, "#" + h, ts) for h in DEFAULT_HASHTAGS],
        )
    conn.commit()


def get_setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return DEFAULT_SETTINGS.get(key, "")
    return row["value"]


def get_int(conn: sqlite3.Connection, key: str) -> int:
    try:
        return int(get_setting(conn, key))
    except ValueError:
        return int(DEFAULT_SETTINGS.get(key, "0"))


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    conn.commit()


def all_settings(conn: sqlite3.Connection) -> dict[str, str]:
    out = dict(DEFAULT_SETTINGS)
    for r in conn.execute("SELECT key, value FROM settings"):
        out[r["key"]] = r["value"]
    return out


def get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def loads(s: str | None, default):
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        return default
