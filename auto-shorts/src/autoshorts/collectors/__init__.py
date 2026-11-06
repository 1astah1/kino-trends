"""Коллекторы метрик своих публикаций (docs/ARCHITECTURE.md §6.5, поток M4-A).

Модули пакета — только сеть и разбор ответов площадок, без БД:

* `http` — общий HTTP-клиент: таймаут 15 с, 3 ретрая, прокси `collectors_proxy_url`, классификация
  ошибок (`CollectorError.kind`), ключи и токены вырезаются из текстов ошибок;
* `youtube_data` — YouTube Data API v3 по ключу куста: `videos.list` пачками по 50 (1 единица),
  `channels.list` для снимков канала, толкование статусов (снят, санкция, заявка правообладателя);
* `vk_video` — VK: `video.get` через vk_api при заданном токене, иначе метаданные yt-dlp с паузой
  между вызовами; дальше по цепочке — ручной ввод;
* `thumbnails` — необязательная выгрузка обложек в `MEDIA_ROOT/thumbs/<platform>/<id>.jpg`.

Оркестрация (лестница опроса, квоты, снимки, алерты) — `services/metrics.py`, SQL —
`repos/metrics.py` и `repos/quota.py`, задачи очереди — `tasks/collectors.py`.
"""

from __future__ import annotations

from autoshorts.collectors.http import CollectorError, ErrorKind

__all__ = ["CollectorError", "ErrorKind"]
