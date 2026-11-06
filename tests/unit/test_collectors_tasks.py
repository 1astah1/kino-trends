"""Задачи очереди коллекторов (M4-A): регистрация `metrics_poll`/`channel_stats` по §6.1 и прогон обработчика
с payload внеочередного опроса (`POST /publications/{id}/refresh-metrics`, M3-A)."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from autoshorts.collectors import http as chttp
from autoshorts.core.errors import PermanentError
from autoshorts.domain.settings_schema import RuntimeSettings
from autoshorts.tasks.registry import TaskContext, get_task_spec, load_all
from tests.unit.test_collectors_support import FakeYouTube, make_publication, seed_channels


def test_tasks_registered_per_architecture() -> None:
    load_all(strict=True)
    poll = get_task_spec("metrics_poll")
    stats = get_task_spec("channel_stats")
    assert poll is not None and poll.module == "autoshorts.tasks.collectors"
    assert (poll.resource_class, poll.max_attempts, poll.priority, poll.paid) == ("light", 5, 80, False)
    assert stats is not None and (stats.resource_class, stats.max_attempts, stats.priority) == (
        "light",
        5,
        80,
    )


def _ctx(task: dict[str, Any], tmp_path: Path) -> TaskContext:
    from autoshorts.core.storage import MediaLayout

    progress: list[float] = []
    return TaskContext(
        task=task,
        worker_id="test",
        cancel=threading.Event(),
        progress=progress.append,
        log=logging.getLogger("test.collectors"),
        settings=RuntimeSettings(),
        media=MediaLayout(tmp_path),
        log_path=tmp_path / "task.log",
    )


@pytest.fixture
def env(db_pool: Any, db_conn: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    from autoshorts.services import secrets

    monkeypatch.setattr(secrets, "ENV_FILE", None)
    monkeypatch.setenv("YOUTUBE_API_KEY", "task-key")
    secrets.invalidate_cache()
    seed_channels(db_conn)
    yield db_conn
    secrets.invalidate_cache()


def test_metrics_poll_handler_refresh_payload(
    env: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoshorts.db.pool import connection
    from autoshorts.services import publications as publications_service
    from autoshorts.tasks.collectors import metrics_poll

    db = env
    pub = make_publication(db, next_poll_at=datetime.now(UTC) + timedelta(hours=4))
    fake = FakeYouTube(views={pub["platform_video_id"]: 777})
    monkeypatch.setattr(chttp, "transport_override", httpx.MockTransport(fake))
    with connection() as conn:  # как POST /publications/{id}/refresh-metrics
        task_id = publications_service.refresh_metrics(conn, pub["id"])
    task = db.execute("SELECT * FROM tasks WHERE id = %s", (task_id,)).fetchone()
    assert task["kind"] == "metrics_poll" and task["resource_class"] == "light"
    result = metrics_poll(_ctx(task, tmp_path))
    assert result["outcomes"] == {"snapshot": 1} and result["api_calls"] == {"youtube_data.videos_list": 1}
    assert result["collectors"] == {"yt_data:default": "ok"}
    assert (
        db.execute("SELECT latest_views FROM publications WHERE id = %s", (pub["id"],)).fetchone()[
            "latest_views"
        ]
        == 777
    )

    scheduled = metrics_poll(_ctx({"id": 0, "kind": "metrics_poll", "payload": {}}, tmp_path))
    assert scheduled["publications"] == 0  # всё уже по расписанию
    with pytest.raises(PermanentError):
        metrics_poll(_ctx({"id": 0, "kind": "metrics_poll", "payload": {"publication_id": "x"}}, tmp_path))


def test_metrics_poll_downloads_thumbnails_when_asked(
    env: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoshorts.tasks.collectors import metrics_poll

    pub = make_publication(env)
    fake = FakeYouTube()
    jpeg = b"\xff\xd8\xff\xe0" + b"j" * 64

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "i.ytimg.com":
            return httpx.Response(200, content=jpeg)
        return fake(request)

    monkeypatch.setattr(chttp, "transport_override", httpx.MockTransport(handler))
    result = metrics_poll(_ctx({"id": 0, "kind": "metrics_poll", "payload": {"thumbnails": True}}, tmp_path))
    assert result["thumbnails"] == 1
    assert (tmp_path / "thumbs" / "youtube" / f"{pub['platform_video_id']}.jpg").read_bytes() == jpeg
