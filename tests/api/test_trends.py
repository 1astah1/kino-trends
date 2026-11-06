"""Трендоскаут — мост к Kino-trends (`api/routers/trends.py`): настройки моста, прокси чтения и действий
через `httpx.MockTransport`, коды ошибок 409/502/400/404, роли viewer/operator и журнал аудита."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from autoshorts.api.routers import trends as trends_router

KINO_URL = "http://kino.test"

SLOT = {
    "id": 33,
    "day": "2026-10-24",
    "slot": 0,
    "title_id": "Q23871",
    "label": "Брестская крепость",
    "year": 2010,
    "kind_ru": "фильм",
    "reason": "1,1 млн просмотров в день по 3 роликам",
    "plan": {
        "duration": "61–90 с",
        "hook": "#shorts",
        "best_time": "сб, 17:00–19:00 МСК",
        "hashtags": [],
        "refs": [],
    },
    "status": "planned",
    "locked": False,
    "source": {
        "video_id": "rt_1",
        "url": "https://rutube.ru/video/1/",
        "channel": "Star Media",
        "duration_s": 8305,
    },
    "source_alt": None,
    "explain": "Резать сцены боёв",
    "rights": {
        "level": "ok",
        "level_ru": "проходит",
        "risk": 12,
        "reasons": ["лицензия платформы"],
        "clip_max_s": 59,
    },
    "kinopoisk_url": "https://www.kinopoisk.ru/film/436263/",
    "new_field_from_kino": "проходит насквозь",
}
PICK = {
    "video_id": "hGnLlWpueSA",
    "title": "Ачивка в яблочко",
    "channel": "DubDuk",
    "source_url": "https://www.youtube.com/watch?v=hGnLlWpueSA",
    "duration_s": 7,
    "lang": "ru",
    "license": "creativeCommon",
    "level": "clean",
    "score": 100,
    "reasons": ["CC"],
    "direct_file_url": None,
    "file_status": "error",
    "frames_url": "api/videos/hGnLlWpueSA/frames.jpg",
    "topic": "Майнкрафт",
    "audience_fit": 0.7,
    "free_zones": ["верх"],
    "is_short": 1,
    "views": 369197,
    "growth_per_day": 41.0,
    "regions": {},
    "pick": 1,
    "thumb": "https://i.ytimg.com/vi/hGnLlWpueSA/maxresdefault.jpg",
    "channel_subs": 871,
    "published_at": "2026-10-07T09:00:38Z",
}
TODAY = {
    "today": "2026-10-24",
    "schedule": {"today": [SLOT], "tomorrow": []},
    "picks": [PICK],
    "candidates": [],
    "counts": {
        "film_total": 1,
        "film_done": 0,
        "picks": 1,
        "used": 0,
        "clean": 5,
        "check": 69,
        "videos": 2080,
    },
    "run": {"running": False, "phase": ""},
    "last_run": {"started_at": "2026-10-24T11:41:03Z", "finished_at": "2026-10-24T13:14:18Z", "status": "ok"},
    "videos_job": {"running": False, "last": None},
}
STATES_RU = {"sent": "источник в auto-shorts", "review": "ждёт ревью", "failed": "ошибка"}
PIPELINE_STATUS = {
    "configured": True,
    "health": {"status": "ok", "checks": {}},
    "channels": [{"id": "kadr-dnya", "title": "Кадр дня"}],
    "counts": {
        "sent": 2,
        "review": 1,
        "needs_rights": 1,
        "failed": 1,
        "qc_failed": 1,
        "published": 3,
        "cancelled": 4,
    },
    "enabled": True,
    "auto_review": True,
    "autopublish": False,
    "rights_default": "licensed_platform",
    "states_ru": STATES_RU,
}
ITEM = {
    "id": 6,
    "kind": "film",
    "ref_id": "Q213326",
    "label": "Суперсемейка",
    "channel": "Junoon Tv",
    "source_url": "https://www.youtube.com/watch?v=VhPOhebtdjI",
    "source_video_id": "VhPOhebtdjI",
    "direct_url": None,
    "license": None,
    "is_short": 0,
    "duration_s": 7361,
    "hint": "сцены",
    "clip_min_s": 31,
    "clip_max_s": 45,
    "niche_id": "kino",
    "channel_slug": "kadr-dnya",
    "status": "sent",
    "rights_type": "licensed_platform",
    "as_source_id": "6",
    "as_jobs": [],
    "as_packages": [],
    "as_publications": [],
    "metrics": {"file_retry": "2026-10-24T14:50:22Z"},
    "last_error": None,
    "note": "календарь 2026-10-24",
    "created_at": "2026-10-24T14:45:23Z",
    "updated_at": "2026-10-24T15:03:27Z",
    "sent_at": "2026-10-24T15:03:27Z",
    "published_at": None,
    "status_ru": "источник в auto-shorts",
}
PIPELINE = {
    "items": [ITEM],
    "counts": PIPELINE_STATUS["counts"],
    "states_ru": STATES_RU,
    "status": PIPELINE_STATUS,
}
VIDEO = {
    "video_id": "hGnLlWpueSA",
    "title": "Ачивка в яблочко",
    "channel_id": "UCV7",
    "channel_name": "DubDuk",
    "channel_subs": 871,
    "duration_s": 7,
    "published_at": "2026-10-07T09:00:38Z",
    "category": "игры",
    "lang": "ru",
    "views": 369197,
    "likes": 4281,
    "comments": 69,
    "license": "creativeCommon",
    "licensed_content": 0,
    "made_for_kids": 0,
    "thumb": "https://i.ytimg.com/vi/hGnLlWpueSA/maxresdefault.jpg",
    "blocked_ru": 0,
    "is_short": 1,
    "vpd": 21497.0,
    "growth": 41.0,
    "analysis": {"topic": "Майнкрафт", "audience_fit": 0.7, "reuse_fit": 0.8},
    "frames": {"free_zones": ["верх", "низ"], "clean": False},
    "level": "clean",
    "level_ru": "чистое",
    "score": 100,
    "reasons": ["CC"],
    "pick": 1,
    "pick_auto": 1,
    "used_at": None,
    "url": "https://www.youtube.com/watch?v=hGnLlWpueSA",
    "frames_available": True,
    "file": {"status": "error", "size": None, "error": "загрузка оборвалась", "file_url": None},
}
VIDEOS = {
    "items": [VIDEO],
    "total": 1,
    "levels": {"check": 69, "clean": 5, "no": 1784, "unknown": 222},
    "picks": 5,
    "shorts": 469,
    "regions_ru": {"RU": "Россия"},
    "categories_ru": {"20": "игры"},
}
VIDEO_DETAIL = {
    **VIDEO,
    "description": "Самый простой способ получить достижение",
    "category_id": "20",
    "tags": ["майнкрафт", "minecraft"],
    "topics": ["Action-adventure game"],
    "embeddable": 1,
    "caption": 0,
    "definition": "hd",
    "origin": "cc",
    "regions": {"RU": 7, "KZ": 12},
    "cats": {"20": 3},
    "analysis": {
        **VIDEO["analysis"],
        "format": "игра",
        "hook": "Демонстрация получения достижения",
        "banner_advice": "Баннер в начале или конце",
        "is_reupload": False,
        "model": "google/gemini-2.5-flash-lite",
        "unknown_analysis_field": "насквозь",
    },
    "analysis_at": "2026-10-23T14:50:27Z",
    "frames": {
        "watermark": False,
        "text_overlay": True,
        "clean": False,
        "orientation": "vertical",
        "quality": "ок",
        "free_zones": ["верх", "низ"],
        "note": "Надписи в кадре",
    },
    "frames_at": "2026-10-23T14:52:45Z",
    "assessed_at": "2026-10-24T13:13:05Z",
    "manual_level": None,
    "pick_at": "2026-10-23T14:55:33Z",
    "pick_note": None,
    "series": [
        {"ts": "2026-10-23T14:31:12Z", "views": 369158, "likes": 4280, "comments": 69},
        {"ts": "2026-10-24T13:12:02Z", "views": 369197, "likes": 4281, "comments": 69},
    ],
    "holder": None,
    "cutter": {**PICK, "frames_url": "api/videos/hGnLlWpueSA/frames.jpg"},
}
FILM_RIGHTS = {
    "title_id": "Q23871",
    "label": "Брестская крепость",
    "level": "ok",
    "level_ru": "проходит",
    "auto_level": "ok",
    "risk": 12,
    "reasons": ["Мосфильм: заявляет права через Content ID", "22 чужих нарезок живут больше месяца"],
    "rule": "клип не длиннее 59 с",
    "clip_max_s": 59,
    "holder": "Мосфильм (дистрибьютор)",
    "holder_policy": "claim",
    "holder_policy_ru": "заявляет права через Content ID",
    "public_domain": False,
    "signals": {"rated": 22, "removed_strict": 0, "claimants": [], "manual_level": None, "manual_note": None},
    "checked_at": "2026-10-24T16:06:59Z",
    "passes": True,
}
FILM_SOURCE = {
    "id": 542,
    "title_id": "Q23871",
    "video_id": "jUzGyns9TCE",
    "url": "https://www.youtube.com/watch?v=jUzGyns9TCE",
    "label": "БРЕСТСКАЯ КРЕПОСТЬ | Военная историческая драма",
    "channel": "Киностудия Беларусьфильм",
    "duration_s": 7983,
    "kind": "film",
    "season": None,
    "episode": None,
    "language": "оригинал",
    "trust": 0.8,
    "verified": 1,
    "note": "С субтитрами, но полный фильм.",
    "alive": None,
}
FILM_ROW = {
    "title_id": "Q23871",
    "label": "Брестская крепость",
    "label_en": "The Brest Fortress",
    "year": 2010,
    "kind": "film",
    "kind_ru": "фильм",
    "genres": ["военный фильм", "драматический фильм"],
    "countries": ["Беларусь", "Россия"],
    "franchise": None,
    "kp_id": "436263",
    "n_shorts": 2,
    "n_fresh7": 1,
    "vpd_sum": 15,
    "views_max": 86,
    "top_video": {
        "video_id": "by5ssNKJCJU",
        "title": "Спрятал гранату",
        "views": 86,
        "thumb": "https://i.ytimg.com/vi/by5ssNKJCJU/oar.jpg",
    },
    "last_pub": "2026-10-16T06:07:14Z",
    "outliers": 0,
    "views_sum": 139,
    "views_median": 70,
    "views_p90": 83,
    "n_channels": 2,
    "dur_avg": 60,
    "growth7": 4039752,
    "score": 61,
    "opportunity": False,
    "wiki_7d": 91.3,
    "rights": {
        k: FILM_RIGHTS[k]
        for k in ("level", "level_ru", "risk", "reasons", "rule", "clip_max_s", "holder", "holder_policy")
    },
    "source": {
        "video_id": "jUzGyns9TCE",
        "url": FILM_SOURCE["url"],
        "channel": "Киностудия Беларусьфильм",
        "duration_s": 7983,
        "language": "оригинал",
        "verified": True,
        "file_status": None,
        "file_error": None,
    },
    "in_pipeline": None,
    "ready": True,
    "ready_reasons": [],
}
FILM_ROW_SERIES = {
    **FILM_ROW,
    "title_id": "Q915313",
    "label": "Бригада",
    "kind": "series",
    "kind_ru": "сериал",
    "kp_id": "77039",
    "source": None,
    "ready": False,
    "ready_reasons": ["нет исходника"],
    "top_video": None,
}
FILMS = {"total": 2, "items": [FILM_ROW, FILM_ROW_SERIES]}
FILM_TITLE = {
    "title_id": "Q23871",
    "label": "Брестская крепость",
    "label_en": "The Brest Fortress",
    "original_title": "Брестская крепость",
    "year": 2010,
    "kind": "film",
    "kind_ru": "фильм",
    "genres": ["военный фильм"],
    "countries": ["Беларусь", "Россия"],
    "kp_id": "436263",
    "imdb_id": "tt1343703",
    "franchise": None,
    "sitelinks": 29,
    "is_manual": False,
    "aliases": ["Brest Fortress"],
}
FILM_STATS_BLOCK = {
    "n_shorts": 2,
    "n_channels": 2,
    "views_sum": 139,
    "views_median": 70,
    "views_p90": 83,
    "views_max": 86,
    "vpd_sum": 15,
    "growth7": 4039752,
    "dur_avg": 60,
}
FILM_BRIEF = {
    "rights": {**FILM_RIGHTS, "passes": True},
    "title": FILM_TITLE,
    "stats": FILM_STATS_BLOCK,
    "why": ["+4 млн просмотров за неделю"],
    "opportunity": None,
    "plan": {
        "duration": "46–60 с",
        "hook": "#shorts",
        "best_time": "вт, 17:00–19:00 МСК",
        "hashtags": ["кино"],
        "refs": [],
    },
    "scenes": [
        {
            "name": "Начало обороны",
            "what_to_cut": "Первые минуты нападения",
            "quotes": ["— Немцы!"],
            "where": "начало",
            "n": 18,
            "views": 3227093,
            "top": [],
        }
    ],
    "hooks": ["Генерал наказал рядового!"],
    "advice": ["Длительность: 30-60 секунд"],
    "insights_at": "2026-10-18T00:05:56Z",
    "top_channels": [],
    "hashtags_all": [{"tag": "кино", "n": 2}],
    "seasons": [],
    "schedule": [{"id": 33, "day": "2026-10-24", "slot": 0, "status": "planned"}],
    "interest": {
        "summary": {},
        "lines": ["Википедия: 91 просмотров статьи в день"],
        "series": {"wiki": [], "trends": []},
    },
    "explain": "Мощная военная драма",
    "explain_at": "2026-10-18T00:05:52Z",
    "kinopoisk_url": "https://www.kinopoisk.ru/film/436263/",
    "sources": [FILM_SOURCE],
    "source": FILM_SOURCE,
    "source_alt": None,
    "references": [
        {
            "url": "https://www.youtube.com/shorts/by5ssNKJCJU",
            "title": "Спрятал гранату",
            "views": 86,
            "duration_s": 55,
            "channel": "KinoComrad",
        }
    ],
    "deliverable": None,
    "cutter": {
        "source_url": FILM_SOURCE["url"],
        "source_kind": "youtube",
        "direct_file_url": "http://192.0.2.10/files/jUzGyns9TCE.mp4",
        "alt_url": None,
        "hint": "Начало обороны",
        "clip_min_s": 46,
        "clip_max_s": 59,
        "rights_ok": True,
        "rights_level": "ok",
        "rights_note": "Мосфильм: заявляет права",
    },
    "brief_extra_field": "насквозь",
}
FILM_STATS = {
    "title": FILM_TITLE,
    "stats": FILM_STATS_BLOCK,
    "channels": [{"channel_id": "UC_B4", "name": "KinoComrad", "n": 1, "views": 86}],
    "hashtags": [{"tag": "кино", "n": 2}],
    "durations": [{"bucket": "46–60 с", "n": 1}],
    "daily": [{"day": "2026-10-13", "n": 1, "views": 86}, {"day": "2026-10-16", "n": 1, "views": 53}],
    "seasons": [{"season": 1, "n": 2, "views": 139}],
    "videos": [
        {
            "video_id": "by5ssNKJCJU",
            "title": "Спрятал гранату",
            "channel_id": "UC_B4",
            "channel_name": "KinoComrad",
            "duration_s": 55,
            "published_at": "2026-10-13T11:04:39Z",
            "views": 86,
            "likes": 1047,
            "thumb": "https://i.ytimg.com/vi/by5ssNKJCJU/oar.jpg",
            "vpd": 8,
            "url": "https://www.youtube.com/shorts/by5ssNKJCJU",
            "outlier_x": 0.0,
            "growth7": 17800,
            "season": 1,
            "episode": None,
            "recog_conf": 0.9,
            "recog_method": "neuro",
        },
    ],
}


class FakeKino:
    """Kino-trends на `httpx.MockTransport`: отдаёт фикстуры, запоминает запросы, умеет ломаться."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.fail: Exception | None = None
        self.responses: dict[tuple[str, str], tuple[int, Any]] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail is not None:
            raise self.fail
        key = (request.method, request.url.path)
        if key in self.responses:
            status, body = self.responses[key]
            return httpx.Response(status, json=body)
        if key == ("GET", "/api/today"):
            return httpx.Response(200, json=TODAY)
        if key == ("GET", "/api/pipeline/status"):
            return httpx.Response(200, json=PIPELINE_STATUS)
        if key == ("GET", "/api/pipeline"):
            return httpx.Response(200, json=PIPELINE)
        if key == ("GET", "/api/videos"):
            return httpx.Response(200, json=VIDEOS)
        if key == ("POST", "/api/pipeline/enqueue"):
            payload = json.loads(request.content)
            return httpx.Response(
                200, json={**ITEM, "id": 42, "kind": payload["kind"], "ref_id": payload["ref_id"]}
            )
        if key in {("POST", "/api/pipeline/6/retry"), ("POST", "/api/pipeline/6/cancel")}:
            return httpx.Response(
                200, json={**ITEM, "status": "new" if key[1].endswith("retry") else "cancelled"}
            )
        if request.url.path.startswith("/api/pipeline/"):
            return httpx.Response(404, json={"detail": "нет такого задания"})
        if request.url.path.startswith("/api/videos/"):
            return self._video(request)
        if request.url.path == "/api/films":
            ready = request.url.params.get("ready") == "1"
            items = [it for it in FILMS["items"] if it["ready"]] if ready else FILMS["items"]
            return httpx.Response(200, json={"total": len(items), "items": items})
        if key == ("POST", "/api/films/sources/find-top"):
            return httpx.Response(
                200, json={"started": True, "limit": int(request.url.params.get("limit", 30))}
            )
        if request.url.path.startswith("/api/films/") or request.url.path.startswith("/api/brief/"):
            return self._film(request)
        if key == ("GET", "/thumb/hGnLlWpueSA.jpg"):
            return httpx.Response(200, content=b"\xff\xd8JPEG", headers={"content-type": "image/jpeg"})
        if request.url.path.startswith("/thumb/"):
            return httpx.Response(404, text="no thumb")
        return httpx.Response(500, text="boom")

    def _video(self, request: httpx.Request) -> httpx.Response:
        """`/api/videos/{id}[/frames.jpg|/pick|/level|/analyze|/file]` для одного известного ролика."""
        parts = request.url.path.split("/")[3:]  # ["", "api", "videos", id, action?]
        video_id, action = parts[0], parts[1] if len(parts) > 1 else None
        if video_id != VIDEO_DETAIL["video_id"]:
            if action == "frames.jpg":
                return httpx.Response(404, json={"detail": "кадры ещё не вынимались"})
            return httpx.Response(404, json={"detail": "нет такого ролика в популярных"})
        payload = json.loads(request.content) if request.content else {}
        if request.method == "GET" and action is None:
            return httpx.Response(200, json=VIDEO_DETAIL)
        if request.method == "GET" and action == "frames.jpg":
            return httpx.Response(200, content=b"\xff\xd8FRAMES", headers={"content-type": "image/jpeg"})
        if request.method == "POST" and action == "pick":
            pick = 1 if payload.get("pick", True) else 0
            return httpx.Response(200, json={**VIDEO_DETAIL, "pick": pick, "pick_auto": 0, "cutter": None})
        if request.method == "POST" and action == "level":
            level = payload.get("level") or None
            if level not in (None, "clean", "check", "no"):
                return httpx.Response(400, json={"detail": "level: clean|check|no или пусто"})
            return httpx.Response(
                200, json={**VIDEO_DETAIL, "manual_level": level, "level": level or "clean", "cutter": None}
            )
        if request.method == "POST" and action == "analyze":
            return httpx.Response(200, json={**VIDEO_DETAIL, "notes": ["кадры: нужен сервер-хранилище"]})
        if request.method == "POST" and action == "file":
            return httpx.Response(200, json={"ok": True, "status": {"status": "running"}})
        return httpx.Response(500, text="boom")

    def _film(self, request: httpx.Request) -> httpx.Response:
        """Бриф, статистика, постер, права (чтение/перепроверка/решение) и исходники одного произведения."""
        parts = request.url.path.split("/")[2:]  # ["", "api", "films"|"brief", id, *action]
        title_id, action = parts[1], "/".join(parts[2:]) or None
        if title_id != FILM_TITLE["title_id"]:
            return httpx.Response(404, json={"detail": "нет такого произведения"})
        payload = json.loads(request.content) if request.content else {}
        if parts[0] == "brief":
            return httpx.Response(200, json=FILM_BRIEF)
        if request.method == "GET" and action is None:
            return httpx.Response(200, json=FILM_STATS)
        if request.method == "GET" and action == "poster.jpg":
            return httpx.Response(200, content=b"\xff\xd8POSTER", headers={"content-type": "image/jpeg"})
        if request.method == "GET" and action == "rights":
            return httpx.Response(200, json=FILM_RIGHTS)
        if request.method == "POST" and action == "rights/refresh":
            notes = ["перепроверено чужих нарезок: 22, снесено: 0"]
            return httpx.Response(200, json={**FILM_RIGHTS, "notes": notes})
        if request.method == "POST" and action == "rights/manual":
            level = payload.get("level") or None
            if level not in (None, "ok", "caution", "block"):
                return httpx.Response(400, json={"detail": "level: ok|caution|block или пусто"})
            signals = {**FILM_RIGHTS["signals"], "manual_level": level, "manual_note": payload.get("note")}
            return httpx.Response(200, json={**FILM_RIGHTS, "level": level or "ok", "signals": signals})
        if action == "sources":
            return httpx.Response(200, json={"items": [FILM_SOURCE], "checked_at": "2026-10-24T13:13:40Z"})
        return httpx.Response(500, text="boom")

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture
def kino(monkeypatch: pytest.MonkeyPatch) -> FakeKino:
    fake = FakeKino()
    monkeypatch.setattr(trends_router, "transport_override", fake.transport())
    return fake


