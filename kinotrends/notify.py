"""Уведомления оператору: ежедневный дайджест «что резать» и проверка связи с Telegram."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from . import db, recommend, schedule, stats, telegram

router = APIRouter()
MSK = timezone(timedelta(hours=3))


def _conn():
    return db.connect()


def digest_text(c, settings: dict) -> str:
    f = stats.Filters("30", lang="ru")
    schedule.build(c, f, days=int(settings.get("schedule_days") or 7), per_day=int(settings.get("schedule_per_day") or 2),
                   cooldown=int(settings.get("schedule_cooldown_days") or 10))
    text = recommend.digest_text(recommend.today(c, f, limit=8), base_url=(settings.get("public_url") or "").strip())
    lines = schedule.digest_lines(c)
    if lines:
        text = "📅 " + "\n📅 ".join(lines) + "\n\n" + text
    return text[:4000]


def maybe_digest(c) -> None:
    """Раз в день в заданный час по Москве шлёт дайджест, если настроен бот."""
    settings = db.all_settings(c)
    if settings.get("digest_enabled", "1") != "1" or not settings.get("telegram_bot_token") or not settings.get("telegram_chat_id"):
        return
    now_msk = datetime.now(MSK)
    today = now_msk.strftime("%Y-%m-%d")
    if now_msk.hour < int(settings.get("digest_hour") or 9) or db.get_meta(c, "last_digest_day", "") == today:
        return
    db.set_meta(c, "last_digest_day", today)
    c.commit()
    try:
        text = digest_text(c, settings)
        ok, err = telegram.send(settings["telegram_bot_token"], settings["telegram_chat_id"], text)
        c.execute("INSERT INTO notifications(ts, kind, text, ok) VALUES (?,?,?,?)", (db.now_iso(), "digest", text if ok else f"{text}\n\nОШИБКА: {err}", int(ok)))
        c.commit()
    except Exception as e:  # noqa: BLE001
        c.execute("INSERT INTO notifications(ts, kind, text, ok) VALUES (?,?,?,0)", (db.now_iso(), "digest", f"не собрался: {e}"))
        c.commit()


@router.post("/api/telegram/test")
def telegram_test() -> dict:
    c = _conn()
    try:
        s = db.all_settings(c)
        ok, info = telegram.check(s.get("telegram_bot_token", ""))
        if not ok:
            raise HTTPException(400, f"бот не отвечает: {info}")
        sent, err = telegram.send(s["telegram_bot_token"], s.get("telegram_chat_id", ""), "✅ Кино-тренды: связь с Telegram работает")
        if not sent:
            raise HTTPException(400, f"бот @{info} жив, но сообщение не ушло: {err}")
        return {"ok": True, "bot": info}
    finally:
        c.close()


@router.post("/api/telegram/digest")
def telegram_digest(send: bool = True) -> dict:
    c = _conn()
    try:
        s = db.all_settings(c)
        text = digest_text(c, s)
        if not send:
            return {"ok": True, "text": text, "sent": False}
        ok, err = telegram.send(s.get("telegram_bot_token", ""), s.get("telegram_chat_id", ""), text)
        c.execute("INSERT INTO notifications(ts, kind, text, ok) VALUES (?,?,?,?)", (db.now_iso(), "digest", text, int(ok)))
        c.commit()
        if not ok:
            raise HTTPException(400, f"не отправлено: {err}")
        return {"ok": True, "text": text, "sent": True}
    finally:
        c.close()
