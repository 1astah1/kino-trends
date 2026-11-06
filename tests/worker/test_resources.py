"""core/resources.py: /proc, политика допуска, дисковый сторож, flock (без БД)."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from autoshorts.core.errors import InsufficientStorage, ResourceDeferred
from autoshorts.core.resources import (
    AdmissionPolicy,
    DiskLevel,
    FileLock,
    ResourceSnapshot,
    admission_policy,
    check_heavy_admission,
    disk_level,
    ensure_upload_space,
    evaluate_heavy_admission,
    heavy_thread_env,
    parse_psi,
    read_mem_available_mb,
    take_snapshot,
)
from autoshorts.domain.settings_schema import RuntimeSettings

PSI_TEXT = (
    "some avg10=12.50 avg60=3.10 avg300=0.40 total=123\nfull avg10=1.00 avg60=0.00 avg300=0.00 total=5\n"
)


def snap(**kw) -> ResourceSnapshot:
    base = dict(
        mem_available_mb=4000, psi_cpu10=1.0, psi_mem10=0.0, psi_io10=0.0, load1=0.5, disk_free_gb=50.0
    )
    base.update(kw)
    return ResourceSnapshot(**base)


def test_parse_psi_and_meminfo(tmp_path: Path) -> None:
    psi = parse_psi(PSI_TEXT)
    assert psi is not None and psi.some_avg10 == 12.5 and psi.full_avg10 == 1.0
    assert parse_psi("garbage") is None
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 8000000 kB\nMemAvailable:    2636384 kB\n")
    assert read_mem_available_mb(meminfo) == 2574
    assert read_mem_available_mb(tmp_path / "missing") is None


def test_real_snapshot_reads_proc(tmp_path: Path) -> None:
    s = take_snapshot(tmp_path)
    assert s.load1 >= 0
    assert s.disk_free_gb is not None and s.disk_free_gb > 0
    if Path("/proc/meminfo").exists():
        assert s.mem_available_mb and s.mem_available_mb > 0


def test_policy_profile_defaults_and_explicit_overrides() -> None:
    shared = admission_policy(RuntimeSettings())
    assert (shared.min_mem_available_mb, shared.max_load1, shared.max_psi_mem10, shared.max_psi_cpu10) == (
        1200,
        3.0,
        10.0,
        40.0,
    )
    dedicated = admission_policy(RuntimeSettings.from_overrides({"resource_profile": "dedicated_small"}))
    assert (dedicated.min_mem_available_mb, dedicated.max_load1) == (600, 2.5)
    assert dedicated.max_psi_mem10 is None and dedicated.max_psi_cpu10 is None
    # явное переопределение главнее профиля
    custom = admission_policy(
        RuntimeSettings.from_overrides(
            {"resource_profile": "dedicated_small", "heavy_min_mem_available_mb": 900}
        )
    )
    assert custom.min_mem_available_mb == 900 and custom.max_load1 == 2.5
    assert (custom.disk_soft_min_gb, custom.disk_hard_min_gb, custom.disk_emergency_gb) == (12.0, 8.0, 5.0)


def test_policy_profile_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """RESOURCE_PROFILE из env (§8.1, §8.3) действует, пока `resource_profile` не переопределён в БД."""
    from autoshorts.core.config import get_env

    monkeypatch.setenv("RESOURCE_PROFILE", "dedicated_small")
    get_env.cache_clear()
    try:
        from_env = admission_policy(RuntimeSettings())
        assert (from_env.min_mem_available_mb, from_env.max_load1, from_env.max_psi_mem10) == (600, 2.5, None)
        # явная настройка в БД главнее env
        pinned = admission_policy(RuntimeSettings.from_overrides({"resource_profile": "shared_dev"}))
        assert (pinned.min_mem_available_mb, pinned.max_load1) == (1200, 3.0)
    finally:
        monkeypatch.delenv("RESOURCE_PROFILE")
        get_env.cache_clear()
    assert admission_policy(RuntimeSettings()).min_mem_available_mb == 1200


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"mem_available_mb": 1000}, "mem_low"),
        ({"psi_mem10": 15.0}, "mem_low"),
        ({"load1": 3.5}, "load_high"),
        ({"psi_cpu10": 55.0}, "load_high"),
        ({"disk_free_gb": 7.5}, "disk_low"),
        ({"disk_free_gb": 4.0}, "disk_low"),
    ],
)
def test_admission_denies_with_reason(kw: dict, reason: str) -> None:
    policy = admission_policy(RuntimeSettings())
    decision = evaluate_heavy_admission(snap(**kw), policy)
    assert not decision.ok and decision.reason == reason and decision.retry_after_sec > 0
    with pytest.raises(ResourceDeferred) as exc:
        check_heavy_admission(snap(**kw), policy)
    assert exc.value.reason == reason


def test_admission_ok_and_psi_optional() -> None:
    policy = admission_policy(RuntimeSettings())
    assert evaluate_heavy_admission(snap(), policy).ok
    assert evaluate_heavy_admission(snap(psi_cpu10=None, psi_mem10=None, mem_available_mb=None), policy).ok
    dedicated = admission_policy(profile="dedicated_small")
    assert evaluate_heavy_admission(snap(psi_cpu10=99.0, mem_available_mb=700), dedicated).ok


def test_disk_levels_and_media_quota() -> None:
    p = AdmissionPolicy(1200, 3.0, None, None, 12.0, 8.0, 5.0, media_max_gb=20.0)
    assert disk_level(50.0, p) == DiskLevel.OK
    assert disk_level(11.0, p) == DiskLevel.SOFT
    assert disk_level(7.9, p) == DiskLevel.HARD
    assert disk_level(4.9, p) == DiskLevel.EMERGENCY
    # квота: занято 13 из 20 — «свободно» 7 ГБ, хотя на диске 50
    assert disk_level(50.0, p, media_used_gb=13.0) == DiskLevel.HARD
    assert not evaluate_heavy_admission(snap(disk_free_gb=50.0, media_used_gb=13.0), p).ok


def test_ensure_upload_space(tmp_path: Path) -> None:
    ok = AdmissionPolicy(1200, 3.0, None, None, 0.001, 0.0005, 0.0001)
    assert ensure_upload_space(tmp_path, ok) > 0
    huge = AdmissionPolicy(1200, 3.0, None, None, 10**9, 8.0, 5.0)
    with pytest.raises(InsufficientStorage) as exc:
        ensure_upload_space(tmp_path, huge)
    assert exc.value.status == 507 and exc.value.code == "insufficient_storage"


def test_file_lock_is_exclusive_in_process_and_across_processes(tmp_path: Path) -> None:
    path = tmp_path / "heavy.lock"
    a, b = FileLock(path), FileLock(path)
    assert a.try_acquire()
    assert not b.try_acquire()  # другое открытое описание файла — конфликт даже в одном процессе
    code = textwrap.dedent(
        f"""
        from autoshorts.core.resources import FileLock
        import sys
        sys.exit(0 if FileLock({str(path)!r}).try_acquire() else 3)
        """
    )
    assert subprocess.run([sys.executable, "-c", code], timeout=30).returncode == 3
    a.release()
    assert b.try_acquire()
    b.release()
    assert subprocess.run([sys.executable, "-c", code], timeout=30).returncode == 0


def test_heavy_thread_env() -> None:
    env = heavy_thread_env(2, {"PATH": "/bin"})
    assert env["OMP_NUM_THREADS"] == "2" and env["OPENBLAS_NUM_THREADS"] == "1"
    assert env["MKL_NUM_THREADS"] == "1" and env["NUMBA_NUM_THREADS"] == "1" and env["PATH"] == "/bin"
