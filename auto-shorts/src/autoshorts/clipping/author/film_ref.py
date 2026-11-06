"""Справка о фильме для викторины «Угадай фильм»: статья русской Википедии — введение и разделы о создании,
съёмках, прокате, наградах, критике. Модель берёт из неё подсказку и факт после ответа, а не выдумывает и не
пересказывает сцену: YouTube с 01.10.2026 не считает вкладом автора «VO descriptions of what's happening on
screen» и шаблонные правки (docs/SHORTS_RULES.md, §1).

Статья ищется по Wikidata-ID из записи прав источника (`wikidata=Q…` в proof_note, ставит Кино-тренды), иначе
поиском «название год фильм» (только с годом и точным совпадением названия статьи). Ответы кешируются на диске; любой сбой сети — пустая справка (сценарий пишется
как раньше, стадия не падает).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import httpx

UA = "auto-shorts/1.0 (+https://github.com/1astah1/kino-trends) python-httpx"
WIKI_API = "https://ru.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
MAX_CHARS = 4500
INTRO_CHARS = 1400
CACHE_TTL_SEC = 30 * 86400
# разделы с фактами вне кадра; сюжет не берём — из него модель пересказывает сцену и спойлерит
KEEP = (
    "создан", "съёмк", "съемк", "производств", "разработк", "кастинг", "подбор акт", "факт", "наград", "преми",
    "критик", "отзыв", "восприят", "кассов", "прокат", "сбор", "интересн", "отлич",
    "экранизац", "истори", "реж", "спецэффект", "грим", "локац",
)
SKIP = ("сюжет", "в ролях", "примечан", "литератур", "ссылки", "см. также", "съёмочная группа", "источник",
        "саундтрек", "музык", "plot", "cast", "references", "external links", "see also", "soundtrack", "notes")
# английская статья, если русская бедная (у «Такси» — введение и список песен): разделы о создании и прокате
KEEP_EN = ("production", "development", "filming", "casting", "writing", "pre-production", "post-production",
           "release", "reception", "box office", "legacy", "accolades", "awards", "trivia", "behind", "design",
           "effects", "stunts", "background", "conception", "origin", "history")
EN_API = "https://en.wikipedia.org/w/api.php"
RICH_CHARS = 2500  # русская справка короче — добираем английскую (перевод на русский, тоже в кеш)
_HEADER = re.compile(r"\n(={2,})\s*(.+?)\s*\1\n")
_QID = re.compile(r"wikidata\s*=\s*(Q\d+)", re.I)


def qid_from(note: str | None) -> str | None:
    m = _QID.search(str(note or ""))
    return m.group(1).upper() if m else None


def _cache_path(key: str) -> Path:
    root = Path(os.environ.get("MEDIA_ROOT") or tempfile.gettempdir()) / "cache" / "film_ref"
    root.mkdir(parents=True, exist_ok=True)
    return root / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")


def _norm(text: str) -> str:
    return str(text).lower().replace("ё", "е")


def _stems(film: str) -> list[str]:
    words = [w for w in re.findall(r"[а-яa-z0-9]+", _norm(film)) if len(w) >= 3]
    return [w if len(w) <= 5 else w[: len(w) - 2] for w in words]


def _title_by_qid(c: httpx.Client, qid: str) -> str | None:
    r = c.get(WIKIDATA_API, params={"action": "wbgetentities", "ids": qid, "props": "sitelinks",
                                    "sitefilter": "ruwiki", "format": "json"})
    r.raise_for_status()
    ent = (r.json().get("entities") or {}).get(qid) or {}
    return ((ent.get("sitelinks") or {}).get("ruwiki") or {}).get("title")


def _base_title(title: str) -> str:
    """Название статьи без уточнения в скобках: «Такси (фильм, 1998)» → «такси»."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", _norm(title).replace("\u0301", "")).strip()


def _title_by_search(c: httpx.Client, film: str, year: str | None) -> str | None:
    """Статья, чьё название до скобок совпадает с фильмом (иначе «Острые козырьки» находят фильм «…: Бессмертный»,
    а «Менталист» — другой сериал). Из совпавших выше та, где год и «фильм/сериал» в названии; год задан, но его нет
    ни в названии, ни в отрывке — не берём."""
    q = f"{film} {year or ''} фильм".strip()
    r = c.get(WIKI_API, params={"action": "query", "list": "search", "srsearch": q, "srlimit": 8,
                                "format": "json", "formatversion": 2})
    r.raise_for_status()
    want = _base_title(film)
    best: tuple[int, str] | None = None
    for hit in (r.json().get("query") or {}).get("search") or []:
        title = str(hit.get("title") or "")
        if _base_title(title) != want:
            continue
        t, snippet = _norm(title), _norm(hit.get("snippet") or "")
        score = 0
        if year:
            if year in t:
                score += 3
            elif year in snippet:
                score += 1
            else:
                continue
        if any(k in t for k in ("(фильм", "(телесериал", "(сериал", "(мульт", "(аниме")):
            score += 1
        if best is None or score > best[0]:
            best = (score, title)
    return best[1] if best else None


