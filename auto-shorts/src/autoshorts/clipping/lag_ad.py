"""Рекламная вставка «видео тормозит → BPN» (владелец 29.10.2026: «добавлять эту херню везде в ролики»).

В интересный момент (граница фразы ближе к середине клипа) видео дёргается — несколько коротких стоп-кадров
последних долей секунды, как при низком fps, — затем замирает с затемнением и крутилкой загрузки
(`BUFFER_SEC`), выезжает плашка BPN и голос говорит слоган, плашка уходит, сцена идёт дальше.

Раскладка (`edl.layout_clip`, `LayoutConfig.lag_ad`) кладёт стоп-кадры и реплику `ad_slogan` (роль `ad`),
в `edl.meta["lag_ad"]` — где и сколько. Затемнение, крутилка и плашка накладываются в том же проходе
рендера (`overlay_chains`), поверх наклейки и субтитров. Плашка сейчас одна — BPN (`assets/`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

ASSETS = Path(__file__).resolve().parent / "assets"
BANNER_PNG = ASSETS / "lag_ad_banner_bpn.png"
SPINNER_PNG = ASSETS / "lag_ad_spinner.png"
SLOGAN = "Тормозит? Включи би-пи-эн. Интернет без тормозов."
SLOGAN_STYLE = (
    "You are a text-to-speech engine, not an assistant. You never reply, comment or answer questions. "
    "Your only output is speech of the exact text between <read> and </read>, word for word, in Russian, "
    "nothing before or after it. Delivery: punchy confident radio-ad announcer, energetic, short pauses "
    "between phrases, native Russian pronunciation; say BPN letter by letter as «би-пи-эн»."
)
# дёрганье: (насколько раньше точки, сколько держать) — четыре «кадра» с пропусками, ~0,5 с
STUTTER: tuple[tuple[float, float], ...] = ((0.27, 0.10), (0.20, 0.13), (0.13, 0.13), (0.07, 0.17))
BUFFER_SEC = 1.0  # зависание с крутилкой до плашки
TAIL_SEC = 0.45  # плашка держится после слогана
DIM = 0.38  # затемнение кадра на время зависания
MIN_FROM_START = 6.0  # не раньше — там вопрос и первая подсказка
MIN_TO_END = 8.0  # не позже — развязка и ответ
MIN_GAP_TO_INSERT = 3.0
MIN_SOURCE_SEC = 28.0  # вставка только в длинных роликах (владелец 29.10): короче — сцена важнее рекламы


def total_sec(slogan_sec: float) -> float:
    """Сколько секунд вставка добавляет к ролику."""
    return sum(d for _, d in STUTTER) + BUFFER_SEC + slogan_sec + TAIL_SEC


def pick_spot(boundaries: list[tuple[int, float]], start: float, end: float, taken: list[float]) -> tuple[int, float] | None:
    """(sid, момент) — граница фразы ближе всего к середине клипа, вдали от краёв и других вставок."""
    mid = (start + end) / 2
    ok = [
        (sid, c) for sid, c in boundaries
        if start + MIN_FROM_START <= c <= end - MIN_TO_END and all(abs(c - t) >= MIN_GAP_TO_INSERT for t in taken)
    ]  # fmt: skip
    return min(ok, key=lambda x: abs(x[1] - mid)) if ok else None


def _f(x: float) -> str:
    return f"{x:.3f}"


def overlay_chains(
    base: str, first_input: int, meta: dict[str, Any], *, fps: float, width: int, height: int, out: str
) -> tuple[list[str], str]:
    """Входы ffmpeg и цепочка: затемнение + крутилка + выезжающая плашка поверх `base` → `out`.

    `meta` — `edl.meta["lag_ad"]`: start (начало зависания на выходной шкале), dur (зависание целиком),
    buffer (крутилка до плашки), cy (центр окна с фильмом)."""
    a, d, b = float(meta["start"]), float(meta["dur"]), float(meta.get("buffer", BUFFER_SEC))
    cy = int(meta.get("cy") or 1090)
    hold = max(0.5, d - b)
    out_at = hold - 0.28
    spin, ban = first_input, first_input + 1
    inputs = [
        "-loop", "1", "-framerate", _f(fps), "-t", _f(b), "-i", str(meta.get("spinner") or SPINNER_PNG),
        "-loop", "1", "-framerate", _f(fps), "-t", _f(hold), "-i", str(meta.get("banner") or BANNER_PNG),
    ]  # fmt: skip
    # пружинка ease-out-back за 0,35 с, в конце — уменьшение на 18 % и растворение за 0,28 с
    scale = (
        f"if(lt(t,0.35),0.55+0.45*(1+2.70158*pow(t/0.35-1,3)+1.70158*pow(t/0.35-1,2)),"
        f"if(gt(t,{_f(out_at)}),1-0.18*(t-{_f(out_at)})/0.28,1))"
    )
    chain = ";".join([
        f"color=c=black@{DIM}:s={width}x{height}:r={_f(fps)}:d={_f(d)},format=rgba,setpts=PTS+{_f(a)}/TB[lagdim]",
        f"[{spin}:v]format=rgba,rotate=a=t*5:c=none:ow=iw:oh=ih,setpts=PTS+{_f(a)}/TB[lagspin]",
        f"[{ban}:v]format=rgba,scale=w='trunc(iw*{scale}/2)*2':h=-2:eval=frame,"
        f"fade=t=out:st={_f(out_at)}:d=0.28:alpha=1,setpts=PTS+{_f(a + b)}/TB[lagban]",
        f"[{base}][lagdim]overlay=0:0:enable='between(t,{_f(a)},{_f(a + d)})':eof_action=pass[lag1]",
        f"[lag1][lagspin]overlay=x=(W-w)/2:y={cy}-h/2:enable='between(t,{_f(a)},{_f(a + b)})':eof_action=pass[lag2]",
        f"[lag2][lagban]overlay=x=(W-w)/2:y={cy}-h/2:eval=frame:enable='between(t,{_f(a + b)},{_f(a + d)})'"
        f":eof_action=pass,format=yuv420p[{out}]",
    ])  # fmt: skip
    return inputs, chain


__all__ = ["BANNER_PNG", "BUFFER_SEC", "MIN_SOURCE_SEC", "SLOGAN", "SLOGAN_STYLE", "SPINNER_PNG", "STUTTER", "TAIL_SEC", "overlay_chains", "pick_spot", "total_sec"]
