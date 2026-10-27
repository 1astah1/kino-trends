"""Конвейер в auto-shorts: права, постановка в очередь, машина состояний на подменённом клиенте."""
import os
from pathlib import Path

import pytest

from kinotrends import db, files, pipeline
from kinotrends.yt import dataapi
from kinotrends import popular

TMP = Path(os.environ["KT_VAR"])


class FakeAS:
    """auto-shorts в миниатюре: источник готов со второго запроса, клип ждёт ревью, пакет публикуется после отметки."""
    configured = True

    def __init__(self):
        self.checked = []
        self.calls = []
        self.source_polls = 0
        self.job_status = "processing"
        self.pub_ref = None              # как в API auto-shorts: одна ссылка на заливку, поле publication
        self.ap = None                   # блок autopublish пакета
        self.no_package = False          # пакет ещё не собран: GET /jobs/{id}/package → 404

    def health(self):
        return {"status": "ok"}

    def source_from_url(self, url, title, niche_id, rights, options):
        self.calls.append(("source", url, rights["rights_type"], options["max_clip_sec"]))
        return {"id": 1}

    def source(self, sid):
        self.source_polls += 1
        return {"id": sid, "status": "ready" if self.source_polls >= 2 else "transcribing", "duration_sec": 40.0}

    def candidates(self, sid):
        return [{"id": 7, "start_sec": 3.0, "end_sec": 33.0, "final_score": 0.9, "blocked": False}]

    def clips(self, sid, items):
        self.calls.append(("clips", items))
        return [{"id": 11}]

    def checklist(self, pid, code, done=True):
        self.checked.append((pid, code, done))
        return {"id": pid}

    def job(self, jid):
        return {"id": jid, "status": self.job_status}

    def retry_job(self, jid):
        self.calls.append(("retry", jid))
        self.job_status = "processing"
        return {"id": jid}

    def review(self, jid, decision, expected, comment=None):
        self.calls.append(("review", decision))
        self.job_status = "approved"
        return {}

    def package_of_job(self, jid):
        if self.no_package:
            raise pipeline.ASError("GET /api/v1/jobs/x/package: HTTP 404: У ролика ещё нет пакета")
        return {"id": 5}

    def package(self, pid):
        return {"id": pid, "publication": self.pub_ref, "autopublish": self.ap}

    def autopublish(self, pid, now=True, privacy="public"):
        self.calls.append(("autopublish", pid, privacy))
        return {}

    def publication(self, pub_id):
        return {"id": pub_id, "url": "https://youtube.com/shorts/x", "latest": {"views": 1200, "retention_3s": 0.8}, "age_views": {"h24": 1000}, "status": "live"}

    def channels(self):
        return [{"id": "kadr-dnya", "title": "Кадр дня"}]


def api_item(vid):
    return {"id": vid, "snippet": {"title": "Прикол " + vid, "description": "", "channelId": "UC1", "channelTitle": "Канал", "categoryId": "20", "publishedAt": "2026-10-20T10:00:00Z",
                                   "tags": [], "defaultAudioLanguage": "ru", "thumbnails": {}},
            "contentDetails": {"duration": "PT40S", "licensedContent": False, "caption": "false", "definition": "hd"},
            "statistics": {"viewCount": "90000", "likeCount": "100", "commentCount": "3"},
            "status": {"license": "creativeCommon", "embeddable": True, "madeForKids": False}, "topicDetails": {}}


@pytest.fixture(scope="module")
def conn():
    c = db.connect(TMP / "pipeline.sqlite")
    db.init_db(c)
    files.init(c)
    ts = db.now_iso()
    items = [dataapi.parse_item(api_item("ccshort00001")), dataapi.parse_item(api_item("plainvid0002"))]
    items[1]["license"] = "youtube"
    popular.upsert(c, items, "cc", ts)
    c.execute("UPDATE pop_videos SET is_short = 1, level = 'clean', score = 80, pick = 1")
    c.execute("INSERT INTO downloads(title_id, video_id, url, status, path, size, created_at, finished_at) VALUES (NULL, 'ccshort00001', 'u', 'done', 'remote:/srv/files/ccshort00001.mp4', 1000, ?, ?)", (ts, ts))
    db.set_setting(c, "storage_host", "1.2.3.4")
    db.set_setting(c, "storage_url", "http://1.2.3.4/files")
    db.set_setting(c, "autoshorts_url", "http://127.0.0.1:1")
    db.set_setting(c, "autoshorts_login", "x")
    db.set_setting(c, "autoshorts_password", "y")
    c.commit()
    return c


