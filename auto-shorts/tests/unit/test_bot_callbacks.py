"""callback_data бота и карточка приёмки (M2-C): латиница, ≤ 64 байт, без JSON; экранирование HTML."""

from __future__ import annotations

from typing import Any

import pytest

from autoshorts.bot import callbacks, handlers
from autoshorts.domain.enums import RejectReason
from autoshorts.services.review import ReviewCard


def test_roundtrip_all_actions() -> None:
    cases = [
        ("approve", "pending_review", None),
        ("rework", "qc_failed", None),
        ("reject_menu", "pending_review", None),
        ("back", "qc_failed", None),
        ("override_qc", None, None),
        ("undo", None, None),
    ] + [("reject", "qc_failed", r.value) for r in RejectReason]
    for action, expected, reason in cases:
        data = callbacks.encode(action, 9_999_999_999, expected, reason)
        assert len(data.encode()) <= callbacks.MAX_CALLBACK_BYTES and data.isascii()
        cb = callbacks.decode(data)
        assert cb == callbacks.Callback(action, 9_999_999_999, expected, reason), data


@pytest.mark.parametrize(
    "data",
    [
        None,
        "",
        "ap:12",  # нет ожидаемого статуса
        "ap:0:p",
        "ap:-1:p",
        "zz:12:p",
        "rr:12:p",  # отклонение без причины
        "rr:12:p:not_a_reason",
        "ap:12:p:hook_slabyy",  # причина у одобрения
        "ap:12:x",
        '{"a":"approve"}',
        "ap:12:p ",
        "ар:12:p",  # кириллица
        "ap:" + "1" * 70 + ":p",
    ],
)
def test_decode_rejects_garbage(data: str | None) -> None:
    assert callbacks.decode(data) is None


def test_keyboards() -> None:
    main = [b.data for b in handlers.iter_keyboard(handlers.review_keyboard(5, "pending_review"))]
    assert main == ["ap:5:p", "rw:5:p", "rj:5:p"]
    failed = [b.data for b in handlers.iter_keyboard(handlers.review_keyboard(5, "qc_failed"))]
    assert failed == ["rw:5:q", "rj:5:q", "oq:5"]
    reasons = handlers.reject_keyboard(5, "qc_failed")
    assert len(reasons) == len(RejectReason) + 1 and reasons[0][0].label == "Слабый хук"
    assert reasons[-1][0].data == "bk:5:q"
    assert handlers.undo_keyboard(5)[0][0].data == "un:5"


def test_card_caption_escapes_user_text() -> None:
    card = ReviewCard(
        job_id=1,
        video_uid="a1b2c3d4e5f6",
        status="qc_failed",
        kind="generate",
        channel_id="taro_main",
        channel_title="Таро <b>",
        platform="youtube",
        niche_id="taro",
        topic="<script>alert(1)</script>",
        duration_sec=21.44,
        attempt_count=1,
        flags=[{"code": "tts_mock", "severity": "warn", "detail": "mock & co"}],
        failed_checks=["loudness"],
        voiceover_text="x" * 300,
        preview_path=None,
    )
    text = handlers.card_caption(card)
    assert "<script>" not in text and "&lt;script&gt;" in text and "Таро &lt;b&gt;" in text
    assert "Не прошёл QC" in text and "попытка 2" in text and "21.4 с" in text
    assert "Озвучка-заглушка: mock &amp; co" in text and "QC не пройден: loudness" in text
    assert "x" * 200 + "…" in text and "x" * 201 not in text


def test_ptb_callback_glue_answers_stale_button() -> None:
    """Обёртка python-telegram-bot: устаревшая кнопка → всплывающий ответ, карточка не трогается."""
    import asyncio
    from types import SimpleNamespace

    from autoshorts.bot import telegram_bot

    calls: list[tuple[str, Any]] = []

    class Query:
        data = "garbage"
        message = SimpleNamespace(caption="x", caption_html="x")

        async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
            calls.append(("answer", (text, show_alert)))

        async def edit_message_caption(self, **kw: Any) -> None:
            calls.append(("edit_caption", kw))

        async def edit_message_reply_markup(self, **kw: Any) -> None:
            calls.append(("edit_markup", kw))

    update = SimpleNamespace(callback_query=Query(), effective_user=SimpleNamespace(id=1))
    asyncio.run(telegram_bot.on_callback(update, SimpleNamespace()))  # type: ignore[arg-type]
    assert calls == [("answer", ("Кнопка устарела", True))]
