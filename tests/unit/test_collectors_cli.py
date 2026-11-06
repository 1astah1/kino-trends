"""`autoshorts-admin collectors-once --publication <id>` обновляет `latest_views` (критерий M4 на стенде),
`collectors-status` печатает состояние. Сеть — `collectors.http.transport_override` (MockTransport)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from autoshorts.cli import main
from autoshorts.collectors import http as chttp
from tests.unit.test_collectors_support import FakeYouTube, make_publication, seed_channels


@pytest.fixture
def cli_env(app_env: Any, db_conn: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    from autoshorts.services import secrets, settings

    monkeypatch.setattr(secrets, "ENV_FILE", None)
    monkeypatch.setenv("YOUTUBE_API_KEY", "cli-key")
    monkeypatch.delenv("VK_ACCESS_TOKEN", raising=False)
    secrets.invalidate_cache()
    settings.invalidate_cache()
    seed_channels(db_conn)
    yield db_conn
    secrets.invalidate_cache()
    settings.invalidate_cache()


def test_collectors_once_for_one_publication(
    cli_env: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = cli_env
    pub = make_publication(db, next_poll_at=datetime.now(UTC) + timedelta(hours=3))
    other = make_publication(db)
    fake = FakeYouTube(views={pub["platform_video_id"]: 31337})
    monkeypatch.setattr(chttp, "transport_override", httpx.MockTransport(fake))
    rc = main(["collectors-once", "--publication", str(pub["id"])])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert f"публикация {pub['id']} (youtube): снимок; просмотры — → 31337" in out
    assert "коллектор yt_data:default: ok" in out
    row = db.execute(
        "SELECT latest_views, last_polled_at FROM publications WHERE id = %s", (pub["id"],)
    ).fetchone()
    assert row["latest_views"] == 31337 and row["last_polled_at"] is not None
    assert fake.video_calls[0]["params"]["key"] == "cli-key"
    # другая созревшая публикация не тронута — опрошена только указанная
    assert (
        db.execute("SELECT latest_views FROM publications WHERE id = %s", (other["id"],)).fetchone()[
            "latest_views"
        ]
        is None
    )

    rc = main(["collectors-once", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert (
        rc == 0 and data["outcomes"] == {"snapshot": 1} and data["items"][0]["publication_id"] == other["id"]
    )

    rc = main(["collectors-once", "--publication", "999999"])
    captured = capsys.readouterr()
    assert rc == 1 and "999999" in captured.err


def test_collectors_status(
    cli_env: Any, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("YOUTUBE_API_KEY")
    make_publication(cli_env)
    rc = main(["collectors-status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "yt_data:default — disabled (no_api_key), ключ YOUTUBE_API_KEY: нет" in out
    assert "квота youtube_data:default" in out and "из 10000 единиц" in out
