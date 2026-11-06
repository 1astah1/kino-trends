"""Сведение звука клипа отдельным проходом (docs/ARCHITECTURE.md §6.3.7 п. 1).

    spec = MixSpec(source=Path("original.mp4"), pieces=[SrcPiece(84.12, 12.28), GapPiece(2.6), ...],
                   duck=[(0.0, 2.4, -16.0)], vo=[VoClip(Path("vo_open.mp3"), 0.12)], duration=41.3)
    res = mix_audio(spec, workdir / "mix.m4a", workdir=workdir)

Граф: для каждого куска исходника отдельный вход `-ss in -t dur` → `atrim/asetpts` (точно по
семплам) → паузы там, где кадр стоит (freeze) → `concat` → огибающая приглушения
`volume=…:eval=frame` с рампами 80 мс → голос автора `adelay` → `amix=normalize=0` →
`apad/atrim` до длины ролика. Затем двухпроходный `loudnorm` I = −14 LUFS, TP = −1 dBTP,
LRA = 11 — та же логика и те же цели, что у `pipeline/render.two_pass_loudnorm` (legacy,
только чтение), но запуск через `ffmpeg_exec.run_media` (nice 10, 2 потока, таймаут, отмена).

`balance=True`: перед сведением громкость исходника и каждой реплики меряется (`loudnorm`
print_format=json, только звук, доли секунды) и выравнивается к −18 LUFS, голос автора чуть
громче (+1 дБ). Иначе тихий TTS на фоне громкого подкаста теряется.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SAMPLE_RATE = 48000
TARGET_I = -14.0
TARGET_TP = -1.0
# Потолок true peak уже ПОСЛЕ кодирования в AAC: кодер добавляет межвыборочный выброс до +1,5 дБ
# поверх выхода loudnorm (замер на реальном источнике: −1,0 → +0,5 dBTP), а QC клипа требует
# ≤ −0,5 dBTP. Если после кодирования пик выше — второй проход повторяется с более низким TP.
MAX_ENCODED_TP = -1.0
TP_RETRIES = 2
TARGET_LRA = 11.0
BALANCE_TARGET_I = -18.0
VO_BOOST_DB = 1.0
MAX_BALANCE_GAIN_DB = 20.0
DUCK_RAMP_SEC = 0.08
AFMT = f"aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo"


@dataclass(frozen=True, slots=True)
class SrcPiece:
    """Кусок исходника: `dur` секунд с момента `start` источника."""

    start: float
    dur: float


@dataclass(frozen=True, slots=True)
class GapPiece:
    """Пауза исходника (кадр стоит): тишина длиной `dur`."""

    dur: float


@dataclass(frozen=True, slots=True)
class VoClip:
    path: Path
    at: float  # секунды выходной шкалы
    gain_db: float = 0.0


@dataclass(slots=True)
class MixSpec:
    source: Path | None
    pieces: list[SrcPiece | GapPiece]
    duration: float
    duck: list[tuple[float, float, float]] = field(default_factory=list)  # (a, b, gain_db<0)
    vo: list[VoClip] = field(default_factory=list)
    source_has_audio: bool = True
    src_gain_db: float = 0.0
    sample_rate: int = SAMPLE_RATE

    @property
    def pieces_duration(self) -> float:
        return sum(p.dur for p in self.pieces)


# ---------------------------------------------------------------------- выражения


def _f(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def db_to_gain(db: float) -> float:
    return 10.0 ** (db / 20.0)


def duck_expression(windows: Sequence[tuple[float, float, float]], *, ramp: float = DUCK_RAMP_SEC) -> str:
    """Огибающая громкости от `t`: произведение трапеций по окнам.

    Для окна (a, b, g) множитель `1-(1-G)*clip(min((t-a)/r,(b-t)/r),0,1)`, G = 10^(g/20):
    полная громкость вне окна, G внутри, линейные рампы длиной r у краёв (внутри окна).
    """
    parts: list[str] = []
    for a, b, g in windows:
        if b - a <= 1e-3:
            continue
        r = max(1e-3, min(ramp, (b - a) / 2))
        gl = db_to_gain(g)
        parts.append(f"(1-{_f(1 - gl)}*clip(min((t-{_f(a)})/{_f(r)},({_f(b)}-t)/{_f(r)}),0,1))")
    return "*".join(parts) if parts else "1"


def envelope_value(
    t: float, windows: Sequence[tuple[float, float, float]], *, ramp: float = DUCK_RAMP_SEC
) -> float:
    """То же, что `duck_expression`, но числом — для тестов и проверки QC."""
    v = 1.0
    for a, b, g in windows:
        if b - a <= 1e-3:
            continue
        r = max(1e-3, min(ramp, (b - a) / 2))
        k = min(max(min((t - a) / r, (b - t) / r), 0.0), 1.0)
        v *= 1 - (1 - db_to_gain(g)) * k
    return v


# ---------------------------------------------------------------------- граф


@dataclass(slots=True)
class MixGraph:
    inputs: list[str]
    filter_complex: str
    out_label: str


def build_mix_graph(spec: MixSpec, *, gain_overrides: dict[str, float] | None = None) -> MixGraph:
    """Входы и filter_complex премикса (без loudnorm)."""
    sr = spec.sample_rate
    inputs: list[str] = []
    chains: list[str] = []
    labels: list[str] = []
    idx = 0
    use_src = spec.source is not None and spec.source_has_audio
    for k, p in enumerate(spec.pieces):
        lab = f"p{k}"
        n = max(1, int(round(p.dur * sr)))
        if isinstance(p, SrcPiece) and use_src:
            inputs += ["-ss", _f(max(0.0, p.start)), "-t", _f(p.dur + 0.5), "-i", str(spec.source)]
            chains.append(
                f"[{idx}:a]{AFMT},atrim=end_sample={n},asetpts=PTS-STARTPTS,apad=whole_len={n}[{lab}]"
            )
            idx += 1
        else:
            chains.append(f"anullsrc=r={sr}:cl=stereo,atrim=end_sample={n},aformat=sample_fmts=fltp[{lab}]")
        labels.append(f"[{lab}]")
    g = gain_overrides or {}
    src_gain = g.get("src", spec.src_gain_db)
    env = duck_expression(spec.duck)
    if labels:
        chains.append(f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[srccat]")
    else:
        chains.append(f"anullsrc=r={sr}:cl=stereo,atrim=duration={_f(spec.duration)}[srccat]")
    src_chain = "asetnsamples=n=480"
    if abs(src_gain) > 1e-3:
        src_chain += f",volume={_f(src_gain)}dB"
    src_chain += f",volume='{env}':eval=frame"
    chains.append(f"[srccat]{src_chain}[src]")
    mix_in = ["[src]"]
    for j, v in enumerate(spec.vo):
        inputs += ["-i", str(v.path)]
        ms = max(0, int(round(v.at * 1000)))
        gain = g.get(f"vo{j}", v.gain_db)
        ch = f"[{idx}:a]{AFMT}"
        if abs(gain) > 1e-3:
            ch += f",volume={_f(gain)}dB"
        ch += f",adelay=delays={ms}:all=1[vo{j}]"
        chains.append(ch)
        mix_in.append(f"[vo{j}]")
        idx += 1
    n_total = int(round(spec.duration * sr))
    if len(mix_in) > 1:
        chains.append(
            f"{''.join(mix_in)}amix=inputs={len(mix_in)}:duration=longest:dropout_transition=0:normalize=0,"
            f"apad=whole_len={n_total},atrim=end_sample={n_total}[mix]"
        )
    else:
        chains.append(f"[src]apad=whole_len={n_total},atrim=end_sample={n_total}[mix]")
    return MixGraph(inputs, ";".join(chains), "mix")


# ---------------------------------------------------------------------- запуск


def _run(
    cmd: list[str],
    *,
    timeout_sec: float,
    threads: int,
    nice: int,
    log_path: Path | None,
    cancel: threading.Event | None,
    duration_sec: float | None = None,
    progress_cb: Callable[[float], None] | None = None,
) -> Any:
    from autoshorts.pipeline.ffmpeg_exec import run_media

    return run_media(
        cmd,
        timeout_sec=timeout_sec,
        threads=threads,
        nice=nice,
        log_path=log_path,
        cancel=cancel,
        duration_sec=duration_sec,
        progress_cb=progress_cb,
    )


_JSON_BLOCK = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.S)


def parse_loudnorm_json(text: str) -> dict[str, str] | None:
    """Последний JSON-блок `loudnorm` из stderr."""
    blocks = _JSON_BLOCK.findall(text or "")
    if not blocks:
        return None
    try:
        return json.loads(blocks[-1])
    except json.JSONDecodeError:
        return None


def _stderr_text(res: Any, log_path: Path | None) -> str:
    if log_path is not None and log_path.is_file():
        try:
            return log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    return getattr(res, "stderr_tail", "") or ""


def measure_loudness(
    path: str | Path,
    *,
    start: float | None = None,
    duration: float | None = None,
    threads: int = 2,
    nice: int = 10,
    timeout_sec: float = 300.0,
    log_path: Path | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, float] | None:
    """Интегральная громкость (loudnorm, первый проход).

    Возвращает {input_i, input_tp, input_lra, input_thresh, target_offset} или None.
    """
    cmd = ["ffmpeg", "-y"]
    if start is not None:
        cmd += ["-ss", _f(max(0.0, start))]
    if duration is not None:
        cmd += ["-t", _f(duration)]
    cmd += [
        "-i",
        str(path),
        "-vn",
        "-af",
        f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json",
        "-f",
        "null",
        "-",
    ]
    if log_path is not None:
        log_path.unlink(missing_ok=True)
    res = _run(cmd, timeout_sec=timeout_sec, threads=threads, nice=nice, log_path=log_path, cancel=cancel)
    data = parse_loudnorm_json(_stderr_text(res, log_path))
    if not data:
        return None
    out: dict[str, float] = {}
    for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
        try:
            out[k] = float(data[k])
        except (KeyError, TypeError, ValueError):
            out[k] = float("nan")
    return out


def two_pass_loudnorm(
    in_audio: Path,
    out_audio: Path,
    *,
    target_i: float = TARGET_I,
    target_tp: float = TARGET_TP,
    target_lra: float = TARGET_LRA,
    threads: int = 2,
    nice: int = 10,
    timeout_sec: float = 300.0,
    workdir: Path | None = None,
    cancel: threading.Event | None = None,
    max_encoded_tp: float | None = MAX_ENCODED_TP,
    tp_retries: int = TP_RETRIES,
) -> dict[str, Any]:
    """Двухпроходная нормализация в AAC 192k 48 кГц (параметры legacy `render.two_pass_loudnorm`).

    После кодирования true peak файла измеряется ещё раз: если AAC вынес пик выше
    `max_encoded_tp`, второй проход повторяется с TP ниже на величину выброса (до `tp_retries` раз).
    """
    log1 = (workdir / "loudnorm_pass1.log") if workdir else None
    measured = measure_loudness(
        in_audio, threads=threads, nice=nice, timeout_sec=timeout_sec, log_path=log1, cancel=cancel
    )
    silent = measured is None or any(v != v for v in measured.values())  # NaN — тишина
    mode = "single_pass" if silent else "two_pass"
    tp = float(target_tp)
    attempts: list[dict[str, Any]] = []
    for attempt in range(1 + max(0, tp_retries)):
        if silent:
            af = f"loudnorm=I={target_i}:TP={tp:g}:LRA={target_lra}"
        else:
            assert measured is not None
            af = (
                f"loudnorm=I={target_i}:TP={tp:g}:LRA={target_lra}:"
                f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
                f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
                f"offset={measured['target_offset']}:linear=true:print_format=summary"
            )
        tmp = out_audio.with_name(f".{out_audio.stem}.tmp{out_audio.suffix}")
        cmd = [
            "ffmpeg", "-y", "-i", str(in_audio), "-vn", "-af", af, "-ar", str(SAMPLE_RATE),
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp),
        ]  # fmt: skip
        suffix = "" if attempt == 0 else f"_retry{attempt}"
        log2 = (workdir / f"loudnorm_pass2{suffix}.log") if workdir else None
        try:
            _run(cmd, timeout_sec=timeout_sec, threads=threads, nice=nice, log_path=log2, cancel=cancel)
            tmp.replace(out_audio)
        finally:
            tmp.unlink(missing_ok=True)
        if max_encoded_tp is None or silent:
            break
        check = measure_loudness(
            out_audio,
            threads=threads,
            nice=nice,
            timeout_sec=timeout_sec,
            log_path=(workdir / f"loudnorm_check{suffix}.log") if workdir else None,
            cancel=cancel,
        )
        enc_tp = None if check is None else check.get("input_tp")
        attempts.append({"target_tp": tp, "encoded_tp": enc_tp})
        if enc_tp is None or enc_tp != enc_tp or enc_tp <= max_encoded_tp:
            break
        tp = round(tp - (enc_tp - max_encoded_tp) - 0.3, 2)
    return {
        "mode": mode,
        "measured": measured,
        "target": {"I": target_i, "TP": target_tp, "LRA": target_lra},
        "tp_used": tp,
        "encoded_tp": attempts[-1]["encoded_tp"] if attempts else None,
        "tp_attempts": attempts,
    }


@dataclass(slots=True)
class MixResult:
    path: Path
    duration: float
    loudnorm: dict[str, Any]
    gains: dict[str, float]
    levels: dict[str, float | None]
    wall_sec: float
    premix_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "duration": round(self.duration, 3),
            "loudnorm": self.loudnorm,
            "gains": {k: round(v, 2) for k, v in self.gains.items()},
            "levels": {k: (None if v is None else round(v, 2)) for k, v in self.levels.items()},
            "wall_sec": round(self.wall_sec, 3),
        }


def _balance_gain(level: float | None, target: float) -> float:
    if level is None or level != level or level < -60:  # тишина или нет данных
        return 0.0
    return max(-MAX_BALANCE_GAIN_DB, min(MAX_BALANCE_GAIN_DB, target - level))


def mix_audio(
    spec: MixSpec,
    out_path: str | Path,
    *,
    workdir: str | Path,
    balance: bool = True,
    target_i: float = TARGET_I,
    threads: int = 2,
    nice: int = 10,
    timeout_sec: float = 600.0,
    keep_premix: bool = False,
    cancel: threading.Event | None = None,
    progress_cb: Callable[[float], None] | None = None,
) -> MixResult:
    """Премикс в WAV (float) → двухпроходный loudnorm → `out_path` (AAC в .m4a)."""
    wd = Path(workdir)
    wd.mkdir(parents=True, exist_ok=True)
    out = Path(out_path)
    t0 = time.monotonic()
    gains: dict[str, float] = {"src": spec.src_gain_db}
    levels: dict[str, float | None] = {}
    if balance:
        src_pieces = [p for p in spec.pieces if isinstance(p, SrcPiece)]
        if spec.source is not None and spec.source_has_audio and src_pieces:
            a = min(p.start for p in src_pieces)
            b = max(p.start + p.dur for p in src_pieces)
            m = measure_loudness(
                spec.source,
                start=a,
                duration=b - a,
                threads=threads,
                nice=nice,
                log_path=wd / "level_src.log",
                cancel=cancel,
            )
            levels["src"] = m["input_i"] if m else None
            gains["src"] = spec.src_gain_db + _balance_gain(levels["src"], BALANCE_TARGET_I)
        for j, v in enumerate(spec.vo):
            m = measure_loudness(
                v.path, threads=threads, nice=nice, log_path=wd / f"level_vo{j}.log", cancel=cancel
            )
            levels[f"vo{j}"] = m["input_i"] if m else None
            gains[f"vo{j}"] = v.gain_db + _balance_gain(levels[f"vo{j}"], BALANCE_TARGET_I + VO_BOOST_DB)
    else:
        for j, v in enumerate(spec.vo):
            gains[f"vo{j}"] = v.gain_db
    graph = build_mix_graph(spec, gain_overrides=gains)
    premix = wd / "premix.wav"
    cmd = [
        "ffmpeg",
        "-y",
        *graph.inputs,
        "-filter_complex",
        graph.filter_complex,
        "-map",
        f"[{graph.out_label}]",
        "-c:a",
        "pcm_f32le",
        "-ar",
        str(spec.sample_rate),
        str(premix),
    ]
    _run(
        cmd,
        timeout_sec=timeout_sec,
        threads=threads,
        nice=nice,
        log_path=wd / "premix.log",
        cancel=cancel,
        duration_sec=spec.duration,
        progress_cb=progress_cb,
    )
    ln = two_pass_loudnorm(
        premix,
        out,
        target_i=target_i,
        threads=threads,
        nice=nice,
        timeout_sec=timeout_sec,
        workdir=wd,
        cancel=cancel,
    )
    if not keep_premix:
        premix.unlink(missing_ok=True)
    return MixResult(
        out, spec.duration, ln, gains, levels, time.monotonic() - t0, premix if keep_premix else None
    )


__all__ = [
    "BALANCE_TARGET_I",
    "DUCK_RAMP_SEC",
    "TARGET_I",
    "TARGET_LRA",
    "TARGET_TP",
    "GapPiece",
    "MixGraph",
    "MixResult",
    "MixSpec",
    "SrcPiece",
    "VoClip",
    "build_mix_graph",
    "db_to_gain",
    "duck_expression",
    "envelope_value",
    "measure_loudness",
    "mix_audio",
    "parse_loudnorm_json",
    "two_pass_loudnorm",
]
