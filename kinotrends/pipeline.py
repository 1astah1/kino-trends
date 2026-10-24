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

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx

from . import files, popular
from .db import dumps, loads, now_iso

STATES = ("new", "file_pending", "needs_rights", "sent", "cutting", "review", "qc_failed", "approved", "publishing", "published", "failed", "cancelled")
ACTIVE = ("new", "needs_rights", "file_pending", "sent", "cutting", "review", "approved", "publishing")
STATE_RU = {"new": "в очереди", "file_pending": "готовится файл", "needs_rights": "нужен тип прав", "sent": "источник в auto-shorts", "cutting": "нарезка",
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

    def review(self, jid, decision: str, expected: str, comment: str | None = None) -> dict:
        return self.post(f"/api/v1/jobs/{jid}/review", {"decision": decision, "expected_status": expected, "comment": comment})

    def package_of_job(self, jid) -> dict:
        return self.get(f"/api/v1/jobs/{jid}/package")

    def package(self, pid) -> dict:
        return self.get(f"/api/v1/packages/{pid}")

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
            raise ValueError("у произведения ещё нет исходника")
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
                  "metrics": dumps({"free_zones": (d.get("frames") or {}).get("free_zones") or []})}
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
    if status == "file_pending" and fields["source_video_id"]:
        files.init(conn)
        res = files.request(conn, ref_id if kind == "film" else None, fields["source_video_id"], fields["source_url"])
        if not res.get("ok"):
            _set(conn, item_id, status="failed", last_error=f"файл: {res.get('error')}")
    return _row(conn, item_id)


def enqueue_today(conn: sqlite3.Connection, settings: dict) -> dict:
    """Всё, что на сегодня: слоты календаря кино и подборка видео."""
    from . import schedule
    out = {"queued": [], "skipped": []}
    v = schedule.view(conn, days=1, back=0)
    for d in v["days"]:
        for it in d["items"]:
            if not it["title_id"] or it["status"] != "planned":
                continue
            try:
                r = enqueue(conn, "film", it["title_id"], settings, note=f"календарь {d['day']}")
                out["queued"].append({"kind": "film", "ref_id": it["title_id"], "label": it["label"], "id": r["id"], "status": r["status"]})
            except ValueError as e:
                out["skipped"].append({"kind": "film", "ref_id": it["title_id"], "label": it["label"], "why": str(e)})
    for p in popular.picks(conn):
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


