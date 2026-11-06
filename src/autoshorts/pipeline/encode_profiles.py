"""Профили кодирования финального ролика (research §7.3, §7.5; YouTube answer 1722171).

CRF 23 давал на малодинамичном ролике ~81 кбит/с, после перекодирования площадкой мелкий текст
баннера размывался. Поэтому финал: libx264 CRF 19–20, `-maxrate 12M -bufsize 24M`, закрытый GOP
в половину fps (`-g 15` при 30 fps) и 2 B-кадра, yuv420p, `+faststart`, AAC 192k 48 кГц.
NVENC — опционально, если есть GPU (`h264_nvenc -preset p5 -tune hq -rc vbr -cq 21`).

    prof = final_profile()                    # EncodeProfile
    args = prof.output_args(fps=30)           # ["-c:v", "libx264", ...] — вставлять перед выходом

Глобальные опции (`-hide_banner`, `-filter_threads`) и `nice` здесь не добавляются: их ставит
запускающий (`probe.run_media_cmd`, в M2 — `ffmpeg_exec.run_media`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from enum import StrEnum

from autoshorts.pipeline.probe import ffmpeg_threads

DEFAULT_CRF = 20
MIN_FINAL_CRF = 19
MAX_FINAL_CRF = 20
DEFAULT_MAXRATE = "12M"
DEFAULT_BUFSIZE = "24M"
NVENC_ENV = "AUTOSHORTS_USE_NVENC"


class VideoEncoder(StrEnum):
    LIBX264 = "libx264"
    H264_NVENC = "h264_nvenc"


@dataclass(frozen=True)
class EncodeProfile:
    name: str
    encoder: VideoEncoder = VideoEncoder.LIBX264
    preset: str = "veryfast"
    crf: int = DEFAULT_CRF
    nvenc_cq: int = 21
    maxrate: str | None = DEFAULT_MAXRATE
    bufsize: str | None = DEFAULT_BUFSIZE
    keyint_sec: float = 0.5  # YouTube: закрытый GOP в половину частоты кадров
    bframes: int = 2
    pix_fmt: str = "yuv420p"
    profile: str = "high"
    tag_bt709: bool = True
    faststart: bool = True
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    audio_rate: int = 48000
    audio_channels: int = 2
    threads: int | None = None  # None → потолок из env AUTOSHORTS_FFMPEG_THREADS

    def keyint(self, fps: float) -> int:
        return max(1, round(max(fps, 1.0) * self.keyint_sec))

    def video_args(self, *, fps: float = 30.0) -> list[str]:
        g = self.keyint(fps)
        if self.encoder == VideoEncoder.H264_NVENC:
            args = [
                "-c:v",
                "h264_nvenc",
                "-preset",
                "p5",
                "-tune",
                "hq",
                "-rc",
                "vbr",
                "-cq",
                str(self.nvenc_cq),
                "-b:v",
                "0",
            ]
        else:
            args = ["-c:v", "libx264", "-preset", self.preset, "-crf", str(self.crf)]
        if self.maxrate:
            args += ["-maxrate", self.maxrate]
        if self.bufsize:
            args += ["-bufsize", self.bufsize]
        args += ["-g", str(g), "-keyint_min", str(g), "-bf", str(self.bframes)]
        if self.encoder == VideoEncoder.LIBX264:
            args += ["-sc_threshold", "0"]  # ровная сетка ключевых кадров (стабильный dHash и перемотка)
        args += ["-pix_fmt", self.pix_fmt, "-profile:v", self.profile]
        if self.tag_bt709:
            args += [
                "-colorspace",
                "bt709",
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-color_range",
                "tv",
            ]
        if self.encoder == VideoEncoder.LIBX264:
            args += ["-threads", str(self.threads or ffmpeg_threads())]
        return args

    def audio_args(self) -> list[str]:
        return [
            "-c:a",
            self.audio_codec,
            "-b:a",
            self.audio_bitrate,
            "-ar",
            str(self.audio_rate),
            "-ac",
            str(self.audio_channels),
        ]

    def container_args(self) -> list[str]:
        return ["-movflags", "+faststart"] if self.faststart else []

    def output_args(self, *, fps: float = 30.0, with_audio: bool = True) -> list[str]:
        args = self.video_args(fps=fps)
        args += self.audio_args() if with_audio else ["-an"]
        return args + self.container_args()


def nvenc_requested() -> bool:
    return os.environ.get(NVENC_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def final_profile(
    *, crf: int = DEFAULT_CRF, preset: str = "veryfast", use_nvenc: bool | None = None
) -> EncodeProfile:
    """Финал для публикации. CRF зажимается в 19–20 (research §7.3); NVENC — по env/флагу."""
    crf = max(MIN_FINAL_CRF, min(MAX_FINAL_CRF, int(crf)))
    nv = nvenc_requested() if use_nvenc is None else use_nvenc
    return EncodeProfile(
        name="final",
        encoder=VideoEncoder.H264_NVENC if nv else VideoEncoder.LIBX264,
        preset=preset,
        crf=crf,
    )


def preview_profile() -> EncodeProfile:
    """Превью для приёмки (Telegram ≤ 50 МБ): дёшево, без VBV-потолка финала."""
    return EncodeProfile(
        name="preview",
        preset="veryfast",
        crf=28,
        maxrate="4M",
        bufsize="8M",
        keyint_sec=1.0,
        audio_bitrate="96k",
    )


def with_threads(profile: EncodeProfile, threads: int) -> EncodeProfile:
    return replace(profile, threads=max(1, min(8, int(threads))))
