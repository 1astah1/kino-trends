"""Конвейер источника в настоящей очереди (M5-A, критерии M5): загрузка → source_prepare (heavy) →
ASR mock в дочернем процессе → source_select (эвристика, без ключа LLM) → clip_candidates, ready.

Плюс: загрузка по ссылке (yt-dlp подменён), отказ без прав до сети, LLM-заглушка с учётом
generation_events и вытеснением прежних кандидатов, окончательный сбой ASR → источник failed,
склейка кусков локального ASR.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from tests.worker.conftest import make_runner, run_in_thread, wait_until

OWN = {"rights_type": "own", "owner_name": "Umka", "proof_note": "свой эфир 12.09"}
CC_BY = {
    "rights_type": "cc_by",
    "owner_name": "Киностудия",
    "proof_note": "лицензия на странице, проверено 27.09.2026",
    "license": "CC BY 3.0",
    "license_url": "https://creativecommons.org/licenses/by/3.0/",
    "attribution_text": "«Выступление» — Киностудия, CC BY 3.0. Изменения: фрагмент, комментарий.",
}
SPEECH = (
    "Почему короткие ролики досматривают до конца? Секрет простой, и сейчас я его покажу. "
    "Первое правило: сильный хук в первые две секунды. Никто не будет ждать, пока вы разгонитесь.\n\n"
    "Второе правило: одна мысль на ролик. Зритель должен понять её без контекста и без предыстории. "
    "Главная ошибка новичков в том, что они пытаются рассказать всё сразу.\n\n"
    "Третье правило вы узнаете прямо сейчас. Концовка должна замыкаться на начало, тогда ролик "
    "смотрят по кругу. Именно поэтому растут досмотры и рекомендации. Запомните эти три правила. "
    "Представьте, что у вас всего тридцать секунд. Что вы скажете первым делом? Вот что важно на самом деле."
)
SELECT_OPTIONS = {"target_clip_sec": 12.0, "min_clip_sec": 6.0, "max_clip_sec": 20.0, "max_candidates": 6}


@pytest.fixture(scope="module")
def talk_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if shutil.which("ffmpeg") is None:
        pytest.skip("нет ffmpeg")
    out = tmp_path_factory.mktemp("talk") / "talk.mp4"
    subprocess.run(
        ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10", "-f", "lavfi", "-i", "sine=frequency=330",
         "-t", "30", "-c:v", "libx264", "-preset", "ultrafast", "-g", "10", "-threads", "1",
         "-c:a", "aac", "-b:a", "48k", "-shortest", str(out)],
        check=True, timeout=120,
    )  # fmt: skip
    return out


@pytest.fixture
def no_llm_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Никаких настоящих ключей: .env проекта не читается, env чистый → отбор эвристикой."""
    from autoshorts.services import secrets, settings

    monkeypatch.setattr(secrets, "ENV_FILE", None)
    for name in secrets.KNOWN_SECRETS:
        monkeypatch.delenv(name, raising=False)
    for name in ("LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    secrets.invalidate_cache()
    settings.invalidate_cache()
    yield
    secrets.invalidate_cache()
    settings.invalidate_cache()


@pytest.fixture
def operator(conn: Any) -> Any:
    from autoshorts.domain.enums import ActorKind, Role
    from autoshorts.services.auth import Principal

    row = conn.execute(
        """INSERT INTO users (login, display_name, password_hash, role)
           VALUES ('op1', 'Оператор', 'x', 'operator') RETURNING id"""
    ).fetchone()
    return Principal(kind=ActorKind.USER, user_id=int(row["id"]), login="op1", display_name="Оператор",
                     role=Role.OPERATOR)  # fmt: skip


def _actor(principal: Any) -> Any:
    return principal.actor(request_id=None, ip_hash=None)


def _complete_upload(conn: Any, media_root: Path, principal: Any, video: Path) -> str:
    uid = "ab" * 16
    dst = media_root / "uploads" / f"{uid}.mp4"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(video, dst)
    n = dst.stat().st_size
    conn.execute(
        """INSERT INTO uploads (id, user_id, purpose, filename, filetype, length_bytes, received_bytes, path,
                                status, expires_at, completed_at)
           VALUES (%s, %s, 'source', 'эфир.mp4', 'video/mp4', %s, %s, %s, 'complete',
                   now() + interval '1 day', now())""",
        (uid, principal.user_id, n, n, f"uploads/{uid}.mp4"),
    )
    return uid


def _runners(queue_env: Any, tmp_path: Path, **kw: Any) -> tuple[Any, Any]:
    from autoshorts.domain.settings_schema import RuntimeSettings

    settings = RuntimeSettings.from_overrides({"heavy_min_mem_available_mb": 0})
    heavy = make_runner(
        queue_env,
        "heavy",
        worker_id="src-heavy",
        heavy_lock_path=tmp_path / "heavy.lock",
        settings=settings,
        **kw,
    )
    light = make_runner(queue_env, "light", worker_id="src-light", concurrency=2, settings=settings, **kw)
    return heavy, light


def _status(conn: Any, sid: int) -> str:
    return conn.execute("SELECT status FROM sources WHERE id = %s", (sid,)).fetchone()["status"]


def _events(listener: Any) -> list[dict[str, Any]]:
    out = []
    for n in listener.notifies(timeout=0.5, stop_after=500):
        out.append(json.loads(n.payload))
    return out


# ---------------------------------------------------------------- сквозной прогон


def test_upload_to_candidates_end_to_end(
    queue_env: Any, conn: Any, operator: Any, talk_video: Path, tmp_path: Path, no_llm_key: None
) -> None:
    import psycopg

    from autoshorts.api.schemas.sources import SourceFromUpload
    from autoshorts.services import sources as svc

    media_root = Path(queue_env.media_root)
    conn.execute(
        "UPDATE provider_configs SET params = %s WHERE key = 'mock_asr'", (json.dumps({"text": SPEECH}),)
    )
    uid = _complete_upload(conn, media_root, operator, talk_video)
    body = SourceFromUpload.model_validate(
        {
            "upload_id": uid,
            "title": "Три правила коротких роликов",
            "rights": OWN,
            "options": {"asr_provider": "mock_asr", **SELECT_OPTIONS},
        }
    )
    listener = psycopg.connect(queue_env.database_url, autocommit=True)
    listener.execute("LISTEN as_events")
    try:
        src = svc.create_from_upload(body, operator, _actor(operator))
        sid = src["id"]
        heavy, light = _runners(queue_env, tmp_path)
        with run_in_thread(heavy), run_in_thread(light):
            wait_until(lambda: _status(conn, sid) in ("ready", "failed"), timeout=240, interval=0.5)
        events = _events(listener)
    finally:
        listener.close()

    row = conn.execute("SELECT * FROM sources WHERE id = %s", (sid,)).fetchone()
    tasks = conn.execute(
        "SELECT kind, status, last_error, result FROM tasks WHERE source_id = %s ORDER BY id", (sid,)
    ).fetchall()
    assert row["status"] == "ready", (row["error"], tasks)
    assert [t["kind"] for t in tasks] == ["source_prepare", "source_transcribe_chunk", "source_select"]
    assert all(t["status"] == "succeeded" for t in tasks)

    # подготовка: прокси, звук, волна, раскадровка, манифест движка
    d = media_root / "sources" / str(sid)
    assert (d / "proxy_540p.mp4").is_file() and (d / "audio.m4a").is_file() and (d / "peaks.json").is_file()
    assert (d / "source.json").is_file() and (d / "transcript.json.gz").is_file()
    assert not (d / "audio16k.wav").exists()  # §8.2: удаляется сразу после ASR
    assert not (d / "asr").exists()
    assert row["proxy_path"] == f"sources/{sid}/proxy_540p.mp4" and row["sha256"]
    assert row["storyboard"]["sheets"] and row["watermark_detail"]["severity"] in ("ok", "hint", "warning")
    manifest = json.loads((d / "source.json").read_text(encoding="utf-8"))
    assert manifest["rights"]["rights_type"] == "own" and manifest["transcript"]["provider"] == "mock"

    # транскрипт (mock в дочернем процессе)
    tr = conn.execute("SELECT * FROM transcripts WHERE source_id = %s AND is_current", (sid,)).fetchone()
    assert tr["provider_key"] == "mock" and tr["words_count"] > 50 and tr["sentences_count"] >= 10

    # отбор: эвристика без ключа LLM — подписано (обработчик source_select — зарегистрированный)
    sel = row["selection"]
    assert sel["mode"] == "heuristic" and sel["llm_configured"] is False and sel["fallback"] is False
    assert "без LLM" in sel["label"]
    cands = conn.execute(
        "SELECT * FROM clip_candidates WHERE source_id = %s AND superseded_at IS NULL ORDER BY id", (sid,)
    ).fetchall()
    assert cands, "эвристика не нашла кандидатов"
    assert sel["n_candidates"] == len(cands)
    for c in cands:
        assert c["origin"] == "heuristic" and c["created_by_kind"] == "auto" and c["status"] == "proposed"
        assert 6.0 - 0.5 <= float(c["end_sec"]) - float(c["start_sec"]) <= 20.0 + 1.0
        assert c["start_word_idx"] is not None and c["end_word_idx"] >= c["start_word_idx"]
        assert c["snap"]["w0"] == c["start_word_idx"] and "lead_sec" in c["snap"]
        assert c["reason"].startswith("[эвристика без LLM]")

    # карточка API: все шаги выполнены, 100 %
    card = svc.get_source(sid)
    steps = {s["step"]: s["status"] for s in card["pipeline"]}
    assert steps == {"download": "skipped", "prepare": "succeeded", "transcribe": "succeeded",
                     "select": "succeeded", "scenes": "skipped"}  # fmt: skip
    assert card["progress"] == 100.0 and card["candidates_count"] == len(cands)
    assert card["selection"]["label"] == sel["label"]

    # SSE: статусы источника и прогресс задач с source_id
    statuses = [e["data"]["status"] for e in events if e.get("event") == "source_status"]
    assert statuses[:1] == ["preparing"] and "transcribing" in statuses and statuses[-1] == "ready"
    assert any(e.get("event") == "task_progress" and e["data"].get("source_id") == sid for e in events)


def _run_direct(conn: Any, task_id: int, handler: Any, log_dir: Path) -> Any:
    """Обработчик в этом потоке с настоящим TaskContext; хуки успеха — одной транзакцией."""
    import logging

    from autoshorts.core.storage import get_layout
    from autoshorts.db.pool import connection
    from autoshorts.domain.settings_schema import RuntimeSettings
    from autoshorts.services.budget import TaskBudgetGate
    from autoshorts.tasks.registry import TaskContext

    task = conn.execute("SELECT * FROM tasks WHERE id = %s", (task_id,)).fetchone()
    ctx = TaskContext(
        task=task,
        worker_id="direct",
        cancel=threading.Event(),
        progress=lambda _f: None,
        log=logging.getLogger("test.source_tasks"),
        settings=RuntimeSettings(),
        media=get_layout(),
        log_path=log_dir / f"{task_id}.log",
        budget=TaskBudgetGate(task_id=task_id, source_id=task["source_id"]),
    )
    result = handler(ctx)
    with connection() as c:
        for hook in ctx.commit_hooks:
            hook(c, task)
        c.execute("UPDATE tasks SET status = 'succeeded', finished_at = now() WHERE id = %s", (task_id,))
    return result


def test_m5a_select_handler_labels_events_supersede(
    queue_env: Any, conn: Any, tmp_path: Path, talk_video: Path, no_llm_key: None
) -> None:
    """`tasks.sources.run_select` (альтернативный обработчик M5-A): без ключа — эвристика с подписью
    «ключ LLM не задан»; LLM-заглушка — режим mock, каждый вызов в generation_events; повторный
    отбор вытесняет прежних автоматических кандидатов; готовый источник получает срок прокси."""
    import autoshorts.tasks.sources as tasks_sources
    from autoshorts.db.pool import connection
    from autoshorts.services import sources as svc
    from autoshorts.services.audit import SYSTEM
    from tests.api.test_sources import make_prepared_dir

    make_prepared_dir(Path(queue_env.media_root), "llm_src", talk_video)
    sid = svc.register_prepared_dir("llm_src", select=False, actor=SYSTEM)["id"]
    params = {"target_clip_sec": 10.0, "min_clip_sec": 5.0, "max_clip_sec": 16.0}

    with connection() as c:
        task_id = int(svc.enqueue_select(c, sid, params=params, actor=SYSTEM).task["id"])
    out = _run_direct(conn, task_id, tasks_sources.run_select, tmp_path)
    row = conn.execute(
        "SELECT status, selection, proxy_retain_until FROM sources WHERE id = %s", (sid,)
    ).fetchone()
    assert row["status"] == "ready" and row["proxy_retain_until"] is not None
    assert out["mode"] == "heuristic" and row["selection"]["label"] == "Эвристика без LLM — ключ LLM не задан"
    assert row["selection"]["params"]["target_clip_sec"] == 10.0
    first = conn.execute(
        "SELECT count(*) AS n FROM clip_candidates WHERE source_id = %s AND superseded_at IS NULL", (sid,)
    ).fetchone()["n"]
    assert first > 0

    conn.execute("UPDATE provider_configs SET is_default = false WHERE kind = 'llm'")
    conn.execute("UPDATE provider_configs SET is_default = true, enabled = true WHERE key = 'mock_llm'")
    with connection() as c:
        task_id = int(svc.enqueue_select(c, sid, params=params).task["id"])
    out = _run_direct(conn, task_id, tasks_sources.run_select, tmp_path)
    row = conn.execute("SELECT selection FROM sources WHERE id = %s", (sid,)).fetchone()
    assert out["mode"] == "mock" and row["selection"]["mode"] == "mock"
    assert "mock" in row["selection"]["label"] and row["selection"]["llm_configured"] is True
    current = conn.execute(
        "SELECT origin FROM clip_candidates WHERE source_id = %s AND superseded_at IS NULL", (sid,)
    ).fetchall()
    old = conn.execute(
        "SELECT count(*) AS n FROM clip_candidates WHERE source_id = %s AND superseded_at IS NOT NULL", (sid,)
    ).fetchone()["n"]
    assert current and all(c["origin"] == "mock" for c in current)
    assert old == first  # эвристические кандидаты первого прогона вытеснены
    ev = conn.execute(
        "SELECT step_name, provider, status, task_id FROM generation_events WHERE source_id = %s", (sid,)
    ).fetchall()
    assert len(ev) == out["events"] > 0
    assert all(e["step_name"] == "highlight_select" and e["provider"] == "mock" for e in ev)
    assert all(e["task_id"] == task_id for e in ev)


def test_asr_permanent_failure_marks_source_failed(
    queue_env: Any, conn: Any, tmp_path: Path, talk_video: Path, no_llm_key: None
) -> None:
    """GigaAM без моделей в MODELS_DIR: дочерний процесс → AsrUnavailable → failed без повторов."""
    from autoshorts.db.pool import connection
    from autoshorts.services import sources as svc
    from autoshorts.services.audit import SYSTEM
    from tests.api.test_sources import make_prepared_dir

    d = make_prepared_dir(Path(queue_env.media_root), "asr_src", talk_video)
    src = svc.register_prepared_dir("asr_src", select=False, actor=SYSTEM)
    sid = src["id"]
    (d / "transcript.json.gz").unlink()
    with connection() as c:
        svc.enqueue_step(c, sid, "transcribe", chain=["select"], asr=svc.resolve_asr(c, "gigaam_v3_onnx"))
    heavy, _ = _runners(queue_env, tmp_path)
    with run_in_thread(heavy):
        wait_until(lambda: _status(conn, sid) in ("failed", "ready"), timeout=120, interval=0.3)
    row = conn.execute("SELECT status, error FROM sources WHERE id = %s", (sid,)).fetchone()
    assert row["status"] == "failed"
    assert row["error"].startswith("Распознавание речи:") and "models_fetch" in row["error"]
    task = conn.execute(
        "SELECT kind, status, attempts, error_class FROM tasks WHERE source_id = %s ORDER BY id DESC LIMIT 1",
        (sid,),
    ).fetchone()
    assert task["kind"] == "source_transcribe_chunk" and task["status"] == "failed" and task["attempts"] == 1
    assert task["error_class"] == "asr_unavailable"


# ---------------------------------------------------------------- загрузка по ссылке


class FakeYDL:
    """Минимум yt_dlp.YoutubeDL: info страницы, «загрузка» — копия локального файла."""

    calls: list[tuple[str, bool, dict[str, Any]]] = []
    video: Path

    def __init__(self, opts: dict[str, Any]) -> None:
        self.opts = opts

    def __enter__(self) -> FakeYDL:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
        FakeYDL.calls.append((url, download, self.opts))
        info = {
            "id": "SP5r7PKG7LA",
            "extractor_key": "Youtube",
            "webpage_url": url,
            "title": "Выступление на CGevent",
            "uploader": "Киностудия",
            "channel_url": "https://www.youtube.com/@28panfilovcev",
            "license": "Creative Commons Attribution license (reuse allowed)",
            "duration": 30.0,
            "upload_date": "20170421",
            "ext": "m4a" if "bestaudio" in str(self.opts.get("format")) else "mp4",
        }
        if download:
            for hook in self.opts.get("progress_hooks") or []:
                hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
            path = Path(self.prepare_filename(info))
            shutil.copyfile(FakeYDL.video, path)
        return info

    def prepare_filename(self, info: dict[str, Any]) -> str:
        return self.opts["outtmpl"].replace("%(ext)s", info["ext"])


def test_url_download_audio_first_then_prepare_queued(
    queue_env: Any,
    conn: Any,
    operator: Any,
    tmp_path: Path,
    talk_video: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_llm_key: None,
) -> None:
    import autoshorts.tasks.sources as tasks_sources
    from autoshorts.api.schemas.sources import SourceFromUrl
    from autoshorts.services import sources as svc

    FakeYDL.calls = []
    FakeYDL.video = talk_video
    monkeypatch.setattr(tasks_sources, "YDL_FACTORY", FakeYDL)
    body = SourceFromUrl.model_validate(
        {"url": "https://www.youtube.com/watch?v=SP5r7PKG7LA",
         "rights": {**CC_BY, "owner_channel_url": "https://www.youtube.com/@other"}}
    )  # fmt: skip
    sid = svc.create_from_url(body, operator, _actor(operator))["id"]
    _, light = _runners(queue_env, tmp_path)
    with run_in_thread(light):
        wait_until(
            lambda: conn.execute(
                "SELECT 1 FROM tasks WHERE source_id = %s AND kind = 'source_prepare'", (sid,)
            ).fetchone(),
            timeout=60,
            interval=0.2,
        )
    row = conn.execute("SELECT * FROM sources WHERE id = %s", (sid,)).fetchone()
    assert row["status"] == "preparing" and row["title"] == "Выступление на CGevent"
    assert (
        row["platform_meta"]["extractor_key"] == "Youtube"
        and row["platform_meta"]["title_from_platform"] is False
    )
    assert row["original_path"] == f"sources/{sid}/original_video.mp4"
    d = Path(queue_env.media_root) / "sources" / str(sid)
    assert (d / "original_audio.m4a").is_file() and (d / "original_video.mp4").is_file()
    formats = [str(c[2].get("format")) for c in FakeYDL.calls if c[1]]
    assert formats[0].startswith("bestaudio[language^=ru]") and "height<=540" in formats[1]
    assert "/bestaudio[ext=m4a]/" in formats[0]  # нет русской дорожки — как раньше
    assert all(
        c[2]["ratelimit"] == 5_000_000 and c[2]["concurrent_fragment_downloads"] == 1 for c in FakeYDL.calls
    )
    warnings = conn.execute("SELECT warnings FROM source_rights WHERE source_id = %s", (sid,)).fetchone()[
        "warnings"
    ]
    assert (
        "owner_channel_differs_from_uploader" in warnings and "cc_by_attribution_in_description" in warnings
    )
    prep = conn.execute(
        "SELECT payload, resource_class FROM tasks WHERE source_id = %s AND kind = 'source_prepare'", (sid,)
    ).fetchone()
    assert prep["resource_class"] == "heavy"
    assert (
        prep["payload"]["video"] == "original_video.mp4" and prep["payload"]["audio"] == "original_audio.m4a"
    )
    assert prep["payload"]["chain"] == ["transcribe", "select"]


def test_download_refuses_expired_rights_before_network(
    queue_env: Any,
    conn: Any,
    operator: Any,
    tmp_path: Path,
    talk_video: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_llm_key: None,
) -> None:
    import autoshorts.tasks.sources as tasks_sources
    from autoshorts.api.schemas.sources import SourceFromUrl
    from autoshorts.services import sources as svc

    FakeYDL.calls = []
    FakeYDL.video = talk_video
    monkeypatch.setattr(tasks_sources, "YDL_FACTORY", FakeYDL)
    body = SourceFromUrl.model_validate({"url": "https://example.com/v/1", "rights": CC_BY})
    sid = svc.create_from_url(body, operator, _actor(operator))["id"]
    conn.execute("UPDATE source_rights SET valid_until = '2020-01-01' WHERE source_id = %s", (sid,))
    _, light = _runners(queue_env, tmp_path)
    with run_in_thread(light):
        wait_until(lambda: _status(conn, sid) == "failed", timeout=60, interval=0.2)
    assert FakeYDL.calls == []  # до сети не дошли
    row = conn.execute("SELECT error FROM sources WHERE id = %s", (sid,)).fetchone()
    assert "rights_expired" in row["error"]


def test_second_download_waits_for_first(
    queue_env: Any,
    conn: Any,
    operator: Any,
    tmp_path: Path,
    talk_video: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_llm_key: None,
) -> None:
    """Одна загрузка по ссылке за раз: вторая откладывается (quota_exhausted) без траты попытки."""
    import autoshorts.tasks.sources as tasks_sources
    from autoshorts.api.schemas.sources import SourceFromUrl
    from autoshorts.services import sources as svc

    FakeYDL.calls = []
    FakeYDL.video = talk_video
    monkeypatch.setattr(tasks_sources, "YDL_FACTORY", FakeYDL)
    conn.execute(
        """INSERT INTO tasks (kind, resource_class, status, locked_by, heartbeat_at)
           VALUES ('source_download', 'light', 'running', 'other-worker', now())"""
    )
    body = SourceFromUrl.model_validate({"url": "https://example.com/v/2", "rights": CC_BY})
    sid = svc.create_from_url(body, operator, _actor(operator))["id"]
    _, light = _runners(queue_env, tmp_path)
    with run_in_thread(light):
        task = wait_until(
            lambda: conn.execute(
                "SELECT * FROM tasks WHERE source_id = %s AND defer_reason IS NOT NULL", (sid,)
            ).fetchone(),
            timeout=30,
            interval=0.1,
        )
    assert task["status"] == "queued" and task["defer_reason"] == "quota_exhausted" and task["attempts"] == 0
    assert FakeYDL.calls == []


def test_download_guard_has_tie_breaker(queue_env: Any, conn: Any) -> None:
    """Регрессия livelock: две загрузки, взятые воркером одновременно, видели друг друга и обе
    откладывались навсегда. Теперь ровно одна (взятая раньше, при равенстве — с меньшим id) идёт."""
    from autoshorts.repos import sources as sources_repo

    ids = [
        conn.execute(
            """INSERT INTO tasks (kind, resource_class, status, locked_by, heartbeat_at, started_at)
               VALUES ('source_download', 'light', 'running', 'w', now(), %s::timestamptz) RETURNING id""",
            (ts,),
        ).fetchone()["id"]
        for ts in ("2026-09-27 10:00:00+00", "2026-09-27 10:00:00+00", "2026-09-27 09:59:00+00")
    ]
    first, second, earliest = ids
    assert sources_repo.running_ahead(conn, "source_download", earliest) == 0
    assert sources_repo.running_ahead(conn, "source_download", first) == 1
    assert sources_repo.running_ahead(conn, "source_download", second) == 2
    conn.execute("UPDATE tasks SET status = 'queued' WHERE id = %s", (earliest,))
    assert sources_repo.running_ahead(conn, "source_download", first) == 0
    assert sources_repo.running_ahead(conn, "source_download", second) == 1


# ---------------------------------------------------------------- склейка кусков локального ASR


def test_assemble_chunks_dedupes_overlap(queue_env: Any, tmp_path: Path, talk_video: Path, conn: Any) -> None:
    import autoshorts.tasks.sources as tasks_sources
    from autoshorts.services import sources as svc
    from autoshorts.services.audit import SYSTEM
    from tests.api.test_sources import make_prepared_dir

    d = make_prepared_dir(Path(queue_env.media_root), "chunk_src", talk_video)
    src = svc.register_prepared_dir("chunk_src", select=False, actor=SYSTEM)
    row = conn.execute("SELECT * FROM sources WHERE id = %s", (src["id"],)).fetchone()
    work = d / "asr" / "r1"
    work.mkdir(parents=True)
    (d / "audio16k.wav").write_bytes(b"RIFF")
    chunk0 = {"start": 0.0, "stop": 10.0, "next_start": 11.0, "audio_sec": 10.0, "wall_sec": 1.0,
              "words": [["Раз", 0.5, 0.9, 0.9], ["два.", 1.0, 1.4, 0.9], ["Три", 10.2, 10.9, 0.9]],
              "vad": [[0.4, 1.5], [10.1, 11.0]]}  # fmt: skip
    chunk1 = {"start": 11.0, "stop": 20.0, "next_start": 20.0, "audio_sec": 9.0, "wall_sec": 1.0,
              "words": [["Три", 10.2, 10.9, 0.9], ["четыре.", 12.0, 12.6, 0.8]],
              "vad": [[11.9, 12.7]]}  # fmt: skip
    (work / "chunk_000.json").write_text(json.dumps(chunk0), encoding="utf-8")
    (work / "chunk_001.json").write_text(json.dumps(chunk1), encoding="utf-8")
    meta = tasks_sources._assemble_chunks(row, work, 20.0)
    assert meta["provider"] == "gigaam_v3_onnx" and meta["words"] == 4 and meta["sentences"] == 2
    assert not (d / "audio16k.wav").exists()
    from autoshorts.clipping.transcript import Transcript

    tr = Transcript.load(d / "transcript.json.gz")
    assert [w.w for w in tr.words] == ["Раз", "два.", "Три", "четыре."]
    assert tr.rtfx == 10.0


def test_progress_poller_reads_child_file(tmp_path: Path) -> None:
    import autoshorts.tasks.sources as tasks_sources

    seen: list[float] = []
    path = tmp_path / "progress.json"
    done = threading.Event()

    def sink(f: float) -> None:
        seen.append(f)
        if f >= 0.5:
            done.set()

    old = tasks_sources.PROGRESS_POLL_SEC
    tasks_sources.PROGRESS_POLL_SEC = 0.05
    try:
        with tasks_sources._ProgressPoller(path, sink):
            path.write_text(json.dumps({"f": 0.5}), encoding="utf-8")
            assert done.wait(3.0)
    finally:
        tasks_sources.PROGRESS_POLL_SEC = old
    assert seen[-1] == 0.5


# ---------------------------------------------------------------- настоящий GigaAM кусками


DEMO_ORIGINAL = Path(__file__).resolve().parents[2] / "var/media/sources/cc_shalyopa_cgevent/original.webm"


def _gigaam_ready() -> str | None:
    from autoshorts.core.resources import read_mem_available_mb
    from autoshorts.providers.asr.models import DEFAULT_MODELS_DIR, missing_files

    if missing_files(["asr", "vad"], DEFAULT_MODELS_DIR):
        return "нет моделей GigaAM в var/models (autoshorts-admin models-fetch)"
    if not DEMO_ORIGINAL.is_file():
        return "нет демо-источника с речью"
    mem = read_mem_available_mb()
    if mem is not None and mem < 1500:
        return f"MemAvailable {mem} МБ < 1500 МБ: общий бокс, ASR не запускаем"
    return None


def test_gigaam_chunked_asr_on_real_speech(
    queue_env: Any,
    conn: Any,
    operator: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_llm_key: None,
) -> None:
    """Локальный ASR кусками (source_transcribe_chunk × N, каждый — дочерний процесс) на 70 с речи
    демо-доклада: куски склеиваются без дублей, audio16k.wav удаляется, отбор находит кандидатов."""
    reason = _gigaam_ready()
    if reason:
        pytest.skip(reason)
    from autoshorts.api.schemas.sources import SourceFromUpload
    from autoshorts.providers.asr.models import DEFAULT_MODELS_DIR
    from autoshorts.services import sources as svc

    monkeypatch.setenv("MODELS_DIR", str(DEFAULT_MODELS_DIR))
    monkeypatch.setattr(svc, "CHUNK_SEC", 25.0)  # 70 с → несколько кусков (кусок кончается по речи)
    clip = tmp_path / "speech.mp4"
    subprocess.run(
        ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "95", "-t", "70",
         "-i", str(DEMO_ORIGINAL), "-vf", "scale=160:-2", "-r", "10", "-c:v", "libx264",
         "-preset", "ultrafast", "-threads", "2", "-c:a", "aac", "-b:a", "64k", str(clip)],
        check=True, timeout=180,
    )  # fmt: skip
    uid = _complete_upload(conn, Path(queue_env.media_root), operator, clip)
    body = SourceFromUpload.model_validate(
        {
            "upload_id": uid,
            "title": "Фрагмент доклада",
            "rights": OWN,
            "options": {"asr_provider": "gigaam_v3_onnx"},
        }
    )
    sid = svc.create_from_upload(body, operator, _actor(operator))["id"]
    heavy, light = _runners(queue_env, tmp_path)
    with run_in_thread(heavy), run_in_thread(light):
        wait_until(lambda: _status(conn, sid) in ("ready", "failed"), timeout=600, interval=1.0)
    tasks = conn.execute(
        "SELECT kind, status, payload, result, last_error FROM tasks WHERE source_id = %s ORDER BY id", (sid,)
    ).fetchall()
    row = conn.execute("SELECT status, error FROM sources WHERE id = %s", (sid,)).fetchone()
    assert row["status"] == "ready", (row, tasks)
    chunks = [t for t in tasks if t["kind"] == "source_transcribe_chunk"]
    assert len(chunks) >= 2 and [t["payload"]["idx"] for t in chunks] == list(range(len(chunks)))
    assert chunks[-1]["result"]["final"] is True and all(not t["result"]["final"] for t in chunks[:-1])
    assert all(
        t["payload"]["start_sec"] <= t2["payload"]["start_sec"]
        for t, t2 in zip(chunks, chunks[1:], strict=False)
    )
    tr = conn.execute("SELECT * FROM transcripts WHERE source_id = %s AND is_current", (sid,)).fetchone()
    assert tr["provider_key"] == "gigaam_v3_onnx" and tr["words_count"] > 100
    d = Path(queue_env.media_root) / "sources" / str(sid)
    assert not (d / "audio16k.wav").exists() and not (d / "asr").exists()
    from autoshorts.clipping.transcript import Transcript

    t = Transcript.load(d / "transcript.json.gz")
    starts = [w.s for w in t.words]
    assert starts == sorted(starts) and len(set(zip(starts, [w.w for w in t.words], strict=True))) == len(
        starts
    )
    card = svc.get_source(sid)
    step = next(s for s in card["pipeline"] if s["step"] == "transcribe")
    assert step["status"] == "succeeded" and step["chunks_done"] == step["chunks_total"] == len(chunks)


