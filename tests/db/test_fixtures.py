"""Фикстуры tests/conftest.py: TRUNCATE-изоляция, сиды миграций, защита dev-базы."""

from __future__ import annotations

import os

import pytest

from tests.conftest import SAFE_DB_PREFIX, assert_safe_database, database_of


def test_db_url_is_a_test_database(db_url: str) -> None:
    assert database_of(db_url).startswith(SAFE_DB_PREFIX)


def test_process_database_url_points_to_test_db() -> None:
    """Код, читающий DATABASE_URL сам (legacy config), в тестах не видит dev-базу из .env."""
    assert database_of(os.environ["DATABASE_URL"]).startswith(SAFE_DB_PREFIX)


@pytest.mark.parametrize("name", ["autoshorts", "postgres", "prod_autoshorts"])
def test_unsafe_database_names_rejected(name: str) -> None:
    with pytest.raises(pytest.UsageError):
        assert_safe_database(name)


def test_database_is_on_latest_migration(db_conn) -> None:
    from autoshorts.db.migrate import latest_version

    row = db_conn.execute("SELECT max(version) AS v FROM schema_migrations").fetchone()
    assert row["v"] == latest_version()


def _insert_user(conn, login: str) -> int:
    return conn.execute(
        "INSERT INTO users (login, display_name, password_hash, role)"
        " VALUES (%s, %s, 'x', 'viewer') RETURNING id",
        (login, login),
    ).fetchone()["id"]


@pytest.mark.parametrize("attempt", [1, 2])
def test_each_test_starts_clean_with_restarted_sequences(db_conn, attempt: int) -> None:
    # оба прогона видят пустую таблицу и id с единицы — очистка между тестами работает
    assert db_conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == 0
    assert _insert_user(db_conn, f"user{attempt}") == 1


def test_seeds_restored_after_truncate(db_conn) -> None:
    db_conn.execute("DELETE FROM schedules")
    db_conn.execute("DELETE FROM provider_configs")
    db_conn.execute("UPDATE budget_state SET spent_today_usd = 5")


def test_seeds_back_in_next_test(db_conn) -> None:
    counts = db_conn.execute(
        "SELECT (SELECT count(*) FROM schedules) AS s, (SELECT count(*) FROM provider_configs) AS p,"
        " (SELECT count(*) FROM products WHERE id = 'umka') AS u,"
        " (SELECT spent_today_usd FROM budget_state WHERE id = 1) AS spent"
    ).fetchone()
    assert (counts["s"], counts["p"], counts["u"], float(counts["spent"])) == (14, 15, 1, 0.0)


def test_db_conn_session_is_utc(db_conn) -> None:
    assert db_conn.execute("SHOW TimeZone").fetchone()["TimeZone"] == "UTC"


def test_app_env_points_core_config_to_test_db(app_env) -> None:
    from autoshorts.core.config import get_env

    env = get_env()
    assert env is app_env
    assert database_of(env.database_url).startswith(SAFE_DB_PREFIX)
    assert env.app_env == "test" and not env.is_prod
    assert env.redirect_salt is not None and env.as_secrets_key is not None


def test_reset_handles_fk_cycle(db_conn, _db_state) -> None:
    """publications → upload_packages → tracking_links → publications: DELETE упирается в цикл,
    очистка откатывается на TRUNCATE … CASCADE и всё равно возвращает сиды."""
    from tests.conftest import reset_database

    c = db_conn
    c.execute("INSERT INTO niches (id, label) VALUES ('taro', 'Таро')")
    c.execute("INSERT INTO clusters (id, title) VALUES ('c1', 'c1')")
    c.execute(
        "INSERT INTO channels (id, niche_id, platform, cluster) VALUES ('ch1', 'taro', 'youtube', 'c1')"
    )
    job = c.execute(
        "INSERT INTO jobs (video_uid, channel_id, niche_id, template_version)"
        " VALUES ('u1', 'ch1', 'taro', 'v1') RETURNING id"
    ).fetchone()["id"]
    art = c.execute(
        "INSERT INTO artifacts (job_id, kind, path, input_hash)"
        " VALUES (%s, 'final', 'jobs/u1/final.mp4', 'h') RETURNING id",
        (job,),
    ).fetchone()["id"]
    link = c.execute(
        "INSERT INTO tracking_links (code, product_id, level, job_id)"
        " VALUES ('k7m2q', 'umka', 'package', %s) RETURNING id",
        (job,),
    ).fetchone()["id"]
    pkg = c.execute(
        "INSERT INTO upload_packages (job_id, channel_id, platform, title, description, tracking_link_id, "
        "final_artifact_id) VALUES (%s, 'ch1', 'youtube', 't', 'd', %s, %s) RETURNING id",
        (job, link, art),
    ).fetchone()["id"]
    pub = c.execute(
        "INSERT INTO publications (job_id, channel_id, platform, master_file_path, package_id) "
        "VALUES (%s, 'ch1', 'youtube', 'masters/u1.mp4', %s) RETURNING id",
        (job, pkg),
    ).fetchone()["id"]
    c.execute("UPDATE tracking_links SET publication_id = %s WHERE id = %s", (pub, link))
    c.execute("DELETE FROM schedules WHERE name = 'cleanup'")

    reset_database(c, _db_state[1])
    left = c.execute(
        "SELECT (SELECT count(*) FROM jobs) + (SELECT count(*) FROM publications)"
        " + (SELECT count(*) FROM tracking_links) + (SELECT count(*) FROM niches) AS n,"
        " (SELECT count(*) FROM schedules) AS s, (SELECT count(*) FROM products) AS p"
    ).fetchone()
    assert (left["n"], left["s"], left["p"]) == (0, 14, 1)
    assert c.execute(
        "INSERT INTO niches (id, label) VALUES ('taro', 'Таро') RETURNING current_version"
    ).fetchone() == {"current_version": 0}