def _configure(login_as: Any, **extra: Any) -> None:
    admin = login_as("admin")
    body = {"kino_trends_url": KINO_URL + "/", "kino_trends_public_url": "https://kino.example/", **extra}
    resp = admin.patch("/settings", json=body)
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------- мост не настроен


def test_requires_login(client: Any) -> None:
    assert client.get("/api/v1/trends/status").status_code == 401


def test_not_configured(login_as: Any, kino: FakeKino) -> None:
    viewer = login_as("viewer")
    status = viewer.get("/trends/status")
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["configured"] is False and body["url_set"] is False and body["reachable"] is False
    assert body["today"] is None and body["pipeline"] is None and body["public_url"] is None
    assert "не задан" in body["error"]

    for path in ("/trends/today", "/trends/videos", "/trends/pipeline"):
        resp = viewer.get(path)
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "trends_not_configured"
    assert kino.requests == []  # без адреса в Kino-trends не ходим

    # адрес задан, но это не URL — настроено «неправильно», в сеть не ходим
    _configure(login_as, kino_trends_url="kino.test")
    body = viewer.get("/trends/status").json()
    assert body["url_set"] is True and body["configured"] is False
    assert "http" in body["error"]
    assert kino.requests == []


# ---------------------------------------------------------------- чтение через прокси


