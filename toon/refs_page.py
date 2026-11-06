"""Страница выбора направлений: python -m toon.refs_page OUT_DIR — index.html + кадры 720×1280 (jpg)."""

from __future__ import annotations

import html
import pathlib
import subprocess
import sys

from .directions import D, HW
from .gen_refs import OUT as REFS

CSS = """
:root{--bg:#0e0d13;--card:#17151f;--line:#2a2735;--text:#ece9f3;--mute:#a19cb0;--acc:#ff4fa3;--acc2:#5fd4ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{max-width:1280px;margin:0 auto;padding:28px 16px 8px}h1{font-size:28px;margin:0 0 6px;letter-spacing:-.01em}
.lead{color:var(--mute);max-width:760px;margin:0}.how{margin:14px 0 0;padding:12px 14px;border:1px solid var(--line);border-radius:12px;color:var(--mute);max-width:760px}
.how b{color:var(--text)}
.grid{max-width:1280px;margin:0 auto;padding:16px;display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:hidden;display:flex;flex-direction:column}
.pic{position:relative;aspect-ratio:9/16;background:#000}.pic img{width:100%;height:100%;object-fit:cover;display:block}
.num{position:absolute;top:10px;left:10px;background:rgba(0,0,0,.72);border:1px solid rgba(255,255,255,.18);color:#fff;font-weight:700;
 font-size:18px;min-width:40px;height:40px;border-radius:10px;display:flex;align-items:center;justify-content:center;padding:0 8px}
.cost{position:absolute;top:10px;right:10px;background:rgba(0,0,0,.72);border-radius:10px;padding:6px 9px;font-size:12px;color:#fff}
.body{padding:14px 14px 16px;display:flex;flex-direction:column;gap:8px}
h2{font-size:19px;margin:0}.idea{margin:0}.row{font-size:13px;color:var(--mute);margin:0}.row b{color:var(--text);font-weight:600}
.hook{margin:2px 0 0;padding:8px 10px;border-left:3px solid var(--acc);background:rgba(255,79,163,.08);border-radius:0 8px 8px 0;font-style:italic}
.style{display:inline-block;margin-top:2px;font-size:12px;color:var(--acc2);border:1px solid rgba(95,212,255,.35);border-radius:999px;padding:3px 9px}
.ours{margin:16px 0 0;display:grid;grid-template-columns:1fr 160px 160px;gap:12px;align-items:start;max-width:760px;border:1px solid var(--line);border-radius:12px;padding:12px}
.ours h3{margin:0 0 4px;font-size:16px}.ours img,.ours video{width:100%;border-radius:8px;display:block;background:#000;aspect-ratio:9/16;object-fit:cover}
.hw{border-top:1px dashed var(--line);padding-top:6px}
@media (max-width:600px){.ours{grid-template-columns:1fr 1fr}.ours>div{grid-column:1/-1}}
footer{max-width:1280px;margin:0 auto;padding:8px 16px 40px;color:var(--mute);font-size:13px}
@media (max-width:600px){h1{font-size:22px}.grid{grid-template-columns:1fr 1fr;gap:10px;padding:10px}.body{padding:10px}h2{font-size:16px}.idea,.row{font-size:12px}}
"""


def build(out: pathlib.Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    cards = []
    for d in D:
        src = REFS / f"{d['id']:02d}.jpg"
        img = f"{d['id']:02d}.jpg"
        if src.exists():
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", "scale=720:-2", "-q:v", "4", str(out / img)], check=True)
        e = html.escape
        lvl, hw = HW[d["id"]]
        dots = "●" * lvl + "○" * (3 - lvl)
        cards.append(f"""<article class="card"><div class="pic">{f'<img src="{img}" alt="" loading="lazy">' if src.exists() else ''}
<span class="num">{d['id']}</span><span class="cost" title="сколько работы собрать стиль один раз">{dots}</span></div>
<div class="body"><h2>{e(d['name'])}</h2><p class="idea">{e(d['idea'])}</p>
<p class="row"><b>Герои:</b> {e(d['heroes'])}</p><p class="row"><b>Откуда серии:</b> {e(d['engine'])}</p>
<p class="hook">{e(d['hook'])}</p><span class="style">{e(d['style'])}</span>
<p class="row hw"><b>На нашем железе:</b> {e(hw)}</p></div></article>""")
    page = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>Направления мультиков</title><style>{CSS}</style></head><body>
<header><h1>28 направлений: кино и игры для взрослых</h1>
<p class="lead">Каждое — свой мир, свой двигатель сюжетов и своя техника отрисовки. Кадры — референсы стиля (нейросеть), в роликах герои будут нашими и одинаковыми из серии в серию.</p>
<div class="how"><b>Как выбрать:</b> пришли номера, которые зацепили (идея), и номера, у которых нравится картинка (отрисовка) — их можно сочетать: идея одного, стиль другого.<br>
<b>Деньги:</b> мультики рендерит наш мак (бесплатно), платим только голос и сценарий — около $0.02–0.05 за ролик. Точки — сколько работы один раз собрать стиль: ●○○ движок уже есть, ●●○ 1–3 дня, ●●● долго или нужны нарисованные кадры.</div>
<section class="ours"><div><h3>Так рендерит наш мак сегодня</h3><p class="row">Черновик движка «Кат-сцены»: свои герои, клуб, мимика, жесты, планы. 0,006 с на кадр на M4 — минутный ролик за ~15 секунд. Картинку дотянем до выбранного стиля.</p></div>
<img src="ours.jpg" alt="наш рендер"><video src="ours.mp4" controls playsinline muted></video></section></header>
<main class="grid">{''.join(cards)}</main>
<footer>Сценарии пишет агент-сценарист, принимает агент-начальник, собирает агент-монтажёр; готовый ролик начальник смотрит целиком перед выпуском.</footer>
</body></html>"""
    ours = REFS / "01_ours.jpg"
    if ours.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(ours), "-vf", "scale=540:-2", "-q:v", "4", str(out / "ours.jpg")], check=True)
    (out / "index.html").write_text(page, encoding="utf-8")
    return len(cards)


if __name__ == "__main__":
    print(build(pathlib.Path(sys.argv[1])))
