"""Конвейер: Кино-тренды → auto-shorts → YouTube.

Кино-тренды находят и проверяют материал (кино по календарю, видео и шортсы под перезалив), auto-shorts режет, оформляет
авторским слоем и баннером, проводит ревью и заливает. Этот модуль склеивает их через API auto-shorts (вход оператора,
cookie-сессия и CSRF):

  new → sent (источник создан: POST /sources/from-url с типом прав) → cutting (клипы: для шортса один клип 0…конец без
  нарезки, для длинного видео лучшие кандидаты модели) → review (ждём ревью в auto-shorts) или сразу approved при
  автоодобрении → publishing (пакет поставлен на автозалив) → published (есть публикация, подтягиваем просмотры и удержание).

Исходник для auto-shorts: прямой mp4 из нашего хранилища (YouTube с этой машины не качается), поэтому перед отправкой
файл готовится (file_pending). Права: Creative Commons уходит как cc_by с указанием автора, свободное как public_domain,
остальное только с типом из настройки pipeline_rights_default, иначе задание ждёт решения оператора (needs_rights).
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx

from . import files, popular
from .db import dumps, get_meta, loads, now_iso, set_meta

STATES = ("new", "file_pending", "needs_rights", "sent", "cutting", "review", "qc_failed", "approved", "publishing", "published", "failed", "cancelled")
ACTIVE = ("new", "needs_rights", "file_pending", "retry_wait", "sent", "cutting", "review", "approved", "publishing")
START_STATES = ("new", "needs_rights", "file_pending", "retry_wait")  # ещё не в auto-shorts: на них действует pipeline_max_per_tick
WORK_STATES = ("sent", "cutting")  # в производстве: занимают хранилище и воркеры auto-shorts
REVIEW_BACKLOG_ITEMS = 0  # 0 — без ограничения: производство не ждёт приёмку (решение владельца 29.10)


AUTO_RETRY_MAX = 2  # сколько раз конвейер сам перезапускает клип после временного сбоя


def _transient(job: dict) -> bool:
    """Клип упал на временной ошибке (повтор с той же стадии может пройти), а не на браке или правах."""
    err = str(job.get("error") or "")
    # worker_lost — задачу потеряли при перезапуске воркера, сам клип исправен
    return err.startswith("RetryableError") or job.get("error_class") in ("retryable", "network", "tts", "worker_lost")


def _in_work(conn: sqlite3.Connection, row) -> bool:
    """Задание уже тратит ресурсы: в auto-shorts (скачивание, распознавание, нарезка) или его файл качается
    на хранилище. Новое с готовым файлом и ждущее прав — ещё нет."""
    st = row["status"]
    if st in WORK_STATES:
        return True
    if st == "file_pending" and row["source_video_id"]:
        fs = files.status(conn, row["source_video_id"])
        return bool(fs) and fs.get("status") == "running"
    return False
STATE_RU = {"new": "в очереди", "file_pending": "готовится файл", "retry_wait": "ждёт повтора нарезки", "needs_rights": "нужен тип прав", "sent": "источник в auto-shorts", "cutting": "нарезка",
            "review": "ждёт ревью", "qc_failed": "не прошёл QC", "approved": "одобрено, ждёт залива", "publishing": "на автозаливе", "published": "опубликовано",
            "failed": "ошибка", "cancelled": "отменено"}
RIGHTS_TYPES = ("own", "licensed_written", "licensed_platform", "cc_by", "public_domain")
JOB_DONE = ("approved", "published", "rejected", "failed", "cancelled")


class ASError(RuntimeError):
    pass


class AutoShorts:
    """Клиент API auto-shorts: вход по логину оператора, cookie-сессия, CSRF на запись."""

    def __init__(self, base_url: str, login: str, password: str, timeout: float = 60.0) -> None:
        self.base = (base_url or "").rstrip("/")
        self.login_name = login or ""
        self.password = password or ""
        self.csrf: str | None = None
        self.client = httpx.Client(base_url=self.base or "http://127.0.0.1:18300", timeout=timeout)

    @property
    def configured(self) -> bool:
        return bool(self.base and self.login_name and self.password)

    def login(self) -> None:
        r = self.client.post("/api/v1/auth/login", json={"login": self.login_name, "password": self.password})
        if r.status_code != 200:
            raise ASError(f"вход в auto-shorts не удался: HTTP {r.status_code} {r.text[:160]}")
        self.csrf = (r.json() or {}).get("csrf_token")

    def _req(self, method: str, path: str, body=None, params=None, retry: bool = True):
        if self.csrf is None:
            self.login()
        headers = {"X-CSRF-Token": self.csrf} if method != "GET" and self.csrf else {}
        try:
            r = self.client.request(method, path, json=body, params=params, headers=headers)
        except httpx.HTTPError as e:
            raise ASError(f"auto-shorts недоступен: {e.__class__.__name__}") from e
        if r.status_code in (401, 403) and retry:
            self.csrf = None
            return self._req(method, path, body, params, retry=False)
        if r.status_code >= 400:
            try:
                j = r.json()
                detail = (j.get("error") or {}).get("message") or j.get("detail") or r.text[:200]
            except Exception:  # noqa: BLE001
                detail = r.text[:200]
            raise ASError(f"{method} {path}: HTTP {r.status_code}: {detail}")
        return r.json() if r.content else {}

    def get(self, path: str, **params):
        return self._req("GET", path, params=params or None)

    def get_bytes(self, path: str) -> bytes:
        """Файл из API (лист раскадровки): байты; 401 — перелогин и повтор."""
        if self.csrf is None:
            self.login()
        try:
            r = self.client.get(path)
            if r.status_code in (401, 403):
                self.login()
                r = self.client.get(path)
        except httpx.HTTPError as e:
            raise ASError(f"auto-shorts недоступен: {e.__class__.__name__}") from e
        if r.status_code >= 400:
            raise ASError(f"GET {path}: HTTP {r.status_code}")
        return r.content

    def post(self, path: str, body: dict | None = None):
        return self._req("POST", path, body=body or {})

    def health(self) -> dict:
        try:
            r = self.client.get("/api/v1/health/ready")
        except httpx.HTTPError as e:
            return {"status": "недоступен", "detail": e.__class__.__name__}
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            return {"status": f"HTTP {r.status_code}"}

    # --- предметные вызовы ---
    def source_from_url(self, url: str, title: str, niche_id: str | None, rights: dict, options: dict) -> dict:
        return self.post("/api/v1/sources/from-url", {"url": url, "title": title[:200], "language": "ru", "niche_id": niche_id, "rights": rights, "options": options})

    def source(self, sid) -> dict:
        return self.get(f"/api/v1/sources/{sid}")

    def candidates(self, sid) -> list[dict]:
        return (self.get(f"/api/v1/sources/{sid}/candidates") or {}).get("items") or []

    def clips(self, sid, items: list[dict]) -> list[dict]:
        return (self.post(f"/api/v1/sources/{sid}/clips", {"items": items}) or {}).get("items") or []

    def job(self, jid) -> dict:
        return self.get(f"/api/v1/jobs/{jid}")

    def retry_job(self, jid) -> dict:
        """Перезапуск упавшего ролика с его стадии."""
        return self.post(f"/api/v1/jobs/{jid}/retry", {})

    def review(self, jid, decision: str, expected: str, comment: str | None = None, reject_reason: str | None = None) -> dict:
        body = {"decision": decision, "expected_status": expected, "comment": comment}
        if reject_reason:
            body["reject_reason"] = reject_reason
        return self.post(f"/api/v1/jobs/{jid}/review", body)

    def package_of_job(self, jid) -> dict:
        return self.get(f"/api/v1/jobs/{jid}/package")

    def package(self, pid) -> dict:
        return self.get(f"/api/v1/packages/{pid}")

    def checklist(self, pid, code: str, done: bool = True) -> dict:
        """Отметить пункт чек-листа пакета (например content_id — проверка Content ID лицензированного источника)."""
        return self._req("PATCH", f"/api/v1/packages/{pid}/checklist", body={"code": code, "done": done})

    def autopublish(self, pid, now: bool = False, privacy: str = "public") -> dict:
        """now=False: пакет встаёт в ближайший свободный слот плана канала; now=True: заливать немедленно."""
        body = {"privacy": privacy}
        if now:
            body["now"] = True
        return self.post(f"/api/v1/packages/{pid}/autopublish", body)

    def publication(self, pub_id) -> dict:
        return self.get(f"/api/v1/publications/{pub_id}")

    def channels(self) -> list[dict]:
        return (self.get("/api/v1/channels") or {}).get("items") or []


_CLIENTS: dict[tuple, AutoShorts] = {}
_CLIENTS_LOCK = threading.Lock()


def client_from_settings(settings: dict) -> AutoShorts:
    """Один клиент на адрес и логин: сессия живёт между шагами, иначе auto-shorts режет частые входы (429)."""
    key = (settings.get("autoshorts_url") or "", settings.get("autoshorts_login") or "", settings.get("autoshorts_password") or "")
    with _CLIENTS_LOCK:
        c = _CLIENTS.get(key)
        if c is None:
            c = AutoShorts(*key)
            _CLIENTS.clear()
            _CLIENTS[key] = c
        return c


# ---------- очередь ----------
def _row(conn: sqlite3.Connection, item_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM pipeline_items WHERE id = ?", (item_id,)).fetchone()
    return _hydrate(dict(r)) if r else None


def _hydrate(d: dict) -> dict:
    for k in ("as_jobs", "as_packages", "as_publications"):
        d[k] = loads(d.get(k), [])
    d["metrics"] = loads(d.get("metrics"), None)
    d["status_ru"] = STATE_RU.get(d["status"], d["status"])
    return d


def _set(conn: sqlite3.Connection, item_id: int, **fields) -> None:
    fields["updated_at"] = now_iso()
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE pipeline_items SET {cols} WHERE id = ?", (*fields.values(), item_id))
    conn.commit()


def enqueue(conn: sqlite3.Connection, kind: str, ref_id: str, settings: dict, note: str | None = None) -> dict:
    """Ставит кино (title_id) или видео (video_id) в очередь. Повтор активного задания не создаёт."""
    if kind not in ("film", "video"):
        raise ValueError("kind: film|video")
    active = conn.execute(f"SELECT id FROM pipeline_items WHERE kind = ? AND ref_id = ? AND status IN ({','.join('?' * len(ACTIVE))})", (kind, ref_id, *ACTIVE)).fetchone()
    if active:
        return _row(conn, active["id"])
    # исходник закрыт по странам — ставить его снова бессмысленно, пока не появится другой исходник
    prev = conn.execute("SELECT source_video_id, last_error FROM pipeline_items WHERE kind = ? AND ref_id = ? AND status = 'failed' ORDER BY id DESC LIMIT 1",
                        (kind, ref_id)).fetchone()
    blocked_src = prev["source_video_id"] if prev and prev["last_error"] and ("гео-блок" in prev["last_error"] or "закрыт по странам" in prev["last_error"]) else None
    if kind == "film":
        from . import recommend
        from .stats import Filters
        b = recommend.brief(conn, Filters("30", lang="ru"), ref_id)
        if not b:
            raise ValueError("нет такого произведения")
        cu = b["cutter"]
        if not cu.get("source_url"):
            raise ValueError("нет нового исходника: не найден или уже нарезан")
        if not cu.get("rights_ok"):
            raise ValueError("произведение не проходит проверку прав: " + (cu.get("rights_note") or ""))
        src = b.get("source") or {}
        fields = {"label": b["title"]["label"], "channel": src.get("channel"), "source_url": cu["source_url"], "source_video_id": src.get("video_id"),
                  "direct_url": cu.get("direct_file_url"), "license": None, "is_short": 0, "duration_s": src.get("duration_s"), "hint": cu.get("hint"),
                  "clip_min_s": cu.get("clip_min_s"), "clip_max_s": cu.get("clip_max_s"),
                  "niche_id": settings.get("pipeline_niche_film") or "kino", "channel_slug": settings.get("pipeline_channel_film") or "",
                  "metrics": dumps({"year": b["title"].get("year"), "title_kind": b["title"].get("kind")})}
    else:
        d = popular.detail(conn, ref_id)
        if not d:
            raise ValueError("нет такого ролика")
        if d.get("level") == "no":
            raise ValueError("ролик помечен «не брать»")
        cu = popular.cutter_block(conn, d)
        fields = {"label": d.get("title"), "channel": d.get("channel_name"), "source_url": cu["source_url"], "source_video_id": ref_id,
                  "direct_url": cu.get("direct_file_url"), "license": d.get("license"), "is_short": d.get("is_short"), "duration_s": d.get("duration_s"),
                  "hint": (d.get("analysis") or {}).get("banner_advice"), "clip_min_s": None, "clip_max_s": 58,
                  "niche_id": settings.get("pipeline_niche_video") or "vpn", "channel_slug": settings.get("pipeline_channel_video") or "",
                  "metrics": dumps({"free_zones": (d.get("frames") or {}).get("free_zones") or [],
                                    "text_overlay": bool((d.get("frames") or {}).get("text_overlay")),
                                    "subtitles": bool((d.get("frames") or {}).get("subtitles")),
                                    "orientation": (d.get("frames") or {}).get("orientation") or None})}
    if blocked_src and fields.get("source_video_id") == blocked_src:
        raise ValueError("исходник закрыт по странам для хранилища, нужен другой исходник или прокси")
    ts = now_iso()
    status = "new" if fields["direct_url"] else "file_pending"
    cur = conn.execute("""INSERT INTO pipeline_items(kind, ref_id, label, channel, source_url, source_video_id, direct_url, license, is_short, duration_s, hint,
                          clip_min_s, clip_max_s, niche_id, channel_slug, status, note, metrics, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (kind, ref_id, fields["label"], fields["channel"], fields["source_url"], fields["source_video_id"], fields["direct_url"], fields["license"],
                        fields["is_short"], fields["duration_s"], fields["hint"], fields["clip_min_s"], fields["clip_max_s"], fields["niche_id"], fields["channel_slug"],
                        status, note, fields.get("metrics"), ts, ts))
    conn.commit()
    item_id = cur.lastrowid
    # файл на хранилище здесь не заказываем: его закажет тик, когда освободится место в работе
    # (pipeline_max_in_work) — иначе ночной календарь разом качал два десятка фильмов и забивал диск
    return _row(conn, item_id)


