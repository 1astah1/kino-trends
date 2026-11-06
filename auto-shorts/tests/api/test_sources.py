"""Источники нарезки (M5-A, docs/ARCHITECTURE.md §5.2, критерии M5): права обязательны (без прав — 422,
cc_by без атрибуции — 422, SA/NC/ND — 422), приём из tus-загрузки и по ссылке, карточка с конвейером,
права (аудит), перезапуск шагов, транскрипт json/srt/vtt, медиа студии, склейки."""

from __future__ import annotations

import gzip
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from autoshorts.api.schemas.uploads import TUS_CONTENT_TYPE, encode_upload_metadata

TUS = {"Tus-Resumable": "1.0.0"}
OWN = {"rights_type": "own", "owner_name": "Umka", "proof_note": "свой эфир 12.09"}
CC_BY = {
    "rights_type": "cc_by",
    "owner_name": "Киностудия «28 панфиловцев»",
    "proof_note": "лицензия на странице YouTube, проверено 27.09.2026",
    "license": "CC BY 3.0",
    "license_url": "https://creativecommons.org/licenses/by/3.0/",
    "attribution_text": "«Выступление…» — Киностудия, CC BY 3.0. Изменения: фрагмент, комментарий.",
}
TEXT = (
    "Сегодня я расскажу, как мы начинали работу над фильмом. Это была очень странная история. "
    "Денег не было совсем, и мы решили собрать их у зрителей.\n\n"
    "Первый трейлер мы сделали за две недели. Люди увидели его и начали присылать деньги. "
    "Так появился самый народный фильм. А почему это сработало, я объясню в конце."
)


# ---------------------------------------------------------------- фикстуры


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if shutil.which("ffmpeg") is None:
        pytest.skip("нет ffmpeg")
    out = tmp_path_factory.mktemp("media") / "tiny.mp4"
    subprocess.run(
        ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10", "-f", "lavfi", "-i", "sine=frequency=440",
         "-t", "4", "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1", "-c:a", "aac", "-shortest",
         str(out)],
        check=True, timeout=60,
    )  # fmt: skip
    return out


@pytest.fixture
def operator(login_as: Any) -> Any:
    return login_as("operator")


def upload_file(user: Any, data: bytes, *, filename: str = "эфир.mp4", purpose: str = "source") -> str:
    resp = user.post(
        "/uploads/tus",
        headers={
            **TUS,
            "Upload-Length": str(len(data)),
            "Upload-Metadata": encode_upload_metadata(
                {"filename": filename, "filetype": "video/mp4", "purpose": purpose}
            ),
        },
    )
    assert resp.status_code == 201, resp.text
    uid = resp.headers["location"].rsplit("/", 1)[-1]
    if data:
        resp = user.patch(
            f"/uploads/tus/{uid}",
            content=data,
            headers={**TUS, "Upload-Offset": "0", "Content-Type": TUS_CONTENT_TYPE},
        )
        assert resp.status_code == 204, resp.text
    return uid


def _errors(resp: Any) -> list[dict[str, Any]]:
    body = resp.json()["error"]
    return body["details"].get("errors") or []