def test_status_and_reads_proxy(login_as: Any, kino: FakeKino) -> None:
    _configure(login_as)
    viewer = login_as("viewer")

    status = viewer.get("/trends/status")
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["configured"] and body["url_set"] and body["reachable"] and body["error"] is None
    assert body["public_url"] == "https://kino.example/"
    assert body["today"] == {"films": 1, "films_done": 0, "videos": 2080, "clean": 5, "check": 69, "picks": 1}
    assert body["pipeline"] == {
        "active": 2,  # sent
        "review": 2,  # review + needs_rights
        "failed": 2,  # failed + qc_failed
        "published": 3,
        "linked": True,
        "enabled": True,
    }
    assert [r.url.path for r in kino.requests] == ["/api/today", "/api/pipeline/status"]
    assert str(kino.requests[0].url).startswith(KINO_URL + "/api/today")  # хвостовой «/» настройки срезан

    today = viewer.get("/trends/today")
    assert today.status_code == 200, today.text
    data = today.json()
    assert data["today"] == "2026-10-24"
    assert data["schedule"]["today"][0]["label"] == "Брестская крепость"
    assert data["schedule"]["today"][0]["rights"]["level"] == "ok"
    assert data["schedule"]["today"][0]["new_field_from_kino"] == "проходит насквозь"  # extra=allow
    assert data["picks"][0]["thumb"] == "/api/v1/trends/videos/hGnLlWpueSA/thumb"  # обложка через наш прокси
    assert data["counts"]["clean"] == 5

    kino.requests.clear()
    videos = viewer.get(
        "/trends/videos",
        params={"level": "clean", "short": 1, "q": "майн", "sort": "views", "limit": 10, "offset": 20},
    )
    assert videos.status_code == 200, videos.text
    assert videos.json()["total"] == 1 and videos.json()["items"][0]["level"] == "clean"
    assert videos.json()["items"][0]["analysis"]["audience_fit"] == 0.7
    forwarded = dict(kino.requests[-1].url.params)
    assert forwarded == {
        "level": "clean",
        "short": "1",
        "sort": "views",
        "q": "майн",
        "limit": "10",
        "offset": "20",
    }

    kino.requests.clear()
    assert viewer.get("/trends/videos", params={"pick": "true", "fit": 0.6}).status_code == 200
    forwarded = dict(kino.requests[-1].url.params)
    assert forwarded["pick"] == "1" and forwarded["fit"] == "0.6" and "level" not in forwarded

    bad = viewer.get("/trends/videos", params={"level": "great", "limit": 999})
    assert bad.status_code == 422

    pipeline = viewer.get("/trends/pipeline", params={"status": "sent"})
    assert pipeline.status_code == 200, pipeline.text
    assert pipeline.json()["items"][0]["status"] == "sent"
    assert pipeline.json()["status"]["configured"] is True
    assert pipeline.json()["states_ru"]["review"] == "ждёт ревью"
    assert dict(kino.requests[-1].url.params) == {"status": "sent", "limit": "200"}