def test_rights_mapping():
    s = {}
    assert pipeline.rights_for({"license": "creativeCommon", "channel": "Автор", "source_url": "u"}, s)["rights_type"] == "cc_by"
    assert pipeline.rights_for({"license": "youtube", "channel": "Автор", "source_url": "u"}, s) is None
    assert pipeline.rights_for({"license": "youtube", "channel": "Автор", "source_url": "u"}, {"pipeline_rights_default": "licensed_platform"})["rights_type"] == "licensed_platform"
    assert pipeline.rights_for({"license": "youtube", "rights_type": "own", "source_url": "u"}, s)["rights_type"] == "own"


def test_enqueue_and_state_machine(conn):
    s = db.all_settings(conn)
    s["pipeline_auto_review"] = "1"
    it = pipeline.enqueue(conn, "video", "ccshort00001", s, note="тест")
    assert it["status"] == "new" and it["direct_url"].startswith("http://1.2.3.4/files/") and it["is_short"] == 1
    assert pipeline.enqueue(conn, "video", "ccshort00001", s)["id"] == it["id"]  # повтор не плодит заданий
    fake = FakeAS()
    assert pipeline.tick(conn, s, log=lambda *a, **k: None, asc=fake)["sent"] == 1
    assert pipeline._row(conn, it["id"])["status"] == "sent" and fake.calls[0][2] == "cc_by"
    pipeline.tick(conn, s, log=lambda *a, **k: None, asc=fake)           # источник ещё транскрибируется
    assert pipeline._row(conn, it["id"])["status"] == "sent"
    pipeline.tick(conn, s, log=lambda *a, **k: None, asc=fake)           # готов: шортс одним клипом без нарезки
    cur = pipeline._row(conn, it["id"])
    assert cur["status"] == "cutting" and cur["as_jobs"] == [11]
    clips = next(c for c in fake.calls if c[0] == "clips")[1]
    assert clips[0]["start_sec"] == 0.0 and clips[0]["end_sec"] == 40.0 and "candidate_id" not in clips[0] and clips[0]["channel_id"] == "kadr-dnya"
    pipeline.tick(conn, s, log=lambda *a, **k: None, asc=fake)           # клип ещё в работе
    assert pipeline._row(conn, it["id"])["status"] == "cutting"
    fake.job_status = "pending_review"
    pipeline.tick(conn, s, log=lambda *a, **k: None, asc=fake)           # автоодобрение → пакет → ждёт залива
    cur = pipeline._row(conn, it["id"])
    assert cur["status"] == "approved" and ("review", "approve") in fake.calls and cur["as_packages"] == [5]
    fake.pub_ref = {"id": 9, "url": None, "status": "live"}
    pipeline.tick(conn, s, log=lambda *a, **k: None, asc=fake)
    cur = pipeline._row(conn, it["id"])
    assert cur["status"] == "published" and cur["as_publications"] == [9]
    pipeline.tick(conn, s, log=lambda *a, **k: None, asc=fake)           # метрики
    cur = pipeline._row(conn, it["id"])
    assert cur["metrics"]["publications"][0]["views"] == 1200
    lst = pipeline.list_items(conn)
    assert lst["counts"]["published"] == 1


