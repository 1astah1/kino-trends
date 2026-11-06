"""`autoshorts-admin` — административный CLI (docs/ARCHITECTURE.md §3.1, §10.1 п. 2).

Подкоманды подхватываются автоматически: каждый модуль `autoshorts/cli/<area>.py`
(кроме начинающихся с `_`) с функцией

    def register(subparsers: argparse._SubParsersAction) -> None:
        p = subparsers.add_parser("render-once", help="...")
        p.add_argument(...)
        p.set_defaults(func=handler)      # handler(args: argparse.Namespace) -> int | None

Добавление области не трогает этот файл. Модуль, который не импортируется (например, нет
опциональной зависимости), не ломает остальные команды: он пропускается с предупреждением.

Коды выхода: 0 — успех, 1 — ошибка/отказ, 2 — неверный вызов, 130 — прерван Ctrl+C.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import pkgutil
import sys
from collections.abc import Sequence
from types import ModuleType

log = logging.getLogger("autoshorts.cli")

PROG = "autoshorts-admin"


class CliError(Exception):
    """Ожидаемая ошибка команды: печатается одной строкой без трейсбека."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def discover_command_modules() -> tuple[list[ModuleType], list[tuple[str, BaseException]]]:
    """Модули `autoshorts.cli.*` с функцией `register`; вторым — те, что не импортировались."""
    pkg = sys.modules[__name__]
    modules: list[ModuleType] = []
    broken: list[tuple[str, BaseException]] = []
    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda i: i.name):
        if info.name.startswith("_"):
            continue
        name = f"{__name__}.{info.name}"
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - чужая подкоманда не должна ронять весь CLI
            broken.append((name, exc))
            continue
        if callable(getattr(module, "register", None)):
            modules.append(module)
    return modules, broken


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Администрирование Auto Shorts: миграции, пользователи, проверка окружения.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        type=str.upper,
        help="уровень логов (по умолчанию LOG_LEVEL или INFO)",
    )
    parser.add_argument(
        "--log-format",
        default="text",
        choices=["text", "json"],
        help="формат логов в stderr (по умолчанию text)",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    modules, broken = discover_command_modules()
    for module in modules:
        try:
            module.register(sub)
        except (argparse.ArgumentError, ValueError) as exc:
            broken.append((module.__name__, exc))
    parser.set_defaults(_broken_modules=broken)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа console script `autoshorts-admin`."""
    parser = build_parser()
    args = parser.parse_args(argv)

    from autoshorts.core.logging import configure_logging

    configure_logging(level=args.log_level or None, fmt=args.log_format)
    for name, exc in getattr(args, "_broken_modules", []):
        log.warning("подкоманды модуля %s недоступны: %s", name, exc)

    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 2
    try:
        rc = func(args)
    except CliError as exc:
        print(f"{PROG}: ошибка: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print(f"{PROG}: прервано", file=sys.stderr)
        return 130
    return int(rc or 0)