def test_unreachable(login_as: Any, kino: FakeKino) -> None:
    _configure(login_as)
    viewer = login_as("viewer")
    kino.fail = httpx.ConnectError("connection refused")

    body = viewer.get("/trends/status").json()
    assert body["configured"] is True and body["reachable"] is False
    assert "не отвечает" in body["error"] and body["today"] is None

    resp = viewer.get("/trends/today")
    assert resp.status_code == 502, resp.text
    assert resp.json()["error"]["code"] == "trends_unreachable"
    assert resp.json()["error"]["details"]["url"] == KINO_URL

    kino.fail = None
    kino.responses[("GET", "/api/today")] = (503, {"detail": "база занята"})
    resp = viewer.get("/trends/today")
    assert resp.status_code == 502
    assert resp.json()["error"]["details"]["reason"] == "база занята"
    status = viewer.get("/trends/status")  # статус моста не падает вместе с Kino-trends
    assert status.status_code == 200 and status.json()["reachable"] is False
    assert "503" in status.json()["error"]


# ---------------------------------------------------------------- действия оператора


def test_pipeline_actions_roles_errors_and_audit(login_as: Any, kino: FakeKino, db: Any) -> None:
    _configure(login_as)
    viewer = login_as("viewer")
    operator = login_as("operator")

    assert viewer.post("/trends/pipeline/enqueue", json={"kind": "video", "ref_id": "abc"}).status_code == 403
    assert viewer.post("/trends/pipeline/6/retry").status_code == 403

    bad = operator.post("/trends/pipeline/enqueue", json={"kind": "song", "ref_id": "abc"})
    assert bad.status_code == 422
    assert kino.requests == []  # невалидное тело до Kino-trends не доходит

    resp = operator.post("/trends/pipeline/enqueue", json={"kind": "video", "ref_id": "hGnLlWpueSA"})
    assert resp.status_code == 200, resp.text
    assert (
        resp.json()["id"] == 42 and resp.json()["kind"] == "video" and resp.json()["ref_id"] == "hGnLlWpueSA"
    )
    sent = json.loads(kino.requests[-1].content)
    assert sent == {"kind": "video", "ref_id": "hGnLlWpueSA"}

    retry = operator.post("/trends/pipeline/6/retry")
    assert retry.status_code == 200 and retry.json()["status"] == "new"
    cancel = operator.post("/trends/pipeline/6/cancel")
    assert cancel.status_code == 200 and cancel.json()["status"] == "cancelled"
    assert operator.post("/trends/pipeline/0/retry").status_code == 422  # item_id >= 1

    missing = operator.post("/trends/pipeline/777/retry")
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"] == {
        "code": "trends_item_not_found",
        "message": "нет такого задания",
        "details": {},
    }

    kino.responses[("POST", "/api/pipeline/enqueue")] = (400, {"detail": "ролик помечен «не брать»"})
    rejected = operator.post("/trends/pipeline/enqueue", json={"kind": "video", "ref_id": "bad"})
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["error"]["code"] == "bad_request"
    assert rejected.json()["error"]["message"] == "ролик помечен «не брать»"

    rows = db.execute(
        "SELECT action, entity_type, entity_id, summary, diff FROM audit_log "
        "WHERE action LIKE 'trends.%' ORDER BY id"
    ).fetchall()
    assert [(r["action"], r["entity_type"], r["entity_id"]) for r in rows] == [
        ("trends.enqueue", "trends_item", "42"),
        ("trends.retry", "trends_item", "6"),
        ("trends.cancel", "trends_item", "6"),
    ]
    assert rows[0]["diff"]["ref_id"] == "hGnLlWpueSA" and "hGnLlWpueSA" in rows[0]["summary"]


