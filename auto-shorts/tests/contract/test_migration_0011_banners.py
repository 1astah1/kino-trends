"""0011: креативы баннера и перенос текстовой плашки 0003 в слои варианта (решение владельца 2026-09-26).

База `autoshorts_test_contract_m2` в том же PostgreSQL, что TEST_DATABASE_URL; создаётся и удаляется
тестом. 0001–0009 → строки варианта в старой форме → 0011 → слои читаются доменной моделью.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from autoshorts.api.schemas.banners import BannerVariantDraft
from tests.contract.conftest import contract_db_name

pytestmark = pytest.mark.db

MIGRATIONS = Path(__file__).resolve().parents[2] / "src" / "autoshorts" / "db" / "migrations"
DEFAULT_URL = "postgresql://autoshorts:autoshorts@127.0.0.1:5432/autoshorts_test"
DB_NAME = contract_db_name("autoshorts_test_contract_m2")
SHA = "c" * 64


def _url_for(db: str) -> str:
    base = urlsplit(os.environ.get("TEST_DATABASE_URL", DEFAULT_URL))
    return urlunsplit(base._replace(path=f"/{db}"))


def _psql(db: str, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["psql", _url_for(db), "-X", "-q", "-v", "ON_ERROR_STOP=1", *args],
        capture_output=True,
        text=True,
        timeout=120,
        input=stdin,
    )


def _apply(db: str, f: Path) -> None:
    version = int(f.name[:4])
    checksum = hashlib.sha256(f.read_bytes()).hexdigest()
    record = (
        "INSERT INTO schema_migrations (version, name, checksum) "
        f"VALUES ({version}, '{f.stem}', '{checksum}')"
    )
    res = _psql(db, "-1", "-f", str(f), "-c", record)
    assert res.returncode == 0, f"{f.name}: {res.stderr}"


@pytest.fixture(scope="module")
def db_before_0011():
    if not shutil.which("psql"):
        pytest.skip("psql not installed")
    probe = _psql("postgres", "-Atc", "SELECT 1")
    if probe.returncode != 0:
        pytest.skip(f"PostgreSQL not reachable: {probe.stderr.strip()[:200]}")
    drop = f'DROP DATABASE IF EXISTS "{DB_NAME}"'
    assert _psql("postgres", "-c", drop).returncode == 0
    created = _psql("postgres", "-c", f'CREATE DATABASE "{DB_NAME}"')
    if created.returncode != 0:
        pytest.skip(f"cannot create {DB_NAME}: {created.stderr.strip()[:200]}")
    try:
        assert _psql(DB_NAME, "-f", str(MIGRATIONS / "_bootstrap.sql")).returncode == 0
        for f in sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            if int(f.name[:4]) < 11:
                _apply(DB_NAME, f)
        yield DB_NAME
    finally:
        _psql("postgres", "-c", drop)


LEGACY_ROWS = """
INSERT INTO banner_templates (name, layout) VALUES ('legacy', '{}');
INSERT INTO campaigns (product_id, name, banner_template_id)
  VALUES ('umka', 'legacy', (SELECT id FROM banner_templates WHERE name = 'legacy'));
INSERT INTO banner_variants (campaign_id, code, name, micro_brand_text, offer_text, offer_text_2,
                             style_overrides, show_code_in_frame, ad_marking)
VALUES ((SELECT id FROM campaigns WHERE name = 'legacy'), 'A', 'full', 'umka.chat', 'Все нейросети',
        'в одном месте', '{"bg_color":"#112233","text_color":"#FFEEDD","background_opacity":0.5}', true,
        '{"enabled":true,"erid":"2SDnjabc","advertiser_text":"ООО «Умка»"}'),
       ((SELECT id FROM campaigns WHERE name = 'legacy'), 'B', 'offer only', '  ', 'Только оффер', NULL,
        '{}', false, '{"enabled":false,"erid":"не-латиница"}');