def _options(item: dict, settings: dict) -> dict:
    cmax = min(58, int(item.get("clip_max_s") or 58))
    cmin = max(15, min(int(item.get("clip_min_s") or 15), cmax - 5))
    n = max(1, int(settings.get("pipeline_clips_per_source") or 3))
    return {"selector": "auto", "target_clip_sec": max(cmin, min(cmax, 30)), "min_clip_sec": cmin, "max_clip_sec": cmax,
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


def _job_options(item: dict, settings: dict) -> dict:
    zones = (item.get("metrics") or {}).get("free_zones") if isinstance(item.get("metrics"), dict) else None
    opts = {"channel_id": item.get("channel_slug") or "", "priority": 100, "start": True, "reframe": {"mode": "auto"},
            "author_layer": {"mode": "llm_draft", "captions": "both", "voice": "tts", "hook_text": None, "title_overlay": None}}
    variant = pick_banner_variant(zones, settings)
    if variant:
        opts["banner_variant_id"] = variant
    return opts


# ---------- шаг конвейера ----------
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
                if not m.get("file_retry"):
                    m["file_retry"] = now_iso()
                    files.init(conn)
                    res = files.request(conn, item["ref_id"] if item["kind"] == "film" else None, item["source_video_id"], item["source_url"])
                    _set(conn, iid, metrics=dumps(m), last_error=f"файл не скачался ({fs.get('error')}), заказан повторно" if res.get("ok") else f"файл: {res.get('error')}")
                    return st
                _set(conn, iid, status="failed", last_error=f"файл не скачался: {fs.get('error')}")
                return "failed"
            if not fs:
                files.init(conn)
                res = files.request(conn, item["ref_id"] if item["kind"] == "film" else None, item["source_video_id"], item["source_url"])
                if not res.get("ok"):
                    _set(conn, iid, status="failed", last_error=f"файл: {res.get('error')}")
                    return "failed"
            return st
        if st in ("new", "needs_rights"):
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
        if st == "sent":
            src = asc.source(item["as_source_id"])
            s = src.get("status")
            if s == "failed":
                _set(conn, iid, status="failed", last_error=f"auto-shorts: источник failed: {src.get('error') or ''}"[:300])
                return "failed"
            if s != "ready":
                return st
            jopts = _job_options(item, settings)
            if item.get("is_short") == 1 or (item.get("duration_s") or 0) <= 60:
                dur = float(src.get("duration_sec") or item.get("duration_s") or 58)
                items = [{"start_sec": 0.0, "end_sec": min(58.0, dur), **jopts}]
            else:
                cands = [c for c in asc.candidates(item["as_source_id"]) if not c.get("blocked")]
                cands.sort(key=lambda c: -(c.get("final_score") or 0))
                n = max(1, int(settings.get("pipeline_clips_per_source") or 3))
                items = [{"candidate_id": c["id"], "start_sec": c["start_sec"], "end_sec": c["end_sec"], **jopts} for c in cands[:n]]
                if not items:
                    # модель не нашла моментов (мало речи): один раз пробуем эвристику по паузам и темпу, потом сдаёмся
                    m = dict(item.get("metrics") or {})
                    if not m.get("heuristic_retry"):
                        opts = dict(_options(item, settings), selector="heuristic")
                        asc.post(f"/api/v1/sources/{item['as_source_id']}/select", {"options": opts})
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
        if st in ("cutting", "review"):
            statuses = {}
            for jid in item["as_jobs"]:
                j = asc.job(jid)
                js = j.get("status")
                if js == "pending_review" and settings.get("pipeline_auto_review", "0") == "1":
                    try:
                        asc.review(jid, "approve", "pending_review", "автоодобрение Кино-трендов: QC и оригинальность пройдены")
                        js = "approved"
                    except ASError as e:
                        log(f"конвейер: автоодобрение {jid}: {e}", "warn")
                statuses[jid] = js
            vals = list(statuses.values())
            if any(v in ("queued", "processing", "draft", "rework") for v in vals):
                return st
            if any(v == "qc_failed" for v in vals) and not any(v in ("approved", "published") for v in vals):
                _set(conn, iid, status="qc_failed", last_error="клипы не прошли QC в auto-shorts", metrics=dumps({"jobs": statuses}))
                return "qc_failed"
            if any(v == "pending_review" for v in vals):
                _set(conn, iid, status="review", metrics=dumps({"jobs": statuses}))
                return "review"
            good = [jid for jid, v in statuses.items() if v in ("approved", "published")]
            if not good:
                _set(conn, iid, status="failed", last_error="все клипы отклонены или не собрались: " + ", ".join(f"{k}:{v}" for k, v in statuses.items()), metrics=dumps({"jobs": statuses}))
                return "failed"
            pkgs = []
            for jid in good:
                try:
                    p = asc.package_of_job(jid)
                    if p.get("id") is not None:
                        pkgs.append(p["id"])
                except ASError as e:
                    log(f"конвейер: пакет для {jid}: {e}", "warn")
            _set(conn, iid, as_packages=dumps(pkgs), metrics=dumps({"jobs": statuses}))
            if settings.get("pipeline_autopublish", "0") == "1" and pkgs:
                for pid in pkgs:
                    asc.autopublish(pid, now=settings.get("pipeline_publish_now", "0") == "1", privacy=settings.get("pipeline_privacy") or "public")
                _set(conn, iid, status="publishing")
                return "publishing"
            _set(conn, iid, status="approved")
            return "approved"
        if st in ("approved", "publishing"):
            pubs = []
            for pid in item["as_packages"]:
                p = asc.package(pid)
                for ref in (p.get("publications") or []):
                    if ref.get("id") is not None:
                        pubs.append(ref["id"])
            if pubs:
                _set(conn, iid, status="published", as_publications=dumps(pubs), published_at=now_iso())
                return "published"
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


def tick(conn: sqlite3.Connection, settings: dict, log=print, asc: AutoShorts | None = None) -> dict:
    """Проходит по активным заданиям и двигает каждое на шаг."""
    out = {"processed": 0, "sent": 0, "changed": 0, "errors": 0, "skipped": "", "health": None}
    asc = asc or client_from_settings(settings)
    if not asc.configured:
        out["skipped"] = "auto-shorts не настроен: autoshorts_url, autoshorts_login, autoshorts_password"
        return out
    h = asc.health()
    out["health"] = h.get("status")
    if h.get("status") != "ok":
        out["skipped"] = f"auto-shorts не готов: {h.get('status')}"
        return out
    rows = conn.execute(f"SELECT * FROM pipeline_items WHERE status IN ({','.join('?' * (len(ACTIVE) + 1))}) ORDER BY created_at LIMIT ?",
                        (*ACTIVE, "published", int(settings.get("pipeline_max_per_tick") or 20))).fetchall()
    for r in rows:
        item = _hydrate(dict(r))
        before = item["status"]
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
