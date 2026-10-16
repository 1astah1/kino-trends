#!/usr/bin/env bash
# Сборка словаря Wikidata (возобновляемая). start [потоки] | stop | status | cron
# cron: если словарь не добран, запускает сборку под flock (вызывается из crontab раз в 10 минут,
# поэтому падения процесса на сетевых сбоях не страшны: следующий запуск продолжит с места остановки).
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
PAT="kinotrends.dictionary.wikidata"
LOCK="$ROOT/var/dict/fetch.lock"
case "${1:-start}" in
  stop) for p in $(pgrep -f "$PAT"); do kill "$p"; done; echo "остановлено";;
  status) pgrep -af "$PAT" || echo "не запущено"; wc -l < var/dict/entities.jsonl;;
  cron)
    total=$(python3 -c "import json;print(len(json.load(open('$ROOT/var/dict/items.json'))))" 2>/dev/null || echo 0)
    have=$(wc -l < "$ROOT/var/dict/entities.jsonl" 2>/dev/null || echo 0)
    if [ "$total" -gt 0 ] && [ "$have" -ge "$total" ] && [ -s "$ROOT/var/dict/labels.json" ]; then exit 0; fi
    exec flock -n "$LOCK" nice -n 10 "$ROOT/.venv/bin/python" -m "$PAT" fetch "${2:-2}" >> "$ROOT/var/dict/fetch.log" 2>&1
    ;;
  start|*) [ "${1:-start}" = start ] && shift
    nohup flock -n "$LOCK" nice -n 10 "$ROOT/.venv/bin/python" -m "$PAT" fetch "${1:-2}" >> "$ROOT/var/dict/fetch.log" 2>&1 &
    echo "pid $!";;
esac
