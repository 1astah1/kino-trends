#!/usr/bin/env python3
"""Download pinned model files into MODELS_DIR with sha256 verification (docs/ARCHITECTURE.md §6.3.2, §8.2).

The manifest (repos, pinned revisions, sizes, sha256) lives in `autoshorts.providers.asr.models`; the
runtime never downloads, it only checks presence. Files are streamed into `<name>.part`, hashed on the
fly and renamed only when the sha256 matches, so an interrupted run never leaves a broken model behind.

Examples:
    python scripts/models_fetch.py                       # asr + vad (GigaAM v3 e2e CTC int8, Silero VAD)
    python scripts/models_fetch.py --groups asr vad faces
    python scripts/models_fetch.py --verify              # only check what is on disk (full sha256)
    MODELS_DIR=/srv/models python scripts/models_fetch.py

Mirrors: HF_ENDPOINT=https://hf-mirror.example overrides https://huggingface.co.
Exit code: 0 all files present and verified, 1 something missing or a checksum mismatch.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from autoshorts.providers.asr.models import (  # noqa: E402
    GROUPS,
    ModelFile,
    files_for,
    models_dir,
    sha256_of,
)

CHUNK = 1 << 20


class ChecksumMismatch(RuntimeError):
    pass


def _fmt_mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB"


def download(mf: ModelFile, dest: Path, *, timeout: float = 60.0, retries: int = 3) -> None:
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    url = mf.resolved_url()
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        h = hashlib.sha256()
        got = 0
        started = time.monotonic()
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=timeout) as resp:
                resp.raise_for_status()
                with open(part, "wb") as fh:
                    last_print = 0.0
                    for block in resp.iter_bytes(CHUNK):
                        fh.write(block)
                        h.update(block)
                        got += len(block)
                        now = time.monotonic()
                        if mf.size > 10 * CHUNK and now - last_print > 5:
                            last_print = now
                            print(f"    {_fmt_mb(got)} / {_fmt_mb(mf.size)}", flush=True)
            digest = h.hexdigest()
            if digest != mf.sha256:
                part.unlink(missing_ok=True)
                raise ChecksumMismatch(f"{mf.rel_path}: sha256 {digest} != pinned {mf.sha256}")
            os.replace(part, dest)
            speed = got / max(time.monotonic() - started, 1e-6)
            print(f"  ok  {mf.rel_path} ({_fmt_mb(got)}, {_fmt_mb(int(speed))}/s)")
            return
        except ChecksumMismatch:
            raise
        except Exception as exc:  # noqa: BLE001 - network errors are retried
            last_exc = exc
            part.unlink(missing_ok=True)
            print(f"  retry {attempt}/{retries} {mf.rel_path}: {exc}", file=sys.stderr)
            time.sleep(2.0 * attempt)
    raise RuntimeError(f"download failed: {mf.rel_path}: {last_exc}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--groups", nargs="+", default=["asr", "vad"], choices=list(GROUPS))
    ap.add_argument("--all", action="store_true", help="all groups (asr, vad, faces)")
    ap.add_argument("--models-dir", type=Path, default=None, help="default: $MODELS_DIR or var/models")
    ap.add_argument("--verify", action="store_true", help="do not download, only verify sha256 on disk")
    ap.add_argument("--force", action="store_true", help="re-download even if the file verifies")
    args = ap.parse_args(argv)

    root = (args.models_dir or models_dir()).resolve()
    groups = list(GROUPS) if args.all else args.groups
    print(f"MODELS_DIR={root} groups={','.join(groups)}")
    failures = 0
    for mf in files_for(groups):
        dest = root / mf.rel_path
        present_ok = dest.is_file() and dest.stat().st_size == mf.size and sha256_of(dest) == mf.sha256
        if present_ok and not args.force:
            print(f"  ok  {mf.rel_path} (verified)")
            continue
        if args.verify:
            state = "missing" if not dest.exists() else "checksum/size mismatch"
            print(f"  BAD {mf.rel_path}: {state}", file=sys.stderr)
            failures += 1
            continue
        print(f"  get {mf.rel_path} <- {mf.resolved_url()}")
        try:
            download(mf, dest)
        except Exception as exc:  # noqa: BLE001
            print(f"  BAD {mf.rel_path}: {exc}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
