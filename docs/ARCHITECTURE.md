# Auto Shorts: архитектура v1

Статус: утверждаемый проект. Дата 26.09.2026.
Источники: ТЗ владельца (`~/.claude/plans/zippy-hatching-peacock.md`), три архитектурных предложения (backend/data, frontend/UX, media/analytics) и сверка с кодом `/home/devaccess/auto-shorts` (коммит `dc4f4ee`).
Язык: описания на русском. Все идентификаторы латиницей: пути API, параметры, ключи JSON, enum-значения, имена таблиц, колонок, настроек, секретов, событий и тегов OpenAPI.

---

## 0. Главное на одной странице

| Вопрос | Решение |
|---|---|
| Продукт | Операторская консоль и конвейер: генерация роликов по нише, нарезка своих или лицензированных длинных видео с авторским слоем, трендоскаут. На выходе пакет к **ручной** заливке, дальше учёт просмотров, кликов, стартов бота и конверсий |
| Бэкенд | FastAPI + pydantic 2 + psycopg 3 (без ORM), PostgreSQL 16. Свои SQL-миграции с предохранителем. Своя очередь на Postgres (`tasks`: SKIP LOCKED, lease и heartbeat, классы `heavy` и `light`) |
| Процессы | `api`, `public` (редиректор и приём конверсий), `worker-light`, `worker-heavy` (строго один), `bot`. `web` в проде раздаётся статикой через nginx |
| Фронтенд | Next.js 16 + React 19 + Tailwind 4 + TanStack Query 5 + zustand + recharts + lucide. `output: 'export'` (статический экспорт) на том же origin, что и API. Node-сервера в проде нет. Ноль внешних ресурсов в рантайме |
| Медиа | Баннер собирается как PNG в Pillow и накладывается overlay с окнами показа. Ken Burns через `crop`. QC одним проходом. Нарезка по EDL и одному кодированию. ASR: GigaAM v3 через onnx-asr без torch, облачный запасной вариант |
| Атрибуция | Код пакета (короткий, Crockford base32) создаётся **до** заливки. После регистрации URL добавляется алиас `yt_<id>` или `vk_<owner>_<id>`. Редиректор на своём домене, Telegram через `tg://resolve`. Продукты шлют конверсии HMAC-вебхуком |
| Аналитика | Ролл-апы `stats_daily_views` и `stats_daily_traffic`, режимы activity и cohort. Честная «цена опубликованного ролика». A/B между роликами: Манн–Уитни по `ln(views_72h)` и CTR, вердикт не раньше 72 ч |
| Ресурсы | Один тяжёлый процесс за раз (advisory lock + flock). `nice`, лимиты systemd на CPU, память и **пропускную способность IO**: `ionice` на `mq-deadline` не работает, проверено. Допуск по MemAvailable, PSI и loadavg, дисковый сторож 12/8/5 ГБ |
| Уникализация | Приёмов, маскирующих чужой контент от Content ID и детекта дублей, **нет**. Вместо них реестр прав, измеряемый авторский слой (70/30), самопроверка похожести своей библиотеки, разные профили каналов. Подробности в §1.3 |

### 0.1 Разрешённые конфликты между предложениями

| # | Вопрос | Варианты | Решение и почему |
|---|---|---|---|
| 1 | Доставка фронтенда | `next dev`/`next start` в compose (backend) против статического экспорта (frontend) | **Статический экспорт + nginx**. Свободно около 2,4 ГБ RAM, swap занят на 100% (`free -m`, проверено), SSR инструменту за логином не нужен. В dev работает `next dev` с rewrites на API |
| 2 | Порты | 18234 (frontend) против 18300/18301/18302 (backend) | **api 18300, web-dev 18301, public 18302**, все на 127.0.0.1. По `ss -ltn` свободны. 18234 остаётся за старым дашбордом до M7 |
| 3 | Статусы ролика | Старый линейный список (frontend TS) против `status` + `stage` + таблица `tasks` (backend) | **Backend**. Бизнес-статус отделён от исполнения, у каждой стадии своя история, ретраи, прогресс и отмена |
| 4 | Трек нарезки | `cut` и `reframe` отдельными перекодированиями (backend) против EDL и одного прохода (media) | **EDL + одно кодирование**. Экономит CPU и диск, упрощает QC (`expected_static`) |
| 5 | ASR | whisper.cpp и vosk, GigaAM отложен из-за torch (backend) против GigaAM v3 через onnx-asr без torch (media) | **GigaAM v3 e2e CTC int8 через onnx-asr** (MIT, проверено) + облако (Yandex SpeechKit или OpenAI-совместимый) + mock. Whisper-семейство откладывается на v1.1. Default подтверждается замером `bench_asr` (гейт в M5) |
| 6 | Приоритет IO | `ionice` (backend) против `IO*BandwidthMax` (media) | На диске `mq-deadline` (`/sys/block/sda/queue/scheduler`, проверено), `ionice` ничего не даёт. **Лимиты пропускной способности systemd + `nice` + потоки ffmpeg=2** |
| 7 | Пороги защит | Диск 8/5 ГБ, память 900 МБ (backend) против 12/8/5 ГБ, 1,2 ГБ + PSI (media) | **Ступени media**, значения задаются настройками. Два профиля ресурсов: `shared_dev` и `dedicated_small` |
| 8 | Расписание опроса метрик | Таблица `snapshot_schedule` (media) против лестницы `next_poll_at` (backend) | **`next_poll_at` + интерполяция на ровный возраст**. Меньше строк, тот же результат |
| 9 | Метка атрибуции | `yt_<id>` из ТЗ против кода пакета (backend) против коротких кодов с уровнями exact/channel/modeled (media) | **Код пакета** (5 символов Crockford, нижний регистр) до заливки + алиас `yt_<id>` после. Уровни `exact` и `channel`. Модельная атрибуция голого домена переносится на v1.1. Код в кадре по умолчанию выключен, решает владелец |
| 10 | Приём конверсий | `/api/v1/ingest/events` (media) против публичного `/ingest/v1/events` (backend) | **Публичный хост редиректора**. Операторский API можно закрыть allowlist'ом, не ломая интеграции |
| 11 | CSRF | `X-Requested-With` (frontend) против double-submit (backend) | **Double-submit** `X-CSRF-Token` = cookie `as_csrf` |
| 12 | Роли | owner/operator/viewer против admin/operator/viewer | **admin/operator/viewer**. В UI `admin` подписан как «Владелец» |
| 13 | Загрузка файлов | tus (backend, frontend) против своего чанкового PUT (media) | **tus 1.0.0**, свой сервер около 200 строк, tus-js-client, чанки по 16 МБ |
| 14 | Права на источник | Колонки в `sources` (backend) против отдельной таблицы (media) | **Отдельная `source_rights`** с типами media. CC-BY-SA и NC не принимаются: несовместимы с промо-баннером |
| 15 | Кросспост | Разрешён на другой канал (backend) против «один мастер = один канал» (frontend) | **На одной площадке один ролик идёт ровно в один канал**, ТЗ §10 про каскад. Для VK создаётся **дочерний job** с перерендером под VK-оверрайды |
| 16 | Ключ пакета | `job_id` (frontend) против `package_id` (backend) | **`package_id`**, плюс `GET /jobs/{job_id}/package` |
| 17 | Баннер | `drawtext` (текущий код) против PNG + overlay (ТЗ §6, media) | **PNG через Pillow**. Превью в редакторе строится тем же кодом (`banner_png.py`), поэтому «кадр из рендера» совпадает с роликом |
| 18 | Хранилище аналитики | MATERIALIZED VIEW (media) против таблиц ролл-апов (backend) | **Таблицы ролл-апов**: пересчёт инкрементальный, учитывает TZ, наглядно отлаживается |
| 19 | Похожесть своих роликов | CLIP (frontend) против dHash (backend) против signature + pHash (media) | **v1: dHash 8 ключевых кадров** (Pillow). `signature` появится в v1.1, CLIP не берём: нужен torch |
| 20 | Учёт стоимости не-ролика | `subject_type/subject_id` (media) против явных FK (backend) | **Явные FK** (`job_id`, `task_id`, `source_id`, `trend_brief_id`) + CHECK «хотя бы одна» |
| 21 | Оптимистичная блокировка | `If-Match` (frontend) против `expected_version` в теле (backend) | **`expected_version` в теле**: проще для openapi-fetch и кодогенерации |
| 22 | Пагинация | Курсор (frontend) против offset (backend) | **offset** для списков, **`before_id`** для журналов |
| 23 | SSE | `/api/v1/stream`, `job_status` (frontend) против `/events/stream`, `job.updated` (backend) | **`/api/v1/events/stream`**, имена событий `snake_case` (`job_status`, `job_progress`, …) |
| 24 | Раскладка пакетов кода | Слои (backend) против фич (media) | **Гибрид**: слои `core/db/domain/repos/services/api` + фичевые пакеты алгоритмов (`clipping/`, `trends/`, `collectors/`, `analytics/`). Владение файлами по областям (§10) |
| 25 | Обнаружение роликов конкурентов | Только RSS (backend) против RSS с fallback на UUSH-плейлист и учётом квоты (media) | **Media**. README фиксирует 404 на RSS в песочнице, fallback обязателен |
| 26 | Rework | Отдельный статус (backend) против мгновенного перезапуска | `review: rework` с `auto_restart=true` (по умолчанию) сразу ставит задачи. При `false` ролик ждёт в `rework` правок и `POST /jobs/{id}/start` |

---

## 1. Цели и границы v1

### 1.1 Цели (KPI владельца)
- **Просмотры и переходы по баннеру.** Монетизация площадок не интересует. Площадки: YouTube Shorts основная, VK Клипы второй контур.
- 5–10 роликов в сутки, каждый проходит **ручную приёмку** и **ручную заливку**.
- Конверсионная воронка «свой домен + Telegram-бот параллельно». Конверсии считаются от engaged views.
- Время оператора на ролик: приёмка ≤ 20 с, заливка по пакету ≤ 2 мин, привязка URL ≤ 10 с.

### 1.2 Что входит в v1

| Область | Входит |
|---|---|
| Источники контента | (1) AI-генерация по нише (есть, доводится). (2) Нарезка своих или лицензированных длинных видео (файлом или по URL) с обязательной записью о правах и авторским слоем. (3) Трендоскаут: каналы конкурентов → аутлаеры → брифы структуры → свои ролики по брифу |
| Продукты и баннеры | Продукты (umka.chat, TMA-магазин ключей, VPN-бот и другие), шаблоны баннера (геометрия), кампании (таргетинг на каналы и ниши), варианты A/B (тексты, веса), предпросмотр с safe zones |
| Производство | Очередь задач, стадии, ретраи, отмена, бюджет-предохранитель, QC, приёмка в вебе (основная) и в Telegram (мобильный дубль) |
| Публикация | Пакет к заливке (файл, заголовок, описание, 3 хэштега, закреп, заметка о звуке, чек-лист, трекинг-ссылки). Оператор вставляет URL, система разбирает и проверяет его и регистрирует публикацию |
| Атрибуция | Редиректор с лендингом (Telegram и сайт), журнал кликов без сырых IP, приём конверсий от продуктов (HMAC), трекинг-ссылки каналов |
| Метрики | YouTube Data API (просмотры, лайки, комментарии, статус и снятие). YouTube Analytics через OAuth (engaged views, удержание), **опционально**, решение владельца. VK по цепочке `vk_api` → `ytdlp_meta` → ручной ввод |
| Аналитика | Разрезы по ролику, каналу, нише, продукту, кампании, варианту баннера, треку и площадке. Воронка, когорты, стоимость, QC, A/B |
| Эксплуатация | Пользователи и роли, аудит, секреты, провайдеры, алерты в Telegram, бэкапы, restore-check, профили ресурсов |

### 1.3 Как работает детект площадок и что в этом проекте называется «уникализацией»

Этот раздел отвечает на вопрос владельца «как обходить и уникализировать». Сначала механика, потом решение.

**Как площадки распознают контент:**
1. **Content ID (YouTube) и аналоги (в VK «Немезида»).** Правообладатель загружает эталонные файлы. Каждая новая заливка получает аудио- и видеоотпечатки, и они сравниваются с базой. Отпечатки специально проектируют устойчивыми к типовым правкам: кропу, зеркалу, смене скорости и тона, наложениям, рамкам, перекодированию. При совпадении ставится **клейм**: блок, монетизация в пользу правообладателя или просто отслеживание. Клейм — не страйк, и для нашего KPI он почти безвреден (ТЗ, «Ключевое следствие»). Опасны **жалобы**, которые правообладатель подаёт вручную: они дают страйки, а три страйка за 90 дней означают удаление канала. Технические правки файла от ручной жалобы не защищают вообще.
2. **Reused / inauthentic content.** С января 2026 оценивается **на уровне канала**, классификатором и модераторами. Проверяют две вещи: есть ли у роликов существенный собственный вклад (комментарий, разбор, сюжет) и не поточные ли загрузки (одинаковый формат, обложки, синтетический голос без вариаций, ежедневные перезаливы с мелкими правками). Санкция — срез рекомендаций, то есть ноль просмотров. Отличие пикселей этот классификатор не оценивает, он оценивает вклад.
3. **Чужие водяные знаки** детектируются всеми площадками, это стоит около 90% охвата.
4. **Дубли и сети.** Перезалив своего ролика с мелкими правками, один креатив на нескольких каналах, кросспост с чужой меткой. Всё это ловится одновременно по визуальному отпечатку, аудио и метаданным, и теряется вся сетка сразу (ТЗ §10).

**Чего система не делает.** Преобразований, цель которых спрятать **чужой** контент от отпечатков и детекта дублей (зеркала, сдвиги тона и скорости, шумовые подложки, джиттер кропа, «вычистка» метаданных ради неузнаваемости), нет. Это вне рамок задачи, противоречит зафиксированному решению владельца («авторский слой 70/30, не техническая уникализация») и по пунктам 1–2 не защищает от того, что реально убивает канал.

**Что система делает, чтобы контент был по-настоящему уникальным и выглядел так:**
- **Реестр прав** на каждый источник нарезки: своё, лицензия (договор или платформенная), CC-BY, public domain. Без записи о правах источник не принимается. Для лицензированного контента отдельно ведётся статус **allowlist в Content ID** у правообладателя: это единственный штатный способ не получать клеймы.
- **Измеряемый авторский слой** `author_share` (своя озвучка, свои вставки, свой текст). Для чужих источников действует гейт ≥ 0,30 в QC (§6.3.8).
- **Отбраковка источников с чужими водяными знаками** проверкой в `probing`. Такие источники не маскируются, а отбрасываются.
- **Самопроверка похожести своей библиотеки** (dHash ключевых кадров против последних роликов канала). Ответ на вопрос «не штампую ли я», в ответ только содержательные меры: другой хук, визуал, голос, шаблон.
- **Разные профили каналов** (`channels.profile`): свой шаблон баннера, голос, визуальный стиль, расписание. UI предупреждает, если два канала совпадают по профилю.
- **Один ролик — один канал на площадке**. Для VK делается отдельный перерендер под VK.
- **Трендоскаут переносит механику** (хук, темп, длительность, петлю), а не файлы. Копия конкурента для анализа скачивается во временный каталог и удаляется в `finally`.
- **Учёт последствий**: `publications.status`, `has_claim`, алерты на снятие и на `rejectionReason=uploaderAccountSuspended`.

### 1.4 Что НЕ входит в v1
- Автозаливка на площадки через API (решение владельца: ручная публикация). TikTok (API запрещает баннеры, ТЗ §6).
- Модельная атрибуция «голого» домена (распределение прямого трафика по роликам). В v1 такой трафик показывается честно, отдельной строкой «прямой трафик продукта».
- Split-экран и active speaker в рефрейме (v2). OCR текста на экране (v2). CLIP-эмбеддинги (v2). Whisper-семейство ASR (v1.1).
- AI-видео (i2v) кроме опционального хук-клипа. Мастер замены канала (v1.1). Pull и CSV-импорт конверсий (v1.1).
- Мультиарендность и публичная регистрация пользователей.

### 1.5 Нефункциональные требования
- Никаких внешних CDN и шрифтов в рантайме фронтенда: CSP `default-src 'self'`, после сборки скрипт проверяет артефакты.
- Кириллица только в **значениях** данных. Автотест проверяет пути, параметры, ключи схем и enum в OpenAPI.
- SQL: параметр внутри `CASE` и в интервальной арифметике — только `CAST(%(x)s AS int)` или `make_interval(days => CAST(%(d)s AS int))`. Конструкции вида `(%s || ' days')::interval` из `dashboard_queries.cost_summary` переписываются.
- Тяжёлая работа не выдавливает соседей: retentionos и ai-reseller postgres/redis на этой машине.
- Каждый блок сначала проверяется на **стенде**, потом идёт в прод. E2E-проверки выполняются **под логином**.

---

## 2. Схема системы и компоненты

### 2.1 Схема

```
         Оператор (браузер, РФ)                               Оператор (Telegram, телефон)
                  │ HTTPS admin.<домен>                                  │
                  ▼                                                      ▼
┌──────────────────────────────────── VPS-воркер Auto Shorts ───────────────────────────────────────┐
│  nginx (admin.<домен>)                                                                            │
│    /                  → web/out  (статический экспорт Next.js, 0 внешних ресурсов, CSP 'self')    │
│    /api/              → api      127.0.0.1:18300  (FastAPI, uvicorn --workers 1, SSE)              │
│    /_protected_media/ → internal alias MEDIA_ROOT (X-Accel-Redirect, Range, sendfile)              │
│  nginx (u.<домен>)                                                                                │
│    /*                 → public   127.0.0.1:18302  (/{code}, /go/{code}/tg|site, /ingest/v1/events) │
│                                                                                                   │
│  bot (python-telegram-bot) ─┐        ┌──────── api / public ────────┐                             │
│                             ▼        ▼                               │                             │
│                        services ──► repos ──► PostgreSQL 16 ◄───────┘                             │
│                                                ▲   ▲  LISTEN/NOTIFY (as_tasks, as_events)         │
│   worker-light (1 процесс, 2–3 слота)  ────────┘   └──────── worker-heavy (строго 1, advisory lock)│
│    LLM, fal, edge-tts, облачный ASR,                          ffmpeg-рендер, QC, прокси, ASR       │
│    коллекторы, планировщик (лидер),                           GigaAM, рефрейм (YuNet),             │
│    ролл-апы, пакеты, алерты                                   анализ структуры конкурента          │
│                 │                                                        │                        │
│                 └──────────────────── MEDIA_ROOT (локальный диск) ───────┘                        │
│                                      masters/ jobs/ sources/ thumbs/ banners/ logs/                │
└───────────────────────────────────────────────────────────────────────────────────────────────────┘
   │ HTTPS наружу (опциональный прокси per-provider)                   ▲ HMAC-вебхуки конверсий
   ▼                                                                    │
 fal.ai · LLM (OpenAI-совместимый / GigaChat) · edge-tts · YouTube Data/Analytics · VK API · RSS
                                                     Продукты: umka.chat, TMA-магазин, VPN-бот, …

Путь зрителя:
 Shorts/Клип → видит домен (и опц. код) → u.<домен>/<code> → лендинг ─┬→ /go/<code>/tg   → tg://resolve?domain=<bot>&start=<code>_l
                                                                       └→ /go/<code>/site → <landing_url>?utm_campaign=<code>…
 Продукт: /start <code>[_l] | регистрация с utm_campaign | оплата → POST u.<домен>/ingest/v1/events (HMAC) → conversions
```

### 2.2 Компоненты

| Компонент | Команда запуска | Порт | Ответственность |
|---|---|---|---|
| `api` | `uvicorn autoshorts.api.app:app --workers 1` | 127.0.0.1:18300 | REST `/api/v1`, сессии, SSE, выдача медиа (X-Accel). Один процесс: SSE-рассылка и лимиты входа в памяти |
| `public` | `uvicorn autoshorts.public.app:app` | 127.0.0.1:18302 | Редиректор, лендинг (Jinja2 autoescape, инлайн-CSS, CSP), `/go/*`, приём конверсий `/ingest/v1/events`, `/health`, `robots.txt` |
| `worker-light` | `autoshorts-worker --class light --concurrency 2` | — | Сетевые шаги (LLM, fal, edge-tts, облачный ASR, yt-dlp-загрузки с `ratelimit`), коллекторы, сборка пакетов, ролл-апы, алерты, планировщик (лидер через `pg_try_advisory_lock(771002)`), reaper |
| `worker-heavy` | `autoshorts-worker --class heavy` | — | ffmpeg (рендер, превью, QC, прокси, peaks, раскадровка), GigaAM ASR, склейки и лица, анализ структуры. **Ровно один экземпляр**: `pg_try_advisory_lock(771001)` + `flock /run/lock/autoshorts-heavy.lock` |
| `bot` | `python -m autoshorts.bot.telegram_bot` | — | Уведомления, приёмка с телефона (только пользователи с `users.tg_user_id`), `/link <код> <url>`. Работает только через `services.*` |
| `web` | prod: статика `web/out`; dev: `next dev -p 18301` | 127.0.0.1:18301 (dev) | Операторская консоль |
| `collectors` | задачи в `worker-light` (не отдельный процесс) | — | YouTube Data, YouTube Analytics (OAuth), VK, RSS и UUSH конкурентов, миниатюры |
| `redirect` | часть `public` | — | см. `public` |
| PostgreSQL 16 | системный или контейнер | 127.0.0.1:5442 (dev compose) | Всё состояние; `LISTEN/NOTIFY` для мгновенного подхвата задач и SSE |

---

## 3. Структура репозитория после рефакторинга

### 3.1 Дерево

```
auto-shorts/
  pyproject.toml              # единственный источник Python-зависимостей; extras: [clipping], [dev]
  docker-compose.yml          # dev (переписан, §8.4)
  config/                     # SEED-данные (импорт в БД CLI-командами), не рантайм-конфиг
    niches/*.yaml  banner.yaml  safe_zones.yaml  fonts/Montserrat-*.ttf  logos/*.png (эталоны чужих водяных знаков)
  deploy/
    Dockerfile  systemd/*.service|*.timer  nginx/admin.conf nginx/public.conf  deploy.sh  backup.sh
  docs/ARCHITECTURE.md
  scripts/
    init_db.sql               # legacy, только для чтения (= миграция 0001)
    bench_asr.py  bench_render.py  bench_clipping.py  e2e_smoke.py
  src/autoshorts/
    core/        config.py logging.py errors.py security.py ids.py timeutil.py resources.py storage.py
    db/          pool.py migrate.py notify.py migrations/0001_baseline.sql … 0009_seed.sql
    domain/      enums.py reject_reasons.py settings_schema.py niche_schema.py banner_schema.py
                 stages.py url_parse.py tags.py events.py
    repos/       по файлу на агрегат (только SQL, принимают conn)
    services/    по файлу на область (бизнес-логика, границы транзакций)
    providers/   registry.py base.py
                 llm/{openai_compat,gigachat,structured,mock}.py  tts/{edge,silero,fal,mock}.py
                 images/{fal,mock}.py  asr/{base,gigaam_onnx,yandex_speechkit,openai_compat,mock}.py
                 faces/{yunet,mock}.py  vision/{gemini,mock}.py
    pipeline/    ffmpeg_exec.py probe.py render.py banner_png.py subtitles.py qc.py similarity.py
                 audio_mix.py proxy.py peaks.py storyboard.py beat_sync.py audio_strategy.py
                 hashtags.py trend_mimicry.py structure_analysis.py
    clipping/    windows.py llm_select.py schemas.py features.py scoring.py snapping.py
                 reframe/{shots,track,camera,graph}.py author/{script,share}.py edl.py render_clip.py
                 watermark_check.py
    trends/      outlier.py baselines.py youtube_api.py discovery.py analyze.py briefs.py field_stats.py
    collectors/  http.py youtube_data.py youtube_analytics.py oauth_google.py vk_video.py thumbnails.py
    analytics/   formulas.py stats.py rollups.py
    stages/      base.py generate_script.py generate_voice.py generate_visuals.py render.py qc.py
                 clip_fetch.py clip_analyze.py author_script.py author_voice.py clip_render.py package_build.py
    tasks/       registry.py  + <area>.py (обработчики задач, регистрируются декоратором)
    workers/     main.py runner.py heartbeat.py reaper.py scheduler.py guards.py policies.py
    api/         app.py deps.py middleware.py pagination.py sse.py media.py
                 schemas/<area>.py  routers/<area>.py   (роутеры подхватываются автоматически)
    public/      app.py landing.py botfilter.py ingest.py emitter_snippet.py templates/landing.html
    bot/         telegram_bot.py
    cli/         __init__.py (autoshorts-admin, подкоманды подхватываются автоматически)  <area>.py
  tests/
    unit/ db/ api/ worker/ migrations/ contract/openapi/<tag>.json media/ e2e_py/
  web/                        # Next.js 16 (§7)
```

Правило зависимостей: `api | bot | public | workers | tasks → services → repos + domain`, `stages → pipeline | clipping | providers | services`. `pipeline`, `clipping`, `trends` (алгоритмы) и `domain` не импортируют `db`, `services` и `api`. Правило проверяется `import-linter` в CI.

### 3.2 Что куда переезжает

| Было | Стало | Примечание |
|---|---|---|
| `config.py` | `core/config.py` (env, `get_env()` с `lru_cache`, **без mkdir при импорте**) + `domain/niche_schema.py` + `services/niches.py` | Ниши и баннер переезжают в БД. YAML только импортируется и экспортируется через CLI и API |
| `db.py` | `db/pool.py`, `repos/jobs.py`, `repos/tasks.py`, `services/budget.py` | Бюджет считается по бизнес-дню БД `(now() AT TIME ZONE 'Europe/Moscow')::date`, а не по `date.today()` |
| `models.py` | `domain/enums.py`, `domain/reject_reasons.py`; `Scene`, `WordTiming`, `QCResult` остаются в `pipeline/` | |
| `dashboard_queries.py` | `repos/analytics.py` | |
| `webapp/dashboard.py` | удаляется в M7. До этого слушает только 127.0.0.1 (M0) | |
| `webapp/redirect.py` | `public/app.py` (переписан) | Хотфикс XSS и `/health` идёт отдельно в M0 |
| `queue/worker.py` | `workers/*` + `tasks/*` | |
| `pipeline/orchestrator.py` | `stages/*.py` | |
| `pipeline/voice.py` | `providers/tts/edge.py` (+ `boundary="WordBoundary"`), `providers/tts/mock.py` | |
| `pipeline/attribution.py` | `domain/tags.py` + `services/tracking.py` | |
| `pipeline/ingest.py` | `services/sources.py` (загрузка), `clipping/reframe/*`, `pipeline/proxy.py` | Докстринг «приём чужого вирусного клипа» удаляется, трек только для своих и лицензированных источников |
| `pipeline/structure_analysis.py` | `trends/analyze.py` | 360p и первые 60 с, удаление в `finally` сохраняется |
| `pipeline/hashtags.py` `_NICHE_HASHTAG_POOLS` | конфиг ниши `hashtags.pool` | |
| `providers/llm.py` | `providers/llm/*` + промпт из конфига ниши (`prompts.system`, `prompts.body_hint`) | Тело про таро перестаёт быть зашитым в код |
| `providers/images.py` | `providers/images/*` | seed считается как `int(blake2b(f"{video_uid}:{i}").hexdigest()[:8],16)`, а не `hash()` |
| `trends/rss_watch.py` | `trends/discovery.py` | |
| `trends/outlier.py` | `trends/outlier.py` (расширяется нормировкой по возрасту) | |
| `scripts/init_db.sql` | `db/migrations/0001_baseline.sql` (копия 1:1) | |
| `scripts/run_pipeline_once.py` | `autoshorts-admin render-once` | |
| `render_tmp/` | `MEDIA_ROOT/jobs/`; старые каталоги убирает `cleanup` | |

### 3.3 Подтверждённые дефекты, которые чинятся первыми (M0)
1. **Озвучка — синус 220 Гц.** В edge-tts 7.2.8 параметр `Communicate(..., boundary=...)` по умолчанию равен `"SentenceBoundary"` (проверено по исходнику в `.venv`). Код ждёт `WordBoundary`, получает 0 слов и падает. Воркер поэтому зашит на `offline=True`, а `run_stage_render` подставляет синус. Лечение: `edge_tts.Communicate(text, voice, boundary="WordBoundary")` и снятие `offline=True`. В prod mock-провайдеры запрещены без `allow_mock_in_prod`.
2. **Редиректор:**
   - `@app.get("/{tag}")` объявлен раньше `/health`, поэтому `/health` пишется в атрибуцию;
   - метка подставляется в HTML и JS без экранирования — отражённый XSS;
   - кириллица и мусор не валидируются;
   - ботов-превьюеров никто не отсеивает.
3. **Дашборд** в `deploy/autoshorts-dashboard.service` слушает `0.0.0.0` с HTTP Basic по голому HTTP. Переводится на `127.0.0.1`.
4. **seed** картинок `hash()` меняется между процессами, ретрай даёт другие картинки.
5. **`/videoid`** всегда пишет `platform='youtube'` и не проверяет URL. Кнопки приёмки может нажать любой участник чата.
6. **Длительность сцен** считается от целевого диапазона ниши, а не от длины озвучки, и `-shortest` режет хвост. Исправляется в M2.

---

## 4. Схема БД и миграции

### 4.1 Раннер миграций (`db/migrate.py`, CLI `autoshorts-admin migrate [--dry-run] [--no-backup]`)

Alembic не берём: ORM нет, autogenerate бесполезен. SQL-файл читается глазами и прогоняется `psql -f` на копии.
- Bootstrap выполняется идемпотентно до файлов:
  ```sql
  CREATE TABLE IF NOT EXISTS schema_migrations (
    version INT PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(), duration_ms INT,
    adopted BOOLEAN NOT NULL DEFAULT false);
  CREATE OR REPLACE FUNCTION as_migration_guard(v int) RETURNS void LANGUAGE plpgsql AS $$
  BEGIN
    IF EXISTS (SELECT 1 FROM schema_migrations WHERE version = v) THEN
      RAISE EXCEPTION 'migration % already applied', v;
    END IF;
  END $$;
  ```
