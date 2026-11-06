"""Файлы миграций: нумерация, предохранитель, baseline = scripts/init_db.sql (§4.1, §4.2).

Номера выдаёт ведущий по этапам (§10.1 п. 3): M1 — 0001–0009, M2 — 0011–0015 (0012–0015 — поток
M5M3-0), M4 — 0016–0019 (поток M4-0). Круглые номера (0010, 0020, …) зарезервированы: 0010 — уборка
legacy (M7-C).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "src" / "autoshorts" / "db" / "migrations"
NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def _files() -> list[Path]:
    return sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql"))


# Выданные ведущим диапазоны: этап -> номера. Новый этап — новая строка здесь.
STAGE_RANGES: dict[str, range] = {
    "M1": range(1, 10),
    "M2": range(11, 16),  # 0011 баннеры M2; 0012–0015 — поток M5M3-0 (нарезка + M3)
    "M4": range(16, 20),  # 0016 резервы бюджета, 0017 коллекторы, 0018 ролл-апы; 0019 — запас M4-0
}
RESERVED = {10}  # 0010_cleanup.sql (M7-C) — номер держится за уборкой legacy


def _versions() -> list[int]:
    return [int(f.name[:4]) for f in _files()]


def test_names_and_unique_versions() -> None:
    files = _files()
    assert all(NAME.match(f.name) for f in files), [f.name for f in files if not NAME.match(f.name)]
    versions = _versions()
    assert len(versions) == len(set(versions)), "две миграции с одним номером"


def test_m1_versions_contiguous_from_one() -> None:
    versions = _versions()
    assert versions[:9] == list(range(1, 10))


def test_versions_within_issued_ranges() -> None:
    issued = set().union(*STAGE_RANGES.values()) | RESERVED
    stray = [v for v in _versions() if v not in issued]
    assert not stray, f"номера вне выданных диапазонов {STAGE_RANGES}: {stray}"


def test_stage_ranges_have_no_holes() -> None:
    """Внутри диапазона этапа номера идут подряд с начала: 0011, 0012, … без дыр."""
    versions = set(_versions())
    for stage, rng in STAGE_RANGES.items():
        used = sorted(v for v in versions if v in rng)
        assert used == list(range(rng.start, rng.start + len(used))), f"{stage}: {used}"


def test_first_line_is_guard_with_own_version() -> None:
    for f in _files():
        first = f.read_text(encoding="utf-8").split("\n", 1)[0]
        assert first == f"SELECT as_migration_guard({int(f.name[:4])});", f.name


def test_files_do_not_manage_transactions() -> None:
    """Раннер оборачивает каждый файл в одну транзакцию; BEGIN/COMMIT внутри файла её сломают."""
    for f in _files():
        text = re.sub(r"--[^\n]*", "", f.read_text(encoding="utf-8"))
        assert not re.search(r"^\s*(BEGIN|COMMIT|ROLLBACK)\s*;", text, re.M | re.I), f.name


def test_baseline_is_init_db_verbatim() -> None:
    baseline = (MIGRATIONS / "0001_baseline.sql").read_text(encoding="utf-8")
    init_db = (ROOT / "scripts" / "init_db.sql").read_text(encoding="utf-8")
    assert baseline == "SELECT as_migration_guard(1);\n" + init_db


def test_bootstrap_defines_guard_and_registry() -> None:
    boot = (MIGRATIONS / "_bootstrap.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS schema_migrations" in boot
    assert "CREATE OR REPLACE FUNCTION as_migration_guard(v int)" in boot
    assert "migration % already applied" in boot
    assert not NAME.match("_bootstrap.sql")


def test_no_cast_on_bound_params() -> None:
    """§1.5: параметр в CASE/интервалах — только CAST(%(x)s AS int), не `%(x)s::int`."""
    for f in _files():
        assert not re.search(r"%\(\w+\)s\s*::", f.read_text(encoding="utf-8")), f.name