def test_needs_rights_then_set(conn, monkeypatch):
    s = db.all_settings(conn)
    monkeypatch.setattr(pipeline.files, "request", lambda *a, **k: {"ok": True})  # хранилище не трогаем
    it = pipeline.enqueue(conn, "video", "plainvid0002", s)
    assert it["status"] == "file_pending"  # файла ещё нет
    conn.execute("INSERT INTO downloads(title_id, video_id, url, status, path, size, created_at, finished_at) VALUES (NULL, 'plainvid0002', 'u', 'done', 'remote:/srv/files/plainvid0002.mp4', 1000, ?, ?)", (db.now_iso(), db.now_iso()))
    conn.commit()
    fake = FakeAS()
    pipeline.tick(conn, s, log=lambda *a, **k: None, asc=fake)   # file_pending → new
    pipeline.tick(conn, s, log=lambda *a, **k: None, asc=fake)   # new → needs_rights (не CC, умолчания нет)
    assert pipeline._row(conn, it["id"])["status"] == "needs_rights"
    assert pipeline.set_rights(conn, it["id"], "licensed_platform")["status"] == "new"
    pipeline.tick(conn, s, log=lambda *a, **k: None, asc=fake)
    assert pipeline._row(conn, it["id"])["status"] == "sent" and fake.calls[-1][2] == "licensed_platform"
    assert pipeline.cancel(conn, it["id"])["status"] == "cancelled"
    assert pipeline.retry(conn, it["id"])["status"] == "sent"


def test_pick_banner_variant():
    s = {"pipeline_banner_bottom": "1", "pipeline_banner_top": "2"}
    assert pipeline.pick_banner_variant(None, s) == 1
    assert pipeline.pick_banner_variant(["низ", "верх"], s) == 1
    assert pipeline.pick_banner_variant(["верх"], s) == 2
    assert pipeline.pick_banner_variant(["лево"], s) == 1
    assert pipeline.pick_banner_variant(["верх"], {"pipeline_banner_bottom": "1"}) == 1
    assert pipeline.pick_banner_variant(["верх"], {}) is None


def test_tick_without_config(conn):
    out = pipeline.tick(conn, {"autoshorts_url": ""}, log=lambda *a, **k: None)
    assert "не настроен" in out["skipped"]


def test_attribution_text_is_public_and_without_storage_links():
    from kinotrends import pipeline

    st = {"public_url": "http://185.102.115.55/files"}
    film = {"kind": "film", "label": "Брестская крепость", "channel": "Киностудия Беларусьфильм",
            "source_url": "https://www.youtube.com/watch?v=jUzGyns9TCE", "metrics": '{"year": 2010, "title_kind": "film"}'}
    assert pipeline.attribution_text(film, "licensed_platform", st) == "фрагмент фильма «Брестская крепость» (2010)"
    cartoon = dict(film, label="Суперсемейка", metrics='{"year": 2004, "title_kind": "animated"}')
    assert pipeline.attribution_text(cartoon, "licensed_platform", st) == "фрагмент мультфильма «Суперсемейка» (2004)"
    video = {"kind": "video", "label": "Ролик", "channel": "Андроид Плюс", "source_url": "https://www.youtube.com/watch?v=SzcngC9Bd5I"}
    assert pipeline.attribution_text(video, "licensed_platform", st) == "фрагмент видео канала «Андроид Плюс»"
    assert pipeline.attribution_text(video, "cc_by", st) == "автор Андроид Плюс (https://www.youtube.com/watch?v=SzcngC9Bd5I), лицензия CC BY"
    storage = dict(video, source_url="http://185.102.115.55/files/sample-t34.mp4")
    assert pipeline.attribution_text(storage, "cc_by", st) == "автор Андроид Плюс, лицензия CC BY"
    assert pipeline.attribution_text(video, "own", st) is None
    assert pipeline.rights_for(video, {"pipeline_rights_default": "licensed_platform", **st})["attribution_text"].startswith("фрагмент видео")


