"""Проверка по авторским правам: пройдёт ли произведение в календарь и к нарезчику.

Четыре группы улик по каждому произведению:
1. Сносы. Опознанные чужие шортсы этого произведения перепроверяются: если YouTube удалил ролик по жалобе
   правообладателя (страница называет заявителя) или закрыл канал целиком, это доля сносов.
2. Правообладатель. Нейросеть называет студию или платформу и то, как она обычно поступает с нарезками
   (блокирует и страйкует, заявляет права через Content ID, терпит). Для известных правообладателей действует
   свой список, он сильнее ответа модели.
3. Исходник: выложен ли фильм официально самим правообладателем, жив ли найденный источник.
4. Наш опыт: заявки, блокировки и страйки на наших роликах (их заносит оператор или нарезчик) и ручное
   решение оператора, которое сильнее всего остального.

Итог: уровень ok | caution | block | unknown, риск 0–100, причины словами и правило длины клипа: шортс дольше
60 с с любой заявкой Content ID блокируется, поэтому для всего, кроме свободных произведений, клип не длиннее
59 с. Политика календаря (настройка rights_policy): strict пускает только ok, balanced (по умолчанию) ok,
caution и непроверенные, off ничего не отсеивает, только помечает.
"""
from __future__ import annotations

import asyncio
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx

from .db import dumps, get_setting, loads, now_iso
from .llm import LLM, LLMError, llm_from_settings, record_usage
from .sources import OFFICIAL
from .stats import KIND_RU
from .yt import page
from .yt.models import VideoDetails

LEVELS = ("ok", "caution", "block", "unknown")
LEVEL_RU = {"ok": "проходит", "caution": "с оговорками", "block": "не проходит", "unknown": "не проверено"}
POLICIES = ("block", "claim", "tolerant", "unknown")
POLICY_RU = {"block": "блокирует и страйкует", "claim": "заявляет права через Content ID, ролик остаётся", "tolerant": "терпит нарезки",
             "unknown": "поведение неизвестно"}
POLICY_CHOICES = ("strict", "balanced", "off")
POLICY_CHOICE_RU = {"strict": "в календарь только «проходит»", "balanced": "«проходит» и «с оговорками»; «не проходит» отсеивается",
                    "off": "только пометка, ничего не отсеивать"}
EVENT_KINDS = ("claim", "block", "strike", "ok", "manual")
EVENT_RU = {"claim": "заявка Content ID", "block": "ролик заблокирован", "strike": "страйк", "ok": "прошёл без претензий", "manual": "решение оператора"}
CLIP_CAP_S = 59
BLOCK_AT, CAUTION_AT = 65, 40
RULE_CAP = f"клип не длиннее {CLIP_CAP_S} с: шортс дольше 60 с с любой заявкой Content ID блокируется по всему миру"
RULE_FREE = "ограничений по длине из-за прав нет: произведение свободно"