class FakeDirectFile(FakeYDL):
    """Прямая ссылка на mp4 (generic): один формат, «звук» = весь файл с видео; уважает max_filesize."""

    def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
        FakeYDL.calls.append((url, download, self.opts))
        info = {"id": "film", "extractor_key": "Generic", "webpage_url": url, "title": "film",
                "duration": 30.0, "ext": "mp4"}  # fmt: skip
        size = FakeYDL.video.stat().st_size
        if download and size <= int(self.opts.get("max_filesize") or size):
            shutil.copyfile(FakeYDL.video, Path(self.prepare_filename(info)))
        return info


def test_direct_file_is_downloaded_once_and_linked_as_video(
    queue_env: Any, conn: Any, operator: Any, tmp_path: Path, talk_video: Path,
    monkeypatch: pytest.MonkeyPatch, no_llm_key: None,
) -> None:  # fmt: skip
    """Файл хранилища Кино-трендов: качаем один раз, original_video — жёсткая ссылка на тот же файл."""
    import autoshorts.tasks.sources as tasks_sources
    from autoshorts.api.schemas.sources import SourceFromUrl
    from autoshorts.services import sources as svc

    FakeYDL.calls = []
    FakeYDL.video = talk_video
    monkeypatch.setattr(tasks_sources, "YDL_FACTORY", FakeDirectFile)
    body = SourceFromUrl.model_validate({"url": "http://storage.example/files/film.mp4", "rights": CC_BY})
    sid = svc.create_from_url(body, operator, _actor(operator))["id"]
    _, light = _runners(queue_env, tmp_path)
    with run_in_thread(light):
        wait_until(
            lambda: conn.execute(
                "SELECT 1 FROM tasks WHERE source_id = %s AND kind = 'source_prepare'", (sid,)
            ).fetchone(),
            timeout=60,
            interval=0.2,
        )
    d = Path(queue_env.media_root) / "sources" / str(sid)
    audio, video = d / "original_audio.mp4", d / "original_video.mp4"
    assert audio.is_file() and video.is_file() and audio.stat().st_ino == video.stat().st_ino
    assert len([c for c in FakeYDL.calls if c[1]]) == 1  # один download=True
    row = conn.execute("SELECT original_path FROM sources WHERE id = %s", (sid,)).fetchone()
    assert row["original_path"] == f"sources/{sid}/original_video.mp4"


