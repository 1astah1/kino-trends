#!/usr/bin/env bash
# Туннель к стенду auto-shorts на хранилище: API 18300 → локальный 18330, веб 18301 → локальный 18331.
# Держится сам: при обрыве переподключается. Запуск: scripts/autoshorts-tunnel.sh start|stop|status
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID="$ROOT/var/autoshorts-tunnel.pid"
LOG="$ROOT/var/log/autoshorts-tunnel.log"
HOST="${AS_TUNNEL_HOST:-$(cat "$ROOT/var/autoshorts-host.txt" 2>/dev/null)}"  # адрес хранилища: var/autoshorts-host.txt (не в git) или AS_TUNNEL_HOST
[ -n "$HOST" ] || { echo "не задан адрес стенда: запишите его в var/autoshorts-host.txt или AS_TUNNEL_HOST"; exit 2; }
KEY="${AS_TUNNEL_KEY:-$HOME/.ssh/kt_storage}"
mkdir -p "$ROOT/var/log"

loop() {
  while true; do
    ssh -i "$KEY" -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
        -o StrictHostKeyChecking=accept-new -N -L 127.0.0.1:18330:127.0.0.1:18300 -L 127.0.0.1:18331:127.0.0.1:18301 "root@$HOST" >>"$LOG" 2>&1
    echo "$(date -u +%FT%TZ) туннель упал, переподключение через 5 с" >>"$LOG"
    sleep 5
  done
}

case "${1:-status}" in
  start)
    if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then echo "уже работает (pid $(cat "$PID"))"; exit 0; fi
    nohup bash "$0" loop >/dev/null 2>&1 &
    echo $! >"$PID"
    sleep 2
    echo "запущен (pid $(cat "$PID"))"; "$0" status
    ;;
  loop) loop ;;
  stop)
    if [ -f "$PID" ]; then pkill -P "$(cat "$PID")" 2>/dev/null; kill "$(cat "$PID")" 2>/dev/null; rm -f "$PID"; echo "остановлен"; else echo "не запущен"; fi
    ;;
  status)
    if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then echo "процесс жив (pid $(cat "$PID"))"; else echo "не запущен"; fi
    curl -s -m 5 -o /dev/null -w "API auto-shorts через туннель: HTTP %{http_code}\n" http://127.0.0.1:18330/api/v1/health/ready || true
    ;;
  *) echo "использование: $0 start|stop|status"; exit 2 ;;
esac