def test_film_ready_flags_and_reasons():
    from kinotrends.api import film_ready

    src = {"video_id": "x", "verified": True, "file_status": "done", "file_error": None}
    assert film_ready("ok", src) == (True, [])
    assert film_ready("caution", src) == (True, [])
    ok, why = film_ready("block", None)
    assert not ok and "права: нельзя" in why and "нет исходника" in why
    geo = dict(src, file_status="error", file_error="видео недоступно из страны хранилища (гео-блок автора)")
    assert film_ready("ok", geo) == (False, ["исходник закрыт по странам"])
    assert film_ready("ok", dict(src, verified=False))[1] == ["исходник не подтверждён"]
    assert film_ready("ok", src, "cutting")[1] == ["уже в конвейере (cutting)"]
    assert film_ready("ok", src, "failed") == (True, [])


def test_top_candidates_prefers_popular_unblocked_titles_without_sources(monkeypatch):
    from kinotrends import sources

    items = [
        {"title_id": "A", "label": "Фильм без исходника", "kind": "film", "year": 2020, "score": 90},
        {"title_id": "B", "label": "Сериал с исходником", "kind": "series", "year": 2021, "score": 85},
        {"title_id": "C", "label": "Нельзя по правам", "kind": "film", "year": 2019, "score": 80},
        {"title_id": "D", "label": "Франшиза", "kind": "franchise", "year": None, "score": 79},
        {"title_id": "E", "label": "Аниме без прав-проверки", "kind": "anime", "year": 2022, "score": 70},
    ]
    monkeypatch.setattr(sources.stats if hasattr(sources, "stats") else __import__("kinotrends.stats", fromlist=["films"]), "films",
                        lambda conn, f, limit=400, sort="score", **kw: {"items": items, "total": len(items)})
    monkeypatch.setattr(__import__("kinotrends.rights", fromlist=["levels"]), "levels",
                        lambda conn, ids: {"A": {"level": "ok"}, "B": {"level": "ok"}, "C": {"level": "block"}})
    monkeypatch.setattr(sources, "get_sources", lambda conn, tid: [{"verified": 1}] if tid == "B" else [])
    picked = sources.top_candidates(None, limit=10)
    assert [t["title_id"] for t in picked] == ["A", "E"]
    assert picked[0]["label"] == "Фильм без исходника" and picked[1]["kind"] == "anime"
    assert sources.top_candidates(None, limit=1)[0]["title_id"] == "A"


def test_retry_of_file_item_requests_fresh_download(conn, monkeypatch):
    conn.execute("""INSERT INTO pipeline_items(kind, ref_id, label, channel, source_url, source_video_id, direct_url, license, is_short, duration_s, hint,
                    clip_min_s, clip_max_s, niche_id, channel_slug, status, note, metrics, created_at, updated_at)
                    VALUES ('video','vid1','Ролик','Канал','https://www.youtube.com/watch?v=vid1','vid1',NULL,NULL,1,30,NULL,NULL,58,'vpn','ch','failed',
                    NULL, '{"file_retry": "2026-10-24T14:50:22Z"}', '2026-10-24T14:00:00Z', '2026-10-24T14:00:00Z')""")
    conn.commit()
    item_id = conn.execute("SELECT id FROM pipeline_items WHERE ref_id = 'vid1'").fetchone()["id"]
    calls = []
    monkeypatch.setattr(files, "init", lambda c: None)
    monkeypatch.setattr(files, "request", lambda c, title_id, video_id, url: calls.append((title_id, video_id, url)) or {"ok": True, "status": {"status": "running"}})
    out = pipeline.retry(conn, item_id)
    assert out["status"] == "file_pending" and out["last_error"] is None
    assert calls == [(None, "vid1", "https://www.youtube.com/watch?v=vid1")]
    assert "file_retry" not in (db.loads(out["metrics"], {}) if isinstance(out["metrics"], str) else (out["metrics"] or {}))


def test_short_error_recognises_geo_block_tail():
    from kinotrends.files import _short_error

    assert _short_error("…Zambia, Zimbabwe\nYou might want to use a VPN or a proxy server (with --proxy) to workaround.") == (
        "видео недоступно из страны хранилища (гео-блок автора)"
    )
    assert _short_error("ERROR: [youtube] x: The uploader has not made this video available in your country. This video is available in Albania, …").startswith("видео недоступно")
    assert _short_error("ERROR: [youtube] x: Sign in to confirm you’re not a bot.").startswith("YouTube требует вход")


