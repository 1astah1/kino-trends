"""Источники нарезки: приём с правами, карточка с прогрессом, медиа студии, конвейер задач.

docs/ARCHITECTURE.md §5.2 «Загрузки, источники, нарезка», §6.3.1–§6.3.3, §8.2; OWNER-DECISIONS п. 5.

**Нет прав — нет источника.** Права проверяет `SourceRightsIn` (внутри — `source_ingest.validate_rights`
движка), предупреждения пишутся в `source_rights.warnings`. Источник по ссылке качается только после
записи прав (`source_download` перепроверяет их до сети) и только своё или лицензированное видео.

Конвейер (задачи очереди, обработчики — `tasks/sources.py`):

    upload:  source_prepare → source_transcribe_* → source_select → ready
    url:     source_download → source_prepare → source_transcribe_* → source_select → ready

* `source_download` (light, 110): yt-dlp audio-first — `bestaudio[ext=m4a]` и видео ≤ 540p avc1,
  5 МБ/с, по одной загрузке за раз на хост (вторая откладывается);
* `source_prepare` (heavy, 120): ffprobe + sha256 → прокси 540p (GOP 1 с), audio.m4a, audio16k.wav,
  peaks.json, раскадровка, проверка чужих логотипов (`clipping.source_ingest.prepare_source`);
* ASR: локальный (`gigaam_v3_onnx`, `mock_asr`) — `source_transcribe_chunk` (heavy, 150) кусками
  по 10 мин, чтобы рендеры вклинивались между кусками; облачный (`yandex_speechkit`,
  `openai_compat_asr`) — `source_transcribe_cloud` (light, 150, платно). Распознавание всегда идёт в
  дочернем процессе (модель ~0,6 ГБ освобождается сразу), `audio16k.wav` удаляется после ASR;
* `source_select` (light, 160): LLM, если настроен ключ (LLM_API_KEY / секрет провайдера,
  OpenAI-совместимый, по умолчанию https://umka.chat/v1), иначе эвристика — режим и подпись
  пишутся в `sources.selection`, у кандидатов — `origin`. Прежние автоматические кандидаты без
  clip-job получают `superseded_at`;
* `source_scenes` (heavy, 170, по запросу): склейки PySceneDetect на прокси → `sources.scenes`.

Шаг продолжает цепочку (`payload.chain`) в той же транзакции, что и свой успех (`ctx.on_commit`).
События SSE: `source_status {source_id, status, pct}` при смене статуса, `task_progress` — воркер.

Для M5-B (повторный отбор `POST /sources/{id}/select`): `enqueue_select(conn, source_id, …)`.
Для стенда (демо-источник `sources/cc_shalyopa_cgevent`): `register_prepared_dir(...)`.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import logging
import math
import os
import secrets as pysecrets
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from psycopg import sql

from autoshorts.api.schemas.sources import (
    RIGHTS_WARNING_LABELS,
    SELECTION_MODE_LABELS,
    SourceOptions,
    SourceRightsIn,
)
from autoshorts.core.errors import AppError, Conflict, NotFound, ValidationFailed
from autoshorts.core.storage import get_layout
from autoshorts.db.notify import publish_event
from autoshorts.domain.enums import RightsType, SourceStatus
from autoshorts.domain.events import EventType
from autoshorts.repos import sources as sources_repo
from autoshorts.repos import transcripts as transcripts_repo
from autoshorts.services import audit
from autoshorts.services.audit import Actor

log = logging.getLogger(__name__)

Row = dict[str, Any]

URL_PREFIX = "/api/v1/sources"
PIPELINE_ORDER: tuple[str, ...] = ("download", "prepare", "transcribe", "select")
STEP_ORDER: tuple[str, ...] = (*PIPELINE_ORDER, "scenes")
STEP_STATUS: dict[str, str] = {
    "download": SourceStatus.DOWNLOADING.value,
    "prepare": SourceStatus.PREPARING.value,
    "transcribe": SourceStatus.TRANSCRIBING.value,
    "select": SourceStatus.SELECTING.value,
}
KIND_STEP: dict[str, str] = {
    "source_download": "download",
    "source_prepare": "prepare",
    "source_transcribe_chunk": "transcribe",
    "source_transcribe_cloud": "transcribe",
    "source_select": "select",
    "source_scenes": "scenes",
}
STEP_WEIGHTS: dict[str, float] = {"download": 10.0, "prepare": 25.0, "transcribe": 45.0, "select": 20.0}
IN_PROGRESS_STATUSES = frozenset({"queued", "downloading", "preparing", "transcribing", "selecting"})
CHUNK_SEC = 600.0
"""Длина куска локального ASR (§6.1: длинный ASR режется на ~10 мин, рендеры вклиниваются)."""

# provider_configs.key → ключ реестра providers/asr
ASR_ENGINE_KEYS: dict[str, str] = {
    "gigaam_v3_onnx": "gigaam_v3_onnx",
    "yandex_speechkit": "yandex_speechkit",
    "openai_compat_asr": "openai_compat_asr",
    "mock_asr": "mock",
}
ASR_ALIASES: dict[str, str] = {
    "mock": "mock_asr",
    "gigaam": "gigaam_v3_onnx",
    "gigaam_onnx": "gigaam_v3_onnx",
    "speechkit": "yandex_speechkit",
    "openai_compat": "openai_compat_asr",
}
LOCAL_ASR = frozenset({"gigaam_v3_onnx", "mock_asr"})
CHUNKED_ASR = frozenset({"gigaam_v3_onnx"})
ASR_SECRET_ENV: dict[str, tuple[str, ...]] = {
    "yandex_speechkit": ("YANDEX_SPEECHKIT_API_KEY",),
    "openai_compat_asr": ("ASR_CLOUD_API_KEY",),
}
MOCK_ASR_PARAMS = ("fixture", "text", "paragraph_pause")

SOURCE_DIR_PREFIX = "sources/"


class SourceRejected(AppError):
    status = 422
    code = "source_rejected"
    default_message = "Источник не принят"


class UnsupportedUrl(AppError):
    status = 422
    code = "unsupported_url"
    default_message = "Ссылка не поддерживается"


def field_error(field_name: str, message: str, kind: str = "invalid") -> ValidationFailed:
    return ValidationFailed(
        message, details={"errors": [{"loc": ["body", field_name], "msg": message, "type": kind}]}
    )


def _source_not_ready(message: str, **details: Any) -> Conflict:
    return Conflict(message, code="source_not_ready", details=details)


# ---------------------------------------------------------------- каталоги и файлы


def media_dir_of(row: Mapping[str, Any]) -> str:
    """Каталог источника относительно MEDIA_ROOT (NULL в БД → `sources/<id>`)."""
    return str(row.get("media_dir") or f"{SOURCE_DIR_PREFIX}{int(row['id'])}")


def source_dir(row: Mapping[str, Any], *, create: bool = False) -> Path:
    path = get_layout().root / media_dir_of(row)
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o750)
    return path


def load_manifest(row: Mapping[str, Any]) -> Any | None:
    """`PreparedSource` из `source.json` каталога источника (None, если источник не подготовлен)."""
    from autoshorts.clipping.source_ingest import MANIFEST_NAME, PreparedSource

    d = source_dir(row)
    if not (d / MANIFEST_NAME).is_file():
        return None
    try:
        return PreparedSource.load(d)
    except (OSError, ValueError, KeyError) as exc:
        log.warning("source %s: unreadable manifest: %s", row.get("id"), exc)
        return None


def _rel_in_dir(row: Mapping[str, Any], name: str | None) -> str | None:
    if not name or not isinstance(name, str) or name.startswith("/"):
        return None
    return f"{media_dir_of(row)}/{name}"


def _file_exists(rel: str | None) -> bool:
    if not rel:
        return False
    try:
        return get_layout().resolve(rel).is_file()
    except ValueError:
        return False


# ---------------------------------------------------------------- права


def rights_engine_dict(rights_row: Mapping[str, Any]) -> dict[str, Any]:
    """Строка `source_rights` → запись прав движка (`RightsRecord.to_db_dict()`)."""
    keys = (
        "rights_type", "owner_name", "owner_channel_url", "proof_note", "proof_file_path",
        "allowlist_status", "attribution_text", "license", "license_url",
    )  # fmt: skip
    out = {k: rights_row.get(k) for k in keys}
    vu = rights_row.get("valid_until")
    out["valid_until"] = vu.isoformat() if isinstance(vu, date) else vu
    return out


def gate_applies(rights_type: str, settings: Any | None = None) -> bool:
    if rights_type != RightsType.OWN.value:
        return True
    if settings is None:
        from autoshorts.services.settings import get_runtime_settings

        settings = get_runtime_settings()
    return bool(settings.clip_author_share_gate_for_own)


def rights_to_api(row: Mapping[str, Any], source_id: int) -> dict[str, Any]:
    return {
        "rights_type": row["rights_type"],
        "owner_name": row["owner_name"],
        "owner_channel_url": row["owner_channel_url"],
        "proof_note": row["proof_note"],
        "proof_file_url": None,  # отдельного эндпоинта для документа нет в контракте (см. known issues)
        "allowlist_status": row["allowlist_status"],
        "attribution_text": row["attribution_text"],
        "license": row["license"],
        "license_url": row["license_url"],
        "valid_until": row["valid_until"],
        "warnings": [
            {"code": w, "label": RIGHTS_WARNING_LABELS.get(w, w)} for w in (row.get("warnings") or [])
        ],
        "gate_applies": gate_applies(row["rights_type"]),
        "recorded_by": row["recorded_by"],
        "recorded_at": row["recorded_at"],
        "updated_at": row["updated_at"],
    }


def _rights_fields(rights: SourceRightsIn, *, platform: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Поля `source_rights` из запроса: запись движка + его предупреждения (+ сверка с площадкой)."""
    from autoshorts.clipping.source_ingest import platform_rights_warnings

    rec, warnings = rights.validate_with_engine()
    if platform:
        warnings = warnings + platform_rights_warnings(rec, dict(platform))
    d = rec.to_db_dict()
    return {
        "rights_type": d["rights_type"],
        "owner_name": d["owner_name"],
        "owner_channel_url": d.get("owner_channel_url"),
        "proof_note": d["proof_note"],
        "proof_file_path": None,
        "allowlist_status": d.get("allowlist_status") or "unknown",
        "attribution_text": d.get("attribution_text"),
        "license": d.get("license"),
        "license_url": d.get("license_url"),
        "valid_until": rec.valid_until,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _attach_proof(conn: Any, principal: Any, upload_id: str | None, source_id: int) -> str | None:
    """Документ прав (tus, purpose=rights_doc) → `MEDIA_ROOT/rights/<source_id>/`, загрузка consumed."""
    if not upload_id:
        return None
    from autoshorts.repos import uploads as uploads_repo
    from autoshorts.services import uploads_tus

    up = uploads_tus.take_complete_upload(conn, upload_id, principal, purpose="rights_doc")
    layout = get_layout()
    src = uploads_tus.upload_path(up)
    dst_dir = layout.rights_dir(source_id, create=True)
    dst = dst_dir / f"proof_{upload_id[:8]}.{uploads_tus.file_ext(up['filename'])}"
    os.replace(src, dst)
    rel = layout.relpath(dst)
    uploads_repo.consume(conn, upload_id, path=rel)
    return rel


def _sync_manifest_rights(row: Mapping[str, Any], rights_row: Mapping[str, Any]) -> None:
    """Права в `source.json` = права в БД (клип-джоб движка читает их из манифеста)."""
    prepared = load_manifest(row)
    if prepared is None:
        return
    prepared.rights = rights_engine_dict(rights_row)
    prepared.rights_warnings = list(rights_row.get("warnings") or [])
    try:
        prepared.save()
    except OSError as exc:
        log.warning("source %s: manifest rights not updated: %s", row.get("id"), exc)


# ---------------------------------------------------------------- провайдеры ASR и LLM


@dataclass(frozen=True)
class AsrChoice:
    key: str  # provider_configs.key
    params: dict[str, Any] = field(default_factory=dict)
    secret_name: str | None = None

    @property
    def engine_key(self) -> str:
        return ASR_ENGINE_KEYS[self.key]

    @property
    def local(self) -> bool:
        return self.key in LOCAL_ASR

    @property
    def chunked(self) -> bool:
        return self.key in CHUNKED_ASR

    @property
    def task_kind(self) -> str:
        return "source_transcribe_chunk" if self.local else "source_transcribe_cloud"

    def engine_kwargs(self) -> dict[str, Any]:
        """Аргументы конструктора провайдера (без секретов: они уходят в env дочернего процесса)."""
        p = self.params
        if self.key == "gigaam_v3_onnx":
            return {"threads": int(p.get("threads") or 2)}
        if self.key == "yandex_speechkit":
            mode = str(p.get("mode") or "deferred")
            return {"model": "deferred-general" if mode == "deferred" else "general"}
        if self.key == "openai_compat_asr":
            return {k: p[k] for k in ("model", "base_url") if p.get(k)}
        if self.key == "mock_asr":
            return {k: p[k] for k in MOCK_ASR_PARAMS if p.get(k) is not None}
        return {}

    def child_env(self) -> dict[str, str]:
        """Секреты облачных провайдеров → переменные окружения, которые читает провайдер."""
        from autoshorts.services import secrets as secrets_service

        out: dict[str, str] = {}
        names = ASR_SECRET_ENV.get(self.key, ())
        if not names:
            return out
        value = secrets_service.get_secret(self.secret_name or names[0])
        if value:
            for name in names:
                out[name] = value
        return out


def resolve_asr(conn: Any, key: str | None, *, for_api: bool = False) -> AsrChoice:
    """Провайдер ASR: явный ключ (или алиас) либо default из `provider_configs` (kind=asr)."""
    if key:
        k = ASR_ALIASES.get(key, key)
        row = conn.execute(
            "SELECT key, kind, enabled, params, secret_name FROM provider_configs WHERE key = %(k)s",
            {"k": k},
        ).fetchone()
        if row is None or row["kind"] != "asr" or k not in ASR_ENGINE_KEYS:
            raise field_error(
                "asr_provider", f"Нет такого провайдера распознавания: {key}", "unknown_provider"
            )
        if not row["enabled"]:
            raise field_error("asr_provider", f"Провайдер распознавания {k} выключен", "provider_disabled")
    else:
        row = conn.execute(
            """
            SELECT key, kind, enabled, params, secret_name FROM provider_configs
            WHERE kind = 'asr' AND enabled ORDER BY is_default DESC, key LIMIT 1
            """
        ).fetchone()
        if row is None or row["key"] not in ASR_ENGINE_KEYS:
            row = {"key": "gigaam_v3_onnx", "params": {}, "secret_name": None}
    choice = AsrChoice(str(row["key"]), dict(row.get("params") or {}), row.get("secret_name"))
    if for_api:
        from autoshorts.providers.asr.base import mock_allowed

        if choice.key == "mock_asr" and not mock_allowed():
            raise field_error("asr_provider", "ASR-заглушка в prod выключена", "mock_in_prod")
        if ASR_SECRET_ENV.get(choice.key) and not choice.child_env():
            raise field_error(
                "asr_provider",
                f"Для {choice.key} не задан ключ ({choice.secret_name or ASR_SECRET_ENV[choice.key][0]})",
                "secret_missing",
            )
    return choice


def _asr_for_chain(conn: Any, row: Mapping[str, Any]) -> AsrChoice:
    """Провайдер для шага цепочки: выбранный в опциях источника, а если его с тех пор выключили —
    провайдер по умолчанию (цепочка не должна падать в хуке фиксации успеха прошлого шага)."""
    try:
        return resolve_asr(conn, _options(row).asr_provider)
    except ValidationFailed:
        log.warning(
            "source %s: asr provider %s unavailable, using default", row.get("id"), _options(row).asr_provider
        )
        return resolve_asr(conn, None)


@dataclass
class LlmChoice:
    client: Any  # clipping.llm_client.LLMClient | None
    provider_key: str | None
    configured: bool
    label: str
    note: str | None = None


def selection_llm(conn: Any, selector: str = "auto", *, target_clip_sec: float = 30.0) -> LlmChoice:
    """LLM для отбора (§6.3.3, OWNER-DECISIONS п. 2): default LLM из `provider_configs`.

    * `mock_llm` — детерминированная заглушка (режим mock, подписан);
    * `openai_compat` — ключ из секрета провайдера (LLM_API_KEY: БД, затем env); адрес и модель —
      env `LLM_BASE_URL` / `LLM_MODEL`, затем `params`, затем умолчание (https://umka.chat/v1);
    * без ключа, `selector=heuristic` или выключенный провайдер — эвристика (client=None).
    """
    from autoshorts.clipping.llm_client import DEFAULT_BASE_URL, DEFAULT_MODEL, LLMClient
    from autoshorts.clipping.llm_select import make_mock_client
    from autoshorts.services import secrets as secrets_service

    if selector == "heuristic":
        return LlmChoice(None, None, False, "Эвристика без LLM — выбрана оператором")
    row = conn.execute(
        """
        SELECT key, params, secret_name FROM provider_configs
        WHERE kind = 'llm' AND enabled AND is_default LIMIT 1
        """
    ).fetchone()
    if row is None:
        return LlmChoice(None, None, False, "Эвристика без LLM — LLM-провайдер не включён")
    key = str(row["key"])
    params = dict(row.get("params") or {})
    if key == "mock_llm":
        return LlmChoice(
            make_mock_client(target=target_clip_sec),
            key,
            True,
            "Заглушка LLM (mock) — не модель, только проверка",
        )
    if key != "openai_compat":
        return LlmChoice(None, key, False, f"Эвристика без LLM — провайдер {key} для отбора не поддержан")
    api_key = secrets_service.get_secret(row.get("secret_name") or "LLM_API_KEY", conn=conn)
    if not api_key:
        note = "выбран LLM, но ключ не задан" if selector == "llm" else None
        return LlmChoice(None, key, False, "Эвристика без LLM — ключ LLM не задан", note)

    def _price(env_name: str, param: str) -> float | None:
        raw = (os.environ.get(env_name) or "").strip() or params.get(param)
        try:
            return float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            return None

    base_url = (os.environ.get("LLM_BASE_URL") or "").strip() or params.get("base_url") or DEFAULT_BASE_URL
    model = (os.environ.get("LLM_MODEL") or "").strip() or params.get("model") or DEFAULT_MODEL
    client = LLMClient(
        base_url=str(base_url),
        api_key=api_key,
        model=str(model),
        price_in_per_mtok=_price("LLM_PRICE_IN_PER_MTOK", "price_in_per_mtok"),
        price_out_per_mtok=_price("LLM_PRICE_OUT_PER_MTOK", "price_out_per_mtok"),
    )
    host = urlsplit(str(base_url)).hostname or str(base_url)
    return LlmChoice(client, key, True, f"LLM {model} ({host})")


def llm_configured(conn: Any) -> bool:
    try:
        return selection_llm(conn, "auto").configured
    except Exception:  # noqa: BLE001 - карточка источника не должна падать из-за конфига LLM
        return False


# ---------------------------------------------------------------- постановка шагов


def _publish_status(conn: Any, source_id: int, status: str, pct: float | None = None) -> None:
    data: dict[str, Any] = {"source_id": int(source_id), "status": status}
    if pct is not None:
        data["pct"] = round(max(0.0, min(100.0, float(pct))), 2)
    publish_event(conn, EventType.SOURCE_STATUS, data)


def set_status(conn: Any, source_id: int, status: str, *, error: str | None = None) -> None:
    row = sources_repo.set_status(conn, source_id, status, error=error)
    if row is not None:
        _publish_status(conn, source_id, status, 100.0 if status == SourceStatus.READY.value else None)


def _normalize_chain(steps: Sequence[str]) -> list[str]:
    """Порядок конвейера; новый транскрипт делает прежних кандидатов устаревшими → и отбор."""
    wanted = set(steps)
    if "transcribe" in wanted:
        wanted.add("select")
    return [s for s in PIPELINE_ORDER if s in wanted]


def enqueue_step(
    conn: Any,
    source: Mapping[str, Any] | int,
    step: str,
    *,
    chain: Sequence[str] = (),
    payload: Mapping[str, Any] | None = None,
    asr: AsrChoice | None = None,
    set_source_status: bool = True,
) -> Any:
    """Поставить шаг конвейера источника (dedup: один активный шаг на источник)."""
    from autoshorts.services.tasks import enqueue

    row = source if isinstance(source, Mapping) else sources_repo.get(conn, int(source))
    if row is None:
        raise NotFound("source", "Источник не найден")
    sid = int(row["id"])
    body: dict[str, Any] = {**dict(payload or {}), "chain": [s for s in chain if s != step]}
    if step == "download":
        kind, dedup = "source_download", f"source:{sid}:download"
    elif step == "prepare":
        kind, dedup = "source_prepare", f"source:{sid}:prepare"
    elif step == "transcribe":
        choice = asr or _asr_for_chain(conn, row)
        rev = str(body.get("rev") or pysecrets.token_hex(4))
        idx = int(body.get("idx") or 0)
        body.update({"provider": choice.key, "rev": rev, "idx": idx})
        body.setdefault("start_sec", 0.0)
        kind, dedup = choice.task_kind, f"source:{sid}:transcribe:{rev}:{idx}"
    elif step == "select":
        kind, dedup = "source_select", f"source:{sid}:select"
    elif step == "scenes":
        kind, dedup = "source_scenes", f"source:{sid}:scenes"
    else:
        raise ValueError(f"unknown source step {step!r}")
    res = enqueue(conn, kind, source_id=sid, payload=body, dedup_key=dedup)
    if set_source_status and step in STEP_STATUS:
        set_status(conn, sid, STEP_STATUS[step])
    return res


def advance(conn: Any, source_id: int, chain: Sequence[str], **step_payload: Any) -> Any | None:
    """Шаг завершён: следующий из цепочки или `ready`, если цепочка кончилась."""
    rest = [s for s in chain if s in PIPELINE_ORDER]
    if rest:
        return enqueue_step(conn, source_id, rest[0], chain=rest[1:], payload=step_payload)
    mark_ready(conn, source_id)
    return None


def mark_ready(conn: Any, source_id: int) -> None:
    from autoshorts.services.settings import get_runtime_settings

    rs = get_runtime_settings(conn)
    conn.execute(
        """
        UPDATE sources SET status = 'ready', error = NULL, updated_at = now(),
               proxy_retain_until = COALESCE(proxy_retain_until,
                   CAST(now() AS date) + CAST(%(days)s AS int))
        WHERE id = %(id)s AND deleted_at IS NULL
        """,
        {"id": int(source_id), "days": int(rs.proxy_retain_days)},
    )
    _publish_status(conn, source_id, SourceStatus.READY.value, 100.0)


def mark_failed(source_id: int, message: str) -> None:
    """Окончательный сбой шага (отдельная транзакция: успех задачи откатывается)."""
    from autoshorts.db.pool import connection

    with connection() as conn:
        set_status(conn, source_id, SourceStatus.FAILED.value, error=message[:2000])


def enqueue_select(
    conn: Any,
    source_id: int,
    *,
    params: Mapping[str, Any] | None = None,
    selector: str | None = None,
    niche_id: str | None = None,
    save_as_default: bool = False,
    actor: Actor | None = None,
) -> Any:
    """Повторный отбор (для `POST /sources/{id}/select`, M5-B) → EnqueueResult задачи source_select.

    `params` — ключи `SourceOptions` (target/min/max_clip_sec, max_candidates, target_count,
    detail_pass, tighten_pauses); `save_as_default` пишет их в `sources.options`. 409
    source_not_ready, пока нет транскрипта.
    """
    row = sources_repo.get(conn, source_id, for_update=True)
    if row is None:
        raise NotFound("source", "Источник не найден")
    if transcripts_repo.get_current(conn, source_id) is None:
        raise _source_not_ready("У источника ещё нет транскрипта", source_id=source_id)
    clean = {k: v for k, v in dict(params or {}).items() if v is not None}
    if selector:
        clean["selector"] = selector
    merged = _options(row).model_dump(mode="json") | clean
    try:
        opts = SourceOptions.model_validate(merged)
    except Exception as exc:  # noqa: BLE001 - pydantic: неверное сочетание длин
        raise field_error("target_clip_sec", str(exc).splitlines()[0], "clip_length_order") from None
    if niche_id:
        _check_niche(conn, niche_id)
    if save_as_default:
        sources_repo.update(conn, source_id, {"options": opts.model_dump(mode="json")})
    payload = {"params": opts.model_dump(mode="json"), "niche_id": niche_id}
    res = enqueue_step(conn, row, "select", payload=payload)
    if actor is not None:
        audit.record(
            conn,
            actor,
            "source.select",
            "source",
            source_id,
            summary="повторный отбор моментов",
            diff={"params": clean, "niche_id": niche_id, "task_id": int(res.task["id"])},
        )
    return res


# ---------------------------------------------------------------- опции, ниша, проверка файла


def _options(row: Mapping[str, Any]) -> SourceOptions:
    try:
        return SourceOptions.model_validate(dict(row.get("options") or {}))
    except Exception:  # noqa: BLE001 - кривые опции в БД не должны ронять карточку
        return SourceOptions()


def _check_niche(conn: Any, niche_id: str | None) -> None:
    if (
        niche_id
        and conn.execute("SELECT 1 FROM niches WHERE id = %(id)s", {"id": niche_id}).fetchone() is None
    ):
        raise field_error("niche_id", "Ниша не найдена", "not_found")


def _probe_or_reject(path: Path, settings: Any) -> Any:
    """ffprobe загруженного файла до создания источника: 422 source_rejected с причиной."""
    from autoshorts.pipeline.proxy import ProbeError, probe_media

    try:
        probe = probe_media(path, timeout_sec=60.0)
    except (ProbeError, OSError, ValueError) as exc:
        raise SourceRejected(
            "Файл не читается как видео", details={"reason": "probe_failed", "detail": str(exc)[:300]}
        ) from None
    limit_sec = float(settings.source_max_hours) * 3600.0
    if probe.duration > limit_sec:
        raise SourceRejected(
            f"Источник длиннее {settings.source_max_hours:g} ч",
            details={"reason": "too_long", "duration_sec": round(probe.duration, 1), "limit_sec": limit_sec},
        )
    if probe.file_bytes > float(settings.upload_max_gb) * 1024**3:
        raise SourceRejected(f"Файл больше {settings.upload_max_gb:g} ГБ", details={"reason": "too_big"})
    if not probe.has_video:
        raise SourceRejected("В файле нет видео", details={"reason": "no_video"})
    if not probe.has_audio:
        raise SourceRejected("В файле нет звука: нарезке нужна речь", details={"reason": "no_audio"})
    return probe


def probe_columns(probe: Mapping[str, Any]) -> dict[str, Any]:
    w, h = int(probe.get("width") or 0), int(probe.get("height") or 0)
    if abs(int(probe.get("rotation") or 0)) % 180 == 90:
        w, h = h, w
    return {
        "probe": dict(probe),
        "duration_sec": round(float(probe.get("duration") or 0.0), 2),
        "width": w or None,
        "height": h or None,
        "fps": round(float(probe.get("fps") or 0.0), 3) or None,
        "video_codec": probe.get("video_codec"),
        "has_audio": bool(probe.get("has_audio")),
        "file_bytes": int(probe.get("file_bytes") or 0) or None,
    }


def check_url(url: str) -> str:
    """Только http(s) на публичный хост: ссылки на локальные и внутренние адреса не качаем."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        raise UnsupportedUrl(details={"url": url[:200]})
    bad = host == "localhost" or host.endswith((".localhost", ".local", ".internal"))
    with contextlib.suppress(ValueError):
        ip = ipaddress.ip_address(host.strip("[]"))
        bad = bad or not ip.is_global
    if bad:
        raise UnsupportedUrl("Ссылки на локальные адреса не принимаются", details={"url": url[:200]})
    return url.strip()


# ---------------------------------------------------------------- создание


def _retain_until(settings: Any) -> date:
    return date.today() + timedelta(days=int(settings.original_retain_days))


def create_from_upload(body: Any, principal: Any, actor: Actor) -> dict[str, Any]:
    """POST /sources/from-upload → Source (202): файл → `sources/<id>/original.<ext>`, source_prepare."""
    from autoshorts.db.pool import connection
    from autoshorts.repos import uploads as uploads_repo
    from autoshorts.services import uploads_tus
    from autoshorts.services.settings import get_runtime_settings

    rights_fields = _rights_fields(body.rights)
    layout = get_layout()
    moved: tuple[Path, Path] | None = None
    try:
        with connection() as conn:
            settings = get_runtime_settings(conn)
            upload = uploads_tus.take_complete_upload(conn, body.upload_id, principal, purpose="source")
            _check_niche(conn, body.niche_id)
            options = body.options or SourceOptions()
            asr = resolve_asr(conn, options.asr_provider, for_api=True)
            src_path = uploads_tus.upload_path(upload)
            probe = _probe_or_reject(src_path, settings)
            row = sources_repo.insert(
                conn,
                {
                    "title": body.title.strip(),
                    "origin": "upload",
                    "upload_id": body.upload_id,
                    "status": SourceStatus.QUEUED.value,
                    "language": body.language,
                    "niche_id": body.niche_id,
                    "options": options.model_dump(mode="json"),
                    "original_retain_until": _retain_until(settings),
                    "created_by": actor.user_id,
                },
            )
            sid = int(row["id"])
            media_dir = f"{SOURCE_DIR_PREFIX}{sid}"
            dest_dir = layout.root / media_dir
            dest_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
            dest = dest_dir / f"original.{uploads_tus.file_ext(upload['filename'])}"
            os.replace(src_path, dest)
            moved = (dest, src_path)
            row = sources_repo.update(
                conn,
                sid,
                {
                    "media_dir": media_dir,
                    "original_path": layout.relpath(dest),
                    **probe_columns(probe.to_dict()),
                },
            )
            rights_fields["proof_file_path"] = _attach_proof(
                conn, principal, body.rights.proof_upload_id, sid
            )
            sources_repo.upsert_rights(conn, sid, rights_fields, recorded_by=actor.user_id)
            uploads_repo.consume(conn, body.upload_id, path=layout.relpath(dest))
            enqueue_step(conn, row, "prepare", chain=["transcribe", "select"], asr=asr)
            audit.record(
                conn,
                actor,
                "source.create",
                "source",
                sid,
                summary=f"источник «{row['title']}» из загрузки ({rights_fields['rights_type']})",
                diff={
                    "origin": "upload",
                    "upload_id": body.upload_id,
                    "rights_type": rights_fields["rights_type"],
                    "owner_name": rights_fields["owner_name"],
                    "warnings": rights_fields["warnings"],
                    "asr_provider": asr.key,
                },
            )
    except BaseException:
        if moved is not None:  # транзакция откатилась — файл возвращается в загрузку
            with contextlib.suppress(OSError):
                os.replace(moved[0], moved[1])
        raise
    return get_source(sid)


def create_from_url(body: Any, principal: Any, actor: Actor) -> dict[str, Any]:
    """POST /sources/from-url → Source (202): права записаны до любой сети, затем source_download."""
    from autoshorts.db.pool import connection
    from autoshorts.services.settings import get_runtime_settings

    url = check_url(body.url)
    rights_fields = _rights_fields(body.rights)
    with connection() as conn:
        settings = get_runtime_settings(conn)
        _check_niche(conn, body.niche_id)
        options = body.options or SourceOptions()
        asr = resolve_asr(conn, options.asr_provider, for_api=True)
        title = (body.title or "").strip()
        row = sources_repo.insert(
            conn,
            {
                "title": title or url[:300],
                "origin": "url",
                "origin_url": url,
                "status": SourceStatus.QUEUED.value,
                "language": body.language,
                "niche_id": body.niche_id,
                "options": options.model_dump(mode="json"),
                "original_retain_until": _retain_until(settings),
                "platform_meta": {"title_from_platform": not title},
                "created_by": actor.user_id,
            },
        )
        sid = int(row["id"])
        row = sources_repo.update(conn, sid, {"media_dir": f"{SOURCE_DIR_PREFIX}{sid}"})
        rights_fields["proof_file_path"] = _attach_proof(conn, principal, body.rights.proof_upload_id, sid)
        sources_repo.upsert_rights(conn, sid, rights_fields, recorded_by=actor.user_id)
        enqueue_step(conn, row, "download", chain=["prepare", "transcribe", "select"], asr=asr)
        audit.record(
            conn,
            actor,
            "source.create",
            "source",
            sid,
            summary=f"источник по ссылке ({rights_fields['rights_type']})",
            diff={
                "origin": "url",
                "url": url,
                "rights_type": rights_fields["rights_type"],
                "owner_name": rights_fields["owner_name"],
                "warnings": rights_fields["warnings"],
                "asr_provider": asr.key,
            },
        )
    return get_source(sid)


# ---------------------------------------------------------------- карточка


def _task_status(status: str) -> str:
    return status if status in ("queued", "running", "succeeded", "failed", "cancelled") else "pending"


def _defer(value: Any) -> str | None:
    from autoshorts.domain.enums import DeferReason

    return value if value in {d.value for d in DeferReason} else None


def build_pipeline(row: Mapping[str, Any], tasks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Шаги конвейера из задач источника (новые первыми). Шаги после активного, сделанные до него,
    показываются как pending (их переделает цепочка)."""
    by_step: dict[str, list[Mapping[str, Any]]] = {}
    for t in tasks:
        step = KIND_STEP.get(str(t["kind"]))
        if step:
            by_step.setdefault(step, []).append(t)
    duration = float(row.get("duration_sec") or 0.0)
    out: list[dict[str, Any]] = []
    for step in STEP_ORDER:
        ts = by_step.get(step, [])
        latest = ts[0] if ts else None
        item: dict[str, Any] = {
            "step": step,
            "status": "pending",
            "task_id": None,
            "progress": None,
            "chunks_done": None,
            "chunks_total": None,
            "queue_position": None,
            "defer_reason": None,
            "error": None,
            "started_at": None,
            "finished_at": None,
        }
        if latest is None:
            if (step == "download" and row.get("origin") == "upload") or step == "scenes":
                item["status"] = "skipped"
            out.append(item)
            continue
        item.update(
            {
                "status": _task_status(str(latest["status"])),
                "task_id": int(latest["id"]),
                "progress": float(latest["progress"]) if latest.get("progress") is not None else None,
                "queue_position": latest.get("queue_position"),
                "defer_reason": _defer(latest.get("defer_reason")),
                "error": latest.get("last_error") if latest["status"] == "failed" else None,
                "started_at": latest.get("started_at"),
                "finished_at": latest.get("finished_at"),
                "_created_at": latest.get("created_at"),
            }
        )
        if latest["status"] == "succeeded":
            item["progress"] = 100.0
        if step == "transcribe" and latest["kind"] == "source_transcribe_chunk":
            rev = (latest.get("payload") or {}).get("rev")
            same = [t for t in ts if (t.get("payload") or {}).get("rev") == rev]
            done = sum(1 for t in same if t["status"] == "succeeded")
            result = latest.get("result") or {}
            finished = bool(result.get("final")) if latest["status"] == "succeeded" else False
            estimate = max(1, math.ceil(duration / CHUNK_SEC)) if duration else len(same)
            total = done if finished else max(estimate, len(same))
            item["chunks_done"], item["chunks_total"] = done, total
            if latest["status"] in ("queued", "running"):
                cur = float(latest.get("progress") or 0.0) / 100.0
                item["progress"] = round(100.0 * min(1.0, (done + cur) / max(total, 1)), 2)
        out.append(item)
    active_created = next(
        (
            it["_created_at"]
            for it in out
            if it["step"] in PIPELINE_ORDER and it["status"] in ("queued", "running")
        ),
        None,
    )
    if active_created is not None:
        seen_active = False
        for it in out:
            if it["step"] not in PIPELINE_ORDER:
                continue
            if it["status"] in ("queued", "running"):
                seen_active = True
                continue
            if seen_active and it.get("_created_at") is not None and it["_created_at"] < active_created:
                it.update({"status": "pending", "task_id": None, "progress": None, "error": None,
                           "started_at": None, "finished_at": None})  # fmt: skip
    for it in out:
        it.pop("_created_at", None)
    return out


def overall_progress(row: Mapping[str, Any], pipeline: Sequence[Mapping[str, Any]]) -> float | None:
    if row.get("status") == SourceStatus.READY.value:
        return 100.0
    total = 0.0
    got = 0.0
    for it in pipeline:
        w = STEP_WEIGHTS.get(it["step"])
        if w is None or it["status"] == "skipped":
            continue
        total += w
        if it["status"] == "succeeded":
            got += w
        elif it["status"] == "running" and it.get("progress") is not None:
            got += w * float(it["progress"]) / 100.0
    return round(100.0 * got / total, 1) if total else None


def watermark_to_api(detail: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not detail:
        return None
    parts: list[str] = []
    for hit in detail.get("hits") or []:
        if isinstance(hit, Mapping):
            name = hit.get("logo") or hit.get("name") or "логотип"
            score = hit.get("score")
            parts.append(f"{name}: {score:.2f}" if isinstance(score, int | float) else str(name))
    overlay = detail.get("static_overlay") or {}
    if isinstance(overlay, Mapping) and overlay.get("flagged"):
        parts.append(f"статичный оверлей в углу {overlay.get('corner') or '?'}")
    severity = str(detail.get("severity") or ("warning" if detail.get("suspect") else "ok"))
    return {
        "suspect": bool(detail.get("suspect")),
        "severity": severity if severity in ("ok", "hint", "warning") else "hint",
        "detail": "; ".join(parts) or None,
        "frames_checked": detail.get("frames_checked"),
        "logos_checked": detail.get("logos_checked"),
    }


def platform_to_api(meta: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not meta or not any(meta.get(k) for k in ("id", "extractor_key", "webpage_url")):
        return None
    return {
        "extractor": meta.get("extractor_key") or meta.get("extractor"),
        "video_id": None if meta.get("id") is None else str(meta.get("id")),
        "webpage_url": meta.get("webpage_url"),
        "uploader": meta.get("uploader"),
        "license": meta.get("license"),
        "upload_date": None if meta.get("upload_date") is None else str(meta.get("upload_date")),
    }


def selection_to_api(sel: Mapping[str, Any] | None, row: Mapping[str, Any]) -> dict[str, Any] | None:
    if not sel or sel.get("mode") not in SELECTION_MODE_LABELS:
        return None
    params = sel.get("params") or {}
    try:
        params = SourceOptions.model_validate(params).model_dump(mode="json")
    except Exception:  # noqa: BLE001
        params = _options(row).model_dump(mode="json")
    return {
        "mode": sel["mode"],
        "label": sel.get("label") or SELECTION_MODE_LABELS[sel["mode"]],
        "llm_configured": bool(sel.get("llm_configured")),
        "fallback": bool(sel.get("fallback")),
        "params": params,
        "n_candidates": int(sel.get("n_candidates") or 0),
        "n_blocked": int(sel.get("n_blocked") or 0),
        "n_requests": int(sel.get("n_requests") or 0),
        "cost_usd": float(sel.get("cost_usd") or 0.0),
        "warnings": [str(w) for w in sel.get("warnings") or []],
        "task_id": sel.get("task_id"),
        "finished_at": sel.get("finished_at"),
    }


def media_to_api(row: Mapping[str, Any], has_transcript: bool) -> dict[str, Any]:
    sid = int(row["id"])
    sb = row.get("storyboard") or {}
    return {
        "proxy_url": f"{URL_PREFIX}/{sid}/media/proxy" if _file_exists(row.get("proxy_path")) else None,
        "audio_url": f"{URL_PREFIX}/{sid}/media/audio" if _file_exists(row.get("audio_path")) else None,
        "peaks_url": f"{URL_PREFIX}/{sid}/peaks" if _file_exists(row.get("peaks_path")) else None,
        "storyboard_url": f"{URL_PREFIX}/{sid}/storyboard" if sb.get("sheets") else None,
        "transcript_url": f"{URL_PREFIX}/{sid}/transcript" if has_transcript else None,
    }


def transcript_info(tr: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if tr is None:
        return None
    return {
        "provider_key": tr["provider_key"],
        "model": tr["model"],
        "language": tr["language"],
        "words_count": int(tr["words_count"]),
        "sentences_count": int(tr["sentences_count"]),
        "duration_sec": float(tr["duration_sec"]) if tr["duration_sec"] is not None else None,
        "rtfx": float(tr["rtfx"]) if tr["rtfx"] is not None else None,
        "cost_usd": float(tr["cost_usd"] or 0),
        "created_at": tr["created_at"],
    }


def _num(value: Any) -> float | None:
    return float(value) if value is not None else None


def source_to_api(conn: Any, row: Mapping[str, Any]) -> dict[str, Any]:
    sid = int(row["id"])
    rights = sources_repo.get_rights(conn, sid)
    if rights is None:  # не бывает: права пишутся в той же транзакции, что и источник
        raise NotFound("source_rights", "У источника нет записи прав")
    cnt = sources_repo.counts(conn, sid)
    tasks = sources_repo.tasks_for(conn, sid)
    tr = transcripts_repo.get_current(conn, sid)
    pipeline = build_pipeline(row, tasks)
    return {
        "id": sid,
        "title": row["title"],
        "origin": row["origin"],
        "origin_url": row["origin_url"],
        "status": row["status"],
        "error": row["error"],
        "language": row["language"],
        "niche_id": row["niche_id"],
        "duration_sec": _num(row["duration_sec"]),
        "width": row["width"],
        "height": row["height"],
        "fps": _num(row["fps"]),
        "video_codec": row["video_codec"],
        "has_audio": row["has_audio"],
        "file_bytes": row["file_bytes"],
        "sha256": row["sha256"],
        "rights": rights_to_api(rights, sid),
        "watermark_suspect": bool(row["watermark_suspect"]),
        "watermark": watermark_to_api(row.get("watermark_detail")),
        "platform": platform_to_api(row.get("platform_meta")) if row["origin"] == "url" else None,
        "options": _options(row).model_dump(mode="json"),
        "pipeline": pipeline,
        "progress": overall_progress(row, pipeline),
        "media": media_to_api(row, tr is not None),
        "transcript": transcript_info(tr),
        "selection": selection_to_api(row.get("selection"), row),
        "candidates_count": cnt["candidates_count"],
        "jobs_count": cnt["jobs_count"],
        "original_retain_until": row["original_retain_until"],
        "proxy_retain_until": row["proxy_retain_until"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _reconcile(conn: Any, row: Row) -> Row:
    """Статус «в работе», а задач нет и последняя упала или отменена → failed (например, воркер
    потерян и reaper исчерпал попытки: обработчик не успел отметить источник)."""
    if row["status"] not in IN_PROGRESS_STATUSES:
        return row
    if sources_repo.active_tasks(conn, int(row["id"])):
        return row
    tasks = [
        t for t in sources_repo.tasks_for(conn, int(row["id"]), limit=20) if t["kind"] != "source_scenes"
    ]
    if not tasks or tasks[0]["status"] not in ("failed", "cancelled"):
        return row
    last = tasks[0]
    message = (
        "Задача отменена" if last["status"] == "cancelled" else (last.get("last_error") or "Шаг не удался")
    )
    set_status(conn, int(row["id"]), SourceStatus.FAILED.value, error=str(message)[-2000:])
    return sources_repo.get(conn, int(row["id"])) or row


def require_source(conn: Any, source_id: int, *, for_update: bool = False) -> Row:
    row = sources_repo.get(conn, source_id, for_update=for_update)
    if row is None:
        raise NotFound("source", "Источник не найден")
    return row


def get_source(source_id: int) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        row = _reconcile(conn, require_source(conn, source_id))
        return source_to_api(conn, row)


def _order(sort: str | None) -> sql.Composable:
    value = sort or "-created_at"
    desc = value.startswith("-")
    column = sources_repo.SORTABLE.get(value.lstrip("-"))
    if column is None:
        raise ValidationFailed(
            "Недопустимая сортировка",
            details={
                "errors": [
                    {
                        "loc": ["query", "sort"],
                        "msg": f"Допустимо: {', '.join(sorted(sources_repo.SORTABLE))} (с '-' — убыв.)",
                        "type": "sort_not_allowed",
                    }
                ]
            },
        )
    direction = sql.SQL("DESC NULLS LAST" if desc else "ASC NULLS FIRST")
    tie = sql.SQL("DESC" if desc else "ASC")
    return sql.SQL("ORDER BY {} {}, s.id {}").format(sql.SQL(column), direction, tie)


def list_sources(
    *,
    statuses: Sequence[str] | None,
    origin: str | None,
    niche_id: str | None,
    q: str | None,
    sort: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    order = _order(sort)
    with connection() as conn:
        rows, total = sources_repo.list_page(
            conn,
            statuses=statuses,
            origin=origin,
            niche_id=niche_id,
            q=(q or "").strip() or None,
            order=order,
            limit=limit,
            offset=offset,
        )
    items = []
    for r in rows:
        sel = r.get("selection") or {}
        active_step = KIND_STEP.get(r.get("active_kind") or "")
        progress: float | None
        if r["status"] == SourceStatus.READY.value:
            progress = 100.0
        elif r.get("active_status") == "running" and r.get("active_progress") is not None:
            progress = float(r["active_progress"])
        else:
            progress = None
        items.append(
            {
                "id": int(r["id"]),
                "title": r["title"],
                "origin": r["origin"],
                "status": r["status"],
                "error": r["error"],
                "language": r["language"],
                "niche_id": r["niche_id"],
                "duration_sec": _num(r["duration_sec"]),
                "rights_type": r["rights_type"],
                "watermark_suspect": bool(r["watermark_suspect"]),
                "current_step": active_step,
                "progress": progress,
                "queue_position": r.get("queue_position"),
                "candidates_count": int(r["candidates_count"]),
                "jobs_count": int(r["jobs_count"]),
                "selection_mode": sel.get("mode") if sel.get("mode") in SELECTION_MODE_LABELS else None,
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
        )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def update_source(source_id: int, changes: Mapping[str, Any], actor: Actor) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    fields = dict(changes)
    if "title" in fields:
        title = (fields["title"] or "").strip()
        if not title:
            raise field_error("title", "Название не может быть пустым", "empty")
        fields["title"] = title
    with connection() as conn:
        require_source(conn, source_id, for_update=True)
        if fields.get("niche_id"):
            _check_niche(conn, fields["niche_id"])
        if fields:
            sources_repo.update(conn, source_id, fields)
            audit.record(conn, actor, "source.update", "source", source_id, diff=_jsonable(fields))
        return source_to_api(conn, require_source(conn, source_id))


def _jsonable(d: Mapping[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if isinstance(v, date | datetime) else v) for k, v in d.items()}


def delete_source(source_id: int, *, purge_files: bool, actor: Actor) -> None:
    """Мягкое удаление; активные задачи отменяются; `purge_files` удаляет каталог источника.

    Пока идут clip-job этого источника (queued/processing) — 409 source_in_use.
    """
    from autoshorts.db.pool import connection
    from autoshorts.services.tasks import request_cancel

    with connection() as conn:
        row = require_source(conn, source_id, for_update=True)
        cnt = sources_repo.counts(conn, source_id)
        if cnt["active_jobs"]:
            raise Conflict(
                "Из источника сейчас делаются ролики: дождитесь их или отмените",
                code="source_in_use",
                details={"active_jobs": cnt["active_jobs"]},
            )
        for t in sources_repo.active_tasks(conn, source_id):
            with contextlib.suppress(AppError):
                request_cancel(conn, int(t["id"]))
        sources_repo.soft_delete(conn, source_id)
        audit.record(
            conn,
            actor,
            "source.delete",
            "source",
            source_id,
            summary=f"источник «{row['title']}» удалён" + (" вместе с файлами" if purge_files else ""),
            diff={
                "purge_files": purge_files,
                "media_dir": media_dir_of(row),
                "dir_kept": purge_files and media_dir_of(row) != f"{SOURCE_DIR_PREFIX}{int(source_id)}",
            },
        )
    if purge_files:
        # каталог, зарегистрированный готовым (например, демо sources/cc_shalyopa_cgevent), не наш —
        # его не удаляем; собственный каталог источника sources/<id> — целиком
        if media_dir_of(row) == f"{SOURCE_DIR_PREFIX}{int(source_id)}":
            shutil.rmtree(source_dir(row), ignore_errors=True)
        with contextlib.suppress(ValueError):
            shutil.rmtree(get_layout().rights_dir(source_id), ignore_errors=True)


# ---------------------------------------------------------------- права (API)


def get_rights(source_id: int) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        require_source(conn, source_id)
        rights = sources_repo.get_rights(conn, source_id)
    if rights is None:
        raise NotFound("source_rights", "У источника нет записи прав")
    return rights_to_api(rights, source_id)


REPACKAGE_RIGHTS_KEYS = ("rights_type", "attribution_text", "license")


def _repackage_after_rights_change(
    conn: Any, source_id: int, old: Mapping[str, Any] | None, fields: Mapping[str, Any]
) -> list[int]:
    """Права поменялись (тип, атрибуция, лицензия) — живые пакеты нарезок источника устарели.

    В них строка «Источник: …» и итог гейта оригинальности при старых правах. Пакеты → superseded,
    одобренным роликам — новая сборка `package_build`: при правах свой → чужой после QC она упрётся в
    `originality_stale` и откроет алерт `package_blocked` (нужен повторный QC).
    """
    if old is None:
        return []
    if all((old.get(k) or None) == (fields.get(k) or None) for k in REPACKAGE_RIGHTS_KEYS):
        return []
    from autoshorts.domain.enums import JobStage
    from autoshorts.repos import packages as packages_repo
    from autoshorts.services import packages as packages_service
    from autoshorts.services.tasks import enqueue

    jobs = packages_repo.live_clip_jobs_for_source(conn, source_id)
    for job in jobs:
        job_id = int(job["id"])
        packages_repo.supersede_live(conn, job_id)
        if job["status"] == "approved":
            enqueue(
                conn,
                "package_build",
                job_id=job_id,
                stage=JobStage.PACKAGE.value,
                payload={"job_id": job_id},
                dedup_key=f"job:{job_id}:package:{int(job['attempt_count'] or 0)}",
            )
    if jobs:
        packages_service.publish_counts(conn)
    return [int(j["id"]) for j in jobs]


def put_rights(source_id: int, body: SourceRightsIn, principal: Any, actor: Actor) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        row = require_source(conn, source_id, for_update=True)
        old = sources_repo.get_rights(conn, source_id)
        platform = row.get("platform_meta") if row["origin"] == "url" else None
        fields = _rights_fields(body, platform=platform)
        proof = _attach_proof(conn, principal, body.proof_upload_id, source_id)
        fields["proof_file_path"] = proof or (old or {}).get("proof_file_path")
        new = sources_repo.upsert_rights(conn, source_id, fields, recorded_by=actor.user_id)
        repacked = _repackage_after_rights_change(conn, source_id, old, fields)
        audit.record(
            conn,
            actor,
            "source.rights",
            "source",
            source_id,
            summary=f"права источника: {fields['rights_type']}, {fields['owner_name']}",
            diff={
                "before": _jsonable(
                    {k: (old or {}).get(k) for k in ("rights_type", "owner_name", "license")}
                ),
                "after": _jsonable({k: fields.get(k) for k in ("rights_type", "owner_name", "license")}),
                "warnings": fields["warnings"],
                "repackaged_jobs": repacked,
            },
        )
    _sync_manifest_rights(row, new)
    return rights_to_api(new, source_id)


# ---------------------------------------------------------------- перезапуск шагов


def reprocess(source_id: int, steps: Sequence[str], asr_provider: str | None, actor: Actor) -> int:
    """POST /sources/{id}/reprocess → id первой поставленной задачи (scenes идёт отдельно)."""
    from autoshorts.db.pool import connection

    wanted = [str(s) for s in steps]
    with connection() as conn:
        row = require_source(conn, source_id, for_update=True)
        chain = _normalize_chain(wanted)
        busy = [t for t in sources_repo.active_tasks(conn, source_id) if t["kind"] != "source_scenes"]
        if chain and busy:
            raise _source_not_ready(
                "Источник ещё обрабатывается: дождитесь конца шага", active_task_ids=[t["id"] for t in busy]
            )
        asr: AsrChoice | None = None
        if "transcribe" in chain:
            asr = resolve_asr(conn, asr_provider or _options(row).asr_provider, for_api=True)
            if asr_provider:
                opts = _options(row).model_copy(update={"asr_provider": asr.key})
                row = sources_repo.update(conn, source_id, {"options": opts.model_dump(mode="json")}) or row
        prepared = load_manifest(row)
        if chain and chain[0] in ("transcribe", "select") and prepared is None:
            raise _source_not_ready("Источник ещё не подготовлен (нет прокси и звука)")
        if chain and chain[0] == "select" and transcripts_repo.get_current(conn, source_id) is None:
            raise _source_not_ready("У источника ещё нет транскрипта")
        if chain and chain[0] == "prepare" and row["origin"] == "url" and not _url_originals(row):
            chain = ["download", *chain]
        if "scenes" in wanted and not _file_exists(row.get("proxy_path")):
            raise _source_not_ready("Нет прокси: склейки считаются по нему")
        first: int | None = None
        if chain:
            res = enqueue_step(conn, row, chain[0], chain=chain[1:], asr=asr)
            first = int(res.task["id"])
        if "scenes" in wanted:
            res = enqueue_step(conn, row, "scenes", set_source_status=False)
            first = first if first is not None else int(res.task["id"])
        assert first is not None
        audit.record(
            conn,
            actor,
            "source.reprocess",
            "source",
            source_id,
            summary="перезапуск шагов: " + ", ".join(wanted),
            diff={
                "steps": wanted,
                "chain": chain,
                "asr_provider": asr.key if asr else None,
                "task_id": first,
            },
        )
        return first


def _url_originals(row: Mapping[str, Any]) -> list[Path]:
    d = source_dir(row)
    return sorted(
        p for p in d.glob("original_video.*") if p.is_file() and not p.name.endswith((".part", ".ytdl"))
    )


# ---------------------------------------------------------------- транскрипт и медиа


def _transcript_path(conn: Any, source_id: int) -> tuple[Row, Path]:
    tr = transcripts_repo.get_current(conn, source_id)
    if tr is None:
        raise NotFound("transcript", "Транскрипта ещё нет")
    try:
        path = get_layout().resolve(tr["path"])
    except ValueError:
        raise NotFound("transcript", "Файл транскрипта недоступен") from None
    if not path.is_file():
        raise NotFound("transcript", "Файл транскрипта не найден")
    return tr, path


def load_transcript(source_id: int) -> Any:
    """Текущий транскрипт источника (`clipping.transcript.Transcript`) — и для M5-B."""
    from autoshorts.clipping.transcript import Transcript
    from autoshorts.db.pool import connection

    with connection() as conn:
        require_source(conn, source_id)
        _, path = _transcript_path(conn, source_id)
    return Transcript.load(path)


def transcript_json(source_id: int) -> dict[str, Any]:
    """`SourceTranscript` (dict, без проверки pydantic: слов в часе — десятки тысяч)."""
    from autoshorts.clipping.transcript import Transcript
    from autoshorts.db.pool import connection

    with connection() as conn:
        row = require_source(conn, source_id)
        tr_row, path = _transcript_path(conn, source_id)
    data = Transcript.load(path).to_dict()
    return {
        "source_id": int(row["id"]),
        "language": data["language"],
        "duration_sec": data["duration"],
        "provider_key": tr_row["provider_key"],
        "model": data.get("model"),
        "words": data["words"],
        "sentences": [{k: s[k] for k in ("sid", "s", "e", "text", "w0", "w1")} for s in data["sentences"]],
    }


def _ts(t: float, sep: str) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def transcript_text(source_id: int, fmt: str) -> str:
    """srt | vtt по предложениям."""
    from autoshorts.clipping.transcript import Transcript
    from autoshorts.db.pool import connection

    with connection() as conn:
        require_source(conn, source_id)
        _, path = _transcript_path(conn, source_id)
    tr = Transcript.load(path)
    lines: list[str] = ["WEBVTT", ""] if fmt == "vtt" else []
    for i, st in enumerate(tr.sentences, 1):
        sep = "." if fmt == "vtt" else ","
        if fmt == "srt":
            lines.append(str(i))
        lines.append(f"{_ts(st.s, sep)} --> {_ts(st.e, sep)}")
        lines.append(st.text)
        lines.append("")
    return "\n".join(lines)


def media_file(source_id: int, kind: str) -> tuple[str, str, str]:
    """(relpath, mime, filename) медиа студии: proxy | audio | peaks."""
    from autoshorts.db.pool import connection

    columns = {
        "proxy": ("proxy_path", "video/mp4", "proxy_540p.mp4"),
        "audio": ("audio_path", "audio/mp4", "audio.m4a"),
        "peaks": ("peaks_path", "application/json", "peaks.json"),
    }
    column, mime, name = columns[kind]
    with connection() as conn:
        row = require_source(conn, source_id)
    rel = row.get(column)
    if not _file_exists(rel):
        raise NotFound("media", "Файл ещё не готов")
    return str(rel), mime, f"source_{source_id}_{name}"


def storyboard(source_id: int) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        row = require_source(conn, source_id)
    sb = row.get("storyboard") or {}
    sheets = sb.get("sheets") or []
    if not sheets:
        raise NotFound("storyboard", "Раскадровки ещё нет")
    return {
        "interval_sec": float(sb.get("interval_sec") or 2.0),
        "tile_w": int(sb.get("tile_w") or 160),
        "tile_h": int(sb.get("tile_h") or 90),
        "cols": int(sb.get("cols") or 10),
        "rows": int(sb.get("rows") or 10),
        "count": int(sb.get("count") or 0),
        "sheets": [f"{URL_PREFIX}/{source_id}/storyboard/{i}" for i in range(len(sheets))],
    }


def storyboard_sheet(source_id: int, sheet_idx: int) -> str:
    from autoshorts.db.pool import connection

    with connection() as conn:
        row = require_source(conn, source_id)
    sheets = (row.get("storyboard") or {}).get("sheets") or []
    if not 0 <= sheet_idx < len(sheets):
        raise NotFound("storyboard_sheet", "Нет такого листа раскадровки")
    rel = _rel_in_dir(row, sheets[sheet_idx])
    if not _file_exists(rel):
        raise NotFound("storyboard_sheet", "Файл листа не найден")
    return str(rel)


def merge_coverage(spans: Sequence[Sequence[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for a, b in sorted((float(a), float(b)) for a, b in spans if float(b) > float(a)):
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [[round(a, 3), round(b, 3)] for a, b in out]


def scenes(source_id: int, start_sec: float | None, end_sec: float | None) -> dict[str, Any]:
    """Склейки в отрезке из `sources.scenes` ({cuts, coverage}); непосчитанное — пусто."""
    from autoshorts.db.pool import connection

    with connection() as conn:
        row = require_source(conn, source_id)
    data = row.get("scenes") or {}
    if isinstance(data, list):  # старая форма 0005: [[s, e], ...]
        data = {"cuts": [], "coverage": [], "spans": data}
    cuts = sorted(float(c) for c in data.get("cuts") or [])
    coverage = merge_coverage(data.get("coverage") or [])
    lo = float(start_sec) if start_sec is not None else 0.0
    hi = float(end_sec) if end_sec is not None else float("inf")
    items: list[dict[str, float]] = []
    for a, b in coverage:
        bounds = [a, *[c for c in cuts if a < c < b], b]
        for s, e in zip(bounds, bounds[1:], strict=False):
            if e > lo and s < hi:
                items.append({"start_sec": round(s, 3), "end_sec": round(e, 3)})
    for s, e in data.get("spans") or []:
        if float(e) > lo and float(s) < hi:
            items.append({"start_sec": float(s), "end_sec": float(e)})
    return {"items": items, "coverage": coverage}


# ---------------------------------------------------------------- регистрация готового каталога


def register_prepared_dir(
    media_dir: str,
    *,
    title: str | None = None,
    niche_id: str | None = None,
    user_id: int | None = None,
    select: bool = True,
    actor: Actor,
) -> dict[str, Any]:
    """Каталог, уже подготовленный движком (`source.json`, прокси, транскрипт), → строка `sources`.

    Для стенда: `sources/cc_shalyopa_cgevent` (CC BY 3.0, права — из манифеста, проверяются тем же
    `validate_rights`). Транскрипт регистрируется как текущий; `select=True` ставит source_select.
    """
    from autoshorts.clipping.source_ingest import MANIFEST_NAME, PreparedSource, validate_rights
    from autoshorts.db.pool import connection

    rel = media_dir.strip().strip("/")
    if not rel.startswith(SOURCE_DIR_PREFIX):
        rel = SOURCE_DIR_PREFIX + rel
    layout = get_layout()
    d = layout.resolve(rel)
    if not (d / MANIFEST_NAME).is_file():
        raise NotFound("source_dir", f"В {rel} нет {MANIFEST_NAME}")
    prepared = PreparedSource.load(d)
    rights = {k: v for k, v in dict(prepared.rights).items() if k != "proof_file_path"}
    rec, warns = validate_rights(rights)
    warns = list(dict.fromkeys([*warns, *prepared.rights_warnings]))
    d_rec = rec.to_db_dict()
    with connection() as conn:
        taken = sources_repo.media_dir_taken(conn, rel)
        if taken is not None:
            raise Conflict("Каталог уже зарегистрирован", code="source_exists", details={"source_id": taken})
        _check_niche(conn, niche_id)
        fields: dict[str, Any] = {
            "title": (title or prepared.title or rel)[:300],
            "origin": prepared.origin if prepared.origin in ("upload", "url") else "upload",
            "status": SourceStatus.QUEUED.value,
            "language": (prepared.transcript or {}).get("language") or "ru",
            "niche_id": niche_id,
            "options": SourceOptions().model_dump(mode="json"),
            "media_dir": rel,
            "created_by": user_id,
            "platform_meta": prepared.platform or {},
        }
        if fields["origin"] == "url" and prepared.origin_url:
            fields["origin_url"] = prepared.origin_url
        else:
            fields["origin"] = "upload"
            fields["upload_id"] = _synthetic_upload(conn, user_id, prepared, rel)
        row = sources_repo.insert(conn, fields)
        sid = int(row["id"])
        sources_repo.update(conn, sid, prepared_columns(row, prepared))
        sources_repo.upsert_rights(
            conn,
            sid,
            {
                **{k: d_rec.get(k) for k in ("rights_type", "owner_name", "owner_channel_url", "proof_note",
                                              "attribution_text", "license", "license_url")},
                "allowlist_status": d_rec.get("allowlist_status") or "unknown",
                "valid_until": rec.valid_until,
                "proof_file_path": None,
                "warnings": warns,
            },
            recorded_by=user_id,
        )  # fmt: skip
        tpath = prepared.path("transcript")
        if tpath is not None and tpath.is_file():
            register_transcript(conn, sid, prepared, layout.relpath(tpath))
            if select:
                enqueue_step(conn, sid, "select")
            else:
                mark_ready(conn, sid)
        else:
            enqueue_step(conn, sid, "transcribe", chain=["select"])
        audit.record(
            conn,
            actor,
            "source.register_dir",
            "source",
            sid,
            summary=f"зарегистрирован готовый каталог {rel}",
            diff={"media_dir": rel, "rights_type": d_rec["rights_type"], "warnings": warns},
        )
    return get_source(sid)


def _synthetic_upload(conn: Any, user_id: int | None, prepared: Any, rel_dir: str) -> str:
    """Источник из готового каталога без tus-загрузки: запись uploads (consumed) для CHECK origin."""
    uid = user_id
    if uid is None:
        found = conn.execute(
            "SELECT id FROM users WHERE role = 'admin' AND is_active ORDER BY id LIMIT 1"
        ).fetchone()
        if found is None:
            raise ValidationFailed("Нет пользователя-админа, от имени которого записать загрузку")
        uid = int(found["id"])
    upload_id = pysecrets.token_hex(16)
    orig = prepared.path("original")
    conn.execute(
        """
        INSERT INTO uploads (id, user_id, purpose, filename, length_bytes, received_bytes, path, status,
                             expires_at, completed_at)
        VALUES (%(id)s, %(uid)s, 'source', %(fn)s, %(n)s, %(n)s, %(path)s, 'consumed', now(), now())
        """,
        {
            "id": upload_id,
            "uid": uid,
            "fn": orig.name if orig else "original",
            "n": int((prepared.probe or {}).get("file_bytes") or 0),
            "path": f"{rel_dir}/{orig.name}" if orig is not None else rel_dir,
        },
    )
    return upload_id


def prepared_columns(row: Mapping[str, Any], prepared: Any) -> dict[str, Any]:
    """Колонки `sources` из манифеста движка (`PreparedSource`)."""
    files = prepared.files or {}
    base = media_dir_of(row)

    def rel(key: str) -> str | None:
        name = files.get(key)
        return f"{base}/{name}" if isinstance(name, str) and name and not name.startswith("/") else None

    wm = prepared.watermark
    return {
        **probe_columns(prepared.probe or {}),
        "sha256": prepared.sha256,
        "original_path": rel("original") or rel("original_video"),
        "proxy_path": rel("proxy"),
        "audio_path": rel("audio"),
        "peaks_path": rel("peaks"),
        "storyboard": prepared.storyboard or None,
        "watermark_suspect": bool(wm and wm.get("suspect")),
        "watermark_detail": wm,
    }


def register_transcript(conn: Any, source_id: int, prepared: Any, rel_path: str) -> Row:
    meta = prepared.transcript or {}
    if not meta or "words" not in meta:
        from autoshorts.clipping.transcript import Transcript

        tr = Transcript.load(get_layout().resolve(rel_path))
        meta = {
            "provider": tr.provider,
            "model": tr.model,
            "language": tr.language,
            "words": len(tr.words),
            "sentences": len(tr.sentences),
            "duration": tr.duration,
            "rtfx": tr.rtfx,
            "cost_usd": tr.cost_usd,
        }
    return transcripts_repo.insert_current(
        conn,
        source_id=source_id,
        provider_key=str(meta.get("provider") or "unknown"),
        model=meta.get("model"),
        language=str(meta.get("language") or "ru"),
        path=rel_path,
        words_count=int(meta.get("words") or 0),
        sentences_count=int(meta.get("sentences") or 0),
        duration_sec=meta.get("duration"),
        rtfx=meta.get("rtfx"),
        cost_usd=float(meta.get("cost_usd") or 0.0),
    )


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


__all__ = [
    "AsrChoice",
    "CHUNK_SEC",
    "KIND_STEP",
    "LlmChoice",
    "PIPELINE_ORDER",
    "SourceRejected",
    "UnsupportedUrl",
    "advance",
    "build_pipeline",
    "create_from_upload",
    "create_from_url",
    "delete_source",
    "enqueue_select",
    "enqueue_step",
    "get_rights",
    "get_source",
    "list_sources",
    "load_manifest",
    "load_transcript",
    "mark_failed",
    "mark_ready",
    "media_file",
    "prepared_columns",
    "put_rights",
    "register_prepared_dir",
    "register_transcript",
    "reprocess",
    "resolve_asr",
    "scenes",
    "selection_llm",
    "set_status",
    "source_dir",
    "storyboard",
    "storyboard_sheet",
    "transcript_json",
    "transcript_text",
    "update_source",
]
