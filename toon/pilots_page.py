"""Страница пилотов для владельца: python -m toon.pilots_page OUT_DIR — видео (сжатая копия), сценарий, решения агентов."""

from __future__ import annotations

import html
import json
import pathlib
import sqlite3
import subprocess
import sys

from . import series, store

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSS = """:root{--bg:#0e0d13;--card:#17151f;--line:#2a2735;--text:#ece9f3;--mute:#a19cb0;--ok:#4fd18b;--warn:#ffb84f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header,main{max-width:1100px;margin:0 auto;padding:20px 16px}h1{margin:0 0 6px;font-size:26px}.lead{color:var(--mute);margin:0}
.ep{display:grid;grid-template-columns:320px 1fr;gap:20px;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;margin:0 0 18px}
video{width:100%;aspect-ratio:9/16;border-radius:12px;background:#000}h2{margin:0 0 4px;font-size:20px}.meta{color:var(--mute);font-size:13px;margin:0 0 10px}
.v{display:inline-block;border-radius:999px;padding:2px 10px;font-size:13px;font-weight:600}.v.ok{background:rgba(79,209,139,.15);color:var(--ok)}.v.fix{background:rgba(255,184,79,.15);color:var(--warn)}
ol{margin:8px 0;padding-left:22px}li{margin:2px 0}.who{color:var(--mute);font-size:12px}.notes{color:var(--mute);font-size:13px;margin-top:8px}
@media (max-width:760px){.ep{grid-template-columns:1fr}}"""


def build(out: pathlib.Path, ids: list[int] | None = None) -> int:
    out.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ROOT / "var" / "kinotrends.sqlite")
    q = "SELECT id FROM toon_episodes WHERE video IS NOT NULL ORDER BY id DESC"
    eps = [r[0] for r in conn.execute(q)] if not ids else ids
    blocks = []
    for ep in eps:
        e = store.episode(conn, ep)
        src = pathlib.Path(e.get("video") or "")
        if not src.exists():
            continue
        dst = out / f"ep{ep}.mp4"
        if not (dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime):  # пережимаем только изменившиеся
          subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-c:v", "libx264", "-preset", "medium", "-crf", "24", "-c:a", "aac", "-b:a", "128k",
                        "-movflags", "+faststart", str(dst)], check=True)
        sc, key = e.get("script") or {}, e.get("series") or "katscena"
        vid_ev = [x for x in e["events"] if x["agent"] == "начальник" and x["action"].startswith("видео")]
        last = vid_ev[-1] if vid_ev else None
        ok = e["status"] == "approved"
        lines = "".join(f"<li><span class='who'>{html.escape(series.S[key]['cast'].get(ln.get('who'), {}).get('name', ln.get('who', '')))}:</span> "
                        f"{html.escape(ln.get('text') or '')}</li>" for ln in sc.get("lines") or [])
        chain = " → ".join(html.escape(f"{x['agent']}: {x['action']}") for x in e["events"])
        blocks.append(f"""<section class="ep"><video src="ep{ep}.mp4" controls playsinline preload="metadata"></video><div>
<p class="meta">{html.escape(series.S[key]['title'])} · серия {ep} · формат {html.escape(e.get('format') or '')}</p>
<h2>{html.escape(sc.get('title') or '')}</h2><p>{html.escape(sc.get('logline') or '')}</p>
<p><span class="v {'ok' if ok else 'fix'}">начальник: {html.escape(last['action'] if last else '—')}</span></p>
<p class="notes">{html.escape(last['detail'] if last else '')}</p><ol>{lines}</ol><p class="notes">Путь серии: {chain}</p></div></section>""")
    page = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>Пилоты мультиков</title><style>{CSS}</style></head><body>
<header><h1>Пилоты мультиков</h1><p class="lead">Каждую серию написал сценарист, принял начальник, озвучил голос, смонтировал монтажёр, отрендерил наш мак, и начальник посмотрел готовое видео целиком.</p></header>
<main>{''.join(blocks)}</main></body></html>"""
    (out / "index.html").write_text(page, encoding="utf-8")
    return len(blocks)


if __name__ == "__main__":
    print(build(pathlib.Path(sys.argv[1]), [int(a) for a in sys.argv[2:]] or None))