# ---------------------------------------------------------------- обложки через свой API


def test_thumbs_are_rewritten_and_proxied(login_as: Any, kino: FakeKino) -> None:
    """Консоль живёт под CSP `img-src 'self'`, а i.ytimg.com у части зрителей закрыт: в ответах `thumb`
    указывает на наш прокси, а прокси берёт картинку из кэша Kino-trends (`/thumb/<id>.jpg`)."""
    _configure(login_as)
    viewer = login_as("viewer")
    proxied = "/api/v1/trends/videos/hGnLlWpueSA/thumb"

    videos = viewer.get("/trends/videos").json()
    assert videos["items"][0]["thumb"] == proxied
    today = viewer.get("/trends/today").json()
    assert today["picks"][0]["thumb"] == proxied
    assert "i.ytimg.com" not in json.dumps(videos) + json.dumps(today)

    img = viewer.get("/trends/videos/hGnLlWpueSA/thumb")
    assert img.status_code == 200, img.text
    assert img.headers["content-type"].startswith("image/jpeg") and img.content.startswith(b"\xff\xd8")
    assert "max-age" in img.headers.get("cache-control", "")
    assert kino.requests[-1].url.path == "/thumb/hGnLlWpueSA.jpg"

    assert viewer.get("/trends/videos/zzzzzzzzzzz/thumb").status_code == 404
    assert viewer.get("/trends/videos/bad id/thumb").status_code in (404, 422)


