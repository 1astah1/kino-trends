"""Подкоманды каталога: импорт ниш и шаблона баннера из YAML (docs/ARCHITECTURE.md §10.2 M2).

    autoshorts-admin import-niches config/niches/*.yaml [--mode auto|create|replace]
    autoshorts-admin import-banner config/banner.yaml --name default [--offer-text "…"]
    autoshorts-admin export-niche taro [> taro.yaml]

* Ниша: новая — версия 1 и статус active (заготовка `needs_import` из миграции 0003 тоже);
  существующая — новая версия, только если конфиг изменился (`--mode create` — отказ).
* Баннер: форма config/banner.yaml → пресет варианта (`BannerVariantDraft`) в `banner_templates`
  по имени; повторный импорт того же файла ничего не меняет.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from autoshorts.cli import CliError


def register(subparsers: argparse._SubParsersAction) -> None:  # noqa: SLF001 - тип argparse
    p = subparsers.add_parser(
        "import-niches",
        help="импортировать ниши из YAML (config/niches/*.yaml)",
        description="Импорт конфигов ниш: новая ниша — версия 1, изменённая — новая версия.",
    )
    p.add_argument("files", nargs="+", type=Path, help="YAML-файлы ниш")
    p.add_argument(
        "--mode",
        choices=("auto", "create", "replace"),
        default="auto",
        help="auto — создать или обновить (по умолчанию); create — только новые; replace — новая версия",
    )
    p.add_argument("--database-url", default=None, help="по умолчанию DATABASE_URL")
    p.set_defaults(func=cmd_import_niches)

    p = subparsers.add_parser(
        "import-banner",
        help="импортировать шаблон баннера из config/banner.yaml",
        description="Форма config/banner.yaml → шаблон-пресет варианта баннера (по имени).",
    )
    p.add_argument("file", type=Path, help="YAML формы config/banner.yaml")
    p.add_argument("--name", required=True, help="имя шаблона (например, default)")
    p.add_argument("--offer-text", default=None, help="текст запасной плашки (если в YAML его нет)")
    p.add_argument("--database-url", default=None, help="по умолчанию DATABASE_URL")
    p.set_defaults(func=cmd_import_banner)

    p = subparsers.add_parser("export-niche", help="выгрузить текущий конфиг ниши в YAML (stdout)")
    p.add_argument("niche_id", help="id ниши (латиница)")
    p.add_argument("--database-url", default=None, help="по умолчанию DATABASE_URL")
    p.set_defaults(func=cmd_export_niche)


# ---------------------------------------------------------------- общее


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
            init_pool(conninfo, min_size=1, max_size=2, application_name="autoshorts-admin")
        except PoolTimeout as exc:
            raise CliError("не подключиться к БД") from exc
        except Exception as exc:  # noqa: BLE001 - MigrationsPending, OperationalError: одной строкой
            message = getattr(exc, "message", None) or str(exc).strip().splitlines()[0]
            raise CliError(f"БД не готова: {message}") from exc
        return self

    def __exit__(self, *exc: object) -> None:
        from autoshorts.db.pool import close_pool

        close_pool()


def _actor() -> Any:
    from autoshorts.services.audit import SYSTEM

    return SYSTEM


def _app_error_text(exc: Any) -> str:
    details = getattr(exc, "details", {}) or {}
    issues = details.get("config_errors") or details.get("errors") or []
    parts = []
    for item in issues[:8]:
        path = item.get("path") or ".".join(str(p) for p in item.get("loc", [])[1:])
        parts.append(f"{path}: {item.get('msg')}")
    return exc.message + (" — " + "; ".join(parts) if parts else "")


# ---------------------------------------------------------------- команды


def cmd_import_niches(args: argparse.Namespace) -> int:
    from autoshorts.core.errors import AppError
    from autoshorts.domain.niche_schema import load_yaml
    from autoshorts.services import niches as niches_service

    failed = 0
    with _Pool(args.database_url):
        for path in args.files:
            try:
                data = load_yaml(path.read_text(encoding="utf-8"))
                row, action = niches_service.import_config(data, mode=args.mode, actor=_actor())
            except (OSError, ValueError) as exc:
                print(f"{path}: ошибка: {exc}", file=sys.stderr)
                failed += 1
                continue
            except AppError as exc:
                print(f"{path}: ошибка: {_app_error_text(exc)}", file=sys.stderr)
                failed += 1
                continue
            print(f"{row['id']}: {action} (версия {row['current_version']}, статус {row['status']})")
    return 1 if failed else 0


def cmd_import_banner(args: argparse.Namespace) -> int:
    from autoshorts.core.errors import AppError
    from autoshorts.services import banners as banners_service

    try:
        text = args.file.read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError(f"{args.file}: {exc}") from exc
    with _Pool(args.database_url):
        try:
            tpl, action = banners_service.upsert_template_from_yaml(
                text, name=args.name, actor=_actor(), offer_text=args.offer_text
            )
        except AppError as exc:
            raise CliError(f"{args.file}: {_app_error_text(exc)}") from exc
    layers = ", ".join(
        f"{la['id']}{'' if la.get('enabled', True) else ' (выкл.)'}" for la in tpl["layout"]["layers"]
    )
    print(f"шаблон «{tpl['name']}» (id {tpl['id']}, версия {tpl['version']}): {action}; слои: {layers}")
    return 0


def cmd_export_niche(args: argparse.Namespace) -> int:
    from autoshorts.core.errors import AppError
    from autoshorts.services import niches as niches_service

    with _Pool(args.database_url):
        try:
            sys.stdout.write(niches_service.export_yaml(args.niche_id))
        except AppError as exc:
            raise CliError(exc.message) from exc
    return 0