# Правообладатели с известным поведением на YouTube: регулярка по названию → имя, политика.
HOLDERS: list[tuple[str, str, str]] = [
    (r"disney|marvel|pixar|lucasfilm|20th century|дисней|марвел|пиксар|лукасфилм", "Disney", "block"),
    (r"warner|\bhbo\b|\bdc\b|new line|уорнер|ворнер", "Warner Bros. Discovery", "block"),
    (r"universal|nbcuniversal|dreamworks|illumination|юниверсал|дримворкс", "Universal", "block"),
    (r"sony pictures|columbia|tristar|сони пикчерз|коламбия", "Sony Pictures", "block"),
    (r"paramount|nickelodeon|парамаунт|никелодеон", "Paramount", "block"),
    (r"lionsgate|лайонсгейт", "Lionsgate", "claim"),
    (r"netflix|нетфликс", "Netflix", "claim"),
    (r"amazon|\bmgm\b|амазон", "Amazon MGM", "claim"),
    (r"toho|toei|aniplex|kadokawa|shueisha|ghibli|crunchyroll|тохо|тоэй|гибли|кранчиролл|анипл", "японский правообладатель", "block"),
    (r"\bbbc\b|би-би-си", "BBC", "block"),
    (r"мосфильм|mosfilm", "Мосфильм", "claim"),
    (r"союзмультфильм|soyuzmultfilm", "Союзмультфильм", "block"),
    (r"первый канал|channel one", "Первый канал", "block"),
    (r"вгтрк|россия 1|россия-1|russia 1|russia-1", "ВГТРК", "block"),
    (r"газпром|\bтнт\b|premier|премьер", "Газпром-Медиа (ТНТ, Premier)", "block"),
    (r"кинопоиск|яндекс|плюс студия|kinopoisk|yandex", "Кинопоиск / Яндекс", "block"),
    (r"\bokko\b|окко", "Okko", "block"),
    (r"\bstart\b|\bстарт\b", "Start", "block"),
    (r"\bwink\b|винк|ростелеком", "Wink", "block"),
    (r"амедиа|amedia", "Амедиа", "block"),
    (r"централ партнершип|central partnership|\bцпш\b", "Централ Партнершип", "claim"),
    (r"star media|стар медиа", "Star Media", "claim"),
    (r"\bстс\b|\bctc\b|yellow,? black", "СТС Медиа", "claim"),
    (r"\bнтв\b|\bntv\b", "НТВ", "claim"),
    (r"ленфильм|lenfilm|горького|gorky", "Ленфильм / студия Горького", "claim"),
    (r"cj enm|\btvn\b|jtbc|\bsbs\b|\bkbs\b|\bmbc\b|studio dragon", "корейский правообладатель", "claim"),
]

SYSTEM_RIGHTS = (
    "Ты аналитик по авторским правам для канала нарезок фильмов и сериалов на YouTube Shorts (Россия и СНГ). "
    "Отвечай только JSON, коротко и по фактам. Если данных мало, ставь низкую confidence и holder_policy unknown, ничего не выдумывай."
)


def match_holder(text: str) -> tuple[str, str] | None:
    t = (text or "").lower()
    if not t:
        return None
    for rx, name, policy in HOLDERS:
        if re.search(rx, t):
            return name, policy
    return None


def classify_removed(reason: str | None) -> tuple[str, str | None]:
    """Тип удаления по тексту страницы YouTube и имя заявителя, если оно названо."""
    if not reason:
        return "none", None
    r = reason.lower()
    m = re.search(r"(?:поданн\w+\s+(?:пользователем\s+|компанией\s+)?|жалоб\w*\s+(?:от|со стороны)\s+|claim(?:ed)?\s+by\s+)[«\"']?([^»\"'\n·]{2,80})", reason, re.IGNORECASE)
    claimant = m.group(1).strip(" .") if m else None
    if "авторск" in r or "copyright" in r or "правооблад" in r:
        return "copyright", claimant
    if ("аккаунт" in r or "account" in r) and ("удал" in r or "закрыт" in r or "terminat" in r or "прекращ" in r):
        return "terminated", None
    if "частн" in r or "private" in r:
        return "private", None
    if "удалено пользователем" in r or "removed by the uploader" in r or "removed by the user" in r:
        return "user_removed", None
    return "other", None


def _days_since(iso: str | None) -> int:
    if not iso:
        return 0
    try:
        dt = datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return 0
    return max(0, (datetime.now(timezone.utc) - dt).days)


def title_info(conn: sqlite3.Connection, title_id: str) -> dict | None:
    r = conn.execute("SELECT id, COALESCE(label_ru, label_en) AS label, label_en, original_title, year, kind, countries, genres FROM titles WHERE id = ?",
                     (title_id,)).fetchone()
    if not r:
        return None
    return {"title_id": r["id"], "label": r["label"], "label_en": r["label_en"], "original_title": r["original_title"], "year": r["year"],
            "kind": r["kind"], "kind_ru": KIND_RU.get(r["kind"], r["kind"]), "countries": loads(r["countries"], []), "genres": loads(r["genres"], [])}


