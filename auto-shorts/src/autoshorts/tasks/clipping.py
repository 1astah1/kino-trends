"""Задачи очереди нарезки (M5-B), тела — `services/clipping.py`.

* `source_select` (light, платно: LLM) — отбор моментов источника: LLM или эвристика без ключа,
  кандидаты и сводка `sources.selection` фиксируются в транзакции успеха; прежние автоматические
  кандидаты без живого clip-job получают `superseded_at`. Ставит `POST /sources/{id}/select` и
  конвейер источника после распознавания (M5-A: статус selecting → ready).
* `author_script_draft` (light, платно) — новый черновик авторского текста ролика
  (`POST /jobs/{id}/author-script/draft`). job_id — в payload: сбой черновика не роняет ролик.
* `author_voice_prepare` (heavy) — запись оператора: тишина по краям, WAV, пословные тайминги ASR
  в дочернем процессе (`POST /jobs/{id}/author-voice`). job_id — в payload.
"""

from __future__ import annotations

from typing import Any

from autoshorts.tasks.registry import TaskContext, register_task


@register_task("source_select", "light", max_attempts=3, paid=True, priority=160)
def source_select(ctx: TaskContext) -> dict[str, Any]:
    from autoshorts.services.clipping import run_source_select

    return run_source_select(ctx)


@register_task("author_script_draft", "light", max_attempts=3, paid=True, priority=50)
def author_script_draft(ctx: TaskContext) -> dict[str, Any]:
    from autoshorts.services.clipping import run_author_script_draft

    return run_author_script_draft(ctx)


@register_task("author_voice_prepare", "heavy", max_attempts=2, priority=50)
def author_voice_prepare(ctx: TaskContext) -> dict[str, Any]:
    from autoshorts.services.clipping import run_author_voice_prepare

    return run_author_voice_prepare(ctx)
