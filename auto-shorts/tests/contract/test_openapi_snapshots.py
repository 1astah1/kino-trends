"""Снимки OpenAPI по тегам и актуальность TS-типов (docs/ARCHITECTURE.md §10.1 п. 4).

Изменил схему осознанно — обнови снимки и типы:
    .venv/bin/python scripts/export_openapi.py --snapshots --ts
Тег принадлежит одному потоку; менять чужой снимок — только через поток контрактов.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

HOW_TO_UPDATE = "run: .venv/bin/python scripts/export_openapi.py --snapshots --ts"


def test_snapshot_per_tag_is_current(export_openapi: ModuleType, openapi_spec: dict[str, Any]) -> None:
    problems = export_openapi.check_snapshots(openapi_spec)
    assert not problems, f"{problems}; {HOW_TO_UPDATE}"


def test_every_tag_has_snapshot(export_openapi: ModuleType, openapi_spec: dict[str, Any]) -> None:
    tags = {t for item in openapi_spec["paths"].values() for op in item.values() for t in op.get("tags", [])}
    files = {p.stem for p in export_openapi.SNAPSHOT_DIR.glob("*.json")}
    assert tags == files, f"tags without snapshot: {tags - files}; stale: {files - tags}; {HOW_TO_UPDATE}"


def test_snapshot_is_self_contained(export_openapi: ModuleType, openapi_spec: dict[str, Any]) -> None:
    """Каждый $ref в снимке тега разрешается внутри этого же снимка."""
    for tag, snap in export_openapi.split_by_tag(openapi_spec).items():
        refs: set[str] = set()
        export_openapi._collect_refs(snap, refs)
        missing = refs - set(snap["components"]["schemas"])
        assert not missing, f"{tag}: unresolved refs {missing}"


def test_ts_types_are_current(export_openapi: ModuleType, openapi_spec: dict[str, Any]) -> None:
    assert export_openapi.TS_PATH.exists(), f"{export_openapi.TS_PATH} missing; {HOW_TO_UPDATE}"
    assert export_openapi.ts_header_hash() == export_openapi.spec_hash(openapi_spec), (
        f"web/lib/api/schema.d.ts is stale; {HOW_TO_UPDATE}"
    )
