"""Пути и настройки процесса. Всё, что меняется из интерфейса, лежит в таблице settings (см. db.py)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VAR = Path(os.environ.get("KT_VAR", ROOT / "var"))
DICT_DIR = VAR / "dict"
LOG_DIR = VAR / "log"
THUMB_DIR = VAR / "thumbs"
WEB_DIR = ROOT / "web"
DB_PATH = Path(os.environ.get("KT_DB", VAR / "kinotrends.sqlite"))

HOST = os.environ.get("KT_HOST", "127.0.0.1")
PORT = int(os.environ.get("KT_PORT", "18320"))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
WIKIDATA_UA = "kino-trends/0.1 (local research tool; contact: owner)"

# Умолчания настроек, редактируемых из интерфейса. Ключи только латиницей.
DEFAULT_SETTINGS: dict[str, str] = {
    "schedule_hours": "6",          # как часто запускать сбор автоматически (0 = выключено)
    "hashtag_depth": "400",         # сколько шортсов брать со страницы хэштега за один прогон
    "channel_depth": "120",         # сколько шортсов брать со вкладки Shorts канала
    "detail_budget": "2500",        # максимум страниц роликов за прогон
    "detail_concurrency": "4",      # параллельных запросов к страницам роликов
    "refresh_days": "21",           # ролики моложе этого возраста переопрашиваются каждый прогон
    "auto_channels_max": "300",     # сколько каналов держать в автослежении
    "auto_channel_min_shorts": "3", # сколько шортсов канала надо увидеть, чтобы взять канал в слежение
    "youtube_api_key": "",          # необязательно: YouTube Data API v3 даёт лайки и комментарии к любому ролику
    "min_views_default": "0",       # фильтр интерфейса по умолчанию
    "dict_min_sitelinks": "4",      # произведения с меньшим числом статей в Википедии в память распознавателя не грузятся
    "llm_base_url": "https://openrouter.ai/api/v1",  # OpenAI-совместимый endpoint для нейро-опознания и сцен
    "llm_api_key": "",
    "llm_model": "",                # модель со зрением, например google/gemini-2.5-flash
    "neuro_enabled": "1",           # нейро-опознание по кадру в каждом прогоне
    "neuro_per_run": "300",         # максимум роликов на прогон
    "neuro_min_views": "20000",     # ролики с меньшими просмотрами нейросети не показываем
    "neuro_daily_usd": "3",         # дневной потолок расходов на модель, долларов
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "digest_enabled": "1",          # ежедневный дайджест «что резать» в Telegram
    "digest_hour": "9",             # час по Москве
    "alerts_enabled": "1",          # тревога в Telegram, когда произведение резко набирает
    "auto_hashtags_max": "20",      # сколько хэштегов добирать автоматически из топовых роликов
    "schedule_days": "7",           # на сколько дней вперёд строить календарь нарезки
    "schedule_per_day": "2",        # произведений в день
    "schedule_cooldown_days": "10", # не повторять произведение раньше, чем через столько дней
    "public_url": "",               # адрес консоли для ссылок в сообщениях
    "interest_wiki_max": "200",     # сколько произведений обновлять по просмотрам Википедии за прогон
    "interest_trends_max": "40",    # сколько произведений спрашивать у Google Trends за прогон (лимиты жёсткие)
    "search_titles_max": "60",      # сколько произведений искать по названию «<название> #shorts» за прогон
    "title_tags_max": "150",        # сколько хэштегов самих произведений (#рикиморти) держать в источниках
    "comments_per_run": "300",      # комментариев YouTube Data API за прогон для неопознанных роликов (1 единица квоты каждый)
    "storage_host": "",             # сервер-хранилище видео (SSH), пусто = качать на этот сервер
    "storage_user": "root",
    "storage_key": "~/.ssh/kt_storage",
    "storage_dir": "/srv/files",
    "storage_url": "",              # публичный адрес каталога файлов, например http://1.2.3.4/files
    "storage_max_gb": "20",         # потолок хранилища на сервере-хранилище
    "storage_ttl_hours": "72",      # сколько держать файлы
    "rights_policy": "balanced",    # авторские права в календаре: strict = только «проходит», balanced = и «с оговорками», off = только пометка
    "rights_llm_max": "40",         # сколько произведений за прогон спрашивать у модели про правообладателя
    "rights_recheck_max": "400",    # сколько чужих нарезок перепроверять за прогон на сносы
}

for _d in (VAR, DICT_DIR, LOG_DIR, THUMB_DIR):
    _d.mkdir(parents=True, exist_ok=True)
