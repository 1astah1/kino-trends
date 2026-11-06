"""core/storage.py: раскладка MEDIA_ROOT и безопасные пути (без БД)."""

from __future__ import annotations

import os
import time
from datetime import date
from pathlib import Path

import pytest

from autoshorts.core.storage import (
    MediaLayout,
    UnsafePathError,
    atomic_write_text,
    prune_older_than,
    sha256_file,
    tail_lines,
    validate_relpath,
    x_accel_path,
)


def test_layout_paths(tmp_path: Path) -> None:
    m = MediaLayout(tmp_path)
    assert m.job_dir("abc123") == tmp_path / "jobs/abc123"
    assert m.master_path("abc123", date(2026, 9, 26)) == tmp_path / "masters/2026/09/abc123.mp4"
    assert m.source_dir(7) == tmp_path / "sources/7"
    assert m.upload_part("up_1") == tmp_path / "uploads/up_1.part"
    assert m.rights_dir(7) == tmp_path / "rights/7"
    assert m.thumb_path("youtube", "dQw4w9WgXcQ") == tmp_path / "thumbs/youtube/dQw4w9WgXcQ.jpg"
    assert m.banner_path("a" * 64) == tmp_path / f"banners/{'a' * 64}.png"
    assert m.task_log_path(42) == tmp_path / "logs/tasks/42.log"
    assert m.tmp_dir() == tmp_path / "tmp"
    assert not (tmp_path / "jobs").exists()  # без create ничего не создаётся
    assert m.job_dir("abc123", create=True).is_dir()
    m.ensure_tree()
    for sub in ("jobs", "masters", "sources", "uploads", "rights", "thumbs", "banners", "logs/tasks", "tmp"):
        assert (tmp_path / sub).is_dir()


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "/etc/passwd",
        "../x",
        "jobs/../../etc",
        "jobs/./a",
        "jobs\\a",
        "jobs/ролик.mp4",
        "jobs/.hidden",
        "a//b",
    ],
)
def test_validate_relpath_rejects(bad: str) -> None:
    with pytest.raises(UnsafePathError):
        validate_relpath(bad)


def test_relpath_resolve_roundtrip_and_escape(tmp_path: Path) -> None:
    m = MediaLayout(tmp_path / "media")
    out = m.job_dir("uid1", create=True) / "final.mp4"
    out.write_bytes(b"x")
    rel = m.relpath(out)
    assert rel == "jobs/uid1/final.mp4"
    assert m.resolve(rel) == out.resolve()
    assert x_accel_path(rel) == "/_protected_media/jobs/uid1/final.mp4"
    with pytest.raises(UnsafePathError):
        m.relpath(tmp_path / "outside.txt")
    # симлинк наружу MEDIA_ROOT не разыменовывается в чужой файл
    secret = tmp_path / "secret.txt"
    secret.write_text("s")
    os.symlink(secret, m.root / "jobs" / "link.txt")
    with pytest.raises(UnsafePathError):
        m.resolve("jobs/link.txt")
    with pytest.raises(UnsafePathError):
        m.job_dir("../evil")


def test_temp_dir_is_removed(tmp_path: Path) -> None:
    m = MediaLayout(tmp_path)
    with pytest.raises(RuntimeError), m.temp_dir() as work:
        (work / "f").write_text("x")
        saved = work
        raise RuntimeError("boom")
    assert not saved.exists()


def test_atomic_write_tail_and_hash(tmp_path: Path) -> None:
    f = atomic_write_text(tmp_path / "a" / "log.txt", "\n".join(f"line {i}" for i in range(500)) + "\n")
    assert tail_lines(f, 3) == ["line 497", "line 498", "line 499"]
    assert tail_lines(f, 3, max_bytes=20)[-1] == "line 499"
    assert tail_lines(tmp_path / "missing.log") == []
    assert len(sha256_file(f)) == 64


def test_prune_older_than(tmp_path: Path) -> None:
    old = tmp_path / "old.log"
    new = tmp_path / "new.log"
    old_dir = tmp_path / "work-old"
    old.write_text("x" * 10)
    new.write_text("y")
    old_dir.mkdir()
    past = time.time() - 3 * 86400
    os.utime(old, (past, past))
    os.utime(old_dir, (past, past))
    stats = prune_older_than(tmp_path, 86400)
    assert (stats.files, stats.dirs, stats.bytes) == (1, 1, 10)
    assert not old.exists() and not old_dir.exists() and new.exists()
