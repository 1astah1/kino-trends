"""Интеграция M5+M3 (интегратор): стыки потоков, которых нет в тестах самих потоков.

* UploadGuard: запись голоса автора `POST /jobs/{id}/author-voice` (multipart до 20 МБ) получает свой
  лимит по шаблону пути, а не 1 МБ по умолчанию; сверх лимита — 413 с `asset_error=file_too_large`.
* Мост бота `/videoid` и `/link`: при живом пакете регистрация идёт через `services.publications.register`
  (мастер в masters/, алиас yt_, пакетная ссылка привязана к публикации); нарезку без пакета мост
  не публикует — атрибуция и гейт оригинальности живут в пакете.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from autoshorts.api.schemas.clipping import AUTHOR_VOICE_MAX_BYTES

YT_ID = "Int3gr4t10n"
OPERATOR_TG = 222_000_222


def _multipart(size: int) -> tuple[bytes, str]:
    boundary = "integration-boundary"
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="segment"\r\n\r\ncold_open\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="audio"; filename="a.webm"\r\n'
        "Content-Type: audio/webm\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return head + b"\x1a\x45\xdf\xa3" + b"\0" * max(0, size - 4) + tail, boundary


def test_author_voice_upload_limit_is_per_path(login_as: Any) -> None:
    op = login_as("operator")
    # 2 МБ: больше общего лимита multipart (1 МБ), но в пределах записи голоса → до роутера (нет ролика → 404)
    body, boundary = _multipart(2 * 1024 * 1024)
    resp = op.post(
        "/jobs/999999/author-voice",
        content=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert resp.status_code != 413, resp.text
    assert resp.status_code == 404, resp.text

    # сверх 20 МБ + 1 МБ на форму → 413 от UploadGuard до чтения тела
    body, boundary = _multipart(AUTHOR_VOICE_MAX_BYTES + 2 * 1024 * 1024)
    resp = op.post(
        "/jobs/999999/author-voice",
        content=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert resp.status_code == 413, resp.text
    details = resp.json()["error"]["details"]
    assert details["asset_error"] == "file_too_large"
    assert details["limit_bytes"] == AUTHOR_VOICE_MAX_BYTES + 1024 * 1024

    # другие multipart-пути по-прежнему с общим лимитом
    body, boundary = _multipart(2 * 1024 * 1024)
    resp = op.post(
        "/banner-templates/import-yaml",
        content=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert resp.status_code == 413


@pytest.fixture
def bot_operator(db: Any) -> dict[str, Any]:
    from autoshorts.services.audit import SYSTEM
    from autoshorts.services.users import create_user

    return create_user(
        login="bot_op",
        display_name="Бот-оператор",
        role="operator",
        password="bot-operator-password-1",
        tg_user_id=OPERATOR_TG,
        actor=SYSTEM,
    )


def test_bot_videoid_with_live_package_goes_through_register(
    db: Any, app_env: Any, bot_operator: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoshorts.bot import handlers
    from autoshorts.services import packages
    from tests.api.test_packages_support import approved_job, run_package_task, seed_m3

    monkeypatch.setattr(packages, "make_llm_client", lambda: None)
    m3 = seed_m3(db)
    job = approved_job(
        db, Path(app_env.media_root), banner_variant_id=m3["variant_b"], campaign_id=m3["campaign_id"]
    )
    run_package_task(int(job["id"]))
    pkg = db.execute("SELECT * FROM upload_packages WHERE job_id = %s", (job["id"],)).fetchone()
    assert pkg["status"] == "ready"

    reply = handlers.handle_videoid(OPERATOR_TG, [job["video_uid"], YT_ID])
    assert reply.ok, reply.text
    pub = db.execute("SELECT * FROM publications WHERE job_id = %s", (job["id"],)).fetchone()
    assert pub["package_id"] == pkg["id"] and pub["platform_video_id"] == YT_ID
    assert pub["master_file_path"].startswith("masters/")
    assert (Path(app_env.media_root) / pub["master_file_path"]).is_file()
    alias = db.execute("SELECT * FROM tracking_links WHERE code = %s", (f"yt_{YT_ID}",)).fetchone()
    assert alias is not None and alias["publication_id"] == pub["id"]
    pkg_link = db.execute(
        "SELECT * FROM tracking_links WHERE level = 'package' AND job_id = %s", (job["id"],)
    ).fetchone()
    assert pkg_link["publication_id"] == pub["id"]
    job_status = db.execute("SELECT status FROM jobs WHERE id = %s", (job["id"],)).fetchone()["status"]
    assert job_status == "published"
    assert (
        db.execute("SELECT status FROM upload_packages WHERE id = %s", (pkg["id"],)).fetchone()["status"]
        == "published"
    )


def test_bot_videoid_refuses_clip_without_package(
    db: Any, app_env: Any, bot_operator: dict[str, Any]
) -> None:
    from autoshorts.bot import handlers
    from tests.api.test_packages_support import clip_job, seed_m3

    seed_m3(db)
    job = clip_job(db, Path(app_env.media_root), verdict="fail", channel_id="taro_main")
    reply = handlers.handle_videoid(OPERATOR_TG, [job["video_uid"], YT_ID])
    assert reply.ok is False and "пакет" in reply.text
    assert db.execute("SELECT count(*) AS n FROM publications").fetchone()["n"] == 0
    job_status = db.execute("SELECT status FROM jobs WHERE id = %s", (job["id"],)).fetchone()["status"]
    assert job_status == "approved"
