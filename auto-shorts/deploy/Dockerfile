# Образ приложения Auto Shorts для dev-compose и CI (docs/ARCHITECTURE.md §8.4).
# Прод работает под systemd из .venv (deploy/systemd, §8.5), этот образ туда не едет.
#
#   docker compose build                                  # из корня репозитория, см. docker-compose.yml
#   docker build -f deploy/Dockerfile -t autoshorts-app:dev .
#
# Контекст сборки — корень репозитория, но в него попадают только pyproject.toml, src/, config/
# и entrypoint: список задаёт deploy/Dockerfile.dockerignore (BuildKit берёт его вместо корневого
# .dockerignore). Поэтому .env, .venv, web/ и render_tmp/ в образ не попадают.
#
# Аргументы сборки (имена латиницей):
#   APP_UID / APP_GID  — uid/gid пользователя autoshorts. Ставьте свои (id -u, id -g), чтобы файлы
#                        в ./var/media на хосте принадлежали вам, а не root.
#   PIP_EXTRAS         — "" (по умолчанию) или "clipping": onnx-asr, onnxruntime, opencv-headless
#                        для трека нарезки (§6.3). На общем dev-боксе без нужды не ставить.
#   PG_MAJOR           — мажор клиента PostgreSQL (pg_dump/psql). Должен совпадать с сервером (16),
#                        иначе pg_dump откажется снимать дамп перед миграцией в APP_ENV=prod.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG NODE_IMAGE=node:22-bookworm-slim

# yt-dlp 2026.x решает JS-челленджи YouTube внешним рантаймом (EJS) и принимает node только
# версии ≥ 22 (yt_dlp/utils/_jsruntime.py). В Debian bookworm nodejs 18, поэтому бинарник node
# берётся из официального образа той же базы (glibc bookworm), а не из apt.
FROM ${NODE_IMAGE} AS node

FROM ${PYTHON_IMAGE} AS app

ARG APP_UID=1000
ARG APP_GID=1000
ARG PIP_EXTRAS=""
ARG PG_MAJOR=16

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    HOME=/var/lib/autoshorts \
    XDG_CACHE_HOME=/var/lib/autoshorts/.cache \
    MEDIA_ROOT=/var/lib/autoshorts/media \
    MODELS_DIR=/var/lib/autoshorts/models \
    BACKUP_DIR=/var/lib/autoshorts/backups

# ffmpeg в Debian собран с libass (субтитры) — §8.4. fonts-dejavu-core — запасной шрифт для libass.
# tini — PID 1: пересылает сигналы и собирает зомби ffmpeg. postgresql-client-<PG_MAJOR> — из
# репозитория PGDG (скрипт и ключ поставляются пакетом postgresql-common, curl не нужен).
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates ffmpeg fonts-dejavu-core tini postgresql-common; \
    /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y; \
    apt-get install -y --no-install-recommends "postgresql-client-${PG_MAJOR}"; \
    rm -rf /var/lib/apt/lists/*; \
    ffmpeg -hide_banner -filters 2>/dev/null | grep -q ' ass ' ; \
    pg_dump --version

COPY --from=node /usr/local/bin/node /usr/local/bin/node
RUN node --version

RUN set -eux; \
    groupadd --gid "${APP_GID}" autoshorts; \
    useradd --uid "${APP_UID}" --gid autoshorts --home-dir /var/lib/autoshorts --no-create-home \
        --shell /usr/sbin/nologin autoshorts; \
    install -d -o autoshorts -g autoshorts -m 0750 \
        /var/lib/autoshorts /var/lib/autoshorts/.cache /var/lib/autoshorts/media \
        /var/lib/autoshorts/models /var/lib/autoshorts/backups; \
    python -m venv "${VIRTUAL_ENV}"

WORKDIR /app

# Слой зависимостей: пересобирается только при изменении pyproject.toml (единственный источник
# Python-зависимостей, §3.1). Пакет-заглушка нужен, чтобы pip разрешил проект; потом он удаляется.
COPY pyproject.toml /app/pyproject.toml
RUN set -eux; \
    mkdir -p /app/src/autoshorts; \
    touch /app/src/autoshorts/__init__.py; \
    pip install ".${PIP_EXTRAS:+[${PIP_EXTRAS}]}"; \
    pip uninstall -y autoshorts; \
    rm -rf /app/src /app/build /app/*.egg-info

# Код ставится в режиме editable: compose монтирует ./src поверх /app/src (живой код без пересборки),
# а консольные команды autoshorts-admin и autoshorts-worker остаются в /opt/venv/bin.
COPY src /app/src
COPY config /app/config
RUN set -eux; \
    pip install --no-deps -e /app; \
    python -c "import autoshorts"; \
    autoshorts-admin --help >/dev/null

COPY deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000 8001

# Entry point стартует от root только для того, чтобы отдать пользователю autoshorts свежесозданные
# docker'ом каталоги томов, и сразу сбрасывает права (setpriv). Сами процессы root не получают.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "autoshorts.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