def test_film_sources_summary_shortens_legacy_errors(conn):
    from kinotrends.api import film_sources_summary

    files.init(conn)
    conn.execute("INSERT OR IGNORE INTO titles(id, label_ru, label_en, year, kind, updated_at) VALUES ('T1','Фильм','Film',2020,'film','2026-10-24T00:00:00Z')")
    conn.execute("""INSERT INTO title_sources(title_id, video_id, url, label, channel, duration_s, views, kind, language, trust, verified, score, found_at)
                    VALUES ('T1','v1','https://www.youtube.com/watch?v=v1','Фильм','Канал',5400,10,'film','русский дубляж',0.9,1,9.0,'2026-10-24T00:00:00Z')""")
    conn.execute("""INSERT INTO downloads(title_id, video_id, url, status, error, created_at)
                    VALUES ('T1','v1','https://www.youtube.com/watch?v=v1','error', ?, '2026-10-24T00:00:00Z')""",
                 ("ERROR: [youtube] v1: Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies for the authentication. " * 2,))
    conn.commit()
    out = film_sources_summary(conn, ["T1"])
    assert out["T1"]["verified"] is True and out["T1"]["file_status"] == "error"
    assert out["T1"]["file_error"].startswith("YouTube требует вход")


def test_enqueue_skips_source_blocked_by_geo(conn, monkeypatch):
    from kinotrends import popular

    conn.execute("""INSERT INTO pipeline_items(kind, ref_id, label, channel, source_url, source_video_id, direct_url, license, is_short, duration_s, hint,
                    clip_min_s, clip_max_s, niche_id, channel_slug, status, note, metrics, created_at, updated_at)
                    VALUES ('video','geo1','Ролик','Канал','https://www.youtube.com/watch?v=geo1','geo1',NULL,NULL,1,30,NULL,NULL,58,'vpn','ch','failed',
                    NULL, NULL, '2026-10-24T14:00:00Z', '2026-10-24T14:00:00Z')""")
    conn.execute("UPDATE pipeline_items SET last_error = 'файл не скачался: видео недоступно из страны хранилища (гео-блок автора)' WHERE ref_id = 'geo1'")
    conn.commit()
    monkeypatch.setattr(popular, "detail", lambda c, vid: {"video_id": vid, "title": "Ролик", "channel_name": "Канал", "level": "clean", "license": None,
                                                           "is_short": 1, "duration_s": 30, "analysis": {}, "frames": {}})
    monkeypatch.setattr(popular, "cutter_block", lambda c, d: {"source_url": "https://www.youtube.com/watch?v=geo1", "direct_file_url": None})
    with pytest.raises(ValueError, match="закрыт по странам"):
        pipeline.enqueue(conn, "video", "geo1", {})
    # у фильма появился другой исходник — ставим как обычно
    conn.execute("""INSERT INTO pipeline_items(kind, ref_id, label, channel, source_url, source_video_id, direct_url, license, is_short, duration_s, hint,
                    clip_min_s, clip_max_s, niche_id, channel_slug, status, note, metrics, created_at, updated_at)
                    VALUES ('film','F1','Фильм','Канал','https://www.youtube.com/watch?v=old','old',NULL,NULL,0,5000,NULL,30,59,'kino','ch','failed',
                    NULL, NULL, '2026-10-24T14:00:00Z', '2026-10-24T14:00:00Z')""")
    conn.execute("UPDATE pipeline_items SET last_error = 'файл не скачался: видео недоступно из страны хранилища (гео-блок автора)' WHERE ref_id = 'F1'")
    conn.commit()
    from kinotrends import recommend

    brief = {"title": {"label": "Фильм", "year": 2020, "kind": "film"}, "source": {"channel": "Канал", "video_id": "new", "duration_s": 5000},
             "cutter": {"source_url": "https://www.youtube.com/watch?v=new", "rights_ok": True, "direct_file_url": None, "hint": None,
                        "clip_min_s": 30, "clip_max_s": 59}}
    monkeypatch.setattr(recommend, "brief", lambda c, f, tid: brief)
    monkeypatch.setattr(files, "init", lambda c: None)
    monkeypatch.setattr(files, "request", lambda c, t, v, u: {"ok": True, "status": {"status": "running"}})
    item = pipeline.enqueue(conn, "film", "F1", {})
    assert item["status"] == "file_pending"
    pipeline.cancel(conn, item["id"])  # иначе enqueue вернёт активное задание, а не проверит исходник
    # а тот же закрытый исходник — нет
    brief["source"]["video_id"] = "old"
    with pytest.raises(ValueError, match="закрыт по странам"):
        pipeline.enqueue(conn, "film", "F1", {})


