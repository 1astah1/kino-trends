# deploy/ — выкладка Auto Shorts v1

Каркас потока M1-E по docs/ARCHITECTURE.md §8. Разработка идёт в docker compose, прод и стенд работают
под systemd из `.venv` за nginx. Порядок работы не меняется: сначала блок проверяют на стенде, причём
под логином, и только потом он идёт в прод (§1.5).

| Путь | Что это |
|---|---|
| `../docker-compose.yml` | dev-compose. Все сервисы за профилями. Режим A — всё в контейнерах, режим B — экономный |
| `compose.mode-b.yml` | надстройка режима B: тяжёлый воркер и web в сети хоста против системного PG16 |
| `dev/mode-b.sh` | режим B: api, public, воркеры и web из `.venv` против отдельной базы |
| `Dockerfile`, `Dockerfile.dockerignore`, `docker-entrypoint.sh` | образ для compose и CI (в прод не едет) |
| `systemd/*.service`, `*.timer`, `autoshorts.target` | юниты v1: api, public, worker-light, worker-heavy, bot, backup, restore-check |
| `systemd/profiles/dedicated_small/` | drop-in'ы профиля ресурсов «отдельный VPS» (§8.1) |
| `nginx/admin.conf`, `nginx/public.conf`, `nginx/snippets/` | admin-хост (статика, API, SSE, tus, X-Accel) и хост редиректора |
| `env/prod.env.example` | образец `/opt/auto-shorts/.env` |
| `backup.sh` | ежедневный `pg_dump -Fc`: 7 дневных и 4 недельных |
| `autoshorts-*.service`, `nginx-dashboard.conf` (в корне deploy/) | **legacy** до M7: старый воркер, бот и дашборд на 18234 |

## Разработка

Готовые команды — в шапке `docker-compose.yml`. Коротко:

- **Режим A** (отдельная машина): `COMPOSE_PROFILES=db,app docker compose up -d --build`. Postgres поднимается на `127.0.0.1:5442`, порт 5432 не занимается. Профили `heavy`, `web` и `bot` включаются только по требованию.
- **Режим B** (общий бокс): в `.env` задать `AS_DEV_DATABASE_URL` на отдельную базу, `AS_UID`/`AS_GID` как у `id -u`/`id -g`, затем:
  ```sh
  deploy/dev/mode-b.sh createdb && deploy/dev/mode-b.sh migrate
  deploy/dev/mode-b.sh api            # в отдельных терминалах: public, worker-light, web
  deploy/dev/mode-b.sh worker-heavy   # или heavy-container; перед запуском проверяется MemAvailable ≥ 1200 МБ
  ```
  Скрипт откажется работать с legacy-базой `autoshorts` и с тестовыми `autoshorts_test*`.

## Прод и стенд на VPS (systemd)

Требования:
- Ubuntu 22.04+ или Debian 12, systemd ≥ 249;
- PostgreSQL 16 с клиентом **той же** версии (иначе `pg_dump` не снимет дамп);
- ffmpeg с libass;
- **node ≥ 22**: его требует yt-dlp 2026.x для JS-челленджей YouTube, а в Debian 12 лежит node 18;
- nginx и certbot.

1. **Пользователь и каталоги**
   ```sh
   sudo useradd --system --home-dir /var/lib/autoshorts --shell /usr/sbin/nologin autoshorts
   sudo install -d -o autoshorts -g autoshorts -m 0750 /var/lib/autoshorts /var/lib/autoshorts/media \
        /var/lib/autoshorts/models /var/backups/autoshorts
   sudo install -d -o root -g autoshorts -m 0750 /etc/autoshorts
   sudo usermod -aG autoshorts www-data        # nginx отдаёт медиа по X-Accel (файлы 0640, каталоги 0750)
   ```
