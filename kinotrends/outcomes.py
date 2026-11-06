"""Итоги наших публикаций: какие ролики залетают, какие нет, какие заблокировали — и обучение подбора на этом.

Раз в 30 минут (планировщик в api.py):
1. collect — берёт из auto-shorts список наших публикаций и спрашивает у YouTube (ключ Кино-трендов, 1 единица
   квоты на 50 роликов) просмотры, лайки и статус: блокировка в России (regionRestriction — так выглядит
   блокировка Content ID: «Наруто» 29.10 разрешён только в США и Канаде), отклонение, удаление;
2. learn — заблокированное произведение и его правообладатель навсегда уходят из подбора, ещё не
   опубликованные ролики этого произведения снимаются (новых страйков не будет); по просмотрам в час
   считается оценка произведения, календарь поднимает залетевшее и опускает провальное;
3. purge — уже набранное в работу, что теперь под запретом (в том числе по правилу ru_enforced: российские
   и японские правообладатели), снимается до выхода.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone

import httpx

from .db import get_setting, loads, now_iso

RUN_EVERY_MIN = 30
MIN_AGE_H_FOR_SCORE = 6  # раньше просмотры ещё не показательны
FLOP_VPH = 1.0  # меньше просмотра в час после суток — провал

# Кто блокирует нарезки в России (наши итоги на 29.10): «Сваты» (Квартал 95 / Россия 1) и «Наруто» (японцы)
# заблокировали в первый час, а Disney, Warner, Sony и Miramax у нас не блокировали ни разу. Права на российский
# прокат своих фильмов и сериалов держат телеканалы и онлайн-кинотеатры и ставят Content ID на блок, японцы
# блокируют аниме по всему миру. Такое не берём вовсе, не дожидаясь первой блокировки (exclude_ru_enforced=0 — брать).
RU_ENFORCED_COUNTRIES = {"россия", "российская федерация", "украина", "беларусь", "белоруссия", "япония"}
RU_ENFORCED_HOLDERS = {"Первый канал", "ВГТРК", "Газпром-Медиа (ТНТ, Premier)", "российский онлайн-кинотеатр или телеканал",
                       "Кинопоиск / Яндекс", "Okko", "Start", "Wink", "Амедиа", "Союзмультфильм", "СТС Медиа", "НТВ",
                       "японский правообладатель"}


def init(conn: sqlite3.Connection) -> None:
    conn.executescript("""
CREATE TABLE IF NOT EXISTS pub_outcomes(
  video_id TEXT PRIMARY KEY, publication_id INTEGER, job_id INTEGER, channel_id TEXT, channel_title TEXT,
  title_id TEXT, title_label TEXT, published_at TEXT, views INTEGER, likes INTEGER, comments INTEGER, vph REAL,
  age_h REAL, status TEXT, block TEXT, block_reason TEXT, checked_at TEXT);