TOPUP_EVERY_MIN = 20


def top_up(conn: sqlite3.Connection, settings: dict, rows: list | None = None, log=print) -> int:
    """Запас конвейера. Календарь трендов почти весь из фильмов без исходника (голливуд на YouTube целиком не лежит:
    под «Матрицей» — обзоры и чужое кино), и 01.11 конвейер давал 2 ролика в день на канал. План владельца —
    4 в день на КАЖДОМ канале: когда ждущих запуска мало, добираем произведения с проверенным ещё не нарезанным
    исходником (у сериала — новая серия), выше — что сильнее в трендах. Запреты те же, что у календаря."""
    if (settings.get("pipeline_topup") or "1") == "0":
        return 0
    last = get_meta(conn, "pipeline_topup_at")
    if last and last > (datetime.now(timezone.utc) - timedelta(minutes=TOPUP_EVERY_MIN)).strftime("%Y-%m-%dT%H:%M:%SZ"):
        return 0
    set_meta(conn, "pipeline_topup_at", now_iso())
    if rows is None:
        rows = conn.execute(f"SELECT * FROM pipeline_items WHERE status IN ({','.join('?' * len(ACTIVE))})", ACTIVE).fetchall()
    waiting = sum(1 for r in rows if r["status"] in ("new", "file_pending", "retry_wait"))
    publishing = sum(1 for r in rows if r["status"] == "publishing" and r["kind"] == "film")
    want = int(settings.get("pipeline_topup_waiting") or 4) - waiting
    # фильмов на автозаливе с запасом (≈5 роликов каждый, 4 канала × 4 в день) — больше не набираем, диск и стенд не резиновые
    if want <= 0 or publishing >= int(settings.get("pipeline_topup_publishing_max") or 14):
        return 0
    from . import outcomes, recommend, sources
    from .stats import Filters, films
    busy = {r["ref_id"] for r in rows}
    ids = [r[0] for r in conn.execute("SELECT DISTINCT title_id FROM title_sources WHERE verified = 1") if r[0] not in busy]
    if not ids:
        return 0
    bad = outcomes.banned_title_ids(conn, ids)
    if (settings.get("exclude_soviet") or "1") != "0":
        bad |= sources.soviet_titles(conn, ids)
    rank = {g["title_id"]: i for i, g in enumerate(films(conn, Filters("30", lang="ru"), limit=1000, sort="score")["items"])}
    kinds = {r[0]: r[1] for r in conn.execute(f"SELECT id, kind FROM titles WHERE id IN ({','.join('?' * len(ids))})", ids)}
    pool = []
    for tid in ids:
        if tid in bad:
            continue
        fresh = [x for x in recommend.fresh_sources(conn, tid, sources.get_sources(conn, tid), kinds.get(tid)) if x["verified"]]
        if fresh:
            pool.append((rank.get(tid, 10**6), tid))
    added = tried = 0
    for _, tid in sorted(pool):
        if added >= want or tried >= 30:
            break
        tried += 1
        try:
            r = enqueue(conn, "film", tid, settings, note="запас: проверенный исходник")
        except ValueError:
            continue
        if r["status"] in ("new", "file_pending") and r["ref_id"] not in busy:
            busy.add(tid)
            added += 1
            log(f"конвейер: запас — «{r.get('label')}» в очередь (проверенный исходник, ещё не нарезан)")
    return added


def enqueue_today(conn: sqlite3.Connection, settings: dict) -> dict:
    """Всё, что на сегодня: слоты календаря кино и подборка видео."""
    from . import schedule
    out = {"queued": [], "skipped": []}
    # поток в приёмку: берём слоты календаря и на дни вперёд (лимиты производства и приёмки держит тик)
    ahead = max(0, min(6, int(settings.get("pipeline_enqueue_days_ahead") or 0)))
    v = schedule.view(conn, days=1 + ahead, back=0)
    soviet: set[str] = set()
    all_ids = [it["title_id"] for d in v["days"] for it in d["items"]]
    if (settings.get("exclude_soviet") or "1") != "0":
        from .sources import soviet_titles
        soviet = soviet_titles(conn, all_ids)
    from . import outcomes
    banned = outcomes.banned_title_ids(conn, all_ids)
    for d in v["days"]:
        for it in d["items"]:
            if not it["title_id"] or it["status"] != "planned":
                continue
            if it["title_id"] in soviet:
                out["skipped"].append({"kind": "film", "ref_id": it["title_id"], "label": it["label"], "why": "советское кино не берём"})
                continue
            if it["title_id"] in banned:
                out["skipped"].append({"kind": "film", "ref_id": it["title_id"], "label": it["label"], "why": "правообладатель блокирует или уже блокировал у нас"})
                continue
            try:
                r = enqueue(conn, "film", it["title_id"], settings, note=f"календарь {d['day']}")
                out["queued"].append({"kind": "film", "ref_id": it["title_id"], "label": it["label"], "id": r["id"], "status": r["status"]})
            except ValueError as e:
                out["skipped"].append({"kind": "film", "ref_id": it["title_id"], "label": it["label"], "why": str(e)})
    # подборка роликов из чартов YouTube (игры, влоги) — не для кино-канала: только если явно включено
    # и для неё задан отдельный канал (pipeline_channel_video не совпадает с каналом кино)
    videos_on = settings.get("pipeline_enqueue_videos", "0") == "1" and (settings.get("pipeline_channel_video") or "") not in ("", settings.get("pipeline_channel_film") or "")
    for p in (popular.picks(conn) if videos_on else []):
        try:
            r = enqueue(conn, "video", p["video_id"], settings, note="подборка")
            out["queued"].append({"kind": "video", "ref_id": p["video_id"], "label": p["title"], "id": r["id"], "status": r["status"]})
        except ValueError as e:
            out["skipped"].append({"kind": "video", "ref_id": p["video_id"], "label": p["title"], "why": str(e)})
    return out


_KIND_RU = {"film": "фильма", "animated": "мультфильма", "series": "сериала", "animated_series": "мультсериала", "tv": "шоу"}


def _public_url(url: str | None, settings: dict) -> str | None:
    """Ссылка годится для публичного описания только если это YouTube; адрес хранилища и прямые mp4 — никогда."""
    u = (url or "").strip()
    if not u or not ("youtube.com/" in u or "youtu.be/" in u):
        return None
    host = (settings.get("public_url") or "").split("://")[-1].split("/")[0]
    if host and host in u:
        return None
    return u


def attribution_text(item: dict, rights_type: str, settings: dict) -> str | None:
    """Публичная строка «откуда фрагмент» для описания ролика. Без служебных ссылок и без «Источник:» —
    это слово добавляет auto-shorts. Для CC BY ссылка на оригинал обязательна по лицензии."""
    owner = (item.get("channel") or "").strip()
    label = (item.get("label") or "").strip()
    if rights_type == "own":
        return None
    if rights_type == "cc_by":
        url = _public_url(item.get("source_url"), settings)
        return f"автор {owner or 'неизвестен'}" + (f" ({url})" if url else "") + ", лицензия CC BY"
    m = loads(item.get("metrics"), {}) if isinstance(item.get("metrics"), str) else (item.get("metrics") or {})
    if item.get("kind") == "film" and label:
        kind = _KIND_RU.get(str((m or {}).get("title_kind") or "film"), "фильма")
        year = (m or {}).get("year")
        return f"фрагмент {kind} «{label}»" + (f" ({year})" if year else "")
    if owner:
        return f"фрагмент видео канала «{owner}»"
    return f"фрагмент видео «{label}»" if label else None


