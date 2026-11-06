"""Приёмка ролика нейросетью вместо владельца (01.11.2026: «правь и заливай без меня»): Gemini смотрит готовый
ролик целиком (картинка и звук, сжатый до 360p) как зритель ленты Shorts и решает — публиковать или нет.

Ролик с отказом отклоняется в auto-shorts с причиной; оценка пишется в метрики задания конвейера, чтобы потом
сверить её с реальными просмотрами (оценка ≠ просмотры — пороги калибруются по итогам).
"""

from __future__ import annotations

import base64
import json
import subprocess
import tempfile
from pathlib import Path

from .llm import LLM, LLMError, parse_json

MODEL = "google/gemini-2.5-flash"
MIN_SCORE = 6
CRITICAL = ("не русская речь", "это не фильм", "чёрные кадры", "обрыв на полуслове", "нет звука", "не то произведение")

PROMPT = """Ты строгий продюсер YouTube Shorts для русскоязычной аудитории 16–35 лет. Перед тобой готовый ролик \
канала перед публикацией. Формат канала: {fmt}. Сверху кадра — рекламный баннер VPN-сервиса: это норма, его не \
оценивай, но отметь, если он закрывает лица или главное действие.

{film_line}
Оцени как зритель ленты: остановится ли человек на первых двух секундах и досмотрит ли до конца. Ответ JSON:
{{"same_title": "yes" | "no" | "unsure" (кадры правда из заявленного произведения: актёры, персонажи, мир, рисовка; \
"no" — только если уверен, что это другое: другой фильм, мультфильм, ремейк с другими актёрами),
 "score": 1-10 (общая оценка: стал бы ролик набирать просмотры),
 "hook": 1-10 (первые 2 секунды: цепляет ли кадр/реплика сразу),
 "payoff": true/false (есть ли законченная развязка, панчлайн или сильный обрыв в конце),
 "problems": [только из списка: "медленное начало", "обрыв на полуслове", "нет развязки", "плохое качество картинки",
   "лица обрезаны", "субтитры не совпадают", "не русская речь", "это не фильм", "чёрные кадры", "нет звука", "скучно",
   "баннер закрывает главное", "непонятно без контекста"],
 "verdict": "publish" или "reject",
 "reason": "одно предложение по-русски: почему"}}
Ставь reject, если ролик скучный, непонятный без контекста фильма, обрывается на полуслове, речь не русская \
или это не фильм/сериал (игра, стрим, слайды, обзор)."""

FMT_RU = {
    "edit": "кино-эдит — законченная сцена фильма на весь экран с оригинальным русским дубляжом, крупные слова по центру",
    "quiz": "викторина «Угадай фильм» — сцена, вопрос в начале, ответ голосом в конце",
    "comment": "сцена с комментарием автора",
    "facts": "«за кадром» — голос автора рассказывает факт о фильме поверх его сцены, крупные слова по центру",
}


def _small(video: bytes) -> bytes:
    """360p, 15 к/с, моно — чтобы ролик целиком ушёл модели одним сообщением (~1 МБ на минуту)."""
    with tempfile.TemporaryDirectory() as d:
        src, dst = Path(d) / "in.mp4", Path(d) / "out.mp4"
        src.write_bytes(video)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", "scale=360:-2", "-r", "15", "-c:v", "libx264",
                        "-preset", "veryfast", "-b:v", "350k", "-c:a", "aac", "-ac", "1", "-b:a", "48k", str(dst)],
                       check=True, timeout=300)
        return dst.read_bytes()


def review_video(video: bytes, settings: dict, fmt: str = "edit", film: str | None = None) -> dict:
    """Оценка ролика: {'ok': bool, 'score', 'hook', 'payoff', 'problems', 'verdict', 'reason', 'same_title', 'cost'}.
    film — заявленное произведение («Менталист», 2008): 01.11 под «Менталистом» шла «Менталистка», под «Кунг-фу» — «Леди Баг»."""
    llm = LLM(settings.get("llm_base_url") or "https://openrouter.ai/api/v1", settings.get("llm_api_key") or "",
              settings.get("pipeline_ai_review_model") or MODEL, timeout=240)
    b64 = base64.b64encode(_small(video)).decode()
    content = [{"type": "video_url", "video_url": {"url": "data:video/mp4;base64," + b64}},
               {"type": "text", "text": PROMPT.format(fmt=FMT_RU.get(fmt, FMT_RU["edit"]),
                                                  film_line=f"Заявлено, что это кадры из {film}." if film else "")}]
    text, usage = llm.chat([{"role": "user", "content": content}], max_tokens=600, temperature=0.1)
    res = parse_json(text)
    try:
        score = int(res.get("score") or 0)
    except (TypeError, ValueError):
        score = 0
    problems = [str(p) for p in (res.get("problems") or []) if isinstance(p, str)]
    if film and str(res.get("same_title") or "").lower() == "no":
        problems.append("не то произведение")
    min_score = int(settings.get("pipeline_ai_review_min") or MIN_SCORE)
    ok = res.get("verdict") == "publish" and score >= min_score and not any(p in CRITICAL for p in problems)
    return {"ok": ok, "score": score, "hook": res.get("hook"), "payoff": res.get("payoff"), "problems": problems,
            "verdict": res.get("verdict"), "reason": str(res.get("reason") or "")[:300], "same_title": res.get("same_title"),
            "cost": usage.cost_usd}


REJECT_CODE = {
    "медленное начало": "hook_slabyy", "скучно": "hook_slabyy", "плохое качество картинки": "vizual_artefakty",
    "лица обрезаны": "vizual_artefakty", "чёрные кадры": "vizual_artefakty", "субтитры не совпадают": "subtitry_rassinhron",
    "баннер закрывает главное": "banner_perekryvaet", "нет звука": "zvuk_problema", "не русская речь": "zvuk_problema",
    "не то произведение": "prava_somnitelny",
}


def reject_reason(problems: list[str]) -> str:
    for p in problems:
        if p in REJECT_CODE:
            return REJECT_CODE[p]
    return "drugoe"


__all__ = ["review_video", "reject_reason", "LLMError", "json"]
