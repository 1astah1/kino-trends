"""Раскладка ролика для редактора приёмки: `GET/PUT /jobs/{id}/layout` (SQL-фикстуры, без ffmpeg).

Сохранение в `author_layer.layout`, перерендер с приёмки решением «на доработку» со стадии render,
409 у одобренного и идущего рендера, 422 за пределами канвы, 403 для viewer."""

from __future__ import annotations

from typing import Any

import pytest
from psycopg.types.json import Jsonb

from tests.api.test_jobs_support import BANNER_PLAN, LAYERS, err, job_row, job_tasks, make_job, seed_catalog

# Блок баннера 1120–1366 поднят на 300 (820–1066): субтитры над ним, заголовок под микробрендом,
# тезисы под баннером — ничего не пересекается, расчёт ничего не двигает.
MANUAL = {
    "mode": "manual",
    "banner": {"dx": 0, "dy": -300, "scale": 1.0},
    "captions_bottom": 810,
    "title_top": 274,
    "onscreen_top": 1090,
}


@pytest.fixture
def catalog(db: Any) -> dict[str, Any]:
    return seed_catalog(db)


def _source(db: Any) -> int:
    row = db.execute(
        """INSERT INTO sources (title, origin, origin_url, status, language, niche_id, duration_sec, width,
                                height, fps, has_audio)
           VALUES ('Лекция', 'url', 'https://example.org/watch?v=layout', 'ready', 'ru', 'taro', 120, 1920,
                   1080, 30, true) RETURNING id"""
    ).fetchone()
    return int(row["id"])


def _clip_job(db: Any, catalog: dict[str, Any], **fields: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "kind": "clip",
        "source_id": _source(db),
        "clip_start_sec": 10.0,
        "clip_end_sec": 40.0,
        "status": "pending_review",
        "stage": "qc",
        "banner_variant_id": catalog["variant_a"],
        "campaign_id": catalog["campaign_id"],
        "config_snapshot": {"banner": {"layers": LAYERS}},
        "author_script": Jsonb(
            {
                "mode": "operator",
                "script": {
                    "cold_open": {"vo_text": "Мы проверили это сами.", "on_screen": "Тезис автора"},
                    "inserts": [],
                    "outro": {"vo_text": "А вы как думаете?", "cta_style": "question"},
                    "on_screen_title": "Мой заголовок",
                },
            }
        ),
        "stage_payload": {
            "banner_plan": BANNER_PLAN,
            "clip_render": {
                "warnings": ["captions_overlap_banner:title_zone"],
                "layout": {"manual": False, "zones": {}, "conflicts": []},
            },
        },
    }
    values.update(fields)
    return make_job(db, **values)


def _put(user: Any, job_id: int, layout: dict[str, Any], *, rerender: bool = False) -> Any:
    return user.put(f"/jobs/{job_id}/layout", json={"layout": layout, "rerender": rerender})


# ---------------------------------------------------------------- GET


def test_get_layout_auto_for_clip_job(login_as: Any, catalog: dict, db: Any) -> None:
    job = _clip_job(db, catalog)
    r = login_as("viewer").get(f"/jobs/{job['id']}/layout")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job_id"] == job["id"] and body["status"] == "pending_review"
    assert body["canvas"] == {"w": 1080, "h": 1920} and body["profile_id"] == "youtube_shorts"
    assert body["safe"] == {"x1": 65, "y1": 180, "x2": 896, "y2": 1382}
    assert body["override"]["mode"] == "auto" and body["override"]["banner"] is None
    assert body["auto"] == body["effective"]
    ids = {rect["id"]: rect for rect in body["effective"]["rects"]}
    assert {"captions", "title", "onscreen"} <= set(ids)
    assert ids["captions"]["kind"] == "captions" and ids["captions"]["movable"] is True
    assert ids["title"]["has_text"] is True and ids["onscreen"]["has_text"] is True
    assert any(rect["kind"] == "banner" and rect["movable"] for rect in ids.values())
    assert body["effective"]["block"] is not None and body["effective"]["conflicts"] == []
    assert body["texts"] == {"title": "Мой заголовок", "onscreen": ["Тезис автора"]}
    assert body["rendered"] == {"manual": False, "overlap_zones": ["title_zone"], "conflicts": []}
    assert body["rerender_allowed"] is True and body["rerender_hint"] is None
    assert body["rerender_started"] is False


