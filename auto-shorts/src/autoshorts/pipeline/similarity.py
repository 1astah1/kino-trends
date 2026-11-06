"""Похожесть на собственные ролики: dHash 8 ключевых кадров (QC `library_similarity`, §6.2, §6.3.9).

Назначение — самопроверка своей библиотеки: не выпустить почти-дубль собственного ролика
(одинаковые картинки, тот же фон, повтор шаблона). Модуль ничего не меняет в видео и не
предназначен для обхода чужих систем распознавания контента.

    fp = fingerprint_video(Path("final.mp4"))         # VideoFingerprint (8 × 64 бит)
    fp.to_hex()                                       # → jobs.fingerprint (JSONB)
    sim = similarity(fp, other_fp)                    # 1.0 — одинаковые, 0.5 — случайные
    hits = find_similar(fp, library, threshold=0.90)  # [(id, sim)], по убыванию

Кадры берутся в моменты (i + 0.5)/8 длительности: из ключевых кадров (`-skip_frame nokey`,
декодируется только ~2 кадра в секунду при GOP 0,5 с) ближайший к каждому моменту. Если
ключевых кадров мало (длинный GOP), видео декодируется целиком с `fps=8/D` (дёшево на 9×8).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from autoshorts.pipeline.probe import probe_media, run_media_cmd

N_FRAMES = 8
HASH_W, HASH_H = 9, 8
FRAME_BYTES = HASH_W * HASH_H
METHOD = "dhash8x8_keyframes_v1"
DEFAULT_THRESHOLD = 0.90  # RuntimeSettings.similarity_warn_threshold
_PTS_RE = re.compile(r"pts_time:\s*(-?[\d.]+)")


def dhash_bytes(gray: bytes, width: int = HASH_W, height: int = HASH_H) -> int:
    """dHash по серому кадру 9×8 (построчно): бит = пиксель левее ярче соседа справа."""
    if len(gray) < width * height:
        raise ValueError("not enough pixels for dhash")
    bits = 0
    for y in range(height):
        row = gray[y * width : (y + 1) * width]
        for x in range(width - 1):
            bits = (bits << 1) | (1 if row[x] > row[x + 1] else 0)
    return bits


def dhash_image(img: Image.Image) -> int:
    small = img.convert("L").resize((HASH_W, HASH_H), Image.Resampling.BOX)
    return dhash_bytes(small.tobytes())


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


@dataclass(frozen=True)
class VideoFingerprint:
    hashes: tuple[int, ...]
    duration_sec: float = 0.0
    method: str = METHOD

    def to_hex(self) -> list[str]:
        return [f"{h:016x}" for h in self.hashes]

    def to_json(self) -> dict[str, object]:
        return {"method": self.method, "duration_sec": round(self.duration_sec, 3), "dhash": self.to_hex()}

    @classmethod
    def from_hex(
        cls, values: Sequence[str], duration_sec: float = 0.0, method: str = METHOD
    ) -> VideoFingerprint:
        return cls(tuple(int(v, 16) for v in values), duration_sec, method)

    @classmethod
    def from_json(cls, data: dict[str, object]) -> VideoFingerprint:
        return cls.from_hex(
            list(data.get("dhash") or []),  # type: ignore[arg-type]
            float(data.get("duration_sec") or 0.0),  # type: ignore[arg-type]
            str(data.get("method") or METHOD),
        )


def mean_hamming(a: VideoFingerprint, b: VideoFingerprint) -> float:
    n = min(len(a.hashes), len(b.hashes))
    if n == 0:
        return 64.0
    return sum(hamming(x, y) for x, y in zip(a.hashes[:n], b.hashes[:n], strict=True)) / n


def similarity(a: VideoFingerprint, b: VideoFingerprint) -> float:
    """1 − средняя дистанция Хэмминга / 64. Разные методы не сравниваются (→ 0)."""
    if a.method != b.method:
        return 0.0
    return round(1.0 - mean_hamming(a, b) / 64.0, 4)


def find_similar(
    fp: VideoFingerprint,
    library: Iterable[tuple[object, VideoFingerprint]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    limit: int = 10,
) -> list[tuple[object, float]]:
    hits = [(key, similarity(fp, other)) for key, other in library]
    hits = [h for h in hits if h[1] >= threshold]
    hits.sort(key=lambda h: h[1], reverse=True)
    return hits[:limit]


def _targets(duration: float, n: int) -> list[float]:
    return [(i + 0.5) * duration / n for i in range(n)]


def _read_frames(stdout: bytes) -> list[bytes]:
    return [stdout[i : i + FRAME_BYTES] for i in range(0, len(stdout) - FRAME_BYTES + 1, FRAME_BYTES)]


def _keyframes(path: Path, timeout_sec: float) -> tuple[list[float], list[bytes]]:
    cmd = [
        "ffmpeg",
        "-skip_frame",
        "nokey",
        "-i",
        str(path),
        "-an",
        "-sn",
        "-vf",
        f"scale={HASH_W}:{HASH_H}:flags=area,format=gray,showinfo",
        "-fps_mode",
        "passthrough",
        "-loglevel",
        "info",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    res = run_media_cmd(cmd, timeout_sec=timeout_sec, capture_stdout=True)
    frames = _read_frames(res.stdout)
    pts = [float(m.group(1)) for m in _PTS_RE.finditer(res.stderr.decode("utf-8", "replace"))]
    n = min(len(frames), len(pts))
    return pts[:n], frames[:n]


def _uniform_frames(path: Path, duration: float, n: int, timeout_sec: float) -> list[bytes]:
    rate = n / max(duration, 1e-3)
    cmd = [
        "ffmpeg",
        "-i",
        str(path),
        "-an",
        "-sn",
        "-vf",
        f"fps={rate:.6f}:start_time=0,scale={HASH_W}:{HASH_H}:flags=area,format=gray",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    res = run_media_cmd(cmd, timeout_sec=timeout_sec, capture_stdout=True)
    return _read_frames(res.stdout)


def fingerprint_video(path: Path, *, n: int = N_FRAMES, timeout_sec: float = 120.0) -> VideoFingerprint:
    """dHash n кадров, равномерно по длительности (из ключевых, при нехватке — из всех)."""
    duration = probe_media(path).duration_sec
    if duration <= 0:
        raise ValueError(f"video has no duration: {path}")
    targets = _targets(duration, n)
    pts, frames = _keyframes(path, timeout_sec)
    chosen: list[bytes] = []
    if pts:
        picks = [min(range(len(pts)), key=lambda i, t=t: abs(pts[i] - t)) for t in targets]
        if len(set(picks)) >= max(2, n // 2):
            chosen = [frames[i] for i in picks]
    if not chosen:
        frames = _uniform_frames(path, duration, n, timeout_sec)
        if not frames:
            raise ValueError(f"no frames decoded: {path}")
        idx = [min(len(frames) - 1, round((i + 0.5) * len(frames) / n - 0.5)) for i in range(n)]
        chosen = [frames[i] for i in idx]
    return VideoFingerprint(tuple(dhash_bytes(f) for f in chosen), duration)
