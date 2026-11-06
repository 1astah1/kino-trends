#!/usr/bin/env bash
# Режим B dev-окружения (docs/ARCHITECTURE.md §8.4): процессы v1 из .venv против ОТДЕЛЬНОЙ базы
# в системном PostgreSQL 16, без контейнеров postgres/web. Для общего бокса (4 ядра, 7,7 ГБ, swap занят).
# Каждая команда работает на переднем плане: Ctrl+C её останавливает, фоновых хвостов не остаётся.
#
#   deploy/dev/mode-b.sh env               итоговое окружение (пароль скрыт) и проверки
#   deploy/dev/mode-b.sh createdb          создать отдельную базу, если её нет
#   deploy/dev/mode-b.sh migrate [...]     autoshorts-admin migrate (аргументы пробрасываются)
#   deploy/dev/mode-b.sh admin <cmd> ...   любая команда autoshorts-admin
#   deploy/dev/mode-b.sh api [...]         uvicorn autoshorts.api.app:app на 127.0.0.1:18300 (например, --reload)
#   deploy/dev/mode-b.sh public [...]      редиректор (AS_PUBLIC_APP) на 127.0.0.1:18302
#   deploy/dev/mode-b.sh worker-light      autoshorts-worker --class light --concurrency 2
#   deploy/dev/mode-b.sh worker-heavy      autoshorts-worker --class heavy под лимитами shared_dev
#   deploy/dev/mode-b.sh web               next dev на 127.0.0.1:18301 из web/ (node_modules хоста)
#   deploy/dev/mode-b.sh heavy-container   тяжёлый воркер контейнером (compose + deploy/compose.mode-b.yml)
#   deploy/dev/mode-b.sh web-container     web контейнером (compose + deploy/compose.mode-b.yml)
#
# Переменные (окружение или строки KEY=VALUE в .env; имена латиницей):
#   AS_DEV_DATABASE_URL  обязательна: отдельная база, например
#                        postgresql://autoshorts:autoshorts@127.0.0.1:5432/autoshorts_v1.
#                        Legacy-база "autoshorts" и тестовые autoshorts_test* отвергаются.
#   AS_DEV_MEDIA_ROOT    медиатека, по умолчанию <репозиторий>/var/media (абсолютный путь)
#   AS_PUBLIC_APP        ASGI-приложение редиректора; до M3 — autoshorts.webapp.redirect:app
#   AS_DEV_MIN_MEM_MB    допуск по MemAvailable перед heavy и web, по умолчанию 1200 (§8.1)
#   AS_VENV_BIN          каталог bin виртуального окружения, по умолчанию <репозиторий>/.venv/bin
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
VENV_BIN=${AS_VENV_BIN:-$REPO/.venv/bin}
COMPOSE_FILES=(-f "$REPO/docker-compose.yml" -f "$REPO/deploy/compose.mode-b.yml")

die() { printf 'mode-b: %s\n' "$*" >&2; exit 1; }
note() { printf 'mode-b: %s\n' "$*" >&2; }

