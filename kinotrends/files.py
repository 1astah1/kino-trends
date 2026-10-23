"""Прямые ссылки на файлы исходников для нарезчика.

Два режима:
  удалённый — задан storage_host: загрузка идёт по SSH на сервер-хранилище (там yt-dlp, ffmpeg, nginx), файл раздаётся
              с его адреса (storage_url). С отдельного IP качаются и YouTube, и Rutube.
  локальный — storage_host пуст: качаем на этот сервер в var/files (YouTube с датацентрового IP не качается).
Ограничения: 720p экономным битрейтом, потолок места и срок жизни файлов, одновременно одна загрузка.
"""
from __future__ import annotations

import os
import shlex
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import ROOT, VAR
from .db import now_iso

FILES_DIR = VAR / "files"
FILES_DIR.mkdir(parents=True, exist_ok=True)
MAX_TOTAL_GB = 8.0
MIN_FREE_GB = 5.0
TTL_HOURS = 48
FORMAT = "bv*[height<=720][tbr<=2000]+ba/b[height<=720][tbr<=2000]/b[height<=480]/bv*[height<=720]+ba/b"
_lock = threading.Lock()
_running: dict[str, bool] = {}

SCHEMA = """
CREATE TABLE IF NOT EXISTS downloads(
  id INTEGER PRIMARY KEY,
  title_id TEXT,
  video_id TEXT NOT NULL,
  url TEXT NOT NULL,
  status TEXT NOT NULL,
  path TEXT,
  size INTEGER,
  error TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT,
  UNIQUE(video_id)
);
"""


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# ---------- настройки хранилища ----------
def storage(conn: sqlite3.Connection) -> dict:
    from .db import all_settings
    s = all_settings(conn)
    host = (s.get("storage_host") or "").strip()
    return {
        "remote": bool(host), "host": host, "user": (s.get("storage_user") or "root").strip(),
        "key": os.path.expanduser((s.get("storage_key") or "~/.ssh/kt_storage").strip()),
        "dir": (s.get("storage_dir") or "/srv/files").strip().rstrip("/"),
        "url": (s.get("storage_url") or "").strip().rstrip("/"),
        "max_gb": float(s.get("storage_max_gb") or 20), "ttl_hours": int(s.get("storage_ttl_hours") or 72),
        "proxy": (s.get("storage_proxy") or "").strip(),
    }


def _ssh(st: dict, command: str, timeout: int = 60) -> subprocess.CompletedProcess:
    cmd = ["ssh", "-i", st["key"], "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", f"ConnectTimeout=20",
           f"{st['user']}@{st['host']}", command]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _safe(video_id: str) -> str:
    return "".join(ch for ch in video_id if ch.isalnum() or ch in "_-")


def _write(conn: sqlite3.Connection, sql: str, params: tuple, attempts: int = 10) -> None:
    for i in range(attempts):
        try:
            conn.execute(sql, params)
            conn.commit()
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or i == attempts - 1:
                raise
            time.sleep(15)


def free_gb() -> float:
    return shutil.disk_usage(str(VAR)).free / 1e9


def total_gb(conn: sqlite3.Connection | None = None) -> float:
    if conn is not None:
        st = storage(conn)
        if st["remote"]:
            r = conn.execute("SELECT COALESCE(SUM(size), 0) FROM downloads WHERE status = 'done'").fetchone()
            return (r[0] or 0) / 1e9
    return sum(p.stat().st_size for p in FILES_DIR.glob("*") if p.is_file()) / 1e9


def remote_free_gb(st: dict) -> float | None:
    try:
        r = _ssh(st, f"df -k --output=avail {shlex.quote(st['dir'])} | tail -1", timeout=30)
        return int(r.stdout.strip()) / 1e6 if r.returncode == 0 and r.stdout.strip().isdigit() else None
    except Exception:  # noqa: BLE001
        return None


