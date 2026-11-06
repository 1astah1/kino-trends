"""Legacy-база → v1: принятие baseline и перенос данных миграциями 0003/0004/0006 (§4.1, §4.2, M1).

Критерий M1: «на копии боевой базы: pg_restore в autoshorts_legacy_check → migrate → adopted=true у
версии 1; pytest tests/migrations -k legacy зелёный (маппинг статусов, перенос задач, клики,
нормализация reject_reason)».

Два источника legacy-базы (параметр фикстуры):
* `dump` — pg_dump dev-базы до M0 (`AUTOSHORTS_LEGACY_DUMP`, иначе путь из задания потока M1-A);
  восстанавливается в `autoshorts_legacy_check`. Нет файла — вариант пропускается.
* `init_db` — `scripts/init_db.sql` в `autoshorts_legacy_check_initdb`, работает всегда.

В обе базы до миграции добавляются legacy-строки на все ветки маппинга (все старые статусы,
смешанный reject_reason, публикация с меткой yt_<id>, клики с меткой, без метки и /health).
Миграция идёт через CLI с бэкапом в tmp-каталог — так же, как на проде.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

from autoshorts.db.migrate import file_versions, latest_version
from tests.migrations.conftest import ROOT, admin_cli, create_db, drop_db, psql

pytestmark = pytest.mark.db

DUMP_CANDIDATES = [
    os.environ.get("AUTOSHORTS_LEGACY_DUMP", ""),
    # файл, выданный потоку M1-A (pg_dump dev-базы до M0); в CI задаётся AUTOSHORTS_LEGACY_DUMP
    "/tmp/claude-1002/-home-devaccess/dc41870f-ac11-4f9a-865c-ef92b986df32/scratchpad/autoshorts-db-before-m0.dump",
]

VIDEO_ID = "dQw4w9WgXcQ"
TAG = f"yt_{VIDEO_ID}"

# video_uid -> (старый статус, старый reject_reason)
LEGACY_JOBS: dict[str, tuple[str, str | None]] = {
    "lg_queued": ("queued", None),
    "lg_script_ready": ("script_ready", None),
    "lg_voiced": ("voiced", None),
    "lg_rendered": ("rendered", None),
    "lg_qc_passed": ("qc_passed", None),
    "lg_qc_failed": ("qc_failed", "banner_overlap"),
    "lg_pending_review": ("pending_review", None),
    "lg_rework": ("rework", "rework_requested"),
    "lg_tech_fail": ("rejected", "tehnicheskiy_sboy"),
    "lg_rejected": ("rejected", "skuchno"),
    "lg_ready": ("ready_to_publish", None),
    "lg_published": ("published", None),
}

# video_uid -> (status, stage) после 0004
EXPECTED_STATUS: dict[str, tuple[str, str | None]] = {
    "lg_queued": ("queued", "script"),
    "lg_script_ready": ("processing", "voice"),
    "lg_voiced": ("processing", "visuals"),
    "lg_rendered": ("processing", "qc"),
    "lg_qc_passed": ("pending_review", None),
    "lg_qc_failed": ("qc_failed", None),
    "lg_pending_review": ("pending_review", None),
    "lg_rework": ("rework", None),
    "lg_tech_fail": ("failed", None),
    "lg_rejected": ("rejected", None),
    "lg_ready": ("approved", None),
    "lg_published": ("published", None),
}


def _dump_path() -> Path | None:
    for candidate in DUMP_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def _inject_legacy_rows(url: str) -> dict[str, Any]:
    """Строки в старой схеме, покрывающие все ветки переноса. Возвращает то, что было до миграции."""
    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as conn:
        conn.execute(
            """
            INSERT INTO channels (id, niche, platform, cluster) VALUES
              ('taro_main', 'taro', 'youtube', 'cluster_1'),
              ('vk_main', 'taro', 'vk', 'cluster_2')
            ON CONFLICT (id) DO NOTHING
            """
        )
        for uid, (status, reason) in LEGACY_JOBS.items():
            conn.execute(
                """
                INSERT INTO jobs (video_uid, channel_id, niche, template_version, status,
                                  reject_reason, attempt_count)
                VALUES (%(uid)s, 'taro_main', 'taro', 'v1', %(status)s, %(reason)s, 1)
                """,
                {"uid": uid, "status": status, "reason": reason},
            )
        job_ids = {r["video_uid"]: r["id"] for r in conn.execute("SELECT id, video_uid FROM jobs").fetchall()}
        conn.execute(
            """
            INSERT INTO generation_events (job_id, step_name, provider, cost_usd)
            VALUES (%(j)s, 'image', 'fal', 0.003)
            """,
            {"j": job_ids["lg_rendered"]},
        )
        pub_id = conn.execute(
            """
            INSERT INTO publications (job_id, channel_id, platform, platform_video_id,
                                      master_file_path, attribution_tag)
            VALUES (%(j)s, 'taro_main', 'youtube', %(vid)s, 'assets/master_archive/lg_published.mp4', %(tag)s)
            RETURNING id
            """,
            {"j": job_ids["lg_published"], "vid": VIDEO_ID, "tag": TAG},
        ).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO attribution_events (tag, source, route, ip_hash, user_agent) VALUES
              (%(tag)s, 'yt', 'domain', 'iphash1', 'Mozilla/5.0'),
              (%(tag)s, 'yt', 'bot', NULL, 'Mozilla/5.0'),
              ('yt_unknown0000', 'yt', 'domain', 'iphash2', 'Mozilla/5.0'),
              ('health', 'yt', 'domain', NULL, 'kube-probe')
            """,
            {"tag": TAG},
        )
        before = {
            "jobs": {
                r["id"]: r
                for r in conn.execute("SELECT id, video_uid, status, reject_reason FROM jobs").fetchall()
            },
            "attribution_events": conn.execute("SELECT count(*) AS n FROM attribution_events").fetchone()[
                "n"
            ],
            "generation_events": conn.execute("SELECT count(*) AS n FROM generation_events").fetchone()["n"],
        }
    before["job_ids"] = job_ids
    before["publication_id"] = pub_id
    return before


