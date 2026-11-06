"""Clip-job целиком (M5-B, §6.3.6, критерии §10.2 M5): кандидат → стадии в очереди → pending_review с
отчётом об оригинальности; приёмка видит источник, авторский слой и отчёт; доработка авторского
текста → повторный рендер. Плюс `clip_fetch` для источника по ссылке (поддельный yt-dlp).

Настоящие воркеры light и heavy, ffmpeg (nice 10, 2 потока), синтетический источник, mock TTS.
Run: pytest -m media tests/api/test_clip_jobs.py
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_clip_support import (
    SynthMedia,
    add_candidate,
    ffmpeg,
    job_row,
    make_synth_media,
    seed_source,
    wait_status,
    wait_task,
    workers,
)
from tests.api.test_jobs_support import seed_catalog

pytestmark = [pytest.mark.media, pytest.mark.usefixtures("clip_env")]

if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
    pytest.skip("ffmpeg/ffprobe not installed", allow_module_level=True)

OPERATOR_SCRIPT = {
    "cold_open": {"vo_text": "Мы проверили это на сотне своих коротких видео, и картина одинаковая."},
    "inserts": [
        {
            "after_sid": 2,
            "mode": "freeze_zoom",
            "vo_text": "Обратите внимание: половина аудитории уходит ещё до конца вступления.",
            "on_screen": "Половина уходит",
        }
    ],
    "outro": {"vo_text": "Напишите, какой приём держит внимание у вас лучше всего.", "cta_style": "question"},
    "on_screen_title": "Как удержать аудиторию",
}


@pytest.fixture(scope="module")
def synth(tmp_path_factory: pytest.TempPathFactory) -> SynthMedia:
    return make_synth_media(tmp_path_factory.mktemp("clip_jobs_synth"))


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


def _artifact_kinds(db: Any, job_id: int) -> dict[str, int]:
    rows = db.execute(
        "SELECT kind, count(*) AS n FROM artifacts WHERE job_id = %s AND deleted_at IS NULL GROUP BY kind",
        (job_id,),
    ).fetchall()
    return {r["kind"]: int(r["n"]) for r in rows}


def _creative_variant(op: Any, campaign_id: int) -> dict[str, Any]:
    """Загруженный креатив (PNG с прозрачностью) и вариант со слоями по умолчанию (код ролика)."""
    import io

    from PIL import Image, ImageDraw

    im = Image.new("RGBA", (800, 200), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, 799, 199), radius=40, fill=(90, 40, 200, 235))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    up = op.post("/banner-creatives", files={"file": ("offer.png", buf.getvalue(), "image/png")})
    assert up.status_code == 201, up.text
    sha = up.json()["creative"]["sha256"]
    variant = op.post(
        f"/campaigns/{campaign_id}/variants", json={"code": "C", "name": "Креатив", "creative_sha256": sha}
    )
    assert variant.status_code == 201, variant.text
    return {**variant.json(), "sha": sha}


def test_candidate_to_pending_review_with_originality_then_rework(
    login_as: Any, db: Any, app_env: Any, synth: SynthMedia, catalog: dict, tmp_path: Path
) -> None:
    src = seed_source(db, app_env.media_root, synth)  # свой источник
    tr = src["transcript"]
    cand = add_candidate(db, src["id"], tr, first=0, last=len(tr.sentences) - 1)
    op = login_as("operator")
    variant = _creative_variant(op, catalog["campaign_id"])
    created = op.post(
        f"/clip-candidates/{cand['id']}/jobs",
        json={"channel_id": "taro_main", "banner_variant_id": variant["id"]},
    )
    assert created.status_code == 201, created.text
    job = created.json()
    assert job["status"] == "queued" and job["stage"] == "clip_analyze"  # загрузка: без clip_fetch
    with workers(app_env, tmp_path):
        row = wait_status(db, job["id"], {"pending_review", "qc_failed", "failed"})
        assert row["status"] == "pending_review", (row["status"], row["error"], row["qc_failed_checks"])

        stages = [
            (t["stage"], t["status"], t["resource_class"])
            for t in db.execute("SELECT * FROM tasks WHERE job_id = %s ORDER BY id", (job["id"],)).fetchall()
        ]
        assert stages == [
            ("clip_analyze", "succeeded", "heavy"),
            ("author_script", "succeeded", "light"),
            ("author_voice", "succeeded", "light"),
            ("render", "succeeded", "heavy"),
            ("qc", "succeeded", "heavy"),
        ]
        kinds = _artifact_kinds(db, job["id"])
        for k in (
            "final",
            "preview",
            "poster",
            "edl_json",
            "crop_cmd",
            "script_json",
            "qc_report",
            "words_json",
        ):
            assert kinds.get(k) == 1, (k, kinds)
        assert kinds["author_voice"] >= 2  # TTS-реплики шаблона: хук и вывод как минимум

        detail = op.get(f"/jobs/{job['id']}").json()
        assert (
            detail["media"]["final_url"] and detail["media"]["preview_url"] and detail["media"]["poster_url"]
        )
        assert 20.0 <= detail["duration_sec"] <= 58.0
        assert detail["qc"]["passed"] is True
        assert (
            detail["author_script"]["mode"] == "heuristic" and detail["author_script"]["needs_operator_edit"]
        )
        rep = detail["originality"]
        assert rep["stage"] == "final" and rep["verdict"] in ("warn", "fail")
        assert rep["summary"]["rights_type"] == "own" and rep["summary"]["gate_applies"] is False
        assert {c["code"]: c["status"] for c in rep["checks"]}["author_text"] == "warn"  # шаблон
        assert detail["author_share"] == pytest.approx(rep["summary"]["author_share"], abs=0.001)
        assert row["originality_verdict"] == rep["verdict"] and row["fingerprint"]
        assert (row["clip_start_sid"], row["clip_end_sid"]) == (0, len(tr.sentences) - 1)
        payload = row["stage_payload"]
        assert payload["voice"]["provider"] == "mock_tts" and payload["clip_reframe"]["modes"]
        assert payload["script"]["voiceover_text"]
        # загруженный креатив варианта отрендерен в кадр, код ролика — из tracking_links
        plan = payload["banner_plan"]
        assert plan["items"] and "creative" in plan["rects"] and payload["clip_render"]["banner"] is True
        assert row["banner_variant_id"] == variant["id"] and len(payload["tracking_code"]) == 5
        assert ctx_banner(op, job["id"])["variant_id"] == variant["id"]

        ctx = op.get(f"/jobs/{job['id']}/review-context").json()
        assert ctx["source"]["rights_type"] == "own" and ctx["source"]["source_id"] == src["id"]
        assert ctx["author_script"]["mode"] == "heuristic" and ctx["author_script"]["voice_kinds"] == ["mock"]
        assert ctx["originality"]["verdict"] == rep["verdict"]
        assert "tts_mock" in {f["code"] for f in ctx["flags"]}
        queue = op.get("/review-queue").json()["items"]
        assert [(i["job_id"], i["originality_verdict"]) for i in queue] == [(job["id"], rep["verdict"])]
        listed = op.get("/jobs", params={"kind": "clip"}).json()["items"]
        assert listed[0]["originality_verdict"] == rep["verdict"]
        assert op.get(f"/jobs/{job['id']}/originality").json()["computed_at"]
        final_before = db.execute(
            "SELECT path FROM artifacts WHERE job_id = %s AND kind = 'final' AND deleted_at IS NULL",
            (job["id"],),
        ).fetchone()["path"]

        # доработка: оператор пишет свой текст и перезапускает с авторского слоя
        rework = op.post(
            f"/jobs/{job['id']}/review",
            json={
                "decision": "rework",
                "rework_from_stage": "author_script",
                "auto_restart": False,
                "expected_status": "pending_review",
            },
        )
        assert rework.status_code == 200, rework.text
        assert rework.json()["status"] == "rework"
        view = op.get(f"/jobs/{job['id']}/author-script").json()
        assert view["editable"] is True
        put = op.put(
            f"/jobs/{job['id']}/author-script",
            json={"script": OPERATOR_SCRIPT, "expected_version": view["version"]},
        )
        assert put.status_code == 200, put.text
        # хук оператор записал своим голосом
        from autoshorts.clipping.author.voice import synthesize_mock

        wav = synthesize_mock("rec", OPERATOR_SCRIPT["cold_open"]["vo_text"], tmp_path, base_hz=190.0)
        up = op.post(
            f"/jobs/{job['id']}/author-voice",
            files={"audio": ("hook.wav", wav.path.read_bytes(), "audio/wav")},
            data={"segment": "cold_open"},
        )
        assert up.status_code == 202, up.text
        assert wait_task(db, up.json()["task_id"])["status"] == "succeeded"
        assert op.post(f"/jobs/{job['id']}/start").json()["status"] == "processing"
        row = wait_status(db, job["id"], {"pending_review", "qc_failed", "failed"})
        assert row["status"] == "pending_review", (row["status"], row["error"], row["qc_failed_checks"])
    detail = op.get(f"/jobs/{job['id']}").json()
    assert (
        detail["author_script"]["mode"] == "operator" and not detail["author_script"]["needs_operator_edit"]
    )
    checks = {c["code"]: c for c in detail["originality"]["checks"]}
    assert checks["author_text"]["status"] == "pass" and checks["own_text"]["status"] == "pass"
    assert "живой голос оператора" in checks["own_voice"]["detail"]
    ctx = op.get(f"/jobs/{job['id']}/review-context").json()
    assert ctx["author_script"]["voice_kinds"] == ["mock", "operator"]
    kinds = job_row(db, job["id"])["stage_payload"]["clip_voice"]["kinds"]
    assert kinds == ["mock", "operator"]
    assert len(detail["reviews"]) == 1 and detail["reviews"][0]["decision"] == "rework"
    final_after = db.execute(
        "SELECT path FROM artifacts WHERE job_id = %s AND kind = 'final' AND deleted_at IS NULL", (job["id"],)
    ).fetchone()["path"]
    assert final_after != final_before  # новый каталог render_<task>: undo может вернуть прежний файл
    payload = job_row(db, job["id"])["stage_payload"]
    assert payload["clip_render"]["author_share_estimate"] > 0.2
    assert payload["tracking_code"] and len(payload["tracking_code"]) == 5  # код ролика для баннера
    link = db.execute("SELECT code, level FROM tracking_links WHERE job_id = %s", (job["id"],)).fetchall()
    assert [(r["code"], r["level"]) for r in link] == [(payload["tracking_code"], "package")]
    assert isinstance(payload["banner_plan"], dict) and payload["banner_plan"]["windows"]

    # одобрено → версия под VK: дочерний ролик сразу со стадии render на тех же голосе и рефрейме
    approve = op.post(
        f"/jobs/{job['id']}/review", json={"decision": "approve", "expected_status": "pending_review"}
    )
    assert approve.status_code == 200, approve.text
    child = op.post(f"/jobs/{job['id']}/adapt", json={"channel_id": "taro_vk"})
    assert child.status_code == 201, child.text
    child_id = child.json()["id"]
    assert child.json()["stage"] == "render"
    with workers(app_env, tmp_path):
        crow = wait_status(db, child_id, {"pending_review", "qc_failed", "failed"})
    assert crow["status"] == "pending_review", (crow["status"], crow["error"], crow["qc_failed_checks"])
    cdetail = op.get(f"/jobs/{child_id}").json()
    assert cdetail["channel"]["platform"] == "vk" and cdetail["parent_job_id"] == job["id"]
    assert cdetail["author_script"]["mode"] == "operator"
    # родня (адаптация того же момента) не считается повтором момента и своих реплик
    cchecks = {c["code"]: c["status"] for c in cdetail["originality"]["checks"]}
    assert cchecks["repeat_moment"] in ("pass", "skip") and cchecks["author_text_repetition"] in (
        "pass",
        "skip",
    )
    ctasks = [
        t["stage"] for t in db.execute("SELECT stage FROM tasks WHERE job_id = %s ORDER BY id", (child_id,))
    ]
    assert ctasks == ["render", "qc"]
    ccode = job_row(db, child_id)["stage_payload"]["tracking_code"]
    assert ccode and ccode != payload["tracking_code"]


def ctx_banner(op: Any, job_id: int) -> dict[str, Any]:
    return op.get(f"/jobs/{job_id}/review-context").json()["banner"]


def test_blocked_author_text_stops_before_render(
    login_as: Any, db: Any, app_env: Any, synth: SynthMedia, catalog: dict, tmp_path: Path
) -> None:
    src = seed_source(db, app_env.media_root, synth)
    tr = src["transcript"]
    cand = add_candidate(db, src["id"], tr, first=0, last=len(tr.sentences) - 1)
    op = login_as("operator")
    bad = {**OPERATOR_SCRIPT, "outro": {"vo_text": "Гарантирую результат за неделю, пишите нам."}}
    job = op.post(
        f"/clip-candidates/{cand['id']}/jobs",
        json={"channel_id": "taro_main", "author_layer": {"mode": "operator_text", "script": bad}},
    ).json()
    with workers(app_env, tmp_path):
        row = wait_status(db, job["id"], {"pending_review", "qc_failed", "failed"})
    assert row["status"] == "failed" and row["stage"] == "author_script"
    assert row["error_class"] == "author_script_blocked" and "Авторский текст заблокирован" in row["error"]
    view = op.get(f"/jobs/{job['id']}/author-script").json()
    assert view["blocked"] and view["editable"] and any(i["code"] == "content_flag" for i in view["issues"])
    kinds = {r["kind"] for r in db.execute("SELECT kind FROM artifacts WHERE job_id = %s", (job["id"],))}
    assert "final" not in kinds  # рендер не тратился


class FakeYdl:
    """yt-dlp: «качает» секцию — вырезает её ffmpeg из локального видео (без звука, как bv*)."""

    calls: list[dict[str, Any]] = []
    lock = threading.Lock()

    def __init__(self, opts: dict[str, Any], video: Path) -> None:
        self.opts, self.video = opts, video

    def __enter__(self) -> FakeYdl:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
        ranges = self.opts["download_ranges"]
        first = next(iter(ranges({}, self) if callable(ranges) else ranges))
        a, b = (first["start_time"], first["end_time"]) if isinstance(first, dict) else first
        out = Path(self.opts["outtmpl"].replace("%(ext)s", "mp4"))
        with self.lock:
            self.calls.append(
                {
                    "url": url,
                    "range": (a, b),
                    "format": self.opts["format"],
                    "keyframes": self.opts.get("force_keyframes_at_cuts"),
                }
            )
        ffmpeg("-ss", f"{a:.3f}", "-i", str(self.video), "-t", f"{b - a:.3f}", "-an", "-vf", "scale=1280:720",
               "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out))  # fmt: skip
        return {"id": "synthetic", "ext": "mp4", "_filename": str(out)}

    def prepare_filename(self, info: dict[str, Any]) -> str:
        return info["_filename"]


def test_clip_fetch_downloads_only_the_section_for_url_sources(
    login_as: Any, db: Any, app_env: Any, synth: SynthMedia, catalog: dict, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    import logging

    from autoshorts.domain.settings_schema import RuntimeSettings
    from autoshorts.services import clipping as svc
    from autoshorts.stages.base import StageContext
    from autoshorts.stages.clip_fetch import clip_fetch

    monkeypatch.setattr(svc, "YDL_FACTORY", lambda opts: FakeYdl(opts, synth.video))
    FakeYdl.calls = []
    src = seed_source(db, app_env.media_root, synth, origin="url", with_original=False)
    tr = src["transcript"]
    cand = add_candidate(db, src["id"], tr, first=1, last=4)
    op = login_as("operator")
    job = op.post(f"/clip-candidates/{cand['id']}/jobs", json={"channel_id": "taro_main"}).json()
    assert job["stage"] == "clip_fetch"
    row = job_row(db, job["id"])
    from autoshorts.core.storage import get_layout

    workdir = get_layout().job_dir(row["video_uid"], create=True)
    ctx = StageContext(
        job=dict(row),
        task={"id": 9001, "stage": "clip_fetch"},
        workdir=workdir,
        settings=RuntimeSettings(),
        providers=None,
        budget=None,
        cancel=threading.Event(),
        progress=lambda f: None,
        log=logging.getLogger("test"),
    )
    res = clip_fetch(ctx)
    section = res.payload_patch["clip_section"]
    start, end = float(row["clip_start_sec"]), float(row["clip_end_sec"])
    # короткий момент дотягивается до 21 с (fit_clip_bounds) и может упереться в конец источника
    dur = db.execute("SELECT duration_sec FROM sources WHERE id = %s", (src["id"],)).fetchone()["duration_sec"]
    stop = min(end + 3.0, float(dur)) if dur else end + 3.0
    assert FakeYdl.calls[0]["range"] == pytest.approx((start - 3.0, stop), abs=0.01)
    assert "height<=1080" in FakeYdl.calls[0]["format"]
    # копирование потока, без перекодирования в light-воркере; mp4 первым (edit list → точное начало)
    assert not FakeYdl.calls[0]["keyframes"] and FakeYdl.calls[0]["format"].startswith("bv*[height<=1080]")
    assert "[ext=mp4]" in FakeYdl.calls[0]["format"].split("/")[0]
    assert section["start"] == pytest.approx(start - 3.0, abs=0.01)
    assert (section["width"], section["height"]) == (1280, 720)
    assert section["duration"] == pytest.approx(end - start + 6.0, abs=0.2)
    assert section["path"].startswith(f"jobs/{row['video_uid']}/fetch_9001/")
    # рендеру: секция 1080p на своей шкале, звук — audio.m4a источника
    with __import__("autoshorts.db.pool", fromlist=["connection"]).connection() as conn:
        sctx = svc.source_context(conn, svc.require_source(conn, src["id"]))
    cs = svc.clip_source(sctx, section)
    assert (
        cs.src_offset == section["start"] and cs.audio_path is not None and cs.audio_path.name == "audio.m4a"
    )
    assert cs.media_range == (section["start"], section["start"] + section["duration"])
    # у источника с полным оригиналом качать нечего
    own = seed_source(db, app_env.media_root, synth, origin="url", with_original=True)
    cand2 = add_candidate(db, own["id"], tr, first=0, last=3)
    job2 = op.post(f"/clip-candidates/{cand2['id']}/jobs", json={"channel_id": "taro_main"}).json()
    ctx.job = dict(job_row(db, job2["id"]))
    assert clip_fetch(ctx).payload_patch == {"clip_section": None}
    assert len(FakeYdl.calls) == 1


def _fetch_ctx(db: Any, job_id: int, task_id: int) -> Any:
    import logging

    from autoshorts.core.storage import get_layout
    from autoshorts.domain.settings_schema import RuntimeSettings
    from autoshorts.stages.base import StageContext

    row = job_row(db, job_id)
    return StageContext(
        job=dict(row),
        task={"id": task_id, "stage": "clip_fetch"},
        workdir=get_layout().job_dir(row["video_uid"], create=True),
        settings=RuntimeSettings(),
        providers=None,
        budget=None,
        cancel=threading.Event(),
        progress=lambda f: None,
        log=logging.getLogger("test"),
    )


def _add_local_original_video(src: dict[str, Any], synth: SynthMedia, extractor: str) -> None:
    """Как у download_url_source: original_video.mp4 в каталоге источника и platform.extractor_key."""
    import json

    folder = Path(src["dir"])
    shutil.copyfile(synth.video, folder / "original_video.mp4")
    manifest = folder / "source.json"
    data = json.loads(manifest.read_text())
    data["files"]["original_video"] = "original_video.mp4"
    data["platform"] = {"id": "x", "extractor_key": extractor}
    manifest.write_text(json.dumps(data, ensure_ascii=False))


def test_direct_file_source_renders_from_local_original_video(
    login_as: Any, db: Any, app_env: Any, synth: SynthMedia, catalog: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Прямая ссылка на файл (generic): original_video — весь файл, секцию по сети не качаем.
    Иначе удалённый на хранилище файл ронял нарезку 404, хотя копия лежала рядом."""
    from autoshorts.db.pool import connection
    from autoshorts.services import clipping as svc
    from autoshorts.stages.clip_fetch import clip_fetch

    monkeypatch.setattr(svc, "YDL_FACTORY", lambda opts: FakeYdl(opts, synth.video))
    FakeYdl.calls = []
    src = seed_source(db, app_env.media_root, synth, origin="url", with_original=False)
    _add_local_original_video(src, synth, "Generic")
    cand = add_candidate(db, src["id"], src["transcript"], first=1, last=4)
    op = login_as("operator")
    job = op.post(f"/clip-candidates/{cand['id']}/jobs", json={"channel_id": "taro_main"}).json()
    assert clip_fetch(_fetch_ctx(db, job["id"], 9101)).payload_patch == {"clip_section": None}
    assert FakeYdl.calls == []
    with connection() as conn:
        sctx = svc.source_context(conn, svc.require_source(conn, src["id"]))
    cs = svc.clip_source(sctx, None)
    assert cs.render_path.name == "original_video.mp4"