def file_url(conn: sqlite3.Connection, row: sqlite3.Row | dict) -> str | None:
    if row["status"] != "done" or not row["path"]:
        return None
    name = Path(row["path"]).name
    st = storage(conn)
    if str(row["path"]).startswith("remote:"):
        return f"{st['url']}/{name}" if st["url"] else None
    return f"files/{name}"


def cleanup(conn: sqlite3.Connection) -> int:
    """Удаляет старые файлы и лишние сверх лимита (старые первыми), локально и на хранилище."""
    st = storage(conn)
    ttl = st["ttl_hours"] if st["remote"] else TTL_HOURS
    max_gb = st["max_gb"] if st["remote"] else MAX_TOTAL_GB
    removed = 0
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=ttl)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute("SELECT id, path, finished_at, size FROM downloads WHERE status = 'done' ORDER BY finished_at").fetchall()
    total = sum((r["size"] or 0) for r in rows) / 1e9
    for r in rows:
        old = (r["finished_at"] or "") < cutoff
        if old or total > max_gb:
            _remove_file(st, r["path"])
            total -= (r["size"] or 0) / 1e9
            conn.execute("UPDATE downloads SET status = 'expired', path = NULL WHERE id = ?", (r["id"],))
            removed += 1
    for p in FILES_DIR.glob("*.part"):
        if time.time() - p.stat().st_mtime > 6 * 3600:
            p.unlink(missing_ok=True)
    conn.commit()
    return removed


def _remove_file(st: dict, path: str | None) -> None:
    if not path:
        return
    if path.startswith("remote:"):
        name = Path(path).name
        try:
            _ssh(st, f"rm -f {shlex.quote(st['dir'] + '/' + name)}", timeout=30)
        except Exception:  # noqa: BLE001
            pass
    elif os.path.exists(path):
        os.remove(path)


