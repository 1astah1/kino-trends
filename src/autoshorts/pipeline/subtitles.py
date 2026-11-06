"""
Караоке-субтитры на ASS: отдельное Dialogue-событие на каждое слово с подсветкой активного —
именно так делают вирусные Shorts (ТЗ §2). Тег \\k/\\kf даёт классическую заливку, а вирусный
стиль — это смена цвета слова, что требует отдельных событий, а не одной строки с тегами.

Рендерится через ffmpeg-фильтр `ass` (требует libass — наличие проверяется перед рендером).
"""
from __future__ import annotations

from pathlib import Path

from autoshorts.models import WordTiming

ASS_HEADER_TEMPLATE = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Base,{font_name},{font_size},{base_color},&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},2,2,60,60,{margin_v},1
Style: Active,{font_name},{font_size},{highlight_color},&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},2,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_time(sec: float) -> str:
    if sec < 0:
        sec = 0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    cs = int(round((s - int(s)) * 100))
    return f"{h:d}:{m:02d}:{int(s):02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def _hex_to_ass_color(hex_color: str) -> str:
    """'#F2C14E' -> '&H004EC1F2' (ASS хранит цвет как BGR, альфа-байт 00 = непрозрачно)."""
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def build_karaoke_ass(words: list[WordTiming], out_path: Path,
                       highlight_color: str = "#F2C14E", base_color: str = "#FFFFFF",
                       font_name: str = "Montserrat", font_size: int = 84,
                       margin_v: int = 520, group_size: int = 3) -> Path:
    """
    Группирует слова по `group_size` (1-3 слова на экран — ТЗ §2: "1-3 слова на экран, кегль 72-110px").
    Внутри группы активное слово подсвечивается отдельным Dialogue-событием поверх базового.
    """
    header = ASS_HEADER_TEMPLATE.format(
        font_name=font_name,
        font_size=font_size,
        base_color=_hex_to_ass_color(base_color),
        highlight_color=_hex_to_ass_color(highlight_color),
        outline=6,
        margin_v=margin_v,
    )

    lines = [header]
    groups: list[list[WordTiming]] = [words[i:i + group_size] for i in range(0, len(words), group_size)]

    for group in groups:
        group_start = group[0].start_sec
        group_end = group[-1].end_sec
        group_text = " ".join(w.word for w in group)

        # Базовая строка группы — видна весь интервал группы
        lines.append(
            f"Dialogue: 0,{_ass_time(group_start)},{_ass_time(group_end)},Base,,0,0,0,,{_ass_escape(group_text)}"
        )

        # Поверх — активное слово подсвеченным цветом, ровно на его длительность
        for w in group:
            before = " ".join(x.word for x in group if x.start_sec < w.start_sec)
            after = " ".join(x.word for x in group if x.start_sec > w.start_sec)
            parts = []
            if before:
                parts.append(_ass_escape(before))
            parts.append(r"{\c" + _hex_to_ass_color(highlight_color).replace("&H00", "&H") + r"}"
                         + _ass_escape(w.word) + r"{\r}")
            if after:
                parts.append(_ass_escape(after))
            text = " ".join(parts)
            lines.append(f"Dialogue: 1,{_ass_time(w.start_sec)},{_ass_time(w.end_sec)},Base,,0,0,0,,{text}")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
