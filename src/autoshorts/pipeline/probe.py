"""ffprobe JSON и щадящий запуск медиа-подпроцессов для баннерного слоя.

    info = probe_media(path)            # MediaInfo: длительность, размер, fps, pix_fmt, альфа, аудио
    run_media_cmd(["ffmpeg", "-i", src, ..., out], timeout_sec=120)

`run_media_cmd` — временная локальная точка запуска: `nice -n 10`, своя группа процессов
(таймаут убивает всю группу), `-threads`/`-filter_threads 1` для ffmpeg, короткий хвост stderr в
ошибке. TODO(M2): перейти на `pipeline/ffmpeg_exec.run_media` (единая точка §5.5) — его сейчас
меняет поток M1-C, поэтому сюда он не импортируется. Команды, которые строят `banner_overlay` и
`encode_profiles`, не содержат глобальных опций и подходят для `run_media` без правок.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

DEFAULT_NICE = 10
DEFAULT_THREADS = 2
STDERR_TAIL_CHARS = 3000
THREADS_ENV = "AUTOSHORTS_FFMPEG_THREADS"
FFMPEG_GLOBAL_OPTS: tuple[str, ...] = (
    "-hide_banner",
    "-nostdin",
    "-nostats",
    "-loglevel",
    "error",
    "-filter_threads",
    "1",
    "-filter_complex_threads",
    "1",
)
ALPHA_PIX_FMTS_HINT = ("yuva", "rgba", "bgra", "argb", "abgr", "ya8", "ya16", "gbrap", "pal8")


class MediaCommandError(RuntimeError):
    """Подпроцесс завершился с ошибкой или по таймауту."""

    def __init__(self, message: str, *, returncode: int | None, stderr_tail: str, cmd: Sequence[str]):
        super().__init__(f"{message}: {stderr_tail.strip()[-600:]}")
        self.returncode = returncode
        self.stderr_tail = stderr_tail
        self.cmd = list(cmd)


class ProbeError(MediaCommandError):
    """ffprobe не смог прочитать файл (битый или не медиа)."""


def ffmpeg_threads(default: int = DEFAULT_THREADS) -> int:
    """Потолок потоков ffmpeg из env `AUTOSHORTS_FFMPEG_THREADS` (1..8).

    TODO(M2): брать из `RuntimeSettings.ffmpeg_threads` (настройка в БД), env — запасной путь.
    """
    raw = os.environ.get(THREADS_ENV, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(1, min(8, value))


def _heavy_env(threads: int, base: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env.update(
        {
            "OMP_NUM_THREADS": str(threads),
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMBA_NUM_THREADS": "1",
        }
    )
    return env


def build_niced_cmd(cmd: Sequence[str], *, nice: int = DEFAULT_NICE, threads: int | None = None) -> list[str]:
    """`nice -n N` + глобальные опции ffmpeg; `-threads N` перед выходом, если своего нет."""
    cmd = [str(c) for c in cmd]
    if not cmd:
        raise ValueError("empty command")
    binary = os.path.basename(cmd[0])
    if binary == "ffmpeg":
        threads = threads or ffmpeg_threads()
        rest = cmd[1:]
        if "-threads" not in rest and rest:
            rest = [*rest[:-1], "-threads", str(threads), rest[-1]]
        cmd = [cmd[0], *FFMPEG_GLOBAL_OPTS, *rest]
    return ["nice", "-n", str(int(nice)), *cmd]


def _kill_group(proc: subprocess.Popen[Any]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)


def run_media_cmd(
    cmd: Sequence[str],
    *,
    timeout_sec: float = 600.0,
    nice: int = DEFAULT_NICE,
    threads: int | None = None,
    capture_stdout: bool = False,
    input_bytes: bytes | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Запустить ffmpeg/ffprobe щадяще для общего бокса. Ненулевой код → MediaCommandError."""
    full = build_niced_cmd(cmd, nice=nice, threads=threads)
    proc = subprocess.Popen(
        full,
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
        env=_heavy_env(threads or ffmpeg_threads()),
        cwd=str(cwd) if cwd else None,
    )
    try:
        out, err = proc.communicate(input=input_bytes, timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        out, err = proc.communicate()
        raise MediaCommandError(
            f"timeout after {timeout_sec:.0f}s",
            returncode=None,
            stderr_tail=(err or b"").decode("utf-8", "replace")[-STDERR_TAIL_CHARS:],
            cmd=full,
        ) from None
    except BaseException:
        _kill_group(proc)
        proc.wait()
        raise
    finally:
        # добить отставших детей группы (ffmpeg сам не плодит, но nice/обёртки — могут)
        if proc.returncode is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, 0)
                _kill_group(proc)
    tail = (err or b"").decode("utf-8", "replace")[-STDERR_TAIL_CHARS:]
    if proc.returncode != 0:
        raise MediaCommandError(
            f"{os.path.basename(str(cmd[0]))} exited with {proc.returncode}",
            returncode=proc.returncode,
            stderr_tail=tail,
            cmd=full,
        )
    return subprocess.CompletedProcess(full, proc.returncode, out or b"", (err or b""))


