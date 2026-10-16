"""Импорт собранных карточек Wikidata в таблицы titles и title_aliases."""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time

from ..config import DICT_DIR
from ..db import connect, init_db, now_iso, set_meta
from ..text import norm
from .wikidata import ENTITIES_PATH, ITEMS_PATH, LABELS_PATH

_PAREN = re.compile(r"\s*\([^)]*\)\s*$")
GENERIC_ALIAS_MIN = 2


def alias_variants(label: str | None) -> list[str]:
    """Варианты написания: без уточнения в скобках, без артикля, часть до двоеточия."""
    if not label:
        return []
    out = [label]
    stripped = _PAREN.sub("", label).strip()
    if stripped and stripped != label:
        out.append(stripped)
    base = stripped or label
    low = base.lower()
    for art in ("the ", "a ", "an "):
        if low.startswith(art) and len(base) > len(art) + 3:
            out.append(base[len(art):])
    if ":" in base:
        head = base.split(":", 1)[0].strip()
        if len(head) >= 5 and any(ch.isdigit() for ch in head):
            out.append(head)
    return out


def import_entities(conn: sqlite3.Connection, limit: int | None = None) -> dict:
    if not ENTITIES_PATH.exists():
        return {"titles": 0, "aliases": 0, "error": "entities.jsonl ещё не собран"}
    kinds: dict[str, dict] = {}
    if ITEMS_PATH.exists():
        for it in json.loads(ITEMS_PATH.read_text()):
            kinds[it["qid"]] = it
    labels: dict[str, dict] = {}
    if LABELS_PATH.exists():
        labels = json.loads(LABELS_PATH.read_text())

    def lab(qid: str) -> str | None:
        l = labels.get(qid) or {}
        return l.get("ru") or l.get("en")

    t0 = time.time()
    ts = now_iso()
    n_titles = n_aliases = 0
    title_rows: list[tuple] = []
    alias_rows: list[tuple] = []
    with ENTITIES_PATH.open() as f:
        for i, line in enumerate(f):
            if limit and i >= limit:
                break
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            qid = e["qid"]
            meta = kinds.get(qid, {})
            kind = meta.get("type", "film")
            sl = int(meta.get("sl", 0))
            label_ru = e.get("label_ru")
            label_en = e.get("label_en")
            if not label_ru and not label_en:
                continue
            genres = [g for g in (lab(x) for x in e.get("genres", [])) if g]
            countries = [c for c in (lab(x) for x in e.get("countries", [])) if c]
            fr = e.get("franchise") or []
            fr_id = fr[0] if fr else None
            title_rows.append((
                qid, label_ru, label_en, e.get("original_title"), e.get("year"), kind, sl,
                e.get("kp") or meta.get("kp"), e.get("imdb"), json.dumps(genres, ensure_ascii=False),
                json.dumps(countries, ensure_ascii=False), fr_id, lab(fr_id) if fr_id else None, ts,
            ))
            n_titles += 1
            seen: set[str] = set()
            for lang, labels_list in (("ru", [label_ru, *e.get("aliases_ru", [])]), ("en", [label_en, e.get("original_title"), *e.get("aliases_en", [])])):
                for raw in labels_list:
                    for a in alias_variants(raw):
                        nz = norm(a)
                        if len(nz) < GENERIC_ALIAS_MIN or nz in seen:
                            continue
                        seen.add(nz)
                        alias_rows.append((qid, a, nz, lang))
                        n_aliases += 1
            if len(title_rows) >= 2000:
                _flush(conn, title_rows, alias_rows)
                title_rows, alias_rows = [], []
    _flush(conn, title_rows, alias_rows)
    set_meta(conn, "dict_imported_at", ts)
    conn.commit()
    return {"titles": n_titles, "aliases": n_aliases, "seconds": round(time.time() - t0, 1)}


def _flush(conn: sqlite3.Connection, title_rows: list[tuple], alias_rows: list[tuple]) -> None:
    if title_rows:
        conn.executemany(
            """INSERT INTO titles(id, label_ru, label_en, original_title, year, kind, sitelinks, kp_id, imdb_id, genres, countries, franchise_id, franchise_label, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET label_ru=excluded.label_ru, label_en=excluded.label_en, original_title=excluded.original_title,
                 year=excluded.year, kind=excluded.kind, sitelinks=excluded.sitelinks, kp_id=excluded.kp_id, imdb_id=excluded.imdb_id,
                 genres=excluded.genres, countries=excluded.countries, franchise_id=excluded.franchise_id, franchise_label=excluded.franchise_label,
                 updated_at=excluded.updated_at""",
            title_rows,
        )
    if alias_rows:
        conn.executemany(
            "INSERT OR IGNORE INTO title_aliases(title_id, alias, norm, lang, source) VALUES (?,?,?,?,'wikidata')",
            alias_rows,
        )
    conn.commit()


def main() -> int:
    conn = connect()
    init_db(conn)
    res = import_entities(conn)
    print(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
