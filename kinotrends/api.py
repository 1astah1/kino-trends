"""HTTP API и интерфейс. Один процесс: FastAPI + фоновый планировщик сбора и автоимпорт словаря."""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import Body, FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, files, neuro, notify, pipeline, popular, posters, recommend, rights, schedule, sources, stats
from .collector import STATE, Collector
from .llm import LLMError, llm_from_settings, usage_today
from .config import DEFAULT_SETTINGS, THUMB_DIR, USER_AGENT, WEB_DIR
from .dictionary import importer, wikidata
from .dictionary.wikidata import ENTITIES_PATH, ITEMS_PATH
from .recognize import Recognizer, result_json
from .text import norm
from .yt import flat

app = FastAPI(title="Kino Trends", docs_url="/api/docs", openapi_url="/api/openapi.json")
REC = Recognizer()
JOBS: dict[str, dict] = {"dict_import": {"running": False, "last": None}, "recognize": {"running": False, "last": None}, "neuro": {"running": False, "last": None},
                         "popular": {"running": False, "last": None}}
SECRET_KEYS = ("youtube_api_key", "llm_api_key", "telegram_bot_token", "autoshorts_password")
TEXT_KEYS = ("llm_base_url", "llm_model", "telegram_chat_id", "public_url", "storage_host", "storage_user", "storage_key", "storage_dir", "storage_url", "rights_policy",
             "pop_regions", "pop_categories", "pop_cc_queries", "pop_lang", "pop_audience",
             "autoshorts_url", "autoshorts_login", "pipeline_channel_film", "pipeline_channel_video", "pipeline_niche_film", "pipeline_niche_video",
             "pipeline_rights_default", "pipeline_rights_note", "pipeline_privacy", "storage_proxy", "storage_cookies")
_bg_lock = threading.Lock()
_SCHED_STOP = threading.Event()


def _conn():
    return db.connect()


def _filters(period: str = "30", min_conf: float = 0.6, min_views: int = 0, lang: str = "all") -> stats.Filters:
    return stats.Filters(period=period, min_conf=min_conf, min_views=min_views, lang=lang)


# ---------- фоновые задачи ----------
def _load_recognizer() -> None:
    c = _conn()
    try:
        REC.load(c)
    finally:
        c.close()


def _import_dict_job() -> None:
    with _bg_lock:
        if JOBS["dict_import"]["running"]:
            return
        JOBS["dict_import"]["running"] = True
    try:
        try:
            wikidata.fetch_labels()  # русские подписи жанров, стран и франшиз для уже полученных карточек
        except Exception as e:  # noqa: BLE001
            JOBS["dict_import"]["labels_error"] = str(e)
        c = _conn()
        try:
            res = importer.import_entities(c)
            REC.load(c)
            if not STATE.running:
                Collector(c, REC, trigger="dict").recognize_pending()
            JOBS["dict_import"]["last"] = {"at": db.now_iso(), **res}
        finally:
            c.close()
    except Exception as e:  # noqa: BLE001
        JOBS["dict_import"]["last"] = {"at": db.now_iso(), "error": str(e)}
    finally:
        JOBS["dict_import"]["running"] = False


def _recognize_job(force: bool) -> None:
    with _bg_lock:
        if JOBS["recognize"]["running"]:
            return
        JOBS["recognize"]["running"] = True
    try:
        c = _conn()
        try:
            col = Collector(c, REC, trigger="recognize")
            col.recognize_pending(force=force)
            JOBS["recognize"]["last"] = {"at": db.now_iso(), **col.stats}
        finally:
            c.close()
    except Exception as e:  # noqa: BLE001
        JOBS["recognize"]["last"] = {"at": db.now_iso(), "error": str(e)}
    finally:
        JOBS["recognize"]["running"] = False


def _start_run(trigger: str) -> int:
    if STATE.running:
        raise HTTPException(409, "прогон уже идёт")
    c = _conn()
    other = c.execute("SELECT id FROM runs WHERE status = 'running'").fetchone()
    if other:
        c.close()
        raise HTTPException(409, f"прогон #{other[0]} идёт в другом процессе (консоль)")
    col = Collector(c, REC, trigger=trigger)

    def target() -> None:
        try:
            col.run()
        finally:
            c.close()

    threading.Thread(target=target, name="collector", daemon=True).start()
    for _ in range(50):
        if STATE.run_id is not None and STATE.running:
            break
        time.sleep(0.05)
    return STATE.run_id or 0


def _neuro_job(limit: int | None = None) -> None:
    with _bg_lock:
        if JOBS["neuro"]["running"]:
            return
        JOBS["neuro"]["running"] = True
    try:
        c = _conn()
        try:
            settings = db.all_settings(c)
            if limit:
                settings["neuro_per_run"] = str(limit)
            res = neuro.run_batch(c, REC, llm_from_settings(settings), settings, log=lambda m, level="info": None)
            JOBS["neuro"]["last"] = {"at": db.now_iso(), **res}
        finally:
            c.close()
    except Exception as e:  # noqa: BLE001
        JOBS["neuro"]["last"] = {"at": db.now_iso(), "error": str(e)}
    finally:
        JOBS["neuro"]["running"] = False


