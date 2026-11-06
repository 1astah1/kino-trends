"""Двойные караоке-субтитры клипа: дорожки `Source` и `Author` + свой заголовок (§6.3.7 п. 3).

Зритель должен с первого взгляда отличать речь исходника от голоса автора канала, поэтому у
дорожек разные стили: `Source` — белый текст с обводкой и жёлтой подсветкой активного слова (как
legacy `pipeline/subtitles.build_karaoke_ass`), `Author` — текст на плашке фирменного цвета.
Заголовок (`Title`) держится сверху, тезисы автора (`OnScreen`) — под ним.

Формат и хелперы ASS берутся из legacy `pipeline/subtitles.py` (только чтение): `_ass_time`,
`_ass_escape`, `_hex_to_ass_color`, `PlayRes 1080×1920`. Караоке без наложений: на каждый
интервал «активно слово k» — одно событие с подсветкой слова, поэтому полупрозрачная плашка
не рисуется дважды.

Зоны по умолчанию держатся в safe zone площадок (config/safe_zones.json `common_organic`:
x 65–895, y 192–1380) и не заходят в зону баннера y 1120–1370: низ субтитров — y 1080.
Если баннер ставит свою зону субтитров (`banner_place.compute_captions_zone`), её можно передать
через `CaptionLayout.from_rect(...)`. Заголовок и тезисы при этом уводятся из-под реальных
прямоугольников баннера (`CaptionLayout.avoiding`): баннер рисуется поверх ASS, и верхний слот
(y 230–480) иначе закрывал бы свой заголовок, который засчитывается в author_share.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoshorts.clipping.transcript import Word

_TERMINAL = (".", "?", "!", "…")
FONT_NAME = "Montserrat"


def _legacy() -> Any:
    from autoshorts.pipeline import subtitles as legacy

    return legacy


@dataclass(frozen=True, slots=True)
class Zone:
    x1: int
    y1: int
    x2: int
    y2: int

    def margins_bottom(self, canvas_w: int, canvas_h: int) -> tuple[int, int, int]:
        """(MarginL, MarginR, MarginV) для Alignment 2 (низ по центру)."""
        return self.x1, canvas_w - self.x2, canvas_h - self.y2

    def margins_top(self, canvas_w: int) -> tuple[int, int, int]:
        return self.x1, canvas_w - self.x2, self.y1


BANNER_AREA = Zone(65, 1120, 890, 1370)  # offer_area профиля common_organic
SAFE_TOP, SAFE_BOTTOM = 192, 1380  # safe zone common_organic по вертикали
ZONE_GAP = 16


def _overlaps(a: Zone, b: Zone) -> bool:
    return a.y2 > b.y1 and a.y1 < b.y2 and a.x2 > b.x1 and a.x1 < b.x2


def _place_band(
    zone: Zone,
    blocked: Sequence[Zone],
    *,
    top: int = SAFE_TOP,
    bottom: int = SAFE_BOTTOM,
    gap: int = ZONE_GAP,
) -> Zone | None:
    """Та же зона по высоте и ширине, сдвинутая по вертикали в ближайшее к исходному место, где
    она не задевает ни один прямоугольник из `blocked` (с зазором `gap`). None — места нет."""
    h = zone.y2 - zone.y1
    rows = [b for b in blocked if b.x2 > zone.x1 and b.x1 < zone.x2]

    def free(y1: int) -> bool:
        cand = Zone(zone.x1, y1, zone.x2, y1 + h)
        return (
            top <= y1
            and y1 + h <= bottom
            and not any(cand.y2 + gap > b.y1 and cand.y1 - gap < b.y2 for b in rows)
        )

    if free(zone.y1):
        return zone
    options = [top, bottom - h]
    for b in rows:
        options += [b.y2 + gap, b.y1 - gap - h]
    ok = sorted((y for y in options if free(y)), key=lambda y: (abs(y - zone.y1), y))
    return Zone(zone.x1, ok[0], zone.x2, ok[0] + h) if ok else None


@dataclass(slots=True)
class CaptionLayout:
    canvas_w: int = 1080
    canvas_h: int = 1920
    source_zone: Zone = field(default_factory=lambda: Zone(65, 820, 895, 1080))
    author_zone: Zone = field(default_factory=lambda: Zone(65, 820, 895, 1080))
    title_zone: Zone = field(default_factory=lambda: Zone(65, 280, 895, 440))
    onscreen_zone: Zone = field(default_factory=lambda: Zone(65, 480, 895, 640))

    @classmethod
    def from_rect(cls, x: int, y: int, x2: int, y2: int, **kw: Any) -> CaptionLayout:
        z = Zone(int(x), int(y), int(x2), int(y2))
        return cls(source_zone=z, author_zone=z, **kw)

    def check_banner_clear(self, banner: Zone = BANNER_AREA) -> list[str]:
        return self.check_clear([banner])

    def check_clear(self, rects: Sequence[Zone]) -> list[str]:
        """Какие зоны задевают хоть один прямоугольник (баннер, его слои)."""
        bad = []
        for name in ("source_zone", "author_zone", "title_zone", "onscreen_zone"):
            z: Zone = getattr(self, name)
            if any(_overlaps(z, r) for r in rects):
                bad.append(name)
        return bad

    def avoiding(
        self, rects: Sequence[Zone], *, gap: int = ZONE_GAP, block_captions: bool = True
    ) -> tuple[CaptionLayout, list[str]]:
        """Раскладка, где заголовок и тезисы не под баннером: каждая зона сдвигается по вертикали в
        ближайшую свободную полосу safe zone, не задевая баннер, субтитры и друг друга.

        Возвращает (новая раскладка, какие зоны сдвинуты — `title_zone:280->436`). Зоны субтитров
        Source/Author не двигаются: их ставит баннерный план (`captions_zone`). `block_captions=False` —
        субтитров в ролике нет (режим none), их полоса свободна для заголовка и тезисов.
        """
        rects = [r for r in rects if r.x2 > r.x1 and r.y2 > r.y1]
        if not rects:
            return self, []
        moved: list[str] = []
        blocked = [*rects, *([self.source_zone, self.author_zone] if block_captions else [])]
        zones: dict[str, Zone] = {}
        for name in ("title_zone", "onscreen_zone"):
            z: Zone = getattr(self, name)
            nz = _place_band(z, blocked, gap=gap) or z
            if nz != z:
                moved.append(f"{name}:{z.y1}->{nz.y1}")
            zones[name] = nz
            blocked.append(nz)
        return (
            CaptionLayout(
                canvas_w=self.canvas_w,
                canvas_h=self.canvas_h,
                source_zone=self.source_zone,
                author_zone=self.author_zone,
                title_zone=zones["title_zone"],
                onscreen_zone=zones["onscreen_zone"],
            ),
            moved,
        )


def banner_zones(banner: Any, *, canvas: tuple[int, int] = (1080, 1920)) -> list[Zone]:
    """Прямоугольники баннера на канве ASS из `BannerPlan.to_json()` (`rects` — слои в координатах
    дизайна 1080×1920, `items` — наложения в пикселях `out_size`) или из самого плана."""
    out: list[Zone] = []
    if banner is None:
        return out
    data = banner if isinstance(banner, dict) else None
    if data is None:
        to_json = getattr(banner, "to_json", None)
        try:
            data = to_json() if callable(to_json) else None
        except Exception:  # noqa: BLE001
            data = None
    if data is None:
        rects = getattr(banner, "output_rects", None) or {}
        for r in rects.values():
            try:
                out.append(Zone(int(r.x), int(r.y), int(r.x2), int(r.y2)))
            except (AttributeError, TypeError, ValueError):
                continue
        return out
    cw, ch = canvas
    ow, oh = (data.get("out_size") or [cw, ch])[:2]
    kx = cw / float(ow or cw)
    ky = ch / float(oh or ch)
    for r in (data.get("rects") or {}).values():
        try:
            out.append(Zone(int(r["x1"]), int(r["y1"]), int(r["x2"]), int(r["y2"])))
        except (KeyError, TypeError, ValueError):
            continue
    for it in data.get("items") or []:
        try:
            x, y, w, h = (float(it[k]) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError):
            continue
        out.append(Zone(round(x * kx), round(y * ky), round((x + w) * kx), round((y + h) * ky)))
    return out


@dataclass(slots=True)
class CaptionStyle:
    name: str
    font_size: int
    primary: str  # #RRGGBB
    highlight: str
    outline: int = 6
    outline_color: str = "#000000"
    back_color: str = "#000000"
    back_alpha: int = 0x80  # 00 — непрозрачно, FF — прозрачно (ASS)
    border_style: int = 1  # 1 — обводка, 3 — плашка
    bold: bool = True
    shadow: int = 2
    font: str = FONT_NAME

    def ass_line(self, alignment: int, margins: tuple[int, int, int]) -> str:
        lg = _legacy()
        primary = lg._hex_to_ass_color(self.primary)
        secondary = lg._hex_to_ass_color(self.highlight)
        outline = lg._hex_to_ass_color(self.outline_color)
        back = lg._hex_to_ass_color(self.back_color).replace("&H00", f"&H{self.back_alpha:02X}", 1)
        ml, mr, mv = margins
        return (
            f"Style: {self.name},{self.font},{self.font_size},{primary},{secondary},{outline},{back},"
            f"{-1 if self.bold else 0},0,0,0,100,100,0,0,{self.border_style},{self.outline},{self.shadow},"
            f"{alignment},{ml},{mr},{mv},1"
        )


SOURCE_STYLE = CaptionStyle("Source", 74, "#FFFFFF", "#F2C14E", outline=6)
AUTHOR_STYLE = CaptionStyle(
    "Author",
    66,
    "#FFFFFF",
    "#7FE3FF",
    outline=14,
    outline_color="#1D3F8C",
    back_color="#1D3F8C",
    back_alpha=0x10,
    border_style=3,
    shadow=0,
)
TITLE_STYLE = CaptionStyle(
    "Title",
    56,
    "#FFFFFF",
    "#FFFFFF",
    outline=16,
    outline_color="#111111",
    back_color="#111111",
    back_alpha=0x30,
    border_style=3,
    shadow=0,
)
ONSCREEN_STYLE = CaptionStyle(
    "OnScreen",
    56,
    "#111111",
    "#111111",
    outline=14,
    outline_color="#F2C14E",
    back_color="#F2C14E",
    back_alpha=0x00,
    border_style=3,
    shadow=0,
)


def group_words(
    words: Sequence[Word], *, max_words: int = 3, max_chars: int = 22, max_gap: float = 0.45
) -> list[list[Word]]:
    """Группы по 1–3 слова на экран (ТЗ §2): разрыв на конце фразы, паузе и по длине строки."""
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if cur:
            text_len = sum(len(x.w) + 1 for x in cur) + len(w.w)
            gap = w.s - cur[-1].e
            if (
                len(cur) >= max_words
                or text_len > max_chars
                or gap > max_gap
                or cur[-1].w.rstrip("»\"')").endswith(_TERMINAL)
            ):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


CAPTION_HOLD_SEC = 0.4  # группа держится на экране в паузе до следующей, чтобы не мигать


def _karaoke_events(
    words: Sequence[Word], style: CaptionStyle, layer: int = 0, *, hold: float = CAPTION_HOLD_SEC
) -> list[str]:
    lg = _legacy()
    hl = lg._hex_to_ass_color(style.highlight).replace("&H00", "&H", 1)
    out: list[str] = []
    groups = group_words(words)
    for gi, grp in enumerate(groups):
        g0 = grp[0].s
        nxt = groups[gi + 1][0].s if gi + 1 < len(groups) else float("inf")
        g1 = max(grp[-1].e, min(grp[-1].e + hold, nxt))
        esc = [lg._ass_escape(w.w) for w in grp]

        def text(active: int | None, esc: list[str] = esc) -> str:
            parts = []
            for k, e in enumerate(esc):
                parts.append(f"{{\\c{hl}&}}{e}{{\\r}}" if k == active else e)
            return " ".join(parts)

        marks: list[tuple[float, int | None]] = []
        if grp[0].s > g0 + 1e-3:
            marks.append((g0, None))
        for k, w in enumerate(grp):
            marks.append((w.s, k))
        for (a, act), (b, _) in zip(marks, [*marks[1:], (g1, None)], strict=True):
            if b - a < 0.01:
                continue
            out.append(
                f"Dialogue: {layer},{lg._ass_time(a)},{lg._ass_time(b)},{style.name},,0,0,0,,{text(act)}"
            )
    return out


@dataclass(slots=True)
class OverlayText:
    text: str
    start: float
    end: float
    kind: str = "on_screen"  # title | on_screen


def build_multitrack_ass(
    out_path: str | Path,
    *,
    source_words: Sequence[Word] = (),
    author_words: Sequence[Word] = (),
    texts: Sequence[OverlayText] = (),
    layout: CaptionLayout | None = None,
    source_style: CaptionStyle = SOURCE_STYLE,
    author_style: CaptionStyle = AUTHOR_STYLE,
    title_style: CaptionStyle = TITLE_STYLE,
    onscreen_style: CaptionStyle = ONSCREEN_STYLE,
) -> Path:
    """ASS с дорожками Source/Author и текстами Title/OnScreen. Совместим с фильтром `ass`."""
    lay = layout or CaptionLayout()
    cw, ch = lay.canvas_w, lay.canvas_h
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {cw}",
        f"PlayResY: {ch}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        source_style.ass_line(2, lay.source_zone.margins_bottom(cw, ch)),
        author_style.ass_line(2, lay.author_zone.margins_bottom(cw, ch)),
        title_style.ass_line(8, lay.title_zone.margins_top(cw)),
        onscreen_style.ass_line(8, lay.onscreen_zone.margins_top(cw)),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    lg = _legacy()
    events: list[str] = []
    for t in texts:
        if t.end - t.start < 0.05 or not t.text.strip():
            continue
        style = title_style if t.kind == "title" else onscreen_style
        events.append(
            f"Dialogue: 2,{lg._ass_time(t.start)},{lg._ass_time(t.end)},{style.name},,0,0,0,,"
            f"{lg._ass_escape(t.text)}"
        )
    events += _karaoke_events(source_words, source_style, layer=0)
    events += _karaoke_events(author_words, author_style, layer=1)
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(header + events) + "\n", encoding="utf-8")
    return p


def captions_from_edl(
    edl: Any,
    transcript_words: Sequence[Word],
    out_path: str | Path,
    *,
    layout: CaptionLayout | None = None,
    hide_source_under_vo: bool = True,
    mode: str = "both",
    styles: Mapping[str, CaptionStyle] | None = None,
) -> Path:
    """ASS для EDL: слова исходника на выходной шкале, реплики автора, заголовок и тезисы.
    `mode` — режим субтитров слоя автора: both | source | author | none; заголовок и тезисы — всегда.
    `styles` — стили канала (`source_style`, `author_style`, `title_style`, `onscreen_style`)."""
    texts = [OverlayText(t.text, t.start, t.end, t.kind) for t in edl.texts]
    source_words: Sequence[Word] = ()
    if mode in ("both", "source"):
        source_words = edl.source_words(transcript_words, hide_under_vo=hide_source_under_vo)
    author_words: Sequence[Word] = edl.author_words() if mode in ("both", "author") else ()
    return build_multitrack_ass(
        out_path,
        source_words=source_words,
        author_words=author_words,
        texts=texts,
        layout=layout,
        **dict(styles or {}),
    )


def parse_ass_events(path: str | Path) -> list[dict[str, Any]]:
    """Разбор Dialogue-строк (для тестов и QC): [{layer, start, end, style, text}]."""

    def sec(ts: str) -> float:
        h, m, s = ts.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line[len("Dialogue:") :].strip().split(",", 9)
        out.append(
            {
                "layer": int(parts[0]),
                "start": sec(parts[1]),
                "end": sec(parts[2]),
                "style": parts[3],
                "text": parts[9],
            }
        )
    return out


__all__ = [
    "AUTHOR_STYLE",
    "BANNER_AREA",
    "ONSCREEN_STYLE",
    "SOURCE_STYLE",
    "TITLE_STYLE",
    "CaptionLayout",
    "CaptionStyle",
    "OverlayText",
    "Zone",
    "banner_zones",
    "build_multitrack_ass",
    "captions_from_edl",
    "group_words",
    "parse_ass_events",
]