def make_prepared_dir(media_root: Path, name: str, video: Path) -> Path:
    """Каталог источника в формате движка (source.json + транскрипт + peaks + раскадровка + прокси)."""
    from PIL import Image

    from autoshorts.clipping.source_ingest import PreparedSource
    from autoshorts.providers.asr.mock import MockAsrProvider

    d = media_root / "sources" / name
    d.mkdir(parents=True, exist_ok=True)
    tr = MockAsrProvider(text=TEXT).transcribe(d / "none.wav", "ru")
    tr.save(d / "transcript.json.gz")
    shutil.copyfile(video, d / "proxy_540p.mp4")
    shutil.copyfile(video, d / "audio.m4a")
    shutil.copyfile(video, d / "original.mp4")
    peaks = {
        "version": 1, "format": "wavesurfer", "duration": tr.duration, "sample_rate": 16000,
        "points_per_min": 2000, "samples_per_point": 480, "length": 4, "channels": 1, "max": 1.0,
        "peaks": [[0.1, 0.5, 0.9, 0.3]],
    }  # fmt: skip
    (d / "peaks.json").write_text(json.dumps(peaks), encoding="utf-8")
    Image.new("RGB", (320, 90), (40, 40, 40)).save(d / "storyboard_000.jpg", "JPEG")
    PreparedSource(
        source_id=name,
        dir=d,
        title="Шальопа на CGevent",
        origin="url",
        origin_url="https://commons.wikimedia.org/wiki/File:demo.webm",
        rights={
            **CC_BY,
            "proof_file_path": None,
            "allowlist_status": "unknown",
            "valid_until": None,
            "owner_channel_url": None,
        },  # fmt: skip
        rights_warnings=["cc_by_attribution_in_description"],
        probe={
            "duration": tr.duration,
            "file_bytes": 1000,
            "has_video": True,
            "has_audio": True,
            "width": 160,
            "height": 120,
            "fps": 10.0,
            "video_codec": "h264",
            "rotation": 0,
        },  # fmt: skip
        sha256="ab" * 32,
        files={
            "original": "original.mp4",
            "proxy": "proxy_540p.mp4",
            "audio": "audio.m4a",
            "peaks": "peaks.json",
            "storyboard": ["storyboard_000.jpg"],
            "transcript": "transcript.json.gz",
        },  # fmt: skip
        platform={
            "id": "58213536",
            "extractor_key": "Wikimedia",
            "license": "Creative Commons Attribution 3.0",
            "uploader": "Butko",
            "upload_date": "20170421",
        },  # fmt: skip
        peaks={k: v for k, v in peaks.items() if k != "peaks"},
        storyboard={
            "sheets": ["storyboard_000.jpg"],
            "cols": 2,
            "rows": 1,
            "tile_w": 160,
            "tile_h": 90,
            "interval_sec": 2.0,
            "count": 2,
        },  # fmt: skip
        watermark={"suspect": False, "severity": "ok", "frames_checked": 12, "logos_checked": 0, "hits": []},
        transcript={
            "path": "transcript.json.gz",
            "provider": "mock",
            "model": "mock-v1",
            "language": "ru",
            "words": len(tr.words),
            "sentences": len(tr.sentences),
            "duration": tr.duration,
            "rtfx": None,
            "cost_usd": 0.0,
        },  # fmt: skip
    ).save()
    return d


@pytest.fixture
def registered(client: Any, app_env: Any, tiny_video: Path, operator: Any) -> dict[str, Any]:
    """Готовый (ready) источник из подготовленного каталога, без отбора."""
    from autoshorts.services import sources as svc
    from autoshorts.services.audit import SYSTEM

    make_prepared_dir(Path(app_env.media_root), "demo_src", tiny_video)
    return svc.register_prepared_dir("sources/demo_src", select=False, actor=SYSTEM)


# ---------------------------------------------------------------- права обязательны


