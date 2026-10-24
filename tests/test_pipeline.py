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
        self.calls = []
        self.source_polls = 0
        self.job_status = "processing"
        self.publications: list = []

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

    def job(self, jid):
        return {"id": jid, "status": self.job_status}

    def review(self, jid, decision, expected, comment=None):
        self.calls.append(("review", decision))
        self.job_status = "approved"
        return {}

    def package_of_job(self, jid):
        return {"id": 5}

    def package(self, pid):
        return {"id": pid, "publications": self.publications}

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
    fake.publications = [{"id": 9}]
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
