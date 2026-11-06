"""Подкоманды БД и окружения: `migrate`, `create-user`, `check-config` (docs/ARCHITECTURE.md §4.1, §8.3).

    autoshorts-admin migrate [--dry-run] [--no-backup]
    autoshorts-admin create-user --login admin --role admin [--password-stdin] [--if-not-exists]
    autoshorts-admin check-config [--json] [--no-db]

Пароль никогда не передаётся аргументом (виден в `ps`): спрашивается дважды через getpass
или читается первой строкой stdin (`--password-stdin`).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from autoshorts.cli import CliError

LOGIN_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{2,31}")  # = users.login CHECK (0002) = api LOGIN_PATTERN
ROLES = ("admin", "operator", "viewer")


def register(subparsers: argparse._SubParsersAction) -> None:  # noqa: SLF001 - тип argparse
    p = subparsers.add_parser(
        "migrate",
        help="применить SQL-миграции",
        description="Применить неприменённые миграции src/autoshorts/db/migrations (§4.1).",
    )
    p.add_argument("--dry-run", action="store_true", help="только показать план, ничего не менять")
    p.add_argument("--no-backup", action="store_true", help="не делать pg_dump перед применением")
    p.add_argument("--database-url", default=None, help="по умолчанию DATABASE_URL")
    p.add_argument("--backup-dir", type=Path, default=None, help="по умолчанию BACKUP_DIR")
    p.add_argument("--lock-timeout", type=float, default=60.0, help="ждать блокировку раннера, секунд")
    p.add_argument("--migrations-dir", type=Path, default=None, help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_migrate)

    p = subparsers.add_parser(
        "create-user",
        help="создать пользователя консоли",
        description="Создать пользователя (argon2id). Пароль спрашивается интерактивно или из stdin.",
    )
    p.add_argument("--login", required=True, help="латиница: ^[a-z0-9][a-z0-9_.-]{2,31}$")
    p.add_argument("--role", required=True, choices=ROLES)
    p.add_argument("--display-name", default=None, help="имя в интерфейсе (по умолчанию = login)")
    p.add_argument("--tg-user-id", type=int, default=None, help="Telegram user_id для кнопок приёмки")
    p.add_argument("--password-stdin", action="store_true", help="прочитать пароль первой строкой stdin")
    p.add_argument("--if-not-exists", action="store_true", help="если логин занят — выйти с кодом 0")
    p.add_argument(
        "--reset-password",
        action="store_true",
        help="если логин занят — сменить пароль, снять блокировку и отозвать сессии",
    )
    p.add_argument("--database-url", default=None, help="по умолчанию DATABASE_URL")
    p.set_defaults(func=cmd_create_user)

    p = subparsers.add_parser(
        "check-config",
        help="проверить окружение (env, БД, каталоги, ключи)",
        description="Проверить переменные окружения и доступность зависимостей (§8.3).",
    )
    p.add_argument("--json", action="store_true", help="вывод в JSON")
    p.add_argument("--no-db", action="store_true", help="не подключаться к БД")
    p.add_argument("--database-url", default=None, help="по умолчанию DATABASE_URL")
    p.set_defaults(func=cmd_check_config)


# ---------------------------------------------------------------- общие помощники


def _database_url(args: argparse.Namespace) -> str:
    if args.database_url:
        return args.database_url
    return _env().database_url


def _env() -> Any:
    from pydantic import ValidationError

    from autoshorts.core.config import reload_env

    try:
        return reload_env()
    except ValidationError as exc:
        raise CliError(f"переменные окружения некорректны: {_validation_summary(exc)}") from exc


def _validation_summary(exc: Any) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", ())) or "?"
        parts.append(f"{loc.upper()}: {err.get('msg')}")
    return "; ".join(parts)


def _connect_error(exc: BaseException) -> CliError:
    from autoshorts.core.logging import redact_text

    first = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return CliError(f"не подключиться к БД: {redact_text(first)}")


# ---------------------------------------------------------------- migrate


def cmd_migrate(args: argparse.Namespace) -> int:
    import psycopg

    from autoshorts.core.config import redact_conninfo
    from autoshorts.db import migrate as runner

    conninfo = _database_url(args)
    backup = not args.no_backup
    backup_dir = args.backup_dir
    if backup and backup_dir is None and not args.dry_run:
        backup_dir = _env().backup_dir

    print(f"База: {redact_conninfo(conninfo)}")
    try:
        result = runner.migrate(
            conninfo,
            dry_run=args.dry_run,
            backup=backup,
            backup_dir=backup_dir,
            migrations_dir=args.migrations_dir,
            lock_timeout=args.lock_timeout,
        )
    except runner.MigrationError as exc:
        raise CliError(f"миграции не применены: {exc}") from exc
    except psycopg.OperationalError as exc:
        raise _connect_error(exc) from exc

    plan = result.plan
    if args.dry_run:
        if plan.applied:
            state = f"версия {max(plan.applied)}"
        else:
            state = "legacy-схема без версии" if plan.adopt_baseline else "версий нет"
        print(f"Состояние: {state}, файлы до {plan.latest_version}")
        if plan.adopt_baseline:
            print("  legacy-база будет принята как baseline: версия 1, adopted=true (файл не выполняется)")
        for m in plan.pending:
            print(f"  будет применена {m.name}")
        if plan.pending and backup and plan.has_user_tables:
            print("  перед применением будет сделан pg_dump (отключить: --no-backup)")
        if plan.problems:
            for problem in plan.problems:
                print(f"ОТКАЗ: {problem}", file=sys.stderr)
            return 1
        if plan.up_to_date:
            print("Миграции не нужны.")
        return 0

    if result.backup_path:
        print(f"Бэкап: {result.backup_path}")
    if result.adopted_baseline:
        print("Принята legacy-база: версия 1 записана с adopted=true")
    for m in result.applied:
        print(f"  + {m.name} ({result.durations_ms.get(m.version, 0)} мс)")
    if not result.applied and not result.adopted_baseline:
        print(f"Миграции не нужны: версия {result.current_version}")
    else:
        print(f"Готово: версия {result.current_version}")
    return 0


# ---------------------------------------------------------------- create-user


def _read_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        line = sys.stdin.readline()
        password = line.rstrip("\r\n")
    else:
        if not sys.stdin.isatty():
            raise CliError("нет терминала для ввода пароля: используйте --password-stdin")
        password = getpass.getpass("Пароль: ")
        again = getpass.getpass("Пароль ещё раз: ")
        if password != again:
            raise CliError("пароли не совпадают")
    from autoshorts.core.security import validate_password

    try:
        validate_password(password)
    except ValueError as exc:
        raise CliError(str(exc)) from exc
    return password


def cmd_create_user(args: argparse.Namespace) -> int:
    import psycopg

    from autoshorts.core.security import hash_password

    login = args.login.strip()
    if not LOGIN_RE.fullmatch(login):
        raise CliError("логин: 3–32 символа, латиница в нижнем регистре, цифры, «_», «.», «-»", exit_code=2)
    display_name = (args.display_name or login).strip()
    if not display_name:
        raise CliError("пустое --display-name", exit_code=2)
    conninfo = _database_url(args)

    try:
        conn = psycopg.connect(
            conninfo, autocommit=True, application_name="autoshorts-admin", connect_timeout=10
        )
    except psycopg.OperationalError as exc:
        raise _connect_error(exc) from exc
    with conn:
        if not conn.execute("SELECT to_regclass('public.users') IS NOT NULL").fetchone()[0]:
            raise CliError("таблицы users нет: сначала выполните autoshorts-admin migrate")
        row = conn.execute("SELECT id, role FROM users WHERE login = %(login)s", {"login": login}).fetchone()
        if row is not None and not args.reset_password:
            if args.if_not_exists:
                print(f"Пользователь {login} уже есть (id={row[0]}, роль {row[1]}) — ничего не меняю")
                return 0
            raise CliError(
                f"пользователь {login} уже существует (id={row[0]}); смена пароля: --reset-password"
            )

        # пароль спрашиваем до открытия транзакции: ввод с клавиатуры не держит блокировки
        password_hash = hash_password(_read_password(args))
        if row is None:
            try:
                with conn.transaction():
                    user_id = conn.execute(
                        """
                        INSERT INTO users (login, display_name, password_hash, role, tg_user_id)
                        VALUES (%(login)s, %(name)s, %(hash)s, %(role)s, %(tg)s)
                        RETURNING id
                        """,
                        {
                            "login": login,
                            "name": display_name,
                            "hash": password_hash,
                            "role": args.role,
                            "tg": args.tg_user_id,
                        },
                    ).fetchone()[0]
                    _audit(
                        conn, "user.create", user_id, f"создан пользователь {login} ({args.role}) через CLI"
                    )
            except psycopg.errors.UniqueViolation as exc:
                raise CliError(f"логин {login} или tg_user_id {args.tg_user_id} уже заняты") from exc
            print(f"Создан пользователь {login} (id={user_id}, роль {args.role})")
        else:
            user_id = row[0]
            with conn.transaction():
                conn.execute(
                    """
                    UPDATE users SET password_hash = %(hash)s, failed_logins = 0, locked_until = NULL,
                                     updated_at = now()
                    WHERE id = %(id)s
                    """,
                    {"hash": password_hash, "id": user_id},
                )
                revoked = conn.execute(
                    "UPDATE sessions SET revoked_at = now() WHERE user_id = %(id)s AND revoked_at IS NULL",
                    {"id": user_id},
                ).rowcount
                _audit(
                    conn,
                    "user.password_reset",
                    user_id,
                    f"пароль {login} сменён через CLI, сессий отозвано: {revoked}",
                )
            print(f"Пароль пользователя {login} сменён, блокировка снята, отозвано сессий: {revoked}")
    return 0


def _audit(conn: Any, action: str, user_id: int, summary: str) -> None:
    conn.execute(
        """
        INSERT INTO audit_log (actor_kind, action, entity_type, entity_id, summary)
        VALUES ('system', %(action)s, 'user', %(id)s, %(summary)s)
        """,
        {"action": action, "id": str(user_id), "summary": summary},
    )


# ---------------------------------------------------------------- check-config


@dataclass
class Check:
    code: str
    status: str  # ok | warn | error
    message: str


def _dir_check(code: str, path: Path, *, required: bool, purpose: str) -> Check:
    if path.is_dir():
        if os.access(path, os.W_OK | os.X_OK):
            return Check(code, "ok", f"{path}")
        return Check(code, "error" if required else "warn", f"{path}: нет прав на запись")
    status = "error" if required else "warn"
    return Check(code, status, f"{path}: каталога нет ({purpose})")


def _free_gb(path: Path) -> float | None:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free / 1024**3
    except OSError:
        return None


def _pg_dump_major() -> tuple[str | None, int | None]:
    exe = shutil.which("pg_dump")
    if not exe:
        return None, None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return exe, None
    m = re.search(r"(\d+)(?:\.\d+)?", out)
    return exe, int(m.group(1)) if m else None


def collect_checks(*, database_url: str | None = None, use_db: bool = True) -> list[Check]:
    from pydantic import ValidationError

    from autoshorts.core.config import redact_conninfo, reload_env
    from autoshorts.core.errors import ConfigError
    from autoshorts.core.security import SecretCipher
    from autoshorts.domain.settings_schema import RuntimeSettings

    checks: list[Check] = []
    try:
        env = reload_env()
    except ValidationError as exc:
        return [Check("env", "error", f"переменные окружения некорректны: {_validation_summary(exc)}")]
    prod = env.is_prod
    must = "error" if prod else "warn"
    checks.append(
        Check("app_env", "ok", f"APP_ENV={env.app_env}, RESOURCE_PROFILE={env.resource_profile.value}")
    )

    # БД и миграции
    server_major: int | None = None
    conninfo = database_url or env.database_url
    if use_db:
        import psycopg

        from autoshorts.db import migrate as runner

        try:
            with psycopg.connect(
                conninfo, autocommit=True, connect_timeout=5, application_name="autoshorts-check"
            ) as conn:
                server_major = conn.info.server_version // 10000
                checks.append(
                    Check(
                        "database",
                        "ok" if server_major >= 16 else "warn",
                        f"PostgreSQL {server_major}: {redact_conninfo(conninfo)}",
                    )
                )
                plan = runner.compute_plan(conn, runner.discover_migrations())
                if plan.problems:
                    checks.append(Check("migrations", "error", "; ".join(str(p) for p in plan.problems)))
                elif plan.pending or plan.adopt_baseline:
                    names = ", ".join(m.name for m in plan.pending)
                    checks.append(Check("migrations", must, f"не применены: {names or 'принятие baseline'}"))
                else:
                    checks.append(Check("migrations", "ok", f"версия {plan.current_version}"))
        except psycopg.OperationalError as exc:
            checks.append(Check("database", "error", str(_connect_error(exc))))
        except runner.MigrationError as exc:
            checks.append(Check("migrations", "error", str(exc)))

    # каталоги и диск
    checks.append(
        _dir_check("media_root", env.media_root, required=prod, purpose="MEDIA_ROOT, создаётся воркером")
    )
    checks.append(
        _dir_check("models_dir", env.models_dir, required=False, purpose="модели ASR/лиц, нужен для нарезки")
    )
    checks.append(
        _dir_check("backup_dir", env.backup_dir, required=prod, purpose="BACKUP_DIR, создаётся migrate")
    )
    free = _free_gb(env.media_root)
    if free is not None:
        soft = RuntimeSettings().disk_soft_min_gb
        checks.append(
            Check(
                "disk_free",
                "ok" if free >= soft else "warn",
                f"свободно {free:.1f} ГБ (порог приёма {soft:g} ГБ)",
            )
        )

    # секреты и безопасность
    try:
        cipher = SecretCipher.from_env(env)
        checks.append(
            Check("secrets_key", "ok", f"ключ шифрования секретов задан, key_id={cipher.primary_key_id}")
        )
    except ConfigError as exc:
        checks.append(Check("secrets_key", must, str(exc)))
    salt = env.redirect_salt.get_secret_value() if env.redirect_salt else ""
    if not salt:
        checks.append(Check("redirect_salt", must, "REDIRECT_SALT не задан (обезличивание кликов)"))
    elif len(salt) < 16:
        checks.append(Check("redirect_salt", must, "REDIRECT_SALT короче 16 символов"))
    else:
        checks.append(Check("redirect_salt", "ok", "задан"))
    if prod and not env.cookie_secure:
        checks.append(Check("session_cookie_secure", "error", "в prod cookie сессии обязана быть Secure"))
    else:
        checks.append(Check("session_cookie_secure", "ok", f"Secure={'да' if env.cookie_secure else 'нет'}"))
    for code, url in (("admin_base_url", env.admin_base_url), ("public_base_url", env.public_base_url)):
        if prod and not url.startswith("https://"):
            checks.append(Check(code, "warn", f"{url}: в prod ожидается https"))
        else:
            checks.append(Check(code, "ok", url))

    # pg_dump для бэкапа перед миграциями
    exe, dump_major = _pg_dump_major()
    if exe is None:
        checks.append(Check("pg_dump", must, "pg_dump не найден: migrate без --no-backup откажется работать"))
    elif server_major and dump_major and dump_major < server_major:
        checks.append(Check("pg_dump", must, f"pg_dump {dump_major} старше сервера {server_major}"))
    else:
        checks.append(Check("pg_dump", "ok", f"{exe} (версия {dump_major or '?'})"))
    return checks


def cmd_check_config(args: argparse.Namespace) -> int:
    checks = collect_checks(database_url=args.database_url, use_db=not args.no_db)
    ok = not any(c.status == "error" for c in checks)
    if args.json:
        print(json.dumps({"ok": ok, "checks": [asdict(c) for c in checks]}, ensure_ascii=False, indent=2))
    else:
        for c in checks:
            print(f"[{c.status:<5}] {c.code}: {c.message}")
        errors = sum(c.status == "error" for c in checks)
        warns = sum(c.status == "warn" for c in checks)
        print(f"Итого: ошибок {errors}, предупреждений {warns}")
    return 0 if ok else 1
