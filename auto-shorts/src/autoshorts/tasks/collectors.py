"""Задачи коллекторов метрик (docs/ARCHITECTURE.md §6.1, §6.5). Владелец — M4-A.

* `metrics_poll` (light, 5 попыток, приоритет 80) — расписание `metrics_poll` раз в 5 минут (0009):
  созревшие публикации (`next_poll_at <= now()`); `payload.publication_id` — внеочередной опрос одной
  публикации (`POST /publications/{id}/refresh-metrics`, M3-A); `payload.thumbnails=true` — скачать
  обложки к себе. Прогон не падает из-за площадки: ошибки пишутся в публикации и `collector_state`.
* `channel_stats` (light, 5 попыток) — раз в сутки в 06:00 МСК: снимки каналов `channel_snapshots`
  (YouTube `channels.list`, VK `groups.getById` при токене).
"""

from __future__ import annotations

from typing import Any

from autoshorts.core.errors import PermanentError
from autoshorts.tasks.registry import TaskContext, register_task


@register_task("metrics_poll", "light", max_attempts=5)
def metrics_poll(ctx: TaskContext) -> dict[str, Any]:
    from autoshorts.services import metrics as metrics_service

    payload = ctx.payload
    ids = None
    if payload.get("publication_id") is not None:
        try:
            ids = [int(payload["publication_id"])]
        except (TypeError, ValueError) as exc:
            raise PermanentError("metrics_poll: bad publication_id", error_class="bad_payload") from exc
    platform = payload.get("platform")
    if platform not in (None, "youtube", "vk"):
        raise PermanentError(f"metrics_poll: bad platform {platform!r}", error_class="bad_payload")
    deps = metrics_service.PollDeps(thumbnails=bool(payload.get("thumbnails")), media=ctx.media)
    report = metrics_service.poll_metrics(
        settings=ctx.settings,
        publication_ids=ids,
        platform=platform,
        deps=deps,
        cancel=ctx.cancel,
        progress=ctx.progress,
    )
    result = report.as_dict()
    ctx.log.info(
        "metrics_poll: %s", {k: result[k] for k in ("publications", "outcomes", "api_calls", "collectors")}
    )
    return result


@register_task("channel_stats", "light", max_attempts=5)
def channel_stats(ctx: TaskContext) -> dict[str, Any]:
    from autoshorts.services import metrics as metrics_service

    result = metrics_service.collect_channel_stats(settings=ctx.settings, cancel=ctx.cancel)
    ctx.log.info("channel_stats: %s", result)
    return result
