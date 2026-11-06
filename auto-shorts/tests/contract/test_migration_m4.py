"""0016–0018 (контракт M4-0): резервы бюджета, коллекторы метрик, ролл-апы аналитики.

База `autoshorts_test_contract_m4` (с pid при AUTOSHORTS_TEST_DB_ISOLATE=1) в том же PostgreSQL, что
TEST_DATABASE_URL; создаётся и удаляется тестом. 0001–0015 → строки в старой форме (публикации YouTube и
VK, снимки, снимок VK-канала, квота) → 0016–0018 → заполнение новых колонок, CHECK-страховки,
представление сирот-резервов и расширенное `v_publication_dims`.
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
DB_NAME = contract_db_name("autoshorts_test_contract_m4")
NEW = (16, 17, 18)


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


def _files() -> list[Path]:
    return sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql"))


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
def db_before_0016():
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
INSERT INTO niches (id, label) VALUES ('taro', 'Таро') ON CONFLICT DO NOTHING;
INSERT INTO clusters (id, title) VALUES ('main', 'main') ON CONFLICT DO NOTHING;
INSERT INTO channels (id, niche_id, platform, cluster) VALUES ('taro_main', 'taro', 'youtube', 'main'),
  ('taro_vk', 'taro', 'vk', 'main');
INSERT INTO jobs (video_uid, channel_id, niche_id, template_version, status) VALUES
  ('a1b2c3d4e5f6', 'taro_main', 'taro', 'v1', 'published'),
  ('b1b2c3d4e5f6', 'taro_vk', 'taro', 'v1', 'published');
INSERT INTO publications (job_id, channel_id, platform, platform_video_id, master_file_path, published_at)
VALUES
  (1, 'taro_main', 'youtube', 'dQw4w9WgXcQ', 'masters/a.mp4', now() - interval '3 days'),
  (2, 'taro_vk', 'vk', '-1_2', 'masters/b.mp4', now() - interval '1 day');
INSERT INTO metrics_snapshots (publication_id, captured_at, views, likes) VALUES
  (1, now() - interval '2 days', 1500, 10);
INSERT INTO channel_snapshots (channel_id, subscribers) VALUES ('taro_main', 100), ('taro_vk', 50);
INSERT INTO api_quota_usage (api, quota_day, units) VALUES ('youtube_data', CURRENT_DATE, 12);
"""


def _fails(db: str, sql: str, constraint: str) -> None:
    res = _psql(db, "-c", sql)
    assert res.returncode != 0, f"должно упасть: {sql}"
    assert constraint in res.stderr, res.stderr


def _ok(db: str, sql: str) -> str:
    res = _psql(db, "-At", "-c", sql)
    assert res.returncode == 0, f"{sql}: {res.stderr}"
    return res.stdout.strip()


