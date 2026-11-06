"""Задачи конвейера источника нарезки (docs/ARCHITECTURE.md §6.1, §6.3.1–§6.3.3, §8.1). Владелец — M5-A.

| kind                      | класс | попыток | что делает                                                  |
|---------------------------|-------|---------|-------------------------------------------------------------|
| `source_download`         | light | 3       | yt-dlp audio-first (звук + видео ≤540p), права — до сети    |
| `source_prepare`          | heavy | 2       | прокси 540p, audio.m4a/16k, peaks, раскадровка, логотипы    |
| `source_transcribe_chunk` | heavy | 2       | локальный ASR куском ~10 мин в дочернем процессе            |
| `source_transcribe_cloud` | light | 3       | облачный ASR (SpeechKit, OpenAI-совм.) в дочернем процессе  |
| `source_select`           | light | 3       | LLM-отбор или эвристика → clip_candidates (см. ниже)        |
| `source_scenes`           | heavy | 2       | склейки PySceneDetect на прокси → sources.scenes            |

Вид `source_select` регистрирует M5-B (`tasks/clipping.py` → `services.clipping.run_source_select`);
здесь — альтернативный обработчик `run_select` (подписи режима по SourceSelectionInfo, учёт каждого
вызова LLM в generation_events и бюджете, огибающая громкости из peaks), без регистрации.

Каждый шаг в конце ставит следующий из `payload.chain` в той же транзакции, что и свой успех
(`ctx.on_commit`). Окончательный сбой шага переводит источник в `failed` с понятной ошибкой.
Движок не переписан: `clipping.source_ingest` (подготовка, yt-dlp), `providers.asr`,
`clipping.selection.select_clips`, `clipping.reframe.shots.detect_shots`.

ASR в дочернем процессе (`python -m autoshorts.tasks.sources asr-child <args.json>`): модель
(~0,6 ГБ) освобождается сразу после распознавания; процесс запускается через
`pipeline.ffmpeg_exec.run_media` (nice 10, OMP=2, своя группа процессов — отмена и таймаут убивают
её целиком). Секреты облачных провайдеров передаются только через env дочернего процесса.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import random
import shutil
import sys
import threading
import time
import traceback
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from autoshorts.core.errors import (
    Cancelled,
    PermanentError,
    ResourceDeferred,
    RetryableError,
)
from autoshorts.tasks.registry import TaskContext, register_task

log = logging.getLogger(__name__)

YDL_FACTORY: Callable[[dict], Any] | None = None
"""Фабрика yt_dlp.YoutubeDL (тесты подменяют); None — `source_ingest._ydl`."""
DOWNLOAD_CONCURRENCY = 1
DOWNLOAD_DEFER_SEC = 60.0
DOWNLOAD_DEFER_JITTER_SEC = 20.0  # отложенные вместе не просыпаются вместе
ASR_RANGE_TIMEOUT_SEC = 1800.0
ASR_LOCAL_TIMEOUT_SEC = 3600.0
ASR_CLOUD_TIMEOUT_SEC = 6 * 3600.0
CHILD_EXIT_PERMANENT = 3
PROGRESS_POLL_SEC = 1.0
SCENES_WINDOW_SEC = 600.0
CLOUD_USD_PER_MIN = 0.006


# ---------------------------------------------------------------- общее


def _source(conn: Any, source_id: int | None) -> dict[str, Any] | None:
    from autoshorts.repos import sources as sources_repo

    return sources_repo.get(conn, int(source_id)) if source_id is not None else None


def _load(ctx: TaskContext) -> dict[str, Any] | None:
    from autoshorts.db.pool import connection

    sid = ctx.source_id if ctx.source_id is not None else ctx.payload.get("source_id")
    with connection() as conn:
        return _source(conn, sid)


def _enter_step(source_id: int, step: str) -> None:
    """Задача взята: источник в статусе шага (повтор после сбоя возвращает его из failed)."""
    from autoshorts.db.pool import connection
    from autoshorts.services import sources as svc

    status = svc.STEP_STATUS.get(step)
    if status is None:
        return
    with connection() as conn:
        row = conn.execute(
            "SELECT status FROM sources WHERE id = %(id)s AND deleted_at IS NULL", {"id": source_id}
        ).fetchone()
        if row is not None and row["status"] != status:
            svc.set_status(conn, source_id, status)


def _final_attempt(ctx: TaskContext) -> bool:
    return ctx.attempt >= int(ctx.task.get("max_attempts") or 1)


def _guard(ctx: TaskContext, step: str, body: Callable[[TaskContext, dict[str, Any]], dict[str, Any]]) -> Any:
    """Общая обвязка шага: удалённый источник — пропуск; окончательный сбой — источник failed."""
    from autoshorts.services import sources as svc

    row = _load(ctx)
    if row is None:
        return {"skipped": "source_deleted"}
    sid = int(row["id"])
    _enter_step(sid, step)
    try:
        return body(ctx, row)
    except (Cancelled, ResourceDeferred):
        raise
    except PermanentError as exc:
        svc.mark_failed(sid, _step_error(step, exc))
        raise
    except Exception as exc:
        if _final_attempt(ctx):
            svc.mark_failed(sid, _step_error(step, exc))
        raise


STEP_TITLES = {
    "download": "Загрузка по ссылке",
    "prepare": "Подготовка",
    "transcribe": "Распознавание речи",
    "select": "Отбор моментов",
    "scenes": "Поиск склеек",
}


def _step_error(step: str, exc: BaseException) -> str:
    return f"{STEP_TITLES.get(step, step)}: {exc}"[:2000]


def _chain(ctx: TaskContext) -> list[str]:
    return [str(s) for s in ctx.payload.get("chain") or []]


def _mem_guard(ctx: TaskContext) -> None:
    """Тяжёлый шаг на общем боксе: мало памяти — отложить, не блокируя слот ожиданием."""
    from autoshorts.core.resources import read_mem_available_mb

    need = int(ctx.settings.heavy_min_mem_available_mb)
    avail = read_mem_available_mb()
    if need and avail is not None and avail < need:
        raise ResourceDeferred(
            "mem_low", retry_after_sec=30.0, message=f"MemAvailable {avail} МБ < {need} МБ"
        )


def _rel(path: Path) -> str:
    from autoshorts.core.storage import get_layout

    return get_layout().relpath(path)


# ---------------------------------------------------------------- source_download


def _ydl_factory() -> Callable[[dict], Any]:
    if YDL_FACTORY is not None:
        return YDL_FACTORY
    from autoshorts.clipping.source_ingest import _ydl

    return _ydl


def _classify_download_error(exc: Exception) -> Exception:
    text = str(exc)
    low = text.lower()
    permanent = (
        "unsupported url", "private video", "video unavailable", "has been removed", "sign in to confirm",
        "copyright", "not available in your country", "members-only", "age-restricted", "http error 404",
        "max-filesize", "larger than max-filesize",
    )  # fmt: skip
    if any(p in low for p in permanent):
        return PermanentError(f"yt-dlp: {text[:500]}", error_class="download_rejected")
    return RetryableError(f"yt-dlp: {text[:500]}")


def _download_limit_bytes(ctx: TaskContext) -> int:
    from autoshorts.core.resources import admission_policy, disk_free_gb

    policy = admission_policy(ctx.settings)
    free_gb = disk_free_gb(ctx.media.root)
    room = (free_gb - policy.disk_soft_min_gb) * 1024**3
    if room <= 0:
        raise ResourceDeferred("disk_low", retry_after_sec=300.0, message=f"свободно {free_gb:.1f} ГБ")
    return int(min(room, float(ctx.settings.upload_max_gb) * 1024**3))


def _reuse_direct_file(info: Mapping[str, Any], audio: Path, out_dir: Path) -> Path | None:
    """Прямая ссылка на файл (yt-dlp generic): форматов нет, и «звук» — это уже весь файл с видео.
    Второй раз его не качаем: original_video — жёсткая ссылка на тот же файл, место не удваивается."""
    from autoshorts.pipeline.proxy import probe_media

    if str(info.get("extractor_key") or "").lower() != "generic":
        return None
    try:
        if not probe_media(audio).has_video:
            return None
    except Exception:  # noqa: BLE001 - не смогли прочитать — пусть качается как обычно
        return None
    video = out_dir / f"original_video{audio.suffix}"
    video.unlink(missing_ok=True)
    try:
        os.link(audio, video)
    except OSError:
        shutil.copyfile(audio, video)
    return video


def _download_body(ctx: TaskContext, row: dict[str, Any]) -> dict[str, Any]:
    from autoshorts.clipping.source_ingest import (
        RightsError,
        SourceRejected,
        _download,
        fetch_url_info,
        platform_rights_warnings,
        platform_summary,
        validate_rights,
        ytdlp_audio_options,
        ytdlp_video_options,
    )
    from autoshorts.db.pool import connection
    from autoshorts.repos import sources as sources_repo
    from autoshorts.services import sources as svc

    sid = int(row["id"])
    url = row.get("origin_url")
    if row["origin"] != "url" or not url:
        raise PermanentError("источник не по ссылке: качать нечего", error_class="not_url_source")
    with connection() as conn:
        rights_row = sources_repo.get_rights(conn, sid)
        busy = (
            sources_repo.running_ahead(conn, "source_download", int(ctx.task_id))
            if ctx.task_id is not None
            else sources_repo.running_count(conn, "source_download")
        )
    if rights_row is None:
        raise PermanentError("нет записи прав: без прав источник не качается", error_class="rights_missing")
    try:  # права перепроверяются до любой сети (срок действия мог истечь)
        rec, base_warnings = validate_rights(svc.rights_engine_dict(rights_row))
    except RightsError as exc:
        raise PermanentError(
            f"права не приняты: {', '.join(exc.codes)}", error_class="rights_rejected"
        ) from None
    if busy >= DOWNLOAD_CONCURRENCY:
        raise ResourceDeferred(
            "quota_exhausted",
            retry_after_sec=DOWNLOAD_DEFER_SEC + random.uniform(0.0, DOWNLOAD_DEFER_JITTER_SEC),
            message="уже идёт другая загрузка по ссылке",
        )
    max_bytes = _download_limit_bytes(ctx)
    factory = _ydl_factory()
    try:
        info = fetch_url_info(url, ydl_factory=factory)
    except SourceRejected as exc:
        raise PermanentError(f"{exc.code}: {exc}", error_class="source_rejected") from None
    except Exception as exc:  # noqa: BLE001 - yt_dlp.utils.DownloadError и сеть
        raise _classify_download_error(exc) from None
    duration = float(info.get("duration") or 0.0)
    limit_sec = float(ctx.settings.source_max_hours) * 3600.0
    if duration > limit_sec:
        raise PermanentError(
            f"источник длиннее {ctx.settings.source_max_hours:g} ч ({duration / 3600:.2f} ч)",
            error_class="too_long",
        )
    ctx.progress(0.02)
    out_dir = svc.source_dir(row, create=True)
    phase = {"base": 0.02, "span": 0.28}

    def hook(d: Mapping[str, Any]) -> None:
        if ctx.cancel.is_set():
            raise Cancelled("cancel requested")
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            got = d.get("downloaded_bytes") or 0
            if total:
                ctx.progress(phase["base"] + phase["span"] * min(1.0, float(got) / float(total)))

    def fetch(opts: dict[str, Any], stem: str) -> Path:
        opts = {**opts, "progress_hooks": [hook]}
        try:
            return _download(url, opts, out_dir, stem, factory)
        except Cancelled:
            raise
        except SourceRejected as exc:
            # max_filesize урезан свободным местом: yt-dlp молча пропускает крупный файл («File is larger
            # than max-filesize»), и наружу выходит только «нет файла». Это нехватка диска, а не битая ссылка:
            # откладываем загрузку, а не роняем источник
            if exc.code == "download_failed" and max_bytes < cap_bytes:
                raise ResourceDeferred(
                    "disk_low",
                    retry_after_sec=600.0,
                    message=f"файл не поместился: над порогом диска свободно {max_bytes / 1024**3:.1f} ГБ",
                ) from None
            raise PermanentError(f"{exc.code}: {exc}", error_class="download_failed") from None
        except Exception as exc:  # noqa: BLE001
            if ctx.cancel.is_set():
                raise Cancelled("cancel requested") from None
            raise _classify_download_error(exc) from None

    cap_bytes = int(float(ctx.settings.upload_max_gb) * 1024**3)
    audio = fetch(ytdlp_audio_options(out_dir, max_filesize=max_bytes), "original_audio")
    ctx.check_cancelled()
    phase.update(base=0.3, span=0.68)
    video = _reuse_direct_file(info, audio, out_dir)
    if video is None:
        video = fetch(ytdlp_video_options(out_dir, max_filesize=max_bytes), "original_video")
    ctx.progress(1.0)
    plat = platform_summary(info)
    warnings = list(dict.fromkeys([*base_warnings, *platform_rights_warnings(rec, info)]))
    chain = _chain(ctx)
    meta = {**plat, "title_from_platform": False}
    title_from_platform = bool((row.get("platform_meta") or {}).get("title_from_platform"))

    def commit(conn: Any, _task: dict[str, Any]) -> None:
        fields: dict[str, Any] = {"platform_meta": meta, "original_path": _rel(video)}
        if title_from_platform and plat.get("title"):
            fields["title"] = str(plat["title"])[:300]
        sources_repo.update(conn, sid, fields)
        sources_repo.set_rights_warnings(conn, sid, warnings)
        svc.advance(conn, sid, chain, video=video.name, audio=audio.name)

    ctx.on_commit(commit)
    return {
        "audio": audio.name,
        "video": video.name,
        "bytes": audio.stat().st_size + video.stat().st_size,
        "platform": {k: plat.get(k) for k in ("extractor_key", "id", "license", "duration")},
        "warnings": warnings,
    }


@register_task("source_download", "light", max_attempts=3)
def source_download(ctx: TaskContext) -> Any:
    return _guard(ctx, "download", _download_body)


# ---------------------------------------------------------------- source_prepare


def _pick(d: Path, stem: str) -> Path | None:
    cands = sorted(p for p in d.glob(f"{stem}.*") if p.is_file() and not p.name.endswith((".part", ".ytdl")))
    return cands[0] if cands else None


def _prepare_inputs(row: dict[str, Any], d: Path, payload: Mapping[str, Any]) -> tuple[Path, Path | None]:
    from autoshorts.core.storage import get_layout

    layout = get_layout()
    if row["origin"] == "url":
        video = (d / str(payload["video"])) if payload.get("video") else _pick(d, "original_video")
        audio = (d / str(payload["audio"])) if payload.get("audio") else _pick(d, "original_audio")
    else:
        video = layout.resolve(row["original_path"]) if row.get("original_path") else _pick(d, "original")
        audio = None
    if video is None or not video.is_file():
        raise PermanentError("нет исходного файла для подготовки", error_class="original_missing")
    if audio is not None and not audio.is_file():
        audio = None
    return video, audio


def _prepare_body(ctx: TaskContext, row: dict[str, Any]) -> dict[str, Any]:
    from autoshorts.clipping.source_ingest import PreparedSource, prepare_source
    from autoshorts.db.pool import connection
    from autoshorts.pipeline.proxy import ProbeError, probe_media
    from autoshorts.repos import sources as sources_repo
    from autoshorts.services import sources as svc

    _mem_guard(ctx)
    sid = int(row["id"])
    d = svc.source_dir(row, create=True)
    video, audio = _prepare_inputs(row, d, ctx.payload)
    try:
        probe = probe_media(video, timeout_sec=120.0)
    except ProbeError as exc:
        raise PermanentError(f"файл не читается: {exc}", error_class="probe_failed") from None
    if probe.duration > float(ctx.settings.source_max_hours) * 3600.0:
        raise PermanentError(f"источник длиннее {ctx.settings.source_max_hours:g} ч", error_class="too_long")
    if not probe.has_video:
        raise PermanentError("в файле нет видео", error_class="no_video")
    weights = {"proxy": (0.05, 0.70), "storyboard": (0.75, 0.20)}

    def progress(step: str, f: float) -> None:
        base, span = weights.get(step, (None, None))
        if base is not None:
            ctx.progress(base + span * max(0.0, min(1.0, f)))

    ctx.progress(0.02)
    prep = prepare_source(
        video,
        d,
        audio_path=audio,
        sha256=row.get("sha256") if row["origin"] == "upload" and row.get("sha256") else None,
        threads=int(ctx.settings.ffmpeg_threads),
        min_mem_mb=0,
        progress=progress,
        cancel=ctx.cancel,
    )
    ctx.check_cancelled()
    if not prep["probe"].get("has_audio") and not (audio and audio.is_file()):
        raise PermanentError("в источнике нет звука: нарезке нужна речь", error_class="no_audio")
    with connection() as conn:
        rights_row = sources_repo.get_rights(conn, sid)
    existing = svc.load_manifest(row)
    files: dict[str, Any] = {}
    if row["origin"] == "url":
        files["original_video"] = video.name
        if audio is not None:
            files["original_audio"] = audio.name
    else:
        files["original"] = video.name if video.parent.resolve() == d.resolve() else str(video)
    files.update(prep["files"])
    transcript_meta = None
    if (
        existing is not None
        and existing.path("transcript") is not None
        and existing.path("transcript").is_file()
    ):
        files["transcript"] = existing.files["transcript"]
        transcript_meta = existing.transcript
    prepared = PreparedSource(
        source_id=existing.source_id if existing is not None else str(sid),
        dir=d,
        title=row["title"],
        origin=row["origin"],
        rights=svc.rights_engine_dict(rights_row or {}),
        rights_warnings=list((rights_row or {}).get("warnings") or []),
        probe=prep["probe"],
        sha256=prep["sha256"],
        files=files,
        origin_url=row.get("origin_url"),
        platform={k: v for k, v in (row.get("platform_meta") or {}).items() if k != "title_from_platform"},
        peaks=prep["peaks"],
        storyboard=prep["storyboard"],
        watermark=prep["watermark"],
        transcript=transcript_meta,
        timings=prep["timings"],
    )
    prepared.save()
    ctx.progress(1.0)
    columns = svc.prepared_columns(row, prepared)
    chain = _chain(ctx)

    def commit(conn: Any, _task: dict[str, Any]) -> None:
        sources_repo.update(conn, sid, columns)
        svc.advance(conn, sid, chain)

    ctx.on_commit(commit)
    wm = prep["watermark"] or {}
    return {
        "duration_sec": round(float(prep["probe"].get("duration") or 0.0), 2),
        "proxy": prep["files"].get("proxy"),
        "storyboard_sheets": len(prep["storyboard"].get("sheets") or []),
        "watermark": wm.get("severity"),
        "timings": {k: round(v, 2) for k, v in prep["timings"].items()},
    }


@register_task("source_prepare", "heavy", max_attempts=2)
def source_prepare(ctx: TaskContext) -> Any:
    return _guard(ctx, "prepare", _prepare_body)


# ---------------------------------------------------------------- ASR (дочерний процесс)


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)


class _ProgressPoller:
    """Читает долю из файла прогресса дочернего процесса и отдаёт в `ctx.progress`."""

    def __init__(self, path: Path, sink: Callable[[float], None]) -> None:
        self.path = path
        self.sink = sink
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="asr-progress")

    def _run(self) -> None:
        last = -1.0
        while not self.stop.wait(PROGRESS_POLL_SEC):
            try:
                f = float(json.loads(self.path.read_text(encoding="utf-8")).get("f", 0.0))
            except (OSError, ValueError, AttributeError):
                continue
            if f != last:
                last = f
                with contextlib.suppress(Exception):
                    self.sink(max(0.0, min(0.99, f)))

    def __enter__(self) -> _ProgressPoller:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop.set()
        self.thread.join(timeout=2.0)


def run_asr_child(
    ctx: TaskContext,
    args: dict[str, Any],
    work: Path,
    *,
    timeout_sec: float,
    env_extra: Mapping[str, str] | None = None,
    progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Запустить распознавание в дочернем процессе и вернуть его результат (dict)."""
    from autoshorts.pipeline.ffmpeg_exec import run_media

    work.mkdir(parents=True, exist_ok=True)
    tag = f"{int(args.get('idx') or 0):03d}"
    args = {
        **args,
        "result": str(work / f"result_{tag}.json"),
        "progress": str(work / f"progress_{tag}.json"),
    }
    Path(args["result"]).unlink(missing_ok=True)
    args_file = work / f"args_{tag}.json"
    _write_json(args_file, args)
    env = {**os.environ, **dict(env_extra or {})}
    src_root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = os.pathsep.join(p for p in (src_root, env.get("PYTHONPATH", "")) if p)
    cmd = [sys.executable, "-m", "autoshorts.tasks.sources", "asr-child", str(args_file)]
    threads = int(args.get("threads") or 2)
    with _ProgressPoller(Path(args["progress"]), progress or ctx.progress):
        res = run_media(
            cmd,
            timeout_sec=timeout_sec,
            threads=threads,
            nice=10,
            cancel=ctx.cancel,
            log_path=ctx.log_path,
            env=env,
            check=False,
        )
    try:
        out = json.loads(Path(args["result"]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        out = None
    if res.returncode == 0 and out and out.get("ok"):
        return out
    message = (out or {}).get("error") or f"ASR завершился с кодом {res.returncode}: {res.stderr_tail[-800:]}"
    if (out or {}).get("permanent") or res.returncode == CHILD_EXIT_PERMANENT:
        raise PermanentError(
            f"ASR: {message}"[:2000], error_class=(out or {}).get("error_class") or "asr_failed"
        )
    raise RetryableError(f"ASR: {message}"[:2000])


def _transcribe_common(ctx: TaskContext, row: dict[str, Any]) -> dict[str, Any]:
    from autoshorts.db.pool import connection
    from autoshorts.services import sources as svc

    sid = int(row["id"])
    prepared = svc.load_manifest(row)
    if prepared is None:
        raise PermanentError("источник не подготовлен: нет source.json", error_class="not_prepared")
    from autoshorts.core.errors import AppError

    try:
        with connection() as conn:
            choice = svc.resolve_asr(conn, ctx.payload.get("provider"))
    except AppError as exc:  # провайдер выключен или удалён после постановки
        raise PermanentError(exc.message, error_class="asr_provider_unavailable") from None
    rev = str(ctx.payload.get("rev") or "r0")
    work = Path(prepared.dir) / "asr" / rev
    base_args = {
        "source_dir": str(prepared.dir),
        "engine": choice.engine_key,
        "kwargs": choice.engine_kwargs(),
        "language": row.get("language") or "ru",
        "threads": int(ctx.settings.ffmpeg_threads),
        "idx": int(ctx.payload.get("idx") or 0),
    }
    if choice.chunked:
        return _transcribe_chunked(ctx, row, sid, choice, work, base_args)
    return _transcribe_whole(ctx, row, sid, choice, work, base_args)


def _finish_transcript(ctx: TaskContext, sid: int, row: dict[str, Any], result: dict[str, Any]) -> None:
    """Транскрипт готов: строка transcripts (текущая) и следующий шаг — в транзакции успеха."""
    from autoshorts.services import sources as svc

    prepared = svc.load_manifest(row)
    if prepared is None or prepared.path("transcript") is None:
        raise RetryableError("манифест без транскрипта после ASR")
    rel = _rel(prepared.path("transcript"))
    chain = _chain(ctx)
    shutil.rmtree(Path(prepared.dir) / "asr", ignore_errors=True)

    def commit(conn: Any, _task: dict[str, Any]) -> None:
        svc.register_transcript(conn, sid, prepared, rel)
        svc.advance(conn, sid, chain)

    ctx.on_commit(commit)


def _transcribe_whole(
    ctx: TaskContext, row: dict[str, Any], sid: int, choice: Any, work: Path, base_args: dict[str, Any]
) -> dict[str, Any]:
    reservation = None
    duration_min = float(row.get("duration_sec") or 0.0) / 60.0
    if not choice.local and ctx.budget is not None:
        estimate = max(0.01, round(duration_min * CLOUD_USD_PER_MIN * 1.2, 4))
        reservation = ctx.budget.reserve(estimate, provider=choice.key)
    timeout = ASR_LOCAL_TIMEOUT_SEC if choice.local else ASR_CLOUD_TIMEOUT_SEC
    started = time.monotonic()
    try:
        out = run_asr_child(
            ctx, {**base_args, "mode": "whole"}, work, timeout_sec=timeout, env_extra=choice.child_env()
        )
    except BaseException:
        if reservation is not None:
            with contextlib.suppress(Exception):
                ctx.budget.release(reservation)
        raise
    meta = out.get("transcript") or {}
    if reservation is not None:
        ctx.budget.settle(
            reservation,
            float(meta.get("cost_usd") or 0.0),
            step_name="asr_transcribe",
            model=meta.get("model"),
            units=round(duration_min, 3),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    ctx.progress(1.0)
    _finish_transcript(ctx, sid, row, out)
    return {
        "provider": choice.key,
        "words": meta.get("words"),
        "sentences": meta.get("sentences"),
        "rtfx": meta.get("rtfx"),
        "cost_usd": meta.get("cost_usd"),
        "final": True,
    }


def _transcribe_chunked(
    ctx: TaskContext, row: dict[str, Any], sid: int, choice: Any, work: Path, base_args: dict[str, Any]
) -> dict[str, Any]:
    from autoshorts.services import sources as svc

    idx = int(ctx.payload.get("idx") or 0)
    start = float(ctx.payload.get("start_sec") or 0.0)
    out = run_asr_child(
        ctx,
        {**base_args, "mode": "range", "start_sec": start, "end_sec": start + svc.CHUNK_SEC},
        work,
        timeout_sec=ASR_RANGE_TIMEOUT_SEC,
        env_extra=choice.child_env(),
    )
    total = float(out.get("duration") or 0.0)
    next_start = float(out.get("next_start") or 0.0)
    _write_json(work / f"chunk_{idx:03d}.json", out)
    ctx.progress(1.0)
    if next_start < total - 0.5:
        chain = _chain(ctx)
        rev = str(ctx.payload.get("rev"))

        def commit(conn: Any, _task: dict[str, Any]) -> None:
            svc.enqueue_step(
                conn,
                sid,
                "transcribe",
                chain=chain,
                payload={"rev": rev, "idx": idx + 1, "start_sec": round(next_start, 3)},
                asr=choice,
                set_source_status=False,
            )

        ctx.on_commit(commit)
        return {"chunk": idx, "start_sec": start, "next_start": next_start, "duration": total, "final": False,
                "words": len(out.get("words") or [])}  # fmt: skip
    meta = _assemble_chunks(row, work, total)
    _finish_transcript(ctx, sid, row, {"transcript": meta})
    return {"chunk": idx, "final": True, "words": meta["words"], "sentences": meta["sentences"],
            "rtfx": meta["rtfx"]}  # fmt: skip


def _assemble_chunks(row: dict[str, Any], work: Path, total: float) -> dict[str, Any]:
    """Куски одного прогона (rev) → transcript.json.gz, манифест обновлён, audio16k.wav удалён."""
    from autoshorts.clipping.source_ingest import TRANSCRIPT_NAME
    from autoshorts.clipping.transcript import Word
    from autoshorts.providers.asr.gigaam_onnx import GigaamOnnxProvider, RangeResult, transcript_from_ranges
    from autoshorts.services import sources as svc

    results: list[RangeResult] = []
    for f in sorted(work.glob("chunk_*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        results.append(
            RangeResult(
                words=[
                    Word(str(w[0]), float(w[1]), float(w[2]), None if w[3] is None else float(w[3]))
                    for w in data.get("words") or []
                ],  # fmt: skip
                vad=[(float(a), float(b)) for a, b in data.get("vad") or []],
                audio_sec=float(data.get("audio_sec") or 0.0),
                wall_sec=float(data.get("wall_sec") or 0.0),
                start=float(data.get("start") or 0.0),
                stop=float(data.get("stop") or 0.0),
                next_start=float(data.get("next_start") or 0.0),
            )
        )
    if not results:
        raise RetryableError("нет кусков распознавания для сборки")
    prepared = svc.load_manifest(row)
    assert prepared is not None
    lang = str(row.get("language") or "ru")
    tr = transcript_from_ranges(results, duration=total, language=lang, model=GigaamOnnxProvider.model_label)
    path = tr.save(Path(prepared.dir) / TRANSCRIPT_NAME)
    prepared.files["transcript"] = path.name
    prepared.transcript = {
        "path": path.name,
        "provider": tr.provider,
        "model": tr.model,
        "language": tr.language,
        "words": len(tr.words),
        "sentences": len(tr.sentences),
        "duration": tr.duration,
        "rtfx": tr.rtfx,
        "cost_usd": tr.cost_usd,
    }
    prepared.timings["asr"] = sum(r.wall_sec for r in results)
    for wav in (prepared.path("audio16k"), Path(prepared.dir) / "audio16k.wav"):
        if wav is not None:
            wav.unlink(missing_ok=True)  # §8.2: audio16k.wav удаляется сразу после ASR
    prepared.files.pop("audio16k", None)
    prepared.save()
    return prepared.transcript


def _transcribe_task(ctx: TaskContext) -> Any:
    return _guard(ctx, "transcribe", _transcribe_common)


@register_task("source_transcribe_chunk", "heavy", max_attempts=2)
def source_transcribe_chunk(ctx: TaskContext) -> Any:
    return _transcribe_task(ctx)


@register_task("source_transcribe_cloud", "light", max_attempts=3, paid=True)
def source_transcribe_cloud(ctx: TaskContext) -> Any:
    return _transcribe_task(ctx)


# ---------------------------------------------------------------- source_select


def _niche_profile(conn: Any, niche_id: str | None) -> Any:
    from autoshorts.clipping.scoring import NicheProfile
    from autoshorts.repos import niches as niches_repo

    if not niche_id:
        return None
    row = niches_repo.get(conn, niche_id)
    if row is None:
        return None
    cfg = row.get("config") or {}
    script = cfg.get("script") or {}
    visuals = cfg.get("visuals") or {}
    desc = [str(row.get("label") or "")]
    if script.get("tone"):
        desc.append(f"Тон: {script['tone']}")
    return NicheProfile(
        id=str(niche_id),
        label=str(row.get("label") or ""),
        description=". ".join(p for p in desc if p),
        forbidden_phrases=[str(p) for p in script.get("forbidden_phrases") or []],
        keywords=[str(s) for s in visuals.get("subjects") or []],
    )


def _energy(row: dict[str, Any], duration: float) -> Any:
    from autoshorts.clipping.features import energy_from_peaks
    from autoshorts.core.storage import get_layout

    rel = row.get("peaks_path")
    if not rel or duration <= 0:
        return None
    try:
        data = json.loads(get_layout().resolve(rel).read_text(encoding="utf-8"))
        peaks = data.get("peaks") or []
        channel = peaks[0] if peaks and isinstance(peaks[0], list) else peaks
        return energy_from_peaks({"data": channel, "length": data.get("length")}, duration)
    except (OSError, ValueError, TypeError, IndexError) as exc:
        log.warning("source %s: peaks unreadable for energy: %s", row.get("id"), exc)
        return None


def candidate_row(c: Any, transcript: Any, cfg: Any) -> dict[str, Any]:
    """`ClipCandidate` движка → строка clip_candidates (+ индексы слов и итог снаппинга)."""
    from autoshorts.clipping.snapping import snap_clip_to_words

    r = c.to_row()
    raw_s = c.raw_start_sec if c.raw_start_sec is not None else c.start_sec
    raw_e = c.raw_end_sec if c.raw_end_sec is not None else c.end_sec
    snap = snap_clip_to_words(
        transcript.words,
        raw_s,
        raw_e,
        search_window=cfg.snap_search_window,
        max_lead=cfg.snap_max_lead,
        max_tail=cfg.snap_max_tail,
        duration=transcript.duration or None,
    )
    r["origin"] = c.origin if c.origin in ("llm", "mock", "heuristic", "manual") else "heuristic"
    if snap.w0 >= 0 and snap.w1 >= snap.w0:
        r["start_word_idx"], r["end_word_idx"] = int(snap.w0), int(snap.w1)
        r["snap"] = {
            "w0": int(snap.w0),
            "w1": int(snap.w1),
            "lead_sec": round(float(snap.lead), 3),
            "tail_sec": round(float(snap.tail), 3),
            "raw_start_sec": None if raw_s is None else round(float(raw_s), 3),
            "raw_end_sec": None if raw_e is None else round(float(raw_e), 3),
        }
    else:
        r["start_word_idx"] = r["end_word_idx"] = None
        r["snap"] = {}
    return r


class _EventWriter:
    """Каждый вызов LLM → строка generation_events сразу после вызова (§6.1) + расход в бюджет."""

    ALLOWED = ("step_name", "provider", "model", "units", "unit_price_usd", "cost_usd", "latency_ms",
               "http_status", "status", "error", "cpu_ms")  # fmt: skip

    def __init__(self, source_id: int, task_id: int) -> None:
        self.source_id = source_id
        self.task_id = task_id
        self.count = 0
        self.cost = 0.0

    def __call__(self, event: Mapping[str, Any]) -> None:
        from autoshorts.db.pool import connection
        from autoshorts.repos import budget as budget_repo
        from autoshorts.services import budget

        fields = {k: event.get(k) for k in self.ALLOWED if event.get(k) is not None}
        fields.setdefault("step_name", "highlight_select")
        fields.setdefault("provider", "openai_compat")
        fields["status"] = "error" if fields.get("status") == "error" else "ok"
        cost = float(fields.get("cost_usd") or 0.0)
        fields["cost_usd"] = cost
        try:
            with connection() as conn:
                budget_repo.insert_generation_event(
                    conn, {**fields, "source_id": self.source_id, "task_id": self.task_id}
                )
                if cost > 0:
                    budget.settle(conn, estimate_usd=0, actual_usd=cost)
            self.count += 1
            self.cost += cost
        except Exception:  # noqa: BLE001 - запись учёта не должна валить отбор посреди окон
            log.exception("source %s: generation_event not written", self.source_id)


def _select_body(ctx: TaskContext, row: dict[str, Any]) -> dict[str, Any]:
    from autoshorts.api.schemas.sources import SourceOptions
    from autoshorts.clipping.selection import SelectionConfig, select_clips
    from autoshorts.core.timeutil import utcnow
    from autoshorts.db.pool import connection
    from autoshorts.repos import sources as sources_repo
    from autoshorts.services import sources as svc

    sid = int(row["id"])
    try:
        transcript = svc.load_transcript(sid)
    except Exception as exc:  # noqa: BLE001 - NotFound и битый файл: без транскрипта отбора нет
        raise PermanentError(f"нет транскрипта: {exc}", error_class="no_transcript") from None
    raw_params = ctx.payload.get("params") or row.get("options") or {}
    try:
        opts = SourceOptions.model_validate(raw_params)
    except Exception:  # noqa: BLE001
        opts = SourceOptions()
    cfg = SelectionConfig.from_options(
        opts.model_dump(mode="json"), scoring=ctx.settings.clip_score_weights.model_dump()
    )
    niche_id = ctx.payload.get("niche_id") or row.get("niche_id")
    with connection() as conn:
        niche = _niche_profile(conn, niche_id)
        choice = svc.selection_llm(conn, str(opts.selector), target_clip_sec=opts.target_clip_sec)
    energy = _energy(row, float(transcript.duration or row.get("duration_sec") or 0.0))
    reservation = None
    if choice.client is not None and not choice.client.is_mock and ctx.budget is not None:
        hours = max(float(transcript.duration or 0.0) / 3600.0, 0.1)
        reservation = ctx.budget.reserve(max(0.01, round(0.04 * hours, 4)), provider=choice.provider_key)
    writer = _EventWriter(sid, ctx.task_id)
    ctx.progress(0.05)
    try:
        res = select_clips(
            transcript, llm=choice.client, niche=niche, config=cfg, energy=energy, on_event=writer
        )
    finally:
        if reservation is not None:
            with contextlib.suppress(Exception):
                ctx.budget.release(reservation)
    ctx.check_cancelled()
    rows = [candidate_row(c, transcript, cfg) for c in res.candidates]
    fallback = choice.client is not None and res.mode == "heuristic"
    if res.mode == "heuristic":
        label = "Эвристика без LLM — LLM недоступен, сработал запасной путь" if fallback else choice.label
    else:
        label = choice.label
    warnings = list(res.warnings)
    if choice.note:
        warnings.append(choice.note)
    selection = {
        "mode": res.mode,
        "label": label,
        "engine_label": res.label,
        "llm_configured": bool(choice.configured),
        "provider_key": choice.provider_key,
        "fallback": fallback,
        "params": opts.model_dump(mode="json"),
        "niche_id": niche_id,
        "n_candidates": len(res.candidates),
        "n_blocked": sum(1 for c in res.candidates if c.blocked),
        "n_requests": res.n_requests,
        "cost_usd": round(float(res.cost_usd), 6),
        "wall_ms": res.wall_ms,
        "dropped": len(res.dropped),
        "warnings": warnings[:50],
        "task_id": ctx.task_id,
        "finished_at": utcnow().isoformat(),
    }
    chain = _chain(ctx)
    ctx.progress(1.0)

    def commit(conn: Any, _task: dict[str, Any]) -> None:
        superseded = sources_repo.supersede_auto_candidates(conn, sid)
        sources_repo.insert_candidates(conn, sid, rows)
        selection["superseded"] = superseded
        sources_repo.update(conn, sid, {"selection": selection})
        svc.advance(conn, sid, chain)

    ctx.on_commit(commit)
    return {
        "mode": res.mode,
        "label": label,
        "candidates": len(rows),
        "blocked": selection["n_blocked"],
        "requests": res.n_requests,
        "cost_usd": selection["cost_usd"],
        "events": writer.count,
    }


def run_select(ctx: TaskContext) -> Any:
    """Обработчик отбора M5-A (не регистрируется: вид `source_select` регистрирует M5-B в
    `tasks/clipping.py`, двойная регистрация роняет `registry.load_all`). Интегратор может сделать
    его обработчиком `source_select`; цепочка конвейера ставит `source_select` в любом случае."""
    return _guard(ctx, "select", _select_body)


# ---------------------------------------------------------------- source_scenes


def _scenes_body(ctx: TaskContext, row: dict[str, Any]) -> dict[str, Any]:
    from autoshorts.clipping.reframe.shots import detect_shots
    from autoshorts.core.storage import get_layout
    from autoshorts.repos import sources as sources_repo
    from autoshorts.services import sources as svc

    sid = int(row["id"])
    rel = row.get("proxy_path")
    if not rel:
        raise PermanentError("нет прокси: склейки считаются по нему", error_class="no_proxy")
    proxy = get_layout().resolve(rel)
    if not proxy.is_file():
        raise PermanentError("файл прокси не найден", error_class="no_proxy")
    duration = float(row.get("duration_sec") or 0.0)
    start = max(0.0, float(ctx.payload.get("start_sec") or 0.0))
    end = float(ctx.payload.get("end_sec") or duration or 0.0)
    if end <= start:
        raise PermanentError("пустой отрезок для склеек", error_class="bad_range")
    cuts: list[float] = []
    covered: list[list[float]] = []
    pos = start
    while pos < end - 1e-3:
        ctx.check_cancelled()
        stop = min(pos + SCENES_WINDOW_SEC, end)
        det = detect_shots(proxy, pos, stop, pad=0.0)
        if det.error:
            raise RetryableError(f"PySceneDetect: {det.error}")
        cuts.extend(c for c in det.cuts if pos <= c <= stop)
        covered.append([pos, stop])
        pos = stop
        ctx.progress((pos - start) / (end - start))

    def commit(conn: Any, _task: dict[str, Any]) -> None:
        fresh = sources_repo.get(conn, sid, for_update=True)
        old = (fresh or {}).get("scenes") or {}
        if isinstance(old, list):
            old = {}
        all_cuts = sorted({round(float(c), 3) for c in [*(old.get("cuts") or []), *cuts]})
        merged: list[float] = []
        for c in all_cuts:
            if not merged or c - merged[-1] > 0.05:
                merged.append(c)
        coverage = svc.merge_coverage([*(old.get("coverage") or []), *covered])
        sources_repo.update(conn, sid, {"scenes": {"version": 1, "cuts": merged, "coverage": coverage}})

    ctx.on_commit(commit)
    return {"cuts": len(cuts), "start_sec": start, "end_sec": end}


@register_task("source_scenes", "heavy", max_attempts=2)
def source_scenes(ctx: TaskContext) -> Any:
    """Склейки — по запросу: их сбой не переводит источник в failed (виден в конвейере шагов)."""
    row = _load(ctx)
    if row is None:
        return {"skipped": "source_deleted"}
    return _scenes_body(ctx, row)


# ---------------------------------------------------------------- дочерний процесс ASR


def _child_progress_writer(path: Path) -> Callable[[float], None]:
    last = [0.0]

    def write(f: float) -> None:
        now = time.monotonic()
        if f < 1.0 and now - last[0] < 0.5:
            return
        last[0] = now
        with contextlib.suppress(OSError):
            _write_json(path, {"f": round(float(f), 4)})

    return write


def _ensure_wav16k(src: Any) -> Path:
    """audio16k.wav источника; если удалён — пересобрать из того же входа, что у прохода прокси."""
    from autoshorts.clipping.source_ingest import SourceRejected
    from autoshorts.pipeline.ffmpeg_exec import run_media

    wav = src.path("audio16k")
    if wav is not None and wav.is_file():
        return wav
    audio = next(
        (
            p
            for p in (src.path("original"), src.path("original_audio"), src.path("audio"))
            if p is not None and p.is_file()
        ),
        None,
    )
    if audio is None:
        raise SourceRejected("no_audio", "source has no audio file to transcribe")
    wav = Path(src.dir) / "audio16k.wav"
    tmp = Path(src.dir) / ".audio16k.tmp.wav"
    run_media(
        ["ffmpeg", "-y", "-i", str(audio), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(tmp)],
        timeout_sec=3600,
        threads=2,
        nice=0,  # процесс уже под nice 10
    )
    os.replace(tmp, wav)
    src.files["audio16k"] = wav.name
    src.save()
    return wav


def asr_child_main(argv: list[str]) -> int:
    """`python -m autoshorts.tasks.sources asr-child <args.json>`: режимы whole | range."""
    args = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    result_path = Path(args["result"])
    progress = _child_progress_writer(Path(args["progress"]))
    try:
        from autoshorts.clipping.source_ingest import PreparedSource, transcribe_prepared
        from autoshorts.providers.asr import get_asr_provider
        from autoshorts.providers.asr.base import wav_info

        provider = get_asr_provider(args["engine"], **dict(args.get("kwargs") or {}))
        src = PreparedSource.load(args["source_dir"])
        if args["mode"] == "range":
            wav = _ensure_wav16k(src)
            info = wav_info(wav)
            total = info.duration if info is not None else 0.0
            res = provider.transcribe_range(
                wav, float(args["start_sec"]), float(args["end_sec"]), progress=progress
            )
            out = {
                "ok": True,
                "idx": int(args.get("idx") or 0),
                "start": res.start,
                "stop": res.stop,
                "next_start": res.next_start,
                "audio_sec": res.audio_sec,
                "wall_sec": res.wall_sec,
                "duration": total,
                "words": [[w.w, w.s, w.e, w.p] for w in res.words],
                "vad": [list(v) for v in res.vad],
            }
        else:
            transcribe_prepared(
                src,
                provider,
                language=str(args.get("language") or "ru"),
                keep_audio16k=False,
                min_mem_mb=0,
                progress=lambda _step, f: progress(f),
            )
            out = {"ok": True, "transcript": PreparedSource.load(args["source_dir"]).transcript}
        progress(1.0)
        _write_json(result_path, out)
        return 0
    except BaseException as exc:  # noqa: BLE001 - любой исход — в файл результата
        permanent = isinstance(exc, PermanentError | ValueError | KeyError | FileNotFoundError)
        with contextlib.suppress(Exception):
            from autoshorts.clipping.source_ingest import SourceRejected as _SR

            permanent = permanent or isinstance(exc, _SR)
        if isinstance(exc, KeyboardInterrupt | SystemExit):
            permanent = False
        _write_json(
            result_path,
            {
                "ok": False,
                "permanent": permanent,
                "error": f"{type(exc).__name__}: {exc}"[:1500],
                "error_class": "asr_unavailable" if type(exc).__name__ == "AsrUnavailable" else "asr_failed",
                "traceback": traceback.format_exc()[-3000:],
            },
        )
        print(traceback.format_exc(), file=sys.stderr)
        return CHILD_EXIT_PERMANENT if permanent else 1


__all__ = [
    "asr_child_main",
    "candidate_row",
    "run_asr_child",
    "source_download",
    "source_prepare",
    "source_scenes",
    "run_select",
    "source_transcribe_chunk",
    "source_transcribe_cloud",
]


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "asr-child":
        with contextlib.suppress(OSError):
            os.nice(0)
        sys.exit(asr_child_main(sys.argv[2:]))
    print("usage: python -m autoshorts.tasks.sources asr-child <args.json>", file=sys.stderr)
    sys.exit(2)
