"""Нарезка в сервисе: кандидаты, отбор, clip-job, авторский слой, голос автора, оригинальность (M5-B).

Движок — `clipping/**` (не переписывается): отбор `selection.select_clips`, снаппинг
`snapping.snap_clip_to_words`, скоринг `scoring.score_candidate`, сценарий
`author.script.generate_author_script`, голос `author.voice`, EDL и рендер `produce.produce_clip`,
QC `qc_clip.run_clip_qc`, отчёт `originality.build_originality_report`. Этот модуль связывает их
с БД (`repos/clips.py`), очередью и API; стадии `stages/{clip_fetch,clip_analyze,author_script,
author_voice,clip_render,clip_qc}.py` — тонкие обёртки над функциями отсюда.

Режимы без ключа (OWNER-DECISIONS п. 2, 5): LLM — OpenAI-совместимый провайдер (`LLM_BASE_URL`,
по умолчанию https://umka.chat/v1, `LLM_API_KEY` из секретов или окружения, `LLM_MODEL`). Нет
ключа — отбор эвристикой, авторский текст — шаблонным черновиком; оба режима подписаны в ответах
(`origin_label`, `label`), отчёт об оригинальности требует переписать шаблон.

Переключатели окружения (латиница): `CLIP_LLM` = auto | mock | none (mock — детерминированная
заглушка для демо и тестов, в prod запрещена), `CLIP_TTS` = edge | mock, `CLIP_VOICE_ASR` —
провайдер пословных таймингов записи оператора (по умолчанию gigaam_v3_onnx; распознавание идёт в
дочернем процессе, как в `scripts/clip_local.py`).

Технической маскировки чужого контента здесь нет: оригинальность — это авторский вклад.
"""

from __future__ import annotations

import bisect
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO

from autoshorts.core.errors import (
    BudgetPaused,
    Conflict,
    JobStateConflict,
    NotFound,
    PayloadTooLarge,
    PermanentError,
    ResourceDeferred,
    RetryableError,
    UnsupportedMediaType,
    ValidationFailed,
    VersionConflict,
)
from autoshorts.repos import clips as clips_repo

if TYPE_CHECKING:
    from autoshorts.clipping.author.script import AuthorScript, AuthorScriptResult
    from autoshorts.clipping.author.voice import VoiceResult, VoiceTake
    from autoshorts.clipping.produce import ClipSource
    from autoshorts.clipping.reframe import ReframePlan
    from autoshorts.clipping.transcript import Transcript, Word
    from autoshorts.services.jobs import Caller
    from autoshorts.tasks.registry import TaskContext

log = logging.getLogger(__name__)

Row = dict[str, Any]

API_PREFIX = "/api/v1"
SEGMENTS: tuple[str, ...] = ("cold_open", "insert_0", "insert_1", "outro")
SEGMENT_IDX: dict[str, int] = {s: i for i, s in enumerate(SEGMENTS)}

SELECT_TASK_KIND = "source_select"
DRAFT_TASK_KIND = "author_script_draft"
VOICE_TASK_KIND = "author_voice_prepare"
STEP_SELECT = "highlight_select"
STEP_AUTHOR = "author_script"
SELECT_ESTIMATE_USD = 0.03  # §6.3.3: ≈ $0,01–0,02 на час источника, с запасом
AUTHOR_ESTIMATE_USD = 0.01
AUTHOR_REGEN_ATTEMPTS = 2  # запрещённая фраза в черновике LLM → ещё 2 попытки, дальше PermanentError
CANDIDATE_MIN_SEC = 3.0
CANDIDATE_MAX_SEC = 180.0
SNAP_WORD_WINDOW = 0.01  # клик по слову: берём ровно это слово, только уводим разрез в тишину
PREROLL_PAD_SEC = 12.0  # анализ кадра захватывает до 12 с перед клипом (cold open preroll)
EXCERPT_MAX_CHARS = 1500
VOICE_PROCESSING_STALE_SEC = 1800.0
TRACKING_CODE_LEN = 5

# статусы, в которых авторский текст можно менять (до стадии author_voice) и просить черновик
AUTHOR_EDITABLE_STATUSES = ("draft", "queued", "rework", "failed")
AUTHOR_EDITABLE_STAGES = ("clip_fetch", "clip_analyze", "author_script")
DRAFT_ALLOWED_STAGES = ("clip_fetch", "clip_analyze")
VOICE_UPLOAD_STATUSES = ("draft", "queued", "processing", "rework", "pending_review", "qc_failed", "failed")

MODE_LABELS = {
    "llm": "LLM",
    "mock": "[mock LLM — заглушка авторского текста]",
    "heuristic": "[черновик без LLM — перепишите своими словами]",
    "operator": "текст оператора",
}

EDGE_VOICE_RE = re.compile(r"[a-z]{2,3}-[A-Z]{2}-[A-Za-z]+Neural")

# yt-dlp: подменяется в тестах (фабрика `YoutubeDL`)
YDL_FACTORY: Callable[[dict[str, Any]], Any] | None = None


# ============================================================================ окружение


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _layout() -> Any:
    from autoshorts.core.storage import get_layout

    return get_layout()


def _connection() -> Any:
    from autoshorts.db.pool import connection

    return connection()


def _mock_allowed() -> bool:
    from autoshorts.pipeline.voice import mock_allowed

    return mock_allowed()


def llm_key() -> str | None:
    """Ключ LLM: секрет `LLM_API_KEY` (БД, затем окружение)."""
    try:
        from autoshorts.services.secrets import get_secret

        return get_secret("LLM_API_KEY") or _env("LLM_API_KEY") or None  # секрет в БД, иначе окружение/.env
    except Exception:  # noqa: BLE001 - нет пула или ключа шифрования: только окружение
        return _env("LLM_API_KEY") or None


def llm_forced_mode() -> str:
    mode = _env("CLIP_LLM", "auto").lower()
    return mode if mode in ("auto", "mock", "none") else "auto"


def llm_configured() -> bool:
    return bool(llm_key())


def llm_client(purpose: str, selector: str = "auto") -> tuple[Any, str]:
    """(клиент или None, режим: llm | mock | heuristic). `purpose`: select | author."""
    forced = llm_forced_mode()
    if forced == "mock" and _mock_allowed():
        if purpose == "select":
            from autoshorts.clipping.llm_select import make_mock_client

            return make_mock_client(), "mock"
        from autoshorts.clipping.author.script import make_mock_author_client

        return make_mock_author_client(), "mock"
    if forced == "none" or selector == "heuristic":
        return None, "heuristic"
    key = llm_key()
    if not key:
        return None, "heuristic"
    from autoshorts.clipping.llm_client import LLMClient

    env = {**os.environ, "LLM_API_KEY": key}
    if purpose == "select" and (os.environ.get("LLM_SELECT_MODEL") or "").strip():
        # отбор моментов решает, будет ли ролик законченной историей — своя, более сильная модель (29.10)
        env["LLM_MODEL"] = os.environ["LLM_SELECT_MODEL"].strip()
    if purpose == "author" and (os.environ.get("LLM_AUTHOR_MODEL") or "").strip():
        # сценарий автора — единственный свой вклад в ролик (YouTube с 01.10.2026 режет перезаливы):
        # слабая модель пересказывает сцену вместо мнения, поэтому своя модель (29.10)
        env["LLM_MODEL"] = os.environ["LLM_AUTHOR_MODEL"].strip()
    client = LLMClient.from_env(env=env)
    return client, ("llm" if client is not None else "heuristic")


def tts_engine() -> str:
    mode = _env("CLIP_TTS", "edge").lower()
    return mode if mode in ("edge", "mock") else "edge"


def voice_asr_key() -> str:
    return _env("CLIP_VOICE_ASR", "gigaam_v3_onnx")


# общий бокс: тяжёлые шаги ждут MemAvailable ≥ 1500 МБ (как clip_local);
# маленький стенд задаёт порог переменной AS_DEV_MIN_MEM_MB
MEM_MIN_MB = int((os.environ.get("AS_DEV_MIN_MEM_MB") or "").strip() or 1500)
MEM_WAIT_SEC = 300.0


def wait_memory_or_defer(min_mb: int | None = None, timeout_sec: float = MEM_WAIT_SEC) -> None:
    """Подождать память; не дождались — задача откладывается (`mem_low`), попытка не тратится.
    Без явного `min_mb` порог берётся на момент вызова (AS_DEV_MIN_MEM_MB или 1500)."""
    from autoshorts.clipping.source_ingest import InsufficientMemory, min_mem_mb_now, wait_for_memory

    if min_mb is None:
        min_mb = min_mem_mb_now()
    try:
        wait_for_memory(min_mb, timeout_sec=timeout_sec)
    except InsufficientMemory as exc:
        raise ResourceDeferred("mem_low", retry_after_sec=120.0, message=str(exc)) from exc


class PaidCalls:
    """Бюджет платных вызовов LLM одной операции (§6.1): резерв до первого вызова, `generation_events`
    и расход — сразу после каждого вызова (`on_event` движка), незанятый резерв снимается в конце."""

    def __init__(self, budget: Any, *, provider: str, estimate_usd: float, enabled: bool = True) -> None:
        self.budget = budget if enabled else None
        self.provider = provider
        self.estimate = float(estimate_usd)
        self.reservation: Any = None
        self._day: date | None = None
        self.events: list[dict[str, Any]] = []
        self.cost_usd = 0.0

    def __enter__(self) -> PaidCalls:
        if self.budget is not None:
            self.reservation = self.budget.reserve(self.estimate, provider=self.provider)
            self._day = getattr(self.reservation, "day", None)
        return self

    def on_event(self, ev: dict[str, Any]) -> None:
        self.events.append(ev)
        cost = float(ev.get("cost_usd") or 0.0)
        self.cost_usd += cost
        if self.budget is None:
            return
        fields = {
            "step_name": str(ev.get("step_name") or "llm"),
            "model": ev.get("model"),
            "units": ev.get("units"),
            "latency_ms": ev.get("latency_ms"),
            "http_status": ev.get("http_status"),
            "status": "error" if ev.get("status") == "error" else "ok",
            "error": (str(ev.get("error"))[:500] if ev.get("error") else None),
        }
        try:
            if self.reservation is not None:
                res, self.reservation = self.reservation, None
            else:
                from autoshorts.services.budget import BudgetReservation

                res = BudgetReservation(Decimal(0), self.provider, self._day or date.today())
            self.budget.settle(res, cost, **fields)
        except Exception:  # noqa: BLE001 - учёт не должен ронять уже оплаченный вызов
            log.warning("generation_event for %s was not recorded", fields["step_name"], exc_info=True)

    def __exit__(self, *exc: Any) -> None:
        if self.budget is not None and self.reservation is not None:
            res, self.reservation = self.reservation, None
            try:
                self.budget.release(res)
            except Exception:  # noqa: BLE001
                log.warning("budget reservation release failed", exc_info=True)


# ============================================================================ источник


@dataclass
class SourceContext:
    row: Row
    dir: Path
    prepared: Any  # PreparedSource | None
    transcript_path: Path | None

    @property
    def id(self) -> int:
        return int(self.row["id"])


def _invalid(loc: Sequence[Any], message: str, kind: str = "value_error") -> ValidationFailed:
    return ValidationFailed(message, details={"errors": [{"loc": list(loc), "msg": message, "type": kind}]})


def require_source(conn: Any, source_id: int, *, for_update: bool = False) -> Row:
    row = clips_repo.get_source(conn, source_id, for_update=for_update)
    if row is None or row.get("deleted_at") is not None:
        raise NotFound("source", "Источник не найден")
    return row


def source_dir(row: Row) -> Path:
    rel = row.get("media_dir") or f"sources/{int(row['id'])}"
    return _layout().resolve(rel)


def _resolve_media(rel: str | None, base: Path | None = None) -> Path | None:
    if not rel:
        return None
    p = Path(rel)
    if p.is_absolute():
        return p if p.is_file() else None
    try:
        full = _layout().resolve(rel)
        if full.is_file():
            return full
    except Exception:  # noqa: BLE001 - относительный путь каталога источника, а не MEDIA_ROOT
        pass
    if base is not None and (base / rel).is_file():
        return base / rel
    return None


def source_context(conn: Any, row: Row) -> SourceContext:
    from autoshorts.clipping.source_ingest import MANIFEST_NAME, PreparedSource

    d = source_dir(row)
    prepared = None
    if (d / MANIFEST_NAME).is_file():
        try:
            prepared = PreparedSource.load(d)
        except (OSError, ValueError, KeyError):
            log.warning("source %s: source.json unreadable", row["id"], exc_info=True)
    tpath = None
    tr_row = clips_repo.current_transcript(conn, int(row["id"]))
    if tr_row is not None:
        tpath = _resolve_media(tr_row["path"], d)
    if tpath is None and prepared is not None:
        cand = prepared.path("transcript")
        tpath = cand if cand is not None and cand.is_file() else None
    return SourceContext(row, d, prepared, tpath)


_TR_CACHE: dict[str, tuple[float, Any]] = {}
_TR_LOCK = threading.Lock()


def load_transcript(ctx: SourceContext) -> Transcript:
    from autoshorts.clipping.transcript import Transcript

    if ctx.transcript_path is None:
        raise Conflict(
            "У источника ещё нет транскрипта",
            code="source_not_ready",
            details={"source_id": ctx.id, "status": ctx.row.get("status")},
        )
    key = str(ctx.transcript_path)
    mtime = ctx.transcript_path.stat().st_mtime
    with _TR_LOCK:
        hit = _TR_CACHE.get(key)
        if hit is not None and hit[0] == mtime:
            return hit[1]
    tr = Transcript.load(ctx.transcript_path)
    with _TR_LOCK:
        _TR_CACHE[key] = (mtime, tr)
        while len(_TR_CACHE) > 6:
            _TR_CACHE.pop(next(iter(_TR_CACHE)))
    return tr


def rights_of(ctx: SourceContext) -> dict[str, Any]:
    r = ctx.row
    if r.get("rights_source_id") is None:
        return dict(ctx.prepared.rights) if ctx.prepared is not None else {}
    out: dict[str, Any] = {
        "rights_type": r.get("rights_type"),
        "owner_name": r.get("owner_name"),
        "owner_channel_url": r.get("owner_channel_url"),
        "proof_note": r.get("proof_note"),
        "allowlist_status": r.get("allowlist_status"),
        "attribution_text": r.get("attribution_text"),
        "valid_until": r["valid_until"].isoformat() if r.get("valid_until") else None,
        "license": r.get("license"),
        "license_url": r.get("license_url"),
    }
    return {k: v for k, v in out.items() if v is not None}


def rights_warnings_of(ctx: SourceContext) -> list[str]:
    raw = ctx.row.get("rights_warnings")
    if raw:
        return [str(w) for w in raw]
    return list(ctx.prepared.rights_warnings) if ctx.prepared is not None else []


def watermark_of(ctx: SourceContext) -> dict[str, Any] | None:
    wm = ctx.row.get("watermark_detail")
    if isinstance(wm, dict) and wm:
        return wm
    if ctx.prepared is not None and ctx.prepared.watermark is not None:
        return dict(ctx.prepared.watermark)
    if ctx.row.get("watermark_suspect"):
        return {"suspect": True, "severity": "warning"}
    return None


@dataclass
class ClipNiche:
    """Профиль ниши для движка (`scoring.NicheProfile` + свои домены для проверки ссылок)."""

    id: str = "generic"
    label: str = ""
    description: str = ""
    forbidden_phrases: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    # секция `selection` ниши (см. scoring.NicheProfile): режим отбора, ворота, кадры в detail-проходе
    selection_mode: str = "talk"
    min_payoff: float | None = None
    min_emotion: float | None = None
    frames_in_detail: bool = True


def niche_profile(conn: Any, niche_id: str | None, *, product_id: str | None = None) -> ClipNiche | None:
    if not niche_id:
        return None
    row = clips_repo.niche_row(conn, niche_id)
    if row is None:
        return None
    cfg = row.get("config") or {}
    script = cfg.get("script") if isinstance(cfg.get("script"), dict) else {}
    visuals = cfg.get("visuals") if isinstance(cfg.get("visuals"), dict) else {}
    forbidden = cfg.get("forbidden_phrases") or script.get("forbidden_phrases") or []
    parts = [str(row.get("label") or cfg.get("label") or "")]
    if script.get("tone"):
        parts.append(f"Тон: {script['tone']}")
    domains: list[str] = []
    if product_id:
        prod = clips_repo.product_row(conn, product_id) or {}
        brand = prod.get("brand") if isinstance(prod.get("brand"), dict) else {}
        for val in (prod.get("redirect_host"), prod.get("landing_url"), brand.get("display_domain")):
            if isinstance(val, str) and val:
                domains.append(val.split("://")[-1].split("/")[0].lower())
    sel = cfg.get("selection") if isinstance(cfg.get("selection"), dict) else {}
    return ClipNiche(
        id=str(row["id"]),
        label=str(row.get("label") or ""),
        description=". ".join(p for p in parts if p),
        forbidden_phrases=[str(p) for p in forbidden if isinstance(p, str)],
        keywords=[str(s) for s in (visuals.get("subjects") or []) if isinstance(s, str)],
        selection_mode=str(sel.get("mode") or "talk"),
        min_payoff=None if sel.get("min_payoff") is None else float(sel["min_payoff"]),
        min_emotion=None if sel.get("min_emotion") is None else float(sel["min_emotion"]),
        frames_in_detail=bool(sel.get("frames_in_detail", True)),
        allowed_domains=domains,
    )


def clip_source(ctx: SourceContext, section: Mapping[str, Any] | None = None) -> ClipSource:
    """Что пойдёт в рендер: оригинал загрузки или 1080p-секция URL-источника (+ звук отдельно)."""
    from autoshorts.clipping.produce import ClipSource

    rights, warns, wm = rights_of(ctx), rights_warnings_of(ctx), watermark_of(ctx)
    prepared = ctx.prepared
    proxy = (prepared.path("proxy") if prepared is not None else None) or _resolve_media(
        ctx.row.get("proxy_path"), ctx.dir
    )
    if section:
        path = _layout().resolve(str(section["path"]))
        audio = None
        if prepared is not None:
            for key in ("audio", "original_audio"):
                cand = prepared.path(key)
                if cand is not None and cand.is_file():
                    audio = cand
                    break
        audio = audio or _resolve_media(ctx.row.get("audio_path"), ctx.dir)
        start = float(section["start"])
        dur = float(section["duration"])
        return ClipSource(
            render_path=path,
            analysis_path=proxy if proxy is not None and proxy.is_file() else path,
            width=int(section["width"]),
            height=int(section["height"]),
            duration=dur,
            fps=float(section.get("fps") or 30.0),
            has_audio=audio is not None or bool(section.get("has_audio")),
            source_id=str(ctx.id),
            title=str(ctx.row.get("title") or ""),
            rights=rights,
            rights_warnings=warns,
            watermark=wm,
            src_offset=start,
            media_range=(start, start + dur),
            audio_path=audio,
            audio_offset=0.0,
        )
    if prepared is not None:
        try:
            src = ClipSource.from_prepared(prepared)
        except FileNotFoundError as exc:
            raise PermanentError(
                "У источника нет видео для рендера (для ссылки нужна секция clip_fetch)",
                error_class="source_media_missing",
            ) from exc
        src.rights, src.rights_warnings, src.watermark = rights, warns, wm
        src.source_id = str(ctx.id)
        return src
    original = _resolve_media(ctx.row.get("original_path"), ctx.dir)
    if original is None:
        raise PermanentError("Нет файла источника для рендера", error_class="source_media_missing")
    src = ClipSource.from_file(
        original, rights=rights, analysis_path=proxy, source_id=str(ctx.id), title=str(ctx.row["title"])
    )
    src.rights_warnings, src.watermark = warns, wm
    return src


def needs_section(ctx: SourceContext) -> bool:
    """URL-источник без полноценного оригинала: рендеру нужна 1080p-секция (§6.3.1).

    Прямая ссылка на файл (yt-dlp generic, например файл хранилища Кино-трендов): `original_video`
    скачан целиком в исходном качестве, секция по сети ничего не добавит — а файл по ссылке к моменту
    нарезки может быть уже удалён. Тогда рендер идёт из локального `original_video`."""
    if ctx.row.get("origin") != "url":
        return False
    if ctx.prepared is not None:
        orig = ctx.prepared.path("original")
        if orig is not None and orig.is_file():
            return False
        return not (_is_direct_file(ctx.prepared) and local_original_video(ctx) is not None)
    return _resolve_media(ctx.row.get("original_path"), ctx.dir) is None


def _is_direct_file(prepared: Any) -> bool:
    """Источник скачан по прямой ссылке на файл (generic): у него нет вариантов качества."""
    platform = getattr(prepared, "platform", None) or {}
    return str(platform.get("extractor_key") or "").lower() == "generic"


def local_original_video(ctx: SourceContext) -> Path | None:
    """Локальное видео URL-источника (`original_video`), если оно на диске; для запасного пути clip_fetch."""
    if ctx.prepared is None:
        return None
    vid = ctx.prepared.path("original_video")
    return vid if vid is not None and vid.is_file() else None


