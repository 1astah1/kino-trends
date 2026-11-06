"""`api/media.py` (Range в dev, X-Accel-Redirect в prod, защита пути) и `api/pagination.py`."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autoshorts.core.errors import AppError, NotFound, ValidationFailed


@pytest.fixture
def media_app(env_settings):
    from autoshorts.api.media import media_response

    root = env_settings.media_root
    (root / "jobs" / "abc").mkdir(parents=True)
    (root / "jobs" / "abc" / "final.mp4").write_bytes(bytes(range(256)) * 4)  # 1024 байта
    (root.parent / "outside.txt").write_text("secret")

    app = FastAPI()

    @app.exception_handler(AppError)
    async def _err(_r, exc: AppError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.status, content=exc.to_payload())

    @app.get("/m/{rel:path}")
    def serve(rel: str, download: bool = False):
        return media_response(rel, filename="Таро выпуск 1.mp4", download=download)

    return app


def test_dev_file_response_with_range(media_app) -> None:
    c = TestClient(media_app)
    full = c.get("/m/jobs/abc/final.mp4")
    assert full.status_code == 200 and len(full.content) == 1024
    assert full.headers["content-type"] == "video/mp4"
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["content-disposition"].startswith('inline; filename="Taro_vypusk_1.mp4"')
    assert "filename*=utf-8''" in full.headers["content-disposition"]

    part = c.get("/m/jobs/abc/final.mp4", headers={"Range": "bytes=10-19"})
    assert part.status_code == 206
    assert part.content == bytes(range(10, 20))
    assert part.headers["content-range"] == "bytes 10-19/1024"

    bad = c.get("/m/jobs/abc/final.mp4", headers={"Range": "bytes=5000-6000"})
    assert bad.status_code == 416

    dl = c.get("/m/jobs/abc/final.mp4", params={"download": "1"})
    assert dl.headers["content-disposition"].startswith("attachment;")


@pytest.mark.parametrize(
    "rel", ["../outside.txt", "jobs/../../outside.txt", "jobs/abc/missing.mp4", "jobs/abc"]
)
def test_path_guard(media_app, rel) -> None:
    resp = TestClient(media_app).get(f"/m/{rel}")
    assert resp.status_code == 404


def test_path_guard_unit() -> None:
    from autoshorts.api.media import safe_relpath

    assert safe_relpath("jobs/abc/final.mp4") == "jobs/abc/final.mp4"
    assert safe_relpath("./jobs//abc/final.mp4") == "jobs/abc/final.mp4"
    for rel in ("/etc/passwd", "a/../b", "..", "jobs/файл.mp4", "a b.mp4", "", "a\\..\\b"):
        with pytest.raises(NotFound):
            safe_relpath(rel)


def test_prod_uses_x_accel(media_app, monkeypatch) -> None:
    from autoshorts.core.config import reload_env

    monkeypatch.setenv("APP_ENV", "prod")
    reload_env()
    resp = TestClient(media_app).get("/m/jobs/abc/final.mp4", params={"download": "1"})
    assert resp.status_code == 200
    assert resp.headers["x-accel-redirect"] == "/_protected_media/jobs/abc/final.mp4"
    assert resp.content == b""
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.headers["content-disposition"].startswith("attachment;")


def test_ascii_filename() -> None:
    from autoshorts.api.media import ascii_filename, content_disposition

    assert ascii_filename("taro_main_2026-09-26_k7m2q.mp4") == "taro_main_2026-09-26_k7m2q.mp4"
    assert ascii_filename("Café Ünïcode.mp4") == "Cafe_Unicode.mp4"
    assert ascii_filename("Таро.mp4") == "Taro.mp4"
    assert ascii_filename("Щука Ёж") == "Shchuka_Ezh"
    assert ascii_filename("日本") == "file"
    assert content_disposition("a.mp4", download=True) == 'attachment; filename="a.mp4"'


# ---------------------------------------------------------------- пагинация


def test_journal_and_page() -> None:
    from autoshorts.api.pagination import clamp_limit, journal, page

    rows = [{"id": 9}, {"id": 8}]
    assert journal(rows, limit=2) == {"items": rows, "next_before_id": 8}
    assert journal(rows, limit=3) == {"items": rows, "next_before_id": None}
    assert journal([], limit=1) == {"items": [], "next_before_id": None}
    assert page([1, 2], total=10, limit=2, offset=4) == {
        "items": [1, 2],
        "total": 10,
        "limit": 2,
        "offset": 4,
    }
    assert clamp_limit(None) == 50 and clamp_limit(1000) == 200 and clamp_limit(0) == 1


def test_order_by_whitelist() -> None:
    from autoshorts.api.pagination import order_by

    allowed = {"created_at": "created_at", "id": "id", "views": "p.latest_views"}
    assert order_by("-created_at", allowed, "-id").as_string() == 'ORDER BY "created_at" DESC, id DESC'
    assert order_by(None, allowed, "id").as_string() == 'ORDER BY "id" ASC'
    assert order_by("views", allowed, "id").as_string() == 'ORDER BY "p"."latest_views" ASC, id ASC'
    with pytest.raises(ValidationFailed):
        order_by("password_hash", allowed, "id")