- Сессионный `pg_advisory_lock(771000)` не даёт двум раннерам работать одновременно.
- **Принятие baseline:** если `schema_migrations` пуста, а `jobs` существует, раннер сверяет колонки baseline через `information_schema` и пишет версию 1 с `adopted=true`, не выполняя файл.
- Каждый файл идёт одной транзакцией. Первая строка файла: `SELECT as_migration_guard(N);`. Строку в `schema_migrations` вставляет раннер. Если sha256 применённого файла изменился, раннер отказывается работать.
- При `APP_ENV=prod` перед первым неприменённым файлом выполняется `pg_dump -Fc -f $BACKUP_DIR/pre_migrate_<ts>.dump`. Если дамп не удался, миграции не применяются.
- `api`, `public` и воркеры при старте сверяют `max(version)` с последним файлом. Если отстаёт, процесс не стартует, а `/health/ready` отвечает 503 `migrations_pending`.
- Номера миграций выдаёт владелец контрактов (§10.1). После слияния файл неизменяем.
- **Перед 0003 и 0004 старые процессы (`queue/worker.py`, бот, дашборд) останавливаются**: миграции переименовывают `niche` → `niche_id`.

### 4.2 Миграции v1

**0001_baseline.sql** — `SELECT as_migration_guard(1);` + `scripts/init_db.sql` без изменений.

**0002_auth_settings.sql**
```sql
SELECT as_migration_guard(2);

CREATE TABLE users (
  id BIGSERIAL PRIMARY KEY,
  login TEXT NOT NULL UNIQUE CHECK (login ~ '^[a-z0-9][a-z0-9_.-]{2,31}$'),
  display_name TEXT NOT NULL,
  password_hash TEXT NOT NULL,                       -- argon2id
  role TEXT NOT NULL CHECK (role IN ('admin','operator','viewer')),
  tg_user_id BIGINT UNIQUE,                          -- кто может жать кнопки приёмки в боте
  is_active BOOLEAN NOT NULL DEFAULT true,
  failed_logins INT NOT NULL DEFAULT 0,
  locked_until TIMESTAMPTZ,
  last_login_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE TABLE sessions (
  id BIGSERIAL PRIMARY KEY,
  token_hash BYTEA NOT NULL UNIQUE,                  -- sha256(token); сам токен только в cookie
  csrf_token TEXT NOT NULL,
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ,
  ip_hash TEXT, user_agent TEXT);
CREATE INDEX ix_sessions_user_active ON sessions(user_id) WHERE revoked_at IS NULL;

CREATE TABLE api_tokens (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users(id),
  name TEXT NOT NULL, prefix TEXT NOT NULL,
  token_hash BYTEA NOT NULL UNIQUE,
  scopes TEXT[] NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ, expires_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ);
CREATE INDEX ix_api_tokens_user ON api_tokens(user_id);

CREATE TABLE audit_log (
  id BIGSERIAL PRIMARY KEY,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  actor_kind TEXT NOT NULL CHECK (actor_kind IN ('user','api_token','bot','system','webhook')),
  actor_user_id BIGINT REFERENCES users(id),
  action TEXT NOT NULL,                              -- 'job.review', 'secret.set', 'product.update'
  entity_type TEXT NOT NULL, entity_id TEXT,
  summary TEXT, diff JSONB,                          -- значения секретов вырезаются до записи
  request_id TEXT, ip_hash TEXT);
CREATE INDEX ix_audit_time ON audit_log(occurred_at DESC);
CREATE INDEX ix_audit_entity ON audit_log(entity_type, entity_id);

CREATE TABLE settings (                              -- только переопределения; дефолты в domain/settings_schema.py
  key TEXT PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{2,63}$'),
  value JSONB NOT NULL,
  updated_by BIGINT REFERENCES users(id),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE TABLE secrets (
  name TEXT PRIMARY KEY CHECK (name ~ '^[A-Z][A-Z0-9_]{2,63}$'),
  ciphertext BYTEA NOT NULL, key_id TEXT NOT NULL, last4 TEXT,
  updated_by BIGINT REFERENCES users(id),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE TABLE provider_configs (
  key TEXT PRIMARY KEY CHECK (key ~ '^[a-z0-9_]{2,63}$'),
  kind TEXT NOT NULL CHECK (kind IN ('llm','tts','image','asr','vision','faces')),
  enabled BOOLEAN NOT NULL DEFAULT true,
  is_default BOOLEAN NOT NULL DEFAULT false,
  params JSONB NOT NULL DEFAULT '{}',
  secret_name TEXT,
  updated_by BIGINT REFERENCES users(id),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE UNIQUE INDEX uq_provider_default_per_kind ON provider_configs(kind) WHERE is_default;

CREATE TABLE alerts (
  id BIGSERIAL PRIMARY KEY,
  code TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('info','warning','critical')),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','acked','resolved')),
  dedup_key TEXT NOT NULL, title TEXT NOT NULL, details JSONB NOT NULL DEFAULT '{}',
  opened_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ, acked_by BIGINT REFERENCES users(id), notified_at TIMESTAMPTZ);
CREATE UNIQUE INDEX uq_alerts_open ON alerts(dedup_key) WHERE status <> 'resolved';
CREATE INDEX ix_alerts_status_time ON alerts(status, opened_at DESC);

CREATE TABLE idempotency_keys (
  key TEXT NOT NULL, user_id BIGINT NOT NULL REFERENCES users(id),
  response JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, key));
```

**0003_catalog.sql** — продукты, кусты, ниши, каналы, ассеты, баннеры, кампании, инциденты.
```sql
SELECT as_migration_guard(3);

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM channels
             WHERE id !~ '^[a-z0-9][a-z0-9_-]{1,39}$' OR niche !~ '^[a-z0-9][a-z0-9_-]{1,39}$'
                OR cluster !~ '^[a-z0-9][a-z0-9_-]{1,39}$' OR platform NOT IN ('youtube','vk')) THEN
    RAISE EXCEPTION 'channels: id/niche/cluster must be latin slugs, platform youtube|vk (fix before 0003)';
  END IF;
  IF EXISTS (SELECT 1 FROM jobs WHERE niche !~ '^[a-z0-9][a-z0-9_-]{1,39}$') THEN
    RAISE EXCEPTION 'jobs.niche must be latin slug (fix before 0003)';
  END IF;
END $$;

CREATE TABLE products (
  id TEXT PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9_-]{1,39}$'),       -- umka, gamekeys_tma, vpn_bot
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('web_service','telegram_bot','telegram_mini_app','other')),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  landing_url TEXT,
  redirect_host TEXT,                                -- u.umka.chat
  redirect_mode TEXT NOT NULL DEFAULT 'landing_split'
    CHECK (redirect_mode IN ('landing_split','direct_site','direct_telegram')),
  telegram_bot_username TEXT CHECK (telegram_bot_username ~ '^[A-Za-z0-9_]{5,32}$'),
  tma_short_name TEXT CHECK (tma_short_name ~ '^[A-Za-z0-9_]{3,64}$'),
  telegram_link_style TEXT NOT NULL DEFAULT 'tg_scheme_with_fallback'
    CHECK (telegram_link_style IN ('tg_scheme_with_fallback','https_tme')),
  utm_defaults JSONB NOT NULL DEFAULT '{}',
  brand JSONB NOT NULL DEFAULT '{}',                 -- {display_domain, primary_color, logo_asset_id}
  landing JSONB NOT NULL DEFAULT '{}',               -- {title, subtitle, button_telegram, button_site}
  webhook_secret_name TEXT,                          -- secrets.name, напр. PRODUCT_WEBHOOK_UMKA
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
-- legacy-продукт: старый код жёстко зашивал umka.chat / umka_bot / u.umka.chat
INSERT INTO products (id, name, kind, landing_url, redirect_host, telegram_bot_username, brand, landing)
VALUES ('umka', 'Умка', 'web_service', 'https://umka.chat/', 'u.umka.chat', 'umka_bot',
        '{"display_domain":"umka.chat"}',
        '{"title":"Бесплатный расклад ждёт тебя","subtitle":"Выбери, как удобнее продолжить","button_telegram":"Открыть в Telegram","button_site":"Продолжить на сайте"}')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE clusters (                              -- «куст» изоляции (ТЗ §10)
  id TEXT PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9_-]{1,39}$'),
  title TEXT NOT NULL, notes TEXT,
  youtube_api_key_secret TEXT,                       -- secrets.name, свой Cloud-проект на куст
  youtube_oauth_client_secret TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
INSERT INTO clusters (id, title) SELECT DISTINCT cluster, cluster FROM channels ON CONFLICT DO NOTHING;

CREATE TABLE niches (
  id TEXT PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9_-]{1,39}$'),
  label TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived','needs_import')),
  current_version INT NOT NULL DEFAULT 0,
  config JSONB NOT NULL DEFAULT '{}',                -- валидируется domain/niche_schema.NicheConfig
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE niche_versions (
  niche_id TEXT NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
  version INT NOT NULL,
  config JSONB NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('ui','yaml_import','migration')),
  created_by BIGINT REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (niche_id, version));
INSERT INTO niches (id, label, status)
  SELECT DISTINCT niche, niche, 'needs_import' FROM (SELECT niche FROM channels UNION SELECT niche FROM jobs) s
  ON CONFLICT DO NOTHING;

ALTER TABLE channels RENAME COLUMN niche TO niche_id;
ALTER TABLE channels
  ADD COLUMN title TEXT,
  ADD COLUMN platform_channel_ref TEXT,              -- UC… для YouTube, -<group_id> для VK
  ADD COLUMN url TEXT,
  ADD COLUMN status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('warming','active','paused','sanctioned','terminated','retired')),
  ADD COLUMN replaced_by TEXT REFERENCES channels(id),
  ADD COLUMN default_product_id TEXT REFERENCES products(id),
  ADD COLUMN profile JSONB NOT NULL DEFAULT '{}',    -- {tts_provider, tts_voice, banner_template_id, visual_style_suffix, seed_family}
  ADD COLUMN publishing_plan JSONB NOT NULL DEFAULT '{}', -- {videos_per_day: 3}
  ADD COLUMN advanced_features BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN subscribers BIGINT,
  ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ADD CONSTRAINT ck_channels_id CHECK (id ~ '^[a-z0-9][a-z0-9_-]{1,39}$'),
  ADD CONSTRAINT ck_channels_platform CHECK (platform IN ('youtube','vk')),
  ADD CONSTRAINT fk_channels_niche FOREIGN KEY (niche_id) REFERENCES niches(id),
  ADD CONSTRAINT fk_channels_cluster FOREIGN KEY (cluster) REFERENCES clusters(id);
CREATE UNIQUE INDEX uq_channels_ref ON channels(platform, platform_channel_ref) WHERE platform_channel_ref IS NOT NULL;
CREATE INDEX ix_channels_cluster ON channels(cluster);

CREATE TABLE channel_incidents (                     -- журнал диагностики охвата/санкций (ТЗ §10)
  id BIGSERIAL PRIMARY KEY,
  channel_id TEXT NOT NULL REFERENCES channels(id),
  kind TEXT NOT NULL CHECK (kind IN ('reach_drop','sanction','strike','claim','unique_phrase_test','freeze','note')),
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  details JSONB NOT NULL DEFAULT '{}', note TEXT,
  created_by BIGINT REFERENCES users(id));
CREATE INDEX ix_incidents_channel ON channel_incidents(channel_id, occurred_at DESC);

CREATE TABLE assets (
  id BIGSERIAL PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('font','logo','music','image','broll','watermark_ref')),
  name TEXT NOT NULL, path TEXT NOT NULL,             -- относительно MEDIA_ROOT
  bytes BIGINT NOT NULL, mime TEXT NOT NULL,
  license_note TEXT NOT NULL,
  meta JSONB NOT NULL DEFAULT '{}',                  -- шрифт: {"cyrillic_ok": true, "missing": []}
  created_by BIGINT REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), deleted_at TIMESTAMPTZ);
CREATE INDEX ix_assets_kind ON assets(kind) WHERE deleted_at IS NULL;

CREATE TABLE banner_templates (                      -- геометрия и тайминг (форма config/banner.yaml)
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  layout JSONB NOT NULL,                             -- валидируется domain/banner_schema.BannerLayout
  version INT NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE TABLE campaigns (
  id BIGSERIAL PRIMARY KEY,
  product_id TEXT NOT NULL REFERENCES products(id),
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','paused','ended')),
  banner_template_id BIGINT NOT NULL REFERENCES banner_templates(id),
  allocation TEXT NOT NULL DEFAULT 'weighted' CHECK (allocation IN ('weighted','round_robin','manual')),
  priority SMALLINT NOT NULL DEFAULT 100,
  targeting JSONB NOT NULL DEFAULT '{}',             -- {channel_ids:[], niche_ids:[], platforms:[], kinds:[]}
  starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ, notes TEXT,
  created_by BIGINT REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX ix_campaigns_active ON campaigns(priority) WHERE status = 'active';
CREATE INDEX ix_campaigns_product ON campaigns(product_id);

CREATE TABLE banner_variants (
  id BIGSERIAL PRIMARY KEY,
  campaign_id BIGINT NOT NULL REFERENCES campaigns(id),
  code TEXT NOT NULL CHECK (code ~ '^[A-Za-z0-9_-]{1,16}$'),   -- A, B, C
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('draft','active','paused','archived')),
  weight SMALLINT NOT NULL DEFAULT 50 CHECK (weight BETWEEN 0 AND 1000),
  micro_brand_text TEXT,                             -- слой 1
  offer_text TEXT NOT NULL,                          -- слой 2, строка 1
  offer_text_2 TEXT,                                 -- слой 2, строка 2
  voice_cta_text TEXT,                               -- произносимый CTA, уходит в сценарий
  pinned_comment_template TEXT, description_cta_template TEXT,
  style_overrides JSONB NOT NULL DEFAULT '{}',       -- {background_opacity, bg_color, text_color, corner_radius_px}
  layout_overrides JSONB NOT NULL DEFAULT '{}',      -- частичная геометрия поверх шаблона (валидируется)
  show_code_in_frame BOOLEAN NOT NULL DEFAULT false, -- «u.umka.chat/k7m2q» вместо голого домена (решает владелец)
  ad_marking JSONB NOT NULL DEFAULT '{}',            -- {enabled:false, erid, advertiser_text} — до 01.01.2027
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (campaign_id, code));
```

**0004_queue.sql** — задачи, расписания, воркеры, артефакты, рецензии, расширение `jobs`.
```sql
SELECT as_migration_guard(4);

ALTER TABLE jobs RENAME COLUMN niche TO niche_id;
ALTER TABLE jobs
  ADD COLUMN kind TEXT NOT NULL DEFAULT 'generate' CHECK (kind IN ('generate','clip')),
  ADD COLUMN stage TEXT,
  ADD COLUMN topic TEXT,
  ADD COLUMN product_id TEXT REFERENCES products(id),
  ADD COLUMN campaign_id BIGINT REFERENCES campaigns(id),
  ADD COLUMN banner_variant_id BIGINT REFERENCES banner_variants(id),
  ADD COLUMN trend_brief_id BIGINT REFERENCES trend_briefs(id),
  ADD COLUMN niche_version INT,
  ADD COLUMN config_snapshot JSONB NOT NULL DEFAULT '{}', -- ниша+шаблон+вариант+провайдеры+пресет на момент старта
  ADD COLUMN duration_sec NUMERIC(7,2),
  ADD COLUMN cost_usd NUMERIC(12,6) NOT NULL DEFAULT 0,   -- кеш суммы generation_events
  ADD COLUMN priority SMALLINT NOT NULL DEFAULT 100,
  ADD COLUMN scheduled_for TIMESTAMPTZ,
  ADD COLUMN parent_job_id BIGINT REFERENCES jobs(id),    -- VK-перерендер, клон с правками
  ADD COLUMN qc_failed_checks TEXT[] NOT NULL DEFAULT '{}',
  ADD COLUMN qc_warnings JSONB NOT NULL DEFAULT '[]',
  ADD COLUMN review_comment TEXT,
  ADD COLUMN rework_from_stage TEXT,
  ADD COLUMN error_class TEXT, ADD COLUMN error TEXT,
  ADD COLUMN fingerprint JSONB,                           -- dHash ключевых кадров
  ADD COLUMN created_by BIGINT REFERENCES users(id),
  ADD COLUMN deleted_at TIMESTAMPTZ,
  ADD CONSTRAINT fk_jobs_niche FOREIGN KEY (niche_id) REFERENCES niches(id);

-- нормализация смешанного reject_reason (QC-склейки, 'rework_requested', 'tehnicheskiy_sboy')
UPDATE jobs SET qc_failed_checks = ARRAY[reject_reason], reject_reason = NULL
  WHERE status = 'qc_failed' AND reject_reason IS NOT NULL;
UPDATE jobs SET reject_reason = NULL WHERE reject_reason = 'rework_requested';
UPDATE jobs SET status = 'failed', error_class = 'legacy_max_attempts', reject_reason = NULL
  WHERE status = 'rejected' AND reject_reason = 'tehnicheskiy_sboy';
-- старые статусы -> (status, stage)
UPDATE jobs SET
  stage = CASE status WHEN 'queued' THEN 'script' WHEN 'script_ready' THEN 'voice'
                      WHEN 'voiced' THEN 'visuals' WHEN 'rendered' THEN 'qc' ELSE NULL END,
  status = CASE status WHEN 'script_ready' THEN 'processing' WHEN 'voiced' THEN 'processing'
                       WHEN 'rendered' THEN 'processing' WHEN 'qc_passed' THEN 'pending_review'
                       WHEN 'ready_to_publish' THEN 'approved' ELSE status END;
ALTER TABLE jobs ADD CONSTRAINT ck_jobs_status CHECK (status IN
  ('draft','queued','processing','qc_failed','pending_review','rework','approved',
   'published','rejected','failed','cancelled'));
CREATE INDEX ix_jobs_status_updated ON jobs(status, updated_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX ix_jobs_campaign ON jobs(campaign_id);
CREATE INDEX ix_jobs_variant ON jobs(banner_variant_id);
CREATE INDEX ix_jobs_product ON jobs(product_id);
CREATE INDEX ix_jobs_created ON jobs(created_at DESC);

CREATE TABLE tasks (
  id BIGSERIAL PRIMARY KEY,
  kind TEXT NOT NULL,                                -- job_stage, source_prepare, metrics_poll, …
  resource_class TEXT NOT NULL CHECK (resource_class IN ('heavy','light')),
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
  priority SMALLINT NOT NULL DEFAULT 100,            -- меньше = раньше
  run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
  job_id BIGINT REFERENCES jobs(id) ON DELETE CASCADE,
  source_id BIGINT,                                  -- FK в 0005
  stage TEXT,
  payload JSONB NOT NULL DEFAULT '{}',
  dedup_key TEXT,
  attempts INT NOT NULL DEFAULT 0, max_attempts INT NOT NULL DEFAULT 3,
  locked_by TEXT, locked_at TIMESTAMPTZ, heartbeat_at TIMESTAMPTZ,
  cancel_requested BOOLEAN NOT NULL DEFAULT false,
  progress NUMERIC(5,2),
  started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
  error_class TEXT, last_error TEXT,                  -- хвост 4 КБ; полный лог MEDIA_ROOT/logs/tasks/<id>.log
  defer_reason TEXT,                                  -- disk_low | load_high | mem_low | budget_exhausted | quota_exhausted
  result JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE UNIQUE INDEX uq_tasks_dedup_active ON tasks(dedup_key) WHERE status IN ('queued','running');
CREATE INDEX ix_tasks_pick ON tasks(resource_class, priority, run_after, id) WHERE status = 'queued';
CREATE INDEX ix_tasks_running ON tasks(heartbeat_at) WHERE status = 'running';
CREATE INDEX ix_tasks_job ON tasks(job_id, created_at);
CREATE INDEX ix_tasks_finished ON tasks(finished_at) WHERE status IN ('succeeded','failed','cancelled');

CREATE TABLE schedules (
  name TEXT PRIMARY KEY CHECK (name ~ '^[a-z0-9_]{2,63}$'),
  task_kind TEXT NOT NULL,
  resource_class TEXT NOT NULL CHECK (resource_class IN ('heavy','light')),
  payload JSONB NOT NULL DEFAULT '{}',
  interval_sec INT NOT NULL CHECK (interval_sec >= 30),
  anchor_local_time TIME,                            -- для суточных: время в business_timezone
  next_run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  enabled BOOLEAN NOT NULL DEFAULT true,
  last_task_id BIGINT);

CREATE TABLE workers (
  id TEXT PRIMARY KEY,                               -- <host>-<pid>-<class>
  resource_class TEXT NOT NULL, hostname TEXT NOT NULL, pid INT NOT NULL, version TEXT,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(), heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  current_task_id BIGINT,
  state TEXT NOT NULL DEFAULT 'idle');               -- idle | busy | deferred:<reason> | stopping

CREATE TABLE artifacts (
  id BIGSERIAL PRIMARY KEY,
  job_id BIGINT REFERENCES jobs(id) ON DELETE CASCADE,
  source_id BIGINT,                                  -- FK в 0005
  kind TEXT NOT NULL,  -- script_json words_json voice scene_image scenes_video banner_png subtitles_ass
                       -- final preview poster author_voice edl_json crop_cmd qc_report
  idx SMALLINT NOT NULL DEFAULT 0,
  path TEXT NOT NULL,                                -- относительно MEDIA_ROOT
  bytes BIGINT, sha256 TEXT,
  input_hash TEXT NOT NULL,                          -- hash(video_uid, stage, входы, template_version)
  stage TEXT, is_master BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), deleted_at TIMESTAMPTZ,
  CHECK (job_id IS NOT NULL OR source_id IS NOT NULL));
CREATE UNIQUE INDEX uq_artifacts_live ON artifacts(job_id, kind, idx, input_hash)
  WHERE deleted_at IS NULL AND job_id IS NOT NULL;
CREATE INDEX ix_artifacts_job_kind ON artifacts(job_id, kind) WHERE deleted_at IS NULL;

CREATE TABLE review_decisions (
  id BIGSERIAL PRIMARY KEY,
  job_id BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  decision TEXT NOT NULL CHECK (decision IN ('approve','rework','reject','override_qc','undo')),
  reject_reason TEXT, rework_from_stage TEXT, comment TEXT,
  undoes_id BIGINT REFERENCES review_decisions(id),
  decided_by BIGINT REFERENCES users(id),
  decided_via TEXT NOT NULL CHECK (decided_via IN ('web','telegram','api')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX ix_review_job ON review_decisions(job_id, created_at);

-- незавершённые задачи старого воркера -> новая очередь
INSERT INTO tasks (kind, resource_class, job_id, stage, dedup_key, priority)
  SELECT 'job_stage', CASE WHEN stage IN ('render','qc') THEN 'heavy' ELSE 'light' END, id, stage,
         'job:' || id || ':' || stage || ':0', 50
  FROM jobs WHERE status IN ('queued','processing') AND stage IS NOT NULL;

ALTER TABLE budget_state
  ADD COLUMN reserved_usd NUMERIC(12,6) NOT NULL DEFAULT 0,
  ADD COLUMN alert_threshold_pct SMALLINT NOT NULL DEFAULT 80,
  ADD COLUMN pause_reason TEXT, ADD COLUMN alerted_at TIMESTAMPTZ;

ALTER TABLE generation_events
  ALTER COLUMN job_id DROP NOT NULL,
  ADD COLUMN task_id BIGINT REFERENCES tasks(id),
  ADD COLUMN source_id BIGINT,                       -- FK в 0005
  ADD COLUMN trend_brief_id BIGINT REFERENCES trend_briefs(id),
  ADD COLUMN status TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','error')),
  ADD COLUMN error TEXT,
  ADD COLUMN cpu_ms INT,
  ADD CONSTRAINT ck_genev_subject CHECK (job_id IS NOT NULL OR task_id IS NOT NULL
                                         OR source_id IS NOT NULL OR trend_brief_id IS NOT NULL);
CREATE INDEX ix_genev_provider_time ON generation_events(provider, created_at);
```
Колонки `jobs.locked_by`, `locked_at`, `review_sent_at` помечаются устаревшими и удаляются в 0010. Отметка о карточке в Telegram переезжает в `jobs.stage_payload.review_card`.

**0005_clipping.sql** — загрузки, источники, права, транскрипты, кандидаты.
```sql
SELECT as_migration_guard(5);

CREATE TABLE uploads (                               -- tus-сессии
  id TEXT PRIMARY KEY CHECK (id ~ '^[a-f0-9]{32}$'),
  user_id BIGINT NOT NULL REFERENCES users(id),
  purpose TEXT NOT NULL CHECK (purpose IN ('source','asset','rights_doc')),
  filename TEXT NOT NULL, filetype TEXT,
  length_bytes BIGINT NOT NULL, received_bytes BIGINT NOT NULL DEFAULT 0,
  path TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'uploading'
    CHECK (status IN ('uploading','complete','consumed','expired','aborted')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL);
CREATE INDEX ix_uploads_expiry ON uploads(expires_at) WHERE status IN ('uploading','complete');

CREATE TABLE sources (
  id BIGSERIAL PRIMARY KEY,
  title TEXT NOT NULL,
  origin TEXT NOT NULL CHECK (origin IN ('upload','url')),
  origin_url TEXT, upload_id TEXT REFERENCES uploads(id),
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN
    ('queued','downloading','preparing','transcribing','selecting','ready','failed','archived')),
  error TEXT,
  language TEXT NOT NULL DEFAULT 'ru' CHECK (language ~ '^[a-z]{2}$'),
  niche_id TEXT REFERENCES niches(id),
  duration_sec NUMERIC(9,2), width INT, height INT, fps NUMERIC(6,3), video_codec TEXT,
  has_audio BOOLEAN, file_bytes BIGINT, sha256 TEXT,
  original_path TEXT, proxy_path TEXT, audio_path TEXT, peaks_path TEXT,
  storyboard JSONB,                                  -- {sheets, cols, rows, tile_w, tile_h, interval_sec}
  scenes JSONB,                                      -- [[start,end],...]; по окнам кандидатов или целиком по запросу
  watermark_suspect BOOLEAN NOT NULL DEFAULT false, watermark_detail JSONB,
  options JSONB NOT NULL DEFAULT '{}',               -- {asr_provider, target_clip_sec, max_candidates}
  original_retain_until DATE, proxy_retain_until DATE,
  created_by BIGINT REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at TIMESTAMPTZ,
  CHECK ((origin = 'url' AND origin_url IS NOT NULL) OR (origin = 'upload' AND upload_id IS NOT NULL)));
CREATE INDEX ix_sources_status ON sources(status) WHERE deleted_at IS NULL;

CREATE TABLE source_rights (                         -- гейт: без записи источник не создаётся
  source_id BIGINT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
  rights_type TEXT NOT NULL CHECK (rights_type IN
    ('own','licensed_written','licensed_platform','cc_by','public_domain')),
  owner_name TEXT NOT NULL,
  owner_channel_url TEXT,
  proof_note TEXT NOT NULL CHECK (length(proof_note) >= 3),   -- что именно разрешено, ссылка на переписку
  proof_file_path TEXT,                              -- MEDIA_ROOT/rights/<source_id>/…
  allowlist_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (allowlist_status IN ('not_needed','requested','allowlisted','unknown')),
  attribution_text TEXT,                             -- обязательна при rights_type <> 'own'
  valid_until DATE,
  recorded_by BIGINT REFERENCES users(id),
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (rights_type = 'own' OR attribution_text IS NOT NULL));

ALTER TABLE tasks ADD CONSTRAINT fk_tasks_source FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE;
ALTER TABLE artifacts ADD CONSTRAINT fk_artifacts_source FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE;
ALTER TABLE generation_events ADD CONSTRAINT fk_genev_source FOREIGN KEY (source_id) REFERENCES sources(id);
CREATE INDEX ix_tasks_source ON tasks(source_id, created_at) WHERE source_id IS NOT NULL;

CREATE TABLE transcripts (
  id BIGSERIAL PRIMARY KEY,
  source_id BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  provider_key TEXT NOT NULL, model TEXT, language TEXT NOT NULL,
  path TEXT NOT NULL,                                -- transcript.json.gz: {words:[{w,s,e,sid}], sentences:[{sid,s,e,text}], vad:[[s,e]]}
  words_count INT NOT NULL, sentences_count INT NOT NULL,
  duration_sec NUMERIC(9,2), rtfx NUMERIC(8,2),
  cost_usd NUMERIC(12,6) NOT NULL DEFAULT 0,
  is_current BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE UNIQUE INDEX uq_transcripts_current ON transcripts(source_id) WHERE is_current;

CREATE TABLE clip_candidates (
  id BIGSERIAL PRIMARY KEY,
  source_id BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  start_sid INT, end_sid INT, hook_sid INT,          -- ID предложений от LLM (NULL для ручных)
  start_sec NUMERIC(9,2) NOT NULL, end_sec NUMERIC(9,2) NOT NULL CHECK (end_sec > start_sec),
  title TEXT, hook_text TEXT, hook_type TEXT, reason TEXT, transcript_excerpt TEXT,
  llm_scores JSONB NOT NULL DEFAULT '{}',            -- {hook, standalone, payoff, emotion, niche_fit, novelty}
  features JSONB NOT NULL DEFAULT '{}',              -- {speech_rate_wps, dead_air_ratio, first_word_latency, …}
  trend_prior NUMERIC(4,3), final_score NUMERIC(4,3),
  content_flags TEXT[] NOT NULL DEFAULT '{}',        -- medical_claim, guarantee_claim, profanity, …
  needs_context BOOLEAN NOT NULL DEFAULT false,
  status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','approved','dismissed','used')),
  created_by_kind TEXT NOT NULL DEFAULT 'auto' CHECK (created_by_kind IN ('auto','manual')),
  operator_edits JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX ix_clipcand_source ON clip_candidates(source_id, final_score DESC NULLS LAST);

ALTER TABLE jobs
  ADD COLUMN source_id BIGINT REFERENCES sources(id),
  ADD COLUMN clip_candidate_id BIGINT REFERENCES clip_candidates(id),
  ADD COLUMN clip_start_sec NUMERIC(9,2), ADD COLUMN clip_end_sec NUMERIC(9,2),
  ADD COLUMN reframe_mode TEXT CHECK (reframe_mode IN ('auto','track','center','fit_blur')),
  ADD COLUMN author_layer JSONB,                     -- ввод оператора или черновик LLM (§6.3.8)
  ADD COLUMN author_share NUMERIC(4,3);
ALTER TABLE clip_candidates ADD COLUMN job_id BIGINT REFERENCES jobs(id);
CREATE INDEX ix_jobs_source ON jobs(source_id) WHERE source_id IS NOT NULL;
```

