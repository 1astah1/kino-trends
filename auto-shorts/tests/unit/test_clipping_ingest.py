"""Rights validation and URL intake rules of the clipping source (no network, no ffmpeg)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from autoshorts.clipping import source_ingest as si
from autoshorts.clipping.source_ingest import (
    AllowlistStatus,
    InsufficientMemory,
    RightsError,
    RightsRecord,
    RightsType,
    SourceRejected,
    ingest_local,
    ingest_url,
    license_problems,
    platform_rights_warnings,
    validate_rights,
    wait_for_memory,
    ytdlp_audio_options,
    ytdlp_video_options,
)

TODAY = date(2026, 9, 26)
OWN = {"rights_type": "own", "owner": "Umka", "proof_note": "наш собственный эфир"}
CC = {
    "rights_type": "cc_by",
    "owner_name": "Иван Петров",
    "proof_note": "YouTube: лицензия Creative Commons Attribution, скрин в rights/",
    "attribution_text": "Иван Петров, «Лекция о звёздах», youtube.com/watch?v=x, CC BY 4.0",
    "license": "CC BY 4.0",
    "license_url": "https://creativecommons.org/licenses/by/4.0/",
}


def codes_of(data: Any) -> list[str]:
    with pytest.raises(RightsError) as ei:
        validate_rights(data, today=TODAY)
    return ei.value.codes


# ---------------------------------------------------------------------- accepted


def test_own_minimal_is_accepted() -> None:
    rec, warns = validate_rights(OWN, today=TODAY)
    assert rec.rights_type is RightsType.OWN
    assert rec.owner_name == "Umka"
    assert rec.allowlist_status is AllowlistStatus.NOT_NEEDED
    assert "author_share_gate_applies" not in warns
    db = rec.to_db_dict()
    assert set(db) >= {"rights_type", "owner_name", "proof_note", "allowlist_status", "attribution_text"}
    assert db["rights_type"] == "own" and db["allowlist_status"] == "not_needed"


def test_cc_by_with_attribution_is_accepted_with_reminders() -> None:
    rec, warns = validate_rights(CC, today=TODAY)
    assert rec.rights_type is RightsType.CC_BY
    assert rec.allowlist_status is AllowlistStatus.UNKNOWN
    assert "cc_by_attribution_in_description" in warns
    assert "author_share_gate_applies" in warns
    assert "license_version_unknown" not in warns


def test_licensed_alias_and_allowlist_warning() -> None:
    data = {
        "rights_type": "licensed",
        "owner": "Подкаст «Вечер»",
        "proof_note": "договор от 01.09.2026, п. 3: нарезки разрешены",
        "attribution_text": "Фрагмент подкаста «Вечер», используется по лицензии",
    }
    rec, warns = validate_rights(data, today=TODAY)
    assert rec.rights_type is RightsType.LICENSED_WRITTEN
    assert "content_id_allowlist_not_confirmed" in warns
    rec2, warns2 = validate_rights({**data, "allowlist_status": "allowlisted"}, today=TODAY)
    assert "content_id_allowlist_not_confirmed" not in warns2


def test_cc_by_without_license_version_warns() -> None:
    data = {k: v for k, v in CC.items() if k not in ("license", "license_url")}
    _, warns = validate_rights(data, today=TODAY)
    assert "license_version_unknown" in warns


def test_rights_expiring_soon_warns() -> None:
    _, warns = validate_rights({**OWN, "valid_until": TODAY + timedelta(days=10)}, today=TODAY)
    assert "rights_expire_within_30_days" in warns


def test_record_instance_passes_through() -> None:
    rec = RightsRecord.model_validate(OWN)
    rec2, _ = validate_rights(rec, today=TODAY)
    assert rec2 is rec


# ---------------------------------------------------------------------- rejected


@pytest.mark.parametrize("data", [None, {}])
def test_missing_rights_rejected(data: Any) -> None:
    assert codes_of(data) == ["rights_missing"]


@pytest.mark.parametrize(
    "patch",
    [
        {"rights_type": "stolen"},
        {"proof_note": "ok"},  # < 3 chars
        {"owner": ""},
        {"unexpected": 1},
    ],
)
def test_invalid_records_rejected(patch: dict) -> None:
    assert codes_of({**OWN, **patch}) == ["rights_invalid"]


def test_cc_by_without_attribution_rejected() -> None:
    data = {k: v for k, v in CC.items() if k != "attribution_text"}
    assert "attribution_required" in codes_of(data)


def test_non_own_requires_attribution() -> None:
    for rt in ("licensed_written", "licensed_platform", "public_domain"):
        assert "attribution_required" in codes_of({**OWN, "rights_type": rt})


@pytest.mark.parametrize(
    ("patch", "problem"),
    [
        ({"license": "CC BY-SA 4.0"}, "share_alike"),
        ({"license": "CC BY-NC 4.0"}, "non_commercial"),
        ({"license": "CC BY-ND 3.0"}, "no_derivatives"),
        ({"license": "Creative Commons Attribution-ShareAlike"}, "share_alike"),
        (
            {"license": None, "license_url": "https://creativecommons.org/licenses/by-nc-sa/4.0/"},
            "non_commercial",
        ),
        (
            {"license": None, "license_url": None, "proof_note": "на странице указано CC BY-SA 3.0"},
            "share_alike",
        ),
        ({"license": "Attribution-NonCommercial"}, "non_commercial"),
        ({"license": "CC BY-SA", "rights_type": "licensed_platform"}, "share_alike"),
    ],
)
def test_incompatible_licenses_rejected(patch: dict, problem: str) -> None:
    data = {**CC, **patch}
    with pytest.raises(RightsError) as ei:
        validate_rights(data, today=TODAY)
    assert "license_incompatible" in ei.value.codes
    assert any(problem in d for d in ei.value.details)


def test_license_problem_detector_is_not_trigger_happy() -> None:
    assert license_problems("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/") == []
    assert license_problems("Иван Петров — Лекция о звёздах (CC BY 4.0)") == []
    assert set(license_problems("CC BY-NC-SA 4.0")) == {"share_alike", "non_commercial"}


def test_expired_rights_rejected() -> None:
    assert codes_of({**OWN, "valid_until": "2026-09-01"}) == ["rights_expired"]


# ---------------------------------------------------------------------- ingest gates


def test_ingest_local_checks_rights_before_touching_files(tmp_path: Path) -> None:
    missing = tmp_path / "nope.mp4"
    with pytest.raises(RightsError):
        ingest_local(missing, None, out_root=tmp_path)
    with pytest.raises(SourceRejected) as ei:
        ingest_local(missing, OWN, out_root=tmp_path)
    assert ei.value.code == "not_found"
    assert not any(tmp_path.iterdir())


class FakeYDL:
    def __init__(self, info: dict, log: list) -> None:
        self.info = info
        self.log = log

    def __call__(self, opts: dict) -> FakeYDL:
        self.log.append(opts)
        return self

    def __enter__(self) -> FakeYDL:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def extract_info(self, url: str, download: bool = False) -> dict:
        if download:
            raise AssertionError("must not download in this test")
        return self.info


def test_ingest_url_rejects_rights_before_network(tmp_path: Path) -> None:
    def boom(opts: dict) -> Any:
        raise AssertionError("network touched before rights validation")

    with pytest.raises(RightsError):
        ingest_url("https://youtu.be/x", {"rights_type": "own"}, out_root=tmp_path, ydl_factory=boom)


@pytest.mark.parametrize(
    ("info", "code"),
    [
        ({"id": "a", "duration": 4 * 3600}, "too_long"),
        ({"_type": "playlist", "id": "p"}, "playlist"),
    ],
)
def test_ingest_url_limits(tmp_path: Path, info: dict, code: str) -> None:
    log: list = []
    with pytest.raises(SourceRejected) as ei:
        ingest_url("https://youtu.be/x", OWN, out_root=tmp_path, ydl_factory=FakeYDL(info, log))
    assert ei.value.code == code
    assert log and log[0]["skip_download"] is True


def test_ytdlp_options_audio_first_and_polite(tmp_path: Path) -> None:
    a = ytdlp_audio_options(tmp_path, max_filesize=6_000_000_000)
    v = ytdlp_video_options(tmp_path, max_filesize=6_000_000_000)
    # русская дорожка первой (у роликов с дубляжом «bestaudio» берёт оригинал), /b: прямой mp4 без отдельной аудиодорожки
    assert a["format"] == "bestaudio[language^=ru][ext=m4a]/bestaudio[language^=ru]/bestaudio[ext=m4a]/bestaudio/b"
    assert v["format"].startswith("bv*[height<=540][vcodec^=avc1]")
    for o in (a, v):
        assert o["ratelimit"] == 5_000_000
        assert o["concurrent_fragment_downloads"] == 1
        assert o["js_runtimes"] == {"node": {}}
        assert o["noplaylist"] is True and "cookiefile" not in o
    assert ytdlp_audio_options(tmp_path, cookiefile="own.txt")["cookiefile"] == "own.txt"
    assert a["outtmpl"].endswith("original_audio.%(ext)s")


def test_platform_rights_cross_check() -> None:
    cc, _ = validate_rights(CC, today=TODAY)
    assert (
        platform_rights_warnings(cc, {"license": "Creative Commons Attribution license (reuse allowed)"})
        == []
    )
    assert platform_rights_warnings(cc, {"license": "Standard YouTube License"}) == [
        "platform_license_mismatch"
    ]
    assert platform_rights_warnings(cc, {}) == ["platform_license_missing"]
    own, _ = validate_rights({**OWN, "owner_channel_url": "https://youtube.com/@umka"}, today=TODAY)
    assert platform_rights_warnings(own, {"channel_url": "https://youtube.com/@other"}) == [
        "owner_channel_differs_from_uploader"
    ]


def test_wait_for_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = iter([900, 1000, 1600])
    monkeypatch.setattr(si, "mem_available_mb", lambda: next(seq))
    slept: list[float] = []
    assert wait_for_memory(1500, timeout_sec=60, poll_sec=1, sleep=slept.append) == 1600
    assert slept == [1, 1]
    monkeypatch.setattr(si, "mem_available_mb", lambda: 100)
    with pytest.raises(InsufficientMemory):
        wait_for_memory(1500, timeout_sec=0, poll_sec=0, sleep=lambda s: None)
