"""Общие помощники тестов нарезки (M5-B). Тестов здесь нет.

* `synth_media(tmp_path_factory)` — крошечный синтетический источник: testsrc2 640×360 + «речь» из
  тональных слогов (mock TTS движка) с точными таймингами слов → talk.mp4 и транскрипт;
* `seed_source(db, media_root, media, rights_type=…)` — источник в БД как после конвейера M5-A:
  каталог `sources/<id>` с source.json движка, прокси, транскрипт, запись прав, `transcripts`;
* `workers(app_env, tmp_path)` — настоящие воркеры light и heavy в потоках (как в test_jobs_flow);
* `wait_status(db, job_id, statuses)` — дождаться статуса ролика.

Окружение нарезки в тестах: `CLIP_LLM`, `CLIP_TTS=mock`, `CLIP_VOICE_ASR=mock` (фикстура `clip_env`).
"""

from __future__ import annotations

import contextlib
import secrets
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

TEXT = (
    "Почему одни ролики досматривают, а другие нет? Всё решают первые три секунды. "
    "Если зритель не понял, о чём ролик, он листает дальше. Поэтому хук должен быть конкретным. "
    "Второе правило касается темпа. Паузы длиннее секунды убивают внимание. "
    "Третье правило про финал. Закончите мыслью, а не прощанием."
)
LEAD_SEC, TAIL_SEC = 5.0, 3.0

OWN_RIGHTS = {
    "rights_type": "own",
    "owner_name": "Umka",
    "proof_note": "own footage",
    "allowlist_status": "not_needed",
}
LICENSED_RIGHTS = {
    "rights_type": "licensed_written",
    "owner_name": "Test Owner",
    "proof_note": "письменная лицензия, переписка 27.09.2026",
    "attribution_text": "Test Owner, youtube.com/@test",
    "allowlist_status": "allowlisted",
}


def ffmpeg(*args: str) -> None:
    subprocess.run(
        ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-threads", "2", *args],
        check=True,
    )


@dataclass
class SynthMedia:
    video: Path
    transcript: Path
    duration: float


def make_synth_media(folder: Path, text: str = TEXT) -> SynthMedia:
    from autoshorts.clipping.author.voice import synthesize_mock
    from autoshorts.clipping.transcript import Transcript, Word

    folder.mkdir(parents=True, exist_ok=True)
    take = synthesize_mock("speech", text, folder, base_hz=140.0)
    dur = LEAD_SEC + take.duration + TAIL_SEC
    video = folder / "talk.mp4"
    ffmpeg(
        "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30:duration={dur:.3f}",
        "-i", str(take.path), "-af", f"adelay={int(LEAD_SEC * 1000)}:all=1,apad", "-t", f"{dur:.3f}",
        "-c:v", "libx264", "-preset", "ultrafast", "-g", "30", "-pix_fmt", "yuv420p", "-c:a", "aac",
        str(video),
    )  # fmt: skip
    words = [Word(w.w, round(w.s + LEAD_SEC, 3), round(w.e + LEAD_SEC, 3)) for w in take.words]
    tr = Transcript.from_words(words, language="ru", duration=dur, provider="mock")
    tpath = tr.save(folder / "transcript.json.gz")
    return SynthMedia(video, tpath, dur)