def signals(conn: sqlite3.Connection, title_id: str) -> dict | None:
    """Все улики по произведению из базы: сносы, события, исходник, ответ модели, ручное решение."""
    t = title_info(conn, title_id)
    if not t:
        return None
    rows = conn.execute("SELECT status, removed_reason, published_at, first_seen_at, details_at FROM videos WHERE title_id = ? AND COALESCE(recog_conf, 0) >= 0.6",
                        (title_id,)).fetchall()
    rated = sum(1 for r in rows if r["details_at"])
    kinds: Counter = Counter()
    claimants: Counter = Counter()
    for r in rows:
        if r["status"] != "removed":
            continue
        k, who = classify_removed(r["removed_reason"])
        kinds[k] += 1
        if who:
            claimants[who] += 1
    dates = [r["published_at"] or r["first_seen_at"] for r in rows if (r["published_at"] or r["first_seen_at"])]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [dict(r) for r in conn.execute("SELECT id, ts, kind, source, claimant, video_url, note FROM rights_events WHERE title_id = ? AND ts >= ? ORDER BY ts DESC LIMIT 30",
                                            (title_id, cutoff))]
    for e in events:
        e["kind_ru"] = EVENT_RU.get(e["kind"], e["kind"])
    src = conn.execute("SELECT url, channel, verified, alive FROM title_sources WHERE title_id = ? ORDER BY verified DESC, score DESC LIMIT 1", (title_id,)).fetchone()
    tr = conn.execute("SELECT llm_json, llm_at, manual_level, manual_note FROM title_rights WHERE title_id = ?", (title_id,)).fetchone()
    return {
        **t, "rated": rated, "removed_total": sum(kinds.values()), "removed_strict": kinds["copyright"] + kinds["terminated"],
        "removed_kinds": dict(kinds), "claimants": claimants.most_common(5), "age_days": _days_since(min(dates)) if dates else 0, "events": events,
        "source": {"url": src["url"], "channel": src["channel"], "official": bool(OFFICIAL.search(src["channel"] or "")),
                   "alive": None if src["alive"] is None else bool(src["alive"]), "verified": bool(src["verified"])} if src else None,
        "llm": loads(tr["llm_json"], None) if tr else None, "llm_at": tr["llm_at"] if tr else None,
        "manual_level": tr["manual_level"] if tr else None, "manual_note": tr["manual_note"] if tr else None,
    }


def _public(sig: dict) -> dict:
    keys = ("rated", "removed_total", "removed_strict", "removed_kinds", "claimants", "age_days", "events", "source", "llm", "llm_at", "manual_level", "manual_note")
    return {k: sig.get(k) for k in keys}