# ============================================================================ транскрипт: слова и предложения


def sid_for_word(tr: Transcript, idx: int) -> int | None:
    if not tr.sentences:
        return None
    starts = [s.w0 for s in tr.sentences]
    k = bisect.bisect_right(starts, idx) - 1
    if k < 0:
        return tr.sentences[0].sid
    return tr.sentences[k].sid


def word_span(words: Sequence[Word], start: float, end: float) -> tuple[int, int] | None:
    """Индексы первого и последнего слова, целиком лежащих в [start, end] (с допуском 50 мс)."""
    inside = [i for i, w in enumerate(words) if w.s >= start - 0.05 and w.e <= end + 0.05]
    if not inside:
        inside = [i for i, w in enumerate(words) if w.e > start and w.s < end]
    if not inside:
        return None
    return inside[0], inside[-1]


# Длина ролика: проверка QC пропускает 20–58 с (clipping/qc_clip.DURATION_RANGE). Подгоняем с запасом.
CLIP_FIT_MIN_SEC = 21.0
CLIP_FIT_MAX_SEC = 57.0
CLIP_FIT_TAIL_SEC = 0.25
CLIP_AUTOFIX_MAX = 2  # сколько раз ролик сам перерезается после проверки длины


def fit_clip_bounds(
    tr: Transcript | None,
    start: float,
    end: float,
    *,
    source_duration: float | None = None,
    lo: float = CLIP_FIT_MIN_SEC,
    hi: float = CLIP_FIT_MAX_SEC,
) -> tuple[float, float] | None:
    """Границы, которые пройдут проверку длины. Короткий ролик дотягивается целыми фразами вперёд
    (у конца источника — назад), длинный обрезается по концу фразы. None — менять нечего."""
    start, end = float(start), float(end)
    if lo <= end - start <= hi:
        return None
    total = float(source_duration or (tr.duration if tr is not None else 0) or 0) or None
    sents = list(tr.sentences) if tr is not None and tr.sentences else []
    s, e = start, end
    if e - s < lo:
        # развязка в конце ролика: сначала добираем завязку (фразы до начала), и только потом — после конца
        for sent in reversed(sents):
            if sent.s >= s - 0.05:
                continue
            if e - (sent.s - CLIP_FIT_TAIL_SEC) > hi:
                break
            s = max(0.0, sent.s - CLIP_FIT_TAIL_SEC)
            if e - s >= lo:
                break
        if e - s < lo:
            for sent in sents:
                if sent.e <= e + 0.05:
                    continue
                if sent.e + CLIP_FIT_TAIL_SEC - s > hi:
                    break
                e = sent.e + CLIP_FIT_TAIL_SEC
                if e - s >= lo:
                    break
        if e - s < lo:  # фраз нет или они слишком длинные — просто секунды
            e = s + lo
            if total is not None and e > total:
                e = total
                s = max(0.0, e - lo)
    else:
        # длинная сцена: развязка в конце — укорачиваем с начала, начало ставим на начало фразы
        starts = [x.s for x in sents if e - hi <= x.s - CLIP_FIT_TAIL_SEC <= e - lo]
        s = max(0.0, starts[0] - CLIP_FIT_TAIL_SEC) if starts else e - hi
    if total is not None:
        e = min(e, total)
    s = max(s, e - hi)
    if abs(s - start) <= 0.01 and abs(e - end) <= 0.01:
        return None
    return round(s, 3), round(e, 3)


def _job_transcript(conn: Any, job: Mapping[str, Any]) -> tuple[Transcript | None, float | None]:
    """Расшифровка и длительность источника ролика (нет — None)."""
    if job.get("source_id") is None:
        return None, None
    src = clips_repo.get_source(conn, int(job["source_id"]))
    if src is None:
        return None, None
    sctx = source_context(conn, src)
    tr = load_transcript(sctx) if sctx.transcript_path is not None else None
    dur = src.get("duration_sec")
    return tr, (float(dur) if dur else None)


def refit_clip_job(conn: Any, job: Mapping[str, Any], reason: str) -> tuple[float, float] | None:
    """Подогнать границы ролика под проверку длины: новые clip_start/end и диапазон фраз, пометка в
    stage_payload.autofit. None — границы и так годятся (или нет источника)."""
    from autoshorts.repos import jobs as jobs_repo

    tr, total = _job_transcript(conn, job)
    fit = fit_clip_bounds(tr, float(job["clip_start_sec"]), float(job["clip_end_sec"]), source_duration=total)
    if fit is None:
        return None
    s, e = fit
    payload = dict(job.get("stage_payload") or {}) if isinstance(job.get("stage_payload"), Mapping) else {}
    history = list((payload.get("autofit") or {}).get("history") or [])
    history.append({"from": [float(job["clip_start_sec"]), float(job["clip_end_sec"])], "to": [s, e], "reason": reason})
    jobs_repo.update(
        conn,
        int(job["id"]),
        {"clip_start_sec": s, "clip_end_sec": e},
        merge_payload={"autofit": {"count": len(history), "history": history[-5:]}},
    )
    if tr is not None:
        s_sid, e_sid = clip_sids(tr, s, e)
        if s_sid is not None and e_sid is not None:
            clips_repo.set_clip_sids(conn, int(job["id"]), s_sid, e_sid)
    log.info("clip job %s: границы %s → %.2f–%.2f (%s)", job.get("id"), (job["clip_start_sec"], job["clip_end_sec"]), s, e, reason)
    return fit


def autofix_qc(conn: Any, job: Row) -> Row | None:
    """Брак, который ролик чинит сам после QC (решение владельца 29.10: «видишь — поправляй автоматически»):
    длина — перерезать по фразам; шаблонный авторский текст — написать заново и перерендерить."""
    return autofix_duration(conn, job) or autofix_author_text(conn, job)


def autofix_author_text(conn: Any, job: Row) -> Row | None:
    """Провалена только оригинальность из-за шаблонного текста (модель не ответила по схеме) — стереть
    шаблон и перезапустить со стадии author_script (не больше CLIP_AUTOFIX_MAX раз)."""
    from autoshorts.repos import jobs as jobs_repo
    from autoshorts.services import jobs as jobs_service

    if job.get("kind") != "clip" or list(job.get("qc_failed_checks") or []) != ["originality"]:
        return None
    # строка из jobs_repo.get/update без этих колонок — читаем их сами (тест брал ролик целиком и не заметил)
    row = conn.execute("SELECT originality, author_script FROM jobs WHERE id = %s", (int(job["id"]),)).fetchone() or {}
    orig = row.get("originality") if isinstance(row.get("originality"), Mapping) else {}
    fails = list((orig or {}).get("fails") or [])
    script = row.get("author_script") if isinstance(row.get("author_script"), Mapping) else {}
    if fails != ["author_text"] or (script or {}).get("mode") not in ("heuristic", "mock"):
        return None
    payload = job.get("stage_payload") if isinstance(job.get("stage_payload"), Mapping) else {}
    tries = int(((payload or {}).get("autofix_author") or {}).get("count") or 0)
    if tries >= CLIP_AUTOFIX_MAX:
        return None
    clips_repo.set_author_script(conn, int(job["id"]), None)  # шаблон стёрт — стадия напишет текст заново
    jobs_repo.update(conn, int(job["id"]), {}, merge_payload={"autofix_author": {"count": tries + 1}})
    fresh = jobs_service._require_job(conn, int(job["id"]))
    return jobs_service.restart_from_stage(
        conn, fresh, "author_script", allowed=(str(fresh["status"]),), fields={"qc_failed_checks": [], "qc_warnings": []}
    )


def autofix_duration(conn: Any, job: Row) -> Row | None:
    """После QC: провалена только длина — перерезать ролик по новым границам и перезапустить с начала
    (не больше CLIP_AUTOFIX_MAX раз). Возвращает перезапущенный ролик или None."""
    from autoshorts.services import jobs as jobs_service

    if job.get("kind") != "clip" or list(job.get("qc_failed_checks") or []) != ["duration"]:
        return None
    payload = job.get("stage_payload") if isinstance(job.get("stage_payload"), Mapping) else {}
    if int(((payload or {}).get("autofit") or {}).get("count") or 0) >= CLIP_AUTOFIX_MAX:
        return None
    if refit_clip_job(conn, job, "проверка длины: 20–58 с") is None:
        return None
    fresh = jobs_service._require_job(conn, int(job["id"]))
    return jobs_service.restart_from_stage(
        conn, fresh, "clip_fetch", allowed=(str(fresh["status"]),), fields={"qc_failed_checks": [], "qc_warnings": []}
    )


def clip_sids(tr: Transcript, start: float, end: float) -> tuple[int | None, int | None]:
    sents = [s for s in tr.sentences if s.e > start + 0.05 and s.s < end - 0.05]
    if not sents:
        return None, None
    return sents[0].sid, sents[-1].sid


# ============================================================================ кандидаты


def _hook_type(value: Any) -> str | None:
    from autoshorts.clipping.schemas import HookType

    return value if isinstance(value, str) and value in HookType._value2member_map_ else None


def _flags(values: Any) -> list[str]:
    from autoshorts.api.schemas.clipping import CandidateFlag

    return [v for v in (values or []) if isinstance(v, str) and v in CandidateFlag._value2member_map_]


def _block_reasons(flags: Sequence[str], unblocked: Sequence[str]) -> list[str]:
    from autoshorts.api.schemas.clipping import BLOCKING_CANDIDATE_FLAGS

    return [f for f in flags if f in BLOCKING_CANDIDATE_FLAGS and f not in set(unblocked)]


def _f(v: Any) -> float | None:
    return None if v is None else float(v)