**0006_publishing_attribution.sql**
```sql
SELECT as_migration_guard(6);

CREATE TABLE tracking_links (
  id BIGSERIAL PRIMARY KEY,
  code TEXT NOT NULL UNIQUE CHECK (code ~ '^[A-Za-z0-9_-]{2,64}$'),  -- алфавит Telegram start-параметра
  product_id TEXT NOT NULL REFERENCES products(id),
  level TEXT NOT NULL CHECK (level IN ('package','publication','channel','campaign','manual')),
  job_id BIGINT REFERENCES jobs(id),
  publication_id BIGINT REFERENCES publications(id),
  channel_id TEXT REFERENCES channels(id),
  campaign_id BIGINT REFERENCES campaigns(id),
  banner_variant_id BIGINT REFERENCES banner_variants(id),
  platform TEXT CHECK (platform IN ('youtube','vk')),
  target_override_url TEXT, label TEXT,
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX ix_tl_publication ON tracking_links(publication_id);
CREATE INDEX ix_tl_job ON tracking_links(job_id);
CREATE INDEX ix_tl_channel ON tracking_links(channel_id);
CREATE INDEX ix_tl_campaign ON tracking_links(campaign_id);

CREATE TABLE upload_packages (
  id BIGSERIAL PRIMARY KEY,
  job_id BIGINT NOT NULL REFERENCES jobs(id),
  channel_id TEXT NOT NULL REFERENCES channels(id),
  platform TEXT NOT NULL CHECK (platform IN ('youtube','vk')),
  status TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready','downloaded','published','superseded')),
  title TEXT NOT NULL, description TEXT NOT NULL, hashtags TEXT[] NOT NULL DEFAULT '{}',
  pinned_comment TEXT, audio_note TEXT, related_video_hint TEXT,
  checklist JSONB NOT NULL DEFAULT '[]',             -- [{code, done, done_at, done_by}]
  tracking_link_id BIGINT NOT NULL REFERENCES tracking_links(id),
  final_artifact_id BIGINT NOT NULL REFERENCES artifacts(id),
  downloaded_at TIMESTAMPTZ,
  edited_by BIGINT REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE UNIQUE INDEX uq_pkg_live_per_job ON upload_packages(job_id) WHERE status IN ('ready','downloaded');
CREATE INDEX ix_pkg_status_channel ON upload_packages(status, channel_id);

ALTER TABLE publications
  ADD COLUMN package_id BIGINT REFERENCES upload_packages(id),
  ADD COLUMN url TEXT, ADD COLUMN canonical_url TEXT,
  ADD COLUMN platform_kind TEXT CHECK (platform_kind IN ('short','clip','video')),
  ADD COLUMN status TEXT NOT NULL DEFAULT 'live' CHECK (status IN
     ('live','unlisted','private','removed_by_platform','deleted_by_us')),
  ADD COLUMN status_reason TEXT,
  ADD COLUMN has_claim BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN verify_status TEXT NOT NULL DEFAULT 'pending'
     CHECK (verify_status IN ('pending','ok','channel_mismatch','not_found','unverifiable')),
  ADD COLUMN verify_detail TEXT,
  ADD COLUMN product_id TEXT REFERENCES products(id),
  ADD COLUMN campaign_id BIGINT REFERENCES campaigns(id),
  ADD COLUMN banner_variant_id BIGINT REFERENCES banner_variants(id),
  ADD COLUMN registered_by BIGINT REFERENCES users(id),
  ADD COLUMN next_poll_at TIMESTAMPTZ, ADD COLUMN poll_step SMALLINT NOT NULL DEFAULT 0,
  ADD COLUMN last_polled_at TIMESTAMPTZ,
  ADD COLUMN latest_views BIGINT, ADD COLUMN latest_likes BIGINT, ADD COLUMN latest_comments BIGINT,
  ADD COLUMN latest_engaged_views BIGINT,
  ADD COLUMN retention_3s NUMERIC(5,4), ADD COLUMN avg_pct_viewed NUMERIC(6,4),
  ADD COLUMN views_24h BIGINT, ADD COLUMN views_72h BIGINT, ADD COLUMN views_7d BIGINT,
  ADD COLUMN deleted_at TIMESTAMPTZ;
CREATE UNIQUE INDEX uq_pub_platform_video ON publications(platform, platform_video_id)
  WHERE platform_video_id IS NOT NULL AND deleted_at IS NULL;
CREATE UNIQUE INDEX uq_pub_job_platform ON publications(job_id, platform) WHERE deleted_at IS NULL;  -- 1 ролик = 1 канал на площадке
CREATE INDEX ix_pub_poll ON publications(next_poll_at) WHERE status = 'live' AND deleted_at IS NULL;
CREATE INDEX ix_pub_published ON publications(published_at DESC);

CREATE TABLE clicks (
  id BIGSERIAL PRIMARY KEY,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  code TEXT NOT NULL,
  tracking_link_id BIGINT REFERENCES tracking_links(id),
  route TEXT NOT NULL CHECK (route IN ('landing','to_telegram','to_site','direct','unknown_code')),
  product_id TEXT, publication_id BIGINT, job_id BIGINT, channel_id TEXT,     -- денормализовано на вставке
  campaign_id BIGINT, banner_variant_id BIGINT, platform TEXT,
  visitor_key TEXT NOT NULL,                         -- hmac(REDIRECT_SALT, day|ip|ua)[:32]; сырой IP не храним
  user_agent TEXT, referer_host TEXT,
  is_bot BOOLEAN NOT NULL DEFAULT false);
CREATE INDEX ix_clicks_time ON clicks(occurred_at);
CREATE INDEX ix_clicks_link_time ON clicks(tracking_link_id, occurred_at);
CREATE INDEX ix_clicks_pub ON clicks(publication_id, occurred_at);

CREATE TABLE conversions (
  id BIGSERIAL PRIMARY KEY,
  product_id TEXT NOT NULL REFERENCES products(id),
  external_event_id TEXT NOT NULL CHECK (length(external_event_id) <= 128),
  event_type TEXT NOT NULL CHECK (event_type IN
     ('bot_start','tma_open','site_visit','signup','trial_start','lead','first_payment','payment','refund')),
  occurred_at TIMESTAMPTZ NOT NULL, received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  tag TEXT, via_landing BOOLEAN NOT NULL DEFAULT false,      -- суффикс _l у start-параметра
  tracking_link_id BIGINT REFERENCES tracking_links(id),
  publication_id BIGINT, job_id BIGINT, channel_id TEXT, campaign_id BIGINT, banner_variant_id BIGINT, platform TEXT,
  user_ref_hash TEXT,                                -- sha256(product|user_ref); PII не храним
  amount_minor BIGINT, currency CHAR(3), meta JSONB NOT NULL DEFAULT '{}',
  UNIQUE (product_id, external_event_id));
CREATE INDEX ix_conv_time ON conversions(occurred_at);
CREATE INDEX ix_conv_link_type ON conversions(tracking_link_id, event_type, occurred_at);
CREATE INDEX ix_conv_pub ON conversions(publication_id, occurred_at);

ALTER TABLE metrics_snapshots
  ADD COLUMN source TEXT NOT NULL DEFAULT 'yt_data'
    CHECK (source IN ('yt_data','yt_analytics','vk_api','ytdlp','manual')),
  ADD COLUMN shares BIGINT, ADD COLUMN avg_view_duration_sec NUMERIC(8,2),
  ADD COLUMN subscribers_gained INT, ADD COLUMN shorts_feed_share NUMERIC(5,4), ADD COLUMN raw JSONB;
CREATE INDEX ix_metrics_pub_time ON metrics_snapshots(publication_id, captured_at DESC);

CREATE TABLE retention_curves (
  id BIGSERIAL PRIMARY KEY,
  publication_id BIGINT NOT NULL REFERENCES publications(id),
  captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  points JSONB NOT NULL,                             -- [[ratio, audience_watch_ratio, relative_retention_performance], …] 100 точек
  retention_3s NUMERIC(5,4),
  failure_class TEXT CHECK (failure_class IN ('hook_drop','middle_sag','no_loop_lift','none')));
CREATE INDEX ix_ret_pub ON retention_curves(publication_id, captured_at DESC);

CREATE TABLE channel_snapshots (
  channel_id TEXT NOT NULL REFERENCES channels(id), captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  subscribers BIGINT, total_views BIGINT, video_count INT,
  PRIMARY KEY (channel_id, captured_at));

CREATE TABLE channel_oauth (                         -- опционально: YouTube Analytics
  channel_id TEXT PRIMARY KEY REFERENCES channels(id),
  refresh_token_secret TEXT NOT NULL,                -- secrets.name
  scopes TEXT[] NOT NULL, granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_ok_at TIMESTAMPTZ, last_error TEXT);

CREATE TABLE api_quota_usage (                       -- YouTube Data API: сброс в полночь PT
  api TEXT NOT NULL, quota_day DATE NOT NULL, cluster_id TEXT NOT NULL DEFAULT 'default',
  units INT NOT NULL DEFAULT 0,
  PRIMARY KEY (api, quota_day, cluster_id));

-- legacy: метки yt_<id> существующих публикаций -> трекинг-ссылки; attribution_events -> clicks
INSERT INTO tracking_links (code, product_id, level, publication_id, job_id, channel_id, platform)
  SELECT p.attribution_tag, 'umka', 'publication', p.id, p.job_id, p.channel_id, p.platform
  FROM publications p
  WHERE p.attribution_tag IS NOT NULL AND p.attribution_tag ~ '^[A-Za-z0-9_-]{2,64}$'
  ON CONFLICT (code) DO NOTHING;
INSERT INTO clicks (occurred_at, code, tracking_link_id, route, product_id, publication_id, job_id,
                    channel_id, platform, visitor_key, user_agent)
  SELECT a.created_at, a.tag, tl.id,
         CASE WHEN tl.id IS NULL THEN 'unknown_code' ELSE 'landing' END,
         'umka', tl.publication_id, tl.job_id, tl.channel_id, tl.platform,
         COALESCE(a.ip_hash, 'legacy'), a.user_agent
  FROM attribution_events a LEFT JOIN tracking_links tl ON tl.code = a.tag
  WHERE a.tag <> 'health';
-- attribution_events остаётся только для чтения; DROP в 0010
```

**0007_trends.sql**
```sql
SELECT as_migration_guard(7);

ALTER TABLE competitor_channels
  ADD COLUMN title TEXT, ADD COLUMN url TEXT, ADD COLUMN handle TEXT,
  ADD COLUMN niche_id TEXT REFERENCES niches(id),
  ADD COLUMN uploads_playlist_id TEXT,               -- UU…
  ADD COLUMN shorts_playlist_id TEXT,                -- UUSH… (недокументировано, с fallback)
  ADD COLUMN discovery_mode TEXT NOT NULL DEFAULT 'auto' CHECK (discovery_mode IN ('auto','rss','uush','uu')),
  ADD COLUMN last_discovery_at TIMESTAMPTZ, ADD COLUMN last_error TEXT,
  ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE TABLE competitor_videos (
  id BIGSERIAL PRIMARY KEY,
  competitor_id TEXT NOT NULL REFERENCES competitor_channels(id),
  platform TEXT NOT NULL, external_video_id TEXT NOT NULL,
  title TEXT, published_at TIMESTAMPTZ, duration_sec NUMERIC(8,2), is_short BOOLEAN,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  watch_until TIMESTAMPTZ,                           -- published + 7d
  next_poll_at TIMESTAMPTZ, poll_step SMALLINT NOT NULL DEFAULT 0,
  latest_views BIGINT, views_24h BIGINT, views_72h BIGINT,
  outlier_score NUMERIC(8,3), outlier_z NUMERIC(8,3),
  outlier_tier TEXT CHECK (outlier_tier IN ('noise','attention','format_works')),
  status TEXT NOT NULL DEFAULT 'watching' CHECK (status IN ('watching','done','analyzed','ignored')),
  thumb_path TEXT, analyzed_at TIMESTAMPTZ,
  UNIQUE (platform, external_video_id));
CREATE INDEX ix_cv_poll ON competitor_videos(next_poll_at) WHERE status = 'watching';
CREATE INDEX ix_cv_outlier ON competitor_videos(outlier_score DESC NULLS LAST);
CREATE INDEX ix_cv_competitor ON competitor_videos(competitor_id, published_at DESC);

CREATE TABLE competitor_baselines (                  -- медиана канала на том же возрасте
  competitor_id TEXT NOT NULL REFERENCES competitor_channels(id),
  age_bucket TEXT NOT NULL CHECK (age_bucket IN ('1h','3h','6h','12h','24h','48h','72h','7d','mature')),
  median_views NUMERIC NOT NULL, n INT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (competitor_id, age_bucket));

ALTER TABLE competitor_video_snapshots ADD COLUMN competitor_video_id BIGINT REFERENCES competitor_videos(id);
INSERT INTO competitor_videos (competitor_id, platform, external_video_id, status)
  SELECT DISTINCT s.competitor_id, c.platform, s.external_video_id, 'done'
  FROM competitor_video_snapshots s JOIN competitor_channels c ON c.id = s.competitor_id
  ON CONFLICT DO NOTHING;
UPDATE competitor_video_snapshots s SET competitor_video_id = v.id
  FROM competitor_videos v
  WHERE v.external_video_id = s.external_video_id AND v.competitor_id = s.competitor_id;
CREATE INDEX ix_cvs_video_time ON competitor_video_snapshots(competitor_video_id, captured_at);

ALTER TABLE trend_briefs
  ADD COLUMN competitor_video_id BIGINT REFERENCES competitor_videos(id),
  ADD COLUMN niche_id TEXT REFERENCES niches(id),
  ADD COLUMN status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','accepted','rejected','used')),
  ADD COLUMN summary TEXT, ADD COLUMN hook_text TEXT,
  ADD COLUMN topic_suggestions JSONB NOT NULL DEFAULT '[]',
  ADD COLUMN adaptation_ideas JSONB NOT NULL DEFAULT '[]',
  ADD COLUMN structure JSONB NOT NULL DEFAULT '{}',
  ADD COLUMN analysis_sources TEXT[] NOT NULL DEFAULT '{}',  -- metadata_llm, subtitles, scenedetect, vision
  ADD COLUMN cost_usd NUMERIC(12,6) NOT NULL DEFAULT 0,
  ADD COLUMN used_count INT NOT NULL DEFAULT 0,
  ADD COLUMN notes TEXT,
  ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
UPDATE trend_briefs SET status = CASE WHEN accepted IS TRUE THEN 'accepted'
                                      WHEN accepted IS FALSE THEN 'rejected' ELSE 'new' END;
CREATE INDEX ix_briefs_status ON trend_briefs(status, created_at DESC);

CREATE TABLE brief_field_stats (
  computed_at TIMESTAMPTZ NOT NULL, niche_id TEXT NOT NULL DEFAULT 'all',
  field TEXT NOT NULL, bucket TEXT NOT NULL,
  n INT NOT NULL, median_outlier NUMERIC, spearman_rho NUMERIC, ci_low NUMERIC, ci_high NUMERIC,
  PRIMARY KEY (computed_at, niche_id, field, bucket));
```

**0008_analytics.sql**
```sql
SELECT as_migration_guard(8);

CREATE TABLE stats_daily_views (
  day DATE NOT NULL,                                 -- бизнес-день (Europe/Moscow)
  publication_id BIGINT NOT NULL REFERENCES publications(id),
  views_delta BIGINT NOT NULL DEFAULT 0, engaged_views_delta BIGINT,
  likes_delta BIGINT NOT NULL DEFAULT 0, comments_delta BIGINT NOT NULL DEFAULT 0,
  views_eod BIGINT NOT NULL DEFAULT 0,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (day, publication_id));
CREATE INDEX ix_sdv_pub ON stats_daily_views(publication_id, day);

CREATE TABLE stats_daily_traffic (
  day DATE NOT NULL,
  tracking_link_id BIGINT NOT NULL REFERENCES tracking_links(id),
  landing_views BIGINT NOT NULL DEFAULT 0, unique_visitors BIGINT NOT NULL DEFAULT 0,
  to_telegram BIGINT NOT NULL DEFAULT 0, to_site BIGINT NOT NULL DEFAULT 0, direct BIGINT NOT NULL DEFAULT 0,
  bot_starts BIGINT NOT NULL DEFAULT 0, unique_bot_starts BIGINT NOT NULL DEFAULT 0,
  direct_bot_starts BIGINT NOT NULL DEFAULT 0,       -- старты без _l (не через лендинг)
  signups BIGINT NOT NULL DEFAULT 0, leads BIGINT NOT NULL DEFAULT 0,
  first_payments BIGINT NOT NULL DEFAULT 0, payments BIGINT NOT NULL DEFAULT 0,
  revenue_minor BIGINT NOT NULL DEFAULT 0,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (day, tracking_link_id));

CREATE VIEW v_publication_dims AS
  SELECT p.id AS publication_id, p.platform, p.channel_id, j.niche_id, j.kind,
         COALESCE(p.product_id, j.product_id) AS product_id,
         COALESCE(p.campaign_id, j.campaign_id) AS campaign_id,
         COALESCE(p.banner_variant_id, j.banner_variant_id) AS banner_variant_id,
         j.source_id, j.trend_brief_id,
         p.job_id, p.published_at, j.cost_usd AS job_cost_usd
  FROM publications p JOIN jobs j ON j.id = p.job_id
  WHERE p.deleted_at IS NULL;
```

**0009_seed.sql**
```sql
SELECT as_migration_guard(9);

INSERT INTO schedules (name, task_kind, resource_class, interval_sec, anchor_local_time, payload) VALUES
 ('task_reaper','task_reaper','light',60,NULL,'{}'),
 ('alerts_eval','alerts_eval','light',60,NULL,'{}'),
 ('metrics_poll','metrics_poll','light',300,NULL,'{}'),                -- берёт публикации с next_poll_at <= now()
 ('competitor_discovery','competitor_discovery','light',1800,NULL,'{}'),
 ('competitor_stats_poll','competitor_stats_poll','light',900,NULL,'{}'),
 ('outlier_compute','outlier_compute','light',1800,NULL,'{}'),
 ('yt_analytics_pull','yt_analytics_pull','light',86400,'07:00','{}'),
 ('channel_stats','channel_stats','light',86400,'06:00','{}'),
 ('stats_rollup_recent','stats_rollup','light',900,NULL,'{"days":3}'),
 ('stats_rollup_full','stats_rollup','light',86400,'03:30','{"days":35}'),
 ('cleanup','cleanup','light',86400,'04:00','{}'),
 ('baselines_refresh','baselines_refresh','light',604800,NULL,'{}'),
 ('brief_field_stats','brief_field_stats','light',604800,NULL,'{}'),
 ('rss_health_check','rss_health_check','light',86400,'05:00','{}')
ON CONFLICT (name) DO NOTHING;

INSERT INTO provider_configs (key, kind, enabled, is_default, params, secret_name) VALUES
 ('openai_compat','llm',true,true,'{"base_url":null,"model":"gpt-4o-mini","structured":"json_schema"}','LLM_API_KEY'),
 ('gigachat_lite','llm',false,false,'{"model":"GigaChat-Lite","scope":"GIGACHAT_API_PERS"}','GIGACHAT_AUTH_KEY'),
 ('mock_llm','llm',true,false,'{}',NULL),
 ('edge_tts','tts',true,true,'{"voice":"ru-RU-DmitryNeural","boundary":"WordBoundary"}',NULL),
 ('silero_v5_cis_base','tts',false,false,'{"speaker":"auto"}',NULL),
 ('fal_minimax_speech','tts',false,false,'{"model":"fal-ai/minimax/speech-2.8-hd"}','FAL_API_KEY'),
 ('mock_tts','tts',true,false,'{}',NULL),
 ('fal_flux_schnell','image',true,true,'{"model":"fal-ai/flux/schnell","price_usd":0.003}','FAL_API_KEY'),
 ('mock_image','image',true,false,'{}',NULL),
 ('gigaam_v3_onnx','asr',true,true,'{"model":"gigaam-v3-e2e-ctc","quantization":"int8","threads":2}',NULL),
 ('yandex_speechkit','asr',false,false,'{"mode":"deferred"}','YANDEX_SPEECHKIT_API_KEY'),
 ('openai_compat_asr','asr',false,false,'{"model":null}','ASR_CLOUD_API_KEY'),
 ('mock_asr','asr',true,false,'{}',NULL),
 ('yunet','faces',true,true,'{"model":"face_detection_yunet_2026may.onnx"}',NULL),
 ('gemini_flash_lite','vision',false,false,'{}','GEMINI_API_KEY')
ON CONFLICT (key) DO NOTHING;
```
Планировщик не ставит задачи тех видов, для которых ещё не зарегистрирован обработчик: в лог уходит одно предупреждение. Поэтому засев всех расписаний сразу безопасен при поэтапной сдаче.

**0010_cleanup.sql** (M7, отдельный релиз, только после того как старые процессы выведены из деплоя):
```sql
SELECT as_migration_guard(10);
ALTER TABLE jobs DROP COLUMN locked_by, DROP COLUMN locked_at, DROP COLUMN review_sent_at;
DROP TABLE attribution_events;
```

### 4.3 Словарь enum-значений (единый для БД, API, TS; `domain/enums.py`)
- `job.kind`: `generate | clip`
- `job.status`: `draft | queued | processing | qc_failed | pending_review | rework | approved | published | rejected | failed | cancelled`
- `job.stage`:
  - generate: `script → voice → visuals → render → qc`
  - clip: `clip_fetch (только URL) → clip_analyze → author_script → author_voice → render → qc`
  - после одобрения: `package`
- `review.decision`: `approve | rework | reject | override_qc | undo`
- `reject_reason`: коды из `models.REJECT_REASONS` (`hook_slabyy`, `vizual_artefakty`, `subtitry_rassinhron`, `banner_perekryvaet`, `zvuk_problema`, `zapreshchennaya_fraza`, `dublikat`, `ne_ta_dlitelnost`, `drugoe`) + `avtorskiy_sloy_slabyy`, `prava_somnitelny`, `povtor_shablona`. `tehnicheskiy_sboy` убирается: это `status=failed`
- `qc_check`: `silence | clipping | loudness | black_frames | frozen_frames | duration | subtitle_sync | author_share | banner_face_overlap | foreign_watermark | library_similarity | forbidden_phrase`
- `task.status`: `queued | running | succeeded | failed | cancelled`; `resource_class`: `heavy | light`
- `platform`: `youtube | vk`; `publication.status`: `live | unlisted | private | removed_by_platform | deleted_by_us`
- `package.status`: `ready | downloaded | published | superseded`
- `click.route`: `landing | to_telegram | to_site | direct | unknown_code`
- `conversion.event_type`: `bot_start | tma_open | site_visit | signup | trial_start | lead | first_payment | payment | refund`
- `rights_type`: `own | licensed_written | licensed_platform | cc_by | public_domain`
- `source.status`: `queued | downloading | preparing | transcribing | selecting | ready | failed | archived`
- `candidate.status`: `proposed | approved | dismissed | used`
- `role`: `admin | operator | viewer`

---

## 5. REST API

### 5.1 Общие соглашения
- **База** `/api/v1`, JSON UTF-8, ключи `snake_case`, только латиница. Параметры пути `{job_id}`, `{product_id}` и т. п.: Starlette принимает только `[a-zA-Z_][a-zA-Z0-9_]*`. Сегменты путей латиницей, через дефис. Теги OpenAPI и `operationId` (`<tag>_<verb>`) латиницей. Контрактный тест проходит `app.routes` и все свойства схем по `^[a-z0-9_]+$`.
- **Идентификаторы.** Слаги (`product_id`, `channel_id`, `niche_id`, `cluster_id`): `^[a-z0-9][a-z0-9_-]{1,39}$`. Остальное BIGINT.
- **Аутентификация.**
  - Cookie `as_session`: HttpOnly, Secure (в prod), SameSite=Lax. Сессия скользящая: idle 7 дней, абсолютный срок 30 дней.
  - Небезопасные методы требуют `X-CSRF-Token`, совпадающий с cookie `as_csrf` (double-submit). Значение фронт берёт из `GET /auth/me`.
  - Для машин: `Authorization: Bearer ast_<token>`, CSRF не нужен.
  - Защита от перебора: 10 неудач за 15 минут дают `locked_until`, плюс лимит 5 попыток в минуту на IP.
- **Роли.**
  - `viewer`: только GET, кроме секретов, настроек и пользователей.
  - `operator`: контент, приёмка, пакеты, публикации, продукты, кампании, ниши, каналы, тренды, источники.
  - `admin`: всё, плюс пользователи, секреты, провайдеры, настройки, расписания, бюджет, удаление публикаций, `override_qc`.
- **Ошибки.** Формат `{"error":{"code":"job_not_found","message":"Задача не найдена","details":{}}}`. Коды:
  - 400 `bad_request`, 401 `not_authenticated`;
  - 403 `forbidden` | `csrf_failed`;
  - 404 `<entity>_not_found`;
  - 409 `job_state_conflict` | `version_conflict` | `publication_exists` | `same_platform_crosspost_forbidden`;
  - 413 `payload_too_large`, 415, 422 `validation_error` (`details.errors=[{loc,msg,type}]`);
  - 423 `budget_paused`, 429 `rate_limited`, 503 `not_ready`, 507 `insufficient_storage`.
- **Пагинация.** Списки: `?limit=50&offset=0` (`limit ≤ 200`), ответ `{items,total,limit,offset}`. Журналы (аудит, клики, конверсии, generation-events): `?before_id=&limit=`, ответ `{items,next_before_id}`.
- **Сортировка и фильтры.** `?sort=-created_at` из белого списка эндпоинта. Мультифильтры повтором: `?status=pending_review&status=qc_failed`.
- **Время и деньги.**
  - Метки времени в ISO 8601 UTC с `Z`. Даты `date_from`/`date_to` в `YYYY-MM-DD` включительно, в `business_timezone`.
  - Деньги: расход провайдеров `*_usd` (number, 6 знаков), выручка продуктов `*_minor` (integer) + `currency`.
- **Конкуренция и повторы.** `expected_version` в теле PUT и PATCH конфигов, при несовпадении 409. Необязательный `Idempotency-Key` на `POST /jobs`, `/jobs/batch`, `/publications`.
- **Медиа.**
  - Отдаётся с Range (206/416). В prod API отвечает `X-Accel-Redirect: /_protected_media/<relpath>`, в dev `FileResponse`.
  - В JSON только непрозрачные URL `/api/v1/...`. Путей ФС и внешних URL картинок нет: миниатюры конкурентов кешируются локально.
- **Документация.** `/docs` и `/redoc` выключены: FastAPI грузит Swagger с cdn.jsdelivr.net. `GET /api/v1/openapi.json` открыт для кодогенерации фронта.
- **SSE.** `GET /api/v1/events/stream` (`text/event-stream`), `event:` = тип, `data:` = JSON, `ping` раз в 15 с. Источник `LISTEN as_events`, полезная нагрузка NOTIFY ≤ 8000 байт.

| event | data |
|---|---|
| `job_status` | `{job_id, status, stage}` |
| `job_progress` | `{job_id, task_id, stage, pct}` |
| `task_progress` | `{task_id, kind, pct, source_id?}` |
| `review_queue` | `{count}` |
| `packages` | `{ready, downloaded}` |
| `alert` | `{alert_id, code, severity}` |
| `worker` | `{worker_id, resource_class, state, task_id?}` |
| `budget` | `{spent_usd, reserved_usd, limit_usd, paused}` |
| `source_status` | `{source_id, status, pct?}` |
| `publication_metrics` | `{publication_id}` |

### 5.2 Эндпоинты `/api/v1`

Нотация: `→` отделяет запрос от ответа. `{job}` и подобные обозначают схемы из §5.4.

**Auth и пользователи** (тег `auth`, `users`)

| Метод | Путь | Назначение | Запрос → Ответ |
|---|---|---|---|
| POST | /auth/login | вход | `{login,password}` → `{user,csrf_token}` + Set-Cookie · 401 · 429 |
| POST | /auth/logout | выход | → 204 |
| GET | /auth/me | текущий пользователь | → `{user:{id,login,display_name,role,tg_user_id},csrf_token}` |
| POST | /auth/change-password | смена пароля | `{current_password,new_password(≥12)}` → 204, остальные сессии отзываются |
| GET | /auth/sessions | мои сессии | → `{items:[{id,created_at,last_seen_at,user_agent,current}]}` |
| DELETE | /auth/sessions/{session_id} | отозвать | → 204 |
| GET | /users | список (admin) | → `{items:[user]}` |
| POST | /users | создать | `{login,display_name,role,password,tg_user_id?}` → `{user}` |
| PATCH | /users/{user_id} | правка | `{display_name?,role?,is_active?,tg_user_id?}` → `{user}` |
| POST | /users/{user_id}/reset-password | сброс | `{new_password}` → 204 |
| GET | /api-tokens | токены | → `{items}` |
| POST | /api-tokens | выпустить | `{name,scopes:["read","ingest"],expires_at?}` → `{id,token}` (токен отдаётся один раз) |
| DELETE | /api-tokens/{token_id} | отозвать | → 204 |

**Система, мета, события** (тег `system`, `meta`)