def assess(sig: dict) -> dict:
    """Чистый расчёт уровня и риска по уликам. Без базы, чтобы легко тестировать."""
    reasons: list[str] = []
    risk = 20
    llm = sig.get("llm") or {}
    holder = (str(llm.get("holder") or "").strip() or None)
    policy = str(llm.get("holder_policy") or "unknown").lower()
    if policy not in POLICIES:
        policy = "unknown"
    static = match_holder(holder or "")
    if static:
        policy = static[1]  # известный правообладатель: наш список надёжнее ответа модели
        if not holder:
            holder = static[0]
    elif not holder and sig.get("kind") == "anime":
        policy = "block"
        holder = "японский правообладатель"
    pd = bool(llm.get("public_domain"))
    who = holder or "правообладатель"
    if pd:
        risk -= 20
        reasons.append("произведение свободно от охраны: общественное достояние")
    elif policy == "block":
        risk += 25
        reasons.append(f"{who}: обычно блокирует нарезки и выдаёт страйки")
    elif policy == "claim":
        risk += 10
        reasons.append(f"{who}: заявляет права через Content ID, ролик остаётся, монетизация уходит ему")
    elif policy == "tolerant":
        risk -= 5
        reasons.append(f"{who}: нарезки терпит")
    else:
        risk += 5
        reasons.append("правообладатель не определён" if not holder else f"{holder}: поведение на YouTube неизвестно")
    if llm.get("music_risk"):
        risk += 5
        reasons.append("музыкальные номера: отдельные претензии от лейблов")
    if llm.get("official_free_ru") and not pd:
        risk -= 8
        reasons.append("правообладатель сам выкладывает произведение полностью и бесплатно")
    # сносы чужих нарезок
    rated, strict = int(sig.get("rated") or 0), int(sig.get("removed_strict") or 0)
    claim_txt = ", ".join(f"{n} ({k})" for n, k in (sig.get("claimants") or [])[:2])
    if strict and rated >= 5:
        share = strict / rated
        risk += 40 if share >= 0.25 else 25 if share >= 0.10 else 10
        reasons.append(f"снесено {strict} из {rated} чужих нарезок" + (f"; жалобы от: {claim_txt}" if claim_txt else ""))
    elif strict:
        risk += 15
        reasons.append(f"снесённые чужие нарезки: {strict}" + (f"; жалобы от: {claim_txt}" if claim_txt else ""))
    elif rated >= 20 and int(sig.get("age_days") or 0) >= 30:
        risk -= 10
        reasons.append(f"{rated} чужих нарезок живут больше месяца, сносов нет")
    # исходник
    src = sig.get("source") or {}
    if src.get("official"):
        risk -= 8
        reasons.append(f"исходник лежит на официальном канале ({src.get('channel') or ''})".replace(" ()", ""))
    if src.get("alive") is False:
        risk += 8
        reasons.append("найденный исходник удалён: источники по этому произведению сносят")
    # наш опыт
    ev = Counter(e.get("kind") for e in sig.get("events") or [])
    if ev["strike"]:
        risk += min(60, 45 * ev["strike"])
        reasons.insert(0, f"наши страйки по этому произведению: {ev['strike']}")
    if ev["block"]:
        risk += min(45, 30 * ev["block"])
        reasons.insert(0, f"наши ролики блокировали: {ev['block']}")
    if ev["claim"]:
        risk += min(16, 8 * ev["claim"])
        reasons.append(f"заявок Content ID на наших роликах: {ev['claim']}")
    if ev["ok"]:
        risk -= min(12, 4 * ev["ok"])
        reasons.append(f"наших роликов прошло без претензий: {ev['ok']}")
    risk = max(0, min(100, risk))
    auto = "block" if risk >= BLOCK_AT else "caution" if risk >= CAUTION_AT else "ok"
    if not llm and not sig.get("events") and not strict and not static and sig.get("kind") != "anime":
        auto = "unknown"
    level = auto
    manual = sig.get("manual_level")
    if manual in ("ok", "caution", "block"):
        level = manual
        reasons.insert(0, "решение оператора: " + LEVEL_RU[manual] + (f" ({sig['manual_note']})" if sig.get("manual_note") else ""))
    free = pd or (level == "ok" and policy == "tolerant")
    return {"level": level, "level_ru": LEVEL_RU[level], "auto_level": auto, "risk": risk, "reasons": reasons,
            "rule": RULE_FREE if free else RULE_CAP, "clip_max_s": None if free else CLIP_CAP_S,
            "holder": holder, "holder_policy": policy, "holder_policy_ru": POLICY_RU[policy], "public_domain": pd}


def allowed(level: str | None, policy: str | None) -> bool:
    policy = policy if policy in POLICY_CHOICES else "balanced"
    if policy == "off":
        return True
    if policy == "strict":
        return level == "ok"
    return level in ("ok", "caution", "unknown", None)


def evaluate(conn: sqlite3.Connection, title_id: str, save: bool = True) -> dict | None:
    """Собирает улики, считает уровень, сохраняет итог. Ответ модели и ручное решение не трогает."""
    sig = signals(conn, title_id)
    if sig is None:
        return None
    a = assess(sig)
    ts = now_iso()
    if save:
        conn.execute("""INSERT INTO title_rights(title_id, level, risk, reasons, signals, rule, clip_max_s, holder, holder_policy, checked_at) VALUES (?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(title_id) DO UPDATE SET level = excluded.level, risk = excluded.risk, reasons = excluded.reasons, signals = excluded.signals,
                        rule = excluded.rule, clip_max_s = excluded.clip_max_s, holder = excluded.holder, holder_policy = excluded.holder_policy, checked_at = excluded.checked_at""",
                     (title_id, a["level"], a["risk"], dumps(a["reasons"]), dumps(_public(sig)), a["rule"], a["clip_max_s"], a["holder"], a["holder_policy"], ts))
        conn.commit()
    return {"title_id": title_id, "label": sig["label"], **a, "signals": _public(sig), "checked_at": ts,
            "passes": allowed(a["level"], get_setting(conn, "rights_policy"))}