def test_job_options_short_video_vs_film():
    st = {"pipeline_banner_bottom": "1", "pipeline_banner_top": "2"}
    short = {"channel_slug": "kadr-dnya", "niche_id": "vpn", "is_short": 1,
             "metrics": {"free_zones": ["верх", "низ"], "text_overlay": True, "subtitles": False, "orientation": "vertical"}}
    o = pipeline._job_options(short, st)
    assert "niche_id" not in o and o["author_layer"]["captions"] == "none"
    assert o["author_layer"]["layout"] == {"mode": "manual", "title_top": pipeline.SHORT_TITLE_TOP, "onscreen_top": pipeline.SHORT_ONSCREEN_TOP}
    assert o["banner_variant_id"] == 1  # низ свободен → нижний вариант
    top_only = dict(short, metrics={**short["metrics"], "free_zones": ["верх"]})
    assert pipeline._job_options(top_only, st)["banner_variant_id"] == 2
    film = {"channel_slug": "kadr-dnya", "niche_id": "kino", "is_short": 0, "metrics": {"year": 2010}}
    f = pipeline._job_options(film, st)
    assert "niche_id" not in f and f["author_layer"]["captions"] == "both" and "layout" not in f["author_layer"]
    assert f["banner_variant_id"] == 1


# Поля, которые принимает auto-shorts (api/schemas/clipping.py: ClipBatchItem, AuthorLayerInput, LayoutOverride;
# SourceSelectRequest). Там extra="forbid": любое лишнее поле — 422 на весь заказ, конвейер встаёт.
CLIP_ITEM_FIELDS = {"channel_id", "campaign_id", "banner_variant_id", "author_layer", "reframe", "priority", "start",
                    "start_sec", "end_sec", "candidate_id"}
AUTHOR_LAYER_FIELDS = {"mode", "hook_text", "voice", "captions", "title_overlay", "script", "layout"}
LAYOUT_FIELDS = {"mode", "banner", "captions_bottom", "title_top", "onscreen_top"}
SELECT_FIELDS = {"selector", "target_clip_sec", "min_clip_sec", "max_clip_sec", "max_candidates", "target_count",
                 "detail_pass", "tighten_pauses", "niche_id", "save_as_default"}


def test_job_options_fit_autoshorts_schema():
    for item in ({"channel_slug": "kadr-dnya", "niche_id": "vpn", "is_short": 1, "metrics": {"orientation": "vertical"}},
                 {"channel_slug": "kadr-dnya", "niche_id": "kino", "is_short": 0, "metrics": {"free_zones": ["низ"]}}):
        o = pipeline._job_options(item, {"pipeline_banner_bottom": "1", "pipeline_banner_top": "2"})
        clip = {"start_sec": 0.0, "end_sec": 30.0, **o}
        assert set(clip) <= CLIP_ITEM_FIELDS, set(clip) - CLIP_ITEM_FIELDS
        assert set(o["author_layer"]) <= AUTHOR_LAYER_FIELDS
        assert set(o["author_layer"].get("layout") or {}) <= LAYOUT_FIELDS