def candidate_view(row: Row) -> dict[str, Any]:
    """Строка `clip_candidates` → схема `ClipCandidate`."""
    from autoshorts.api.schemas.clipping import CANDIDATE_ORIGIN_LABELS

    feats = dict(row.get("features") or {})
    score_parts = feats.pop("score_parts", None) or {}
    hits = feats.pop("forbidden_hits", None) or []
    for key in ("origin", "detail_status"):
        feats.pop(key, None)
    llm = row.get("llm_scores") or {}
    keys = ("hook", "standalone", "payoff", "emotion", "niche_fit", "novelty")
    llm_scores = (
        {k: float(llm[k]) for k in keys} if all(isinstance(llm.get(k), int | float) for k in keys) else None
    )
    snap = row.get("snap") or {}
    snap_out = None
    if isinstance(snap, dict) and isinstance(snap.get("w0"), int) and isinstance(snap.get("w1"), int):
        snap_out = {
            "w0": int(snap["w0"]),
            "w1": int(snap["w1"]),
            "lead_sec": float(snap.get("lead_sec") or 0.0),
            "tail_sec": float(snap.get("tail_sec") or 0.0),
            "raw_start_sec": _f(snap.get("raw_start_sec")),
            "raw_end_sec": _f(snap.get("raw_end_sec")),
        }
    flags = _flags(row.get("content_flags"))
    unblocked = _flags(row.get("unblocked_flags"))
    reasons = _block_reasons(flags, unblocked)
    origin = row.get("origin") or ("manual" if row.get("created_by_kind") == "manual" else "llm")
    return {
        "id": int(row["id"]),
        "source_id": int(row["source_id"]),
        "start_sec": float(row["start_sec"]),
        "end_sec": float(row["end_sec"]),
        "duration_sec": round(float(row["end_sec"]) - float(row["start_sec"]), 2),
        "start_sid": row.get("start_sid"),
        "end_sid": row.get("end_sid"),
        "hook_sid": row.get("hook_sid"),
        "start_word_idx": row.get("start_word_idx"),
        "end_word_idx": row.get("end_word_idx"),
        "snap": snap_out,
        "final_score": _f(row.get("final_score")),
        "trend_prior": _f(row.get("trend_prior")),
        "score_parts": {k: float(v) for k, v in score_parts.items() if isinstance(v, int | float)},
        "llm_scores": llm_scores,
        "features": feats,
        "hook_type": _hook_type(row.get("hook_type")),
        "reason": row.get("reason") or None,
        "transcript_excerpt": row.get("transcript_excerpt") or None,
        "title": row.get("title") or None,
        "hook_text": row.get("hook_text") or None,
        "content_flags": flags,
        "forbidden_hits": [
            {"phrase": str(h.get("phrase")), "start": int(h.get("start") or 0), "end": int(h.get("end") or 0)}
            for h in hits
            if isinstance(h, dict) and h.get("phrase")
        ],
        "unblocked_flags": unblocked,
        "blocked": bool(reasons),
        "block_reasons": reasons,
        "needs_context": bool(row.get("needs_context")),
        "status": row["status"],
        "created_by_kind": row["created_by_kind"],
        "origin": origin,
        "origin_label": CANDIDATE_ORIGIN_LABELS.get(origin, origin),
        "notes": row.get("notes"),
        "job_id": row.get("job_id"),
        "superseded": row.get("superseded_at") is not None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def selection_view(row: Row) -> dict[str, Any] | None:
    from autoshorts.api.schemas.sources import SourceSelectionInfo

    raw = row.get("selection")
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        return SourceSelectionInfo.model_validate(raw).model_dump(mode="json")
    except ValueError:
        log.warning("source %s: selection summary does not match the schema", row.get("id"))
        return None


def list_candidates(
    conn: Any, source_id: int, *, statuses: Sequence[str] | None = None, include_superseded: bool = False
) -> dict[str, Any]:
    src = require_source(conn, source_id)
    rows = clips_repo.list_candidates(
        conn, source_id, statuses=statuses, include_superseded=include_superseded
    )
    return {"items": [candidate_view(r) for r in rows], "selection": selection_view(src)}


def get_candidate(conn: Any, candidate_id: int) -> dict[str, Any]:
    return candidate_view(_require_candidate(conn, candidate_id))


def _require_candidate(conn: Any, candidate_id: int, *, for_update: bool = False) -> Row:
    row = clips_repo.get_candidate(conn, candidate_id, for_update=for_update)
    if row is None:
        raise NotFound("clip_candidate", "Кандидат нарезки не найден")
    return row


def _score_mapping(conn: Any) -> dict[str, Any] | None:
    """Веса итоговой оценки из настроек (`clip_score_weights`) в форме `ScoringConfig.from_mapping`."""
    try:
        from autoshorts.services.settings import get_runtime_settings

        weights = get_runtime_settings(conn).clip_score_weights
        return {"final_weights": weights.model_dump() if hasattr(weights, "model_dump") else dict(weights)}
    except Exception:  # noqa: BLE001 - нет настроек: веса по умолчанию
        return None


def _scoring_config(conn: Any) -> Any:
    from autoshorts.clipping.scoring import ScoringConfig

    return ScoringConfig.from_mapping(_score_mapping(conn))


@dataclass
class _Bounds:
    start: float
    end: float
    w0: int
    w1: int
    lead: float
    tail: float
    raw_start: float
    raw_end: float


def resolve_bounds(
    tr: Transcript,
    *,
    start_word_idx: int | None,
    end_word_idx: int | None,
    start_sec: float | None,
    end_sec: float | None,
    snap: bool,
    current: tuple[float, float] | None = None,
) -> _Bounds:
    """Границы кандидата: слово или секунда на каждый край, снаппинг в тишину (§6.3.5).

    Клик по слову (индекс) берёт ровно это слово и лишь уводит разрез в тишину; секунда ищет
    естественный стык в окне ±1,5 с. Нет края в запросе — берётся текущий (`current`).
    """
    from autoshorts.clipping.snapping import SEARCH_WINDOW, snap_clip_to_words

    words = tr.words
    if not words:
        raise Conflict("В транскрипте нет слов", code="source_not_ready")
    n = len(words)
    for name, idx in (("start_word_idx", start_word_idx), ("end_word_idx", end_word_idx)):
        if idx is not None and not 0 <= idx < n:
            raise _invalid(["body", name], f"Нет слова с индексом {idx} (слов {n})", "word_index")
    if start_word_idx is not None:
        raw_s, win_s = words[start_word_idx].s, SNAP_WORD_WINDOW
    elif start_sec is not None:
        raw_s, win_s = float(start_sec), SEARCH_WINDOW
    elif current is not None:
        raw_s, win_s = float(current[0]), SEARCH_WINDOW
    else:
        raise _invalid(["body", "start_sec"], "Нужно начало: start_word_idx или start_sec", "edge_missing")
    if end_word_idx is not None:
        raw_e, win_e = words[end_word_idx].e, SNAP_WORD_WINDOW
    elif end_sec is not None:
        raw_e, win_e = float(end_sec), SEARCH_WINDOW
    elif current is not None:
        raw_e, win_e = float(current[1]), SEARCH_WINDOW
    else:
        raise _invalid(["body", "end_sec"], "Нужен конец: end_word_idx или end_sec", "edge_missing")
    if raw_e <= raw_s:
        raise _invalid(["body", "end_sec"], "Конец клипа раньше начала", "edge_order")
    dur = tr.duration or None
    if snap:
        a = snap_clip_to_words(words, raw_s, raw_e, search_window=win_s, duration=dur)
        b = snap_clip_to_words(words, raw_s, raw_e, search_window=win_e, duration=dur)
        start, w0, lead = a.start, a.w0, a.lead
        end, w1, tail = b.end, b.w1, b.tail
    else:
        span = word_span(words, raw_s, raw_e)
        if span is None:
            raise _invalid(["body", "end_sec"], "В отрезке нет ни одного слова", "no_words")
        w0, w1 = span
        start, end = raw_s, raw_e
        lead, tail = max(0.0, words[w0].s - start), max(0.0, end - words[w1].e)
    if w1 < w0 or end <= start:
        raise _invalid(["body", "end_sec"], "Конец клипа раньше начала", "edge_order")
    length = end - start
    if not CANDIDATE_MIN_SEC <= length <= CANDIDATE_MAX_SEC:
        raise _invalid(
            ["body", "end_sec"],
            f"Длина фрагмента {length:.1f} с вне {CANDIDATE_MIN_SEC:g}–{CANDIDATE_MAX_SEC:g} с",
            "clip_length",
        )
    return _Bounds(
        round(start, 3), round(end, 3), int(w0), int(w1), round(lead, 3), round(tail, 3), raw_s, raw_e
    )


def _excerpt(words: Sequence[Word], w0: int, w1: int) -> str:
    text = " ".join(w.w for w in words[w0 : w1 + 1])
    return text if len(text) <= EXCERPT_MAX_CHARS else text[: EXCERPT_MAX_CHARS - 1].rstrip() + "…"


def _snap_json(b: _Bounds) -> dict[str, Any]:
    return {
        "w0": b.w0,
        "w1": b.w1,
        "lead_sec": b.lead,
        "tail_sec": b.tail,
        "raw_start_sec": round(b.raw_start, 3),
        "raw_end_sec": round(b.raw_end, 3),
    }


def _rescore(
    conn: Any,
    tr: Transcript,
    b: _Bounds,
    *,
    niche: ClipNiche | None,
    title: str,
    hook_text: str,
    origin: str,
    llm_scores: Mapping[str, float] | None = None,
    hook_type: str | None = None,
    llm_flags: Sequence[str] = (),
    needs_context: bool = False,
    unblocked: Sequence[str] = (),
) -> dict[str, Any]:
    """Признаки, оценка и флаги кандидата после правки границ (движок: score + словарь ниши)."""
    from autoshorts.clipping.schemas import FORBIDDEN_PHRASE_FLAG
    from autoshorts.clipping.scoring import ClipCandidate, apply_niche_filter, score_candidate

    s_sid, e_sid = sid_for_word(tr, b.w0), sid_for_word(tr, b.w1)
    c = ClipCandidate(
        start_sid=s_sid,
        end_sid=e_sid,
        hook_sid=s_sid,
        start_sec=b.start,
        end_sec=b.end,
        title=title or "",
        hook_text=hook_text or "",
        hook_type=hook_type or "other",
        llm_scores=dict(llm_scores or {}),
        needs_context=needs_context,
        # флаги LLM остаются (medical/guarantee — до явной разблокировки), словарь ниши — заново
        content_flags=[f for f in llm_flags if f != FORBIDDEN_PHRASE_FLAG],
        origin=origin,  # type: ignore[arg-type]
        transcript_excerpt=_excerpt(tr.words, b.w0, b.w1),
        unblocked_flags=list(unblocked),
        raw_start_sec=b.raw_start,
        raw_end_sec=b.raw_end,
    )
    score_candidate(c, tr.words, cfg=_scoring_config(conn))
    apply_niche_filter(c, niche, keyword_flags=True)  # type: ignore[arg-type]
    row = c.to_row()
    return {
        "start_sid": s_sid,
        "end_sid": e_sid,
        "start_sec": b.start,
        "end_sec": b.end,
        "transcript_excerpt": row["transcript_excerpt"],
        "features": row["features"],
        "trend_prior": row["trend_prior"],
        "final_score": row["final_score"],
        "content_flags": row["content_flags"],
        "start_word_idx": b.w0,
        "end_word_idx": b.w1,
        "snap": _snap_json(b),
    }


def create_candidate(conn: Any, source_id: int, body: Any, caller: Caller) -> dict[str, Any]:
    """Ручной кандидат (по словам или секундам, со снаппингом)."""
    from autoshorts.services import audit

    src = require_source(conn, source_id)
    ctx = source_context(conn, src)
    tr = load_transcript(ctx)
    b = resolve_bounds(
        tr,
        start_word_idx=body.start_word_idx,
        end_word_idx=body.end_word_idx,
        start_sec=body.start_sec,
        end_sec=body.end_sec,
        snap=body.snap,
    )
    niche = niche_profile(conn, src.get("niche_id"))
    fields = _rescore(
        conn, tr, b, niche=niche, title=body.title or "", hook_text=body.hook_text or "", origin="manual"
    )
    fields.update(
        {
            "source_id": source_id,
            "hook_sid": fields["start_sid"],
            "title": body.title,
            "hook_text": body.hook_text,
            "notes": body.notes,
            "status": "proposed",
            "created_by_kind": "manual",
            "origin": "manual",
            "updated_by": caller.user_id,
            "operator_edits": {"created": {"at": _now_iso(), "by": caller.user_id}},
        }
    )
    row = clips_repo.insert_candidate(conn, fields)
    audit.record(
        conn,
        caller.actor,
        "clip_candidate.create",
        "clip_candidate",
        int(row["id"]),
        summary=f"источник {source_id}: {b.start:.1f}–{b.end:.1f} с",
    )
    return candidate_view(row)


def patch_candidate(conn: Any, candidate_id: int, body: Any, caller: Caller) -> dict[str, Any]:
    """Правка границ (снаппинг и признаки пересчитываются), статуса, текстов, флагов, заметок."""
    from autoshorts.services import audit

    row = _require_candidate(conn, candidate_id, for_update=True)
    changed = body.model_fields_set
    fields: dict[str, Any] = {}
    diff: dict[str, Any] = {}
    edges = {"start_word_idx", "end_word_idx", "start_sec", "end_sec"} & changed
    edits = dict(row.get("operator_edits") or {})
    unblocked = list(row.get("unblocked_flags") or [])
    if body.unblock_flags:
        add = [str(f) for f in body.unblock_flags if str(f) not in unblocked]
        unblocked += add
        if add:
            diff["unblock_flags"] = add
    if body.block_flags:
        drop = {str(f) for f in body.block_flags}
        if drop & set(unblocked):
            diff["block_flags"] = sorted(drop & set(unblocked))
        unblocked = [f for f in unblocked if f not in drop]
    if unblocked != list(row.get("unblocked_flags") or []):
        fields["unblocked_flags"] = unblocked
    title = body.title if "title" in changed else row.get("title")
    hook_text = body.hook_text if "hook_text" in changed else row.get("hook_text")
    if edges or {"title", "hook_text"} & changed:
        src = require_source(conn, int(row["source_id"]))
        ctx = source_context(conn, src)
        tr = load_transcript(ctx)
        if edges:
            b = resolve_bounds(
                tr,
                start_word_idx=body.start_word_idx,
                end_word_idx=body.end_word_idx,
                start_sec=body.start_sec,
                end_sec=body.end_sec,
                snap=body.snap,
                current=(float(row["start_sec"]), float(row["end_sec"])),
            )
            diff["range"] = {
                "from": [float(row["start_sec"]), float(row["end_sec"])],
                "to": [b.start, b.end],
            }
            history = list(edits.get("bounds") or [])[-19:]
            history.append({"at": _now_iso(), "by": caller.user_id, **diff["range"]})
            edits["bounds"] = history
            fields["operator_edits"] = edits
        else:
            snap = row.get("snap") or {}
            w0 = row.get("start_word_idx")
            w1 = row.get("end_word_idx")
            if w0 is None or w1 is None:
                span = word_span(tr.words, float(row["start_sec"]), float(row["end_sec"])) or (0, 0)
                w0, w1 = span
            b = _Bounds(
                float(row["start_sec"]),
                float(row["end_sec"]),
                int(w0),
                int(w1),
                float(snap.get("lead_sec") or 0.0),
                float(snap.get("tail_sec") or 0.0),
                float(snap.get("raw_start_sec") or row["start_sec"]),
                float(snap.get("raw_end_sec") or row["end_sec"]),
            )
        niche = niche_profile(conn, src.get("niche_id"))
        rescored = _rescore(
            conn,
            tr,
            b,
            niche=niche,
            title=title or "",
            hook_text=hook_text or "",
            origin=row.get("origin") or "manual",
            llm_scores=row.get("llm_scores") or {},
            hook_type=row.get("hook_type"),
            llm_flags=row.get("content_flags") or [],
            needs_context=bool(row.get("needs_context")),
            unblocked=unblocked,
        )
        if not edges:
            for key in (
                "start_sec",
                "end_sec",
                "start_word_idx",
                "end_word_idx",
                "snap",
                "start_sid",
                "end_sid",
            ):
                rescored.pop(key, None)
        fields.update(rescored)
    for name in ("title", "hook_text", "notes"):
        if name in changed:
            fields[name] = getattr(body, name)
            diff[name] = True
    if "status" in changed and body.status is not None:
        status = str(body.status)
        if status == "used":
            raise _invalid(["body", "status"], "Статус used ставится созданием clip-job", "status_used")
        live_job = row.get("job_id") is not None and row.get("job_status") not in clips_repo.DEAD_JOB_STATUSES
        if row["status"] == "used" and live_job and status != "used":
            raise Conflict(
                "Кандидат в работе: сначала отмените или отклоните его ролик",
                code="clip_candidate_in_use",
                details={"clip_candidate_id": candidate_id, "job_id": row.get("job_id")},
            )
        fields["status"] = status
        diff["status"] = {"from": row["status"], "to": status}
    if not fields:
        return candidate_view(row)
    fields["updated_by"] = caller.user_id
    updated = clips_repo.update_candidate(conn, candidate_id, fields)
    assert updated is not None
    audit.record(
        conn,
        caller.actor,
        "clip_candidate.update",
        "clip_candidate",
        candidate_id,
        summary=f"кандидат {candidate_id}",
        diff=diff or {"fields": sorted(fields)},
    )
    return candidate_view(updated)


# ---------------------------------------------------------------- повторный отбор


def _selection_params(src: Row, body: Any | None) -> dict[str, Any]:
    from autoshorts.api.schemas.sources import SourceOptions

    base = {k: v for k, v in dict(src.get("options") or {}).items() if k in SourceOptions.model_fields}
    if body is not None:
        for name in (
            "selector",
            "target_clip_sec",
            "min_clip_sec",
            "max_clip_sec",
            "max_candidates",
            "target_count",
            "detail_pass",
            "tighten_pauses",
        ):
            value = getattr(body, name, None)
            if value is not None:
                base[name] = value.value if hasattr(value, "value") else value
    try:
        return SourceOptions.model_validate(base).model_dump(mode="json")
    except ValueError as exc:
        raise _invalid(["body"], f"Параметры отбора несовместимы: {exc}", "clip_length_order") from exc


def request_selection(conn: Any, source_id: int, body: Any, caller: Caller) -> int:
    """`POST /sources/{id}/select` → задача `source_select` (light, LLM платно через бюджет)."""
    from autoshorts.services import audit
    from autoshorts.services.jobs import _budget_paused
    from autoshorts.services.tasks import enqueue

    src = require_source(conn, source_id, for_update=True)
    ctx = source_context(conn, src)
    if src["status"] not in ("ready", "selecting") or ctx.transcript_path is None:
        raise Conflict(
            "Источник ещё не готов к отбору",
            code="source_not_ready",
            details={"source_id": source_id, "status": src["status"]},
        )
    params = _selection_params(src, body)
    if params.get("selector") != "heuristic" and llm_configured() and _budget_paused(conn):
        raise BudgetPaused(details={"kind": "source_select"})
    if body.niche_id:
        if clips_repo.niche_row(conn, body.niche_id) is None:
            raise NotFound("niche", "Ниша не найдена")
    if body.save_as_default:
        clips_repo.merge_source_options(conn, source_id, params)
    res = enqueue(
        conn,
        SELECT_TASK_KIND,
        resource_class="light",
        max_attempts=3,
        source_id=source_id,
        payload={
            "source_id": source_id,
            "params": params,
            "niche_id": body.niche_id,
            "requested_by": caller.user_id,
        },
        dedup_key=f"source:{int(source_id)}:select",
    )
    audit.record(
        conn,
        caller.actor,
        "source.select",
        "source",
        source_id,
        summary=f"повторный отбор ({params.get('selector')})",
        diff={"params": params, "task_id": int(res.task["id"])},
    )
    return int(res.task["id"])


def _engine_candidate_row(c: Any, tr: Transcript) -> dict[str, Any]:
    row = c.to_row()
    span = word_span(tr.words, float(c.start_sec), float(c.end_sec))
    if span is not None:
        w0, w1 = span
        row["start_word_idx"], row["end_word_idx"] = w0, w1
        row["snap"] = {
            "w0": w0,
            "w1": w1,
            "lead_sec": round(max(0.0, tr.words[w0].s - float(c.start_sec)), 3),
            "tail_sec": round(max(0.0, float(c.end_sec) - tr.words[w1].e), 3),
            "raw_start_sec": None if c.raw_start_sec is None else round(float(c.raw_start_sec), 3),
            "raw_end_sec": None if c.raw_end_sec is None else round(float(c.raw_end_sec), 3),
        }
    row["origin"] = c.origin
    row["unblocked_flags"] = list(c.unblocked_flags or [])
    return row


def _energy(src: Row, duration: float) -> Any:
    """Огибающая громкости из peaks.json источника (признак «энергии» в скоринге); нет — None."""
    from autoshorts.clipping.features import energy_from_peaks

    path = _resolve_media(src.get("peaks_path"), source_dir(src)) if src.get("peaks_path") else None
    if path is None:
        path = _resolve_media("peaks.json", source_dir(src))
    if path is None or duration <= 0:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        peaks = data.get("peaks") if isinstance(data, dict) and data.get("peaks") is not None else data
        if isinstance(peaks, dict):
            peaks = peaks.get("data") or []
        channel = peaks[0] if peaks and isinstance(peaks[0], list) else peaks
        return energy_from_peaks({"data": channel}, duration)
    except (OSError, ValueError, TypeError, IndexError):
        log.warning("source %s: peaks unreadable for energy", src.get("id"), exc_info=True)
        return None


def _clip_frames_image(src: Row, sctx: SourceContext, tr: Transcript, s_sid: int, e_sid: int) -> str | None:
    """Плитка кадров фрагмента для авторского слоя (data-URL) или None, если прокси нет или ffmpeg не смог."""
    from autoshorts.pipeline.scenes import frames_sheet

    proxy = _proxy_path(src, sctx)
    if proxy is None or not tr.sentences:
        return None
    a = max(0, min(int(s_sid), len(tr.sentences) - 1))
    b = max(a, min(int(e_sid), len(tr.sentences) - 1))
    try:
        return frames_sheet(proxy, tr.sentences[a].s, tr.sentences[b].e)
    except Exception:  # noqa: BLE001 - кадры вспомогательные, текст важнее
        log.warning("job frames for author script failed", exc_info=True)
        return None


def _proxy_path(src: Row, sctx: SourceContext) -> Path | None:
    prepared = sctx.prepared
    p = prepared.path("proxy") if prepared is not None else None
    if p is None or not p.is_file():
        p = _resolve_media(src.get("proxy_path"), sctx.dir) if src.get("proxy_path") else None
    return p if p is not None and p.is_file() else None


def _scene_cuts(src: Row, sctx: SourceContext, duration: float, *, detect: bool) -> list[float]:
    """Склейки сцен источника: из scenes.json (пишется в prepare); если файла нет и `detect` —
    считаем сейчас по прокси и сохраняем (старые источники, подготовленные до появления режима «сцена»)."""
    from autoshorts.pipeline.scenes import SCENES_NAME, detect_scene_cuts, read_scenes, write_scenes

    prepared = sctx.prepared
    path = prepared.path("scenes") if prepared is not None else None
    if path is None or not path.is_file():
        path = sctx.dir / SCENES_NAME
    cuts = read_scenes(path) if path.is_file() else []
    if cuts or path.is_file() or not detect:
        return cuts
    proxy = _proxy_path(src, sctx)
    if proxy is None:
        return []
    cuts = detect_scene_cuts(proxy, duration=duration or None)
    try:
        write_scenes(sctx.dir, cuts)
    except OSError:
        log.warning("source %s: scenes.json not written", src.get("id"), exc_info=True)
    return cuts


def _pipeline_advance() -> Callable[..., Any] | None:
    """Следующий шаг конвейера источника (M5-A `services.sources.advance`) или None."""
    try:
        from autoshorts.services.sources import advance
    except Exception:  # noqa: BLE001 - без конвейера M5-A источник просто становится ready
        return None
    return advance


def run_source_select(ctx: TaskContext) -> dict[str, Any]:
    """Задача `source_select`: отбор (LLM или эвристика); кандидаты и сводка — в транзакции успеха.

    Ставится конвейером источника M5-A (payload.chain — следующие шаги, после них `ready`) и
    повторным отбором `POST /sources/{id}/select`. Окончательный сбой отбора в конвейере переводит
    источник в `failed`; при повторном отборе готовый источник остаётся `ready` с прежними кандидатами.
    """
    try:
        return _run_source_select(ctx)
    except PermanentError as exc:
        source_id = int(ctx.source_id or ctx.payload.get("source_id") or 0)
        if source_id:
            _mark_select_failed(source_id, f"Отбор моментов: {exc}")
        raise


def _mark_select_failed(source_id: int, message: str) -> None:
    try:
        with _connection() as conn:
            conn.execute(
                "UPDATE sources SET status = 'failed', error = %(e)s, updated_at = now()"
                " WHERE id = %(id)s AND status = 'selecting'",
                {"id": source_id, "e": message[:2000]},
            )
    except Exception:  # noqa: BLE001
        log.warning("source %s: failed status not written", source_id, exc_info=True)


def _run_source_select(ctx: TaskContext) -> dict[str, Any]:
    from autoshorts.clipping.selection import SelectionConfig, select_clips

    payload = ctx.payload
    source_id = int(ctx.source_id or payload.get("source_id") or 0)
    if not source_id:
        raise PermanentError("source_select without source_id", error_class="bad_task")
    chain = [str(s) for s in payload.get("chain") or []]
    with _connection() as conn:
        src = clips_repo.get_source(conn, source_id)
        if src is None or src.get("deleted_at") is not None:
            return {"source_id": source_id, "skipped": "source_gone"}
        sctx = source_context(conn, src)
        if sctx.transcript_path is None:
            raise PermanentError("У источника нет транскрипта", error_class="transcript_missing")
        tr = load_transcript(sctx)
        niche = niche_profile(conn, payload.get("niche_id") or src.get("niche_id"))
        try:
            params = _selection_params({"options": payload.get("params") or src.get("options") or {}}, None)
        except ValidationFailed:
            params = _selection_params({"options": {}}, None)
        scoring = _score_mapping(conn)
    selector = str(params.get("selector") or "auto")
    client, mode = llm_client("select", selector)
    warnings: list[str] = []
    fallback = False
    if selector == "llm" and client is None:
        warnings.append("llm_not_configured")
        fallback = True
    cfg = SelectionConfig.from_options(params, scoring, niche=niche)  # type: ignore[arg-type]
    paid = PaidCalls(
        ctx.budget, provider="openai_compat", estimate_usd=SELECT_ESTIMATE_USD, enabled=mode == "llm"
    )
    duration = float(tr.duration or src.get("duration_sec") or 0.0)
    energy = _energy(src, duration)
    scene_mode = cfg.mode == "scene"
    # склейки нужны и в talk-режиме (маркеры в промпте), но считать их на лету стоит только для сцен
    cfg.scene_cuts = _scene_cuts(src, sctx, duration, detect=scene_mode)
    frames = None
    if scene_mode and client is not None and bool(getattr(niche, "frames_in_detail", True)):
        from autoshorts.pipeline.scenes import frames_provider

        frames = frames_provider(_proxy_path(src, sctx), max_calls=cfg.max_detail)
    ctx.progress(0.05)
    with paid:
        res = select_clips(
            tr,
            llm=client,
            niche=niche,  # type: ignore[arg-type]
            config=cfg,
            energy=energy,
            on_event=paid.on_event,
            frames=frames,
        )
    ctx.check_cancelled()
    if client is not None and res.mode == "heuristic":
        fallback = True
    rows = [_engine_candidate_row(c, tr) for c in res.candidates]
    summary = {
        "mode": res.mode,
        "label": res.label,
        "selection_mode": cfg.mode,
        "scene_cuts": len(cfg.scene_cuts),
        "frames_in_detail": frames is not None,
        "llm_configured": llm_configured(),
        "fallback": fallback,
        "params": params,
        "n_candidates": len(res.candidates),
        "n_blocked": sum(1 for c in res.candidates if c.blocked),
        "n_requests": res.n_requests,
        "cost_usd": round(float(res.cost_usd), 6),
        "warnings": [*warnings, *res.warnings][:50],
        "task_id": ctx.task_id,
        "finished_at": _now_iso(),
    }

    advance = _pipeline_advance()

    def commit(conn: Any, _task: Row) -> None:
        summary["superseded"] = clips_repo.supersede_auto_candidates(conn, source_id)
        for r in rows:
            clips_repo.insert_candidate(conn, {**r, "source_id": source_id})
        status = clips_repo.set_source_selection(conn, source_id, summary, mark_ready=advance is None)
        if advance is not None:
            advance(conn, source_id, chain)  # следующий шаг конвейера M5-A или ready
        elif status is not None:
            from autoshorts.db.notify import publish_event
            from autoshorts.domain.events import EventType

            publish_event(conn, EventType.SOURCE_STATUS, {"source_id": source_id, "status": status})

    ctx.on_commit(commit)
    ctx.progress(1.0)
    return {
        "source_id": source_id,
        "mode": res.mode,
        "candidates": len(rows),
        "cost_usd": summary["cost_usd"],
    }


# ---------------------------------------------------------------- clip-job из кандидата и пачкой


def _job_spec(body: Any, **extra: Any) -> Any:
    from autoshorts.services.jobs import JobSpec

    return JobSpec(
        kind="clip",
        channel_id=body.channel_id,
        campaign_id=body.campaign_id,
        banner_variant_id=body.banner_variant_id,
        priority=body.priority,
        start=body.start,
        author_layer=body.author_layer.model_dump(mode="json") if body.author_layer else None,
        reframe_mode=str(
            body.reframe.mode.value if hasattr(body.reframe.mode, "value") else body.reframe.mode
        )
        if body.reframe
        else None,
        **extra,
    )


def _check_candidate_usable(cand: Row) -> None:
    flags = _flags(cand.get("content_flags"))
    reasons = _block_reasons(flags, _flags(cand.get("unblocked_flags")))
    if reasons:
        raise Conflict(
            "Кандидат заблокирован: снимите флаги или отредактируйте момент",
            code="clip_candidate_blocked",
            details={"clip_candidate_id": int(cand["id"]), "block_reasons": reasons},
        )


def create_job_from_candidate(conn: Any, candidate_id: int, body: Any, caller: Caller) -> Row:
    """«В рендер»: clip-job из кандидата (409 clip_candidate_blocked | clip_candidate_in_use, 423)."""
    from autoshorts.services import jobs as jobs_service

    cand = _require_candidate(conn, candidate_id, for_update=True)
    _check_candidate_usable(cand)
    niche_id = _source_niche(conn, cand["source_id"])
    spec = _job_spec(body, clip_candidate_id=candidate_id, niche_id=niche_id)
    return jobs_service.create_job(conn, spec, caller)


def _source_niche(conn: Any, source_id: Any) -> str | None:
    """Ниша клипа — ниша источника (кино, техно…), а не канала: на одном канале бывают ролики разных ниш,
    а от ниши зависят словарь запретов, тон авторского текста и хэштеги. Нет ниши или она в архиве —
    None, и create_job возьмёт нишу канала."""
    from autoshorts.repos import jobs as jobs_repo

    src = clips_repo.get_source(conn, int(source_id)) if source_id is not None else None
    niche_id = (src or {}).get("niche_id")
    if not niche_id:
        return None
    niche = jobs_repo.get_niche(conn, niche_id)
    return niche_id if niche is not None and niche["status"] != "archived" else None


def create_clips_batch(conn: Any, source_id: int, body: Any, caller: Caller) -> list[Row]:
    """Пачка clip-job из отрезков источника и (подправленных) кандидатов — одной транзакцией."""
    from autoshorts.services import jobs as jobs_service

    require_source(conn, source_id)
    niche_id = _source_niche(conn, source_id)
    out: list[Row] = []
    for i, item in enumerate(body.items):
        if item.candidate_id is not None:
            cand = _require_candidate(conn, item.candidate_id, for_update=True)
            if int(cand["source_id"]) != int(source_id):
                raise _invalid(
                    ["body", "items", i, "candidate_id"], "Кандидат другого источника", "source_mismatch"
                )
            _check_candidate_usable(cand)
            spec = _job_spec(
                item,
                clip_candidate_id=int(item.candidate_id),
                start_sec=item.start_sec,
                end_sec=item.end_sec,
                niche_id=niche_id,
            )
        else:
            spec = _job_spec(
                item, source_id=source_id, start_sec=item.start_sec, end_sec=item.end_sec, niche_id=niche_id
            )
        out.append(jobs_service.create_job(conn, spec, caller))
    return out


def on_clip_job_created(conn: Any, job: Row) -> None:
    """Хук `services.jobs.create_job` для нарезки: диапазон предложений клипа (повтор момента),
    авторский слой родителя (клон, адаптация) и текст оператора из `author_layer`.

    Работает в точке сохранения: сбой хука откатывает только его, создание ролика продолжается
    (диапазон предложений дозаполнит стадия clip_analyze)."""
    try:
        with conn.transaction():
            _on_clip_job_created(conn, job)
    except Exception:  # noqa: BLE001 - хук не должен ломать создание ролика
        log.warning("clip job %s: post-create hook failed", job.get("id"), exc_info=True)


def _on_clip_job_created(conn: Any, job: Row) -> None:
    job_id = int(job["id"])
    state = clips_repo.get_clip_state(conn, job_id)
    if state is None:
        return
    if job.get("parent_job_id") is None and job.get("clip_start_sec") is not None and job.get("clip_end_sec") is not None:
        fit = refit_clip_job(conn, job, "при создании: длина вне 20–58 с")
        if fit is not None:
            job = {**job, "clip_start_sec": fit[0], "clip_end_sec": fit[1]}
    s_sid = e_sid = None
    if job.get("clip_candidate_id") is not None:
        cand = clips_repo.get_candidate(conn, int(job["clip_candidate_id"]))
        same = (
            cand is not None
            and abs(float(cand["start_sec"]) - float(job["clip_start_sec"])) < 0.05
            and abs(float(cand["end_sec"]) - float(job["clip_end_sec"])) < 0.05
        )
        if same and cand.get("start_sid") is not None and cand.get("end_sid") is not None:
            s_sid, e_sid = int(cand["start_sid"]), int(cand["end_sid"])
    if s_sid is None and job.get("source_id") is not None:
        src = clips_repo.get_source(conn, int(job["source_id"]))
        if src is not None:
            sctx = source_context(conn, src)
            if sctx.transcript_path is not None:
                s_sid, e_sid = clip_sids(
                    load_transcript(sctx), float(job["clip_start_sec"]), float(job["clip_end_sec"])
                )
    if s_sid is not None and e_sid is not None:
        clips_repo.set_clip_sids(conn, job_id, s_sid, e_sid)
    if job.get("parent_job_id") is not None:
        clips_repo.copy_author_layer(conn, int(job["parent_job_id"]), job_id)
        state = clips_repo.get_clip_state(conn, job_id)
        data = (state or {}).get("author_script")
        if isinstance(data, dict):
            clips_repo.set_author_script(conn, job_id, {**data, "source": "copied"})
    layer = job.get("author_layer") or {}
    if (
        (state or {}).get("author_script") is None
        and layer.get("mode") == "operator_text"
        and isinstance(layer.get("script"), dict)
    ):
        clips_repo.set_author_script(
            conn,
            job_id,
            {
                "script": layer["script"],
                "mode": "operator",
                "label": MODE_LABELS["operator"],
                "needs_operator_edit": False,
                "blocked": False,
                "issues": [],
                "warnings": ["not_linted_yet"],
                "source": "operator",
                "updated_at": _now_iso(),
                "updated_by": job.get("created_by"),
            },
        )


# ============================================================================ авторский текст


def author_result_from_dict(data: Mapping[str, Any]) -> AuthorScriptResult:
    from autoshorts.clipping.author.script import AuthorScript, AuthorScriptResult, ScriptIssue

    issues = [
        ScriptIssue(
            str(i.get("code") or "issue"),
            i.get("severity") if i.get("severity") in ("info", "warn", "error") else "warn",
            str(i.get("where") or "script"),
            str(i.get("detail") or ""),
        )
        for i in data.get("issues") or []
        if isinstance(i, dict)
    ]
    mode = data.get("mode") if data.get("mode") in ("llm", "mock", "heuristic", "operator") else "operator"
    return AuthorScriptResult(
        AuthorScript.model_validate(data.get("script") or {}),
        mode,
        str(data.get("label") or MODE_LABELS.get(mode, mode)),
        issues,
        [str(w) for w in data.get("warnings") or []],
        [],
        float(data.get("cost_usd") or 0.0),
        int(data.get("latency_ms") or 0),
    )


def author_result_to_dict(
    res: AuthorScriptResult, *, source: str, updated_by: int | None = None, start_sid: int | None = None,
    end_sid: int | None = None,
) -> dict[str, Any]:  # fmt: skip
    data = res.to_dict()
    data.pop("events", None)
    data.update(
        {
            "source": source,
            "updated_at": _now_iso(),
            "updated_by": updated_by,
            "clip": {"start_sid": start_sid, "end_sid": end_sid},
        }
    )
    return data


def lint_author_script(
    script: AuthorScript | Mapping[str, Any],
    tr: Transcript,
    start_sid: int,
    end_sid: int,
    *,
    mode: str,
    label: str | None = None,
    niche: ClipNiche | None = None,
    warnings: Sequence[str] = (),
) -> AuthorScriptResult:
    """Проверка сценария (запреты ниши, гарантии, ссылки, пересказ).

    Режим текста не меняется, кроме одного случая: «текст оператора», который почти дословно
    повторяет шаблон без LLM (сохранён без правки, поправлена пара букв, вернулся из черновика в
    студию и ушёл как operator_text), остаётся шаблоном — `heuristic`. Иначе метка «Текст
    оператора» пропускала бы шаблон через отчёт об оригинальности (OWNER-DECISIONS п. 5).
    """
    from autoshorts.clipping.author.script import (
        AuthorScript,
        AuthorScriptResult,
        ScriptIssue,
        clip_sentences,
        fit_script_to_clip,
        is_unedited_template,
        lint_script,
    )

    sc = script if isinstance(script, AuthorScript) else AuthorScript.model_validate(dict(script))
    clip_text = " ".join(s.text for s in clip_sentences(tr, start_sid, end_sid))
    fixed, fit_warn = fit_script_to_clip(sc, start_sid=start_sid, end_sid=end_sid)
    if mode == "operator" and is_unedited_template(fixed):
        mode, label = "heuristic", MODE_LABELS["heuristic"]
        warnings = [*warnings, "operator_text_is_template"]
    issues = lint_script(
        fixed,
        clip_text,
        forbidden_phrases=list(niche.forbidden_phrases) if niche else [],
        allowed_domains=list(niche.allowed_domains) if niche else [],
    )
    if mode in ("heuristic", "mock"):
        issues.insert(
            0,
            ScriptIssue(
                "template_text",
                "warn",
                "script",
                "авторский текст — шаблон, а не комментарий: перепишите своими словами перед публикацией",
            ),
        )
    return AuthorScriptResult(
        fixed,
        mode,
        label or MODE_LABELS.get(mode, mode),
        issues,
        [*warnings, *fit_warn],  # type: ignore[arg-type]
    )


def draft_author_script(
    tr: Transcript,
    start_sid: int,
    end_sid: int,
    *,
    selector: str = "auto",
    niche: ClipNiche | None,
    rights: Mapping[str, Any],
    title: str = "",
    hook_text: str = "",
    budget: Any = None,
    frame_image: str | None = None,
    text_only: bool = False,
    quiz: tuple[str, str | None] | None = None,
    edit: bool = False,
    facts: tuple[str, str | None] | None = None,
) -> AuthorScriptResult:
    """Черновик авторского слоя: LLM (или mock) либо шаблон. Черновик LLM с запретом — ещё
    `AUTHOR_REGEN_ATTEMPTS` попыток; дальше возвращается как есть (blocked).
    `frame_image` — кадры фрагмента для модели (см. `pipeline.scenes.frames_sheet`)."""
    from autoshorts.clipping.author.script import generate_author_script

    film_ref = quiz_reference(rights, quiz or facts)
    client, mode = llm_client("author", selector)
    attempts = 1 + (AUTHOR_REGEN_ATTEMPTS if client is not None else 0)
    result = None
    for _ in range(attempts):
        paid = PaidCalls(
            budget, provider="openai_compat", estimate_usd=AUTHOR_ESTIMATE_USD, enabled=mode == "llm"
        )
        with paid:
            result = generate_author_script(
                tr,
                start_sid,
                end_sid,
                llm=client,
                title=title,
                hook_text=hook_text,
                niche=niche,
                owner=str(rights.get("owner_name") or ""),
                rights_type=rights.get("rights_type"),
                on_event=paid.on_event,
                frame_image=frame_image,
                text_only=text_only,
                quiz=quiz,
                film_ref=film_ref,
                edit=edit,
                facts=facts,
                verify=_verify_fact if mode == "llm" else None,
            )
        if not result.blocked or result.mode not in ("llm", "mock"):
            break
    assert result is not None
    if result.mode == "heuristic" and client is not None and _llm_fell_back(result):
        # основная модель ответила не по схеме (LLMSchemaError у flash-lite на длинных сценах, 29.10) — шаблон
        # «Остановлюсь здесь…» гарантированно провалит оригинальность; пробуем модель отбора (LLM_SELECT_MODEL)
        fb, _fb_mode = llm_client("select", selector)
        if fb is not None and getattr(fb, "model", None) != getattr(client, "model", None):
            paid = PaidCalls(budget, provider="openai_compat", estimate_usd=AUTHOR_ESTIMATE_USD, enabled=True)
            with paid:
                retry = generate_author_script(
                    tr, start_sid, end_sid, llm=fb, title=title, hook_text=hook_text, niche=niche,
                    owner=str(rights.get("owner_name") or ""), rights_type=rights.get("rights_type"),
                    on_event=paid.on_event, frame_image=frame_image, text_only=text_only, quiz=quiz,
                    film_ref=film_ref, edit=edit, facts=facts, verify=_verify_fact,
                )  # fmt: skip
            if retry.mode != "heuristic":
                return retry
    return result


def _llm_fell_back(res: Any) -> bool:
    return any(str(w).startswith("llm_failed_fallback_heuristic:") for w in getattr(res, "warnings", []) or [])


def _clip_range_sids(conn: Any, job: Row, state: Row, tr: Transcript) -> tuple[int, int]:
    s_sid, e_sid = state.get("clip_start_sid"), state.get("clip_end_sid")
    if s_sid is None or e_sid is None:
        s_sid, e_sid = clip_sids(tr, float(job["clip_start_sec"]), float(job["clip_end_sec"]))
        if s_sid is None or e_sid is None:
            raise PermanentError("В диапазоне клипа нет речи", error_class="clip_no_speech")
        clips_repo.set_clip_sids(conn, int(job["id"]), s_sid, e_sid)
    return int(s_sid), int(e_sid)


def _require_clip_job(conn: Any, job_id: int, *, for_update: bool = False) -> Row:
    from autoshorts.repos import jobs as jobs_repo

    job = jobs_repo.get(conn, job_id, for_update=for_update)
    if job is None:
        raise NotFound("job", "Ролик не найден")
    if job["kind"] != "clip":
        raise _invalid(["path", "job_id"], "Авторский слой есть только у нарезки", "kind_mismatch")
    return job


def author_editable(job: Row) -> bool:
    if job["status"] in AUTHOR_EDITABLE_STATUSES:
        return True
    return job["status"] == "processing" and job.get("stage") in AUTHOR_EDITABLE_STAGES


def _candidate_texts(conn: Any, job: Row) -> tuple[str, str]:
    title, hook = str(job.get("topic") or ""), ""
    if job.get("clip_candidate_id") is not None:
        cand = clips_repo.get_candidate(conn, int(job["clip_candidate_id"]))
        if cand is not None:
            title = str(cand.get("title") or title)
            hook = str(cand.get("hook_text") or "")
    layer = job.get("author_layer") or {}
    return str(layer.get("title_overlay") or title), str(layer.get("hook_text") or hook)


def author_summary(state: Row | None) -> dict[str, Any] | None:
    data = (state or {}).get("author_script")
    if not isinstance(data, dict):
        return None
    mode = data.get("mode") if data.get("mode") in ("llm", "mock", "heuristic", "operator") else None
    return {
        "mode": mode,
        "label": data.get("label"),
        "needs_operator_edit": bool(data.get("needs_operator_edit")),
        "blocked": bool(data.get("blocked")),
        "issues_count": len([i for i in data.get("issues") or [] if isinstance(i, dict)]),
    }


def _issues_out(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for i in data.get("issues") or []:
        if not isinstance(i, dict):
            continue
        sev = i.get("severity") if i.get("severity") in ("info", "warn", "error") else "warn"
        out.append(
            {
                "code": str(i.get("code") or "issue"),
                "severity": sev,
                "where": str(i.get("where") or "script"),
                "detail": str(i.get("detail") or ""),
            }
        )
    return out


def _script_body(data: Mapping[str, Any]) -> dict[str, Any] | None:
    from autoshorts.api.schemas.clipping import AuthorScriptBody

    raw = data.get("script")
    if not isinstance(raw, dict):
        return None
    try:
        return AuthorScriptBody.model_validate(raw).model_dump(mode="json")
    except ValueError:
        from autoshorts.clipping.author.script import AuthorScript

        return AuthorScriptBody.model_validate(AuthorScript.model_validate(raw).model_dump()).model_dump(
            mode="json"
        )


def author_script_view(conn: Any, job_id: int) -> dict[str, Any]:
    job = _require_clip_job(conn, job_id)
    state = clips_repo.get_clip_state(conn, job_id) or {}
    data = state.get("author_script") if isinstance(state.get("author_script"), dict) else {}
    task = clips_repo.active_task(conn, DRAFT_TASK_KIND, job_id=job_id)
    updated_at = data.get("updated_at")
    try:
        updated = datetime.fromisoformat(updated_at) if isinstance(updated_at, str) else None
    except ValueError:
        updated = None
    mode = data.get("mode") if data.get("mode") in ("llm", "mock", "heuristic", "operator") else None
    return {
        "job_id": job_id,
        "script": _script_body(data) if data else None,
        "mode": mode,
        "label": data.get("label") if data else None,
        "llm_configured": llm_configured(),
        "needs_operator_edit": bool(data.get("needs_operator_edit")),
        "blocked": bool(data.get("blocked")),
        "issues": _issues_out(data) if data else [],
        "warnings": [str(w) for w in data.get("warnings") or []] if data else [],
        "version": int(state.get("author_script_version") or 0),
        "drafting_task_id": int(task["id"]) if task else None,
        "editable": author_editable(job),
        "updated_at": updated,
        "updated_by": data.get("updated_by") if isinstance(data.get("updated_by"), int) else None,
    }


def put_author_script(conn: Any, job_id: int, body: Any, caller: Caller) -> dict[str, Any]:
    """Текст оператора → mode=operator, lint пересчитан (409 version_conflict | job_state_conflict)."""
    from autoshorts.services import audit

    job = _require_clip_job(conn, job_id, for_update=True)
    if not author_editable(job):
        raise JobStateConflict(
            "Авторский текст правится до озвучки: отправьте ролик на доработку",
            details={"job_id": job_id, "status": job["status"], "stage": job.get("stage"), "hint": "rework"},
        )
    state = clips_repo.get_clip_state(conn, job_id, for_update=True) or {}
    version = int(state.get("author_script_version") or 0)
    if body.expected_version is not None and int(body.expected_version) != version:
        raise VersionConflict(details={"job_id": job_id, "version": version})
    src = require_source(conn, int(job["source_id"]))
    sctx = source_context(conn, src)
    tr = load_transcript(sctx)
    s_sid, e_sid = _clip_range_sids(conn, job, state, tr)
    script = body.script.model_dump(mode="json")
    for i, ins in enumerate(script.get("inserts") or []):
        sid = int(ins.get("after_sid", -1))
        if not s_sid <= sid <= e_sid - 1:
            raise _invalid(
                ["body", "script", "inserts", i, "after_sid"],
                f"Вставка после предложения {sid} вне клипа ({s_sid}–{e_sid - 1})",
                "insert_out_of_clip",
            )
    niche = niche_profile(conn, job.get("niche_id") or src.get("niche_id"), product_id=job.get("product_id"))
    res = lint_author_script(script, tr, s_sid, e_sid, mode="operator", niche=niche)
    data = author_result_to_dict(
        res, source="operator", updated_by=caller.user_id, start_sid=s_sid, end_sid=e_sid
    )
    saved = clips_repo.set_author_script(conn, job_id, data, expected_version=version)
    if saved is None:
        raise VersionConflict(details={"job_id": job_id})
    audit.record(
        conn,
        caller.actor,
        "job.author_script",
        "job",
        job_id,
        summary=f"{job['video_uid']}: текст оператора",
        diff={"version": int(saved["author_script_version"]), "blocked": res.blocked},
    )
    return author_script_view(conn, job_id)


def request_author_draft(conn: Any, job_id: int, body: Any, caller: Caller) -> int:
    """Новый черновик авторского текста задачей `author_script_draft` (LLM платно; без ключа — шаблон)."""
    from autoshorts.services import audit
    from autoshorts.services.jobs import _budget_paused
    from autoshorts.services.tasks import enqueue

    job = _require_clip_job(conn, job_id, for_update=True)
    allowed = job["status"] in AUTHOR_EDITABLE_STATUSES or (
        job["status"] == "processing" and job.get("stage") in DRAFT_ALLOWED_STAGES
    )
    if not allowed:
        raise JobStateConflict(
            "Черновик пишется до стадии авторского текста или на доработке",
            details={"job_id": job_id, "status": job["status"], "stage": job.get("stage")},
        )
    state = clips_repo.get_clip_state(conn, job_id) or {}
    data = state.get("author_script") if isinstance(state.get("author_script"), dict) else {}
    if body.keep_operator_edits and data.get("mode") == "operator":
        raise Conflict(
            "У ролика текст оператора, а keep_operator_edits=true: черновик его не заменит",
            code="job_state_conflict",
            details={"job_id": job_id, "mode": "operator"},
        )
    selector = str(body.mode.value if hasattr(body.mode, "value") else body.mode)
    if selector != "heuristic" and llm_configured() and _budget_paused(conn):
        raise BudgetPaused(details={"kind": "author_script"})
    res = enqueue(
        conn,
        DRAFT_TASK_KIND,
        resource_class="light",
        max_attempts=3,
        priority=50,
        payload={"job_id": job_id, "selector": selector, "requested_by": caller.user_id},
        dedup_key=f"job:{job_id}:author_draft",
    )
    audit.record(
        conn,
        caller.actor,
        "job.author_script_draft",
        "job",
        job_id,
        summary=f"{job['video_uid']}: новый черновик ({selector})",
        diff={"task_id": int(res.task["id"])},
    )
    return int(res.task["id"])


def run_author_script_draft(ctx: TaskContext) -> dict[str, Any]:
    """Задача `author_script_draft`: черновик в `jobs.author_script` (роль «draft_task»)."""
    payload = ctx.payload
    job_id = int(payload.get("job_id") or 0)
    with _connection() as conn:
        job = _require_clip_job(conn, job_id)
        state = clips_repo.get_clip_state(conn, job_id) or {}
        src = require_source(conn, int(job["source_id"]))
        sctx = source_context(conn, src)
        tr = load_transcript(sctx)
        s_sid, e_sid = _clip_range_sids(conn, job, state, tr)
        niche = niche_profile(
            conn, job.get("niche_id") or src.get("niche_id"), product_id=job.get("product_id")
        )
        rights = rights_of(sctx)
        title, hook = _candidate_texts(conn, job)
        version = int(state.get("author_script_version") or 0)
        text_only = not channel_author_voice(conn, job) and channel_author_text(conn, job)
        quiz = quiz_answer(conn, job, rights, src)
        edit = channel_format(conn, job) == "edit"
        facts = film_answer(conn, job, rights, src)
    res = draft_author_script(
        tr,
        s_sid,
        e_sid,
        selector=str(payload.get("selector") or "auto"),
        niche=niche,
        rights=rights,
        title=title,
        hook_text=hook,
        budget=ctx.budget,
        text_only=text_only,
        quiz=quiz,
        edit=edit,
        facts=facts,
    )
    data = author_result_to_dict(
        res, source="draft_task", updated_by=payload.get("requested_by"), start_sid=s_sid, end_sid=e_sid
    )
    if quiz:
        data["format"], data["answer"] = "quiz", {"film": quiz[0], "year": quiz[1]}
    with _connection() as conn:
        job = _require_clip_job(conn, job_id, for_update=True)
        allowed = job["status"] in AUTHOR_EDITABLE_STATUSES or (
            job["status"] == "processing" and job.get("stage") in DRAFT_ALLOWED_STAGES
        )
        if not allowed:
            return {"job_id": job_id, "skipped": "job_moved_on"}
        saved = clips_repo.set_author_script(conn, job_id, data, expected_version=version)
    return {"job_id": job_id, "mode": res.mode, "blocked": res.blocked, "saved": saved is not None}


# ---------------------------------------------------------------- стадия author_script


BACKGROUNDS_DIR = "backgrounds"  # MEDIA_ROOT/backgrounds/<набор>/*.mp4 — фоны стиля «кадр в фигуре»


def channel_profile(conn: Any, job: Mapping[str, Any]) -> dict[str, Any]:
    from autoshorts.repos import jobs as jobs_repo

    ch = jobs_repo.get_channel(conn, str(job.get("channel_id") or ""))
    return dict((ch or {}).get("profile") or {})


def channel_author_voice(conn: Any, job: Mapping[str, Any]) -> bool:
    """Голос автора (нейроозвучка вступления, вставок и концовки). `profile.author_voice: false` — ролик
    без голоса: только сцена со своим звуком, субтитры, заголовок и баннер (решение владельца канала).
    Формат ролика решает раньше профиля: «за кадром» — всегда с голосом, кино-эдит — всегда без."""
    fmt = channel_format(conn, job)
    if fmt in ("facts", "edit"):
        return fmt == "facts"
    return bool(channel_profile(conn, job).get("author_voice", True))


def channel_format(conn: Any, job: Mapping[str, Any]) -> str:
    """Формат роликов канала: `comment` (комментарии автора) | `quiz` («Угадай фильм»: вопрос, подсказки,
    ответ в конце; владелец 29.10.2026) | `edit` (кино-эдит: кадр на весь экран, оригинальный дубляж, крупные
    слова по центру, заголовок-история — так растут кино-каналы рунета, разбор 01.11.2026,
    docs/SHORTS_STRATEGY.md) | `facts` («за кадром»: факт о фильме из Википедии голосом поверх его сцены).
    `profile.format_mix` ({"edit": 2, "facts": 1}) — смесь по номеру ролика; иначе `profile.format`, по умолчанию comment."""
    layer = job.get("author_layer") if isinstance(job.get("author_layer"), Mapping) else {}
    if str(layer.get("format") or "") in FORMATS:
        return str(layer["format"])  # формат, заданный ролику вручную (проба, перерендер в другом формате)
    prof = channel_profile(conn, job)
    mix = prof.get("format_mix")
    fmt = str(prof.get("format") or "comment")
    if isinstance(mix, Mapping) and mix:
        # смесь форматов канала («edit»: 2, «facts»: 1): формат ролика — по его номеру, соседние ролики разные;
        # канал из одних нарезок YouTube считает агрегатором (docs/SHORTS_STRATEGY.md)
        seq = [str(k) for k, n in mix.items() for _ in range(max(0, int(n or 0)))]
        if seq:
            fmt = seq[int(job.get("id") or 0) % len(seq)]
    return fmt if fmt in FORMATS else "comment"


FORMATS = ("comment", "quiz", "edit", "facts")


def film_answer(conn: Any, job: Mapping[str, Any], rights: Mapping[str, Any], src: Mapping[str, Any]) -> tuple[str, str | None] | None:
    """(фильм, год) для формата «за кадром» (факт о фильме голосом поверх его сцены) или None."""
    if channel_format(conn, job) != "facts":
        return None
    from autoshorts.clipping.author.script import film_of

    film, year = film_of(rights.get("attribution_text"), src.get("title"))
    return (film, year) if film else None


def quiz_answer(conn: Any, job: Mapping[str, Any], rights: Mapping[str, Any], src: Mapping[str, Any]) -> tuple[str, str | None] | None:
    """(фильм, год) для викторины или None, если канал не в формате quiz или название неизвестно."""
    if channel_format(conn, job) != "quiz":
        return None
    from autoshorts.clipping.author.script import film_of

    film, year = film_of(rights.get("attribution_text"), src.get("title"))
    return (film, year) if film else None


def quiz_reference(rights: Mapping[str, Any], quiz: tuple[str, str | None] | None) -> str:
    """Справка из Википедии для подсказки и факта после ответа (clipping/author/film_ref.py). Wikidata-ID —
    из proof_note записи прав («wikidata=Q…», ставит Кино-тренды), иначе поиск по названию и году."""
    if not quiz or not quiz[0] or os.environ.get("QUIZ_FILM_REF", "1") == "0":
        return ""
    from autoshorts.clipping.author.film_ref import film_reference, qid_from

    return film_reference(quiz[0], quiz[1], qid=qid_from(rights.get("proof_note")), translate=_translate_ref)


def _verify_fact(text: str, ref: str) -> bool | None:
    """Сверка фразы со справкой моделью: True — следует из справки (пересказ можно) или это мнение без новых
    фактов; False — есть имя, число, событие, которого в справке нет; None — модель не ответила."""
    import json as _json
    import urllib.request

    body = {"model": os.environ.get("FACT_VERIFY_MODEL", "google/gemini-2.5-flash"), "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": "Ты фактчекер. Дана справка и фраза для озвучки ролика. "
                          "supported=true, если всё, что фраза утверждает, следует из справки (пересказ своими "
                          "словами и очевидный вывод допустимы) или это явное мнение без новых фактов. "
                          "supported=false, если в фразе есть имя, число, дата, событие или причина, которых в "
                          "справке нет или которые ей противоречат. Ответ JSON {\"supported\": bool}."},
                         {"role": "user", "content": f"<reference>\n{ref[:7000]}\n</reference>\nФраза: {text}"}]}
    try:
        req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=_json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {llm_key()}", "Content-Type": "application/json"})
        r = _json.loads(urllib.request.urlopen(req, timeout=60).read())
        v = _json.loads(r["choices"][0]["message"]["content"]).get("supported")
        return bool(v) if isinstance(v, bool) else None
    except Exception:  # noqa: BLE001
        return None