@pytest.fixture(scope="module", params=["dump", "init_db"])
def legacy(request: pytest.FixtureRequest, pg_server_url: str, tmp_path_factory) -> Iterator[dict[str, Any]]:
    source = request.param
    if source == "dump":
        dump = _dump_path()
        if dump is None:
            pytest.skip("нет pg_dump legacy-базы (AUTOSHORTS_LEGACY_DUMP)")
        if not shutil.which("pg_restore"):
            pytest.skip("pg_restore не установлен")
        dbname = "autoshorts_legacy_check"
    else:
        dbname = "autoshorts_legacy_check_initdb"
    url = create_db(pg_server_url, dbname)
    try:
        if source == "dump":
            res = subprocess.run(
                ["pg_restore", "--no-owner", "--no-acl", "--exit-on-error", "-d", url, str(dump)],
                capture_output=True,
                text=True,
                timeout=300,
            )
            assert res.returncode == 0, res.stderr
        else:
            res = psql(url, "-f", str(ROOT / "scripts" / "init_db.sql"))
            assert res.returncode == 0, res.stderr
        before = _inject_legacy_rows(url)

        backup_dir = tmp_path_factory.mktemp(f"legacy_backups_{source}")
        migrated = admin_cli("migrate", "--database-url", url, "--backup-dir", str(backup_dir))
        yield {
            "source": source,
            "url": url,
            "before": before,
            "cli": migrated,
            "backup_dir": backup_dir,
        }
    finally:
        drop_db(pg_server_url, dbname)


def _q(url: str, query: str, params: Any = None) -> list[dict[str, Any]]:
    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as conn:
        return conn.execute(query, params).fetchall()


