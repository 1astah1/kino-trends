"""Публичный контур на хосте редиректора `u.<домен>` (docs/ARCHITECTURE.md §5.3): лендинг и редиректор
кодов атрибуции, `/go/*`, приём конверсий по HMAC. ASGI-приложение — `autoshorts.public.app:app`.

Пакет не импортирует приложение при импорте: `public.landing`, `public.botfilter` и
`public.emitter_snippet` используются и из API (`services/ingest.py`).
"""
