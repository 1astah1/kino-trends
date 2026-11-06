"""Оформление канала «кадр в фигуре на живом фоне» (вместо размытой копии кадра).

Ролик собирается так (стиль канала, `channels.profile.frame_style`):

* фон — зацикленный спокойный ролик (небо, море, дождь…) на весь кадр 1080×1920, чуть притемнён;
* кадр фильма — квадратный кроп вокруг того же центра, что выбрал рефрейм (слежение за говорящим
  сохраняется), в фигуре: круг (`circle`) или квадрат со скруглёнными углами (`rounded`);
* под фигурой мягкая тень, по краю — обводка цвета канала;
* субтитры — крупный белый текст с чёрной обводкой внутри фигуры снизу, слово подсвечивается
  акцентом канала; заголовок и тезисы — над фигурой.

Стиль у каждого канала свой (форма, фон, цвета, голос) — это оформление канала, а не попытка
спрятать исходник: распознавание правообладателя (Content ID) идёт по звуку и картинке фильма.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from autoshorts.clipping.captions import CaptionLayout, CaptionStyle, Zone

CANVAS_W, CANVAS_H = 1080, 1920
SAFE_SUB_BOTTOM = 1440  # низ субтитров: ниже — подпись ролика и ник канала в плеере Shorts
SAFE_SUB_RIGHT = 930  # правый край субтитров: правее — колонка кнопок (зона симметрична: 150–930)
SHAPES = ("circle", "rounded", "auto")


@dataclass(slots=True)
class FrameStyle:
    shape: str = "circle"
    size: int = 900  # диаметр круга / сторона квадрата, px канвы
    width: int = 0  # ширина фигуры (0 — size); прямоугольник для сцен, где лица не влезают в круг
    height: int = 0
    rect_w: int = 1000  # прямоугольник режима auto, если лица не помещаются в круг (4:3)
    rect_h: int = 750
    cx: int = 540
    cy: int = 1040
    radius: int = 72  # скругление углов для rounded
    ring_px: int = 10
    ring_color: str = "#FFFFFF"
    shadow_alpha: int = 150  # 0..255
    bg_path: str = ""  # абсолютный путь фонового ролика (подставляет сервис по набору канала)
    bg_set: str = "nature"
    bg_dim: float = 0.10  # 0..0.5 — насколько притемнить фон
    accent: str = "#FFD84D"  # подсветка слова в субтитрах
    caption_size: int = 80
    title_size: int = 56  # заголовок над фигурой; у викторины это вопрос-хук первой секунды (78 у каналов с 29.10)
    mask_path: str = "frame_mask.png"  # файлы рисуются в каталоге рендера
    shadow_path: str = "frame_shadow.png"
    ring_path: str = "frame_ring.png"

    @classmethod
    def coerce(cls, data: Any) -> FrameStyle | None:
        if isinstance(data, FrameStyle):
            return data
        if not isinstance(data, Mapping) or not data.get("enabled", True):
            return None
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        fs = cls(**known)
        fs.shape = fs.shape if fs.shape in SHAPES else "circle"
        fs.size = max(400, min(1040, int(fs.size)) // 2 * 2)
        fs.width = max(0, min(1060, int(fs.width or 0))) // 2 * 2
        fs.height = max(0, min(1600, int(fs.height or 0))) // 2 * 2
        fs.cx = max(fs.w // 2, min(CANVAS_W - fs.w // 2, int(fs.cx)))
        fs.cy = max(fs.h // 2, min(CANVAS_H - fs.h // 2, int(fs.cy)))
        fs.bg_dim = max(0.0, min(0.5, float(fs.bg_dim)))
        return fs

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def w(self) -> int:
        return int(self.width or self.size)

    @property
    def h(self) -> int:
        return int(self.height or self.size)

    @property
    def x1(self) -> int:
        return self.cx - self.w // 2

    @property
    def y1(self) -> int:
        return self.cy - self.h // 2

    @property
    def x2(self) -> int:
        return self.x1 + self.w

    @property
    def y2(self) -> int:
        return self.y1 + self.h


# ---------------------------------------------------------------- маски, тень, обводка (PNG)


def _shape(draw: Any, box: tuple[int, int, int, int], fs: FrameStyle, **kw: Any) -> None:
    if fs.shape == "circle":
        draw.ellipse(box, **kw)
    else:
        draw.rounded_rectangle(box, radius=fs.radius, **kw)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)) if len(h) == 6 else (255, 255, 255)


def draw_assets(fs: FrameStyle, workdir: str | Path) -> dict[str, Path]:
    """Маска фигуры (w×h, белое — видно), тень и обводка на всю канву. Сглаживание — рисованием
    в 4× и уменьшением."""
    from PIL import Image, ImageDraw, ImageFilter

    wd = Path(workdir)
    k = 4
    fw, fh = fs.w, fs.h
    big = Image.new("L", (fw * k, fh * k), 0)
    _shape(
        ImageDraw.Draw(big),
        (0, 0, fw * k - 1, fh * k - 1),
        FrameStyle(**{**fs.to_json(), "radius": fs.radius * k}),
        fill=255,
    )
    big.resize((fw, fh), Image.LANCZOS).save(wd / fs.mask_path)

    shadow = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    _shape(
        ImageDraw.Draw(shadow),
        (fs.x1 - 6, fs.y1 + 10, fs.x2 + 6, fs.y2 + 26),
        fs,
        fill=(0, 0, 0, fs.shadow_alpha),
    )
    shadow.filter(ImageFilter.GaussianBlur(26)).save(wd / fs.shadow_path)

    ring_big = Image.new("RGBA", (CANVAS_W * 2, CANVAS_H * 2), (0, 0, 0, 0))
    r, g, b = _rgb(fs.ring_color)
    half = fs.ring_px  # обводка наполовину наружу, наполовину на кадр
    box = (2 * (fs.x1 - half // 2), 2 * (fs.y1 - half // 2), 2 * (fs.x2 + half // 2), 2 * (fs.y2 + half // 2))
    _shape(
        ImageDraw.Draw(ring_big),
        box,
        FrameStyle(**{**fs.to_json(), "radius": (fs.radius + half // 2) * 2}),
        outline=(r, g, b, 255),
        width=fs.ring_px * 2,
    )
    ring_big.resize((CANVAS_W, CANVAS_H), Image.LANCZOS).save(wd / fs.ring_path)
    return {"mask": wd / fs.mask_path, "shadow": wd / fs.shadow_path, "ring": wd / fs.ring_path}


# ---------------------------------------------------------------- квадратный кроп из 9:16


_CMD_X = re.compile(r"^(\s*[\d.]+\s+crop@\w+\s+x\s+)(-?\d+)(\s*;.*)$")


def square_crop(
    crop: Mapping[str, Any] | None,
    src_w: int,
    src_h: int,
    aspect: float = 1.0,
    box: Mapping[str, int] | None = None,
) -> dict[str, int]:
    """Кроп с пропорцией фигуры (1 — квадрат, 4/3 — прямоугольник) вокруг центра кропа рефрейма,
    максимального размера, какой позволяет кадр источника. `box` — область картинки без чёрных полос
    (detect_content_box): кроп строится внутри неё, полосы в рамку не попадают."""
    src_w, src_h = int(src_w), int(src_h)
    bx, by, bw, bh = (int(box["x"]), int(box["y"]), int(box["w"]), int(box["h"])) if box else (0, 0, src_w, src_h)
    ch_ = bh
    cw_ = int(round(ch_ * aspect))
    if cw_ > bw:
        cw_, ch_ = bw, int(round(bw / aspect))
    cw_, ch_ = cw_ // 2 * 2, ch_ // 2 * 2
    c = dict(crop or {})
    cw, ch = int(c.get("w") or src_w), int(c.get("h") or src_h)
    cx = int(c.get("x") or 0) + cw / 2.0
    cy = int(c.get("y") or 0) + ch / 2.0
    x = int(round(max(float(bx), min(bx + bw - cw_, cx - cw_ / 2.0))))
    y = int(round(max(float(by), min(by + bh - ch_, cy - ch_ / 2.0))))
    return {"x": x, "y": y, "w": cw_, "h": ch_}


_CROPDETECT = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")
_STREAM_SIZE = re.compile(r"Video:.*?(\d{2,5})x(\d{2,5})")


def detect_content_box(
    path: str | Path, start: float, end: float, src_w: int, src_h: int, *, samples: int = 4, timeout: float = 30.0
) -> dict[str, int] | None:
    """Область картинки без чёрных полос (letterbox у широкоэкранного кино, pillarbox у 4:3) в отрезке
    [start, end] файла, в координатах src_w×src_h. Объединение по нескольким точкам — тёмная сцена не
    урежет картинку. None — полос нет (< 4 %) или результат сомнительный."""
    boxes: list[tuple[int, int, int, int]] = []
    file_w = file_h = 0
    span = max(0.5, float(end) - float(start))
    for k in range(max(1, samples)):
        t = max(0.0, float(start) + span * (k + 0.5) / samples - 0.5)
        cmd = ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{t:.2f}", "-t", "1.0", "-i", str(path),
               "-vf", "cropdetect=limit=24:round=2:reset=0", "-an", "-f", "null", "-"]  # fmt: skip
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError):
            continue
        if not file_w:
            m = _STREAM_SIZE.search(res.stderr)
            if m:
                file_w, file_h = int(m.group(1)), int(m.group(2))
        found = _CROPDETECT.findall(res.stderr)
        if found:
            w, h, x, y = (int(v) for v in found[-1])
            boxes.append((x, y, w, h))
    if not boxes or not file_w:
        return None
    x1, y1 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x2, y2 = max(b[0] + b[2] for b in boxes), max(b[1] + b[3] for b in boxes)
    w, h = x2 - x1, y2 - y1
    if w >= file_w * 0.96 and h >= file_h * 0.96:
        return None  # полос нет
    if w < file_w * 0.3 or h < file_h * 0.3:
        return None  # слишком мало картинки — скорее тёмная сцена, не трогаем
    sx, sy = int(src_w) / file_w, int(src_h) / file_h
    return {"x": int(x1 * sx) // 2 * 2, "y": int(y1 * sy) // 2 * 2, "w": int(w * sx) // 2 * 2, "h": int(h * sy) // 2 * 2}


def choose_shape(fs: FrameStyle, shots: Any) -> FrameStyle:
    """Режим `auto`: круг, если бо́льшую часть клипа в кадре одно лицо и слежение держит его по центру;
    иначе (несколько людей по краям, лица не распознаются — мультфильмы) — скруглённый прямоугольник
    rect_w×rect_h, шире квадрата, чтобы никого не обрезать."""
    if fs.shape != "auto":
        return fs
    total = good = 0.0
    for sh in shots or []:
        d = (
            sh
            if isinstance(sh, Mapping)
            else {
                "start": getattr(sh, "start", 0),
                "end": getattr(sh, "end", 0),
                "mode": getattr(sh, "mode", ""),
                "stats": getattr(sh, "stats", {}),
            }
        )
        dur = max(0.0, float(d.get("end") or 0) - float(d.get("start") or 0))
        st = d.get("stats") or {}
        total += dur
        single = float(st.get("face_coverage") or 0) >= 0.6 and float(st.get("group_spread_share") or 0) < 0.3
        if d.get("mode") in ("track", "center") and single:
            good += dur
    data = fs.to_json()
    if total > 0 and good / total >= 0.6:
        data.update(shape="circle", width=0, height=0)
    else:
        data.update(shape="rounded", width=fs.rect_w, height=fs.rect_h)
    return FrameStyle.coerce(data) or fs


def shift_crop_cmd(text: str, old_w: int, side: int, src_w: int, x_min: int = 0, x_max: int | None = None) -> str:
    """Команды слежения `t crop@cK x N;` для кропа ширины old_w → для квадрата side (тот же центр);
    x_min/x_max — край области картинки без полос (по умолчанию весь кадр)."""
    hi = (src_w if x_max is None else int(x_max)) - side
    out = []
    for line in text.splitlines():
        m = _CMD_X.match(line)
        if m:
            x = int(m.group(2)) + old_w / 2.0 - side / 2.0
            x = int(round(max(float(x_min), min(hi, x))))
            line = f"{m.group(1)}{x}{m.group(3)}"
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def apply_to_edl(
    edl: Any, fs: FrameStyle, workdir: str | Path, src_w: int, src_h: int, box: Mapping[str, int] | None = None
) -> None:
    """Переводит кадрирование EDL в квадрат (`fit_blur` → `center`), переписывает файлы слежения,
    рисует маску/тень/обводку и кладёт стиль в `edl.meta["frame_style"]` для рендера. `box` — область
    картинки без чёрных полос: кроп и слежение не выходят за неё."""
    wd = Path(workdir)
    x_min = int(box["x"]) if box else 0
    x_max = int(box["x"]) + int(box["w"]) if box else src_w
    for v in edl.video:
        old = dict(v.crop or {"x": 0, "y": 0, "w": src_w, "h": src_h})
        sq = square_crop(old, src_w, src_h, fs.w / fs.h, box=box)
        if v.reframe == "track" and v.crop_cmd and (wd / v.crop_cmd).is_file():
            p = wd / v.crop_cmd
            p.write_text(
                shift_crop_cmd(
                    p.read_text(encoding="utf-8"), int(old.get("w") or src_w), sq["w"], src_w, x_min=x_min, x_max=x_max
                ),
                encoding="utf-8",
            )
        if v.reframe == "fit_blur":
            v.reframe = "center"
        v.crop = sq
    draw_assets(fs, wd)
    edl.meta["frame_style"] = fs.to_json()
    if box:
        edl.meta["content_box"] = dict(box)


# ---------------------------------------------------------------- текст: зоны и стили


def caption_layout(fs: FrameStyle) -> CaptionLayout:
    """Субтитры — внутри фигуры снизу; заголовок и тезисы — над фигурой, под стикером (он стоит
    сверху, y ≈ 100–432)."""
    inset = 90 if fs.shape == "circle" else 50
    # безопасная зона Shorts (docs/SHORTS_RULES.md §5): ниже ~1440 — подпись, ник и кнопка подписки, справа
    # от ~930 — колонка лайк/комментарии/поделиться; субтитры туда не пускаем, зона симметрична по центру
    bottom = min(fs.y2 - (110 if fs.shape == "circle" else 60), SAFE_SUB_BOTTOM)
    left = max(fs.x1 + inset, CANVAS_W - SAFE_SUB_RIGHT)
    right = min(fs.x2 - inset, SAFE_SUB_RIGHT)
    sub = Zone(left, bottom - (180 if fs.shape == "circle" else 230), right, bottom)
    top = 440  # нижний край стикера сверху
    th = max(105, int(fs.title_size * 2.4))  # две строки заголовка
    t1 = max(top, fs.y1 - 95 - th)
    title = Zone(70, t1, 1010, t1 + th)
    onscreen = Zone(70, t1 + th + 5, 1010, max(t1 + th + 55, fs.y1 - 10))
    return CaptionLayout(source_zone=sub, author_zone=sub, title_zone=title, onscreen_zone=onscreen)


def caption_styles(fs: FrameStyle) -> dict[str, CaptionStyle]:
    """Крупный белый текст с чёрной обводкой (как в популярных шортсах), слово — акцентом канала."""
    big = int(fs.caption_size)
    return {
        "source_style": CaptionStyle(
            "Source", big, "#FFFFFF", fs.accent, outline=7, outline_color="#000000", border_style=1, shadow=3
        ),
        "author_style": CaptionStyle(
            "Author",
            big - 4,
            "#FFFFFF",
            fs.accent,
            outline=7,
            outline_color="#000000",
            border_style=1,
            shadow=3,
        ),
        "title_style": CaptionStyle(
            "Title", int(fs.title_size), "#FFFFFF", "#FFFFFF", outline=5 if fs.title_size < 70 else 7,
            outline_color="#000000", border_style=1, shadow=2,
        ),
        "onscreen_style": CaptionStyle(
            "OnScreen", 48, fs.accent, fs.accent, outline=5, outline_color="#000000", border_style=1, shadow=2
        ),
    }


# ---------------------------------------------------------------- граф ffmpeg (используется render_clip)


def compose_chains(
    fs: Mapping[str, Any], *, fg_label: str, first_input: int, duration: float, fps: float, out_label: str
) -> tuple[list[str], list[str]]:
    """Входы и цепочки: фон (зацикленный ролик) → тень → кадр в маске → обводка → `out_label`."""
    st = FrameStyle.coerce(fs) or FrameStyle()
    d = f"{duration + 1.0:.3f}"
    inputs = [
        "-stream_loop",
        "-1",
        "-t",
        d,
        "-i",
        st.bg_path,
        "-loop",
        "1",
        "-t",
        d,
        "-i",
        st.mask_path,
        "-loop",
        "1",
        "-t",
        d,
        "-i",
        st.shadow_path,
        "-loop",
        "1",
        "-t",
        d,
        "-i",
        st.ring_path,
    ]
    ib, im, ish, ir = first_input, first_input + 1, first_input + 2, first_input + 3
    w, h = CANVAS_W, CANVAS_H
    dim = -round(st.bg_dim * 0.6, 3)
    chains = [
        f"[{ib}:v]fps={fps:g},scale={w}:{h}:force_original_aspect_ratio=increase:flags=bicubic,crop={w}:{h},"
        f"eq=brightness={dim}:saturation=1.08,setsar=1,format=yuv420p[fsbg]",
        f"[{im}:v]fps={fps:g},format=gray,scale={st.w}:{st.h}[fsmask]",
        f"[{fg_label}]format=yuva420p[fsfg0]",
        "[fsfg0][fsmask]alphamerge[fsfg]",
        f"[{ish}:v]fps={fps:g},format=rgba[fsshadow]",
        f"[{ir}:v]fps={fps:g},format=rgba[fsring]",
        "[fsbg][fsshadow]overlay=0:0:shortest=1[fsc0]",
        f"[fsc0][fsfg]overlay=x={st.x1}:y={st.y1}:shortest=1[fsc1]",
        f"[fsc1][fsring]overlay=0:0:shortest=1,format=yuv420p[{out_label}]",
    ]
    return inputs, chains
