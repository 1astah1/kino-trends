#!/usr/bin/env bash
# Управление сервисом Kino Trends: start | stop | restart | status | logs
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID="$ROOT/var/service.pid"
LOG="$ROOT/var/log/service.log"
PORT="${KT_PORT:-18320}"
HOST="${KT_HOST:-127.0.0.1}"
mkdir -p "$ROOT/var/log"

is_running() { [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }

case "${1:-status}" in
  start)
    if is_running; then echo "уже работает (pid $(cat "$PID"))"; exit 0; fi
    cd "$ROOT" || exit 1
    nohup nice -n 10 "$ROOT/.venv/bin/python" -m kinotrends serve --host "$HOST" --port "$PORT" >> "$LOG" 2>&1 &
    echo $! > "$PID"
    sleep 2
    if is_running; then echo "запущен: http://$HOST:$PORT (pid $(cat "$PID"))"; else echo "не поднялся, смотри $LOG"; tail -20 "$LOG"; exit 1; fi
    ;;
  stop)
    if is_running; then kill "$(cat "$PID")"; sleep 1; echo "остановлен"; else echo "не запущен"; fi
    rm -f "$PID"
    ;;
  restart)
    "$0" stop; "$0" start
    ;;
  status)
    if is_running; then
      echo "работает (pid $(cat "$PID"))"
      curl -s -m 5 "http://$HOST:$PORT/api/health" || echo "API не отвечает"
      echo
    else
      echo "не запущен"
    fi
    ;;
  logs)
    tail -n "${2:-50}" "$LOG"
    ;;
  *)
    echo "использование: $0 start|stop|restart|status|logs"; exit 2
    ;;
esac