def test_from_upload_without_rights_is_422(operator: Any) -> None:
    resp = operator.post("/sources/from-upload", json={"upload_id": "a" * 32, "title": "эфир"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert any(e["loc"][-1] == "rights" and e["type"] == "missing" for e in _errors(resp))
    # пустая запись прав — тоже отказ
    resp = operator.post("/sources/from-url", json={"url": "https://youtu.be/abc", "rights": {}})
    assert resp.status_code == 422


def test_cc_by_without_attribution_is_422(operator: Any) -> None:
    rights = {k: v for k, v in CC_BY.items() if k != "attribution_text"}
    resp = operator.post("/sources/from-url", json={"url": "https://youtu.be/abc", "rights": rights})
    assert resp.status_code == 422
    errs = _errors(resp)
    assert any(e["type"] == "rights_rejected" and "attribution_required" in e["msg"] for e in errs), errs


@pytest.mark.parametrize(
    ("patch", "code"),
    [
        ({"license": "CC BY-SA 4.0"}, "license_incompatible"),
        ({"license": "CC BY-NC 4.0"}, "license_incompatible"),
        ({"license_url": "https://creativecommons.org/licenses/by-nd/4.0/"}, "license_incompatible"),
        ({"valid_until": "2020-01-01"}, "rights_expired"),
    ],
)
def test_incompatible_rights_are_422(operator: Any, patch: dict[str, Any], code: str) -> None:
    resp = operator.post(
        "/sources/from-url", json={"url": "https://youtu.be/abc", "rights": {**CC_BY, **patch}}
    )
    assert resp.status_code == 422
    assert any(e["type"] == "rights_rejected" and code in e["msg"] for e in _errors(resp))


# ---------------------------------------------------------------- приём


def test_from_upload_creates_source_and_queues_prepare(
    operator: Any, tiny_video: Path, app_env: Any, db: Any
) -> None:
    uid = upload_file(operator, tiny_video.read_bytes())
    resp = operator.post(
        "/sources/from-upload",
        json={
            "upload_id": uid,
            "title": "  Эфир 12.09  ",
            "rights": OWN,
            "options": {"asr_provider": "mock_asr"},
        },
    )
    assert resp.status_code == 202, resp.text
    src = resp.json()
    assert src["title"] == "Эфир 12.09"
    assert src["origin"] == "upload" and src["status"] == "preparing"
    assert src["rights"]["rights_type"] == "own" and src["rights"]["gate_applies"] is False
    assert src["rights"]["allowlist_status"] == "not_needed"
    assert src["duration_sec"] == pytest.approx(4.0, abs=0.3)
    assert src["width"] == 160 and src["height"] == 120 and src["has_audio"] is True
    assert src["options"]["asr_provider"] == "mock_asr"
    steps = {s["step"]: s for s in src["pipeline"]}
    assert steps["download"]["status"] == "skipped"
    assert steps["prepare"]["status"] == "queued" and steps["prepare"]["queue_position"] == 1
    assert steps["transcribe"]["status"] == "pending"
    assert src["media"]["proxy_url"] is None and src["transcript"] is None
    assert src["candidates_count"] == 0 and src["jobs_count"] == 0

    sid = src["id"]
    original = Path(app_env.media_root) / "sources" / str(sid) / "original.mp4"
    assert original.read_bytes() == tiny_video.read_bytes()
    up = db.execute("SELECT status, path FROM uploads WHERE id = %s", (uid,)).fetchone()
    assert up["status"] == "consumed" and up["path"] == f"sources/{sid}/original.mp4"
    task = db.execute(
        "SELECT kind, resource_class, payload, dedup_key FROM tasks WHERE source_id = %s", (sid,)
    ).fetchone()
    assert task["kind"] == "source_prepare" and task["resource_class"] == "heavy"
    assert task["payload"]["chain"] == ["transcribe", "select"]
    audit = db.execute("SELECT action, diff FROM audit_log WHERE entity_type = 'source'").fetchone()
    assert audit["action"] == "source.create" and audit["diff"]["rights_type"] == "own"

    # повторно ту же загрузку — 409 upload_consumed
    resp = operator.post("/sources/from-upload", json={"upload_id": uid, "title": "x", "rights": OWN})
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "upload_consumed"

    # удаление с файлами: задача отменена, каталог sources/<id> удалён
    assert operator.delete(f"/sources/{sid}?purge_files=true").status_code == 204
    assert not original.parent.exists()
    task = db.execute("SELECT status FROM tasks WHERE source_id = %s", (sid,)).fetchone()
    assert task["status"] == "cancelled"


def test_from_upload_state_errors(operator: Any, tiny_video: Path) -> None:
    # незавершённая загрузка
    resp = operator.post(
        "/uploads/tus",
        headers={**TUS, "Upload-Length": "1000",
                 "Upload-Metadata": encode_upload_metadata({"filename": "a.mp4", "purpose": "source"})},
    )  # fmt: skip
    uid = resp.headers["location"].rsplit("/", 1)[-1]
    resp = operator.post("/sources/from-upload", json={"upload_id": uid, "title": "x", "rights": OWN})
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "upload_not_complete"
    # неизвестная загрузка
    resp = operator.post("/sources/from-upload", json={"upload_id": "f" * 32, "title": "x", "rights": OWN})
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "upload_not_found"
    # не видео → 422 source_rejected (probe_failed)
    bad = upload_file(operator, b"not a video at all" * 100, filename="notes.mp4")
    resp = operator.post("/sources/from-upload", json={"upload_id": bad, "title": "x", "rights": OWN})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "source_rejected"
    assert resp.json()["error"]["details"]["reason"] == "probe_failed"
    # загрузка «для документа прав» не годится как источник
    doc = upload_file(operator, tiny_video.read_bytes(), filename="scan.pdf", purpose="rights_doc")
    resp = operator.post("/sources/from-upload", json={"upload_id": doc, "title": "x", "rights": OWN})
    assert resp.status_code == 422 and _errors(resp)[0]["type"] == "upload_purpose"
    # неизвестная ниша и неизвестный провайдер ASR
    ok = upload_file(operator, tiny_video.read_bytes())
    resp = operator.post(
        "/sources/from-upload", json={"upload_id": ok, "title": "x", "rights": OWN, "niche_id": "no-such"}
    )
    assert resp.status_code == 422 and _errors(resp)[0]["loc"][-1] == "niche_id"
    resp = operator.post(
        "/sources/from-upload",
        json={"upload_id": ok, "title": "x", "rights": OWN, "options": {"asr_provider": "yandex_speechkit"}},
    )
    assert resp.status_code == 422 and _errors(resp)[0]["type"] == "provider_disabled"


def test_from_upload_with_rights_document(operator: Any, tiny_video: Path, app_env: Any, db: Any) -> None:
    doc = upload_file(operator, b"%PDF-1.4 fake", filename="договор.pdf", purpose="rights_doc")
    uid = upload_file(operator, tiny_video.read_bytes())
    rights = {**CC_BY, "rights_type": "licensed", "proof_upload_id": doc}
    resp = operator.post(
        "/sources/from-upload", json={"upload_id": uid, "title": "Лицензия", "rights": rights}
    )
    assert resp.status_code == 202, resp.text
    src = resp.json()
    assert src["rights"]["rights_type"] == "licensed_written"
    codes = {w["code"] for w in src["rights"]["warnings"]}
    assert {"content_id_allowlist_not_confirmed", "author_share_gate_applies"} <= codes
    assert all(w["label"] for w in src["rights"]["warnings"])
    assert src["rights"]["gate_applies"] is True
    row = db.execute(
        "SELECT proof_file_path FROM source_rights WHERE source_id = %s", (src["id"],)
    ).fetchone()
    assert row["proof_file_path"].startswith(f"rights/{src['id']}/proof_")
    assert (Path(app_env.media_root) / row["proof_file_path"]).read_bytes() == b"%PDF-1.4 fake"


def test_from_url_records_rights_and_queues_download(operator: Any, db: Any) -> None:
    resp = operator.post(
        "/sources/from-url",
        json={"url": "https://www.youtube.com/watch?v=SP5r7PKG7LA", "rights": CC_BY, "language": "ru"},
    )
    assert resp.status_code == 202, resp.text
    src = resp.json()
    assert src["origin"] == "url" and src["status"] == "downloading"
    assert src["origin_url"] == "https://www.youtube.com/watch?v=SP5r7PKG7LA"
    assert {w["code"] for w in src["rights"]["warnings"]} >= {"cc_by_attribution_in_description"}
    task = db.execute(
        "SELECT kind, resource_class, payload FROM tasks WHERE source_id = %s", (src["id"],)
    ).fetchone()
    assert task["kind"] == "source_download" and task["resource_class"] == "light"
    assert task["payload"]["chain"] == ["prepare", "transcribe", "select"]
    steps = {s["step"]: s["status"] for s in src["pipeline"]}
    assert steps["download"] == "queued" and steps["prepare"] == "pending"
    # локальные адреса не качаем
    for url in ("http://127.0.0.1:8080/v.mp4", "http://localhost/v", "http://10.0.0.5/x", "http://[::1]/x"):
        resp = operator.post("/sources/from-url", json={"url": url, "rights": OWN})
        assert resp.status_code == 422, url
        assert resp.json()["error"]["code"] == "unsupported_url"


# ---------------------------------------------------------------- карточка, список, правка, удаление


def test_list_get_patch_delete(operator: Any, login_as: Any, registered: dict[str, Any], db: Any) -> None:
    sid = registered["id"]
    viewer = login_as("viewer")
    page = viewer.get("/sources").json()
    assert page["total"] == 1 and page["items"][0]["id"] == sid
    item = page["items"][0]
    assert item["status"] == "ready" and item["rights_type"] == "cc_by" and item["progress"] == 100.0
    assert viewer.get("/sources?status=failed").json()["total"] == 0
    assert viewer.get("/sources?q=шальопа").json()["total"] == 1
    assert viewer.get("/sources?origin=upload").json()["total"] == 0
    assert viewer.get("/sources?sort=-bogus").status_code == 422
    assert (
        viewer.post("/sources/from-url", json={"url": "https://youtu.be/x", "rights": OWN}).status_code == 403
    )

    card = viewer.get(f"/sources/{sid}").json()
    assert card["platform"]["extractor"] == "Wikimedia" and card["platform"]["upload_date"] == "20170421"
    assert card["watermark"]["severity"] == "ok" and card["watermark_suspect"] is False
    assert card["transcript"]["provider_key"] == "mock" and card["transcript"]["words_count"] > 20
    assert card["media"]["proxy_url"] == f"/api/v1/sources/{sid}/media/proxy"
    assert card["media"]["peaks_url"] and card["media"]["storyboard_url"] and card["media"]["transcript_url"]
    assert card["proxy_retain_until"] is not None

    resp = operator.patch(f"/sources/{sid}", json={"title": "Новое название"})
    assert resp.status_code == 200 and resp.json()["title"] == "Новое название"
    assert operator.patch(f"/sources/{sid}", json={"niche_id": "nope"}).status_code == 422

    assert operator.delete(f"/sources/{sid}?purge_files=true").status_code == 204
    assert viewer.get(f"/sources/{sid}").status_code == 404
    assert viewer.get("/sources").json()["total"] == 0
    row = db.execute("SELECT status, deleted_at FROM sources WHERE id = %s", (sid,)).fetchone()
    assert row["status"] == "archived" and row["deleted_at"] is not None


def test_rights_get_put_audited(operator: Any, registered: dict[str, Any], db: Any) -> None:
    sid = registered["id"]
    got = operator.get(f"/sources/{sid}/rights").json()
    assert got["license"] == "CC BY 3.0" and got["gate_applies"] is True
    resp = operator.put(f"/sources/{sid}/rights", json=OWN)
    assert resp.status_code == 200, resp.text
    assert resp.json()["rights_type"] == "own" and resp.json()["gate_applies"] is False
    assert resp.json()["updated_at"] is not None
    bad = operator.put(f"/sources/{sid}/rights", json={**CC_BY, "license": "CC BY-NC-SA 4.0"})
    assert bad.status_code == 422
    audit = db.execute("SELECT diff FROM audit_log WHERE action = 'source.rights'").fetchone()
    assert (
        audit["diff"]["before"]["rights_type"] == "cc_by" and audit["diff"]["after"]["rights_type"] == "own"
    )
    # манифест движка получил новые права (clip-job читает их оттуда)
    from autoshorts.services import sources as svc

    with_manifest = svc.load_manifest({"id": sid, "media_dir": "sources/demo_src"})
    assert with_manifest.rights["rights_type"] == "own"


# ---------------------------------------------------------------- транскрипт и медиа студии


def test_transcript_formats(operator: Any, registered: dict[str, Any]) -> None:
    sid = registered["id"]
    data = operator.get(f"/sources/{sid}/transcript").json()
    assert data["source_id"] == sid and data["provider_key"] == "mock" and data["language"] == "ru"
    assert {"w", "s", "e", "sid"} <= set(data["words"][0])
    assert set(data["sentences"][0]) == {"sid", "s", "e", "text", "w0", "w1"}
    assert data["sentences"][-1]["w1"] == len(data["words"]) - 1
    srt = operator.get(f"/sources/{sid}/transcript?format=srt")
    assert srt.headers["content-type"].startswith("text/plain")
    assert srt.text.startswith("1\n00:00:00,") and "-->" in srt.text
    vtt = operator.get(f"/sources/{sid}/transcript?format=vtt").text
    assert vtt.startswith("WEBVTT\n\n00:00:00.")


def test_media_endpoints(operator: Any, registered: dict[str, Any], tiny_video: Path) -> None:
    sid = registered["id"]
    resp = operator.get(f"/sources/{sid}/media/proxy", headers={"Range": "bytes=0-99"})
    assert resp.status_code == 206 and len(resp.content) == 100
    assert resp.headers["content-type"] == "video/mp4"
    resp = operator.get(f"/sources/{sid}/media/audio")
    assert resp.status_code == 200 and resp.headers["content-type"] == "audio/mp4"
    peaks = operator.get(f"/sources/{sid}/peaks")
    assert peaks.status_code == 200 and peaks.json()["format"] == "wavesurfer"
    sb = operator.get(f"/sources/{sid}/storyboard").json()
    assert sb["sheets"] == [f"/api/v1/sources/{sid}/storyboard/0"] and sb["interval_sec"] == 2.0
    sheet = operator.get(f"/sources/{sid}/storyboard/0")
    assert sheet.status_code == 200 and sheet.headers["content-type"] == "image/jpeg"
    assert operator.get(f"/sources/{sid}/storyboard/5").status_code == 404
    assert operator.get("/sources/999999/media/proxy").status_code == 404


def test_scenes_report_only_computed(operator: Any, registered: dict[str, Any], db: Any) -> None:
    sid = registered["id"]
    empty = operator.get(f"/sources/{sid}/scenes").json()
    assert empty == {"items": [], "coverage": []}
    db.execute(
        "UPDATE sources SET scenes = %s WHERE id = %s",
        (json.dumps({"version": 1, "cuts": [3.0, 7.5], "coverage": [[0, 10], [20, 30]]}), sid),
    )
    out = operator.get(f"/sources/{sid}/scenes?start_sec=2&end_sec=25").json()
    assert out["coverage"] == [[0.0, 10.0], [20.0, 30.0]]
    assert out["items"] == [
        {"start_sec": 0.0, "end_sec": 3.0},
        {"start_sec": 3.0, "end_sec": 7.5},
        {"start_sec": 7.5, "end_sec": 10.0},
        {"start_sec": 20.0, "end_sec": 30.0},
    ]


# ---------------------------------------------------------------- перезапуск шагов


def test_reprocess_steps(operator: Any, registered: dict[str, Any], db: Any) -> None:
    sid = registered["id"]
    resp = operator.post(f"/sources/{sid}/reprocess", json={"steps": ["select"]})
    assert resp.status_code == 202, resp.text
    task = db.execute("SELECT kind, payload FROM tasks WHERE id = %s", (resp.json()["task_id"],)).fetchone()
    assert task["kind"] == "source_select" and task["payload"]["chain"] == []
    assert operator.get(f"/sources/{sid}").json()["status"] == "selecting"
    # пока шаг в работе — 409
    busy = operator.post(f"/sources/{sid}/reprocess", json={"steps": ["transcribe"]})
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "source_not_ready"
    db.execute("UPDATE tasks SET status = 'cancelled' WHERE source_id = %s", (sid,))

    resp = operator.post(
        f"/sources/{sid}/reprocess", json={"steps": ["transcribe"], "asr_provider": "mock_asr"}
    )
    assert resp.status_code == 202, resp.text
    task = db.execute(
        "SELECT kind, resource_class, payload FROM tasks WHERE id = %s", (resp.json()["task_id"],)
    ).fetchone()
    assert task["kind"] == "source_transcribe_chunk" and task["resource_class"] == "heavy"
    assert task["payload"]["provider"] == "mock_asr" and task["payload"]["chain"] == ["select"]
    assert task["payload"]["idx"] == 0 and task["payload"]["rev"]
    card = operator.get(f"/sources/{sid}").json()
    assert card["status"] == "transcribing" and card["options"]["asr_provider"] == "mock_asr"
    steps = {s["step"]: s for s in card["pipeline"]}
    assert steps["transcribe"]["status"] == "queued" and steps["select"]["status"] == "pending"
    db.execute("UPDATE tasks SET status = 'cancelled' WHERE source_id = %s", (sid,))

    resp = operator.post(f"/sources/{sid}/reprocess", json={"steps": ["scenes"]})
    assert resp.status_code == 202
    task = db.execute("SELECT kind FROM tasks WHERE id = %s", (resp.json()["task_id"],)).fetchone()
    assert task["kind"] == "source_scenes"
    # отменённый шаг без активных задач: карточка честно показывает failed
    db.execute("UPDATE tasks SET status = 'cancelled' WHERE source_id = %s", (sid,))
    card = operator.get(f"/sources/{sid}").json()
    assert card["status"] == "failed" and card["error"] == "Задача отменена"
    assert operator.post(f"/sources/{sid}/reprocess", json={"steps": ["select", "select"]}).status_code == 422


def test_register_prepared_dir_with_select(client: Any, app_env: Any, tiny_video: Path, db: Any) -> None:
    from autoshorts.core.errors import Conflict
    from autoshorts.services import sources as svc
    from autoshorts.services.audit import SYSTEM

    make_prepared_dir(Path(app_env.media_root), "demo_sel", tiny_video)
    src = svc.register_prepared_dir("demo_sel", actor=SYSTEM)
    assert src["status"] == "selecting" and src["media"]["transcript_url"]
    task = db.execute("SELECT kind FROM tasks WHERE source_id = %s", (src["id"],)).fetchone()
    assert task["kind"] == "source_select"
    tr = db.execute(
        "SELECT provider_key, path, is_current FROM transcripts WHERE source_id = %s", (src["id"],)
    ).fetchone()
    assert tr["is_current"] and tr["path"] == "sources/demo_sel/transcript.json.gz"
    with pytest.raises(Conflict):
        svc.register_prepared_dir("sources/demo_sel", actor=SYSTEM)
    raw = gzip.decompress((Path(app_env.media_root) / tr["path"]).read_bytes())
    assert json.loads(raw)["provider"] == "mock"
