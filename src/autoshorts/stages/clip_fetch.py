"""Стадия clip-job `clip_fetch` (light, §6.3.1, §6.3.6): только для источников по ссылке.

Качается ТОЛЬКО нужный кусок видео ≤1080p `[start − 3, end + 3]` (yt-dlp `download_ranges`), а не
весь ролик: анализ и отбор шли по прокси 540p и звуку. Кусок копируется без перекодирования
(`services.clipping.fetch_section`): стадия лёгкая, тяжёлое кодирование делает только `render`.
Если у источника уже есть полноценный оригинал (загрузили целиком), стадия ничего не качает.
Секция — `stage_payload.clip_section` (путь от MEDIA_ROOT, начало на шкале источника, размер
кадра); звук берётся из `audio.m4a` источника, выровненного с транскриптом.
"""

from __future__ import annotations

import logging

from autoshorts.core.errors import PermanentError, RetryableError
from autoshorts.stages.base import StageContext, StageResult, register_stage

SECTION_PAD_SEC = 3.0
log = logging.getLogger(__name__)


@register_stage("clip", "clip_fetch", "light", 3, False)
def clip_fetch(ctx: StageContext) -> StageResult:
    from autoshorts.db.pool import connection
    from autoshorts.pipeline.proxy import probe_media
    from autoshorts.services import clipping

    job = ctx.job
    with connection() as conn:
        src = clipping.require_source(conn, int(job["source_id"]))
        sctx = clipping.source_context(conn, src)
    if not clipping.needs_section(sctx):
        return StageResult(payload_patch={"clip_section": None})
    url = src.get("origin_url")
    if not url:
        raise PermanentError("У источника по ссылке нет URL", error_class="source_media_missing")
    start, end = float(job["clip_start_sec"]), float(job["clip_end_sec"])
    duration = float(src.get("duration_sec") or 0.0) or None
    a = max(0.0, start - SECTION_PAD_SEC)
    b = end + SECTION_PAD_SEC if duration is None else min(duration, end + SECTION_PAD_SEC)
    ctx.check_cancelled()
    out_dir = ctx.workdir / f"fetch_{int(ctx.task['id'])}"
    try:
        path = clipping.fetch_section(str(url), a, b, out_dir)
    except Exception as exc:  # noqa: BLE001 - сеть и площадка: повтор с backoff (§6.1)
        # ссылка умерла (файл удалён, ролик снят), а локальное видео анализа на диске — режем из него:
        # качество может быть ниже 1080p, но ролик соберётся, а не упадёт
        if clipping.local_original_video(sctx) is not None:
            log.warning("clip_fetch: секция не скачалась (%s), рендер из локального original_video", exc)
            patch = {"clip_section": None, "clip_section_fallback": "local_original_video"}
            return StageResult(payload_patch=patch)
        if isinstance(exc, (RetryableError, PermanentError)):
            raise
        raise RetryableError(f"Секция не скачалась: {type(exc).__name__}: {exc}"[:400]) from exc
    probe = probe_media(path)
    w, h = probe.display_size
    if not probe.duration or not w or not h:
        raise RetryableError("Секция скачалась битой (нет видео)")
    ctx.progress(1.0)
    # mp4 с edit list начинается ровно с `a`; другой контейнер — с ключевого кадра раньше
    s0 = clipping.section_start(path, a, b, float(probe.duration))
    section = {
        "path": clipping._layout().relpath(path),
        "start": round(s0, 3),
        "end": round(s0 + float(probe.duration), 3),
        "duration": round(float(probe.duration), 3),
        "width": int(w),
        "height": int(h),
        "fps": float(probe.fps or 30.0),
        "has_audio": bool(probe.has_audio),
    }
    return StageResult(payload_patch={"clip_section": section})
