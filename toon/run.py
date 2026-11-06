"""Запуск: python -m toon.run scripts [N] — N серий через «сценарист → начальник»."""

from __future__ import annotations

import sys

from kinotrends import db

from . import agents, store


def main(argv: list[str]) -> None:
    cmd = argv[0] if argv else "scripts"
    conn = db.connect()
    settings = db.all_settings(conn)
    store.init(conn)
    if cmd == "scripts":
        n = int(argv[1]) if len(argv) > 1 else 3
        for fmt, seed, fact in agents.seeds(conn, n):
            ep = agents.produce_script(conn, settings, fmt, seed, fact)
            e = store.episode(conn, ep)
            print(f"== серия {ep} [{fmt}] {e['status']}: «{e['title']}»")


if __name__ == "__main__":
    main(sys.argv[1:])
