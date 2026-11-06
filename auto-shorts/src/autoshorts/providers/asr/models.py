"""Pinned model files for local ASR/VAD/faces (docs/ARCHITECTURE.md §6.3.2, §8.2 `MODELS_DIR`).

The runtime never downloads anything: providers only *check* that the files listed here are present
under `MODELS_DIR` (and, optionally, that their sha256 matches). `scripts/models_fetch.py` is the only
place that downloads them, from pinned revisions, verifying sha256 on the fly.

Layout (§8.2):
    MODELS_DIR/gigaam-v3-e2e-ctc-int8/{config.json, v3_e2e_ctc.int8.onnx, v3_e2e_ctc_vocab.txt}
    MODELS_DIR/silero_vad.onnx
    MODELS_DIR/face_detection_yunet_2026may.onnx          (group "faces", used by providers/faces)

`MODELS_DIR` comes from the env (relative paths are resolved against the project root), default
`<project>/var/models`.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_MODELS_DIR = PROJECT_ROOT / "var" / "models"

HF_ENDPOINT_DEFAULT = "https://huggingface.co"

GIGAAM_REPO = "istupakov/gigaam-v3-onnx"
GIGAAM_REVISION = "322c3b29492673eb7d0b434bfa9dfb8653e34d02"  # 2026-02-18, MIT
SILERO_REPO = "istupakov/silero-vad-onnx"
SILERO_REVISION = "b3e3ee3cce4c11ceb63b1a0b229d916069c1ddf6"  # 2026-03-22, MIT
YUNET_COMMIT = "47534e27c9851bb1128ccc0102f1145e27f23f98"  # opencv/opencv_zoo main @ 2026-09-26, MIT

GIGAAM_DIRNAME = "gigaam-v3-e2e-ctc-int8"


@dataclass(frozen=True, slots=True)
class ModelFile:
    group: str  # "asr" | "vad" | "faces"
    rel_path: str  # path under MODELS_DIR
    url: str  # full URL; "{hf}" is replaced by HF_ENDPOINT
    sha256: str
    size: int  # bytes, used for progress and a cheap sanity check

    def resolved_url(self) -> str:
        return self.url.replace("{hf}", os.environ.get("HF_ENDPOINT", HF_ENDPOINT_DEFAULT).rstrip("/"))


def _hf(repo: str, rev: str, name: str) -> str:
    return f"{{hf}}/{repo}/resolve/{rev}/{name}"


MODEL_FILES: tuple[ModelFile, ...] = (
    ModelFile(
        "asr",
        f"{GIGAAM_DIRNAME}/config.json",
        _hf(GIGAAM_REPO, GIGAAM_REVISION, "config.json"),
        "0641fcf73f4af791c73f05083e38a658ff5dcbee3534a0c61396c10f4b97f0fe",
        135,
    ),
    ModelFile(
        "asr",
        f"{GIGAAM_DIRNAME}/v3_e2e_ctc.int8.onnx",
        _hf(GIGAAM_REPO, GIGAAM_REVISION, "v3_e2e_ctc.int8.onnx"),
        "2e3fcb7a7b66030336fd10c2fcfb033bd1dc7e1bf238fe5cfd83b1d0cfc9d28e",
        224893347,
    ),
    ModelFile(
        "asr",
        f"{GIGAAM_DIRNAME}/v3_e2e_ctc_vocab.txt",
        _hf(GIGAAM_REPO, GIGAAM_REVISION, "v3_e2e_ctc_vocab.txt"),
        "142de7570b3de5b3035ce111a89c228e80e6085273731d944093ddf24fa539cd",
        2007,
    ),
    ModelFile(
        "vad",
        "silero_vad.onnx",
        _hf(SILERO_REPO, SILERO_REVISION, "silero_vad.onnx"),
        "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3",
        2327524,
    ),
    ModelFile(
        "faces",
        "face_detection_yunet_2026may.onnx",
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/"
        f"{YUNET_COMMIT}/models/face_detection_yunet/face_detection_yunet_2026may.onnx",
        "ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0",
        229738,
    ),
)

GROUPS: tuple[str, ...] = ("asr", "vad", "faces")


def models_dir() -> Path:
    """`MODELS_DIR` from env (relative to the project root) or `<project>/var/models`."""
    raw = os.environ.get("MODELS_DIR", "").strip()
    if not raw:
        return DEFAULT_MODELS_DIR
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def files_for(groups: tuple[str, ...] | list[str] | None = None) -> list[ModelFile]:
    wanted = set(groups or GROUPS)
    unknown = wanted - set(GROUPS)
    if unknown:
        raise ValueError(f"unknown model groups: {sorted(unknown)}")
    return [mf for mf in MODEL_FILES if mf.group in wanted]


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def missing_files(
    groups: tuple[str, ...] | list[str], root: Path | None = None, *, check_sha: bool = False
) -> list[str]:
    """Relative paths that are absent (or have the wrong size / sha256) under `root`."""
    base = root or models_dir()
    bad: list[str] = []
    for mf in files_for(groups):
        path = base / mf.rel_path
        if not path.is_file() or path.stat().st_size != mf.size:
            bad.append(mf.rel_path)
        elif check_sha and sha256_of(path) != mf.sha256:
            bad.append(mf.rel_path)
    return bad


__all__ = [
    "DEFAULT_MODELS_DIR",
    "GIGAAM_DIRNAME",
    "GROUPS",
    "MODEL_FILES",
    "ModelFile",
    "files_for",
    "missing_files",
    "models_dir",
    "sha256_of",
]