def test_legacy_migrate_adopts_baseline(legacy: dict[str, Any]) -> None:
    res = legacy["cli"]
    assert res.returncode == 0, res.stderr + res.stdout
    assert "Принята legacy-база" in res.stdout and f"Готово: версия {latest_version()}" in res.stdout
    got = [
        (r["version"], r["adopted"])
        for r in _q(legacy["url"], "SELECT version, adopted FROM schema_migrations")
    ]
    assert sorted(got) == [(1, True)] + [(v, False) for v in file_versions() if v > 1]


def test_legacy_backup_dump_taken_before_migrating(legacy: dict[str, Any]) -> None:
    dumps = list(legacy["backup_dir"].glob("pre_migrate_*.dump"))
    assert len(dumps) == 1
    listing = subprocess.run(
        ["pg_restore", "-l", str(dumps[0])], capture_output=True, text=True, check=True
    ).stdout
    assert "TABLE DATA public jobs" in listing
    assert "TABLE public users" not in listing  # дамп снят ДО 0002


def test_legacy_status_mapping(legacy: dict[str, Any]) -> None:
    jobs = {r["video_uid"]: r for r in _q(legacy["url"], "SELECT video_uid, status, stage FROM jobs")}
    for uid, (status, stage) in EXPECTED_STATUS.items():
        assert (jobs[uid]["status"], jobs[uid]["stage"]) == (status, stage), uid
    allowed = {
        "draft", "queued", "processing", "qc_failed", "pending_review", "rework", "approved",
        "published", "rejected", "failed", "cancelled",
    }  # fmt: skip
    assert {r["status"] for r in jobs.values()} <= allowed
    if legacy["source"] == "dump":
        # из дампа: e2e_test_1 был script_ready, e2e_orchestrator — pending_review
        assert (jobs["e2e_test_1"]["status"], jobs["e2e_test_1"]["stage"]) == ("processing", "voice")
        assert jobs["e2e_orchestrator"]["status"] == "pending_review"
    # данных не потеряли
    assert len(jobs) == len(legacy["before"]["jobs"])


def test_legacy_reject_reason_normalization(legacy: dict[str, Any]) -> None:
    jobs = {
        r["video_uid"]: r
        for r in _q(
            legacy["url"], "SELECT video_uid, status, reject_reason, qc_failed_checks, error_class FROM jobs"
        )
    }
    assert jobs["lg_qc_failed"]["qc_failed_checks"] == ["banner_overlap"]
    assert jobs["lg_qc_failed"]["reject_reason"] is None
    assert jobs["lg_rework"]["reject_reason"] is None
    assert jobs["lg_tech_fail"]["error_class"] == "legacy_max_attempts"
    assert jobs["lg_tech_fail"]["reject_reason"] is None
    assert jobs["lg_rejected"]["reject_reason"] == "skuchno"
    leftovers = _q(
        legacy["url"],
        "SELECT count(*) AS n FROM jobs WHERE reject_reason IN ('rework_requested', 'tehnicheskiy_sboy')",
    )
    assert leftovers[0]["n"] == 0


def test_legacy_unfinished_jobs_moved_to_task_queue(legacy: dict[str, Any]) -> None:
    tasks = _q(
        legacy["url"],
        "SELECT t.kind, t.resource_class, t.stage, t.dedup_key, t.status, t.priority, j.video_uid "
        "FROM tasks t JOIN jobs j ON j.id = t.job_id",
    )
    by_uid = {t["video_uid"]: t for t in tasks}
    ids = legacy["before"]["job_ids"]
    expected = {
        "lg_queued": ("light", "script"),
        "lg_script_ready": ("light", "voice"),
        "lg_voiced": ("light", "visuals"),
        "lg_rendered": ("heavy", "qc"),
    }
    for uid, (rclass, stage) in expected.items():
        t = by_uid[uid]
        assert (t["kind"], t["resource_class"], t["stage"], t["status"]) == (
            "job_stage",
            rclass,
            stage,
            "queued",
        )
        assert t["dedup_key"] == f"job:{ids[uid]}:{stage}:0"
        assert t["priority"] == 50
    if legacy["source"] == "dump":
        assert by_uid["e2e_test_1"]["dedup_key"] == f"job:{ids['e2e_test_1']}:voice:0"
    # терминальные и ожидающие решения задач не получают
    assert not {"lg_qc_passed", "lg_pending_review", "lg_ready", "lg_published", "lg_rejected"} & set(by_uid)
    assert len(tasks) == len(expected) + (1 if legacy["source"] == "dump" else 0)


