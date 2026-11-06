"""Реестр провайдеров и их конфиги (docs/ARCHITECTURE.md §5.2 `/providers`, таблица `provider_configs`).

Метаданные адаптеров (модуль, подпись, цена, лицензия, класс ресурса) лежат здесь, в
`PROVIDER_CATALOG`, пока адаптеры не переехали в `providers/registry.py` (M2-B). Состояние
«последний успех / последняя ошибка» берётся из `generation_events` и из проверок связи.

Проверка связи (`POST /providers/{key}/test`) в M1 — **офлайн**: конфиг включён, секрет задан,
локальная модель на месте, адаптер импортируется. Сетевые проверки подключает M2-B:

    from autoshorts.services.providers import register_tester
    register_tester("openai_compat", lambda cfg, secret: (True, "200 /models"))
"""

from __future__ import annotations

import importlib.util
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from autoshorts.core.errors import NotFound, ValidationFailed
from autoshorts.core.timeutil import utcnow
from autoshorts.domain.enums import ProviderKind, ResourceClass
from autoshorts.repos import providers as providers_repo
from autoshorts.services import audit
from autoshorts.services import secrets as secrets_service
from autoshorts.services.audit import Actor

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderMeta:
    kind: ProviderKind
    adapter: str
    label: str
    resource_class: ResourceClass = ResourceClass.LIGHT
    price_unit: str | None = None
    price_usd: float | None = None
    license_note: str | None = None
    model_file: str | None = None  # файл/каталог в MODELS_DIR (для офлайн-проверки)
    python_module: str | None = None  # импортируемый модуль (для офлайн-проверки)

    @property
    def is_mock(self) -> bool:
        return self.adapter.endswith(".mock")


PROVIDER_CATALOG: dict[str, ProviderMeta] = {
    "openai_compat": ProviderMeta(
        ProviderKind.LLM, "llm.openai_compat", "OpenAI-совместимый LLM", price_unit="1k_tokens"
    ),
    "gigachat_lite": ProviderMeta(ProviderKind.LLM, "llm.gigachat", "GigaChat Lite", price_unit="1k_tokens"),
    "mock_llm": ProviderMeta(
        ProviderKind.LLM, "llm.mock", "LLM-заглушка (тесты)", price_unit="call", price_usd=0.0
    ),
    "edge_tts": ProviderMeta(
        ProviderKind.TTS,
        "tts.edge",
        "Edge TTS (Microsoft)",
        price_unit="call",
        price_usd=0.0,
        license_note="Неофициальный бесплатный API Microsoft Edge (WSS), без SLA",
        python_module="edge_tts",
    ),
    "silero_v5_cis_base": ProviderMeta(
        ProviderKind.TTS,
        "tts.silero",
        "Silero v5 CIS (локально)",
        resource_class=ResourceClass.HEAVY,
        price_unit="call",
        price_usd=0.0,
        license_note="Только модель v5_cis_base; остальные модели Silero — CC-BY-NC",
    ),
    "fal_minimax_speech": ProviderMeta(
        ProviderKind.TTS, "tts.fal", "fal.ai MiniMax Speech", price_unit="1k_chars"
    ),
    "mock_tts": ProviderMeta(
        ProviderKind.TTS, "tts.mock", "TTS-заглушка (синус, тесты)", price_unit="call", price_usd=0.0
    ),
    "fal_flux_schnell": ProviderMeta(
        ProviderKind.IMAGE, "images.fal", "fal.ai FLUX.1 schnell", price_unit="image", price_usd=0.003
    ),
    "mock_image": ProviderMeta(
        ProviderKind.IMAGE, "images.mock", "Картинки-заглушки (тесты)", price_unit="image", price_usd=0.0
    ),
    "gigaam_v3_onnx": ProviderMeta(
        ProviderKind.ASR,
        "asr.gigaam_onnx",
        "GigaAM v3 (onnx-asr, локально)",
        resource_class=ResourceClass.HEAVY,
        price_unit="audio_minute",
        price_usd=0.0,
        license_note="MIT (веса GigaAM и onnx-asr)",
        model_file="gigaam-v3-e2e-ctc-int8",
        python_module="onnx_asr",
    ),
    "yandex_speechkit": ProviderMeta(
        ProviderKind.ASR, "asr.yandex_speechkit", "Yandex SpeechKit (облако)", price_unit="audio_minute"
    ),
    "openai_compat_asr": ProviderMeta(
        ProviderKind.ASR, "asr.openai_compat", "OpenAI-совместимый ASR (облако)", price_unit="audio_minute"
    ),
    "mock_asr": ProviderMeta(
        ProviderKind.ASR, "asr.mock", "ASR-заглушка (тесты)", price_unit="audio_minute", price_usd=0.0
    ),
    "yunet": ProviderMeta(
        ProviderKind.FACES,
        "faces.yunet",
        "YuNet (OpenCV, локально)",
        resource_class=ResourceClass.HEAVY,
        price_unit="call",
        price_usd=0.0,
        license_note="MIT (opencv_zoo)",
        model_file="face_detection_yunet_2026may.onnx",
        python_module="cv2",
    ),
    "gemini_flash_lite": ProviderMeta(
        ProviderKind.VISION, "vision.gemini", "Gemini Flash-Lite (vision)", price_unit="call"
    ),
}