def test_select_options_fit_autoshorts_schema():
    body = dict(pipeline._options({"clip_min_s": 20, "clip_max_s": 50}, {"pipeline_clips_per_source": "3"}), selector="heuristic", niche_id="kino")
    assert set(body) <= SELECT_FIELDS, set(body) - SELECT_FIELDS


def _put_item(conn, ref, status, **cols):
    ts = db.now_iso()
    conn.execute("INSERT INTO pipeline_items(kind, ref_id, label, channel_slug, status, created_at, updated_at) VALUES ('video', ?, ?, 'kadr-dnya', ?, ?, ?)",
                  (ref, ref, status, ts, ts))
    iid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    if cols:
        pipeline._set(conn, iid, **cols)
    conn.commit()
    return iid


def test_package_built_after_approve_is_picked_up_and_autopublished(conn):
    """Клип одобрен раньше, чем собран пакет: задание не должно навсегда остаться в «одобрено»."""
    s = dict(db.all_settings(conn), pipeline_autopublish="1", pipeline_rights_default="licensed_platform")
    iid = _put_item(conn, "late-pkg", "cutting", as_jobs=pipeline.dumps([31]), rights_type="licensed_platform")
    fake = FakeAS()
    fake.job_status, fake.no_package = "approved", True
    pipeline.step(conn, fake, pipeline._row(conn, iid), s, log=lambda *a, **k: None)
    assert pipeline._row(conn, iid)["status"] == "approved"            # пакета нет — ждём
    fake.no_package = False
    pipeline.step(conn, fake, pipeline._row(conn, iid), s, log=lambda *a, **k: None)
    cur = pipeline._row(conn, iid)
    assert cur["status"] == "publishing" and cur["as_packages"] == [5]
    assert ("autopublish", 5, "public") in fake.calls and (5, "content_id", True) in fake.checked
    fake.ap = {"state": "scheduled"}
    pipeline.step(conn, fake, pipeline._row(conn, iid), s, log=lambda *a, **k: None)
    assert pipeline._row(conn, iid)["status"] == "publishing"
    fake.ap, fake.pub_ref = {"state": "done"}, {"id": 44}
    pipeline.step(conn, fake, pipeline._row(conn, iid), s, log=lambda *a, **k: None)
    cur = pipeline._row(conn, iid)
    assert cur["status"] == "published" and cur["as_publications"] == [44]


def test_failed_autopublish_is_shown_not_retried(conn):
    s = dict(db.all_settings(conn), pipeline_autopublish="1")
    iid = _put_item(conn, "ap-failed", "publishing", as_jobs=pipeline.dumps([32]), as_packages=pipeline.dumps([6]))
    fake = FakeAS()
    fake.ap = {"state": "failed", "error": "квота YouTube на сутки исчерпана"}
    pipeline.step(conn, fake, pipeline._row(conn, iid), s, log=lambda *a, **k: None)
    cur = pipeline._row(conn, iid)
    assert cur["status"] == "publishing" and "квота" in cur["last_error"]
    assert not any(c[0] == "autopublish" for c in fake.calls)


def test_tick_limit_does_not_starve_items_in_work(conn):
    """Лимит тика — только на новые исходники: зависшие старые задания не должны держать очередь."""
    for st in ("approved", "approved", "approved"):
        _put_item(conn, f"old-{st}", st, as_jobs=pipeline.dumps([1]), as_packages=pipeline.dumps([7]))
    fresh = _put_item(conn, "fresh-sent", "sent", as_source_id="99")
    fake = FakeAS()
    fake.source_polls = 5  # источник готов
    fake.ap = {"state": "scheduled"}
    pipeline.tick(conn, dict(db.all_settings(conn), pipeline_max_per_tick="1"), log=lambda *a, **k: None, asc=fake)
    assert pipeline._row(conn, fresh)["status"] == "cutting"


def test_concurrent_tick_is_skipped(conn):
    pipeline._TICK_LOCK.acquire()
    try:
        out = pipeline.tick(conn, db.all_settings(conn), log=lambda *a, **k: None, asc=FakeAS())
    finally:
        pipeline._TICK_LOCK.release()
    assert out["skipped"] == "предыдущий тик ещё идёт" and out["processed"] == 0