def test_legacy_clicks_and_tracking_links(legacy: dict[str, Any]) -> None:
    url = legacy["url"]
    links = _q(
        url, "SELECT id, code, product_id, level, publication_id, channel_id, platform FROM tracking_links"
    )
    assert len(links) == 1
    link = links[0]
    assert (link["code"], link["product_id"], link["level"]) == (TAG, "umka", "publication")
    assert (link["publication_id"], link["channel_id"], link["platform"]) == (
        legacy["before"]["publication_id"],
        "taro_main",
        "youtube",
    )

    clicks = _q(
        url, "SELECT code, route, tracking_link_id, visitor_key, product_id, publication_id FROM clicks"
    )
    assert "health" not in {c["code"] for c in clicks}
    assert len(clicks) == legacy["before"]["attribution_events"] - 1
    tagged = [c for c in clicks if c["code"] == TAG]
    assert {c["route"] for c in tagged} == {"landing"}
    assert {c["tracking_link_id"] for c in tagged} == {link["id"]}
    assert sorted(c["visitor_key"] for c in tagged) == ["iphash1", "legacy"]
    assert {c["publication_id"] for c in tagged} == {legacy["before"]["publication_id"]}
    unknown = [c for c in clicks if c["code"] == "yt_unknown0000"]
    assert (
        len(unknown) == 1 and unknown[0]["route"] == "unknown_code" and unknown[0]["tracking_link_id"] is None
    )


def test_legacy_catalog_backfilled(legacy: dict[str, Any]) -> None:
    url = legacy["url"]
    niches = _q(url, "SELECT id, status FROM niches")
    assert {(n["id"], n["status"]) for n in niches} == {("taro", "needs_import")}
    assert {r["id"] for r in _q(url, "SELECT id FROM clusters")} >= {"cluster_1", "cluster_2"}
    channels = {r["id"]: r for r in _q(url, "SELECT id, niche_id, platform, status FROM channels")}
    assert channels["vk_main"]["niche_id"] == "taro" and channels["vk_main"]["platform"] == "vk"
    assert {r["niche_id"] for r in _q(url, "SELECT DISTINCT niche_id FROM jobs")} == {"taro"}
    assert _q(url, "SELECT count(*) AS n FROM products WHERE id = 'umka'")[0]["n"] == 1


def test_legacy_cost_and_budget_preserved(legacy: dict[str, Any]) -> None:
    url = legacy["url"]
    ge = _q(url, "SELECT count(*) AS n, count(*) FILTER (WHERE status = 'ok') AS ok FROM generation_events")[
        0
    ]
    assert ge["n"] == ge["ok"] == legacy["before"]["generation_events"]
    budget = _q(url, "SELECT id, reserved_usd, alert_threshold_pct FROM budget_state")
    assert budget == [{"id": 1, "reserved_usd": 0, "alert_threshold_pct": 80}]


def test_legacy_second_migrate_is_noop(legacy: dict[str, Any]) -> None:
    res = admin_cli("migrate", "--dry-run", "--database-url", legacy["url"])
    assert res.returncode == 0, res.stderr
    assert "Миграции не нужны" in res.stdout
