"""Ролл-апы и расчёты аналитики на настоящей БД (M4-B).

Критерий M4: фикстура снимков даёт ожидаемые `views_eod` и приросты по бизнес-дням МСК; трафик без
ботов и тестовых событий; `direct_bot_starts` — только старты без `_l`. Плюс сервис аналитики поверх
ролл-апов: сводка со свежестью данных, честная цена опубликованного ролика с браком, режим cohort,
разрезы, ряды, воронка, карточка ролика и A/B.

Время фикстур строится от «сегодня» по часам БД в Europe/Moscow (дни `today-1`, `today-2` уже прошли
целиком), поэтому тест не зависит от того, когда его запускают.
"""

from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from psycopg.types.json import Jsonb

from autoshorts.analytics.rollups import stats_rollup

MSK = ZoneInfo("Europe/Moscow")


# ---------------------------------------------------------------- фикстуры данных


def msk(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=MSK)


@pytest.fixture
def today(db_conn: Any) -> date:
    return db_conn.execute("SELECT (now() AT TIME ZONE 'Europe/Moscow')::date AS d").fetchone()["d"]


@pytest.fixture
def base(db_conn: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from autoshorts.services import secrets as secrets_service
    from autoshorts.services import settings as settings_service

    monkeypatch.setattr(secrets_service, "ENV_FILE", None)
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    secrets_service.invalidate_cache()
    settings_service.invalidate_cache()
    db = db_conn
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
    va = db.execute(
        "INSERT INTO banner_variants (campaign_id, code, name, status, weight) "
        "VALUES (%s, 'A', 'Домен', 'active', 50) RETURNING id",
        (campaign,),
    ).fetchone()["id"]
    vb = db.execute(
        "INSERT INTO banner_variants (campaign_id, code, name, status, weight) "
        "VALUES (%s, 'B', 'Домен и код', 'active', 50) RETURNING id",
        (campaign,),
    ).fetchone()["id"]
    return {"campaign": campaign, "variant_a": va, "variant_b": vb}


def make_job(db: Any, **fields: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "video_uid": secrets.token_hex(6),
        "channel_id": "taro_main",
        "niche_id": "taro",
        "template_version": "v1",
        "kind": "generate",
        "status": "published",
        "product_id": "umka",
    }
    values.update(fields)
    names = sorted(values)
    return db.execute(
        f"INSERT INTO jobs ({', '.join(names)}) "
        f"VALUES ({', '.join('%(' + n + ')s' for n in names)}) RETURNING *",
        values,
    ).fetchone()


def make_pub(db: Any, job: dict[str, Any], published_at: datetime, **fields: Any) -> dict[str, Any]:
    platform = fields.pop("platform", "youtube")
    values: dict[str, Any] = {
        "job_id": job["id"],
        "channel_id": job["channel_id"],
        "platform": platform,
        "platform_video_id": secrets.token_hex(5)[:11],
        "master_file_path": f"masters/{job['video_uid']}.mp4",
        "published_at": published_at,
        "status": "live",
        "verify_status": "ok",
        "title": f"Ролик {job['video_uid']}",
        "product_id": job.get("product_id"),
        "campaign_id": job.get("campaign_id"),
        "banner_variant_id": job.get("banner_variant_id"),
    }
    values.update(fields)
    names = sorted(values)
    return db.execute(
        f"INSERT INTO publications ({', '.join(names)}) "
        f"VALUES ({', '.join('%(' + n + ')s' for n in names)}) RETURNING *",
        values,
    ).fetchone()


def make_link(db: Any, pub: dict[str, Any] | None = None, **fields: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "code": fields.pop("code", "c" + secrets.token_hex(4)),
        "product_id": "umka",
        "level": "publication" if pub else "channel",
        "publication_id": pub["id"] if pub else None,
        "job_id": pub["job_id"] if pub else None,
        "channel_id": pub["channel_id"] if pub else fields.pop("channel_id", "taro_main"),
        "platform": pub["platform"] if pub else None,
    }
    values.update(fields)
    names = sorted(values)
    return db.execute(
        f"INSERT INTO tracking_links ({', '.join(names)}) "
        f"VALUES ({', '.join('%(' + n + ')s' for n in names)}) RETURNING *",
        values,
    ).fetchone()


def snap(db: Any, pub: dict[str, Any], at: datetime, views: int, **kw: Any) -> None:
    db.execute(
        "INSERT INTO metrics_snapshots (publication_id, captured_at, views, likes, comments, source) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (pub["id"], at, views, kw.get("likes"), kw.get("comments"), kw.get("source", "yt_data")),
    )


def click(
    db: Any, link: dict[str, Any] | None, at: datetime, route: str, visitor: str, *, bot: bool = False
) -> None:
    db.execute(
        """INSERT INTO clicks (occurred_at, code, tracking_link_id, route, product_id, publication_id, job_id,
                               channel_id, platform, visitor_key, is_bot)
           VALUES (%s, %s, %s, %s, 'umka', %s, %s, %s, %s, %s, %s)""",
        (
            at,
            link["code"] if link else "zzunknown",
            link["id"] if link else None,
            route,
            (link or {}).get("publication_id"),
            (link or {}).get("job_id"),
            (link or {}).get("channel_id"),
            (link or {}).get("platform"),
            visitor,
            bot,
        ),
    )


def conv(
    db: Any,
    link: dict[str, Any] | None,
    at: datetime,
    event: str,
    user: str | None,
    *,
    via_landing: bool = False,
    amount: int | None = None,
    test: bool = False,
) -> None:
    db.execute(
        """INSERT INTO conversions (product_id, external_event_id, event_type, occurred_at, tag, via_landing,
                                    tracking_link_id, publication_id, job_id, channel_id, platform,
                                    user_ref_hash, amount_minor, currency, is_test, meta)
           VALUES ('umka', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            secrets.token_hex(8),
            event,
            at,
            ((link["code"] + ("_l" if via_landing else "")) if link else None),
            via_landing,
            (link or {}).get("id"),
            (link or {}).get("publication_id"),
            (link or {}).get("job_id"),
            (link or {}).get("channel_id"),
            (link or {}).get("platform"),
            user,
            amount,
            "RUB" if amount is not None else None,
            test,
            Jsonb({}),
        ),
    )


def rollup(db: Any, days: int = 3) -> dict[str, Any]:
    with db.transaction():
        return stats_rollup(db, days=days, tz="Europe/Moscow")


# ---------------------------------------------------------------- ролл-апы (критерий M4)


def test_views_eod_and_deltas_by_moscow_day(db_conn: Any, base: dict[str, Any], today: date) -> None:
    db = db_conn
    d2, d1 = today - timedelta(days=2), today - timedelta(days=1)
    job = make_job(db)
    pub = make_pub(db, job, msk(d2, 10))
    snap(db, pub, msk(d2, 22), 1000, likes=10, comments=1)
    snap(db, pub, msk(d1, 22), 3400, likes=34, comments=3)
    # удалённая по ошибке регистрация в ролл-ап не идёт
    gone = make_pub(db, make_job(db), msk(d2, 11), deleted_at=msk(d1, 1))
    snap(db, gone, msk(d1, 12), 500)
    res = rollup(db)
    assert res["date_from"] == d2.isoformat() and res["date_to"] == today.isoformat()
    rows = db.execute(
        "SELECT day, views_eod, views_delta, likes_delta, comments_delta FROM stats_daily_views "
        "WHERE publication_id = %s ORDER BY day",
        (pub["id"],),
    ).fetchall()
    # конец d2 МСК = полночь d1: между 22:00 d2 (1000) и 22:00 d1 (3400) прошло 2 ч из 24 → 1200
    assert [(r["day"], r["views_eod"], r["views_delta"]) for r in rows] == [
        (d2, 1200, 1200),
        (d1, 3400, 2200),
        (today, 3400, 0),
    ]
    assert [r["likes_delta"] for r in rows] == [12, 22, 0]
    assert sum(r["views_delta"] for r in rows) == 3400
    assert not db.execute(
        "SELECT 1 FROM stats_daily_views WHERE publication_id = %s", (gone["id"],)
    ).fetchone()
    # повторный прогон — те же строки (окно заменяется целиком)
    rollup(db)
    again = db.execute(
        "SELECT day, views_eod, views_delta FROM stats_daily_views WHERE publication_id = %s ORDER BY day",
        (pub["id"],),
    ).fetchall()
    assert [(r["day"], r["views_eod"], r["views_delta"]) for r in again] == [
        (r["day"], r["views_eod"], r["views_delta"]) for r in rows
    ]


def test_traffic_rollup_excludes_bots_and_tests_direct_starts_without_l(
    db_conn: Any, base: dict[str, Any], today: date
) -> None:
    db = db_conn
    d1 = today - timedelta(days=1)
    pub = make_pub(db, make_job(db), msk(d1 - timedelta(days=1), 10))
    link = make_link(db, pub, code="yt_abc")
    early = msk(d1, 1)  # 01:00 МСК — это ещё вчера по UTC: день должен быть d1
    click(db, link, early, "landing", "v1")
    click(db, link, early + timedelta(minutes=1), "landing", "v1")
    click(db, link, early + timedelta(minutes=2), "to_telegram", "v1")
    click(db, link, early + timedelta(minutes=3), "direct", "v2")
    click(db, link, early + timedelta(minutes=4), "landing", "bot1", bot=True)
    conv(db, link, early + timedelta(minutes=5), "bot_start", "u1", via_landing=True)
    conv(db, link, early + timedelta(minutes=6), "bot_start", "u2")
    conv(db, link, early + timedelta(minutes=7), "bot_start", "u2")  # тот же человек повторно
    conv(db, link, early + timedelta(minutes=8), "bot_start", "u9", test=True)
    conv(db, link, early + timedelta(minutes=9), "first_payment", "u1", amount=19000)
    conv(db, link, early + timedelta(minutes=10), "refund", "u1", amount=5000)
    # прямой трафик продукта: неизвестный код и событие без тега
    click(db, None, early, "unknown_code", "v3")
    conv(db, None, early, "bot_start", "u5")
    # строка вне окна не трогается
    db.execute(
        "INSERT INTO stats_daily_traffic (day, tracking_link_id, landing_views) VALUES (%s, %s, 77)",
        (today - timedelta(days=10), link["id"]),
    )
    rollup(db)
    row = db.execute(
        "SELECT * FROM stats_daily_traffic WHERE tracking_link_id = %s AND day = %s", (link["id"], d1)
    ).fetchone()
    assert row is not None
    assert (row["landing_views"], row["to_telegram"], row["direct"]) == (2, 1, 1)
    assert row["unique_visitors"] == 2  # v1 и v2; бот не считается
    assert (row["bot_starts"], row["unique_bot_starts"], row["direct_bot_starts"]) == (3, 2, 1)
    assert (row["first_payments"], row["revenue_minor"], row["refunds"], row["refund_minor"]) == (
        1,
        19000,
        1,
        5000,
    )
    assert not db.execute(
        "SELECT 1 FROM stats_daily_traffic WHERE day = %s", (d1 - timedelta(days=1),)
    ).fetchone(), "01:00 МСК не должен уйти в предыдущий день по UTC"
    old = db.execute(
        "SELECT landing_views FROM stats_daily_traffic WHERE day = %s", (today - timedelta(days=10),)
    ).fetchone()
    assert old["landing_views"] == 77
    un = db.execute(
        "SELECT * FROM stats_daily_unattributed WHERE product_id = 'umka' AND day = %s", (d1,)
    ).fetchone()
    assert (un["clicks_unknown_code"], un["unique_visitors"], un["bot_starts"], un["unique_bot_starts"]) == (
        1,
        1,
        1,
        1,
    )


def test_direct_telegram_click_is_not_a_second_visitor(
    db_conn: Any, base: dict[str, Any], today: date
) -> None:
    db = db_conn
    d1 = today - timedelta(days=1)
    db.execute("UPDATE products SET redirect_mode = 'direct_telegram' WHERE id = 'umka'")
    pub = make_pub(db, make_job(db), msk(d1, 9))
    link = make_link(db, pub)
    click(db, link, msk(d1, 12), "direct", "v1")
    conv(db, link, msk(d1, 12, 1), "bot_start", "u1")  # старт без _l
    rollup(db)
    row = db.execute(
        "SELECT * FROM stats_daily_traffic WHERE tracking_link_id = %s", (link["id"],)
    ).fetchone()
    assert row["direct"] == 1 and row["unique_visitors"] == 0 and row["direct_bot_starts"] == 1


# ---------------------------------------------------------------- сервис аналитики


def _q(model: Any, **kw: Any) -> Any:
    return model.model_validate(kw)


@pytest.fixture
def world(db_conn: Any, base: dict[str, Any], today: date) -> dict[str, Any]:
    """Два опубликованных ролика (генерация и нарезка), отклонённый, трафик, расход, ролл-ап."""
    db = db_conn
    d3, d2, d1 = (today - timedelta(days=n) for n in (3, 2, 1))
    gen = make_job(
        db, campaign_id=base["campaign"], banner_variant_id=base["variant_a"], created_at=msk(d3, 9)
    )
    source = db.execute(
        "INSERT INTO sources (title, origin, origin_url, status, niche_id) "
        "VALUES ('Лекция', 'url', 'https://www.youtube.com/watch?v=abcdefghijk', 'ready', 'taro') "
        "RETURNING id"
    ).fetchone()["id"]
    clip = make_job(db, kind="clip", source_id=source, created_at=msk(d3, 10))
    bad = make_job(db, status="rejected", created_at=msk(d3, 11))
    p_gen = make_pub(db, gen, msk(d3, 12))
    p_clip = make_pub(db, clip, msk(d2, 12))
    for pub, v1, v2 in ((p_gen, 2000, 5000), (p_clip, 800, 1000)):
        snap(db, pub, pub["published_at"] + timedelta(hours=10), v1)
        snap(db, pub, pub["published_at"] + timedelta(hours=30), v2)
    link = make_link(db, p_gen, code="yt_gen")
    click(db, link, msk(d1, 10), "landing", "a")
    click(db, link, msk(d1, 10, 5), "landing", "b")
    conv(db, link, msk(d1, 10, 6), "bot_start", "ua", via_landing=True)
    conv(db, link, msk(d1, 10, 7), "bot_start", "uc")
    conv(db, link, msk(d1, 11), "first_payment", "ua", amount=29900)
    chan = make_link(db, None, channel_id="taro_main", code="ch_taro")
    click(db, chan, msk(d1, 12), "landing", "z")
    conv(db, None, msk(d1, 12), "bot_start", "organic")  # прямой трафик — в итоги не идёт
    for job, cost in ((gen, 0.30), (clip, 0.05), (bad, 0.20)):
        db.execute(
            "INSERT INTO generation_events (job_id, step_name, provider, cost_usd, created_at) "
            "VALUES (%s, 'image', 'fal', %s, %s)",
            (job["id"], cost, msk(d3, 13)),
        )
    db.execute(
        "INSERT INTO generation_events (source_id, step_name, provider, cost_usd, created_at) "
        "VALUES (%s, 'asr', 'local', 0.10, %s)",
        (source, msk(d3, 13)),
    )
    # разбор брифа, по которому роликов нет, — накладные: в spend_usd только с include_overhead
    brief = db.execute(
        "INSERT INTO trend_briefs (external_video_id, niche_id) VALUES ('abcdefghijk', 'taro') RETURNING id"
    ).fetchone()["id"]
    db.execute(
        "INSERT INTO generation_events (trend_brief_id, step_name, provider, cost_usd, created_at) "
        "VALUES (%s, 'brief_analyze', 'llm', 0.07, %s)",
        (brief, msk(d3, 13)),
    )
    rollup(db, days=7)
    return {"gen": gen, "clip": clip, "bad": bad, "p_gen": p_gen, "p_clip": p_clip, "link": link, "d3": d3}


def test_summary_totals_attribution_and_freshness(db_conn: Any, world: dict[str, Any], today: date) -> None:
    from autoshorts.api.schemas.analytics import AnalyticsQuery, AnalyticsSummary
    from autoshorts.services import analytics

    q = _q(AnalyticsQuery, date_from=world["d3"].isoformat(), date_to=today.isoformat())
    out = AnalyticsSummary.model_validate(analytics.summary(db_conn, q)).model_dump(mode="json")
    t = out["totals"]
    assert t["views"] == 6000  # оба ролика: 5000 + 1000 к сегодняшнему дню
    assert t["landing_views"] == 3 and t["clicks"] == 3  # ролик и ссылка канала
    assert t["visitors"] == 3 + 1  # уникальные лендинга (a, b, z) + старт без _l (uc)
    assert t["users"] == 2 and t["bot_starts"] == 2  # organic — прямой трафик, не в итогах
    assert t["first_payments"] == 1 and t["revenue_minor"] == 29900
    assert t["videos_published"] == 2 and t["videos_rejected"] == 1 and t["videos_produced"] == 3
    # честная цена: 0.30 + 0.05 + 0.20 (брак) + 0.10 (ASR источника на клип) = 0.65 / 2
    assert t["spend_usd"] == pytest.approx(0.65)
    assert t["rejected_spend_usd"] == pytest.approx(0.20)
    assert t["cost_per_published_video_usd"] == pytest.approx(0.325)
    att = out["attribution"]
    assert att["exact"]["visitors"] == 3 and att["channel"]["visitors"] == 1
    assert att["direct"]["users"] == 1 and att["direct"]["bot_starts"] == 1
    fr = out["data_freshness"]
    assert fr["rollup_at"] is not None and fr["analytics_pulled_at"] is None
    yt = next(c for c in fr["collectors"] if c["collector"] == "yt_data")
    assert yt["state"] == "disabled" and yt["reason"] == "no_api_key" and yt["key_set"] is False
    assert any(w["code"] == "collector_disabled" for w in fr["warnings"]) and fr["stale"]
    assert any(qd["api"] == "youtube_data" and qd["limit"] == 10000 for qd in fr["quota"])
    assert out["previous"]["views"] == 0 and out["cohort"] is None
    assert t["overhead_usd"] == pytest.approx(0.07)
    with_oh = analytics.summary(
        db_conn,
        _q(
            AnalyticsQuery,
            date_from=world["d3"].isoformat(),
            date_to=today.isoformat(),
            include_overhead=True,
        ),
    )["totals"]
    assert with_oh["spend_usd"] == pytest.approx(0.72)


def test_filters_by_track_and_variant(db_conn: Any, world: dict[str, Any], today: date) -> None:
    from autoshorts.api.schemas.analytics import AnalyticsQuery
    from autoshorts.services import analytics

    clip = analytics.summary(
        db_conn,
        _q(AnalyticsQuery, date_from=world["d3"].isoformat(), date_to=today.isoformat(), track=["clip"]),
    )["totals"]
    assert clip["views"] == 1000 and clip["visitors"] == 0 and clip["videos_published"] == 1
    assert clip["spend_usd"] == pytest.approx(0.15)  # свой вызов + весь ASR источника (один клип)
    var = analytics.summary(
        db_conn,
        _q(
            AnalyticsQuery,
            date_from=world["d3"].isoformat(),
            date_to=today.isoformat(),
            banner_variant_id=[world["gen"]["banner_variant_id"]],
        ),
    )["totals"]
    # ссылка канала без варианта в фильтр по варианту не попадает
    assert var["views"] == 5000 and var["visitors"] == 3 and var["users"] == 2


def test_breakdown_timeseries_funnel_costs(db_conn: Any, world: dict[str, Any], today: date) -> None:
    from autoshorts.api.schemas.analytics import (
        AnalyticsBreakdown,
        AnalyticsCosts,
        AnalyticsFunnel,
        AnalyticsQuery,
        AnalyticsTimeseries,
        BreakdownQuery,
        CostsQuery,
        TimeseriesQuery,
    )
    from autoshorts.services import analytics

    rng = {"date_from": world["d3"].isoformat(), "date_to": today.isoformat()}
    bd = AnalyticsBreakdown.model_validate(
        analytics.breakdown(db_conn, _q(BreakdownQuery, **rng, dimension="video"))
    ).model_dump(mode="json")
    assert [r["key"] for r in bd["rows"]] == [str(world["p_gen"]["id"]), str(world["p_clip"]["id"])]
    top = bd["rows"][0]
    assert top["video"]["video_uid"] == world["gen"]["video_uid"] and top["metrics"]["views"] == 5000
    assert top["video"]["poster_url"].endswith("/media/poster") and top["needs_more_n"] is True
    by_track = analytics.breakdown(db_conn, _q(BreakdownQuery, **rng, dimension="track", sort="label"))
    labels = {r["key"]: r["label"] for r in by_track["rows"]}
    assert labels["generate"] == "Генерация" and labels["clip"] == "Нарезка"
    none_row = next(r for r in by_track["rows"] if r["key"] == "none")  # ссылка канала без трека
    assert none_row["metrics"]["visitors"] == 1
    ts = AnalyticsTimeseries.model_validate(
        analytics.timeseries(db_conn, _q(TimeseriesQuery, **rng, metric="views", group_by="channel", top=1))
    ).model_dump(mode="json")
    assert len(ts["series"][0]["points"]) == (today - world["d3"]).days + 1
    assert ts["series"][0]["total"] == 6000
    fn = AnalyticsFunnel.model_validate(analytics.funnel(db_conn, _q(AnalyticsQuery, **rng))).model_dump(
        mode="json"
    )
    assert fn["views"] == 6000 and fn["branches"]["bot"][-1]["value"] == 1
    co = AnalyticsCosts.model_validate(
        analytics.costs(db_conn, _q(CostsQuery, **rng, group_by="outcome"))
    ).model_dump(mode="json")
    assert co["totals"]["rejected_spend_usd"] == pytest.approx(0.20)
    assert co["totals"]["cost_per_published_video_usd"] == pytest.approx(0.325)
    groups = {g["key"]: g["spend_usd"] for g in co["groups"]}
    assert groups["published"] == pytest.approx(0.45) and groups["rejected"] == pytest.approx(0.20)


def test_cohort_mode_counts_only_mature_videos(db_conn: Any, world: dict[str, Any], today: date) -> None:
    from autoshorts.api.schemas.analytics import AnalyticsCohorts, AnalyticsQuery, CohortsQuery
    from autoshorts.services import analytics

    rng = {"date_from": world["d3"].isoformat(), "date_to": today.isoformat()}
    out = analytics.summary(db_conn, _q(AnalyticsQuery, **rng, mode="cohort", cohort_age="h24"))
    assert out["cohort"] == {"age": "h24", "videos": 2, "videos_mature": 2, "immature": 0}
    # на 24 ч: gen — между 2000 (10 ч) и 5000 (30 ч) → 4100; clip — 800 + 200·14/20 = 940
    assert out["totals"]["views"] == 4100 + 940
    life = analytics.summary(db_conn, _q(AnalyticsQuery, **rng, mode="cohort"))
    assert life["totals"]["views"] == 6000 and life["totals"]["users"] == 2
    ch = AnalyticsCohorts.model_validate(
        analytics.cohorts(db_conn, _q(CohortsQuery, **rng, granularity="day", aggregate="sum"))
    ).model_dump(mode="json")
    assert sum(r["n_videos"] for r in ch["rows"]) == 2
    first = ch["rows"][0]
    assert first["values"]["h24"] == pytest.approx(4100) and first["mature"]["h24"] == 1


def test_video_card(db_conn: Any, world: dict[str, Any]) -> None:
    from autoshorts.api.schemas.analytics import VideoAnalytics, VideoAnalyticsQuery
    from autoshorts.services import analytics

    out = VideoAnalytics.model_validate(
        analytics.video(db_conn, world["p_gen"]["id"], VideoAnalyticsQuery())
    ).model_dump(mode="json")
    assert len(out["snapshots"]) == 2 and out["ladder"][0]["age_hours"] == 1
    assert out["publication"]["freshness"]["collector"] == "yt_data"
    h24 = next(a for a in out["age_views"] if a["age"] == "h24")
    assert h24["reached"] and h24["views"] == pytest.approx(4100) and h24["interpolated"]
    assert out["totals"]["users"] == 2 and out["totals"]["revenue_minor"] == 29900
    assert out["costs"]["job_cost_usd"] == pytest.approx(0.30) and out["costs"]["total_usd"] == pytest.approx(
        0.30
    )
    assert {c["event_type"] for c in out["conversions"]} == {"bot_start", "first_payment"}
    clip = analytics.video(db_conn, world["p_clip"]["id"], VideoAnalyticsQuery())
    assert clip["costs"]["source_share_usd"] == pytest.approx(0.10)


def test_experiment_not_ready_with_two_videos(
    db_conn: Any, world: dict[str, Any], base: dict[str, Any]
) -> None:
    from autoshorts.api.schemas.experiments import Experiment
    from autoshorts.services import analytics

    out = Experiment.model_validate(analytics.experiment(db_conn, base["campaign"])).model_dump(mode="json")
    assert out["ready"] is False and out["reason"] == "not_enough_mature_videos" and out["verdict"] is None
    va = next(v for v in out["variants"] if v["code"] == "A")
    # ролик A вышел три дня назад в 12:00 МСК: зрелый (≥ 72 ч) или нет — зависит от времени прогона
    assert va["videos"] == 1 and va["needs_more"] in (19, 20) and va["spend_usd"] == pytest.approx(0.30)
    assert out["min_videos_per_variant"] == 20 and out["min_age_hours"] == 72


def test_channel_stats_helper_for_channels_screen(db_conn: Any, world: dict[str, Any]) -> None:
    from autoshorts.api.schemas.channels import ChannelStats
    from autoshorts.repos.analytics import channel_stats

    stats = ChannelStats.model_validate(channel_stats(db_conn, "taro_main"))
    assert stats.publications_total == 2 and stats.publications_30d == 2
    assert stats.views_30d == 6000 and stats.captured_at is None
    # 72 ч прошло у ролика трёхдневной давности не всегда — медиана по достигшим, без экстраполяции
    assert stats.views_72h_median is None or stats.views_72h_median == pytest.approx(5000)


# ---------------------------------------------------------------- seed-demo


def test_seed_demo_guard_and_fresh_database(make_database: Any) -> None:
    import argparse

    import psycopg
    from psycopg.rows import dict_row

    from autoshorts.cli import CliError
    from autoshorts.cli.analytics import check_target, cmd_seed_demo
    from autoshorts.db.migrate import migrate

    server = "postgresql://u:p@127.0.0.1:5432"
    for protected in ("autoshorts", "autoshorts_v1"):
        with pytest.raises(CliError, match="--i-know"):
            check_target(f"{server}/{protected}", i_know=False)
        assert check_target(f"{server}/{protected}", i_know=True) == protected
    with pytest.raises(CliError):
        check_target(server, i_know=False)

    url = make_database("m4b_seed")
    migrate(url, backup=False, lock_timeout=30)
    args = argparse.Namespace(database_url=url, i_know=False, days=12, per_day=3, seed=7)
    assert cmd_seed_demo(args) == 0
    with psycopg.connect(url, row_factory=dict_row) as conn:
        counts = conn.execute(
            """SELECT (SELECT count(DISTINCT j.kind) FROM publications p JOIN jobs j ON j.id = p.job_id)
                        AS tracks,
                      (SELECT count(DISTINCT platform) FROM publications) AS platforms,
                      (SELECT count(*) FROM jobs WHERE status = 'rejected') AS rejected,
                      (SELECT count(DISTINCT via_landing) FROM conversions WHERE event_type = 'bot_start')
                        AS vl,
                      (SELECT count(*) FROM clicks WHERE is_bot) AS bots,
                      (SELECT count(*) FROM conversions WHERE tracking_link_id IS NULL) AS direct,
                      (SELECT count(*) FROM stats_daily_views) AS sdv,
                      (SELECT count(*) FROM stats_daily_traffic) AS sdt"""
        ).fetchone()
        assert counts["tracks"] == 2 and counts["platforms"] == 2 and counts["vl"] == 2
        assert counts["rejected"] > 0 and counts["bots"] > 0 and counts["direct"] > 0
        assert counts["sdv"] > 0 and counts["sdt"] > 0
        before = conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"]
    assert cmd_seed_demo(args) == 0  # повтор: ничего не добавляет
    with psycopg.connect(url, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == before


def test_legacy_dashboard_ports(db_conn: Any, world: dict[str, Any]) -> None:
    from autoshorts.repos.analytics import qc_pass_rate, reject_reasons

    db_conn.execute("UPDATE jobs SET reject_reason = 'hook_slabyy' WHERE id = %s", (world["bad"]["id"],))
    make_job(db_conn, status="qc_failed")
    rate = qc_pass_rate(db_conn)
    assert rate == {"passed": 3, "failed": 1, "total": 4, "pass_rate_pct": 75.0}
    assert reject_reasons(db_conn) == [{"reject_reason": "hook_slabyy", "count": 1}]
