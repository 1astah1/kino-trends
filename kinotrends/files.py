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
        "cookies": (s.get("storage_cookies") or "").strip(),
    }


def ytdlp_extra(st: dict) -> str:
    """Общие флаги yt-dlp для хранилища: прокси и cookies, если заданы."""
    parts = []
    if st.get("proxy"):
        parts.append(f"--proxy {shlex.quote(st['proxy'])}")
    if st.get("cookies"):
        parts.append(f"--cookies {shlex.quote(st['cookies'])}")
    return (" ".join(parts) + " ") if parts else ""


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
        st = storage(conn)
        if st["remote"]:
            # сервис перезапускался: yt-dlp на хранилище живёт отдельно, просто снова ждём его итог
            with _watch_lock:
                if not _running.get(video_id):
                    _running[video_id] = True
                    threading.Thread(target=_watch_remote, args=(video_id, f"{st['dir']}/{_safe(video_id)}.mp4", st),
                                     name=f"dl-{video_id}", daemon=True).start()
        else:
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


_watch_lock = threading.Lock()


def _short_error(log: str) -> str:
    low = log.lower()
    if ("available in your country" in low or "not available in the country" in low or ("geo" in low and "restricted" in low)
            or "use a vpn or a proxy" in low):
        return "видео недоступно из страны хранилища (гео-блок автора)"
    if "sign in to confirm" in low or "not a bot" in low or "cookies-from-browser" in low or "pass cookies" in low:
        return "YouTube требует вход (бот-проверка): нужны cookies или прокси хранилища"
    if "private video" in low:
        return "видео приватное"
    if "video unavailable" in low or "has been removed" in low:
        return "видео удалено или недоступно"
    if "requested format is not available" in low:
        return "нет подходящего формата (yt-dlp)"
    lines = [ln.strip() for ln in log.strip().splitlines() if ln.strip()]
    err = [ln for ln in lines if ln.startswith("ERROR")]
    text = err[-1] if err else (lines[-1] if lines else "ошибка yt-dlp на хранилище")
    return text[:300]


def _watch_remote(video_id: str, remote_path: str, st: dict, conn=None, hold: bool = False, timeout: int = 5400) -> None:
    """Ждёт маркеры .ok/.fail загрузки на хранилище. Работает и после перезапуска сервиса."""
    from . import db

    q = shlex.quote
    own = conn is None
    if own:
        conn = db.connect()
    try:
        if not hold:
            _running[video_id] = True
        probe = (f"if [ -f {q(remote_path + '.ok')} ]; then echo OK; cat {q(remote_path + '.ok')}; "
                 f"elif [ -f {q(remote_path + '.fail')} ]; then echo FAIL; "
                 f"(grep -E '^ERROR' {q(remote_path + '.log')} | cut -c1-600 | tail -n 3; tail -c 300 {q(remote_path + '.log')}) 2>/dev/null; "
                 f"elif pgrep -f {q('[y]t-dlp.*' + remote_path)} >/dev/null; then echo RUN; else echo GONE; fi")
        started = time.monotonic()
        while True:
            try:
                res = _ssh(st, probe, timeout=60)
                out = res.stdout.strip()
            except subprocess.TimeoutExpired:
                out = ""
            head = out.split("\n", 1)[0] if out else ""
            rest = out.split("\n", 1)[1] if "\n" in out else ""
            if head == "OK" and rest.strip().isdigit():
                _write(conn, "UPDATE downloads SET status = 'done', path = ?, size = ?, finished_at = ? WHERE video_id = ?",
                       (f"remote:{remote_path}", int(rest.strip()), now_iso(), video_id))
                return
            if head == "FAIL":
                _write(conn, "UPDATE downloads SET status = 'error', error = ?, finished_at = ? WHERE video_id = ?", (_short_error(rest), now_iso(), video_id))
                return
            if head == "GONE":
                # процесса нет, маркеров нет: сразу после запуска это бывает, даём минуту
                if time.monotonic() - started > 90:
                    _write(conn, "UPDATE downloads SET status = 'error', error = 'загрузка оборвалась', finished_at = ? WHERE video_id = ?", (now_iso(), video_id))
                    return
            if time.monotonic() - started > timeout:
                _ssh(st, f"pkill -f {q('[y]t-dlp.*' + remote_path)} || true", timeout=30)
                _write(conn, "UPDATE downloads SET status = 'error', error = 'таймаут загрузки', finished_at = ? WHERE video_id = ?", (now_iso(), video_id))
                return
            time.sleep(10)
    finally:
        if own:
            conn.close()
            _running.pop(video_id, None)


def _download(video_id: str, url: str, st: dict) -> None:
    from . import db

    with _lock:
        _running[video_id] = True
        conn = db.connect()
        try:
            name = f"{_safe(video_id)}.mp4"
            if st["remote"]:
                remote_path = f"{st['dir']}/{name}"
                q = shlex.quote
                proxy = ytdlp_extra(st)
                # yt-dlp запускается отдельной сессией (setsid): перезапуск kino-trends или обрыв ssh его не убивает.
                # Итог пишется в маркеры <файл>.ok (размер) или <файл>.fail, лог в <файл>.log — их читает _watch_remote.
                inner = (f"rm -f {q(remote_path + '.ok')} {q(remote_path + '.fail')}; "
                         f"if yt-dlp --no-warnings --js-runtimes node {proxy}-f {q(FORMAT)} --merge-output-format mp4 --no-playlist "
                         f"-o {q(remote_path)} {q(url)} > {q(remote_path + '.log')} 2>&1; then chown www-data:www-data {q(remote_path)}; "
                         f"stat -c %s {q(remote_path)} > {q(remote_path + '.ok')}; else echo fail > {q(remote_path + '.fail')}; fi")
                launch = f"setsid nohup bash -c {q(inner)} >/dev/null 2>&1 < /dev/null & echo started"
                try:
                    res = _ssh(st, launch, timeout=60)
                except subprocess.TimeoutExpired:
                    _write(conn, "UPDATE downloads SET status = 'error', error = 'хранилище не отвечает', finished_at = ? WHERE video_id = ?", (now_iso(), video_id))
                    return
                if res.returncode != 0 or "started" not in res.stdout:
                    err = (res.stderr or res.stdout or "не удалось запустить yt-dlp на хранилище").strip()[-300:]
                    _write(conn, "UPDATE downloads SET status = 'error', error = ?, finished_at = ? WHERE video_id = ?", (err, now_iso(), video_id))
                    return
                _watch_remote(video_id, remote_path, st, conn=conn, hold=True)
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