# ---------------------------------------------------------------- разбор ролика


def test_video_detail_frames_and_not_found(login_as: Any, kino: FakeKino) -> None:
    """`GET /trends/videos/{id}`: деталь Kino-trends с картинками через наш API, вложенные блоки
    типизированы, но лишние поля проходят насквозь; кадры — прокси с коротким приватным кэшем."""
    _configure(login_as)
    viewer = login_as("viewer")

    resp = viewer.get("/trends/videos/hGnLlWpueSA")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "Ачивка в яблочко" and body["description"].startswith("Самый простой")
    assert body["thumb"] == "/api/v1/trends/videos/hGnLlWpueSA/thumb"
    assert body["frames_url"] == "/api/v1/trends/videos/hGnLlWpueSA/frames"
    assert body["cutter"]["thumb"] == "/api/v1/trends/videos/hGnLlWpueSA/thumb"
    assert body["cutter"]["frames_url"] == "/api/v1/trends/videos/hGnLlWpueSA/frames"
    assert body["analysis"]["audience_fit"] == 0.7 and body["analysis"]["is_reupload"] is False
    assert body["analysis"]["unknown_analysis_field"] == "насквозь"  # extra=allow во вложенных моделях
    assert body["frames"]["free_zones"] == ["верх", "низ"] and body["frames"]["text_overlay"] is True
    assert [p["views"] for p in body["series"]] == [369158, 369197]
    assert body["file"] == {"status": "error", "size": None, "error": "загрузка оборвалась", "file_url": None}
    assert body["regions"] == {"RU": 7, "KZ": 12} and body["manual_level"] is None
    assert "i.ytimg.com" not in resp.text and "api/videos/hGnLlWpueSA/frames.jpg" not in resp.text
    assert kino.requests[-1].url.path == "/api/videos/hGnLlWpueSA"

    # без кадров — frames_url пустой, даже если Kino-trends что-то прислал
    kino.responses[("GET", "/api/videos/hGnLlWpueSA")] = (200, {**VIDEO_DETAIL, "frames_available": False})
    assert viewer.get("/trends/videos/hGnLlWpueSA").json()["frames_url"] is None
    del kino.responses[("GET", "/api/videos/hGnLlWpueSA")]

    img = viewer.get("/trends/videos/hGnLlWpueSA/frames")
    assert img.status_code == 200, img.text
    assert img.headers["content-type"].startswith("image/jpeg") and img.content.startswith(b"\xff\xd8")
    assert img.headers["cache-control"] == "private, max-age=300"
    assert kino.requests[-1].url.path == "/api/videos/hGnLlWpueSA/frames.jpg"

    missing = viewer.get("/trends/videos/zzzzzzzzzzz")
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"]["code"] == "trends_video_not_found"
    assert missing.json()["error"]["message"] == "нет такого ролика в популярных"
    no_frames = viewer.get("/trends/videos/zzzzzzzzzzz/frames")
    assert no_frames.status_code == 404 and no_frames.json()["error"]["code"] == "trends_frames_not_found"
    assert viewer.get("/trends/videos/bad id").status_code in (404, 422)