| Метод | Путь | Назначение | Запрос → Ответ |
|---|---|---|---|
| GET | /health | живость (публично, без БД) | → `{status:"ok"}` |
| GET | /health/ready | готовность | → `{status,checks:{db,migrations,media_writable,disk}}` · 503 |
| GET | /system/status | воркеры и ресурсы | → `{workers:[…],heavy_slot:{owner,task_id,stage,pct},disk:{free_gb,thresholds},load1,psi:{cpu10,mem10},mem_available_mb,queue:{heavy:{queued,running,deferred},light:{…}},budget,versions:{app,db_schema}}` |
| GET | /system/quota | квоты внешних API | → `{items:[{api,cluster_id,quota_day,units,limit,pct}]}` |
| GET | /overview | сводка «Пульта» | `?date_from&date_to&channel_id&basis=engaged|public` → `{kpi:{views,engaged_views,clicks,unique_visitors,bot_starts,conversions,revenue_minor,spend_usd,cost_per_visitor_usd,videos_published},todo:{pending_review,qc_failed,packages_ready,packages_awaiting_url,unverified_publications,verdicts_72h_ready},alerts_open,budget}` |
| GET | /events/stream | SSE | → поток событий §5.1 |
| GET | /alerts | алерты | `?status=open` → `{items}` |
| POST | /alerts/{alert_id}/ack | подтвердить | → 204 |
| GET | /meta/enums | все enum с русскими подписями | → `{job_status:[{code,label}],…}` |
| GET | /meta/reject-reasons | справочник причин | → `{items:[{code,label,hotkey}]}` |
| GET | /meta/safe-zones | пресеты мёртвых зон | → `{items:[SafeZonePreset]}` |
| GET | /audit-log | журнал | `?actor_user_id&entity_type&entity_id&action&date_from&date_to&before_id` → журнал |

**Настройки, провайдеры, секреты, ассеты, бюджет, обслуживание** (тег `settings`, `providers`, `secrets`, `assets`, `budget`, `maintenance`)

| Метод | Путь | Назначение | Запрос → Ответ |
|---|---|---|---|
| GET | /settings | настройки | → `{items:[{key,value,default,description,updated_at}]}` |
| PATCH | /settings | изменить (admin) | `{key: value,…}` → `{items}` |
| GET | /providers | реестр + конфиг | → `{items:[{key,kind,adapter,label,enabled,is_default,params,secret_name,secret_set,price,license_note,resource_class,last_ok_at,last_error}]}` |
| PUT | /providers/{provider_key} | конфиг | `{enabled,is_default,params,secret_name}` → `{provider}` |
| POST | /providers/{provider_key}/test | проверка связи | → `{ok,latency_ms,detail}` |
| GET | /secrets | секреты (без значений) | → `{items:[{name,is_set,source:"db"|"env",last4,updated_at,used_by}]}` |
| PUT | /secrets/{secret_name} | записать | `{value}` → 204 (аудит без значения) |
| DELETE | /secrets/{secret_name} | удалить | → 204 |
| GET | /assets | ассеты | `?kind` → `{items}` |
| POST | /assets | зарегистрировать из загрузки | `{upload_id,kind,name,license_note}` → `{asset}` · 422 `font_no_cyrillic` (проверка fontTools) |
| GET | /assets/{asset_id}/file | файл (шрифты для превью баннера) | → бинарь |
| DELETE | /assets/{asset_id} | удалить | → 204 |
| GET | /budget | бюджет дня | → `{day,daily_limit_usd,spent_today_usd,reserved_usd,paused,pause_reason,alert_threshold_pct}` |
| PATCH | /budget | лимиты | `{daily_limit_usd?,alert_threshold_pct?}` → `{budget}` |
| POST | /budget/pause | ручная пауза платных шагов | `{reason}` → `{budget}` |
| POST | /budget/resume | снять паузу | → `{budget}` |
| GET | /generation-events | журнал вызовов провайдеров | `?job_id&source_id&provider&step_name&date_from&date_to&before_id` → журнал |
| POST | /maintenance/cleanup-preview | оценить уборку | → `{items:[{category,files,bytes}]}` |
| POST | /maintenance/cleanup | запустить уборку (admin) | → 202 `{task_id}` |

**Продукты, кусты, шаблоны баннера, кампании, варианты** (теги `products`, `clusters`, `banners`, `campaigns`)

| Метод | Путь | Назначение | Запрос → Ответ |
|---|---|---|---|
| GET | /products | список | `?status` → `{items:[product]}` (+ `stats_7d`) |
| POST | /products | создать | `{id,name,kind,landing_url,redirect_host,…}` → `{product}` |
| GET | /products/{product_id} | карточка | → `{product}` |
| PATCH | /products/{product_id} | правка | частичный `{product}` → `{product}` |
| POST | /products/{product_id}/archive | архив | → `{product}` |
| POST | /products/{product_id}/webhook-secret/rotate | новый секрет | → `{secret}` (один раз) |
| GET | /products/{product_id}/integration | инструкция интеграции | → `{ingest_url,headers,signature_spec,example_payload,deeplink_examples,emitter_snippets:{python,node}}` |
| POST | /products/{product_id}/test-conversion | тестовое событие через публичный ingest | → `{status,response}` |
| GET | /clusters · POST /clusters | кусты | `{id,title,notes?}` → `{cluster}` |
| GET | /clusters/{cluster_id} · PATCH | карточка / правка | → `{cluster}` |
| GET | /banner-templates · POST | шаблоны | `{name,layout}` → `{template}` |
| GET | /banner-templates/{template_id} · PATCH | карточка / правка | `{layout,expected_version}` → `{template}` · 409 |
| POST | /banner-templates/{template_id}/duplicate | копия | → `{template}` |
| POST | /banner-templates/import-yaml | импорт формы banner.yaml | multipart `file`, `name` → `{template}` |
| GET | /banner-templates/{template_id}/export-yaml | экспорт | → `text/yaml` |
| POST | /banner-templates/{template_id}/validate | серверная валидация (источник истины) | `{layout?,variant_id?,variant_draft?,platform,duration_sec}` → `BannerValidateResponse` |
| POST | /banner-templates/{template_id}/preview | PNG-кадр тем же кодом, что в рендере | `{variant_id?,variant_draft?,platform,t_sec,background:"sample"|"job:<id>"|"asset:<id>"}` → `image/png` 540×960 |
| GET | /campaigns · POST | кампании | `?product_id&status`; `{product_id,name,banner_template_id,allocation,priority,targeting}` → `{campaign}` |
| GET | /campaigns/{campaign_id} · PATCH | карточка / правка | → `{campaign}` |
| POST | /campaigns/{campaign_id}/status | статус | `{status}` → `{campaign}` |
| GET | /campaigns/{campaign_id}/variants · POST | варианты | `{code,name,weight,offer_text,…}` → `{variant}` |
| PATCH | /banner-variants/{variant_id} | правка | частичный `{variant}` → `{variant}` |
| POST | /banner-variants/{variant_id}/status | статус | `{status}` → `{variant}` |
| GET | /campaigns/{campaign_id}/experiment | A/B-статистика | → `Experiment` (§6.7) |

**Ниши и каналы** (теги `niches`, `channels`)

| Метод | Путь | Назначение | Запрос → Ответ |
|---|---|---|---|
| GET | /niches · POST | список / создать или клонировать | `{id,label,config,clone_from?}` → `{niche}` |
| GET | /niches/{niche_id} | карточка | → `{id,label,status,current_version,config,used_by_channels}` |
| PUT | /niches/{niche_id} | новая версия | `{config,expected_version}` → `{niche}` · 409 · 422 (ошибки по путям) |
| PATCH | /niches/{niche_id} | метаданные | `{label?,status?}` → `{niche}` |
| GET | /niches/{niche_id}/versions | история | → `{items:[{version,source,created_by,created_at}]}` |
| GET | /niches/{niche_id}/versions/{version} | конфиг версии | → `{config}` |
| POST | /niches/{niche_id}/validate | проверка без сохранения | `{config}` → `{ok,errors:[{path,msg}],warnings}` |
| GET | /niches/{niche_id}/export-yaml | экспорт | → `text/yaml` |
| POST | /niches/import-yaml | импорт | multipart `file`, `mode=create|replace` → `{niche}` |
| POST | /niches/{niche_id}/test-script | сухой прогон сценария | `{topic}` → `{script,forbidden_hits,cost_usd}` (синхронно, таймаут 40 с, через бюджет) |
| POST | /niches/{niche_id}/voice-sample | пример голоса | `{text?,provider_key?}` → 202 `{task_id}`; готовое аудио по `GET /tasks/{task_id}/result-file` |
| POST | /text/check-forbidden | словарный фильтр | `{niche_id,text}` → `{hits:[{phrase,start,end}]}` |
| GET | /channels · POST | каналы | `?platform&status&niche_id&cluster_id`; `{id,platform,niche_id,cluster,title,platform_channel_ref,…}` → `{channel}` |
| GET | /channels/{channel_id} · PATCH | карточка / правка | → `{channel}` (+ предупреждение `profile_duplicate_of`) |
| GET | /channels/{channel_id}/health | здоровье | → `{status,last_publications,removed_count_30d,sanction_signals,views_trend_7d,below_p25_streak,shorts_feed_share}` |
| GET | /channels/{channel_id}/incidents · POST | журнал инцидентов | `{kind,note,details}` → `{incident}` |
| POST | /channels/{channel_id}/tracking-link | ссылка канала (кнопка «Подписаться») | → `{tracking_link}` |
| GET | /channels/{channel_id}/oauth/youtube/start | OAuth YT Analytics | → `{authorize_url}` |
| GET | /oauth/youtube/callback | колбэк | `?code&state` → 302 в UI (state подписан и привязан к сессии) |

**Ролики, приёмка, задачи** (теги `jobs`, `review`, `tasks`)

| Метод | Путь | Назначение | Запрос → Ответ |
|---|---|---|---|
| GET | /jobs | список | `?kind&status&stage&channel_id&niche_id&product_id&campaign_id&banner_variant_id&source_id&q&date_from&date_to&sort` → `{items:[job_row]}` |
| GET | /jobs/stage-counts | лента стадий | → `{items:[{status,stage,count}]}` |
| POST | /jobs | создать generate или clip | `JobCreate` → 201 `{job}` · 423 · 409 |
| POST | /jobs/batch | пачка генерации | `{kind:"generate",channel_id,topics:[≤20],campaign_id?,banner_variant_id?,start}` → `{items:[job]}` |
| GET | /jobs/{job_id} | детально | `?include=words,script` → `Job` |
| PATCH | /jobs/{job_id} | правка до старта | `{topic?,script_override?,banner_variant_id?,author_layer?,priority?}` (только `draft|queued|rework`) → `Job` |
| POST | /jobs/{job_id}/start | запустить | → `Job` |
| POST | /jobs/{job_id}/cancel | отменить | → `Job` |
| POST | /jobs/{job_id}/retry | перезапуск с стадии | `{from_stage?}` → `Job` |
| POST | /jobs/{job_id}/clone | клон с правками | `{overrides}` → 201 `{job}` (`parent_job_id`) |
| POST | /jobs/{job_id}/adapt | перерендер под другую площадку (VK) | `{channel_id}` → 201 `{job}` (дочерний, со стадии `render`) |
| GET | /review-queue | очередь приёмки | `?channel_id` → `{items:[{job_id,video_uid,poster_url,preview_url,flags,…}]}` |
| GET | /jobs/{job_id}/review-context | всё для экрана приёмки | → `ReviewContext` |
| POST | /jobs/{job_id}/review | решение | `{decision,reject_reason?,rework_from_stage?,auto_restart?,comment?,expected_status:"pending_review"|"qc_failed"}` → `Job` · 409 `job_state_conflict` (с `details.decided_by`, `decided_via`) |
| POST | /jobs/{job_id}/review/undo | отмена решения ≤ 10 мин | → `Job` · 409, если пакет уже скачан или есть публикация |
| GET | /jobs/{job_id}/media/{media_kind} | медиа (`preview|final|poster|voice|banner_impulse|banner_final`) | `?download=1` → Range-стрим, латинское имя файла |
| GET | /jobs/{job_id}/scenes | сцены | → `{items:[{idx,prompt,image_url,seed,duration_sec}]}` |
| GET | /jobs/{job_id}/scenes/{scene_idx}/image | картинка сцены | → `image/png` |
| POST | /jobs/{job_id}/scenes/{scene_idx}/regenerate | перегенерация сцены (платно) | `{prompt?}` → 202 `{task_id}` (job → processing со стадии `render`) |
| GET | /jobs/{job_id}/tasks | таймлайн стадий | → `{items:[{stage,status,task_id,attempts,started_at,finished_at,duration_ms,cost_usd,error,progress}]}` |
| GET | /jobs/{job_id}/history | переходы и решения | → `{items:[{at,kind,from,to,by,via,comment}]}` |
| GET | /jobs/{job_id}/similar | похожие свои ролики | → `{items:[{job_id,video_uid,similarity,channel_id}]}` |
| POST | /jobs/{job_id}/author-voice | запись голоса оператора (clip) | multipart `audio` (webm/ogg/wav ≤ 20 МБ), `segment:"cold_open"|"insert_0"|"insert_1"|"outro"` → 202 `{task_id}` |
| GET | /tasks | задачи | `?status&kind&resource_class&job_id&source_id` → `{items}` |
| GET | /tasks/{task_id} | задача | → `{task,log_tail}` (200 строк) |
| GET | /tasks/{task_id}/log/stream | живой лог (SSE) | → поток строк |
| GET | /tasks/{task_id}/result-file | файл-результат (voice-sample и т. п.) | → бинарь |
| POST | /tasks/{task_id}/retry · /tasks/{task_id}/cancel | управление | → `{task}` |
| GET | /workers | воркеры | → `{items}` |
| GET | /schedules · PATCH /schedules/{schedule_name} | расписания (admin) | `{enabled?,interval_sec?}` → `{schedule}` |

**Пакеты, публикации, трекинг** (теги `packages`, `publications`, `tracking`)

| Метод | Путь | Назначение | Запрос → Ответ |
|---|---|---|---|
| GET | /packages | очередь «К заливке» | `?status=ready|downloaded|published&channel_id` → `{items:[UploadPackage]}` |
| GET | /packages/{package_id} | пакет | → `UploadPackage` |
| GET | /jobs/{job_id}/package | текущий пакет ролика | → `UploadPackage` · 404 |
| POST | /jobs/{job_id}/package | пересобрать | `{regenerate_texts?:bool}` → `UploadPackage` |
| PATCH | /packages/{package_id} | правка текстов | `{title?,description?,hashtags?,pinned_comment?}` → `UploadPackage` |
| PATCH | /packages/{package_id}/checklist | отметка пункта | `{code,done}` → `UploadPackage` |
| POST | /packages/{package_id}/mark-uploaded | «залил, ссылку вставлю позже» | → `UploadPackage` (status `downloaded`) |
| GET | /packages/{package_id}/video | файл (attachment, Range; ставит `downloaded`) | → mp4 `<channel>_<date>_<code>.mp4` |
| GET | /packages/{package_id}/text | тексты одним файлом | → `text/plain` |
| GET | /packages/{package_id}/zip | архив (ZIP_STORED) | → `application/zip` |
| POST | /publications/parse-url | разбор URL | `{url}` → `{platform,platform_video_id,platform_kind,canonical_url,alias_code}` · 422 `unsupported_url` |
| POST | /publications | регистрация публикации | `{package_id,url,published_at?}` → 201 `{publication,tracking_links}` · 409 `publication_exists` · 422 `platform_mismatch` |
| GET | /publications | реестр | `?platform&channel_id&niche_id&product_id&campaign_id&banner_variant_id&status&verify_status&date_from&date_to&sort=-latest_views` → `{items}` |
| GET | /publications/{publication_id} | карточка | → `Publication` |
| PATCH | /publications/{publication_id} | статус и отметки | `{status?,status_reason?,has_claim?,published_at?}` → `Publication` |
| DELETE | /publications/{publication_id} | мягкое удаление ошибочной регистрации (admin) | → 204; job → approved, пакет → ready |
| POST | /publications/{publication_id}/reverify | повторить проверку принадлежности | → 202 |
| POST | /publications/{publication_id}/refresh-metrics | внеочередной опрос | → 202 |
| GET | /publications/{publication_id}/metrics | ряд | `?granularity=raw|day` → `{points:[{t,age_hours,views,likes,comments,engaged_views,source}]}` |
| GET | /publications/{publication_id}/retention | удержание | → `{captured_at,duration_sec,retention_3s,failure_class,points:[{ratio,audience_watch_ratio,relative}]}` |
| GET | /publications/{publication_id}/traffic | клики и конверсии | `?granularity=hour|day` → `{points}` |
| GET | /tracking-links · POST | ссылки | `{product_id,level:"manual"|"channel"|"campaign",channel_id?,campaign_id?,code?,label?,target_override_url?}` → `{tracking_link}` |
| PATCH | /tracking-links/{link_id} | правка | `{label?,is_active?,target_override_url?}` |
| GET | /tracking-links/{link_id}/stats | статистика | `?date_from&date_to` → `{totals,series}` |
| GET | /clicks · GET /conversions | журналы | `?tracking_link_id&publication_id&product_id&event_type&before_id` → журнал |

**Загрузки (tus 1.0.0), источники, нарезка** (теги `uploads`, `sources`, `clipping`)

| Метод | Путь | Назначение | Запрос → Ответ |
|---|---|---|---|
| OPTIONS | /uploads/tus | возможности | → 204 `Tus-Version: 1.0.0`, `Tus-Extension: creation,termination,expiration`, `Tus-Max-Size` |
| POST | /uploads/tus | создать | `Upload-Length`, `Upload-Metadata: filename,filetype,purpose (base64)` → 201 `Location`, `Upload-Expires` · 413 · 507 |
| HEAD | /uploads/tus/{upload_id} | смещение | → 200 `Upload-Offset`, `Upload-Length`, `Cache-Control: no-store` |
| PATCH | /uploads/tus/{upload_id} | чанк ≤ 32 МБ | `application/offset+octet-stream`, `Upload-Offset` → 204 · 409 · 410 · 415 |
| DELETE | /uploads/tus/{upload_id} | прервать | → 204 |
| POST | /sources/from-upload | источник из загрузки | `{upload_id,title,rights:SourceRights,niche_id?,language?,options?}` → 202 `Source` · 422 без прав |
| POST | /sources/from-url | источник по URL (свой или лицензированный) | `{url,title?,rights:SourceRights,niche_id?,options?}` → 202 `Source` |
| GET | /sources | список | `?status&q&niche_id` → `{items}` |
| GET | /sources/{source_id} | карточка + прогресс | → `Source` |
| PATCH | /sources/{source_id} | правка | `{title?,niche_id?,original_retain_until?}` → `Source` |
| DELETE | /sources/{source_id} | удалить | `?purge_files=true` → 204 |
| GET | /sources/{source_id}/rights · PUT | права | `SourceRights` → `SourceRights` (аудит) |
| POST | /sources/{source_id}/reprocess | перезапуск шагов | `{steps:["transcribe","select","scenes"],asr_provider?}` → 202 |
| GET | /sources/{source_id}/transcript | транскрипт | `?format=json|srt|vtt` → компактно `{words:[{w,s,e,sid}],sentences:[{sid,s,e,text}]}` |
| GET | /sources/{source_id}/media/proxy | прокси 540p (GOP 1 с), Range | → mp4 |
| GET | /sources/{source_id}/media/audio | аудио m4a для прослушивания кандидата | → m4a |
| GET | /sources/{source_id}/peaks | волна | → `{duration_sec,sample_rate,data:[…]}` (формат wavesurfer) |
| GET | /sources/{source_id}/storyboard | метаданные раскадровки | → `{interval_sec,tile_w,tile_h,cols,rows,sheets:[url]}` |
| GET | /sources/{source_id}/storyboard/{sheet_idx} | лист спрайта | → `image/jpeg` |
| GET | /sources/{source_id}/scenes | склейки (что посчитано) | `?start_sec&end_sec` → `{items:[{start_sec,end_sec}],coverage:[[s,e]]}` |
| GET | /sources/{source_id}/clip-candidates · POST | кандидаты / ручной | `{start_sec,end_sec,title?}` → `{candidate}` |
| PATCH | /clip-candidates/{candidate_id} | правка границ или статуса | `{start_sec?,end_sec?,status?,title?,hook_text?,unblock_flags?}` → `ClipCandidate` (снаппинг пересчитывается) |
| POST | /clip-candidates/{candidate_id}/create-job | в рендер | `ClipJobOptions` → 201 `{job}` |
| POST | /sources/{source_id}/clips | пачка клипов | `{items:[{start_sec,end_sec,candidate_id?,…ClipJobOptions}]}` → `{items:[job]}` |

**Трендоскаут** (тег `trends`)

| Метод | Путь | Назначение | Запрос → Ответ |
|---|---|---|---|
| POST | /competitors/resolve | URL или @handle → канал | `{url}` → `{platform,channel_ref,title,handle,uploads_playlist_id}` (1 юнит) |
| GET | /competitors · POST | доноры | `?pool&niche_id&active`; `{id,platform,channel_ref,pool:"ru"|"donor_en",niche_id?}` → `{competitor}` (+ задача baseline) |
| PATCH | /competitors/{competitor_id} · DELETE | правка / удаление | → `{competitor}` / 204 |
| POST | /competitors/import | импорт списком | `{lines:[…]}` → `{created,skipped,errors}` |
| GET | /competitor-videos | лента аутлаеров | `?competitor_id&tier&min_score&age_max_hours&is_short&pool&status&sort=-outlier_score` → `{items}` |
| GET | /competitor-videos/{video_id} | со снимками | → `{video,snapshots}` |
| GET | /competitor-videos/{video_id}/thumbnail | миниатюра из локального кеша | → `image/jpeg` |
| POST | /competitor-videos/{video_id}/analyze | разбор в бриф | `{niche_id,use_structure:true,use_vision:false}` → 202 `{task_id}` |
| POST | /competitor-videos/{video_id}/ignore | скрыть | → 204 |
| GET | /trend-briefs | брифы | `?status&niche_id&min_score` → `{items}` |
| GET | /trend-briefs/{brief_id} · PATCH | бриф / правка | `{status?,notes?,hook_type?}` → `{brief}` |
| POST | /trend-briefs/{brief_id}/create-jobs | ролики по брифу | `{channel_id,topics:[…],campaign_id?,start}` → `{items:[job]}` |
| GET | /trend-scout/field-stats | корреляции полей | `?niche_id` → `{computed_at,items:[{field,bucket,n,median_outlier,spearman_rho,ci_low,ci_high}]}` |
| POST | /trend-scout/run | ручной запуск (admin) | `{what:"discovery"|"stats"|"outliers"|"baselines"}` → 202 |

**Аналитика** (тег `analytics`). Общие фильтры: `date_from,date_to,platform,channel_id,niche_id,product_id,campaign_id,banner_variant_id,kind,basis=engaged|public,mode=activity|cohort`.

| Метод | Путь | Ответ |
|---|---|---|
| GET | /analytics/summary | `{range,filters,totals,previous,data_freshness:{metrics_polled_at,analytics_pulled_at,rollup_at}}` |
| GET | /analytics/timeseries | `?metric&granularity=day|week&group_by=none|channel|niche|product|campaign|banner_variant|platform|kind&top=8` → `{metric,granularity,group_by,series:[{key,label,points:[{t,v}]}]}` (хвост сворачивается в `other`) |
| GET | /analytics/breakdown | `?dimension=channel|niche|product|campaign|banner_variant|kind|hook_type|duration_bucket|rights_type` → `{dimension,rows:[{key,label,n,views_median,views_p75,ctr_per_1k,cr,cost_per_visitor_usd,needs_more_n,p_value}],totals}` |
| GET | /analytics/videos | лидерборд публикаций (колонки как в §7.3 «Ролики») |
| GET | /analytics/cohorts | `?metric&age=d1|d3|d7|d14|d30&group_by` → `{rows:[{week,values:{d1,…}}]}` |
| GET | /analytics/funnel | `{branches:{domain:[{code,value}],bot:[…]},rates:[{from,to,rate}],per_1k_views}` |
| GET | /analytics/costs | `?granularity&group_by=provider|step|kind` → `{series,cost_per_published_video_usd,cost_per_video_usd,overhead_usd}` |
| GET | /analytics/qc | `{pass_rate:[{t,v}],reject_reasons:[{code,label,count}],qc_failed_checks:[…]}` |
| GET | /analytics/retention-classes | `{items:[{failure_class,count,share}],retention_3s_hist}` (только при OAuth) |
| GET | /analytics/freshness | `{public_counts_age_min,analytics_lag_hours,quota:{…}}` |
| GET | /analytics/experiments/{campaign_id} | как `/campaigns/{id}/experiment` |
| GET | /analytics/export.csv | `?report=breakdown|videos|timeseries&…` → CSV (UTF-8 BOM, `;`) |

`metric` ∈ `views, engaged_views, likes, comments, landing_views, unique_visitors, to_telegram, to_site, bot_starts, signups, first_payments, revenue_minor, spend_usd, videos_produced, videos_published, ctr_per_1k_views, cost_per_visitor_usd`.

### 5.3 Публичный контур (`public`, хост редиректора, без `/api/v1`)

Статические маршруты объявляются **до** catch-all.

| Метод | Путь | Поведение |
|---|---|---|
| GET | /health | `{status:"ok"}` |
| GET | /robots.txt | `Disallow: /` |
| POST | /ingest/v1/events | приём конверсий с HMAC (ниже) |
| GET | /go/{code}/tg | лог `to_telegram`. Для стиля `tg_scheme_with_fallback` отдаёт HTML: `tg://resolve?domain=<bot>&start=<code>_l`, через 1,5 с запасной `https://telegram.me/<bot>?start=<code>_l`. Домен t.me в РФ ненадёжен. Для TMA используется `startapp=<code>_l` |
| GET | /go/{code}/site | лог `to_site`, 302 на `landing_url?utm_source=<yt|vk>&utm_medium=shorts&utm_campaign=<code>&utm_content=<variant_code>` |
| GET | /{code} | код должен пройти `^[A-Za-z0-9_-]{2,64}$`, иначе 404. Поиск по точному совпадению, затем для сгенерированных кодов (5 символов) — нижний регистр и исправление опечаток (`o→0`, `i/l→1`); алиасы `yt_` чувствительны к регистру. Дальше по `redirect_mode`: лендинг или прямой 302. Неизвестный код: лог `unknown_code` и лендинг продукта `redirect_default_product` |

Лендинг:
- Jinja2 с autoescape, CSS инлайн, **без внешних ресурсов**.
- Метка передаётся в JS через `data-code` и `JSON.parse`. В `localStorage` кладётся `as_code` для веб-ветки.
- Заголовок `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-…'; img-src 'self' data:`.
- HEAD-запросы и боты по UA (bot, crawler, spider, preview, TelegramBot, vkShare, facebookexternalhit, WhatsApp, Discordbot, Slackbot, Googlebot, YandexBot, curl, python-httpx) пишутся с `is_bot=true` и в статистику не идут.
- `visitor_key = hmac_sha256(REDIRECT_SALT, f"{day}|{ip}|{ua}")[:32]`.

**Приём конверсий** `POST /ingest/v1/events`:
- Заголовки: `X-AS-Product: umka`, `X-AS-Timestamp: <unix>`, `X-AS-Signature: sha256=<hex(HMAC_SHA256(secret, f"{ts}." + raw_body))>`.
- Проверки: `|now − ts| ≤ 300 с`, `hmac.compare_digest`, тело ≤ 256 КБ, не больше 100 событий, лимит 20 запросов/с на продукт.
- Тело:
  ```json
  {"events":[{"event_id":"umka-pay-99812","type":"first_payment","occurred_at":"2026-09-26T10:00:00Z",
              "tag":"k7m2q_l","user_ref":"tg:123456","amount_minor":49000,"currency":"RUB","meta":{}}]}
  ```
- Ответ 200: `{"accepted":1,"duplicates":0,"unknown_tag":0,"results":[{"event_id":"…","status":"accepted"}]}`.
- Идемпотентность через `ON CONFLICT (product_id, external_event_id) DO NOTHING`.
- Суффикс `_l` снимается и даёт `via_landing=true`. `user_ref` хешируется. `tag` разрешается в `tracking_link`, и измерения копируются в строку.
- Что шлют продукты:
  - бот на `/start <param>` отправляет `bot_start`;
  - TMA отправляет `tma_open` с `start_param`;
  - сайт при первом заходе кладёт `utm_campaign` в localStorage и шлёт его с `signup` и `first_payment`;
  - хранимый код пользователя (first-touch, окно 30 дней) подставляется во все последующие события.
- Эталонный отправитель: `public/emitter_snippet.py` (stdlib: hmac, json, urllib) и Node-вариант в `GET /products/{id}/integration`.

### 5.4 Ключевые схемы (фиксируются первыми, из них генерируются TS-типы)

