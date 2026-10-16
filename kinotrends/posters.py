"""Постеры произведений: Wikidata P18 → Wikimedia Commons, кэш в var/posters."""
from __future__ import annotations

import sqlite3
import urllib.parse

import httpx

from .config import VAR, WIKIDATA_UA

POSTER_DIR = VAR / "posters"
POSTER_DIR.mkdir(parents=True, exist_ok=True)


def poster_file(title_id: str):
    for ext in ("jpg", "png", "webp"):
        p = POSTER_DIR / f"{title_id}.{ext}"
        if p.exists():
            return p
    return None


def fetch_poster(conn: sqlite3.Connection, title_id: str, timeout: float = 20.0):
    """Возвращает путь к файлу постера или None. «Нет постера» запоминается, чтобы не ходить повторно."""
    p = poster_file(title_id)
    if p:
        return p
    row = conn.execute("SELECT poster, kp_id FROM titles WHERE id = ?", (title_id,)).fetchone()
    if not row or row["poster"] == "none":
        return None
    # 1) статика Кинопоиска по kp_id (редирект на avatars.mds.yandex.net), доступна из РФ
    if row["kp_id"] and str(row["kp_id"]).isdigit():
        try:
            with httpx.Client(timeout=timeout, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0"}, follow_redirects=True) as c:
                r = c.get(f"https://st.kp.yandex.net/images/film_iphone/iphone360_{row['kp_id']}.jpg")
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("image") and len(r.content) > 2000:
                    path = POSTER_DIR / f"{title_id}.jpg"
                    path.write_bytes(r.content)
                    conn.execute("UPDATE titles SET poster = ? WHERE id = ?", (f"kp:{row['kp_id']}", title_id))
                    conn.commit()
                    return path
        except Exception:  # noqa: BLE001
            pass
    if not title_id.startswith("Q"):
        conn.execute("UPDATE titles SET poster = 'none' WHERE id = ?", (title_id,))
        conn.commit()
        return None
    filename = row["poster"] if row["poster"] and not str(row["poster"]).startswith("kp:") else None
    try:
        with httpx.Client(timeout=timeout, headers={"User-Agent": WIKIDATA_UA}, follow_redirects=True) as c:
            if not filename:
                r = c.get("https://www.wikidata.org/w/api.php", params={"action": "wbgetclaims", "entity": title_id, "property": "P18", "format": "json"})
                claims = (r.json().get("claims") or {}).get("P18") or []
                filename = None
                for cl in claims:
                    v = cl.get("mainsnak", {}).get("datavalue", {}).get("value")
                    if isinstance(v, str):
                        filename = v
                        break
                if not filename:
                    conn.execute("UPDATE titles SET poster = 'none' WHERE id = ?", (title_id,))
                    conn.commit()
                    return None
                conn.execute("UPDATE titles SET poster = ? WHERE id = ?", (filename, title_id))
                conn.commit()
            url = "https://commons.wikimedia.org/wiki/Special:FilePath/" + urllib.parse.quote(filename.replace(" ", "_")) + "?width=360"
            r = c.get(url)
            if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image"):
                return None
            ctype = r.headers["content-type"]
            ext = "png" if "png" in ctype else "webp" if "webp" in ctype else "jpg"
            path = POSTER_DIR / f"{title_id}.{ext}"
            path.write_bytes(r.content)
            return path
    except Exception:  # noqa: BLE001
        return None
