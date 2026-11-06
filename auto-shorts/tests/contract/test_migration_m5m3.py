"""0012–0015 (контракт M5M3-0): нарезка (права, кандидаты, авторский слой, оригинальность) и M3.

База `autoshorts_test_contract_m5m3` в том же PostgreSQL, что TEST_DATABASE_URL; создаётся и удаляется
тестом. 0001–0011 → строки в старой форме (источник, права, кандидаты llm/heuristic/ручной, clip-job) →
0012–0015 → проверка заполнения новых колонок и CHECK-страховок.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from tests.contract.conftest import contract_db_name

pytestmark = pytest.mark.db

MIGRATIONS = Path(__file__).resolve().parents[2] / "src" / "autoshorts" / "db" / "migrations"
DEFAULT_URL = "postgresql://autoshorts:autoshorts@127.0.0.1:5432/autoshorts_test"
DB_NAME = contract_db_name("autoshorts_test_contract_m5m3")
NEW = (12, 13, 14, 15)


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


def _files() -> list[Path]:
    return sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql"))


@pytest.fixture(scope="module")
def db_before_0012():
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
        for f in _files():
            if int(f.name[:4]) < NEW[0]:
                _apply(DB_NAME, f)
        yield DB_NAME
    finally:
        _psql("postgres", "-c", drop)


LEGACY_ROWS = """
INSERT INTO users (login, display_name, password_hash, role) VALUES ('oper', 'Оператор', 'x', 'operator');
INSERT INTO niches (id, label) VALUES ('astro', 'Астро') ON CONFLICT DO NOTHING;
INSERT INTO clusters (id, title) VALUES ('main', 'main') ON CONFLICT DO NOTHING;
INSERT INTO channels (id, niche_id, platform, cluster) VALUES ('astro_main', 'astro', 'youtube', 'main');
INSERT INTO sources (title, origin, origin_url, status) VALUES ('Эфир', 'url', 'https://youtu.be/x', 'ready');
INSERT INTO source_rights (source_id, rights_type, owner_name, proof_note, attribution_text)
  VALUES (1, 'cc_by', 'Автор', 'лицензия на странице', 'Автор, CC BY 4.0');
INSERT INTO clip_candidates (source_id, start_sec, end_sec, features, created_by_kind) VALUES
  (1, 10, 40, '{"origin":"heuristic"}', 'auto'),
  (1, 50, 80, '{}', 'auto'),
  (1, 90, 120, '{"origin":"llm"}', 'manual');
INSERT INTO jobs (video_uid, channel_id, niche_id, template_version, kind, source_id, clip_candidate_id)
  VALUES ('a1b2c3d4e5f6', 'astro_main', 'astro', 'v1', 'clip', 1, 1);
"""


def _fails(db: str, sql: str, constraint: str) -> None:
    res = _psql(db, "-c", sql)
    assert res.returncode != 0, f"должно упасть: {sql}"
    assert constraint in res.stderr, res.stderr


def _ok(db: str, sql: str) -> str:
    res = _psql(db, "-At", "-c", sql)
    assert res.returncode == 0, f"{sql}: {res.stderr}"
    return res.stdout.strip()


def test_0012_0015_backfill_and_guards(db_before_0012: str) -> None:
    db = db_before_0012
    seeded = _psql(db, stdin=LEGACY_ROWS)
    assert seeded.returncode == 0, seeded.stderr
    for f in _files():
        if int(f.name[:4]) in NEW:
            _apply(db, f)
    assert _ok(db, "SELECT max(version) FROM schema_migrations") == str(max(NEW))

    # кандидаты: origin из features / created_by_kind, новые колонки с умолчаниями
    assert (
        _ok(db, "SELECT string_agg(origin, ',' ORDER BY id) FROM clip_candidates") == "heuristic,llm,manual"
    )
    assert _ok(db, "SELECT count(*) FROM clip_candidates WHERE unblocked_flags = '{}' AND snap = '{}'") == "3"
    _fails(
        db,
        "UPDATE clip_candidates SET origin = 'magic' WHERE id = 1",
        "clip_candidates_origin_check",
    )
    _fails(
        db,
        "UPDATE clip_candidates SET start_word_idx = 50, end_word_idx = 10 WHERE id = 1",
        "ck_clipcand_word_range",
    )
    _ok(
        db,
        "UPDATE clip_candidates SET start_word_idx = 10, end_word_idx = 50, superseded_at = now() "
        "WHERE id = 2",
    )

    # права: CC BY-SA / NC / ND не проходят и на уровне БД
    for bad in ("license = 'CC BY-SA 4.0'", "license = 'CC BY-NC 4.0'", "license = 'Attribution-NoDerivs'"):
        _fails(
            db, f"UPDATE source_rights SET {bad} WHERE source_id = 1", "ck_source_rights_license_compatible"
        )
    _fails(
        db,
        "UPDATE source_rights SET license_url = 'https://creativecommons.org/licenses/by-nc-sa/4.0/'",
        "ck_source_rights_license_compatible",
    )
    _ok(
        db,
        "UPDATE source_rights SET license = 'CC BY 4.0', "
        "license_url = 'https://creativecommons.org/licenses/by/4.0/', "
        "warnings = ARRAY['cc_by_attribution_in_description'] WHERE source_id = 1",
    )

    # источник: каталог только sources/<латиница>
    _fails(db, "UPDATE sources SET media_dir = 'sources/../etc' WHERE id = 1", "sources_media_dir_check")
    _fails(db, "UPDATE sources SET media_dir = 'sources/эфир' WHERE id = 1", "sources_media_dir_check")
    _ok(
        db,
        "UPDATE sources SET media_dir = 'sources/cc_shalyopa_cgevent', "
        'selection = \'{"mode":"heuristic"}\'',
    )

    # clip-job: авторский слой и отчёт
    assert _ok(db, "SELECT author_script_version, author_voice::text FROM jobs") == "0|{}"
    _fails(db, "UPDATE jobs SET originality_verdict = 'maybe'", "jobs_originality_verdict_check")
    _fails(db, "UPDATE jobs SET clip_start_sid = 9, clip_end_sid = 3", "ck_jobs_clip_sid_range")
    _ok(
        db,
        "UPDATE jobs SET originality_verdict = 'fail', originality = '{\"verdict\":\"fail\"}', "
        'clip_start_sid = 3, clip_end_sid = 9, author_script = \'{"mode":"heuristic"}\'',
    )

    # M3: пакеты, приём, продукты
    cols = _ok(
        db,
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_name = 'upload_packages' AND column_name = 'texts_mode'",
    )
    assert "template" in cols
    assert _ok(db, "SELECT count(*) FROM conversions WHERE is_test") == "0"
    _fails(
        db,
        "INSERT INTO ingest_requests (outcome, http_status) VALUES ('bogus', 200)",
        "ingest_requests_outcome_check",
    )
    _ok(
        db,
        "INSERT INTO ingest_requests (product_id, outcome, http_status, events_total, accepted) "
        "VALUES ('umka', 'accepted', 200, 1, 1)",
    )
    _fails(
        db,
        "UPDATE products SET webhook_prev_secret_name = 'product_webhook_umka' WHERE id = 'umka'",
        "products_webhook_prev_secret_name_check",
    )

    again = _psql(db, "-1", "-f", str(MIGRATIONS / "0015_attribution_ingest.sql"))
    assert again.returncode != 0 and "migration 15 already applied" in again.stderr