def _scheduler() -> None:
    last_dict_check = 0.0
    while not _SCHED_STOP.wait(30):
        try:
            c = _conn()
            try:
                now = time.time()
                if ENTITIES_PATH.exists() and now - last_dict_check > 600 and not JOBS["dict_import"]["running"]:
                    last_dict_check = now
                    imported = db.get_meta(c, "dict_imported_at", "")
                    mtime = datetime.fromtimestamp(ENTITIES_PATH.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    if not imported or mtime > imported:
                        threading.Thread(target=_import_dict_job, name="dict-import", daemon=True).start()
                notify.maybe_digest(c)
                hours = db.get_int(c, "schedule_hours")
                if hours > 0 and not STATE.running:
                    running_elsewhere = c.execute("SELECT 1 FROM runs WHERE status = 'running'").fetchone()
                    last = c.execute("SELECT started_at FROM runs WHERE status != 'running' ORDER BY id DESC LIMIT 1").fetchone()
                    due = True
                    if last:
                        started = datetime.strptime(last[0][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                        due = datetime.now(timezone.utc) - started >= timedelta(hours=hours)
                    if due and not running_elsewhere and REC.size > 0:
                        try:
                            _start_run("schedule")
                        except HTTPException:
                            pass
            finally:
                c.close()
        except Exception:  # noqa: BLE001
            pass


@app.on_event("startup")
def _startup() -> None:
    c = _conn()
    db.init_db(c)
    threading.Thread(target=pipeline.loop, args=(db.connect, lambda m, level="info": print(f"[конвейер:{level}] {m}", flush=True)), name="pipeline", daemon=True).start()
    files.init(c)
    c.execute("UPDATE downloads SET status = 'error', error = 'процесс прерван' WHERE status = 'running'")
    try:
        files.cleanup(c)
    except Exception:  # noqa: BLE001
        pass
    c.execute("UPDATE runs SET status = 'error', finished_at = ?, error = 'процесс прерван (перезапуск сервиса)' WHERE status = 'running'", (db.now_iso(),))
    if not db.get_meta(c, "seasons_backfilled", ""):
        from .text import season_episode
        for r in c.execute("SELECT video_id, title, description FROM videos WHERE details_at IS NOT NULL").fetchall():
            se, ep = season_episode(r["title"], r["description"])
            if se or ep:
                c.execute("UPDATE videos SET season = ?, episode = ? WHERE video_id = ?", (se, ep, r["video_id"]))
        db.set_meta(c, "seasons_backfilled", db.now_iso())
    c.commit()
    c.close()
    threading.Thread(target=_load_recognizer, name="rec-load", daemon=True).start()
    threading.Thread(target=_scheduler, name="scheduler", daemon=True).start()


@app.on_event("shutdown")
def _shutdown() -> None:
    _SCHED_STOP.set()


# ---------- интерфейс ----------
@app.get("/", include_in_schema=False)
def index() -> Response:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "styles.css"):
        ver = int((WEB_DIR / name).stat().st_mtime)
        html = html.replace(f"static/{name}", f"static/{name}?v={ver}")
    return Response(html, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
app.mount("/files", StaticFiles(directory=str(files.FILES_DIR)), name="files")


@app.post("/api/downloads")
def downloads_request(payload: dict = Body(...)) -> dict:
    """Подготовить прямую ссылку на файл исходника (Rutube). Тело: {source_id} или {video_id, url, title_id}."""
    c = _conn()
    try:
        if payload.get("source_id"):
            srow = c.execute("SELECT title_id, video_id, url FROM title_sources WHERE id = ?", (int(payload["source_id"]),)).fetchone()
            if not srow:
                raise HTTPException(404, "нет такого источника")
            title_id, video_id, url = srow["title_id"], srow["video_id"], srow["url"]
        else:
            title_id, video_id, url = payload.get("title_id"), payload.get("video_id"), payload.get("url")
            if not video_id or not url:
                raise HTTPException(400, "нужны video_id и url")
        res = files.request(c, title_id, video_id, url)
        if not res["ok"]:
            raise HTTPException(400, res["error"])
        return res["status"]
    finally:
        c.close()


@app.post("/api/schedule/prepare-files")
def api_prepare_files() -> dict:
    """Поставить загрузки для сегодняшних и завтрашних слотов с Rutube-исходником (в фоне, по одной)."""
    c = _conn()
    try:
        files.init(c)
        remote = files.storage(c)["remote"]
        v = schedule.view(c, days=2, back=0)
        queued = []
        for d in v["days"]:
            for it in d["items"]:
                src = None
                for cand in (it.get("source"), it.get("source_alt")):
                    if cand and (remote or "rutube.ru" in cand["url"]):
                        src = cand
                        break
                if not src or it["status"] == "skipped":
                    continue
                st = files.status(c, src["video_id"])
                if st and st["status"] in ("done", "running"):
                    continue
                res = files.request(c, it["title_id"], src["video_id"], src["url"])
                queued.append({"title": it["label"], "ok": res["ok"], "error": res.get("error")})
        return {"queued": queued}
    finally:
        c.close()


@app.get("/api/downloads/{video_id}")
def downloads_status(video_id: str) -> dict:
    c = _conn()
    try:
        st = files.status(c, video_id)
        if not st:
            raise HTTPException(404, "загрузки не было")
        return st
    finally:
        c.close()


@app.get("/api/downloads")
def downloads_list() -> dict:
    c = _conn()
    try:
        rows = []
        for r in c.execute("SELECT * FROM downloads ORDER BY id DESC LIMIT 50"):
            d = dict(r)
            d["file_url"] = files.file_url(c, r)
            rows.append(d)
        st = files.storage(c)
        return {"items": rows, "remote": st["remote"], "host": st["host"], "free_gb": round(files.remote_free_gb(st) or 0, 1) if st["remote"] else round(files.free_gb(), 1),
                "used_gb": round(files.total_gb(c), 2), "max_gb": st["max_gb"] if st["remote"] else files.MAX_TOTAL_GB}
    finally:
        c.close()


@app.get("/thumb/{video_id}.jpg", include_in_schema=False)
def thumb(video_id: str) -> Response:
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id):
        raise HTTPException(404)
    path = THUMB_DIR / f"{video_id}.jpg"
    if not path.exists():
        c = _conn()
        row = c.execute("SELECT thumb FROM videos WHERE video_id = ?", (video_id,)).fetchone()
        c.close()
        urls = []
        if row and row["thumb"]:
            urls.append(row["thumb"])
        urls += [f"https://i.ytimg.com/vi/{video_id}/oar2.jpg", f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"]
        data = None
        with httpx.Client(timeout=15, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as cl:
            for u in urls:
                try:
                    r = cl.get(u)
                    if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                        data = r.content
                        break
                except httpx.HTTPError:
                    continue
        if not data:
            raise HTTPException(404)
        path.write_bytes(data)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=604800"})


# ---------- статистика ----------
@app.get("/api/overview")
def api_overview(period: str = "30", min_conf: float = 0.6, min_views: int = 0, lang: str = "all") -> dict:
    c = _conn()
    try:
        out = stats.overview(c, _filters(period, min_conf, min_views, lang))
        out["jobs"] = JOBS
        out["recognizer"] = {"ready": REC.size > 0, "titles": REC.size, "loaded_at": REC.loaded_at}
        out["run"] = STATE.snapshot()
        return out
    finally:
        c.close()


@app.get("/api/films")
def api_films(period: str = "30", min_conf: float = 0.6, min_views: int = 0, lang: str = "all", sort: str = "score",
              kind: str = "all", genre: str | None = None, q: str | None = None, limit: int = 100, offset: int = 0) -> dict:
    c = _conn()
    try:
        out = stats.films(c, _filters(period, min_conf, min_views, lang), limit=min(limit, 1000), sort=sort, kind=kind, genre=genre, q=q, offset=offset)
        lv = rights.levels(c, [it["title_id"] for it in out["items"]])
        for it in out["items"]:
            it["rights"] = lv.get(it["title_id"])
        return out
    finally:
        c.close()


@app.get("/api/films/{title_id}")
def api_film(title_id: str, period: str = "30", min_conf: float = 0.6, min_views: int = 0, lang: str = "all") -> dict:
    c = _conn()
    try:
        out = stats.film_detail(c, _filters(period, min_conf, min_views, lang), title_id)
        if out is None:
            raise HTTPException(404, "нет такого произведения")
        return out
    finally:
        c.close()


@app.get("/api/interests")
def api_interests(period: str = "30", min_conf: float = 0.6, min_views: int = 0, lang: str = "all") -> dict:
    c = _conn()
    try:
        return stats.interests(c, _filters(period, min_conf, min_views, lang))
    finally:
        c.close()


@app.get("/api/channels")
def api_channels(period: str = "30", min_conf: float = 0.6, min_views: int = 0, lang: str = "all", sort: str = "views",
                 q: str | None = None, limit: int = 100, offset: int = 0) -> dict:
    c = _conn()
    try:
        return stats.channels(c, _filters(period, min_conf, min_views, lang), limit=min(limit, 1000), sort=sort, q=q, offset=offset)
    finally:
        c.close()


@app.get("/api/channels/{channel_id}")
def api_channel(channel_id: str, period: str = "30", min_conf: float = 0.6, min_views: int = 0, lang: str = "all") -> dict:
    c = _conn()
    try:
        out = stats.channel_detail(c, _filters(period, min_conf, min_views, lang), channel_id)
        if out is None:
            raise HTTPException(404, "нет такого канала")
        return out
    finally:
        c.close()


@app.get("/api/shorts")
def api_shorts(period: str = "30", min_conf: float = 0.6, min_views: int = 0, lang: str = "all", state: str = "all",
               q: str | None = None, title_id: str | None = None, channel_id: str | None = None, sort: str = "views",
               limit: int = 50, offset: int = 0) -> dict:
    c = _conn()
    try:
        return stats.shorts(c, _filters(period, min_conf, min_views, lang), state=state, q=q, title_id=title_id,
                            channel_id=channel_id, sort=sort, limit=min(limit, 500), offset=offset)
    finally:
        c.close()


@app.get("/api/export/films.csv")
def export_films(period: str = "30", min_conf: float = 0.6, min_views: int = 0, lang: str = "all") -> Response:
    c = _conn()
    try:
        body = stats.films_csv(c, _filters(period, min_conf, min_views, lang))
    finally:
        c.close()
    return Response(("﻿" + body).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="films_{period}.csv"'})


@app.get("/api/export/shorts.csv")
def export_shorts(period: str = "30", min_conf: float = 0.6, min_views: int = 0, lang: str = "all", state: str = "all") -> Response:
    c = _conn()
    try:
        body = stats.shorts_csv(c, _filters(period, min_conf, min_views, lang), state=state)
    finally:
        c.close()
    return Response(("﻿" + body).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="shorts_{period}.csv"'})


# ---------- словарь и ручная разметка ----------
@app.get("/api/titles/search")
def titles_search(q: str = Query(min_length=1), limit: int = 12) -> dict:
    hits = REC.search(q, limit=limit)
    c = _conn()
    try:
        if len(hits) < limit and len(q.strip()) >= 3:
            seen = {h[0] for h in hits}
            like = q.strip().replace("ё", "е") + "%"
            for r in c.execute("SELECT id FROM titles WHERE (label_ru LIKE ? OR label_en LIKE ?) ORDER BY sitelinks DESC LIMIT ?", (like, like, limit)):
                if r["id"] not in seen:
                    hits.append((r["id"], 60.0))
                    seen.add(r["id"])
        if not hits:
            return {"items": []}
        ids = [h[0] for h in hits]
        qs = ",".join("?" * len(ids))
        rows = {r["id"]: r for r in c.execute(f"SELECT id, label_ru, label_en, year, kind, genres, sitelinks FROM titles WHERE id IN ({qs})", ids)}
        items = []
        for tid, score in hits:
            r = rows.get(tid)
            if r:
                items.append({"title_id": tid, "label": r["label_ru"] or r["label_en"], "label_en": r["label_en"], "year": r["year"],
                              "kind": r["kind"], "kind_ru": stats.KIND_RU.get(r["kind"], r["kind"]), "genres": db.loads(r["genres"], []),
                              "score": score})
        return {"items": items}
    finally:
        c.close()


@app.post("/api/titles")
def titles_create(payload: dict = Body(...)) -> dict:
    label = (payload.get("label_ru") or "").strip()
    if len(label) < 2:
        raise HTTPException(400, "нужно название")
    kind = payload.get("kind") or "film"
    if kind not in stats.KIND_RU:
        raise HTTPException(400, "неизвестный тип")
    year = payload.get("year")
    year = int(year) if year else None
    tid = f"man_{int(time.time() * 1000):x}"
    c = _conn()
    try:
        c.execute("INSERT INTO titles(id, label_ru, year, kind, sitelinks, genres, countries, is_manual, updated_at) VALUES (?,?,?,?,0,'[]','[]',1,?)",
                  (tid, label, year, kind, db.now_iso()))
        c.execute("INSERT OR IGNORE INTO title_aliases(title_id, alias, norm, lang, source) VALUES (?,?,?,?,'manual')", (tid, label, norm(label), "ru"))
        c.commit()
    finally:
        c.close()
    with REC.lock:
        REC.meta[tid] = (year, 0, kind, label)
    REC.add_alias(tid, label)
    return {"title_id": tid, "label": label, "year": year, "kind": kind}


# ---------- популярные видео СНГ ----------
def _popular_job(charts_only: bool) -> None:
    with _bg_lock:
        if JOBS["popular"]["running"]:
            return
        JOBS["popular"]["running"] = True
    c = _conn()
    try:
        s = db.all_settings(c)
        res = popular.run_phase(c, s, llm_from_settings(s), log=lambda m, level="info": None, charts_only=charts_only)
        JOBS["popular"]["last"] = {"ok": True, "ts": db.now_iso(), **res}
    except Exception as e:  # noqa: BLE001
        JOBS["popular"]["last"] = {"ok": False, "ts": db.now_iso(), "error": str(e)[:300]}
    finally:
        c.close()
        JOBS["popular"]["running"] = False


@app.post("/api/videos/refresh")
def api_videos_refresh(payload: dict = Body(default={})) -> dict:
    """Собрать чарты и прогнать анализ прямо сейчас, в фоне. charts_only: без нейросети и кадров."""
    c = _conn()
    try:
        if not (db.get_setting(c, "youtube_api_key") or "").strip():
            raise HTTPException(400, "нет ключа YouTube Data API: задайте youtube_api_key в настройках сбора")
    finally:
        c.close()
    if JOBS["popular"]["running"]:
        return {"ok": True, "running": True}
    threading.Thread(target=_popular_job, args=(bool(payload.get("charts_only")),), name="popular", daemon=True).start()
    return {"ok": True, "running": True}


# ---------- конвейер: Кино-тренды → auto-shorts ----------
@app.get("/api/pipeline")
def api_pipeline(status: str | None = None, limit: int = 100) -> dict:
    c = _conn()
    try:
        out = pipeline.list_items(c, status=status, limit=min(limit, 500))
        out["status"] = pipeline.status(c, db.all_settings(c))
        return out
    finally:
        c.close()


@app.get("/api/pipeline/status")
def api_pipeline_status() -> dict:
    c = _conn()
    try:
        return pipeline.status(c, db.all_settings(c))
    finally:
        c.close()


@app.post("/api/pipeline/enqueue")
def api_pipeline_enqueue(payload: dict = Body(...)) -> dict:
    c = _conn()
    try:
        try:
            return pipeline.enqueue(c, str(payload.get("kind") or ""), str(payload.get("ref_id") or ""), db.all_settings(c), note=(payload.get("note") or None))
        except ValueError as e:
            raise HTTPException(400, str(e))
    finally:
        c.close()


@app.post("/api/pipeline/enqueue-today")
def api_pipeline_enqueue_today() -> dict:
    c = _conn()
    try:
        return pipeline.enqueue_today(c, db.all_settings(c))
    finally:
        c.close()


@app.post("/api/pipeline/tick")
def api_pipeline_tick() -> dict:
    """Сдвинуть очередь на шаг прямо сейчас."""
    c = _conn()
    try:
        return pipeline.tick(c, db.all_settings(c), log=lambda m, level="info": None)
    finally:
        c.close()


@app.post("/api/pipeline/{item_id}/retry")
def api_pipeline_retry(item_id: int) -> dict:
    c = _conn()
    try:
        d = pipeline.retry(c, item_id)
        if d is None:
            raise HTTPException(404, "нет такого задания")
        return d
    finally:
        c.close()


@app.post("/api/pipeline/{item_id}/cancel")
def api_pipeline_cancel(item_id: int) -> dict:
    c = _conn()
    try:
        d = pipeline.cancel(c, item_id)
        if d is None:
            raise HTTPException(404, "нет такого задания")
        return d
    finally:
        c.close()


@app.post("/api/pipeline/{item_id}/rights")
def api_pipeline_rights(item_id: int, payload: dict = Body(...)) -> dict:
    c = _conn()
    try:
        try:
            d = pipeline.set_rights(c, item_id, str(payload.get("rights_type") or ""))
        except ValueError as e:
            raise HTTPException(400, str(e))
        if d is None:
            raise HTTPException(404, "нет такого задания")
        return d
    finally:
        c.close()


@app.get("/api/today")
def api_today() -> dict:
    """Рабочий экран дня: кино по календарю на сегодня и завтра, подборка видео, кандидаты на проверку, счётчики."""
    c = _conn()
    try:
        v = schedule.view(c, days=2, back=0)
        today = [it for it in v["days"][0]["items"]] if v["days"] else []
        tomorrow = [it for it in v["days"][1]["items"]] if len(v["days"]) > 1 else []
        pv = popular.overview(c)
        lr = c.execute("SELECT started_at, finished_at, status FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return {"today": v["today"], "schedule": {"today": today, "tomorrow": tomorrow},
                "picks": popular.picks(c, limit=40), "candidates": popular.candidates_to_check(c),
                "counts": {"film_total": len([i for i in today if i["title_id"]]), "film_done": len([i for i in today if i["status"] == "done"]),
                           "picks": pv["picks"], "used": pv["used"], "clean": pv["levels"].get("clean", 0), "check": pv["levels"].get("check", 0), "videos": pv["total"]},
                "run": STATE.snapshot(), "last_run": dict(lr) if lr else None, "videos_job": JOBS["popular"]}
    finally:
        c.close()


@app.post("/api/videos/{video_id}/used")
def api_video_used(video_id: str, payload: dict = Body(default={})) -> dict:
    """Отметить ролик залитым (уходит из подборки) или вернуть."""
    c = _conn()
    try:
        d = popular.set_used(c, video_id, bool(payload.get("used", True)))
        if d is None:
            raise HTTPException(404, "нет такого ролика")
        return d
    finally:
        c.close()


@app.get("/api/videos/overview")
def api_videos_overview() -> dict:
    c = _conn()
    try:
        out = popular.overview(c)
        out["job"] = JOBS["popular"]
        return out
    finally:
        c.close()


@app.get("/api/videos/picks")
def api_videos_picks() -> dict:
    """Подборка для перезалива: ролик, ссылка, прямой mp4 из хранилища, кадры, уровень чистоты, совет по баннеру."""
    c = _conn()
    try:
        return {"items": popular.picks(c), "generated_at": db.now_iso()}
    finally:
        c.close()


@app.post("/api/videos/picks/prepare-files")
def api_videos_picks_files() -> dict:
    """Поставить на скачивание mp4 для всех роликов подборки без файла."""
    c = _conn()
    try:
        files.init(c)
        queued = []
        for it in popular.picks(c):
            if it.get("direct_file_url") or it.get("file_status") == "running":
                continue
            r = files.request(c, None, it["video_id"], it["source_url"])
            queued.append({"video_id": it["video_id"], "ok": r["ok"], "error": r.get("error")})
        return {"queued": queued}
    finally:
        c.close()


@app.get("/api/videos")
def api_videos(level: str | None = None, region: str | None = None, category: str | None = None, dur: int | None = None, lang: str | None = None,
               q: str | None = None, sort: str = "score", pick: int = 0, fit: float | None = None, short: int | None = None, limit: int = 50, offset: int = 0) -> dict:
    c = _conn()
    try:
        return popular.list_videos(c, level=level, region=region, category=category, dur=dur, lang=lang, q=q, sort=sort, pick=bool(pick), fit=fit, short=short,
                                   limit=min(limit, 200), offset=max(0, offset))
    finally:
        c.close()


@app.get("/api/videos/{video_id}/frames.jpg", include_in_schema=False)
def api_video_frames(video_id: str) -> Response:
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id):
        raise HTTPException(404)
    path = popular.FRAMES_DIR / f"{video_id}.jpg"
    if not path.exists():
        raise HTTPException(404, "кадры ещё не вынимались")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/videos/{video_id}")
def api_video(video_id: str) -> dict:
    c = _conn()
    try:
        d = popular.detail(c, video_id)
        if d is None:
            raise HTTPException(404, "нет такого ролика в популярных")
        d["cutter"] = popular.cutter_block(c, d)
        return d
    finally:
        c.close()


@app.post("/api/videos/{video_id}/pick")
def api_video_pick(video_id: str, payload: dict = Body(default={})) -> dict:
    c = _conn()
    try:
        d = popular.set_pick(c, video_id, bool(payload.get("pick", True)), (payload.get("note") or "").strip() or None)
        if d is None:
            raise HTTPException(404, "нет такого ролика")
        return d
    finally:
        c.close()


@app.post("/api/videos/{video_id}/level")
def api_video_level(video_id: str, payload: dict = Body(default={})) -> dict:
    c = _conn()
    try:
        try:
            d = popular.set_level(c, video_id, payload.get("level") or None, popular.cfg(db.all_settings(c)))
        except ValueError as e:
            raise HTTPException(400, str(e))
        if d is None:
            raise HTTPException(404, "нет такого ролика")
        return d
    finally:
        c.close()


@app.post("/api/videos/{video_id}/analyze")
def api_video_analyze(video_id: str, payload: dict = Body(default={})) -> dict:
    """Разобрать один ролик сейчас: текст, кадры с хранилища, оценка."""
    c = _conn()
    try:
        s = db.all_settings(c)
        d = popular.analyze_one(c, llm_from_settings(s), video_id, popular.cfg(s), with_frames=payload.get("frames", True) is not False)
        if d is None:
            raise HTTPException(404, "нет такого ролика")
        d["cutter"] = popular.cutter_block(c, d)
        return d
    finally:
        c.close()


@app.post("/api/videos/{video_id}/file")
def api_video_file(video_id: str) -> dict:
    c = _conn()
    try:
        d = popular.detail(c, video_id)
        if d is None:
            raise HTTPException(404, "нет такого ролика")
        files.init(c)
        r = files.request(c, None, video_id, d["url"])
        if not r["ok"]:
            raise HTTPException(400, r["error"])
        return r
    finally:
        c.close()


@app.post("/api/videos/{video_id}/assign")
def video_assign(video_id: str, payload: dict = Body(...)) -> dict:
    title_id = payload.get("title_id")
    learn = bool(payload.get("learn", True))
    channel_default = bool(payload.get("channel_default", False))
    reset = bool(payload.get("reset", False))
    c = _conn()
    try:
        v = c.execute("SELECT * FROM videos WHERE video_id = ?", (video_id,)).fetchone()
        if not v:
            raise HTTPException(404, "нет такого ролика")
        ts = db.now_iso()
        if reset:
            m = REC.recognize(v["title"], v["description"], db.loads(v["keywords"], []), None)
            c.execute("""UPDATE videos SET recog_manual = 0, title_id = ?, recog_conf = ?, recog_method = ?, recog_evidence = ?, recog_at = ?,
                         recog_candidates = ? WHERE video_id = ?""",
                      (m.title_id, m.conf if m.title_id else None, m.method, m.evidence, ts, result_json(m), video_id))
            c.commit()
            return {"video_id": video_id, "title_id": m.title_id, "conf": m.conf, "method": m.method}
        if title_id:
            t = c.execute("SELECT * FROM titles WHERE id = ?", (title_id,)).fetchone()
            if not t:
                raise HTTPException(404, "нет такого произведения")
            c.execute("""UPDATE videos SET title_id = ?, recog_conf = 1.0, recog_method = 'manual', recog_evidence = 'оператор', recog_manual = 1,
                         recog_at = ? WHERE video_id = ?""", (title_id, ts, video_id))
            learned = None
            if learn:
                from rapidfuzz import fuzz

                cands = db.loads(v["recog_candidates"], {}).get("candidates", [])
                label = t["label_ru"] or t["label_en"] or ""
                best = None
                for cand in cands:
                    if cand.startswith("#"):
                        continue
                    sc = fuzz.ratio(norm(cand), norm(label))
                    if sc >= 55 and (best is None or sc > best[1]):
                        best = (cand, sc)
                if best and len(norm(best[0])) >= 3:
                    c.execute("INSERT OR IGNORE INTO title_aliases(title_id, alias, norm, lang, source) VALUES (?,?,?,?,'learned')",
                              (title_id, best[0], norm(best[0]), "ru"))
                    REC.add_alias(title_id, best[0])
                    learned = best[0]
            if channel_default and v["channel_id"]:
                c.execute("UPDATE channels SET default_title_id = ? WHERE channel_id = ?", (title_id, v["channel_id"]))
                c.execute("""UPDATE videos SET title_id = ?, recog_conf = 0.75, recog_method = 'channel', recog_evidence = 'канал', recog_at = ?
                             WHERE channel_id = ? AND title_id IS NULL AND recog_manual = 0""", (title_id, ts, v["channel_id"]))
            c.commit()
            return {"video_id": video_id, "title_id": title_id, "learned_alias": learned}
        banned = None
        ban_text = (payload.get("ban") or "").strip()
        if ban_text:
            banned = REC.ban(ban_text)
            c.execute("INSERT OR IGNORE INTO alias_bans(norm, note, added_at) VALUES (?, ?, ?)", (banned, f"из ролика {video_id}", ts))
            # снимаем ту же ложную привязку у остальных роликов
            c.execute("""UPDATE videos SET title_id = NULL, recog_conf = NULL, recog_at = NULL
                         WHERE recog_manual = 0 AND title_id = ? AND lower(replace(replace(recog_evidence, '#', ''), 'ё', 'е')) = ?""",
                      (v["title_id"], ban_text.lstrip("#").lower().replace("ё", "е")))
        c.execute("""UPDATE videos SET title_id = NULL, recog_conf = NULL, recog_method = 'manual', recog_evidence = 'оператор: не фильм',
                     recog_manual = 1, recog_at = ? WHERE video_id = ?""", (ts, video_id))
        c.commit()
        return {"video_id": video_id, "title_id": None, "banned": banned}
    finally:
        c.close()


@app.get("/api/aliases/bans")
def alias_bans() -> dict:
    c = _conn()
    try:
        return {"items": [dict(r) for r in c.execute("SELECT * FROM alias_bans ORDER BY added_at DESC")]}
    finally:
        c.close()


@app.delete("/api/aliases/bans/{text}")
def alias_unban(text: str) -> dict:
    nz = norm(text)
    c = _conn()
    try:
        c.execute("DELETE FROM alias_bans WHERE norm = ?", (nz,))
        c.commit()
    finally:
        c.close()
    with REC.lock:
        REC.bans.discard(nz)
        REC.bans.discard(nz.replace(" ", ""))
    return {"deleted": nz}


@app.post("/api/channels/{channel_id}/default")
def channel_default(channel_id: str, payload: dict = Body(...)) -> dict:
    title_id = payload.get("title_id")
    c = _conn()
    try:
        if title_id and not c.execute("SELECT 1 FROM titles WHERE id = ?", (title_id,)).fetchone():
            raise HTTPException(404, "нет такого произведения")
        c.execute("UPDATE channels SET default_title_id = ? WHERE channel_id = ?", (title_id, channel_id))
        if title_id:
            c.execute("""UPDATE videos SET title_id = ?, recog_conf = 0.75, recog_method = 'channel', recog_evidence = 'канал', recog_at = ?
                         WHERE channel_id = ? AND title_id IS NULL AND recog_manual = 0""", (title_id, db.now_iso(), channel_id))
        c.commit()
        return {"channel_id": channel_id, "default_title_id": title_id}
    finally:
        c.close()


# ---------- источники, настройки, прогоны ----------
_CHANNEL_URL = re.compile(r"(UC[\w-]{20,})|youtube\.com/(@[\w.-]+)|^(@[\w.-]+)$")


@app.get("/api/sources")
def sources_list() -> dict:
    c = _conn()
    try:
        return {"items": [dict(r) for r in c.execute("SELECT * FROM sources ORDER BY kind, auto, id")]}
    finally:
        c.close()


@app.post("/api/sources")
def sources_add(payload: dict = Body(...)) -> dict:
    kind = payload.get("kind")
    value = (payload.get("value") or "").strip()
    if kind not in ("hashtag", "channel") or not value:
        raise HTTPException(400, "нужны kind (hashtag|channel) и value")
    label = None
    if kind == "hashtag":
        value = value.lstrip("#").lower().replace("ё", "е")
        if not re.fullmatch(r"[\w]+", value):
            raise HTTPException(400, "хэштег: только буквы и цифры")
        label = "#" + value
    else:
        m = _CHANNEL_URL.search(value)
        if not m:
            raise HTTPException(400, "канал: нужен id вида UC… или @handle")
        value = m.group(1) or m.group(2) or m.group(3)
        try:
            res = flat.channel_shorts(value, 1)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"канал не открылся: {e}")
        ch = res.get("channel") or {}
        if ch.get("channel_id"):
            value = ch["channel_id"]
        label = ch.get("name")
        if not value.startswith("UC"):
            raise HTTPException(400, "не удалось определить channel_id")
    c = _conn()
    try:
        cur = c.execute("INSERT OR IGNORE INTO sources(kind, value, label, enabled, auto, added_at) VALUES (?,?,?,1,0,?)", (kind, value, label, db.now_iso()))
        if cur.rowcount == 0:
            c.execute("UPDATE sources SET enabled = 1, auto = 0 WHERE kind = ? AND value = ?", (kind, value))
        if kind == "channel":
            c.execute("""INSERT INTO channels(channel_id, name, first_seen_at, last_seen_at) VALUES (?,?,?,?)
                         ON CONFLICT(channel_id) DO UPDATE SET name = COALESCE(excluded.name, channels.name)""", (value, label, db.now_iso(), db.now_iso()))
        c.commit()
        return dict(c.execute("SELECT * FROM sources WHERE kind = ? AND value = ?", (kind, value)).fetchone())
    finally:
        c.close()


@app.patch("/api/sources/{source_id}")
def sources_patch(source_id: int, payload: dict = Body(...)) -> dict:
    c = _conn()
    try:
        if "enabled" in payload:
            c.execute("UPDATE sources SET enabled = ? WHERE id = ?", (int(bool(payload["enabled"])), source_id))
        c.commit()
        row = c.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        return dict(row)
    finally:
        c.close()


@app.delete("/api/sources/{source_id}")
def sources_delete(source_id: int) -> dict:
    c = _conn()
    try:
        c.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        c.commit()
        return {"deleted": source_id}
    finally:
        c.close()


@app.get("/api/settings")
def settings_get() -> dict:
    c = _conn()
    try:
        s = db.all_settings(c)
        for k in SECRET_KEYS:
            s[k] = "••••" + s[k][-4:] if s.get(k) else ""
        return {"settings": s, "defaults": DEFAULT_SETTINGS}
    finally:
        c.close()


@app.put("/api/settings")
def settings_put(payload: dict = Body(...)) -> dict:
    c = _conn()
    try:
        for k, v in payload.items():
            if k not in DEFAULT_SETTINGS:
                raise HTTPException(400, f"неизвестная настройка {k}")
            if k in SECRET_KEYS:
                if str(v).startswith("••••"):
                    continue
            elif k in TEXT_KEYS:
                if k == "rights_policy" and str(v).strip() not in rights.POLICY_CHOICES:
                    raise HTTPException(400, "rights_policy: strict|balanced|off")
            else:
                try:
                    int(v)
                except (TypeError, ValueError):
                    raise HTTPException(400, f"{k}: нужно число")
            db.set_setting(c, k, str(v).strip())
        return {"ok": True}
    finally:
        c.close()


@app.get("/api/runs")
def runs_list(limit: int = 30) -> dict:
    c = _conn()
    try:
        rows = []
        for r in c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)):
            d = dict(r)
            d["stats"] = db.loads(r["stats"], {})
            rows.append(d)
        return {"items": rows, "current": STATE.snapshot(), "jobs": JOBS}
    finally:
        c.close()


@app.get("/api/runs/{run_id}/log")
def runs_log(run_id: int, limit: int = 300) -> dict:
    c = _conn()
    try:
        rows = [dict(r) for r in c.execute("SELECT ts, level, msg FROM run_log WHERE run_id = ? ORDER BY id DESC LIMIT ?", (run_id, limit))]
        return {"items": rows[::-1]}
    finally:
        c.close()


@app.post("/api/runs")
def runs_start() -> dict:
    run_id = _start_run("manual")
    return {"run_id": run_id, "state": STATE.snapshot()}


@app.post("/api/runs/cancel")
def runs_cancel() -> dict:
    STATE.cancel.set()
    return {"cancelling": STATE.running}


@app.get("/api/dict/status")
def dict_status() -> dict:
    c = _conn()
    try:
        total_items = len(json.loads(ITEMS_PATH.read_text())) if ITEMS_PATH.exists() else 0
        fetched = 0
        if ENTITIES_PATH.exists():
            with ENTITIES_PATH.open("rb") as f:
                fetched = sum(1 for _ in f)
        return {
            "items_total": total_items, "entities_fetched": fetched,
            "titles_in_db": c.execute("SELECT COUNT(*) FROM titles").fetchone()[0],
            "aliases_in_db": c.execute("SELECT COUNT(*) FROM title_aliases").fetchone()[0],
            "manual_titles": c.execute("SELECT COUNT(*) FROM titles WHERE is_manual = 1").fetchone()[0],
            "learned_aliases": c.execute("SELECT COUNT(*) FROM title_aliases WHERE source IN ('learned','manual')").fetchone()[0],
            "imported_at": db.get_meta(c, "dict_imported_at", ""),
            "recognizer": {"ready": REC.size > 0, "titles": REC.size, "loaded_at": REC.loaded_at},
            "jobs": JOBS,
        }
    finally:
        c.close()


@app.post("/api/dict/import")
def dict_import() -> dict:
    threading.Thread(target=_import_dict_job, name="dict-import", daemon=True).start()
    return {"started": True}


@app.post("/api/recognize")
def recognize_now(force: bool = False) -> dict:
    threading.Thread(target=_recognize_job, args=(force,), name="recognize", daemon=True).start()
    return {"started": True, "force": force}


# ---------- рекомендации, календарь, нейро, бриф ----------
@app.get("/api/recommend")
def api_recommend(period: str = "30", min_conf: float = 0.6, min_views: int = 0, lang: str = "ru", limit: int = 12) -> dict:
    c = _conn()
    try:
        return recommend.today(c, _filters(period, min_conf, min_views, lang), limit=min(limit, 50))
    finally:
        c.close()


@app.get("/api/schedule")
def api_schedule(days: int = 7, back: int = 3) -> dict:
    c = _conn()
    try:
        return schedule.view(c, days=min(days, 31), back=min(back, 14))
    finally:
        c.close()


@app.post("/api/schedule/build")
def api_schedule_build(payload: dict = Body(default={})) -> dict:
    c = _conn()
    try:
        s = db.all_settings(c)
        return schedule.build(c, stats.Filters("30", lang="ru"), days=int(s.get("schedule_days") or 7), per_day=int(s.get("schedule_per_day") or 2),
                              cooldown=int(s.get("schedule_cooldown_days") or 10), rebuild=bool(payload.get("rebuild")))
    finally:
        c.close()


@app.post("/api/schedule/{item_id}/status")
def api_schedule_status(item_id: int, payload: dict = Body(...)) -> dict:
    c = _conn()
    try:
        schedule.set_status(c, item_id, payload.get("status", "planned"))
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        c.close()


@app.post("/api/schedule/{item_id}/replace")
def api_schedule_replace(item_id: int, payload: dict = Body(...)) -> dict:
    c = _conn()
    try:
        tid = payload.get("title_id")
        if tid and not c.execute("SELECT 1 FROM titles WHERE id = ?", (tid,)).fetchone():
            raise HTTPException(404, "нет такого произведения")
        schedule.replace(c, item_id, tid, stats.Filters("30", lang="ru"))
        return {"ok": True}
    finally:
        c.close()


@app.post("/api/schedule/add")
def api_schedule_add(payload: dict = Body(...)) -> dict:
    c = _conn()
    try:
        day, tid = payload.get("day"), payload.get("title_id")
        if not day or not tid or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise HTTPException(400, "нужны day и title_id")
        if not c.execute("SELECT 1 FROM titles WHERE id = ?", (tid,)).fetchone():
            raise HTTPException(404, "нет такого произведения")
        return {"id": schedule.add_slot(c, day, tid, stats.Filters("30", lang="ru"))}
    finally:
        c.close()


@app.get("/api/neuro/status")
def api_neuro_status() -> dict:
    c = _conn()
    try:
        s = db.all_settings(c)
        llm = llm_from_settings(s)
        return {
            "configured": llm.configured, "model": s.get("llm_model"), "enabled": s.get("neuro_enabled") == "1",
            "usage_today": usage_today(c), "daily_budget_usd": float(s.get("neuro_daily_usd") or 0),
            "pending": c.execute("""SELECT COUNT(*) FROM videos WHERE details_at IS NOT NULL AND status = 'ok' AND recog_manual = 0 AND neuro_at IS NULL
                                    AND lang IN ('ru','uk','kk') AND (title_id IS NULL OR recog_conf < 0.6) AND COALESCE(views,0) >= ?""",
                                (int(s.get("neuro_min_views") or 0),)).fetchone()[0],
            "done": c.execute("SELECT COUNT(*) FROM videos WHERE neuro_at IS NOT NULL").fetchone()[0],
            "recognized": c.execute("SELECT COUNT(*) FROM videos WHERE recog_method = 'neuro' AND title_id IS NOT NULL").fetchone()[0],
            "created_titles": c.execute("SELECT COUNT(*) FROM titles WHERE source = 'neuro'").fetchone()[0],
            "job": JOBS["neuro"],
        }
    finally:
        c.close()


@app.post("/api/neuro/run")
def api_neuro_run(limit: int | None = None) -> dict:
    if JOBS["neuro"]["running"] or STATE.running:
        raise HTTPException(409, "нейро-опознание уже идёт или идёт сбор")
    threading.Thread(target=_neuro_job, args=(limit,), name="neuro", daemon=True).start()
    return {"started": True}


@app.post("/api/neuro/test")
def api_neuro_test() -> dict:
    c = _conn()
    try:
        llm = llm_from_settings(db.all_settings(c))
        if not llm.configured:
            raise HTTPException(400, "модель не настроена")
        try:
            ans, usage = llm.ask_json("Отвечай только JSON.", 'Верни {"ok": true, "model": "название модели, если знаешь"}', max_tokens=60)
        except LLMError as e:
            raise HTTPException(502, f"модель не ответила: {e}")
        return {"ok": True, "answer": ans, "cost_usd": usage.cost_usd, "tokens": usage.prompt_tokens + usage.completion_tokens}
    finally:
        c.close()


@app.get("/api/films/{title_id}/insights")
def api_insights(title_id: str) -> dict:
    c = _conn()
    try:
        return {"insights": neuro.get_insights(c, title_id)}
    finally:
        c.close()


@app.post("/api/films/{title_id}/insights")
def api_insights_generate(title_id: str, period: str = "all", min_conf: float = 0.6, lang: str = "all") -> dict:
    c = _conn()
    try:
        llm = llm_from_settings(db.all_settings(c))
        if not llm.configured:
            raise HTTPException(400, "модель не настроена: раздел «Сбор» → нейросеть")
        d = stats.film_detail(c, _filters(period, min_conf, 0, lang), title_id)
        if d is not None and len(d["videos"]) < 2 and period != "all":
            d = stats.film_detail(c, _filters("all", min_conf, 0, lang), title_id)
        if d is None:
            raise HTTPException(404, "нет такого произведения")
        if len(d["videos"]) < 2:
            raise HTTPException(400, "мало роликов для разбора, нужно хотя бы два")
        try:
            return {"insights": neuro.title_insights(c, llm, title_id, d["videos"], d["title"])}
        except LLMError as e:
            raise HTTPException(502, f"модель не ответила: {e}")
    finally:
        c.close()


@app.get("/api/films/{title_id}/interest")
def api_interest(title_id: str, refresh: bool = False) -> dict:
    from . import interest
    c = _conn()
    try:
        if refresh:
            t = c.execute("SELECT id, label_ru, kind FROM titles WHERE id = ?", (title_id,)).fetchone()
            if not t:
                raise HTTPException(404, "нет такого произведения")
            interest.update_wiki(c, [title_id], max_age_hours=0, log=lambda m, level="info": None)
            interest.update_trends(c, [{"title_id": title_id, "label": t["label_ru"], "kind": t["kind"]}], max_age_hours=0, log=lambda m, level="info": None)
        return recommend._interest_block(c, title_id)
    finally:
        c.close()


@app.get("/api/films/{title_id}/sources")
def api_sources(title_id: str) -> dict:
    c = _conn()
    try:
        return {"items": sources.get_sources(c, title_id), "checked_at": sources.checked_at(c, title_id)}
    finally:
        c.close()


@app.post("/api/films/{title_id}/sources")
def api_sources_find(title_id: str) -> dict:
    c = _conn()
    try:
        t = c.execute("SELECT id, label_ru, label_en, year, kind FROM titles WHERE id = ?", (title_id,)).fetchone()
        if not t:
            raise HTTPException(404, "нет такого произведения")
        llm = llm_from_settings(db.all_settings(c))
        items = sources.find_sources(c, llm if llm.configured else None, {"title_id": t["id"], "label": t["label_ru"] or t["label_en"], "label_en": t["label_en"], "year": t["year"], "kind": t["kind"]},
                                     log=lambda m, level="info": None)
        return {"items": items, "checked_at": sources.checked_at(c, title_id)}
    finally:
        c.close()


@app.get("/api/films/{title_id}/explain")
def api_explain(title_id: str) -> dict:
    c = _conn()
    try:
        return {"explain": neuro.get_explain(c, title_id)}
    finally:
        c.close()


@app.post("/api/films/{title_id}/explain")
def api_explain_generate(title_id: str) -> dict:
    c = _conn()
    try:
        llm = llm_from_settings(db.all_settings(c))
        if not llm.configured:
            raise HTTPException(400, "модель не настроена")
        f = stats.Filters("30", lang="ru")
        item = next((it for it in recommend.today(c, f, limit=400)["items"] if it["title_id"] == title_id), None)
        if item is None:
            item = next((it for it in recommend.today(c, stats.Filters("all"), limit=2000)["items"] if it["title_id"] == title_id), None)
        if item is None:
            raise HTTPException(404, "нет статистики по произведению")
        try:
            text = neuro.explain_title(c, llm, item, item["plan"].get("refs", []), sources.get_sources(c, title_id))
        except LLMError as e:
            raise HTTPException(502, f"модель не ответила: {e}")
        return {"explain": {"text": text, "generated_at": db.now_iso()}}
    finally:
        c.close()


@app.get("/api/films/{title_id}/rights")
def api_rights(title_id: str) -> dict:
    """Оценка по авторским правам: пересчитывается по сохранённым уликам при каждом запросе."""
    c = _conn()
    try:
        a = rights.evaluate(c, title_id)
        if a is None:
            raise HTTPException(404, "нет такого произведения")
        return a
    finally:
        c.close()


@app.post("/api/films/{title_id}/rights/refresh")
def api_rights_refresh(title_id: str) -> dict:
    """Полная перепроверка одного произведения: модель о правообладателе, страницы чужих нарезок, живость исходников."""
    c = _conn()
    try:
        t = rights.title_info(c, title_id)
        if t is None:
            raise HTTPException(404, "нет такого произведения")
        notes = []
        llm = llm_from_settings(db.all_settings(c))
        if llm.configured:
            try:
                rights.llm_assess(c, llm, t)
            except LLMError as e:
                notes.append(f"модель не ответила: {e}")
        else:
            notes.append("модель не настроена: правообладатель не определяется")
        r = rights.recheck_videos(c, [title_id], budget=60, min_age_days=0)
        notes.append(f"перепроверено чужих нарезок: {r['checked']}, снесено: {r['removed']}")
        s = rights.check_sources_alive(c, [title_id])
        if s["checked"]:
            notes.append(f"исходников проверено: {s['checked']}, удалённых: {s['dead']}")
        a = rights.evaluate(c, title_id)
        a["notes"] = notes
        return a
    finally:
        c.close()


@app.post("/api/films/{title_id}/rights/manual")
def api_rights_manual(title_id: str, payload: dict = Body(default={})) -> dict:
    c = _conn()
    try:
        if not c.execute("SELECT 1 FROM titles WHERE id = ?", (title_id,)).fetchone():
            raise HTTPException(404, "нет такого произведения")
        try:
            return rights.set_manual(c, title_id, payload.get("level") or None, (payload.get("note") or "").strip() or None)
        except ValueError as e:
            raise HTTPException(400, str(e))
    finally:
        c.close()


@app.post("/api/rights/events")
def api_rights_event(payload: dict = Body(...)) -> dict:
    """Оператор или нарезчик сообщает, что случилось с нашим роликом: claim | block | strike | ok. Оценка пересчитывается сразу."""
    c = _conn()
    try:
        tid = payload.get("title_id")
        if not tid:
            raise HTTPException(400, "нужен title_id")
        try:
            a = rights.add_event(c, tid, str(payload.get("kind") or ""), source=str(payload.get("source") or "operator"),
                                 claimant=(payload.get("claimant") or None), video_url=(payload.get("video_url") or None), note=(payload.get("note") or None))
        except ValueError as e:
            raise HTTPException(400, str(e))
        if a is None:
            raise HTTPException(404, "нет такого произведения")
        return a
    finally:
        c.close()


@app.get("/api/rights/overview")
def api_rights_overview() -> dict:
    c = _conn()
    try:
        return rights.overview(c)
    finally:
        c.close()


@app.get("/api/brief/today")
def api_brief_today() -> dict:
    c = _conn()
    try:
        f = stats.Filters("30", lang="ru")
        v = schedule.view(c, days=2, back=0)
        out = []
        for d in v["days"]:
            for it in d["items"]:
                if it["title_id"] and it["status"] != "skipped":
                    b = recommend.brief(c, f, it["title_id"])
                    if b:
                        b["day"] = d["day"]
                        b["schedule_id"] = it["id"]
                        out.append(b)
        return {"today": v["today"], "items": out}
    finally:
        c.close()


@app.get("/api/brief/{title_id}")
def api_brief(title_id: str, period: str = "30", min_conf: float = 0.6, lang: str = "ru") -> dict:
    c = _conn()
    try:
        b = recommend.brief(c, _filters(period, min_conf, 0, lang), title_id)
        if b is None:
            raise HTTPException(404, "нет такого произведения")
        return b
    finally:
        c.close()


@app.get("/api/films/{title_id}/poster.jpg", include_in_schema=False)
def api_poster(title_id: str) -> Response:
    if not re.fullmatch(r"[A-Za-z0-9_]+", title_id):
        raise HTTPException(404)
    c = _conn()
    try:
        p = posters.fetch_poster(c, title_id)
    finally:
        c.close()
    if not p:
        raise HTTPException(404)
    media = {"png": "image/png", "webp": "image/webp"}.get(p.suffix.lstrip("."), "image/jpeg")
    return FileResponse(p, media_type=media, headers={"Cache-Control": "public, max-age=2592000"})


@app.get("/api/notifications")
def api_notifications(limit: int = 20) -> dict:
    c = _conn()
    try:
        return {"items": [dict(r) for r in c.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,))]}
    finally:
        c.close()


app.include_router(notify.router)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "recognizer": REC.size, "running": STATE.running}
