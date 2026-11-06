"""Рендер клипа по EDL одним кодированием (docs/ARCHITECTURE.md §6.3.7 п. 2).

    cmd = build_render_command(edl, src_path=..., audio_path="mix.m4a", ass_path="captions.ass",
                               out_path="final.mp4")
    res = render_clip(edl, src_path=..., audio_path=..., ass_path=..., out_path=..., workdir=wd)

Граф видео:

* на каждый элемент EDL — свой вход `-ss in -t dur -i src` (декодируется только нужный кусок;
  `split` + `trim` одного входа буферизовал бы кадры будущих кусков в памяти);
* `src`: `fps=30,trim=end_frame=N,setpts=PTS-STARTPTS` → рефрейм:
  - `track`: `sendcmd=f=crop_00k.cmd,crop@ck=…,scale=1080:1920:flags=lanczos`,
  - `center`: `crop=…,scale=1080:1920:flags=lanczos`,
  - `fit_blur`: дешёвый фон `scale=270:480,boxblur=10:2,scale=1080:1920` + кадр целиком по ширине;
* `freeze`: один кадр → рефрейм → `scale` 1,15× → `loop` → медленный наезд `crop` с выражением
  от `t` (логика legacy `render._crop_pan_filter`, zoompan не используется — он в 60 раз дороже);
* `concat` → `ass=…:fontsdir=config/fonts` (или граф баннера, см. ниже) → `yuv420p`;
* `libx264 -preset veryfast -crf 20 -maxrate 12M -bufsize 24M`, 2 потока, `+faststart`,
  звук `mix.m4a` копируется без перекодирования, в метаданные пишется AI-disclosure.

Баннер: `banner_graph` — функция, которая строит входы и filter_complex поверх готового видео
(`banner_graph_from_plan(plan)` берёт `pipeline/banner_overlay.build_filter_graph`, если модуль
есть и импортируется). Без неё клип рендерится без баннера.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FONTS_DIR = PROJECT_ROOT / "config" / "fonts"
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_./+-]+$")


@dataclass(slots=True)
class RenderConfig:
    preset: str = "veryfast"
    crf: int = 20
    maxrate: str = "12M"
    bufsize: str = "24M"
    profile: str = "high"
    threads: int = 2
    nice: int = 10
    gop_sec: float = 2.0
    freeze_zoom: float = 1.15
    blur_size: tuple[int, int] = (270, 480)
    boxblur: str = "10:2"
    fonts_dir: Path = FONTS_DIR
    timeout_sec: float = 900.0
    input_threads: int = 2
    min_mem_mb: int = 1500
    mem_wait_sec: float = 600.0
    # чистый файл (владелец 29.10): без наших тегов, глав, версий кодировщика и SEI x264 с его настройками.
    # На показы и Content ID не влияет (YouTube перекодирует, сверяет картинку и звук) — гигиена файла
    clean_metadata: bool = True


def filter_path(p: str | Path) -> str:
    """Путь как значение опции фильтра: безопасные символы как есть, иначе в кавычках."""
    s = str(p)
    if _SAFE_PATH.match(s):
        return s
    return "'" + s.replace("\\", "\\\\").replace("'", "'\\''") + "'"


def _f(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s or "0"


def freeze_pan_filters(direction: str, dur: float, w: int, h: int, zoom: float = 1.15) -> tuple[str, str]:
    """Наезд на стоп-кадр: (до loop, после loop).

    Повторяет legacy `pipeline.render._crop_pan_filter` (кадр 1,15× и окно `crop` с выражением
    от `t`), но масштаб делается один раз — до размножения кадра, а не на каждом кадре.
    `direction`: in | out | left | right | none.
    """
    if direction in ("none", "", None):
        return "", ""
    ss_w, ss_h = int(w * zoom), int(h * zoom)
    max_x, max_y = ss_w - w, ss_h - h
    d = dur
    exprs = {
        "in": (f"x='{max_x}/2'", f"y='{max_y}*(1-t/{d})/2'"),
        "out": (f"x='{max_x}/2'", f"y='{max_y}*(t/{d})/2'"),
        "left": (f"x='{max_x}*(1-t/{d})'", f"y='{max_y}/2'"),
        "right": (f"x='{max_x}*(t/{d})'", f"y='{max_y}/2'"),
    }
    x_expr, y_expr = exprs.get(direction, exprs["in"])
    pre = f"scale={ss_w}:{ss_h}:force_original_aspect_ratio=increase,crop={ss_w}:{ss_h}"
    post = f"crop={w}:{h}:{x_expr}:{y_expr}"
    return pre, post


def _crop_index(name: str | None) -> int | None:
    if not name:
        return None
    m = re.search(r"(\d+)", Path(name).stem)
    return int(m.group(1)) if m else None


@dataclass(slots=True)
class VideoGraph:
    inputs: list[str]
    chains: list[str]
    out_label: str
    n_inputs: int


def build_video_graph(
    edl: Any, src_path: str | Path, cfg: RenderConfig | None = None, *, src_offset: float = 0.0
) -> VideoGraph:
    """Входы и цепочки видео по EDL до `concat` включительно (метка `vcat`).

    `src_offset` — момент источника, с которого начинается файл рендера (1080p-секция URL-источника
    начинается не с нуля); время EDL всегда в секундах источника.
    """
    cfg = cfg or RenderConfig()
    W, H = int(edl.canvas["w"]), int(edl.canvas["h"])
    fps = float(edl.canvas.get("fps", 30))
    fps_s = _f(fps)
    bw, bh = cfg.blur_size
    # fit_blur: где кончается кадр целиком (EDL meta, ставит produce_clip при баннере); None — по центру
    panel_bottom = (edl.meta or {}).get("fit_panel_bottom")
    # стиль канала «кадр в фигуре на живом фоне»: куски фильма — квадрат size×size, фон — отдельный ролик
    frame_style = (edl.meta or {}).get("frame_style") or None
    if frame_style:
        FW = int(frame_style.get("width") or frame_style["size"])
        FH = int(frame_style.get("height") or frame_style["size"])
    else:
        FW, FH = W, H
    inputs: list[str] = []
    chains: list[str] = []
    labels: list[str] = []
    scale_full = f"scale={FW}:{FH}:flags=lanczos"
    for k, v in enumerate(edl.video):
        lab = f"v{k}"
        it = ["-threads", str(cfg.input_threads)]
        if v.type == "src":
            it += ["-ss", _f(max(0.0, float(v.in_) - src_offset)), "-t", _f(v.dur + 0.5)]
            head = f"[{k}:v]fps={fps_s},trim=end_frame={v.frames},setpts=PTS-STARTPTS"
        else:
            it += ["-ss", _f(max(0.0, float(v.at_src) - src_offset)), "-t", "1"]
            head = f"[{k}:v]trim=end_frame=1,setpts=PTS-STARTPTS"
        inputs += [*it, "-i", str(src_path)]
        crop = v.crop or {}
        cw, ch_ = int(crop.get("w", 0)), int(crop.get("h", 0))
        cx, cy = int(crop.get("x", 0)), int(crop.get("y", 0))
        if v.reframe == "fit_blur":
            chains.append(f"{head},split=2[{lab}b][{lab}f]")
            chains.append(
                f"[{lab}b]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
                f"boxblur={cfg.boxblur},scale={W}:{H}[{lab}bg]"
            )
            chains.append(
                f"[{lab}f]scale={W}:{H}:force_original_aspect_ratio=decrease:force_divisible_by=2:"
                f"flags=lanczos[{lab}fg]"
            )
            if panel_bottom:
                # кадр целиком поднимается над баннером (низ кадра = верх плашки − отступ)
                framed = f"[{lab}bg][{lab}fg]overlay=x=(W-w)/2:y='max(0,min((H-h)/2,{int(panel_bottom)}-h))'"
            else:
                framed = f"[{lab}bg][{lab}fg]overlay=(W-w)/2:(H-h)/2"
        else:
            ops: list[str] = []
            idx = _crop_index(v.crop_cmd)
            if v.type == "src" and v.reframe == "track" and v.crop_cmd and idx is not None and cw:
                ops.append(f"sendcmd=f={filter_path(v.crop_cmd)}")
                ops.append(f"crop@c{idx}=w={cw}:h={ch_}:x={cx}:y={cy}")
            elif cw and ch_:
                ops.append(f"crop={cw}:{ch_}:{cx}:{cy}")
            ops.append(scale_full)
            framed = f"{head},{','.join(ops)}"
        if v.type == "src":
            chains.append(f"{framed},setsar=1,format=yuv420p[{lab}]")
        else:
            pre, post = freeze_pan_filters(v.pan, v.dur, FW, FH, cfg.freeze_zoom)
            tail = [f"loop=loop={max(0, v.frames - 1)}:size=1:start=0", f"setpts=N/{fps_s}/TB"]
            if post:
                tail.append(post)
            tail += [f"fps={fps_s}", "setsar=1", "format=yuv420p"]
            chains.append(f"{framed}{',' + pre if pre else ''},{','.join(tail)}[{lab}]")
        labels.append(f"[{lab}]")
    if not frame_style:
        chains.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[vcat]")
        return VideoGraph(inputs, chains, "vcat", len(edl.video))
    from autoshorts.clipping.frame_style import compose_chains

    chains.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[vcatfg]")
    extra_in, extra_chains = compose_chains(
        frame_style, fg_label="vcatfg", first_input=len(edl.video), duration=float(edl.duration), fps=fps,
        out_label="vcat",
    )
    return VideoGraph(inputs + extra_in, chains + extra_chains, "vcat", len(edl.video) + 4)


BannerGraphFn = Callable[..., Any]


def banner_graph_from_plan(plan: Any) -> BannerGraphFn | None:
    """Хук баннера: `pipeline/banner_overlay.build_filter_graph` поверх готового видео.

    Модуль баннера пишется параллельно; если его нет или он не импортируется — None (рендер
    без баннера, предупреждение в результате).
    """
    try:
        from autoshorts.pipeline import banner_overlay  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None

    def fn(
        *,
        base_label: str,
        first_input_index: int,
        ass_path: str | None,
        fonts_dir: Path,
        duration_sec: float,
        fps: float,
    ) -> Any:
        return banner_overlay.build_filter_graph(
            plan.items,
            duration_sec=duration_sec,
            fps=fps,
            first_input_index=first_input_index,
            base_label=base_label,
            ass_path=Path(ass_path) if ass_path else None,
            fonts_dir=fonts_dir,
        )

    return fn


def ai_disclosure_text(edl: Any) -> str:
    meta = edl.meta or {}
    parts = ["Edited clip with author commentary"]
    kinds = meta.get("voice_kinds") or []
    if meta.get("synthetic_voice"):
        parts.append(
            "author voice is synthetic (TTS)"
            if "operator" not in kinds
            else "part of author voice is synthetic (TTS)"
        )
    if meta.get("author_mode") in ("llm", "mock", "heuristic"):
        parts.append("author script drafted with AI")
    return "; ".join(parts)


def build_render_command(
    edl: Any,
    *,
    src_path: str | Path,
    audio_path: str | Path | None,
    out_path: str | Path,
    ass_path: str | None = None,
    cfg: RenderConfig | None = None,
    banner_graph: BannerGraphFn | None = None,
    metadata: Mapping[str, str] | None = None,
    src_offset: float = 0.0,
) -> list[str]:
    """Команда ffmpeg (без nice и глобальных опций — их добавляет `ffmpeg_exec.run_media`).

    `ass_path` и файлы `crop_*.cmd` — относительные имена: ffmpeg запускается с cwd=workdir,
    так в filter_complex не нужно экранировать абсолютные пути.
    """
    cfg = cfg or RenderConfig()
    fps = float(edl.canvas.get("fps", 30))
    graph = build_video_graph(edl, src_path, cfg, src_offset=src_offset)
    cmd = ["ffmpeg", "-y", *graph.inputs]
    audio_idx = None
    if audio_path is not None:
        audio_idx = graph.n_inputs
        cmd += ["-i", str(audio_path)]
    chains = list(graph.chains)
    out_label = "vout"
    fonts = filter_path(Path(cfg.fonts_dir).resolve())
    if banner_graph is not None:
        first = graph.n_inputs + (1 if audio_idx is not None else 0)
        bg = banner_graph(
            base_label=graph.out_label,
            first_input_index=first,
            ass_path=ass_path,
            fonts_dir=Path(cfg.fonts_dir).resolve(),
            duration_sec=edl.duration,
            fps=fps,
        )
        cmd += list(bg.input_args)
        chains.append(bg.filter_complex)
        out_label = bg.out_label
    elif ass_path:
        chains.append(
            f"[{graph.out_label}]ass=filename={filter_path(ass_path)}:fontsdir={fonts},format=yuv420p[vout]"
        )
    else:
        chains.append(f"[{graph.out_label}]format=yuv420p[vout]")
    lag = (edl.meta or {}).get("lag_ad")
    if lag:
        # рекламная вставка: затемнение, крутилка и плашка поверх наклейки и субтитров (clipping/lag_ad.py)
        from autoshorts.clipping import lag_ad

        fs = (edl.meta or {}).get("frame_style") or {}
        W, H = int(edl.canvas["w"]), int(edl.canvas["h"])
        cy = int(fs.get("cy") or 0) if fs.get("enabled", True) else 0
        first = sum(1 for x in cmd if x == "-i")
        ins, chain = lag_ad.overlay_chains(
            out_label, first, dict(lag, cy=cy or H // 2), fps=fps, width=W, height=H, out="vlag"
        )
        cmd += ins
        chains.append(chain)
        out_label = "vlag"
    cmd += ["-filter_complex", ";".join(chains), "-map", f"[{out_label}]"]
    if audio_idx is not None:
        cmd += ["-map", f"{audio_idx}:a:0", "-c:a", "copy"]
    gop = max(1, int(round(cfg.gop_sec * fps)))
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        cfg.preset,
        "-crf",
        str(cfg.crf),
        "-maxrate",
        cfg.maxrate,
        "-bufsize",
        cfg.bufsize,
        "-profile:v",
        cfg.profile,
        "-pix_fmt",
        "yuv420p",
        "-r",
        _f(fps),
        "-g",
        str(gop),
    ]
    if cfg.clean_metadata:
        cmd += [
            "-movflags", "+faststart",
            "-map_metadata", "-1", "-map_chapters", "-1",
            "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
            "-bsf:v", "filter_units=remove_types=6",  # SEI: строка «x264 - core … options: …»
            "-threads", str(cfg.threads), "-t", _f(edl.duration), str(out_path),
        ]  # fmt: skip
        return cmd
    cmd += ["-movflags", "+faststart+use_metadata_tags"]
    md = {
        "comment": ai_disclosure_text(edl),
        "ai_disclosure": "true" if (edl.meta or {}).get("ai_disclosure") else "false",
        "synthetic_voice": "true" if (edl.meta or {}).get("synthetic_voice") else "false",
        "encoder_pipeline": "autoshorts.clipping",
    }
    if (edl.meta or {}).get("attribution"):
        md["copyright"] = str(edl.meta["attribution"])[:250]
    md.update(dict(metadata or {}))
    for k, v in md.items():
        cmd += ["-metadata", f"{k}={v}"]
    cmd += ["-threads", str(cfg.threads), "-t", _f(edl.duration), str(out_path)]
    return cmd


@dataclass(slots=True)
class RenderResult:
    path: Path
    duration: float
    wall_sec: float
    size_bytes: int
    cmd: list[str]
    log_path: Path | None
    banner: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "duration": round(self.duration, 3),
            "wall_sec": round(self.wall_sec, 3),
            "size_bytes": self.size_bytes,
            "banner": self.banner,
            "log_path": str(self.log_path) if self.log_path else None,
            "warnings": list(self.warnings),
        }


def render_clip(
    edl: Any,
    *,
    src_path: str | Path,
    audio_path: str | Path | None,
    out_path: str | Path,
    workdir: str | Path,
    ass_path: str | None = None,
    cfg: RenderConfig | None = None,
    banner_graph: BannerGraphFn | None = None,
    metadata: Mapping[str, str] | None = None,
    progress_cb: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
    wait_memory: bool = True,
    src_offset: float = 0.0,
) -> RenderResult:
    """Один проход ffmpeg по EDL; запись атомарная (tmp → rename)."""
    from autoshorts.pipeline.ffmpeg_exec import run_media
    from autoshorts.pipeline.proxy import probe_media

    cfg = cfg or RenderConfig()
    wd = Path(workdir)
    wd.mkdir(parents=True, exist_ok=True)
    out = Path(out_path).resolve()
    tmp = out.with_name(f".{out.stem}.tmp{out.suffix}")
    warnings: list[str] = []
    if wait_memory:
        from autoshorts.clipping.source_ingest import wait_for_memory

        wait_for_memory(cfg.min_mem_mb, timeout_sec=cfg.mem_wait_sec)
    cmd = build_render_command(
        edl,
        src_path=Path(src_path).resolve(),
        audio_path=Path(audio_path).resolve() if audio_path else None,
        out_path=tmp,
        ass_path=ass_path,
        cfg=cfg,
        banner_graph=banner_graph,
        metadata=metadata,
        src_offset=src_offset,
    )
    log_path = wd / "render.log"
    log_path.unlink(missing_ok=True)
    t0 = time.monotonic()
    try:
        run_media(
            cmd,
            timeout_sec=cfg.timeout_sec,
            threads=cfg.threads,
            nice=cfg.nice,
            cwd=wd,
            log_path=log_path,
            duration_sec=edl.duration,
            progress_cb=progress_cb,
            cancel=cancel,
        )
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    wall = time.monotonic() - t0
    dur = float(probe_media(out).duration)
    if abs(dur - edl.duration) > 0.2:
        warnings.append(f"duration_mismatch:{dur:.3f}!={edl.duration:.3f}")
    return RenderResult(out, dur, wall, out.stat().st_size, cmd, log_path, banner_graph is not None, warnings)


__all__ = [
    "FONTS_DIR",
    "RenderConfig",
    "RenderResult",
    "VideoGraph",
    "ai_disclosure_text",
    "banner_graph_from_plan",
    "build_render_command",
    "build_video_graph",
    "filter_path",
    "freeze_pan_filters",
    "render_clip",
]