def levels(conn: sqlite3.Connection, ids: list[str]) -> dict[str, dict]:
    """Короткая сводка по списку произведений; у непроверенных уровень unknown."""
    out: dict[str, dict] = {}
    ids = [i for i in dict.fromkeys(ids) if i]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in conn.execute(f"SELECT title_id, level, risk, reasons, rule, clip_max_s, holder, holder_policy, checked_at FROM title_rights WHERE title_id IN ({','.join('?' * len(chunk))})", chunk):
            out[r["title_id"]] = {"level": r["level"], "level_ru": LEVEL_RU.get(r["level"], r["level"]), "risk": r["risk"], "reasons": loads(r["reasons"], []),
                                  "rule": r["rule"], "clip_max_s": r["clip_max_s"], "holder": r["holder"], "holder_policy": r["holder_policy"], "checked_at": r["checked_at"]}
    for i in ids:
        out.setdefault(i, {"level": "unknown", "level_ru": LEVEL_RU["unknown"], "risk": None, "reasons": ["авторские права ещё не проверялись: оценка появится после прогона"],
                           "rule": RULE_CAP, "clip_max_s": CLIP_CAP_S, "holder": None, "holder_policy": None, "checked_at": None})
    return out


def blocked_ids(conn: sqlite3.Connection, ids: list[str]) -> set[str]:
    policy = get_setting(conn, "rights_policy")
    lv = levels(conn, ids)
    return {tid for tid in ids if not allowed(lv.get(tid, {}).get("level"), policy)}


# ---------- нейросеть ----------
def llm_assess(conn: sqlite3.Connection, llm: LLM, title: dict) -> dict:
    """Спрашивает модель о правообладателе и его поведении, сохраняет ответ в title_rights."""
    user = (
        f"Произведение: {title['label']} ({title.get('year') or 'год неизвестен'}, {title.get('kind_ru') or title.get('kind')}"
        f"{'; оригинальное название: ' + title['label_en'] if title.get('label_en') and title['label_en'] != title['label'] else ''}"
        f"; страны: {', '.join(title.get('countries') or []) or '—'}; жанры: {', '.join((title.get('genres') or [])[:4]) or '—'}).\n"
        'Ответь JSON: {"holder": "правообладатель: студия, платформа или телеканал, и дистрибьютор в России, если есть", '
        '"holder_policy": "block|claim|tolerant|unknown: как он обычно поступает с фан-нарезками и клипами на YouTube '
        '(block = блокирует и выдаёт страйки, claim = заявляет права через Content ID, ролик остаётся, tolerant = терпит)", '
        '"public_domain": false, "official_free_ru": false, "music_risk": false, "confidence": 0.0, "note": "одна фраза"}.\n'
        "public_domain: срок охраны истёк. official_free_ru: правообладатель сам официально выкладывает произведение полностью и бесплатно "
        "на YouTube или Rutube. music_risk: музыкальные номера или клипы, по которым отдельно жалуются лейблы."
    )
    data, usage = llm.ask_json(SYSTEM_RIGHTS, user, max_tokens=320)
    record_usage(conn, "rights", llm.model, usage, ok=True, note=title["title_id"])
    policy = str(data.get("holder_policy") or "unknown").lower()
    ans = {"holder": str(data.get("holder") or "").strip()[:160] or None, "holder_policy": policy if policy in POLICIES else "unknown",
           "public_domain": bool(data.get("public_domain")), "official_free_ru": bool(data.get("official_free_ru")), "music_risk": bool(data.get("music_risk")),
           "confidence": float(data.get("confidence") or 0), "note": str(data.get("note") or "").strip()[:200], "model": llm.model}
    ts = now_iso()
    conn.execute("""INSERT INTO title_rights(title_id, llm_json, llm_at, checked_at) VALUES (?,?,?,?)
                    ON CONFLICT(title_id) DO UPDATE SET llm_json = excluded.llm_json, llm_at = excluded.llm_at""", (title["title_id"], dumps(ans), ts, ts))
    conn.commit()
    return ans