def _translate_ref(text: str) -> str:
    """Перевод английской справки о фильме на русский для подсказок и фактов (дешёвая модель; сбой — пусто)."""
    import json as _json
    import urllib.request

    body = {"model": os.environ.get("REF_TRANSLATE_MODEL", "google/gemini-2.5-flash-lite"), "temperature": 0,
            "messages": [{"role": "system", "content": "Переведи текст на русский язык точно, без пересказа и "
                          "сокращений; названия фильмов — как в русском прокате; квадратные скобки с названиями "
                          "разделов сохрани. Ответь только переводом."},
                         {"role": "user", "content": text[:6000]}]}
    try:
        req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=_json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {llm_key()}", "Content-Type": "application/json"})
        r = _json.loads(urllib.request.urlopen(req, timeout=90).read())
        return str(r["choices"][0]["message"]["content"] or "")
    except Exception:  # noqa: BLE001 — без перевода справка просто короче
        return ""


def channel_lag_ad(conn: Any, job: Mapping[str, Any]) -> bool:
    """Рекламная вставка «видео тормозит → BPN» (clipping/lag_ad.py). `profile.lag_ad: true`, нужен голос."""
    if channel_format(conn, job) in ("edit", "facts"):
        return False
    return bool(channel_profile(conn, job).get("lag_ad", False))