def rights_for(item: dict, settings: dict) -> dict | None:
    """Права для источника в auto-shorts. None = нужен выбор оператора."""
    url = item.get("source_url") or ""
    owner = item.get("channel") or "неизвестен"
    if item.get("license") == "creativeCommon":
        return {"rights_type": "cc_by", "owner_name": owner, "proof_note": f"YouTube показывает лицензию Creative Commons: {url}",
                "attribution_text": attribution_text(item, "cc_by", settings), "license": "CC BY", "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "owner_channel_url": None}
    if item.get("rights_type") in RIGHTS_TYPES:
        rt = item["rights_type"]
    else:
        rt = (settings.get("pipeline_rights_default") or "").strip()
        if rt not in RIGHTS_TYPES:
            return None
    note = (settings.get("pipeline_rights_note") or "").strip() or f"тип прав задан в Кино-трендах ({rt})"
    ref = str(item.get("ref_id") or "")
    if item.get("kind") == "film" and re.fullmatch(r"Q\d+", ref):
        # auto-shorts берёт по нему справку из Википедии для подсказки и факта после ответа (film_ref.py)
        note += f"; wikidata={ref}"
    return {"rights_type": rt, "owner_name": owner, "proof_note": note, "attribution_text": attribution_text(item, rt, settings),
            "license": None, "license_url": None, "owner_channel_url": None}


def set_rights(conn: sqlite3.Connection, item_id: int, rights_type: str) -> dict | None:
    if rights_type not in RIGHTS_TYPES:
        raise ValueError("rights_type: " + "|".join(RIGHTS_TYPES))
    it = _row(conn, item_id)
    if not it:
        return None
    _set(conn, item_id, rights_type=rights_type, status="new" if it["status"] == "needs_rights" else it["status"], last_error=None)
    return _row(conn, item_id)


# сцена 22–40 с (цель 32): с вопросом, ответом викторины и рекламной вставкой ролик ~45 с — длинные шортсы
# с паузами досматривают хуже (29.10). Короче 22 нельзя: при потолке 25–30 сцены обрывались без развязки.
# Настройки pipeline_clip_min_sec / _target_sec / _max_sec перекрывают.
CLIP_MIN_SEC, CLIP_TARGET_SEC, CLIP_MAX_SEC = 22, 32, 40


def _options(item: dict, settings: dict) -> dict:
    """Параметры отбора моментов. Длина — под законченную сцену (завязка → развязка), а не под «модную»
    длину шортсов этого произведения: 29.10 потолок 25–30 с резал сцены до обрывков без развязки."""
    n = max(1, int(settings.get("pipeline_clips_per_source") or 3))
    if item.get("kind") == "film":
        cmin = int(settings.get("pipeline_clip_min_sec") or CLIP_MIN_SEC)
        target = int(settings.get("pipeline_clip_target_sec") or CLIP_TARGET_SEC)
        cmax = int(settings.get("pipeline_clip_max_sec") or CLIP_MAX_SEC)
    else:
        cmax = min(58, int(item.get("clip_max_s") or 58))
        cmin = max(15, min(int(item.get("clip_min_s") or 15), cmax - 5))
        target = max(cmin, min(cmax, 30))
    return {"selector": "auto", "target_clip_sec": target, "min_clip_sec": cmin, "max_clip_sec": cmax,
            "max_candidates": max(12, n * 2), "target_count": n, "detail_pass": True, "tighten_pauses": False}


def pick_banner_variant(zones: list[str] | None, settings: dict) -> int | None:
    """Вариант баннера по свободным зонам кадра из анализа Кино-трендов: низ свободен → нижний, иначе верх свободен → верхний."""
    def _id(key: str) -> int | None:
        try:
            return int(settings.get(key) or 0) or None
        except (TypeError, ValueError):
            return None
    bottom, top = _id("pipeline_banner_bottom"), _id("pipeline_banner_top")
    z = " ".join(zones or []).lower()
    if not z:
        return bottom
    if "низ" in z or not top:
        return bottom
    if "верх" in z:
        return top
    return bottom


SHORT_TITLE_TOP = 560  # у шортсов с чужими надписями свой заголовок уводим из верхней трети, где обычно стоит хук автора ролика
SHORT_ONSCREEN_TOP = 740  # тезисы — сразу под заголовком (полоса субтитров у шортсов свободна: своих субтитров нет)


def _job_options(item: dict, settings: dict) -> dict:
    """Параметры ролика для auto-shorts (тело ClipBatchItem, лишние поля там запрещены → 422): баннер по свободным
    зонам, у вертикальных шортсов — без своих субтитров (у них свои вшитые) и заголовок с тезисами ниже чужого хука.
    Нишу ролик берёт у источника (source_from_url передаёт niche_id задания), в заказе клипа её нет."""
    m = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    zones = m.get("free_zones")
    is_short = item.get("is_short") == 1 or m.get("orientation") == "vertical"
    # у вертикальных шортсов почти всегда свои вшитые субтитры и хук сверху: наши субтитры с ними сливаются в кашу,
    # поэтому текст у нас — только заголовок и тезисы (ниже чужого хука), а голос автора идёт поверх
    layer = {"mode": "llm_draft", "captions": "none" if is_short else "both", "voice": "tts", "hook_text": None, "title_overlay": None}
    if is_short:
        layer["layout"] = {"mode": "manual", "title_top": SHORT_TITLE_TOP, "onscreen_top": SHORT_ONSCREEN_TOP}
    opts = {"channel_id": item.get("channel_slug") or "", "priority": 100, "start": True, "reframe": {"mode": "auto"}, "author_layer": layer}
    variant = pick_banner_variant(zones, settings)
    if variant:
        opts["banner_variant_id"] = variant
    return opts


def _autopublish(asc: "AutoShorts", item: dict, settings: dict, pid, log=print) -> None:
    """Поставить пакет в автозалив. Лицензированный источник: пункт «Content ID» чек-листа закрывает проверка
    прав Кино-трендов (сносы чужих нарезок, правообладатель, заявки Content ID) — без отметки автозалив заблокирован."""
    if (item.get("rights_type") or settings.get("pipeline_rights_default") or "").startswith("licensed_"):
        try:
            asc.checklist(pid, "content_id", True)
        except ASError as e:
            log(f"конвейер: чек-лист content_id для пакета {pid}: {e}", "warn")
    asc.autopublish(pid, now=settings.get("pipeline_publish_now", "0") == "1", privacy=settings.get("pipeline_privacy") or "public")


CONTENT_CHECK_MAX_TRIES = 12  # модель не отвечает час — задание падает с понятной причиной


def _content_gate(conn: sqlite3.Connection, asc: "AutoShorts", item: dict, settings: dict, log=print):
    """Исходник фильма проверен по содержанию (русская речь, само произведение)? True — дальше; иначе статус."""
    from . import content_check, sources
    from .llm import llm_from_settings

    m = dict(item.get("metrics") or {})
    cc = m.get("content_check") or {}
    if item.get("kind") == "film" and not m.get("runtime_ok"):
        # одноимённая экранизация другой длины («Отверженные»: 109 мин вместо 158) — не тот фильм и не тот год
        kind = (conn.execute("SELECT kind FROM titles WHERE id = ?", (item["ref_id"],)).fetchone() or {"kind": None})["kind"]
        bad = sources.runtime_mismatch(conn, item["ref_id"], kind, item.get("duration_s"))
        if bad:
            return _reject_source(conn, asc, item, m, bad, log)
        m["runtime_ok"] = True
        item["metrics"] = m
        _set(conn, item["id"], metrics=dumps(m))
    if cc.get("ok") is True:
        return _frames_gate(conn, asc, item, settings, m, log)
    tries = int(cc.get("tries") or 0)
    try:
        t = asc.get(f"/api/v1/sources/{item['as_source_id']}/transcript")
    except ASError as e:
        log(f"конвейер: «{item.get('label')}»: расшифровка для проверки: {e}", "warn")
        return item["status"]
    sents = [str(x.get("text") or "") for x in (t.get("sentences") or [])]
    src_row = conn.execute("SELECT label, channel FROM title_sources WHERE title_id = ? AND video_id = ?",
                           (item["ref_id"], item.get("source_video_id"))).fetchone() if item.get("source_video_id") else None
    hint = f"«{src_row['label']}», канал «{src_row['channel']}»" if src_row else (f"канал «{item.get('channel')}»" if item.get("channel") else None)
    res = content_check.check_transcript(llm_from_settings(settings), item.get("label") or "", m.get("year"), m.get("title_kind"), sents,
                                         duration_sec=t.get("duration_sec"), video_hint=hint, words=t.get("words"))
    res.update(tries=tries + 1, at=now_iso())
    m["content_check"] = res
    if res["ok"] is None:
        if tries + 1 >= CONTENT_CHECK_MAX_TRIES:
            _set(conn, item["id"], status="failed", metrics=dumps(m), last_error="исходник не проверен: модель не отвечала час, повторите позже")
            return "failed"
        _set(conn, item["id"], metrics=dumps(m))
        return item["status"]
    if res["ok"] is False:
        return _reject_source(conn, asc, item, m, res["reason"], log)
    item["metrics"] = m
    _set(conn, item["id"], metrics=dumps(m))
    log(f"конвейер: «{item.get('label')}»: исходник проверен по содержанию ({res.get('what') or 'язык'})")
    return _frames_gate(conn, asc, item, settings, m, log)


def _purge_source(asc: "AutoShorts", item: dict, log=print) -> None:
    """Клипы фильма решены (или вышли) — исходник на стенде больше не нужен: диск стенда 30 ГБ, при свободных меньше
    pipeline_min_free_gb новые фильмы не берутся (01.11 стояло 6,7 ГБ из-за 28 отработанных исходников).
    Готовые ролики (jobs/…/final.mp4), публикации и сбор просмотров от исходника не зависят; пока из исходника
    режутся клипы, auto-shorts удалять не даст (409)."""
    if not item.get("as_source_id"):
        return
    try:
        asc._req("DELETE", f"/api/v1/sources/{item['as_source_id']}", params={"purge_files": "true"})
    except ASError as e:
        if "HTTP 404" not in str(e):  # уже удалён при одобрении клипов
            log(f"конвейер: «{item.get('label')}»: исходник {item['as_source_id']} на стенде не удалён: {e}", "warn")
        return
    log(f"конвейер: «{item.get('label')}»: клипы готовы — исходник {item['as_source_id']} удалён со стенда")


# ошибки, после которых исходник не оживёт: ролик закрыт/удалён (Rutube 403/404 и через мак из России), сборник длиннее 3 ч.
# 01.11: «Великолепный век» с закрытого ролика Rutube падал 8 раз подряд — календарь брал тот же исходник
DEAD_SOURCE = re.compile(r"\[rutube\][^\n]*HTTP Error 40[34]|Video unavailable|Private video|has been removed|no longer available|длиннее 3 ч", re.I)


def _forget_dead_source(conn: sqlite3.Connection, item: dict, error: str, log=print) -> None:
    if item.get("kind") != "film" or not item.get("source_video_id") or not DEAD_SOURCE.search(error or ""):
        return
    from . import sources
    sources.mark_bad(conn, item["ref_id"], item["source_video_id"], "исходник не отдаётся: " + error[:120])
    log(f"конвейер: «{item.get('label')}»: исходник {item['source_video_id']} закрыт — больше не берём")


def _reject_source(conn: sqlite3.Connection, asc: "AutoShorts", item: dict, m: dict, reason: str, log=print) -> str:
    """Исходник не годится: запрет навсегда (не вернётся при новом поиске), удалить из auto-shorts, задание — failed."""
    from . import sources
    if item.get("source_video_id"):
        sources.mark_bad(conn, item["ref_id"], item["source_video_id"], reason)
    try:
        asc._req("DELETE", f"/api/v1/sources/{item['as_source_id']}", params={"purge_files": "true"})
    except ASError as e:
        log(f"конвейер: удалить негодный исходник {item['as_source_id']}: {e}", "warn")
    _set(conn, item["id"], status="failed", metrics=dumps(m), last_error=f"исходник не годится: {reason} — в следующий раз возьмём другой")
    log(f"конвейер: «{item.get('label')}»: исходник отбракован — {reason}")
    return "failed"


def _visual_check(asc: "AutoShorts", item: dict, settings: dict) -> dict:
    """Разрешение и кадры исходника: мыло, игра, реакция, зеркало, окно на фоне. ok: True/False/None (не проверено)."""
    from . import content_check
    from .llm import llm_from_settings

    sid = item["as_source_id"]
    src = asc.source(sid)
    res = content_check.check_resolution(src.get("width"), src.get("height"))
    if res["ok"] is False:
        return res
    try:
        sheets = (asc.get(f"/api/v1/sources/{sid}/storyboard") or {}).get("sheets") or []
    except ASError as e:
        return {"ok": None, "reason": f"раскадровки нет: {e}"}
    if not sheets:
        return {"ok": None, "reason": "раскадровки нет"}
    m = dict(item.get("metrics") or {})
    llm = llm_from_settings(settings)
    for i in sorted({len(sheets) // 5, (len(sheets) * 3) // 5}):
        try:
            img = asc.get_bytes(sheets[i])
        except ASError as e:
            return {"ok": None, "reason": f"лист раскадровки: {e}"}
        r = content_check.check_frames(llm, item.get("label") or "", m.get("year"), m.get("title_kind"), img)
        if r.get("ok") is not True:
            return r
    return {"ok": True, "reason": "кадры в порядке", "size": res.get("size")}


def _frames_gate(conn: sqlite3.Connection, asc: "AutoShorts", item: dict, settings: dict, m: dict, log=print):
    """Вторая ступень проверки исходника — по кадрам. True — резать; иначе статус задания."""
    vc = m.get("visual_check") or {}
    if vc.get("ok") is True:
        return True
    tries = int(vc.get("tries") or 0) + 1
    res = _visual_check(asc, item, settings)
    res.update(tries=tries, at=now_iso())
    if res.get("ok") is None and tries >= CONTENT_CHECK_MAX_TRIES:
        res.update(ok=True, reason=f"кадры не проверены ({res.get('reason')}): пропускаем, чтобы не стоять")
    m["visual_check"] = res
    item["metrics"] = m
    if res.get("ok") is None:
        _set(conn, item["id"], metrics=dumps(m))
        return item["status"]
    if res.get("ok") is False:
        return _reject_source(conn, asc, item, m, res["reason"], log)
    _set(conn, item["id"], metrics=dumps(m))
    log(f"конвейер: «{item.get('label')}»: кадры исходника в порядке ({res.get('size') or ''})")
    return True


def _late_frames_gate(conn: sqlite3.Connection, asc: "AutoShorts", item: dict, settings: dict, log=print) -> str | None:
    """Фильмы, нарезанные до проверки по кадрам (29.10): проверить один раз; брак — снять ещё не одобренные ролики."""
    m = dict(item.get("metrics") or {})
    if (m.get("visual_check") or {}).get("ok") is True or not item.get("as_source_id"):
        return None
    tries = int((m.get("visual_check") or {}).get("tries") or 0) + 1
    from . import content_check
    try:
        sq = content_check.speech_quality((asc.get(f"/api/v1/sources/{item['as_source_id']}/transcript") or {}).get("words"))
    except ASError:
        sq = {"ok": None}
    res = {"ok": False, "reason": sq["reason"]} if sq.get("ok") is False else _visual_check(asc, item, settings)
    res.update(tries=tries, at=now_iso(), late=True)
    if res.get("ok") is None and tries >= CONTENT_CHECK_MAX_TRIES:
        res.update(ok=True, reason="кадры не проверены: пропускаем")
    m["visual_check"] = res
    item["metrics"] = m  # дальше в этом шаге метрики пишутся из item — не затирать проверку старым снимком
    if res.get("ok") is not False:
        _set(conn, item["id"], metrics=dumps(m))
        return None
    for jid in item.get("as_jobs") or []:
        try:
            j = asc.get(f"/api/v1/jobs/{int(jid)}")
            if j.get("status") not in ("approved", "published", "cancelled", "rejected"):
                asc.post(f"/api/v1/jobs/{int(jid)}/cancel", {})
        except ASError as e:
            log(f"конвейер: снять ролик {jid}: {e}", "warn")
    return _reject_source(conn, asc, item, m, res["reason"], log)


def _channel_targets(asc: "AutoShorts", item: dict, settings: dict, jopts: dict) -> list[tuple[str, int | None]]:
    """Каналы для роликов фильма: основной (канал задания) и второй (`pipeline_channel_film_2`). Стикер/баннер —
    по каналу (`pipeline_banner_by_channel`). Подключение к YouTube проверяется при заливке, а не здесь:
    ролик второго канала собирается и ждёт приёмки, даже пока канал не подключён."""
    from .db import loads as _loads

    by_channel = _loads(settings.get("pipeline_banner_by_channel"), {}) or {}
    main = item.get("channel_slug") or jopts.get("channel_id") or ""
    out = [(main, by_channel.get(main) or jopts.get("banner_variant_id"))]
    if item.get("kind") != "film":
        return out
    # все каналы ротации (pipeline_channels_film через запятую; по старой настройке — основной + второй).
    # Баннер не задан в pipeline_banner_by_channel — auto-shorts поставит баннер канала по умолчанию.
    listed = [c.strip() for c in (settings.get("pipeline_channels_film") or "").split(",") if c.strip()]
    if not listed:
        listed = [x for x in (main, (settings.get("pipeline_channel_film_2") or "").strip()) if x]
    ready_cache: dict = {}
    for ch in listed:
        if ch != main and ch not in (c for c, _ in out):
            # канал ротации ещё не подключён к YouTube — ролики на него не режем: начнут идти сами после входа
            if asc is not None and not _channel_ready(asc, ch, ready_cache):
                continue
            out.append((ch, by_channel.get(ch)))
    return out


# Темы каналов (владелец 29.10: «у каждого канала своя тема» — YouTube подбирает каналу зрителей по похожести
# роликов). Подстроки жанров Wikidata; kinds — бонус за тип произведения. pipeline_channel_themes (JSON) перекрывает.
CHANNEL_THEMES: dict[str, dict] = {
    "emerald": {"genres": ["боевик", "фантаст", "супергер", "ужас", "триллер", "фэнтез", "приключ", "эпическ", "военн", "слэшер"],
                "kinds": ["film"]},
    "bpn-shorts": {"genres": ["комед", "ситком", "мульт", "анимац", "пародий"], "kinds": ["animated_series", "animated"]},
    "kadr-dnya": {"genres": ["драм", "мелодрам", "мыльн", "романтич", "историческ"], "kinds": ["series"]},
    "myhostbox-2": {"genres": ["криминал", "детектив", "полицейск", "нуар", "мистери", "ограблен"], "kinds": []},
}
CHANNEL_BACKLOG_MAX = 6  # фильмов в работе на канале; больше — следующий подходящий или самый свободный
CHANNEL_SPREAD = 1  # тема канала решает только среди каналов, у которых фильмов в работе не больше минимума + 1


def _channel_load(conn: sqlite3.Connection, slug: str) -> int:
    # qc_failed — фильм кончился (клипы не прошли проверку качества), канал он больше не занимает
    return sum(1 for r in conn.execute("SELECT metrics FROM pipeline_items WHERE kind = 'film' AND status NOT IN ('published', 'cancelled', 'rejected', 'failed', 'qc_failed')")
               if (loads(r["metrics"], {}) or {}).get("channel") == slug)


HUNGRY_MIN = 2         # роликов дефицита на сегодня-завтра, с которого канал «голодный»
HUNGRY_FILM_CLIPS = 5  # столько роликов в среднем даёт фильм в нарезке


def _hungry(conn: sqlite3.Connection) -> dict[str, int]:
    from . import guard
    return guard.hungry(conn)


def _channel_pending(conn: sqlite3.Connection, slug: str) -> int:
    """Фильмов канала, которые ещё режутся (ролики от них будут, но в плане их пока нет)."""
    return sum(1 for r in conn.execute("SELECT metrics FROM pipeline_items WHERE kind = 'film' AND status IN "
                                       "('new', 'file_pending', 'retry_wait', 'sent', 'cutting', 'review')")
               if (loads(r["metrics"], {}) or {}).get("channel") == slug)


def _theme_score(theme: dict, kind: str | None, genres: list[str]) -> float:
    g = " ".join(genres).lower()
    return sum(1 for x in theme.get("genres") or [] if x in g) + (0.5 if kind in (theme.get("kinds") or []) else 0)


def _film_channel(conn: sqlite3.Connection, item: dict, targets: list[tuple[str, int | None]], settings: dict | None = None) -> tuple[str, int | None] | None:
    """Канал для всех клипов фильма. Произведение уже идёт или выходило на канале (другая серия, другой исходник) —
    туда же: одно кино на двух наших каналах YouTube считает сетью перезаливов (01.11: «Викинги» на двух каналах).
    Иначе — тема канала (жанры), но только среди самых свободных (разница ≤ pipeline_channel_spread фильмов в работе):
    план — 4 ролика в день на КАЖДОМ канале, канал без фильмов по своей теме не голодает. Каналы, которые YouTube
    прижал (outcomes.channel_health = suppressed), пропускаются; прижаты все — None (фильм ждёт)."""
    by_slug = dict(targets)
    if (settings or {}).get("pipeline_channel_gate", "1") != "0":
        from . import outcomes
        health = outcomes.channel_health(conn, settings)
        by_slug = {k: v for k, v in by_slug.items() if (health.get(k) or {}).get("status") != "suppressed"}
        targets = [t for t in targets if t[0] in by_slug]
        if not targets:
            return None
    pinned = (item.get("metrics") or {}).get("channel") if isinstance(item.get("metrics"), dict) else None
    if pinned in by_slug:
        return pinned, by_slug[pinned]  # канал закреплён за заданием (вручную: «этот фильм — на Аниме-момент»)
    for r in conn.execute("SELECT metrics FROM pipeline_items WHERE kind = 'film' AND ref_id = ? AND id != ? "
                          "AND status NOT IN ('cancelled', 'rejected', 'failed') ORDER BY id DESC", (item.get("ref_id"), item.get("id") or -1)):
        ch = (loads(r["metrics"], {}) or {}).get("channel")
        if ch in by_slug:
            return ch, by_slug[ch]
    from . import outcomes
    outcomes.init(conn)
    prev = conn.execute("SELECT channel_id FROM pub_outcomes WHERE title_id = ? AND channel_id IS NOT NULL ORDER BY published_at DESC LIMIT 1",
                        (item.get("ref_id"),)).fetchone()
    if prev and prev[0] in by_slug:
        return prev[0], by_slug[prev[0]]
    themes = loads((settings or {}).get("pipeline_channel_themes"), None) or CHANNEL_THEMES
    t = conn.execute("SELECT kind, genres FROM titles WHERE id = ?", (item.get("ref_id"),)).fetchone()
    kind, genres = (t["kind"], [str(x) for x in loads(t["genres"], []) or []]) if t else (None, [])
    # голодный канал (сторож правил: на сегодня-завтра не хватает роликов до 4 в день) получает фильм первым;
    # фильм уже в нарезке для канала покрывает ≈5 роликов дефицита
    starving = {s: d - HUNGRY_FILM_CLIPS * _channel_pending(conn, s) for s, d in _hungry(conn).items() if s in by_slug}
    starving = {s: d for s, d in starving.items() if d >= HUNGRY_MIN}
    if starving:
        best = max(starving, key=lambda s: (starving[s], _theme_score(themes.get(s) or {}, kind, genres)))
        return best, by_slug[best]
    cap = int((settings or {}).get("pipeline_channel_backlog_max") or CHANNEL_BACKLOG_MAX)
    spread = int((settings or {}).get("pipeline_channel_spread") or CHANNEL_SPREAD)
    load = {slug: _channel_load(conn, slug) for slug in by_slug}
    low = min(load.values())
    pool = [s for s in by_slug if load[s] <= low + spread and load[s] < cap] or [s for s in by_slug if load[s] == low]
    score = {s: _theme_score(themes.get(s) or {}, kind, genres) for s in pool}
    best = max(score.values())
    if best >= 1:
        top = sorted((s for s in pool if score[s] == best), key=lambda s: load[s])
        return top[0], by_slug[top[0]]
    free = [x for x in targets if x[0] in pool and load[x[0]] == min(load[s] for s in pool)]
    return free[_rr_next(conn, 1) % len(free)]


def _rr_next(conn: sqlite3.Connection, n: int) -> int:
    """Счётчик чередования каналов: вернуть текущее значение и сдвинуть на n клипов."""
    r = conn.execute("SELECT value FROM settings WHERE key = 'pipeline_rr_counter'").fetchone()
    cur = int(r[0]) if r and str(r[0]).isdigit() else 0
    conn.execute("INSERT INTO settings(key, value) VALUES ('pipeline_rr_counter', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (str(cur + n),))
    conn.commit()
    return cur


_FMT_CACHE: dict[str, str] = {}


def _channel_fmt(asc: "AutoShorts", ch: str) -> str:
    if ch not in _FMT_CACHE:
        try:
            _FMT_CACHE[ch] = str(((asc.get(f"/api/v1/channels/{ch}") or {}).get("profile") or {}).get("format") or "comment")
        except ASError:
            return "comment"
    return _FMT_CACHE[ch]


def _ai_review(conn: sqlite3.Connection, asc: "AutoShorts", item: dict, iid, jid, job: dict, settings: dict, log=print) -> dict | None:
    """Приёмка нейросетью (ai_review.py) — один раз на клип, результат в metrics.ai_review. None — проверка
    выключена или не удалась (тогда ролик одобряется по QC, как раньше: простой хуже одного слабого ролика)."""
    if settings.get("pipeline_ai_review", "1") == "0":
        return None
    m = dict(item.get("metrics") or {})
    done = dict(m.get("ai_review") or {})
    if str(jid) in done:
        return done[str(jid)]
    from .ai_review import review_video
    from .llm import LLMError
    try:
        video = asc.get_bytes(f"/api/v1/jobs/{jid}/media/final")
        year = (conn.execute("SELECT year FROM titles WHERE id = ?", (item.get("ref_id"),)).fetchone() or [None])[0]
        film = f"«{item['label']}»" + (f" ({year})" if year else "") if item.get("kind") == "film" and item.get("label") else None
        verdict = review_video(video, settings, _channel_fmt(asc, str(job.get("channel_id") or "")), film=film)
    except (ASError, LLMError, OSError, subprocess.SubprocessError, ValueError) as e:
        log(f"конвейер: приёмка нейросетью клипа {jid} не удалась: {e}", "warn")
        return None
    done[str(jid)] = verdict
    m["ai_review"] = done
    item["metrics"] = m
    _set(conn, iid, metrics=dumps(m))
    return verdict


PROBE_HOURS = 1.0  # первый ролик произведения — проба: остальные ждут, пока он провисит столько без блокировки
# (01.11 с 3 ч до 1 ч, pipeline_probe_hours: при плане 4 ролика в день 3 ч не давали новому фильму выйти больше
# двух раз за вечер; блокировка правообладателя приходит при обработке ролика — «Наруто» забанило сразу)


def _probe_hold(conn: sqlite3.Connection, item: dict, settings: dict) -> bool:
    """Держать ли одобренный ролик (решение владельца 29.10: «наруто забанило сразу — учитывай»).
    Первый ролик произведения выходит сразу (проба); остальные — когда итоги (outcomes) покажут, что проба
    провисела PROBE_HOURS без блокировки. Проба заблокирована — держим всё (итоги снимут ролики сами)."""
    if item.get("kind") != "film" or (settings.get("pipeline_probe") or "1") == "0":
        return False
    if not (item.get("metrics") or {}).get("probe_pid"):
        return False  # пробы ещё не было — этот ролик сам станет пробой
    from . import outcomes
    outcomes.init(conn)
    rows = conn.execute("SELECT block, status, age_h FROM pub_outcomes WHERE title_id = ?", (item["ref_id"],)).fetchall()
    if any(r["block"] in ("blocked_ru", "rejected") or (r["block"] == "removed" and r["status"] == "live") for r in rows):
        return True
    hours = float(settings.get("pipeline_probe_hours") or PROBE_HOURS)
    return not any((r["age_h"] or 0) >= hours and r["block"] is None for r in rows)


def _mark_probe(conn: sqlite3.Connection, item: dict, pid) -> None:
    m = dict(item.get("metrics") or {})
    if not m.get("probe_pid"):
        m["probe_pid"] = pid
        item["metrics"] = m
        _set(conn, item["id"], metrics=dumps(m))


def _publish_approved_now(conn: sqlite3.Connection, asc: "AutoShorts", item: dict, settings: dict, statuses: dict, log=print) -> None:
    """Пока соседние клипы фильма ждут приёмки или рендера, уже одобренные ставим в автозалив."""
    pkgs = list(item.get("as_packages") or [])
    cache: dict = {}
    changed = False
    for jid, v in statuses.items():
        if v != "approved":
            continue
        try:
            pk = asc.package_of_job(int(jid))
        except ASError:
            continue  # пакет ещё собирается — на следующем тике
        pid = pk.get("id")
        if pid is None or pk.get("publication") or (pk.get("autopublish") or {}).get("state"):
            if pid is not None and pid not in pkgs:
                pkgs.append(pid); changed = True
            continue
        ch = (pk.get("channel") or {}).get("id") or ""
        if ch and not _channel_ready(asc, ch, cache):
            continue
        if _probe_hold(conn, item, settings):
            continue  # проба произведения ещё не провисела без блокировки
        if _drop_twin(asc, item, ch, pid, pk, jid, cache, log):
            continue
        _autopublish(asc, item, settings, pid, log)
        _mark_probe(conn, item, pid)
        if pid not in pkgs:
            pkgs.append(pid)
        changed = True
        log(f"конвейер: «{item.get('label')}»: клип {jid} одобрен — в план канала {ch or '?'}")
    if changed:
        _set(conn, item["id"], as_packages=dumps(pkgs))
        item["as_packages"] = pkgs


def _drop_twin(asc: "AutoShorts", item: dict, ch: str, pid, pk: dict, jid, cache: dict, log=print) -> bool:
    """Клип повторяет заголовок, уже стоящий в плане канала, — снять задание (оба пути постановки в план).
    True — повтор (в план не ставить)."""
    twin = _twin_title(asc, ch, pid, (pk.get("texts") or {}).get("title") or "", cache)
    if not twin:
        return False
    jid = jid or pk.get("job_id")
    try:
        asc.post(f"/api/v1/jobs/{int(jid)}/cancel", {})
    except (ASError, TypeError, ValueError) as e:
        log(f"конвейер: повтор клипа {jid} не снят: {e}", "warn")
        return True
    log(f"конвейер: «{item.get('label')}»: клип {jid} снят — на канале {ch} уже есть «{twin}»")
    return True


TWIN_RATIO = 0.9  # 0,96 — тот же факт другими словами; 0,83 — шаблон викторины с другой сценой (не повтор)


def _norm_title(t: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", (t or "").lower().replace("ё", "е")).split())


def _twin_title(asc: "AutoShorts", channel_id: str, pid, title: str, cache: dict) -> str | None:
    """Заголовок, почти совпадающий с уже стоящим в плане или вышедшим на канале (01.11: «Такси» трижды с одним фактом).
    Повтор на одном канале — «repetitive content» по правилам YouTube и пустой слот для зрителя."""
    import difflib
    if not channel_id or not title:
        return None
    key = ("titles", channel_id)
    if key not in cache:
        try:
            items = asc.get("/api/v1/packages", channel_id=channel_id, limit=200).get("items") or []
        except ASError:
            items = []
        cache[key] = [(p.get("id"), (p.get("texts") or {}).get("title") or "") for p in items
                      if p.get("publication") or ((p.get("autopublish") or {}).get("state") in ("scheduled", "publishing", "done"))]
    me = _norm_title(title)
    for other_id, other in cache[key]:
        if other_id != pid and difflib.SequenceMatcher(None, me, _norm_title(other)).ratio() >= TWIN_RATIO:
            return other
    cache[key].append((pid, title))
    return None


def _channel_ready(asc: "AutoShorts", channel_id: str, cache: dict) -> bool:
    """Канал подключён к YouTube с правом заливки (кэш на один шаг)."""
    if channel_id not in cache:
        try:
            st = asc.get(f"/api/v1/channels/{channel_id}/oauth/youtube")
            cache[channel_id] = bool(st.get("connected") and st.get("can_upload"))
        except ASError:
            cache[channel_id] = False
    return cache[channel_id]


def _scene_picks(conn: sqlite3.Connection, item: dict, settings: dict, cands: list[dict], n: int) -> list[dict]:
    """Моменты, совпадающие со сценами, которые сейчас режут в Shorts (разбор сайта)."""
    from . import content_check
    from .llm import llm_from_settings
    from .neuro import get_insights

    ins = get_insights(conn, item["ref_id"]) or {}
    return content_check.pick_by_scenes(llm_from_settings(settings), item.get("label") or "", ins.get("scenes") or [], cands, n)


# ---------- шаг конвейера ----------
def _rutube_alternative(conn: sqlite3.Connection, item: dict) -> dict | None:
    """Проверенный исходник фильма с Rutube, отличный от текущего (для фильмов; у роликов из подборки его нет)."""
    if item.get("kind") != "film":
        return None
    r = conn.execute("SELECT video_id, url, channel FROM title_sources WHERE title_id = ? AND verified = 1 AND url LIKE '%rutube.ru%'"
                     " AND video_id <> ? ORDER BY score DESC LIMIT 1", (item["ref_id"], item.get("source_video_id") or "")).fetchone()
    return dict(r) if r else None


def step(conn: sqlite3.Connection, asc: AutoShorts, item: dict, settings: dict, log=print) -> str:
    """Один шаг для одного задания. Возвращает новый статус."""
    iid = item["id"]
    st = item["status"]
    try:
        if st == "file_pending":
            fs = files.status(conn, item["source_video_id"]) if item.get("source_video_id") else None
            if fs and fs.get("status") == "done" and fs.get("file_url"):
                _set(conn, iid, direct_url=fs["file_url"], status="new")
                return "new"
            if fs and fs.get("status") == "error":
                m = dict(item.get("metrics") or {})
                alt = _rutube_alternative(conn, item) if "youtube" in (item.get("source_url") or "") else None
                if alt:
                    # YouTube не отдал файл (бот-проверка, гео): берём проверенный исходник с Rutube
                    files.init(conn)
                    res = files.request(conn, item["ref_id"] if item["kind"] == "film" else None, alt["video_id"], alt["url"])
                    _set(conn, iid, source_url=alt["url"], source_video_id=alt["video_id"], channel=alt.get("channel"),
                         last_error=f"YouTube не отдал файл ({fs.get('error')}), взят Rutube" if res.get("ok") else f"файл: {res.get('error')}")
                    log(f"конвейер: «{item.get('label')}» — YouTube не качается, переключено на Rutube")
                    return st if res.get("ok") else "failed"
                if not m.get("file_retry"):
                    m["file_retry"] = now_iso()
                    files.init(conn)
                    res = files.request(conn, item["ref_id"] if item["kind"] == "film" else None, item["source_video_id"], item["source_url"])
                    _set(conn, iid, metrics=dumps(m), last_error=f"файл не скачался ({fs.get('error')}), заказан повторно" if res.get("ok") else f"файл: {res.get('error')}")
                    return st
                _set(conn, iid, status="failed", last_error=f"файл не скачался: {fs.get('error')}")
                _forget_dead_source(conn, item, str(fs.get("error") or ""), log)
                return "failed"
            if not fs or fs.get("status") == "expired":  # файла ещё не было или его уже убрала очистка — заказать
                files.init(conn)
                res = files.request(conn, item["ref_id"] if item["kind"] == "film" else None, item["source_video_id"], item["source_url"])
                if not res.get("ok"):
                    _set(conn, iid, status="failed", last_error=f"файл: {res.get('error')}")
                    return "failed"
            return st
        if st in ("new", "needs_rights"):
            if item.get("source_video_id") and item.get("direct_url"):
                fs = files.status(conn, item["source_video_id"])
                if fs and fs.get("status") == "expired":
                    # пока ждали очереди, файл на хранилище убрала очистка: заказать заново, иначе auto-shorts получит 404
                    _set(conn, iid, status="file_pending", direct_url=None, last_error="файл хранилища истёк, заказан заново")
                    return "file_pending"
            rights = rights_for(item, settings)
            if not rights:
                if st != "needs_rights":
                    _set(conn, iid, status="needs_rights", last_error=None)
                return "needs_rights"
            if not item.get("direct_url") and item.get("source_video_id"):
                # в auto-shorts уходит только файл с хранилища: YouTube оттуда напрямую не качается
                files.init(conn)
                res = files.request(conn, item["ref_id"] if item["kind"] == "film" else None, item["source_video_id"], item["source_url"])
                _set(conn, iid, status="file_pending" if res.get("ok") else "failed", last_error=None if res.get("ok") else f"файл: {res.get('error')}")
                return "file_pending" if res.get("ok") else "failed"
            url = item.get("direct_url") or item.get("source_url")
            if url and not url.startswith("http"):
                url = (settings.get("public_url") or "").rstrip("/") + "/" + url.lstrip("/")
            src = asc.source_from_url(url, item.get("label") or item["ref_id"], item.get("niche_id"), rights, _options(item, settings))
            _set(conn, iid, status="sent", as_source_id=str(src.get("id")), sent_at=now_iso(), rights_type=rights["rights_type"], last_error=None)
            log(f"конвейер: «{item.get('label')}» отправлено в auto-shorts, источник {src.get('id')}")
            return "sent"
        if st in ("cutting", "review") and item["kind"] == "film":
            late = _late_frames_gate(conn, asc, item, settings, log)
            if late is not None:
                return late
        if st == "sent":
            src = asc.source(item["as_source_id"])
            s = src.get("status")
            if s == "failed":
                _set(conn, iid, status="failed", last_error=f"auto-shorts: источник failed: {src.get('error') or ''}"[:300])
                _forget_dead_source(conn, item, str(src.get("error") or ""), log)
                return "failed"
            if s != "ready":
                return st
            if item["kind"] == "film":
                gate = _content_gate(conn, asc, item, settings, log)
                if gate is not True:
                    return gate
            jopts = _job_options(item, settings)
            if item.get("is_short") == 1 or (item.get("duration_s") or 0) <= 60:
                dur = float(src.get("duration_sec") or item.get("duration_s") or 58)
                items = [{"start_sec": 0.0, "end_sec": min(58.0, dur), **jopts}]
            else:
                # занятые моменты (у них уже есть ролик, даже снятый) повторно не заказываем: auto-shorts ответит 409
                cands = [c for c in asc.candidates(item["as_source_id"]) if not c.get("blocked")
                         and c.get("status") in (None, "proposed", "approved") and not c.get("job_id")]
                cands.sort(key=lambda c: -(c.get("final_score") or 0))
                n = max(1, int(settings.get("pipeline_clips_per_source") or 3))
                targets = _channel_targets(asc, item, settings, jopts)
                picked = _scene_picks(conn, item, settings, cands, n) if item["kind"] == "film" else []
                chosen = (picked + [c for c in cands if c not in picked])[:n]
                # один фильм — один канал (YouTube с 01.10.2026 режет охват сетям каналов с одинаковым контентом):
                # фильмы чередуются между каналами; уже выходившее произведение остаётся на своём канале.
                # pipeline_film_one_channel=0 — по-старому, клипы фильма поровну по всем каналам
                if len(targets) > 1 and (settings.get("pipeline_film_one_channel") or "1") != "0":
                    one = _film_channel(conn, item, targets, settings)
                    if one is None:
                        _set(conn, iid, last_error="все каналы ротации YouTube прижал (нули подряд на разных фильмах, "
                                                    "правило оригинальности 01.10.2026) — фильм ждёт канал")
                        return st
                    targets = [one]
                    m_ch = dict(item.get("metrics") or {}, channel=targets[0][0])
                    item["metrics"] = m_ch
                    _set(conn, iid, metrics=dumps(m_ch))
                start = _rr_next(conn, len(chosen)) if len(targets) > 1 else 0
                items = []
                for k, cnd in enumerate(chosen):
                    slug, variant = targets[(start + k) % len(targets)]
                    jo = dict(jopts, channel_id=slug)
                    if variant:
                        jo["banner_variant_id"] = variant
                    items.append({"candidate_id": cnd["id"], "start_sec": cnd["start_sec"], "end_sec": cnd["end_sec"], **jo})
                if picked:
                    log(f"конвейер: «{item.get('label')}»: моменты по трендовым сценам — {len(picked)} из {len(items)}")
                if not items:
                    # модель не нашла моментов (мало речи): один раз пробуем эвристику по паузам и темпу, потом сдаёмся
                    m = dict(item.get("metrics") or {})
                    if not m.get("heuristic_retry"):
                        # SourceSelectRequest: параметры на верхнем уровне тела, без обёртки options
                        body = dict(_options(item, settings), selector="heuristic")
                        if item.get("niche_id"):
                            body["niche_id"] = item["niche_id"]
                        asc.post(f"/api/v1/sources/{item['as_source_id']}/select", body)
                        m["heuristic_retry"] = now_iso()
                        _set(conn, iid, metrics=dumps(m), last_error="модель не нашла моментов, запущен подбор эвристикой")
                        return st
                    _set(conn, iid, status="failed", last_error="auto-shorts не нашёл ни одного момента для клипа (ни модель, ни эвристика): добавьте момент вручную в auto-shorts и повторите")
                    return "failed"
            jobs = asc.clips(item["as_source_id"], items)
            ids = [j.get("id") for j in jobs if j.get("id") is not None]
            _set(conn, iid, status="cutting", as_jobs=dumps(ids), last_error=None)
            log(f"конвейер: «{item.get('label')}»: клипов поставлено {len(ids)}")
            return "cutting"
        if st == "retry_wait":
            # клипы упали по сбою стенда (например, удалённый файл хранилища): перезапускаем их, когда
            # дошла очередь по лимиту «в работе», а не все разом
            again = 0
            for jid in item["as_jobs"]:
                if asc.job(jid).get("status") == "failed":
                    asc.retry_job(jid)
                    again += 1
            _set(conn, iid, status="cutting", last_error=None)
            log(f"конвейер: «{item.get('label')}»: перезапущено клипов {again}")
            return "cutting"
        if st in ("cutting", "review"):
            statuses = {}
            m_retry = dict((item.get("metrics") or {}).get("auto_retry") or {})
            for jid in item["as_jobs"]:
                j = asc.job(jid)
                js = j.get("status")
                if js == "failed" and _transient(j) and int(m_retry.get(str(jid)) or 0) < AUTO_RETRY_MAX:
                    # временный сбой (озвучка Microsoft молчит, сеть): перезапуск с упавшей стадии, а не «ошибка» задания
                    try:
                        asc.retry_job(jid)
                        m_retry[str(jid)] = int(m_retry.get(str(jid)) or 0) + 1
                        _set(conn, iid, metrics=dumps({**(item.get("metrics") or {}), "auto_retry": m_retry}))
                        log(f"конвейер: «{item.get('label')}»: клип {jid} перезапущен после сбоя ({j.get('stage')})")
                        js = "processing"
                    except ASError as e:
                        log(f"конвейер: перезапуск клипа {jid}: {e}", "warn")
                if js == "pending_review" and settings.get("pipeline_auto_review", "0") == "1":
                    verdict = _ai_review(conn, asc, item, iid, jid, j, settings, log)
                    try:
                        if verdict is None or verdict.get("ok"):
                            note = "автоодобрение Кино-трендов: QC и оригинальность пройдены"
                            if verdict:
                                note += f"; нейросеть {verdict.get('score')}/10: {verdict.get('reason')}"
                            asc.review(jid, "approve", "pending_review", note[:500])
                            js = "approved"
                        else:
                            from .ai_review import reject_reason
                            asc.review(jid, "reject", "pending_review",
                                       f"нейросеть {verdict.get('score')}/10: {verdict.get('reason')} ({', '.join(verdict.get('problems') or [])})"[:500],
                                       reject_reason(verdict.get("problems") or []))
                            js = "rejected"
                            log(f"конвейер: «{item.get('label')}»: клип {jid} отклонён нейросетью {verdict.get('score')}/10 — {verdict.get('reason')}")
                    except ASError as e:
                        log(f"конвейер: автоприёмка {jid}: {e}", "warn")
                statuses[jid] = js
            vals = list(statuses.values())
            # одобренный клип уходит в план своего канала сразу, не дожидаясь решения по соседним клипам
            if settings.get("pipeline_autopublish", "0") == "1" and any(v == "approved" for v in vals) \
                    and any(v in ("queued", "processing", "draft", "rework", "pending_review") for v in vals):
                _publish_approved_now(conn, asc, item, settings, statuses, log)
            if any(v in ("queued", "processing", "draft", "rework") for v in vals):
                return st
            if any(v == "qc_failed" for v in vals) and not any(v in ("approved", "published") for v in vals):
                _set(conn, iid, status="qc_failed", last_error="клипы не прошли QC в auto-shorts", metrics=dumps({**(item.get("metrics") or {}), "jobs": statuses, "auto_retry": m_retry}))
                return "qc_failed"
            if any(v == "pending_review" for v in vals):
                _set(conn, iid, status="review", metrics=dumps({**(item.get("metrics") or {}), "jobs": statuses, "auto_retry": m_retry}))
                return "review"
            good = [jid for jid, v in statuses.items() if v in ("approved", "published")]
            if not good:
                _set(conn, iid, status="failed", last_error="все клипы отклонены или не собрались: " + ", ".join(f"{k}:{v}" for k, v in statuses.items()), metrics=dumps({**(item.get("metrics") or {}), "jobs": statuses, "auto_retry": m_retry}))
                return "failed"
            pkgs = []
            for jid in good:
                try:
                    p = asc.package_of_job(jid)
                    if p.get("id") is not None:
                        pkgs.append(p["id"])
                except ASError as e:
                    log(f"конвейер: пакет для {jid}: {e}", "warn")
            # в автозалив ставит только шаг approved/publishing: он видит, что уже запланировано, и добирает
            # поздние пакеты — так задание можно вернуть в нарезку (перезапуск клипа) без двойной постановки
            _set(conn, iid, status="approved", as_packages=dumps(pkgs), metrics=dumps({**(item.get("metrics") or {}), "jobs": statuses, "auto_retry": m_retry}))
            item = {**item, "status": "approved", "as_packages": pkgs, "metrics": {**(item.get("metrics") or {}), "jobs": statuses, "auto_retry": m_retry}}
            st = "approved"
            # все клипы решены — исходник больше не нужен: готовые ролики лежат в папках заданий, а нарезанный
            # файл второй раз не режем (fresh_sources). 01.11 три копии «Такси» по 1,6 ГБ держали стенд у порога 7 ГБ
            _purge_source(asc, item, log)
        if st in ("approved", "publishing"):
            pkgs = list(item["as_packages"] or [])
            jobs_map = (item.get("metrics") or {}).get("jobs") or {}
            good = [int(k) for k, v in jobs_map.items() if v in ("approved", "published")] or list(item["as_jobs"])
            # соседний клип упал по временной причине (воркер перезапускали, TTS молчал): вернуть его в нарезку,
            # уже запланированные пакеты при этом не трогаются
            m_retry = dict((item.get("metrics") or {}).get("auto_retry") or {})
            retried = False
            for k, v in jobs_map.items():
                if v != "failed" or int(m_retry.get(str(k)) or 0) >= AUTO_RETRY_MAX:
                    continue
                j = asc.job(int(k))
                if j.get("status") == "failed" and _transient(j):
                    asc.retry_job(int(k))
                    m_retry[str(k)] = int(m_retry.get(str(k)) or 0) + 1
                    retried = True
                    log(f"конвейер: «{item.get('label')}»: клип {k} перезапущен после сбоя ({j.get('stage')})")
                else:
                    m_retry[str(k)] = AUTO_RETRY_MAX  # брак или права: больше не проверяем
            if m_retry != ((item.get("metrics") or {}).get("auto_retry") or {}):
                _set(conn, iid, metrics=dumps({**(item.get("metrics") or {}), "auto_retry": m_retry}))
            if retried:
                _set(conn, iid, status="cutting", last_error=None)
                return "cutting"
            pubs, idle, errors, covered, active_pk = [], [], [], set(), []

            def take(pid, pk):
                if pk.get("status") == "superseded":
                    return  # пакет пересобран (новые тексты, новый рендер): актуальный доберём по заданию
                refs = list(pk.get("publications") or []) + ([pk["publication"]] if pk.get("publication") else [])
                ids = [r["id"] for r in refs if r.get("id") is not None]
                ap = pk.get("autopublish") or {}
                if pk.get("job_id") is not None:
                    covered.add(int(pk["job_id"]))
                if ids:
                    pubs.extend(ids)
                elif ap.get("state") in ("scheduled", "uploading"):
                    active_pk.append(pid)
                elif ap.get("state") == "failed":
                    # упавший автозалив сам не повторяем (квота, блок чек-листа): причина видна в задании
                    errors.append(f"пакет {pid}: {ap.get('error') or 'автозалив не удался'}")
                elif not ap:
                    idle.append(pid)  # в автозалив ещё не ставили; снятый вручную (cancelled) не трогаем

            superseded_ids: set = set()
            for pid in list(pkgs):
                pk = asc.package(pid)
                if pk.get("status") == "superseded":
                    superseded_ids.add(pid)
                take(pid, pk)
            # пакет клипа мог собраться позже одобрения (или позже соседнего клипа): добираем по заданиям
            waiting_pkg = False
            for jid in good:
                if jid in covered:
                    continue
                try:
                    pk = asc.package_of_job(jid)
                except ASError:
                    waiting_pkg = True  # пакет ещё собирается — задание не закрываем
                    continue
                if pk.get("id") is None:
                    waiting_pkg = True
                    continue
                if pk["id"] not in pkgs:
                    pkgs.append(pk["id"])
                    take(pk["id"], pk)
            active = len(active_pk)
            live = [pid for pid in pkgs if pid not in superseded_ids]
            if live != list(item["as_packages"] or []):
                pkgs = live
                _set(conn, iid, as_packages=dumps(pkgs))
            if not pkgs:
                return st
            held = 0  # ролики, ждущие итогов пробы: пока они есть, произведение не «опубликовано»
            if settings.get("pipeline_autopublish", "0") == "1" and idle:
                ready_cache: dict = {}
                waiting = []
                started = 0
                for pid in idle:
                    pk = asc.package(pid)
                    ch = ((pk.get("channel") or {}).get("id")) or ""
                    if ch and not _channel_ready(asc, ch, ready_cache):
                        waiting.append(ch)  # канал не подключён к YouTube: ролик одобрен и ждёт подключения
                        continue
                    if _probe_hold(conn, item, settings):
                        held += 1
                        continue
                    if _drop_twin(asc, item, ch, pid, pk, None, ready_cache, log):
                        continue
                    _autopublish(asc, item, settings, pid, log)
                    log(f"конвейер: «{item.get('label')}»: пакет {pid} — в план канала {ch or '?'}")
                    _mark_probe(conn, item, pid)
                    started += 1
                if held and not started:
                    _set(conn, iid, last_error=f"проба: {held} ролик(ов) ждут, пока первый провисит {float(settings.get('pipeline_probe_hours') or PROBE_HOURS):g} ч без блокировки")
                note = ("ждёт подключения YouTube: " + ", ".join(sorted(set(waiting)))) if waiting else None
                if started:
                    _set(conn, iid, status="publishing", as_publications=dumps(pubs), last_error=note)
                    return "publishing"
                if note:
                    _set(conn, iid, last_error=note)
                    return st
            if pubs and not active and not waiting_pkg and not held:
                _set(conn, iid, status="published", as_publications=dumps(pubs), published_at=item.get("published_at") or now_iso(),
                     last_error="; ".join(errors)[:300] or None)
                _purge_source(asc, item, log)
                return "published"
            if errors:
                _set(conn, iid, last_error="автозалив: " + "; ".join(errors)[:280])
            return st
        if st == "published":
            last = (item.get("metrics") or {}).get("metrics_at")
            if last and last > (datetime.now(timezone.utc) - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ"):
                return st
            m = dict(item.get("metrics") or {})
            m["publications"] = []
            for pub_id in item["as_publications"]:
                p = asc.publication(pub_id)
                m["publications"].append({"id": pub_id, "url": p.get("url"), "views": (p.get("latest") or {}).get("views"), "retention_3s": (p.get("latest") or {}).get("retention_3s"),
                                          "h24": (p.get("age_views") or {}).get("h24"), "d7": (p.get("age_views") or {}).get("d7"), "status": p.get("status")})
            m["metrics_at"] = now_iso()
            _set(conn, iid, metrics=dumps(m))
            return st
    except ASError as e:
        _set(conn, iid, last_error=str(e)[:300])
        log(f"конвейер: «{item.get('label')}» ({st}): {e}", "warn")
    return st


_TICK_LOCK = threading.Lock()


def _stand_free_gb(health: dict | None) -> float | None:
    """Свободное место на стенде из /health/ready auto-shorts («free 5.9 GB, …»); нет данных — None."""
    import re
    disk = ((health or {}).get("checks") or {}).get("disk") or {}
    m = re.search(r"free ([\d.]+) GB", str(disk.get("detail") or ""))
    if m:
        return float(m.group(1))
    return None


STORAGE_CLEANUP_EVERY_MIN = 15


def _storage_cleanup(conn: sqlite3.Connection, log=print) -> None:
    """Раз в 15 минут убирает с хранилища фильмы, которые auto-shorts уже забрал: диск стенда 30 ГБ общий,
    и заранее скачанные фильмы его забивали (29.10 — 12 ГБ, все задачи стояли с «мало места»)."""
    last = get_meta(conn, "storage_cleanup_at", "")
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=STORAGE_CLEANUP_EVERY_MIN)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if last and last > cutoff:
        return
    set_meta(conn, "storage_cleanup_at", now_iso())
    try:
        res = files.cleanup_ingested(conn)
        if res.get("removed"):
            log(f"хранилище: убрано файлов {res['removed']}, освобождено {res['freed_mb']} МБ")
    except Exception as e:  # noqa: BLE001
        log(f"хранилище: уборка не прошла: {e}", "warn")


def tick(conn: sqlite3.Connection, settings: dict, log=print, asc: AutoShorts | None = None) -> dict:
    """Проходит по активным заданиям и двигает каждое на шаг. Два тика сразу (фоновый цикл и кнопка «Сдвинуть»)
    отправляли один исходник дважды и заказывали шортсу два одинаковых клипа — второй тик ждёт не дольше пары
    секунд и пропускается."""
    if not _TICK_LOCK.acquire(timeout=2):
        return {"processed": 0, "sent": 0, "changed": 0, "errors": 0, "skipped": "предыдущий тик ещё идёт", "health": None}
    # блокировка и между процессами (сервис и ручной запуск из консоли): 29.10 два тика в одну секунду
    # отправили «Афоню» в auto-shorts дважды
    import fcntl
    from .config import VAR
    lock_file = open(VAR / "pipeline-tick.lock", "w")
    try:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"processed": 0, "sent": 0, "changed": 0, "errors": 0, "skipped": "тик идёт в другом процессе", "health": None}
        set_meta(conn, "pipeline_tick_at", now_iso())  # доктор видит, что цикл живой (даже если auto-shorts не ответил)
        return _tick(conn, settings, log, asc)
    finally:
        lock_file.close()
        _TICK_LOCK.release()


def _tick(conn: sqlite3.Connection, settings: dict, log=print, asc: AutoShorts | None = None) -> dict:
    out = {"processed": 0, "sent": 0, "changed": 0, "errors": 0, "skipped": "", "health": None}
    asc = asc or client_from_settings(settings)
    if not asc.configured:
        out["skipped"] = "auto-shorts не настроен: autoshorts_url, autoshorts_login, autoshorts_password"
        return out
    h = asc.health()
    out["health"] = h.get("status")
    if h.get("status") != "ok":
        # «не готов» только из-за диска — текущие фильмы ведём дальше (одобренное уходит в автозалив), новые не берём
        bad = [k for k, v in ((h.get("checks") or {}).items()) if isinstance(v, dict) and not v.get("ok")]
        if bad != ["disk"]:
            out["skipped"] = f"auto-shorts не готов: {h.get('status')} {', '.join(bad)}".strip()
            return out
    # Лимит тика — только на запуск новых исходников (скачать и распознать — тяжело для хранилища). Задания, которые
    # уже в работе, двигаются каждый тик: раньше LIMIT по created_at давал старым зависшим заданиям занять все места,
    # и свежие стояли в «отправлено» сутками.
    # «По чуть-чуть»: запуск нового (заказ файла на хранилище или отправка в auto-shorts) — не больше
    # pipeline_max_per_tick за тик и только пока в работе меньше pipeline_max_in_work заданий.
    limit = int(settings.get("pipeline_max_per_tick") or 1)
    max_in_work = int(settings.get("pipeline_max_in_work") or 3)
    # новый фильм — только при запасе диска на стенде (фильм занимает там 0,5–1,7 ГБ; 29.10 при 10 в работе
    # диск упал до 4 ГБ и API ушёл в «не готов»): скорость сама подстраивается под место
    free_gb = _stand_free_gb(h)
    min_free = float(settings.get("pipeline_min_free_gb") or 7)
    if free_gb is not None and free_gb < min_free:
        limit = 0
        out["skipped"] = f"новые фильмы ждут места на стенде: свободно {free_gb:.1f} ГБ < {min_free:g} ГБ"
    rows = conn.execute(f"SELECT * FROM pipeline_items WHERE status IN ({','.join('?' * (len(ACTIVE) + 1))}) ORDER BY created_at",
                        (*ACTIVE, "published")).fetchall()
    files.init(conn)
    _storage_cleanup(conn, log)
    in_work = sum(1 for r in rows if _in_work(conn, r))
    if limit and top_up(conn, settings, rows, log):
        rows = conn.execute(f"SELECT * FROM pipeline_items WHERE status IN ({','.join('?' * (len(ACTIVE) + 1))}) ORDER BY created_at",
                            (*ACTIVE, "published")).fetchall()
    # Поток в приёмку без остановки (решение владельца 29.10: «бесконечный цикл, очередь на неделю вперёд»):
    # pipeline_review_backlog = 0 — без ограничения; число > 0 — пауза, когда столько фильмов ждут приёмки.
    backlog = sum(1 for r in rows if r["status"] == "review")
    backlog_max = int(settings.get("pipeline_review_backlog", REVIEW_BACKLOG_ITEMS) or 0) or 10**9
    starts = 0
    for r in rows:
        item = _hydrate(dict(r))
        before = item["status"]
        if before in START_STATES and not _in_work(conn, r):
            if starts >= limit or in_work >= max_in_work or backlog >= backlog_max:
                continue
            starts += 1
            in_work += 1
        after = step(conn, asc, item, settings, log)
        out["processed"] += 1
        if after != before:
            out["changed"] += 1
        if after == "sent" and before == "new":
            out["sent"] += 1
    out["errors"] = conn.execute("SELECT COUNT(*) FROM pipeline_items WHERE status = 'failed'").fetchone()[0]
    return out


def list_items(conn: sqlite3.Connection, status: str | None = None, limit: int = 100) -> dict:
    where, params = "", []
    if status:
        where, params = "WHERE status = ?", [status]
    rows = conn.execute(f"SELECT * FROM pipeline_items {where} ORDER BY updated_at DESC LIMIT ?", params + [limit]).fetchall()
    counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM pipeline_items GROUP BY status")}
    return {"items": [_hydrate(dict(r)) for r in rows], "counts": counts, "states_ru": STATE_RU}


def retry(conn: sqlite3.Connection, item_id: int) -> dict | None:
    it = _row(conn, item_id)
    if not it:
        return None
    back = "new" if not it.get("as_source_id") else "sent" if not it["as_jobs"] else "cutting"
    if it["status"] == "file_pending" or (back == "new" and not it.get("direct_url") and it.get("source_video_id")):
        back = "file_pending"  # файл так и не готов: заказать заново, YouTube-ссылку напрямую в auto-shorts не отдаём
        # ручной повтор — это новая попытка: сбрасываем автоповтор и сразу заказываем файл, иначе шаг увидит
        # старую ошибку загрузки и тут же снова уронит задание с прежним текстом
        m = dict(it.get("metrics") or {})
        m.pop("file_retry", None)
        files.init(conn)
        res = files.request(conn, it["ref_id"] if it["kind"] == "film" else None, it["source_video_id"], it["source_url"])
        _set(conn, item_id, status=back, metrics=dumps(m), last_error=None if res.get("ok") else f"файл: {res.get('error')}")
        return _row(conn, item_id)
    _set(conn, item_id, status=back, last_error=None)
    return _row(conn, item_id)


def cancel(conn: sqlite3.Connection, item_id: int) -> dict | None:
    if not _row(conn, item_id):
        return None
    _set(conn, item_id, status="cancelled")
    return _row(conn, item_id)


def status(conn: sqlite3.Connection, settings: dict) -> dict:
    asc = client_from_settings(settings)
    h = asc.health() if asc.configured else {"status": "не настроен"}
    chans = []
    if asc.configured and h.get("status") == "ok":
        try:
            chans = [{"id": c.get("id"), "title": c.get("title"), "niche_id": c.get("niche_id"), "status": c.get("status")} for c in asc.channels()]
        except ASError as e:
            h["channels_error"] = str(e)
    counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM pipeline_items GROUP BY status")}
    return {"configured": asc.configured, "health": h, "channels": chans, "counts": counts, "enabled": settings.get("pipeline_enabled", "0") == "1",
            "auto_review": settings.get("pipeline_auto_review", "0") == "1", "autopublish": settings.get("pipeline_autopublish", "0") == "1",
            "rights_default": settings.get("pipeline_rights_default") or "", "states_ru": STATE_RU}


# ---------- фоновый цикл ----------
_stop = threading.Event()


def loop(connect, log=print) -> None:
    """Раз в pipeline_tick_min минут двигает очередь, если конвейер включён."""
    from .db import all_settings
    while not _stop.is_set():
        try:
            c = connect()
            try:
                s = all_settings(c)
                if s.get("pipeline_enabled", "0") == "1":
                    tick(c, s, log)
                mins = max(1, int(s.get("pipeline_tick_min") or 5))
            finally:
                c.close()
        except Exception as e:  # noqa: BLE001
            log(f"конвейер: цикл: {e}", "warn")
            mins = 5
        _stop.wait(mins * 60)