```jsonc
// JobCreate (generate)
{"kind":"generate","channel_id":"taro_main","topic":"отношения","trend_brief_id":null,
 "campaign_id":null,"banner_variant_id":null,"scheduled_for":null,"priority":100,
 "options":{"llm_provider":"openai_compat","tts_provider":"edge_tts","image_provider":"fal_flux_schnell",
            "scene_count":6,"duration_target_sec":[15,25]},"start":true}
// JobCreate (clip) = ClipJobOptions + source
{"kind":"clip","channel_id":"astro_main","clip_candidate_id":88,"source":null,   // или {"source_id":5,"start_sec":1204.3,"end_sec":1226.9}
 "campaign_id":7,"banner_variant_id":null,
 "author_layer":{"mode":"llm_draft","hook_text":null,"voice":"tts","captions":"both","title_overlay":null},
 "reframe":{"mode":"auto"},"start":true}
// Job (detail)
{"id":1234,"video_uid":"a1b2c3d4e5f6","kind":"generate","status":"pending_review","stage":"qc",
 "channel":{"id":"taro_main","title":"Таро · основной","platform":"youtube"},
 "niche_id":"taro","niche_version":3,"product_id":"umka","campaign_id":7,
 "banner_variant":{"id":21,"code":"A","name":"Домен + расклад"},"topic":"отношения",
 "duration_sec":21.4,"cost_usd":0.0512,"author_share":null,
 "qc":{"passed":true,"failed_checks":[],"checks":[{"code":"loudness","ok":true,"measured":"-14.2 LUFS","threshold":"-14±1.5"}],
       "warnings":[{"code":"library_similarity","detail":"похож на a9f0…","job_id":1200,"similarity":0.91}]},
 "script":{"hook_text":"…","body_text":"…","cta_text":"…","voice_cta_text":"…","voiceover_text":"…"},
 "stages":[{"stage":"script","status":"succeeded","task_id":991,"attempts":1,"duration_ms":2100,"cost_usd":0.002,"error":null}],
 "media":{"preview_url":"/api/v1/jobs/1234/media/preview","final_url":"/api/v1/jobs/1234/media/final",
          "poster_url":"/api/v1/jobs/1234/media/poster"},
 "reviews":[],"package":null,"publications":[],"source":null,"parent_job_id":null,
 "created_at":"…","updated_at":"…"}
// ReviewContext
{"job":{"id":1234,"video_uid":"…","kind":"generate","channel_id":"…","niche_id":"…","product_id":"umka",
        "banner_variant_id":21,"attempt_count":1},
 "media":{"preview_url":"…","final_url":"…","poster_url":"…","duration_sec":21.4},
 "script":{"topic":"…","hook_pattern":"incomplete_visual","voiceover_text":"…","cta_text":"…",
           "words":[{"word":"Три","start_sec":0.0,"end_sec":0.31}]},
 "banner":{"windows":[{"layer":"offer","start_sec":8.6,"end_sec":13.6}],
           "boxes":[{"layer":"micro_brand","x1":146,"y1":56,"x2":420,"y2":106},{"layer":"offer","x1":60,"y1":1150,"x2":900,"y2":1400}]},
 "scene_cuts_sec":[1.2,4.6,8.1],
 "qc":{"passed":true,"checks":[…]},
 "flags":[{"code":"tts_mock","severity":"warn","detail":"…"}],   // forbidden_phrase | duration_dead_zone | banner_in_dead_zone | library_similarity | tts_mock | author_share_low | foreign_watermark
 "similarity":{"nearest_job_id":1200,"score":0.91,"method":"dhash"},
 "cost_usd":0.0512,"previous_attempts":[{"job_id":1230,"decision":"rework","reject_reason":null,"comment":"…"}]}
// UploadPackage
{"id":77,"job_id":1234,"video_uid":"…","status":"ready",
 "channel":{"id":"taro_main","display_name":"Таро — выбери карту","platform":"youtube",
            "account_hint":"Brand Account: Таро — выбери карту (@taro_main)","platform_channel_ref":"UC…"},
 "texts":{"title":"…","title_max":100,"description":"…","hashtags":["#таро","#расклад","#эзотерика"],
          "pinned_comment":"umka.chat — бесплатный расклад (ссылка в шапке канала)",
          "related_video_hint":"Поставь Related video на длинный ролик темы","audio_note":"…"},
 "checklist":[{"code":"file_downloaded","done":false,"done_at":null},{"code":"right_account","done":false},
              {"code":"texts_pasted","done":false},{"code":"hashtags","done":false},{"code":"related_video","done":false},
              {"code":"pinned_comment","done":false},{"code":"ai_disclosure","done":false},{"code":"published","done":false}],
 "tracking":{"code":"k7m2q","redirect_url":"https://u.umka.chat/k7m2q",
             "telegram_url":"tg://resolve?domain=umka_bot&start=k7m2q","telegram_https_url":"https://telegram.me/umka_bot?start=k7m2q"},
 "file":{"download_url":"/api/v1/packages/77/video","filename":"taro_main_2026-09-26_k7m2q.mp4","size_bytes":8123456,"duration_sec":21.4},
 "publication":null}
// Publication
{"id":55,"job_id":1234,"package_id":77,"platform":"youtube","platform_video_id":"dQw4w9WgXcQ","platform_kind":"short",
 "url":"https://www.youtube.com/shorts/dQw4w9WgXcQ","status":"live","verify_status":"ok","has_claim":false,
 "channel":{…},"niche_id":"taro","product_id":"umka","campaign_id":7,"banner_variant":{"id":21,"code":"A"},
 "tracking":{"codes":["k7m2q","yt_dQw4w9WgXcQ"]},
 "latest":{"captured_at":"…","views":12345,"likes":410,"comments":96,"engaged_views":null,"retention_3s":null,"avg_pct_viewed":null},
 "age_views":{"h24":1500,"h72":6400,"d7":9000},"verdict":"h72",
 "traffic":{"landing_views":31,"unique_visitors":27,"to_telegram":14,"to_site":9,"bot_starts":11,"signups":3,
            "first_payments":0,"revenue_minor":0,"currency":"RUB"},
 "unit_economics":{"job_cost_usd":0.0512,"ctr_per_1k_views":2.51,"cost_per_visitor_usd":0.0019},"next_poll_at":"…"}
// Source
{"id":5,"title":"Эфир 12.09","origin":"upload","status":"transcribing","language":"ru","niche_id":"astro",
 "duration_sec":3620.4,"width":1920,"height":1080,"file_bytes":2143000000,
 "rights":{"rights_type":"own","owner_name":"…","proof_note":"…","allowlist_status":"not_needed","attribution_text":null},
 "watermark_suspect":false,
 "pipeline":[{"step":"prepare","status":"succeeded","task_id":301,"progress":100},
             {"step":"transcribe","status":"running","task_id":305,"progress":42.0,"chunks_done":3,"chunks_total":7,"queue_position":1}],
 "candidates_count":14,"original_retain_until":"2026-10-26","created_at":"…"}
// ClipCandidate
{"id":88,"source_id":5,"start_sec":1204.3,"end_sec":1226.9,"duration_sec":22.6,"final_score":0.81,
 "llm_scores":{"hook":9,"standalone":8,"payoff":7,"emotion":7,"niche_fit":8,"novelty":6},
 "features":{"speech_rate_wps":2.8,"dead_air_ratio":0.04,"first_word_latency":0.12},
 "hook_type":"question","reason":"Законченная мысль, сильный вопрос в первой фразе",
 "transcript_excerpt":"…","title":"…","hook_text":"…","content_flags":[],"needs_context":false,
 "status":"proposed","created_by_kind":"auto"}
// SafeZonePreset
{"platform":"youtube","canvas":{"w":1080,"h":1920},
 "dead_zones":[{"id":"top_bar","x1":0,"y1":0,"x2":1080,"y2":170},{"id":"bottom_ui","x1":0,"y1":1500,"x2":1080,"y2":1920},
               {"id":"right_rail","x1":930,"y1":0,"x2":1080,"y2":1920}],
 "conservative_safe_rect":{"x1":90,"y1":380,"x2":990,"y2":1540},"source_note":"ТЗ §6, community-derived"}
// BannerValidateResponse
{"windows":[{"layer":"micro_brand","start_sec":0,"end_sec":21.4},{"layer":"offer","start_sec":8.6,"end_sec":13.6},
            {"layer":"offer","start_sec":18.9,"end_sec":21.4}],
 "fitted_text_size_px":58,
 "warnings":[{"code":"offer_in_bottom_dead_zone","platform":"youtube","severity":"warn"}]}
 // коды: offer_in_bottom_dead_zone, offer_under_right_rail, offer_in_hook_window, text_shrunk_below_min,
 //       micro_brand_opacity_out_of_range, layer_outside_canvas, vk_top10_bottom20_violation
```

### 5.5 Внутренние интерфейсы (Python), фиксируются в M1

```python
# stages/base.py
@dataclass
class StageContext:
    job: dict; task: dict; workdir: Path; settings: RuntimeSettings
    providers: ProviderSet; budget: BudgetGate; cancel: threading.Event
    progress: Callable[[float], None]; log: logging.LoggerAdapter

@dataclass
class StageResult:
    artifacts: list[NewArtifact]          # (kind, idx, path, input_hash)
    payload_patch: dict                   # мерж в jobs.stage_payload
    job_fields: dict                      # {"duration_sec": 21.4, "qc_failed_checks": [...]}
    next_stage: str | None = None         # None -> по таблице domain/stages.py
    job_status: str | None = None         # 'pending_review' | 'qc_failed'

def register_stage(kind: str, stage: str, resource_class: str, max_attempts: int, paid: bool): ...

# tasks/registry.py
def register_task(kind: str, resource_class: str, max_attempts: int, paid: bool = False): ...
# обработчик: def handle(ctx: TaskContext) -> dict | None ; исключения: RetryableError, PermanentError, ResourceDeferred, Cancelled

# providers/base.py
class LlmProvider(Protocol):
    def chat_json(self, schema: dict, messages: list[dict], *, max_tokens: int) -> tuple[dict, Usage]: ...
    def chat_text(self, messages: list[dict], *, max_tokens: int) -> tuple[str, Usage]: ...
class TtsProvider(Protocol):
    def synthesize(self, text: str, out_path: Path, *, voice: str, rate: str | None) -> tuple[Path, list[WordTiming], Usage]: ...
class ImageProvider(Protocol):
    def generate(self, prompt: str, out_path: Path, *, seed: int, width: int, height: int) -> Usage: ...
class AsrProvider(Protocol):
    def transcribe(self, wav_path: Path, language: str = "ru", progress=None) -> Transcript: ...  # words, sentences, vad, rtfx, cost_usd
class FaceDetector(Protocol):
    def detect(self, frame_bgr) -> list[FaceBox]: ...

# pipeline/ffmpeg_exec.py — единственная точка запуска тяжёлых подпроцессов
def run_media(cmd: list[str], *, timeout_sec: float, threads: int, nice: int = 10,
              progress_cb=None, cancel: threading.Event | None = None, log_path: Path | None = None) -> MediaResult
# итог: ["nice","-n",str(nice), *cmd]; ffmpeg: -hide_banner -nostdin -threads N -filter_threads 1
#       -filter_complex_threads 1 -progress pipe:1; start_new_session=True -> killpg по timeout/cancel
```

---

## 6. Конвейеры

### 6.1 Очередь задач и исполнение

**Почему своя очередь.** Нагрузка — единицы задач в минуту. UI нужны `progress`, `defer_reason`, отмена и связь задачи с job и source. Защиты должны срабатывать **до** взятия задачи. procrastinate и pgqueuer (оба MIT) — запасной путь, если появятся сотни задач в час или несколько хостов-воркеров. Контракт обработчиков переносится туда без переписывания.

**Взятие задачи** (одна транзакция):
```sql
WITH next AS (
  SELECT id FROM tasks
  WHERE status = 'queued' AND resource_class = %(cls)s AND run_after <= now()
  ORDER BY priority, run_after, id
  FOR UPDATE SKIP LOCKED LIMIT 1)
UPDATE tasks t SET status = 'running', locked_by = %(worker)s, locked_at = now(), heartbeat_at = now(),
       started_at = now(), attempts = t.attempts + 1, updated_at = now()
FROM next WHERE t.id = next.id
RETURNING t.*;
```
- **Heartbeat** раз в 20 с пишет `tasks.heartbeat_at` и `workers.heartbeat_at` и читает `cancel_requested`. Если отмена запрошена, группа процессов ffmpeg убивается через `os.killpg`.
- **Reaper** (каждую минуту) обрабатывает `running` с `heartbeat_at < now() - interval '3 minutes'`. При `attempts < max_attempts` задача возвращается в `queued` с backoff, иначе переходит в `failed` (`error_class='worker_lost'`), и job тоже становится `failed`.
- **Мгновенный подхват:** при вставке задачи `NOTIFY as_tasks, '<class>'`. Воркер делает `LISTEN` и дополнительно опрашивает очередь раз в 5 с.
- Результат стадии фиксируется **одной транзакцией**: artifacts, патч job, `tasks.succeeded`, вставка следующей задачи (dedup `job:<id>:<stage>:<rev>`), `NOTIFY as_events`. Платный вызов пишет `generation_events` **сразу после вызова** отдельной короткой транзакцией: деньги потрачены, даже если стадия потом упадёт.

**Классы ошибок** (`workers/policies.py`):

| Класс | Поведение |
|---|---|
| `RetryableError` (сеть, 5xx, 429, таймаут) | backoff `min(30·4^(n−1), 1800)` с ±20% джиттера, до `max_attempts` |
| `PermanentError` (валидация, 4xx кроме 429, запрещённая фраза после N перегенераций) | сразу `failed`, у job понятный `error` |
| `ResourceDeferred` (диск, память, нагрузка, бюджет, квота) | `queued`, `run_after` сдвигается, `attempts−1`, заполняется `defer_reason`. Попытка не тратится |
| `Cancelled` | `cancelled` |

**Виды задач, классы и приоритеты** (меньше = раньше; доделать начатое важнее, чем начать новое):

| kind / stage | Класс | max_attempts | Платно | Приоритет |
|---|---|---|---|---|
| `package_build` | light | 3 | нет | 10 |
| `job_stage:qc` | heavy | 2 | нет | 20 |
| `job_stage:render` | heavy | 2 | нет | 30 |
| `job_stage:clip_analyze` | heavy | 2 | нет | 40 |
| `job_stage:script`, `author_script` | light | 3 | да (LLM) | 50 |
| `job_stage:voice`, `author_voice` | light (edge, fal) / heavy (silero, ASR записи) | 3 / fal 2 | fal: да | 50 |
| `job_stage:visuals` | light | 2 | да (fal) | 50 |
| `job_stage:clip_fetch` | light | 3 | нет | 60 |
| коллекторы (`metrics_poll`, `competitor_*`, `yt_analytics_pull`, `channel_stats`) | light | 5 | нет | 80 |
| `source_download` | light | 3 | нет | 110 |
| `source_prepare` | heavy | 2 | нет | 120 |
| `source_transcribe_chunk` (локальный ASR) | heavy | 2 | нет | 150 |
| `source_transcribe_cloud` | light | 3 | да | 150 |
| `source_select` (LLM-отбор) | light | 3 | да | 160 |
| `source_scenes` (по запросу) | heavy | 2 | нет | 170 |
| `competitor_analyze` | heavy (структура) + light (LLM) | 2 | да | 180 |
| `stats_rollup`, `cleanup`, `alerts_eval`, `task_reaper`, `brief_field_stats` | light | 5 | нет | 200 |

Длинный ASR режется по VAD на куски примерно по 10 минут (`source_transcribe_chunk × N`), чтобы рендеры вклинивались между кусками.

**Бюджет** (`services/budget.py`):
- `reserve(amount, task_id)`: `SELECT … FOR UPDATE` строки `budget_state`. Если бизнес-день `(now() AT TIME ZONE 'Europe/Moscow')::date` сменился, `spent=0, reserved=0, paused=false`.
- Если `spent + reserved + amount > limit`, бросается `ResourceDeferred('budget_exhausted')`, задача откладывается до начала следующего бизнес-дня + 5 мин, ставится `paused=true`.
- Порог `alert_threshold_pct` открывает алерт `budget_80`.
- `settle(task_id, estimate, actual)` в той же транзакции, что `generation_event`: `reserved −= estimate; spent += actual`. `jobs.cost_usd` пересчитывается.

**Планировщик** работает внутри `worker-light`; лидер выбирается через `pg_try_advisory_lock(771002)`. Раз в 10 с: `SELECT … FROM schedules WHERE enabled AND next_run_at <= now() FOR UPDATE SKIP LOCKED`, вставка задачи с `dedup_key='sched:<name>'`, `next_run_at` += интервал (для якорных — следующее `anchor_local_time` в `business_timezone`).

### 6.2 Трек генерации (state machine)

**Статусы job:**
```
draft ──start──► queued ──(claim первой задачи)──► processing
processing ──(qc ok)──► pending_review          processing ──(qc fail)──► qc_failed
processing ──(задача failed окончательно)──► failed ──retry(from_stage)──► processing
pending_review ──approve──► approved ──(package_build)──► [пакет ready]
approved ──(POST /publications)──► published    approved ──adapt(vk)──► дочерний job (processing с render)
pending_review | qc_failed ──rework(from_stage, auto_restart=true)──► processing   (артефакты стадий ≥ from_stage → deleted_at)
pending_review | qc_failed ──rework(auto_restart=false)──► rework ──PATCH…, start──► processing
pending_review ──reject(reason)──► rejected     qc_failed ──override_qc (admin, аудит)──► approved
approved | rejected | rework ──undo (≤10 мин, нет скачанного пакета и публикации)──► pending_review
любой нетерминальный ──cancel──► cancelled
```
Переход выполняется с оптимистичной проверкой: `UPDATE jobs SET status=… WHERE id=%(id)s AND status = ANY(%(allowed)s) RETURNING id`. Ноль строк означает 409 `job_state_conflict`, например веб и бот нажали одновременно.

**Стадии generate:**

| Стадия | Класс | Вход | Выход (artifacts) | Изменения относительно текущего кода |
|---|---|---|---|---|
| `script` | light | ниша из `config_snapshot`, `topic`, `trend_brief`, `variant.voice_cta_text` | `script_json` (hook, body, cta, voiceover_text, `scenes:[{sid,prompt_en,subject,pan}]`) | `chat_json` со строгой схемой вместо `split('.')`. Словарный фильтр, до 3 перегенераций, дальше `PermanentError` |
| `voice` | light / heavy | сценарий, голос из профиля канала | `voice`, `words_json` | `boundary="WordBoundary"`. mock в prod запрещён |
| `visuals` | light | промпты сцен, стиль ниши и канала (`style_suffix`) | `scene_image × N` | seed `blake2b`, бюджет на каждую картинку. Вынесено из render: сетевое ожидание не занимает тяжёлый слот |
| `render` | heavy | картинки, voice, words, шаблон и вариант баннера | `banner_png` (L1, L2), `subtitles_ass`, `final`, `preview`, `poster` | Длительности сцен от `probe_duration_sec(voice)`: первая склейка 1,2 с, остальное точно до суммы, петля (последняя сцена = первая картинка с обратным паном), без `-shortest`. Баннер PNG + `overlay=…:enable=` из `_banner_enable_expression` |
| `qc` | heavy | final | `qc_report` | Один проход ffmpeg (§6.3.9) + `library_similarity` (dHash против последних 200 роликов канала) + `subtitle_sync` (опционально) |

**Назначение варианта баннера** (`services/campaigns.assign_variant(job)`):
1. Берутся активные кампании, у которых `targeting` совпадает с channel, niche, platform и kind; сортировка по `priority`.
2. Внутри первой подходящей: `weighted` делает детерминированный выбор `blake2b(video_uid) mod Σweights`, `round_robin` берёт вариант с минимумом job за 7 дней.
3. Оператор может перебить выбор при создании или до стадии `render`.
4. Выбор фиксируется в `jobs.banner_variant_id` и `config_snapshot.banner`.

### 6.3 Трек нарезки

Главный принцип — **render-on-approve**. Анализ идёт на прокси и транскрипте; тяжёлый рендер запускается только для кандидатов, утверждённых оператором. Превью кандидата — это аудиофрагмент, прокси и раскадровка, без рендера.

#### 6.3.1 Конвейер источника
```
POST /sources/from-upload|from-url (права обязательны)
  → source_download   (только URL; light; yt-dlp audio-first)          status: downloading
  → source_prepare    (heavy)                                           status: preparing
       ffprobe + sha256 → proxy 540p H.264 (-g 30, CRF 28, veryfast, 2 потока) → audio.m4a + audio16k.wav
       → peaks.json (2000 точек/мин, формат wavesurfer) → storyboard (fps=1/2, scale=160:-1, tile=10x10)
       → watermark_check (12 кадров, cv2.matchTemplate по config/logos/*.png, порог 0.8 → warning)
  → source_transcribe_chunk × N (heavy, GigaAM) | source_transcribe_cloud (light)   status: transcribing
       → transcript.json.gz, audio16k.wav удаляется
  → source_select     (light, LLM-отбор + скоринг)                     status: selecting
  → ready
```
- URL (только свои и лицензированные): сначала `bestaudio[ext=m4a]/bestaudio` (около 60 МБ на час) и прокси `bv*[height<=540][vcodec^=avc1]`. 1080p качается позже **только секциями** утверждённых кандидатов (`download_ranges`, ±3 с) на стадии `clip_fetch`.
- Опции yt-dlp: `ratelimit=5_000_000`, `concurrent_fragment_downloads=1`, `max_filesize` по дисковому сторожу, cookies только своего аккаунта. Нужны `yt-dlp[default]` (yt-dlp-ejs) и JS-рантайм (`js_runtimes={'node':{}}`, node на машине есть).
- Лимит `duration < 600` из `ingest.py` снимается. Вместо него `source_max_hours` (3) и `upload_max_gb` (6).

#### 6.3.2 ASR (`providers/asr`)

| provider_key | Как | Когда |
|---|---|---|
| `gigaam_v3_onnx` (default для ru) | onnx-asr: `load_model("gigaam-v3-e2e-ctc", quantization="int8").with_timestamps()`, VAD `load_vad("silero")` режет на куски ≤ 20 с. ORT intra=2, inter=1. Токены в слова — порт `_words_from_tokens` из openshorts | русский, e2e даёт пунктуацию для разбиения на предложения |
| `yandex_speechkit` (облако) | API v3 `recognizeFileAsync`, `deferred-general` (около 9 ₽ за час) или async (около 36 ₽ за час), слова со `startTimeMs/endTimeMs` | бокс занят или длинный источник. CPU почти ноль |
| `openai_compat_asr` | `/audio/transcriptions` с `timestamp_granularities=["word"]`, модель задаётся параметром (whisper-1 снимают 26.02.2027) | запасной вариант |
| `mock` | фикстура | тесты и CI |

- Модели лежат в `MODELS_DIR` с зафиксированными ревизиями и sha256. В рантайме **ничего не скачивается**.
- Ожидаемая скорость GigaAM на 2 потоках — RTFx 8–15, то есть 4–8 мин на час аудио, при RSS 0,4–0,6 ГБ. Это **экстраполяция**; решение принимает `scripts/bench_asr.py` (гейт в M5).
- Предложения: по `.?!…` плюс принудительный разрез на паузе > 1,2 с.

#### 6.3.3 LLM-отбор хайлайтов (`clipping/windows.py`, `llm_select.py`, `schemas.py`)
- LLM плохо считает миллисекунды, поэтому получает **пронумерованные предложения** `[sid] (mm:ss) текст` и возвращает **диапазоны sid**.
- Окна по 90 с с перекрытием 30 с по границам предложений, 6 окон в запросе. На час около 10 запросов. Затем detail-проход по шортлисту (2 × цель, не больше 12) с контекстом ±60 с.
- Строгая JSON-схема. Ключи: `start_sid, end_sid, hook_sid, title(≤90), hook_text(≤140), why(≤300), hook_type(enum), scores{hook,standalone,payoff,emotion,niche_fit,novelty: 0..10}, needs_context, content_flags(enum)`.
- `chat_json` деградирует по цепочке `json_schema → json_object → plain` с проверкой pydantic и одним repair-запросом. Каждый вызов пишет `generation_events(source_id, step_name='highlight_select')`.
- Словарный фильтр ниши применяется к тексту кандидата. `medical_claim` и `guarantee_claim` блокируют кандидата до явной разблокировки оператором.
- Стоимость около $0,01–0,02 на час источника.

#### 6.3.4 Скоринг (`clipping/features.py`, `scoring.py`)
```
llm      = (0.30·hook + 0.20·standalone + 0.20·payoff + 0.10·emotion + 0.15·niche_fit + 0.05·novelty) / 10
feat     = mean(rate_fit(speech_rate 2.3–3.5 wps), 1 − 3·dead_air_ratio, first_word_latency < 0.3, ends_on_terminal_punct)
len_fit  = треугольник вокруг target_clip_sec (стартово 30±15 с; VK жёстко ≤ 58 с)
trend    = доля hook_type среди брифов ниши за 30 дней, взвешенная по outlier_score
final    = 0.55·llm + 0.20·feat + 0.15·len_fit + 0.10·trend ; needs_context → ×0.7
```
- Затем `dedupe_overlapping(0.5)`: не больше одного кандидата на 3 минуты исходника, если `final < 0.8`.
- Веса лежат в настройках. Признаки и итоги (`views_72h`, CTR) сохраняются, раз в неделю по ним калибруются веса. Так замыкается петля обратной связи.

#### 6.3.5 Снаппинг (`clipping/snapping.py`)
1. `sid` превращается в `[sentence.start, sentence.end]`.
2. `snap_clip_to_words(search_window=1.5, max_lead=0.35, max_tail=0.45)` уводит подход и хвост в тишину.
3. Опционально `tighten_pauses(max_gap=0.45, keep=0.15)`: джамп-каты по паузам, выключается для контента с музыкой.
4. Оператор правит границы кликом по слову, снаппинг пересчитывается.

#### 6.3.6 Стадии clip-job

| Стадия | Класс | Что делает |
|---|---|---|
| `clip_fetch` | light | только URL: 1080p-секция `[start−3, end+3]` |
| `clip_analyze` | heavy | PySceneDetect `AdaptiveDetector` на прокси в окне ±3 с; YuNet на 640×360 каждый 3-й кадр (10 fps); стратегия на шот `track`/`center`/`fit_blur`; `SmoothedCameraman` + EMA τ=0,6 с, лимит 0,35·crop_w/с; выход `crop_000.cmd` (sendcmd) и `scene_cuts` |
| `author_script` | light | LLM-черновик авторского слоя (схема §6.3.8) или текст оператора из `author_layer` |
| `author_voice` | light / heavy | TTS (edge или Silero) либо запись оператора (`POST /jobs/{id}/author-voice`) + GigaAM-тайминги |
| `render` | heavy | EDL → одно кодирование (§6.3.7) |
| `qc` | heavy | QC-профиль `clipping` + `author_share` + `subtitle_sync` + `banner_face_overlap` + срок и тип прав |

Проверка «баннер на лице»: если лицо пересекается с плашкой (y 1150–1400) больше чем на 20%, плашка уходит в `alt_position` шаблона (верхняя полоса y 230–480), иначе выдаётся предупреждение.

#### 6.3.7 EDL и рендер одним проходом (`clipping/edl.py`, `render_clip.py`)
```json
{"canvas":{"w":1080,"h":1920,"fps":30},
 "video":[{"type":"src","in":84.12,"out":96.40,"reframe":"track","crop_cmd":"crop_000.cmd"},
          {"type":"freeze","at_src":96.40,"dur":2.6,"pan":"in"},
          {"type":"src","in":96.40,"out":121.90,"reframe":"fit_blur"}],
 "audio":{"src_duck":[[0.0,2.4,-16],[12.28,14.88,-60]],
          "vo":[{"file":"vo_open.wav","at":0.0},{"file":"vo_ins1.wav","at":12.28},{"file":"vo_out.wav","at":38.1}]},
 "captions":[{"style":"Source","words_ref":"src_words.json"},{"style":"Author","words_ref":"vo_words.json"}],
 "banner":{"variant_id":12,"l1_png":"l1.png","l2_png":"l2.png","windows":[[16.0,20.5],[38.5,41.0]]},
 "expected_static":[[12.28,14.88]],
 "meta":{"ai_disclosure":true}}
```
1. **Аудио** собирается отдельно: `atrim/asetpts`, огибающая `volume=…:eval=frame`, `adelay` для голоса, `amix=normalize=0`, затем `two_pass_loudnorm` (текущий код) → `mix.m4a`.
2. **Видео** кодируется один раз:
   - `trim/setpts` по сегментам, `sendcmd` + `crop@c` + `scale=1080:1920:flags=lanczos`;
   - fit_blur дешёвый: фон `scale=270:480,boxblur=10:2,scale=1080:1920`, текущий `gblur sigma=25` примерно в 10 раз дороже;
   - `concat`, `overlay` PNG-баннеров с `enable=`, `ass=…:fontsdir=config/fonts`;
   - `libx264 -preset veryfast -crf 23 -threads 2 -filter_complex_threads 1 -movflags +faststart -progress pipe:1`.
3. `subtitles.build_karaoke_ass` расширяется до нескольких дорожек со стилями `Source` и `Author`, интерфейс остаётся обратно совместимым.
4. В метаданные mp4 пишется AI-disclosure (задел под 243-ФЗ с 01.03.2027 и ст. 50 EU AI Act).

#### 6.3.8 Авторский слой (правило 70/30)
- Схема сценария: `cold_open{vo_text≤160,on_screen≤60,duck_source}`, `inserts[≤2]{after_sid,mode:freeze_zoom|over_ducked,vo_text≤220,on_screen≤60}`, `outro{vo_text≤160,cta_style:question|loop_back|offer}`, `on_screen_title≤48`.
- **Cold open без заставки** (ТЗ §3). `freeze_zoom` использует `_crop_pan_filter`, и окна помечаются `expected_static`, чтобы QC не принимал их за зависший кадр. Outro смыкается с первым кадром (петля) и с финальным повтором баннера.
- Метрика:
  ```
  author_share = ( Σ author-only сегментов (freeze-вставки, cold open/outro при duck ≥ 12 дБ)
                 + 0.5 · Σ авторского голоса поверх приглушённого исходника
                 + 0.25 · Σ экранного авторского текста, не дублирующего транскрипт ) / длительность
  ```
- Гейт: при `rights_type <> 'own'` нужно `author_share ≥ clip_author_share_min` (0,30), иначе `qc_failed` с `author_share`. Для `own` значение только показывается (`clip_author_share_gate_for_own=false`). Это внутренний прокси трансформативности, а не гарантия площадки. Толкование 70/30 — открытый вопрос владельцу.

#### 6.3.9 QC одним проходом (`pipeline/qc.py`)
- Один вызов ffmpeg: `-filter_complex "[0:a]silencedetect,volumedetect,ebur128=peak=true[a];[0:v]blackdetect,freezedetect[v]"`, stderr разбирается теми же регулярками. Вместо шести декодов остаётся один.

| Профиль | silence d | Длительность | freeze |
|---|---|---|---|
| `generation` | 3,0 с | диапазон ниши, +3 с | строго |
| `clipping` | 4,0 с | 20–58 с (VK ≤ 60 жёстко) | окна `expected_static` вычитаются |