def channel_author_text(conn: Any, job: Mapping[str, Any]) -> bool:
    """Канал без голоса автора: реплики сценария (комментарии вставок, вопрос в конце) выводятся текстом.
    `profile.author_text: false` — только заголовок, как было до 29.10.2026."""
    if channel_format(conn, job) in ("edit", "facts"):
        return False
    return bool(channel_profile(conn, job).get("author_text", True))


def channel_cold_open(conn: Any, job: Mapping[str, Any]) -> bool:
    """Вступление автора (стоп-кадр с голосом в начале). `profile.cold_open: false` — ролик начинается
    сразу со сцены; по умолчанию вступление есть (как было). «За кадром» — с крючком голосом, эдит — без."""
    fmt = channel_format(conn, job)
    if fmt in ("edit", "facts"):
        return fmt == "facts"
    return bool(channel_profile(conn, job).get("cold_open", True))


# Оформление по умолчанию, если у канала его нет, а оператор выбрал форму для ролика
FRAME_DEFAULTS: dict[str, Any] = {
    "shape": "auto", "size": 880, "cx": 540, "cy": 1090, "rect_w": 1000, "rect_h": 750, "radius": 56,
    "ring_px": 8, "ring_color": "#FFFFFF", "accent": "#FFD84D", "bg_set": "nature", "bg_dim": 0.05,
    "caption_size": 76, "title_size": 78,
}
FRAME_ASPECTS: dict[str, tuple[int, int]] = {"square": (1, 1), "landscape": (4, 3), "portrait": (3, 4), "wide": (16, 9)}


def apply_frame_override(fs: dict[str, Any], job: Mapping[str, Any]) -> dict[str, Any]:
    """Оформление ролика (`author_layer.frame`) поверх стиля канала. shape=none — обычный кадр без рамки
    (вернёт `{"enabled": False}`), channel — форма канала, остальные поля перекрывают канальные."""
    layer = job.get("author_layer") if isinstance(job.get("author_layer"), Mapping) else {}
    ov = layer.get("frame") if isinstance(layer.get("frame"), Mapping) else None
    if not ov:
        return fs
    shape = str(ov.get("shape") or "channel")
    if shape == "none":
        return {"enabled": False}
    tuned = {k: v for k, v in ov.items() if v is not None and k not in ("shape", "aspect")}
    aspect = ov.get("aspect")
    if shape == "channel" and not tuned and not aspect:
        return fs
    out = dict(fs) if fs and fs.get("enabled") is not False else dict(FRAME_DEFAULTS)
    out["enabled"] = True
    out.update(tuned)
    if shape != "channel":
        out["shape"] = shape
    if out.get("shape") == "rounded" and aspect in FRAME_ASPECTS:
        a, b = FRAME_ASPECTS[str(aspect)]
        size = int(out.get("size") or 900)
        out["width"], out["height"] = (size, size * b // a) if a >= b else (size * a // b, size)
    elif out.get("shape") in ("circle", "rounded"):
        out.pop("width", None)
        out.pop("height", None)
    return out


def resolve_frame_style(conn: Any, job: Mapping[str, Any]) -> dict[str, Any] | None:
    """Стиль кадра канала из ТЕКУЩЕГО профиля (`channels.profile.frame_style`) с выбранным фоном.

    Читаем канал на момент рендера, а не снимок задания: перерендер после смены оформления канала
    берёт новое оформление. Фон — из набора канала по номеру ролика (соседние ролики — разные фоны).
    Нет стиля или нет файлов фона — None (прежний вид с размытым фоном)."""
    from autoshorts.repos import jobs as jobs_repo

    ch = jobs_repo.get_channel(conn, str(job.get("channel_id") or ""))
    fs = apply_frame_override(dict(((ch or {}).get("profile") or {}).get("frame_style") or {}), job)
    if not fs or fs.get("enabled") is False:
        return None
    folder = _layout().root / BACKGROUNDS_DIR / str(fs.get("bg_set") or "nature")
    files = sorted(folder.glob("*.mp4")) if folder.is_dir() else []
    if not files:
        return None
    fs["bg_path"] = str(files[int(job.get("id") or 0) % len(files)].resolve())
    return fs


LLM_TRANSIENT_RETRY_SEC = 120.0
TTS_OUTAGE_RETRY_SEC = 240.0
TTS_OUTAGE_MAX_SEC = 3 * 3600.0


def tts_outage_deferrable(exc: BaseException, task: Mapping[str, Any] | None) -> bool:
    """Сбой Edge TTS (пустой поток, сеть) у задачи моложе TTS_OUTAGE_MAX_SEC: отложить, попытку не тратить."""
    import asyncio

    import aiohttp
    from edge_tts.exceptions import EdgeTTSException

    if not isinstance(exc, (EdgeTTSException, aiohttp.ClientError, asyncio.TimeoutError, ConnectionError)):
        return False
    created = (task or {}).get("created_at")
    if not isinstance(created, datetime):
        return True
    age = (datetime.now(UTC) - (created if created.tzinfo else created.replace(tzinfo=UTC))).total_seconds()
    return age < TTS_OUTAGE_MAX_SEC


def llm_failed_transiently(res: Any) -> bool:
    """Черновик стал шаблоном из-за временного сбоя LLM (лимит, таймаут, 5xx), а не из-за отсутствия ключа."""
    return any(
        str(w).startswith("llm_failed_fallback_heuristic:") and ("Retryable" in str(w) or "Timeout" in str(w))
        for w in getattr(res, "warnings", []) or []
    )


def stage_author_script(ctx: Any) -> dict[str, Any]:
    """Тело стадии `author_script` (см. `stages/author_script.py`). Возвращает поля для StageResult."""
    job = ctx.job
    job_id = int(job["id"])
    with _connection() as conn:
        state = clips_repo.get_clip_state(conn, job_id) or {}
        src = require_source(conn, int(job["source_id"]))
        sctx = source_context(conn, src)
        tr = load_transcript(sctx)
        s_sid, e_sid = _clip_range_sids(conn, job, state, tr)
        niche = niche_profile(
            conn, job.get("niche_id") or src.get("niche_id"), product_id=job.get("product_id")
        )
        rights = rights_of(sctx)
        title, hook = _candidate_texts(conn, job)
        text_only = not channel_author_voice(conn, job) and channel_author_text(conn, job)
        quiz = quiz_answer(conn, job, rights, src)
        edit = channel_format(conn, job) == "edit"
        facts = film_answer(conn, job, rights, src)
    existing = state.get("author_script") if isinstance(state.get("author_script"), dict) else None
    version = int(state.get("author_script_version") or 0)
    layer = job.get("author_layer") or {}
    source = "stage"
    # клон копирует сценарий родителя; если у родителя был шаблон (LLM не ответил), копировать нечего —
    # пишем текст заново, иначе клон повторит тот же провал оригинальности
    copied_template = (
        existing is not None
        and existing.get("source") == "copied"
        and existing.get("mode") in ("heuristic", "mock")
        and llm_configured()
    )
    keep = existing is not None and existing.get("source") in ("operator", "draft_task", "copied")
    if keep and existing is not None and not copied_template:
        mode = (
            existing.get("mode")
            if existing.get("mode") in ("llm", "mock", "heuristic", "operator")
            else "operator"
        )
        res = lint_author_script(
            existing.get("script") or {},
            tr,
            s_sid,
            e_sid,
            mode=mode,
            label=existing.get("label"),
            niche=niche,
        )
        source = str(existing.get("source"))
    elif layer.get("mode") == "operator_text" and isinstance(layer.get("script"), dict):
        res = lint_author_script(layer["script"], tr, s_sid, e_sid, mode="operator", niche=niche)
        source = "operator"
    else:
        frame_image = _clip_frames_image(src, sctx, tr, s_sid, e_sid)
        res = draft_author_script(
            tr,
            s_sid,
            e_sid,
            niche=niche,
            rights=rights,
            title=title,
            hook_text=hook,
            budget=ctx.budget,
            frame_image=frame_image,
            text_only=text_only,
            quiz=quiz,
            edit=edit,
            facts=facts,
        )
        if res.mode == "heuristic" and llm_failed_transiently(res):
            # модель упёрлась в лимит провайдера (OpenRouter 429) или сеть: шаблон «здесь спикер формулирует
            # мысль…» гарантированно провалит оригинальность, поэтому откладываем стадию, попытку не тратим
            raise ResourceDeferred(
                "quota_exhausted",
                retry_after_sec=LLM_TRANSIENT_RETRY_SEC,
                message="модель сценария временно недоступна (лимит провайдера или сеть)",
            )
        if layer.get("mode") == "operator_text" and (layer.get("hook_text") or layer.get("title_overlay")):
            script = res.script.model_copy(deep=True)
            if layer.get("hook_text"):
                script.cold_open.vo_text = str(layer["hook_text"])
            if layer.get("title_overlay"):
                script.on_screen_title = str(layer["title_overlay"])
            res = lint_author_script(script, tr, s_sid, e_sid, mode=res.mode, label=res.label, niche=niche)
    ctx.check_cancelled()
    data = author_result_to_dict(res, source=source, start_sid=s_sid, end_sid=e_sid)
    if quiz:  # сборка пакета прячет ответ из названия ролика на площадке
        data["format"], data["answer"] = "quiz", {"film": quiz[0], "year": quiz[1]}
    if existing is not None:
        data["updated_by"] = existing.get("updated_by")
    with _connection() as conn:
        saved = clips_repo.set_author_script(
            conn, job_id, data, expected_version=version, guard_stage="author_script"
        )
        if saved is None:
            fresh = clips_repo.get_clip_state(conn, job_id) or {}
            op = fresh.get("author_script")
            if isinstance(op, dict) and fresh.get("author_script_version") != version:
                # оператор успел сохранить свой текст во время стадии: берём его
                res = lint_author_script(
                    op.get("script") or {}, tr, s_sid, e_sid, mode="operator", niche=niche
                )
                data = author_result_to_dict(
                    res, source="operator", updated_by=op.get("updated_by"), start_sid=s_sid, end_sid=e_sid
                )
                clips_repo.set_author_script(conn, job_id, data, guard_stage="author_script")
    if res.blocked:
        errs = [i.detail for i in res.issues if i.severity == "error"]
        raise PermanentError(
            "Авторский текст заблокирован: "
            + "; ".join(errs)[:400]
            + ". Исправьте текст (PUT /author-script) и перезапустите со стадии author_script",
            error_class="author_script_blocked",
        )
    return {"result": res, "data": data, "cost_usd": res.cost_usd}


# ============================================================================ голос автора


def _sniff_audio(head: bytes) -> str | None:
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "audio/webm"
    if head[:4] == b"OggS":
        return "audio/ogg"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav"
    return None


_EXT = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/wav": ".wav"}


def _script_parts(state: Row) -> dict[str, str]:
    data = state.get("author_script")
    if not isinstance(data, dict) or not isinstance(data.get("script"), dict):
        return {}
    from autoshorts.clipping.author.script import AuthorScript

    return dict(AuthorScript.model_validate(data["script"]).vo_parts())


def author_voice_view(conn: Any, job_id: int) -> dict[str, Any]:
    job = _require_clip_job(conn, job_id)
    state = clips_repo.get_clip_state(conn, job_id) or {}
    parts = _script_parts(state)
    segs = (
        ((state.get("author_voice") or {}).get("segments") or {})
        if isinstance(state.get("author_voice"), dict)
        else {}
    )
    layer = job.get("author_layer") or {}
    items = []
    synthetic = False
    for seg in SEGMENTS:
        d = segs.get(seg) if isinstance(segs.get(seg), dict) else {}
        status = d.get("status") if d.get("status") in ("processing", "ready", "failed") else "missing"
        kind = (
            d.get("kind")
            if d.get("kind") in ("tts", "operator", "mock")
            else ("operator" if d.get("source") == "operator" else None)
        )
        timing = d.get("timing") if d.get("timing") in ("tts", "asr", "estimated", "mock") else None
        if seg in parts and not (kind == "operator" and status == "ready"):
            synthetic = True
        uploaded = d.get("uploaded_at")
        try:
            uploaded_at = datetime.fromisoformat(uploaded) if isinstance(uploaded, str) else None
        except ValueError:
            uploaded_at = None
        items.append(
            {
                "segment": seg,
                "text": parts.get(seg),
                "kind": kind,
                "status": status,
                "duration_sec": _f(d.get("duration_sec")),
                "timing": timing,
                "words_count": int(d["words_count"]) if isinstance(d.get("words_count"), int) else None,
                "audio_url": f"{API_PREFIX}/jobs/{job_id}/author-voice/{seg}/audio"
                if status == "ready" and d.get("path")
                else None,
                "task_id": int(d["task_id"]) if isinstance(d.get("task_id"), int) else None,
                "error": d.get("error"),
                "uploaded_at": uploaded_at,
            }
        )
    voice = layer.get("voice") if layer.get("voice") in ("tts", "operator_recording") else "tts"
    return {"job_id": job_id, "voice": voice, "segments": items, "synthetic": synthetic}


def upload_author_voice(
    job_id: int, segment: str, fileobj: BinaryIO, content_type: str | None, caller: Caller
) -> dict[str, Any]:
    """Запись голоса оператора на реплику → задача `author_voice_prepare` (тишина по краям, тайминги)."""
    from autoshorts.api.schemas.clipping import AUTHOR_VOICE_MAX_BYTES, AUTHOR_VOICE_MIME_TYPES
    from autoshorts.services import audit
    from autoshorts.services.tasks import enqueue

    if segment not in SEGMENT_IDX:
        raise _invalid(["body", "segment"], f"Неизвестная реплика {segment}", "segment")
    declared = (content_type or "").split(";")[0].strip().lower()
    with _connection() as conn:
        job = _require_clip_job(conn, job_id)
        if job["status"] not in VOICE_UPLOAD_STATUSES:
            raise JobStateConflict(
                "Запись голоса недоступна в этом статусе ролика",
                details={"job_id": job_id, "status": job["status"]},
            )
        uid = str(job["video_uid"])
    layout = _layout()
    folder = layout.job_dir(uid, create=True) / "voice_rec"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    tmp = folder / f".{segment}_{stamp}.upload"
    size = 0
    head = b""
    try:
        with tmp.open("wb") as out:
            while True:
                chunk = fileobj.read(1024 * 1024)
                if not chunk:
                    break
                if len(head) < 16:
                    head += chunk[: 16 - len(head)]
                size += len(chunk)
                if size > AUTHOR_VOICE_MAX_BYTES:
                    raise PayloadTooLarge(
                        f"Запись больше {AUTHOR_VOICE_MAX_BYTES // (1024 * 1024)} МБ",
                        details={"limit_bytes": AUTHOR_VOICE_MAX_BYTES, "asset_error": "file_too_large"},
                    )
                out.write(chunk)
        mime = _sniff_audio(head)
        if (
            size == 0
            or mime is None
            or (
                declared
                and declared not in AUTHOR_VOICE_MIME_TYPES
                and declared != "application/octet-stream"
            )
        ):
            raise UnsupportedMediaType(
                "Нужна запись webm/ogg (opus) или wav",
                details={"content_type": declared or None, "accepted": list(AUTHOR_VOICE_MIME_TYPES)},
            )
        raw = folder / f"{segment}_{stamp}{_EXT[mime]}"
        os.replace(tmp, raw)
    finally:
        tmp.unlink(missing_ok=True)
    rel = layout.relpath(raw)
    with _connection() as conn:
        job = _require_clip_job(conn, job_id, for_update=True)
        state = clips_repo.get_clip_state(conn, job_id) or {}
        text = _script_parts(state).get(segment)
        res = enqueue(
            conn,
            VOICE_TASK_KIND,
            resource_class="heavy",
            max_attempts=2,
            priority=50,
            payload={"job_id": job_id, "segment": segment, "raw_path": rel},
        )
        task_id = int(res.task["id"])
        clips_repo.set_author_voice_segment(
            conn,
            job_id,
            segment,
            {
                "source": "operator",
                "kind": "operator",
                "status": "processing",
                "raw_path": rel,
                "mime": mime,
                "bytes": size,
                "text": text,
                "uploaded_at": _now_iso(),
                "uploaded_by": caller.user_id,
                "task_id": task_id,
            },
        )
        audit.record(
            conn,
            caller.actor,
            "job.author_voice_upload",
            "job",
            job_id,
            summary=f"{job['video_uid']}: запись {segment}",
            diff={"segment": segment, "bytes": size, "mime": mime, "task_id": task_id},
        )
    return {"task_id": task_id, "segment": segment, "bytes": size, "mime": mime}


_ASR_CHILD = """
import json, sys
sys.path.insert(0, sys.argv[1])
from autoshorts.providers.asr import get_asr_provider
key, wav, lang, text = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
kwargs = {"text": text} if key == "mock" and text else {}
tr = get_asr_provider(key, **kwargs).transcribe(wav, lang)
print("@@words " + json.dumps([[w.w, w.s, w.e] for w in tr.words], ensure_ascii=False), flush=True)
"""


def asr_words_in_child(
    wav16: Path, *, provider: str, language: str = "ru", text: str = "", timeout_sec: float = 600.0
) -> list[Word]:
    """Пословные тайминги записи в дочернем процессе (модель ASR не остаётся в памяти воркера)."""
    from autoshorts.clipping.transcript import Word

    src_root = str(Path(__file__).resolve().parents[2])
    cmd = [sys.executable, "-c", _ASR_CHILD, src_root, provider, str(wav16), language, text or ""]
    try:
        cur = os.nice(0)
    except OSError:
        cur = 10
    if cur < 10:
        cmd = ["nice", "-n", str(10 - cur), *cmd]
    env = {**os.environ, "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, env=env, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ASR {provider} exit {proc.returncode}: {(proc.stderr or '')[-300:]}")
    for line in proc.stdout.splitlines():
        if line.startswith("@@words "):
            return [Word(str(w), float(s), float(e)) for w, s, e in json.loads(line[8:])]
    raise RuntimeError(f"ASR {provider}: no words in output")


def run_author_voice_prepare(ctx: TaskContext) -> dict[str, Any]:
    """Задача `author_voice_prepare` (heavy): запись оператора → WAV без тишины + тайминги (ASR)."""
    from autoshorts.clipping.author.voice import _estimate_words, prepare_operator_take
    from autoshorts.pipeline.ffmpeg_exec import run_media
    from autoshorts.repos import artifacts as artifacts_repo
    from autoshorts.stages.base import input_hash

    payload = ctx.payload
    job_id, segment, raw_rel = (
        int(payload.get("job_id") or 0),
        str(payload.get("segment")),
        str(payload.get("raw_path")),
    )
    layout = _layout()
    with _connection() as conn:
        state = clips_repo.get_clip_state(conn, job_id)
        if state is None:
            return {"skipped": "job_gone"}
        seg = ((state.get("author_voice") or {}).get("segments") or {}).get(segment) or {}
        if seg.get("raw_path") != raw_rel:
            return {"skipped": "superseded"}
        uid = str(state["video_uid"])
    raw = layout.resolve(raw_rel)
    out_dir = raw.parent
    text = str(seg.get("text") or "")
    try:
        take = prepare_operator_take(
            f"{segment}_{raw.stem.split('_')[-1]}", raw, out_dir, text=text, asr=None
        )
        wav16 = out_dir / f"{raw.stem}_16k.wav"
        timing, words, warns = "estimated", [], list(take.warnings)
        try:
            run_media(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(take.path),
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(wav16),
                ],
                timeout_sec=120,
                threads=2,
                nice=10,
                cancel=ctx.cancel,
            )
            words = asr_words_in_child(wav16, provider=voice_asr_key(), text=text)
            timing = "asr"
        except Exception as exc:  # noqa: BLE001 - без ASR тайминги оцениваются по длине слов
            warns.append(f"asr_failed: {type(exc).__name__}: {exc}"[:200])
        finally:
            wav16.unlink(missing_ok=True)
        if not words:
            words = _estimate_words(text or "…", 0.0, take.duration)
            warns = [w for w in warns if w != "word_timing_estimated"] + ["word_timing_estimated"]
        entry = {
            **seg,
            "status": "ready",
            "kind": "operator",
            "path": layout.relpath(take.path),
            "duration_sec": round(float(take.duration), 3),
            "timing": timing,
            "words": [[w.w, round(w.s, 3), round(w.e, 3)] for w in words],
            "words_count": len(words),
            "warnings": warns,
            "processed_at": _now_iso(),
            "error": None,
        }
        with _connection() as conn:
            fresh = clips_repo.get_clip_state(conn, job_id, for_update=True) or {}
            cur = ((fresh.get("author_voice") or {}).get("segments") or {}).get(segment) or {}
            if cur.get("raw_path") != raw_rel:
                return {"skipped": "superseded"}
            clips_repo.set_author_voice_segment(conn, job_id, segment, entry)
            for old in artifacts_repo.list_for_job(conn, job_id, kinds=["author_voice"]):
                if int(old["idx"]) == SEGMENT_IDX[segment] and old.get("stage") is None:
                    artifacts_repo.soft_delete(conn, [int(old["id"])])
            artifacts_repo.insert(
                conn,
                kind="author_voice",
                path=entry["path"],
                input_hash=input_hash(uid, "author_voice_operator", raw_rel),
                job_id=job_id,
                idx=SEGMENT_IDX[segment],
                bytes_=take.path.stat().st_size,
                stage=None,
            )
        return {"job_id": job_id, "segment": segment, "timing": timing, "duration_sec": entry["duration_sec"]}
    except (ResourceDeferred, RetryableError):
        raise
    except Exception as exc:  # noqa: BLE001 - битая запись: реплика помечается failed, ролик не падает
        from autoshorts.core.errors import Cancelled

        if isinstance(exc, Cancelled):
            raise
        log.warning("job %s: operator recording %s failed", job_id, segment, exc_info=True)
        with _connection() as conn:
            fresh = clips_repo.get_clip_state(conn, job_id) or {}
            cur = ((fresh.get("author_voice") or {}).get("segments") or {}).get(segment) or {}
            if cur.get("raw_path") == raw_rel:
                clips_repo.set_author_voice_segment(
                    conn,
                    job_id,
                    segment,
                    {**cur, "status": "failed", "error": f"{type(exc).__name__}: {exc}"[:300]},
                )
        return {"job_id": job_id, "segment": segment, "failed": True}


