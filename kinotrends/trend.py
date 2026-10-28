"""Тренд рынка: что сейчас массово режут и смотрят в русскоязычных Shorts.

«Возможность» (opportunity) ищет свободные ниши и выводит малоизвестное; для канала, который должен
расти на хитах, нужен другой порядок — по силе тренда:
- произведение режут несколько разных авторов (не один вирусный ролик одного канала);
- есть свежие шортсы за неделю;
- их много смотрят (просмотры в день по свежим роликам).

Распознавание по хэштегам привязывает к «фильмам» обычные слова: #концерт, #интервью, #скандал,
#дикаяприрода. Поэтому кандидата проверяет модель по названиям его самых популярных шортсов —
действительно ли это ролики про это произведение. Ответ кэшируется на неделю.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta, timezone

from .db import dumps, loads, now_iso
from .llm import LLM, LLMError, record_usage

KINDS = ("film", "series", "animated", "animated_series", "anime", "tv_show")
MIN_CHANNELS = 3
MIN_FRESH = 2
VALID_DAYS = 7

SYSTEM_VALIDATE = (
    "Тебе дают фильм или сериал (название, год, оригинальное название) и пронумерованные заголовки YouTube Shorts, которые "
    "система к нему привязала. Для КАЖДОГО заголовка реши: ролик про это самое произведение (его сцены, герои, актёры в роли)? "
    "Нет — если слово совпало случайно: настоящий концерт или песня, интервью, животные, телефоны, игра, другой фильм с похожим "
    "названием, новости. Отвечай только JSON: {\"items\": [{\"n\": 1, \"about\": true}, {\"n\": 2, \"about\": false}]}"
)


def trend_score(it: dict) -> float:
    """Сила тренда: просмотры свежих роликов в день, число авторов, число свежих роликов (логарифмы, чтобы
    один вирусный ролик не перевешивал массовость)."""
    return round(math.log10((it.get("vpd_sum") or 0) + 10) * 2 + math.log2(it.get("n_channels") or 1)
                 + math.log2((it.get("n_fresh7") or 0) + 1), 2)


def is_market_trend(it: dict, this_year: int | None = None) -> bool:
    """Фильм текущего года в тренде — это ажиотаж вокруг трейлеров: полной версии в сети нет, а «полные фильмы»
    на YouTube — подделки и игрофильмы. Сериалы этого года — можно (серии выходят)."""
    this_year = this_year or datetime.now(timezone.utc).year
    if it.get("kind") in ("film", "animated") and (it.get("year") or 0) >= this_year:
        return False
    return it.get("kind") in KINDS and (it.get("n_channels") or 0) >= MIN_CHANNELS and (it.get("n_fresh7") or 0) >= MIN_FRESH


def _cached(conn: sqlite3.Connection, title_id: str) -> dict | None:
    r = conn.execute("SELECT value FROM meta WHERE key = ?", (f"trend_valid:{title_id}",)).fetchone()
    d = loads(r["value"], None) if r else None
    if not d:
        return None
    cutoff = (datetime.now(timezone.utc) - timedelta(days=VALID_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return d if (d.get("at") or "") >= cutoff else None


def validate(conn: sqlite3.Connection, llm: LLM | None, title_id: str, label: str, year=None, label_en: str | None = None) -> dict:
    """{"ok": bool|None, "share": 0..1}. None — модель не ответила (тогда кандидат не отбрасывается)."""
    c = _cached(conn, title_id)
    if c:
        return c
    rows = conn.execute("SELECT title FROM videos WHERE title_id = ? AND title IS NOT NULL ORDER BY COALESCE(views, 0) DESC LIMIT 12",
                        (title_id,)).fetchall()
    titles = [r["title"] for r in rows if r["title"]]
    if len(titles) < 3 or llm is None or not llm.configured:
        return {"ok": None, "share": None}
    orig = f", оригинал: {label_en}" if label_en and label_en != label else ""
    user = (f"Произведение: {label} ({year or 'год неизвестен'}{orig}).\nЗаголовки шортсов:\n"
            + "\n".join(f"{i + 1}. {t[:120]}" for i, t in enumerate(titles)))
    try:
        data, usage = llm.ask_json(SYSTEM_VALIDATE, user, max_tokens=400)
        record_usage(conn, "trend", llm.model, usage, ok=True, note=title_id)
    except LLMError as e:
        record_usage(conn, "trend", llm.model, None, ok=False, note=str(e)[:200])
        return {"ok": None, "share": None}
    marks = {int(x.get("n")): bool(x.get("about")) for x in (data.get("items") or []) if str(x.get("n") or "").isdigit()}
    if len(marks) < len(titles) // 2:
        return {"ok": None, "share": None}  # ответ неполный — не решаем по нему
    share = sum(1 for v in marks.values() if v) / len(marks)
    res = {"ok": share >= 0.6, "share": round(share, 2), "n": len(marks), "at": now_iso()}
    conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (f"trend_valid:{title_id}", dumps(res)))
    conn.commit()
    return res


def market(conn: sqlite3.Connection, llm: LLM | None, period: str = "7", limit: int = 30, validate_top: int = 40) -> list[dict]:
    """Хиты рынка по силе тренда, проверенные моделью. Каждому добавлены trend и trend_share."""
    from . import stats
    from .stats import Filters

    def ranked(per: str) -> list[dict]:
        got = [it for it in stats.films(conn, Filters(per, lang="ru"), limit=600, sort="score")["items"] if is_market_trend(it)]
        for it in got:
            it["trend"] = trend_score(it)
            it["trend_period"] = per
        return sorted(got, key=lambda it: -it["trend"])

    # сначала то, что набирает за неделю; хиты месяца — следом, чтобы календарю хватило названий
    items = ranked(period)
    if period == "7":
        seen = {it["title_id"] for it in items}
        items += [it for it in ranked("30") if it["title_id"] not in seen]
    out: list[dict] = []
    for it in items[:validate_top]:
        v = validate(conn, llm, it["title_id"], it["label"], it.get("year"), it.get("label_en"))
        if v.get("ok") is False:
            continue
        it["trend_share"] = v.get("share")
        out.append(it)
        if len(out) >= limit:
            break
    return out
