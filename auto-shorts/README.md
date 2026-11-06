# Auto Shorts — конвейер вертикальных шортсов

Рабочий скелет конвейера из ТЗ: генерация сценария → озвучка с пословными таймингами →
рендер (Ken Burns без zoompan + двухслойный баннер + караоке-субтитры) → preflight-QC →
ручная приёмка в Telegram → ручная заливка → атрибуция трафика.

Публикация на площадки **ручная** (решение владельца) — конвейер не грузит на YouTube/VK API,
а отдаёт человеку готовый «пакет к заливке».

## Статус

Проверено локально сквозным прогоном (`scripts/run_pipeline_once.py`), включая визуальную
проверку кадров и полный проход preflight-QC. 11/11 тестов зелёные (`pytest tests/`).

**Что реально работает без доработки:**
- Генерация сценария из YAML-конфига ниши (mock-режим без ключей; GigaChat/OpenAI-совместимый режим готов, не тестирован живьём)
- Озвучка: edge-tts с пословными таймингами (проверена связь с Microsoft, RU-голоса Dmitry/Svetlana живы) + offline-заглушка для разработки
- Рендер: crop-based Ken Burns, двухслойный баннер с импульсным таймингом (проверено визуально по кадрам), караоке-субтитры на ASS, двухпроходный loudnorm
- Preflight-QC: тишина, клиппинг, громкость, чёрные/зависшие кадры, длительность
- Атрибуция: deeplink с video ID, защита от кириллицы в start-параметре
- Redirect-сервер (FastAPI) — поднимается, роуты корректны
- Postgres-схема (10 таблиц) — очередь, реестр публикаций, бюджет, тренды
- Telegram-бот приёмки — синтаксически корректен, живьём не тестирован (нужен токен)

**Что требует доводки на реальном железе:**
- `trends/rss_watch.py` — парсер проверен на реальной XML-схеме, но сетевой вызов
  `/feeds/videos.xml` заблокирован в этой песочнице (сам youtube.com отдаёт 200, RSS-путь — 404
  на любой ID). Проверить на проде перед использованием.
- fal.ai провайдеры (`providers/`) — интерфейсы готовы, реальные вызовы не тестированы (нет ключа)
- Postgres не поднят в этой среде — `db.py` не тестирован живьём, только синтаксически

## Быстрый старт

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Прогон одного ролика без Postgres — для обкатки шаблона (Этап 1 плана запуска, ТЗ §12)
mkdir -p render_tmp/test_scenes
# ВАЖНО: testsrc2, не сплошной цвет — plain color на панораме ложно триггерит freezedetect
# (найдено на реальном QC-прогоне: панорама по плоскому цвету не даёт пикселям меняться
# выше порога -60dB, freezedetect путает это с зависшим кадром). Реальные AI-картинки
# с текстурой этой проблемы не имеют.
for i in 0 1 2; do
  ffmpeg -f lavfi -i "testsrc2=s=1080x1920:d=1,format=yuv420p" -frames:v 1 render_tmp/test_scenes/scene_$i.png
done
.venv/bin/python scripts/run_pipeline_once.py --niche taro --topic "отношения" --platform youtube
```

## С очередью и Postgres

```bash
docker compose up -d postgres
cp .env.example .env   # заполнить FAL_API_KEY, TELEGRAM_BOT_TOKEN и т.д.

python3 -c "
import sys; sys.path.insert(0, 'src')
from autoshorts import db
job_id = db.enqueue_job('demo1', 'taro_main', 'taro', 'v1')
print('job:', job_id)
"

.venv/bin/python -m autoshorts.queue.worker   # тянет задачи по стадиям
.venv/bin/python -m autoshorts.bot.telegram_bot  # приёмка в Telegram
uvicorn autoshorts.webapp.redirect:app --port 8080  # редиректор домена
uvicorn autoshorts.webapp.dashboard:app --app-dir src --port 18234  # дашборд мониторинга
```

## Дашборд — "видеть, как всё работает"

Веб-панель на реальных данных Postgres: очередь по стадиям, расход за сегодня против дневного
лимита, процент прохождения QC, причины отказов, детали каждой задачи (сценарий, хэштеги,
события генерации с ценой и latency), реестр публикаций, каналы. Закрыт HTTP Basic Auth
(`DASHBOARD_USER`/`DASHBOARD_PASSWORD` в `.env`) — это операционные данные с расходами, наружу
без пароля лежать не должны.

Проверено вживую: поднят локальный Postgres, прогнан полный job через оркестратор, дашборд
показал реальные карточки, таблицы и статусы — не заглушки (скриншоты снимались headless Chrome
в процессе разработки).

**Деплой на сервер** — конфиги в `deploy/`:
- `deploy/autoshorts-dashboard.service`, `-worker.service`, `-bot.service` — systemd-юниты
- `deploy/nginx-dashboard.conf` — реверс-прокси, два варианта: поддомен `admin.umka.chat`
  (рекомендуется, не пересекается с основным сайтом) или путь `/dashboard` под текущим доменом.
  Порт 18234 слушает только `127.0.0.1` — наружу торчит только nginx с TLS.

## Структура

```
config/niches/*.yaml     — сценарий, голос, визуал, субтитры одним файлом на нишу
config/banner.yaml       — координаты и тайминг баннера, платформенные оверрайды
config/fonts/            — Montserrat (кириллица проверена: 64/64 буквы)
src/autoshorts/
  providers/registry.py  — реестр LLM/TTS/image-провайдеров (паттерн MoneyPrinterTurbo)
  pipeline/voice.py       — озвучка + пословные тайминги БЕЗ Whisper (edge-tts WordBoundary)
  pipeline/render.py      — сборка: crop-Ken-Burns, баннер, субтитры, loudnorm
  pipeline/qc.py          — preflight-проверки перед показом человеку
  pipeline/attribution.py — deeplink + UTM, защита от кириллицы
  pipeline/orchestrator.py — связка стадий очереди
  bot/telegram_bot.py     — приёмка + генерация пакета к ручной заливке
  webapp/redirect.py      — домен-редиректор (воронка «домен + бот параллельно»)
  trends/                 — RSS-фиды конкурентов, outlier-score
scripts/init_db.sql       — 10 таблиц: очередь, реестр публикаций, бюджет, тренды
scripts/run_pipeline_once.py — локальный прогон без инфраструктуры
```

## Дальше по плану запуска (ТЗ §12)

- [ ] Верификация канала до Advanced features (нужна для кликабельной ссылки — решает человек)
- [ ] Реальный fal.ai ключ → подключить `providers/registry.py` image/TTS адаптеры к живому API
- [ ] Postgres на VPS-воркере → `docker compose up -d`, прогнать `init_db.sql`
- [ ] Telegram-бот: токен, чат приёмки → живой тест `send_for_review`
- [ ] Первые 50-100 роликов → собственный бенчмарк retention@3s (платформенных порогов нет)
- [ ] RSS-трендоскаут: проверить `/feeds/videos.xml` с продового IP (в песочнице заблокирован)
