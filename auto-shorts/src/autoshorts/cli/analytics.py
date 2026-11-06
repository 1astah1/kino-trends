"""Подкоманды аналитики (docs/ARCHITECTURE.md §6.7, §10.2 M4):

    autoshorts-admin stats-rollup [--days 3] [--database-url …]
    autoshorts-admin seed-demo --database-url postgresql://…/autoshorts_demo [--days 45] [--per-day 4]

* `stats-rollup` — пересчитать ролл-апы `stats_daily_*` за последние `--days` бизнес-дней (то же, что
  задача `stats_rollup` по расписанию).
* `seed-demo` — правдоподобные демо-данные для экранов Обзора и Аналитики: два трека (генерация и
  нарезка), три канала (YouTube и VK), две ниши, два продукта, кампании с вариантами A/B (у одной —
  настоящий сдвиг CTR, чтобы вердикт был виден), снимки метрик по лестнице опроса, клики с ботами,
  старты с лендинга (`_l`) и напрямую, регистрации, оплаты и возвраты, расход с браком и накладными,
  прямой трафик продукта; в конце — ролл-ап. Пишет **только** в базу из обязательного `--database-url`;
  базы `autoshorts` и `autoshorts_v1` (legacy и стенд) — только с `--i-know`. Повторный запуск на уже
  засеянной базе ничего не добавляет (метка — ниша `demo_taro`), только пересчитывает ролл-ап.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import random
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from autoshorts.cli import CliError

PROTECTED_DATABASES = frozenset({"autoshorts", "autoshorts_v1"})
DEMO_MARKER_NICHE = "demo_taro"
YT_ID_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
PACKAGE_ALPHABET = "23456789abcdefghjkmnpqrstvwxyz"
LADDER_HOURS = [1, 2, 4, 8, 12, 24, 36, 48, 72, 96, 120, 168]
TOPICS = [
    "Что он думает о тебе прямо сейчас",
    "Три карты на неделю",
    "Карта дня: знак перемен",
    "Почему деньги не задерживаются",
    "Одна привычка богатых людей",
    "Как не слить зарплату за неделю",
    "Расклад на отношения за минуту",
    "Совет старшего аркана",
]
REJECT_REASONS = ["hook_slabyy", "vizual_artefakty", "banner_perekryvaet", "zvuk_problema", "dublikat"]


def register(subparsers: argparse._SubParsersAction) -> None:  # noqa: SLF001 - тип argparse
    p = subparsers.add_parser(
        "stats-rollup",
        help="пересчитать ролл-апы аналитики за последние N бизнес-дней",
        description="Ролл-апы stats_daily_views/traffic/unattributed за окно (как задача stats_rollup).",
    )
    p.add_argument("--days", type=int, default=3, help="окно в бизнес-днях (1…400), по умолчанию 3")
    p.add_argument("--database-url", default=None, help="по умолчанию DATABASE_URL")
    p.set_defaults(func=cmd_stats_rollup)

    p = subparsers.add_parser(
        "seed-demo",
        help="залить демо-данные аналитики в отдельную базу",
        description=(
            "Демо-данные для Обзора и Аналитики: ролики обоих треков, варианты A/B, снимки, клики, "
            "конверсии, расход. Только в базу из --database-url; autoshorts и autoshorts_v1 — с --i-know."
        ),
    )
    p.add_argument("--database-url", required=True, help="база для демо-данных (обязательно)")
    p.add_argument("--i-know", action="store_true", help="разрешить защищённые базы autoshorts/autoshorts_v1")
    p.add_argument("--days", type=int, default=45, help="сколько дней истории (по умолчанию 45)")
    p.add_argument("--per-day", type=int, default=4, help="роликов в день (по умолчанию 4)")
    p.add_argument("--seed", type=int, default=20260927, help="зерно генератора (воспроизводимость)")
    p.set_defaults(func=cmd_seed_demo)


# ---------------------------------------------------------------- stats-rollup


def cmd_stats_rollup(args: argparse.Namespace) -> int:
    from autoshorts.analytics.rollups import stats_rollup
    from autoshorts.cli.catalog import _Pool
    from autoshorts.db.pool import connection

    if not 1 <= args.days <= 400:
        raise CliError("--days: от 1 до 400", exit_code=2)
    with _Pool(args.database_url), connection() as conn:
        res = stats_rollup(conn, days=args.days)
    print(
        f"ролл-ап {res['date_from']}…{res['date_to']} ({res['timezone']}): публикаций {res['publications']}, "
        f"строк просмотров {res['views_rows']}, трафика {res['traffic_rows']}, "
        f"прямого трафика {res['unattributed_rows']}"
    )
    return 0


# ---------------------------------------------------------------- seed-demo


def database_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def check_target(url: str, *, i_know: bool) -> str:
    """Имя базы или отказ: legacy и стенд — только с явным `--i-know`."""
    name = database_name(url)
    if not name:
        raise CliError("в --database-url нет имени базы", exit_code=2)
    if name in PROTECTED_DATABASES and not i_know:
        raise CliError(
            f"база {name} защищена (legacy или стенд): демо-данные туда только с --i-know", exit_code=2
        )
    return name


def cmd_seed_demo(args: argparse.Namespace) -> int:
    import psycopg
    from psycopg.rows import dict_row

    from autoshorts.analytics.rollups import stats_rollup
    from autoshorts.db.migrate import assert_schema_current
    from autoshorts.services.settings import load_runtime_settings

    name = check_target(args.database_url, i_know=args.i_know)
    if not 1 <= args.days <= 120 or not 1 <= args.per_day <= 50:
        raise CliError("--days: 1…120, --per-day: 1…50", exit_code=2)
    try:
        assert_schema_current(conninfo=args.database_url)
    except Exception as exc:  # noqa: BLE001 - MigrationsPending, OperationalError: одной строкой
        message = getattr(exc, "message", None) or str(exc).strip().splitlines()[0]
        raise CliError(f"БД {name} не готова: {message}") from exc
    with psycopg.connect(args.database_url, row_factory=dict_row) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        with conn.transaction():
            exists = conn.execute("SELECT 1 FROM niches WHERE id = %s", (DEMO_MARKER_NICHE,)).fetchone()
            if exists:
                print(f"{name}: демо-данные уже есть (ниша {DEMO_MARKER_NICHE}) — только пересчёт ролл-апа")
                stats = None
            else:
                stats = DemoSeeder(conn, random.Random(args.seed), days=args.days, per_day=args.per_day).run()
        with conn.transaction():
            tz = load_runtime_settings(conn).business_timezone
            res = stats_rollup(conn, days=args.days + 3, tz=tz)
    if stats:
        print(
            f"{name}: роликов {stats['jobs']}, публикаций {stats['publications']}, "
            f"снимков {stats['snapshots']}, "
            f"кликов {stats['clicks']}, событий {stats['conversions']}, вызовов с расходом {stats['events']}"
        )
    print(f"ролл-ап {res['date_from']}…{res['date_to']}: строк просмотров {res['views_rows']}")
    return 0


class DemoSeeder:
    """Генератор демо-мира. Всё время — относительно `now()` БД; всё в одной транзакции вызывающего."""

    CHANNELS = (
        # id, платформа, ниша, продукт, доля роликов, медиана просмотров
        ("demo_taro_yt", "youtube", "demo_taro", "umka", 0.5, 4200),
        ("demo_money_yt", "youtube", "demo_money", "demo_vpn", 0.3, 2600),
        ("demo_taro_vk", "vk", "demo_taro", "umka", 0.2, 900),
    )

    def __init__(self, conn: Any, rng: random.Random, *, days: int, per_day: int) -> None:
        self.conn = conn
        self.rng = rng
        self.days = days
        self.per_day = per_day
        self.now: datetime = conn.execute("SELECT now() AS t").fetchone()["t"]
        self.counters = {
            "jobs": 0,
            "publications": 0,
            "snapshots": 0,
            "clicks": 0,
            "conversions": 0,
            "events": 0,
        }
        self.clicks: list[tuple[Any, ...]] = []
        self.convs: list[tuple[Any, ...]] = []
        self.events: list[tuple[Any, ...]] = []
        self.snaps: list[tuple[Any, ...]] = []
        self.codes: set[str] = set()
        self.event_seq = 0
        self.user_seq = 0

    # ------------------------------------------------------------ справочники

    def catalog(self) -> None:
        c = self.conn
        c.execute(
            """INSERT INTO products (id, name, kind, status, landing_url, telegram_bot_username,
                                     redirect_mode)
               VALUES ('umka', 'Умка', 'web_service', 'active', 'https://umka.chat/', 'umka_bot',
                       'landing_split'),
                      ('demo_vpn', 'VPN-бот (демо)', 'telegram_bot', 'active', NULL, 'demo_vpn_bot',
                       'landing_split')
               ON CONFLICT (id) DO NOTHING"""
        )
        c.execute(
            """INSERT INTO niches (id, label, status, current_version)
               VALUES ('demo_taro', 'Таро (демо)', 'active', 1),
                      ('demo_money', 'Деньги (демо)', 'active', 1)"""
        )
        c.execute("INSERT INTO clusters (id, title) VALUES ('demo', 'Демо-куст') ON CONFLICT (id) DO NOTHING")
        for ch_id, platform, niche, product, _, _ in self.CHANNELS:
            ref = "UCdemo" + ch_id.replace("_", "")[:18] if platform == "youtube" else "-100100"
            c.execute(
                """INSERT INTO channels (id, niche_id, platform, cluster, title, status, default_product_id,
                                         platform_channel_ref, url)
                   VALUES (%s, %s, %s, 'demo', %s, 'active', %s, %s, %s)""",
                (
                    ch_id,
                    niche,
                    platform,
                    {
                        "demo_taro_yt": "Таро · демо",
                        "demo_money_yt": "Деньги · демо",
                        "demo_taro_vk": "Таро · VK",
                    }[ch_id],
                    product,
                    ref,
                    f"https://www.youtube.com/@{ch_id}"
                    if platform == "youtube"
                    else "https://vk.com/club100100",
                ),
            )
        self.campaigns: dict[str, dict[str, Any]] = {}
        for product, name, variants in (
            ("umka", "Осень · демо", (("A", "Только домен", 1.0), ("B", "Домен и код в кадре", 1.6))),
            ("demo_vpn", "VPN · демо", (("A", "Скидка", 1.0), ("B", "Пробный период", 1.05))),
        ):
            cid = c.execute(
                "INSERT INTO campaigns (product_id, name, status) VALUES (%s, %s, 'active') RETURNING id",
                (product, name),
            ).fetchone()["id"]
            vs = []
            for code, vname, lift in variants:
                vid = c.execute(
                    "INSERT INTO banner_variants (campaign_id, code, name, status, weight) "
                    "VALUES (%s, %s, %s, 'active', 50) RETURNING id",
                    (cid, code, vname),
                ).fetchone()["id"]
                vs.append((int(vid), lift))
            self.campaigns[product] = {"id": int(cid), "variants": vs}
        self.sources = {}
        for niche, title in (
            ("demo_taro", "Лекция о картах (демо)"),
            ("demo_money", "Подкаст о деньгах (демо)"),
        ):
            sid = c.execute(
                "INSERT INTO sources (title, origin, origin_url, status, niche_id) "
                "VALUES (%s, 'url', %s, 'ready', %s) RETURNING id",
                (title, f"https://www.youtube.com/watch?v={self.yt_id()}", niche),
            ).fetchone()["id"]
            self.sources[niche] = int(sid)
            # ASR локально бесплатен, отбор моментов LLM — платный; размазывается по клипам источника
            self.event(None, "asr", "local_asr", 0.0, self.now - timedelta(days=self.days), source_id=sid)
            self.event(None, "select", "umka_llm", 0.03, self.now - timedelta(days=self.days), source_id=sid)
        self.brief = int(
            c.execute(
                "INSERT INTO trend_briefs (external_video_id, niche_id, status) "
                "VALUES (%s, 'demo_taro', 'accepted') RETURNING id",
                (self.yt_id(),),
            ).fetchone()["id"]
        )
        idle = c.execute(
            "INSERT INTO trend_briefs (external_video_id, niche_id, status) "
            "VALUES (%s, 'demo_money', 'new') RETURNING id",
            (self.yt_id(),),
        ).fetchone()["id"]
        self.event(
            None,
            "brief_analyze",
            "umka_llm",
            0.012,
            self.now - timedelta(days=self.days - 1),
            brief_id=self.brief,
        )
        self.event(None, "brief_analyze", "umka_llm", 0.01, self.now - timedelta(days=3), brief_id=int(idle))
        self.channel_links = {}
        for ch_id, platform, _, product, _, _ in self.CHANNELS:
            code = f"ch_{ch_id}"
            lid = c.execute(
                "INSERT INTO tracking_links (code, product_id, level, channel_id, platform, label) "
                "VALUES (%s, %s, 'channel', %s, %s, 'Ссылка в шапке канала') RETURNING id",
                (code, product, ch_id, platform),
            ).fetchone()["id"]
            self.channel_links[ch_id] = {
                "id": int(lid),
                "code": code,
                "product": product,
                "platform": platform,
            }

    # ------------------------------------------------------------ помощники

    def yt_id(self) -> str:
        return "".join(self.rng.choice(YT_ID_ALPHABET) for _ in range(11))

    def package_code(self) -> str:
        while True:
            code = "".join(self.rng.choice(PACKAGE_ALPHABET) for _ in range(5))
            if code not in self.codes:
                self.codes.add(code)
                return code

    def user(self) -> str:
        self.user_seq += 1
        return hashlib.sha256(f"demo-user-{self.user_seq}".encode()).hexdigest()

    def visitor(self) -> str:
        return hashlib.sha256(f"demo-visitor-{self.rng.random()}".encode()).hexdigest()[:32]

    def event(
        self,
        job_id: int | None,
        step: str,
        provider: str,
        cost: float,
        at: datetime,
        *,
        source_id: int | None = None,
        brief_id: int | None = None,
    ) -> None:
        self.events.append((job_id, step, provider, round(cost, 6), at, source_id, brief_id))

    def click(
        self, link: dict[str, Any], at: datetime, route: str, visitor: str, *, bot: bool = False
    ) -> None:
        if at >= self.now:
            return
        self.clicks.append(
            (
                at,
                link["code"],
                link.get("id"),
                route,
                link["product"],
                link.get("publication_id"),
                link.get("job_id"),
                link.get("channel_id"),
                link.get("platform"),
                visitor,
                "Mozilla/5.0 (demo)" if not bot else "Googlebot/2.1",
                bot,
            )
        )

    def conv(
        self,
        link: dict[str, Any] | None,
        product: str,
        at: datetime,
        event: str,
        user: str | None,
        *,
        via_landing: bool = False,
        amount: int | None = None,
        test: bool = False,
    ) -> None:
        if at >= self.now:
            return
        self.event_seq += 1
        link = link or {}
        tag = (link["code"] + ("_l" if via_landing else "")) if link.get("code") else None
        self.convs.append(
            (
                product,
                f"demo-{self.event_seq}",
                event,
                at,
                tag,
                via_landing,
                link.get("id"),
                link.get("publication_id"),
                link.get("job_id"),
                link.get("channel_id"),
                link.get("platform"),
                user,
                amount,
                "RUB" if amount is not None else None,
                test,
            )
        )

    # ------------------------------------------------------------ ролики

    def pick_channel(self) -> tuple[str, str, str, str, float, int]:
        r = self.rng.random()
        acc = 0.0
        for ch in self.CHANNELS:
            acc += ch[4]
            if r <= acc:
                return ch
        return self.CHANNELS[-1]

    def status_for(self, age_h: float) -> str:
        if age_h < 3:
            return self.rng.choice(["processing", "queued"])
        if age_h < 8:
            return self.rng.choice(["pending_review", "qc_failed", "approved"])
        r = self.rng.random()
        for status, share in (
            ("published", 0.72),
            ("rejected", 0.14),
            ("failed", 0.05),
            ("approved", 0.04),
            ("pending_review", 0.03),
        ):
            if r < share:
                return status
            r -= share
        return "cancelled"

    def job(self, created: datetime) -> None:
        ch_id, platform, niche, product, _, median_views = self.pick_channel()
        kind = "clip" if self.rng.random() < 0.3 else "generate"
        camp = self.campaigns[product]
        variant_id, lift = self.rng.choice(camp["variants"])
        status = self.status_for((self.now - created).total_seconds() / 3600.0)
        brief = (
            self.brief if kind == "generate" and niche == "demo_taro" and self.rng.random() < 0.2 else None
        )
        uid = "demo" + hashlib.sha1(f"{created.isoformat()}{self.rng.random()}".encode()).hexdigest()[:10]
        row = self.conn.execute(
            """INSERT INTO jobs (video_uid, channel_id, niche_id, template_version, status, kind, product_id,
                                 campaign_id, banner_variant_id, trend_brief_id, source_id, topic,
                                 duration_sec,
                                 reject_reason, created_at, updated_at)
               VALUES (%s, %s, %s, 'v1', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                uid,
                ch_id,
                niche,
                status,
                kind,
                product,
                camp["id"],
                variant_id,
                brief,
                self.sources[niche] if kind == "clip" else None,
                self.rng.choice(TOPICS),
                round(self.rng.uniform(22, 55), 1),
                self.rng.choice(REJECT_REASONS) if status == "rejected" else None,
                created,
                created + timedelta(hours=2),
            ),
        ).fetchone()
        job_id = int(row["id"])
        self.counters["jobs"] += 1
        # расход: брак и сбои тоже стоят денег
        spend = 0.0
        if status not in ("queued",):
            steps = (
                [("script", "umka_llm", 0.0025), ("voice", "edge_tts", 0.0), ("visuals", "fal", 0.04)]
                if kind == "generate"
                else [("author_script", "umka_llm", 0.003), ("author_voice", "edge_tts", 0.0)]
            )
            if status == "failed":
                steps = steps[:2]
            if self.rng.random() < 0.15:  # перегенерация сцены
                steps.append(("visuals", "fal", 0.03))
            for i, (step, provider, cost) in enumerate(steps):
                c = cost * self.rng.uniform(0.7, 1.4)
                spend += c
                self.event(job_id, step, provider, c, created + timedelta(minutes=5 + 7 * i))
        self.conn.execute("UPDATE jobs SET cost_usd = %s WHERE id = %s", (round(spend, 6), job_id))
        if status == "published":
            self.publication(
                job_id,
                uid,
                ch_id,
                platform,
                product,
                camp["id"],
                variant_id,
                kind,
                lift,
                created,
                median_views,
            )

    def curve(self, final: float, tau: float, hours: float) -> float:
        return final * (1.0 - math.exp(-max(hours, 0.0) / tau))

    def publication(
        self,
        job_id: int,
        uid: str,
        ch_id: str,
        platform: str,
        product: str,
        campaign_id: int,
        variant_id: int,
        kind: str,
        lift: float,
        created: datetime,
        median_views: int,
    ) -> None:
        published = created + timedelta(hours=self.rng.uniform(4, 10))
        if published >= self.now - timedelta(minutes=30):
            self.conn.execute("UPDATE jobs SET status = 'approved' WHERE id = %s", (job_id,))
            return
        if platform == "youtube":
            vid = self.yt_id()
            url = f"https://www.youtube.com/shorts/{vid}"
            alias = f"yt_{vid}"
            collector = "yt_data"
        else:
            vid = f"-100100_{self.rng.randint(456000000, 456999999)}"
            url = f"https://vk.com/clip{vid}"
            alias = f"vk_{vid}"
            collector = "ytdlp"
        final = median_views * math.exp(self.rng.gauss(0.0, 0.8)) * (0.85 if kind == "clip" else 1.0)
        tau = self.rng.uniform(18, 60)
        age_h = (self.now - published).total_seconds() / 3600.0
        status = "removed_by_platform" if self.rng.random() < 0.02 else "live"
        manual_due = self.now - timedelta(hours=2) if platform == "vk" and self.rng.random() < 0.08 else None
        pub_id = int(
            self.conn.execute(
                """INSERT INTO publications (job_id, channel_id, platform, platform_video_id,
                                             master_file_path,
                                             title, published_at, url, canonical_url, platform_kind, status,
                                             verify_status, product_id, campaign_id, banner_variant_id,
                                             metrics_collector, manual_due_at, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'ok', %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    job_id,
                    ch_id,
                    platform,
                    vid,
                    f"masters/demo/{uid}.mp4",
                    self.rng.choice(TOPICS),
                    published,
                    url,
                    url,
                    "short" if platform == "youtube" else "clip",
                    status,
                    product,
                    campaign_id,
                    variant_id,
                    collector,
                    manual_due,
                    published + timedelta(minutes=20),
                ),
            ).fetchone()["id"]
        )
        self.counters["publications"] += 1
        # снимки по лестнице опроса (VK — реже и частично руками)
        ladder = LADDER_HOURS + list(range(192, 721, 24)) + list(range(888, 2161, 168))
        if platform == "vk":
            ladder = [h for h in ladder if h >= 24 and h % 24 == 0]
        last_views = None
        last_at = None
        for h in ladder:
            at = published + timedelta(hours=h, minutes=self.rng.uniform(0, 20))
            if at >= self.now:
                break
            views = int(self.curve(final, tau, h) * self.rng.uniform(0.97, 1.03))
            if last_views is not None:
                views = max(views, last_views)
            source = "manual" if platform == "vk" and self.rng.random() < 0.25 else collector
            self.snaps.append(
                (pub_id, at, h, views, int(views * self.rng.uniform(0.03, 0.06)), int(views * 0.003), source)
            )
            last_views, last_at = views, at
            self.counters["snapshots"] += 1
        next_poll = None
        if status == "live":
            future = [
                published + timedelta(hours=h) for h in ladder if published + timedelta(hours=h) > self.now
            ]
            next_poll = future[0] if future else None

        def at_age(hours: float) -> int | None:
            return int(self.curve(final, tau, hours)) if age_h >= hours and last_views is not None else None

        self.conn.execute(
            """UPDATE publications SET latest_views = %s, last_polled_at = %s, next_poll_at = %s,
                      views_24h = %s, views_72h = %s, views_7d = %s, views_14d = %s, views_30d = %s
               WHERE id = %s""",
            (
                last_views,
                last_at,
                next_poll,
                at_age(24),
                at_age(72),
                at_age(168),
                at_age(336),
                at_age(720),
                pub_id,
            ),
        )
        # ссылки: пакетная и алиас площадки
        links = []
        for level, code in (("package", self.package_code()), ("publication", alias)):
            lid = self.conn.execute(
                """INSERT INTO tracking_links (code, product_id, level, job_id, publication_id, channel_id,
                                               campaign_id, banner_variant_id, platform)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (code, product, level, job_id, pub_id, ch_id, campaign_id, variant_id, platform),
            ).fetchone()["id"]
            links.append(
                {
                    "id": int(lid),
                    "code": code,
                    "product": product,
                    "publication_id": pub_id,
                    "job_id": job_id,
                    "channel_id": ch_id,
                    "platform": platform,
                }
            )
        self.traffic(links, product, published, final, tau, lift * (0.8 if kind == "clip" else 1.0))

    def traffic(
        self,
        links: list[dict[str, Any]],
        product: str,
        published: datetime,
        final: float,
        tau: float,
        lift: float,
    ) -> None:
        rng = self.rng
        views_now = self.curve(final, tau, (self.now - published).total_seconds() / 3600.0)
        ctr = (2.4 if product == "umka" else 3.2) * lift  # посетители на 1000 просмотров
        n = int(views_now * ctr / 1000.0 + rng.random())

        def when() -> datetime:
            # время посещения — пропорционально приросту просмотров
            frac = min(views_now / final, 0.999) if final else 0.0
            u = rng.random() * frac
            return published + timedelta(hours=-tau * math.log(1.0 - u), minutes=rng.uniform(0, 30))

        for _ in range(n):
            link = rng.choice(links)
            at = when()
            v = self.visitor()
            self.click(link, at, "landing", v)
            if rng.random() < 0.2:
                self.click(link, at + timedelta(seconds=40), "landing", v)
            r = rng.random()
            if r < 0.55:
                self.click(link, at + timedelta(seconds=20), "to_telegram", v)
                if rng.random() < 0.7:
                    self.funnel(link, product, at + timedelta(minutes=1), via_landing=True)
            elif r < 0.75:
                self.click(link, at + timedelta(seconds=25), "to_site", v)
                if rng.random() < 0.3:
                    self.conv(link, product, at + timedelta(minutes=4), "signup", self.user())
            if rng.random() < 0.05:
                self.click(link, at + timedelta(seconds=3), "landing", self.visitor(), bot=True)
        # старты напрямую: код из описания набран руками (без _l)
        for _ in range(int(n * 0.12 + rng.random())):
            self.funnel(links[0], product, when(), via_landing=False)

    def funnel(self, link: dict[str, Any], product: str, at: datetime, *, via_landing: bool) -> None:
        rng = self.rng
        u = self.user()
        self.conv(link, product, at, "bot_start", u, via_landing=via_landing)
        if rng.random() < 0.04:
            self.conv(link, product, at + timedelta(hours=5), "bot_start", u, via_landing=via_landing)
        if product == "umka" and rng.random() < 0.4:
            self.conv(link, product, at + timedelta(minutes=3), "signup", u, via_landing=via_landing)
        if rng.random() < 0.07:
            amount = rng.choice([29900, 49900, 99000])
            self.conv(
                link, product, at + timedelta(hours=rng.uniform(1, 48)), "first_payment", u, amount=amount
            )
            if rng.random() < 0.3:
                self.conv(link, product, at + timedelta(days=rng.uniform(5, 20)), "payment", u, amount=amount)
            if rng.random() < 0.05:
                self.conv(link, product, at + timedelta(days=2), "refund", u, amount=amount)

    # ------------------------------------------------------------ фоновый трафик

    def background(self) -> None:
        rng = self.rng
        for d in range(self.days, -1, -1):
            day = self.now - timedelta(days=d)
            for ch_id, link in self.channel_links.items():
                for _ in range(rng.randint(0, 3)):
                    at = day - timedelta(hours=rng.uniform(0, 23))
                    v = self.visitor()
                    info = {**link, "channel_id": ch_id}
                    self.click(info, at, "landing", v)
                    if rng.random() < 0.5:
                        self.click(info, at + timedelta(seconds=15), "to_telegram", v)
                        self.funnel(info, link["product"], at + timedelta(minutes=1), via_landing=True)
            # неизвестный код и органика продукта без тега — уровень direct
            for _ in range(rng.randint(0, 2)):
                self.click(
                    {"code": f"zz{rng.randint(1000, 9999)}", "product": "umka"},
                    day - timedelta(hours=rng.uniform(0, 23)),
                    "unknown_code",
                    self.visitor(),
                )
            for _ in range(rng.randint(2, 6)):
                self.conv(None, "umka", day - timedelta(hours=rng.uniform(0, 23)), "bot_start", self.user())
            if rng.random() < 0.2:  # тестовое событие из вкладки «Вебхук» — в статистику не идёт
                self.conv(None, "umka", day - timedelta(hours=1), "bot_start", self.user(), test=True)

    # ------------------------------------------------------------ запись

    def flush(self) -> None:
        with self.conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO metrics_snapshots (publication_id, captured_at, age_hours, views, likes,
                                                  comments,
                                                  source)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                self.snaps,
            )
            cur.executemany(
                """INSERT INTO clicks (occurred_at, code, tracking_link_id, route, product_id, publication_id,
                                       job_id, channel_id, platform, visitor_key, user_agent, is_bot)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                self.clicks,
            )
            cur.executemany(
                """INSERT INTO conversions (product_id, external_event_id, event_type, occurred_at, tag,
                                            via_landing, tracking_link_id, publication_id, job_id, channel_id,
                                            platform, user_ref_hash, amount_minor, currency, is_test)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                self.convs,
            )
            cur.executemany(
                """INSERT INTO generation_events (job_id, step_name, provider, cost_usd, created_at,
                                                  source_id,
                                                  trend_brief_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                self.events,
            )
        self.counters["clicks"] = len(self.clicks)
        self.counters["conversions"] = len(self.convs)
        self.counters["events"] = len(self.events)

    def run(self) -> dict[str, int]:
        self.catalog()
        for d in range(self.days, -1, -1):
            for _ in range(self.per_day):
                created = self.now - timedelta(days=d, hours=self.rng.uniform(0, 23))
                if created < self.now:
                    self.job(created)
        self.background()
        self.flush()
        return dict(self.counters)
