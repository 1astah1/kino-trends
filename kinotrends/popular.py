"""Популярные видео СНГ для перезалива с нашим баннером.

Источник: YouTube Data API v3. Чарт «самое популярное» по странам СНГ (videos.list chart=mostPopular, 1 единица квоты
за страницу из 50), для России ещё по категориям; раз в сутки поиск роликов с лицензией Creative Commons (search.list,
100 единиц за запрос). У каждого ролика: замеры просмотров между прогонами (скорость и прирост), язык, лицензия,
флаг «контент заявлен партнёром» (Content ID), подписчики канала.

Анализ: (1) модель по тексту: тема, формат, почему зашло, хук, интеграции, перезалив ли это сам по себе, пригодность
под чужой баннер; (2) кадры: на сервере-хранилище ffmpeg вынимает 6 кадров без скачивания всего видео и собирает лист,
модель со зрением ищет водяные знаки, логотипы, плашки, призывы подписаться, рекламу, рамки и свободные зоны под баннер.
Итог: уровень clean | check | no | unknown, балл 0–100, причины. Чистые с высоким баллом попадают в подборку сами;
подборку с прямыми ссылками на mp4 отдаёт GET /api/videos/picks.
"""
from __future__ import annotations

import asyncio
import shlex
import sqlite3
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx

from . import files, rights
from .config import VAR
from .db import dumps, get_meta, loads, now_iso, set_meta
from .llm import LLM, LLMError, record_usage
from .text import detect_lang
from .yt import dataapi, page

FRAMES_DIR = VAR / "frames"
FRAMES_DIR.mkdir(parents=True, exist_ok=True)

LEVELS = ("clean", "check", "no", "unknown")
LEVEL_RU = {"clean": "чистое", "check": "проверить", "no": "не брать", "unknown": "не проверено"}
REGION_RU = {"RU": "Россия", "KZ": "Казахстан", "BY": "Беларусь", "AZ": "Азербайджан", "AM": "Армения", "MD": "Молдова", "GE": "Грузия",
             "UA": "Украина", "UZ": "Узбекистан", "KG": "Киргизия", "TJ": "Таджикистан"}
FORMATS = ("шортс", "влог", "юмор", "игра", "музыка", "новости", "обзор", "животные", "спорт", "кино-клип", "образование", "реакция", "компиляция", "другое")
CLEAN_AT, CHECK_AT = 70, 45

SYSTEM_TEXT = (
    "Ты редактор канала коротких видео для русскоязычной аудитории: канал перезаливает чужие популярные ролики со своим баннером "
    "и продвигает им свой продукт. Тебе дан профиль нашей аудитории: оцени, насколько зрители ролика совпадают с ней. "
    "Оценивай только по данным, ничего не выдумывай. Отвечай только JSON."
)
DEFAULT_AUDIENCE = "аудитория VPN-сервиса в России и СНГ: нужен YouTube без замедления, Discord, Instagram, Roblox, зарубежные игры и сервисы"
CATEGORY_FIT = {"20": 8, "28": 8, "24": 3, "23": 3, "17": 3, "19": 3, "26": 2, "27": 2, "15": 2, "2": 2}  # категории, где живёт наша аудитория
TOPIC_FIT = ("video game", "technology", "anime", "esports", "software", "gadget")
SYSTEM_FRAMES = (
    "Ты проверяешь кадры видео перед перезаливом. Найди всё чужое: водяные знаки, логотипы каналов и телеканалов, плашки и надписи, "
    "призывы подписаться, рекламу, QR и ссылки, рамки. Отвечай только JSON, без догадок: отмечай только то, что видно."
)


# ---------- настройки ----------
def cfg(settings: dict) -> dict:
    def ival(k: str, d: int) -> int:
        try:
            return int(settings.get(k) or d)
        except (TypeError, ValueError):
            return d
    lst = lambda k: [x.strip() for x in (settings.get(k) or "").split(",") if x.strip()]  # noqa: E731
    return {"regions": [x.upper() for x in lst("pop_regions")], "pages": ival("pop_pages", 4), "categories": lst("pop_categories"),
            "cc_queries": lst("pop_cc_queries"), "cc_hours": ival("pop_cc_hours", 24), "lang": (settings.get("pop_lang") or "ru").strip().lower(),
            "llm_max": ival("pop_llm_max", 60), "frames_max": ival("pop_frames_max", 25), "max_duration": ival("pop_max_duration", 3600),
            "auto_pick": str(settings.get("pop_auto_pick", "1")) == "1", "pick_min": ival("pop_pick_min_score", 70), "min_views": ival("pop_min_views", 50000),
            "audience": (settings.get("pop_audience") or DEFAULT_AUDIENCE).strip()}


