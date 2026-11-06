"""Waveform peaks for the studio (wavesurfer.js v7), ARCHITECTURE.md §6.3.1 `peaks.json`.

    meta = write_peaks("audio16k.wav", "peaks.json")          # 2000 points per minute

Output (loads straight into `WaveSurfer.create({peaks: j.peaks, duration: j.duration, ...})`):

    {"version": 1, "format": "wavesurfer", "duration": 3620.41, "sample_rate": 16000,
     "points_per_min": 2000, "samples_per_point": 480, "length": 120681, "channels": 1,
     "max": 0.93, "peaks": [[0.012, 0.35, ...]]}

Each point is the max |sample| of its bucket (0..1, 3 decimals), i.e. what wavesurfer's
`exportPeaks()` produces for one channel. The audio is streamed in blocks (flat memory for hours of
audio): a PCM WAV is read with soundfile, anything else is decoded by ffmpeg to 16 kHz mono s16le
through a pipe (nice 10, 2 threads).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

POINTS_PER_MIN = 2000
DEFAULT_SR = 16_000
BLOCK_POINTS = 4096  # buckets per read block


def _buckets_max(x: np.ndarray, spp: int) -> np.ndarray:
    n = x.size // spp
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    return np.abs(x[: n * spp].reshape(n, spp)).max(axis=1)


def _iter_blocks_wav(path: Path, block_samples: int):
    import soundfile as sf

    with sf.SoundFile(str(path)) as fh:
        sr = fh.samplerate
        yield sr
        while True:
            data = fh.read(block_samples, dtype="float32", always_2d=True)
            if data.size == 0:
                break
            yield data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]


def _iter_blocks_ffmpeg(path: Path, block_samples: int, sr: int):
    cmd = ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-threads", "2",
           "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-threads", "2",
           "pipe:1"]  # fmt: skip
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    try:
        yield sr
        need = block_samples * 2
        while True:
            raw = proc.stdout.read(need)
            if not raw:
                break
            if len(raw) % 2:
                raw = raw[:-1]
            yield np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    finally:
        proc.stdout.close()
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"ffmpeg failed while reading {path.name}: {err.strip()[-300:]}")


def compute_peaks(
    audio_path: str | Path, *, points_per_min: int = POINTS_PER_MIN, precision: int = 3
) -> dict[str, Any]:
    path = Path(audio_path)
    is_wav = path.suffix.lower() == ".wav"
    probe_sr = DEFAULT_SR
    if is_wav:
        import soundfile as sf

        probe_sr = sf.info(str(path)).samplerate
    spp = max(1, int(round(probe_sr * 60.0 / points_per_min)))
    block = spp * BLOCK_POINTS
    gen = _iter_blocks_wav(path, block) if is_wav else _iter_blocks_ffmpeg(path, block, DEFAULT_SR)
    sr = next(gen)
    parts: list[np.ndarray] = []
    carry = np.zeros(0, dtype=np.float32)
    total = 0
    for chunk in gen:
        total += chunk.size
        x = np.concatenate([carry, chunk]) if carry.size else chunk
        full = (x.size // spp) * spp
        parts.append(_buckets_max(x[:full], spp))
        carry = x[full:]
    if carry.size:
        parts.append(np.array([np.abs(carry).max()], dtype=np.float32))
    peaks = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
    peaks = np.clip(peaks, 0.0, 1.0)
    return {
        "version": 1,
        "format": "wavesurfer",
        "duration": round(total / float(sr), 3),
        "sample_rate": int(sr),
        "points_per_min": int(points_per_min),
        "samples_per_point": spp,
        "length": int(peaks.size),
        "channels": 1,
        "max": round(float(peaks.max()) if peaks.size else 0.0, precision),
        "peaks": [np.round(peaks, precision).tolist()],
    }


def write_peaks(audio_path: str | Path, out_path: str | Path, **kwargs: Any) -> dict[str, Any]:
    """Compute peaks and write them atomically; returns the metadata without the peaks array."""
    data = compute_peaks(audio_path, **kwargs)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    meta = {k: v for k, v in data.items() if k != "peaks"}
    meta["path"] = str(out)
    return meta


__all__ = ["POINTS_PER_MIN", "compute_peaks", "write_peaks"]