def test_0016_0018_backfill_and_guards(db_before_0016: str) -> None:
    db = db_before_0016
    seeded = _psql(db, stdin=LEGACY_ROWS)
    assert seeded.returncode == 0, seeded.stderr
    for f in _files():
        if int(f.name[:4]) in NEW:
            _apply(db, f)
    assert _ok(db, "SELECT max(version) FROM schema_migrations") == str(max(NEW))

    # 0017: коллектор публикаций по площадке, источник снимков VK-канала, умолчания квоты
    assert (
        _ok(db, "SELECT string_agg(metrics_collector, ',' ORDER BY id) FROM publications") == "yt_data,ytdlp"
    )
    assert (
        _ok(db, "SELECT string_agg(source, ',' ORDER BY channel_id) FROM channel_snapshots")
        == "yt_data,ytdlp"
    )
    assert _ok(db, "SELECT calls, errors, units FROM api_quota_usage") == "0|0|12"
    _fails(
        db, "INSERT INTO api_quota_usage (api, quota_day) VALUES ('youtube', CURRENT_DATE)", "ck_quota_api"
    )
    _fails(
        db,
        "INSERT INTO metrics_snapshots (publication_id, views, source) VALUES (2, -5, 'manual')",
        "ck_metrics_nonneg",
    )
    _ok(
        db,
        "INSERT INTO metrics_snapshots (publication_id, views, source, created_by, note) "
        "VALUES (2, 900, 'manual', 1, 'из приложения VK')",
    )
    _fails(
        db,
        "INSERT INTO collector_state (collector, state) VALUES ('yt_data', 'disabled')",
        "ck_collector_state_reason",
    )
    _fails(
        db,
        "INSERT INTO collector_state (collector, state, reason) VALUES ('yt_data', 'disabled', 'no_key')",
        "collector_state_reason_check",
    )
    _ok(
        db,
        "INSERT INTO collector_state (collector, state, reason, message) "
        "VALUES ('yt_data', 'disabled', 'no_api_key', 'Нет ключа YouTube Data API')",
    )
    _ok(db, "INSERT INTO collector_state (collector, cluster_id) VALUES ('ytdlp', 'main')")
    _fails(
        db,
        "INSERT INTO collector_state (collector, cluster_id) VALUES ('manual', 'Куст')",
        "cluster_id_check",
    )
    _fails(db, "UPDATE publications SET metrics_collector = 'oauth' WHERE id = 1", "metrics_collector_check")
    _ok(db, "UPDATE publications SET manual_due_at = now() - interval '1 hour', views_14d = 10 WHERE id = 2")

    # 0018: прямой трафик продукта, расширенные измерения публикации
    _ok(
        db,
        "INSERT INTO stats_daily_unattributed (day, product_id, clicks_unknown_code) "
        "VALUES (CURRENT_DATE, 'umka', 3)",
    )
    assert (
        _ok(
            db,
            "SELECT video_uid, publication_status, track FROM v_publication_dims "
            "ORDER BY publication_id LIMIT 1",
        )
        == "a1b2c3d4e5f6|live|generate"
    )
    assert _ok(db, "SELECT count(*) FROM stats_daily_traffic WHERE tma_opens = 0") == "0"

    # 0016: резервы бюджета и сироты
    _ok(
        db,
        """
        INSERT INTO tasks (id, kind, resource_class, status, attempts, locked_by)
          VALUES (101, 'job_stage', 'light', 'running', 1, 'w1'),
                 (102, 'job_stage', 'light', 'queued', 1, NULL),
                 (103, 'job_stage', 'light', 'running', 2, 'w2'),
                 (104, 'job_stage', 'light', 'running', 3, 'w1');
        INSERT INTO budget_reservations
          (id, task_id, task_attempt, worker_id, provider, amount_usd, business_day, created_at) VALUES
          (1, 101, 1, 'w1', 'fal', 0.02, CURRENT_DATE, now()),           -- живой
          (2, 102, 1, 'w1', 'fal', 0.02, CURRENT_DATE, now()),           -- задача вернулась в очередь
          (3, 103, 2, 'w1', 'fal', 0.02, CURRENT_DATE, now()),           -- взял другой воркер
          (4, 104, 2, 'w1', 'fal', 0.02, CURRENT_DATE, now()),           -- уже другая попытка
          (5, NULL, NULL, NULL, 'llm', 0.01, CURRENT_DATE, now()),       -- синхронный вызов, свежий
          (6, NULL, NULL, NULL, 'llm', 0.01, CURRENT_DATE, now() - interval '2 hours'),
          (7, 101, NULL, NULL, 'llm', 0.01, CURRENT_DATE, now())         -- код без worker_id и попытки
        """,
    )
    orphans = _ok(
        db, "SELECT string_agg(id || ':' || orphan_reason, ',' ORDER BY id) FROM v_budget_orphan_reservations"
    )
    assert orphans == "2:task_not_running,3:task_repicked,4:task_repicked,6:no_task_stale"

    # закрытие строки: условный UPDATE не снимает резерв дважды
    released = (
        "UPDATE budget_reservations SET status = 'released', release_reason = 'worker_lost', "
        "closed_at = now() "
    )
    assert _ok(db, released + "WHERE id = 2 AND status = 'open' RETURNING amount_usd") == "0.020000"
    assert _ok(db, released + "WHERE id = 2 AND status = 'open' RETURNING amount_usd") == ""
    _fails(db, "UPDATE budget_reservations SET status = 'settled' WHERE id = 1", "ck_budget_res_closed")
    _fails(
        db,
        "UPDATE budget_reservations SET status = 'settled', closed_at = now() WHERE id = 1",
        "ck_budget_res_settled",
    )
    _fails(
        db,
        "UPDATE budget_reservations SET status = 'released', closed_at = now() WHERE id = 1",
        "ck_budget_res_released",
    )
    _fails(
        db,
        "UPDATE budget_reservations SET status = 'released', closed_at = now(), release_reason = 'oom' "
        "WHERE id = 1",
        "budget_reservations_release_reason_check",
    )
    _ok(
        db,
        "UPDATE budget_reservations SET status = 'settled', closed_at = now(), actual_usd = 0.018 "
        "WHERE id = 1",
    )
    _fails(
        db,
        "INSERT INTO budget_reservations (provider, amount_usd, business_day) VALUES ('x', -1, CURRENT_DATE)",
        "budget_reservations_amount_usd_check",
    )
    # задача удалена уборкой — резерв остаётся, task_id обнуляется
    _ok(db, "DELETE FROM tasks WHERE id = 102")
    assert _ok(db, "SELECT task_id IS NULL FROM budget_reservations WHERE id = 2") == "t"
