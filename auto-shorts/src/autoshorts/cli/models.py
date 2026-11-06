"""Подкоманды моделей и источников нарезки (docs/ARCHITECTURE.md §6.3.2, §8.2, §10.2 M5-A).

    autoshorts-admin models-fetch                    # asr + vad: GigaAM v3 e2e CTC int8, Silero VAD
    autoshorts-admin models-fetch --all              # + YuNet (лица для рефрейма)
    autoshorts-admin models-fetch --verify           # только проверить sha256 того, что на диске
    autoshorts-admin source-register sources/cc_shalyopa_cgevent [--niche taro] [--no-select]

* Модели — только из закреплённых ревизий (`providers/asr/models.py`), файл качается в `.part`,
  sha256 считается на лету, переименование — только при совпадении. Рантайм ничего не качает.
  Зеркало: `HF_ENDPOINT=https://…`. Каталог — `MODELS_DIR` (по умолчанию var/models).
* `source-register` регистрирует каталог, уже подготовленный движком (`source.json`, прокси,
  транскрипт), как источник в БД: права берутся из манифеста и проверяются тем же
  `validate_rights`; для стенда это демо CC BY 3.0 `sources/cc_shalyopa_cgevent`.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import time
from pathlib import Path
from typing import Any

from autoshorts.cli import CliError

CHUNK = 1 << 20


def register(subparsers: argparse._SubParsersAction) -> None:  # noqa: SLF001 - тип argparse
    p = subparsers.add_parser(
        "models-fetch",
        help="скачать закреплённые модели ASR/VAD/лиц в MODELS_DIR (с проверкой sha256)",
        description="Модели из закреплённых ревизий: GigaAM v3 e2e CTC int8, Silero VAD, YuNet.",
    )
    p.add_argument("--groups", nargs="+", default=["asr", "vad"], choices=["asr", "vad", "faces"])
    p.add_argument("--all", action="store_true", help="все группы (asr, vad, faces)")
    p.add_argument("--models-dir", type=Path, default=None, help="по умолчанию MODELS_DIR или var/models")
    p.add_argument("--verify", action="store_true", help="ничего не качать, только проверить sha256")
    p.add_argument("--force", action="store_true", help="скачать заново, даже если файл проходит проверку")
    p.set_defaults(func=cmd_models_fetch)

    p = subparsers.add_parser(
        "source-register",
        help="зарегистрировать подготовленный каталог источника (source.json) в БД",
        description="Каталог MEDIA_ROOT/sources/<имя> с source.json движка → источник нарезки.",
    )
    p.add_argument("media_dir", help="каталог относительно MEDIA_ROOT, например sources/cc_shalyopa_cgevent")
    p.add_argument("--title", default=None, help="название (по умолчанию из манифеста)")
    p.add_argument("--niche", default=None, help="id ниши для словаря запретов при отборе")
    p.add_argument("--user", default=None, help="логин владельца (по умолчанию — первый admin)")
    p.add_argument("--no-select", action="store_true", help="не запускать отбор кандидатов")
    p.add_argument("--database-url", default=None, help="по умолчанию DATABASE_URL")
    p.set_defaults(func=cmd_source_register)


# ---------------------------------------------------------------- модели


def _download(mf: Any, dest: Path, *, timeout: float = 60.0, retries: int = 3) -> None:
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    url = mf.resolved_url()
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        h = hashlib.sha256()
        got = 0
        started = time.monotonic()
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=timeout) as resp:
                resp.raise_for_status()
                with open(part, "wb") as fh:
                    last_print = 0.0
                    for block in resp.iter_bytes(CHUNK):
                        fh.write(block)
                        h.update(block)
                        got += len(block)
                        now = time.monotonic()
                        if mf.size > 10 * CHUNK and now - last_print > 5:
                            last_print = now
                            print(f"    {got / 1e6:.1f} / {mf.size / 1e6:.1f} МБ", flush=True)
            digest = h.hexdigest()
            if digest != mf.sha256:
                part.unlink(missing_ok=True)
                raise CliError(f"{mf.rel_path}: sha256 {digest} не совпадает с закреплённым {mf.sha256}")
            os.replace(part, dest)
            speed = got / max(time.monotonic() - started, 1e-6)
            print(f"  ok  {mf.rel_path} ({got / 1e6:.1f} МБ, {speed / 1e6:.1f} МБ/с)")
            return
        except CliError:
            raise
        except Exception as exc:  # noqa: BLE001 - сеть: повтор с паузой
            last = exc
            part.unlink(missing_ok=True)
            print(f"  попытка {attempt}/{retries} не удалась: {type(exc).__name__}: {exc}")
            time.sleep(min(2.0 * attempt, 10.0))
    raise CliError(f"{mf.rel_path}: не скачался ({last})")


def cmd_models_fetch(args: argparse.Namespace) -> int:
    from autoshorts.providers.asr.models import GROUPS, files_for, models_dir, sha256_of

    root = (args.models_dir or models_dir()).resolve()
    groups = list(GROUPS) if args.all else list(dict.fromkeys(args.groups))
    print(f"MODELS_DIR: {root}; группы: {', '.join(groups)}")
    bad: list[str] = []
    for mf in files_for(groups):
        path = root / mf.rel_path
        present = path.is_file() and path.stat().st_size == mf.size
        if present and not args.force:
            ok = sha256_of(path) == mf.sha256
            print(f"  {'ok ' if ok else 'BAD'} {mf.rel_path}")
            if ok:
                continue
            if args.verify:
                bad.append(mf.rel_path)
                continue
        elif args.verify:
            print(f"  нет {mf.rel_path}")
            bad.append(mf.rel_path)
            continue
        print(f"  качаю {mf.rel_path} ({mf.size / 1e6:.1f} МБ)")
        _download(mf, path)
    if bad:
        raise CliError("не хватает или испорчены: " + ", ".join(bad))
    return 0


# ---------------------------------------------------------------- регистрация источника


def _init_pool(database_url: str | None) -> None:
    from psycopg_pool import PoolTimeout

    from autoshorts.core.config import reload_env
    from autoshorts.db.migrate import assert_schema_current
    from autoshorts.db.pool import init_pool

    conninfo = database_url or reload_env().database_url
    try:
        assert_schema_current(conninfo=conninfo)
        init_pool(conninfo, min_size=1, max_size=2, application_name="autoshorts-admin")
    except PoolTimeout as exc:
        raise CliError("не подключиться к БД") from exc
    except Exception as exc:  # noqa: BLE001 - MigrationsPending, OperationalError: одной строкой
        message = getattr(exc, "message", None) or str(exc).strip().splitlines()[0]
        raise CliError(f"БД не готова: {message}") from exc


def cmd_source_register(args: argparse.Namespace) -> int:
    from autoshorts.core.errors import AppError
    from autoshorts.db.pool import close_pool, connection
    from autoshorts.services import sources as svc
    from autoshorts.services.audit import SYSTEM

    _init_pool(args.database_url)
    try:
        user_id: int | None = None
        if args.user:
            with connection() as conn:
                row = conn.execute("SELECT id FROM users WHERE login = %(l)s", {"l": args.user}).fetchone()
            if row is None:
                raise CliError(f"нет пользователя {args.user}")
            user_id = int(row["id"])
        try:
            src = svc.register_prepared_dir(
                args.media_dir,
                title=args.title,
                niche_id=args.niche,
                user_id=user_id,
                select=not args.no_select,
                actor=SYSTEM,
            )
        except AppError as exc:
            raise CliError(f"{exc.code}: {exc.message}") from None
        except (OSError, ValueError) as exc:
            raise CliError(str(exc)) from None
    finally:
        close_pool()
    rights = src["rights"]
    print(
        f"источник #{src['id']}: «{src['title']}», {src['status']}, права {rights['rights_type']} "
        f"({rights['owner_name']}); предупреждения: "
        + (", ".join(w["code"] for w in rights["warnings"]) or "нет")
    )
    return 0