def test_download_that_does_not_fit_the_disk_is_deferred_not_failed(
    queue_env: Any, conn: Any, operator: Any, tmp_path: Path, talk_video: Path,
    monkeypatch: pytest.MonkeyPatch, no_llm_key: None,
) -> None:  # fmt: skip
    """Мало места над порогом: yt-dlp молча пропускает крупный файл — загрузка откладывается (disk_low),
    а не роняет источник ошибкой «нет файла»."""
    import autoshorts.tasks.sources as tasks_sources
    from autoshorts.api.schemas.sources import SourceFromUrl
    from autoshorts.services import sources as svc

    FakeYDL.calls = []
    FakeYDL.video = talk_video
    monkeypatch.setattr(tasks_sources, "YDL_FACTORY", FakeDirectFile)
    monkeypatch.setattr(tasks_sources, "_download_limit_bytes", lambda ctx: 1000)
    body = SourceFromUrl.model_validate({"url": "http://storage.example/files/big.mp4", "rights": CC_BY})
    sid = svc.create_from_url(body, operator, _actor(operator))["id"]
    _, light = _runners(queue_env, tmp_path)
    with run_in_thread(light):
        task = wait_until(
            lambda: conn.execute(
                "SELECT * FROM tasks WHERE source_id = %s AND defer_reason IS NOT NULL", (sid,)
            ).fetchone(),
            timeout=30,
            interval=0.1,
        )
    assert task["status"] == "queued" and task["defer_reason"] == "disk_low"
    status = conn.execute("SELECT status FROM sources WHERE id = %s", (sid,)).fetchone()["status"]
    assert status != "failed"
