"""Подкоманды коллекторов метрик (docs/ARCHITECTURE.md §6.5, §10.2 M4). Владелец — M4-A.

    autoshorts-admin collectors-once [--publication 12 --publication 15] [--platform youtube|vk]
                                     [--thumbnails] [--json]
    autoshorts-admin channel-stats-once [--json]
    autoshorts-admin collectors-status [--json]

`collectors-once` — один прогон коллекторов прямо в процессе, без очереди: без `--publication` —
созревшие публикации (как задача `metrics_poll`), с ним — указанные, вне расписания. Печатает по
строке на публикацию: просмотры до и после, источник, статус, ошибка. Код выхода 1 — если указанная
публикация не опрошена (нет такой, удалена, нет ID площадки) или опрос закончился ошибкой.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from autoshorts.cli import CliError

OUTCOME_LABELS = {
    "snapshot": "снимок",
    "no_data": "нет счётчиков",
    "removed": "снят площадкой",
    "restored": "снова доступен",
    "missing": "не виден по ключу",
    "deferred": "отложен (квота)",
    "manual_due": "ждёт ручного ввода",
    "failed": "ошибка",
    "released": "не опрошен (отмена)",
    "error": "сбой обработки",
}


def register(subparsers: argparse._SubParsersAction) -> None:  # noqa: SLF001 - тип argparse
    p = subparsers.add_parser(
        "collectors-once",
        help="один прогон коллекторов метрик (созревшие публикации или указанные)",
        description="Опросить метрики публикаций сейчас, без очереди задач.",
    )
    p.add_argument(
        "--publication",
        type=int,
        action="append",
        default=None,
        metavar="ID",
        help="id публикации (можно повторять)",
    )
    p.add_argument("--platform", choices=("youtube", "vk"), default=None, help="только эта площадка")
    p.add_argument("--thumbnails", action="store_true", help="скачать обложки в MEDIA_ROOT/thumbs")
    p.add_argument("--json", action="store_true", help="итог в JSON")
    p.add_argument("--database-url", default=None, help="по умолчанию DATABASE_URL")
    p.set_defaults(func=cmd_collectors_once)

    p = subparsers.add_parser("channel-stats-once", help="снимки каналов сейчас (как задача channel_stats)")
    p.add_argument("--json", action="store_true", help="итог в JSON")
    p.add_argument("--database-url", default=None, help="по умолчанию DATABASE_URL")
    p.set_defaults(func=cmd_channel_stats_once)

    p = subparsers.add_parser("collectors-status", help="состояние коллекторов и квота YouTube на сегодня")
    p.add_argument("--json", action="store_true", help="итог в JSON")
    p.add_argument("--database-url", default=None, help="по умолчанию DATABASE_URL")
    p.set_defaults(func=cmd_collectors_status)


class _Pool:
    """Пул процесса на время команды (сервисы берут соединения через db.pool.connection)."""

    def __init__(self, database_url: str | None) -> None:
        self.database_url = database_url

    def __enter__(self) -> _Pool:
        from psycopg_pool import PoolTimeout

        from autoshorts.core.config import reload_env
        from autoshorts.db.migrate import assert_schema_current
        from autoshorts.db.pool import init_pool

        conninfo = self.database_url or reload_env().database_url
        try:
            assert_schema_current(conninfo=conninfo)
            init_pool(conninfo, min_size=1, max_size=3, application_name="autoshorts-admin")
        except PoolTimeout as exc:
            raise CliError("не подключиться к БД") from exc
        except Exception as exc:  # noqa: BLE001 - MigrationsPending, OperationalError: одной строкой
            message = getattr(exc, "message", None) or str(exc).strip().splitlines()[0]
            raise CliError(f"БД не готова: {message}") from exc
        return self

    def __exit__(self, *exc: object) -> None:
        from autoshorts.db.pool import close_pool

        close_pool()


def _dump(data: Any) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n")


def cmd_collectors_once(args: argparse.Namespace) -> int:
    from autoshorts.services import metrics as metrics_service

    ids = sorted(set(args.publication)) if args.publication else None
    with _Pool(args.database_url):
        report = metrics_service.poll_metrics(
            publication_ids=ids,
            platform=args.platform,
            deps=metrics_service.PollDeps(thumbnails=bool(args.thumbnails)),
        )
    polled = {o.publication_id for o in report.outcomes}
    missing = [i for i in (ids or []) if i not in polled]
    bad = [o for o in report.outcomes if o.outcome in ("failed", "error", "released")]
    if args.json:
        _dump({**report.as_dict(max_items=1000), "not_polled": missing})
    else:
        if not report.outcomes and not missing:
            print("опрашивать нечего: созревших публикаций нет")
        for o in report.outcomes:
            label = OUTCOME_LABELS.get(o.outcome, o.outcome)
            before = o.views_before if o.views_before is not None else "—"
            views = f"{before} → {o.views if o.views is not None else '—'}"
            tail = f", {o.error}" if o.error else ""
            print(
                f"публикация {o.publication_id} ({o.platform}): {label}; просмотры {views}; "
                f"источник {o.source or '—'}; статус {o.status or '—'}{tail}"
            )
        for pid in missing:
            print(f"публикация {pid}: не опрошена — нет такой, удалена или без ID площадки", file=sys.stderr)
        for key, state in sorted(report.collectors.items()):
            print(f"коллектор {key}: {state}")
        if report.thumbnails:
            print(f"обложек скачано: {report.thumbnails}")
    return 1 if (missing or (ids and bad)) else 0


def cmd_channel_stats_once(args: argparse.Namespace) -> int:
    from autoshorts.services import metrics as metrics_service

    with _Pool(args.database_url):
        result = metrics_service.collect_channel_stats()
    if args.json:
        _dump(result)
    else:
        yt, vk = result["youtube"], result["vk"]
        print(f"YouTube: снимков {yt['snapshots']}, не найдено {len(yt['missing'])}")
        for item in yt["skipped"]:
            print(f"  пропущено ({item['cluster_id']}): {item['reason']}")
        print(f"VK: снимков {vk['snapshots']}" + (f", пропущено: {vk['skipped']}" if vk["skipped"] else ""))
    return 0


def cmd_collectors_status(args: argparse.Namespace) -> int:
    from autoshorts.db.pool import connection
    from autoshorts.services import metrics as metrics_service

    with _Pool(args.database_url), connection() as conn:
        usage = metrics_service.quota_usage(conn, date_from=None, date_to=None, apis=None, cluster_ids=None)
    if args.json:
        _dump({"today": usage["today"], "reset_at": usage["reset_at"], "collectors": usage["collectors"]})
        return 0
    for item in usage["collectors"]:
        reason = f" ({item['reason']})" if item["reason"] else ""
        key = (
            f", ключ {item['key_secret']}: {'задан' if item['key_set'] else 'нет'}"
            if item["key_secret"]
            else ""
        )
        print(
            f"{item['collector']}:{item['cluster_id']} — {item['state']}{reason}{key}; "
            f"публикаций {item['publications_total']}, к опросу {item['publications_due']}"
        )
        if item["message"]:
            print(f"  {item['message']}")
    for q in usage["today"]:
        limit = f" из {q['limit']}" if q["limit"] else ""
        print(
            f"квота {q['api']}:{q['cluster_id']} на {q['quota_day']}: {q['units']}{limit} единиц,"
            f" вызовов {q['calls']}"
        )
    print(f"сброс квоты YouTube: {usage['reset_at']:%Y-%m-%d %H:%M} UTC")
    return 0