2. **Код и окружение.** Репозиторий кладётся в `/opt/auto-shorts` и остаётся доступным только на чтение: сервисам разрешено писать лишь в `/var/lib/autoshorts` (`ProtectSystem=strict`).
   ```sh
   cd /opt/auto-shorts && sudo python3.12 -m venv .venv
   sudo .venv/bin/pip install -e '.[clipping]'   # на общей машине без [clipping], пока нет трека нарезки
   sudo install -o root -g autoshorts -m 0640 deploy/env/prod.env.example /opt/auto-shorts/.env   # заполнить
   # файл ключа сначала создаётся пустым с правами 0640, потом заполняется (без окна 0644)
   sudo install -o root -g autoshorts -m 0640 /dev/null /etc/autoshorts/as_secrets_key
   .venv/bin/python -c "from autoshorts.core.security import generate_fernet_key as g; print(g())" \
     | sudo tee /etc/autoshorts/as_secrets_key >/dev/null
   # до M7-C: legacy config.py создаёт эти каталоги при импорте
   sudo install -d -o autoshorts -g autoshorts /opt/auto-shorts/render_tmp /opt/auto-shorts/assets/master_archive
   ```
3. **Миграции.** Перед 0003 и 0004 нужно остановить legacy-процессы (§4.1). При `APP_ENV=prod` раннер сам снимает дамп перед миграцией.
   ```sh
   sudo systemctl stop autoshorts-worker autoshorts-bot autoshorts-dashboard 2>/dev/null || true
   sudo -u autoshorts sh -c 'cd /opt/auto-shorts && .venv/bin/autoshorts-admin migrate'
   ```
4. **Юниты.** Новый `autoshorts-bot.service` ставится **вместо** legacy-юнита с тем же именем. Legacy-воркер `autoshorts-worker.service` выключает M3-D, дашборд на 18234 живёт до M7.
   ```sh
   sudo install -m 0644 deploy/systemd/*.service deploy/systemd/*.timer deploy/systemd/autoshorts.target /etc/systemd/system/
   # только на отдельном VPS, вместе с RESOURCE_PROFILE=dedicated_small в .env:
   sudo cp -r deploy/systemd/profiles/dedicated_small/*.service.d /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now autoshorts.target autoshorts-api autoshorts-public \
        autoshorts-worker-light autoshorts-worker-heavy autoshorts-bot autoshorts-backup.timer
   # autoshorts-restore-check.timer включать после M7-A (команда restore-check)
   ```
   Лимиты тяжёлого воркера по умолчанию соответствуют профилю `shared_dev`: `CPUQuota=200%`, `MemoryMax=1800M`, IO 60/40 МБ/с на `/dev/sda`. Если корневой диск другой (`lsblk`, на VPS часто `/dev/vda`), поправьте устройство: `systemctl edit autoshorts-worker-heavy`.
5. **nginx и TLS.** Шапки `nginx/admin.conf` и `nginx/public.conf` описывают установку. В них нужно заменить `admin.example.com` и `u.example.com` на свои домены (§11.2 п. 1). У admin-хоста есть закомментированный allowlist по IP или VPN.
   Если 80/443 держит Coolify/Traefik в Docker, до `127.0.0.1` хоста он не достанет. Возможные варианты: маршрут Traefik на хостовый nginx через адрес docker-моста (порт закрыт файрволом снаружи) или запасная раздача по §7.1 (FastAPI `StaticFiles` и `FileResponse`). Выбор за владельцем.
6. **Проверка**
   ```sh
   sudo systemd-analyze verify /etc/systemd/system/autoshorts-*.service      # без вывода = ок
   systemctl show autoshorts-worker-heavy -p CPUQuotaPerSecUSec -p MemoryMax -p Nice
   ss -ltn | grep -E ':1830[02]'                                              # только 127.0.0.1
   curl -s 127.0.0.1:18300/api/v1/health/ready && curl -s -o /dev/null -w '%{http_code}\n' 127.0.0.1:18300/docs   # 200, 404
   AS_SMOKE_LOGIN=… AS_SMOKE_PASSWORD=… .venv/bin/python scripts/e2e_smoke.py --prod-readonly \
        --admin-url https://admin.<домен> --public-url https://u.<домен>
   sudo systemctl start autoshorts-backup && journalctl -u autoshorts-backup -n 5
   ```

Выкладка обновлений (`deploy.sh`), сторож `/health/ready` и `restore-check` делаются в M7-A.