# Значение из .env без исполнения файла (source выполнил бы произвольный код и сломался бы на пробелах).
dotenv_get() {
    local key=$1 file=$REPO/.env line value
    [ -r "$file" ] || return 0
    line=$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$file" | tail -n 1 || true)
    [ -n "$line" ] || return 0
    value=${line#*=}
    value=${value%$'\r'}
    case $value in
        \"*\") value=${value#\"}; value=${value%\"} ;;
        \'*\') value=${value#\'}; value=${value%\'} ;;
    esac
    printf '%s' "$value"
}

setting() {
    local key=$1 default=${2:-} value
    value=${!key:-}
    [ -n "$value" ] || value=$(dotenv_get "$key")
    [ -n "$value" ] || value=$default
    printf '%s' "$value"
}

python_bin() {
    [ -x "$VENV_BIN/python" ] || die "нет $VENV_BIN/python — создайте .venv и выполните pip install -e '.[dev]'"
    printf '%s' "$VENV_BIN/python"
}

db_name_of() {
    "$(python_bin)" - "$1" <<'PY'
import sys
from psycopg.conninfo import conninfo_to_dict
print(conninfo_to_dict(sys.argv[1]).get("dbname") or "")
PY
}

redacted_url() {
    "$(python_bin)" - "$1" <<'PY'
import sys
from psycopg.conninfo import conninfo_to_dict, make_conninfo
params = conninfo_to_dict(sys.argv[1])
if "password" in params:
    params["password"] = "***"
print(make_conninfo(**params))
PY
}

load_env() {
    DEV_DATABASE_URL=$(setting AS_DEV_DATABASE_URL)
    [ -n "$DEV_DATABASE_URL" ] || die "задайте AS_DEV_DATABASE_URL (отдельная база, например postgresql://autoshorts:autoshorts@127.0.0.1:5432/autoshorts_v1)"
    DEV_DB_NAME=$(db_name_of "$DEV_DATABASE_URL") || die "AS_DEV_DATABASE_URL не разбирается как строка подключения"
    case $DEV_DB_NAME in
        "") die "в AS_DEV_DATABASE_URL не указано имя базы" ;;
        autoshorts) die "база 'autoshorts' — legacy-данные разработки, режим B работает только с отдельной базой" ;;
        autoshorts_test*) die "базы autoshorts_test* принадлежат тестам (их чистят и пересоздают), возьмите другое имя" ;;
    esac
    DEV_MEDIA_ROOT=$(setting AS_DEV_MEDIA_ROOT "$REPO/var/media")
    case $DEV_MEDIA_ROOT in
        /*) ;;
        *) die "AS_DEV_MEDIA_ROOT должен быть абсолютным путём: $DEV_MEDIA_ROOT" ;;
    esac
    PUBLIC_APP=$(setting AS_PUBLIC_APP "autoshorts.public.app:app")
    MIN_MEM_MB=$(setting AS_DEV_MIN_MEM_MB 1200)

    export DATABASE_URL=$DEV_DATABASE_URL
    export MEDIA_ROOT=$DEV_MEDIA_ROOT
    export MODELS_DIR=${MODELS_DIR:-$REPO/var/models}
    export BACKUP_DIR=${BACKUP_DIR:-$REPO/var/backups}
    export APP_ENV=${APP_ENV:-dev}
    export RESOURCE_PROFILE=shared_dev
    export ADMIN_BASE_URL=$(setting ADMIN_BASE_URL http://127.0.0.1:18301)
    # LLM для нарезки и авторского слоя: из окружения или .env (ключ лучше держать в секретах БД)
    # LLM_SELECT_MODEL — отбор моментов, LLM_AUTHOR_MODEL — сценарий автора (services/clipping.llm_client)
    for k in LLM_PROVIDER LLM_BASE_URL LLM_MODEL LLM_SELECT_MODEL LLM_AUTHOR_MODEL LLM_API_KEY; do
        v=$(setting "$k"); [ -n "$v" ] && export "$k=$v"
    done
    export PUBLIC_BASE_URL=${PUBLIC_BASE_URL:-http://127.0.0.1:18302}
    export PYTHONUNBUFFERED=1
}

prepare_dirs() {
    mkdir -p "$MEDIA_ROOT" "$MODELS_DIR" "$BACKUP_DIR"
}

mem_available_mb() {
    awk '/^MemAvailable:/ {print int($2 / 1024)}' /proc/meminfo
}

require_memory() {
    local what=$1 avail
    avail=$(mem_available_mb)
    if [ "${avail:-0}" -lt "$MIN_MEM_MB" ]; then
        die "$what не запускается: MemAvailable ${avail} МБ < ${MIN_MEM_MB} МБ (§8.1). Освободите память или поднимите AS_DEV_MIN_MEM_MB осознанно"
    fi
}

# Лимиты shared_dev (§8.1) через пользовательский systemd, если он доступен; иначе только nice.
heavy_wrapper() {
    if command -v systemd-run >/dev/null 2>&1 \
        && systemd-run --user --scope --quiet --collect -p MemoryMax=64M -- true >/dev/null 2>&1; then
        printf '%s\n' systemd-run --user --scope --quiet --collect \
            -p CPUQuota=200% -p CPUWeight=20 -p MemoryHigh=1300M -p MemoryMax=1800M -- nice -n 15
    else
        note "systemd-run --user недоступен: лимиты CPU и памяти не применены, работает только nice -n 15"
        printf '%s\n' nice -n 15
    fi
}

# Справка — шапка этого файла (комментарии со второй строки до первой строки кода)
usage() {
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"
}

compose_env() {
    export AS_DEV_DATABASE_URL=$DEV_DATABASE_URL
    export AS_DEV_MEDIA_ROOT=$DEV_MEDIA_ROOT
    export AS_UID=${AS_UID:-$(id -u)}
    export AS_GID=${AS_GID:-$(id -g)}
}

cmd=${1:-}
[ -n "$cmd" ] || { usage; exit 2; }
shift

case $cmd in
    env)
        load_env
        printf 'DATABASE_URL=%s\n' "$(redacted_url "$DATABASE_URL")"
        printf 'MEDIA_ROOT=%s\nMODELS_DIR=%s\nBACKUP_DIR=%s\n' "$MEDIA_ROOT" "$MODELS_DIR" "$BACKUP_DIR"
        printf 'APP_ENV=%s\nRESOURCE_PROFILE=%s\nAS_PUBLIC_APP=%s\n' "$APP_ENV" "$RESOURCE_PROFILE" "$PUBLIC_APP"
        printf 'MemAvailable=%s МБ (допуск heavy/web: %s МБ)\n' "$(mem_available_mb)" "$MIN_MEM_MB"
        ;;
    createdb)
        load_env
        "$(python_bin)" - "$DATABASE_URL" <<'PY'
import sys
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

params = conninfo_to_dict(sys.argv[1])
name = params["dbname"]
params["dbname"] = "postgres"
with psycopg.connect(make_conninfo(**params), autocommit=True) as conn:
    exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
    if exists:
        print(f"mode-b: база {name} уже есть")
    else:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        print(f"mode-b: база {name} создана")
PY
        ;;
    migrate)
        load_env
        prepare_dirs
        exec "$VENV_BIN/autoshorts-admin" migrate "$@"
        ;;
    admin)
        load_env
        prepare_dirs
        exec "$VENV_BIN/autoshorts-admin" "$@"
        ;;
    api)
        load_env
        prepare_dirs
        exec "$VENV_BIN/uvicorn" autoshorts.api.app:app --host 127.0.0.1 --port 18300 --workers 1 \
            --timeout-graceful-shutdown 5 "$@"
        ;;
    public)
        load_env
        prepare_dirs
        exec "$VENV_BIN/uvicorn" "$PUBLIC_APP" --host 127.0.0.1 --port 18302 --workers 1 --no-access-log "$@"
        ;;
    worker-light)
        load_env
        prepare_dirs
        exec nice -n 5 "$VENV_BIN/autoshorts-worker" --class light --concurrency 2 "$@"
        ;;
    worker-heavy)
        load_env
        prepare_dirs
        require_memory "тяжёлый воркер"
        export FFMPEG_THREADS=2 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMBA_NUM_THREADS=1
        mapfile -t wrapper < <(heavy_wrapper)
        exec "${wrapper[@]}" "$VENV_BIN/autoshorts-worker" --class heavy "$@"
        ;;
    web)
        load_env
        require_memory "next dev"
        cd "$REPO/web"
        export AS_API_ORIGIN=http://127.0.0.1:18300 NEXT_TELEMETRY_DISABLED=1
        export NODE_OPTIONS=${NODE_OPTIONS:---max-old-space-size=1536}
        exec nice -n 10 npm run dev
        ;;
    heavy-container)
        load_env
        prepare_dirs
        require_memory "тяжёлый воркер"
        compose_env
        exec docker compose "${COMPOSE_FILES[@]}" --profile heavy up worker-heavy "$@"
        ;;
    web-container)
        load_env
        prepare_dirs
        require_memory "next dev"
        compose_env
        exec docker compose "${COMPOSE_FILES[@]}" --profile web up web "$@"
        ;;
    -h|--help|help)
        usage
        ;;
    *)
        die "неизвестная команда: $cmd (см. deploy/dev/mode-b.sh help)"
        ;;
esac
