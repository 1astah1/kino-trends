#!/bin/sh
# Entry point образа deploy/Dockerfile.
#
# Docker создаёт отсутствующие каталоги bind-томов (./var/media, ./var/models, ./var/backups) от root,
# и процесс под autoshorts не смог бы в них писать. Поэтому entry point стартует от root, отдаёт
# пользователю autoshorts только ВЕРХНИЙ уровень этих каталогов и только если ими владеет root
# (никакого рекурсивного chown по медиатеке), затем сбрасывает права через setpriv.
# Если контейнер уже запущен не от root (docker run --user ...), просто исполняет команду.
set -eu

if [ "$(id -u)" = "0" ]; then
    for dir in "${MEDIA_ROOT:-}" "${MODELS_DIR:-}" "${BACKUP_DIR:-}"; do
        [ -n "$dir" ] || continue
        mkdir -p "$dir"
        if [ "$(stat -c %u "$dir")" = "0" ]; then
            chown autoshorts:autoshorts "$dir"
        fi
    done
    exec setpriv --reuid=autoshorts --regid=autoshorts --init-groups --inh-caps=-all -- "$@"
fi

exec "$@"
