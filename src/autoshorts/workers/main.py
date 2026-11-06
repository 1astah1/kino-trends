"""Точка входа воркера: `autoshorts-worker --class heavy|light [--concurrency N]` (§2.2, §8.1).

Порядок старта: логирование → env → проверка миграций (`MigrationsPending` — выход с кодом 3)
→ реестр обработчиков (`tasks.registry.load_all`, сломанный модуль роняет старт) → пул БД →
цикл `Runner`. SIGTERM/SIGINT — мягкая остановка (§ `workers/runner.py`).

Коды выхода: 0 — штатная остановка, 1 — ошибка, 2 — неверные аргументы, 3 — миграции не применены.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

log = logging.getLogger("autoshorts.worker")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_MIGRATIONS_PENDING = 3

DEFAULT_LIGHT_CONCURRENCY = 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="autoshorts-worker", description="Воркер очереди задач Auto Shorts")
    p.add_argument("--class", dest="resource_class", choices=("heavy", "light"), required=True)
    p.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help=f"параллельных задач (light, по умолчанию {DEFAULT_LIGHT_CONCURRENCY}; heavy — всегда 1)",
    )
    p.add_argument("--database-url", default=None, help="по умолчанию DATABASE_URL")
    p.add_argument(
        "--import",
        dest="extra_modules",
        action="append",
        default=[],
        metavar="MODULE",
        help="дополнительный модуль с @register_task (можно повторять)",
    )
    p.add_argument("--no-scheduler", action="store_true", help="не запускать планировщик (light)")
    p.add_argument("--no-admission", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--worker-id", default=None, help=argparse.SUPPRESS)
    p.add_argument("--poll-interval", type=float, default=5.0, help=argparse.SUPPRESS)
    p.add_argument("--heartbeat-interval", type=float, default=20.0, help=argparse.SUPPRESS)
    p.add_argument("--progress-interval", type=float, default=2.0, help=argparse.SUPPRESS)
    p.add_argument("--standby-retry", type=float, default=5.0, help=argparse.SUPPRESS)
    p.add_argument("--shutdown-grace", type=float, default=20.0, help=argparse.SUPPRESS)
    p.add_argument("--lock-path", type=Path, default=None, help=argparse.SUPPRESS)
    p.add_argument("--max-tasks", type=int, default=None, help="выйти после N взятых задач")
    p.add_argument("--exit-when-idle", action="store_true", help="выйти, когда очередь класса опустеет")
    p.add_argument("--skip-schema-check", action="store_true", help=argparse.SUPPRESS)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from autoshorts.core.logging import configure_logging

    configure_logging()

    from autoshorts.core.config import get_env
    from autoshorts.core.errors import MigrationsPending

    env = get_env()
    conninfo = args.database_url or env.database_url

    if not args.skip_schema_check:
        from autoshorts.db.migrate import assert_schema_current

        try:
            assert_schema_current(conninfo=conninfo)
        except MigrationsPending as exc:
            log.error("%s", exc.message)
            return EXIT_MIGRATIONS_PENDING

    from autoshorts.tasks.registry import load_all

    load_all(args.extra_modules, strict=True)

    from autoshorts.db.pool import close_pool, init_pool
    from autoshorts.workers.runner import Runner, RunnerConfig

    concurrency = args.concurrency or (DEFAULT_LIGHT_CONCURRENCY if args.resource_class == "light" else 1)
    config = RunnerConfig(
        resource_class=args.resource_class,
        concurrency=concurrency,
        conninfo=conninfo,
        worker_id=args.worker_id,
        poll_interval_sec=args.poll_interval,
        heartbeat_interval_sec=args.heartbeat_interval,
        progress_interval_sec=args.progress_interval,
        standby_retry_sec=args.standby_retry,
        shutdown_grace_sec=args.shutdown_grace,
        heavy_lock_path=args.lock_path,
        admission=not args.no_admission,
        scheduler=False if args.no_scheduler else None,
        max_tasks=args.max_tasks,
        exit_when_idle=args.exit_when_idle,
    )
    init_pool(
        conninfo,
        min_size=1,
        max_size=config.concurrency * 2 + 3,
        application_name=f"autoshorts-worker-{args.resource_class}",
    )
    try:
        runner = Runner(config)
        return runner.run(install_signals=True)
    except Exception:
        log.exception("worker crashed")
        return EXIT_ERROR
    finally:
        close_pool()


if __name__ == "__main__":
    sys.exit(main())
