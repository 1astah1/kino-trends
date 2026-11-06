"""Эндпоинты аналитики M4-B под логином: Обзор, сводка со свежестью, разрезы, ряды, воронка, когорты,
стоимость, карточка ролика, A/B и CSV с BOM (обе локали), ошибки 401/404/422.

Данные — помощники `tests/db/test_rollups.py` (SQL-фикстуры публикаций, снимков, кликов, конверсий)."""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from typing import Any

import pytest

from tests.db.test_rollups import click, conv, make_job, make_link, make_pub, msk, rollup, snap

CSV_BOM = b"\xef\xbb\xbf"


@pytest.fixture
def viewer(login_as: Any) -> Any:
    return login_as("viewer")


@pytest.fixture
def seeded(db: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from autoshorts.services import secrets as secrets_service

    monkeypatch.setattr(secrets_service, "ENV_FILE", None)
    today = db.execute("SELECT (now() AT TIME ZONE 'Europe/Moscow')::date AS d").fetchone()["d"]
    d3, d1 = today - timedelta(days=3), today - timedelta(days=1)
    db.execute("INSERT INTO niches (id, label, status, current_version) VALUES ('taro', 'Таро', 'active', 1)")
    db.execute("INSERT INTO clusters (id, title) VALUES ('main', 'Основной')")
    db.execute(
        "INSERT INTO channels (id, niche_id, platform, cluster, title, status) "
        "VALUES ('taro_main', 'taro', 'youtube', 'main', 'Таро · основной', 'active')"
    )
    campaign = db.execute(
        "INSERT INTO campaigns (product_id, name, status) VALUES ('umka', 'Осень', 'active') RETURNING id"
    ).fetchone()["id"]
    variant = db.execute(
        "INSERT INTO banner_variants (campaign_id, code, name, status, weight) "
        "VALUES (%s, 'A', 'Домен', 'active', 50) RETURNING id",
        (campaign,),
    ).fetchone()["id"]
    job = make_job(db, campaign_id=campaign, banner_variant_id=variant, created_at=msk(d3, 9))
    pub = make_pub(db, job, msk(d3, 12), title='=HYPERLINK("x")')  # формула в названии — экранируется
    snap(db, pub, msk(d3, 22), 1500)
    snap(db, pub, msk(d1, 22), 4000)
    link = make_link(db, pub, code="yt_api")
    click(db, link, msk(d1, 10), "landing", "v1")
    conv(db, link, msk(d1, 10, 1), "bot_start", "u1", via_landing=True)
    db.execute(
        "INSERT INTO generation_events (job_id, step_name, provider, cost_usd, created_at) "
        "VALUES (%s, 'image', 'fal', 0.25, %s)",
        (job["id"], msk(d3, 10)),
    )
    rollup(db, days=7)
    return {"today": today, "d3": d3, "pub": pub, "campaign": campaign}


def _range(s: dict[str, Any]) -> str:
    return f"date_from={s['d3'].isoformat()}&date_to={s['today'].isoformat()}"


def test_requires_login(client: Any) -> None:
    for path in ("/overview", "/analytics/summary", "/analytics/export", "/campaigns/1/experiment"):
        resp = client.get(f"/api/v1{path}")
        assert resp.status_code == 401 and resp.json()["error"]["code"] == "not_authenticated"


def test_overview_on_empty_db(viewer: Any) -> None:
    resp = viewer.get("/overview")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["range"]["days"] == 7 and body["onboarding"]["done"] is False
    assert body["onboarding"]["products"] >= 1  # продукт umka — из сидов миграций
    assert body["kpi"]["views"]["value"] == 0 and body["kpi"]["cost_per_visitor_usd"]["value"] is None
    assert body["top_videos"] == [] and body["todo"]["pending_review"] == 0
    assert "data_freshness" in body and body["budget"]["daily_limit_usd"] > 0
    assert len(body["series"]) == 15 and all(len(s["points"]) == 7 for s in body["series"])


def test_summary_and_freshness(viewer: Any, seeded: dict[str, Any]) -> None:
    resp = viewer.get(f"/analytics/summary?{_range(seeded)}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["totals"]["views"] == 4000 and body["totals"]["visitors"] == 1
    assert body["totals"]["cost_per_published_video_usd"] == pytest.approx(0.25)
    assert body["data_freshness"]["rollup_at"] is not None
    assert body["data_freshness"]["analytics_pulled_at"] is None
    engaged = viewer.get(f"/analytics/summary?{_range(seeded)}&basis=engaged").json()
    assert engaged["basis_fallback"] is True
    assert any(w["code"] == "engaged_views_unavailable" for w in engaged["data_freshness"]["warnings"])
    fresh = viewer.get("/analytics/freshness")
    assert fresh.status_code == 200 and fresh.json()["collectors"]


def test_other_endpoints_answer(viewer: Any, seeded: dict[str, Any]) -> None:
    r = _range(seeded)
    for path in (
        f"/analytics/timeseries?{r}&metric=visitors&granularity=week&group_by=variant",
        f"/analytics/breakdown?{r}&dimension=video",
        f"/analytics/breakdown?{r}&dimension=campaign&mode=cohort&cohort_age=h72",
        f"/analytics/funnel?{r}",
        f"/analytics/cohorts?{r}&metric=ctr_per_1k_views",
        f"/analytics/costs?{r}&group_by=provider&granularity=week",
        f"/overview?{r}",
        f"/campaigns/{seeded['campaign']}/experiment?platform=youtube",
        f"/analytics/videos/{seeded['pub']['id']}",
    ):
        resp = viewer.get(path)
        assert resp.status_code == 200, (path, resp.text[:300])
    video = viewer.get(f"/analytics/breakdown?{r}&dimension=video").json()
    assert video["rows"][0]["video"]["publication_id"] == seeded["pub"]["id"]


def test_errors(viewer: Any, seeded: dict[str, Any]) -> None:
    missing = viewer.get("/analytics/videos/999999")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "publication_not_found"
    camp = viewer.get("/campaigns/999999/experiment")
    assert camp.status_code == 404 and camp.json()["error"]["code"] == "campaign_not_found"
    bad_sort = viewer.get("/analytics/breakdown?sort=-nonsense")
    assert bad_sort.status_code == 422 and bad_sort.json()["error"]["code"] == "validation_error"
    for q in ("report=experiment", "report=video"):
        resp = viewer.get(f"/analytics/export?{q}")
        assert resp.status_code == 422 and resp.json()["error"]["code"] == "validation_error", q


def _rows(body: bytes, delimiter: str) -> list[list[str]]:
    assert body.startswith(CSV_BOM)
    text = body[len(CSV_BOM) :].decode("utf-8")
    assert "\r\n" in text
    return list(csv.reader(io.StringIO(text), delimiter=delimiter))


def test_csv_export_with_bom_both_dialects(viewer: Any, seeded: dict[str, Any]) -> None:
    r = _range(seeded)
    resp = viewer.get(f"/analytics/export?report=breakdown&dimension=video&{r}")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["content-disposition"] == (
        'attachment; filename="autoshorts_breakdown_'
        f'{seeded["d3"].isoformat()}_{seeded["today"].isoformat()}.csv"'
    )
    rows = _rows(resp.content, ";")
    assert rows[0][0] == "Ключ" and rows[0][1] == "Ролик" and "Просмотры" in rows[0]
    assert rows[1][0] == str(seeded["pub"]["id"])
    assert rows[1][rows[0].index("Заголовок")].startswith("'=")  # формула обезврежена
    machine = viewer.get(f"/analytics/export?report=summary&dialect=rfc4180&{r}")
    mrows = _rows(machine.content, ",")
    assert mrows[0] == ["metric", "value", "previous", "delta", "delta_pct"]
    views = next(row for row in mrows if row[0] == "views")
    assert views[1] == "4000"
    spend = next(
        row
        for row in _rows(viewer.get(f"/analytics/export?report=summary&{r}").content, ";")
        if row[0] == "Расход, $"
    )
    assert spend[1] == "0,25"  # десятичная запятая для Excel
    for report, extra in (
        ("timeseries", "&metric=views"),
        ("funnel", ""),
        ("cohorts", ""),
        ("costs", ""),
        ("experiment", f"&campaign_id={seeded['campaign']}"),
        ("video", f"&publication_id={seeded['pub']['id']}"),
    ):
        out = viewer.get(f"/analytics/export?report={report}{extra}&{r}")
        assert out.status_code == 200 and out.content.startswith(CSV_BOM), (report, out.text[:200])


def test_today_default_range(viewer: Any, seeded: dict[str, Any]) -> None:
    body = viewer.get("/analytics/summary").json()
    assert body["range"]["date_to"] == seeded["today"].isoformat() and body["range"]["days"] == 28
    assert date.fromisoformat(body["previous_range"]["date_to"]) == seeded["today"] - timedelta(days=28)
