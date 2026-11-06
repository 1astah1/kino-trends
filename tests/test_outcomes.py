"""Итоги публикаций: распознавание блокировки, запреты подбора, оценка по просмотрам, снятие роликов."""
import os
from pathlib import Path

import pytest

from kinotrends import db, outcomes

TMP = Path(os.environ["KT_VAR"])


def test_classify_block_states():
    assert outcomes.classify(None)[0] == "removed"
    naruto = {"status": {"uploadStatus": "processed"}, "contentDetails": {"regionRestriction": {"allowed": ["CA", "US"]}}}
    block, why = outcomes.classify(naruto)
    assert block == "blocked_ru" and "CA, US" in why
    assert outcomes.classify({"status": {}, "contentDetails": {"regionRestriction": {"blocked": ["RU", "BY"]}}})[0] == "blocked_ru"
    assert outcomes.classify({"status": {"uploadStatus": "rejected", "rejectionReason": "copyright"}})[0] == "rejected"
    assert outcomes.classify({"status": {"uploadStatus": "processed"}, "contentDetails": {}}) == (None, None)
    assert outcomes.classify({"status": {}, "contentDetails": {"regionRestriction": {"allowed": ["RU", "KZ"]}}}) == (None, None)


def test_score_bonus_prefers_hits_and_punishes_flops():
    assert outcomes.score_bonus(None) == 0.0
    assert outcomes.score_bonus({"n": 2, "vph": 99, "flops": 0}) == pytest.approx(30.0)
    assert outcomes.score_bonus({"n": 2, "vph": 0.2, "flops": 2}) < -20


@pytest.fixture()
def conn():
    c = db.connect(TMP / "outcomes.sqlite")
    db.init_db(c)
    outcomes.init(c)
    for t in ("pub_outcomes", "title_bans", "holder_bans", "pipeline_items", "title_rights"):
        c.execute(f"DELETE FROM {t}")
    c.commit()
    yield c
    c.close()


def _rights(c, tid, holder, policy, level="caution", kind="series", countries='["США"]'):
    c.execute("INSERT INTO titles(id, label_ru, kind, countries, updated_at) VALUES (?, ?, ?, ?, 'now') "
              "ON CONFLICT(id) DO UPDATE SET kind = excluded.kind, countries = excluded.countries", (tid, tid, kind, countries))
    c.execute("INSERT INTO title_rights(title_id, level, risk, reasons, rule, clip_max_s, holder, holder_policy, checked_at) "
              "VALUES (?, ?, 45, '[]', '', 59, ?, ?, '2026-10-29T00:00:00Z')", (tid, level, holder, policy))


def test_block_holders_and_learned_bans_are_excluded(conn):
    _rights(conn, "Q_naruto", "TV Tokyo, Shueisha, Pierrot", "block")
    _rights(conn, "Q_rick", "Warner Bros", "claim")
    _rights(conn, "Q_dragon", "TV Tokyo, Shueisha, Pierrot", "claim")
    conn.execute("INSERT INTO settings(key, value) VALUES ('exclude_ru_enforced', '0') ON CONFLICT(key) DO UPDATE SET value = '0'")
    conn.commit()
    assert outcomes.banned_title_ids(conn, ["Q_naruto", "Q_rick", "Q_dragon"]) == set()  # по умолчанию решают итоги
    conn.execute("INSERT INTO holder_bans(holder, reason, at) VALUES ('TV Tokyo, Shueisha, Pierrot', 'x', 'now')")
    conn.commit()
    assert outcomes.banned_title_ids(conn, ["Q_naruto", "Q_rick", "Q_dragon"]) == {"Q_naruto", "Q_dragon"}
    conn.execute("DELETE FROM holder_bans")
    conn.execute("INSERT INTO settings(key, value) VALUES ('exclude_block_holders', '1') ON CONFLICT(key) DO UPDATE SET value = '1'")
    conn.commit()
    assert outcomes.banned_title_ids(conn, ["Q_naruto", "Q_rick", "Q_dragon"]) == {"Q_naruto"}  # строгий режим
    conn.execute("DELETE FROM settings WHERE key IN ('exclude_block_holders', 'exclude_ru_enforced')")
    conn.commit()


def test_russian_and_japanese_holders_are_excluded_before_any_block(conn):
    _rights(conn, "Q_svaty", "Студия Квартал 95, телеканал Россия 1", "block", countries='["Украина"]')
    _rights(conn, "Q_papiny", "КиноПоиск", "block", countries='["Россия"]')
    _rights(conn, "Q_dmb", "unknown", "unknown", kind="film", countries='["Россия"]')
    _rights(conn, "Q_onepiece", "Toei Animation", "block", kind="anime", countries="[]")
    _rights(conn, "Q_standup", "unknown", "unknown", countries='["Япония"]')
    _rights(conn, "Q_griffins", "20th Television, Disney+", "block", kind="animated_series")
    _rights(conn, "Q_vek", "unknown", "unknown", countries='["Турция"]')
    conn.commit()
    ids = ["Q_svaty", "Q_papiny", "Q_dmb", "Q_onepiece", "Q_standup", "Q_griffins", "Q_vek"]
    assert outcomes.banned_title_ids(conn, ids) == {"Q_svaty", "Q_papiny", "Q_dmb", "Q_onepiece", "Q_standup"}
    assert "Россия 1" in outcomes.ru_enforced(conn, ["Q_svaty"])["Q_svaty"]
    conn.execute("INSERT INTO settings(key, value) VALUES ('exclude_ru_enforced', '0') ON CONFLICT(key) DO UPDATE SET value = '0'")
    conn.commit()
    assert outcomes.banned_title_ids(conn, ids) == set()
    # запрет одного правообладателя закрывает всю его группу: «Россия 1» → ВГТРК
    _rights(conn, "Q_anna", "ВГТРК, Star Media", "block", countries='["Польша"]')
    conn.execute("INSERT INTO holder_bans(holder, reason, at) VALUES ('Студия Квартал 95, телеканал Россия 1', 'x', 'now')")
    conn.commit()
    assert outcomes.banned_title_ids(conn, ids + ["Q_anna"]) == {"Q_svaty", "Q_anna"}
    conn.execute("DELETE FROM settings WHERE key = 'exclude_ru_enforced'")
    conn.commit()


