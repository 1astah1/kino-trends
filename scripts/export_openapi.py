#!/usr/bin/env python3
"""Экспорт OpenAPI `/api/v1`, снимки по тегам и TS-типы фронта (docs/ARCHITECTURE.md §10.1 п. 4).

Примеры:
    python scripts/export_openapi.py --out openapi.json   # полная схема ("-" — в stdout)
    python scripts/export_openapi.py --snapshots          # tests/contract/openapi/<tag>.json
    python scripts/export_openapi.py --ts                 # web/lib/api/schema.d.ts (npx openapi-typescript@7)
    python scripts/export_openapi.py --check              # снимки и schema.d.ts актуальны? иначе exit 1

Снимок тега = операции с этим тегом + транзитивное замыкание `$ref` по components/schemas.
В шапке schema.d.ts хранится sha256 канонической схемы — по нему контрактный тест ловит устаревшие типы
без запуска node. TS генерирует только поток контрактов.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = ROOT / "tests" / "contract" / "openapi"
TS_PATH = ROOT / "web" / "lib" / "api" / "schema.d.ts"
TS_HASH_PREFIX = "// openapi-sha256: "
OPENAPI_TYPESCRIPT = "openapi-typescript@7"
REF_PREFIX = "#/components/schemas/"
HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")

if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def build_spec() -> dict[str, Any]:
    """Полная OpenAPI-схема из фабрики приложения (без БД и сети)."""
    from autoshorts.api.app import create_app

    return create_app().openapi()


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def pretty_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def spec_hash(spec: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()


def _collect_refs(node: Any, out: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith(REF_PREFIX):
                out.add(value[len(REF_PREFIX) :])
            elif key == "mapping" and isinstance(value, dict):  # discriminator.mapping
                for target in value.values():
                    if isinstance(target, str) and target.startswith(REF_PREFIX):
                        out.add(target[len(REF_PREFIX) :])
            else:
                _collect_refs(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_refs(item, out)


def schema_closure(start: Any, schemas: dict[str, Any]) -> dict[str, Any]:
    """Все схемы components, достижимые по `$ref` из `start`."""
    pending: set[str] = set()
    _collect_refs(start, pending)
    seen: dict[str, Any] = {}
    while pending:
        name = pending.pop()
        if name in seen or name not in schemas:
            continue
        seen[name] = schemas[name]
        nested: set[str] = set()
        _collect_refs(schemas[name], nested)
        pending |= nested - seen.keys()
    return dict(sorted(seen.items()))


def split_by_tag(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """`{tag: snapshot}`; операция с несколькими тегами попадает в снимок каждого."""
    schemas = spec.get("components", {}).get("schemas", {})
    by_tag: dict[str, dict[str, dict[str, Any]]] = {}
    for path, item in spec.get("paths", {}).items():
        for method, op in item.items():
            if method not in HTTP_METHODS:
                continue
            for tag in op.get("tags") or ["untagged"]:
                by_tag.setdefault(tag, {}).setdefault(path, {})[method] = op
    out: dict[str, dict[str, Any]] = {}
    for tag, paths in sorted(by_tag.items()):
        out[tag] = {
            "openapi": spec.get("openapi"),
            "tag": tag,
            "paths": paths,
            "components": {"schemas": schema_closure(paths, schemas)},
        }
    return out


def snapshot_texts(spec: dict[str, Any]) -> dict[str, str]:
    return {f"{tag}.json": pretty_json(snap) for tag, snap in split_by_tag(spec).items()}


def write_snapshots(spec: dict[str, Any], directory: Path = SNAPSHOT_DIR) -> list[str]:
    directory.mkdir(parents=True, exist_ok=True)
    texts = snapshot_texts(spec)
    changed: list[str] = []
    for name, text in texts.items():
        path = directory / name
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            changed.append(name)
    for stale in directory.glob("*.json"):
        if stale.name not in texts:
            stale.unlink()
            changed.append(f"-{stale.name}")
    return changed


def check_snapshots(spec: dict[str, Any], directory: Path = SNAPSHOT_DIR) -> list[str]:
    """Список расхождений (пусто — снимки актуальны)."""
    texts = snapshot_texts(spec)
    problems: list[str] = []
    for name, text in texts.items():
        path = directory / name
        if not path.exists():
            problems.append(f"missing snapshot {name}")
        elif path.read_text(encoding="utf-8") != text:
            problems.append(f"snapshot differs: {name}")
    for extra in sorted(directory.glob("*.json")):
        if extra.name not in texts:
            problems.append(f"stale snapshot (tag gone): {extra.name}")
    return problems


def ts_header_hash(ts_path: Path = TS_PATH) -> str | None:
    if not ts_path.exists():
        return None
    first = ts_path.read_text(encoding="utf-8").split("\n", 1)[0]
    return first[len(TS_HASH_PREFIX) :].strip() if first.startswith(TS_HASH_PREFIX) else None


def generate_ts(spec: dict[str, Any], ts_path: Path = TS_PATH) -> None:
    """`npx -y openapi-typescript@7 <spec> -o <ts>` + строка с хэшем схемы в начале файла."""
    npx = shutil.which("npx")
    if not npx:
        raise SystemExit("npx not found: install Node.js to generate TS types")
    ts_path.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "NODE_OPTIONS": os.environ.get("NODE_OPTIONS", "--max-old-space-size=1536")}
    with tempfile.TemporaryDirectory(prefix="autoshorts-openapi-") as tmp:
        src = Path(tmp) / "openapi.json"
        out = Path(tmp) / "schema.d.ts"
        src.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        cmd = ["nice", "-n", "10", npx, "-y", OPENAPI_TYPESCRIPT, str(src), "-o", str(out)]
        subprocess.run(cmd, check=True, env=env)
        body = out.read_text(encoding="utf-8")
    ts_path.write_text(f"{TS_HASH_PREFIX}{spec_hash(spec)}\n{body}", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="записать полную схему в файл ('-' — stdout)")
    ap.add_argument(
        "--snapshots", action="store_true", help=f"обновить снимки в {SNAPSHOT_DIR.relative_to(ROOT)}"
    )
    ap.add_argument("--ts", action="store_true", help=f"сгенерировать {TS_PATH.relative_to(ROOT)}")
    ap.add_argument("--check", action="store_true", help="проверить актуальность снимков и schema.d.ts")
    args = ap.parse_args(argv)
    if not (args.out or args.snapshots or args.ts or args.check):
        ap.error("nothing to do: pass --out, --snapshots, --ts or --check")

    spec = build_spec()
    if args.out:
        text = json.dumps(spec, ensure_ascii=False, indent=2) + "\n"
        if args.out == "-":
            sys.stdout.write(text)
        else:
            Path(args.out).write_text(text, encoding="utf-8")
    if args.snapshots:
        changed = write_snapshots(spec)
        print(
            f"snapshots: {len(changed)} changed {changed}" if changed else "snapshots: up to date",
            file=sys.stderr,
        )
    if args.ts:
        generate_ts(spec)
        print(f"ts: {TS_PATH.relative_to(ROOT)} regenerated", file=sys.stderr)
    if args.check:
        problems = check_snapshots(spec)
        if ts_header_hash() != spec_hash(spec):
            problems.append(f"{TS_PATH.relative_to(ROOT)} is stale (run with --ts)")
        for p in problems:
            print(p, file=sys.stderr)
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
