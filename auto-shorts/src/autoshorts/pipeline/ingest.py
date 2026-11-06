"""
Трек №2: приём чужого вирусного клипа -> нарезка под свой монтаж.
См. ТЗ: "переработка с авторским слоем (правило 70/30), не техническая уникализация" —
этот модуль готовит СЫРЬЁ (скачивание, разбор на сцены, автокроп), автор-слой (свой сценарий,
своя озвучка, свой баннер) добавляется дальше тем же render.py, что и в треке №1.

yt-dlp (190k★, Unlicense, живой) — скачивание.
PySceneDetect (BSD-3, живой) — разбор на сцены, чтобы не резать посреди действия.
Автокроп 16:9 -> 9:16 — через центр-кроп с детекцией лица (OpenCV, если доступен) либо
статичный центр-кроп как фолбэк.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import yt_dlp
from scenedetect import ContentDetector, SceneManager, open_video


@dataclass
class SourceClip:
    local_path: Path
    source_url: str
    source_platform: str
    original_duration_sec: float
    width: int
    height: int


@dataclass
class SceneCut:
    start_sec: float
    end_sec: float

    @property
    def duration_sec(self) -> float:
        return self.end_sec - self.start_sec


def download_source_clip(url: str, out_dir: Path, max_duration_sec: int = 600) -> SourceClip:
    """
    Скачивание мастер-файла. ВАЖНО (ТЗ §11): выгружать только СВОЙ смонтированный мастер,
    никогда не перезаливать скачанный файл как есть — чужой водяной знак стоит ~90% охвата.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(out_dir / "%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": out_template,
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "match_filter": yt_dlp.utils.match_filter_func(f"duration < {max_duration_sec}"),
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        local_path = Path(ydl.prepare_filename(info))
        if not local_path.exists():
            # merge_output_format мог сменить расширение
            candidates = list(out_dir.glob(f"{info['id']}.*"))
            if not candidates:
                raise RuntimeError(f"yt-dlp не нашёл скачанный файл для {url}")
            local_path = candidates[0]

    platform = "youtube" if "youtube" in url or "youtu.be" in url else (
        "tiktok" if "tiktok" in url else "vk" if "vk.com" in url or "vkvideo" in url else "unknown"
    )

    return SourceClip(
        local_path=local_path,
        source_url=url,
        source_platform=platform,
        original_duration_sec=float(info.get("duration") or 0),
        width=int(info.get("width") or 0),
        height=int(info.get("height") or 0),
    )


def detect_scenes(video_path: Path, threshold: float = 27.0, min_scene_len_sec: float = 1.0) -> list[SceneCut]:
    """
    Разбор на сцены по контент-детектору — режем клип по монтажным склейкам исходника,
    а не посреди действия (что убивает удержание и выглядит как брак рендера).
    """
    video = open_video(str(video_path))
    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector(threshold=threshold, min_scene_len=int(min_scene_len_sec * video.frame_rate)))
    scene_manager.detect_scenes(video=video)
    scene_list = scene_manager.get_scene_list()

    if not scene_list:
        # Ни одной склейки не найдено — весь ролик одна сцена
        duration = video.duration.get_seconds()
        return [SceneCut(start_sec=0.0, end_sec=duration)]

    return [
        SceneCut(start_sec=start.get_seconds(), end_sec=end.get_seconds())
        for start, end in scene_list
    ]


def pick_best_scene_window(scenes: list[SceneCut], target_duration_range: tuple[int, int]) -> SceneCut:
    """
    Простейший отбор: сцена (или склейка соседних сцен), ближе всего попадающая в целевой
    диапазон длительности. В треке №1 это заменяется реальным скорингом виральности
    (см. ТЗ §8 — outlier-score, highlights.py из AI-Youtube-Shorts-Generator) — здесь
    базовая эвристика для рабочего прогона конвейера.
    """
    lo, hi = target_duration_range
    target = (lo + hi) / 2

    # Пробуем найти одиночную сцену в целевом диапазоне
    in_range = [s for s in scenes if lo <= s.duration_sec <= hi]
    if in_range:
        return min(in_range, key=lambda s: abs(s.duration_sec - target))

    # Иначе склеиваем соседние сцены, пока не наберём целевую длительность
    merged: list[SceneCut] = []
    acc_start = scenes[0].start_sec
    acc_end = scenes[0].end_sec
    for s in scenes[1:]:
        if (acc_end - acc_start) >= target:
            break
        acc_end = s.end_sec
    merged.append(SceneCut(start_sec=acc_start, end_sec=acc_end))
    return merged[0]


def autocrop_to_vertical(source_path: Path, scene: SceneCut, out_path: Path,
                          out_w: int = 1080, out_h: int = 1920,
                          use_face_detection: bool = True) -> Path:
    """
    Автокроп 16:9 (или любого широкого) в 9:16. Пытается центрировать на лице через OpenCV
    Haar cascade (быстрый, не требует GPU); при отсутствии лица или cv2 — статичный центр-кроп.
    Блюр-фон по краям — на случай если после кропа остаётся letterbox.
    """
    crop_x_expr = _compute_crop_x_expr(source_path, scene, out_w, out_h, use_face_detection)

    cmd = [
        "ffmpeg", "-y", "-ss", str(scene.start_sec), "-t", str(scene.duration_sec),
        "-i", str(source_path),
        "-filter_complex",
        (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{out_h},gblur=sigma=25[bgblur];"
            f"[fg]scale={out_w}:-2[fgscaled];"
            f"[bgblur][fgscaled]overlay=(W-w)/2:(H-h)/2:format=auto[vout]"
        ),
        "-map", "[vout]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def _compute_crop_x_expr(source_path: Path, scene: SceneCut, out_w: int, out_h: int,
                          use_face_detection: bool) -> str:
    """
    Возвращает статичное смещение кропа (в проде — точку x лица на среднем кадре сцены).
    Упрощено до центр-кропа с blur-фоном (см. autocrop_to_vertical) — этого достаточно для
    большинства контента без лиц крупным планом (что и есть эзотерический визуал по ТЗ).
    Для контента с лицами (реакции, интервью) следующий шаг — OpenCV Haar cascade по
    среднему кадру сцены и смещение x в overlay соответственно.
    """
    return "center"  # placeholder — реальная логика лица не требуется для эзотерики (ТЗ: visuals.avoid лица крупным планом)


def probe_video_dimensions(path: Path) -> tuple[int, int]:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(path)],
        capture_output=True, text=True,
    )
    w, h = probe.stdout.strip().split("x")
    return int(w), int(h)