@dataclass(frozen=True)
class MediaFile:
    relpath: str
    filename: str
    media_type: str


def author_voice_file(conn: Any, job_id: int, segment: str) -> MediaFile:
    job = _require_clip_job(conn, job_id)
    state = clips_repo.get_clip_state(conn, job_id) or {}
    seg = ((state.get("author_voice") or {}).get("segments") or {}).get(segment) or {}
    rel = seg.get("path") if seg.get("status") == "ready" else None
    if not rel:
        raise NotFound("author_voice", "Реплики ещё нет")
    ext = Path(rel).suffix.lower() or ".wav"
    mtype = mimetypes.guess_type(f"x{ext}")[0] or "audio/wav"
    if ext == ".wav":
        mtype = "audio/wav"
    return MediaFile(rel, f"{job['video_uid']}_{segment}{ext}", mtype)


def delete_author_voice(conn: Any, job_id: int, segment: str, caller: Caller) -> None:
    from autoshorts.repos import artifacts as artifacts_repo
    from autoshorts.services import audit

    job = _require_clip_job(conn, job_id, for_update=True)
    state = clips_repo.get_clip_state(conn, job_id, for_update=True) or {}
    seg = ((state.get("author_voice") or {}).get("segments") or {}).get(segment)
    if not isinstance(seg, dict) or seg.get("source") != "operator":
        raise NotFound("author_voice", "Записи оператора для этой реплики нет")
    clips_repo.set_author_voice_segment(conn, job_id, segment, None)
    for art in artifacts_repo.list_for_job(conn, job_id, kinds=["author_voice"]):
        if int(art["idx"]) == SEGMENT_IDX[segment] and art.get("stage") is None:
            artifacts_repo.soft_delete(conn, [int(art["id"])])
    audit.record(
        conn,
        caller.actor,
        "job.author_voice_delete",
        "job",
        job_id,
        summary=f"{job['video_uid']}: запись {segment} удалена",
        diff={"segment": segment},
    )


def take_from_dict(role: str, d: Mapping[str, Any]) -> VoiceTake:
    from autoshorts.clipping.author.voice import VoiceTake
    from autoshorts.clipping.transcript import Word

    words = []
    for w in d.get("words") or []:
        if isinstance(w, list | tuple) and len(w) >= 3:
            words.append(Word(str(w[0]), float(w[1]), float(w[2])))
        elif isinstance(w, dict):
            words.append(Word(str(w.get("w")), float(w.get("s") or 0), float(w.get("e") or 0)))
    kind = d.get("kind") if d.get("kind") in ("tts", "operator", "mock") else "tts"
    timing = d.get("timing") if d.get("timing") in ("tts", "asr", "estimated", "mock") else "tts"
    return VoiceTake(
        role,
        _layout().resolve(str(d["path"])),
        float(d.get("duration_sec") or d.get("duration") or 0.0),
        words,
        kind,  # type: ignore[arg-type]
        str(d.get("text") or ""),
        d.get("voice"),
        str(d.get("provider") or ("operator_recording" if kind == "operator" else "")),
        timing,  # type: ignore[arg-type]
        warnings=[str(w) for w in d.get("warnings") or []],
    )


def take_to_dict(take: VoiceTake) -> dict[str, Any]:
    return {
        "path": _layout().relpath(take.path),
        "duration_sec": round(float(take.duration), 3),
        "kind": take.kind,
        "text": take.text,
        "voice": take.voice,
        "provider": take.provider,
        "timing": take.timing,
        "words": [[w.w, round(w.s, 3), round(w.e, 3)] for w in take.words],
        "warnings": list(take.warnings),
    }


def stage_author_voice(ctx: Any) -> dict[str, Any]:
    """Тело стадии `author_voice`: запись оператора (готовая) или TTS (edge/mock) на каждую реплику."""
    from autoshorts.clipping.author.voice import VoiceResult, synthesize_edge, synthesize_mock

    job = ctx.job
    job_id = int(job["id"])
    with _connection() as conn:
        state = clips_repo.get_clip_state(conn, job_id) or {}
    data = state.get("author_script")
    if not isinstance(data, dict):
        raise PermanentError(
            "Нет авторского текста: стадия author_script не прошла", error_class="author_script_missing"
        )
    author = author_result_from_dict(data)
    parts = author.script.vo_parts()
    segs = (
        ((state.get("author_voice") or {}).get("segments") or {})
        if isinstance(state.get("author_voice"), dict)
        else {}
    )
    layer = job.get("author_layer") or {}
    out_dir = Path(ctx.workdir) / f"voice_{int(ctx.task['id'])}"
    out_dir.mkdir(parents=True, exist_ok=True)
    engine = tts_engine()
    # свой голос канала (профиль канала, разные голоса против детекта сетки §1.3), если это голос edge-tts
    profile = ((job.get("config_snapshot") or {}).get("channel") or {}).get("profile") or {}
    edge_voice = (
        profile.get("tts_voice") if EDGE_VOICE_RE.fullmatch(str(profile.get("tts_voice") or "")) else None
    )
    result = VoiceResult()
    t0 = time.monotonic()
    tts_takes: dict[str, VoiceTake] = {}
    with _connection() as conn:
        with_cold_open = channel_cold_open(conn, job)
        with_voice = channel_author_voice(conn, job)
        live = channel_profile(conn, job)
    # голос gpt-audio через OpenRouter (владелец 29.10: «хороший голос, не нейронная хуйня»; edge-tts — 403)
    audio_tts = str(live.get("tts_provider") or "") == "openai_audio"
    if not with_voice:
        parts = []  # канал без нейроозвучки: ничего не синтезируем
    elif not with_cold_open:
        # вступление не озвучиваем — его не будет в ролике
        parts = [(r, t) for r, t in parts if r != "cold_open"]
    for n, (role, text) in enumerate(parts):
        ctx.check_cancelled()
        seg = segs.get(role) if isinstance(segs.get(role), dict) else {}
        if seg.get("source") == "operator":
            if seg.get("status") == "processing":
                uploaded = seg.get("uploaded_at")
                age = 0.0
                try:
                    age = (
                        (datetime.now(UTC) - datetime.fromisoformat(uploaded)).total_seconds()
                        if uploaded
                        else 0.0
                    )
                except ValueError:
                    age = 0.0
                if age < VOICE_PROCESSING_STALE_SEC:
                    raise ResourceDeferred(
                        "load_high", retry_after_sec=20.0, message=f"запись {role} ещё обрабатывается"
                    )
            if seg.get("status") == "ready" and seg.get("path"):
                take = take_from_dict(role, seg)
                if take.path.is_file():
                    if seg.get("text") and str(seg.get("text")).strip() != text.strip():
                        take.warnings.append("operator_recording_text_changed")
                        result.warnings.append(f"operator_recording_text_changed:{role}")
                    result.takes[role] = take
                    continue
            result.warnings.append(f"operator_recording_unusable:{role}")
        elif layer.get("voice") == "operator_recording":
            result.warnings.append(f"operator_recording_missing:{role}")
        try:
            if engine == "mock":
                take = synthesize_mock(role, text, out_dir)
            elif audio_tts:
                take = _synthesize_audio_tts(role, text, out_dir, live)
            else:
                take = synthesize_edge(role, text, out_dir, voice=edge_voice)
        except Exception as exc:
            from autoshorts.pipeline.voice import MockInProdError

            if isinstance(exc, MockInProdError):
                raise PermanentError(str(exc), error_class="mock_in_prod") from exc
            if engine != "mock" and tts_outage_deferrable(exc, ctx.task):
                # бесплатный Edge TTS лежит волнами по несколько минут (NoAudioReceived у всех): ждём
                # окончания волны, попытку не тратим; дольше TTS_OUTAGE_MAX_SEC — обычная ошибка с повтором
                raise ResourceDeferred(
                    "quota_exhausted",
                    retry_after_sec=TTS_OUTAGE_RETRY_SEC,
                    message=f"TTS {engine} молчит ({type(exc).__name__}), повтор через пару минут",
                ) from exc
            raise RetryableError(f"TTS {engine} не ответил: {type(exc).__name__}: {exc}"[:300]) from exc
        result.takes[role] = take
        tts_takes[role] = take
        ctx.progress(min(0.95, (n + 1) / max(1, len(parts))))
    if with_voice and audio_tts and bool(live.get("lag_ad")):
        ctx.check_cancelled()
        try:
            result.takes["ad_slogan"] = _lag_slogan_take(live)
        except Exception as exc:  # noqa: BLE001 - без слогана ролик выходит без вставки, а не падает
            result.warnings.append(f"lag_ad_slogan_failed: {type(exc).__name__}: {exc}"[:200])
    result.wall_sec = time.monotonic() - t0
    with _connection() as conn:
        for role, take in tts_takes.items():
            entry = {
                "source": "tts",
                "status": "ready",
                **take_to_dict(take),
                "words_count": len(take.words),
                "processed_at": _now_iso(),
            }
            entry.pop("words", None)
            clips_repo.set_author_voice_segment(conn, job_id, role, entry)
    return {"voice": result, "tts_takes": tts_takes, "author": author}


AUDIO_TTS_VOICE = "ash"


def _lag_slogan_take(profile: Mapping[str, Any]) -> Any:
    """Слоган рекламной вставки голосом канала: озвучивается один раз на голос и берётся из кеша
    (`<MEDIA_ROOT>/assets/lag_ad/slogan_<голос>.wav` + `.json`) — везде звучит одинаково."""
    from autoshorts.clipping import lag_ad
    from autoshorts.clipping.author.voice import VoiceTake
    from autoshorts.clipping.transcript import Word

    voice = str(profile.get("tts_voice") or AUDIO_TTS_VOICE)
    cache = _layout().root / "assets" / "lag_ad"
    wav, meta = cache / f"slogan_{voice}.wav", cache / f"slogan_{voice}.json"
    if wav.is_file() and meta.is_file():
        d = json.loads(meta.read_text(encoding="utf-8"))
        if d.get("text") == lag_ad.SLOGAN:
            words = [Word(str(w), float(a), float(b)) for w, a, b in d.get("words") or []]
            return VoiceTake("ad_slogan", wav, float(d["duration"]), words, "tts", lag_ad.SLOGAN, voice, str(d.get("provider") or ""), "asr")
    cache.mkdir(parents=True, exist_ok=True)
    tmp = cache / f"tmp_{voice}"
    take = _synthesize_audio_tts("ad_slogan", lag_ad.SLOGAN, tmp, dict(profile, tts_voice=voice), style=lag_ad.SLOGAN_STYLE)
    take.path.replace(wav)
    shutil.rmtree(tmp, ignore_errors=True)
    take.path = wav
    meta.write_text(json.dumps({"text": lag_ad.SLOGAN, "duration": take.duration, "provider": take.provider,
                                "words": [[w.w, w.s, w.e] for w in take.words]}, ensure_ascii=False), encoding="utf-8")
    return take


def _synthesize_audio_tts(role: str, text: str, out_dir: Path, profile: Mapping[str, Any], *, style: str | None = None) -> Any:
    """Реплика голосом gpt-audio (`profile.tts_voice`, модель `TTS_AUDIO_MODEL`, ключ LLM — OpenRouter)."""
    from autoshorts.clipping.author.voice import synthesize_openai_audio
    from autoshorts.pipeline.ffmpeg_exec import run_media

    key = llm_key()
    if not key:
        raise PermanentError("Нет ключа LLM (OpenRouter) для голоса gpt-audio", error_class="tts_no_key")

    def words(wav: Path) -> list[Word]:
        wav16 = wav.with_name(wav.stem + "_16k.wav")
        try:
            run_media(["ffmpeg", "-y", "-i", str(wav), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav16)],
                      timeout_sec=120, threads=2, nice=10)  # fmt: skip
            return asr_words_in_child(wav16, provider=voice_asr_key(), text=text)
        finally:
            wav16.unlink(missing_ok=True)

    return synthesize_openai_audio(
        role,
        text,
        out_dir,
        voice=str(profile.get("tts_voice") or AUDIO_TTS_VOICE),
        model=_env("TTS_AUDIO_MODEL", "openai/gpt-audio"),
        api_key=key,
        base_url=_env("TTS_AUDIO_BASE_URL", _env("LLM_BASE_URL", "https://openrouter.ai/api/v1")),
        words_fn=words,
        **({"style": style} if style else {}),
    )


def voice_result_from_payload(payload: Mapping[str, Any]) -> VoiceResult | None:
    from autoshorts.clipping.author.voice import VoiceResult

    cv = payload.get("clip_voice") if isinstance(payload.get("clip_voice"), dict) else None
    if not cv:
        return None
    res = VoiceResult()
    for role, d in (cv.get("takes") or {}).items():
        if isinstance(d, dict) and d.get("path"):
            res.takes[str(role)] = take_from_dict(str(role), d)
    res.warnings = [str(w) for w in cv.get("warnings") or []]
    return res


# ============================================================================ рефрейм: план между стадиями


def reframe_plan_to_json(plan: ReframePlan) -> dict[str, Any]:
    return {
        "v": 1,
        "geometry": plan.geometry.to_dict(),
        "start": plan.start,
        "end": plan.end,
        "fps": plan.fps,
        "requested_mode": plan.requested_mode,
        "scene_cuts": list(plan.scene_cuts),
        "shot_detection": plan.shot_detection,
        "faces": plan.faces,
        "warnings": list(plan.warnings),
        "timings": dict(plan.timings),
        "shots": [
            {
                "idx": s.idx,
                "start": s.start,
                "end": s.end,
                "mode": s.mode,
                "reason": s.reason,
                "x_static": s.x_static,
                "points": [[float(t), float(x)] for t, x in s.points],  # без округления: sendcmd как есть
                "stats": s.stats,
            }
            for s in plan.shots
        ],
    }


