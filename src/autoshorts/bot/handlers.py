"""Логика бота без python-telegram-bot: кто жмёт, что решено, что ответить (§2.2, §3.3 п. 5).

Бот работает только через сервисы (`services.review`, `services.jobs`), как веб. Любое действие,
кроме `/start` и `/link` без аргументов (они показывают только ID самого пишущего), требует
активного пользователя с `users.tg_user_id` = ID отправителя и ролью operator или admin:
чужой участник чата кнопки приёмки нажать не может.

Функции синхронные (psycopg): обёртка `telegram_bot.py` вызывает их через `asyncio.to_thread`.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any

from autoshorts.bot import callbacks
from autoshorts.core.errors import AppError, JobStateConflict
from autoshorts.domain.enums import LABELS, DecidedVia, JobStatus, RejectReason, ReviewFlagCode, Role
from autoshorts.services import jobs as jobs_service
from autoshorts.services import review as review_service

log = logging.getLogger("autoshorts.bot")

ConnectionFactory = Callable[[], AbstractContextManager[Any]]

VIA_LABELS = {DecidedVia.WEB.value: "веб", DecidedVia.TELEGRAM.value: "Telegram", DecidedVia.API.value: "API"}
DECISION_DONE = {
    "approve": "Одобрено",
    "rework": "На доработку",
    "reject": "Отклонено",
    "override_qc": "Принято вопреки QC",
    "undo": "Решение отменено",
}
NOT_LINKED = (
    "Нет доступа: ваш Telegram не привязан к пользователю. "
    "Команда /link покажет ваш Telegram ID — владелец впишет его в «Настройки → Пользователи»."
)


@dataclass(frozen=True)
class Button:
    label: str
    data: str


@dataclass
class Reply:
    """Ответ бота. Для кнопки: `text` — всплывающий ответ, `status_line` — строка к карточке,
    `keyboard` — новые кнопки карточки (None — не трогать, [] — убрать)."""

    text: str
    ok: bool = True
    alert: bool = False
    keyboard: list[list[Button]] | None = None
    status_line: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _default_connection() -> AbstractContextManager[Any]:
    from autoshorts.db.pool import connection

    return connection()


# ---------------------------------------------------------------- клавиатуры и карточка


def review_keyboard(job_id: int, status: str) -> list[list[Button]]:
    if status == JobStatus.QC_FAILED.value:
        return [
            [
                Button("На доработку", callbacks.encode("rework", job_id, status)),
                Button("Отклонить", callbacks.encode("reject_menu", job_id, status)),
            ],
            [Button("Принять вопреки QC (admin)", callbacks.encode("override_qc", job_id))],
        ]
    return [
        [
            Button("Одобрить", callbacks.encode("approve", job_id, status)),
            Button("На доработку", callbacks.encode("rework", job_id, status)),
        ],
        [Button("Отклонить", callbacks.encode("reject_menu", job_id, status))],
    ]


def reject_keyboard(job_id: int, status: str) -> list[list[Button]]:
    rows = [
        [Button(LABELS[RejectReason][r.value], callbacks.encode("reject", job_id, status, r.value))]
        for r in RejectReason
    ]
    rows.append([Button("Назад", callbacks.encode("back", job_id, status))])
    return rows


def undo_keyboard(job_id: int) -> list[list[Button]]:
    return [[Button("Отменить решение (10 мин)", callbacks.encode("undo", job_id))]]


def card_caption(card: review_service.ReviewCard) -> str:
    """Подпись карточки на приёмку (HTML, всё пользовательское экранировано)."""
    e = html.escape
    status = LABELS[JobStatus].get(card.status, card.status)
    lines = [
        f"<b>{e(card.video_uid)}</b> · {e(card.channel_title or card.channel_id)} ({e(card.platform)})",
        f"Статус: {e(status)} · попытка {card.attempt_count + 1}",
    ]
    if card.topic:
        lines.append(f"Тема: {e(card.topic)}")
    if card.duration_sec is not None:
        lines.append(f"Длительность: {card.duration_sec:.1f} с")
    if card.failed_checks:
        lines.append("QC не пройден: " + e(", ".join(card.failed_checks)))
    for flag in card.flags:
        label = LABELS[ReviewFlagCode].get(flag["code"], flag["code"])
        detail = f": {flag['detail']}" if flag.get("detail") else ""
        lines.append(f"• {e(label)}{e(detail)}")
    if card.voiceover_text:
        text = card.voiceover_text[:200] + ("…" if len(card.voiceover_text) > 200 else "")
        lines.append(f"\n<i>{e(text)}</i>")
    return "\n".join(lines)


# ---------------------------------------------------------------- доступ


def _caller(conn: Any, tg_user_id: int | None) -> jobs_service.Caller | None:
    caller = review_service.reviewer_for_tg(conn, tg_user_id)
    if caller is None or not caller.at_least(Role.OPERATOR):
        return None
    return caller


def _denied(tg_user_id: int | None, what: str) -> Reply:
    log.warning("bot: %s from unauthorized tg_user_id=%s rejected", what, tg_user_id)
    return Reply(NOT_LINKED, ok=False, alert=True)


def _error_reply(exc: AppError) -> Reply:
    if isinstance(exc, JobStateConflict):
        d = exc.details or {}
        who = d.get("decided_by") or "кто-то"
        via = VIA_LABELS.get(str(d.get("decided_via")), str(d.get("decided_via") or ""))
        decision = DECISION_DONE.get(str(d.get("decision")), "")
        if d.get("reason"):  # отмена решения невозможна: окно истекло, пакет скачан, …
            return Reply(exc.message, ok=False, alert=True)
        suffix = f": {decision.lower()} — {who}" + (f" ({via})" if via else "") if decision else ""
        return Reply(f"Уже решено{suffix}", ok=False, alert=True, keyboard=[])
    return Reply(exc.message, ok=False, alert=True)


# ---------------------------------------------------------------- кнопки


def handle_callback(
    tg_user_id: int | None, data: str | None, *, connection: ConnectionFactory = _default_connection
) -> Reply:
    cb = callbacks.decode(data)
    if cb is None:
        return Reply("Кнопка устарела", ok=False, alert=True)
    with connection() as conn:
        caller = _caller(conn, tg_user_id)
    if caller is None:
        return _denied(tg_user_id, f"callback {cb.action}:{cb.job_id}")

    if cb.action == "reject_menu":
        return Reply("Выберите причину", keyboard=reject_keyboard(cb.job_id, cb.expected_status or ""))
    if cb.action == "back":
        return Reply("", keyboard=review_keyboard(cb.job_id, cb.expected_status or ""))

    try:
        with connection() as conn:
            if cb.action == "undo":
                job = review_service.undo(conn, cb.job_id, caller)
                label = DECISION_DONE["undo"]
                keyboard = review_keyboard(cb.job_id, job["status"])
                # кнопки решения возвращаются в эту же карточку — новая карточка не нужна
                review_service.mark_card_sent(conn, cb.job_id)
            else:
                decision = cb.action
                inp = review_service.DecisionInput(
                    decision=decision,
                    expected_status=cb.expected_status or JobStatus.QC_FAILED.value,
                    reject_reason=cb.reject_reason,
                    auto_restart=True,
                )
                job = review_service.decide(conn, cb.job_id, inp, caller)
                label = DECISION_DONE[decision]
                if cb.reject_reason:
                    label += f": {LABELS[RejectReason].get(cb.reject_reason, cb.reject_reason)}"
                keyboard = undo_keyboard(cb.job_id)
    except AppError as exc:
        log.info("bot: %s on job %s by user %s -> %s", cb.action, cb.job_id, caller.user_id, exc.code)
        return _error_reply(exc)
    who = caller.display_name or f"user {caller.user_id}"
    return Reply(
        label,
        status_line=f"{label} — {who}",
        keyboard=keyboard,
        extra={"job_id": cb.job_id, "status": job["status"]},
    )


# ---------------------------------------------------------------- команды


def handle_start(chat_id: int, chat_type: str) -> Reply:
    return Reply(
        "Бот приёмки Auto Shorts.\n\n"
        f"chat_id этого чата: <code>{int(chat_id)}</code> (тип: {html.escape(chat_type)}).\n"
        "Впишите его в настройку «Telegram-чат приёмки» (review_chat_id) — сюда будут приходить карточки.\n"
        "Кнопки приёмки работают только у пользователей с привязанным Telegram ID (/link)."
    )


def _publish_reply(result: dict[str, Any]) -> Reply:
    e = html.escape
    return Reply(
        f"Привязано: {e(result['platform'])} <code>{e(result['platform_video_id'])}</code>\n"
        f"Ссылка: {e(result['url'])}\n"
        f"Метка атрибуции: <code>{e(result['attribution_tag'])}</code>",
        extra={"publication_id": result["publication_id"]},
    )


def _bind(tg_user_id: int | None, job_ref: str, raw: str, connection: ConnectionFactory, what: str) -> Reply:
    with connection() as conn:
        caller = _caller(conn, tg_user_id)
    if caller is None:
        return _denied(tg_user_id, what)
    try:
        with connection() as conn:
            result = jobs_service.bind_platform_video(conn, job_ref, raw, caller)
    except AppError as exc:
        return Reply(html.escape(exc.message), ok=False)
    return _publish_reply(result)


def handle_videoid(
    tg_user_id: int | None, args: list[str], *, connection: ConnectionFactory = _default_connection
) -> Reply:
    """`/videoid <video_uid> <ID или ссылка>`: площадка — из канала ролика, формат ID проверяется."""
    if len(args) != 2:
        return Reply(
            "Формат: /videoid &lt;video_uid&gt; &lt;ID ролика на площадке или ссылка&gt;\n"
            "YouTube — 11 символов (dQw4w9WgXcQ), VK — owner_video (-123456_456239017).",
            ok=False,
        )
    return _bind(tg_user_id, args[0], args[1], connection, "/videoid")


def handle_link(
    tg_user_id: int | None, args: list[str], *, connection: ConnectionFactory = _default_connection
) -> Reply:
    """`/link` — ваш Telegram ID и привязка; `/link <video_uid|код пакета> <ссылка>` — регистрация заливки."""
    if not args:
        if tg_user_id is None:
            return Reply("Не удалось определить ваш Telegram ID", ok=False)
        with connection() as conn:
            user = review_service.linked_user_for_tg(conn, tg_user_id)
        head = f"Ваш Telegram ID: <code>{int(tg_user_id)}</code>\n"
        if user is None:
            return Reply(
                head + "Привязки нет. Владелец вписывает этот ID в «Настройки → Пользователи → Telegram ID»; "
                "после этого вам станут доступны кнопки приёмки."
            )
        role = LABELS[Role].get(user["role"], user["role"])
        return Reply(
            head + f"Привязан к пользователю {html.escape(user['display_name'])} ({html.escape(role)})."
        )
    if len(args) != 2:
        return Reply("Формат: /link &lt;video_uid или код пакета&gt; &lt;ссылка на ролик&gt;", ok=False)
    return _bind(tg_user_id, args[0], args[1], connection, "/link")


# ---------------------------------------------------------------- карточки на приёмку


def pending_cards(*, limit: int = 10, connection: ConnectionFactory = _default_connection) -> list[Any]:
    with connection() as conn:
        return review_service.pending_cards(conn, limit=limit)


def mark_card_sent(job_id: int, *, connection: ConnectionFactory = _default_connection) -> None:
    with connection() as conn:
        review_service.mark_card_sent(conn, job_id)


def iter_keyboard(rows: list[list[Button]]) -> Iterator[Button]:
    for row in rows:
        yield from row
