#!/usr/bin/env bash
# Управление сервисом Kino Trends: start | stop | restart | status | logs | ensure (для cron)
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
    "$ROOT/scripts/mac-proxy.sh" start >/dev/null 2>&1 || true  # YouTube для хранилища через мак (storage_proxy)
    ;;
  ensure)
    # для cron раз в 5 мин: сервис не запущен — поднять; запущен, но API молчит дважды подряд — перезапустить;
    # туннель YouTube через мак — поднять, если упал (start ничего не делает, если всё живо)
    if ! is_running; then
      echo "$(date -u +%FT%TZ) ensure: сервис не работал — запускаю" >> "$LOG"; "$0" start; exit $?
    fi
    if ! curl -s -m 15 -o /dev/null "http://$HOST:$PORT/api/health"; then
      sleep 20
      if ! curl -s -m 15 -o /dev/null "http://$HOST:$PORT/api/health"; then
        echo "$(date -u +%FT%TZ) ensure: API не отвечает — перезапуск" >> "$LOG"; "$0" restart; exit $?
      fi
    fi
    "$ROOT/scripts/mac-proxy.sh" start >/dev/null 2>&1 || true
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