Tester = Callable[[dict[str, Any], str | None], tuple[bool, str]]
_testers: dict[str, Tester] = {}

_test_state_lock = threading.Lock()
_test_state: dict[
    str, dict[str, Any]
] = {}  # key -> {"ok_at": dt|None, "error_at": dt|None, "error": str|None}


def register_tester(key_or_adapter: str, tester: Tester) -> None:
    """Подключить сетевую проверку провайдера (по ключу или по имени адаптера `llm.openai_compat`)."""
    _testers[key_or_adapter] = tester


def meta_for(key: str, kind: str) -> ProviderMeta:
    meta = PROVIDER_CATALOG.get(key)
    if meta is not None:
        return meta
    return ProviderMeta(ProviderKind(kind), f"{kind}.{key}", key)


def _price(meta: ProviderMeta, params: dict[str, Any]) -> dict[str, Any] | None:
    usd = params.get("price_usd", meta.price_usd)
    if meta.price_unit is None or usd is None:
        return None
    try:
        return {"unit": meta.price_unit, "usd": float(usd)}
    except (TypeError, ValueError):
        return None


def _latest(*values: datetime | None) -> datetime | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None


def _item(row: dict[str, Any], stats: dict[str, Any] | None, db_secrets: dict[str, Any]) -> dict[str, Any]:
    meta = meta_for(row["key"], row["kind"])
    test = _test_state.get(row["key"], {})
    last_ok = _latest(stats["last_ok_at"] if stats else None, test.get("ok_at"))
    err_at = _latest(stats["last_error_at"] if stats else None, test.get("error_at"))
    last_error = None
    if err_at is not None and (last_ok is None or err_at > last_ok):
        if test.get("error_at") == err_at:
            last_error = test.get("error")
        else:
            last_error = (stats or {}).get("last_error") or "ошибка вызова"
    params = row["params"] if isinstance(row["params"], dict) else {}
    return {
        "key": row["key"],
        "kind": row["kind"],
        "adapter": meta.adapter,
        "label": meta.label,
        "enabled": row["enabled"],
        "is_default": row["is_default"],
        "params": params,
        "secret_name": row["secret_name"],
        "secret_set": secrets_service.is_set(row["secret_name"], db_secrets),
        "price": _price(meta, params),
        "license_note": meta.license_note,
        "resource_class": meta.resource_class,
        "last_ok_at": last_ok,
        "last_error": last_error,
    }


def list_providers() -> list[dict[str, Any]]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        rows = providers_repo.list_configs(conn)
        stats = providers_repo.call_stats(conn)
        db_secrets = secrets_service.status_map(conn)
    return [_item(r, stats.get(r["key"]), db_secrets) for r in rows]