def _link(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        dst.hardlink_to(src)
    except OSError:
        shutil.copy2(src, dst)


def seed_user(db: Any, login: str = "seed_uploader") -> int:
    row = db.execute("SELECT id FROM users WHERE login = %s", (login,)).fetchone()
    if row:
        return int(row["id"])
    return int(
        db.execute(
            "INSERT INTO users (login, display_name, role, password_hash) VALUES (%s, %s, 'operator', 'x') "
            "RETURNING id",
            (login, login),
        ).fetchone()["id"]
    )


def seed_source(
    db: Any,
    media_root: Path | str,
    media: SynthMedia,
    *,
    rights: dict[str, Any] | None = None,
    origin: str = "upload",
    with_original: bool = True,
    niche_id: str | None = "taro",
    status: str = "ready",
    title: str = "Синтетический доклад",
) -> dict[str, Any]:
    """Источник как после source_prepare + ASR: файлы в MEDIA_ROOT/sources/<id>, строки в БД."""
    from autoshorts.clipping.source_ingest import PreparedSource
    from autoshorts.clipping.transcript import Transcript
    from autoshorts.pipeline.proxy import probe_media

    rights = dict(rights or OWN_RIGHTS)
    upload_id = None
    if origin == "upload":
        uid = seed_user(db)
        upload_id = secrets.token_hex(16)
        db.execute(
            "INSERT INTO uploads (id, user_id, purpose, filename, length_bytes, received_bytes, path, status,"
            " expires_at) VALUES (%s, %s, 'source', 'talk.mp4', 1, 1, %s, 'consumed',"
            " now() + interval '1 day')",
            (upload_id, uid, f"uploads/{upload_id}.part"),
        )
    pr = probe_media(media.video)
    row = db.execute(
        """INSERT INTO sources (title, origin, origin_url, upload_id, status, language, niche_id,
                                duration_sec, width, height, fps, has_audio, watermark_suspect,
                                watermark_detail)
           VALUES (%s, %s, %s, %s, %s, 'ru', %s, %s, %s, %s, 30, true, false, %s) RETURNING *""",
        (
            title,
            origin,
            "https://example.org/watch?v=synthetic" if origin == "url" else None,
            upload_id,
            status,
            niche_id,
            round(media.duration, 2),
            pr.width,
            pr.height,
            Jsonb({"suspect": False, "severity": "ok"}),
        ),
    ).fetchone()
    sid = int(row["id"])
    rel_dir = f"sources/{sid}"
    folder = Path(media_root) / rel_dir
    files: dict[str, Any] = {"proxy": "proxy_540p.mp4", "transcript": "transcript.json.gz"}
    _link(media.video, folder / "proxy_540p.mp4")
    _link(media.transcript, folder / "transcript.json.gz")
    if with_original:
        _link(media.video, folder / "original.mp4")
        files["original"] = "original.mp4"
    else:
        audio = folder / "audio.m4a"
        ffmpeg("-i", str(media.video), "-vn", "-c:a", "copy", str(audio))
        files["audio"] = "audio.m4a"
    tr = Transcript.load(media.transcript)
    prepared = PreparedSource(
        source_id=str(sid),
        dir=folder,
        title=title,
        origin=origin,
        rights=rights,
        rights_warnings=[],
        probe={
            "duration": pr.duration,
            "width": pr.width,
            "height": pr.height,
            "fps": 30.0,
            "has_audio": True,
            "rotation": 0,
        },
        sha256=None,
        files=files,
        origin_url="https://example.org/watch?v=synthetic" if origin == "url" else None,
        watermark={"suspect": False, "severity": "ok"},
    )
    prepared.save()
    db.execute("UPDATE sources SET media_dir = %s WHERE id = %s", (rel_dir, sid))
    db.execute(
        """INSERT INTO source_rights (source_id, rights_type, owner_name, proof_note, allowlist_status,
                                      attribution_text)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (
            sid,
            rights["rights_type"],
            rights["owner_name"],
            rights.get("proof_note") or "test",
            rights.get("allowlist_status") or "unknown",
            rights.get("attribution_text"),
        ),
    )
    db.execute(
        """INSERT INTO transcripts (source_id, provider_key, model, language, path, words_count,
                                    sentences_count, duration_sec, is_current)
           VALUES (%s, 'mock', 'mock-v1', 'ru', %s, %s, %s, %s, true)""",
        (sid, f"{rel_dir}/transcript.json.gz", len(tr.words), len(tr.sentences), round(tr.duration, 2)),
    )
    return {**row, "media_dir": rel_dir, "dir": folder, "transcript": tr}


def add_candidate(
    db: Any, source_id: int, tr: Any, *, first: int = 0, last: int | None = None, **fields: Any
) -> dict[str, Any]:
    """Автоматический кандидат (как после отбора) на предложения first..last."""
    last = len(tr.sentences) - 1 if last is None else last
    s, e = tr.sentences[first], tr.sentences[last]
    values = {
        "source_id": source_id,
        "start_sid": first,
        "end_sid": last,
        "hook_sid": first,
        "start_sec": round(s.s - 0.2, 2),
        "end_sec": round(e.e + 0.3, 2),
        "title": "Первые три секунды решают",
        "hook_text": "Почему ролики не досматривают",
        "hook_type": "question",
        "reason": "тест",
        "transcript_excerpt": " ".join(x.text for x in tr.sentences[first : last + 1]),
        "llm_scores": Jsonb(
            {"hook": 7, "standalone": 7, "payoff": 6, "emotion": 5, "niche_fit": 6, "novelty": 5}
        ),
        "features": Jsonb({"score_parts": {"llm": 0.6, "feat": 0.7, "len_fit": 0.9, "trend": 0.5}}),
        "final_score": 0.71,
        "content_flags": [],
        "origin": "heuristic",
        "start_word_idx": s.w0,
        "end_word_idx": e.w1,
    }
    for key, val in fields.items():
        values[key] = Jsonb(val) if key in ("llm_scores", "features", "snap") else val
    names = sorted(values)
    return db.execute(
        f"INSERT INTO clip_candidates ({', '.join(names)}) "
        f"VALUES ({', '.join('%(' + n + ')s' for n in names)}) RETURNING *",
        values,
    ).fetchone()


# ---------------------------------------------------------------- воркеры


def _runner(app_env: Any, resource_class: str, tmp_path: Path) -> Any:
    from autoshorts.domain.settings_schema import RuntimeSettings
    from autoshorts.workers.runner import Runner, RunnerConfig

    settings = RuntimeSettings()
    cfg = RunnerConfig(
        resource_class=resource_class,
        conninfo=app_env.database_url,
        poll_interval_sec=0.2,
        heartbeat_interval_sec=0.5,
        progress_interval_sec=0.2,
        worker_heartbeat_sec=0.5,
        standby_retry_sec=0.2,
        shutdown_grace_sec=10.0,
        kill_grace_sec=1.0,
        admission=False,
        scheduler=False,
        media_root=Path(app_env.media_root),
        heavy_lock_path=tmp_path / "heavy.lock",
        worker_id=f"test-clip-{resource_class}",
    )
    return Runner(cfg, settings_provider=lambda: settings)


@contextlib.contextmanager
def workers(app_env: Any, tmp_path: Path) -> Iterator[None]:
    from autoshorts.tasks.registry import load_all

    load_all(strict=True)
    runners = [_runner(app_env, "light", tmp_path), _runner(app_env, "heavy", tmp_path)]
    threads = [threading.Thread(target=r.run, daemon=True) for r in runners]
    for t in threads:
        t.start()
    try:
        yield
    finally:
        for r in runners:
            r.request_stop()
        for t in threads:
            t.join(60)
        assert not any(t.is_alive() for t in threads), "runner did not stop"


def job_row(db: Any, job_id: int) -> dict[str, Any]:
    return db.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone()


def job_tasks(db: Any, job_id: int) -> list[dict[str, Any]]:
    return db.execute("SELECT * FROM tasks WHERE job_id = %s ORDER BY id", (job_id,)).fetchall()


def wait_status(db: Any, job_id: int, statuses: set[str], timeout: float = 240.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    row = job_row(db, job_id)
    while time.monotonic() < deadline:
        row = job_row(db, job_id)
        if row["status"] in statuses:
            return row
        time.sleep(0.25)
    tasks = [(t["stage"], t["status"], t["last_error"]) for t in job_tasks(db, job_id)]
    raise AssertionError(f"job {job_id} stuck in {row['status']}/{row['stage']}: {tasks}")


def wait_task(db: Any, task_id: int, timeout: float = 120.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = db.execute("SELECT * FROM tasks WHERE id = %s", (task_id,)).fetchone()
        if row["status"] in ("succeeded", "failed", "cancelled"):
            return row
        time.sleep(0.2)
    raise AssertionError(f"task {task_id} not finished: {row['status']} {row['last_error']}")
