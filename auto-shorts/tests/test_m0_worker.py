"""M0: воркер без offline=True и без mock-провайдеров в prod (ARCHITECTURE §3.3 п.1, §10.2 M0)."""
import dataclasses
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autoshorts.models import JobStatus  # noqa: E402
from autoshorts.pipeline.voice import MockInProdError  # noqa: E402
from autoshorts.queue import worker  # noqa: E402


def test_voice_stage_runs_real_tts(monkeypatch):
    seen = {}

    def fake_run_stage_voice(job, offline=True):
        seen["offline"] = offline

    monkeypatch.setattr(worker, "run_stage_voice", fake_run_stage_voice)
    stage_name, handler = worker.STAGE_HANDLERS[JobStatus.SCRIPT_READY.value]
    assert stage_name == "voice"
    handler({"id": 1, "video_uid": "x", "stage_payload": {}})
    assert seen == {"offline": False}


def _with_settings(monkeypatch, **overrides):
    monkeypatch.setattr(worker, "SETTINGS", dataclasses.replace(worker.SETTINGS, **overrides))


_REAL = {"llm_provider": "gigachat", "llm_api_key": "k", "fal_api_key": "k"}


def test_prod_refuses_mock_llm(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("ALLOW_MOCK_IN_PROD", raising=False)
    _with_settings(monkeypatch, **{**_REAL, "llm_provider": "mock"})
    with pytest.raises(MockInProdError, match="llm"):
        worker.assert_no_mock_in_prod()


def test_prod_refuses_llm_without_key(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("ALLOW_MOCK_IN_PROD", raising=False)
    _with_settings(monkeypatch, **{**_REAL, "llm_api_key": ""})
    with pytest.raises(MockInProdError, match="llm"):
        worker.assert_no_mock_in_prod()


def test_prod_refuses_mock_images(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("ALLOW_MOCK_IN_PROD", raising=False)
    _with_settings(monkeypatch, **{**_REAL, "fal_api_key": ""})
    with pytest.raises(MockInProdError, match="images"):
        worker.assert_no_mock_in_prod()


def test_prod_with_real_providers_starts(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("ALLOW_MOCK_IN_PROD", raising=False)
    _with_settings(monkeypatch, **_REAL)
    worker.assert_no_mock_in_prod()


def test_prod_allow_mock_flag(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("ALLOW_MOCK_IN_PROD", "1")
    _with_settings(monkeypatch, llm_provider="mock", llm_api_key="", fal_api_key="")
    worker.assert_no_mock_in_prod()


def test_dev_allows_mock(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    _with_settings(monkeypatch, llm_provider="mock", llm_api_key="", fal_api_key="")
    worker.assert_no_mock_in_prod()


def test_run_forever_checks_before_loop(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("ALLOW_MOCK_IN_PROD", raising=False)
    _with_settings(monkeypatch, llm_provider="mock", llm_api_key="", fal_api_key="")

    def must_not_run():
        raise AssertionError("цикл не должен стартовать")

    monkeypatch.setattr(worker, "process_one_cycle", must_not_run)
    with pytest.raises(MockInProdError):
        worker.run_forever()
