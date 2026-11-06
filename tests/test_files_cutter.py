"""Файлы исходников и блок для нарезчика в брифе."""
import os
from pathlib import Path

from kinotrends import db, files, recommend, stats

TMP = Path(os.environ["KT_VAR"])


def test_cleanup_and_request_rules(monkeypatch):
    c = db.connect(TMP / "files.sqlite")
    db.init_db(c)
    files.init(c)
    monkeypatch.setattr(files, "FILES_DIR", TMP / "files")
    (TMP / "files").mkdir(exist_ok=True)
    old = TMP / "files" / "old.mp4"
    old.write_bytes(b"x" * 1000)
    c.execute("INSERT INTO downloads(title_id, video_id, url, status, path, size, created_at, finished_at) VALUES ('Q1','rt_old','https://rutube.ru/video/old/','done',?,1000,'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')", (str(old),))
    c.commit()
    assert files.cleanup(c) == 1 and not old.exists()
    assert c.execute("SELECT status FROM downloads WHERE video_id = 'rt_old'").fetchone()[0] == "expired"
    res = files.request(c, "Q1", "yt1", "https://www.youtube.com/watch?v=yt1")
    assert not res["ok"] and "YouTube" in res["error"]
    monkeypatch.setattr(files, "free_gb", lambda: 1.0)
    res = files.request(c, "Q1", "rt_new", "https://rutube.ru/video/new/")
    assert not res["ok"] and "места" in res["error"]


def test_cutter_hint_and_brief_block():
    ins = {"scenes": [{"name": "Драка в столовой", "what_to_cut": "от входа до падения", "quotes": ["Ты кто такой?"]}], "advice": ["до 30 секунд"]}
    hint = recommend.cutter_hint(ins, None)
    assert "Драка в столовой" in hint and "«Ты кто такой?»" in hint and "Подача" in hint
    assert recommend.cutter_hint(None, {"text": "Почему: потому.\nРиск: нет"}) == "Почему: потому."
    c = db.connect(TMP / "neuro.sqlite")  # база из test_neuro_recommend с Q1 и роликами
    db.init_db(c)
    if c.execute("SELECT 1 FROM titles WHERE id = 'Q1'").fetchone():
        files.init(c)
        b = recommend.brief(c, stats.Filters("30", lang="ru"), "Q1")
        assert "cutter" in b and b["cutter"]["clip_min_s"] <= b["cutter"]["clip_max_s"] and "hint" in b["cutter"]


def test_watch_relaunches_download_that_never_started(monkeypatch):
    """Загрузка ждала очереди внутри сервиса и пропала с перезапуском: на хранилище нет ни процесса, ни лога —
    сторож запускает её заново, а не пишет «загрузка оборвалась» (01.11 «Резня зомби», «Need for Speed»)."""
    import subprocess
    import time as _time

    c = db.connect()  # сторож открывает базу по умолчанию (в тестах — во временной папке KT_VAR)
    db.init_db(c)
    files.init(c)
    c.execute("INSERT OR REPLACE INTO downloads(title_id, video_id, url, status, created_at) VALUES ('Q1','w1','https://rutube.ru/video/w1/','running','2026-11-01T00:00:00Z')")
    c.commit()
    monkeypatch.setattr(files, "_ssh", lambda st, cmd, timeout=60: subprocess.CompletedProcess(cmd, 0, "NOLOG\n", ""))
    clock = iter([0, 100, 200, 300, 400])
    monkeypatch.setattr(files.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(files.time, "sleep", lambda s: None)
    started = []
    monkeypatch.setattr(files, "_download", lambda vid, url, st: started.append((vid, url)))
    files._watch_remote("w1", "/srv/files/w1.mp4", {"remote": True})
    deadline = _time.time() + 2
    while not started and _time.time() < deadline:
        _time.sleep(0.01)
    assert started == [("w1", "https://rutube.ru/video/w1/")]
    assert c.execute("SELECT status FROM downloads WHERE video_id = 'w1'").fetchone()[0] == "running"
    assert files._running.get("w1") is True  # флаг держится, второго сторожа не будет
    files._running.pop("w1", None)
