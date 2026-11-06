"""Сквозной путь M3 (как `scripts/e2e_smoke.py --stand`, но в процессе): приёмка → задача package_build →
пакет → регистрация ссылки → клик по коду на публичном хосте → подписанный bot_start → карточка
публикации показывает 1 посетителя и 1 старт."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from autoshorts.api.schemas.conversions import INGEST_PATH, ingest_signature

BROWSER = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"


@pytest.fixture
def public(client: Any) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from autoshorts.public import app as public_app
    from autoshorts.services import ingest

    ingest.rate_limiter.reset()
    public_app.products.clear()
    with TestClient(public_app.create_app(), headers={"User-Agent": BROWSER}) as c:
        yield c


def test_job_to_publication_to_click_to_bot_start(
    client: Any, public: Any, db: Any, app_env: Any, login_as: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoshorts.services import packages
    from tests.api.test_packages_support import approved_job, run_package_task, seed_m3

    monkeypatch.setattr(packages, "make_llm_client", lambda: None)
    m3 = seed_m3(db)
    operator = login_as("operator")
    admin = login_as("admin")
    job = approved_job(
        db,
        Path(app_env.media_root),
        status="pending_review",
        banner_variant_id=m3["variant_b"],
        campaign_id=m3["campaign_id"],
    )

    # приёмка ставит задачу сборки пакета
    decided = operator.post(
        f"/jobs/{job['id']}/review", json={"decision": "approve", "expected_status": "pending_review"}
    )
    assert decided.status_code == 200, decided.text
    task = db.execute(
        "SELECT * FROM tasks WHERE kind = 'package_build' AND job_id = %s", (job["id"],)
    ).fetchone()
    assert task is not None and task["resource_class"] == "light"
    run_package_task(int(job["id"]))
    pkg = operator.get(f"/jobs/{job['id']}/package").json()
    code = pkg["tracking"]["code"]
    assert pkg["status"] == "ready" and code in pkg["texts"]["description"]

    # оператор скачал, залил и вставил ссылку
    assert operator.get(f"/packages/{pkg['id']}/video").status_code == 200
    created = operator.post(
        "/publications", json={"package_id": pkg["id"], "url": "https://youtube.com/shorts/dQw4w9WgXcQ"}
    )
    assert created.status_code == 201, created.text
    pub_id = created.json()["publication"]["id"]

    # зритель набрал домен/код с плашки → лендинг → кнопка Telegram
    assert public.get(f"/{code.upper()}").status_code == 200
    assert "start=" + code + "_l" in public.get(f"/go/{code}/tg").text
    # превьюер мессенджера статистику не портит
    public.get(f"/{code}", headers={"User-Agent": "TelegramBot (like TwitterBot)"})

    # бот продукта получил /start <code>_l и шлёт bot_start
    secret = admin.post("/products/umka/webhook-secret/rotate", json={}).json()["secret"]
    body = json.dumps(
        {
            "events": [
                {
                    "event_id": "bot-start-1",
                    "type": "bot_start",
                    "occurred_at": "2026-09-27T10:00:00Z",
                    "tag": f"{code}_l",
                    "user_ref": "tg:42",
                }
            ]
        }
    ).encode()
    ts = int(time.time())
    resp = public.post(
        INGEST_PATH,
        content=body,
        headers={
            "X-AS-Product": "umka",
            "X-AS-Timestamp": str(ts),
            "X-AS-Signature": ingest_signature(secret, ts, body),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200 and resp.json()["accepted"] == 1

    card = operator.get(f"/publications/{pub_id}").json()
    assert card["traffic"]["landing_views"] == 1 and card["traffic"]["unique_visitors"] == 1
    assert card["traffic"]["to_telegram"] == 1 and card["traffic"]["bot_starts"] == 1
    conv = db.execute("SELECT * FROM conversions WHERE external_event_id = 'bot-start-1'").fetchone()
    assert conv["publication_id"] == pub_id and conv["via_landing"] is True and conv["job_id"] == job["id"]
    # алиас площадки тоже ведёт на публикацию
    assert public.get("/yt_dQw4w9WgXcQ").status_code == 200
    last = db.execute("SELECT * FROM clicks ORDER BY id DESC LIMIT 1").fetchone()
    assert last["publication_id"] == pub_id and last["code"] == "yt_dQw4w9WgXcQ"
