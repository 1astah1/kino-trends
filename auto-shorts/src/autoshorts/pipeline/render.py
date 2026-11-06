"""
Сборка финального mp4: фон + Ken Burns + караоке-субтитры + двухслойный баннер + аудио.

КРИТИЧНО (ТЗ §9): движение камеры делается через `crop` с выражениями от `t`, НИКОГДА через
zoompan — замер показал >3100 CPU-сек на 60 сек 1080x1920 против 52 CPU-сек у обычного рендера
(разница в 60 раз). crop с выражением по времени даёт визуально тот же плавный пан на порядки дешевле.

Кодирование: libx264 preset veryfast CRF 23 — площадка всё равно перекодирует загруженное,
platить за preset medium бессмысленно (замер: 101 CPU-сек против 51.5, при том же результате).
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from autoshorts.config import SETTINGS
from autoshorts.models import Scene


def fit_offer_fontsize(text: str, box_w_px: int, max_size_px: int, min_size_px: int = 40,
                        avg_char_width_ratio: float = 0.56) -> int:
    """
    Автоподбор кегля под ширину плашки — без него длинный оффер обрезается по правому краю
    (замечено на первом рендер-тесте: 'umka.chat — бесплатный расклад' вылезал за 840px бокс).
    avg_char_width_ratio — эмпирический коэффициент для Montserrat Bold, при смене шрифта перепроверить
    визуально. Нижняя граница 40px — всё ещё выше минимально читаемого 36px из ТЗ §7.
    """
    if not text:
        return max_size_px
    usable_w = box_w_px - 48  # отступы по 24px с каждой стороны, см. text_x в _build_banner_filters
    size = max_size_px
    while size > min_size_px:
        estimated_w = len(text) * size * avg_char_width_ratio
        if estimated_w <= usable_w:
            return size
        size -= 2
    return min_size_px


def caption_margin_v_above_banner(canvas_h: int, banner_cfg: dict, gap_px: int = 40) -> int:
    """
    Субтитры не должны залезать в зону импульсной плашки оффера (см. рендер-тест: без этого
    расчёта караоке-текст оказывался ВНУТРИ чёрного бокса баннера). ASS alignment=2 (bottom-center)
    считает MarginV от НИЗА кадра, поэтому margin_v = canvas_h - (banner_y1 - gap_px).
    """
    l2 = banner_cfg.get("layer_2_offer_banner", {})
    if not l2.get("enabled"):
        return int(canvas_h * 0.20)  # баннера нет — дефолтный отступ от низа
    banner_y1 = l2["position"]["y1"]
    return max(200, canvas_h - (banner_y1 - gap_px))


@dataclass
class RenderResult:
    output_path: Path
    duration_sec: float
    ffmpeg_cmd: list[str]


def probe_duration_sec(path: Path) -> float:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    return float(probe.stdout.strip() or 0)


def check_libass_available() -> bool:
    """Деградация вместо падения, если сборка ffmpeg без libass (идея из Verticals v3, ТЗ §2)."""
    try:
        out = subprocess.run(["ffmpeg", "-filters"], capture_output=True, text=True, timeout=10)
        return " ass " in out.stdout or "ass  " in out.stdout or "\nass " in out.stdout
    except FileNotFoundError:
        return False


def _crop_pan_filter(scene_idx: int, in_w: int, in_h: int, out_w: int, out_h: int,
                      duration: float, direction: str) -> str:
    """
    Плавный пан БЕЗ zoompan. Кадр рендерится крупнее (supersample) и вырезается движущимся
    окном crop_w x crop_h — координаты x/y считаются выражением от t, ffmpeg делает это дёшево.
    """
    #供给 supersampled фон 1.15x от целевого размера, чтобы было куда панорамировать
    ss_w, ss_h = int(out_w * 1.15), int(out_h * 1.15)
    crop_w, crop_h = out_w, out_h
    max_x, max_y = ss_w - crop_w, ss_h - crop_h

    exprs = {
        "in": (f"x='{max_x}/2'", f"y='{max_y}*(1-t/{duration})/2'"),
        "out": (f"x='{max_x}/2'", f"y='{max_y}*(t/{duration})/2'"),
        "left": (f"x='{max_x}*(1-t/{duration})'", f"y='{max_y}/2'"),
        "right": (f"x='{max_x}*(t/{duration})'", f"y='{max_y}/2'"),
    }
    x_expr, y_expr = exprs.get(direction, exprs["in"])

    return (
        f"scale={ss_w}:{ss_h}:force_original_aspect_ratio=increase,"
        f"crop={ss_w}:{ss_h},"
        f"crop={crop_w}:{crop_h}:{x_expr}:{y_expr}"
    )


def build_scene_clip(scene: Scene, out_dir: Path, canvas_w: int, canvas_h: int) -> Path:
    """Один статичный кадр -> клип с медленным паном. Дешёвая альтернатива AI-видео (ТЗ §1)."""
    out_path = out_dir / f"scene_{scene.index:02d}.mp4"
    pan = _crop_pan_filter(scene.index, canvas_w, canvas_h, canvas_w, canvas_h,
                            scene.duration_sec, scene.pan_direction)

    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", scene.image_path,
        "-t", str(scene.duration_sec),
        "-vf", f"{pan},fps=30,setsar=1",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def concat_scenes(scene_clips: list[Path], out_dir: Path, job_id: str) -> Path:
    """Concat-demuxer вместо посегментного перекодирования (паттерн из MoneyPrinterTurbo, ТЗ §2)."""
    list_path = out_dir / f"{job_id}_concat.txt"
    list_path.write_text(
        "\n".join(f"file '{p.resolve()}'" for p in scene_clips), encoding="utf-8"
    )
    out_path = out_dir / f"{job_id}_scenes.mp4"
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def two_pass_loudnorm(in_audio: Path, out_audio: Path,
                       target_i: float = -14.0, target_tp: float = -1.0, target_lra: float = 11.0) -> Path:
    """I=-14 LUFS, TP=-1.0 dBTP — попадает в нормализацию YouTube и безопасно для TikTok/Reels (ТЗ §9)."""
    measure_cmd = [
        "ffmpeg", "-i", str(in_audio),
        "-af", f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}:print_format=json",
        "-f", "null", "-",
    ]
    proc = subprocess.run(measure_cmd, capture_output=True, text=True)
    # loudnorm печатает JSON-блок в stderr, последний '{...}' в выводе
    stderr = proc.stderr
    json_start = stderr.rfind("{")
    measured = json.loads(stderr[json_start:])

    apply_cmd = [
        "ffmpeg", "-y", "-i", str(in_audio),
        "-af", (
            f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}:"
            f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
            f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
            f"offset={measured['target_offset']}:linear=true:print_format=summary"
        ),
        "-ar", "48000", "-c:a", "aac", "-b:a", "192k",
        str(out_audio),
    ]
    subprocess.run(apply_cmd, check=True, capture_output=True)
    return out_audio


def _banner_enable_expression(timing: dict, total_duration_sec: float) -> str:
    """
    Строит ffmpeg enable-выражение с двумя окнами показа: импульс на impulse_window_pct
    и финальный повтор последние final_repeat_sec секунд. between(t,a,b) — булево окно,
    сумма нескольких between() даёт "или" для нескольких непересекающихся интервалов.
    """
    hook_silence = timing["hook_silence_sec"]
    pct_lo, pct_hi = timing["impulse_window_pct"]
    impulse_dur = timing["impulse_duration_sec"][1]  # верхняя граница 3-5с

    window_start = max(hook_silence, total_duration_sec * pct_lo)
    window_end_cap = total_duration_sec * pct_hi
    impulse_start = window_start
    impulse_end = min(impulse_start + impulse_dur, window_end_cap)
    if impulse_end <= impulse_start:
        impulse_end = impulse_start + 0.1  # защита от вырожденного окна на очень коротких роликах

    parts = [f"between(t,{impulse_start:.2f},{impulse_end:.2f})"]

    if timing.get("final_repeat"):
        final_sec = timing.get("final_repeat_sec", 2.5)
        final_start = max(impulse_end, total_duration_sec - final_sec)
        if final_start < total_duration_sec:
            parts.append(f"between(t,{final_start:.2f},{total_duration_sec:.2f})")

    return "+".join(parts)


def _build_banner_filters(banner_cfg: dict, base_label: str, fonts_dir: Path,
                           total_duration_sec: float, offer_text: str = "") -> tuple[str, str]:
    """Двухслойный баннер: постоянный микро-бренд + импульсная плашка оффера (ТЗ §7)."""
    filters = []
    cur = base_label

    l1 = banner_cfg["layer_1_micro_brand"]
    if l1.get("enabled"):
        text = l1["text"].replace(":", r"\:").replace("'", r"\'")
        x, y = l1["position"]["x"], l1["position"]["y"]
        opacity = l1["opacity"]
        filters.append(
            f"[{cur}]drawtext=fontfile={fonts_dir}/Montserrat-SemiBold.ttf:text='{text}':"
            f"fontcolor=white@{opacity}:fontsize={l1['height_px']}:x={x}:y={y}:"
            f"box=1:boxcolor=black@0.35:boxborderw=10[l1out]"
        )
        cur = "l1out"

    l2 = banner_cfg["layer_2_offer_banner"]
    if l2.get("enabled"):
        pos = l2["position"]
        x1, y1, x2, y2 = pos["x1"], pos["y1"], pos["x2"], pos["y2"]
        enable_expr = _banner_enable_expression(l2["timing"], total_duration_sec)

        # Импульсный показ: 0-2с скрыт (см. ТЗ §7 "первые 2 секунды чисто, иначе поднимается
        # swipe-away"), затем окно impulse_window_pct + финальный повтор поверх действия.
        # enable-выражение прячет баннер МЕЖДУ импульсами — не статичный эндкард, ролик уходит в петлю.
        filters.append(
            f"[{cur}]drawbox=x={x1}:y={y1}:w={x2 - x1}:h={y2 - y1}:"
            f"color=black@{l2['background_opacity']}:t=fill:"
            f"enable='{enable_expr}'[l2box]"
        )
        cur = "l2box"

        if offer_text:
            safe_text = offer_text.replace(":", r"\:").replace("'", r"\'")
            fitted_size = fit_offer_fontsize(offer_text, x2 - x1, l2["text_size_px"])
            text_x = x1 + 24
            text_y = y1 + (y2 - y1) // 2 - fitted_size // 2
            filters.append(
                f"[{cur}]drawtext=fontfile={fonts_dir}/Montserrat-Bold.ttf:text='{safe_text}':"
                f"fontcolor=white:fontsize={fitted_size}:x={text_x}:y={text_y}:"
                f"enable='{enable_expr}'[l2text]"
            )
            cur = "l2text"

    return ";".join(filters), cur


def render_final_video(
    scenes_video: Path,
    audio_path: Path,
    ass_subtitle_path: Path,
    banner_cfg: dict,
    offer_text: str,
    out_path: Path,
    fonts_dir: Path = None,
) -> RenderResult:
    fonts_dir = fonts_dir or SETTINGS.fonts_dir

    libass_ok = check_libass_available()
    ass_filter = f"ass={ass_subtitle_path}:fontsdir={fonts_dir}" if libass_ok else None
    if not libass_ok:
        # Мягкая деградация вместо падения (паттерн Verticals v3, ТЗ §2)
        print("[render] ПРЕДУПРЕЖДЕНИЕ: libass недоступен в этой сборке ffmpeg — субтитры не будут прожжены")

    total_duration_sec = probe_duration_sec(audio_path)
    banner_filter_chain, banner_out_label = _build_banner_filters(
        banner_cfg, "0:v", fonts_dir, total_duration_sec, offer_text
    )

    filter_parts = []
    cur_label = "0:v"
    if banner_filter_chain:
        filter_parts.append(banner_filter_chain)
        cur_label = banner_out_label
    if ass_filter:
        filter_parts.append(f"[{cur_label}]{ass_filter}[vout]")
        cur_label = "vout"
    else:
        filter_parts.append(f"[{cur_label}]null[vout]")
        cur_label = "vout"

    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(scenes_video),
        "-i", str(audio_path),
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    duration = probe_duration_sec(out_path)
    return RenderResult(output_path=out_path, duration_sec=duration, ffmpeg_cmd=cmd)


def render_preview(out_path: Path, source_path: Path) -> Path:
    """Профиль превью для показа человеку в Telegram: ultrafast CRF30, в лимит 50 МБ Bot API (ТЗ §10)."""
    cmd = [
        "ffmpeg", "-y", "-i", str(source_path),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path
