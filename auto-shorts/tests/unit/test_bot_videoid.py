"""`/videoid` и `/link` бота (M2-C, запрос M0 §3.3 п. 5): площадка — из канала ролика, формат ID
проверяется (YouTube `^[A-Za-z0-9_-]{11}$`, VK owner_video), команды — только привязанным операторам."""

from __future__ import annotations

from typing import Any

import pytest

from autoshorts.bot import handlers
from autoshorts.core.errors import AppError, ValidationFailed
from autoshorts.services.jobs import parse_platform_video_ref
from tests.api.test_jobs_support import job_row, make_job, seed_catalog

OPERATOR_TG = 111_000_111
YT_ID = "dQw4w9WgXcQ"
VK_ID = "-220754053_456239017"


@pytest.fixture
def catalog(db: Any) -> dict[str, Any]:
    return seed_catalog(db)


# ---------------------------------------------------------------- разбор ID (без БД)


@pytest.mark.parametrize(
    "text",
    [
        YT_ID,
        f"https://www.youtube.com/shorts/{YT_ID}",
        f"https://youtube.com/shorts/{YT_ID}?si=abc",
        f"youtube.com/shorts/{YT_ID}",
        f"https://m.youtube.com/watch?v={YT_ID}&t=3",
        f"https://youtu.be/{YT_ID}",
        f"https://www.youtube.com/embed/{YT_ID}",
    ],
)
def test_youtube_forms(text: str) -> None:
    ref = parse_platform_video_ref("youtube", text)
    assert (ref.video_id, ref.platform_kind) == (YT_ID, "short")
    assert ref.url == f"https://www.youtube.com/shorts/{YT_ID}" and ref.attribution_tag == f"yt_{YT_ID}"


@pytest.mark.parametrize(
    "text",
    [
        VK_ID,
        f"clip{VK_ID}",
        f"https://vk.com/clip{VK_ID}",
        f"https://vk.com/video{VK_ID}",
        f"https://vkvideo.ru/clip{VK_ID}",
        f"https://m.vk.com/clip{VK_ID}?from=feed",
        f"https://vk.com/clips?z=clip{VK_ID}%2Fsomething",
    ],
)
def test_vk_forms(text: str) -> None:
    ref = parse_platform_video_ref("vk", text)
    assert (ref.video_id, ref.platform_kind) == (VK_ID, "clip")
    assert ref.url == f"https://vk.com/clip{VK_ID}" and ref.attribution_tag == f"vk_{VK_ID}"


@pytest.mark.parametrize(
    ("platform", "text"),
    [
        ("youtube", "dQw4w9WgXc"),  # 10 символов
        ("youtube", "dQw4w9WgXcQQ"),
        ("youtube", "dQw4w9WgXcЯ"),  # кириллица
        ("youtube", "dQw4w9 WgXc"),
        ("youtube", ""),
        ("youtube", "https://youtube.com/@channel"),
        ("vk", YT_ID),  # не owner_video
        ("vk", "220754053"),
        ("vk", "-220754053_"),
        ("vk", "клип-1_2"),
    ],
)
def test_bad_ids_are_422(platform: str, text: str) -> None:
    with pytest.raises(ValidationFailed) as info:
        parse_platform_video_ref(platform, text)
    assert info.value.status == 422


def test_url_for_other_platform_or_foreign_host() -> None:
    with pytest.raises(AppError) as info:
        parse_platform_video_ref("vk", f"https://youtube.com/shorts/{YT_ID}")
    assert (info.value.status, info.value.code) == (422, "platform_mismatch")
    with pytest.raises(AppError) as info:
        parse_platform_video_ref("youtube", f"https://vk.com/clip{VK_ID}")
    assert info.value.code == "platform_mismatch"
    with pytest.raises(AppError) as info:
        parse_platform_video_ref("youtube", "https://evil.example/shorts/dQw4w9WgXcQ")
    assert info.value.code == "unsupported_url"


# ---------------------------------------------------------------- команды бота (БД)


@pytest.fixture
def db(db_conn: Any) -> Any:
    return db_conn


@pytest.fixture
def operator(db_pool: Any, fast_password_hasher: None) -> dict[str, Any]:
    from autoshorts.services.audit import SYSTEM
    from autoshorts.services.users import create_user

    return create_user(
        login="olga",
        display_name="Ольга",
        role="operator",
        password="olga-password-1",
        tg_user_id=OPERATOR_TG,
        actor=SYSTEM,
    )


def _final(db: Any, job: dict[str, Any]) -> None:
    db.execute(
        "INSERT INTO artifacts (job_id, kind, path, input_hash, stage) "
        "VALUES (%s, 'final', %s, 'h', 'render')",
        (job["id"], f"jobs/{job['video_uid']}/final.mp4"),
    )


