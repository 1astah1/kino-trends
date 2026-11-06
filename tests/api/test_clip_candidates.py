"""Кандидаты нарезки, повторный отбор, clip-job из кандидата и пачкой, авторский текст и голос (M5-B).

Без рендера: источник синтетический (testsrc2 + «речь» из тональных слогов, mock-транскрипт), задачи
очереди — настоящими воркерами. LLM не настроен (эвристика и шаблон с пометкой), TTS и ASR — mock.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from psycopg.types.json import Jsonb

from tests.api.test_clip_support import (
    LICENSED_RIGHTS,
    SynthMedia,
    add_candidate,
    job_row,
    make_synth_media,
    seed_source,
    wait_task,
    workers,
)
from tests.api.test_jobs_support import err, seed_catalog

pytestmark = pytest.mark.usefixtures("clip_env")


@pytest.fixture(scope="session")
def synth(tmp_path_factory: pytest.TempPathFactory) -> SynthMedia:
    return make_synth_media(tmp_path_factory.mktemp("clip_synth"))


@pytest.fixture
def clip_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLIP_LLM", "auto")
    monkeypatch.setenv("CLIP_TTS", "mock")
    monkeypatch.setenv("CLIP_VOICE_ASR", "mock")


@pytest.fixture
def catalog(db: Any) -> dict[str, Any]:
    return seed_catalog(db)


@pytest.fixture
def source(db: Any, app_env: Any, synth: SynthMedia, catalog: dict[str, Any]) -> dict[str, Any]:
    return seed_source(db, app_env.media_root, synth)


def _job_opts(**kw: Any) -> dict[str, Any]:
    return {"channel_id": "taro_main", "start": False, **kw}


# ---------------------------------------------------------------- список и ручной кандидат


def test_list_candidates_hides_superseded_and_labels_selection(login_as: Any, db: Any, source: dict) -> None:
    tr = source["transcript"]
    fresh = add_candidate(db, source["id"], tr, first=0, last=3)
    old = add_candidate(db, source["id"], tr, first=4, last=6, origin="llm")
    db.execute("UPDATE clip_candidates SET superseded_at = now() WHERE id = %s", (old["id"],))
    selection = {
        "mode": "heuristic",
        "label": "ЭВРИСТИКА без LLM [эвристика без LLM]",
        "llm_configured": False,
        "fallback": False,
        "params": {"selector": "auto", "target_clip_sec": 30},
        "n_candidates": 1,
        "n_blocked": 0,
        "n_requests": 0,
        "cost_usd": 0.0,
        "warnings": [],
        "task_id": None,
        "finished_at": None,
    }
    db.execute("UPDATE sources SET selection = %s WHERE id = %s", (Jsonb(selection), source["id"]))
    viewer = login_as("viewer")
    body = viewer.get(f"/sources/{source['id']}/candidates").json()
    assert [c["id"] for c in body["items"]] == [fresh["id"]]
    item = body["items"][0]
    assert item["origin"] == "heuristic" and item["origin_label"] == "Эвристика без LLM"
    assert item["llm_scores"]["hook"] == 7 and item["score_parts"]["llm"] == 0.6
    assert body["selection"]["mode"] == "heuristic" and body["selection"]["llm_configured"] is False
    both = viewer.get(f"/sources/{source['id']}/candidates", params={"include_superseded": "true"}).json()
    assert {c["id"]: c["superseded"] for c in both["items"]} == {fresh["id"]: False, old["id"]: True}
    assert viewer.get("/sources/999999/candidates").status_code == 404


def test_manual_candidate_by_word_clicks_snaps_into_silence(login_as: Any, db: Any, source: dict) -> None:
    tr = source["transcript"]
    op = login_as("operator")
    w0, w1 = tr.sentences[1].w0, tr.sentences[4].w1
    resp = op.post(
        f"/sources/{source['id']}/candidates",
        json={"start_word_idx": w0, "end_word_idx": w1, "title": "Хук решает", "notes": "моя правка"},
    )
    assert resp.status_code == 201, resp.text
    c = resp.json()
    assert (c["start_word_idx"], c["end_word_idx"]) == (w0, w1)
    assert c["snap"]["w0"] == w0 and c["snap"]["w1"] == w1
    assert tr.words[w0 - 1].e <= c["start_sec"] <= tr.words[w0].s
    assert tr.words[w1].e <= c["end_sec"] <= tr.words[w1 + 1].s
    assert (c["start_sid"], c["end_sid"]) == (1, 4)
    assert c["origin"] == "manual" and c["created_by_kind"] == "manual"
    assert c["origin_label"] == "Отмечено вручную" and c["notes"] == "моя правка"
    assert c["llm_scores"] is None and "speech_rate_wps" in c["features"] and c["final_score"] is not None
    # короче 3 с — 422
    bad = op.post(f"/sources/{source['id']}/candidates", json={"start_word_idx": 0, "end_word_idx": 1})
    assert bad.status_code == 422 and err(bad)["details"]["errors"][0]["type"] == "clip_length"
    # viewer не правит
    assert login_as("viewer").post(f"/sources/{source['id']}/candidates", json={}).status_code == 403


def test_patch_candidate_resnaps_edge_and_keeps_history(login_as: Any, db: Any, source: dict) -> None:
    tr = source["transcript"]
    cand = add_candidate(db, source["id"], tr, first=0, last=5)
    op = login_as("operator")
    new_end = tr.sentences[3].w1
    resp = op.patch(f"/clip-candidates/{cand['id']}", json={"end_word_idx": new_end, "status": "approved"})
    assert resp.status_code == 200, resp.text
    c = resp.json()
    assert c["end_word_idx"] == new_end and c["end_sid"] == 3 and c["status"] == "approved"
    assert c["start_sec"] == pytest.approx(float(cand["start_sec"]), abs=0.4)
    assert tr.words[new_end].e <= c["end_sec"] <= tr.words[new_end + 1].s
    row = db.execute(
        "SELECT operator_edits, updated_by FROM clip_candidates WHERE id = %s", (cand["id"],)
    ).fetchone()
    assert row["operator_edits"]["bounds"][-1]["to"][1] == c["end_sec"] and row["updated_by"] is not None
    audit = db.execute("SELECT action FROM audit_log WHERE entity_type = 'clip_candidate'").fetchall()
    assert [a["action"] for a in audit] == ["clip_candidate.update"]
    used = op.patch(f"/clip-candidates/{cand['id']}", json={"status": "used"})
    assert used.status_code == 422


def test_forbidden_phrase_blocks_candidate_until_unblocked(login_as: Any, db: Any, source: dict) -> None:
    db.execute(
        "UPDATE niches SET config = config || %s WHERE id = 'taro'",
        (Jsonb({"script": {"forbidden_phrases": ["три секунды"]}}),),
    )
    op = login_as("operator")
    tr = source["transcript"]
    resp = op.post(
        f"/sources/{source['id']}/candidates",
        json={"start_word_idx": tr.sentences[0].w0, "end_word_idx": tr.sentences[3].w1},
    )
    c = resp.json()
    assert c["blocked"] and c["block_reasons"] == ["forbidden_phrase"]
    assert c["forbidden_hits"] and c["forbidden_hits"][0]["phrase"] == "три секунды"
    blocked = op.post(f"/clip-candidates/{c['id']}/jobs", json=_job_opts())
    assert blocked.status_code == 409 and err(blocked)["code"] == "clip_candidate_blocked"
    assert err(blocked)["details"]["block_reasons"] == ["forbidden_phrase"]
    unblocked = op.patch(f"/clip-candidates/{c['id']}", json={"unblock_flags": ["forbidden_phrase"]}).json()
    assert not unblocked["blocked"] and unblocked["unblocked_flags"] == ["forbidden_phrase"]
    job = op.post(f"/clip-candidates/{c['id']}/jobs", json=_job_opts())
    assert job.status_code == 201, job.text
    j = job.json()
    assert j["kind"] == "clip" and j["status"] == "draft" and j["source"]["clip_candidate_id"] == c["id"]
    row = job_row(db, j["id"])
    assert (row["clip_start_sid"], row["clip_end_sid"]) == (0, 3)
    after = op.get(f"/clip-candidates/{c['id']}").json()
    assert after["status"] == "used" and after["job_id"] == j["id"]
    again = op.post(f"/clip-candidates/{c['id']}/jobs", json=_job_opts())
    assert again.status_code == 409 and err(again)["code"] == "clip_candidate_in_use"


def test_batch_clips_from_ranges_and_candidates(
    login_as: Any, db: Any, source: dict, app_env: Any, synth: SynthMedia
) -> None:
    tr = source["transcript"]
    cand = add_candidate(db, source["id"], tr, first=0, last=4)
    op = login_as("operator")
    items = [
        _job_opts(start_sec=tr.sentences[2].s - 0.2, end_sec=tr.sentences[6].e + 0.2),
        _job_opts(
            candidate_id=cand["id"], start_sec=float(cand["start_sec"]), end_sec=tr.sentences[3].e + 0.1
        ),
    ]
    resp = op.post(f"/sources/{source['id']}/clips", json={"items": items})
    assert resp.status_code == 200, resp.text
    jobs = resp.json()["items"]
    assert len(jobs) == 2 and all(j["kind"] == "clip" for j in jobs)
    rows = [job_row(db, j["id"]) for j in jobs]
    assert (rows[0]["clip_start_sid"], rows[0]["clip_end_sid"]) == (2, 6)
    assert (rows[1]["clip_start_sid"], rows[1]["clip_end_sid"]) == (0, 3)
    assert rows[1]["clip_candidate_id"] == cand["id"]
    other = seed_source(db, app_env.media_root, synth, title="Другой")
    wrong = op.post(
        f"/sources/{other['id']}/clips",
        json={"items": [_job_opts(candidate_id=cand["id"], start_sec=1.0, end_sec=12.0)]},
    )
    assert wrong.status_code == 422


# ---------------------------------------------------------------- повторный отбор


def test_select_again_supersedes_auto_and_keeps_manual(
    login_as: Any, db: Any, app_env: Any, source: dict, tmp_path: Path
) -> None:
    tr = source["transcript"]
    old_auto = add_candidate(db, source["id"], tr, first=0, last=4)
    op = login_as("operator")
    manual = op.post(
        f"/sources/{source['id']}/candidates",
        json={"start_word_idx": tr.sentences[1].w0, "end_word_idx": tr.sentences[5].w1},
    ).json()
    resp = op.post(
        f"/sources/{source['id']}/select",
        json={
            "selector": "auto",
            "target_clip_sec": 15,
            "min_clip_sec": 8,
            "max_clip_sec": 30,
            "save_as_default": True,
        },
    )
    assert resp.status_code == 202, resp.text
    task_id = resp.json()["task_id"]
    with workers(app_env, tmp_path):
        task = wait_task(db, task_id)
    assert task["status"] == "succeeded", task["last_error"]
    body = op.get(f"/sources/{source['id']}/candidates").json()
    ids = {c["id"] for c in body["items"]}
    assert old_auto["id"] not in ids and manual["id"] in ids
    auto = [c for c in body["items"] if c["created_by_kind"] == "auto"]
    assert auto and all(c["origin"] == "heuristic" and c["origin_label"] == "Эвристика без LLM" for c in auto)
    assert all(c["snap"] is not None and c["start_word_idx"] is not None for c in auto)
    sel = body["selection"]
    assert sel["mode"] == "heuristic" and not sel["llm_configured"] and sel["task_id"] == task_id
    assert sel["params"]["target_clip_sec"] == 15 and sel["n_candidates"] == len(auto)
    opts = db.execute("SELECT options FROM sources WHERE id = %s", (source["id"],)).fetchone()["options"]
    assert opts["target_clip_sec"] == 15
    # тот же источник не готов → 409
    db.execute("UPDATE sources SET status = 'transcribing' WHERE id = %s", (source["id"],))
    busy = op.post(f"/sources/{source['id']}/select", json={})
    assert busy.status_code == 409 and err(busy)["code"] == "source_not_ready"


def test_select_with_mock_llm_is_labelled(
    login_as: Any, db: Any, app_env: Any, source: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLIP_LLM", "mock")
    op = login_as("operator")
    resp = op.post(
        f"/sources/{source['id']}/select",
        json={"target_clip_sec": 15, "min_clip_sec": 8, "max_clip_sec": 30, "detail_pass": False},
    )
    with workers(app_env, tmp_path):
        task = wait_task(db, resp.json()["task_id"])
    assert task["status"] == "succeeded", task["last_error"]
    body = op.get(f"/sources/{source['id']}/candidates").json()
    assert body["selection"]["mode"] in ("mock", "heuristic")
    if body["selection"]["mode"] == "mock":
        assert "MOCK" in body["selection"]["label"]
        assert all(c["origin"] == "mock" for c in body["items"])


# ---------------------------------------------------------------- оценка оригинальности до рендера


def test_candidate_originality_estimate(
    login_as: Any, db: Any, app_env: Any, synth: SynthMedia, catalog: dict
) -> None:
    src = seed_source(db, app_env.media_root, synth, rights=LICENSED_RIGHTS)
    tr = src["transcript"]
    cand = add_candidate(db, src["id"], tr, first=0, last=6)
    op = login_as("operator")
    tpl = op.post(f"/clip-candidates/{cand['id']}/originality-estimate", json={}).json()
    assert tpl["stage"] == "estimate" and tpl["summary"]["author_mode"] == "heuristic"
    assert tpl["summary"]["gate_applies"] is True
    # чужие права: неотредактированный шаблон без LLM — стоп (OWNER-DECISIONS п. 5), а не совет
    assert {c["code"]: c["status"] for c in tpl["checks"]}["author_text"] == "fail"
    assert tpl["verdict"] == "fail"
    script = {
        "cold_open": {
            "vo_text": "Мы проверили это на сотне своих коротких видео — картина везде одинаковая."
        },
        "inserts": [
            {
                "after_sid": 2,
                "mode": "freeze_zoom",
                "vo_text": "Обратите внимание: половина аудитории уходит ещё до конца вступления.",
            }
        ],
        "outro": {"vo_text": "Напишите, какой приём держит внимание у вас лучше всего."},
        "on_screen_title": "Как удержать аудиторию",
    }
    own = op.post(f"/clip-candidates/{cand['id']}/originality-estimate", json={"script": script}).json()
    assert own["summary"]["author_mode"] == "operator"
    assert own["author_share"]["value"] >= 0.30 and own["summary"]["gate_passed"] is True
    at = {c["code"]: c for c in own["checks"]}["author_text"]
    assert at["status"] == "pass", at["detail"]
    assert own["verdict_label"]


# ---------------------------------------------------------------- авторский текст


def _draft_job(op: Any, db: Any, source: dict, **layer: Any) -> dict[str, Any]:
    tr = source["transcript"]
    cand = add_candidate(db, source["id"], tr, first=0, last=6)
    opts = _job_opts(author_layer=layer) if layer else _job_opts()
    resp = op.post(f"/clip-candidates/{cand['id']}/jobs", json=opts)
    assert resp.status_code == 201, resp.text
    return resp.json()


SCRIPT = {
    "cold_open": {"vo_text": "Одно правило решает, досмотрят ли ваш ролик до конца."},
    "inserts": [
        {"after_sid": 2, "mode": "freeze_zoom", "vo_text": "Заметьте: речь о ясности, а не о громкости."}
    ],
    "outro": {"vo_text": "А вы на какой секунде листаете?", "cta_style": "question"},
    "on_screen_title": "Ясность важнее громкости",
}


def test_author_script_put_versions_and_lint(login_as: Any, db: Any, source: dict) -> None:
    op = login_as("operator")
    job = _draft_job(op, db, source)
    empty = op.get(f"/jobs/{job['id']}/author-script").json()
    assert empty["script"] is None and empty["version"] == 0 and empty["editable"] is True
    assert empty["llm_configured"] is False
    put = op.put(f"/jobs/{job['id']}/author-script", json={"script": SCRIPT, "expected_version": 0})
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["mode"] == "operator" and body["label"] == "текст оператора" and body["version"] == 1
    assert not body["blocked"] and not body["needs_operator_edit"]
    assert body["script"]["inserts"][0]["after_sid"] == 2
    stale = op.put(f"/jobs/{job['id']}/author-script", json={"script": SCRIPT, "expected_version": 0})
    assert stale.status_code == 409 and err(stale)["code"] == "version_conflict"
    out_of_clip = {**SCRIPT, "inserts": [{"after_sid": 40, "vo_text": "Вне клипа."}]}
    bad = op.put(f"/jobs/{job['id']}/author-script", json={"script": out_of_clip})
    assert bad.status_code == 422 and err(bad)["details"]["errors"][0]["type"] == "insert_out_of_clip"
    link = {**SCRIPT, "outro": {"vo_text": "Подробности на example.com и у @someone в канале."}}
    blocked = op.put(f"/jobs/{job['id']}/author-script", json={"script": link}).json()
    assert blocked["blocked"] and any(i["code"] == "link_or_handle" for i in blocked["issues"])
    detail = op.get(f"/jobs/{job['id']}").json()
    assert detail["author_script"]["mode"] == "operator" and detail["author_script"]["blocked"] is True
    # приёмка: править нельзя, пока ролик не на доработке
    db.execute("UPDATE jobs SET status = 'pending_review', stage = 'qc' WHERE id = %s", (job["id"],))
    locked = op.put(f"/jobs/{job['id']}/author-script", json={"script": SCRIPT})
    assert locked.status_code == 409 and err(locked)["code"] == "job_state_conflict"
    assert err(locked)["details"]["hint"] == "rework"
    assert op.get(f"/jobs/{job['id']}/author-script").json()["editable"] is False


def test_operator_script_from_author_layer_is_visible_before_stage(
    login_as: Any, db: Any, source: dict
) -> None:
    op = login_as("operator")
    job = _draft_job(op, db, source, mode="operator_text", voice="operator_recording", script=SCRIPT)
    view = op.get(f"/jobs/{job['id']}/author-script").json()
    assert view["mode"] == "operator" and view["script"]["on_screen_title"] == SCRIPT["on_screen_title"]
    voice = op.get(f"/jobs/{job['id']}/author-voice").json()
    assert voice["voice"] == "operator_recording" and voice["synthetic"] is True
    assert [s["segment"] for s in voice["segments"]] == ["cold_open", "insert_0", "insert_1", "outro"]
    assert voice["segments"][0]["text"] == SCRIPT["cold_open"]["vo_text"]
    assert all(s["status"] == "missing" for s in voice["segments"])


def test_author_script_draft_task_without_llm_is_template(
    login_as: Any, db: Any, app_env: Any, source: dict, tmp_path: Path
) -> None:
    op = login_as("operator")
    job = _draft_job(op, db, source)
    resp = op.post(f"/jobs/{job['id']}/author-script/draft", json={"mode": "auto"})
    assert resp.status_code == 202, resp.text
    task_id = resp.json()["task_id"]
    assert op.get(f"/jobs/{job['id']}/author-script").json()["drafting_task_id"] == task_id
    with workers(app_env, tmp_path):
        task = wait_task(db, task_id)
    assert task["status"] == "succeeded", task["last_error"]
    view = op.get(f"/jobs/{job['id']}/author-script").json()
    assert view["mode"] == "heuristic" and view["needs_operator_edit"] is True
    assert view["label"] == "[черновик без LLM — перепишите своими словами]"
    assert view["issues"][0]["code"] == "template_text" and view["version"] == 1
    assert view["drafting_task_id"] is None
    assert job_row(db, job["id"])["status"] == "draft"


# ---------------------------------------------------------------- голос автора


def _wav_bytes(tmp_path: Path, text: str = "Одно правило решает, досмотрят ли ваш ролик.") -> bytes:
    from autoshorts.clipping.author.voice import synthesize_mock

    return synthesize_mock("rec", text, tmp_path, base_hz=180.0).path.read_bytes()


def test_author_voice_upload_prepare_listen_delete(
    login_as: Any, db: Any, app_env: Any, source: dict, tmp_path: Path
) -> None:
    op = login_as("operator")
    job = _draft_job(op, db, source, mode="operator_text", voice="operator_recording", script=SCRIPT)
    wav = _wav_bytes(tmp_path)
    resp = op.post(
        f"/jobs/{job['id']}/author-voice",
        files={"audio": ("take.wav", io.BytesIO(wav), "audio/wav")},
        data={"segment": "cold_open"},
    )
    assert resp.status_code == 202, resp.text
    acc = resp.json()
    assert acc["segment"] == "cold_open" and acc["mime"] == "audio/wav" and acc["bytes"] == len(wav)
    pending = op.get(f"/jobs/{job['id']}/author-voice").json()["segments"][0]
    assert (
        pending["status"] == "processing"
        and pending["kind"] == "operator"
        and pending["task_id"] == acc["task_id"]
    )
    with workers(app_env, tmp_path):
        task = wait_task(db, acc["task_id"])
    assert task["status"] == "succeeded", task["last_error"]
    seg = op.get(f"/jobs/{job['id']}/author-voice").json()
    cold = seg["segments"][0]
    assert cold["status"] == "ready" and cold["kind"] == "operator" and cold["timing"] == "asr"
    assert cold["duration_sec"] > 1.0 and cold["words_count"] >= 5 and cold["audio_url"]
    assert seg["synthetic"] is True  # вставка и вывод ещё озвучит TTS
    audio = op.get(f"/jobs/{job['id']}/author-voice/cold_open/audio")
    assert audio.status_code == 200 and audio.headers["content-type"].startswith("audio/wav")
    assert audio.content[:4] == b"RIFF"
    arts = db.execute(
        "SELECT idx, stage FROM artifacts WHERE job_id = %s AND kind = 'author_voice' AND deleted_at IS NULL",
        (job["id"],),
    ).fetchall()
    assert [(a["idx"], a["stage"]) for a in arts] == [(0, None)]
    # мусор вместо звука → 415
    junk = op.post(
        f"/jobs/{job['id']}/author-voice",
        files={"audio": ("x.webm", io.BytesIO(b"not audio at all"), "audio/webm")},
        data={"segment": "outro"},
    )
    assert junk.status_code == 415 and err(junk)["code"] == "unsupported_media_type"
    assert op.delete(f"/jobs/{job['id']}/author-voice/cold_open").status_code == 204
    after = op.get(f"/jobs/{job['id']}/author-voice").json()["segments"][0]
    assert after["status"] == "missing"
    assert op.get(f"/jobs/{job['id']}/author-voice/cold_open/audio").status_code == 404
    assert op.delete(f"/jobs/{job['id']}/author-voice/cold_open").status_code == 404


def test_author_endpoints_reject_generate_jobs(login_as: Any, db: Any, catalog: dict) -> None:
    from tests.api.test_jobs_support import make_job

    gen = make_job(db)
    op = login_as("operator")
    assert op.get(f"/jobs/{gen['id']}/author-script").status_code == 422
    assert op.get(f"/jobs/{gen['id']}/originality").status_code == 422
    assert op.get("/jobs/999999/author-voice").status_code == 404


def test_job_originality_estimate_before_render(login_as: Any, db: Any, source: dict) -> None:
    op = login_as("operator")
    job = _draft_job(op, db, source, mode="operator_text", script=SCRIPT)
    est = op.post(f"/jobs/{job['id']}/originality/estimate", json={})
    assert est.status_code == 200, est.text
    rep = est.json()
    assert rep["stage"] == "estimate" and rep["summary"]["author_mode"] == "operator"
    assert rep["summary"]["rights_type"] == "own" and rep["author_share"]["value"] > 0
    lib = {c["code"]: c for c in rep["checks"]}["library_similarity"]
    assert lib["status"] == "skip" and "после рендера" in lib["detail"]
    # свой черновик в запросе, без сохранения
    alt = op.post(
        f"/jobs/{job['id']}/originality/estimate",
        json={"script": {"cold_open": {"vo_text": "Коротко."}}, "voice": "operator_recording"},
    ).json()
    assert alt["author_share"]["value"] < rep["author_share"]["value"]
    assert op.get(f"/jobs/{job['id']}/originality").status_code == 404  # финального отчёта ещё нет


def test_pipeline_select_step_marks_source_ready_or_failed(
    db: Any, app_env: Any, synth: SynthMedia, catalog: dict, tmp_path: Path
) -> None:
    """Конвейер M5-A ставит `source_select` с `payload.chain`: успех → следующий шаг или ready;
    окончательный сбой (нет транскрипта) → источник failed с понятной ошибкой."""
    import psycopg

    from autoshorts.services.tasks import enqueue

    ok = seed_source(db, app_env.media_root, synth, status="selecting")
    broken = seed_source(db, app_env.media_root, synth, status="selecting", title="Без транскрипта")
    db.execute("DELETE FROM transcripts WHERE source_id = %s", (broken["id"],))
    (Path(broken["dir"]) / "transcript.json.gz").unlink()
    from psycopg.rows import dict_row

    with psycopg.connect(app_env.database_url, row_factory=dict_row) as conn:
        t_ok = enqueue(conn, "source_select", source_id=ok["id"], payload={"chain": []}).task["id"]
        t_bad = enqueue(conn, "source_select", source_id=broken["id"], payload={"chain": []}).task["id"]
    with workers(app_env, tmp_path):
        assert wait_task(db, t_ok)["status"] == "succeeded"
        assert wait_task(db, t_bad)["status"] == "failed"
    rows = {r["id"]: r for r in db.execute("SELECT id, status, error, selection FROM sources").fetchall()}
    assert rows[ok["id"]]["status"] == "ready" and rows[ok["id"]]["selection"]["mode"] == "heuristic"
    assert rows[broken["id"]]["status"] == "failed" and "транскрипт" in rows[broken["id"]]["error"]
    n = db.execute("SELECT count(*) AS n FROM clip_candidates WHERE source_id = %s", (ok["id"],)).fetchone()[
        "n"
    ]
    assert n == rows[ok["id"]]["selection"]["n_candidates"]