- Дополнительные проверки:
  - `subtitle_sync`: ASR по финалу, WER ≤ 0,25 против эталона. Фильтра `whisper` в ffmpeg 6.1.1 нет;
  - `author_share`, `banner_face_overlap`, `foreign_watermark`;
  - `library_similarity`: dHash 8 ключевых кадров против последних 200 роликов канала, порог `similarity_warn_threshold`.

#### 6.3.10 Оценка времени и диска (4 vCPU, 2 потока, nice; экстраполяция, проверяется `bench_clipping.py`)

| Этап | GigaAM |
|---|---|
| Аудио и прокси по URL (сеть) | 1–4 мин |
| Прокси для загруженного файла | 3–5 мин |
| VAD + ASR на час | 4–8 мин |
| LLM-отбор | 1–3 мин |
| **Анализ часа до кандидатов** | **≈ 8–18 мин** |
| На один утверждённый клип 45 с (секция, анализ, голос, рендер, QC, превью) | ≈ 2–3,5 мин |

Пиковый RSS тяжёлого слота ≤ 0,8 ГБ. Диск на час источника: URL-режим около 0,5–0,7 ГБ, upload-режим плюс оригинал 2–4 ГБ.

### 6.4 Трендоскаут
- **Обнаружение** (`trends/discovery.py`):
  - RSS `feeds/videos.xml` стоит 0 единиц квоты, если с IP воркера отвечает 200 (`rss_health_check` проверяет раз в сутки);
  - иначе `playlistItems.list(playlistId="UUSH"+channel_id[2:], maxResults=50)` — 1 единица за 50 шортсов;
  - при 404 `UU…` + фильтр `duration ≤ 180 с`;
  - канал резолвится через `channels.list(forHandle=@x)`. `search.list` (100 единиц) **запрещён**.
- **Снимки:** `videos.list(part=snippet,statistics,contentDetails)` пачками по 50. Лестница возрастов 1h, 3h, 6h, 12h, 24h, 48h, 72h, 7d, дальше стоп; хранится в `next_poll_at/poll_step`. Тик каждые 15 минут.
- **Outlier против медианы канала на том же возрасте** (`trends/baselines.py`, `outlier.py`):
  - `views_at_age(v, a)` — лог-лог интерполяция между снимками;
  - `baseline(c, a)` — медиана `views_at_age` последних 30 шортсов канала без самого ролика, при n ≥ 8;
  - холодный старт: `median(views зрелых ≥ 14 дней) × growth_prior(a)`, стартовые значения 1h 0,03; 3h 0,08; 6h 0,15; 24h 0,40; 72h 0,70; 7d 0,88; пересчитываются по своим данным;
  - `score = views_at_age / max(baseline, 50)` плюс робастный z по `ln(views)` через MAD;
  - тиры: `noise` < 3 ≤ `attention` < 10 ≤ `format_works`.
  - На разбор уходят ролики уровня ≥ `attention` в возрасте ≥ 24 ч, либо набравшие ≥ 10× уже к 6 ч.
- **Разбор** (`trends/analyze.py`, копия для анализа удаляется в `finally`):
  - транскрипт через `yt-dlp --skip-download --write-auto-subs --sub-langs ru,en`, без сабов — аудио ≤ 60 с через GigaAM;
  - копия `bv*[height<=360]` первых 60 с → PySceneDetect `ContentDetector` → `cuts_per_sec_first5`, `scene_count`;
  - `loop_seam` = SSIM первого и последнего кадра > 0,8;
  - `text_on_screen_density` в v1 = NULL;
  - опционально vision-провайдер (Gemini Flash-Lite по URL, ТЗ §7, через `collectors_proxy_url`).
- **Бриф** (`trends/briefs.py`): числовые поля (`cuts_per_sec_first5`, `total_duration_bucket`, `loop_seam`) **считаются**. У LLM спрашиваются только `hook_type`, `hook_duration_sec`, `payoff_timestamp_ratio`, `cta_position`, `summary`, `topic_suggestions`, `adaptation_ideas[3]`, около $0,001 за бриф.
- Раз в неделю считается Спирмен каждого поля против `ln(outlier_score)` по нише (`brief_field_stats`). Брифы питают `trend_mimicry.build_scenario_from_brief` и `trend_prior` нарезок.
- **Квота** (`api_quota_usage`, сброс в полночь PT, на куст): 80% даёт алерт, 8000 единиц — жёсткий стоп (`ResourceDeferred('quota_exhausted')`). При 75 каналах ожидается около 1,2–1,3 тыс. единиц в сутки, при работающем RSS около 350.
- **VK:** `video.get(owner_id=-gid)` через `execute` пачками, не больше 3 rps. Без права `video` — `wall.get` с сервисным ключом, затем `yt-dlp -J --skip-download`. Что из этого реально отдаёт просмотры клипов, проверяется эмпирически.

### 6.5 Коллекторы метрик своих публикаций
- **Регистрация** (одна транзакция):
  1. Строгий разбор URL (`domain/url_parse.py`):
     - YouTube: `youtube.com/shorts/{id}`, `m.youtube.com/shorts/{id}`, `youtu.be/{id}`, `watch?v={id}`, `studio.youtube.com/video/{id}/edit`, голый id, хвосты `?si=`; id строго `[A-Za-z0-9_-]{11}`;
     - VK: `vk.com|vkvideo.ru/(clip|video)(-?\d+)_(\d+)`, `?z=clip…`.
  2. Площадка должна совпасть с `channel.platform`, иначе 422. На той же площадке у job уже есть публикация — 409.
  3. INSERT `publications` (измерения копируются из job), пакет получает статус `published`, `tracking_links` пакета — `publication_id`.
  4. Создаётся алиас `yt_<id>` или `vk_<owner>_<id>` с `level='publication'`, `next_poll_at = now()+1h`, job становится `published`.
  5. `final` переезжает в `masters/YYYY/MM/<video_uid>.mp4` (`is_master=true`, **не удаляется никогда**), пишется аудит.
  6. Асинхронно `videos.list` сверяет `snippet.channelId` с `channel.platform_channel_ref` (`verify_status`) и берёт `publishedAt` из API.
- **Лестница опроса** (`metrics_poll_ladder_hours`): 1, 2, 4, 8, 12, 24, 36, 48, 72, 96, 120, 168, дальше раз в сутки до 30 дней и раз в неделю до 90. Значения на ровный возраст (24h, 72h, 7d) получаются линейной интерполяцией между снимками.
- **YouTube Data:** `videos.list?part=statistics,status&id=<≤50>&key=<ключ куста>` стоит 1 единицу за пачку. Если ролик пропал из ответа — `removed_by_platform` + алерт. `rejectionReason=uploaderAccountSuspended` — критический алерт канала.
- **YouTube Analytics** (опционально, OAuth только на чтение; приложение куста в статусе **Published**, иначе токены живут 7 дней):
  - ежедневно в 07:00 МСК для роликов в возрасте 2–30 дней: `views, engagedViews, averageViewDuration, averageViewPercentage, likes, comments, shares, subscribersGained`;
  - удержание (`elapsedVideoTimeRatio` × `audienceWatchRatio, relativeRetentionPerformance`, 100 точек, один videoId на запрос) на возрастах 72h, 7d, 30d → `retention_curves` и `failure_class` (`hook_drop | middle_sag | no_loop_lift`);
  - `insightTrafficSourceType` по каналу даёт долю ленты Shorts (диагностика среза охвата).
- **VK:** цепочка `vk_api` → `ytdlp_meta` → ручной ввод в UI.
- Все HTTP-вызовы идут через `collectors/http.py`: таймаут 15 с, 3 ретрая, прокси на провайдера (`collectors_proxy_url`). Доступность googleapis с российского VPS не гарантирована.

### 6.6 Атрибуция и конверсии
- **Коды:**
  - пакетный код генерируется при `package_build`: 5 символов Crockford base32 в нижнем регистре, без `0/o/1/l/i/u`, проверка уникальности;
  - алиас площадки `yt_<id>` или `vk_<owner>_<id>` создаётся при регистрации;
  - код канала (кнопка «Подписаться», CTR 8–15%) создаётся по запросу;
  - ручные и кампанийные коды.
  
  Все подчиняются алфавиту Telegram start-параметра `^[A-Za-z0-9_-]{2,64}$`, кириллица невозможна по построению.
- **Где код виден зрителю:**
  - в кадре по умолчанию только домен (ТЗ §5-тер); код в кадре включается `banner_variants.show_code_in_frame` (вопрос владельцу);
  - в закрепе и описании «ссылка в шапке канала»;
  - в описании длинного ролика из Related video;
  - в VK — кликабельные места сообщества.
- **Уровни атрибуции в отчётах:**
  - `exact`: код пакета или алиас → ролик;
  - `channel`: ссылка канала → канал и продукт, в разрез ролика не попадает;
  - `direct`: голый домен, неизвестный код → «прямой трафик продукта».
- **Без двойного счёта:** старт бота с лендинга приходит как `<code>_l` (`via_landing=true`). Посетители считаются как `visitors = unique_visitors (клики лендинга) + direct_bot_starts (старты без _l)`.
- **Сквозной путь:** клик → `clicks` (измерения денормализованы) → лендинг → `tg://resolve …start=<code>_l` или сайт с `utm_campaign=<code>` → продукт шлёт `bot_start`, `signup`, `first_payment` с тем же кодом → `conversions` → ролл-ап `stats_daily_traffic` → разрезы.
- **Интеграция продуктов:**
  - umka: около 30 строк эмиттера на трёх событиях. Бот уже передаёт `start_param` в бэкенд, веб-метки лежат в `touches`;
  - TMA-магазин: `start_param` + покупка;
  - VPN-бот: `/start <code>` + оплата.
  
  Эти правки делаются в репозиториях продуктов вне этого проекта; задача для владельца продуктов, см. §11.

### 6.7 Аналитика
- **Ролл-апы** (`analytics/rollups.py`, `stats_rollup(days=N)`):
  1. `views_eod` — интерполяция снимков на конец бизнес-дня без экстраполяции; `views_delta = eod(d) − eod(d−1)`, upsert.
  2. Трафик одним SQL на день и ссылку (параметры приведены явно):
  ```sql
  INSERT INTO stats_daily_traffic (day, tracking_link_id, landing_views, unique_visitors, to_telegram, to_site, direct)
  SELECT (c.occurred_at AT TIME ZONE %(tz)s)::date, c.tracking_link_id,
         count(*) FILTER (WHERE route = 'landing'), count(DISTINCT visitor_key),
         count(*) FILTER (WHERE route = 'to_telegram'), count(*) FILTER (WHERE route = 'to_site'),
         count(*) FILTER (WHERE route = 'direct')
  FROM clicks c
  WHERE NOT c.is_bot AND c.tracking_link_id IS NOT NULL
    AND c.occurred_at >= now() - make_interval(days => CAST(%(days)s AS int))
  GROUP BY 1, 2
  ON CONFLICT (day, tracking_link_id) DO UPDATE SET landing_views = EXCLUDED.landing_views,
    unique_visitors = EXCLUDED.unique_visitors, to_telegram = EXCLUDED.to_telegram,
    to_site = EXCLUDED.to_site, direct = EXCLUDED.direct, computed_at = now();
  ```
  Конверсии считаются так же: `unique_bot_starts = count(DISTINCT user_ref_hash) FILTER (WHERE event_type='bot_start')`, `direct_bot_starts … AND NOT via_landing`.
- **Формулы** (`analytics/formulas.py`):
  - `ctr_per_1k = visitors / engaged_views × 1000`; если engaged нет, берутся публичные просмотры с пометкой. Публичные просмотры завышены на 20–30% с 24.08.2026;
  - `spend_usd` — сумма `generation_events` job группы. Накладные (трендоскаут, ASR без job) идут отдельно как `overhead_usd`, в итог попадают при `include_overhead=true`. Стоимость ASR и LLM источника размазывается по отрендеренным из него клипам, стоимость брифа — по роликам по нему;
  - `cost_per_published_video_usd` = расход **всех** job периода (включая брак) / число опубликованных — честная цена из ТЗ §8;
  - `cost_per_visitor_usd`, `cost_per_bot_start_usd`, `cost_per_first_payment_usd`;
  - выручка в рублях, расход в рублях по `fx_usd_rub`.
- **A/B** (`analytics/stats.py`): один ролик с двумя баннерами **не публикуется** (дубль). A/B идёт между разными роликами:
  - Манн–Уитни (scipy) по `ln(views_72h)` и CTR на ролик;
  - pooled CTR с доверительным интервалом Пуассон–Гамма;
  - в расчёт идут только ролики старше `ab_min_age_hours` (72);
  - вердикт `ready=false`, пока в каждом варианте меньше `ab_min_videos_per_variant` (20) зрелых роликов или меньше 50 посетителей всего.
  ```json
  {"campaign_id":7,"ready":false,"reason":"not_enough_mature_videos","min_videos_per_variant":20,"min_age_hours":72,
   "variants":[{"id":21,"code":"A","weight":50,"videos":14,"videos_mature":10,"views_72h_median":6400,
                "visitors":190,"ctr_per_1k_pooled":2.34,"ctr_ci":[1.9,2.8],"bot_starts":72,"first_payments":2,
                "spend_usd":0.72,"cost_per_visitor_usd":0.0041}],
   "tests":[{"metric":"ln_views_72h","method":"mann_whitney_u","a":"A","b":"B","u":57.0,"p_value":0.64,"n_a":10,"n_b":11}]}
  ```

---

## 7. Фронтенд

### 7.1 Доставка и сборка
- `web/next.config.ts`: `output: 'export'` включается только при `NEXT_EXPORT=1`. Плюс `trailingSlash: true`, `images.unoptimized: true`. В dev работают `rewrites` `/api/:path*` → `http://127.0.0.1:18300/api/:path*`, то есть тоже same-origin.
- Динамических сегментов нет: деталь адресуется query-параметром (`?job=`, `?id=`). Каждая страница с `useSearchParams` обёрнута в `PageSuspense` со скелетоном. Защита маршрутов — клиентский `AuthGate`: скелетон до ответа `GET /auth/me`, при 401 переход на `/login?next=`.
- **Сборка:** `NEXT_EXPORT=1 nice -n 19 NODE_OPTIONS=--max-old-space-size=1536 next build && node scripts/check-no-external.mjs`. Собирать лучше не на боксе с живыми сервисами (swap 100%), а в CI или на машине разработчика, и отвозить `out/` через rsync (3–6 МБ).
- **Бюджет бандла:** оболочка ≤ 180 КБ gz. recharts, wavesurfer и CodeMirror грузятся `dynamic()` только на своих экранах.
- **Раздача:** nginx `root web/out; try_files $uri $uri/ $uri.html =404;`. Запасной вариант, если 80/443 держит Coolify/Traefik: FastAPI `StaticFiles(html=True)` отдаёт `out/`, медиа через `FileResponse` с Range.

### 7.2 Стек (npm, лицензии проверены архитектором фронтенда 26.09.2026)

| Пакет | Лицензия | Назначение |
|---|---|---|
| next 16.3.x, react/react-dom 19.2.x, typescript ^5 | MIT/Apache-2.0 | как в retentionos |
| tailwindcss 4.3 + @tailwindcss/postcss | MIT | токены через `@theme inline` |
| @tanstack/react-query 5, @tanstack/react-table 9, @tanstack/react-virtual 3 | MIT | кэш и мутации, плотные таблицы, виртуализация транскрипта |
| zustand 5, nuqs 2 | MIT | тема, плеер, редактор нарезки; фильтры в URL |
| recharts 3.10 | MIT | графики |
| lucide-react | ISC | иконки (в бандле) |
| openapi-typescript 7.13 + openapi-fetch 0.17 | MIT | типы и клиент из `/api/v1/openapi.json` |
| react-hook-form 7 + zod 4 + @hookform/resolvers | MIT | формы |
| radix-ui 1.x, cmdk 1.1, sonner 2, react-hotkeys-hook 5 | MIT | примитивы, палитра Ctrl+K, тосты с «Отменить», скоупы клавиш |
| wavesurfer.js **7.12.12 (зафиксирован)** + regions/timeline/zoom | BSD-3 | волна в студии нарезок; 8.0.1 от 24.09.2026 пока не берём |
| tus-js-client 4.3 | MIT | возобновляемая загрузка |
| react-rnd 10.5 | MIT | перетаскивание плашки в редакторе баннера |
| @uiw/react-codemirror + @codemirror/lang-yaml | MIT | «сырой YAML» ниши. **Monaco не берём**: грузится с jsdelivr |
| date-fns 4 + @date-fns/tz | MIT | периоды, МСК |
| @fontsource-variable/onest, @fontsource-variable/jetbrains-mono | OFL-1.1 | шрифты с кириллицей в бандле |
| msw 2, @playwright/test 1.63 | MIT/Apache-2.0 | моки и e2e под логином |

Из `retentionos/apps/web` **копируются** (с пометкой источника):
- `packages/ui/components/*` (card, badge, button, skeleton, empty-state, input, form-field, dialog, table, tabs, key-value, stat-tile, section-card, tooltip, status-badge, pagination, date-range-input, callout) + `cn`;
- `lib/api-client.ts` (семантика ошибок), `lib/sse-parse.ts`, `lib/store/theme.ts`;
- тройное объявление токенов в `globals.css`;
- `components/layout/*`, `section-error`, `kpi-card`, паттерны `status-meta.ts` и `metric-definitions.ts`.

**Не копируются:** `next/font/google` (запрещён ESLint `no-restricted-imports`), заголовки org/role, i18n-слой (только RU), Dockerfile с `next dev`. Workspace-связей между проектами нет.

### 7.3 Карта маршрутов и экраны

Сайдбар (216 px, свёртка до 56 px), группы по вопросу оператора:
- **Пульт:** Обзор.
- **Производство:** Конвейер, Приёмка (янтарный бейдж), Пакеты (бейдж).
- **Источники:** Нарезки, Трендоскаут (бейдж ≥10×).
- **Продвижение:** Продукты и баннеры.
- **Анализ:** Аналитика.
- **Настройка:** Ниши, Каналы, Настройки.

Шапка (44 px), слева направо:
- крошки;
- **индикатор воркера** (idle / рендер `uid · стадия · 42%` / отложено: причина / down), по клику поповер с очередью тяжёлых задач, диском, памятью и PSI;
- **бюджет дня** с полоской;
- «Новый ролик» (`N`);
- алерты;
- tally-точка SSE (live или polling);
- тема и пользователь.

Палитра Ctrl/Cmd+K: переход по `video_uid`, `yt_…`/`vk_…`, коду пакета, каналу; действия.

| URL | Экран | Состояние в URL | Данные (эндпоинты) | Ключевые действия |
|---|---|---|---|---|
| `/login` | Вход | `next` | `POST /auth/login`, `GET /auth/me` | вход |
| `/` | Обзор | `range`, `channel`, `basis` | `/overview`, `/analytics/timeseries`, `/alerts`, `/analytics/videos?limit=10`, `/jobs/stage-counts`, `/budget` | KPI со спарклайнами и определениями; колонка «Требует тебя»; малые мультипликаторы без двойной оси; мини-воронка домен/бот; алерты; топ роликов; здоровье конвейера; онбординг-чек-лист при пустой БД |
| `/production` | Конвейер | `stage`, `kind`, `channel`, `q`, `job`, `tab` | `/jobs`, `/jobs/stage-counts`, `/jobs/{id}`, `/jobs/{id}/tasks`, `/jobs/{id}/history`, `/tasks/{id}/log/stream`, `/system/status` | лента стадий-фильтров; таблица 28–32 px; панель задачи (таймлайн стадий, мини-плеер, вкладки Сценарий / Сцены / QC / Стоимость / Логи / Файлы / История); повтор, «с…», отмена, клон, приоритет, перегенерация сцены. **Красный callout, если TTS в mock** |
| `/review` | Приёмка | `job`, `platform` | `/review-queue`, `/jobs/{id}/review-context`, `POST /jobs/{id}/review`, `/review/undo`, `/meta/reject-reasons`, `/meta/safe-zones` | три колонки: очередь / плеер 9:16 с SVG-оверлеем `viewBox 0 0 1080 1920` (мёртвые зоны, боксы баннера, зона субтитров) и дорожкой (склейки, окно хука, окна баннера) / факты (караоке-текст, QC, флаги, похожесть, сравнение с прошлой попыткой). Клавиши: `A`/`R`/`X`+`1–9`/`U`, `J`/`K`, `Space`, `←/→`, `,`/`.`, `S`/`P`/`B`/`F`/`L`/`M`/`?`. Оптимистичное удаление из очереди, тост «Отменить» 6 с, 409 «уже решено в Telegram» |
| `/packages` | Пакеты | `status`, `channel`, `package` | `/packages`, `PATCH /packages/{id}`, `/checklist`, `/mark-uploaded`, `/packages/{id}/video`, `POST /publications/parse-url`, `POST /publications` | группировка по каналу с **подсказкой аккаунта**; копирование полей в один клик (клавиши `1–6`); автосохранение текстов; серверный чек-лист (включая AI-disclosure); Web Share на телефоне; поле URL с живым разбором (`lib/platform-url.ts` повторяет бэкенд) → регистрация → вкладка «Проблемы», если `verify_status` ≠ ok |
| `/clipping` | Источники | `status` | `/sources`, tus `/uploads/tus`, `POST /sources/from-upload|from-url`, `/system/status` | таблица с прогрессом и местом в тяжёлой очереди; диалог «Добавить» (файл или ссылка + **обязательная форма прав**, свободный диск и оценка) |
| `/clipping/source` | Студия нарезки | `id`, `clip`, `t` | `/sources/{id}`, `/transcript`, `/media/proxy`, `/media/audio`, `/peaks`, `/storyboard`, `/scenes`, `/clip-candidates`, `PATCH /clip-candidates/{id}`, `POST /sources/{id}/clips` | плеер прокси с рамкой 9:16 (центр / ручной X / blur); вертикальный предпросмотр; AI-моменты со score и подоценками; дорожка wavesurfer (волна, слова, склейки, регионы, прилипание, hover-кадр из спрайта); виртуализированный транскрипт с монтажом по тексту; инспектор (in/out, зоны длительности, авторский слой, **индикатор 70/30** для чужих источников, продукт и вариант); «В рендер». Клавиши `I`/`O`, `J`/`K`/`L`, `[`/`]`, `Enter`, `Del`, `Z`. Только десктоп |
| `/trends` | Трендоскаут | `tab=outliers|channels|briefs`, `pool`, `tier`, `brief` | `/competitor-videos`, `/competitors`, `/trend-briefs`, `/trend-scout/field-stats` | аутлаеры (миниатюра из кеша, ×score, тир, VPH, спарклайн); доноры (добавление по URL/@handle); брифы со «структурной полосой»; «Сделать ролик по брифу»; график корреляций. iframe площадок нет |
| `/products` · `/products/view` | Продукты | `id`, `tab=overview|banners|stats|webhook` | `/products`, `/campaigns`, `/campaigns/{id}/variants`, `/products/{id}/integration`, `/conversions`, `/campaigns/{id}/experiment` | карточка (лендинг, редиректор, deeplink, UTM); вебхук (секрет только на запись, тестовое событие, последние 20 событий); кампании и варианты; A/B (strip-plot, «рано судить: нужно ещё N») |
| `/products/banner` | Редактор баннера | `template`, `variant`, `platform`, `t` | `/banner-templates/{id}`, `/validate`, `/preview`, `/meta/safe-zones`, `/assets/{id}/file` | форма (слой 1, слой 2, тайминг, CTA голосом, маркировка ERID выключена, `show_code_in_frame`, вес) + SVG-превью 1080×1920 с мёртвыми зонами и силуэтами UI; перетаскивание плашки; **валидация на сервере** (debounce 150 мс); «Кадр из рендера» (PNG тем же `banner_png.py`) |
| `/niches` · `/niches/edit` | Ниши | `id` | `/niches`, `/niches/{id}`, `/versions`, `/validate`, `/test-script`, `/voice-sample`, `/text/check-forbidden`, `/providers` | форма-зеркало YAML (сценарий, хуки с плейсхолдерами, запрещённые фразы с тестером, голос с примером, визуал без zoompan, субтитры с живым караоке, длительность с зонами ТЗ, хэштеги); сухой прогон; «сырой YAML» (CodeMirror); история версий с diff; 409 на устаревшей версии |
| `/channels` | Каналы | `channel`, `view=list|clusters` | `/channels`, `/channels/{id}/health`, `/incidents`, `/tracking-link`, `/oauth/youtube/start` | таблица (куст, статус, Advanced features, подписчики, загрузок против плана, охват); drawer (форма, OAuth YT Analytics с предупреждением про Testing, журнал инцидентов, **предупреждение о совпадающем профиле**) |
| `/analytics` | Аналитика | `tab=funnel|videos|breakdown|cohorts|retention|costs` + фильтры | `/analytics/*` | строка фильтров в URL; плашка свежести (счётчики, лаг Analytics, квота); воронка двух веток; таблица роликов с выбором колонок и CSV (BOM); разрезы (медиана и p75, не среднее); когорты-тепловая карта; удержание; стоимость (цена опубликованного ролика). Опорная линия 24.08.2026 на всех временных графиках |
| `/analytics/video` | Карточка ролика | `id` | `/publications/{id}`, `/metrics`, `/retention`, `/traffic` | просмотры от возраста на фоне коридора p25–p75 канала, удержание с классом провала, переходы по часам, конверсии, стоимость |
| `/settings` | Настройки | `tab=providers|budget|worker|attribution|notifications|users|audit|system` | `/providers`, `/secrets`, `/budget`, `/settings`, `/users`, `/audit-log`, `/system/status`, `/maintenance/*` | провайдеры (секрет только на запись, «Проверить связь», пометка ресурсоёмкости); бюджет и курс; воркер (слот = 1 только для чтения, пороги, очистка с оценкой); пользователи и роли; аудит; система |

Все сегменты и параметры латиницей. Кириллица появляется только в значении `q` через `URLSearchParams`. На телефоне полноценно работают **Приёмка** и **Пакеты**, остальные экраны остаются читаемыми.

### 7.4 Общие механики
- **Данные.** openapi-fetch с middleware:
  - 401 → login; 409 → `ConflictError` (тост и перечитывание); 422 → ошибки по полям формы; 423 → «платные шаги на паузе»; 507 → «нет места на диске»;
  - все мутации шлют `X-CSRF-Token`;
  - ключи запросов `['jobs', filters]`, `['job', id]`, …; `staleTime` 15 с, `keepPreviousData` для таблиц.
- **Realtime.** Один `EventSource('/api/v1/events/stream')`, хук `useLiveEvents` делает `invalidateQueries` или `setQueryData`. Если событий нет дольше 45 с, индикатор становится серым, и видимые экраны переходят на `refetchInterval` 10 с. Лог задачи открывается отдельным стримом только при видимой вкладке.
- **Медиа.** URL берутся только из ответов API. Пути фронт не собирает.
- **Время и числа.** Хранится UTC, показывается МСК с пометкой. `Intl` ru-RU, tabular-nums, `$` с 3–4 знаками для себестоимости.
- **Доступность.** Видимый фокус; статус всегда цветом **и** иконкой с подписью; `aria-live` для тостов и SSE; `prefers-reduced-motion`; контраст AA.

### 7.5 Компоненты (дерево)
```
RootLayout(fonts, data-theme) → Providers(QueryClient, LiveEvents, Toaster, Hotkeys)
 ├ LoginPage
 └ ConsoleLayout → AuthGate → AppShell
    ├ Sidebar(NavList, NavItem+badge) · Header(Breadcrumbs, WorkerIndicator, BudgetMeter, NewJobButton, AlertsBell, LiveDot, ThemeToggle, UserMenu)
    ├ CommandPalette · NewJobSheet(SourceTabs, ChannelSelect, TopicsInput, ProductVariantPicker, EstimateSummary)
    └ main:
       Overview(FilterBar, KpiRow, TodoPanel, TrendSmallMultiples, MiniFunnel, AlertsPanel, LegalNotice, TopVideosTable, PipelineHealth)
       Production(StageRail, JobsTable, JobDetailPane(StageTimeline, MiniPlayer, Tabs…, JobActions))
       Review(ReviewQueueList, SessionCounter, VerticalPlayer(OverlaySvg, PlayerTimeline), FactsPanel(KaraokeScript, QcChips, FlagsList, SimilarityCard, AttemptsCompare), DecisionBar(ReasonGrid, UndoToast))
       Packages(StatusTabs, ChannelGroup(AccountHint), PackageCard(CopyField×6, Checklist, PublishedUrlInput, AttributionBlock))
       Clipping(SourcesTable, AddSourceDialog(TusUploader, UrlForm, RightsForm, DiskEstimate))
       ClipStudio(SourcePlayer(CropFrame), VerticalPreview, MomentsPanel, TranscriptPanel, ClipTimeline(wavesurfer), ClipInspector(AuthorShareMeter…), SendToRenderBar)
       Trends(OutlierFeed, CompetitorTable, BriefList(StructureStrip), CorrelationChart)
       Products(ProductView(Overview, Webhook, Variants, AbStats)) · BannerEditor(BannerForm, BannerPreview(OverlaySvg, DraggableOfferBox, TimeScrubber), WarningsList, RenderFrameCompare)
       Niches(NicheEditor(…, RawYaml, VersionHistory, DryRunPanel)) · Channels(ChannelDrawer) · Analytics(…) · VideoAnalytics · Settings(…)
 Общие: SectionError, EmptyState, Skeleton, StatusBadge, KpiTile, CopyField, Timecode, MoneyCell, Sparkline, PageSuspense
```

### 7.6 Визуальное направление «Аппаратная»
- Идея: монтажная аппаратная, а не SaaS-админка. Графитовый фон, видео на абсолютно чёрном поле, циановые направляющие как title-safe-рамки. **Цвет говорит, чья очередь действовать.** Тёмная тема по умолчанию: оператор смотрит видео.
- Токены (oklch) объявляются трижды по схеме retentionos: `:root`, `@media (prefers-color-scheme: dark) :root:not([data-theme="light"])`, `:root[data-theme="dark"]`. У `body` явный фон.

