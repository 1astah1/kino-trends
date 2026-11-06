"""tus 1.0.0 (M5-A, docs/ARCHITECTURE.md §5.2, критерии M5): создание, два PATCH, HEAD и продолжение,
409 на неверное смещение, 507 при подменённой `disk_usage`, 410/412/413/415, прерывание, доступ."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from autoshorts.api.schemas.uploads import TUS_CHUNK_MAX_BYTES, TUS_CONTENT_TYPE, encode_upload_metadata

TUS = {"Tus-Resumable": "1.0.0"}
UPLOAD_ID_LEN = 32


def _meta(filename: str = "видео выпуск 1.mp4", purpose: str = "source", **extra: str) -> str:
    return encode_upload_metadata(
        {"filename": filename, "filetype": "video/mp4", "purpose": purpose, **extra}
    )


def _create(user: Any, length: int, *, purpose: str = "source", filename: str = "видео выпуск 1.mp4") -> Any:
    return user.post(
        "/uploads/tus",
        headers={**TUS, "Upload-Length": str(length), "Upload-Metadata": _meta(filename, purpose)},
    )


def _upload_id(resp: Any) -> str:
    loc = resp.headers["location"]
    assert loc.startswith("/api/v1/uploads/tus/"), loc
    uid = loc.rsplit("/", 1)[-1]
    assert len(uid) == UPLOAD_ID_LEN
    return uid


def _patch(user: Any, uid: str, offset: int, data: bytes, **headers: str) -> Any:
    return user.patch(
        f"/uploads/tus/{uid}",
        content=data,
        headers={**TUS, "Upload-Offset": str(offset), "Content-Type": TUS_CONTENT_TYPE, **headers},
    )


@pytest.fixture
def operator(login_as: Any) -> Any:
    return login_as("operator")


def test_options_advertises_capabilities(operator: Any) -> None:
    resp = operator.request("OPTIONS", "/uploads/tus")
    assert resp.status_code == 204
    assert resp.headers["tus-version"] == "1.0.0"
    assert resp.headers["tus-resumable"] == "1.0.0"
    assert set(resp.headers["tus-extension"].split(",")) == {"creation", "termination", "expiration"}
    assert int(resp.headers["tus-max-size"]) == 6 * 1024**3


def test_create_two_patches_head_resume_complete(operator: Any, app_env: Any, db: Any) -> None:
    payload = bytes(range(256)) * 400  # 102 400 байт
    first, second = payload[:60_000], payload[60_000:]

    resp = _create(operator, len(payload))
    assert resp.status_code == 201, resp.text
    assert resp.headers["tus-resumable"] == "1.0.0"
    assert resp.headers["upload-expires"].endswith("GMT")
    uid = _upload_id(resp)
    part = Path(app_env.media_root) / "uploads" / f"{uid}.part"
    assert part.is_file() and part.stat().st_size == 0

    resp = _patch(operator, uid, 0, first)
    assert resp.status_code == 204, resp.text
    assert resp.headers["upload-offset"] == str(len(first))
    assert resp.headers["tus-resumable"] == "1.0.0"

    # клиент «упал» — продолжение по HEAD
    head = operator.request("HEAD", f"/uploads/tus/{uid}", headers=TUS)
    assert head.status_code == 200
    assert head.headers["upload-offset"] == str(len(first))
    assert head.headers["upload-length"] == str(len(payload))
    assert head.headers["cache-control"] == "no-store"
    assert head.content == b""

    info = operator.get(f"/uploads/{uid}").json()
    assert info["status"] == "uploading" and info["received_bytes"] == len(first)
    assert info["filename"] == "видео выпуск 1.mp4" and info["purpose"] == "source"

    resp = _patch(operator, uid, int(head.headers["upload-offset"]), second)
    assert resp.status_code == 204, resp.text
    assert resp.headers["upload-offset"] == str(len(payload))

    info = operator.get(f"/uploads/{uid}").json()
    assert info["status"] == "complete"
    assert info["received_bytes"] == info["length_bytes"] == len(payload)
    assert info["completed_at"] is not None
    row = db.execute("SELECT path, status FROM uploads WHERE id = %s", (uid,)).fetchone()
    assert row["path"] == f"uploads/{uid}.mp4"  # без .part: уборка tus-частей его не тронет
    final = Path(app_env.media_root) / row["path"]
    assert final.read_bytes() == payload
    assert not part.exists()

    # HEAD готовой загрузки: смещение = длина (клиент с отпечатком сразу видит конец)
    head = operator.request("HEAD", f"/uploads/tus/{uid}", headers=TUS)
    assert head.status_code == 200 and head.headers["upload-offset"] == str(len(payload))
    # повторный пустой PATCH по концу — не ошибка
    assert _patch(operator, uid, len(payload), b"").status_code == 204


def test_wrong_offset_is_409_with_current_offset(operator: Any) -> None:
    uid = _upload_id(_create(operator, 1000))
    assert _patch(operator, uid, 0, b"a" * 300).status_code == 204
    resp = _patch(operator, uid, 100, b"b" * 100)
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "upload_offset_mismatch"
    assert err["details"]["offset"] == 300
    assert resp.headers["tus-resumable"] == "1.0.0"
    # принятое не испорчено: продолжаем с 300
    assert _patch(operator, uid, 300, b"c" * 700).status_code == 204


def test_create_is_507_when_disk_is_low(operator: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import autoshorts.core.resources as resources

    fake = shutil._ntuple_diskusage(total=100 * 1024**3, used=95 * 1024**3, free=5 * 1024**3)  # type: ignore[attr-defined]
    monkeypatch.setattr(resources.shutil, "disk_usage", lambda _path: fake)
    resp = _create(operator, 10 * 1024**2)
    assert resp.status_code == 507, resp.text
    err = resp.json()["error"]
    assert err["code"] == "insufficient_storage"
    assert err["details"]["min_gb"] == 12.0
    assert resp.headers["tus-resumable"] == "1.0.0"


def test_disk_guard_counts_pending_uploads(operator: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import autoshorts.core.resources as resources

    free = 13 * 1024**3  # после одной загрузки на 0,6 ГБ останется больше 12 ГБ, после двух — нет
    fake = shutil._ntuple_diskusage(total=100 * 1024**3, used=100 * 1024**3 - free, free=free)  # type: ignore[attr-defined]
    monkeypatch.setattr(resources.shutil, "disk_usage", lambda _path: fake)
    big = int(0.6 * 1024**3)
    assert _create(operator, big).status_code == 201
    assert _create(operator, big).status_code == 507


def test_protocol_errors(operator: Any) -> None:
    # нет Tus-Resumable → 412 с Tus-Version
    resp = operator.post("/uploads/tus", headers={"Upload-Length": "10", "Upload-Metadata": _meta()})
    assert resp.status_code == 412
    assert resp.json()["error"]["code"] == "tus_version_unsupported"
    assert resp.headers["tus-version"] == "1.0.0"
    # нет Upload-Length / purpose → 400
    resp = operator.post("/uploads/tus", headers={**TUS, "Upload-Metadata": _meta()})
    assert resp.status_code == 400
    resp = operator.post(
        "/uploads/tus",
        headers={
            **TUS,
            "Upload-Length": "10",
            "Upload-Metadata": encode_upload_metadata({"filename": "a.mp4"}),
        },
    )
    assert resp.status_code == 400 and resp.json()["error"]["details"]["missing"] == ["purpose"]
    resp = operator.post(
        "/uploads/tus", headers={**TUS, "Upload-Length": "10", "Upload-Metadata": "filename !!!"}
    )
    assert resp.status_code == 400
    # документ прав больше 50 МБ → 413
    resp = _create(operator, 60 * 1024**2, purpose="rights_doc", filename="договор.pdf")
    assert resp.status_code == 413 and resp.json()["error"]["details"]["limit_bytes"] == 50 * 1024**2
    # источник больше upload_max_gb → 413
    assert _create(operator, 7 * 1024**3).status_code == 413

    uid = _upload_id(_create(operator, 100))
    # не тот Content-Type → 415
    resp = _patch(operator, uid, 0, b"x" * 10, **{"Content-Type": "application/octet-stream"})
    assert resp.status_code == 415
    # чанк длиннее остатка → 413, ничего не принято
    resp = _patch(operator, uid, 0, b"x" * 101)
    assert resp.status_code == 413
    head = operator.request("HEAD", f"/uploads/tus/{uid}", headers=TUS)
    assert head.headers["upload-offset"] == "0"
    # PATCH без Tus-Resumable → 412
    resp = operator.patch(
        f"/uploads/tus/{uid}",
        content=b"x",
        headers={"Upload-Offset": "0", "Content-Type": TUS_CONTENT_TYPE},
    )
    assert resp.status_code == 412


def test_chunk_limit_is_16_mib() -> None:
    assert TUS_CHUNK_MAX_BYTES == 16 * 1024 * 1024


def test_declared_chunk_over_16_mib_is_413(operator: Any) -> None:
    uid = _upload_id(_create(operator, 40 * 1024**2))
    resp = operator.patch(
        f"/uploads/tus/{uid}",
        content=b"",
        headers={
            **TUS,
            "Upload-Offset": "0",
            "Content-Type": TUS_CONTENT_TYPE,
            "Content-Length": str(TUS_CHUNK_MAX_BYTES + 1),
        },
    )
    assert resp.status_code == 413
    assert resp.json()["error"]["details"]["chunk_max_bytes"] == TUS_CHUNK_MAX_BYTES


def test_terminate_and_expire(operator: Any, app_env: Any, db: Any) -> None:
    uid = _upload_id(_create(operator, 50))
    assert _patch(operator, uid, 0, b"z" * 10).status_code == 204
    resp = operator.delete(f"/uploads/tus/{uid}", headers=TUS)
    assert resp.status_code == 204 and resp.headers["tus-resumable"] == "1.0.0"
    assert not (Path(app_env.media_root) / "uploads" / f"{uid}.part").exists()
    assert operator.request("HEAD", f"/uploads/tus/{uid}", headers=TUS).status_code == 404
    assert operator.delete(f"/uploads/tus/{uid}", headers=TUS).status_code == 204  # повтор — не ошибка
    assert operator.get(f"/uploads/{uid}").json()["status"] == "aborted"

    uid2 = _upload_id(_create(operator, 50))
    db.execute("UPDATE uploads SET expires_at = now() - interval '1 minute' WHERE id = %s", (uid2,))
    resp = _patch(operator, uid2, 0, b"z" * 10)
    assert resp.status_code == 410 and resp.json()["error"]["code"] == "upload_expired"
    assert operator.request("HEAD", f"/uploads/tus/{uid2}", headers=TUS).status_code == 410
    assert operator.get(f"/uploads/{uid2}").json()["status"] == "expired"
    assert not (Path(app_env.media_root) / "uploads" / f"{uid2}.part").exists()


def test_access_owner_or_admin_viewer_cannot_upload(login_as: Any, operator: Any) -> None:
    uid = _upload_id(_create(operator, 20))
    other = login_as("operator", "operator2")
    assert other.get(f"/uploads/{uid}").status_code == 404
    assert other.request("HEAD", f"/uploads/tus/{uid}", headers=TUS).status_code == 404
    assert _patch(other, uid, 0, b"x").status_code == 404
    admin = login_as("admin")
    assert admin.get(f"/uploads/{uid}").status_code == 200
    viewer = login_as("viewer")
    assert _create(viewer, 10).status_code == 403
    # без CSRF-заголовка PATCH не проходит
    resp = operator.request(
        "PATCH",
        f"/uploads/tus/{uid}",
        csrf=False,
        content=b"x",
        headers={**TUS, "Upload-Offset": "0", "Content-Type": TUS_CONTENT_TYPE},
    )
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "csrf_failed"


def test_zero_length_upload_is_complete_at_once(operator: Any) -> None:
    resp = _create(operator, 0, purpose="rights_doc", filename="empty.txt")
    assert resp.status_code == 201
    uid = _upload_id(resp)
    assert operator.get(f"/uploads/{uid}").json()["status"] == "complete"


def test_parallel_patch_of_same_upload_is_409(operator: Any, app_env: Any) -> None:
    """Второй PATCH той же загрузки, пока первый пишет (flock на файле), — 409 и продолжение по HEAD."""
    import fcntl
    import os

    uid = _upload_id(_create(operator, 100))
    part = Path(app_env.media_root) / "uploads" / f"{uid}.part"
    fd = os.open(part, os.O_WRONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        resp = _patch(operator, uid, 0, b"x" * 10)
        assert resp.status_code == 409
        assert resp.json()["error"]["details"] == {"offset": 0, "locked": True}
    finally:
        os.close(fd)
    assert _patch(operator, uid, 0, b"x" * 10).status_code == 204