def test_videoid_requires_linked_operator(operator: dict, catalog: dict, db: Any) -> None:
    job = make_job(db, status="approved")
    reply = handlers.handle_videoid(999, [job["video_uid"], YT_ID])
    assert reply.ok is False and "не привязан" in reply.text
    assert job_row(db, job["id"])["status"] == "approved"


def test_videoid_youtube_channel(operator: dict, catalog: dict, db: Any) -> None:
    job = make_job(
        db, status="approved", banner_variant_id=catalog["variant_a"], campaign_id=catalog["campaign_id"]
    )
    _final(db, job)
    reply = handlers.handle_videoid(OPERATOR_TG, [job["video_uid"], VK_ID])  # VK-ID у YouTube-канала
    assert reply.ok is False and "youtube" in reply.text.lower()
    assert job_row(db, job["id"])["status"] == "approved"
    reply = handlers.handle_videoid(OPERATOR_TG, [job["video_uid"], f"https://youtube.com/shorts/{YT_ID}"])
    assert reply.ok, reply.text
    pub = db.execute("SELECT * FROM publications WHERE job_id = %s", (job["id"],)).fetchone()
    assert (pub["platform"], pub["platform_video_id"], pub["platform_kind"]) == ("youtube", YT_ID, "short")
    assert pub["attribution_tag"] == f"yt_{YT_ID}" and pub["url"] == f"https://www.youtube.com/shorts/{YT_ID}"
    assert pub["master_file_path"] == f"jobs/{job['video_uid']}/final.mp4"
    assert pub["registered_by"] == operator["id"] and pub["banner_variant_id"] == catalog["variant_a"]
    assert job_row(db, job["id"])["status"] == "published"
    again = handlers.handle_videoid(OPERATOR_TG, [job["video_uid"], YT_ID])
    assert again.ok is False and "опубликован" in again.text


def test_videoid_vk_channel_takes_platform_from_channel(operator: dict, catalog: dict, db: Any) -> None:
    job = make_job(db, status="approved", channel_id="taro_vk")
    reply = handlers.handle_videoid(OPERATOR_TG, [job["video_uid"], YT_ID])
    assert reply.ok is False
    reply = handlers.handle_videoid(OPERATOR_TG, [job["video_uid"], VK_ID])
    assert reply.ok, reply.text
    pub = db.execute(
        "SELECT platform, platform_video_id, url FROM publications WHERE job_id = %s", (job["id"],)
    ).fetchone()
    assert pub == {"platform": "vk", "platform_video_id": VK_ID, "url": f"https://vk.com/clip{VK_ID}"}


def test_videoid_duplicate_and_wrong_status(operator: dict, catalog: dict, db: Any) -> None:
    first = make_job(db, status="approved")
    second = make_job(db, status="approved")
    pending = make_job(db, status="pending_review")
    assert handlers.handle_videoid(OPERATOR_TG, [first["video_uid"], YT_ID]).ok
    dup = handlers.handle_videoid(OPERATOR_TG, [second["video_uid"], YT_ID])
    assert dup.ok is False and "уже привязан" in dup.text
    assert job_row(db, second["id"])["status"] == "approved"
    reply = handlers.handle_videoid(OPERATOR_TG, [pending["video_uid"], "abcdefghijk"])
    assert reply.ok is False and "одобренному" in reply.text
    assert handlers.handle_videoid(OPERATOR_TG, ["nope", YT_ID]).text == "Ролик не найден"
    assert handlers.handle_videoid(OPERATOR_TG, [first["video_uid"]]).ok is False


def test_link_without_args_shows_own_id(operator: dict, catalog: dict, db: Any) -> None:
    reply = handlers.handle_link(555, [])
    assert "555" in reply.text and "Привязки нет" in reply.text
    reply = handlers.handle_link(OPERATOR_TG, [])
    assert str(OPERATOR_TG) in reply.text and "Ольга" in reply.text


def test_link_with_package_code_and_url(operator: dict, catalog: dict, db: Any) -> None:
    job = make_job(db, status="approved")
    db.execute(
        "INSERT INTO tracking_links (code, product_id, level, job_id) "
        "VALUES ('k7m2q', 'umka', 'package', %s)",
        (job["id"],),
    )
    assert handlers.handle_link(999, ["k7m2q", f"https://youtu.be/{YT_ID}"]).ok is False
    reply = handlers.handle_link(OPERATOR_TG, ["k7m2q", f"https://youtu.be/{YT_ID}"])
    assert reply.ok, reply.text
    assert job_row(db, job["id"])["status"] == "published"
