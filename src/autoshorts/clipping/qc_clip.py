"""QC клипа одним проходом ffmpeg, профиль `clipping` (docs/ARCHITECTURE.md §6.3.9).

Один декод вместо шести (legacy `pipeline/qc.py` запускает ffmpeg на каждую проверку):

    -filter_complex "[0:a]silencedetect,volumedetect,ebur128=peak=true[a];[0:v]blackdetect,freezedetect[v]"

stderr пишется в лог целиком (ebur128 с `framelog=verbose` не засоряет его построчными замерами)
и разбирается регулярками. Профиль `clipping`:

| проверка | порог |
|---|---|
| duration | 20–58 с (VK жёстко ≤ 60) |
| silence | тишина ≥ 4,0 с (−50 дБ) — провал |
| loudness | I = −14 ± 1,5 LUFS |
| clipping | true peak ≤ −0,5 dBTP (цель −1) |
| black_frames | чёрное ≥ 2 с |
| frozen_frames | зависание ≥ 2 с после вычитания окон `expected_static` (freeze-вставки EDL) |

Дополнительно, если переданы: `author_share` (гейт §6.3.8), `foreign_watermark`,
`library_similarity` — их считает `originality.py`, здесь только результат в общем списке.
Коды проверок — словарь `qc_check` (§4.3).
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoshorts.clipping import intervals as iv

SILENCE_DB = -50.0
SILENCE_MIN_SEC = 4.0
BLACK_MIN_SEC = 2.0
FREEZE_NOISE_DB = -60.0
FREEZE_MIN_SEC = 2.0
TARGET_I = -14.0
LOUDNESS_TOL = 1.5
MAX_TRUE_PEAK = -0.5
DURATION_RANGE = (20.0, 58.0)
DURATION_HARD_MAX = 60.0
STATIC_TOLERANCE = 0.5  # запас вокруг expected_static (freezedetect ловит с задержкой)


@dataclass(slots=True)
class QCCheck:
    code: str
    ok: bool
    measured: str
    threshold: str
    detail: str = ""
    severity: str = "fail"  # fail | warn

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "ok": self.ok,
            "measured": self.measured,
            "threshold": self.threshold,
            "detail": self.detail,
            "severity": self.severity,
        }


@dataclass(slots=True)
class QCReport:
    passed: bool
    checks: list[QCCheck]
    stats: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    wall_sec: float = 0.0

    @property
    def failed_checks(self) -> list[str]:
        return [c.code for c in self.checks if not c.ok and c.severity == "fail"]

    def check(self, code: str) -> QCCheck | None:
        return next((c for c in self.checks if c.code == code), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "failed_checks": self.failed_checks,
            "checks": [c.to_dict() for c in self.checks],
            "warnings": list(self.warnings),
            "stats": self.stats,
            "wall_sec": round(self.wall_sec, 3),
        }


def build_qc_command(video: str | Path, *, has_audio: bool = True) -> list[str]:
    v = (
        f"[0:v]blackdetect=d={BLACK_MIN_SEC}:pic_th=0.98:pix_th=0.10,"
        f"freezedetect=n={FREEZE_NOISE_DB}dB:d={FREEZE_MIN_SEC}[v]"
    )
    cmd = ["ffmpeg", "-y", "-i", str(video)]
    if has_audio:
        a = (
            f"[0:a]silencedetect=n={SILENCE_DB}dB:d={SILENCE_MIN_SEC},volumedetect,"
            "ebur128=peak=true:framelog=verbose[a]"
        )
        cmd += ["-filter_complex", f"{a};{v}", "-map", "[v]", "-map", "[a]"]
    else:
        cmd += ["-filter_complex", v, "-map", "[v]"]
    return [*cmd, "-f", "null", "-"]


_F = r"(-?[\d.]+|-?inf|nan)"


def _num(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        return float("nan")


def parse_qc_log(text: str, duration: float) -> dict[str, Any]:
    """Разбор stderr одного прохода: тишина, громкость, пик, чёрное, зависания."""

    def pairs(start_key: str, end_key: str) -> list[tuple[float, float]]:
        starts = [_num(x) for x in re.findall(rf"{start_key}[:=]\s*{_F}", text)]
        ends = [_num(x) for x in re.findall(rf"{end_key}[:=]\s*{_F}", text)]
        out = []
        for i, s in enumerate(starts):
            e = ends[i] if i < len(ends) else duration
            out.append((s, e))
        return out

    stats: dict[str, Any] = {}
    stats["silences"] = pairs("silence_start", "silence_end")
    stats["blacks"] = [
        (_num(a), _num(b)) for a, b in re.findall(rf"black_start:\s*{_F}\s+black_end:\s*{_F}", text)
    ]
    stats["freezes"] = pairs(r"lavfi\.freezedetect\.freeze_start", r"lavfi\.freezedetect\.freeze_end")
    m = re.search(rf"mean_volume:\s*{_F} dB", text)
    stats["mean_volume_db"] = _num(m.group(1)) if m else None
    m = re.search(rf"max_volume:\s*{_F} dB", text)
    stats["max_volume_db"] = _num(m.group(1)) if m else None
    summary = text[text.rfind("Summary:") :] if "Summary:" in text else ""
    m = re.search(rf"I:\s*{_F} LUFS", summary)
    stats["integrated_lufs"] = _num(m.group(1)) if m else None
    m = re.search(rf"LRA:\s*{_F} LU", summary)
    stats["lra"] = _num(m.group(1)) if m else None
    m = re.search(rf"True peak:\s*Peak:\s*{_F} dBFS", summary) or re.search(rf"Peak:\s*{_F} dBFS", summary)
    stats["true_peak_dbfs"] = _num(m.group(1)) if m else None
    return stats


def evaluate(
    stats: dict[str, Any],
    duration: float,
    *,
    expected_static: Sequence[Sequence[float]] = (),
    has_audio: bool = True,
    duration_range: tuple[float, float] = DURATION_RANGE,
    hard_max: float = DURATION_HARD_MAX,
) -> list[QCCheck]:
    lo, hi = duration_range
    checks: list[QCCheck] = []
    ok_dur = lo <= duration <= min(hi, hard_max)
    checks.append(
        QCCheck("duration", ok_dur, f"{duration:.1f} с", f"{lo:.0f}–{hi:.0f} с (VK ≤ {hard_max:.0f})")
    )
    if has_audio:
        sil = [(a, b) for a, b in stats.get("silences", []) if b - a >= SILENCE_MIN_SEC - 1e-3]
        checks.append(
            QCCheck(
                "silence",
                not sil,
                f"{len(sil)} пауз",
                f"нет тишины ≥ {SILENCE_MIN_SEC:.0f} с",
                "; ".join(f"{a:.1f}–{b:.1f}" for a, b in sil),
            )
        )
        i_lufs = stats.get("integrated_lufs")
        ok_l = i_lufs is not None and i_lufs == i_lufs and abs(i_lufs - TARGET_I) <= LOUDNESS_TOL
        checks.append(
            QCCheck(
                "loudness",
                ok_l,
                "—" if i_lufs is None else f"{i_lufs:.1f} LUFS",
                f"{TARGET_I:.0f}±{LOUDNESS_TOL}",
            )
        )
        tp = stats.get("true_peak_dbfs")
        mx = stats.get("max_volume_db")
        peak = tp if tp is not None and tp == tp else mx
        ok_p = peak is not None and peak <= MAX_TRUE_PEAK + 1e-6
        checks.append(
            QCCheck(
                "clipping",
                ok_p,
                "—" if peak is None else f"{peak:.1f} dB",
                f"≤ {MAX_TRUE_PEAK} dBTP",
                f"max_volume={mx}",
            )
        )
    blacks = [(a, b) for a, b in stats.get("blacks", []) if b - a >= BLACK_MIN_SEC - 1e-3]
    checks.append(
        QCCheck(
            "black_frames",
            not blacks,
            f"{len(blacks)} участков",
            f"нет чёрного ≥ {BLACK_MIN_SEC:.0f} с",
            "; ".join(f"{a:.1f}–{b:.1f}" for a, b in blacks),
        )
    )
    allowed = iv.pad(expected_static, STATIC_TOLERANCE)
    freezes = stats.get("freezes", [])
    left = [(a, b) for a, b in iv.subtract(freezes, allowed) if b - a >= FREEZE_MIN_SEC - 1e-3]
    checks.append(
        QCCheck(
            "frozen_frames",
            not left,
            f"{len(left)} участков вне expected_static",
            f"нет зависаний ≥ {FREEZE_MIN_SEC:.0f} с (кроме freeze-вставок)",
            f"найдено {len(freezes)}, ожидаемых окон {len(list(expected_static))}; вне окон: "
            + ("; ".join(f"{a:.1f}–{b:.1f}" for a, b in left) or "нет"),
        )
    )
    return checks


def run_clip_qc(
    video: str | Path,
    *,
    expected_static: Sequence[Sequence[float]] = (),
    duration_range: tuple[float, float] = DURATION_RANGE,
    extra_checks: Sequence[QCCheck] = (),
    workdir: str | Path | None = None,
    threads: int = 2,
    nice: int = 10,
    timeout_sec: float = 600.0,
    cancel: threading.Event | None = None,
) -> QCReport:
    """Один проход ffmpeg по готовому клипу + разбор + вердикт."""
    from autoshorts.pipeline.ffmpeg_exec import run_media
    from autoshorts.pipeline.proxy import probe_media

    t0 = time.monotonic()
    probe = probe_media(video)
    duration = float(probe.duration)
    wd = Path(workdir) if workdir else Path(video).parent
    log_path = wd / "qc.log"
    log_path.unlink(missing_ok=True)
    cmd = build_qc_command(video, has_audio=probe.has_audio)
    res = run_media(
        cmd,
        timeout_sec=timeout_sec,
        threads=threads,
        nice=nice,
        log_path=log_path,
        duration_sec=duration,
        cancel=cancel,
        check=False,
    )
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else res.stderr_tail
    stats = parse_qc_log(text, duration)
    checks = evaluate(
        stats,
        duration,
        expected_static=expected_static,
        has_audio=probe.has_audio,
        duration_range=duration_range,
    )
    if res.returncode != 0:
        checks.append(QCCheck("qc_run", False, f"exit {res.returncode}", "0", res.stderr_tail[-300:]))
    checks.extend(extra_checks)
    stats["duration"] = duration
    stats["expected_static"] = [list(x) for x in expected_static]
    warnings = [c.to_dict() for c in checks if not c.ok and c.severity == "warn"]
    passed = all(c.ok or c.severity != "fail" for c in checks)
    return QCReport(passed, checks, stats, warnings, time.monotonic() - t0)


__all__ = [
    "DURATION_RANGE",
    "QCCheck",
    "QCReport",
    "build_qc_command",
    "evaluate",
    "parse_qc_log",
    "run_clip_qc",
]