def _extract(c: httpx.Client, title: str) -> str:
    r = c.get(WIKI_API, params={"action": "query", "prop": "extracts", "explaintext": 1, "exsectionformat": "wiki",
                                "titles": title, "redirects": 1, "format": "json", "formatversion": 2})
    r.raise_for_status()
    pages = (r.json().get("query") or {}).get("pages") or []
    return str((pages[0] if pages else {}).get("extract") or "")


def _en_title(c: httpx.Client, qid: str | None, ru_title: str | None) -> str | None:
    if qid:
        r = c.get(WIKIDATA_API, params={"action": "wbgetentities", "ids": qid, "props": "sitelinks",
                                        "sitefilter": "enwiki", "format": "json"})
        r.raise_for_status()
        t = ((((r.json().get("entities") or {}).get(qid) or {}).get("sitelinks") or {}).get("enwiki") or {}).get("title")
        if t:
            return t
    if ru_title:
        r = c.get(WIKI_API, params={"action": "query", "prop": "langlinks", "lllang": "en", "titles": ru_title,
                                    "redirects": 1, "format": "json", "formatversion": 2})
        r.raise_for_status()
        pages = (r.json().get("query") or {}).get("pages") or []
        links = (pages[0] if pages else {}).get("langlinks") or []
        if links:
            return links[0].get("title")
    return None


def _en_extract(c: httpx.Client, title: str) -> str:
    r = c.get(EN_API, params={"action": "query", "prop": "extracts", "explaintext": 1, "exsectionformat": "wiki",
                              "titles": title, "redirects": 1, "format": "json", "formatversion": 2})
    r.raise_for_status()
    pages = (r.json().get("query") or {}).get("pages") or []
    return str((pages[0] if pages else {}).get("extract") or "")


def trim_article(text: str, max_chars: int = MAX_CHARS, keep: tuple[str, ...] = KEEP) -> str:
    """Введение (до INTRO_CHARS) и разделы о создании/наградах/критике, без сюжета и списков, до max_chars."""
    text = "\n" + str(text or "").strip() + "\n"
    parts = _HEADER.split(text)
    out = [re.sub(r"\s+", " ", parts[0]).strip()[:INTRO_CHARS]]
    total = len(out[0])
    # parts: [intro, "==", name, body, "===", name, body, ...]
    for i in range(1, len(parts) - 2, 3):
        name, body = parts[i + 1], parts[i + 2]
        low = _norm(name)
        if any(s in low for s in SKIP) or not any(k in low for k in keep):
            continue
        chunk = re.sub(r"\s+", " ", body).strip()
        if len(chunk) < 40:
            continue
        piece = f"[{name.strip()}] {chunk}"
        room = max_chars - total
        if room < 200:
            break
        out.append(piece[:room])
        total += len(out[-1])
    return "\n".join(x for x in out if x)


def film_reference(
    film: str, year: str | None = None, *, qid: str | None = None, timeout: float = 8.0,
    translate: "Callable[[str], str] | None" = None,
) -> str:
    """Справка о фильме (может быть пустой). Кеш 30 дней, включая «не нашли». `translate` — перевод английской
    справки на русский (LLM); без него английская не добирается."""
    film = str(film or "").strip()
    if not film and not qid:
        return ""
    path = _cache_path(f"{qid or ''}|{_norm(film)}|{year or ''}|{'en' if translate else 'ru'}")
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(cached.get("at") or 0) < CACHE_TTL_SEC:
            return str(cached.get("text") or "")
    except (OSError, ValueError):
        pass
    text, title = "", None
    try:
        with httpx.Client(timeout=timeout, headers={"User-Agent": UA}, follow_redirects=True) as c:
            if qid:
                title = _title_by_qid(c, qid)
            if not title and film and year:
                # без года поиск путает одноимённое («Менталист» → российский сериал 2018) — лучше без справки
                title = _title_by_search(c, film, year)
            if title:
                text = trim_article(_extract(c, title))
            if (title or qid) and translate is not None and len(text) < RICH_CHARS:
                en_title = _en_title(c, qid, title)
                en = trim_article(_en_extract(c, en_title), MAX_CHARS, KEEP_EN) if en_title else ""
                if len(en) > 600:
                    ru = str(translate(en) or "").strip()
                    if ru:
                        text = (text + "\n[из английской Википедии] " + ru).strip()
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return ""  # сеть или формат ответа: без справки, в кеш не пишем — в следующий раз попробуем снова
    try:
        path.write_text(json.dumps({"at": time.time(), "title": title, "text": text}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return text


__all__ = ["film_reference", "qid_from", "trim_article"]