| Токен | Dark | Light | Смысл |
|---|---|---|---|
| `--bg` | 0.16 0.006 250 | 0.985 0.002 250 | фон |
| `--surface` / `--surface-2` | 0.195 / 0.23 | 1 0 0 / 0.965 | панели и шапки таблиц |
| `--border` | 0.30 0.008 250 | 0.90 0.004 250 | 1 px, без теней |
| `--fg` / `--fg-muted` | 0.94 / 0.70 | 0.20 / 0.47 | текст |
| `--accent` (циан) | 0.80 0.11 200 | 0.52 0.11 210 | фокус, выбор, основная кнопка, safe-zone |
| `--human` (янтарь) | 0.83 0.14 80 | 0.62 0.14 70 | «твой ход»: приёмка, пакеты, «вставь ссылку» |
| `--machine` (фиолет) | 0.74 0.13 295 | 0.52 0.16 295 | идущие стадии, AI-моменты |
| `--positive` / `--warning` / `--negative` | 0.74 0.15 150 / 0.75 0.16 55 / 0.67 0.20 25 | 0.52 / 0.60 / 0.55 | ок / 80% бюджета / провал, санкции |
| `--video` | #000 | #000 | поле плеера |

- **Графики.** Категориальная палитра фиксирована и проверена на CVD: dark `#3987e5 #d95926 #199e70`, light `#2a78d6 #eb6834 #1baf7a`. Цвет закреплён за метрикой: просмотры — слот 1, переходы — 2, старты — 3. Воронка и когорты — последовательная синяя рампа. Двойных осей нет, только малые мультипликаторы. Статусные цвета в сериях не используются.
- **Типографика.** Onest Variable (UI, 13/19 px, шкала 11–32), JetBrains Mono (таймкоды `00:12.40`, uid, коды, деньги, логи), Montserrat из `config/fonts` только в превью баннера и субтитров (WYSIWYG с рендером).
- **Плотность.** Строки 32 и 28 px, сетка 4 px, радиусы 6 и 8 px, **0 у видео**, движение 120–160 мс. Эмодзи нет, только lucide.

### 7.7 Гарантия «без чужих CDN»
1. Шрифты fontsource лежат в `out/_next/static/media`. `next/font/google` запрещён ESLint.
2. Иконки, графики и волна собираются в бандл. Monaco не используется.
3. Миниатюры конкурентов отдаются только через API (локальный кеш). `<iframe>` YouTube и VK нет.
4. CSP на nginx: `default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; font-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; frame-ancestors 'none'`. `unsafe-inline` нужен для инлайн-скриптов RSC-payload статического экспорта.
5. `scripts/check-no-external.mjs` ищет `https?://` в `out/**/*.{html,js,css}` вне allowlist (свои домены, `w3.org/2000/svg`) и валит сборку.
6. Внешней аналитики и счётчиков нет.

---

## 8. Ресурсы и эксплуатация

### 8.1 Профили ресурсов (`RESOURCE_PROFILE`)

| Параметр | `shared_dev` (эта машина: 4 ядра, 7,7 ГБ, swap занят, диск 82%) | `dedicated_small` (VPS 2 vCPU / 4 ГБ / 50 ГБ) |
|---|---|---|
| Тяжёлых процессов | 1 | 1 |
| ffmpeg `-threads` / ORT intra / cv2 | 2 / 2 / 1 | 2 / 2 / 1 |
| systemd heavy | `Nice=15 CPUWeight=20 CPUQuota=200% MemoryHigh=1300M MemoryMax=1800M IOReadBandwidthMax=/dev/sda 60M IOWriteBandwidthMax=/dev/sda 40M` | `Nice=5 MemoryMax=3G` (без CPUQuota) |
| Допуск перед heavy | `MemAvailable ≥ 1200 МБ`, PSI mem some avg10 < 10, PSI cpu some avg10 < 40, load1 < 3,0 | `MemAvailable ≥ 600 МБ`, load1 < 2,5 |
| Диск | `< 12 ГБ` — новые загрузки не принимаются (507); `< 8 ГБ` — пауза heavy + алерт; `< 5 ГБ` — аварийный GC (прокси и оригиналы терминальных источников, tmp), текущая стадия падает чисто | то же, квота `media_max_gb` |
| worker-light | `--concurrency 2`, `MemoryMax=512M` | `--concurrency 3` |
| web в dev | только по профилю compose `web` | — |

Env потоков для heavy: `OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMBA_NUM_THREADS=1`. `ionice` не используется как механизм: планировщик `mq-deadline` приоритеты не учитывает.

### 8.2 Политика хранения (`cleanup`, ежедневно 04:00)

| Что | Срок |
|---|---|
| `jobs/<uid>/` рабочие (scene_*, scenes_video, voice, секции, crop.cmd) | после `qc` passed и решения: approved, published и rejected через `work_artifacts_retain_days` (7) |
| final и preview отклонённых и отменённых | `rejected_jobs_retain_days` (3) |
| **Мастер опубликованного** (`masters/YYYY/MM/`) | **никогда** (ТЗ §10) |
| `audio16k.wav` | сразу после ASR |
| прокси источника | `proxy_retain_days` (14) после `ready` |
| оригинал загрузки | `original_retain_days` (30) или пока все кандидаты не терминальны |
| копии конкурентов для анализа | в `finally` |
| незавершённые tus | по `expires_at` (24 ч) |
| логи задач | 14 дней |
| legacy `render_tmp/` | один раз после миграции артефактов (`autoshorts-admin adopt-legacy-media`) |

`MEDIA_ROOT`:
```
MEDIA_ROOT/
  jobs/<video_uid>/…   masters/YYYY/MM/<video_uid>.mp4
  sources/<source_id>/{original.*, proxy_540p.mp4, audio.m4a, peaks.json, storyboard_<n>.jpg, transcript.json.gz, sections/}
  uploads/<upload_id>.part   rights/<source_id>/…   thumbs/<platform>/<video_id>.jpg
  banners/<hash>.png   logs/tasks/<task_id>.log   tmp/
MODELS_DIR/ gigaam-v3-e2e-ctc-int8/  silero_vad.onnx  face_detection_yunet_2026may.onnx
```

### 8.3 Конфигурация
- **Env** (только инфраструктура, латиница): `APP_ENV`, `DATABASE_URL`, `MEDIA_ROOT`, `MODELS_DIR`, `BACKUP_DIR`, `AS_SECRETS_KEY` или `AS_SECRETS_KEY_FILE` (Fernet, MultiFernet для ротации), `REDIRECT_SALT`, `SESSION_COOKIE_SECURE`, `ADMIN_BASE_URL`, `PUBLIC_BASE_URL`, `RESOURCE_PROFILE`, `LOG_LEVEL`, `LOG_FORMAT`.
- **Секреты** (таблица `secrets`, шифрование Fernet): `FAL_API_KEY`, `LLM_API_KEY`, `GIGACHAT_AUTH_KEY`, `YOUTUBE_API_KEY_<CLUSTER>`, `VK_ACCESS_TOKEN`, `TELEGRAM_BOT_TOKEN`, `PRODUCT_WEBHOOK_<PRODUCT>`, `YANDEX_SPEECHKIT_API_KEY`, `ASR_CLOUD_API_KEY`, `GEMINI_API_KEY`, `YT_OAUTH_REFRESH_<CHANNEL>`.
  - Порядок чтения: БД, затем env с тем же именем (текущий `.env` продолжает работать), затем None.
  - API значения не отдаёт. Кеш в процессе 60 с. Логгер вырезает `*key*`, `*token*`, `*secret*`, `password`.
- **Настройки** (`domain/settings_schema.py`, в БД только переопределения): `business_timezone`, `resource_profile`, `disk_soft_min_gb`, `disk_hard_min_gb`, `disk_emergency_gb`, `media_max_gb`, `heavy_max_load1`, `heavy_min_mem_available_mb`, `heavy_max_psi_mem10`, `heavy_max_psi_cpu10`, `ffmpeg_threads`, `render_preset`, `render_crf`, `preview_preset`, `metrics_poll_ladder_hours`, `clip_author_share_min`, `clip_author_share_gate_for_own`, `clip_score_weights`, `similarity_warn_threshold`, `qc_subtitle_sync_enabled`, `source_max_hours`, `upload_max_gb`, `proxy_retain_days`, `original_retain_days`, `work_artifacts_retain_days`, `rejected_jobs_retain_days`, `review_chat_id`, `alert_chat_id`, `fx_usd_rub`, `ab_min_videos_per_variant`, `ab_min_age_hours`, `outlier_attention`, `outlier_format_works`, `yt_quota_soft_pct`, `yt_quota_hard_units`, `yt_upload_units`, `redirect_default_product`, `collectors_proxy_url`, `telegram_proxy_url`, `safe_zone_presets`, `allow_mock_in_prod` (false).

### 8.4 docker-compose для dev

Порты только 127.0.0.1 и только свободные. Заняты: 3000, 3001, 3004, 3100, 5432, 5433, 8000, 8080, 8090, 8091, 8100 и др. (`ss -ltn`).
```yaml
# Режим A: всё в compose. Режим B (экономный): postgres/web не поднимаем, api/workers из .venv
# против отдельной базы; тяжёлый воркер и web поднимаются профилями только по требованию.
x-app: &app
  build: {context: ., dockerfile: deploy/Dockerfile}
  env_file: .env
  environment:
    MEDIA_ROOT: /var/lib/autoshorts/media
    MODELS_DIR: /var/lib/autoshorts/models
    DATABASE_URL: postgresql://autoshorts:autoshorts@postgres:5432/autoshorts
    RESOURCE_PROFILE: shared_dev
  volumes: ["./src:/app/src:ro", "./config:/app/config:ro", "./var/media:/var/lib/autoshorts/media", "./var/models:/var/lib/autoshorts/models"]
  restart: unless-stopped
services:
  postgres:
    image: postgres:16-alpine
    profiles: ["db"]
    environment: {POSTGRES_USER: autoshorts, POSTGRES_PASSWORD: autoshorts, POSTGRES_DB: autoshorts}
    command: ["postgres","-c","shared_buffers=64MB","-c","max_connections=30","-c","work_mem=4MB",
              "-c","maintenance_work_mem=32MB","-c","effective_cache_size=256MB"]
    ports: ["127.0.0.1:5442:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
    mem_limit: 384m
    healthcheck: {test: ["CMD-SHELL","pg_isready -U autoshorts"], interval: 5s, retries: 10}
  migrate:
    <<: *app
    profiles: ["db"]
    command: ["autoshorts-admin","migrate"]
    restart: "no"
    depends_on: {postgres: {condition: service_healthy}}
  api:
    <<: *app
    command: ["uvicorn","autoshorts.api.app:app","--host","0.0.0.0","--port","8000","--workers","1"]
    ports: ["127.0.0.1:18300:8000"]
    mem_limit: 384m
    cpus: "1.0"
  public:
    <<: *app
    command: ["uvicorn","autoshorts.public.app:app","--host","0.0.0.0","--port","8001"]
    ports: ["127.0.0.1:18302:8001"]
    mem_limit: 160m
    cpus: "0.5"
  worker-light:
    <<: *app
    command: ["autoshorts-worker","--class","light","--concurrency","2"]
    mem_limit: 512m
    cpus: "1.0"
  worker-heavy:
    <<: *app
    profiles: ["heavy"]
    command: ["autoshorts-worker","--class","heavy"]
    environment: {FFMPEG_THREADS: "2", OMP_NUM_THREADS: "2", OPENBLAS_NUM_THREADS: "1"}
    mem_limit: 1536m
    cpus: "2.0"
    cpu_shares: 256
  bot:
    <<: *app
    profiles: ["bot"]
    command: ["python","-m","autoshorts.bot.telegram_bot"]
    mem_limit: 192m
  web:
    image: node:22-alpine
    profiles: ["web"]
    working_dir: /web
    command: ["sh","-c","npm ci && npm run dev -- -p 3000 -H 0.0.0.0"]
    environment: {API_ORIGIN: "http://api:8000", NEXT_TELEMETRY_DISABLED: "1"}
    volumes: ["./web:/web"]
    ports: ["127.0.0.1:18301:3000"]
    mem_limit: 1g
volumes: {pgdata: {}}
```
Dockerfile: `python:3.12-slim-bookworm` + `apt-get install ffmpeg fonts-dejavu-core nodejs` (ffmpeg в Debian собран с libass; node нужен как JS-рантайм yt-dlp). Модели скачиваются **явной** командой `autoshorts-admin models-fetch` с проверкой sha256, а не в рантайме. Тестовая БД задаётся `TEST_DATABASE_URL`: compose-профиль `db` (5442) или отдельная база в системном PG16, если есть доступ.

### 8.5 Прод на отдельном VPS (ТЗ §8)
- **systemd:**
  - `autoshorts-api`, `-public`, `-worker-light`, `-worker-heavy` (лимиты из §8.1), `-bot`;
  - таймеры `autoshorts-backup` (03:00) и `autoshorts-restore-check` (еженедельно);
  - все слушают 127.0.0.1.
- **nginx, admin-хост:**
  ```nginx
  root /opt/auto-shorts/web/out;
  location / { try_files $uri $uri/ $uri.html =404; }
  location /api/ { proxy_pass http://127.0.0.1:18300; proxy_set_header X-Forwarded-Proto $scheme; client_max_body_size 40m; }
  location ~ ^/api/v1/(events/stream|tasks/\d+/log/stream) { proxy_pass http://127.0.0.1:18300; proxy_buffering off; proxy_read_timeout 1h; }
  location /api/v1/uploads/tus { proxy_pass http://127.0.0.1:18300; client_max_body_size 40m; proxy_request_buffering off; proxy_read_timeout 300s; }
  location /_protected_media/ { internal; alias /var/lib/autoshorts/media/; }
  add_header Content-Security-Policy "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; font-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; frame-ancestors 'none'" always;
  ```
  Admin-хост дополнительно можно закрыть allowlist по IP или VPN.
- **nginx, public-хост** (`u.<домен>`): всё проксируется на 127.0.0.1:18302, TLS. Если 80/443 держит Coolify/Traefik (см. `deploy/autoshorts-dashboard.service`), маршруты заводятся через Traefik с тем же набором заголовков.
- **Выкладка** (`deploy/deploy.sh`, только после проверки на стенде):
  1. `git pull` тега;
  2. `pip install -e .[clipping]`;
  3. `autoshorts-admin migrate` (автоматический pre-migrate дамп);
  4. `rsync web/out`;
  5. `systemctl restart` по одному;
  6. `curl /health/ready`;
  7. смоук `scripts/e2e_smoke.py --prod-readonly`.
- **Бэкапы:**
  - `pg_dump -Fc` ежедневно в 03:00, хранить 7 дневных и 4 недельных;
  - перед каждой миграцией отдельный дамп;
  - `masters/` и `rights/` синхронизируются `rsync`/`rclone` за пределы VPS (цель выбирает владелец);
  - раз в неделю `autoshorts-admin restore-check`: дамп восстанавливается во временную базу, сверяется число строк ключевых таблиц, база удаляется, при ошибке открывается алерт.
- **Логи:**
  - JSON в stdout (journald) с полями `ts, level, logger, msg, request_id, user_id, task_id, job_id, kind, duration_ms`; `X-Request-ID`;
  - полный stderr ffmpeg пишется в `logs/tasks/<id>.log`, хвост 4 КБ — в `tasks.last_error`.
- **Алерты** (`alerts_eval` раз в минуту, дедупликация, в Telegram только открытие и закрытие): `disk_low`, `worker_down`, `heavy_slot_stuck`, `budget_80`, `budget_paused`, `provider_failing` (5 ошибок подряд), `collector_failing`, `quota_80`, `publication_removed`, `channel_sanction_signal`, `channel_reach_drop` (5 последних роликов ниже p25), `qc_fail_spike` (больше 50% из 20), `migrations_pending`, `product_webhook_silent` (N дней без событий), `tts_mock_in_prod`, `ad_marking_deadline` (напоминание до 01.01.2027), `package_blocked` (ролик одобрен, а пакет не собрался: права, оригинальность, erid; открывает `package_build`, закрывает удачная сборка).
- **Внешний сторож:** cron-скрипт на хосте дергает `/health/ready` и шлёт в Telegram **только изменения** состояния (как сторож Умки, переменные латиницей).

---

## 9. Что берём из GitHub и реестров

Лицензии и активность проверены архитекторами 26.09.2026. Лицензии openshorts (MIT для ядра, `cloud/` под коммерческой лицензией) и onnx-asr (MIT, поддержка GigaAM v3, таймстемпов и VAD) перепроверены при сведении. GitHub API на момент сведения отвечал rate limit. Правило ТЗ: **перед добавлением зависимости перепроверить `pushed_at`**.

| Репозиторий / пакет | Лицензия | Активность | Что берём (файл → функция) | Куда |
|---|---|---|---|---|
| mutonby/openshorts | MIT (ядро); `cloud/` коммерческая | активен (2026-09-25) | `clip_selection.py`: `build_transcript_windows`, `dedupe_overlapping`, `snap_clip_to_words`, `compact_words`, `trim_to_best`, `clip_count_targets`; `llm_backend.py`: цепочка `json_schema → json_object → plain`; `transcribe_backends.py`: `host_asr_slot`, `_words_from_tokens`, `_has_audio_stream`; `main.py`: **только класс `SmoothedCameraman`**; `reframe_v2.py`: `delivery_size`, `source_already_fits`, `dedupe_sendcmd_lines`, `scene_frame_ranges`, `general_filtergraph`, `apply_crop_overrides`; `ffmpeg_utils.py`: `escape_filter_value`, `blurred_backdrop`, идея `mark_ai_generated`. Перенос с MIT-нотисом | `clipping/*`, `providers/llm/structured.py`, `providers/asr/gigaam_onnx.py`, `core/resources.py` |
| ↳ **не брать** | — | — | `main.py` целиком (импортирует **ultralytics, AGPL-3.0**), TransNetV2 (torch), `cloud/`, legacy `mp.solutions` | — |
| TaimourKhan2132/opensource-clipping | MIT | 2026-09-19 | `clipping/studio/watermark.py`: `validate_watermark_config`, `_calculate_position` (9 якорей), `_scale_image`. Только геометрия | `pipeline/banner_png.py` |
| harry0703/MoneyPrinterTurbo | MIT | 2026-09-24 | реестр провайдеров (уже перенесён), concat-demuxer (уже есть). Range-стрим не нужен: есть в Starlette 1.6 | — |
| rushindrasinha/youtube-shorts-pipeline (Verticals v3) | MIT | 2026-06-09 | параметры дакинга голоса и музыки, деградация без libass (уже есть) | `pipeline/audio_mix.py` |
| WyattBlue/auto-editor | Unlicense | 2026-09-19 | только идея threshold/margin; своя реализация по словам | `clipping/snapping.py` |
| Breakthrough/PySceneDetect 0.7.1 | BSD-3 | 2026-09-21 | `detect()`, `AdaptiveDetector`, `ContentDetector` (уже в venv) | `clipping/reframe/shots.py`, `trends/analyze.py` |
| istupakov/onnx-asr 0.12.0 | MIT (проверено) | 2026-08-16 | `load_model("gigaam-v3-e2e-ctc", quantization="int8").with_timestamps()`, `load_vad("silero")` | `providers/asr/gigaam_onnx.py` |
| salute-developers/GigaAM (веса) | MIT | 2026-08-17 | модели v3 e2e CTC через onnx-asr. Пакет `gigaam` **не ставим** (torch, pyannote, HF_TOKEN) | `MODELS_DIR` |
| opencv/opencv_zoo YuNet | MIT | 2026-05-28 | `face_detection_yunet_2026may.onnx` для `cv2.FaceDetectorYN` (opencv 5.0 уже в venv) | `providers/faces/yunet.py` |
| yt-dlp 2026.8.19 | Unlicense | 2026-09-16 | `yt-dlp[default]` (ejs) + node как JS-рантайм | `services/sources.py`, `trends/analyze.py` |
| whisper.cpp / faster-whisper | MIT | whisper.cpp активен; faster-whisper без коммитов с 19.11.2025 | **не в v1**. В v1.1 опциональный адаптер (предпочтительно whisper.cpp) | — |
| tus/tus-js-client 4.3 | MIT | 2026-09-16 | клиент загрузки. Сервер свой; tuspyserver (MIT, 36★) только как справочник | `web/components/clipping/tus-uploader.tsx` |
| katspaugh/wavesurfer.js 7.12.12 | BSD-3 | 2026-09-24 (v8 свежий, не берём) | волна и регионы | `web/components/clipping/` |
| openapi-typescript / openapi-fetch | MIT | активны | кодогенерация типов и клиента | `web/lib/api/` |
| retentionos/apps/web (свой проект владельца) | — | — | UI-кит, api-client, sse-parse, theme, layout (копией) | `web/components/ui`, `web/lib` |
| Python-зависимости | — | — | fastapi 0.141 (MIT), pydantic 2.13 + pydantic-settings (MIT), psycopg 3.3 + psycopg-pool (LGPL-3.0, как библиотека), argon2-cffi (MIT), cryptography (Apache/BSD), python-json-logger 4.2 (**форк nhairs**, BSD-2), Pillow 12.3 (MIT-CMU), scipy 1.18 (BSD, уже есть), fontTools (MIT), onnxruntime 1.30 (MIT), onnx-asr (MIT) | `pyproject.toml`; extras `[clipping]`: onnx-asr, onnxruntime |

**Сознательно не берём:**
- MinIO: архивирован 24.04.2026.
- procrastinate и pgqueuer: запасной путь для очереди.
- Alembic, Celery, Temporal.
- Monaco: грузится с CDN. Vidstack: тег `latest` на 0.6.
- Remotion: лимит бесплатности до 3 человек. peaks.js: LGPL.
- ultralytics: AGPL. XTTS-v2: запрет коммерции.
- Silero вне `v5_cis_base`: CC-BY-NC.
- MoneyPrinterV2, clipforge, Postiz: AGPL.

---

## 10. План реализации

### 10.1 Правила параллельной работы (обязательны для всех потоков)
1. **Владение путями.** Поток меняет только файлы из своей колонки «владеет». Изменение чужого файла — только запрос владельцу контрактов (ведущему). Никаких массовых переформатирований, переименований и «приборки» вне своих путей: `ruff format` и `prettier` запускаются только на своих путях. Урок из прошлых проектов: без явного запрета агенты двигают чужие файлы.
2. **Реестры с автообнаружением вместо общих файлов:**
   - `api/app.py` подключает все `api/routers/*.py` с атрибутом `router`;
   - `tasks/registry.py` импортирует все `tasks/*.py` (`@register_task`);
   - `stages/__init__.py` импортирует все `stages/*.py` (`@register_stage`);
   - `cli/__init__.py` подключает подкоманды из `cli/*.py`;
   - `web/lib/nav.ts` заполняется **всеми** маршрутами v1 в M1, с заглушкой «Скоро».
   
   Добавление области не трогает общих файлов.
3. **Миграции.** SQL 0001–0009 из этого документа переносит в файлы поток контрактов в M1. Новые номера выдаёт ведущий (M2: 0011–0019, M3: 0021–0029 и т. д.). Слитый файл неизменяем (checksum).
4. **OpenAPI.** Снимок хранится **по тегам**: `tests/contract/openapi/<tag>.json`, каждый тег принадлежит одному потоку. TS-типы `web/lib/api/schema.d.ts` генерирует **только** владелец контрактов (`npm run gen:api`), CI проверяет, что файл актуален.
5. **Contract-first в каждом этапе.** В первые 1–2 дня этапа владелец контрактов пишет `api/schemas/<area>.py` и роутеры-заглушки, отвечающие 501. Из них получаются снимок и TS-типы. Фронт работает на MSW-моках по этим типам параллельно с бэкендом. Изменение формы после фиксации — через ведущего.
6. **Зависимости** (`pyproject.toml`, `web/package.json`) меняет только владелец контрактов. Все зависимости v1 объявляются в M1.
7. **Интеграция.** Ветка на поток, слияние в `integration` ежедневно. В конце этапа — **стенд**, ручная проверка владельцем под логином, и только потом прод.

**Контракты, фиксируемые первыми (M1, день 1–3):**
- SQL миграций 0001–0009;
- `domain/enums.py`, `domain/settings_schema.py`;
- формат ошибок и список кодов; события SSE (§5.1);
- `StageContext`/`StageResult`, `register_task`, протоколы провайдеров (§5.5);
- раскладка `MEDIA_ROOT`; формат кодов атрибуции; спецификация HMAC-ingest;
- `api/schemas/*` для M1–M2; `schema.d.ts`.

### 10.2 Этапы

Оценки ориентировочные, при 3–5 параллельных исполнителях.

#### M0. Хотфиксы прода (1–2 дня, один поток, сразу)
**Цель:** в прод перестаёт уходить синус, закрыты XSS и открытый дашборд.
**Задачи:**
- edge-tts `boundary="WordBoundary"`, снять `offline=True` в `queue/worker.py`, запрет mock при `APP_ENV=prod`;
- `webapp/redirect.py`: объявить `/health` первым, проверять метку регуляркой (иначе 404), экранировать через `html.escape` и `json.dumps`, отсеивать ботов по UA;
- дашборд перевести на `--host 127.0.0.1`;
- seed через `blake2b`; бюджет по `CURRENT_DATE` БД;
- в `/videoid` проверять `platform` по каналу.

**Владеет:** `src/autoshorts/pipeline/voice.py`, `queue/worker.py`, `webapp/redirect.py`, `pipeline/orchestrator.py` (только seed), `db.py` (только бюджет), `deploy/autoshorts-dashboard.service`, `tests/test_m0_*.py`.

**Критерии готовности:**
- `pytest tests/test_m0_voice.py` — мок `edge_tts.Communicate` получает `boundary="WordBoundary"`, а при 0 слов бросается ошибка;
- `pytest tests/test_m0_redirect.py`:
  - `GET /health` → 200 JSON и **нет** INSERT;
  - `GET /%3Cscript%3E` → 404;
  - `GET /yt_dQw4w9WgXcQ` → HTML без сырой метки в JS;
  - UA `TelegramBot` → строка не пишется;
- `pytest tests/test_m0_seed.py` — seed совпадает при `PYTHONHASHSEED=1` и `=2`;
- на стенде один ролик: `ffprobe` показывает mp3 edge-tts, `stage_payload->'words'` содержит больше 0 слов, в `render_tmp` нет `_mock_audio.wav`;
- `ss -ltn | grep 18234` показывает только `127.0.0.1`.

#### M1. Фундамент и контракты (≈ 1,5 недели)
**Цель:** миграции, API-каркас с доступом, очередь с защитами и оболочка веба. Старый конвейер пока работает параллельно.

| Поток | Задачи | Владеет | Зависит от |
|---|---|---|---|
| **M1-0 Контракты** (ведущий) | миграции 0001–0009 из §4; `domain/enums.py`, `settings_schema.py`, `domain/events.py`; `api/schemas/{common,auth,users,system,meta,settings,providers,secrets,budget,jobs,review,tasks,products,banners,campaigns,niches,channels}.py` + роутеры-заглушки 501; `pyproject.toml` (все зависимости v1, extras); `tests/contract/**`; генерация `web/lib/api/schema.d.ts` | `src/autoshorts/db/migrations/`, `src/autoshorts/domain/{enums,settings_schema,events}.py`, `src/autoshorts/api/schemas/`, `pyproject.toml`, `tests/contract/`, `web/lib/api/schema.d.ts` | — |
| **M1-A Core и БД** | `core/{config,logging,errors,security,ids,timeutil}.py`, `db/{pool,migrate,notify}.py`, `cli/{__init__,db}.py` (`migrate`, `create-user`, `check-config`), тестовые фикстуры (TRUNCATE), тесты миграций (пустая база, legacy-копия, guard) | `src/autoshorts/core/{config,logging,errors,security,ids,timeutil}.py`, `src/autoshorts/db/{pool,migrate,notify}.py`, `src/autoshorts/cli/__init__.py`, `src/autoshorts/cli/db.py`, `tests/conftest.py`, `tests/db/`, `tests/migrations/` | M1-0 (миграции) |
| **M1-B API и доступ** | `api/{app,deps,middleware,pagination,sse,media}.py`, роутеры и сервисы `auth, users, api_tokens, system, meta, settings, secrets, providers, audit, alerts, events, budget`, лимиты входа, CSRF, X-Accel/FileResponse | `src/autoshorts/api/{app,deps,middleware,pagination,sse,media}.py`, `src/autoshorts/api/routers/{auth,users,api_tokens,system,meta,settings,secrets,providers,audit,alerts,events,budget}.py`, `src/autoshorts/repos/{users,sessions,api_tokens,audit,settings,secrets,providers,alerts,budget}.py`, `src/autoshorts/services/{auth,users,audit,settings,secrets,providers,alerts,budget}.py`, `tests/api/test_{auth,users,system,settings,secrets,budget}*.py` | M1-0, интерфейс `db.pool` от M1-A (заглушка сигнатуры в день 1) |
| **M1-C Очередь и воркер** | `workers/*`, `tasks/{registry,system}.py` (reaper, cleanup-каркас), `stages/base.py`, `core/{resources,storage}.py` (допуск, PSI, диск, flock), `pipeline/ffmpeg_exec.py`, `repos/{tasks,workers,schedules,artifacts}.py`, `api/routers/{tasks,workers,schedules}.py` | `src/autoshorts/workers/`, `src/autoshorts/tasks/{__init__,registry,system}.py`, `src/autoshorts/stages/{__init__,base}.py`, `src/autoshorts/core/{resources,storage}.py`, `src/autoshorts/pipeline/ffmpeg_exec.py`, `src/autoshorts/repos/{tasks,workers,schedules,artifacts}.py`, `src/autoshorts/services/tasks.py`, `src/autoshorts/api/routers/{tasks,workers,schedules}.py`, `tests/worker/` | M1-0, M1-A |
| **M1-D Оболочка веба** | `web/` каркас: Next 16 + export, токены «Аппаратная», fontsource, копия UI-кита, `lib/api/{client,sse,sse-parse,media}.ts`, AuthGate, AppShell (шапка с воркером, бюджетом, SSE, алертами), login, `lib/nav.ts` со всеми маршрутами-заглушками, CommandPalette, MSW, `scripts/check-no-external.mjs`, ESLint-запрет `next/font/google`, Playwright `e2e/login.spec.ts`, экран «Настройки → Пользователи/Система» | `web/**`, кроме `web/lib/api/schema.d.ts` | M1-0 (типы) |
| **M1-E Деплой-каркас** | новый `docker-compose.yml`, `deploy/Dockerfile`, `deploy/systemd/*` (heavy с лимитами), `deploy/nginx/*`, `scripts/e2e_smoke.py` (каркас) | `docker-compose.yml`, `deploy/`, `scripts/e2e_smoke.py` | — |

