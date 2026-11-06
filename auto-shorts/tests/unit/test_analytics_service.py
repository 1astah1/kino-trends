"""Сервис аналитики M4-B (`services/analytics.py`) через API.

Регресс 29.10.2026: `services/analytics.py` затёрли коллектором YouTube Analytics, и `GET /overview`
(«Пульт») падал `AttributeError: … has no attribute 'overview'`. Здесь — каждый эндпоинт Обзора,
`/analytics/*`, A/B и CSV отвечает 200 и валидной схемой на пустой базе и на базе с несколькими
публикациями; цифры «Пульта» сходятся с рядами, «Требует тебя», алертами и A/B; коллектор живёт в
`services/yt_analytics.py`, и `POST /publications/analytics/collect` зовёт именно его.

Подробные расчёты (ролл-апы, атрибуция, когорты, честная цена) — `tests/db/test_rollups.py` и
`tests/api/test_analytics_api.py`; помощники SQL-фикстур берутся оттуда же.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from psycopg.types.json import Jsonb

from tests.api.conftest import api_app, client, db, login_as, make_user, new_client  # noqa: F401
from tests.db.test_rollups import click, conv, make_job, make_link, make_pub, msk, rollup, snap

CSV_BOM = b"\xef\xbb\xbf"

DIMENSIONS = ("video", "channel", "niche", "product", "campaign", "variant", "track", "platform")
COST_GROUPS = ("provider", "step", "track", "outcome", "channel", "niche", "product")
REPORTS = ("summary", "timeseries", "breakdown", "funnel", "cohorts", "costs")


@pytest.fixture
def viewer(login_as: Any) -> Any:  # noqa: F811
    return login_as("viewer")


def _models() -> dict[str, type]:
    from autoshorts.api.schemas import analytics as A
    from autoshorts.api.schemas.experiments import Experiment
    from autoshorts.api.schemas.overview import Overview

    return {
        "overview": Overview,
        "summary": A.AnalyticsSummary,
        "timeseries": A.AnalyticsTimeseries,
        "breakdown": A.AnalyticsBreakdown,
        "funnel": A.AnalyticsFunnel,
        "cohorts": A.AnalyticsCohorts,
        "costs": A.AnalyticsCosts,
        "freshness": A.DataFreshness,
        "experiment": Experiment,
        "video": A.VideoAnalytics,
    }


def _cases(rng: str, campaign_id: int, publication_id: int | None) -> list[tuple[str, str]]:
    """(путь, модель ответа): все GET-эндпоинты с типичными сочетаниями параметров."""
    cases = [
        ("/overview", "overview"),
        (f"/overview?{rng}&basis=engaged&platform=youtube", "overview"),
        ("/analytics/summary", "summary"),
        (f"/analytics/summary?{rng}&mode=cohort&cohort_age=h24", "summary"),
        (f"/analytics/summary?{rng}&basis=engaged&include_overhead=true&track=generate", "summary"),
        (f"/analytics/timeseries?{rng}", "timeseries"),
        (
            f"/analytics/timeseries?{rng}&metric=visitors&granularity=week&group_by=variant&top=1",
            "timeseries",
        ),
        (f"/analytics/timeseries?{rng}&metric=users&group_by=video", "timeseries"),
        (
            f"/analytics/timeseries?{rng}&metric=cost_per_visitor_usd&group_by=channel&mode=cohort",
            "timeseries",
        ),
        (f"/analytics/funnel?{rng}", "funnel"),
        (f"/analytics/funnel?{rng}&mode=cohort&cohort_age=h72", "funnel"),
        (f"/analytics/cohorts?{rng}", "cohorts"),
        (f"/analytics/cohorts?{rng}&metric=ctr_per_1k_views&aggregate=sum&granularity=day", "cohorts"),
        (f"/analytics/cohorts?{rng}&metric=users&aggregate=mean", "cohorts"),
        ("/analytics/freshness", "freshness"),
        (f"/campaigns/{campaign_id}/experiment", "experiment"),
        (f"/campaigns/{campaign_id}/experiment?platform=youtube", "experiment"),
        (f"/analytics/breakdown?{rng}&dimension=channel&sort=label", "breakdown"),
        (f"/analytics/breakdown?{rng}&dimension=video&sort=-published_at&limit=2&offset=1", "breakdown"),
    ]
    for dim in DIMENSIONS:
        cases.append((f"/analytics/breakdown?{rng}&dimension={dim}", "breakdown"))
        cases.append((f"/analytics/breakdown?{rng}&dimension={dim}&mode=cohort&cohort_age=h72", "breakdown"))
    for group in COST_GROUPS:
        cases.append((f"/analytics/costs?{rng}&group_by={group}&granularity=week&top=2", "costs"))
    cases.append((f"/analytics/costs?{rng}&mode=cohort&include_overhead=true", "costs"))
    if publication_id is not None:
        cases.append((f"/analytics/videos/{publication_id}", "video"))
        cases.append((f"/analytics/videos/{publication_id}?basis=engaged&traffic_granularity=day", "video"))
    return cases


def _check_all(viewer: Any, cases: list[tuple[str, str]]) -> dict[str, Any]:
    models = _models()
    failures: list[str] = []
    bodies: dict[str, Any] = {}
    for path, model in cases:
        resp = viewer.get(path)
        if resp.status_code != 200:
            failures.append(f"{path}: {resp.status_code} {resp.text[:300]}")
            continue
        try:
            models[model].model_validate(resp.json())
        except Exception as exc:  # noqa: BLE001 - собрать все расхождения со схемой разом
            failures.append(f"{path}: схема {model}: {str(exc)[:300]}")
        bodies[path] = resp.json()
    assert not failures, "\n".join(failures)
    return bodies


def _check_exports(viewer: Any, rng: str, extra: dict[str, str]) -> None:
    failures: list[str] = []
    for report in (*REPORTS, *extra):
        for dialect in ("excel_ru", "rfc4180"):
            q = f"report={report}&dialect={dialect}&{rng}{extra.get(report, '')}"
            resp = viewer.get(f"/analytics/export?{q}")
            ok = (
                resp.status_code == 200
                and resp.headers["content-type"].startswith("text/csv")
                and resp.content.startswith(CSV_BOM)
                and resp.headers["content-disposition"].startswith('attachment; filename="autoshorts_')
            )
            if not ok:
                failures.append(f"{q}: {resp.status_code} {resp.text[:200]}")
    assert not failures, "\n".join(failures)


# ---------------------------------------------------------------- пустая база


def test_module_split_collector_and_dashboard() -> None:
    from autoshorts.services import analytics, yt_analytics

    for name in (
        "overview",
        "summary",
        "timeseries",
        "breakdown",
        "funnel",
        "cohorts",
        "costs",
        "freshness",
        "video",
        "export_csv",
        "experiment",
    ):
        assert callable(getattr(analytics, name)), name
    for name in (
        "collect",
        "video_stats",
        "retention_curve",
        "watch_ratio_at",
        "failure_class",
        "AnalyticsError",
    ):
        assert hasattr(yt_analytics, name), name
    assert not hasattr(analytics, "collect"), "коллектор YouTube Analytics — в services/yt_analytics.py"


def test_collect_endpoint_uses_yt_analytics(login_as: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from autoshorts.services import yt_analytics

    calls: list[bool] = []

    def fake_collect(*, force: bool = False, **_: Any) -> dict[str, Any]:
        calls.append(force)
        return {"channels": [], "skipped": [], "fake": True}

    monkeypatch.setattr(yt_analytics, "collect", fake_collect)
    resp = login_as("operator").post("/publications/analytics/collect?force=true")
    assert resp.status_code == 200, resp.text
    assert resp.json()["fake"] is True and calls == [True]


def test_every_endpoint_on_empty_db(viewer: Any, db: Any) -> None:  # noqa: F811
    today = db.execute("SELECT (now() AT TIME ZONE 'Europe/Moscow')::date AS d").fetchone()["d"]
    rng = f"date_from={(today - timedelta(days=6)).isoformat()}&date_to={today.isoformat()}"
    # кампания без вариантов и роликов: A/B отвечает 200 «нет вариантов», а не 404
    campaign = db.execute(
        "INSERT INTO campaigns (product_id, name, status) VALUES ('umka', 'Пусто', 'active') RETURNING id"
    ).fetchone()["id"]
    bodies = _check_all(viewer, _cases(rng, campaign, None))

    ov = bodies["/overview"]
    assert ov["range"]["days"] == 7 and ov["range"]["date_to"] == today.isoformat()
    assert all(k["value"] in (0, None) for k in ov["kpi"].values())
    assert ov["kpi"]["ctr_per_1k_views"]["value"] is None and ov["kpi"]["views"]["delta_pct"] is None
    assert ov["top_videos"] == [] and ov["alerts"] == [] and ov["alerts_open"] == 0
    assert all(v == 0 for v in ov["todo"].values())
    assert ov["onboarding"]["done"] is False and ov["onboarding"]["publications"] == 0
    assert ov["data_freshness"]["stale"] is False and ov["data_freshness"]["rollup_at"] is None
    assert ov["basis_fallback"] is False and ov["annotations"] == []
    assert len(ov["series"]) == 15 and {len(s["points"]) for s in ov["series"]} == {7}
    assert bodies[f"/overview?{rng}&basis=engaged&platform=youtube"]["basis_fallback"] is True

    exp = bodies[f"/campaigns/{campaign}/experiment"]
    assert exp["ready"] is False and exp["reason"] == "no_variants" and exp["variants"] == []
    assert bodies[f"/analytics/breakdown?{rng}&dimension=video"]["rows"] == []
    assert bodies[f"/analytics/cohorts?{rng}"]["rows"] == []
    assert bodies[f"/analytics/timeseries?{rng}"]["series"][0]["total"] == 0

    missing = viewer.get("/analytics/videos/1")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "publication_not_found"
    _check_exports(viewer, rng, {"experiment": f"&campaign_id={campaign}"})


# ---------------------------------------------------------------- база с публикациями


@pytest.fixture
def seeded(db: Any) -> dict[str, Any]:  # noqa: F811
    """Три публикации (два варианта A/B на YouTube и нарезка в VK), брак, ролик на приёмке, трафик за первые
    72 ч, оплата, прямой трафик, расход, алерты; A/B готов при пороге «1 зрелый ролик на вариант»."""
    from autoshorts.services import settings as settings_service

    today = db.execute("SELECT (now() AT TIME ZONE 'Europe/Moscow')::date AS d").fetchone()["d"]
    d5, d4, d3, d1 = (today - timedelta(days=n) for n in (5, 4, 3, 1))
    db.execute("INSERT INTO niches (id, label, status, current_version) VALUES ('taro', 'Таро', 'active', 1)")
    db.execute("INSERT INTO clusters (id, title) VALUES ('main', 'Основной')")
    db.execute(
        """INSERT INTO channels (id, niche_id, platform, cluster, title, status, default_product_id)
           VALUES ('taro_main', 'taro', 'youtube', 'main', 'Таро · основной', 'active', 'umka'),
                  ('taro_vk', 'taro', 'vk', 'main', 'Таро · VK', 'active', 'umka')"""
    )
    campaign = db.execute(
        "INSERT INTO campaigns (product_id, name, status) VALUES ('umka', 'Осень', 'active') RETURNING id"
    ).fetchone()["id"]
    va, vb = (
        db.execute(
            "INSERT INTO banner_variants (campaign_id, code, name, status, weight) "
            "VALUES (%s, %s, %s, 'active', 50) RETURNING id",
            (campaign, code, name),
        ).fetchone()["id"]
        for code, name in (("A", "Домен"), ("B", "Домен и код"))
    )
    db.execute("INSERT INTO settings (key, value) VALUES ('ab_min_videos_per_variant', %s)", (Jsonb(1),))
    settings_service.invalidate_cache()
    gen_a = make_job(db, campaign_id=campaign, banner_variant_id=va, created_at=msk(d5, 8))
    gen_b = make_job(db, campaign_id=campaign, banner_variant_id=vb, created_at=msk(d4, 8))
    source = db.execute(
        "INSERT INTO sources (title, origin, origin_url, status, niche_id) "
        "VALUES ('Лекция', 'url', 'https://www.youtube.com/watch?v=abcdefghijk', 'ready', 'taro') "
        "RETURNING id"
    ).fetchone()["id"]
    clip = make_job(db, kind="clip", source_id=source, channel_id="taro_vk", created_at=msk(d3, 8))
    bad = make_job(db, status="rejected", created_at=msk(d4, 9))
    make_job(db, status="pending_review", created_at=msk(d1, 9))
    p_a = make_pub(db, gen_a, msk(d5, 10))
    p_b = make_pub(db, gen_b, msk(d4, 10))
    p_vk = make_pub(db, clip, msk(d3, 10), platform="vk", channel_id="taro_vk")
    for pub, points in (
        (p_a, ((6, 1000), (30, 3000), (80, 5000))),
        (p_b, ((6, 400), (30, 1200), (80, 2000))),
        (p_vk, ((10, 300), (40, 600))),
    ):
        for hours, views in points:
            snap(db, pub, pub["published_at"] + timedelta(hours=hours), views, likes=views // 100, comments=1)
    link_a, link_b = make_link(db, p_a, code="yt_a"), make_link(db, p_b, code="yt_b")
    for link, pub, n in ((link_a, p_a, 30), (link_b, p_b, 25)):
        for i in range(n):  # уникальные посетители лендинга в первые 72 ч
            click(
                db,
                link,
                pub["published_at"] + timedelta(hours=2, minutes=i),
                "landing",
                f"{link['code']}-{i}",
            )
        click(db, link, pub["published_at"] + timedelta(hours=3), "landing", "bot", bot=True)
    conv(db, link_a, p_a["published_at"] + timedelta(hours=4), "bot_start", "u1", via_landing=True)
    conv(db, link_a, p_a["published_at"] + timedelta(hours=5), "bot_start", "u2")
    conv(db, link_a, p_a["published_at"] + timedelta(hours=6), "first_payment", "u1", amount=19900)
    chan = make_link(db, None, channel_id="taro_main", code="ch_taro")
    click(db, chan, msk(d1, 12), "landing", "z")
    conv(db, None, msk(d1, 12), "bot_start", "organic")  # прямой трафик продукта
    for job, cost in ((gen_a, 0.30), (gen_b, 0.20), (clip, 0.10), (bad, 0.15)):
        db.execute(
            "INSERT INTO generation_events (job_id, step_name, provider, cost_usd, created_at) "
            "VALUES (%s, 'image', 'fal', %s, %s)",
            (job["id"], cost, msk(d4, 13)),
        )
    for i, severity in enumerate(("info", "warning", "critical", "warning", "info", "critical", "warning")):
        db.execute(
            "INSERT INTO alerts (code, severity, dedup_key, title, opened_at) "
            "VALUES ('disk_low', %s, %s, %s, %s)",
            (severity, f"k{i}", f"Алерт {i}", msk(d1, 10 + i)),
        )
    db.execute(
        "INSERT INTO alerts (code, severity, status, dedup_key, title, resolved_at) "
        "VALUES ('worker_down', 'critical', 'resolved', 'old', 'Закрыт', now())"
    )
    rollup(db, days=10)
    return {"today": today, "d5": d5, "campaign": campaign, "pubs": (p_a, p_b, p_vk)}


def test_every_endpoint_with_publications(viewer: Any, seeded: dict[str, Any]) -> None:
    rng = f"date_from={seeded['d5'].isoformat()}&date_to={seeded['today'].isoformat()}"
    p_a, p_b, p_vk = seeded["pubs"]
    bodies = _check_all(viewer, _cases(rng, seeded["campaign"], p_a["id"]))

    summary = bodies["/analytics/summary"]["totals"]
    assert summary["views"] == 5000 + 2000 + 600 and summary["videos_published"] == 3
    assert summary["visitors"] == 30 + 25 + 1 + 1  # лендинги роликов, ссылка канала, старт без _l
    cohort = bodies[f"/analytics/summary?{rng}&mode=cohort&cohort_age=h24"]
    # на 24 ч: 1000 + 2000·18/24, 400 + 800·18/24, 300 + 300·14/30
    assert cohort["totals"]["views"] == 2500 + 1000 + 440 and cohort["cohort"]["videos_mature"] == 3
    video_rows = bodies[f"/analytics/breakdown?{rng}&dimension=video"]["rows"]
    assert [r["key"] for r in video_rows] == [str(p["id"]) for p in (p_a, p_b, p_vk)]
    assert video_rows[0]["video"]["views_72h"] == 4680  # 3000 + 2000·42/50
    page = bodies[f"/analytics/breakdown?{rng}&dimension=video&sort=-published_at&limit=2&offset=1"]
    assert page["total_rows"] == 3 and [r["key"] for r in page["rows"]] == [str(p_b["id"]), str(p_a["id"])]

    exp = bodies[f"/campaigns/{seeded['campaign']}/experiment"]
    assert exp["ready"] is True and exp["verdict"] is not None and exp["visitors_total"] == 31 + 25
    assert {v["code"]: v["videos_mature"] for v in exp["variants"]} == {"A": 1, "B": 1}
    card = bodies[f"/analytics/videos/{p_a['id']}"]
    assert card["costs"]["job_cost_usd"] == pytest.approx(0.30) and card["totals"]["first_payments"] == 1
    assert next(a for a in card["age_views"] if a["age"] == "h72")["views"] == pytest.approx(4680)
    _check_exports(
        viewer,
        rng,
        {"experiment": f"&campaign_id={seeded['campaign']}", "video": f"&publication_id={p_a['id']}"},
    )


def test_overview_numbers_agree(viewer: Any, seeded: dict[str, Any]) -> None:
    rng = f"date_from={seeded['d5'].isoformat()}&date_to={seeded['today'].isoformat()}"
    resp = viewer.get(f"/overview?{rng}")
    assert resp.status_code == 200, resp.text
    ov = resp.json()
    kpi = ov["kpi"]
    assert ov["range"]["days"] == 6 and ov["previous_range"]["days"] == 6
    assert (
        kpi["views"]["value"] == 7600 and kpi["views"]["previous"] == 0 and kpi["views"]["delta_pct"] is None
    )
    assert kpi["videos_published"]["value"] == 3 and kpi["users"]["value"] == 2
    assert kpi["spend_usd"]["value"] == pytest.approx(0.75)  # весь расход роликов периода, включая брак
    assert kpi["cost_per_published_video_usd"]["value"] == pytest.approx(0.25)
    assert kpi["first_payments"]["value"] == 1 and kpi["revenue_minor"]["value"] == 19900
    # спарклайны — дневные точки тех же KPI: суммы аддитивных метрик сходятся с плитками
    series = {s["metric"]: s["points"] for s in ov["series"]}
    assert set(series) == set(kpi) and all(len(p) == 6 for p in series.values())
    for metric in ("views", "spend_usd", "videos_published", "first_payments", "revenue_minor", "visitors"):
        assert sum(p["v"] or 0 for p in series[metric]) == pytest.approx(kpi[metric]["value"]), metric
    assert ov["funnel"]["bot"][-1]["value"] == 1 and ov["funnel"]["domain"][0]["value"] == 7600
    tops = ov["top_videos"]
    assert [r["metrics"]["views"] for r in tops] == [5000, 2000, 600]
    assert (
        tops[0]["video"]["poster_url"].endswith("/media/poster") and tops[0]["video"]["track"] == "generate"
    )
    assert ov["todo"]["pending_review"] == 1 and ov["todo"]["experiments_ready"] == 1
    assert ov["alerts_open"] == 7 and len(ov["alerts"]) == 5
    assert [a["severity"] for a in ov["alerts"][:3]] == ["critical", "critical", "warning"]
    assert all(a["status"] == "open" for a in ov["alerts"])
    assert ov["onboarding"]["publications"] == 3 and ov["onboarding"]["channels"] == 2
    assert ov["onboarding"]["youtube_key_set"] is False and ov["onboarding"]["done"] is False
    fresh = ov["data_freshness"]
    assert fresh["rollup_at"] is not None and fresh["stale"] is True  # ключа YouTube нет, а ролики есть
    assert any(w["code"] == "collector_disabled" for w in fresh["warnings"])
    assert ov["budget"]["daily_limit_usd"] > 0