def reframe_plan_from_json(d: Mapping[str, Any]) -> ReframePlan:
    from autoshorts.clipping.reframe import CropGeometry, ReframePlan, ShotPlan

    g = d["geometry"]
    plan = ReframePlan(
        CropGeometry(int(g["src_w"]), int(g["src_h"]), int(g["crop_w"]), int(g["crop_h"]), int(g["y"])),
        float(d["start"]),
        float(d["end"]),
        fps=float(d.get("fps") or 30.0),
        requested_mode=str(d.get("requested_mode") or "auto"),
        shot_detection=d.get("shot_detection"),
        faces=d.get("faces"),
    )
    plan.scene_cuts = [float(c) for c in d.get("scene_cuts") or []]
    plan.warnings = [str(w) for w in d.get("warnings") or []]
    plan.timings = {str(k): float(v) for k, v in (d.get("timings") or {}).items()}
    for s in d.get("shots") or []:
        plan.shots.append(
            ShotPlan(
                int(s["idx"]),
                float(s["start"]),
                float(s["end"]),
                s["mode"],
                str(s.get("reason") or ""),
                int(s["x_static"]),
                points=[(float(t), float(x)) for t, x in s.get("points") or []],
                stats=dict(s.get("stats") or {}),
            )
        )
    return plan


# ============================================================================ clip_fetch: 1080p-секция


def _ydl(opts: dict[str, Any]) -> Any:
    if YDL_FACTORY is not None:
        return YDL_FACTORY(opts)
    import yt_dlp

    return yt_dlp.YoutubeDL(opts)


SECTION_EXACT_EXTS = frozenset({".mp4", ".m4v", ".mov"})


