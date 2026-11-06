"""Source preparation: ffprobe, sha256, 540p proxy and audio tracks in one decode (ARCHITECTURE.md §6.3.1).

    probe = probe_media(src)
    res = make_proxy(src, out_dir)               # proxy_540p.mp4 + audio.m4a + audio16k.wav
    res = make_proxy(video_540p, out_dir, audio_src=audio_m4a)   # URL mode: separate audio download

One ffmpeg call decodes the source once and writes three outputs:

* `proxy_540p.mp4` — H.264 CRF 28 `veryfast`, short side 540 px (landscape 960x540, vertical 540x960),
  fps capped at 30, closed GOP of 1 s (`-g`/`-keyint_min` = fps, `-sc_threshold 0`) so the studio and
  the storyboard can seek to any second cheaply; AAC 64 kbit/s mono for preview; `+faststart`.
* `audio.m4a` — the source audio (stream copy when it is already AAC, else AAC 128 kbit/s).
* `audio16k.wav` — PCM s16le 16 kHz mono for ASR and peaks (deleted after ASR, §8.2).

Runs through `pipeline.ffmpeg_exec.run_media` (nice 10, `-threads 2`, killpg on timeout/cancel).
Outputs are written under temporary names and renamed only after ffmpeg succeeded.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROXY_NAME = "proxy_540p.mp4"
AUDIO_NAME = "audio.m4a"
AUDIO16K_NAME = "audio16k.wav"

PROXY_SHORT_SIDE = 540
PROXY_CRF = 28
PROXY_PRESET = "veryfast"
PROXY_MAX_FPS = 30.0
DEFAULT_THREADS = 2


class ProbeError(RuntimeError):
    """ffprobe failed or the file has no usable streams."""


@dataclass(slots=True)
class MediaProbe:
    path: str
    duration: float
    file_bytes: int
    format_name: str = ""
    has_video: bool = False
    has_audio: bool = False
    width: int = 0
    height: int = 0
    fps: float = 0.0
    video_codec: str | None = None
    rotation: int = 0
    audio_codec: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    bit_rate: int | None = None
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def display_size(self) -> tuple[int, int]:
        """(w, h) after applying the rotation side data (phones record rotated frames)."""
        if abs(self.rotation) % 180 == 90:
            return self.height, self.width
        return self.width, self.height

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fps(rate: str | None) -> float:
    if not rate or rate in ("0/0", "0"):
        return 0.0
    try:
        num, _, den = rate.partition("/")
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe_media(path: str | Path, *, timeout_sec: float = 120.0) -> MediaProbe:
    """ffprobe JSON -> MediaProbe. Raises ProbeError when the file is not media."""
    path = Path(path)
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"ffprobe timed out: {path.name}") from exc
    if proc.returncode != 0:
        raise ProbeError(f"ffprobe failed for {path.name}: {proc.stderr.strip()[-300:]}")
    data = json.loads(proc.stdout or "{}")
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    v = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
        ),
        None,
    )
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None and a is None:
        raise ProbeError(f"no audio or video streams in {path.name}")
    duration = float(fmt.get("duration") or 0.0)
    if duration <= 0:
        for s in (v, a):
            if s and s.get("duration"):
                duration = max(duration, float(s["duration"]))
    probe = MediaProbe(
        path=str(path),
        duration=round(duration, 3),
        file_bytes=int(fmt.get("size") or path.stat().st_size),
        format_name=str(fmt.get("format_name", "")),
        bit_rate=int(fmt["bit_rate"]) if str(fmt.get("bit_rate", "")).isdigit() else None,
        tags={str(k): str(val) for k, val in (fmt.get("tags") or {}).items()},
    )
    if v is not None:
        probe.has_video = True
        probe.width = int(v.get("width") or 0)
        probe.height = int(v.get("height") or 0)
        probe.fps = round(_fps(v.get("avg_frame_rate")) or _fps(v.get("r_frame_rate")), 3)
        probe.video_codec = v.get("codec_name")
        rot = (v.get("tags") or {}).get("rotate")
        for sd in v.get("side_data_list") or []:
            if "rotation" in sd:
                rot = sd["rotation"]
        try:
            probe.rotation = int(float(rot or 0))
        except ValueError:
            probe.rotation = 0
    if a is not None:
        probe.has_audio = True
        probe.audio_codec = a.get("codec_name")
        probe.sample_rate = int(a["sample_rate"]) if a.get("sample_rate") else None
        probe.channels = int(a["channels"]) if a.get("channels") else None
    return probe


def sha256_file(path: str | Path, chunk: int = 4 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------------- command


def proxy_geometry(probe: MediaProbe, short_side: int = PROXY_SHORT_SIDE) -> tuple[int, int]:
    """Output (w, h): short side = min(short_side, source short side), even numbers, aspect kept."""
    w, h = probe.display_size
    if w <= 0 or h <= 0:
        return 0, 0
    target = min(short_side, min(w, h))
    if w >= h:
        out_h = target
        out_w = int(round(w * target / h / 2.0)) * 2
    else:
        out_w = target
        out_h = int(round(h * target / w / 2.0)) * 2
    return max(2, out_w - out_w % 2), max(2, out_h - out_h % 2)


def proxy_fps(probe: MediaProbe) -> float:
    return min(probe.fps or PROXY_MAX_FPS, PROXY_MAX_FPS)


def build_prepare_cmd(
    src: str | Path,
    out_dir: str | Path,
    probe: MediaProbe,
    *,
    audio_src: str | Path | None = None,
    audio_probe: MediaProbe | None = None,
    short_side: int = PROXY_SHORT_SIDE,
    crf: int = PROXY_CRF,
    preset: str = PROXY_PRESET,
    threads: int = DEFAULT_THREADS,
    suffix: str = ".part",
) -> tuple[list[str], dict[str, Path]]:
    """ffmpeg argv and {kind: temporary output path}. Kinds: proxy, audio, audio16k."""
    out = Path(out_dir)
    outputs: dict[str, Path] = {}
    cmd = ["ffmpeg", "-y", "-threads", str(threads), "-i", str(src)]
    a_input = "0:a:0"
    a_probe = probe
    if audio_src is not None:
        cmd += ["-threads", str(threads), "-i", str(audio_src)]
        a_input = "1:a:0"
        a_probe = audio_probe or probe
    has_audio = a_probe.has_audio if audio_src is None else True

    if probe.has_video:
        w, h = proxy_geometry(probe, short_side)
        fps = proxy_fps(probe)
        gop = max(1, int(round(fps)))
        vf = [f"scale={w}:{h}:flags=bilinear"]
        if probe.fps > PROXY_MAX_FPS + 0.5:
            vf.insert(0, f"fps={PROXY_MAX_FPS:g}")
        vf.append("format=yuv420p")
        proxy_tmp = out / f"{PROXY_NAME}{suffix}.mp4"
        cmd += ["-map", "0:v:0", "-vf", ",".join(vf)]
        cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "high"]
        cmd += ["-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0", "-bf", "2"]
        if has_audio:
            cmd += ["-map", a_input, "-c:a", "aac", "-b:a", "64k", "-ac", "1"]
        cmd += ["-movflags", "+faststart", "-threads", str(threads), str(proxy_tmp)]
        outputs["proxy"] = proxy_tmp

    if has_audio:
        audio_tmp = out / f"{AUDIO_NAME}{suffix}.m4a"
        codec = (
            a_probe.audio_codec if audio_src is None else (audio_probe.audio_codec if audio_probe else None)
        )
        cmd += ["-map", a_input, "-vn"]
        cmd += ["-c:a", "copy"] if codec == "aac" else ["-c:a", "aac", "-b:a", "128k"]
        cmd += ["-movflags", "+faststart", str(audio_tmp)]
        outputs["audio"] = audio_tmp

        wav_tmp = out / f"{AUDIO16K_NAME}{suffix}.wav"
        cmd += ["-map", a_input, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav_tmp)]
        outputs["audio16k"] = wav_tmp
    if not outputs:
        raise ProbeError("nothing to prepare: no video and no audio")
    return cmd, outputs


@dataclass(slots=True)
class ProxyResult:
    probe: MediaProbe
    sha256: str | None
    proxy_path: Path | None
    audio_path: Path | None
    audio16k_path: Path | None
    proxy_size: tuple[int, int]
    proxy_fps: float
    wall_sec: float
    cmd: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe": self.probe.to_dict(),
            "sha256": self.sha256,
            "proxy_path": str(self.proxy_path) if self.proxy_path else None,
            "audio_path": str(self.audio_path) if self.audio_path else None,
            "audio16k_path": str(self.audio16k_path) if self.audio16k_path else None,
            "proxy_size": list(self.proxy_size),
            "proxy_fps": self.proxy_fps,
            "wall_sec": round(self.wall_sec, 2),
        }


def make_proxy(
    src: str | Path,
    out_dir: str | Path,
    *,
    audio_src: str | Path | None = None,
    probe: MediaProbe | None = None,
    compute_sha: bool = True,
    threads: int = DEFAULT_THREADS,
    preset: str = PROXY_PRESET,
    crf: int = PROXY_CRF,
    nice: int = 10,
    timeout_sec: float | None = None,
    progress_cb: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
    log_path: Path | None = None,
) -> ProxyResult:
    """Probe + sha256 + one ffmpeg pass -> proxy/audio/audio16k in `out_dir` (see module docstring)."""
    from autoshorts.pipeline.ffmpeg_exec import run_media

    src = Path(src)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    probe = probe or probe_media(src)
    audio_probe = probe_media(audio_src) if audio_src is not None else None
    digest = sha256_file(src) if compute_sha else None
    cmd, tmp_outputs = build_prepare_cmd(
        src, out, probe, audio_src=audio_src, audio_probe=audio_probe, threads=threads, preset=preset, crf=crf
    )
    duration = probe.duration or (audio_probe.duration if audio_probe else 0.0)
    timeout = timeout_sec if timeout_sec is not None else 300.0 + 1.5 * max(duration, 1.0)
    try:
        res = run_media(
            cmd,
            timeout_sec=timeout,
            threads=threads,
            nice=nice,
            progress_cb=progress_cb,
            cancel=cancel,
            log_path=log_path,
            duration_sec=duration or None,
        )
    except BaseException:
        for p in tmp_outputs.values():
            p.unlink(missing_ok=True)
        raise
    final: dict[str, Path] = {}
    names = {"proxy": PROXY_NAME, "audio": AUDIO_NAME, "audio16k": AUDIO16K_NAME}
    for kind, tmp in tmp_outputs.items():
        dst = out / names[kind]
        tmp.replace(dst)
        final[kind] = dst
    return ProxyResult(
        probe=probe,
        sha256=digest,
        proxy_path=final.get("proxy"),
        audio_path=final.get("audio"),
        audio16k_path=final.get("audio16k"),
        proxy_size=proxy_geometry(probe) if probe.has_video else (0, 0),
        proxy_fps=proxy_fps(probe) if probe.has_video else 0.0,
        wall_sec=res.duration_ms / 1000.0,
        cmd=res.cmd,
    )


def estimate_proxy_bytes(duration_sec: float) -> int:
    """Rough disk estimate for the proxy + audio tracks (for the disk guard): ~0.6 GB per hour."""
    return int(math.ceil(duration_sec / 3600.0 * 0.6e9))


__all__ = [
    "AUDIO16K_NAME",
    "AUDIO_NAME",
    "PROXY_NAME",
    "MediaProbe",
    "ProbeError",
    "ProxyResult",
    "build_prepare_cmd",
    "estimate_proxy_bytes",
    "make_proxy",
    "probe_media",
    "proxy_geometry",
    "sha256_file",
]
