"""Профиль ниши сервиса (`services.clipping.ClipNiche`) несёт секцию `selection` из конфига ниши."""

from __future__ import annotations

from autoshorts.clipping.selection import SelectionConfig
from autoshorts.services.clipping import ClipNiche


def test_clip_niche_carries_selection_and_drives_mode() -> None:
    niche = ClipNiche(id="kino", selection_mode="scene", min_payoff=5.0, min_emotion=4.0)
    cfg = SelectionConfig.from_options({}, None, niche=niche)  # type: ignore[arg-type]
    assert cfg.mode == "scene" and cfg.scoring.min_payoff == 5.0
    assert ClipNiche(id="vpn").selection_mode == "talk"


def test_memory_threshold_is_read_at_call_time(monkeypatch) -> None:
    from autoshorts.clipping import source_ingest
    from autoshorts.services import clipping as svc

    monkeypatch.setenv("AS_DEV_MIN_MEM_MB", "800")
    assert source_ingest.min_mem_mb_now() == 800
    seen: dict[str, int] = {}
    monkeypatch.setattr(source_ingest, "mem_available_mb", lambda: 900)
    # None → порог из окружения (800); явное 1500 уважается как есть
    assert source_ingest.wait_for_memory(None, timeout_sec=0.01, sleep=lambda s: None) == 900
    monkeypatch.setattr(source_ingest, "wait_for_memory", lambda mb, timeout_sec: seen.setdefault("mb", mb))
    svc.wait_memory_or_defer()
    assert seen["mb"] == 800
    monkeypatch.delenv("AS_DEV_MIN_MEM_MB")
    monkeypatch.setattr(source_ingest, "_ensure_dotenv", lambda: None)  # без .env репозитория
    assert source_ingest.min_mem_mb_now() == 1500
