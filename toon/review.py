"""Начальник смотрит готовый ролик целиком (Gemini видит видео 360p со звуком) и решает: в выпуск, доработать или в корзину."""

from __future__ import annotations

import base64
import sqlite3

from kinotrends.ai_review import _small
from kinotrends.llm import LLM, parse_json, record_usage

from . import series

MODEL = "google/gemini-3.8-flash"
PROMPT = """Ты — начальник (шоураннер) сериала «{title}». Перед тобой готовая серия-мультик для ленты YouTube Shorts, взрослая аудитория 20–45.
Сценарий был такой: {logline}
Посмотри ролик целиком со звуком и честно оцени, как зритель в ленте, который листает за полсекунды.
Ответ — только JSON:
{{"score": 1-10, "hook": "держит ли первые 1,5 с и почему", "story": "понятна ли история без контекста", "punchline": "срабатывает ли финал",
 "visual": "картинка: читаются ли герои и эмоции, нет ли брака, обрезанных лиц, пустых кадров",
 "sound": "голоса: естественно ли, совпадает ли рот и субтитры", "kids": true/false — выглядит ли как детский мультик,
 "problems": ["конкретные проблемы"], "verdict": "publish|fix|reject",
 "fix_notes": ["что поправить: сценарию / монтажу / рендеру — с адресатом"]}}
publish — только если score ≥ 7, финал срабатывает и нет брака."""


def watch(conn: sqlite3.Connection, settings: dict, key: str, video: bytes, logline: str) -> dict:
    llm = LLM(settings.get("llm_base_url") or "https://openrouter.ai/api/v1", settings.get("llm_api_key") or "",
              settings.get("toon_chief_model") or MODEL, timeout=240)
    content = [{"type": "video_url", "video_url": {"url": "data:video/mp4;base64," + base64.b64encode(_small(video)).decode()}},
               {"type": "text", "text": PROMPT.format(title=series.S[key]["title"], logline=logline or "")}]
    text, usage = llm.chat([{"role": "user", "content": content}], max_tokens=1200, temperature=0.1)
    record_usage(conn, "toon.chief_video", llm.model, usage)
    res = parse_json(text)
    try:
        score = int(res.get("score") or 0)
    except (TypeError, ValueError):
        score = 0
    res["score"] = score
    if res.get("verdict") == "publish" and (score < 7 or res.get("kids") is True):
        res["verdict"] = "fix"
    return res
