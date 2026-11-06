#!/usr/bin/env bash
# Ежедневный дамп базы Auto Shorts (docs/ARCHITECTURE.md §8.5): pg_dump -Fc, хранятся 7 дневных и 4 недельных.
# Запускается юнитом autoshorts-backup.service по таймеру (03:00 Europe/Moscow) от пользователя autoshorts.
#   вручную на VPS:   sudo systemctl start autoshorts-backup && journalctl -u autoshorts-backup -n 20
#   вне systemd:      DATABASE_URL=... BACKUP_DIR=/tmp/x deploy/backup.sh
#
# Переменные (латиница): DATABASE_URL (обязательна), BACKUP_DIR (по умолчанию /var/backups/autoshorts),
#   AS_BACKUP_KEEP_DAILY (7), AS_BACKUP_KEEP_WEEKLY (4), AS_BACKUP_WEEKLY_DOW (7 = воскресенье, как date +%u).
# Раскладка: $BACKUP_DIR/daily/autoshorts_<UTC>.dump и $BACKUP_DIR/weekly/autoshorts_<UTC>.dump.
# Дамп пишется во временный файл, проверяется через pg_restore --list и только потом получает имя:
# оборванный или пустой дамп не вытесняет хорошие при ротации.
# Не трогает: pre_migrate_*.dump в корне BACKUP_DIR (их делает раннер миграций, §4.1);
# восстановление проверяет autoshorts-admin restore-check (таймер autoshorts-restore-check);
# вынос masters/ и rights/ за пределы VPS — решение владельца (§8.5, §11.2 п. 8).
set -euo pipefail
umask 027

log() { printf 'autoshorts-backup: %s\n' "$*"; }
die() { printf 'autoshorts-backup: ОШИБКА: %s\n' "$*" >&2; exit 1; }

: "${DATABASE_URL:?DATABASE_URL is required}"
BACKUP_DIR=${BACKUP_DIR:-/var/backups/autoshorts}
KEEP_DAILY=${AS_BACKUP_KEEP_DAILY:-7}
KEEP_WEEKLY=${AS_BACKUP_KEEP_WEEKLY:-4}
WEEKLY_DOW=${AS_BACKUP_WEEKLY_DOW:-7}

for value in "$KEEP_DAILY" "$KEEP_WEEKLY"; do
    [[ $value =~ ^[1-9][0-9]*$ ]] || die "число хранимых дампов должно быть ≥ 1, получено: $value"
done
[[ $WEEKLY_DOW =~ ^[1-7]$ ]] || die "AS_BACKUP_WEEKLY_DOW — день недели 1..7, получено: $WEEKLY_DOW"
command -v pg_dump >/dev/null || die "нет pg_dump (пакет postgresql-client той же версии, что сервер)"

daily_dir=$BACKUP_DIR/daily
weekly_dir=$BACKUP_DIR/weekly
mkdir -p -- "$daily_dir" "$weekly_dir"

# Один прогон за раз (ручной запуск во время таймерного)
exec 9>"$BACKUP_DIR/.backup.lock"
flock -n 9 || die "другой прогон бэкапа ещё идёт"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
target=$daily_dir/autoshorts_$stamp.dump
partial=$daily_dir/.autoshorts_$stamp.dump.partial
trap 'rm -f -- "$partial"' EXIT

started=$(date +%s)
pg_dump --format=custom --no-password --dbname="$DATABASE_URL" --file="$partial" \
    || die "pg_dump завершился с ошибкой"

toc=$(pg_restore --list "$partial") || die "pg_restore не читает оглавление дампа"
tables=$(grep -c ' TABLE DATA ' <<<"$toc" || true)
[ "${tables:-0}" -gt 0 ] || die "в дампе нет данных ни одной таблицы — база пустая или не та"

mv -- "$partial" "$target"
size=$(du -h -- "$target" | cut -f1)
log "дамп $target: $size, таблиц с данными $tables, $(( $(date +%s) - started )) с"

if [ "$(date +%u)" = "$WEEKLY_DOW" ]; then
    # жёсткая ссылка не занимает места; на другой ФС — копия
    ln -- "$target" "$weekly_dir/" 2>/dev/null || cp -p -- "$target" "$weekly_dir/"
    log "недельная копия в $weekly_dir"
fi

# Ротация: имена содержат UTC-метку, поэтому обратная лексикографическая сортировка = от новых к старым
prune() {
    local dir=$1 keep=$2 file
    find "$dir" -maxdepth 1 -type f -name 'autoshorts_*.dump' -printf '%f\n' | sort -r | tail -n +"$((keep + 1))" |
        while IFS= read -r file; do
            rm -f -- "$dir/$file"
            log "удалён старый дамп $dir/$file"
        done
}
prune "$daily_dir" "$KEEP_DAILY"
prune "$weekly_dir" "$KEEP_WEEKLY"
