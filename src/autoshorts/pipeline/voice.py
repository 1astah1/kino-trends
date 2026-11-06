"""
Озвучка + пословные тайминги БЕЗ Whisper.
Ключевая находка из MoneyPrinterTurbo (voice.py:2679, ТЗ §2): edge-tts отдаёт WordBoundary-кьюсы
прямо при синтезе — Whisper не нужен вообще. На CPU без GPU это разница между 5 и 60 секундами на ролик.

M0 (ARCHITECTURE §3.3 п.1): в edge-tts 7.x `Communicate(..., boundary=...)` по умолчанию равен
"SentenceBoundary", и WordBoundary-кьюсы не приходят вовсе. Поэтому boundary="WordBoundary" передаётся
явно, а ноль слов — это ошибка стадии, а не повод молча подставить заглушку.

Mock-заглушки (озвучка, LLM, картинки) при APP_ENV=prod запрещены, пока явно не включён
ALLOW_MOCK_IN_PROD. Проверка живёт здесь до M1, потом переезжает в providers/registry.

Silero v5_cis_base (MIT) — для чисто русской продакшен-озвучки без облака и без денег.
edge-tts используется здесь как источник word-level таймингов (плюс как TTS-фолбэк).
"""
from __future__ import annotations

import asyncio
import os
import random
import time
from pathlib import Path

import aiohttp
import edge_tts
from edge_tts.exceptions import EdgeTTSException

from autoshorts.config import SETTINGS  # noqa: F401  (импорт подгружает .env до чтения APP_ENV)
from autoshorts.models import WordTiming

# Голос по умолчанию для word-boundary таймингов и как fallback-TTS.
EDGE_VOICE_RU = "ru-RU-DmitryNeural"

# Без этого параметра edge-tts >= 7 отдаёт только SentenceBoundary (см. докстринг модуля).
EDGE_BOUNDARY = "WordBoundary"

# Сетевые сбои edge-tts на этой машине случаются (NoAudioReceived пару раз из четырёх подряд,
# проверено 26.09.2026), поэтому транспортные ошибки повторяются внутри стадии, а не жгут её попытки.
# Бесплатный endpoint Microsoft то и дело отвечает пустым потоком (NoAudioReceived): в октябре 2026 проходил
# примерно каждый четвёртый запрос. 10 попыток с растущей паузой дают ~94% на реплику; переопределяется
# AS_EDGE_TTS_ATTEMPTS.
EDGE_TRANSPORT_ATTEMPTS = int(os.environ.get("AS_EDGE_TTS_ATTEMPTS") or 10)
EDGE_RETRY_DELAY_SEC = 2.0
EDGE_RETRY_DELAY_MAX_SEC = 10.0
_TRANSIENT_ERRORS = (EdgeTTSException, aiohttp.ClientError, asyncio.TimeoutError, OSError)

_PROD_ENVS = frozenset({"prod", "production"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})


class VoiceSynthesisError(RuntimeError):
    """Синтез не дал пригодного результата: нет аудио или нет пословных таймингов."""


class MockInProdError(RuntimeError):
    """Mock-провайдер вызван при APP_ENV=prod без ALLOW_MOCK_IN_PROD."""


def is_prod() -> bool:
    return os.getenv("APP_ENV", "").strip().lower() in _PROD_ENVS


def mock_allowed() -> bool:
    """Mock разрешён везде, кроме prod. В prod — только при ALLOW_MOCK_IN_PROD=true."""
    if not is_prod():
        return True
    return os.getenv("ALLOW_MOCK_IN_PROD", "").strip().lower() in _TRUTHY


def ensure_mock_allowed(component: str) -> None:
    if not mock_allowed():
        raise MockInProdError(
            f"Mock-провайдер '{component}' запрещён при APP_ENV=prod. "
            f"Настройте реальный провайдер или явно выставьте ALLOW_MOCK_IN_PROD=true."
        )


async def _synthesize_with_word_boundaries(text: str, out_path: Path, voice: str) -> tuple[list[WordTiming], int]:
    communicate = edge_tts.Communicate(text, voice, boundary=EDGE_BOUNDARY)
    words: list[WordTiming] = []
    audio_bytes = 0

    with out_path.open("wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
                audio_bytes += len(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                # offset/duration приходят в 100-наносекундных тиках (как в SSML)
                start = chunk["offset"] / 10_000_000
                dur = chunk["duration"] / 10_000_000
                words.append(WordTiming(word=chunk["text"], start_sec=start, end_sec=start + dur))
    return words, audio_bytes


def synthesize_voiceover(text: str, out_dir: Path, job_id: str,
                          voice: str = EDGE_VOICE_RU) -> tuple[Path, list[WordTiming], int]:
    """Возвращает путь к аудио, пословные тайминги и latency в мс.

    Бросает VoiceSynthesisError, если edge-tts не вернул ни одного слова или ни байта аудио.
    Недоделанный mp3 при этом удаляется, чтобы стадия render не подхватила его как готовую озвучку.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{job_id}_voice.mp3"

    t0 = time.monotonic()
    for attempt in range(1, EDGE_TRANSPORT_ATTEMPTS + 1):
        try:
            words, audio_bytes = asyncio.run(_synthesize_with_word_boundaries(text, out_path, voice))
            break
        except _TRANSIENT_ERRORS:
            out_path.unlink(missing_ok=True)
            if attempt == EDGE_TRANSPORT_ATTEMPTS:
                raise
            delay = min(EDGE_RETRY_DELAY_MAX_SEC, EDGE_RETRY_DELAY_SEC * attempt)
            time.sleep(delay + (random.uniform(0.0, 1.0) if delay else 0.0))
        except BaseException:
            out_path.unlink(missing_ok=True)
            raise
    latency_ms = int((time.monotonic() - t0) * 1000)

    if not words or audio_bytes == 0:
        out_path.unlink(missing_ok=True)
        raise VoiceSynthesisError(
            f"edge-tts вернул {len(words)} WordBoundary-кьюсов и {audio_bytes} байт аудио "
            f"(голос {voice}, boundary={EDGE_BOUNDARY}) — проверить голос/сеть. "
            "Без пословных таймингов караоке-субтитры не собрать."
        )

    # Остаток прежнего mock-прогона того же ролика мешает render: тот берёт первый *_voice.* файл.
    (out_dir / f"{job_id}_voice.mock.txt").unlink(missing_ok=True)
    return out_path, words, latency_ms


def mock_synthesize_voiceover(text: str, out_dir: Path, job_id: str) -> tuple[Path, list[WordTiming], int]:
    """Офлайн-заглушка для тестов пайплайна без сети: считает тайминги по средней скорости речи.

    При APP_ENV=prod без ALLOW_MOCK_IN_PROD бросает MockInProdError.
    """
    ensure_mock_allowed("tts")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{job_id}_voice.mock.txt"
    out_path.write_text(text, encoding="utf-8")

    words: list[WordTiming] = []
    t = 0.0
    for w in text.split():
        dur = len(w) * 0.06 + 0.08  # грубая оценка длины слова
        words.append(WordTiming(word=w, start_sec=t, end_sec=t + dur))
        t += dur + 0.05
    return out_path, words, 0