def test_video_actions_roles_and_audit(login_as: Any, kino: FakeKino, db: Any) -> None:
    """Решения по ролику — только оператор; тела уходят в Kino-trends по его контракту, ответ — деталь
    через rewrite_thumbs; разбор заново идёт с длинным таймаутом; каждое действие — в журнале аудита."""
    _configure(login_as)
    viewer = login_as("viewer")
    operator = login_as("operator")
    video = "/trends/videos/hGnLlWpueSA"

    assert viewer.post(f"{video}/pick", json={"pick": True}).status_code == 403
    assert viewer.post(f"{video}/level", json={"level": "no"}).status_code == 403
    assert viewer.post(f"{video}/analyze").status_code == 403
    assert viewer.post(f"{video}/file").status_code == 403
    assert kino.requests == []

    assert operator.post(f"{video}/pick", json={}).status_code == 422  # pick обязателен
    assert operator.post(f"{video}/level", json={"level": "great"}).status_code == 422
    assert kino.requests == []

    picked = operator.post(f"{video}/pick", json={"pick": False})
    assert picked.status_code == 200, picked.text
    assert picked.json()["pick"] == 0 and picked.json()["pick_auto"] == 0
    assert picked.json()["thumb"] == "/api/v1/trends/videos/hGnLlWpueSA/thumb"
    assert json.loads(kino.requests[-1].content) == {"pick": False}

    noted = operator.post(f"{video}/pick", json={"pick": True, "note": "под баннер BPN"})
    assert noted.status_code == 200 and json.loads(kino.requests[-1].content) == {
        "pick": True,
        "note": "под баннер BPN",
    }

    level = operator.post(f"{video}/level", json={"level": "check"})
    assert level.status_code == 200, level.text
    assert level.json()["manual_level"] == "check" and level.json()["level"] == "check"
    assert json.loads(kino.requests[-1].content) == {"level": "check"}
    auto = operator.post(f"{video}/level", json={"level": None})
    assert auto.status_code == 200 and auto.json()["manual_level"] is None
    assert json.loads(kino.requests[-1].content) == {"level": None}

    analyzed = operator.post(f"{video}/analyze")
    assert analyzed.status_code == 200, analyzed.text
    assert analyzed.json()["notes"] == ["кадры: нужен сервер-хранилище"]
    assert analyzed.json()["frames_url"] == "/api/v1/trends/videos/hGnLlWpueSA/frames"
    assert kino.requests[-1].url.path == "/api/videos/hGnLlWpueSA/analyze"
    assert kino.requests[-1].extensions["timeout"]["read"] == 90.0  # разбор длится до минуты

    kino.requests.clear()
    ordered = operator.post(f"{video}/file")
    assert ordered.status_code == 200, ordered.text
    assert ordered.json()["video_id"] == "hGnLlWpueSA"  # деталь, а не {ok, status} Kino-trends
    assert [r.url.path for r in kino.requests] == ["/api/videos/hGnLlWpueSA/file", "/api/videos/hGnLlWpueSA"]

    kino.responses[("POST", "/api/videos/hGnLlWpueSA/file")] = (400, {"detail": "мало места на хранилище"})
    rejected = operator.post(f"{video}/file")
    assert rejected.status_code == 400 and rejected.json()["error"]["message"] == "мало места на хранилище"

    missing = operator.post("/trends/videos/zzzzzzzzzzz/pick", json={"pick": True})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "trends_video_not_found"

    rows = db.execute(
        "SELECT action, entity_type, entity_id, summary, diff FROM audit_log "
        "WHERE action LIKE 'trends.video_%' ORDER BY id"
    ).fetchall()
    assert [(r["action"], r["entity_type"], r["entity_id"]) for r in rows] == [
        ("trends.video_pick", "trends_video", "hGnLlWpueSA"),
        ("trends.video_pick", "trends_video", "hGnLlWpueSA"),
        ("trends.video_level", "trends_video", "hGnLlWpueSA"),
        ("trends.video_level", "trends_video", "hGnLlWpueSA"),
        ("trends.video_analyze", "trends_video", "hGnLlWpueSA"),
        ("trends.video_file", "trends_video", "hGnLlWpueSA"),
    ]
    assert rows[0]["diff"]["pick"] == 0 and "убран из подборки" in rows[0]["summary"]
    assert rows[2]["diff"]["manual_level"] == "check" and "уровень check" in rows[2]["summary"]
    assert "уровень авто" in rows[3]["summary"]
    assert "нужен сервер-хранилище" in rows[4]["summary"]


# ---------------------------------------------------------------- кино и сериалы


