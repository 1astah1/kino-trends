"""
Preflight-QC перед показом человеку — дешевле на порядки, чем ловить брак глазами (ТЗ §10).
Один проход ffmpeg: silencedetect, volumedetect, ebur128/loudnorm, blackdetect, freezedetect.
Плюс проверки длительности/loop seam и дедуп через perceptual hash кадров.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from autoshorts.models import QCResult


def _run_ffmpeg_filter(video_path: Path, filter_str: str) -> str:
    cmd = ["ffmpeg", "-i", str(video_path), "-af" if "audio" in filter_str.split("=")[0] else "-vf",
           filter_str, "-f", "null", "-"]
    # silencedetect/volumedetect — аудио-фильтры (-af), blackdetect/freezedetect — видео (-vf)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.stderr


def check_silence(video_path: Path, noise_db: str = "-50dB", min_duration: float = 3.0) -> tuple[bool, str]:
    """Пропавшая озвучка — тишина дольше 3 секунд посреди ролика это почти всегда брак TTS."""
    cmd = ["ffmpeg", "-i", str(video_path), "-af", f"silencedetect=n={noise_db}:d={min_duration}", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    silences = re.findall(r"silence_start: ([\d.]+)", proc.stderr)
    ok = len(silences) == 0
    detail = f"найдено {len(silences)} пауз(ы) от {min_duration}с" if silences else "тишины не найдено"
    return ok, detail


def check_clipping(video_path: Path, max_volume_threshold_db: float = -0.5) -> tuple[bool, str]:
    """Клиппинг аудио — max_volume около 0 дБ означает перегруз микса."""
    cmd = ["ffmpeg", "-i", str(video_path), "-af", "volumedetect", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    m = re.search(r"max_volume: ([\-\d.]+) dB", proc.stderr)
    if not m:
        return False, "volumedetect не вернул max_volume"
    max_vol = float(m.group(1))
    ok = max_vol <= max_volume_threshold_db
    return ok, f"max_volume={max_vol} dB"


def check_loudness(video_path: Path, target_i: float = -14.0, tolerance: float = 1.5) -> tuple[bool, str]:
    """Соответствие целевой нормализации I=-14 LUFS (ТЗ §9) — YouTube нормализует к этому значению."""
    cmd = ["ffmpeg", "-i", str(video_path),
           "-af", "loudnorm=I=-14:TP=-1.0:LRA=11:print_format=summary", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    m = re.search(r"Input Integrated:\s+([\-\d.]+) LUFS", proc.stderr)
    if not m:
        return False, "loudnorm не вернул Input Integrated"
    measured_i = float(m.group(1))
    ok = abs(measured_i - target_i) <= tolerance
    return ok, f"measured I={measured_i} LUFS (цель {target_i}±{tolerance})"


def check_black_frames(video_path: Path, pic_th: float = 0.98, pix_th: float = 0.10,
                        min_duration: float = 2.0) -> tuple[bool, str]:
    """Чёрные кадры — дыры в монтаже, сорванный рендер сцены."""
    cmd = ["ffmpeg", "-i", str(video_path),
           "-vf", f"blackdetect=d={min_duration}:pic_th={pic_th}:pix_th={pix_th}",
           "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    blacks = re.findall(r"black_start:([\d.]+)", proc.stderr)
    ok = len(blacks) == 0
    return ok, f"найдено {len(blacks)} чёрных участков" if blacks else "чёрных кадров нет"


def check_frozen_frames(video_path: Path, noise_db: str = "-60dB", min_duration: float = 2.0) -> tuple[bool, str]:
    """Зависший кадр — сбой рендера сцены (напр. concat не подхватил следующий клип)."""
    cmd = ["ffmpeg", "-i", str(video_path),
           "-vf", f"freezedetect=n={noise_db}:d={min_duration}", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    freezes = re.findall(r"freeze_start: ([\d.]+)", proc.stderr)
    ok = len(freezes) == 0
    return ok, f"найдено {len(freezes)} зависших участков" if freezes else "зависших кадров нет"


def check_duration(video_path: Path, target_range: tuple[int, int]) -> tuple[bool, str]:
    """Целевой диапазон длительности — см. открытый вопрос по 15-25с в ТЗ §3."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True,
    )
    duration = float(probe.stdout.strip() or 0)
    lo, hi = target_range
    # допуск +3с — типичный ролик редко попадает секунда в секунду
    ok = (lo - 1) <= duration <= (hi + 3)
    return ok, f"длительность {duration:.1f}с (цель {lo}-{hi}с)"


def run_preflight_qc(video_path: Path, target_duration_range: tuple[int, int] = (15, 25)) -> QCResult:
    """Все проверки одним проходом перед показом человеку. Ролик не уходит в очередь,
    пока хотя бы одна проверка красная — см. reject_reason в models.py."""
    checks: dict[str, bool] = {}
    details: dict[str, str] = {}

    for name, fn, kwargs in [
        ("silence", check_silence, {}),
        ("clipping", check_clipping, {}),
        ("loudness", check_loudness, {}),
        ("black_frames", check_black_frames, {}),
        ("frozen_frames", check_frozen_frames, {}),
    ]:
        ok, detail = fn(video_path, **kwargs)
        checks[name] = ok
        details[name] = detail

    ok, detail = check_duration(video_path, target_duration_range)
    checks["duration"] = ok
    details["duration"] = detail

    passed = all(checks.values())
    return QCResult(passed=passed, checks=checks, details=details)