CREATE TABLE IF NOT EXISTS title_bans(
  title_id TEXT PRIMARY KEY, label TEXT, reason TEXT, source TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS holder_bans(
  holder TEXT PRIMARY KEY, reason TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS pub_views(
  video_id TEXT NOT NULL, ts TEXT NOT NULL, age_h REAL, views INTEGER, likes INTEGER, PRIMARY KEY(video_id, ts));
""")
    have = {r[1] for r in conn.execute("PRAGMA table_info(pub_outcomes)")}
    for col, typ in (("avg_pct", "REAL"), ("engaged", "INTEGER"), ("r3s", "REAL")):
        if col not in have:
            conn.execute(f"ALTER TABLE pub_outcomes ADD COLUMN {col} {typ}")
    conn.commit()


def _age_h(published_at: str | None) -> float:
    if not published_at:
        return 0.0
    try:
        t = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - t).total_seconds() / 3600)


def classify(item: dict | None) -> tuple[str | None, str | None]:
    """(block, reason) по карточке videos.list. None в карточке — ролика нет (удалён или закрыт)."""
    if item is None:
        return "removed", "ролика нет на YouTube: удалён или закрыт"
    st = item.get("status") or {}
    if st.get("uploadStatus") == "rejected":
        return "rejected", f"YouTube отклонил: {st.get('rejectionReason') or 'без причины'}"
    rr = (item.get("contentDetails") or {}).get("regionRestriction") or {}
    allowed, blocked = rr.get("allowed"), rr.get("blocked") or []
    if allowed is not None and "RU" not in allowed:
        where = ", ".join(allowed[:6]) or "нигде"
        return "blocked_ru", f"заблокирован в России по авторскому праву (разрешён только: {where})"
    if "RU" in blocked:
        return "blocked_ru", "заблокирован в России по авторскому праву"
    return None, None


def _title_of_job(conn: sqlite3.Connection) -> dict[int, tuple[str, str]]:
    """Ролик auto-shorts → произведение по заданиям конвейера."""
    out: dict[int, tuple[str, str]] = {}
    for r in conn.execute("SELECT ref_id, label, as_jobs FROM pipeline_items WHERE kind = 'film' AND as_jobs IS NOT NULL"):
        for jid in loads(r["as_jobs"], []) or []:
            try:
                out[int(jid)] = (r["ref_id"], r["label"])
            except (TypeError, ValueError):
                continue
    return out


def _title_by_label(conn: sqlite3.Connection, label: str) -> str | None:
    r = conn.execute("SELECT id FROM titles WHERE lower(label_ru) = lower(?) LIMIT 1", (label.strip(),)).fetchone()
    return r["id"] if r else None


def collect(conn: sqlite3.Connection, settings: dict, asc, log=print) -> dict:
    """Наши публикации из auto-shorts + статус и просмотры с YouTube → pub_outcomes."""
    from .yt.dataapi import VIDEOS_URL
    init(conn)
    key = (settings.get("youtube_api_key") or "").strip()
    if not key:
        return {"skipped": "нет ключа YouTube Data API"}
    try:  # досмотры из YouTube Analytics (auto-shorts сам держит паузу 3 ч на канал)
        asc.post("/api/v1/publications/analytics/collect", {})
    except Exception as e:  # noqa: BLE001 - без досмотров итоги всё равно считаются
        log(f"итоги: досмотры не собраны: {e}")
    pubs = [p for p in (asc.get("/api/v1/publications", limit=200, sort="-published_at").get("items") or [])
            if p.get("platform") == "youtube" and p.get("platform_video_id")]
    if not pubs:
        return {"publications": 0}
    by_job = _title_of_job(conn)
    cards: dict[str, dict] = {}
    with httpx.Client(timeout=30) as cl:
        ids = [p["platform_video_id"] for p in pubs]
        for i in range(0, len(ids), 50):
            r = cl.get(VIDEOS_URL, params={"part": "snippet,contentDetails,statistics,status", "id": ",".join(ids[i:i + 50]), "key": key})
            r.raise_for_status()
            for it in r.json().get("items", []):
                cards[it["id"]] = it
    ts = now_iso()
    n_block = 0
    for p in pubs:
        vid = p["platform_video_id"]
        card = cards.get(vid)
        block, reason = classify(card)
        st = (card or {}).get("statistics") or {}
        views = int(st["viewCount"]) if st.get("viewCount") else (0 if card else None)
        age = _age_h(p.get("published_at"))
        tid, label = by_job.get(int(p.get("job_id") or 0), (None, None))
        if tid is None:
            try:
                j = asc.get(f"/api/v1/jobs/{int(p['job_id'])}")
                label = ((j.get("source") or {}).get("title") or j.get("topic") or "").strip() or None
                tid = _title_by_label(conn, label) if label else None
            except Exception:  # noqa: BLE001 - без названия запись всё равно полезна
                pass
        ch = p.get("channel") or {}
        conn.execute("""INSERT INTO pub_outcomes(video_id, publication_id, job_id, channel_id, channel_title, title_id, title_label, published_at,
                        views, likes, comments, vph, age_h, status, block, block_reason, checked_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(video_id) DO UPDATE SET views = excluded.views, likes = excluded.likes, comments = excluded.comments,
                        vph = excluded.vph, age_h = excluded.age_h, status = excluded.status, block = excluded.block,
                        block_reason = excluded.block_reason, checked_at = excluded.checked_at,
                        title_id = COALESCE(excluded.title_id, pub_outcomes.title_id), title_label = COALESCE(excluded.title_label, pub_outcomes.title_label)""",
                     (vid, p.get("id"), p.get("job_id"), ch.get("id"), ch.get("title"), tid, label, p.get("published_at"),
                      views, int(st["likeCount"]) if st.get("likeCount") else None, int(st["commentCount"]) if st.get("commentCount") else None,
                      round((views or 0) / max(1.0, age), 2), round(age, 1), p.get("status"), block, reason, ts))
        lt = p.get("latest") or {}
        if lt.get("avg_pct_viewed") is not None or lt.get("engaged_views") is not None or lt.get("retention_3s") is not None:
            conn.execute("UPDATE pub_outcomes SET avg_pct = ?, engaged = ?, r3s = ? WHERE video_id = ?",
                         (lt.get("avg_pct_viewed"), lt.get("engaged_views"), lt.get("retention_3s"), vid))
        n_block += 1 if block else 0
        if views is not None and age <= 24 * 14:  # кривая первых двух недель: как быстро ролик набирает
            conn.execute("INSERT OR IGNORE INTO pub_views(video_id, ts, age_h, views, likes) VALUES (?,?,?,?,?)",
                         (vid, ts, round(age, 2), views, int(st["likeCount"]) if st.get("likeCount") else None))
    conn.commit()
    return {"publications": len(pubs), "blocked": n_block}


def learn(conn: sqlite3.Connection, settings: dict, asc, log=print) -> dict:
    """Блокировка → запрет произведения и правообладателя, снять неопубликованные ролики этого произведения."""
    from . import rights
    init(conn)
    out = {"banned_titles": [], "banned_holders": [], "cancelled_jobs": []}
    # явные сигналы площадки плюс «пропал», если сами мы его не снимали (статус live): 01.11 «Запретный плод» закрыли
    # везде кроме Перу, а ролики Мосфильма исчезли совсем — публичный ключ видит это только как «нет ролика»
    rows = conn.execute("SELECT DISTINCT title_id, title_label, block_reason FROM pub_outcomes "
                        "WHERE (block IN ('blocked_ru', 'rejected') OR (block = 'removed' AND status = 'live')) "
                        "AND title_id IS NOT NULL").fetchall()
    for r in rows:
        tid = r["title_id"]
        if conn.execute("SELECT 1 FROM title_bans WHERE title_id = ?", (tid,)).fetchone():
            continue
        conn.execute("INSERT INTO title_bans(title_id, label, reason, source, at) VALUES (?,?,?,?,?)",
                     (tid, r["title_label"], r["block_reason"], "публикация", now_iso()))
        out["banned_titles"].append(r["title_label"])
        holder = (rights.levels(conn, [tid]).get(tid) or {}).get("holder")
        if holder and holder != "unknown" and not conn.execute("SELECT 1 FROM holder_bans WHERE holder = ?", (holder,)).fetchone():
            conn.execute("INSERT INTO holder_bans(holder, reason, at) VALUES (?,?,?)",
                         (holder, f"заблокировали «{r['title_label']}»: {r['block_reason']}", now_iso()))
            out["banned_holders"].append(holder)
        # снять ещё не опубликованные ролики этого произведения: следующий заблокированный ролик — уже страйк каналу
        out["cancelled_jobs"] += withdraw(conn, asc, tid, f"произведение заблокировали на YouTube: {r['block_reason']}")
    conn.commit()
    for t in out["banned_titles"]:
        log(f"итоги: «{t}» заблокировали — убрано из подбора, неопубликованные ролики сняты")
    return out


def withdraw(conn: sqlite3.Connection, asc, title_id: str, why: str) -> list[int]:
    """Снять неопубликованные ролики произведения: убрать из плана выкладки и отменить задания в auto-shorts."""
    cancelled: list[int] = []
    # и у «published»: у пересобранного произведения часть клипов уже вышла, а остальные ещё стоят в плане (01.11 — «Сверхъестественное»)
    for it in conn.execute("SELECT id, as_jobs, status FROM pipeline_items WHERE kind = 'film' AND ref_id = ? AND status != 'cancelled'",
                           (title_id,)).fetchall():
        for jid in loads(it["as_jobs"], []) or []:
            try:
                j = asc.get(f"/api/v1/jobs/{int(jid)}")
            except Exception:  # noqa: BLE001
                continue
            if j.get("status") in ("published", "cancelled", "rejected"):
                continue
            try:
                pkg = (asc.package_of_job(int(jid)) or {}).get("id")
            except Exception:  # noqa: BLE001 - пакета ещё нет
                pkg = None
            if pkg:
                try:
                    asc._req("DELETE", f"/api/v1/packages/{int(pkg)}/autopublish")
                except Exception:  # noqa: BLE001
                    pass
            try:
                asc.post(f"/api/v1/jobs/{int(jid)}/cancel", {})
                cancelled.append(int(jid))
            except Exception:  # noqa: BLE001
                pass
        if it["status"] != "published":
            conn.execute("UPDATE pipeline_items SET status = 'cancelled', last_error = ?, updated_at = ? WHERE id = ?", (why, now_iso(), it["id"]))
    return cancelled


def purge(conn: sqlite3.Connection, asc, log=print) -> dict:
    """Уже набранное в работу, что теперь под запретом (новое правило, запрет правообладателя): снять до выхода."""
    rows = conn.execute("SELECT DISTINCT ref_id, label FROM pipeline_items WHERE kind = 'film' AND status NOT IN ('published', 'cancelled', 'rejected', 'failed')").fetchall()
    labels = {r["ref_id"]: r["label"] for r in rows}
    banned = banned_title_ids(conn, list(labels))
    why = ru_enforced(conn, list(banned))
    out = {"titles": [], "cancelled_jobs": []}
    for tid in banned:
        out["cancelled_jobs"] += withdraw(conn, asc, tid, f"снято до выхода: {why.get(tid) or 'правообладатель в запрете'}")
        out["titles"].append(labels[tid])
        log(f"итоги: «{labels[tid]}» снято до выхода — {why.get(tid) or 'правообладатель в запрете'}")
    conn.commit()
    return out


def ru_enforced(conn: sqlite3.Connection, ids) -> dict[str, str]:
    """Произведения, которые в России блокируют по Content ID: id → причина словами."""
    from . import rights
    ids = [i for i in dict.fromkeys(ids) if i]
    out: dict[str, str] = {}
    lv = rights.levels(conn, ids)
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for r in conn.execute(f"SELECT id, kind, countries FROM titles WHERE id IN ({','.join('?' * len(part))})", part):
            holder = (lv.get(r["id"]) or {}).get("holder") or ""
            group = (rights.match_holder(holder) or ("", ""))[0]
            countries = {str(x).strip().lower() for x in (loads(r["countries"], []) or [])}
            if group in RU_ENFORCED_HOLDERS:
                out[r["id"]] = f"{holder}: блокирует нарезки в России"
            elif r["kind"] == "anime" or "япония" in countries:
                out[r["id"]] = "японское: блокируют по всему миру (как «Наруто»)"
            elif countries & RU_ENFORCED_COUNTRIES:
                out[r["id"]] = "российское или украинское: права у телеканала или онлайн-кинотеатра, блокируют в России (как «Сваты»)"
    return out


def banned_title_ids(conn: sqlite3.Connection, ids) -> set[str]:
    """Произведения, которые нельзя ставить: заблокированные у нас, всё от правообладателя (и его группы:
    «Россия 1» → ВГТРК), который у нас блокировал, и всё, что в России блокируют (ru_enforced).
    Строгий режим (exclude_block_holders=1) — ещё и всё с политикой «блокирует и страйкует»."""
    from . import rights
    init(conn)
    ids = [i for i in dict.fromkeys(ids) if i]
    banned = {r[0] for r in conn.execute("SELECT title_id FROM title_bans")}
    holders = {r[0].lower() for r in conn.execute("SELECT holder FROM holder_bans")}
    groups = {g[0] for g in (rights.match_holder(h) for h in holders) if g}
    out = {i for i in ids if i in banned}
    # оценка «блокирует» стоит почти у всех крупных студий, а «Гриффины» (Disney) и «Джентльмены» у нас не
    # заблокировали — по умолчанию решают реальные итоги и проба; включить строгий режим: exclude_block_holders=1
    strict = (get_setting(conn, "exclude_block_holders") or "0") == "1"
    for tid, lv in rights.levels(conn, ids).items():
        holder = lv.get("holder") or ""
        group = (rights.match_holder(holder) or ("", ""))[0]
        if (holder and holder.lower() in holders) or (group and group in groups) or (strict and lv.get("holder_policy") == "block"):
            out.add(tid)
    if (get_setting(conn, "exclude_ru_enforced") or "1") != "0":
        out |= set(ru_enforced(conn, ids))
    return out


def title_scores(conn: sqlite3.Connection) -> dict[str, dict]:
    """Оценка произведения по нашим роликам: средние просмотры в час (ролики старше 6 ч), число провалов."""
    init(conn)
    out: dict[str, dict] = {}
    for r in conn.execute("""SELECT title_id, COUNT(*) n, AVG(vph) vph, SUM(CASE WHEN vph < ? AND age_h >= 24 THEN 1 ELSE 0 END) flops
                             FROM pub_outcomes WHERE title_id IS NOT NULL AND block IS NULL AND age_h >= ? GROUP BY title_id""",
                          (FLOP_VPH, MIN_AGE_H_FOR_SCORE)):
        out[r["title_id"]] = {"n": r["n"], "vph": round(r["vph"] or 0, 2), "flops": r["flops"] or 0}
    return out


def score_bonus(stats: dict | None) -> float:
    """Поправка к баллу календаря: залетевшее выше (логарифм просмотров в час), провальное ниже."""
    if not stats:
        return 0.0
    bonus = 15 * math.log10(1 + float(stats.get("vph") or 0))
    if stats.get("n") and stats.get("flops", 0) >= stats["n"]:
        bonus -= 25  # все ролики произведения провалились
    return round(bonus, 2)


# Поколения формата роликов: без YouTube Analytics (владелец 29.10: «давай без него») сравниваем по открытым
# цифрам. Ролик старше 6 ч с ≤ 5 просмотрами — YouTube его не показывал; показы есть, а лайков < 1 на 100
# просмотров — смотрят, но листают. format_epochs (JSON [{since, label}]) перекрывает.
FORMAT_EPOCHS = [
    {"since": "2000-01-01T00:00:00Z", "label": "шаблон: окно, субтитры, без голоса"},
    {"since": "2026-10-29T18:30:00Z", "label": "викторина + голос Ash + вставка BPN"},
]
SHOWN_MIN_VIEWS = 5
MATURE_H = 6


def formats_report(conn: sqlite3.Connection) -> list[dict]:
    """Сравнение поколений формата: доля непоказанных, просмотры в час, лайки на 100 просмотров."""
    epochs = sorted(loads(get_setting(conn, "format_epochs"), None) or FORMAT_EPOCHS, key=lambda e: e["since"])
    rows = [dict(r) for r in conn.execute("SELECT published_at, views, likes, vph, age_h, block, channel_title FROM pub_outcomes "
                                          "WHERE published_at IS NOT NULL AND COALESCE(block, '') NOT IN ('removed')")]
    out = []
    for n, ep in enumerate(epochs):
        until = epochs[n + 1]["since"] if n + 1 < len(epochs) else "9999"
        mine = [r for r in rows if ep["since"] <= _iso(r["published_at"]) < until]
        mature = [r for r in mine if (r["age_h"] or 0) >= MATURE_H and not r["block"]]
        views = sum(int(r["views"] or 0) for r in mature)
        likes = sum(int(r["likes"] or 0) for r in mature)
        out.append({
            "label": ep["label"], "since": ep["since"], "published": len(mine), "mature": len(mature),
            "not_shown": sum(1 for r in mature if int(r["views"] or 0) <= SHOWN_MIN_VIEWS),
            "vph_avg": round(sum(float(r["vph"] or 0) for r in mature) / len(mature), 1) if mature else None,
            "likes_per_100": round(100 * likes / views, 1) if views else None,
            "blocked": sum(1 for r in mine if r["block"] in ("blocked_ru", "rejected")),
        })
    return out


def _iso(ts: str | None) -> str:
    """published_at из auto-shorts бывает с часовым поясом — к UTC «…Z» для сравнения строк."""
    if not ts:
        return ""
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return str(ts)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Ворота распространения (сбор правил 31.10.2026, auto-shorts docs/SHORTS_RULES.md §2): YouTube с 01.10.2026 оценивает
# канал ЦЕЛИКОМ («primarily aggregating … will likely see less distribution»). Нули подряд на разных произведениях —
# признак ограничения канала, а не провала формата; новые нарезки туда только закрепляют ярлык агрегатора.
GATE_MATURE_H = 48
GATE_LAST_N = 8


def channel_health(conn: sqlite3.Connection, settings: dict | None = None) -> dict[str, dict]:
    """Состояние каналов по последним зрелым (≥48 ч) роликам без блокировок: suppressed — медиана просмотров ниже
    порога на ≥min_samples роликах из ≥min_titles разных произведений; healthy — медиана ≥10× порога; иначе unproven."""
    init(conn)
    st = settings or {}
    min_n = int(st.get("pipeline_gate_min_samples") or 4)
    min_titles = int(st.get("pipeline_gate_min_titles") or 2)
    floor = float(st.get("pipeline_gate_median_views") or 30)
    # смена формата канала (pipeline_gate_since, ISO UTC) — старые ролики другого формата не в счёт: канал снова «новый»
    since = str(st.get("pipeline_gate_since") or "")
    rows = conn.execute("SELECT channel_id, title_id, title_label, video_id, views, published_at FROM pub_outcomes "
                        "WHERE channel_id IS NOT NULL AND block IS NULL AND views IS NOT NULL AND published_at >= ? "
                        "ORDER BY published_at DESC", (since,)).fetchall()
    by: dict[str, list] = {}
    for r in rows:
        if _age_h(r["published_at"]) >= GATE_MATURE_H and len(by.setdefault(r["channel_id"], [])) < GATE_LAST_N:
            by[r["channel_id"]].append(r)
    out: dict[str, dict] = {}
    for ch, rs in by.items():
        views = sorted(int(r["views"] or 0) for r in rs)
        med = float(views[len(views) // 2]) if len(views) % 2 else (views[len(views) // 2 - 1] + views[len(views) // 2]) / 2
        titles = len({r["title_id"] or r["title_label"] or r["video_id"] for r in rs})
        if len(rs) >= min_n and titles >= min_titles and med < floor:
            status = "suppressed"
        elif len(rs) >= 2 and med >= floor * 10:
            status = "healthy"
        else:
            status = "unproven"
        out[ch] = {"status": status, "median_views": med, "samples": len(rs), "titles": titles}
    return out


def report(conn: sqlite3.Connection) -> dict:
    """Для вкладки «Итоги»: публикации по каналам, рейтинг произведений, запреты."""
    init(conn)
    pubs = [dict(r) for r in conn.execute("SELECT * FROM pub_outcomes ORDER BY published_at DESC LIMIT 300")]
    titles = [dict(r) for r in conn.execute("""SELECT title_id, MAX(title_label) label, COUNT(*) n, SUM(COALESCE(views,0)) views,
                                                ROUND(AVG(vph), 2) vph, SUM(CASE WHEN block IS NOT NULL THEN 1 ELSE 0 END) blocked
                                                FROM pub_outcomes GROUP BY COALESCE(title_id, title_label) ORDER BY vph DESC""")]
    bans = [dict(r) for r in conn.execute("SELECT * FROM title_bans ORDER BY at DESC")]
    holders = [dict(r) for r in conn.execute("SELECT * FROM holder_bans ORDER BY at DESC")]
    return {"publications": pubs, "titles": titles, "title_bans": bans, "holder_bans": holders, "formats": formats_report(conn),
            "channels": channel_health(conn),
            "checked_at": max((p["checked_at"] for p in pubs if p.get("checked_at")), default=None)}


def run(conn: sqlite3.Connection, settings: dict, asc=None, log=print) -> dict:
    from .pipeline import client_from_settings
    asc = asc or client_from_settings(settings)
    res = collect(conn, settings, asc, log)
    res["learn"] = learn(conn, settings, asc, log)
    res["purge"] = purge(conn, asc, log)
    conn.execute("INSERT INTO meta(key, value) VALUES ('outcomes_at', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (now_iso(),))
    conn.commit()
    return res
