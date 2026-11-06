"""Гейт author_share на QC clip-job (критерий §10.2 M5, §6.3.8, OWNER-DECISIONS п. 5).

Один и тот же клип с авторским вкладом ≈ 0,2 (только хук голосом поверх начала клипа):
* лицензированный источник → `qc_failed`, провал `author_share`, отчёт об оригинальности «не заливать»;
* свой источник → проходит на приёмку, в `qc_warnings` пометка про долю (гейт не применяется).

Настоящие воркеры и ffmpeg (nice 10, 2 потока), синтетический источник, mock TTS.
Run: pytest -m media tests/api/test_clip_qc.py
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_clip_support import (
    LICENSED_RIGHTS,
    OWN_RIGHTS,
    SynthMedia,
    add_candidate,
    make_synth_media,
    seed_source,
    wait_status,
    workers,
)
from tests.api.test_jobs_support import seed_catalog

pytestmark = [pytest.mark.media]

if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
    pytest.skip("ffmpeg/ffprobe not installed", allow_module_level=True)

# ≈ 5 с голоса mock TTS поверх начала клипа длиной ≈ 25 с → author_share ≈ 0,2
LOW_SHARE_SCRIPT = {"cold_open": {"vo_text": "Мы сравнили сотню своих коротких видео, и вывод всех удивил."}}


@pytest.fixture(scope="module")
def synth(tmp_path_factory: pytest.TempPathFactory) -> SynthMedia:
    return make_synth_media(tmp_path_factory.mktemp("clip_qc_synth"))


@pytest.fixture(autouse=True)
def clip_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLIP_LLM", "none")
    monkeypatch.setenv("CLIP_TTS", "mock")


def _clip_job(op: Any, db: Any, source: dict[str, Any], channel_id: str) -> dict[str, Any]:
    tr = source["transcript"]
    cand = add_candidate(db, source["id"], tr, first=0, last=len(tr.sentences) - 1)
    resp = op.post(
        f"/clip-candidates/{cand['id']}/jobs",
        json={
            "channel_id": channel_id,
            "author_layer": {"mode": "operator_text", "voice": "tts", "script": LOW_SHARE_SCRIPT},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_licensed_low_share_fails_qc_own_passes_with_note(
    login_as: Any, db: Any, app_env: Any, synth: SynthMedia, tmp_path: Path
) -> None:
    seed_catalog(db)
    licensed = seed_source(db, app_env.media_root, synth, rights=LICENSED_RIGHTS, title="Чужой доклад")
    own = seed_source(db, app_env.media_root, synth, rights=OWN_RIGHTS, title="Свой доклад")
    op = login_as("operator")
    # разные каналы: одинаковая озвучка в одном канале — отдельный провал (повтор своих реплик)
    lic_job = _clip_job(op, db, licensed, "taro_main")
    own_job = _clip_job(op, db, own, "astro_yt")
    with workers(app_env, tmp_path):
        lic_row = wait_status(db, lic_job["id"], {"pending_review", "qc_failed", "failed"})
        own_row = wait_status(db, own_job["id"], {"pending_review", "qc_failed", "failed"})

    # лицензированный: доля ≈ 0,2 < 0,30 → qc_failed + author_share
    assert lic_row["status"] == "qc_failed", (lic_row["status"], lic_row["error"])
    assert "author_share" in lic_row["qc_failed_checks"]
    share = float(lic_row["author_share"])
    assert 0.12 <= share < 0.30, share
    lic = op.get(f"/jobs/{lic_job['id']}").json()
    assert lic["qc"]["passed"] is False and "author_share" in lic["qc"]["failed_checks"]
    rep = lic["originality"]
    assert rep["verdict"] == "fail" and "author_share" in rep["fails"]
    assert rep["summary"]["gate_applies"] is True and rep["summary"]["gate_passed"] is False
    assert rep["summary"]["rights_type"] == "licensed_written"
    assert any("вставку-комментарий" in s or "авторского вклада" in s for s in rep["suggestions"])
    ctx = op.get(f"/jobs/{lic_job['id']}/review-context").json()
    flag = {f["code"]: f for f in ctx["flags"]}["author_share_low"]
    assert flag["severity"] == "error"
    assert ctx["source"]["attribution_text"] == LICENSED_RIGHTS["attribution_text"]
    assert ctx["originality"]["verdict"] == "fail"

    # свой: та же доля — проходит на приёмку с пометкой
    assert own_row["status"] == "pending_review", (
        own_row["status"],
        own_row["error"],
        own_row["qc_failed_checks"],
    )
    assert own_row["qc_failed_checks"] == []
    assert float(own_row["author_share"]) == pytest.approx(share, abs=0.02)
    note = [w for w in own_row["qc_warnings"] if w["code"] == "author_share"]
    assert note and "гейт не применяется" in note[0]["detail"]
    mine = op.get(f"/jobs/{own_job['id']}").json()
    assert mine["qc"]["passed"] is True
    assert {w["code"] for w in mine["qc"]["warnings"]} >= {"author_share"}
    orep = mine["originality"]
    assert orep["summary"]["gate_applies"] is False and orep["verdict"] in ("warn", "pass")
    assert {c["code"]: c["status"] for c in orep["checks"]}["author_share"] == "info"
    own_ctx = op.get(f"/jobs/{own_job['id']}/review-context").json()
    assert (
        own_ctx["flags"]
        and {f["code"]: f["severity"] for f in own_ctx["flags"]}["author_share_low"] == "warn"
    )
    assert own_row["originality_verdict"] == orep["verdict"]
