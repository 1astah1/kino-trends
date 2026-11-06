"""M0: бюджет по бизнес-дню БД (Europe/Moscow), а не по date.today() процесса (ARCHITECTURE §3.2, §10.2 M0).

Нужен живой PostgreSQL. Тест создаёт и в конце удаляет свою базу autoshorts_test_m0_budget,
dev-базу autoshorts не трогает. Нет Postgres — модуль пропускается.
"""
import dataclasses
import os
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

psycopg = pytest.importorskip("psycopg")

from autoshorts import db  # noqa: E402

PG_BASE = os.getenv("AUTOSHORTS_M0_PG_BASE", "postgresql://autoshorts:autoshorts@127.0.0.1:5432")
TEST_DB = "autoshorts_test_m0_budget"
# Сессия БД в часовом поясе UTC+14: её CURRENT_DATE часто на день впереди Москвы.
# Бюджет обязан жить по московскому дню независимо от TimeZone сессии и TZ процесса.
SKEWED_TZ = "Pacific/Kiritimati"


def _admin():
    return psycopg.connect(f"{PG_BASE}/postgres", autocommit=True, connect_timeout=5)


@pytest.fixture(scope="module")
def test_db_url():
    try:
        with _admin() as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}"')
            conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    except psycopg.OperationalError as e:  # pragma: no cover - окружение без Postgres
        pytest.skip(f"PostgreSQL недоступен: {e}")
    url = f"{PG_BASE}/{TEST_DB}"
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute((ROOT / "scripts" / "init_db.sql").read_text(encoding="utf-8"))
    try:
        yield url
    finally:
        with _admin() as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')


@pytest.fixture
def budget(monkeypatch, test_db_url):
    skewed_url = f"{test_db_url}?options={quote(f'-c TimeZone={SKEWED_TZ}')}"
    monkeypatch.setattr(db, "SETTINGS", dataclasses.replace(db.SETTINGS, database_url=skewed_url))
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        moscow_day = conn.execute("SELECT (now() AT TIME ZONE 'Europe/Moscow')::date").fetchone()[0]

    def set_state(day_sql: str, spent: float, paused: bool, limit: float = 15.0):
        with psycopg.connect(test_db_url, autocommit=True) as conn:
            conn.execute(
                f"UPDATE budget_state SET day = {day_sql}, spent_today_usd = %s, paused = %s, "
                f"daily_limit_usd = %s WHERE id = 1",
                (spent, paused, limit),
            )

    def get_state():
        with psycopg.connect(test_db_url, autocommit=True) as conn:
            row = conn.execute("SELECT day, spent_today_usd, paused FROM budget_state WHERE id = 1").fetchone()
        return {"day": row[0], "spent": float(row[1]), "paused": row[2]}

    return set_state, get_state, moscow_day


def test_new_business_day_resets_counter(budget):
    set_state, get_state, moscow_day = budget
    set_state("(now() AT TIME ZONE 'Europe/Moscow')::date - 1", spent=14.9, paused=True)
    assert db.check_and_reserve_budget(0.003) is True
    st = get_state()
    assert st["day"] == moscow_day
    assert st["spent"] == pytest.approx(0.003)
    assert st["paused"] is False


def test_same_business_day_keeps_counter(budget):
    set_state, get_state, moscow_day = budget
    set_state("(now() AT TIME ZONE 'Europe/Moscow')::date", spent=5.0, paused=False)
    assert db.check_and_reserve_budget(1.0) is True
    st = get_state()
    assert st["day"] == moscow_day
    assert st["spent"] == pytest.approx(6.0)


def test_session_current_date_is_not_used(budget):
    """В строке уже московский день. Если бы код сравнивал с CURRENT_DATE сессии (UTC+14),
    счётчик сбросился бы посреди московского дня, когда в Кирибати уже завтра."""
    set_state, get_state, moscow_day = budget
    set_state("(now() AT TIME ZONE 'Europe/Moscow')::date", spent=14.0, paused=True)
    assert db.check_and_reserve_budget(0.5) is False
    st = get_state()
    assert st["day"] == moscow_day
    assert st["spent"] == pytest.approx(14.0)
    assert st["paused"] is True


def test_limit_exceeded_pauses(budget):
    set_state, get_state, _ = budget
    set_state("(now() AT TIME ZONE 'Europe/Moscow')::date", spent=14.999, paused=False)
    assert db.check_and_reserve_budget(0.003) is False
    st = get_state()
    assert st["paused"] is True
    assert st["spent"] == pytest.approx(14.999)