def test_get_layout_without_variant_and_texts(login_as: Any, catalog: dict, db: Any) -> None:
    job = _clip_job(
        db, catalog, banner_variant_id=None, campaign_id=None, author_script=None, stage_payload={}
    )
    body = login_as("viewer").get(f"/jobs/{job['id']}/layout").json()
    assert body["effective"]["block"] is None
    assert [rect["kind"] for rect in body["effective"]["rects"]] == ["captions", "title", "onscreen"]
    ids = {rect["id"]: rect for rect in body["effective"]["rects"]}
    assert ids["title"]["has_text"] is False and ids["onscreen"]["has_text"] is False
    assert body["texts"] == {"title": None, "onscreen": []} and body["rendered"] is None


def test_get_layout_only_for_clips(login_as: Any, catalog: dict, db: Any) -> None:
    viewer = login_as("viewer")
    generate = make_job(db)
    r = viewer.get(f"/jobs/{generate['id']}/layout")
    assert r.status_code == 422 and err(r)["details"]["errors"][0]["type"] == "kind_mismatch"
    assert viewer.get("/jobs/424242/layout").status_code == 404


# ---------------------------------------------------------------- PUT


def test_put_layout_saves_override_without_rerender(login_as: Any, catalog: dict, db: Any) -> None:
    job = _clip_job(db, catalog, author_layer={"mode": "operator_text", "hook_text": "Хук"})
    op = login_as("operator")
    before = op.get(f"/jobs/{job['id']}/layout").json()
    r = _put(op, job["id"], MANUAL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rerender_started"] is False and body["status"] == "pending_review"
    assert body["override"] == MANUAL
    assert body["effective"]["captions"]["y2"] == 810
    assert body["effective"]["title"]["y1"] == 274 and body["effective"]["onscreen"]["y1"] == 1090
    assert body["effective"]["block"]["y1"] == before["auto"]["block"]["y1"] - 300
    assert body["effective"]["conflicts"] == []
    # расчёт ничего не двигал сам: только применённые ручные позиции (captions:/title:/onscreen:)
    assert [a for a in body["effective"]["adjustments"] if "_zone:" in a or a.startswith("banner")] == []
    assert body["auto"] == before["auto"]  # авто-расчёт не зависит от переопределения
    row = job_row(db, job["id"])
    assert row["status"] == "pending_review" and row["author_layer"]["layout"] == MANUAL
    assert row["author_layer"]["hook_text"] == "Хук"  # остальной авторский слой не тронут
    assert job_tasks(db, job["id"], kind="job_stage") == []
    again = op.get(f"/jobs/{job['id']}/layout").json()
    assert again["override"] == MANUAL and again["effective"] == body["effective"]
    audit = db.execute(
        "SELECT diff FROM audit_log WHERE action = 'job.layout' AND entity_id = %s", (str(job["id"]),)
    ).fetchone()
    assert audit["diff"]["layout"]["mode"] == "manual" and audit["diff"]["rerender"] is False


def test_put_layout_back_to_auto(login_as: Any, catalog: dict, db: Any) -> None:
    job = _clip_job(db, catalog, author_layer={"mode": "llm_draft", "layout": MANUAL})
    op = login_as("operator")
    body = _put(op, job["id"], {"mode": "auto"}).json()
    assert body["override"]["mode"] == "auto" and body["auto"] == body["effective"]
    assert job_row(db, job["id"])["author_layer"]["layout"]["mode"] == "auto"


def test_put_layout_rerender_from_review_restarts_render(login_as: Any, catalog: dict, db: Any) -> None:
    job = _clip_job(db, catalog)
    op = login_as("operator")
    r = _put(op, job["id"], MANUAL, rerender=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rerender_started"] is True and body["status"] == "processing"
    assert body["rerender_allowed"] is False and "в работе" in body["rerender_hint"]
    row = job_row(db, job["id"])
    assert (row["status"], row["stage"], row["rework_from_stage"], row["attempt_count"]) == (
        "processing",
        "render",
        "render",
        1,
    )
    assert row["author_layer"]["layout"] == MANUAL
    (task,) = job_tasks(db, job["id"], kind="job_stage")
    assert task["stage"] == "render" and task["status"] == "queued"
    decision = db.execute(
        "SELECT decision, rework_from_stage, comment FROM review_decisions WHERE job_id = %s", (job["id"],)
    ).fetchone()
    assert decision["decision"] == "rework" and decision["rework_from_stage"] == "render"
    # повторный PUT, пока идёт рендер — 409
    r = _put(op, job["id"], MANUAL)
    assert r.status_code == 409 and err(r)["code"] == "job_state_conflict"


@pytest.mark.parametrize("status", ["qc_failed"])
def test_put_layout_rerender_from_qc_failed(login_as: Any, catalog: dict, db: Any, status: str) -> None:
    job = _clip_job(db, catalog, status=status, qc_failed_checks=["loudness"])
    body = _put(login_as("operator"), job["id"], MANUAL, rerender=True).json()
    assert body["rerender_started"] is True and job_row(db, job["id"])["stage"] == "render"


def test_put_layout_rerender_from_rework_and_failed(login_as: Any, catalog: dict, db: Any) -> None:
    op = login_as("operator")
    rework = _clip_job(db, catalog, status="rework", stage="qc", rework_from_stage="author_script")
    body = _put(op, rework["id"], MANUAL, rerender=True).json()
    row = job_row(db, rework["id"])
    assert body["rerender_started"] is True and (row["status"], row["stage"]) == ("processing", "render")
    assert [t["stage"] for t in job_tasks(db, rework["id"], kind="job_stage")] == ["render"]
    failed = _clip_job(db, catalog, status="failed", stage="render", error="boom")
    body = _put(op, failed["id"], MANUAL, rerender=True).json()
    row = job_row(db, failed["id"])
    assert body["rerender_started"] is True and (row["status"], row["stage"]) == ("processing", "render")


def test_put_layout_on_draft_only_saves(login_as: Any, catalog: dict, db: Any) -> None:
    job = _clip_job(db, catalog, status="draft", stage="clip_fetch", stage_payload={})
    body = _put(login_as("operator"), job["id"], MANUAL, rerender=True).json()
    assert body["rerender_started"] is False and body["status"] == "draft"
    assert body["rerender_allowed"] is False and "ещё не рендерился" in body["rerender_hint"]
    assert job_row(db, job["id"])["author_layer"]["layout"] == MANUAL


def test_put_layout_conflicts_and_validation(login_as: Any, catalog: dict, db: Any) -> None:
    op = login_as("operator")
    approved = _clip_job(db, catalog, status="approved")
    r = _put(op, approved["id"], MANUAL, rerender=True)
    assert r.status_code == 409 and err(r)["code"] == "job_state_conflict"
    assert job_row(db, approved["id"])["author_layer"] is None
    rendering = _clip_job(db, catalog, status="processing", stage="render")
    assert _put(op, rendering["id"], MANUAL).status_code == 409
    early = _clip_job(db, catalog, status="processing", stage="author_script")
    assert _put(op, early["id"], MANUAL).status_code == 200  # до рендера сохранить можно
    job = _clip_job(db, catalog)
    assert _put(op, job["id"], {**MANUAL, "captions_bottom": 5000}).status_code == 422
    assert _put(op, job["id"], {**MANUAL, "banner": {"dx": 0, "dy": 0, "scale": 3.0}}).status_code == 422
    assert _put(op, job["id"], {**MANUAL, "bogus": 1}).status_code == 422
    assert _put(login_as("viewer"), job["id"], MANUAL).status_code == 403
    generate = make_job(db)
    assert _put(op, generate["id"], MANUAL).status_code == 422


def test_captions_toggle_from_review_rerenders_without_subtitles(
    login_as: Any, catalog: dict, db: Any
) -> None:
    """Галочка «Субтитры» в приёмке: выключить — author_layer.captions=none и перерендер со стадии render."""
    job = _clip_job(db, catalog)
    op = login_as("operator")
    r = op.get(f"/jobs/{job['id']}/captions")
    assert r.status_code == 200 and r.json()["enabled"] is True and r.json()["rerender_allowed"] is True
    r = op.put(f"/jobs/{job['id']}/captions", json={"enabled": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is False and body["mode"] == "none" and body["rerender_started"] is True
    row = job_row(db, job["id"])
    assert row["author_layer"]["captions"] == "none"
    assert (row["status"], row["stage"], row["rework_from_stage"]) == ("processing", "render", "render")
    (task,) = job_tasks(db, job["id"], kind="job_stage")
    assert task["stage"] == "render"
    # пока идёт рендер — 409
    r = op.put(f"/jobs/{job['id']}/captions", json={"enabled": True})
    assert r.status_code == 409 and err(r)["code"] == "job_state_conflict"


def test_captions_toggle_locked_for_approved_and_viewer(login_as: Any, catalog: dict, db: Any) -> None:
    job = _clip_job(db, catalog, status="approved")
    r = login_as("operator").put(f"/jobs/{job['id']}/captions", json={"enabled": False})
    assert r.status_code == 409 and err(r)["code"] == "job_state_conflict"
    other = _clip_job(db, catalog)
    assert login_as("viewer").put(f"/jobs/{other['id']}/captions", json={"enabled": False}).status_code == 403


def test_channel_change_in_review_moves_banner_and_rerenders(login_as: Any, catalog: dict, db: Any) -> None:
    """Канал в приёмке: ролик уходит на другой канал, баннер канала по умолчанию — на баннер нового канала,
    перерендер со стадии render; вручную выбранный особый баннер остаётся."""
    db.execute(
        """INSERT INTO channels (id, niche_id, platform, cluster, title, status, default_product_id, profile)
           VALUES ('taro_two', 'taro', 'youtube', 'main', 'Второй', 'active', 'umka', %s)""",
        (Jsonb({"banner_variant_id": catalog["variant_b"]}),),
    )
    db.execute("UPDATE channels SET profile = profile || %s WHERE id = 'taro_main'", (Jsonb({"banner_variant_id": catalog["variant_a"]}),))
    job = _clip_job(db, catalog)
    op = login_as("operator")
    r = op.get(f"/jobs/{job['id']}/channel")
    assert r.status_code == 200 and r.json()["change_allowed"] is True and r.json()["channel_id"] == "taro_main"
    r = op.put(f"/jobs/{job['id']}/channel", json={"channel_id": "taro_two"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["channel_id"] == "taro_two" and body["banner_variant_id"] == catalog["variant_b"] and body["rerender_started"] is True
    row = job_row(db, job["id"])
    assert row["channel_id"] == "taro_two" and row["config_snapshot"]["channel"]["id"] == "taro_two"
    assert (row["status"], row["stage"]) == ("processing", "render")
    # пока рендерится — 409; одобренный — 409; несуществующий канал — 404; зритель — 403
    assert op.put(f"/jobs/{job['id']}/channel", json={"channel_id": "taro_main"}).status_code == 409
    approved = _clip_job(db, catalog, status="approved")
    assert op.put(f"/jobs/{approved['id']}/channel", json={"channel_id": "taro_two"}).status_code == 409
    fresh = _clip_job(db, catalog)
    assert op.put(f"/jobs/{fresh['id']}/channel", json={"channel_id": "no_such"}).status_code == 404
    assert login_as("viewer").put(f"/jobs/{fresh['id']}/channel", json={"channel_id": "taro_two"}).status_code == 403


def test_channel_change_keeps_special_banner(login_as: Any, catalog: dict, db: Any) -> None:
    db.execute(
        """INSERT INTO channels (id, niche_id, platform, cluster, title, status, default_product_id, profile)
           VALUES ('taro_three', 'taro', 'youtube', 'main', 'Третий', 'active', 'umka', %s)""",
        (Jsonb({"banner_variant_id": catalog["variant_b"]}),),
    )
    job = _clip_job(db, catalog)  # вариант A — особый: ни у одного канала он не по умолчанию
    r = login_as("operator").put(f"/jobs/{job['id']}/channel", json={"channel_id": "taro_three", "rerender": False})
    assert r.status_code == 200, r.text
    assert r.json()["banner_variant_id"] == catalog["variant_a"] and r.json()["rerender_started"] is False
    assert job_row(db, job["id"])["status"] == "pending_review"


def test_autofix_rewrites_template_author_text(catalog: dict, db: Any) -> None:
    """QC провален только оригинальностью из-за шаблонного текста — стадия author_script заново, шаблон стёрт;
    чужие провалы и текст от модели/оператора — не трогаем; не больше двух раз."""
    from autoshorts.db.pool import connection
    from autoshorts.services import clipping

    tmpl = Jsonb({"mode": "heuristic", "script": {"on_screen_title": "Остановлюсь здесь"}})
    job = _clip_job(db, catalog, status="qc_failed", qc_failed_checks=["originality"], author_script=tmpl,
                    originality=Jsonb({"fails": ["author_text"], "verdict": "fail"}))
    other = _clip_job(db, catalog, status="qc_failed", qc_failed_checks=["originality", "duration"], author_script=tmpl,
                      originality=Jsonb({"fails": ["author_text"]}))
    llm_text = _clip_job(db, catalog, status="qc_failed", qc_failed_checks=["originality"],
                         author_script=Jsonb({"mode": "llm", "script": {}}), originality=Jsonb({"fails": ["author_text"]}))
    from autoshorts.repos import jobs as jobs_repo

    with connection() as conn:
        # как в проде: строка из jobs_repo.get — без колонок originality и author_script
        fixed = clipping.autofix_qc(conn, jobs_repo.get(conn, job["id"]))
        assert fixed is not None and (fixed["status"], fixed["stage"]) == ("processing", "author_script")
        assert clipping.autofix_qc(conn, jobs_repo.get(conn, other["id"])) is None
        assert clipping.autofix_qc(conn, jobs_repo.get(conn, llm_text["id"])) is None
    row = job_row(db, job["id"])
    assert row["author_script"] is None and row["stage_payload"]["autofix_author"]["count"] == 1