# ---------- запись ----------
def upsert(conn: sqlite3.Connection, items: list[dict], origin: str, ts: str, region: str | None = None, cat: str | None = None) -> tuple[int, int]:
    """Сохраняет карточки из API, сливает ранги по странам и категориям, пишет замер просмотров."""
    new = 0
    for d in items:
        row = conn.execute("SELECT regions, cats FROM pop_videos WHERE video_id = ?", (d["video_id"],)).fetchone()
        regions = loads(row["regions"], {}) if row else {}
        cats = loads(row["cats"], {}) if row else {}
        if region and cat:
            cats[str(cat)] = d.get("rank")
            regions.setdefault(region, None)
        elif region:
            regions[region] = d.get("rank")
        lang = d.get("lang_hint") or detect_lang(d.get("title"), d.get("description"))
        vals = (d.get("title"), d.get("description"), d.get("channel_id"), d.get("channel_name"), d.get("duration_s"), d.get("published_at"),
                d.get("category_id"), d.get("category"), dumps(d.get("tags") or []), d.get("views"), d.get("likes"), d.get("comments"), ts,
                d.get("license"), d.get("licensed_content"), d.get("embeddable"), d.get("made_for_kids"), d.get("caption"), d.get("definition"),
                dumps(d.get("topics") or []), d.get("thumb"), d.get("blocked_ru"), d.get("lang_hint"), lang, dumps(regions), dumps(cats))
        if row:
            conn.execute("""UPDATE pop_videos SET title = ?, description = ?, channel_id = ?, channel_name = ?, duration_s = ?, published_at = ?, category_id = ?, category = ?,
                            tags = ?, views = ?, likes = ?, comments = ?, stats_at = ?, license = ?, licensed_content = ?, embeddable = ?, made_for_kids = ?, caption = ?,
                            definition = ?, topics = ?, thumb = ?, blocked_ru = ?, lang_hint = ?, lang = ?, regions = ?, cats = ?, last_seen_at = ?, status = 'ok'
                            WHERE video_id = ?""", vals + (ts, d["video_id"]))
        else:
            conn.execute("""INSERT INTO pop_videos(title, description, channel_id, channel_name, duration_s, published_at, category_id, category, tags, views, likes, comments,
                            stats_at, license, licensed_content, embeddable, made_for_kids, caption, definition, topics, thumb, blocked_ru, lang_hint, lang, regions, cats,
                            last_seen_at, video_id, origin, first_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         vals + (ts, d["video_id"], origin, ts))
            new += 1
        if d.get("views") is not None:
            conn.execute("INSERT OR REPLACE INTO pop_video_stats(video_id, ts, views, likes, comments) VALUES (?,?,?,?,?)",
                         (d["video_id"], ts, d["views"], d.get("likes"), d.get("comments")))
    conn.commit()
    return new, len(items)


def derive(conn: sqlite3.Connection, ts: str) -> int:
    """Скорость и прирост по замерам, признак шортса. Для роликов, увиденных в этом прогоне."""
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    n = 0
    for r in conn.execute("SELECT video_id, views, published_at, duration_s FROM pop_videos WHERE last_seen_at = ?", (ts,)).fetchall():
        vpd = None
        if r["views"] is not None and r["published_at"]:
            try:
                age = (now - datetime.strptime(r["published_at"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)).total_seconds() / 86400
                vpd = r["views"] / max(0.25, age)
            except ValueError:
                vpd = None
        growth = None
        snaps = conn.execute("SELECT ts, views FROM pop_video_stats WHERE video_id = ? AND ts >= ? AND views IS NOT NULL ORDER BY ts", (r["video_id"], since)).fetchall()
        if len(snaps) >= 2:
            t0 = datetime.strptime(snaps[0]["ts"][:19], "%Y-%m-%dT%H:%M:%S")
            t1 = datetime.strptime(snaps[-1]["ts"][:19], "%Y-%m-%dT%H:%M:%S")
            span = (t1 - t0).total_seconds() / 86400
            if span >= 0.08:
                growth = max(0.0, (snaps[-1]["views"] - snaps[0]["views"]) / span)
        d = r["duration_s"]
        is_short = 1 if d and d <= 60 else (0 if d and d > 180 else None)
        conn.execute("UPDATE pop_videos SET vpd = ?, growth = ?, is_short = ? WHERE video_id = ?", (round(vpd) if vpd is not None else None, round(growth) if growth is not None else None, is_short, r["video_id"]))
        n += 1
    conn.commit()
    return n


def detect_shorts(conn: sqlite3.Connection, c: dict, budget: int = 200, should_stop=None) -> dict:
    """Ролики до 3 минут без признака: страница youtube.com/shorts/<id> либо остаётся на /shorts/ (шортс), либо уходит на /watch."""
    w, p = _eligible_where(c)
    ids = [r[0] for r in conn.execute(f"SELECT video_id FROM pop_videos WHERE {w} AND is_short IS NULL AND COALESCE(duration_s, 0) BETWEEN 1 AND 180 ORDER BY COALESCE(growth, vpd, 0) DESC LIMIT ?", p + [budget])]
    if not ids:
        return {"checked": 0, "shorts": 0}
    found = {"shorts": 0}

    def on_result(d):
        if d.status in ("ok", "not_short") and d.is_short is not None:
            conn.execute("UPDATE pop_videos SET is_short = ? WHERE video_id = ?", (1 if d.is_short else 0, d.video_id))
            found["shorts"] += 1 if d.is_short else 0
        elif d.status == "removed":
            conn.execute("UPDATE pop_videos SET status = 'removed', pick = 0 WHERE video_id = ?", (d.video_id,))
    res = asyncio.run(page.fetch_many(ids, concurrency=4, on_result=on_result, should_stop=should_stop))
    conn.commit()
    return {"checked": len(ids), "shorts": found["shorts"], "aborted": res.get("aborted", False)}


# ---------- нейросеть: текст ----------
def _row(conn: sqlite3.Connection, video_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM pop_videos WHERE video_id = ?", (video_id,)).fetchone()
    return _hydrate(dict(r)) if r else None


def _hydrate(d: dict) -> dict:
    for k, default in (("tags", []), ("topics", []), ("regions", {}), ("cats", {}), ("analysis", None), ("frames", None), ("reasons", [])):
        d[k] = loads(d.get(k), default)
    d["url"] = f"https://www.youtube.com/watch?v={d['video_id']}"
    if isinstance(d.get("frames"), dict) and d["frames"].get("free_zones"):
        d["frames"]["free_zones"] = [_zone_ru(z) for z in d["frames"]["free_zones"]]
    d["level_ru"] = LEVEL_RU.get(d.get("level") or "unknown", d.get("level"))
    d["frames_available"] = (FRAMES_DIR / f"{files._safe(d['video_id'])}.jpg").exists()
    return d


def analyze_text(conn: sqlite3.Connection, llm: LLM, v: dict, audience: str = DEFAULT_AUDIENCE) -> dict:
    subs = f"{v['channel_subs']:,}".replace(",", " ") if v.get("channel_subs") else "неизвестно сколько"
    user = (
        f"Наша аудитория: {audience}.\n"
        f"Ролик: {v.get('title') or ''}\nКанал: {v.get('channel_name') or ''} ({subs} подписчиков)\n"
        f"Категория: {v.get('category') or '—'}; длительность {v.get('duration_s') or '?'} с; просмотров {v.get('views') or '?'}, лайков {v.get('likes') or '?'}, "
        f"комментариев {v.get('comments') or '?'}; опубликован {(v.get('published_at') or '')[:10]}; язык {v.get('lang') or '?'}\n"
        f"Теги: {', '.join((v.get('tags') or [])[:15]) or '—'}\nОписание: {(v.get('description') or '')[:800] or '—'}\n"
        'Ответь JSON: {"topic": "тема, 2–4 слова", "niche": "ниша или интерес аудитории", '
        '"format": "один из: шортс|влог|юмор|игра|музыка|новости|обзор|животные|спорт|кино-клип|образование|реакция|компиляция|другое", '
        '"why_popular": "1–2 предложения, почему зашло", "hook": "что цепляет в первые секунды", "audience": "кто смотрит", '
        '"is_reupload": false, "has_sponsor": false, "music_heavy": false, "film_title": "", "reuse_fit": 0.0, "banner_advice": "одна фраза", '
        '"audience_fit": 0.0, "audience_why": "одна фраза"}.\n'
        "is_reupload: это уже чужой перезалив или компиляция чужих кадров. has_sponsor: интеграция, промокод, спонсор в ролике или описании. "
        "music_heavy: музыка или клип составляют основу ролика. film_title: если это кадры из фильма или сериала, его название. "
        "reuse_fit от 0 до 1: насколько ролик самодостаточен и понятен без автора и канала, годится ли для перезалива с чужим баннером. "
        "banner_advice: где и когда ставить наш баннер. audience_fit от 0 до 1: насколько зрители этого ролика совпадают с нашей аудиторией "
        "(кому нужен VPN: доступ к YouTube, Discord, Instagram, Roblox, зарубежным играм и сервисам), audience_why: почему."
    )
    data, usage = llm.ask_json(SYSTEM_TEXT, user, max_tokens=500)
    record_usage(conn, "popular_text", llm.model, usage, ok=True, note=v["video_id"])
    fmt = str(data.get("format") or "другое").strip().lower()
    ans = {"topic": str(data.get("topic") or "")[:80], "niche": str(data.get("niche") or "")[:80], "format": fmt if fmt in FORMATS else "другое",
           "why_popular": str(data.get("why_popular") or "")[:400], "hook": str(data.get("hook") or "")[:200], "audience": str(data.get("audience") or "")[:120],
           "is_reupload": bool(data.get("is_reupload")), "has_sponsor": bool(data.get("has_sponsor")), "music_heavy": bool(data.get("music_heavy")),
           "film_title": str(data.get("film_title") or "")[:120], "reuse_fit": max(0.0, min(1.0, float(data.get("reuse_fit") or 0))),
           "banner_advice": str(data.get("banner_advice") or "")[:200], "model": llm.model,
           "audience_fit": max(0.0, min(1.0, float(data.get("audience_fit") or 0))), "audience_why": str(data.get("audience_why") or "")[:200]}
    conn.execute("UPDATE pop_videos SET analysis = ?, analysis_at = ? WHERE video_id = ?", (dumps(ans), now_iso(), v["video_id"]))
    conn.commit()
    return ans


# ---------- кадры ----------
def frames_remote(st: dict, video_id: str, duration_s: int | None) -> dict:
    """На хранилище: прямой URL потока через yt-dlp, 6 кадров ffmpeg по времени, лист 3×2; файл забираем по HTTP и кладём в var/frames.
    Возвращает {"image": bytes|None, "error": str, "width": int|None, "height": int|None} (размер одного кадра, по нему ориентация)."""
    vid = files._safe(video_id)
    dur = max(6, int(duration_s or 60))
    d = shlex.quote(st["dir"])
    url = shlex.quote(f"https://www.youtube.com/watch?v={video_id}")
    script = ("set -e; id=" + vid + "; D=" + str(dur) + "; mkdir -p " + d + "/frames; "
              "u=$(yt-dlp --no-warnings -q --js-runtimes node -f 'bv*[height<=480][ext=mp4]/b[height<=480]/bv*[height<=720]/b' -g " + url + " | head -1); "
              "test -n \"$u\"; for i in 1 2 3 4 5 6; do t=$((D*i/7)); ffmpeg -loglevel error -y -ss $t -i \"$u\" -frames:v 1 -q:v 3 -vf scale=480:-2 /tmp/fr_${id}_$i.jpg; done; "
              "ffmpeg -loglevel error -y -start_number 1 -i /tmp/fr_${id}_%d.jpg -filter_complex 'tile=3x2:padding=4:margin=4' -q:v 4 " + d + "/frames/$id.jpg; "
              "chown www-data:www-data " + d + "/frames/$id.jpg; "
              "ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 /tmp/fr_${id}_1.jpg | sed 's/^/DIM /'; "
              "rm -f /tmp/fr_${id}_*.jpg; echo FRAMES_OK")
    out: dict = {"image": None, "error": "", "width": None, "height": None}
    try:
        res = files._ssh(st, script, timeout=180)
    except subprocess.TimeoutExpired:
        return dict(out, error="таймаут на хранилище")
    if res.returncode != 0 or "FRAMES_OK" not in res.stdout:
        return dict(out, error=(res.stderr or res.stdout or "ошибка ffmpeg").strip()[-240:])
    for line in res.stdout.splitlines():
        if line.startswith("DIM "):
            try:
                w, h = line[4:].strip().split(",")[:2]
                out["width"], out["height"] = int(w), int(h)
            except ValueError:
                pass
    try:
        with httpx.Client(timeout=30) as c:
            r = c.get(f"{st['url']}/frames/{vid}.jpg")
    except httpx.HTTPError as e:
        return dict(out, error=f"лист кадров не скачался: {e}")
    if r.status_code != 200 or len(r.content) < 5000:
        return dict(out, error=f"лист кадров не отдался: HTTP {r.status_code}")
    (FRAMES_DIR / f"{vid}.jpg").write_bytes(r.content)
    out["image"] = r.content
    return out


_ZONES = {"top": "верх", "bottom": "низ", "left": "лево", "right": "право", "center": "центр", "top left": "левый верх", "top right": "правый верх",
          "bottom left": "левый низ", "bottom right": "правый низ", "left bottom": "левый низ", "right bottom": "правый низ", "left top": "левый верх", "right top": "правый верх"}


def _zone_ru(z) -> str:
    s = str(z).strip()[:40]
    return _ZONES.get(s.lower().replace("-", " ").replace("_", " "), s)


def analyze_frames(conn: sqlite3.Connection, llm: LLM, v: dict, image: bytes, width: int | None = None, height: int | None = None) -> dict:
    user = (
        f"Ролик «{v.get('title') or ''}» ({v.get('duration_s') or '?'} с, канал {v.get('channel_name') or ''}). "
        "На картинке 6 кадров 3×2: слева направо, сверху вниз, равномерно по длительности.\n"
        'Ответь JSON: {"watermark": false, "watermark_where": "", "text_overlay": false, "subtitles": false, "subscribe_cta": false, "banner": false, '
        '"borders": false, "orientation": "vertical|horizontal|square", "faces": false, "screen_recording": false, "quality": "ок|низкое", '
        '"free_zones": [], "clean": false, "note": "одна фраза"}.\n'
        "watermark: постоянный логотип канала, телеканала или водяной знак (где именно в watermark_where). text_overlay: наложенные надписи и плашки, кроме субтитров. "
        "subtitles: вшитые субтитры. subscribe_cta: призыв подписаться, лайк, колокольчик, стрелки. banner: реклама, промокод, QR, ссылка, чужой баннер. "
        "borders: чёрные или размытые поля, рамка. free_zones: где можно поставить наш баннер, не закрывая важное (верх, низ, левый верхний угол и т.п.). "
        "clean: чужого брендинга и плашек нет."
    )
    data, usage = llm.ask_json(SYSTEM_FRAMES, user, image=image, max_tokens=400)
    record_usage(conn, "popular_frames", llm.model, usage, ok=True, note=v["video_id"])
    orient = str(data.get("orientation") or "").lower()
    if width and height:  # размер кадра надёжнее мнения модели
        orient = "vertical" if height > width * 1.1 else "horizontal" if width > height * 1.1 else "square"
    ans = {k: bool(data.get(k)) for k in ("watermark", "text_overlay", "subtitles", "subscribe_cta", "banner", "borders", "faces", "screen_recording", "clean")}
    ans.update({"watermark_where": str(data.get("watermark_where") or "")[:80], "orientation": orient if orient in ("vertical", "horizontal", "square") else "",
                "quality": str(data.get("quality") or "")[:20], "free_zones": [_zone_ru(z) for z in (data.get("free_zones") or []) if z][:6],
                "note": str(data.get("note") or "")[:200], "model": llm.model, "width": width, "height": height})
    conn.execute("UPDATE pop_videos SET frames = ?, frames_at = ? WHERE video_id = ?", (dumps(ans), now_iso(), v["video_id"]))
    conn.commit()
    return ans


# ---------- оценка ----------
def assess(v: dict, lang_ok: str = "ru") -> dict:
    """Чистый расчёт уровня и балла по карточке, анализу текста и кадров."""
    reasons: list[str] = []
    score = 50
    hard_no = False
    if v.get("license") == "creativeCommon":
        score += 25
        reasons.append("лицензия Creative Commons: перезалив разрешён с указанием автора")
    if v.get("licensed_content"):
        score -= 40
        hard_no = True
        reasons.append("контент заявлен партнёром YouTube: перезалив получит заявку Content ID или блок")
    if v.get("made_for_kids"):
        score -= 15
        reasons.append("помечено как детское: ограничения YouTube на такие ролики")
    holder = rights.match_holder(v.get("channel_name") or "")
    if holder:
        score -= 35
        hard_no = True
        reasons.append(f"канал правообладателя: {holder[0]}")
    cat = str(v.get("category_id") or "")
    if cat == "10":
        score -= 25
        reasons.append("музыка: лейблы жалуются первыми")
    elif cat == "25":
        score -= 10
        reasons.append("новости: кадры агентств и телеканалов под Content ID")
    elif cat == "1":
        score -= 10
        reasons.append("кино и анимация: материал чаще всего чужой")
    elif cat in CATEGORY_FIT:
        score += CATEGORY_FIT[cat]
    if any(t in " ".join(v.get("topics") or []).lower() for t in TOPIC_FIT):
        score += 3
    if v.get("blocked_ru"):
        score -= 10
        reasons.append("ролик закрыт для России")
    if lang_ok and lang_ok != "all" and (v.get("lang") or "") != lang_ok:
        score -= 15
        reasons.append(f"язык ролика: {v.get('lang') or 'неизвестен'}")
    d = v.get("duration_s") or 0
    if d and d <= 180:
        score += 5
        reasons.append("короткий: подходит под Shorts как есть")
    elif d > 1200:
        score -= 5
        reasons.append("длинный: нужна нарезка")
    a = v.get("analysis") or {}
    if a:
        if a.get("is_reupload"):
            score -= 15
            reasons.append("это уже чужой перезалив или компиляция: оригинал может пожаловаться")
        if a.get("has_sponsor"):
            score -= 10
            reasons.append("в ролике спонсорская интеграция")
        if a.get("music_heavy"):
            score -= 10
            reasons.append("музыка в основе ролика: риск заявок лейблов")
        if a.get("film_title"):
            score -= 10
            reasons.append(f"кадры из «{a['film_title']}»: права у студии")
        fit = float(a.get("reuse_fit") or 0)
        score += round((fit - 0.5) * 30)
        reasons.append(f"пригодность под чужой баннер по модели: {round(fit * 100)} %")
        if a.get("audience_fit") is not None:
            af = float(a.get("audience_fit") or 0)
            score += round((af - 0.5) * 40)
            reasons.insert(0, f"наша аудитория (VPN): совпадение {round(af * 100)} %" + (f": {a['audience_why']}" if a.get("audience_why") else ""))
    f = v.get("frames") or {}
    if f:
        if f.get("watermark"):
            score -= 30
            reasons.append("водяной знак или логотип в кадре" + (f" ({f['watermark_where']})" if f.get("watermark_where") else ""))
        if f.get("banner"):
            score -= 20
            reasons.append("чужая реклама, плашка или QR в кадре")
        if f.get("subscribe_cta"):
            score -= 15
            reasons.append("призыв подписаться в кадре")
        if f.get("text_overlay"):
            score -= 8
            reasons.append("наложенные надписи в кадре")
        if f.get("subtitles"):
            score -= 5
            reasons.append("вшитые субтитры")
        if f.get("borders"):
            score -= 5
            reasons.append("рамки или поля в кадре")
        if f.get("quality") == "низкое":
            score -= 8
            reasons.append("низкое качество картинки")
        if f.get("clean") and not any(f.get(k) for k in ("watermark", "banner", "subscribe_cta")):
            score += 10
            reasons.append("кадры чистые: чужого брендинга нет")
        if f.get("free_zones"):
            reasons.append("свободно под баннер: " + ", ".join(f["free_zones"][:3]))
    growth, vpd = v.get("growth") or 0, v.get("vpd") or 0
    if growth >= 100_000:
        score += 10
        reasons.append(f"набирает {round(growth / 1000)} тыс. просмотров в день")
    elif vpd >= 50_000:
        score += 5
        reasons.append(f"в среднем {round(vpd / 1000)} тыс. просмотров в день")
    nreg = len([k for k, r in (v.get("regions") or {}).items()])
    if nreg >= 3:
        score += 5
        reasons.append(f"в чартах {nreg} стран")
    score = max(0, min(100, score))
    if hard_no:
        auto = "no"  # заявка партнёра или канал правообладателя решают сами по себе, без разбора
    elif not a and not f:
        auto = "unknown"
    elif not f:
        auto = "check" if score >= CHECK_AT and not hard_no else "no"
    else:
        visual_bad = f.get("watermark") or f.get("banner")
        auto = "clean" if score >= CLEAN_AT and not hard_no and not visual_bad else "check" if score >= CHECK_AT and not hard_no else "no"
    level = auto
    manual = v.get("manual_level")
    if manual in ("clean", "check", "no"):
        level = manual
        reasons.insert(0, "решение оператора: " + LEVEL_RU[manual])
    return {"level": level, "auto_level": auto, "score": score, "reasons": reasons, "level_ru": LEVEL_RU[level]}


def evaluate(conn: sqlite3.Connection, video_id: str, c: dict) -> dict | None:
    v = _row(conn, video_id)
    if not v:
        return None
    a = assess(v, c["lang"])
    conn.execute("UPDATE pop_videos SET level = ?, score = ?, reasons = ?, assessed_at = ? WHERE video_id = ?",
                 (a["level"], a["score"], dumps(a["reasons"]), now_iso(), video_id))
    conn.commit()
    return a


def auto_pick(conn: sqlite3.Connection, c: dict) -> tuple[int, int]:
    """Чистые с высоким баллом в подборку; автоматически взятые, но переставшие быть чистыми, из подборки долой."""
    ts = now_iso()
    cur = conn.execute("""UPDATE pop_videos SET pick = 1, pick_auto = 1, pick_at = ? WHERE pick = 0 AND status = 'ok' AND level = 'clean' AND COALESCE(score, 0) >= ?
                          AND COALESCE(manual_level, '') != 'no'""", (ts, c["pick_min"]))
    picked = cur.rowcount
    cur = conn.execute("UPDATE pop_videos SET pick = 0, pick_auto = 0 WHERE pick = 1 AND pick_auto = 1 AND (level != 'clean' OR status != 'ok')")
    unpicked = cur.rowcount
    conn.commit()
    return picked, unpicked


# ---------- выборки ----------
SORTS = {"score": "COALESCE(v.score, 0) DESC, COALESCE(v.views, 0) DESC", "growth": "COALESCE(v.growth, v.vpd, 0) DESC", "views": "COALESCE(v.views, 0) DESC",
         "vpd": "COALESCE(v.vpd, 0) DESC", "published": "v.published_at DESC", "seen": "v.first_seen_at DESC, COALESCE(v.views, 0) DESC",
         "audience": "COALESCE(json_extract(v.analysis, '$.audience_fit'), 0) DESC, COALESCE(v.score, 0) DESC"}


def _file_info(conn: sqlite3.Connection, video_id: str) -> dict | None:
    try:
        r = conn.execute("SELECT status, path, size, error FROM downloads WHERE video_id = ?", (video_id,)).fetchone()
    except sqlite3.OperationalError:  # таблица загрузок создаётся при первом обращении к файлам
        return None
    if not r:
        return None
    return {"status": r["status"], "size": r["size"], "error": r["error"], "file_url": files.file_url(conn, r)}


def list_videos(conn: sqlite3.Connection, level: str | None = None, region: str | None = None, category: str | None = None, dur: int | None = None,
                lang: str | None = None, q: str | None = None, sort: str = "score", pick: bool | None = None, fit: float | None = None,
                short: int | None = None, limit: int = 50, offset: int = 0) -> dict:
    where = ["v.status = 'ok'"]
    params: list = []
    if short == 1:
        where.append("(v.is_short = 1 OR (v.is_short IS NULL AND v.duration_s <= 60))")
    elif short == 0:
        where.append("(v.is_short = 0 OR (v.is_short IS NULL AND v.duration_s > 180))")
    if fit is not None:
        where.append("COALESCE(json_extract(v.analysis, '$.audience_fit'), 0) >= ?")
        params.append(float(fit))
    if level in LEVELS:
        where.append("v.level = ?")
        params.append(level)
    if pick:
        where.append("v.pick = 1")
    if region:
        where.append("v.regions LIKE ?")
        params.append(f'%"{region.upper()}"%')
    if category:
        where.append("v.category_id = ?")
        params.append(str(category))
    if dur:
        where.append("v.duration_s <= ?")
        params.append(int(dur))
    if lang and lang != "all":
        where.append("v.lang = ?")
        params.append(lang)
    if q:
        where.append("(v.title LIKE ? OR v.channel_name LIKE ? OR v.description LIKE ?)")
        params += [f"%{q}%"] * 3
    w = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM pop_videos v WHERE {w}", params).fetchone()[0]
    rows = conn.execute(f"SELECT v.* FROM pop_videos v WHERE {w} ORDER BY {SORTS.get(sort, SORTS['score'])} LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
    items = []
    for r in rows:
        d = _hydrate(dict(r))
        d.pop("description", None)
        d["file"] = _file_info(conn, d["video_id"])
        items.append(d)
    levels = {r["level"]: r["n"] for r in conn.execute("SELECT level, COUNT(*) n FROM pop_videos WHERE status = 'ok' GROUP BY level")}
    picks = conn.execute("SELECT COUNT(*) FROM pop_videos WHERE pick = 1 AND status = 'ok'").fetchone()[0]
    shorts = conn.execute("SELECT COUNT(*) FROM pop_videos WHERE status = 'ok' AND (is_short = 1 OR (is_short IS NULL AND duration_s <= 60))").fetchone()[0]
    return {"items": items, "total": total, "levels": levels, "picks": picks, "shorts": shorts, "regions_ru": REGION_RU, "categories_ru": dataapi.CATEGORY_RU}


def detail(conn: sqlite3.Connection, video_id: str) -> dict | None:
    d = _row(conn, video_id)
    if not d:
        return None
    d["series"] = [dict(r) for r in conn.execute("SELECT ts, views, likes, comments FROM pop_video_stats WHERE video_id = ? ORDER BY ts", (video_id,))]
    d["file"] = _file_info(conn, video_id)
    d["holder"] = (rights.match_holder(d.get("channel_name") or "") or [None])[0]
    return d


def cutter_block(conn: sqlite3.Connection, d: dict) -> dict:
    a, f = d.get("analysis") or {}, d.get("frames") or {}
    fi = d.get("file") or _file_info(conn, d["video_id"])
    return {"video_id": d["video_id"], "title": d.get("title"), "channel": d.get("channel_name"), "source_url": d["url"], "duration_s": d.get("duration_s"),
            "lang": d.get("lang"), "license": d.get("license"), "level": d.get("level"), "score": d.get("score"), "reasons": d.get("reasons") or [],
            "direct_file_url": fi["file_url"] if fi and fi.get("file_url") else None, "file_status": fi["status"] if fi else None,
            "frames_url": f"api/videos/{d['video_id']}/frames.jpg" if d.get("frames_available") else None,
            "topic": a.get("topic"), "format": a.get("format"), "hook": a.get("hook"), "banner_advice": a.get("banner_advice"),
            "audience_fit": a.get("audience_fit"), "audience_why": a.get("audience_why"),
            "free_zones": f.get("free_zones") or [], "orientation": f.get("orientation") or None, "is_short": d.get("is_short"),
            "views": d.get("views"), "growth_per_day": d.get("growth"), "regions": d.get("regions") or {}, "picked_at": d.get("pick_at"), "pick_note": d.get("pick_note")}


def picks(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM pop_videos WHERE pick = 1 AND status = 'ok' ORDER BY COALESCE(score, 0) DESC, COALESCE(views, 0) DESC").fetchall()
    return [cutter_block(conn, _hydrate(dict(r))) for r in rows]


def set_pick(conn: sqlite3.Connection, video_id: str, pick: bool, note: str | None = None) -> dict | None:
    if not conn.execute("SELECT 1 FROM pop_videos WHERE video_id = ?", (video_id,)).fetchone():
        return None
    conn.execute("UPDATE pop_videos SET pick = ?, pick_auto = 0, pick_at = ?, pick_note = ? WHERE video_id = ?", (1 if pick else 0, now_iso() if pick else None, note, video_id))
    conn.commit()
    return detail(conn, video_id)


def set_level(conn: sqlite3.Connection, video_id: str, level: str | None, c: dict) -> dict | None:
    if level not in (None, "", "clean", "check", "no"):
        raise ValueError("level: clean|check|no или пусто")
    if not conn.execute("SELECT 1 FROM pop_videos WHERE video_id = ?", (video_id,)).fetchone():
        return None
    conn.execute("UPDATE pop_videos SET manual_level = ? WHERE video_id = ?", (level or None, video_id))
    conn.commit()
    evaluate(conn, video_id, c)
    if level == "no":
        conn.execute("UPDATE pop_videos SET pick = 0, pick_auto = 0 WHERE video_id = ?", (video_id,))
        conn.commit()
    return detail(conn, video_id)


def analyze_one(conn: sqlite3.Connection, llm: LLM | None, video_id: str, c: dict, with_frames: bool = True) -> dict | None:
    """Полный разбор одного ролика по кнопке: текст, кадры, оценка."""
    v = _row(conn, video_id)
    if not v:
        return None
    notes = []
    if llm and llm.configured:
        try:
            analyze_text(conn, llm, v, c["audience"])
        except LLMError as e:
            notes.append(f"текст: модель не ответила: {e}")
        st = files.storage(conn)
        if with_frames and st["remote"] and st["url"]:
            fr = frames_remote(st, video_id, v.get("duration_s"))
            if fr["image"]:
                try:
                    analyze_frames(conn, llm, _row(conn, video_id), fr["image"], fr["width"], fr["height"])
                except LLMError as e:
                    notes.append(f"кадры: модель не ответила: {e}")
            else:
                notes.append(f"кадры не вынулись: {fr['error']}")
        elif with_frames:
            notes.append("кадры: нужен сервер-хранилище (storage_host и storage_url)")
    else:
        notes.append("модель не настроена: только лицензия, канал и категория")
    evaluate(conn, video_id, c)
    d = detail(conn, video_id)
    d["notes"] = notes
    return d


# ---------- фаза прогона ----------
def _eligible_where(c: dict) -> tuple[str, list]:
    # Creative Commons пускаем в разбор с меньшим порогом просмотров: это главный источник чистых роликов
    w = ("status = 'ok' AND (COALESCE(views, 0) >= ? OR (license = 'creativeCommon' AND COALESCE(views, 0) >= 3000)) AND COALESCE(duration_s, 0) <= ? "
         "AND made_for_kids = 0 AND licensed_content = 0 AND COALESCE(manual_level, '') != 'no'")
    p: list = [c["min_views"], c["max_duration"]]
    if c["lang"] and c["lang"] != "all":
        w += " AND lang = ?"
        p.append(c["lang"])
    return w, p


def run_phase(conn: sqlite3.Connection, settings: dict, llm: LLM | None, log=print, should_stop=None, charts_only: bool = False) -> dict:
    key = (settings.get("youtube_api_key") or "").strip()
    out = {"quota": 0, "seen": 0, "new": 0, "cc_new": 0, "refreshed": 0, "removed": 0, "channels": 0, "text": 0, "frames": 0, "frames_failed": 0,
           "assessed": 0, "picked": 0, "unpicked": 0, "clean": 0, "check": 0, "no": 0, "shorts_checked": 0, "shorts_found": 0}
    if not key:
        log("популярные видео: нет ключа YouTube Data API (настройка youtube_api_key)", "warn")
        return out
    c = cfg(settings)
    ts = now_iso()
    stop = lambda: bool(should_stop and should_stop())  # noqa: E731
    # 1. чарты по странам и категориям
    for region in c["regions"]:
        if stop():
            break
        try:
            items, q = dataapi.most_popular(key, region, pages=c["pages"])
            out["quota"] += q
            n_new, n_seen = upsert(conn, items, "chart", ts, region=region)
            out["new"] += n_new
            out["seen"] += n_seen
        except Exception as e:  # noqa: BLE001
            log(f"чарт {region}: {e}", "warn")
    if "RU" in c["regions"]:
        for cat in c["categories"]:
            if stop():
                break
            try:
                items, q = dataapi.most_popular(key, "RU", category_id=cat, pages=1)
                out["quota"] += q
                n_new, n_seen = upsert(conn, items, "chart", ts, region="RU", cat=cat)
                out["new"] += n_new
                out["seen"] += n_seen
            except Exception as e:  # noqa: BLE001
                log(f"чарт RU/{cat}: {e}", "warn")
    # 2. Creative Commons раз в cc_hours
    last_cc = get_meta(conn, "pop_cc_at", "")
    due = (datetime.now(timezone.utc) - timedelta(hours=c["cc_hours"])).strftime("%Y-%m-%dT%H:%M:%SZ")
    if c["cc_queries"] and (not last_cc or last_cc < due) and not stop():
        after = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ids: list[str] = []
        for q_text in c["cc_queries"]:
            for dur in (None, "short"):  # общий поиск и отдельно короткие (до 4 минут): там живут шортсы
                try:
                    found, q = dataapi.search_ids(key, q_text, region="RU", published_after=after, cc=True, duration=dur, lang=c["lang"] if c["lang"] != "all" else "ru")
                    out["quota"] += q
                    ids += found
                except Exception as e:  # noqa: BLE001
                    log(f"поиск CC «{q_text}»: {e}", "warn")
        ids = [i for i in dict.fromkeys(ids) if not conn.execute("SELECT 1 FROM pop_videos WHERE video_id = ?", (i,)).fetchone()]
        if ids:
            try:
                items, q = dataapi.videos_raw(key, ids)
                out["quota"] += q
                n_new, _ = upsert(conn, items, "cc", ts)
                out["cc_new"] += n_new
            except Exception as e:  # noqa: BLE001
                log(f"карточки CC: {e}", "warn")
        set_meta(conn, "pop_cc_at", ts)
        conn.commit()
    # 3. обновить замеры у недавних роликов, выпавших из чарта
    since = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale = [r[0] for r in conn.execute("SELECT video_id FROM pop_videos WHERE status = 'ok' AND last_seen_at < ? AND first_seen_at >= ? ORDER BY COALESCE(views, 0) DESC LIMIT 300", (ts, since))]
    if stale and not stop():
        try:
            items, q = dataapi.videos_raw(key, stale)
            out["quota"] += q
            upsert(conn, items, "chart", ts)
            got = {i["video_id"] for i in items}
            gone = [v for v in stale if v not in got]
            if gone:
                conn.executemany("UPDATE pop_videos SET status = 'removed', pick = 0 WHERE video_id = ?", [(v,) for v in gone])
                conn.commit()
            out["refreshed"], out["removed"] = len(got), len(gone)
        except Exception as e:  # noqa: BLE001
            log(f"обновление замеров: {e}", "warn")
    # 4. подписчики каналов
    need = [r[0] for r in conn.execute("SELECT DISTINCT channel_id FROM pop_videos WHERE channel_subs IS NULL AND channel_id IS NOT NULL AND status = 'ok' LIMIT 500")]
    if need and not stop():
        try:
            subs, q = dataapi.channels_subs(key, need)
            out["quota"] += q
            conn.executemany("UPDATE pop_videos SET channel_subs = ? WHERE channel_id = ?", [(v["subs"] or 0, k) for k, v in subs.items()])
            conn.commit()
            out["channels"] = len(subs)
        except Exception as e:  # noqa: BLE001
            log(f"каналы: {e}", "warn")
    derive(conn, ts)
    if not stop():
        try:
            r = detect_shorts(conn, c, budget=200, should_stop=should_stop)
            out["shorts_checked"], out["shorts_found"] = r["checked"], r["shorts"]
        except Exception as e:  # noqa: BLE001
            log(f"определение шортсов: {e}", "warn")
    if charts_only:
        _finish(conn, out, ts, log)
        return out
    # 5. нейросеть по тексту
    w, p = _eligible_where(c)
    if llm and llm.configured and c["llm_max"] > 0:
        for r in conn.execute(f"SELECT video_id FROM pop_videos WHERE {w} AND analysis IS NULL ORDER BY (license = 'creativeCommon') DESC, COALESCE(growth, vpd, 0) DESC LIMIT ?", p + [c["llm_max"]]).fetchall():
            if stop():
                break
            v = _row(conn, r[0])
            try:
                analyze_text(conn, llm, v, c["audience"])
                out["text"] += 1
            except LLMError as e:
                record_usage(conn, "popular_text", llm.model, None, ok=False, note=str(e)[:200])
                log(f"разбор «{(v.get('title') or '')[:40]}»: {e}", "warn")
    # 6. кадры: только для прошедших отбор по тексту
    st = files.storage(conn)
    if llm and llm.configured and c["frames_max"] > 0 and st["remote"] and st["url"]:
        rows = conn.execute(f"""SELECT video_id FROM pop_videos WHERE {w} AND frames IS NULL AND analysis IS NOT NULL AND category_id != '10'
                                ORDER BY (license = 'creativeCommon') DESC, COALESCE(json_extract(analysis, '$.audience_fit'), 0) DESC, COALESCE(growth, vpd, 0) DESC LIMIT ?""",
                            p + [c["frames_max"] * 3]).fetchall()
        done = 0
        for r in rows:
            if done >= c["frames_max"] or stop():
                break
            v = _row(conn, r[0])
            a = v.get("analysis") or {}
            if a.get("is_reupload") or float(a.get("reuse_fit") or 0) < 0.4 or rights.match_holder(v.get("channel_name") or ""):
                continue
            fr = frames_remote(st, v["video_id"], v.get("duration_s"))
            if not fr["image"]:
                out["frames_failed"] += 1
                log(f"кадры «{(v.get('title') or '')[:40]}»: {fr['error']}", "warn")
                conn.execute("UPDATE pop_videos SET frames = ?, frames_at = ? WHERE video_id = ?", (dumps({"error": fr["error"][:200]}), now_iso(), v["video_id"]))
                conn.commit()
                done += 1
                continue
            try:
                analyze_frames(conn, llm, v, fr["image"], fr["width"], fr["height"])
                out["frames"] += 1
            except LLMError as e:
                record_usage(conn, "popular_frames", llm.model, None, ok=False, note=str(e)[:200])
                log(f"кадры «{(v.get('title') or '')[:40]}»: модель не ответила: {e}", "warn")
            done += 1
        try:
            files._ssh(st, f"find {shlex.quote(st['dir'])}/frames -type f -mtime +7 -delete", timeout=30)
        except Exception:  # noqa: BLE001
            pass
    elif llm and llm.configured and c["frames_max"] > 0:
        log("кадры не проверяются: нужен сервер-хранилище (storage_host и storage_url)", "warn")
    _finish(conn, out, ts, log, c)
    return out


def _finish(conn: sqlite3.Connection, out: dict, ts: str, log, c: dict | None = None) -> None:
    c = c or cfg({})
    month = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for r in conn.execute("SELECT video_id FROM pop_videos WHERE status = 'ok' AND last_seen_at >= ?", (month,)).fetchall():
        a = evaluate(conn, r[0], c)
        if a:
            out["assessed"] += 1
            if a["level"] in out:
                out[a["level"]] += 1
    if c["auto_pick"]:
        out["picked"], out["unpicked"] = auto_pick(conn, c)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    used = int(get_meta(conn, f"pop_quota:{day}", "0") or 0) + out["quota"]
    set_meta(conn, f"pop_quota:{day}", str(used))
    set_meta(conn, "pop_last_run", dumps({"ts": ts, **out}))
    conn.commit()
    log(f"популярные видео: в чартах {out['seen']}, новых {out['new']} (+CC {out['cc_new']}), квота {out['quota']} (за день {used}); "
        f"разбор текста {out['text']}, кадров {out['frames']} (сбоев {out['frames_failed']}); чистых {out['clean']}, проверить {out['check']}, не брать {out['no']}; "
        f"в подборку +{out['picked']} −{out['unpicked']}")


def overview(conn: sqlite3.Connection) -> dict:
    levels = {r["level"]: r["n"] for r in conn.execute("SELECT level, COUNT(*) n FROM pop_videos WHERE status = 'ok' GROUP BY level")}
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    top = [cutter_block(conn, _hydrate(dict(r))) for r in conn.execute("SELECT * FROM pop_videos WHERE pick = 1 AND status = 'ok' ORDER BY COALESCE(score, 0) DESC LIMIT 6")]
    return {"levels": levels, "total": sum(levels.values()), "picks": conn.execute("SELECT COUNT(*) FROM pop_videos WHERE pick = 1 AND status = 'ok'").fetchone()[0],
            "last_run": loads(get_meta(conn, "pop_last_run", ""), None), "quota_today": int(get_meta(conn, f"pop_quota:{day}", "0") or 0), "top_picks": top,
            "cc": conn.execute("SELECT COUNT(*) FROM pop_videos WHERE status = 'ok' AND license = 'creativeCommon'").fetchone()[0],
            "unclaimed": conn.execute("SELECT COUNT(*) FROM pop_videos WHERE status = 'ok' AND licensed_content = 0").fetchone()[0]}
