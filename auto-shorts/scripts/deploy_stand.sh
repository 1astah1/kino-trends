#!/usr/bin/env bash
# Выкладка auto-shorts на стенд с проверками до и после. Адрес стенда — в .stand.env (вне git):
#   STAND_HOST=root@<ip>  STAND_WEB=https://<домен панели>
#
#   scripts/deploy_stand.sh                       # тесты → код → перезапуск api и воркеров → дымовая проверка → доктор
#   scripts/deploy_stand.sh --web                 # то же + сборка и выкладка фронта
#   scripts/deploy_stand.sh --skip-tests api      # без тестов, перезапустить только api
#   TESTS="tests/api/test_clip_jobs.py" scripts/deploy_stand.sh   # дополнительные тесты к быстрым
#
# Быстрые тесты: tests/unit и tests/contract (схемы API и снапшоты). Падение — выкладки нет.
# После перезапуска: ждём /health/ready = 200 (до 90 с), проверяем страницы консоли, потом доктор
# кино-трендов (~/kino-trends, python -m kinotrends.doctor --no-fix) печатает состояние системы.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$ROOT/.stand.env" ] && . "$ROOT/.stand.env"
HOST="${STAND_HOST:?задайте STAND_HOST в .stand.env}"
KEY="$HOME/.ssh/kt_storage"
REMOTE="/home/devaccess/auto-shorts"
WEB_URL="${STAND_WEB:?задайте STAND_WEB в .stand.env}"
SSH=(ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=15 "$HOST")
RSYNC_SSH="ssh -i $KEY -o BatchMode=yes"

skip_tests=0
with_web=0
services=()
for arg in "$@"; do
  case "$arg" in
    --skip-tests) skip_tests=1 ;;
    --web) with_web=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) services+=("$arg") ;;
  esac
done
[ ${#services[@]} -eq 0 ] && services=(api worker-light worker-heavy)
[ $with_web -eq 1 ] && [[ ! " ${services[*]} " =~ " web " ]] && services+=(web)

step() { printf '\n== %s\n' "$*"; }
die() { printf '\n!! %s\n' "$*" >&2; exit 1; }

cd "$ROOT"

if [ $skip_tests -eq 0 ]; then
  step "тесты: tests/unit tests/contract ${TESTS:-}"
  # shellcheck disable=SC2086
  nice -n 10 .venv/bin/python -m pytest -q -x tests/unit tests/contract ${TESTS:-} || die "тесты упали — выкладки нет"
  if [ $with_web -eq 1 ]; then
    step "фронт: типы и юнит-тесты"
    (cd web && npx tsc --noEmit -p . && npx playwright test -c playwright.unit.config.ts) || die "проверка фронта упала — выкладки нет"
  fi
fi

if [ $with_web -eq 1 ]; then
  step "сборка фронта (5–15 минут на этом боксе)"
  (cd web && rm -rf out-new && NEXT_EXPORT=1 NEXT_EXPORT_DIR=out-new NODE_OPTIONS=--max-old-space-size=2048 nice -n 10 npx next build --webpack >/tmp/as-web-build.log 2>&1) \
    || { tail -30 /tmp/as-web-build.log; die "сборка фронта упала"; }
  (cd web && NEXT_EXPORT_DIR=out-new node scripts/postbuild.mjs >/dev/null && rm -f out-new/mockServiceWorker.js)
fi

step "код на стенд"
rsync -rc --out-format='  %n' -e "$RSYNC_SSH" --exclude __pycache__ src/ "$HOST:$REMOTE/src/" | grep -v '/$' || true
if [ $with_web -eq 1 ]; then
  rsync -az -e "$RSYNC_SSH" web/out-new/ "$HOST:$REMOTE/web/out/"
fi

step "перезапуск: ${services[*]}"
"${SSH[@]}" "bash /root/as_restart.sh ${services[*]} 2>&1 | grep -E 'работает|запущен|API|Веб' || true"

step "дымовая проверка"
ok=0
for _ in $(seq 1 45); do
  code=$("${SSH[@]}" "curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:18300/api/v1/health/ready" || echo 000)
  if [ "$code" = "200" ]; then ok=1; break; fi
  sleep 2
done
[ $ok -eq 1 ] || die "API не поднялся за 90 с — смотреть $REMOTE/var/stand/logs/api.log"
echo "  API /health/ready: 200"
for page in /review/ /clipping/ /queue/ /channels/ /packages/; do
  code=$(curl -s -o /dev/null -m 15 -w '%{http_code}' "$WEB_URL$page" || echo 000)
  echo "  $page $code"
  [ "$code" = "200" ] || die "страница $page отвечает $code"
done

step "доктор системы"
if [ -d "$HOME/kino-trends" ]; then
  (cd "$HOME/kino-trends" && timeout 180 .venv/bin/python -m kinotrends.doctor --no-fix 2>/dev/null | head -60) || echo "  доктор нашёл поломки — см. выше и вкладку «Система» в кино-трендах"
fi
echo
echo "готово"
