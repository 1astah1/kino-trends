"""Воркеры очереди задач (docs/ARCHITECTURE.md §6.1, §8.1).

Модули: `main.py` (CLI `autoshorts-worker`), `runner.py` (цикл), `heartbeat.py` (аренда и
прогресс), `reaper.py`, `scheduler.py` (лидер 771002), `guards.py` (тяжёлый слот 771001 + flock,
допуск по ресурсам), `policies.py` (классы ошибок и backoff).
"""