def update_provider(
    key: str,
    *,
    enabled: bool,
    is_default: bool,
    params: dict[str, Any],
    secret_name: str | None,
    actor: Actor,
) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    if is_default and not enabled:
        raise ValidationFailed(
            "Провайдер по умолчанию должен быть включён",
            details={
                "errors": [
                    {
                        "loc": ["body", "is_default"],
                        "msg": "Выключенный провайдер не может быть по умолчанию",
                        "type": "default_disabled",
                    }
                ]
            },
        )
    with connection() as conn:
        before = providers_repo.get(conn, key, for_update=True)
        if before is None and key not in PROVIDER_CATALOG:
            raise NotFound("provider", "Провайдер не найден")
        kind = before["kind"] if before is not None else str(PROVIDER_CATALOG[key].kind)
        if is_default:
            providers_repo.clear_default(conn, kind, except_key=key)
        row = providers_repo.upsert(
            conn,
            key=key,
            kind=kind,
            enabled=enabled,
            is_default=is_default,
            params=params,
            secret_name=secret_name,
            updated_by=actor.user_id,
        )
        diff: dict[str, Any] = {}
        for field_name in ("enabled", "is_default", "params", "secret_name"):
            old = before[field_name] if before is not None else None
            if old != row[field_name]:
                diff[field_name] = {"from": old, "to": row[field_name]}
        if diff or before is None:
            audit.record(
                conn,
                actor,
                "provider.update",
                "provider",
                key,
                summary=f"изменён провайдер {key}: {', '.join(sorted(diff)) or 'создан'}",
                diff=diff,
                safe_keys=frozenset({"secret_name", "enabled", "is_default"}),
            )
        stats = providers_repo.call_stats(conn).get(key)
        db_secrets = secrets_service.status_map(conn)
    return _item(row, stats, db_secrets)


def _offline_check(meta: ProviderMeta, cfg: dict[str, Any], secret: str | None) -> tuple[bool, str]:
    from autoshorts.core.config import get_env

    notes: list[str] = []
    if meta.is_mock:
        if get_env().is_prod:
            from autoshorts.services.settings import get_runtime_settings

            if not get_runtime_settings().allow_mock_in_prod:
                return False, "mock-провайдер запрещён при APP_ENV=prod (настройка allow_mock_in_prod=false)"
        return True, "mock-провайдер: сетевых вызовов нет"
    if cfg["secret_name"]:
        if not secret:
            return False, f"Секрет {cfg['secret_name']} не задан (ни в БД, ни в env)"
        notes.append(f"секрет {cfg['secret_name']} задан")
    if meta.python_module and importlib.util.find_spec(meta.python_module) is None:
        return False, f"Не установлен модуль {meta.python_module}"
    if meta.python_module:
        notes.append(f"модуль {meta.python_module} есть")
    if meta.model_file:
        path = get_env().models_dir / meta.model_file
        if not path.exists():
            return False, f"Нет модели {meta.model_file} в MODELS_DIR"
        notes.append(f"модель {meta.model_file} на месте")
    notes.append("сетевая проверка появится вместе с адаптером (M2)")
    return True, "; ".join(notes)


def test_provider(key: str, actor: Actor) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        cfg = providers_repo.get(conn, key)
    if cfg is None:
        raise NotFound("provider", "Провайдер не найден")
    meta = meta_for(key, cfg["kind"])
    secret = secrets_service.get_secret(cfg["secret_name"]) if cfg["secret_name"] else None
    tester = _testers.get(key) or _testers.get(meta.adapter)
    started = time.monotonic()
    try:
        if tester is not None:
            ok, detail = tester(cfg, secret)
        else:
            ok, detail = _offline_check(meta, cfg, secret)
    except Exception as exc:  # noqa: BLE001 - проверка связи не должна ронять API
        log.warning("provider test %s failed: %s", key, exc)
        ok, detail = False, f"{type(exc).__name__}: {exc}"[:500]
    latency_ms = int((time.monotonic() - started) * 1000)
    now = utcnow()
    with _test_state_lock:
        state = _test_state.setdefault(key, {"ok_at": None, "error_at": None, "error": None})
        if ok:
            state["ok_at"] = now
        else:
            state.update(error_at=now, error=detail)
    if not cfg["enabled"]:
        detail = f"{detail} (провайдер выключен)"
    return {"ok": ok, "latency_ms": latency_ms, "detail": detail}
