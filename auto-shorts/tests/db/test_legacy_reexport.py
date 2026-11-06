"""`autoshorts.db` стал пакетом: старые импорты работают, legacy грузится лениво,
monkeypatch имени пакета доходит до legacy."""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

import autoshorts.db as db_pkg
from autoshorts import db
from autoshorts.db import LEGACY_EXPORTS, legacy
from tests.migrations.conftest import ROOT, psql

SRC = ROOT / "src" / "autoshorts"


def test_old_module_file_removed_and_package_in_place() -> None:
    assert not (SRC / "db.py").exists()
    assert Path(db_pkg.__file__).resolve() == (SRC / "db" / "__init__.py").resolve()


@pytest.mark.parametrize("name", sorted(LEGACY_EXPORTS))
def test_legacy_names_reexported(name: str) -> None:
    assert getattr(db, name) is getattr(legacy, name)


def test_from_import_style_used_by_dashboard_queries() -> None:
    from autoshorts.db import check_and_reserve_budget, get_conn

    assert get_conn is legacy.get_conn and check_and_reserve_budget is legacy.check_and_reserve_budget


def test_unknown_attribute_still_raises() -> None:
    with pytest.raises(AttributeError):
        _ = db.no_such_function  # noqa: B018


def test_new_modules_do_not_import_legacy_config() -> None:
    """`import autoshorts.db.migrate` не тянет legacy config (mkdir render_tmp и load_dotenv при импорте)."""
    code = (
        "import sys, autoshorts.db.migrate, autoshorts.db.pool, autoshorts.db.notify, autoshorts.cli;"
        "print('autoshorts.config' in sys.modules, 'autoshorts.db.legacy' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd="/")
    assert out.stdout.split() == ["False", "False"]


def test_setattr_on_package_reaches_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    original = legacy.SETTINGS
    patched = dataclasses.replace(original, database_url="postgresql://nobody@127.0.0.1:1/none")
    monkeypatch.setattr(db, "SETTINGS", patched)
    assert legacy.SETTINGS is patched and db.SETTINGS is patched

    sentinel = object()
    monkeypatch.setattr(db, "get_conn", sentinel)
    assert legacy.get_conn is sentinel
    monkeypatch.undo()
    assert legacy.SETTINGS is original and db.get_conn is legacy.get_conn is not sentinel


def test_legacy_functions_work_on_baseline_schema(make_database, monkeypatch: pytest.MonkeyPatch) -> None:
    """Старый конвейер живёт до M7: его функции через пакет работают на legacy-схеме (0001)."""
    url = make_database("legacy_funcs")
    assert psql(url, "-f", str(ROOT / "scripts" / "init_db.sql")).returncode == 0
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO channels (id, niche, platform, cluster) VALUES ('taro_main','taro','youtube','c1')"
        )
    monkeypatch.setattr(db, "SETTINGS", dataclasses.replace(db.SETTINGS, database_url=url))

    job_id = db.enqueue_job("uid_legacy_1", "taro_main", "taro", "v1")
    job = db.claim_next_job("worker-test", "queued")
    assert job is not None and job["id"] == job_id and job["locked_by"] == "worker-test"
    db.update_job_status(job_id, "script_ready", stage_payload_patch={"script": {"hook": "x"}})
    assert db.increment_attempt_count(job_id) == 1
    db.record_generation_event(job_id, "script", "mock", None, None, None, 0.01)
    assert db.get_published_video_cost(job_id) == pytest.approx(0.01)
    assert db.check_and_reserve_budget(0.5) is True
    with psycopg.connect(url, autocommit=True) as conn:
        status, payload = conn.execute(
            "SELECT status, stage_payload FROM jobs WHERE id = %s", (job_id,)
        ).fetchone()
    assert status == "script_ready" and payload == {"script": {"hook": "x"}}
