"""Таблица `transcripts` (миграция 0005): транскрипты источников, текущий — один на источник.

Сам транскрипт лежит файлом `MEDIA_ROOT/<sources.media_dir>/transcript.json.gz`
(контракт `clipping/transcript.py`); строка хранит путь и сводку для карточки источника.
"""

from __future__ import annotations

from typing import Any

Row = dict[str, Any]

COLUMNS = (
    "id, source_id, provider_key, model, language, path, words_count, sentences_count, duration_sec,"
    " rtfx, cost_usd, is_current, created_at"
)


def get_current(conn: Any, source_id: int) -> Row | None:
    return conn.execute(
        f"SELECT {COLUMNS} FROM transcripts WHERE source_id = %(id)s AND is_current",
        {"id": int(source_id)},
    ).fetchone()


def insert_current(
    conn: Any,
    *,
    source_id: int,
    provider_key: str,
    model: str | None,
    language: str,
    path: str,
    words_count: int,
    sentences_count: int,
    duration_sec: float | None,
    rtfx: float | None,
    cost_usd: float,
) -> Row:
    """Новый текущий транскрипт; прежний остаётся в истории с is_current = false."""
    conn.execute(
        "UPDATE transcripts SET is_current = false WHERE source_id = %(id)s AND is_current",
        {"id": int(source_id)},
    )
    return conn.execute(
        f"""
        INSERT INTO transcripts (source_id, provider_key, model, language, path, words_count,
                                 sentences_count, duration_sec, rtfx, cost_usd, is_current)
        VALUES (%(source_id)s, %(provider_key)s, %(model)s, %(language)s, %(path)s, %(words)s,
                %(sentences)s, %(duration)s, %(rtfx)s, %(cost)s, true)
        RETURNING {COLUMNS}
        """,
        {
            "source_id": int(source_id),
            "provider_key": provider_key,
            "model": model,
            "language": language,
            "path": path,
            "words": int(words_count),
            "sentences": int(sentences_count),
            "duration": None if duration_sec is None else round(float(duration_sec), 2),
            "rtfx": None if rtfx is None else round(min(float(rtfx), 999999.0), 2),
            "cost": round(float(cost_usd or 0.0), 6),
        },
    ).fetchone()


def list_for(conn: Any, source_id: int) -> list[Row]:
    return conn.execute(
        f"SELECT {COLUMNS} FROM transcripts WHERE source_id = %(id)s ORDER BY id DESC",
        {"id": int(source_id)},
    ).fetchall()


__all__ = ["COLUMNS", "get_current", "insert_current", "list_for"]