def open_media_process(
    cmd: Sequence[str], *, nice: int = DEFAULT_NICE, threads: int | None = None
) -> subprocess.Popen[bytes]:
    """Popen со stdin-трубой (сырые кадры из Pillow в ffmpeg). Вызывающий закрывает stdin и ждёт."""
    full = build_niced_cmd(cmd, nice=nice, threads=threads)
    return subprocess.Popen(
        full,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
        env=_heavy_env(threads or ffmpeg_threads()),
    )


def kill_media_process(proc: subprocess.Popen[Any]) -> None:
    _kill_group(proc)
    with contextlib.suppress(Exception):
        proc.wait(timeout=5)


# ---------------------------------------------------------------- ffprobe


def _ratio(value: str | None) -> float:
    if not value or value in {"0/0", "N/A"}:
        return 0.0
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return 0.0


def _float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN → None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class VideoStreamInfo:
    index: int
    codec: str
    width: int
    height: int
    fps: float
    avg_fps: float
    pix_fmt: str
    has_alpha: bool
    duration_sec: float | None
    nb_frames: int | None
    rotation: int
    sar: str | None
    color_space: str | None
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def display_size(self) -> tuple[int, int]:
        """Размер с учётом поворота из displaymatrix (телефонные MP4)."""
        if abs(self.rotation) % 180 == 90:
            return self.height, self.width
        return self.width, self.height


@dataclass(frozen=True)
class AudioStreamInfo:
    index: int
    codec: str
    sample_rate: int
    channels: int
    channel_layout: str | None
    bit_rate: int | None
    duration_sec: float | None


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    format_name: str
    duration_sec: float
    size_bytes: int
    bit_rate: int | None
    video: VideoStreamInfo | None
    audio: AudioStreamInfo | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def width(self) -> int:
        return self.video.width if self.video else 0

    @property
    def height(self) -> int:
        return self.video.height if self.video else 0

    @property
    def fps(self) -> float:
        return self.video.fps if self.video else 0.0

    @property
    def has_alpha(self) -> bool:
        return bool(self.video and self.video.has_alpha)


def _rotation(stream: dict[str, Any]) -> int:
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            return int(_float(sd.get("rotation")) or 0)
    rot = (stream.get("tags") or {}).get("rotate")
    return int(_float(rot) or 0)


def _stream_has_alpha(stream: dict[str, Any]) -> bool:
    tags = {str(k).lower(): str(v) for k, v in (stream.get("tags") or {}).items()}
    if tags.get("alpha_mode") == "1":
        return True
    pix = str(stream.get("pix_fmt") or "")
    if pix == "pal8":  # GIF/PNG-палитра: прозрачность возможна, решает banner_assets по байтам
        return False
    return any(pix.startswith(p) for p in ALPHA_PIX_FMTS_HINT)


