"""Распознавание фильма или сериала по заголовку, описанию, тегам и хэштегам ролика.

Порядок силы улик:
  cue      «Название: Орвилл (2017)», «Фильм — Джентльмены», «из фильма «Мумия»»     0.95
  quote    «…» или "…" в заголовке                                                     0.85
  hashtag  #гаррипоттер, #джентльмены (непустые, не общие)                              0.80
  segment  кусок заголовка между | или после тире                                       0.70
  text     название словаря встретилось в тексте целиком (чем длиннее, тем надёжнее)    0.50–0.70
  channel  канал режет одно произведение (ставит оператор)                              0.75
Год в тексте сверяется с годом из словаря. Все кандидаты сохраняются, чтобы учиться на ручной разметке.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import threading
import unicodedata
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from .db import now_iso
from .text import norm, squash, hashtags, strip_hashtags, strip_year_season, year_hint

GENERIC_TAGS = {
    "фильм", "фильмы", "кино", "сериал", "сериалы", "shorts", "short", "shortvideo", "shortsvideo", "моменты", "момент",
    "моментыизфильмов", "моментыизсериалов", "нарезка", "нарезки", "нарезкафильмов", "нарезкасериалов", "отрывок", "отрывки",
    "отрывокизфильма", "киномоменты", "кинонарезка", "кинонарезки", "film", "films", "movie", "movies", "series", "serial",
    "tvseries", "рекомендации", "рек", "реки", "топ", "юмор", "смех", "прикол", "приколы", "мем", "мемы", "fyp", "foryou",
    "foryoupage", "viral", "trending", "тренды", "тренд", "лучшиемоменты", "лучшее", "кинопоиск", "актер", "актеры", "актёр",
    "кадры", "сцена", "сцены", "боевик", "боевики", "комедия", "комедии", "драма", "ужасы", "хоррор", "фантастика", "триллер",
    "мультфильм", "мультфильмы", "мультик", "мультики", "аниме", "дорама", "дорамы", "turkish", "турецкийсериал",
    "русскиесериалы", "российскиесериалы", "советскоекино", "ссср", "советскиефильмы", "русскоекино", "русскиефильмы",
    "фильмыонлайн", "смотреть", "интересно", "интересное", "youtube", "youtubeshorts", "ютуб", "video", "видео", "тикток",
    "tiktok", "reels", "instagram", "подписывайся", "подпишись", "лайк", "жиза", "жизнь", "мотивация", "цитаты", "эмоции",
    "кинофильм", "киноклипы", "кинолента", "movieclips", "clips", "clip", "scene", "scenes", "edit", "edits", "кино2026",
    "кино2025", "фильм2026", "фильм2025", "новинки", "новинкикино", "кинообзор", "обзор", "сериалы2026", "фильмы2026",
    "лучшиефильмы", "фильмнавечер", "чтопосмотреть", "кинотеатр", "премьера", "трейлер", "trailer", "music", "музыка",
    "song", "песня", "танец", "dance", "funny", "comedy", "humor", "lol", "meme", "memes", "animation", "anime", "cartoon",
    "kdrama", "drama", "action", "horror", "thriller", "scifi", "fantasy", "фэнтези", "мелодрама", "детектив", "криминал",
    "приключения", "семейный", "мистика", "вестерн", "военный", "история", "исторический", "биография", "спорт", "мюзикл",
    "документальный", "короткометражка", "shortfilm", "фильмсмысл", "смысл", "философия", "психология", "love", "любовь",
    "отношения", "семья", "дети", "школа", "учитель", "работа", "деньги", "бизнес", "успех", "мафия", "гангстеры", "бандиты",
    "полиция", "детективы", "сериалпрокриминал", "90е", "2000е", "ностальгия", "детство", "ералаш", "сериалкомедия",
}

STOP_NORMS = {
    "фильм", "кино", "сериал", "мама", "папа", "она", "оно", "он", "они", "мы", "дом", "игра", "любовь", "брат", "друг", "ночь",
    "день", "жизнь", "смерть", "война", "мир", "время", "семья", "школа", "лето", "зима", "весна", "осень", "дети", "мужчина",
    "женщина", "король", "королева", "принц", "принцесса", "монстр", "гость", "гости", "сосед", "соседи", "враг", "враги", "герой",
    "месть", "побег", "охота", "дорога", "город", "деревня", "утро", "вечер", "свадьба", "развод", "учитель", "ученик", "доктор",
    "врач", "полиция", "тюрьма", "суд", "русский", "москва", "россия", "америка", "shorts", "short", "movie", "film", "it", "her",
    "him", "us", "them", "up", "life", "love", "home", "mother", "father", "the", "no", "yes", "да", "нет", "ты", "вы", "я", "мой",
    "твой", "наш", "сын", "дочь", "отец", "мать", "жена", "муж", "девушка", "парень", "человек", "люди", "кот", "собака", "волк",
    "медведь", "лев", "тигр", "зверь", "ребенок", "малыш", "бабушка", "дедушка", "тетя", "дядя", "секрет", "тайна", "выбор", "шанс",
    "ошибка", "правда", "ложь", "страх", "сон", "кошмар", "прошлое", "будущее", "один", "одна", "первый", "последний", "новый",
    "старый", "большой", "маленький", "черный", "белый", "красный", "зеленый", "синий", "золото", "деньги", "работа", "отпуск",
    "праздник", "подарок", "урок", "экзамен", "матч", "бой", "драка", "погоня", "авария", "пожар", "взрыв", "удар", "выстрел",
    "ограбление", "кража", "обман", "предатель", "предательство", "ревность", "измена", "соблазн", "желание", "страсть", "начало",
    "конец", "финал", "история", "легенда", "миф", "сказка", "шок", "ужас", "чудо", "судьба", "карма", "удача", "богатство",
    "нищета", "голод", "жажда", "боль", "слезы", "смех", "улыбка", "взгляд", "голос", "крик", "шепот", "тишина", "музыка", "песня",
    "танец", "вечеринка", "клуб", "бар", "ресторан", "кафе", "магазин", "рынок", "банк", "офис", "завод", "больница", "аптека",
    "вокзал", "аэропорт", "самолет", "поезд", "машина", "такси", "автобус", "метро", "корабль", "лодка", "море", "океан", "река",
    "озеро", "лес", "гора", "горы", "пустыня", "остров", "небо", "звезды", "луна", "солнце", "дождь", "снег", "ветер", "буря",
    "гроза", "туман", "огонь", "вода", "земля", "воздух", "камень", "дерево", "цветы", "трава", "хлеб", "вино", "еда", "обед",
    "ужин", "завтрак", "кухня", "комната", "окно", "дверь", "стена", "крыша", "подвал", "гараж", "двор", "улица", "мост", "башня",
    "замок", "дворец", "храм", "церковь", "кладбище", "могила", "призрак", "демон", "ангел", "бог", "дьявол", "рай", "ад",
    "ведьма", "колдун", "маг", "волшебник", "рыцарь", "воин", "солдат", "генерал", "капитан", "пилот", "моряк", "ковбой", "индеец",
    "пират", "разбойник", "вор", "убийца", "маньяк", "шпион", "агент", "сыщик", "адвокат", "судья", "прокурор", "мэр",
    "президент", "министр", "депутат", "директор", "босс", "начальник", "шеф", "хозяин", "слуга", "раб", "гений", "идиот",
    "дурак", "псих", "чудак", "красавица", "чудовище", "зомби", "вампир", "оборотень", "дракон", "робот", "пришелец", "монстры",
    "соседка", "подруга", "невеста", "жених", "вдова", "сирота", "близнецы", "двойник", "незнакомец", "чужой", "свой", "родной",
    "брак", "роман", "дружба", "встреча", "разлука", "прощание", "возвращение", "путь", "поиск", "находка", "потеря", "спасение",
    "наказание", "преступление", "расследование", "допрос", "арест", "приговор", "казнь", "миссия", "операция", "задание", "план",
    "сделка", "контракт", "долг", "кредит", "наследство", "завещание", "клад", "сокровище", "карта", "ключ", "код", "пароль",
    "письмо", "звонок", "сообщение", "новости", "газета", "книга", "дневник", "фото", "картина", "кино", "театр", "цирк", "зоопарк",
    "парк", "сад", "пляж", "бассейн", "спортзал", "стадион", "ринг", "арена", "трасса", "гонка", "финиш", "старт", "победа",
    "поражение", "ничья", "рекорд", "чемпион", "команда", "тренер", "капитан", "игрок", "судья", "фанат", "болельщик", "зритель",
    "актер", "актриса", "режиссер", "сценарий", "роль", "сцена", "кадр", "съемка", "премьера", "успех", "провал", "слава", "деньги",
    "любви", "любовью", "жизни", "смерти", "семьи", "дома", "мира", "войны", "времени", "судьбы", "правды", "сердца", "сердце", "души",
    "ночи", "дня", "детства", "школы", "города", "дороги", "мечты", "мечта", "силы", "сила", "страха", "боли", "счастья", "счастье",
    "надежды", "надежда", "веры", "вера", "денег", "крови", "кровь", "огня", "воды", "свободы", "свобода", "власти", "власть", "славы",
    "чести", "честь", "тьмы", "тьма", "света", "свет", "зла", "зло", "добра", "добро", "игры", "лета", "зимы", "весны", "осени", "ада",
    "рая", "мести", "вины", "вина", "ревности", "измены", "брака", "дружбы", "встречи", "пути", "поиска", "спасения", "наказания",
    "преступления", "истории", "легенды", "сказки", "тайны", "тайна", "секреты", "секрета", "выбора", "ошибки", "лжи", "сна", "сны",
    "прошлого", "будущего", "конца", "начала", "финала", "героя", "героев", "друзей", "друзья", "врагов", "врага", "братья", "сестры",
    "сестра", "родители", "родителей", "детей", "сына", "дочери", "отца", "матери", "жены", "мужа", "девушки", "парня", "человека",
    "людей", "короля", "королевы", "принца", "принцессы", "мужчины", "женщины", "любимый", "любимая", "лучший", "лучшая", "лучшие",
    "первая", "первые", "последняя", "последние", "новая", "новые", "старая", "старые", "большая", "большие", "маленькая", "черная",
    "белая", "красная", "темная", "темный", "светлая", "светлый", "русские", "русская", "американский", "американская", "наша", "наше",
    "наши", "моя", "мое", "мои", "твоя", "твое", "твои", "ваша", "ваше", "ваши", "своя", "свое", "свои", "другой", "другая", "другие",
    "вкусно", "смешно", "страшно", "больно", "грустно", "весело", "красиво", "жесть", "жестко", "мило", "круто", "супер", "интересно",
    "факты", "факт", "истории", "цитата", "мудрость", "шутка", "шутки", "реакция", "реакции", "лайфхак", "лайфхаки", "рецепт", "рецепты",
    "еда", "готовка", "спорт", "футбол", "хоккей", "бокс", "песни", "танцы", "машины", "авто", "мото", "роблокс", "майнкрафт", "манга",
    "косплей", "животные", "коты", "собаки", "универ", "блогер", "блог", "влог", "стрим", "стример", "геймер", "челлендж", "эксперимент",
    "флешбэк", "флэшбек", "флешбек", "flashback", "новости", "служба новостей", "хэллоуин", "хеллоуин", "halloween", "рождество", "пасха", "новый год", "каникулы", "выпускной", "свидание", "знакомство", "экзамен",
    "собеседование", "тренировка", "рыбалка", "охота", "пикник", "ремонт", "переезд", "отпуск", "командировка", "зарплата", "премия",
}
NUMERIC = re.compile(r"^\d+$")
FUNCTION_WORDS = {
    "в", "во", "на", "под", "за", "из", "от", "до", "по", "с", "со", "без", "для", "про", "при", "через", "над", "о", "об", "обо", "у", "к", "ко",
    "и", "а", "но", "не", "ни", "или", "да", "я", "ты", "он", "она", "оно", "мы", "вы", "они", "мой", "моя", "мое", "мои", "твой", "твоя", "наш",
    "наша", "ваш", "ваша", "его", "ее", "их", "это", "этот", "эта", "эти", "тот", "та", "те", "все", "всех", "кто", "что", "где", "когда", "как",
    "зачем", "почему", "the", "a", "an", "my", "your", "our", "his", "her", "its", "no", "not", "in", "on", "at", "to", "of", "for", "with",
}

CUE = re.compile(
    r"(?:название(?:\s+(?:фильма|сериала|мультфильма|аниме|дорамы))?|назв\.?|фильм|сериал|мультфильм|мультик|аниме|дорама|кино|тайтл|movie|film|series|anime|name|title)"
    r"\s*[:：\-–—=]\s*(.+?)(?=\s*(?:[|#\n•]|$))",
    re.IGNORECASE,
)
FROM = re.compile(
    r"(?:из|по|фрагмент|отрывок|момент|моменты|сцена|нарезка|кадры)\s+(?:фильма|сериала|мультфильма|аниме|дорамы|кино)\s+[«\"“„']?(.+?)(?=\s*(?:[|#\n(\[«»\"”']|$))",
    re.IGNORECASE,
)
QUOTE = re.compile(r"[«\"“„]([^»\"”“„]{2,70})[»\"”“]|(?<![\w])'([^']{2,70})'(?![\w])")
JUNK = re.compile(r"^(?:\d+p|\d+k|hd|full\s*hd|uhd|4k|1080p|720p|hdr|сцена|сцены|момент|моменты|отрывок|фрагмент|scene|clip|part\s*\d+|часть\s*\d+)$", re.IGNORECASE)
PAREN_JUNK = re.compile(r"\((?:фильм|сериал|кино|мультфильм|movie|film|series|сцена|момент|отрывок|\s|\d|[,.;:-])*\)?", re.IGNORECASE)
TAIL_WORDS = re.compile(r"(?:\s+(?:сцена|сцены|момент|моменты|отрывок|фрагмент|нарезка|кадры|scene|clip|hd|4k|1080p|720p))+\s*$", re.IGNORECASE)
DASH_SPLIT = re.compile(r"\s[—–-]\s")
TRAIL = re.compile(r"[\s\-–—:|,.!?…·•]+$")
LEAD = re.compile(r"^[\s\-–—:|,.!?…·•]+")


@dataclass
class Candidate:
    text: str
    method: str
    base_conf: float
    year: int | None = None


@dataclass
class Match:
    title_id: str | None = None
    conf: float = 0.0
    method: str | None = None
    evidence: str | None = None
    candidates: list[str] = field(default_factory=list)
    alternatives: list[tuple[str, float]] = field(default_factory=list)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text or ""))


def clean_candidate(s: str) -> str:
    s = strip_hashtags(s)
    s = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]+", " ", s)
    s = re.sub(r"\[[^\]]*\]?", " ", s)
    s = strip_year_season(s)
    s = PAREN_JUNK.sub(" ", s)
    s = TAIL_WORDS.sub("", s)
    s = re.sub(r"\s+", " ", s)
    s = LEAD.sub("", TRAIL.sub("", s))
    return s.strip()


def extract_candidates(title: str | None, description: str | None, keywords: list[str] | None) -> list[Candidate]:
    out: list[Candidate] = []
    seen: set[tuple[str, str]] = set()

    def add(text: str, method: str, conf: float) -> None:
        y = year_hint(text)
        c = clean_candidate(text)
        if len(c) < 2 or len(c) > 80 or NUMERIC.match(c) or JUNK.match(c) or sum(ch.isalpha() for ch in c) < 2:
            return
        key = (norm(c), method)
        if key in seen:
            return
        seen.add(key)
        out.append(Candidate(text=c, method=method, base_conf=conf, year=y))

    t = title or ""
    d = (description or "")[:1500]
    for src in (t, d):
        for m in CUE.finditer(src):
            add(m.group(1), "cue", 0.95)
        for m in FROM.finditer(src):
            add(m.group(1), "cue", 0.92)
        for m in QUOTE.finditer(src):
            add(m.group(1) or m.group(2) or "", "quote", 0.85)
    for part in t.split("|"):
        p = part.strip()
        if p and "#" not in p and 2 <= len(p) <= 60 and p != t.strip():
            add(p, "segment", 0.7)
            if ". " in p:
                for sub in p.split(". "):
                    if 3 <= len(sub.strip()) <= 60:
                        add(sub, "segment", 0.62)
    if DASH_SPLIT.search(t):
        for p in DASH_SPLIT.split(t):
            p = p.strip()
            if p and 2 <= len(p) <= 60:
                add(p, "segment", 0.65)
    for kw in keywords or []:
        if kw and 3 <= len(kw) <= 60:
            add(kw, "keyword", 0.55)
    return out


class Recognizer:
    def __init__(self) -> None:
        self.norm_index: dict[str, list[str]] = {}
        self.squash_index: dict[str, list[str]] = {}
        self.meta: dict[str, tuple] = {}  # id -> (year, sitelinks, kind, label)
        self.keys_by_len: dict[int, list[str]] = {}
        self.bans: set[str] = set()
        self.loaded_at: str | None = None
        self.lock = threading.RLock()

    # ---------- загрузка ----------
    def load(self, conn: sqlite3.Connection, min_sitelinks: int | None = None) -> None:
        if min_sitelinks is None:
            row = conn.execute("SELECT value FROM settings WHERE key = 'dict_min_sitelinks'").fetchone()
            try:
                min_sitelinks = int(row[0]) if row else 4
            except ValueError:
                min_sitelinks = 4
        norm_index: dict[str, list[str]] = {}
        squash_index: dict[str, list[str]] = {}
        meta: dict[str, tuple] = {}
        for r in conn.execute("SELECT id, label_ru, label_en, year, kind, sitelinks FROM titles WHERE sitelinks >= ? OR is_manual = 1", (min_sitelinks,)):
            meta[r["id"]] = (r["year"], r["sitelinks"], r["kind"], r["label_ru"] or r["label_en"])
        for r in conn.execute("SELECT a.title_id, a.norm FROM title_aliases a JOIN titles t ON t.id = a.title_id WHERE t.sitelinks >= ? OR t.is_manual = 1", (min_sitelinks,)):
            nz = sys.intern(r["norm"])
            tid = sys.intern(r["title_id"])
            cur = norm_index.get(nz)
            if cur is None:
                norm_index[nz] = (tid,)
            elif tid not in cur:
                norm_index[nz] = cur + (tid,)
            if " " in nz:
                sq = nz.replace(" ", "")
                if len(sq) >= 5:
                    cur = squash_index.get(sq)
                    if cur is None:
                        squash_index[sq] = (tid,)
                    elif tid not in cur:
                        squash_index[sq] = cur + (tid,)
        keys_by_len: dict[int, list[str]] = {}
        for k in norm_index:
            keys_by_len.setdefault(len(k), []).append(k)
        bans = {r["norm"] for r in conn.execute("SELECT norm FROM alias_bans")}
        with self.lock:
            self.norm_index, self.squash_index, self.meta, self.keys_by_len, self.bans = norm_index, squash_index, meta, keys_by_len, bans
            self.loaded_at = now_iso()

    def ban(self, text: str) -> str:
        nz = norm(text.lstrip("#"))
        with self.lock:
            self.bans.add(nz)
            self.bans.add(nz.replace(" ", ""))
        return nz

    def is_banned(self, key: str) -> bool:
        return key in self.bans or key.replace(" ", "") in self.bans

    @property
    def size(self) -> int:
        return len(self.meta)

    def add_alias(self, title_id: str, alias: str) -> None:
        nz = norm(alias)
        if len(nz) < 2:
            return
        with self.lock:
            ids = self.norm_index.get(nz)
            if ids is None:
                self.norm_index[nz] = (title_id,)
                self.keys_by_len.setdefault(len(nz), []).append(nz)
            elif title_id not in ids:
                self.norm_index[nz] = ids + (title_id,)
            sq = nz.replace(" ", "")
            if " " in nz and len(sq) >= 5:
                sids = self.squash_index.get(sq)
                if sids is None:
                    self.squash_index[sq] = (title_id,)
                elif title_id not in sids:
                    self.squash_index[sq] = sids + (title_id,)

    # ---------- подбор ----------
    def _pick(self, ids: list[str], year: int | None) -> tuple[str, float]:
        """Из нескольких произведений с одинаковым названием выбираем по году, потом по известности."""
        best, bonus = None, 0.0
        if year:
            same = [i for i in ids if self.meta.get(i, (None,))[0] in (year, year - 1, year + 1)]
            if same:
                best = max(same, key=lambda i: self.meta[i][1])
                return best, 0.05
            bonus = -0.15 if any(self.meta.get(i, (None,))[0] for i in ids) else 0.0
        best = max(ids, key=lambda i: self.meta.get(i, (None, 0))[1])
        return best, bonus

    def _lookup(self, text: str, year: int | None, fuzzy_cutoff: float) -> tuple[str, float, str] | None:
        """Точное совпадение, затем нечёткое по ключам близкой длины. Возвращает (id, множитель, ключ)."""
        nz = norm(text)
        if not nz:
            return None
        ids = self.norm_index.get(nz)
        if ids:
            tid, bonus = self._pick(list(ids), year)
            return tid, 1.0 + bonus, nz
        if len(nz) < 4:
            return None
        choices: list[str] = []
        for ln in range(max(2, len(nz) - 3), len(nz) + 4):
            choices.extend(self.keys_by_len.get(ln, ()))
        if not choices:
            return None
        res = process.extractOne(nz, choices, scorer=fuzz.ratio, score_cutoff=fuzzy_cutoff)
        if not res:
            return None
        key, score, _ = res
        tid, bonus = self._pick(list(self.norm_index[key]), year)
        return tid, (score / 100.0) - 0.05 + bonus, key

    def _generic_ok(self, key: str, method: str, tid: str) -> bool:
        toks = key.split()
        sl = self.meta.get(tid, (None, 0))[1]
        if NUMERIC.match(key):
            return False
        all_common = all(t in STOP_NORMS or t in FUNCTION_WORDS for t in toks)
        if method == "text":
            if len(toks) == 1:
                return len(key) >= 5 and key not in STOP_NORMS and key not in FUNCTION_WORDS and sl >= 10
            if len(toks) == 2:
                return sl >= 6 and not all_common and toks[0] not in FUNCTION_WORDS
            return sl >= 3 and not all_common
        if method == "hashtag":
            if len(toks) == 1:
                return len(key) >= 6 and key not in STOP_NORMS and sl >= 15
            return not all_common or sl >= 40
        if method == "keyword":
            if len(toks) == 1:
                return len(key) >= 7 and key not in STOP_NORMS and sl >= 15
            return not all_common
        if len(toks) == 1 and (key in STOP_NORMS or key in FUNCTION_WORDS) and method == "segment":
            return False
        return True

    def recognize(self, title: str | None, description: str | None, keywords: list[str] | None = None,
                  channel_default: str | None = None) -> Match:
        with self.lock:
            return self._recognize(title, description, keywords, channel_default)

    def _recognize(self, title, description, keywords, channel_default) -> Match:
        scored: dict[str, tuple[float, str, str]] = {}  # id -> (conf, method, evidence)
        cands = extract_candidates(title, description, keywords)
        texts = [c.text for c in cands]

        def offer(tid: str, conf: float, method: str, evidence: str) -> None:
            conf = round(min(0.99, max(0.0, conf)), 3)
            cur = scored.get(tid)
            if cur is None or conf > cur[0]:
                scored[tid] = (conf, method, evidence)

        for c in cands:
            cutoff = 88 if c.method == "cue" else 90
            hit = self._lookup(c.text, c.year, cutoff)
            if not hit:
                continue
            tid, mult, key = hit
            if self.is_banned(key) or not self._generic_ok(key, c.method, tid):
                continue
            offer(tid, c.base_conf * mult, c.method, c.text)

        # хэштеги: слитное написание
        for h in hashtags(title, description):
            if h in GENERIC_TAGS or len(h) < 5 or NUMERIC.match(h):
                continue
            ids = self.squash_index.get(squash(h)) or self.norm_index.get(squash(h))
            if not ids:
                continue
            tid, bonus = self._pick(list(ids), None)
            label_norm = norm(self.meta.get(tid, (None, 0, None, ""))[3]) or squash(h)
            sq = squash(h)
            if self.is_banned(sq) or self.is_banned(label_norm) or sq in STOP_NORMS or sq in GENERIC_TAGS:
                continue
            if not self._generic_ok(label_norm, "hashtag", tid) or (" " not in label_norm and not self._generic_ok(sq, "hashtag", tid)):
                continue
            conf = 0.8 if len(squash(h)) >= 7 else 0.7
            if all(t in STOP_NORMS or t in FUNCTION_WORDS for t in label_norm.split()):
                conf -= 0.25
            offer(tid, conf + bonus, "hashtag", "#" + h)
            texts.append("#" + h)

        # словарный проход по тексту: совпадение считается только если фраза написана с заглавной буквы
        title_toks = _tokens(strip_hashtags(title or ""))
        desc_toks = _tokens(strip_hashtags((description or "")[:400]))
        orig = title_toks + desc_toks
        toks = [t.lower().replace("ё", "е") for t in orig]
        title_len = len(title_toks)
        caps_title = sum(1 for t in title_toks if len(t) > 1 and t.isupper()) >= max(2, 0.6 * len(title_toks))
        yh = year_hint(title or "")
        covered: set[int] = set()
        for n in range(6, 0, -1):
            for i in range(0, len(toks) - n + 1):
                span = range(i, i + n)
                if any(j in covered for j in span):
                    continue
                key = " ".join(toks[i:i + n])
                ids = self.norm_index.get(key)
                if not ids:
                    continue
                tid, bonus = self._pick(list(ids), yh)
                if self.is_banned(key) or not self._generic_ok(key, "text", tid):
                    continue
                in_title = i < title_len
                titlecase = orig[i][:1].isupper() and not (in_title and caps_title and n == 1)
                at_start = i == 0 or i == title_len
                if n == 1:
                    if not titlecase or at_start:
                        continue
                    conf = 0.5
                elif n == 2:
                    if not titlecase:
                        continue
                    conf = 0.62 if not at_start else 0.58
                else:
                    conf = 0.72 if titlecase else 0.68
                if all(t in STOP_NORMS or t in FUNCTION_WORDS for t in key.split()):
                    conf -= 0.1
                offer(tid, conf + bonus, "text", key)
                covered.update(span)

        if channel_default and channel_default in self.meta:
            offer(channel_default, 0.75, "channel", "канал")

        m = Match(candidates=texts)
        if not scored:
            return m
        ranked = sorted(scored.items(), key=lambda kv: -kv[1][0])
        tid, (conf, method, evidence) = ranked[0]
        m.title_id, m.conf, m.method, m.evidence = tid, conf, method, evidence
        m.alternatives = [(i, s[0]) for i, s in ranked[1:4]]
        return m

    def search(self, q: str, limit: int = 12) -> list[tuple[str, float]]:
        """Поиск произведения по строке для ручной привязки."""
        nz = norm(q)
        if not nz:
            return []
        out: dict[str, float] = {}
        with self.lock:
            for key, ids in self.norm_index.items():
                if key.startswith(nz) or (len(nz) >= 4 and nz in key):
                    for tid in ids:
                        out[tid] = max(out.get(tid, 0), 100.0 if key == nz else 90.0 - min(20, len(key) - len(nz)))
                    if len(out) > 200:
                        break
            if len(out) < limit and len(nz) >= 4:
                choices = []
                for ln in range(max(2, len(nz) - 4), len(nz) + 5):
                    choices.extend(self.keys_by_len.get(ln, ()))
                for key, score, _ in process.extract(nz, choices, scorer=fuzz.ratio, limit=limit * 2, score_cutoff=70):
                    for tid in self.norm_index.get(key, ()):
                        out[tid] = max(out.get(tid, 0), float(score) - 5)
            ranked = sorted(out.items(), key=lambda kv: (-kv[1], -self.meta.get(kv[0], (None, 0))[1]))
        return ranked[:limit]


def result_json(m: Match) -> str:
    return json.dumps({"candidates": m.candidates[:12], "alternatives": m.alternatives}, ensure_ascii=False)