def test_clip_fetch_falls_back_to_local_video_when_link_is_dead(
    login_as: Any, db: Any, app_env: Any, synth: SynthMedia, catalog: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """YouTube-источник: секция 1080p по ссылке, но если ссылка умерла — режем из локального видео анализа."""
    from autoshorts.services import clipping as svc
    from autoshorts.stages.clip_fetch import clip_fetch

    def dead(*a: Any, **k: Any) -> Any:
        raise RuntimeError("ERROR: Unable to download webpage: HTTP Error 404: Not Found")

    monkeypatch.setattr(svc, "fetch_section", dead)
    src = seed_source(db, app_env.media_root, synth, origin="url", with_original=False)
    op = login_as("operator")
    cand = add_candidate(db, src["id"], src["transcript"], first=1, last=4)
    job = op.post(f"/clip-candidates/{cand['id']}/jobs", json={"channel_id": "taro_main"}).json()
    # без локального видео — ошибка с повтором, как раньше
    with pytest.raises(svc.RetryableError, match="Секция не скачалась"):
        clip_fetch(_fetch_ctx(db, job["id"], 9102))
    _add_local_original_video(src, synth, "Youtube")
    patch = clip_fetch(_fetch_ctx(db, job["id"], 9103)).payload_patch
    assert patch == {"clip_section": None, "clip_section_fallback": "local_original_video"}


def test_clip_jobs_take_the_niche_of_their_source(
    login_as: Any, db: Any, app_env: Any, synth: SynthMedia, catalog: dict
) -> None:
    """Ниша клипа — ниша источника: техно-видео на кино-канале не получает кино-сценарий и кино-хэштеги."""
    src = seed_source(db, app_env.media_root, synth, niche_id="astro")
    tr = src["transcript"]
    op = login_as("operator")
    cand = add_candidate(db, src["id"], tr, first=0, last=3)
    one = op.post(f"/clip-candidates/{cand['id']}/jobs", json={"channel_id": "taro_main"}).json()
    assert job_row(db, one["id"])["niche_id"] == "astro"
    end = round(min(20.0, float(src["duration_sec"])), 2)
    batch = op.post(
        f"/sources/{src['id']}/clips",
        json={"items": [{"channel_id": "taro_main", "start_sec": 0, "end_sec": end}]},
    ).json()
    assert [job_row(db, j["id"])["niche_id"] for j in batch["items"]] == ["astro"]
    # у источника без ниши — ниша канала
    plain = seed_source(db, app_env.media_root, synth, niche_id=None)
    cand2 = add_candidate(db, plain["id"], plain["transcript"], first=0, last=3)
    two = op.post(f"/clip-candidates/{cand2['id']}/jobs", json={"channel_id": "taro_main"}).json()
    assert job_row(db, two["id"])["niche_id"] == "taro"


def test_clip_clone_fixes_niche_from_source(
    login_as: Any, db: Any, app_env: Any, synth: SynthMedia, catalog: dict
) -> None:
    """Старый ролик получил нишу канала (taro) вместо ниши источника (astro): клон её исправляет."""
    src = seed_source(db, app_env.media_root, synth, niche_id="astro")
    op = login_as("operator")
    cand = add_candidate(db, src["id"], src["transcript"], first=0, last=3)
    job = op.post(f"/clip-candidates/{cand['id']}/jobs", json={"channel_id": "taro_main"}).json()
    db.execute("UPDATE jobs SET niche_id = 'taro' WHERE id = %s", (job["id"],))
    clone = op.post(f"/jobs/{job['id']}/clone", json={}).json()
    assert job_row(db, clone["id"])["niche_id"] == "astro"


def test_author_script_defers_on_llm_rate_limit_instead_of_template(
    login_as: Any, db: Any, app_env: Any, synth: SynthMedia, catalog: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenRouter 429: шаблон «здесь спикер формулирует мысль…» провалит оригинальность.
    Стадия откладывается, а не подставляет шаблон."""
    import dataclasses

    from autoshorts.core.errors import ResourceDeferred
    from autoshorts.services import clipping as svc
    from autoshorts.stages.author_script import author_script

    src = seed_source(db, app_env.media_root, synth)
    cand = add_candidate(db, src["id"], src["transcript"], first=0, last=3)
    job = login_as("operator").post(f"/clip-candidates/{cand['id']}/jobs", json={"channel_id": "taro_main"})
    real = svc.draft_author_script

    def limited(*a: Any, **k: Any) -> Any:
        res = real(*a, **k)
        warns = [*res.warnings, "llm_failed_fallback_heuristic: LLMRetryableError"]
        return dataclasses.replace(res, mode="heuristic", warnings=warns)

    monkeypatch.setattr(svc, "draft_author_script", limited)
    with pytest.raises(ResourceDeferred) as exc:
        author_script(_fetch_ctx(db, job.json()["id"], 9201))
    assert exc.value.reason == "quota_exhausted"


def test_template_without_llm_failure_is_not_treated_as_transient() -> None:
    from types import SimpleNamespace

    from autoshorts.services import clipping as svc

    def res(*warns: str) -> SimpleNamespace:
        return SimpleNamespace(warnings=list(warns))

    assert not svc.llm_failed_transiently(res())
    assert not svc.llm_failed_transiently(res("llm_failed_fallback_heuristic: LLMAuthError"))
    assert svc.llm_failed_transiently(res("llm_failed_fallback_heuristic: ReadTimeout"))


def test_tts_outage_is_deferred_only_while_young() -> None:
    """Edge TTS лежит волнами: молодую задачу откладываем без траты попытки, старше 3 ч — обычная ошибка."""
    from datetime import UTC, datetime, timedelta

    from edge_tts.exceptions import NoAudioReceived

    from autoshorts.services import clipping as svc

    now = datetime.now(UTC)
    assert svc.tts_outage_deferrable(NoAudioReceived("x"), {"created_at": now - timedelta(minutes=20)})
    assert not svc.tts_outage_deferrable(NoAudioReceived("x"), {"created_at": now - timedelta(hours=4)})
    assert not svc.tts_outage_deferrable(ValueError("bad text"), {"created_at": now})


def test_clone_with_copied_template_script_gets_a_fresh_llm_text(
    login_as: Any, db: Any, app_env: Any, synth: SynthMedia, catalog: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Родитель остался с шаблоном (LLM упёрся в лимит) — клон пишет текст заново, а не копирует шаблон."""
    import dataclasses

    from autoshorts.clipping.author.script import AuthorScriptResult, draft_script_heuristic
    from autoshorts.db.pool import connection
    from autoshorts.repos import clips as clips_repo
    from autoshorts.services import clipping as svc
    from autoshorts.stages.author_script import author_script

    src = seed_source(db, app_env.media_root, synth)
    cand = add_candidate(db, src["id"], src["transcript"], first=0, last=3)
    op = login_as("operator")
    job = op.post(f"/clip-candidates/{cand['id']}/jobs", json={"channel_id": "taro_main"}).json()
    real = svc.draft_author_script
    calls: list[int] = []

    def fresh(*a: Any, **k: Any) -> Any:
        calls.append(1)
        return dataclasses.replace(real(*a, **k), mode="llm", label="LLM test")

    template = AuthorScriptResult(
        script=draft_script_heuristic([(0, "Первая фраза."), (1, "Вторая фраза.")], title="т"),
        mode="heuristic",
        label="[черновик без LLM — перепишите своими словами]",
    )
    with connection() as conn:
        clips_repo.set_author_script(conn, job["id"], svc.author_result_to_dict(template, source="copied"))
    monkeypatch.setattr(svc, "llm_configured", lambda: True)
    monkeypatch.setattr(svc, "draft_author_script", fresh)
    res = author_script(_fetch_ctx(db, job["id"], 9301))
    assert calls == [1] and res.payload_patch["clip_author"]["mode"] == "llm"


def test_clone_of_a_clone_is_family_of_the_whole_tree(
    login_as: Any, db: Any, app_env: Any, synth: SynthMedia, catalog: dict
) -> None:
    """Клон клона не «повторяет момент» деда: родня — всё дерево клонов от корня."""
    from autoshorts.db.pool import connection
    from autoshorts.repos import clips as clips_repo

    src = seed_source(db, app_env.media_root, synth)
    cand = add_candidate(db, src["id"], src["transcript"], first=0, last=3)
    op = login_as("operator")
    root = op.post(f"/clip-candidates/{cand['id']}/jobs", json={"channel_id": "taro_main"}).json()["id"]
    child = op.post(f"/jobs/{root}/clone", json={}).json()["id"]
    grandchild = op.post(f"/jobs/{child}/clone", json={}).json()["id"]
    other = op.post(f"/jobs/{root}/clone", json={}).json()["id"]
    with connection() as conn:
        assert clips_repo.family_job_ids(conn, grandchild) == {root, child, other}
        assert clips_repo.family_job_ids(conn, root) == {child, grandchild, other}
