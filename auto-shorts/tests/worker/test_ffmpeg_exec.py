"""pipeline/ffmpeg_exec.py: nice, потоки ffmpeg, прогресс, таймаут и отмена убивают группу."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from autoshorts.core.errors import Cancelled
from autoshorts.pipeline.ffmpeg_exec import (
    MediaError,
    MediaTimeout,
    active_process_groups,
    build_command,
    kill_all_active,
    run_media,
)


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def group_members(pgid: int) -> list[str]:
    out = subprocess.run(["ps", "-o", "pid=,comm=", "-g", str(pgid)], capture_output=True, text=True)
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def wait_pid_file(path: Path, timeout: float = 10.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return int(path.read_text().strip())
        time.sleep(0.02)
    raise AssertionError("pid file not written")


def test_build_command_ffmpeg_and_plain() -> None:
    cmd = build_command(["ffmpeg", "-y", "-i", "in.mp4", "-c:v", "libx264", "out.mp4"], threads=2, nice=10)
    assert cmd[:4] == ["nice", "-n", "10", "ffmpeg"]
    for opt in ("-hide_banner", "-nostdin", "-nostats"):
        assert opt in cmd
    assert cmd[cmd.index("-filter_threads") + 1] == "1"
    assert cmd[cmd.index("-filter_complex_threads") + 1] == "1"
    assert cmd[cmd.index("-progress") + 1] == "pipe:1"
    assert cmd[-3:] == ["-threads", "2", "out.mp4"]  # -threads — опция выхода
    # свой -threads вызывающего не дублируется
    own = build_command(["ffmpeg", "-i", "a", "-threads", "1", "b"], threads=2)
    assert own.count("-threads") == 1
    assert build_command(["sleep", "1"], threads=2, nice=15) == ["nice", "-n", "15", "sleep", "1"]
    with pytest.raises(ValueError):
        build_command([], threads=2)


def test_success_nice_and_log(tmp_path: Path) -> None:
    log = tmp_path / "t.log"
    res = run_media(
        ["sh", "-c", "ps -o ni= -p $$; echo to-stderr >&2"], timeout_sec=20, threads=2, log_path=log
    )
    assert res.ok and res.returncode == 0 and res.pgid
    text = log.read_text()
    niceness = int(text.splitlines()[1].strip())
    assert niceness >= 10
    assert "to-stderr" in res.stderr_tail and "to-stderr" in text and "# exit 0" in text
    assert active_process_groups() == []


def test_secrets_redacted_in_log_and_tail(tmp_path: Path) -> None:
    """Лог задачи и stderr_tail видят viewer и выше: argv и вывод проходят маскировку."""
    log = tmp_path / "secret.log"
    script = (
        "printf 'GET https://x/m:generateContent?key=AIzaSECRET1 failed\\n' >&2; "
        "printf 'Authorization: Bearer sk-live-SECRET2' >&2; exit 2"
    )
    with pytest.raises(MediaError) as exc:
        run_media(["sh", "-c", script, "api_key=SECRET3"], timeout_sec=20, threads=1, log_path=log)
    text = log.read_text()
    for secret in ("SECRET1", "SECRET2", "SECRET3"):
        assert secret not in text and secret not in exc.value.stderr_tail, secret
    assert "key=***" in text and "Bearer ***" in text and "# exit 2" in text


def test_thread_env_is_passed(tmp_path: Path) -> None:
    log = tmp_path / "env.log"
    run_media(
        ["sh", "-c", "echo OMP=$OMP_NUM_THREADS BLAS=$OPENBLAS_NUM_THREADS"],
        timeout_sec=20,
        threads=2,
        log_path=log,
    )
    assert "OMP=2 BLAS=1" in log.read_text()


def test_nonzero_exit_raises_or_returns() -> None:
    with pytest.raises(MediaError) as exc:
        run_media(["sh", "-c", "echo bad-thing >&2; exit 3"], timeout_sec=20, threads=1)
    assert exc.value.returncode == 3 and "bad-thing" in exc.value.stderr_tail
    res = run_media(["sh", "-c", "exit 4"], timeout_sec=20, threads=1, check=False)
    assert res.returncode == 4 and not res.ok


def test_timeout_kills_whole_group(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    started = time.monotonic()
    with pytest.raises(MediaTimeout):
        run_media(
            ["sh", "-c", f"echo $$ > {pid_file}; sleep 60 & sleep 60 & wait"],
            timeout_sec=1.0,
            threads=1,
            kill_grace_sec=1.0,
        )
    assert time.monotonic() - started < 15
    pgid = int(pid_file.read_text())
    assert not group_alive(pgid), group_members(pgid)


def test_cancel_kills_sleep_60_group(tmp_path: Path) -> None:
    """Критерий M1: отмена убивает группу процессов `sleep 60`."""
    pid_file = tmp_path / "pid"
    cancel = threading.Event()
    errors: list[BaseException] = []

    def target() -> None:
        try:
            run_media(
                ["sh", "-c", f"echo $$ > {pid_file}; sleep 60 & sleep 60 & wait"],
                timeout_sec=120,
                threads=1,
                cancel=cancel,
                kill_grace_sec=1.0,
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=target)
    t.start()
    pgid = wait_pid_file(pid_file)
    deadline = time.monotonic() + 5
    while len(group_members(pgid)) < 3 and time.monotonic() < deadline:  # sh + 2 × sleep
        time.sleep(0.05)
    members = group_members(pgid)
    assert sum("sleep" in m for m in members) == 2, members
    cancel.set()
    t.join(15)
    assert not t.is_alive()
    assert errors and isinstance(errors[0], Cancelled)
    assert not group_alive(pgid), group_members(pgid)


def test_straggler_in_group_is_killed_after_leader_exits(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    run_media(["sh", "-c", f"echo $$ > {pid_file}; sleep 60 &"], timeout_sec=20, threads=1)
    pgid = int(pid_file.read_text())
    time.sleep(0.1)
    assert not group_alive(pgid), group_members(pgid)


def test_kill_all_active(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    errors: list[BaseException] = []

    def target() -> None:
        try:
            run_media(["sh", "-c", f"echo $$ > {pid_file}; sleep 60"], timeout_sec=120, threads=1)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=target)
    t.start()
    pgid = wait_pid_file(pid_file)
    assert pgid in active_process_groups()
    assert kill_all_active(grace_sec=1.0) >= 1
    t.join(15)
    assert errors and isinstance(errors[0], MediaError)  # убит сигналом → ненулевой код
    assert not group_alive(pgid)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg не установлен")
def test_ffmpeg_progress_reaches_one(tmp_path: Path) -> None:
    seen: list[float] = []
    res = run_media(
        ["ffmpeg", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=10:duration=2", "-f", "null", "-"],
        timeout_sec=60,
        threads=1,
        duration_sec=2.0,
        progress_cb=seen.append,
        progress_interval_sec=0.0,
        log_path=tmp_path / "ff.log",
    )
    assert res.ok
    assert seen and seen[-1] == 1.0 and all(0.0 <= v <= 1.0 for v in seen)
    assert res.out_time_sec is not None and res.out_time_sec >= 1.5
    assert res.progress.get("progress") == "end"
    assert "-threads 1 -" in " ".join(res.cmd)
