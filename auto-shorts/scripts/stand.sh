#!/usr/bin/env bash
# Локальный стенд Auto Shorts (режим B) на этой машине: api, public, воркеры и статика веба.
#
#   scripts/stand.sh start     поднять всё в фоне (логи в var/stand/logs)
#   scripts/stand.sh stop      остановить
#   scripts/stand.sh status    что запущено и отвечает ли API
#
# Всё слушает только 127.0.0.1:
#   18301 — веб-интерфейс (заходить сюда; /api проксируется на 18300)
#   18300 — API
#   18302 — редиректор кодов с баннера и приём конверсий
# Снаружи — через проброс порта в VS Code (вкладка Ports) или ssh -L 18301:127.0.0.1:18301.
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RUN="$REPO/var/stand"
LOGS="$RUN/logs"
MODEB="$REPO/deploy/dev/mode-b.sh"
SERVICES=(api public worker-light worker-heavy web)

mkdir -p "$LOGS"

pidfile() { echo "$RUN/$1.pid"; }

is_running() {
    local pf; pf=$(pidfile "$1")
    [ -f "$pf" ] && kill -0 "$(cat "$pf")" 2>/dev/null
}

start_one() {
    local name=$1
    if is_running "$name"; then
        echo "$name: уже запущен (pid $(cat "$(pidfile "$name")"))"
        return
    fi
    case $name in
        web)
            [ -d "$REPO/web/out" ] || { echo "web: нет web/out — соберите: cd web && npm run build:export"; return 1; }
            # в фон уходит только node (не весь подпроцесс), иначе $! — pid обёртки
            # и она держит stdout вызывающего терминала
            (
                cd "$REPO/web" || exit 1
                setsid nohup node scripts/serve-static.mjs --dir out --port 18301 \
                    </dev/null >>"$LOGS/web.log" 2>&1 &
                echo $! >"$(pidfile web)"
            )
            ;;
        *)
            setsid nohup nice -n 5 "$MODEB" "$name" </dev/null >>"$LOGS/$name.log" 2>&1 &
            echo $! >"$(pidfile "$name")"
            ;;
    esac
    sleep 1
    if is_running "$name"; then echo "$name: запущен (pid $(cat "$(pidfile "$name")"))"; else echo "$name: не стартовал, см. $LOGS/$name.log"; fi
}

stop_one() {
    local name=$1 pf pid
    pf=$(pidfile "$name")
    if ! is_running "$name"; then rm -f "$pf"; echo "$name: не запущен"; return; fi
    pid=$(cat "$pf")
    # setsid сделал процесс лидером группы: гасим всю группу (uvicorn, воркеры, дочерние ffmpeg)
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    kill -0 "$pid" 2>/dev/null && kill -KILL -- "-$pid" 2>/dev/null || true
    rm -f "$pf"
    echo "$name: остановлен"
}

case ${1:-status} in
    start)
        for s in "${SERVICES[@]}"; do start_one "$s"; done
        sleep 2
        "$0" status
        ;;
    stop)
        for s in "${SERVICES[@]}"; do stop_one "$s"; done
        ;;
    status)
        for s in "${SERVICES[@]}"; do
            if is_running "$s"; then echo "$s: работает (pid $(cat "$(pidfile "$s")"))"; else echo "$s: не запущен"; fi
        done
        code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18300/api/v1/health/ready || true)
        echo "API /health/ready: ${code:-нет ответа}"
        code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18301/ || true)
        echo "Веб 127.0.0.1:18301: ${code:-нет ответа}"
        ;;
    *)
        echo "usage: $0 start|stop|status" >&2
        exit 2
        ;;
esac