# ---------- перепроверка чужих нарезок ----------
def apply_recheck(conn: sqlite3.Connection, d: VideoDetails, ts: str) -> None:
    if d.status == "removed":
        conn.execute("""UPDATE videos SET status = 'removed', details_at = ?, removed_at = COALESCE(removed_at, ?), removed_reason = COALESCE(?, removed_reason)
                        WHERE video_id = ?""", (ts, ts, (d.error or None), d.video_id))
    elif d.status in ("ok", "not_short"):
        conn.execute("UPDATE videos SET details_at = ?, views = COALESCE(?, views), stats_at = CASE WHEN ? IS NOT NULL THEN ? ELSE stats_at END WHERE video_id = ?",
                     (ts, d.views, d.views, ts, d.video_id))
        if d.views is not None:
            conn.execute("INSERT OR REPLACE INTO video_stats(video_id, ts, views, likes, comments, src) VALUES (?,?,?,?,?,'page')",
                         (d.video_id, ts, d.views, d.likes, d.comments))


def recheck_videos(conn: sqlite3.Connection, title_ids: list[str], budget: int, concurrency: int = 4, min_age_days: int = 7,
                   should_stop=None) -> dict:
    """Перечитывает страницы опознанных чужих нарезок по списку произведений: живы ли, не снесены ли."""
    ids = [i for i in dict.fromkeys(title_ids) if i]
    if not ids or budget <= 0:
        return {"checked": 0, "removed": 0, "ok": 0, "blocked": 0, "error": 0, "aborted": False}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=min_age_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    vids: list[str] = []
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        vids += [r[0] for r in conn.execute(
            f"""SELECT video_id FROM videos WHERE title_id IN ({','.join('?' * len(chunk))}) AND status = 'ok' AND COALESCE(recog_conf, 0) >= 0.6
                AND details_at IS NOT NULL AND details_at < ? ORDER BY COALESCE(views, 0) DESC LIMIT ?""", (*chunk, cutoff, budget))]
    vids = vids[:budget]
    if not vids:
        return {"checked": 0, "removed": 0, "ok": 0, "blocked": 0, "error": 0, "aborted": False}
    ts = now_iso()
    res = asyncio.run(page.fetch_many(vids, concurrency=max(1, min(8, concurrency)), on_result=lambda d: apply_recheck(conn, d, ts), should_stop=should_stop))
    conn.commit()
    return {"checked": len(vids), **res}


# ---------- живы ли исходники ----------
async def _alive(client: httpx.AsyncClient, url: str, video_id: str) -> bool | None:
    if "rutube.ru" in url:
        rid = video_id[3:] if video_id.startswith("rt_") else video_id
        try:
            r = await client.get(f"https://rutube.ru/api/video/{rid}/")
        except httpx.HTTPError:
            return None
        return True if r.status_code == 200 else False if r.status_code in (404, 410) else None
    d = await page.fetch_one(client, video_id)
    return False if d.status == "removed" else True if d.status in ("ok", "not_short") else None


async def _check_sources(rows: list[dict]) -> list[tuple[dict, bool | None]]:
    limits = httpx.Limits(max_connections=3, max_keepalive_connections=3)
    sem = asyncio.Semaphore(3)
    async with httpx.AsyncClient(headers=page.HEADERS, cookies=page.COOKIES, timeout=40, follow_redirects=True, limits=limits) as client:
        async def one(row: dict):
            async with sem:
                await asyncio.sleep(0.3)
                return row, await _alive(client, row["url"], row["video_id"])
        return list(await asyncio.gather(*(one(r) for r in rows)))


