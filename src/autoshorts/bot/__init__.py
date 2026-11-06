"""Telegram-бот приёмки (docs/ARCHITECTURE.md §2.2): уведомления, решения кнопками, `/videoid`, `/link`.

Публикация РУЧНАЯ (решение владельца): бот не грузит на площадки, а присылает карточку ролика
на приёмку и после ручной заливки регистрирует ID ролика площадки.

* `callbacks.py` — callback_data (≤ 64 байт, латиница, без JSON);
* `handlers.py` — логика без python-telegram-bot (тестируется напрямую): доступ только
  пользователям с привязанным `users.tg_user_id`, решения через `services.review`;
* `telegram_bot.py` — обёртка python-telegram-bot и точка входа `python -m autoshorts.bot.telegram_bot`.
"""