**Критерии готовности M1:**
- `autoshorts-admin migrate` на пустой базе → exit 0, `SELECT max(version) FROM schema_migrations` = 9;
- на копии боевой базы: `pg_restore` в `autoshorts_legacy_check` → `migrate` → `adopted=true` у версии 1; `pytest tests/migrations -k legacy` зелёный (маппинг статусов, перенос задач, клики, нормализация `reject_reason`);
- `psql -f src/autoshorts/db/migrations/0004_queue.sql` повторно → `ERROR: migration 4 already applied`; изменение байта в применённом файле → `migrate` отказывается;
- `pytest -m "not media"` зелёный. `pytest tests/contract` проверяет латиницу в путях, параметрах, свойствах и enum и сравнивает снимки по тегам;
- `pytest tests/api/test_auth.py` — логин, 403 без `X-CSRF-Token`, роли, блокировка после 10 неудач;
- `pytest tests/worker`:
  - 2 процесса × 200 задач без двойного захвата;
  - reaper возвращает задачу с протухшим heartbeat;
  - `ResourceDeferred` не тратит попытку;
  - отмена убивает группу процессов `sleep 60`;
  - второй heavy-воркер не берёт задачи, пока жив первый;
- `curl -s 127.0.0.1:18300/api/v1/health/ready` → 200. `curl 127.0.0.1:18300/docs` → 404;
- `cd web && npm ci && npm run typecheck && NEXT_EXPORT=1 npm run build && node scripts/check-no-external.mjs` → 0 внешних URL;
- `npx playwright test e2e/login.spec.ts` на стенде **под логином**: вход, AppShell, SSE-точка live, выход.

#### M2. Каталог, трек генерации в новой очереди, приёмка в вебе (≈ 2 недели)
**Цель:** ролик по нише проходит новую очередь с баннером продукта и варианта и принимается в вебе. Бот работает дублёром.

| Поток | Задачи | Владеет | Зависит от |
|---|---|---|---|
| **M2-A Каталог (backend)** | продукты, кусты, ниши (версии, YAML импорт и экспорт, `NicheConfig` с `hashtags`, `prompts`), шаблоны баннера (`BannerLayout`, импорт banner.yaml), кампании и варианты, `assign_variant`, каналы (профиль, предупреждение о дублях, инциденты), ассеты (fontTools), `validate` и `preview` баннера (через `pipeline/banner_png.py` от M2-B) | `src/autoshorts/domain/{niche_schema,banner_schema}.py`, `src/autoshorts/repos/{products,clusters,niches,banners,campaigns,channels,assets}.py`, `src/autoshorts/services/{products,clusters,niches,banners,campaigns,channels,assets}.py`, `src/autoshorts/api/routers/{products,clusters,niches,banner_templates,campaigns,banner_variants,channels,assets}.py`, `src/autoshorts/cli/catalog.py`, `tests/api/test_catalog*.py` | M1 |
| **M2-B Медиа и стадии generate** | провайдеры (`llm/{openai_compat,gigachat,structured,mock}`, `tts/{edge,silero,fal,mock}`, `images/{fal,mock}`), `registry.py` + `provider_configs`, стадии `generate_script/voice/visuals`, `render` (длительности от аудио, петля, PNG-баннер), `qc` (один проход, профили, dHash), `pipeline/{banner_png,render,subtitles,qc,similarity,probe,hashtags}.py`, `cli render-once`, `adopt-legacy-media`, `scripts/bench_render.py` | `src/autoshorts/providers/`, `src/autoshorts/stages/{generate_script,generate_voice,generate_visuals,render,qc}.py`, `src/autoshorts/pipeline/{banner_png,render,subtitles,qc,similarity,probe,hashtags,audio_strategy,beat_sync,trend_mimicry}.py`, `src/autoshorts/tasks/generate.py`, `src/autoshorts/cli/media.py`, `scripts/bench_render.py`, `tests/unit/test_{render,banner_png,qc,providers}*.py`, `tests/media/` | M1-C (`StageContext`) |
| **M2-C Ролики и приёмка (backend + бот)** | `repos/{jobs,reviews}.py`, `services/{jobs,review}.py` (переходы, 409, undo, rework, clone, adapt), `api/routers/{jobs,review}.py`, `/review-context`, SSE-события `job_*`, `review_queue`; бот через сервисы (только `tg_user_id`, `/link`) | `src/autoshorts/repos/{jobs,reviews}.py`, `src/autoshorts/services/{jobs,review}.py`, `src/autoshorts/api/routers/{jobs,review}.py`, `src/autoshorts/bot/`, `tests/api/test_jobs*.py`, `tests/api/test_review*.py`, `tests/unit/test_bot*.py` | M1, контракт стадий |
| **M2-D1 Веб: производство** | Конвейер, Приёмка (плеер, SVG-оверлей, клавиши, undo, 409), NewJobSheet | `web/app/(console)/{production,review}/`, `web/components/{production,review,player,timeline,create-job}/`, `web/lib/queries/{jobs,review,tasks}.ts`, `web/lib/store/review-prefs.ts`, `web/e2e/{production,review}.spec.ts` | M1-D, типы M2 |
| **M2-D2 Веб: каталог** | Продукты (без вебхука и A/B), редактор баннера, Каналы, Настройки (провайдеры, бюджет) | `web/app/(console)/{products,channels,settings}/`, `web/components/{products,banner-editor,channels,settings}/`, `web/lib/queries/{products,banners,campaigns,channels,settings,providers,users}.ts`, `web/lib/safe-zones.ts`, `web/e2e/{banner,channels}.spec.ts` | M1-D, типы M2 |

**Критерии готовности M2:**
- `autoshorts-admin import-niches config/niches/*.yaml && autoshorts-admin import-banner config/banner.yaml --name default` → `GET /api/v1/niches` отдаёт 2 ниши со статусом `active`. `pytest tests/unit/test_niche_yaml_roundtrip.py` (YAML → схема → YAML без потерь);
- `pytest tests/api/test_jobs_flow.py` с mock-провайдерами: `POST /jobs` → воркеры в тесте → `pending_review`, артефакты final, preview и poster на месте, `generation_events` записаны; approve → `approved`; параллельный approve и reject → один 409;
- `pytest tests/unit/test_assign_variant.py` — выбор детерминирован по `video_uid`, распределение соответствует весам (χ² на 10 000);
- `pytest -m media tests/media/test_render_banner_png.py` на стенде: 3-секундный вход 320×568, на кадре окна импульса есть непрозрачные пиксели плашки, на 1-й секунде их нет;
- на стенде 5 роликов с edge-tts и fal: `SELECT count(*) FROM tasks WHERE status='failed'` = 0; в логе воркера нет пересечения интервалов heavy-задач; длительность видео равна длительности голоса ±0,1 с;
- `pytest tests/unit/test_bot_auth.py` — callback от неизвестного `tg_user_id` отвергается;
- Playwright под логином: `review.spec` (A, затем U, затем X+3), `banner.spec` (плашка ниже y=1500 → предупреждение `offer_in_bottom_dead_zone`, «Кадр из рендера» возвращает PNG).

#### M3. Пакеты, публикация, атрибуция, редиректор, конверсии (≈ 1,5 недели)

| Поток | Задачи | Владеет | Зависит от |
|---|---|---|---|
| **M3-A Пакеты и публикации** | `stages/package_build.py` (тексты, хэштеги из ниши, закреп из варианта, чек-лист, код), `domain/url_parse.py`, регистрация (одна транзакция, мастер-архив), проверка принадлежности, `api/routers/{packages,publications}.py` | `src/autoshorts/stages/package_build.py`, `src/autoshorts/domain/url_parse.py`, `src/autoshorts/repos/{packages,publications}.py`, `src/autoshorts/services/{packages,publications}.py`, `src/autoshorts/api/routers/{packages,publications}.py`, `tests/unit/test_url_parse.py`, `tests/api/test_{packages,publications}*.py` | M2 |
| **M3-B Атрибуция и public** | `domain/tags.py` (Crockford, алиасы, `_l`), `public/*` (редиректор, лендинг, CSP, `/go/*`, botfilter, ingest HMAC, rate-limit, `emitter_snippet.py`), `repos/{tracking,clicks,conversions}.py`, `services/{tracking,ingest}.py`, `api/routers/{tracking_links,clicks,conversions}.py`, вебхук-часть `products` (rotate, integration, test-conversion) | `src/autoshorts/domain/tags.py`, `src/autoshorts/public/`, `src/autoshorts/repos/{tracking,clicks,conversions}.py`, `src/autoshorts/services/{tracking,ingest}.py`, `src/autoshorts/api/routers/{tracking_links,clicks,conversions,product_integration}.py`, `tests/api/test_public*.py`, `tests/unit/test_tags.py` | M1, M2-A |
| **M3-C Веб: пакеты** | экран Пакеты (копирование, чек-лист, Web Share, разбор URL, вкладка «Проблемы»), вкладка «Вебхук» продукта, трекинг-ссылки канала | `web/app/(console)/packages/`, `web/components/packages/`, `web/components/products/webhook-tab.tsx`, `web/lib/platform-url.ts`, `web/lib/queries/{packages,publications,tracking}.ts`, `web/e2e/packages.spec.ts` | M2-D2 (сдан), типы M3 |
| **M3-D Legacy-мост** | убрать старые стадии и воркер из деплоя, перевод прод-данных (`adopt-legacy-media` на копии и затем на проде) | `src/autoshorts/queue/`, `src/autoshorts/pipeline/orchestrator.py` (удаление), `deploy/systemd/autoshorts-worker.service` | M2 |

**Критерии готовности M3:**
- `pytest tests/unit/test_url_parse.py` — 25 и больше форм YouTube и VK, кириллица и мусор дают 422;
- `pytest tests/unit/test_tags.py` — коды без `0/o/1/l/i/u`, исправление опечаток, `_l` снимается;
- `pytest tests/api/test_public_ingest.py`:
  - неверная подпись → 401;
  - `ts` старше 300 с → 401;
  - повтор `event_id` → `duplicates=1`;
  - неизвестный `tag` → `unknown_tag=1`;
  - 101 событие → 413;
- `pytest tests/api/test_public_redirect.py`:
  - `/health` не пишет клик;
  - бот по UA → `is_bot=true`;
  - неизвестный код → лендинг продукта по умолчанию;
  - заголовок CSP присутствует;
  - `<script>` в коде → 404;
- `pytest tests/api/test_publications.py` — площадка не совпадает с каналом → 422; второй канал той же площадки → 409 `same_platform_crosspost_forbidden`; мастер лежит в `masters/`;
- `python scripts/e2e_smoke.py --stand`: job → approve → пакет → регистрация URL → `curl u.<стенд>/<code>` → подписанный `bot_start` → `GET /publications/{id}` показывает 1 клик и 1 старт;
- Playwright `packages.spec` под логином: вставка URL → статус `published`.

#### M4. Коллекторы метрик и аналитика (≈ 1,5 недели)

| Поток | Задачи | Владеет | Зависит от |
|---|---|---|---|
| **M4-A Коллекторы** | `collectors/{http,youtube_data,vk_video,thumbnails}.py`, лестница опроса, снятие и санкции → алерты, квота (`api_quota_usage`), `channel_stats` | `src/autoshorts/collectors/{http,youtube_data,vk_video,thumbnails}.py`, `src/autoshorts/tasks/collectors.py`, `src/autoshorts/repos/{metrics,quota}.py`, `src/autoshorts/services/metrics.py`, `src/autoshorts/api/routers/quota.py`, `tests/unit/test_collectors*.py` | M3 |
| **M4-B Аналитика** | `analytics/{formulas,stats,rollups}.py`, `repos/analytics.py` (перенос `dashboard_queries.py`), `services/analytics.py`, `/overview`, `/analytics/*`, `/campaigns/{id}/experiment`, CSV | `src/autoshorts/analytics/`, `src/autoshorts/repos/analytics.py`, `src/autoshorts/services/analytics.py`, `src/autoshorts/tasks/rollups.py`, `src/autoshorts/api/routers/{overview,analytics,experiments}.py`, `tests/unit/test_{formulas,stats,interp}.py`, `tests/db/test_rollups.py` | M3 |
| **M4-C Веб: аналитика** | полный Обзор, Аналитика (6 вкладок), карточка ролика, A/B-вкладка продукта | `web/app/(console)/page.tsx`, `web/app/(console)/analytics/`, `web/components/{overview,analytics,charts}/`, `web/components/products/ab-tab.tsx`, `web/lib/{metric-definitions,csv}.ts`, `web/lib/queries/{overview,analytics}.ts`, `web/e2e/analytics.spec.ts` | M1-D, типы M4 |
| **M4-D YouTube Analytics OAuth** (если владелец подтвердит) | `collectors/{youtube_analytics,oauth_google}.py`, `/channels/{id}/oauth/*`, `retention_curves`, `failure_class`, доля ленты Shorts | `src/autoshorts/collectors/{youtube_analytics,oauth_google}.py`, `src/autoshorts/api/routers/oauth.py`, `src/autoshorts/tasks/yt_analytics.py`, `tests/unit/test_yt_analytics.py` | M4-A |

**Критерии готовности M4:**
- `pytest tests/unit/test_collectors_youtube.py` (`httpx.MockTransport`): пачки по 50; пропавший ролик → `removed_by_platform` + алерт; `uploaderAccountSuspended` → критический алерт; квота учитывается, на 8000 опрос откладывается;
- `pytest tests/db/test_rollups.py` — фикстура снимков даёт ожидаемые `views_eod` и дельты по МСК, трафик без ботов, `direct_bot_starts` без `_l`;
- `pytest tests/unit/test_stats.py` — меньше 20 зрелых роликов → `ready=false`; на синтетике с известным сдвигом p < 0,05;
- на стенде `autoshorts-admin collectors-once --publication <id>` обновляет `latest_views`; `GET /api/v1/analytics/summary` отдаёт `data_freshness`;
- `autoshorts-admin seed-demo` + Playwright `analytics.spec` под логином: все вкладки рендерятся, CSV скачивается с BOM, в консоли нет нарушений CSP.

#### M5. Трек нарезки (≈ 2,5 недели)

| Поток | Задачи | Владеет | Зависит от |
|---|---|---|---|
| **M5-A Приём и ASR** | tus-сервер, `sources` (права обязательны, CC-BY-SA и NC отклоняются), `source_download` (audio-first), `source_prepare` (прокси 540p GOP 1 с, peaks, раскадровка, проверка водяных знаков), ASR-провайдеры (`gigaam_onnx`, `yandex_speechkit`, `openai_compat`, `mock`), нарезка по VAD, `cli models-fetch`, `scripts/bench_asr.py` | `src/autoshorts/services/{uploads_tus,sources}.py`, `src/autoshorts/repos/{uploads,sources,transcripts}.py`, `src/autoshorts/api/routers/{uploads,sources}.py`, `src/autoshorts/providers/asr/`, `src/autoshorts/pipeline/{proxy,peaks,storyboard}.py`, `src/autoshorts/clipping/watermark_check.py`, `src/autoshorts/tasks/sources.py`, `src/autoshorts/cli/models.py`, `config/logos/`, `scripts/bench_asr.py`, `tests/api/test_tus.py`, `tests/api/test_sources.py` | M1, M2 |
| **M5-B Отбор и clip-job** | `clipping/{windows,llm_select,schemas,features,scoring,snapping,edl,render_clip}.py`, `clipping/reframe/*`, `clipping/author/*`, `providers/faces/*`, стадии `clip_fetch/clip_analyze/author_script/author_voice/clip_render`, расширение `pipeline/subtitles.py` (дорожки) и `pipeline/qc.py` (профиль `clipping`, `author_share`, `expected_static`), `repos/clips.py`, `services/clipping.py`, `api/routers/clip_candidates.py`, `/jobs/{id}/author-voice`, `scripts/bench_clipping.py` | `src/autoshorts/clipping/` (кроме `watermark_check.py`), `src/autoshorts/providers/faces/`, `src/autoshorts/stages/{clip_fetch,clip_analyze,author_script,author_voice,clip_render}.py`, `src/autoshorts/pipeline/{subtitles,qc,audio_mix}.py` (в этом этапе), `src/autoshorts/repos/clips.py`, `src/autoshorts/services/clipping.py`, `src/autoshorts/api/routers/{clip_candidates,author_voice}.py`, `src/autoshorts/tasks/clipping.py`, `scripts/bench_clipping.py`, `tests/unit/test_clipping*.py` | M5-A (транскрипт и прокси), M2-B |
| **M5-C Веб: студия** | Источники, AddSourceDialog (tus и права), Студия (wavesurfer, транскрипт, моменты, рамка, инспектор, 70/30), запись голоса MediaRecorder | `web/app/(console)/clipping/`, `web/components/clipping/`, `web/lib/store/clip-editor.ts`, `web/lib/queries/clipping.ts`, `web/e2e/clipping.spec.ts` | M1-D, типы M5 |

**Критерии готовности M5:**
- **Гейт ASR:** `python scripts/bench_asr.py --sample var/bench/10min_ru.wav --provider gigaam_v3_onnx --threads 2` показывает RTFx ≥ 5 и пиковый RSS ≤ 800 МБ (`/usr/bin/time -v`). Если не проходит, default переключается на облачный провайдер (решение владельца);
- `python scripts/bench_clipping.py --clip 45` → wall ≤ 4 мин, пиковый RSS ≤ 1,2 ГБ, за время прогона load1 не выходит за порог допуска;
- `pytest tests/api/test_tus.py` — создание, два PATCH, HEAD и продолжение, 409 на неверный offset, 507 при подменённой `disk_usage`;
- `pytest tests/api/test_sources.py` — без `rights` → 422; `cc_by` без `attribution_text` → 422;
- `pytest tests/unit/test_clipping_selection.py` на фикстурном транскрипте: окна по границам предложений, dedupe, снаппинг в тишину, запрещённая фраза блокирует кандидата;
- `pytest tests/unit/test_author_share.py` и `tests/api/test_clip_qc.py` — лицензированный источник с долей 0,2 → `qc_failed` + `author_share`; `own` с 0,2 → проходит с пометкой;
- на стенде: загрузка 1-часового своего видео → `ready` за ≤ 25 мин (с GigaAM) → 3 клипа отрендерены и приняты;
- Playwright `clipping.spec` под логином: in/out, «В рендер», задача в Конвейере.

#### M6. Трендоскаут и редактор ниш (≈ 1,5 недели)

| Поток | Задачи | Владеет | Зависит от |
|---|---|---|---|
| **M6-A Тренды (backend)** | `trends/{youtube_api,discovery,baselines,outlier,analyze,briefs,field_stats}.py`, RSS→UUSH→UU, квота, `competitor_*`, брифы, `create-jobs`, `trend_prior` | `src/autoshorts/trends/`, `src/autoshorts/repos/{competitors,briefs}.py`, `src/autoshorts/services/trends.py`, `src/autoshorts/tasks/trends.py`, `src/autoshorts/api/routers/{competitors,competitor_videos,trend_briefs,trend_scout}.py`, `tests/unit/test_trends*.py` | M4-A (квота, http) |
| **M6-B Инструменты ниши (backend)** | `test-script`, `voice-sample`, `text/check-forbidden`, пересечения forbidden с хуками и CTA в `validate` | `src/autoshorts/services/niche_tools.py`, `src/autoshorts/api/routers/niche_tools.py`, `tests/api/test_niche_tools.py` | M2-A |
| **M6-C Веб** | Трендоскаут (аутлаеры, доноры, брифы, корреляции), редактор ниш (форма, CodeMirror, версии, сухой прогон) | `web/app/(console)/{trends,niches}/`, `web/components/{trends,niches}/`, `web/lib/queries/{trends,niches}.ts`, `web/e2e/{trends,niches}.spec.ts` | M1-D, типы M6 |

**Критерии готовности M6:**
- `pytest tests/unit/test_trends_outlier.py` — лог-лог интерполяция, baseline по возрасту при n ≥ 8, холодный старт, тиры;
- `pytest tests/unit/test_trends_discovery.py` — RSS 404 → UUSH → UU с фильтром ≤ 180 с; `search.list` не вызывается ни разу;
- `pytest tests/unit/test_analyze_discard.py` — после `analyze` временный каталог пуст, в том числе при исключении;
- бриф → `POST /trend-briefs/{id}/create-jobs` → job с `trend_brief_id`, сценарий учитывает `hook_type` и темп;
- Playwright `niches.spec`: правка запрещённых фраз → предупреждение пересечения с хуком; сохранение с устаревшей `expected_version` → диалог конфликта.

#### M7. Прод, эксплуатация, уборка legacy (≈ 1 неделя)

| Поток | Задачи | Владеет | Зависит от |
|---|---|---|---|
| **M7-A Деплой** | VPS, systemd, nginx и CSP, TLS, `deploy.sh`, бэкап-таймеры, `restore-check`, сторож | `deploy/`, `src/autoshorts/cli/ops.py`, `scripts/watchdog.sh` | все |
| **M7-B Алерты и наблюдаемость** | `alerts_eval` (все коды §8.5), отправка в Telegram (открытие и закрытие), `/system/status` с PSI | `src/autoshorts/services/alerts_eval.py`, `src/autoshorts/tasks/alerts.py`, `tests/unit/test_alerts_eval.py` | M1-B |
| **M7-C Уборка** | миграция 0010, удаление `webapp/`, `queue/`, `dashboard_queries.py`, `config.py`, старых юнитов; обновление README | `src/autoshorts/db/migrations/0010_cleanup.sql`, `src/autoshorts/webapp/`, `src/autoshorts/queue/`, `src/autoshorts/{config,db,models,dashboard_queries}.py`, `README.md` | M3-D |

**Критерии готовности M7:**
- `systemd-analyze verify deploy/systemd/*.service` без ошибок. На VPS `systemctl show autoshorts-worker-heavy -p CPUQuotaPerSecUSec -p MemoryMax` показывает лимиты профиля;
- `systemctl start autoshorts-backup` создаёт дамп; `autoshorts-admin restore-check` → exit 0;
- на стенде `PATCH /settings {"disk_hard_min_gb": 10000}` → алерт `disk_low` в Telegram, heavy откладывает задачи; после отката настройки алерт закрывается;
- `autoshorts-admin migrate` применяет 0010; `grep -rn "webapp.dashboard\|queue.worker" src deploy` пусто;
- полный Playwright-набор под логином на prod-like стенде, 0 нарушений CSP в консоли; `curl -I https://admin.<домен>/` показывает CSP-заголовок.

### 10.3 Граф зависимостей этапов
```
M0 ─► M1 ─► M2 ─► M3 ─► M4 ─┬─► M6 (M6-A зависит от M4-A)
                     │       └─► M7
                     └──► M5 (после M2; параллельно M4 при отдельной команде)
M4-D (OAuth) — по решению владельца, в любой момент после M4-A
```

---

## 11. Риски и открытые вопросы

### 11.1 Риски

| Риск | Вероятность / влияние | Смягчение |
|---|---|---|
| **Память на dev-боксе:** swap занят целиком, свободно около 2,4 ГБ. Heavy (ffmpeg около 400 МБ + ASR около 500 МБ) вместе с `next dev` может вытеснить retentionos и ai-reseller | высокая / высокое | допуск по MemAvailable и PSI, systemd `MemoryMax`, compose-профили `heavy` и `web` только по требованию, сборка веба вне бокса |
| **Диск:** мастеры около 30 ГБ в год, источники 1–3 ГБ за час, на dev 29 ГБ свободно | высокая / высокое | политика хранения, ступени 12/8/5, вынос мастеров и бэкапов за пределы машины (решение владельца) |
| Скорость ASR и рендера экстраполирована | средняя / среднее | гейты `bench_asr` и `bench_clipping` в M5, облачный запасной ASR |
| googleapis, RSS YouTube и api.telegram.org с российского VPS недоступны или медленные | средняя / высокое | `collectors_proxy_url` и `telegram_proxy_url`, алерты `collector_failing` и `provider_failing`, fallback UUSH |
| Атрибуция в Shorts структурно недосчитывает: ссылки некликабельны, домен набирают руками | высокая / среднее | коды пакета и канала, честный «прямой трафик», опция кода в кадре, A/B с порогами зрелости |
| OAuth YouTube Analytics в режиме Testing: токены умирают через 7 дней. Общий Cloud-проект создаёт риск связанных каналов | средняя / среднее | ключи и OAuth на уровне куста (`clusters`), приложение в статусе Published, алерт на ошибку токена |
| Принятие боевой базы и перенос статусов | средняя / высокое | прогон на копии дампа (критерий M1), автодамп перед миграцией, legacy-процессы остановлены |
| Своя очередь — своя поддержка (гонки, reaper, отмена) | средняя / среднее | тесты конкурентного захвата, запасной путь на procrastinate |
| Загрузку по URL используют для чужого контента | низкая (решение владельца) / высокое | обязательные права, аудит, роль `operator` и выше, проверка водяных знаков, гейт `author_share` |
| `author_share` — прокси, а не гарантия. Площадка может признать нарезку reused content, у лицензированного контента без allowlist будут клеймы | средняя / среднее | allowlist у правообладателя, живой голос оператора, мониторинг `has_claim` и снятий |
| edge-tts на проде зависит от WSS Microsoft | средняя / высокое | проверка провайдера, алерт `tts_mock_in_prod`, Silero `v5_cis_base` как локальная альтернатива (адаптер в M2-B) |
| Лицензионные мины при портировании из openshorts (AGPL в `main.py`, коммерческий `cloud/`) | низкая / высокое | переносятся только перечисленные функции с MIT-нотисом, ревью импорта |
| Статический экспорт Next.js: нет динамических роутов и middleware | средняя / низкое | маршруты на `?id=`, `PageSuspense`, AuthGate |
| Превью баннера в браузере не совпадает с ffmpeg по метрикам шрифта | средняя / низкое | «Кадр из рендера» тем же `banner_png.py`, серверный `validate` |
| Юридическое: маркировка рекламы с 01.01.2027 (ст. 5 ФЗ «О рекламе»), разметка ИИ по 243-ФЗ с 01.03.2027 | высокая / высокое | поле `ad_marking` в варианте (выключено), пункт `ai_disclosure` в чек-листе, AI-disclosure в метаданных, алерт-напоминание |
| Расползание объёма: 12 экранов для 1–3 человек | средняя / среднее | этапы M0–M7, заморозка M5 и M6 до работающих приёмки и пакетов |

### 11.2 Открытые вопросы владельцу
1. **Где прод и домены.** Отдельный VPS-воркер из ТЗ (2 vCPU / 4 ГБ / 50 ГБ) или эта машина? Какой домен у админки (`admin.<домен>`) и у редиректора (`u.umka.chat` или короткий `.ru` на российском хостинге)? Кто держит 80/443: nginx или Coolify/Traefik? Можно ли закрыть админку allowlist по IP или VPN?
2. **Код ролика в кадре.** Оставляем только домен (атрибуция с домена тогда канальная и «прямая») или показываем короткий код `u.umka.chat/k7m2q` ради точной поштучной атрибуции ценой длины плашки и озвучки?
3. **Правило 70/30.** Это «не меньше 30% авторского вклада» или «не меньше 70%»? Что считается авторским слоем (только свой голос и вставки или ещё экранный текст)? Нужен ли гейт для ваших собственных длинных видео (`own`)? Блокировка или предупреждение?
4. **Конверсии из продуктов.** Кто и когда добавит HMAC-эмиттер в бота и сайт umka, TMA-магазин и VPN-бот? Какие события реально доступны (старт, регистрация, первая оплата, сумма)?
5. **Провайдеры и бюджеты.** Какой LLM: платный сервисный ключ umka reseller (`https://umka.chat/v1`), GigaChat (OAuth + сертификат Минцифры) или другой OpenAI-совместимый? Облачный ASR (Yandex SpeechKit, около 9–36 ₽ за час) да или нет? Дневной лимит бюджета?
6. **YouTube Analytics через OAuth** (engaged views, удержание, доля ленты Shorts) нужен в v1? Нужен Google Cloud-проект на каждый куст, переведённый в Published.
7. **VK-контур в v1.** Есть ли токен с правом `video` (письмо в devsupport идёт неделями) или начинаем с yt-dlp и ручного ввода?
8. **Хранение.** Куда выносить мастер-архив и бэкапы (второй сервер или S3-совместимое хранилище в РФ)? Сроки хранения оригиналов источников (предлагается 30 дней) и лимиты загрузки (3 ч и 6 ГБ)?
9. **Операторы.** Сколько людей, какие роли, Telegram ID для приёмки? Приёмка в Telegram остаётся рабочим каналом решений или только уведомляет?
10. **Валюта.** Основная ₽ или $? Курс задаётся вручную или по ЦБ ежедневно?
11. **Маркировка рекламы (ERID)** до 01.01.2027: заводим поле сейчас (выключенным) и ждём юриста или переносим баннерную нагрузку на VK-контур?
12. **Трендоскаут.** Подтверждаете пулы (50 русскоязычных и 25 англоязычных доноров) и кто составит первичный список? Разрешён ли Gemini-разбор по URL (нужен прокси)?
13. **Запись голоса оператора** из браузера для авторского слоя нужна или только TTS?
14. **Визуальное направление** «Аппаратная» с тёмной темой подходит или нужна привязка к бренду umka.chat?