def test_films_list_detail_poster_and_not_found(login_as: Any, kino: FakeKino) -> None:
    """`GET /trends/films` — строки с правами, исходником и готовностью (ready=1 уходит в Kino-trends);
    `GET /trends/films/{id}` — бриф и статистика одним ответом, картинки через наш API."""
    _configure(login_as)
    viewer = login_as("viewer")

    resp = viewer.get(
        "/trends/films", params={"kind": "series", "sort": "growth", "period": "7", "limit": 20}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2 and [it["title_id"] for it in body["items"]] == ["Q23871", "Q915313"]
    film, series = body["items"]
    assert film["rights"]["level"] == "ok" and film["source"]["verified"] is True and film["ready"] is True
    assert film["top_video"]["thumb"] == "/api/v1/trends/videos/by5ssNKJCJU/thumb"
    assert film["poster_url"] == "/api/v1/trends/films/Q23871/poster"
    assert (
        series["ready"] is False and series["ready_reasons"] == ["нет исходника"] and series["source"] is None
    )
    forwarded = dict(kino.requests[-1].url.params)
    assert forwarded == {
        "period": "7",
        "lang": "ru",
        "sort": "growth",
        "kind": "series",
        "limit": "20",
        "offset": "0",
    }

    ready = viewer.get("/trends/films", params={"ready": "true"})
    assert ready.status_code == 200 and ready.json()["total"] == 1
    assert dict(kino.requests[-1].url.params)["ready"] == "1"
    assert viewer.get("/trends/films", params={"kind": "song"}).status_code == 422
    assert viewer.get("/trends/films", params={"period": "365"}).status_code == 422

    kino.requests.clear()
    detail = viewer.get("/trends/films/Q23871", params={"period": "90"})
    assert detail.status_code == 200, detail.text
    data = detail.json()
    assert [r.url.path for r in kino.requests] == ["/api/brief/Q23871", "/api/films/Q23871"]
    assert all(dict(r.url.params) == {"period": "90", "lang": "ru"} for r in kino.requests)
    assert data["title"]["label"] == "Брестская крепость" and data["title"]["imdb_id"] == "tt1343703"
    assert data["stats"]["n_shorts"] == 2 and data["why"] == ["+4 млн просмотров за неделю"]
    assert data["rights"]["risk"] == 12 and data["cutter"]["clip_max_s"] == 59
    assert data["plan"]["duration"] == "46–60 с" and data["scenes"][0]["name"] == "Начало обороны"
    assert data["sources"][0]["channel"] == "Киностудия Беларусьфильм"
    assert data["source"]["video_id"] == "jUzGyns9TCE"
    assert data["brief_extra_field"] == "насквозь"  # extra=allow
    # статистика подмешана: каналы, дни, сезоны, чужие нарезки — с обложкой через наш API
    assert data["channels"][0]["name"] == "KinoComrad" and len(data["daily"]) == 2
    assert data["seasons"] == [{"season": 1, "n": 2, "views": 139}]  # у брифа пусто — берём из статистики
    assert data["videos"][0]["thumb"] == "/api/v1/trends/videos/by5ssNKJCJU/thumb"
    # у референсов брифа video_id из ссылки, миниатюра через прокси
    assert data["references"][0]["video_id"] == "by5ssNKJCJU"
    assert data["references"][0]["thumb"] == "/api/v1/trends/videos/by5ssNKJCJU/thumb"
    assert data["poster_url"] == "/api/v1/trends/films/Q23871/poster"
    assert data["imdb_url"] == "https://www.imdb.com/title/tt1343703/"
    assert data["kinopoisk_url"] == "https://www.kinopoisk.ru/film/436263/"
    assert "i.ytimg.com" not in detail.text

    # статистики за период нет (404) — карточка всё равно собирается из брифа
    kino.responses[("GET", "/api/films/Q23871")] = (404, {"detail": "нет статистики"})
    partial = viewer.get("/trends/films/Q23871")
    assert partial.status_code == 200 and partial.json()["videos"] == []
    assert partial.json()["stats"]["n_shorts"] == 2
    del kino.responses[("GET", "/api/films/Q23871")]

    poster = viewer.get("/trends/films/Q23871/poster")
    assert poster.status_code == 200 and poster.headers["content-type"].startswith("image/jpeg")
    assert poster.content.startswith(b"\xff\xd8") and "max-age" in poster.headers["cache-control"]
    assert kino.requests[-1].url.path == "/api/films/Q23871/poster.jpg"

    rights = viewer.get("/trends/films/Q23871/rights")
    assert (
        rights.status_code == 200 and rights.json()["holder_policy_ru"] == "заявляет права через Content ID"
    )
    sources = viewer.get("/trends/films/Q23871/sources")
    assert sources.status_code == 200 and sources.json()["items"][0]["verified"] == 1

    missing = viewer.get("/trends/films/Q404")
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"]["code"] == "trends_film_not_found"
    assert viewer.get("/trends/films/Q404/poster").status_code == 404
    assert viewer.get("/trends/films/bad id/rights").status_code in (404, 422)


def test_film_actions_roles_and_audit(login_as: Any, kino: FakeKino, db: Any) -> None:
    """Права и исходники — только оператор: перепроверка и поиск идут с длинным таймаутом, решение по правам
    уходит телом `{level, note}`, повторный запуск поиска для топа — 409; всё в журнале аудита."""
    _configure(login_as)
    viewer = login_as("viewer")
    operator = login_as("operator")
    film = "/trends/films/Q23871"

    assert viewer.post(f"{film}/rights/refresh").status_code == 403
    assert viewer.post(f"{film}/rights/manual", json={"level": "block"}).status_code == 403
    assert viewer.post(f"{film}/sources").status_code == 403
    assert viewer.post("/trends/films/find-sources").status_code == 403
    assert kino.requests == []
    assert operator.post(f"{film}/rights/manual", json={"level": "maybe"}).status_code == 422
    assert kino.requests == []

    refreshed = operator.post(f"{film}/rights/refresh")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["notes"] == ["перепроверено чужих нарезок: 22, снесено: 0"]
    assert kino.requests[-1].extensions["timeout"]["read"] == 90.0

    manual = operator.post(f"{film}/rights/manual", json={"level": "caution", "note": "музыка в сценах"})
    assert manual.status_code == 200, manual.text
    assert manual.json()["level"] == "caution" and manual.json()["signals"]["manual_level"] == "caution"
    assert json.loads(kino.requests[-1].content) == {"level": "caution", "note": "музыка в сценах"}
    auto = operator.post(f"{film}/rights/manual", json={"level": None})
    assert auto.status_code == 200 and json.loads(kino.requests[-1].content) == {"level": None}

    found = operator.post(f"{film}/sources")
    assert found.status_code == 200 and found.json()["items"][0]["video_id"] == "jUzGyns9TCE"
    assert kino.requests[-1].method == "POST" and kino.requests[-1].url.path == "/api/films/Q23871/sources"
    assert kino.requests[-1].extensions["timeout"]["read"] == 90.0

    started = operator.post("/trends/films/find-sources", params={"limit": 40})
    assert started.status_code == 200, started.text
    assert started.json() == {"started": True, "limit": 40}
    assert kino.requests[-1].url.path == "/api/films/sources/find-top"
    assert dict(kino.requests[-1].url.params) == {"limit": "40"}
    kino.responses[("POST", "/api/films/sources/find-top")] = (409, {"detail": "поиск исходников уже идёт"})
    busy = operator.post("/trends/films/find-sources")
    assert busy.status_code == 409, busy.text
    assert busy.json()["error"] == {"code": "conflict", "message": "поиск исходников уже идёт", "details": {}}

    missing = operator.post("/trends/films/Q404/rights/manual", json={"level": "ok"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "trends_film_not_found"

    rows = db.execute(
        "SELECT action, entity_type, entity_id, summary, diff FROM audit_log "
        "WHERE action LIKE 'trends.film%' ORDER BY id"
    ).fetchall()
    assert [(r["action"], r["entity_type"], r["entity_id"]) for r in rows] == [
        ("trends.film_rights_refresh", "trends_film", "Q23871"),
        ("trends.film_rights_manual", "trends_film", "Q23871"),
        ("trends.film_rights_manual", "trends_film", "Q23871"),
        ("trends.film_sources_find", "trends_film", "Q23871"),
        ("trends.films_find_sources", "trends_film", "top"),
    ]
    assert rows[1]["diff"]["manual_level"] == "caution" and "музыка в сценах" in rows[1]["summary"]
    assert "права авто" in rows[2]["summary"]
    assert rows[3]["diff"]["sources"] == 1