def check_sources_alive(conn: sqlite3.Connection, title_ids: list[str], per_title: int = 2, log=print) -> dict:
    """Проверяет лучшие исходники произведений; мёртвый исходник снимает отметку «проверено», чтобы поиск повторился."""
    rows: list[dict] = []
    for tid in dict.fromkeys(title_ids):
        rows += [dict(r) for r in conn.execute("SELECT id, title_id, url, video_id FROM title_sources WHERE title_id = ? ORDER BY verified DESC, score DESC LIMIT ?", (tid, per_title))]
    if not rows:
        return {"checked": 0, "dead": 0}
    ts = now_iso()
    dead = 0
    for row, alive in asyncio.run(_check_sources(rows)):
        if alive is None:
            continue
        conn.execute("UPDATE title_sources SET alive = ?, checked_at = ? WHERE id = ?", (int(alive), ts, row["id"]))
        if not alive:
            dead += 1
            conn.execute("DELETE FROM meta WHERE key = ?", (f"sources_checked:{row['title_id']}",))
            log(f"исходник удалён, будет найден заново: {row['url']}", "warn")
    conn.commit()
    return {"checked": len(rows), "dead": dead}


# ---------- фаза прогона ----------
def candidates(conn: sqlite3.Connection, limit: int = 120) -> tuple[list[str], list[str]]:
    """Произведения календаря (впереди) и кандидаты по возможности."""
    from . import recommend
    from .schedule import today_msk
    from .stats import Filters
    sched = [r[0] for r in conn.execute("SELECT DISTINCT title_id FROM schedule WHERE title_id IS NOT NULL AND day >= ? AND status != 'skipped'", (today_msk(),))]
    recs = [it["title_id"] for it in recommend.today(conn, Filters("30", lang="ru"), limit=limit)["items"]]
    return sched, list(dict.fromkeys(sched + recs))


def run_phase(conn: sqlite3.Connection, settings: dict, log=print, should_stop=None) -> dict:
    sched, ids = candidates(conn)
    out = {"titles": len(ids), "rechecked": 0, "removed": 0, "llm": 0, "sources_checked": 0, "sources_dead": 0}
    budget = int(settings.get("rights_recheck_max") or 0)
    if budget > 0 and ids:
        r = recheck_videos(conn, ids, budget, concurrency=int(settings.get("detail_concurrency") or 4), should_stop=should_stop)
        out["rechecked"], out["removed"] = r["checked"], r["removed"]
        if r.get("aborted"):
            log("перепроверка чужих нарезок: YouTube начал резать запросы, остановлено до следующего прогона", "warn")
    llm = llm_from_settings(settings)
    max_llm = int(settings.get("rights_llm_max") or 0)
    if llm.configured and max_llm > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for tid in ids:
            if out["llm"] >= max_llm or (should_stop and should_stop()):
                break
            row = conn.execute("SELECT llm_at FROM title_rights WHERE title_id = ?", (tid,)).fetchone()
            if row and row["llm_at"] and row["llm_at"] >= cutoff:
                continue
            t = title_info(conn, tid)
            if not t:
                continue
            try:
                llm_assess(conn, llm, t)
                out["llm"] += 1
            except LLMError as e:
                record_usage(conn, "rights", llm.model, None, ok=False, note=str(e)[:200])
                log(f"права «{t['label']}»: модель не ответила: {e}", "warn")
    if sched and not (should_stop and should_stop()):
        try:
            r = check_sources_alive(conn, sched, log=log)
            out["sources_checked"], out["sources_dead"] = r["checked"], r["dead"]
        except Exception as e:  # noqa: BLE001
            log(f"проверка исходников: {e}", "warn")
    for tid in ids:
        a = evaluate(conn, tid)
        if a:
            out[a["level"]] = out.get(a["level"], 0) + 1
    log(f"авторские права: {len(ids)} произведений; перепроверено нарезок {out['rechecked']}, снесено {out['removed']}; модель {out['llm']}; "
        f"не проходит {out.get('block', 0)}, с оговорками {out.get('caution', 0)}, проходит {out.get('ok', 0)}; мёртвых исходников {out['sources_dead']}")
    return out


# ---------- события и ручные решения ----------
def add_event(conn: sqlite3.Connection, title_id: str, kind: str, source: str = "operator", claimant: str | None = None,
              video_url: str | None = None, note: str | None = None) -> dict | None:
    if kind not in EVENT_KINDS:
        raise ValueError("kind: " + "|".join(EVENT_KINDS))
    if not conn.execute("SELECT 1 FROM titles WHERE id = ?", (title_id,)).fetchone():
        return None
    conn.execute("INSERT INTO rights_events(ts, title_id, kind, source, claimant, video_url, note) VALUES (?,?,?,?,?,?,?)",
                 (now_iso(), title_id, kind, (source or "operator")[:40], (claimant or None), (video_url or None), (note or None)))
    conn.commit()
    return evaluate(conn, title_id)


