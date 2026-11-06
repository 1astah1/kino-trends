"""Кадры-референсы к направлениям: Seedream 5.0 Flash через OpenRouter (общий ключ; $0.018 за кадр 1440×2560,
06.11.2026 лучше Pro и Lite по детализации; BytePlus Умки закрыт долгом — AccountOverdueError).
Запуск: .venv/bin/python -m toon.gen_refs [id ...]. Готовые не перегенерируются. Файлы — var/toon/refs (вне git)."""

from __future__ import annotations

import concurrent.futures as cf
import pathlib
import sys

from .directions import D, NEG

OUT = pathlib.Path(__file__).resolve().parent.parent / "var" / "toon" / "refs"
URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "bytedance-seed/seedream-5-0-flash"


def gen(d: dict) -> str:
    import base64

    import httpx

    from kinotrends import db

    out = OUT / f"{d['id']:02d}.jpg"
    if out.exists():
        return f"{d['id']}: уже есть"
    key = db.all_settings(db.connect()).get("llm_api_key") or ""
    body = {"model": MODEL, "modalities": ["image"], "messages": [{"role": "user", "content": d["prompt"] + NEG}],
            "image_config": {"aspect_ratio": "9:16"}, "usage": {"include": True}}
    err = ""
    for _ in range(3):
        try:
            r = httpx.post(URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=300)
            data = r.json()
            imgs = ((data.get("choices") or [{}])[0].get("message") or {}).get("images") or []
            if r.status_code != 200 or not imgs:
                raise RuntimeError(f"HTTP {r.status_code}: {str(data)[:200]}")
            url = imgs[0]["image_url"]["url"]
            out.write_bytes(base64.b64decode(url.split(",", 1)[1]) if url.startswith("data:") else httpx.get(url, timeout=120).content)
            return f"{d['id']}: готово, ${(data.get('usage') or {}).get('cost')}"
        except Exception as e:  # noqa: BLE001 — повтор, потом отчёт
            err = str(e)[:200]
    return f"{d['id']}: ошибка {err}"


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    ids = {int(a) for a in sys.argv[1:]}
    todo = [d for d in D if not ids or d["id"] in ids]
    with cf.ThreadPoolExecutor(4) as ex:
        for msg in ex.map(gen, todo):
            print(msg, flush=True)
