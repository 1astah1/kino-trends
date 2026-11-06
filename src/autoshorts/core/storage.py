"""Раскладка `MEDIA_ROOT` и безопасная работа с путями (docs/ARCHITECTURE.md §8.2, §5.1 «Медиа»).

```
MEDIA_ROOT/
  jobs/<video_uid>/…   masters/YYYY/MM/<video_uid>.mp4
  sources/<source_id>/{original.*, proxy_540p.mp4, audio.m4a, peaks.json, storyboard_<n>.jpg,
                       transcript.json.gz, sections/}
  uploads/<upload_id>.part   rights/<source_id>/…   thumbs/<platform>/<video_id>.jpg
  banners/<hash>.png   logs/tasks/<task_id>.log   tmp/
```

* В БД (`artifacts.path`, `tasks.result.file`) хранится путь **относительно** MEDIA_ROOT, в
  POSIX-виде, только ASCII, без `..` и ведущего `/` (`relpath()` / `resolve()` это проверяют).
  Тот же относительный путь уходит в nginx: `X-Accel-Redirect: /_protected_media/<relpath>`.
* Каталоги создаются теми, кто пишет (`ensure_dir`, `layout.job_dir(uid, create=True)`); при
  импорте модуль ничего не создаёт.

    from autoshorts.core.storage import get_layout
    media = get_layout()                       # MEDIA_ROOT из env (core.config)
    out = media.job_dir("a1b2c3", create=True) / "final.mp4"
    rel = media.relpath(out)                   # 'jobs/a1b2c3/final.mp4' -> artifacts.path
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import tempfile
import time
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

X_ACCEL_PREFIX = "/_protected_media/"

# Сегмент имени: латиница, цифры, точка, дефис, подчёркивание; без ведущей точки.
SEGMENT_RE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}")
SHA256_HEX_RE = re.compile(r"[0-9a-f]{16,64}")
PLATFORM_RE = re.compile(r"[a-z][a-z0-9_]{1,15}")

JOBS_DIR = "jobs"
MASTERS_DIR = "masters"
SOURCES_DIR = "sources"
UPLOADS_DIR = "uploads"
RIGHTS_DIR = "rights"
THUMBS_DIR = "thumbs"
BANNERS_DIR = "banners"
LOGS_DIR = "logs"
TASK_LOGS_DIR = "logs/tasks"
TMP_DIR = "tmp"

TOP_LEVEL_DIRS: tuple[str, ...] = (
    JOBS_DIR,
    MASTERS_DIR,
    SOURCES_DIR,
    UPLOADS_DIR,
    RIGHTS_DIR,
    THUMBS_DIR,
    BANNERS_DIR,
    TASK_LOGS_DIR,
    TMP_DIR,
)


class UnsafePathError(ValueError):
    """Путь выходит за MEDIA_ROOT или содержит недопустимые символы."""


def check_segment(value: str | int, what: str = "segment") -> str:
    text = str(value)
    if not SEGMENT_RE.fullmatch(text):
        raise UnsafePathError(f"invalid {what}: {text!r}")
    return text


def validate_relpath(rel: str) -> str:
    """Проверяет относительный путь внутри MEDIA_ROOT и возвращает его нормализованным (POSIX)."""
    if not isinstance(rel, str) or not rel:
        raise UnsafePathError("empty media path")
    if not rel.isascii() or "\\" in rel or "\x00" in rel:
        raise UnsafePathError(f"media path must be ASCII POSIX: {rel!r}")
    if rel.startswith("/"):
        raise UnsafePathError(f"media path must be relative: {rel!r}")
    parts = rel.split("/")
    for part in parts:
        if part in ("", ".", "..") or not SEGMENT_RE.fullmatch(part):
            raise UnsafePathError(f"invalid media path segment {part!r} in {rel!r}")
    return rel


def x_accel_path(rel: str) -> str:
    """Значение `X-Accel-Redirect` для nginx (§8.5): `/_protected_media/<relpath>`."""
    return X_ACCEL_PREFIX + validate_relpath(rel)


def ensure_dir(path: Path, mode: int = 0o750) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    return path


@dataclass(frozen=True)
class MediaLayout:
    root: Path

    # ------------------------------------------------------------ пути

    def _dir(self, rel: str, create: bool) -> Path:
        path = self.root / rel
        if create:
            ensure_dir(path)
        return path

    def jobs_dir(self, *, create: bool = False) -> Path:
        return self._dir(JOBS_DIR, create)

    def job_dir(self, video_uid: str, *, create: bool = False) -> Path:
        return self._dir(f"{JOBS_DIR}/{check_segment(video_uid, 'video_uid')}", create)

    def master_path(
        self, video_uid: str, when: date | datetime, ext: str = "mp4", *, create: bool = False
    ) -> Path:
        """`masters/YYYY/MM/<video_uid>.mp4` — мастер опубликованного, не удаляется никогда (§8.2)."""
        folder = self._dir(f"{MASTERS_DIR}/{when.year:04d}/{when.month:02d}", create)
        return folder / f"{check_segment(video_uid, 'video_uid')}.{check_segment(ext, 'ext')}"

    def source_dir(self, source_id: int, *, create: bool = False) -> Path:
        return self._dir(f"{SOURCES_DIR}/{int(source_id)}", create)

    def upload_part(self, upload_id: str, *, create_dir: bool = False) -> Path:
        return self._dir(UPLOADS_DIR, create_dir) / f"{check_segment(upload_id, 'upload_id')}.part"

    def rights_dir(self, source_id: int, *, create: bool = False) -> Path:
        return self._dir(f"{RIGHTS_DIR}/{int(source_id)}", create)

    def thumb_path(self, platform: str, video_id: str, *, create_dir: bool = False) -> Path:
        if not PLATFORM_RE.fullmatch(platform):
            raise UnsafePathError(f"invalid platform: {platform!r}")
        folder = self._dir(f"{THUMBS_DIR}/{platform}", create_dir)
        return folder / f"{check_segment(video_id, 'video_id')}.jpg"

    def banner_path(self, content_hash: str, *, create_dir: bool = False) -> Path:
        if not SHA256_HEX_RE.fullmatch(content_hash):
            raise UnsafePathError(f"invalid banner hash: {content_hash!r}")
        return self._dir(BANNERS_DIR, create_dir) / f"{content_hash}.png"

    def task_logs_dir(self, *, create: bool = False) -> Path:
        return self._dir(TASK_LOGS_DIR, create)

    def task_log_path(self, task_id: int, *, create_dir: bool = False) -> Path:
        return self.task_logs_dir(create=create_dir) / f"{int(task_id)}.log"

    def tmp_dir(self, *, create: bool = False) -> Path:
        return self._dir(TMP_DIR, create)

    # ------------------------------------------------------------ относительные пути

    def relpath(self, path: Path | str) -> str:
        """Абсолютный путь внутри MEDIA_ROOT → относительный POSIX для БД. Снаружи — UnsafePathError."""
        p = Path(path)
        if not p.is_absolute():
            return validate_relpath(p.as_posix())
        root = self.root.resolve()
        try:
            rel = p.resolve().relative_to(root)
        except ValueError:
            raise UnsafePathError(f"path is outside MEDIA_ROOT: {p}") from None
        return validate_relpath(rel.as_posix())

    def resolve(self, rel: str) -> Path:
        """Относительный путь из БД → абсолютный. Симлинки наружу MEDIA_ROOT отвергаются."""
        clean = validate_relpath(rel)
        root = self.root.resolve()
        full = (root / clean).resolve()
        if full != root and root not in full.parents:
            raise UnsafePathError(f"path escapes MEDIA_ROOT: {rel!r}")
        return full

    def ensure_tree(self) -> None:
        """Создать верхние каталоги раскладки (deploy, `check-config`)."""
        for rel in TOP_LEVEL_DIRS:
            ensure_dir(self.root / rel)

    @contextlib.contextmanager
    def temp_dir(self, prefix: str = "work-") -> Iterator[Path]:
        """Временный каталог в `MEDIA_ROOT/tmp` (тот же диск, что и итог: `rename` атомарен).
        Удаляется при выходе, в том числе по исключению."""
        base = self.tmp_dir(create=True)
        path = Path(tempfile.mkdtemp(prefix=check_segment(prefix, "prefix"), dir=base))
        try:
            yield path
        finally:
            shutil.rmtree(path, ignore_errors=True)


def get_layout() -> MediaLayout:
    """Раскладка по `MEDIA_ROOT` из env (`core.config.get_env()`)."""
    from autoshorts.core.config import get_env

    return MediaLayout(Path(get_env().media_root))


# ---------------------------------------------------------------- файлы


def atomic_write_bytes(path: Path, data: bytes, mode: int = 0o640) -> Path:
    """Запись через временный файл в том же каталоге и `os.replace`: читатель не видит половину."""
    path = Path(path)
    ensure_dir(path.parent)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return path


def atomic_write_text(path: Path, text: str, mode: int = 0o640) -> Path:
    return atomic_write_bytes(path, text.encode("utf-8"), mode)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def tail_lines(path: Path, lines: int = 200, max_bytes: int = 256 * 1024) -> list[str]:
    """Последние `lines` строк текстового файла (читается не больше `max_bytes` с конца)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            start = max(size - max_bytes, 0)
            fh.seek(start)
            data = fh.read()
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace")
    if start > 0:
        text = text.split("\n", 1)[1] if "\n" in text else ""
    out = deque(text.splitlines(), maxlen=max(lines, 0))
    return list(out)


@dataclass(frozen=True)
class PruneStats:
    files: int = 0
    dirs: int = 0
    bytes: int = 0


def prune_older_than(directory: Path, max_age_sec: float, *, remove_dirs: bool = True) -> PruneStats:
    """Удалить файлы (и пустые/устаревшие подкаталоги первого уровня) старше `max_age_sec` по mtime.

    Не переходит по симлинкам. Для каркаса `cleanup` (tmp, логи задач).
    """
    directory = Path(directory)
    if not directory.is_dir():
        return PruneStats()
    cutoff = time.time() - max_age_sec
    files = dirs = size = 0
    with os.scandir(directory) as it:
        for entry in it:
            try:
                st = entry.stat(follow_symlinks=False)
                if st.st_mtime >= cutoff:
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if remove_dirs:
                        shutil.rmtree(entry.path, ignore_errors=True)
                        dirs += 1
                else:
                    size += st.st_size
                    os.unlink(entry.path)
                    files += 1
            except OSError:
                continue
    return PruneStats(files=files, dirs=dirs, bytes=size)