def test_new_items_start_little_by_little(conn):
    """По чуть-чуть: один запуск за тик и не больше pipeline_max_in_work заданий в работе."""
    conn.execute("UPDATE pipeline_items SET status = 'cancelled' WHERE status IN ('new','sent','cutting','review','file_pending','approved','publishing')")
    conn.commit()
    ids = [_put_item(conn, f"slow-{i}", "new", direct_url=f"http://1.2.3.4/files/slow{i}.mp4", license="creativeCommon",
                     channel="Автор", source_url="u") for i in range(4)]
    s = dict(db.all_settings(conn), pipeline_max_per_tick="1", pipeline_max_in_work="2")
    fake = FakeAS()

    def sent():
        return sum(1 for i in ids if pipeline._row(conn, i)["status"] != "new")

    pipeline.tick(conn, s, log=lambda *a, **k: None, asc=fake)
    assert sent() == 1
    pipeline.tick(conn, s, log=lambda *a, **k: None, asc=fake)
    assert sent() == 2, [pipeline._row(conn, i)["status"] for i in ids]
    fake.job_status = "processing"
    pipeline.tick(conn, s, log=lambda *a, **k: None, asc=fake)
    assert sent() == 2  # в работе уже два — новые ждут


def test_enqueue_does_not_order_the_file(conn, monkeypatch):
    called = []
    monkeypatch.setattr(pipeline.files, "request", lambda *a, **k: called.append(a) or {"ok": True})
    popular.upsert(conn, [dataapi.parse_item(api_item("lazyfile0003"))], "cc", db.now_iso())
    conn.execute("UPDATE pop_videos SET license = 'youtube', is_short = 1, level = 'clean', score = 80, pick = 1 WHERE video_id = 'lazyfile0003'")
    conn.commit()
    it = pipeline.enqueue(conn, "video", "lazyfile0003", db.all_settings(conn))
    assert it["status"] == "file_pending" and called == []


def test_retry_wait_restarts_failed_clips_in_turn(conn):
    conn.execute("UPDATE pipeline_items SET status = 'cancelled' WHERE status IN ('new','sent','cutting','review','file_pending','approved','publishing','retry_wait')")
    conn.commit()
    a = _put_item(conn, "rw-a", "retry_wait", as_jobs=pipeline.dumps([61, 62]))
    b = _put_item(conn, "rw-b", "retry_wait", as_jobs=pipeline.dumps([63]))
    fake = FakeAS()
    fake.job_status = "failed"
    s = dict(db.all_settings(conn), pipeline_max_per_tick="1", pipeline_max_in_work="3")
    pipeline.tick(conn, s, log=lambda *a, **k: None, asc=fake)
    assert pipeline._row(conn, a)["status"] == "cutting" and pipeline._row(conn, b)["status"] == "retry_wait"
    assert ("retry", 61) in fake.calls


def test_expired_file_is_ordered_again(conn, monkeypatch):
    """Файл хранилища убрала очистка, а задание ещё ждёт его: заказываем заново, а не висим вечно."""
    asked = []
    monkeypatch.setattr(pipeline.files, "request", lambda *a, **k: asked.append(a) or {"ok": True})
    conn.execute("UPDATE pipeline_items SET status = 'cancelled' WHERE status IN ('new','sent','cutting','review','file_pending','approved','publishing','retry_wait')")
    ts = db.now_iso()
    conn.execute("INSERT INTO downloads(title_id, video_id, url, status, created_at) VALUES (NULL, 'gonefile001', 'u', 'expired', ?)", (ts,))
    conn.commit()
    iid = _put_item(conn, "gone", "file_pending", source_video_id="gonefile001", source_url="https://rutube.ru/video/x/")
    pipeline.tick(conn, dict(db.all_settings(conn), pipeline_max_per_tick="1"), log=lambda *a, **k: None, asc=FakeAS())
    assert asked and pipeline._row(conn, iid)["status"] == "file_pending"
