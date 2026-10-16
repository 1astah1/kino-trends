"""Консоль: serve | collect | import-dict | recognize | stats."""
from __future__ import annotations

import argparse
import json
import sys

from . import db
from .config import HOST, PORT


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kinotrends", description="Какие фильмы режут на YouTube Shorts: сбор и статистика")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="поднять API и интерфейс")
    s.add_argument("--host", default=HOST)
    s.add_argument("--port", type=int, default=PORT)
    c = sub.add_parser("collect", help="один прогон сбора")
    c.add_argument("--trigger", default="cli")
    sub.add_parser("import-dict", help="импортировать словарь Wikidata в базу")
    r = sub.add_parser("recognize", help="прогнать распознавание")
    r.add_argument("--force", action="store_true", help="пересчитать все ролики, кроме размеченных вручную")
    sub.add_parser("stats", help="краткая сводка по базе")
    args = p.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("kinotrends.api:app", host=args.host, port=args.port, log_level="info", workers=1)
        return 0

    conn = db.connect()
    db.init_db(conn)
    if args.cmd == "import-dict":
        from .dictionary.importer import import_entities

        print(json.dumps(import_entities(conn), ensure_ascii=False))
        return 0
    if args.cmd in ("collect", "recognize"):
        from .collector import Collector
        from .recognize import Recognizer

        rec = Recognizer()
        rec.load(conn)
        col = Collector(conn, rec, trigger=getattr(args, "trigger", "cli"))
        if args.cmd == "collect":
            res = col.run()
            print(json.dumps(res, ensure_ascii=False))
            return 0 if res["status"] in ("ok", "cancelled") else 1
        col.recognize_pending(force=args.force)
        print(json.dumps(col.stats, ensure_ascii=False))
        return 0
    if args.cmd == "stats":
        from .stats import Filters, overview

        o = overview(conn, Filters("30"))
        print(json.dumps({"totals": o["totals"], "period": o["period"], "last_run": o["last_run"]}, ensure_ascii=False, indent=1))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