def status(conn: sqlite3.Connection, video_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM downloads WHERE video_id = ?", (video_id,)).fetchone()
    if not r:
        return None
    if r["status"] == "running" and not _running.get(video_id):
        # поток загрузки умер, не записав итог
        out = FILES_DIR / f"{_safe(video_id)}.mp4"
        if out.exists() and out.stat().st_size > 1_000_000 and not (FILES_DIR / f"{_safe(video_id)}.mp4.part").exists():
            _write(conn, "UPDATE downloads SET status = 'done', path = ?, size = ?, finished_at = ? WHERE video_id = ?", (str(out), out.stat().st_size, now_iso(), video_id), attempts=2)
        else:
            _write(conn, "UPDATE downloads SET status = 'error', error = 'загрузка оборвалась', finished_at = ? WHERE video_id = ?", (now_iso(), video_id), attempts=2)
        r = conn.execute("SELECT * FROM downloads WHERE video_id = ?", (video_id,)).fetchone()
    d = dict(r)
    d["file_url"] = file_url(conn, r)
    d["running"] = _running.get(video_id, False)
    return d


def request(conn: sqlite3.Connection, title_id: str | None, video_id: str, url: str) -> dict:
    """Ставит загрузку. Без хранилища YouTube отклоняется сразу с объяснением."""
    st = storage(conn)
    if not st["remote"] and ("youtube.com" in url or "youtu.be" in url):
        return {"ok": False, "error": "YouTube с этого сервера не качается: вставьте ссылку YouTube в нарезчик напрямую или настройте сервер-хранилище"}
    if st["remote"] and not st["url"]:
        return {"ok": False, "error": "у хранилища не задан публичный адрес (storage_url)"}
    cur = status(conn, video_id)
    if cur and cur["status"] == "done" and cur["path"]:
        return {"ok": True, "status": cur}
    if cur and cur["status"] == "running":
        return {"ok": True, "status": cur}
    cleanup(conn)
    if st["remote"]:
        free = remote_free_gb(st)
        if free is not None and free < 3:
            return {"ok": False, "error": f"мало места на хранилище ({free:.1f} ГБ)"}
        if total_gb(conn) >= st["max_gb"]:
            return {"ok": False, "error": f"лимит хранилища {st['max_gb']:.0f} ГБ, подождите очистки"}
    else:
        if free_gb() < MIN_FREE_GB:
            return {"ok": False, "error": f"мало места на диске ({free_gb():.1f} ГБ), старые файлы уже удалены"}
        if total_gb() >= MAX_TOTAL_GB:
            return {"ok": False, "error": f"лимит хранилища {MAX_TOTAL_GB:.0f} ГБ, подождите очистки"}
    ts = now_iso()
    _write(conn, """INSERT INTO downloads(title_id, video_id, url, status, created_at) VALUES (?,?,?,'running',?)
                    ON CONFLICT(video_id) DO UPDATE SET status = 'running', error = NULL, created_at = excluded.created_at, finished_at = NULL, path = NULL, size = NULL""",
           (title_id, video_id, url, ts))
    threading.Thread(target=_download, args=(video_id, url, st), name=f"dl-{video_id}", daemon=True).start()
    return {"ok": True, "status": status(conn, video_id)}


def _download(video_id: str, url: str, st: dict) -> None:
    from . import db

    with _lock:
        _running[video_id] = True
        conn = db.connect()
        try:
            name = f"{_safe(video_id)}.mp4"
            if st["remote"]:
                remote_path = f"{st['dir']}/{name}"
                proxy = f"--proxy {shlex.quote(st['proxy'])} " if st.get("proxy") else ""
                ytdlp = (f"yt-dlp --no-warnings --quiet --js-runtimes node {proxy}-f {shlex.quote(FORMAT)} --merge-output-format mp4 --no-playlist "
                         f"-o {shlex.quote(remote_path)} {shlex.quote(url)} && chown www-data:www-data {shlex.quote(remote_path)} && stat -c %s {shlex.quote(remote_path)}")
                try:
                    res = _ssh(st, ytdlp, timeout=5400)
                except subprocess.TimeoutExpired:
                    _write(conn, "UPDATE downloads SET status = 'error', error = 'таймаут загрузки', finished_at = ? WHERE video_id = ?", (now_iso(), video_id))
                    return
                size_line = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""
                if res.returncode != 0 or not size_line.isdigit():
                    err = (res.stderr or res.stdout or "ошибка yt-dlp на хранилище").strip()[-300:]
                    _write(conn, "UPDATE downloads SET status = 'error', error = ?, finished_at = ? WHERE video_id = ?", (err, now_iso(), video_id))
                    return
                _write(conn, "UPDATE downloads SET status = 'done', path = ?, size = ?, finished_at = ? WHERE video_id = ?",
                       (f"remote:{remote_path}", int(size_line), now_iso(), video_id))
                return
            out = FILES_DIR / name
            cmd = [str(ROOT / ".venv" / "bin" / "yt-dlp"), "--no-warnings", "--quiet", "-f", FORMAT, "--merge-output-format", "mp4", "--no-playlist", "-o", str(out), url]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
            except subprocess.TimeoutExpired:
                _write(conn, "UPDATE downloads SET status = 'error', error = 'таймаут загрузки', finished_at = ? WHERE video_id = ?", (now_iso(), video_id))
                return
            if res.returncode != 0 or not out.exists():
                err = (res.stderr or res.stdout or "ошибка yt-dlp").strip()[-300:]
                _write(conn, "UPDATE downloads SET status = 'error', error = ?, finished_at = ? WHERE video_id = ?", (err, now_iso(), video_id))
                return
            _write(conn, "UPDATE downloads SET status = 'done', path = ?, size = ?, finished_at = ? WHERE video_id = ?",
                   (str(out), out.stat().st_size, now_iso(), video_id))
        finally:
            conn.close()
            _running.pop(video_id, None)
