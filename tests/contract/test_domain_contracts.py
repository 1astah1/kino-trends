"""Поведение контрактов домена: настройки (§8.3) и события SSE/NOTIFY (§5.1)."""

from __future__ import annotations

import json

import pytest

from autoshorts.domain.events import (
    EVENT_PAYLOADS,
    MAX_NOTIFY_PAYLOAD_BYTES,
    EventType,
    decode_notify,
    encode_notify,
    format_sse,
)
from autoshorts.domain.settings_schema import (
    DEFAULT_SAFE_ZONE_PRESETS,
    RuntimeSettings,
    setting_specs,
    validate_setting_value,
)

DOC_SETTINGS = """business_timezone resource_profile disk_soft_min_gb disk_hard_min_gb disk_emergency_gb
media_max_gb heavy_max_load1 heavy_min_mem_available_mb heavy_max_psi_mem10 heavy_max_psi_cpu10 ffmpeg_threads
render_preset render_crf preview_preset metrics_poll_ladder_hours clip_author_share_min
clip_author_share_gate_for_own clip_score_weights similarity_warn_threshold qc_subtitle_sync_enabled
source_max_hours upload_max_gb proxy_retain_days original_retain_days work_artifacts_retain_days
rejected_jobs_retain_days review_chat_id alert_chat_id fx_usd_rub ab_min_videos_per_variant ab_min_age_hours
outlier_attention outlier_format_works yt_quota_soft_pct yt_quota_hard_units yt_upload_units
redirect_default_product collectors_proxy_url kino_trends_url kino_trends_public_url kino_trends_timeout_sec
telegram_proxy_url safe_zone_presets allow_mock_in_prod""".split()


def test_settings_cover_doc_list_exactly() -> None:
    assert set(RuntimeSettings.model_fields) == set(DOC_SETTINGS)


def test_setting_defaults_from_doc() -> None:
    d = RuntimeSettings()
    assert (d.disk_soft_min_gb, d.disk_hard_min_gb, d.disk_emergency_gb) == (12, 8, 5)
    assert d.heavy_min_mem_available_mb == 1200 and d.ffmpeg_threads == 2
    assert d.clip_author_share_min == 0.30 and d.clip_author_share_gate_for_own is False
    assert d.allow_mock_in_prod is False
    assert d.business_timezone == "Europe/Moscow"
    assert d.metrics_poll_ladder_hours[:3] == [1, 2, 4] and d.metrics_poll_ladder_hours[-1] == 168
    assert d.yt_quota_hard_units == 8000 and d.ab_min_videos_per_variant == 20 and d.ab_min_age_hours == 72
    assert (d.proxy_retain_days, d.original_retain_days, d.work_artifacts_retain_days) == (14, 30, 7)
    assert [p.platform for p in d.safe_zone_presets] == ["youtube", "vk"]
    assert len(DEFAULT_SAFE_ZONE_PRESETS) == 2


def test_setting_specs_are_json_serializable_with_russian_descriptions() -> None:
    specs = setting_specs()
    json.dumps({k: s.default for k, s in specs.items()})
    assert all(s.description for s in specs.values())


def test_validate_setting_value() -> None:
    assert validate_setting_value("render_crf", 21) == 21
    with pytest.raises(ValueError):
        validate_setting_value("render_crf", 99)
    with pytest.raises(ValueError):
        validate_setting_value("no_such_setting", 1)
    with pytest.raises(ValueError):
        RuntimeSettings.from_overrides({"typo_key": 1})


def test_every_event_type_has_payload_schema() -> None:
    assert set(EVENT_PAYLOADS) == set(EventType)


def test_notify_roundtrip_and_sse_frame() -> None:
    text = encode_notify(EventType.JOB_STATUS, {"job_id": 7, "status": "pending_review", "stage": "qc"})
    etype, payload = decode_notify(text)
    assert etype is EventType.JOB_STATUS and payload.model_dump()["job_id"] == 7
    frame = format_sse(etype, payload)
    assert frame.startswith("event: job_status\ndata: {") and frame.endswith("\n\n")


def test_notify_rejects_bad_payload_and_oversize() -> None:
    with pytest.raises(ValueError):
        encode_notify("job_status", {"job_id": 1, "status": "не_статус"})
    with pytest.raises(ValueError):
        encode_notify("task_progress", {"task_id": 1, "kind": "x" * MAX_NOTIFY_PAYLOAD_BYTES, "pct": 1})
