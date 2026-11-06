#!/usr/bin/env bash
# YouTube через мак владельца (01.11: «можешь от туда качать видео через ютуб»): с адреса хранилища YouTube требует
# вход (бот-проверка), с домашнего адреса мака — нет. На маке ничего не ставится и не пишется: только SOCKS через ssh.
#   этот сервер 127.0.0.1:18341 (ssh -D на мак) → хранилище 127.0.0.1:18341 (ssh -R) → yt-dlp --proxy socks5://127.0.0.1:18341
# Держится сам: при обрыве переподключается. Запуск: scripts/mac-proxy.sh start|stop|status
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID="$ROOT/var/mac-proxy.pid"
LOG="$ROOT/var/log/mac-proxy.log"
MAC="${MAC_HOST:-giga@100.66.203.50}"
MAC_KEY="${MAC_KEY:-$HOME/.ssh/bpn_ios}"
STORE="${STORE_HOST:-root@45.157.235.178}"
STORE_KEY="${STORE_KEY:-$HOME/.ssh/kt_storage}"
PORT="${MAC_PROXY_PORT:-18341}"
OPTS=(-o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=accept-new -N)
mkdir -p "$ROOT/var/log"

keep() {  # keep <имя> <команда…>: перезапускать при обрыве
  local name="$1"; shift
  while true; do
    "$@" >>"$LOG" 2>&1
    echo "$(date -u +%FT%TZ) $name упал, переподключение через 10 с" >>"$LOG"
    sleep 10
  done
}

case "${1:-status}" in
  start)
    if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then echo "уже работает (pid $(cat "$PID"))"; exit 0; fi
    nohup bash "$0" loop >/dev/null 2>&1 &
    echo $! >"$PID"
    sleep 4
    "$0" status
    ;;
  loop)
    keep "мак (SOCKS)" ssh -i "$MAC_KEY" "${OPTS[@]}" -D "127.0.0.1:$PORT" "$MAC" &
    keep "хранилище (-R)" ssh -i "$STORE_KEY" "${OPTS[@]}" -R "127.0.0.1:$PORT:127.0.0.1:$PORT" "$STORE" &
    wait
    ;;
  stop)
    if [ -f "$PID" ]; then pkill -P "$(cat "$PID")" 2>/dev/null; kill "$(cat "$PID")" 2>/dev/null; rm -f "$PID"; echo "остановлен"; else echo "не запущен"; fi
    ;;
  status)
    if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then echo "процесс жив (pid $(cat "$PID"))"; else echo "не запущен"; fi
    ip=$(ssh -i "$STORE_KEY" -o BatchMode=yes -o ConnectTimeout=10 "$STORE" "curl -s -m 15 --socks5-hostname 127.0.0.1:$PORT https://api.ipify.org" 2>/dev/null)
    echo "выход YouTube с хранилища через мак: ${ip:-нет связи}"
    ;;
  *) echo "использование: $0 start|stop|status"; exit 2 ;;
esac