def test_purge_withdraws_queued_titles_now_banned(conn):
    _rights(conn, "Q_rublevka", "ТНТ, Premier", "block", countries='["Россия"]')
    _rights(conn, "Q_dune", "Warner Bros. Pictures", "block", kind="film")
    conn.execute("INSERT INTO pipeline_items(kind, ref_id, label, status, as_jobs, created_at, updated_at) "
                 "VALUES ('film', 'Q_rublevka', 'Полицейский с Рублёвки', 'review', '[7, 8]', 'now', 'now')")
    conn.execute("INSERT INTO pipeline_items(kind, ref_id, label, status, as_jobs, created_at, updated_at) "
                 "VALUES ('film', 'Q_dune', 'Дюна', 'review', '[9]', 'now', 'now')")
    conn.commit()
    fake = FakeAS({7: "pending_review", 8: "published", 9: "pending_review"})
    res = outcomes.purge(conn, fake)
    assert res["titles"] == ["Полицейский с Рублёвки"] and fake.cancelled == [7]
    st = dict(conn.execute("SELECT ref_id, status FROM pipeline_items").fetchall())
    assert st == {"Q_rublevka": "cancelled", "Q_dune": "review"}


class FakeAS:
    def __init__(self, statuses):
        self.statuses, self.cancelled, self.unscheduled = statuses, [], []

    def get(self, path, **params):
        jid = int(path.rsplit("/", 1)[-1])
        return {"id": jid, "status": self.statuses[jid]}

    def package_of_job(self, jid):
        return {"id": 1000 + int(jid)} if self.statuses[int(jid)] == "approved" else {}

    def _req(self, method, path, body=None, params=None, retry=True):
        self.unscheduled.append(path)
        return {}

    def post(self, path, body=None):
        self.cancelled.append(int(path.split("/")[-2]))
        return {}


def test_learn_bans_blocked_title_and_cancels_unpublished_clips(conn):
    _rights(conn, "Q_naruto", "TV Tokyo, Shueisha, Pierrot", "claim")
    conn.execute("INSERT INTO pipeline_items(kind, ref_id, label, status, as_jobs, created_at, updated_at) "
                 "VALUES ('film', 'Q_naruto', 'Наруто', 'review', '[1, 2, 3, 4]', 'now', 'now')")
    conn.execute("INSERT INTO pub_outcomes(video_id, job_id, title_id, title_label, block, block_reason, vph, age_h) "
                 "VALUES ('3KrOxcVBaic', 1, 'Q_naruto', 'Наруто', 'blocked_ru', 'заблокирован в России', 0, 2)")
    conn.commit()
    fake = FakeAS({1: "published", 2: "approved", 3: "pending_review", 4: "cancelled"})
    res = outcomes.learn(conn, {}, fake)
    assert res["banned_titles"] == ["Наруто"] and res["banned_holders"] == ["TV Tokyo, Shueisha, Pierrot"]
    assert sorted(fake.cancelled) == [2, 3] and fake.unscheduled == ["/api/v1/packages/1002/autopublish"]
    assert conn.execute("SELECT status FROM pipeline_items WHERE ref_id = 'Q_naruto'").fetchone()[0] == "cancelled"
    assert outcomes.learn(conn, {}, fake)["banned_titles"] == []  # повторно не банит и не снимает


def test_removed_video_does_not_ban(conn):
    conn.execute("INSERT INTO pub_outcomes(video_id, job_id, title_id, title_label, block, block_reason, vph, age_h) "
                 "VALUES ('gone', 5, 'Q_brest', 'Брестская крепость', 'removed', 'ролика нет на YouTube', 0, 30)")
    conn.commit()
    assert outcomes.learn(conn, {}, FakeAS({}))["banned_titles"] == []


def test_probe_holds_rest_until_first_clip_proven(conn):
    from kinotrends import pipeline
    item = {"id": 1, "kind": "film", "ref_id": "Q_gent", "metrics": {}}
    assert pipeline._probe_hold(conn, item, {}) is False  # первый ролик — проба, выходит
    item["metrics"] = {"probe_pid": 77}
    assert pipeline._probe_hold(conn, item, {}) is True  # проба ещё не проверена
    conn.execute("INSERT INTO pub_outcomes(video_id, title_id, title_label, block, vph, age_h) VALUES ('v1', 'Q_gent', 'Джентльмены', NULL, 119, 0.5)")
    conn.commit()
    assert pipeline._probe_hold(conn, item, {}) is True  # провисела меньше часа
    assert pipeline._probe_hold(conn, item, {"pipeline_probe_hours": "0.4"}) is False  # порог из настроек
    conn.execute("UPDATE pub_outcomes SET age_h = 1.5 WHERE video_id = 'v1'")
    conn.commit()
    assert pipeline._probe_hold(conn, item, {}) is False  # проба прошла — остальные выходят
    conn.execute("INSERT INTO pub_outcomes(video_id, title_id, title_label, block, vph, age_h) VALUES ('v2', 'Q_gent', 'Джентльмены', 'blocked_ru', 0, 5)")
    conn.commit()
    assert pipeline._probe_hold(conn, item, {}) is True  # был блок — держим всё
    assert pipeline._probe_hold(conn, item, {"pipeline_probe": "0"}) is False