"""


def test_0011_converts_text_plate_and_adds_creatives(db_before_0011: str) -> None:
    db = db_before_0011
    seeded = _psql(db, stdin=LEGACY_ROWS)
    assert seeded.returncode == 0, seeded.stderr
    _apply(db, MIGRATIONS / "0011_banner_creatives.sql")

    rows = _psql(db, "-Atc", "SELECT code, layers::text, ad_marking::text FROM banner_variants ORDER BY code")
    assert rows.returncode == 0, rows.stderr
    parsed = {}
    for line in rows.stdout.strip().splitlines():
        code, layers, marking = line.split("|")
        parsed[code] = BannerVariantDraft(
            code=code, layers=json.loads(layers), ad_marking=json.loads(marking)
        )

    a = parsed["A"].to_domain()
    assert [(la.id, la.kind.value) for la in a.layers] == [
        ("micro_brand", "micro_brand"),
        ("fallback_plate", "fallback_plate"),
        ("code", "text_code"),
    ]
    plate = a.layers[1]
    assert plate.text == "Все нейросети в одном месте"
    assert plate.style is not None and (plate.style.bg_color, plate.style.color, plate.style.bg_opacity) == (
        "#112233",
        "#FFEEDD",
        0.5,
    )
    assert a.ad_marking.required is True and a.ad_marking.erid == "2SDnjabc"
    b = parsed["B"].to_domain()
    assert [la.id for la in b.layers] == ["fallback_plate"]
    assert b.ad_marking.erid is None and b.ad_marking.required is False

    cols = _psql(
        db,
        "-Atc",
        "SELECT string_agg(column_name, ',') FROM information_schema.columns "
        "WHERE table_name = 'banner_variants'",
    )
    legacy_cols = {"offer_text", "offer_text_2", "micro_brand_text", "style_overrides", "layout_overrides"}
    assert not set(cols.stdout.strip().split(",")) & (legacy_cols | {"show_code_in_frame"})

    checks = f"""
    INSERT INTO campaigns (product_id, name) VALUES ('umka', 'no template');
    INSERT INTO banner_creatives (sha256, name, kind, source_format, source_bytes)
      VALUES ('{SHA}', 'a.gif', 'animated', 'gif', 1000);
    UPDATE banner_variants SET layers = layers || jsonb_build_array(jsonb_build_object(
      'id', 'creative', 'kind', 'creative', 'creative_sha256', '{SHA}',
      'slot', jsonb_build_object('x', 65, 'y', 1120, 'w', 825, 'h', 190))) WHERE code = 'B';
    SELECT string_agg(code, ',') FROM banner_variants
      WHERE layers @> jsonb_build_array(jsonb_build_object('creative_sha256', '{SHA}'));
    """
    ok = _psql(db, "-At", stdin=checks)
    assert ok.returncode == 0, ok.stderr
    assert ok.stdout.strip().splitlines()[-1] == "B"

    ready_without_file = _psql(
        db, "-c", f"UPDATE banner_creatives SET status = 'ready' WHERE sha256 = '{SHA}'"
    )
    assert ready_without_file.returncode != 0 and "ck_banner_creatives_ready" in ready_without_file.stderr
    failed_without_code = _psql(
        db, "-c", f"UPDATE banner_creatives SET status = 'failed' WHERE sha256 = '{SHA}'"
    )
    assert failed_without_code.returncode != 0 and "ck_banner_creatives_failed" in failed_without_code.stderr
    bad_template = _psql(db, "-c", "UPDATE banner_variants SET code_url_template = 'u.umka.chat/x'")
    assert bad_template.returncode != 0 and "ck_banner_variants_code_url" in bad_template.stderr

    again = _psql(db, "-1", "-f", str(MIGRATIONS / "0011_banner_creatives.sql"))
    assert again.returncode != 0 and "migration 11 already applied" in again.stderr