def set_manual(conn: sqlite3.Connection, title_id: str, level: str | None, note: str | None = None) -> dict | None:
    if level not in (None, "", "ok", "caution", "block"):
        raise ValueError("level: ok|caution|block или пусто")
    level = level or None
    ts = now_iso()
    conn.execute("""INSERT INTO title_rights(title_id, manual_level, manual_note, checked_at) VALUES (?,?,?,?)
                    ON CONFLICT(title_id) DO UPDATE SET manual_level = excluded.manual_level, manual_note = excluded.manual_note""",
                 (title_id, level, (note or None), ts))
    conn.execute("INSERT INTO rights_events(ts, title_id, kind, source, note) VALUES (?,?,'manual','operator',?)",
                 (ts, title_id, ("решение: " + (LEVEL_RU.get(level, "авто") if level else "авто")) + (f"; {note}" if note else "")))
    conn.commit()
    return evaluate(conn, title_id)


def overview(conn: sqlite3.Connection, limit_events: int = 15) -> dict:
    from .schedule import today_msk
    policy = get_setting(conn, "rights_policy") or "balanced"
    cal: Counter = Counter()
    for r in conn.execute("""SELECT DISTINCT s.title_id, COALESCE(tr.level, 'unknown') AS level FROM schedule s LEFT JOIN title_rights tr ON tr.title_id = s.title_id
                             WHERE s.title_id IS NOT NULL AND s.day >= ? AND s.status != 'skipped'""", (today_msk(),)):
        cal[r["level"]] += 1
    all_levels = Counter({r["level"]: r["n"] for r in conn.execute("SELECT level, COUNT(*) n FROM title_rights GROUP BY level")})
    claimants: dict[str, dict] = {}
    kinds: Counter = Counter()
    for r in conn.execute("SELECT v.removed_reason, COALESCE(t.label_ru, t.label_en) AS label FROM videos v LEFT JOIN titles t ON t.id = v.title_id WHERE v.status = 'removed'"):
        k, who = classify_removed(r["removed_reason"])
        kinds[k] += 1
        if who:
            c = claimants.setdefault(who, {"name": who, "n": 0, "titles": []})
            c["n"] += 1
            if r["label"] and r["label"] not in c["titles"]:
                c["titles"].append(r["label"])
    events = [dict(r) for r in conn.execute("""SELECT e.id, e.ts, e.kind, e.source, e.claimant, e.video_url, e.note, e.title_id, COALESCE(t.label_ru, t.label_en) AS label
                                               FROM rights_events e LEFT JOIN titles t ON t.id = e.title_id ORDER BY e.ts DESC LIMIT ?""", (limit_events,))]
    for e in events:
        e["kind_ru"] = EVENT_RU.get(e["kind"], e["kind"])
    blocked = [dict(r) for r in conn.execute("""SELECT tr.title_id, COALESCE(t.label_ru, t.label_en) AS label, tr.risk, tr.reasons FROM title_rights tr
                                                JOIN titles t ON t.id = tr.title_id WHERE tr.level = 'block' ORDER BY tr.risk DESC LIMIT 10""")]
    for b in blocked:
        b["reasons"] = loads(b["reasons"], [])
    return {"policy": policy, "policy_ru": POLICY_CHOICE_RU.get(policy, policy), "calendar": dict(cal), "levels": dict(all_levels),
            "checked": sum(all_levels.values()), "last_checked": conn.execute("SELECT MAX(checked_at) FROM title_rights").fetchone()[0],
            "removed": {"total": sum(kinds.values()), **{k: kinds[k] for k in ("copyright", "terminated", "private", "user_removed", "other", "none")}},
            "claimants": sorted(claimants.values(), key=lambda x: -x["n"])[:10], "events": events, "blocked": blocked, "clip_cap_s": CLIP_CAP_S}