def parse_ffprobe_json(data: dict[str, Any], path: Path | None = None) -> MediaInfo:
    """Разбор вывода `ffprobe -show_format -show_streams -of json` (отдельно — для тестов)."""
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video = audio = None
    for s in streams:
        ctype = s.get("codec_type")
        disposition = s.get("disposition") or {}
        if ctype == "video" and video is None and not disposition.get("attached_pic"):
            video = VideoStreamInfo(
                index=int(s.get("index", 0)),
                codec=str(s.get("codec_name") or ""),
                width=int(s.get("width") or 0),
                height=int(s.get("height") or 0),
                fps=_ratio(s.get("r_frame_rate")),
                avg_fps=_ratio(s.get("avg_frame_rate")),
                pix_fmt=str(s.get("pix_fmt") or ""),
                has_alpha=_stream_has_alpha(s),
                duration_sec=_float(s.get("duration")),
                nb_frames=_int(s.get("nb_frames")),
                rotation=_rotation(s),
                sar=s.get("sample_aspect_ratio"),
                color_space=s.get("color_space"),
                tags={str(k): str(v) for k, v in (s.get("tags") or {}).items()},
            )
        elif ctype == "audio" and audio is None:
            audio = AudioStreamInfo(
                index=int(s.get("index", 0)),
                codec=str(s.get("codec_name") or ""),
                sample_rate=int(_int(s.get("sample_rate")) or 0),
                channels=int(s.get("channels") or 0),
                channel_layout=s.get("channel_layout"),
                bit_rate=_int(s.get("bit_rate")),
                duration_sec=_float(s.get("duration")),
            )
    duration = _float(fmt.get("duration"))
    if duration is None and video and video.duration_sec:
        duration = video.duration_sec
    if duration is None and video is not None:
        # Matroska/WebM: длительность дорожки лежит в теге DURATION «00:00:01.000000000»
        tag = video.tags.get("DURATION") or video.tags.get("duration")
        if tag:
            h, m, s = tag.split(":")
            duration = int(h) * 3600 + int(m) * 60 + float(s)
    return MediaInfo(
        path=Path(path) if path else Path(str(fmt.get("filename", ""))),
        format_name=str(fmt.get("format_name") or ""),
        duration_sec=float(duration or 0.0),
        size_bytes=int(_int(fmt.get("size")) or 0),
        bit_rate=_int(fmt.get("bit_rate")),
        video=video,
        audio=audio,
        raw=data,
    )


def probe_media(path: Path, *, timeout_sec: float = 30.0, count_frames: bool = False) -> MediaInfo:
    """ffprobe → MediaInfo. Битый/не медиа файл → ProbeError."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
    ]
    if count_frames:
        cmd.append("-count_packets")
    cmd.append(str(path))
    try:
        res = run_media_cmd(cmd, timeout_sec=timeout_sec, capture_stdout=True)
    except MediaCommandError as exc:
        raise ProbeError(
            "ffprobe failed", returncode=exc.returncode, stderr_tail=exc.stderr_tail, cmd=exc.cmd
        ) from exc
    try:
        data = json.loads(res.stdout.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError(
            "ffprobe returned invalid json", returncode=0, stderr_tail=str(exc), cmd=cmd
        ) from exc
    info = parse_ffprobe_json(data, path)
    if count_frames and info.video is not None and info.video.nb_frames is None:
        for s in data.get("streams") or []:
            if s.get("index") == info.video.index and s.get("nb_read_packets"):
                video = dataclasses.replace(info.video, nb_frames=_int(s.get("nb_read_packets")))
                info = dataclasses.replace(info, video=video)
    return info


def probe_duration_sec(path: Path) -> float:
    return probe_media(path).duration_sec


def extract_frame_rgba(
    path: Path,
    t_sec: float,
    *,
    decoder: str | None = None,
    size: tuple[int, int] | None = None,
    timeout_sec: float = 60.0,
) -> tuple[int, int, bytes]:
    """Один кадр в RGBA (w, h, bytes). `decoder="libvpx-vp9"` — для WebM с альфой."""
    info = probe_media(path)
    if info.video is None:
        raise ProbeError("no video stream", returncode=0, stderr_tail="", cmd=[str(path)])
    w, h = size or (info.video.width, info.video.height)
    cmd = ["ffmpeg", "-ss", f"{max(0.0, t_sec):.3f}"]
    if decoder:
        cmd += ["-c:v", decoder]
    cmd += ["-i", str(path), "-frames:v", "1", "-an"]
    if size:
        cmd += ["-vf", f"scale={w}:{h}:flags=area"]
    cmd += ["-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"]
    res = run_media_cmd(cmd, timeout_sec=timeout_sec, capture_stdout=True)
    expected = w * h * 4
    if len(res.stdout) < expected:
        raise ProbeError(
            f"short frame read {len(res.stdout)} < {expected}", returncode=0, stderr_tail="", cmd=cmd
        )
    return w, h, res.stdout[:expected]
