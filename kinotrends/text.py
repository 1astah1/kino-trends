"""Нормализация текста, хэштеги, язык, признаки заголовка."""
from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_HASHTAG = re.compile(r"#([\w]+)", re.UNICODE)
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U0001F900-\U0001F9FF⭐⭕‼⁉™ℹ↔-↪⌚-⏿▪-◾️‍]"
)
_CYR = re.compile(r"[а-яёА-ЯЁ]")
_LAT = re.compile(r"[a-zA-Z]")
_UK = re.compile(r"[іїєґІЇЄҐ]")
_KK = re.compile(r"[әғқңөұүһӘҒҚҢӨҰҮҺ]")
_YEAR = re.compile(r"\(?\b(19\d{2}|20\d{2})\b\)?")
_SEASON = re.compile(r"\b(\d+\s*(?:сезон|серия|season|episode)|(?:сезон|серия|season|episode)\s*\d+)\b", re.IGNORECASE)


def norm(s: str | None) -> str:
    """Нижний регистр, ё→е, без пунктуации и диакритики, одиночные пробелы."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.lower().replace("ё", "е")
    s = _EMOJI.sub(" ", s)
    s = _NON_WORD.sub(" ", s)
    s = s.replace("_", " ")
    return _WS.sub(" ", s).strip()


def squash(s: str | None) -> str:
    return norm(s).replace(" ", "")


def hashtags(*texts: str | None) -> list[str]:
    out: list[str] = []
    seen = set()
    for t in texts:
        if not t:
            continue
        for m in _HASHTAG.finditer(t):
            h = m.group(1).lower().replace("ё", "е")
            if h not in seen:
                seen.add(h)
                out.append(h)
    return out


def strip_hashtags(s: str | None) -> str:
    return _HASHTAG.sub(" ", s or "")


def detect_lang(*texts: str | None) -> str:
    t = " ".join(x for x in texts if x)
    t = strip_hashtags(t)
    cyr = len(_CYR.findall(t))
    lat = len(_LAT.findall(t))
    if cyr == 0 and lat == 0:
        return "other"
    if _UK.search(t) and cyr >= 3:
        return "uk"
    if _KK.search(t) and cyr >= 3:
        return "kk"
    if cyr >= lat:
        return "ru"
    return "en"


def year_hint(s: str) -> int | None:
    m = _YEAR.search(s or "")
    return int(m.group(1)) if m else None


def strip_year_season(s: str) -> str:
    s = _YEAR.sub(" ", s or "")
    s = _SEASON.sub(" ", s)
    return _WS.sub(" ", s).strip(" -–—:|,.«»\"'“”‘’()[]")


def duration_bucket(d: int | None) -> str:
    if d is None:
        return "неизвестно"
    if d <= 15:
        return "до 15 с"
    if d <= 30:
        return "16–30 с"
    if d <= 45:
        return "31–45 с"
    if d <= 60:
        return "46–60 с"
    if d <= 90:
        return "61–90 с"
    if d <= 180:
        return "91–180 с"
    return "более 3 мин"


def title_features(title: str | None, description: str | None) -> dict:
    """Признаки оформления ролика, по которым считается «что заходит»."""
    t = title or ""
    plain = strip_hashtags(t)
    tags = hashtags(t, description)
    low = t.lower()
    return {
        "question": "?" in plain,
        "emoji": bool(_EMOJI.search(t)),
        "names_title": bool(re.search(r"название|назв\.|фильм\s*[:—–-]|сериал\s*[:—–-]|из фильма|из сериала", low)),
        "hidden_title": bool(re.search(r"название в комм|назв\. в комм|в комментар|в закреп|в описании", low)),
        "caps": sum(1 for ch in plain if ch.isupper()) >= max(6, len([c for c in plain if c.isalpha()]) * 0.5) if plain.strip() else False,
        "pipe": "|" in t,
        "n_hashtags": len(tags),
        "has_shorts_tag": "shorts" in tags,
        "title_len": len(plain.strip()),
        "exclaim": "!" in plain,
    }


_SEASON_RE = re.compile(r"(?:(\d{1,2})\s*сезон|сезон\s*(\d{1,2})|\bs(\d{1,2})e(\d{1,3})\b|(\d{1,2})\s*season|season\s*(\d{1,2}))", re.IGNORECASE)
_EPISODE_RE = re.compile(r"(?:(\d{1,3})\s*серия|серия\s*(\d{1,3})|(\d{1,3})\s*эпизод|эпизод\s*(\d{1,3})|(\d{1,3})\s*episode|episode\s*(\d{1,3}))", re.IGNORECASE)


def season_episode(*texts: str | None) -> tuple[int | None, int | None]:
    """«3 сезон 7 серия», «s02e05», «сезон 1» → (сезон, серия)."""
    t = " ".join(x for x in texts if x)
    season = episode = None
    m = _SEASON_RE.search(t)
    if m:
        vals = [g for g in m.groups() if g]
        if m.group(3) and m.group(4):
            season, episode = int(m.group(3)), int(m.group(4))
        elif vals:
            season = int(vals[0])
    if episode is None:
        m2 = _EPISODE_RE.search(t)
        if m2:
            vals = [g for g in m2.groups() if g]
            if vals:
                episode = int(vals[0])
    if season is not None and not (1 <= season <= 40):
        season = None
    if episode is not None and not (1 <= episode <= 500):
        episode = None
    return season, episode