def fetch_section(url: str, start: float, end: float, out_dir: Path, *, max_height: int = 1080) -> Path:
    """Только нужный кусок видео ≤1080p `[start, end]` (yt-dlp download_ranges, §6.3.1).

    Кусок качается копированием потока (`-c copy`), без перекодирования: стадия `clip_fetch` лёгкая
    (light-воркер, 512 МБ, без допуска heavy), а `force_keyframes_at_cuts` превращал загрузку в
    полное кодирование 1080p x264 без nice и отмены. Точность начала держит mp4: ffmpeg копирует
    кадры от ключевого перед `start` и пишет edit list, декодер начинает ровно со `start` (проверено
    на ffmpeg 6.1: первый кадр секции совпадает с кадром источника на `start`). Поэтому формат —
    сначала mp4 (у YouTube avc1 до 1080p есть всегда); другой контейнер начнётся с ключевого кадра
    раньше `start` — это учитывает `section_start`.
    """
    from autoshorts.clipping.source_ingest import ytdlp_base_options

    try:
        from yt_dlp.utils import download_range_func

        ranges: Any = download_range_func(None, [(float(start), float(end))])
    except Exception:  # noqa: BLE001 - фабрика в тестах, yt-dlp может отсутствовать
        ranges = [(float(start), float(end))]
    out_dir.mkdir(parents=True, exist_ok=True)
    h = int(max_height)
    opts = {
        **ytdlp_base_options(),
        "format": (
            f"bv*[height<={h}][vcodec^=avc1][ext=mp4]/bv*[height<={h}][ext=mp4]/b[height<={h}][ext=mp4]"
            f"/bv*[height<={h}]/b[height<={h}]/b"  # /b: прямые mp4-ссылки без метаданных высоты (generic)
        ),
        "outtmpl": str(out_dir / "section.%(ext)s"),
        "download_ranges": ranges,
        "force_keyframes_at_cuts": False,
    }
    with _ydl(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        try:
            path = Path(ydl.prepare_filename(info))
        except Exception:  # noqa: BLE001
            path = out_dir / "section.mp4"
    if path.is_file():
        return path
    cands = sorted(p for p in out_dir.glob("section*.*") if not p.name.endswith((".part", ".ytdl")))
    if not cands:
        raise RetryableError(f"yt-dlp не сохранил секцию {start:.1f}–{end:.1f} с")
    return cands[0]


def section_start(path: Path, start: float, end: float, duration: float) -> float:
    """Где на шкале источника начинается скачанная секция.

    mp4/mov с edit list — ровно `start`. Другой контейнер (webm/mkv) при копировании потока
    начинается с ключевого кадра до `start`, а конец режется по `end`: начало ≈ `end − длительность`.
    """
    if path.suffix.lower() in SECTION_EXACT_EXTS or duration <= 0:
        return float(start)
    return max(0.0, min(float(start), float(end) - float(duration)))


# ============================================================================ баннер и код ролика


def ensure_tracking_code(conn: Any, job: Row) -> str | None:
    """Короткий код ролика для баннера (5 символов Crockford без 0/o/1/l/i/u, §6.6).

    Резервируется при первом рендере как `tracking_links(level='package', job_id)`; rework и
    повторный рендер берут тот же код, `package_build` (M3-A) должен переиспользовать эту ссылку.
    """
    from autoshorts.api.schemas.tracking import PACKAGE_CODE_ALPHABET

    existing = clips_repo.job_tracking_link(conn, int(job["id"]))
    if existing is not None:
        return str(existing["code"])
    if not job.get("product_id"):
        return None
    try:  # тот же генератор кодов, что у пакетов и ссылок (M3-B), если модуль уже есть
        from autoshorts.services.tracking import ensure_package_link
    except ImportError:
        ensure_package_link = None
    if ensure_package_link is not None:
        return str(ensure_package_link(conn, job, product_id=str(job["product_id"]))["code"])
    for _ in range(20):
        code = "".join(secrets.choice(PACKAGE_CODE_ALPHABET) for _ in range(TRACKING_CODE_LEN))
        row = clips_repo.insert_tracking_link(
            conn,
            {
                "code": code,
                "product_id": job["product_id"],
                "level": "package",
                "job_id": int(job["id"]),
                "channel_id": job.get("channel_id"),
                "campaign_id": job.get("campaign_id"),
                "banner_variant_id": job.get("banner_variant_id"),
                "platform": job.get("channel_platform"),
            },
        )
        if row is not None:
            return str(row["code"])
    return None


@dataclass
class BannerSetup:
    factory: Any = None
    job_fields: dict[str, Any] = field(default_factory=dict)
    layers: list[dict[str, Any]] | None = None
    code: str | None = None
    warnings: list[str] = field(default_factory=list)
    layout_notes: list[str] = field(default_factory=list)  # что сделала ручная раскладка с баннером


def layout_spec_of(job: Row) -> Any:
    """Ручная раскладка ролика (`author_layer.layout`) → `layout_override.LayoutSpec` или None."""
    from autoshorts.clipping.layout_override import LayoutSpec

    layer = job.get("author_layer")
    if not isinstance(layer, Mapping):
        return None
    return LayoutSpec.from_json(layer.get("layout"))


def _safe_zone_profile(platform: Any) -> Any:
    from autoshorts.pipeline.banner_place import PLATFORM_PROFILE, load_safe_zone_catalog

    catalog = load_safe_zone_catalog()
    profile_id = PLATFORM_PROFILE.get(str(platform or "youtube"))
    try:
        return catalog.get(profile_id) if profile_id else catalog.get()
    except KeyError:
        return catalog.get()


def _variant_inputs(conn: Any, variant_id: int) -> tuple[Any, dict[str, Any], dict[str, tuple[int, int]]]:
    """(доменный вариант, креативы sha→BannerCreative, размеры sha→(w, h)) для раскладки и рендера."""
    from autoshorts.repos import products as products_repo
    from autoshorts.services import banner_creatives as creatives_service
    from autoshorts.services.campaigns import require_variant, to_domain_variant

    vrow = require_variant(conn, int(variant_id))
    product = products_repo.get(conn, vrow["product_id"])
    variant = to_domain_variant(vrow, product)
    creatives = creatives_service.domain_creatives(conn, variant.creative_shas())
    sizes = {sha: (int(c.width), int(c.height)) for sha, c in creatives.items()}
    return variant, creatives, sizes


def banner_setup(conn: Any, job: Row) -> BannerSetup:
    """Вариант баннера ролика (или назначение по кампании до рендера) → фабрика плана для движка.

    Ручная раскладка (`author_layer.layout`, mode=manual, banner.dx/dy/scale) сдвигает блок оффера
    варианта до построения плана (`layout_override.shift_variant`): блок вписывается в safe zone.
    """
    out = BannerSetup()
    variant_id = job.get("banner_variant_id")
    try:
        if variant_id is None:
            from autoshorts.services.campaigns import assign_banner

            assigned = assign_banner(
                conn,
                video_uid=str(job["video_uid"]),
                channel_id=job.get("channel_id"),
                niche_id=job.get("niche_id"),
                platform=job.get("channel_platform"),
                kind="clip",
                product_id=job.get("product_id"),
                campaign_id=job.get("campaign_id"),
            )
            if assigned and assigned.get("banner_variant_id") is not None:
                variant_id = int(assigned["banner_variant_id"])
                out.job_fields = {
                    "banner_variant_id": variant_id,
                    "campaign_id": assigned["campaign_id"],
                    "product_id": assigned["product_id"],
                }
        if variant_id is None:
            return out
        from autoshorts.clipping.layout_override import shift_variant
        from autoshorts.pipeline.banner_overlay import prepare_banner_plan

        variant, creatives, sizes = _variant_inputs(conn, int(variant_id))
        profile = _safe_zone_profile(job.get("channel_platform"))
        spec = layout_spec_of(job)
        if spec is not None and spec.has_banner_shift:
            shifted = shift_variant(variant, spec.banner, profile, sizes)
            variant = shifted.variant
            out.layout_notes = list(shifted.notes)
            log.info("job %s: banner block moved by layout override %s", job.get("id"), spec.banner.to_json())
        job_for_code = {**job, **out.job_fields}
        out.code = ensure_tracking_code(conn, job_for_code)
        out.layers = [la.model_dump(mode="json") for la in variant.layers]
        media_root = _layout().root
        code = out.code

        def factory(*, duration_sec: float, fps: float, out_size: tuple[int, int], workdir: Path) -> Any:
            return prepare_banner_plan(
                variant,
                creatives,
                media_root=media_root,
                duration_sec=duration_sec,
                out_size=out_size,
                profile=profile,
                cache_dir=Path(workdir) / "banner_cache",
                fps=fps,
                code=code,
            )

        out.factory = factory
    except Exception as exc:  # noqa: BLE001 - без баннера ролик всё равно рендерится (с предупреждением)
        log.warning("job %s: banner setup failed", job.get("id"), exc_info=True)
        out.warnings.append(f"banner_setup_failed: {type(exc).__name__}: {exc}"[:300])
        out.factory = None
    return out


# ============================================================================ оригинальность


def _rights_type_out(rt: Any) -> str | None:
    from autoshorts.domain.enums import RightsType

    if rt == "licensed":
        rt = "licensed_written"
    return rt if isinstance(rt, str) and rt in RightsType._value2member_map_ else None


def report_out(rep: Mapping[str, Any], *, stage: str, computed_at: Any = None) -> dict[str, Any]:
    """`OriginalityReport.to_dict()` → схема `OriginalityReportOut` (подписи по-русски)."""
    from autoshorts.api.schemas.clipping import OriginalityCheckCode
    from autoshorts.clipping.originality import CHECK_TITLES_RU, VERDICT_RU

    valid = OriginalityCheckCode._value2member_map_
    checks = [
        {
            "code": c["code"],
            "title": CHECK_TITLES_RU.get(c["code"], c["code"]),
            "status": c.get("status")
            if c.get("status") in ("pass", "warn", "fail", "info", "skip")
            else "info",
            "value": c.get("value"),
            "threshold": c.get("threshold"),
            "detail": str(c.get("detail") or ""),
        }
        for c in rep.get("checks") or []
        if isinstance(c, dict) and c.get("code") in valid
    ]
    summary = dict(rep.get("summary") or {})
    summary["rights_type"] = _rights_type_out(summary.get("rights_type"))
    summary.setdefault("author_mode", None)
    verdict = rep.get("verdict") if rep.get("verdict") in ("pass", "warn", "fail") else "warn"
    if isinstance(computed_at, str):
        try:
            computed_at = datetime.fromisoformat(computed_at)
        except ValueError:
            computed_at = None
    return {
        "stage": stage,
        "verdict": verdict,
        "verdict_label": VERDICT_RU.get(verdict, verdict),
        "summary": summary,
        "fails": [c for c in rep.get("fails") or [] if c in valid],
        "warns": [c for c in rep.get("warns") or [] if c in valid],
        "checks": checks,
        "suggestions": [str(s) for s in rep.get("suggestions") or []],
        "author_share": rep.get("author_share") or {},
        "rights": dict(rep.get("rights") or {}),
        "notes": [str(n) for n in rep.get("notes") or []],
        "computed_at": computed_at,
    }


def originality_history(
    conn: Any, *, source_id: int, channel_id: str | None, job_id: int | None
) -> tuple[list[dict[str, Any]], list[tuple[Any, Any]], list[tuple[Any, Any]]]:
    """(прошлые клипы источника по sid, прошлые реплики канала, отпечатки канала) без родни ролика."""
    from autoshorts.clipping.author.script import AuthorScript
    from autoshorts.pipeline.similarity import VideoFingerprint

    family = clips_repo.family_job_ids(conn, job_id) if job_id is not None else set()
    previous = []
    for r in clips_repo.source_clip_jobs(conn, source_id, exclude_job_id=job_id):
        if int(r["id"]) in family or r.get("clip_start_sid") is None or r.get("clip_end_sid") is None:
            continue
        previous.append(
            {
                "id": f"job {r['id']} ({r['video_uid']})",
                "sids": list(range(int(r["clip_start_sid"]), int(r["clip_end_sid"]) + 1)),
            }
        )
    texts: list[tuple[Any, Any]] = []
    library: list[tuple[Any, Any]] = []
    if channel_id:
        for r in clips_repo.channel_author_scripts(conn, channel_id, exclude_job_id=job_id):
            if int(r["id"]) in family:
                continue
            data = r.get("author_script") or {}
            try:
                parts = AuthorScript.model_validate(data.get("script") or {}).vo_parts()
            except ValueError:
                continue
            if parts:
                texts.append((f"job {r['id']}", [list(p) for p in parts]))
        for r in clips_repo.channel_fingerprints(conn, channel_id, exclude_job_id=job_id):
            if int(r["id"]) in family:
                continue
            fp = r.get("fingerprint")
            try:
                if isinstance(fp, dict) and fp.get("dhash"):
                    VideoFingerprint.from_json(fp)
                elif isinstance(fp, list) and fp:
                    VideoFingerprint.from_hex([str(x) for x in fp])
                else:
                    continue
            except (TypeError, ValueError):
                continue
            library.append((int(r["id"]), fp))
    return previous, texts, library


def _settings(conn: Any) -> Any:
    from autoshorts.services.settings import get_runtime_settings

    return get_runtime_settings(conn)


def _estimate_takes(parts: Sequence[tuple[str, str]], voice: str, segs: Mapping[str, Any]) -> dict[str, Any]:
    """Длительности реплик без озвучки: готовая запись/TTS ролика или ≈ 14 символов в секунду.

    Реплика без готовой записи оператора считается синтезом даже при `voice=operator_recording`:
    рендер (`stage_author_voice`) озвучит её TTS, и оценка до рендера не должна обещать живой голос
    и молчать об AI-метке.
    """
    out: dict[str, Any] = {}
    for role, text in parts:
        seg = segs.get(role) if isinstance(segs.get(role), dict) else {}
        if seg.get("status") == "ready" and seg.get("duration_sec"):
            kind = seg.get("kind") if seg.get("kind") in ("tts", "operator", "mock") else "tts"
            out[role] = {
                "path": "",
                "duration": float(seg["duration_sec"]),
                "kind": kind,
                "text": text,
                "words": [],
            }
            continue
        dur = max(1.0, len(text) / 14.0 + 0.3)
        out[role] = {
            "path": "",
            "duration": round(dur, 2),
            "kind": "tts",
            "text": text,
            "words": [],
        }
    return out


def _estimate(
    conn: Any,
    *,
    src: Row,
    start: float,
    end: float,
    start_sid: int | None,
    end_sid: int | None,
    script: Any,
    mode: str | None,
    voice: str,
    segs: Mapping[str, Any],
    channel_id: str | None,
    job_id: int | None,
    niche: ClipNiche | None,
) -> dict[str, Any]:
    from autoshorts.clipping.edl import layout_clip
    from autoshorts.clipping.originality import build_originality_report

    sctx = source_context(conn, src)
    tr = load_transcript(sctx)
    if start_sid is None or end_sid is None:
        start_sid, end_sid = clip_sids(tr, start, end)
    if start_sid is None or end_sid is None:
        raise _invalid(["body", "start_sec"], "В отрезке нет речи", "no_words")
    res = lint_author_script(script, tr, start_sid, end_sid, mode=mode or "operator", niche=niche)
    takes = _estimate_takes(res.script.vo_parts(), voice, segs)
    duration = float(src.get("duration_sec") or tr.duration or end)
    edl = layout_clip(
        tr,
        start=start,
        end=end,
        start_sid=start_sid,
        end_sid=end_sid,
        script=res.script,
        takes=takes,
        source={"duration": duration, "width": src.get("width") or 0, "height": src.get("height") or 0},
        media_range=(0.0, duration),
        meta={"author_mode": res.mode},
    )
    settings = _settings(conn)
    previous, texts, _library = originality_history(
        conn, source_id=int(src["id"]), channel_id=channel_id, job_id=job_id
    )
    kinds = sorted({str(t["kind"]) for t in takes.values()})
    report = build_originality_report(
        share=edl.author_share(),
        rights=rights_of(sctx),
        rights_warnings=rights_warnings_of(sctx),
        watermark=watermark_of(sctx),
        edl=edl,
        transcript=tr,
        previous_clips=previous,
        library_similarity=None,
        library_note="оценка до рендера: похожесть картинки считается после рендера",
        author_mode=res.mode,
        operator_edited=res.mode == "operator",
        script_issues=[i.to_dict() for i in res.issues],
        author_parts=res.script.vo_parts(),
        previous_author_texts=texts,
        voice_kinds=kinds,
        min_share=float(settings.clip_author_share_min),
        gate_for_own=bool(settings.clip_author_share_gate_for_own),
    )
    return report_out(report.to_dict(), stage="estimate", computed_at=datetime.now(UTC))


def estimate_candidate_originality(conn: Any, candidate_id: int, body: Any) -> dict[str, Any]:
    """Оценка 70/30 до рендера для кандидата: сценарий из запроса или шаблонный черновик."""
    cand = _require_candidate(conn, candidate_id)
    src = require_source(conn, int(cand["source_id"]))
    start = float(body.start_sec) if body.start_sec is not None else float(cand["start_sec"])
    end = float(body.end_sec) if body.end_sec is not None else float(cand["end_sec"])
    if end <= start:
        raise _invalid(["body", "end_sec"], "Конец раньше начала", "edge_order")
    same = body.start_sec is None and body.end_sec is None
    niche = niche_profile(conn, src.get("niche_id"))
    sctx = source_context(conn, src)
    tr = load_transcript(sctx)
    s_sid = cand.get("start_sid") if same else None
    e_sid = cand.get("end_sid") if same else None
    if s_sid is None or e_sid is None:
        s_sid, e_sid = clip_sids(tr, start, end)
    if s_sid is None or e_sid is None:
        raise _invalid(["body", "start_sec"], "В отрезке нет речи", "no_words")
    if body.script is not None:
        script, mode = body.script.model_dump(mode="json"), "operator"
    else:
        from autoshorts.clipping.author.script import clip_sentences, draft_script_heuristic

        sents = clip_sentences(tr, int(s_sid), int(e_sid))
        script = draft_script_heuristic([(s.sid, s.text) for s in sents], title=str(cand.get("title") or ""))
        mode = "heuristic"
    voice = str(
        body.voice.value if body.voice is not None and hasattr(body.voice, "value") else body.voice or "tts"
    )
    return _estimate(
        conn,
        src=src,
        start=start,
        end=end,
        start_sid=int(s_sid),
        end_sid=int(e_sid),
        script=script,
        mode=mode,
        voice=voice,
        segs={},
        channel_id=body.channel_id,
        job_id=None,
        niche=niche,
    )


def estimate_job_originality(conn: Any, job_id: int, body: Any) -> dict[str, Any]:
    """Оценка до рендера для ролика: текущий сценарий и реплики (или сценарий из запроса)."""
    job = _require_clip_job(conn, job_id)
    state = clips_repo.get_clip_state(conn, job_id) or {}
    src = require_source(conn, int(job["source_id"]))
    data = state.get("author_script") if isinstance(state.get("author_script"), dict) else None
    start = float(body.start_sec) if body.start_sec is not None else float(job["clip_start_sec"])
    end = float(body.end_sec) if body.end_sec is not None else float(job["clip_end_sec"])
    if end <= start:
        raise _invalid(["body", "end_sec"], "Конец раньше начала", "edge_order")
    same = body.start_sec is None and body.end_sec is None
    if body.script is not None:
        script, mode = body.script.model_dump(mode="json"), "operator"
    elif data is not None:
        script, mode = data.get("script") or {}, str(data.get("mode") or "operator")
    else:
        sctx = source_context(conn, src)
        tr = load_transcript(sctx)
        s_sid, e_sid = clip_sids(tr, start, end)
        from autoshorts.clipping.author.script import clip_sentences, draft_script_heuristic

        sents = clip_sentences(tr, int(s_sid or 0), int(e_sid or 0))
        script, mode = (
            draft_script_heuristic([(s.sid, s.text) for s in sents], title=str(job.get("topic") or "")),
            "heuristic",
        )
    layer = job.get("author_layer") or {}
    voice = (
        body.voice.value
        if body.voice is not None and hasattr(body.voice, "value")
        else (body.voice or layer.get("voice") or "tts")
    )
    segs = (
        ((state.get("author_voice") or {}).get("segments") or {})
        if isinstance(state.get("author_voice"), dict)
        else {}
    )
    niche = niche_profile(conn, job.get("niche_id") or src.get("niche_id"), product_id=job.get("product_id"))
    return _estimate(
        conn,
        src=src,
        start=start,
        end=end,
        start_sid=state.get("clip_start_sid") if same else None,
        end_sid=state.get("clip_end_sid") if same else None,
        script=script,
        mode=mode,
        voice=str(voice),
        segs=segs,
        channel_id=body.channel_id or job.get("channel_id"),
        job_id=job_id,
        niche=niche,
    )


def originality_view(state: Row | None) -> dict[str, Any] | None:
    rep = (state or {}).get("originality")
    if not isinstance(rep, dict) or not rep.get("checks"):
        return None
    try:
        return report_out(rep, stage=str(rep.get("stage") or "final"), computed_at=rep.get("computed_at"))
    except Exception:  # noqa: BLE001 - старый формат отчёта не роняет экран
        log.warning("job originality report unreadable", exc_info=True)
        return None


def get_originality(conn: Any, job_id: int) -> dict[str, Any]:
    _require_clip_job(conn, job_id)
    out = originality_view(clips_repo.get_clip_state(conn, job_id))
    if out is None:
        raise NotFound("originality", "Отчёта об оригинальности ещё нет: ролик не прошёл QC")
    return out


# ============================================================================ раскладка ролика (редактор)

LAYOUT_LOCKED_STATUSES = ("approved", "published", "rejected", "cancelled")
LAYOUT_RERENDER_STATUSES = ("pending_review", "qc_failed", "rework", "failed")
LAYOUT_RENDER_STAGES = ("render", "qc")


def _rerender_state(job: Row) -> tuple[bool, str | None]:
    status = str(job["status"])
    if status in LAYOUT_RERENDER_STATUSES:
        return True, None
    hints = {
        "approved": "Ролик одобрен: раскладка закреплена, перерендер недоступен",
        "published": "Ролик опубликован: раскладка закреплена",
        "rejected": "Ролик отклонён",
        "cancelled": "Ролик отменён",
        "processing": "Ролик в работе: раскладка применится в ближайшем рендере",
        "draft": "Ролик ещё не рендерился: раскладка применится при рендере",
        "queued": "Ролик ещё не рендерился: раскладка применится при рендере",
    }
    return False, hints.get(status, "Перерендер сейчас недоступен")


def _layout_texts(state: Row) -> dict[str, Any]:
    data = state.get("author_script") if isinstance(state.get("author_script"), dict) else {}
    script = data.get("script") if isinstance(data.get("script"), dict) else {}
    title = str(script.get("on_screen_title") or "").strip() or None
    onscreen: list[str] = []
    cold = script.get("cold_open") if isinstance(script.get("cold_open"), dict) else {}
    if str(cold.get("on_screen") or "").strip():
        onscreen.append(str(cold["on_screen"]).strip())
    for ins in script.get("inserts") or []:
        if isinstance(ins, dict) and str(ins.get("on_screen") or "").strip():
            onscreen.append(str(ins["on_screen"]).strip())
    return {"title": title, "onscreen": onscreen}


def _rendered_layout(job: Row) -> dict[str, Any] | None:
    payload = job.get("stage_payload") or {}
    render = payload.get("clip_render") if isinstance(payload.get("clip_render"), dict) else None
    if not render:
        return None
    layout = render.get("layout") if isinstance(render.get("layout"), dict) else {}
    overlap: list[str] = []
    for w in render.get("warnings") or []:
        if isinstance(w, str) and w.startswith("captions_overlap_banner"):
            _, _, zones = w.partition(":")
            overlap += [z for z in zones.split(",") if z]
    conflicts = [
        c
        for c in layout.get("conflicts") or []
        if isinstance(c, dict) and isinstance(c.get("rect"), dict) and {"a", "b", "overlap_px"} <= set(c)
    ]
    return {
        "manual": bool(layout.get("manual")),
        "overlap_zones": sorted(set(overlap)),
        "conflicts": conflicts,
    }


def job_layout_view(conn: Any, job_id: int) -> dict[str, Any]:
    """Схема `JobLayout`: расчёт «как будет» тем же кодом, что рендер (без ffmpeg и текста)."""
    from autoshorts.clipping.layout_override import LayoutSpec, compute_layout

    job = _require_clip_job(conn, job_id)
    state = clips_repo.get_clip_state(conn, job_id) or {}
    spec = layout_spec_of(job) or LayoutSpec()
    profile = _safe_zone_profile(job.get("channel_platform"))
    variant, sizes = None, {}
    if job.get("banner_variant_id") is not None:
        try:
            variant, _creatives, sizes = _variant_inputs(conn, int(job["banner_variant_id"]))
        except NotFound:
            variant = None
    texts = _layout_texts(state)
    empty = [z for z, has in (("title", texts["title"]), ("onscreen", texts["onscreen"])) if not has]
    comp = compute_layout(variant, sizes, profile, spec, empty_zones=empty)
    allowed, hint = _rerender_state(job)
    return {
        "job_id": job_id,
        "status": job["status"],
        "canvas": {"w": profile.canvas_w, "h": profile.canvas_h},
        "profile_id": profile.id,
        "safe": profile.safe_rect.edges(),
        "offer_area": profile.offer_area.rect.edges() if profile.offer_area else None,
        "micro_brand_area": profile.micro_brand_area.rect.edges() if profile.micro_brand_area else None,
        "override": spec.to_json(),
        "auto": comp.auto.to_json(),
        "effective": comp.effective.to_json(),
        "texts": texts,
        "rendered": _rendered_layout(job),
        "rerender_allowed": allowed,
        "rerender_hint": hint,
        "rerender_started": False,
    }


def put_job_layout(conn: Any, job_id: int, body: Any, caller: Caller) -> dict[str, Any]:
    """Сохранить `author_layer.layout`; `rerender=true` — повтор со стадии render.

    С приёмки (pending_review | qc_failed) — решением «на доработку» со стадии render (учитывается в
    попытках, отменяется как обычное решение); rework → перезапуск; failed → retry. Одобренный,
    опубликованный и закрытый ролик, а также идущий рендер — 409 `job_state_conflict`.
    """
    from autoshorts.repos import jobs as jobs_repo
    from autoshorts.services import audit

    job = _require_clip_job(conn, job_id, for_update=True)
    status = str(job["status"])
    if status in LAYOUT_LOCKED_STATUSES:
        raise JobStateConflict(
            "Раскладка закреплена: ролик одобрен или закрыт",
            details={"job_id": job_id, "status": status, "hint": "clone"},
        )
    if status == "processing" and job.get("stage") in LAYOUT_RENDER_STAGES:
        raise JobStateConflict(
            "Ролик сейчас рендерится — дождитесь окончания",
            details={"job_id": job_id, "status": status, "stage": job.get("stage")},
        )
    layer = dict(job.get("author_layer") or {}) if isinstance(job.get("author_layer"), Mapping) else {}
    layer["layout"] = body.layout.model_dump(mode="json")
    updated = jobs_repo.update(conn, job_id, {"author_layer": layer}, allowed_statuses=(status,))
    if updated is None:
        raise JobStateConflict("Статус ролика изменился, обновите экран", details={"job_id": job_id})
    audit.record(
        conn,
        caller.actor,
        "job.layout",
        "job",
        job_id,
        summary=f"{job['video_uid']}: раскладка {'вручную' if body.layout.mode == 'manual' else 'авто'}",
        diff={"layout": layer["layout"], "rerender": bool(body.rerender)},
    )
    restarted = False
    if body.rerender:
        restarted = _rerender_from_render(conn, job_id, status, updated, caller, "Раскладка: перерендер")
    view = job_layout_view(conn, job_id)
    view["rerender_started"] = restarted
    return view


def _rerender_from_render(
    conn: Any, job_id: int, status: str, updated: Row, caller: Caller, comment: str
) -> bool:
    """Повтор со стадии render: с приёмки — решением «на доработку» (как кнопка), из rework — перезапуск,
    упавший — retry. Иначе False (ролик в работе или ещё не рендерился — правка применится сама)."""
    from autoshorts.services import jobs as jobs_service

    if status in ("pending_review", "qc_failed"):
        from autoshorts.services import review as review_service

        review_service.decide(
            conn,
            job_id,
            review_service.DecisionInput(
                decision="rework",
                expected_status=status,
                rework_from_stage="render",
                auto_restart=True,
                comment=comment,
            ),
            caller,
        )
        return True
    if status == "rework":
        after = jobs_service.restart_from_stage(
            conn, updated, "render", allowed=("rework",), fields={"rework_from_stage": "render"}
        )
        jobs_service.audit_job(
            conn, caller, "job.start", after, before=updated, diff={"from_stage": "render"}
        )
        jobs_service.after_transition(conn, updated, after)
        return True
    if status == "failed":
        jobs_service.retry_job(conn, job_id, "render", caller)
        return True
    return False


def job_captions_view(conn: Any, job_id: int) -> dict[str, Any]:
    job = _require_clip_job(conn, job_id)
    layer = job.get("author_layer") if isinstance(job.get("author_layer"), Mapping) else {}
    mode = str((layer or {}).get("captions") or "both")
    allowed, hint = _rerender_state(job)
    return {
        "job_id": job_id,
        "enabled": mode != "none",
        "mode": mode,
        "rerender_allowed": allowed,
        "rerender_hint": hint,
        "rerender_started": False,
    }


def put_job_captions(conn: Any, job_id: int, body: Any, caller: Caller) -> dict[str, Any]:
    """Субтитры одной галочкой: `author_layer.captions` = none | both и сразу перерендер со стадии render.
    409 у одобренного/закрытого ролика и пока идёт рендер (как у раскладки)."""
    from autoshorts.repos import jobs as jobs_repo
    from autoshorts.services import audit

    job = _require_clip_job(conn, job_id, for_update=True)
    status = str(job["status"])
    if status in LAYOUT_LOCKED_STATUSES:
        raise JobStateConflict(
            "Ролик одобрен или закрыт: субтитры уже не поменять",
            details={"job_id": job_id, "status": status, "hint": "clone"},
        )
    if status == "processing" and job.get("stage") in LAYOUT_RENDER_STAGES:
        raise JobStateConflict(
            "Ролик сейчас рендерится — дождитесь окончания",
            details={"job_id": job_id, "status": status, "stage": job.get("stage")},
        )
    layer = dict(job.get("author_layer") or {}) if isinstance(job.get("author_layer"), Mapping) else {}
    before = str(layer.get("captions") or "both")
    layer["captions"] = "both" if body.enabled else "none"
    updated = jobs_repo.update(conn, job_id, {"author_layer": layer}, allowed_statuses=(status,))
    if updated is None:
        raise JobStateConflict("Статус ролика изменился, обновите экран", details={"job_id": job_id})
    audit.record(
        conn, caller.actor, "job.captions", "job", job_id,
        summary=f"{job['video_uid']}: субтитры {'включены' if body.enabled else 'выключены'}",
        diff={"captions": [before, layer["captions"]], "rerender": bool(body.rerender)},
    )  # fmt: skip
    restarted = False
    if body.rerender and before != layer["captions"]:
        restarted = _rerender_from_render(
            conn, job_id, status, updated, caller,
            "Субтитры " + ("включены" if body.enabled else "выключены") + ": перерендер",
        )  # fmt: skip
    view = job_captions_view(conn, job_id)
    view["rerender_started"] = restarted
    return view


CHANNEL_CHANGE_STATUSES = ("pending_review", "qc_failed")


def job_channel_view(conn: Any, job_id: int) -> dict[str, Any]:
    from autoshorts.repos import jobs as jobs_repo

    job = _require_clip_job(conn, job_id)
    ch = jobs_repo.get_channel(conn, str(job["channel_id"])) or {}
    status = str(job["status"])
    allowed = status in CHANNEL_CHANGE_STATUSES
    hint = None if allowed else (
        "Ролик ещё в работе — канал можно сменить, когда он придёт в приёмку"
        if status in ("queued", "processing", "draft", "rework")
        else "Ролик одобрен или закрыт: канал уже не сменить"
    )
    return {
        "job_id": job_id,
        "channel_id": str(job["channel_id"]),
        "channel_title": ch.get("title"),
        "banner_variant_id": job.get("banner_variant_id"),
        "change_allowed": allowed,
        "change_hint": hint,
    }


def put_job_channel(conn: Any, job_id: int, body: Any, caller: Caller) -> dict[str, Any]:
    """Сменить канал ролика в приёмке: после одобрения пакет и автозалив пойдут в план нового канала.
    Баннер канала по умолчанию меняется на баннер нового канала (особый, выбранный вручную, остаётся),
    снимок канала обновляется, `rerender=true` — перерендер в оформлении нового канала. 409 вне приёмки."""
    from autoshorts.repos import jobs as jobs_repo
    from autoshorts.services import audit
    from autoshorts.services import jobs as jobs_service

    job = _require_clip_job(conn, job_id, for_update=True)
    status = str(job["status"])
    if status not in CHANNEL_CHANGE_STATUSES:
        raise JobStateConflict(
            job_channel_view(conn, job_id)["change_hint"] or "Канал сейчас не сменить",
            details={"job_id": job_id, "status": status},
        )
    old = jobs_repo.get_channel(conn, str(job["channel_id"])) or {}
    new = jobs_repo.get_channel(conn, str(body.channel_id))
    if new is None:
        raise NotFound("channel", "Канал не найден")
    if new["status"] in ("terminated", "retired"):
        raise _invalid(["body", "channel_id"], "Канал выведен из работы", "channel_inactive")
    if old and new["platform"] != old.get("platform"):
        raise _invalid(["body", "channel_id"], "Канал другой площадки: ролик собран под её требования", "platform_mismatch")
    if str(new["id"]) == str(job["channel_id"]):
        view = job_channel_view(conn, job_id)
        view["rerender_started"] = False
        return view
    # баннер: если стоял баннер «по умолчанию» какого-то канала (или не стоял) — берём баннер нового канала
    defaults = {
        int(r["v"])
        for r in conn.execute(
            "SELECT (profile->>'banner_variant_id') AS v FROM channels WHERE profile ? 'banner_variant_id'"
        ).fetchall()
        if str(r["v"] or "").isdigit()
    }
    variant_id = job.get("banner_variant_id")
    if variant_id is None or int(variant_id) in defaults:
        variant_id = jobs_service.channel_default_variant(conn, new)
    campaign_id, variant_id, product_id, banner = jobs_service._resolve_banner(
        conn, campaign_id=None, banner_variant_id=variant_id, channel=new
    )
    snapshot = dict(job.get("config_snapshot") or {})
    snapshot["channel"] = {"id": new["id"], "platform": new["platform"], "profile": new.get("profile") or {}}
    snapshot["banner"] = banner
    fields: dict[str, Any] = {"channel_id": new["id"], "config_snapshot": snapshot, "banner_variant_id": variant_id}
    if variant_id is not None:
        fields.update({"campaign_id": campaign_id, "product_id": product_id})
    updated = jobs_repo.update(conn, job_id, fields, allowed_statuses=(status,))
    if updated is None:
        raise JobStateConflict("Статус ролика изменился, обновите экран", details={"job_id": job_id})
    audit.record(
        conn, caller.actor, "job.channel", "job", job_id,
        summary=f"{job['video_uid']}: канал {job['channel_id']} → {new['id']}",
        diff={"channel_id": [job["channel_id"], new["id"]], "banner_variant_id": [job.get("banner_variant_id"), variant_id],
              "rerender": bool(body.rerender)},
    )  # fmt: skip
    restarted = False
    if body.rerender:
        restarted = _rerender_from_render(
            conn, job_id, status, updated, caller, f"Канал → {new.get('title') or new['id']}: перерендер в его оформлении"
        )
    view = job_channel_view(conn, job_id)
    view["rerender_started"] = restarted
    return view


# ============================================================================ представления для jobs и review


def job_view_extras(conn: Any, job: Row) -> dict[str, Any]:
    """Необязательные поля `Job` для нарезки: `author_script` (кратко) и `originality`."""
    if job.get("kind") != "clip":
        return {}
    try:
        state = clips_repo.get_clip_state(conn, int(job["id"]))
        return {"author_script": author_summary(state), "originality": originality_view(state)}
    except Exception:  # noqa: BLE001 - дополнительные поля не роняют карточку ролика
        log.warning("job %s: clip extras unavailable", job.get("id"), exc_info=True)
        return {}


def attach_originality_verdicts(conn: Any, items: list[dict[str, Any]], *, id_key: str = "id") -> None:
    """`JobRow` / `ReviewQueueItem`: итог отчёта об оригинальности (у нарезки)."""
    ids = [int(i[id_key]) for i in items if i.get("kind") == "clip"]
    verdicts = clips_repo.originality_verdicts(conn, ids) if ids else {}
    for item in items:
        if item.get("kind") == "clip":
            item["originality_verdict"] = verdicts.get(int(item[id_key]))


def review_extras(conn: Any, job: Row) -> dict[str, Any]:
    """`ReviewContext.source/author_script/originality` для нарезки."""
    if job.get("kind") != "clip" or job.get("source_id") is None:
        return {}
    try:
        state = clips_repo.get_clip_state(conn, int(job["id"])) or {}
        src = clips_repo.get_source(conn, int(job["source_id"]))
        source = None
        if src is not None and _rights_type_out(src.get("rights_type")) is not None:
            source = {
                "source_id": int(src["id"]),
                "title": str(src.get("title") or ""),
                "start_sec": _f(job.get("clip_start_sec")),
                "end_sec": _f(job.get("clip_end_sec")),
                "rights_type": _rights_type_out(src.get("rights_type")),
                "owner_name": str(src.get("owner_name") or ""),
                "attribution_text": src.get("attribution_text"),
                "license": src.get("license"),
                "watermark_suspect": bool(src.get("watermark_suspect")),
            }
        data = state.get("author_script") if isinstance(state.get("author_script"), dict) else None
        author = None
        if data is not None:
            segs = (
                ((state.get("author_voice") or {}).get("segments") or {})
                if isinstance(state.get("author_voice"), dict)
                else {}
            )
            kinds = sorted(
                {
                    str(d.get("kind"))
                    for d in segs.values()
                    if isinstance(d, dict)
                    and d.get("status") == "ready"
                    and d.get("kind") in ("tts", "operator", "mock")
                }
            )
            summary = author_summary(state) or {}
            author = {
                "mode": summary.get("mode"),
                "label": summary.get("label"),
                "needs_operator_edit": summary.get("needs_operator_edit", False),
                "blocked": summary.get("blocked", False),
                "script": _script_body(data),
                "issues": _issues_out(data),
                "voice_kinds": kinds,
            }
        return {"source": source, "author_script": author, "originality": originality_view(state)}
    except Exception:  # noqa: BLE001
        log.warning("job %s: review extras unavailable", job.get("id"), exc_info=True)
        return {}


__all__ = [
    "ClipNiche",
    "MediaFile",
    "PaidCalls",
    "SEGMENTS",
    "SourceContext",
    "attach_originality_verdicts",
    "author_result_from_dict",
    "author_script_view",
    "author_voice_file",
    "author_voice_view",
    "banner_setup",
    "candidate_view",
    "clip_source",
    "create_candidate",
    "create_clips_batch",
    "create_job_from_candidate",
    "delete_author_voice",
    "draft_author_script",
    "ensure_tracking_code",
    "estimate_candidate_originality",
    "estimate_job_originality",
    "fetch_section",
    "section_start",
    "get_candidate",
    "get_originality",
    "job_layout_view",
    "job_view_extras",
    "layout_spec_of",
    "lint_author_script",
    "list_candidates",
    "llm_client",
    "load_transcript",
    "on_clip_job_created",
    "patch_candidate",
    "put_author_script",
    "put_job_layout",
    "reframe_plan_from_json",
    "reframe_plan_to_json",
    "report_out",
    "request_author_draft",
    "request_selection",
    "review_extras",
    "run_author_script_draft",
    "run_author_voice_prepare",
    "run_source_select",
    "source_context",
    "upload_author_voice",
]
