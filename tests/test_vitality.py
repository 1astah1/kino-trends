"""Жизнь каналов: когда канал умер (ролики есть, показов нет) и когда ожил."""
from kinotrends import vitality


def _r(i, v, blocked=False):
    return {"video_id": f"v{i}", "published_at": f"2026-11-{i:02d}T09:00:00Z", "v24": v, "title": f"ролик {i}", "blocked": blocked}


def test_v24_interpolates_and_bounds():
    assert vitality.v24([(12.0, 100), (36.0, 300)], 40.0, 320) == (200, True)
    assert vitality.v24([], 10.0, 5) == (None, False)          # моложе суток
    assert vitality.v24([], 72.0, 3) == (3, True)               # и сейчас ноль — значит и за сутки ноль
    assert vitality.v24([], 72.0, 900)[1] is False              # без снимков большое число — приблизительно


def test_dies_after_streak_and_revives():
    rows = [_r(1, 1200), _r(2, 900), _r(3, 3), _r(4, 0), _r(5, 7, blocked=True), _r(6, 1), _r(7, 2), _r(8, 1500)]
    t = vitality.timeline(rows)
    kinds = [(e["kind"], e["at"][:10]) for e in t["events"]]
    assert kinds == [("died", "2026-11-03"), ("revived", "2026-11-08")]   # смерть — выход первого нуля серии
    assert t["events"][0]["evidence"]["last_alive"]["video_id"] == "v2"
    assert t["status"] == "alive"


def test_weak_video_breaks_zero_streak():
    rows = [_r(1, 500), _r(2, 0), _r(3, 0), _r(4, 50), _r(5, 0), _r(6, 0)]
    t = vitality.timeline(rows)
    assert t["events"] == [] and t["status"] == "dying" and t["zero_run"] == 2


def test_never_alive_channel_is_not_dead():
    t = vitality.timeline([_r(i, 2) for i in range(1, 7)])
    assert t["status"] == "never" and t["events"] == []
    assert vitality.timeline([_r(1, 2), _r(2, 1)])["status"] == "new"
