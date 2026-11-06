"""Telegram-бот: карточки на приёмку, кнопки решений, `/videoid`, `/link` (docs/ARCHITECTURE.md §2.2).

Запуск: `python -m autoshorts.bot.telegram_bot` (systemd `autoshorts-bot.service`, compose-профиль `bot`).

Публикация ручная: бот не заливает на площадки. Он присылает карточку ролика на приёмку в чат
`review_chat_id` (настройка; запасной вариант — env `TELEGRAM_REVIEW_CHAT_ID`), принимает решения
кнопками и регистрирует ID ролика после ручной заливки. Вся логика — `bot/handlers.py` поверх
`services.review` / `services.jobs` (те же функции, что у веба): кнопки и команды работают
только у пользователей с привязанным `users.tg_user_id` и ролью operator/admin.

Токен — секрет `TELEGRAM_BOT_TOKEN` (таблица `secrets`, затем env). Прокси до api.telegram.org —
настройка `telegram_proxy_url`. callback_data — короткие латинские строки (`bot/callbacks.py`).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from autoshorts.bot import handlers

log = logging.getLogger("autoshorts.bot")

REVIEW_POLL_SEC = 10.0
CARDS_PER_POLL = 10
CAPTION_LIMIT = 1024  # подпись к видео в Telegram
REVIEW_CHAT_ENV = "TELEGRAM_REVIEW_CHAT_ID"


def _markup(rows: list[list[handlers.Button]] | None) -> InlineKeyboardMarkup | None:
    if rows is None:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(b.label, callback_data=b.data) for b in row] for row in rows]
    )


def _user_id(update: Update) -> int | None:
    user = update.effective_user
    return int(user.id) if user is not None else None


# ---------------------------------------------------------------- обработчики PTB


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    reply = await asyncio.to_thread(handlers.handle_callback, _user_id(update), query.data)
    await query.answer(reply.text[:190] or None, show_alert=reply.alert)
    message = query.message
    if message is None:
        return
    try:
        if reply.status_line:
            base = getattr(message, "caption_html", None) or getattr(message, "text_html", None) or ""
            text = f"{base}\n\n<b>{reply.status_line}</b>"
            if getattr(message, "caption", None) is not None:
                await query.edit_message_caption(
                    caption=text[:CAPTION_LIMIT],
                    parse_mode=ParseMode.HTML,
                    reply_markup=_markup(reply.keyboard),
                )
            else:
                await query.edit_message_text(
                    text, parse_mode=ParseMode.HTML, reply_markup=_markup(reply.keyboard)
                )
        elif reply.keyboard is not None:
            await query.edit_message_reply_markup(
                reply_markup=_markup(reply.keyboard) if reply.keyboard else None
            )
    except BadRequest as exc:  # «message is not modified» и т. п. — не повод падать
        log.info("bot: cannot edit message: %s", exc)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None or update.message is None:
        return
    reply = handlers.handle_start(chat.id, chat.type)
    await update.message.reply_text(reply.text, parse_mode=ParseMode.HTML)


async def cmd_videoid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    reply = await asyncio.to_thread(handlers.handle_videoid, _user_id(update), list(context.args or []))
    await update.message.reply_text(reply.text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    reply = await asyncio.to_thread(handlers.handle_link, _user_id(update), list(context.args or []))
    await update.message.reply_text(reply.text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


# ---------------------------------------------------------------- карточки на приёмку


def review_chat_id() -> str | None:
    """Чат приёмки: настройка `review_chat_id`, затем env `TELEGRAM_REVIEW_CHAT_ID`."""
    try:
        from autoshorts.services.settings import get_runtime_settings

        value = get_runtime_settings().review_chat_id
    except Exception:  # noqa: BLE001 - БД недоступна: попробуем env, цикл повторится
        log.warning("bot: settings unavailable", exc_info=True)
        value = None
    return value or os.environ.get(REVIEW_CHAT_ENV) or None


async def send_card(app: Application, chat_id: str, card: Any) -> None:
    caption = handlers.card_caption(card)
    markup = _markup(handlers.review_keyboard(card.job_id, card.status))
    if card.preview_path:
        with open(card.preview_path, "rb") as fh:
            await app.bot.send_video(
                chat_id=chat_id,
                video=fh,
                caption=caption[:CAPTION_LIMIT],
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
                supports_streaming=True,
            )
    else:
        await app.bot.send_message(
            chat_id=chat_id,
            text=caption + "\n\n(превью не найдено)",
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )


async def review_poll_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Раз в 10 с: ролики на приёмке без карточки → карточка в чат; отметка только после отправки."""
    chat_id = await asyncio.to_thread(review_chat_id)
    if not chat_id:
        return
    try:
        cards = await asyncio.to_thread(handlers.pending_cards, limit=CARDS_PER_POLL)
    except Exception:  # noqa: BLE001 - БД мигнула — следующий цикл
        log.exception("bot: review poll failed")
        return
    for card in cards:
        try:
            await send_card(context.application, chat_id, card)
        except (TelegramError, OSError):
            log.exception("bot: card for %s not sent, retry next cycle", card.video_uid)
            continue
        await asyncio.to_thread(handlers.mark_card_sent, card.job_id)
        log.info("bot: review card sent for %s", card.video_uid)


# ---------------------------------------------------------------- сборка и запуск


def bot_token() -> str | None:
    from autoshorts.services.secrets import get_secret

    return get_secret("TELEGRAM_BOT_TOKEN")


def proxy_url() -> str | None:
    try:
        from autoshorts.services.settings import get_runtime_settings

        return get_runtime_settings().telegram_proxy_url
    except Exception:  # noqa: BLE001
        return None


def build_app(token: str | None = None, *, proxy: str | None = None) -> Application:
    token = token or bot_token()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан (секреты или env)")
    builder = Application.builder().token(token)
    if proxy:
        builder = builder.proxy(proxy).get_updates_proxy(proxy)
    app = builder.build()
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("videoid", cmd_videoid))
    app.add_handler(CommandHandler("link", cmd_link))
    if app.job_queue is not None:
        app.job_queue.run_repeating(review_poll_job, interval=REVIEW_POLL_SEC, first=1.0)
    else:  # без extras job-queue карточки не рассылаются, решения кнопками работают
        log.warning("bot: python-telegram-bot job-queue extra missing, review cards are disabled")
    return app


def main() -> None:
    from autoshorts.core.logging import configure_logging
    from autoshorts.db.pool import close_pool, init_pool

    configure_logging()
    init_pool(application_name="autoshorts-bot", min_size=1, max_size=3, wait=False)
    try:
        app = build_app(proxy=proxy_url())
        app.run_polling(allowed_updates=["message", "callback_query"])
    finally:
        close_pool()


if __name__ == "__main__":
    main()
