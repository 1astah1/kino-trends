"""Бот приёмки (M2-C, критерий M2 `test_bot_auth`): кнопки жмут только привязанные операторы,
решения идут через `services.review` (как у веба), конфликт с вебом — понятный ответ, а не смена статуса."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from autoshorts.bot import handlers
from tests.api.test_jobs_support import job_row, make_job, seed_catalog

OPERATOR_TG = 111_000_111
ADMIN_TG = 222_000_222
VIEWER_TG = 333_000_333
INACTIVE_TG = 444_000_444
STRANGER_TG = 999_000_999


@pytest.fixture
def catalog(db: Any) -> dict[str, Any]:
    return seed_catalog(db)


@pytest.fixture
def db(db_conn: Any) -> Any:
    return db_conn


@pytest.fixture
def users(db_pool: Any, fast_password_hasher: None) -> dict[str, dict[str, Any]]:
    from autoshorts.services import settings
    from autoshorts.services.audit import SYSTEM
    from autoshorts.services.users import create_user, update_user

    settings.invalidate_cache()
    out = {}
    for login, role, tg in (
        ("olga", "operator", OPERATOR_TG),
        ("anton", "admin", ADMIN_TG),
        ("vera", "viewer", VIEWER_TG),
        ("ivan", "operator", INACTIVE_TG),
        ("webby", "operator", None),
    ):
        out[login] = create_user(
            login=login,
            display_name=login.title(),
            role=role,
            password=f"{login}-password-1",
            tg_user_id=tg,
            actor=SYSTEM,
        )
    update_user(out["ivan"]["id"], {"is_active": False}, SYSTEM)
    return out


def _decisions(db: Any, job_id: int) -> list[dict[str, Any]]:
    return db.execute(
        "SELECT decision, decided_via, decided_by FROM review_decisions WHERE job_id = %s ORDER BY id",
        (job_id,),
    ).fetchall()


@pytest.mark.parametrize("tg", [STRANGER_TG, None, VIEWER_TG, INACTIVE_TG])
def test_callback_from_unknown_or_unprivileged_tg_user_rejected(
    users: dict, catalog: dict, db: Any, tg: int | None
) -> None:
    job = make_job(db)
    for data in (f"ap:{job['id']}:p", f"rr:{job['id']}:p:dublikat", f"rj:{job['id']}:p", f"un:{job['id']}"):
        reply = handlers.handle_callback(tg, data)
        assert reply.ok is False and reply.alert is True and "не привязан" in reply.text, data
    assert job_row(db, job["id"])["status"] == "pending_review"
    assert _decisions(db, job["id"]) == []


def test_operator_approves_via_telegram(users: dict, catalog: dict, db: Any) -> None:
    job = make_job(db)
    reply = handlers.handle_callback(OPERATOR_TG, f"ap:{job['id']}:p")
    assert reply.ok and reply.text == "Одобрено" and reply.status_line == "Одобрено — Olga"
    assert [b.data for b in handlers.iter_keyboard(reply.keyboard or [])] == [f"un:{job['id']}"]
    assert job_row(db, job["id"])["status"] == "approved"
    assert _decisions(db, job["id"]) == [
        {"decision": "approve", "decided_via": "telegram", "decided_by": users["olga"]["id"]}
    ]
    audit = db.execute(
        "SELECT actor_kind, actor_user_id FROM audit_log WHERE action = 'job.review' AND entity_id = %s",
        (str(job["id"]),),
    ).fetchone()
    assert audit == {"actor_kind": "bot", "actor_user_id": users["olga"]["id"]}
    # отмена из бота
    reply = handlers.handle_callback(OPERATOR_TG, f"un:{job['id']}")
    assert reply.ok and job_row(db, job["id"])["status"] == "pending_review"
    assert [b.data for b in handlers.iter_keyboard(reply.keyboard or [])][0] == f"ap:{job['id']}:p"


def test_reject_menu_and_reject_with_reason(users: dict, catalog: dict, db: Any) -> None:
    job = make_job(db)
    menu = handlers.handle_callback(OPERATOR_TG, f"rj:{job['id']}:p")
    assert menu.ok and menu.status_line is None and len(menu.keyboard or []) > 5
    assert job_row(db, job["id"])["status"] == "pending_review"
    reply = handlers.handle_callback(OPERATOR_TG, f"rr:{job['id']}:p:hook_slabyy")
    assert reply.ok and reply.status_line == "Отклонено: Слабый хук — Olga"
    row = job_row(db, job["id"])
    assert row["status"] == "rejected" and row["reject_reason"] == "hook_slabyy"


def test_rework_from_bot_restarts(users: dict, catalog: dict, db: Any) -> None:
    job = make_job(db)
    reply = handlers.handle_callback(OPERATOR_TG, f"rw:{job['id']}:p")
    assert reply.ok
    row = job_row(db, job["id"])
    assert row["status"] == "processing" and row["stage"] == "script" and row["attempt_count"] == 1


def test_override_qc_needs_admin(users: dict, catalog: dict, db: Any) -> None:
    job = make_job(db, status="qc_failed", qc_failed_checks=["loudness"])
    reply = handlers.handle_callback(OPERATOR_TG, f"oq:{job['id']}")
    assert reply.ok is False and "admin" in reply.text
    assert job_row(db, job["id"])["status"] == "qc_failed"
    reply = handlers.handle_callback(ADMIN_TG, f"oq:{job['id']}")
    assert reply.ok and job_row(db, job["id"])["status"] == "approved"


def test_already_decided_on_web_gives_conflict_reply(users: dict, catalog: dict, db: Any) -> None:
    from autoshorts.db.pool import connection
    from autoshorts.domain.enums import ActorKind, DecidedVia, Role
    from autoshorts.services import review
    from autoshorts.services.audit import Actor
    from autoshorts.services.jobs import Caller

    job = make_job(db)
    web = Caller(
        Actor(ActorKind.USER, users["webby"]["id"]),
        users["webby"]["id"],
        Role.OPERATOR,
        "Webby",
        DecidedVia.WEB,
    )
    with connection() as conn:
        review.decide(conn, job["id"], review.DecisionInput("approve", "pending_review"), web)
    reply = handlers.handle_callback(OPERATOR_TG, f"rr:{job['id']}:p:drugoe")
    assert reply.ok is False and reply.alert and reply.text == "Уже решено: одобрено — Webby (веб)"
    assert reply.keyboard == []
    assert job_row(db, job["id"])["status"] == "approved"


def test_bot_and_web_race_exactly_one_wins(users: dict, catalog: dict, db: Any) -> None:
    from autoshorts.core.errors import JobStateConflict
    from autoshorts.db.pool import connection
    from autoshorts.domain.enums import ActorKind, DecidedVia, Role
    from autoshorts.services import review
    from autoshorts.services.audit import Actor
    from autoshorts.services.jobs import Caller

    web = Caller(
        Actor(ActorKind.USER, users["webby"]["id"]),
        users["webby"]["id"],
        Role.OPERATOR,
        "Webby",
        DecidedVia.WEB,
    )

    def race(job_id: int) -> dict[str, Any]:
        barrier = threading.Barrier(2)
        out: dict[str, Any] = {}

        def bot() -> None:
            barrier.wait()
            out["bot"] = handlers.handle_callback(OPERATOR_TG, f"rr:{job_id}:p:dublikat")

        def site() -> None:
            barrier.wait()
            try:
                with connection() as conn:
                    review.decide(conn, job_id, review.DecisionInput("approve", "pending_review"), web)
                out["web"] = "ok"
            except JobStateConflict as exc:
                out["web"] = exc

        threads = [threading.Thread(target=bot), threading.Thread(target=site)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        return out

    for _ in range(3):
        job = make_job(db)
        out = race(job["id"])
        bot_won = out["bot"].ok
        web_won = out["web"] == "ok"
        assert bot_won != web_won, out
        assert len(_decisions(db, job["id"])) == 1
        assert job_row(db, job["id"])["status"] == ("rejected" if bot_won else "approved")


def test_stale_button_and_missing_job(users: dict, catalog: dict, db: Any) -> None:
    assert handlers.handle_callback(OPERATOR_TG, "garbage").text == "Кнопка устарела"
    reply = handlers.handle_callback(OPERATOR_TG, "ap:424242:p")
    assert reply.ok is False and reply.text == "Ролик не найден"


def test_pending_cards_and_mark_sent(users: dict, catalog: dict, db: Any, app_env: Any) -> None:
    from pathlib import Path

    from tests.api.test_jobs_support import add_artifact

    job = make_job(db, review_sent_at=None, stage_payload={"script": {"voiceover_text": "Текст"}})
    make_job(db, status="approved", review_sent_at=None)
    add_artifact(db, app_env.media_root, job, "preview", content=b"mp4")
    cards = handlers.pending_cards()
    assert [c.job_id for c in cards] == [job["id"]]
    card = cards[0]
    assert (
        card.voiceover_text == "Текст"
        and card.preview_path
        and Path(card.preview_path).read_bytes() == b"mp4"
    )
    handlers.mark_card_sent(job["id"])
    assert handlers.pending_cards() == []
    assert job_row(db, job["id"])["review_sent_at"] is not None
