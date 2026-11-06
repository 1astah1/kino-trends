"""Очередь — фон Kino-trends: прогоны сбора, фоновые работы, конвейер и тумблеры автоматики
(тег `trends`, контракт — `schemas.trends_ops`; экран `/queue`).

Дополняет мост `routers/trends.py` (откуда берёт `call`, `require_bridge`): там — подборка дня, чарты
и очередь конвейера; здесь — то, что нужно, чтобы видеть и направлять фоновую работу Kino-trends из
консоли, не открывая его собственный интерфейс:

* чтение (viewer): `GET /trends/runs`, `GET /trends/runs/{run_id}/log`, `GET /trends/settings`;
* действия (operator): `POST /trends/runs` (собрать сейчас), `POST /trends/runs/cancel`,
  `POST /trends/videos-refresh` (чарты и разбор), `POST /trends/pipeline/tick` (шаг конвейера);
* автоматика (admin): `PUT /trends/settings` — только ключи белого списка `SETTING_SPECS`, остальные 422.

Каждое действие и каждая смена тумблера пишется в журнал аудита (`trends.run_start`, `trends.run_cancel`,
`trends.videos_refresh`, `trends.pipeline_tick`, `trends.settings`): состояние живёт в Kino-trends, но кто
нажал кнопку в консоли — должно быть видно здесь. Ошибки моста — как у `trends.py`:
409 `trends_not_configured`, 502 `trends_unreachable`, 400 с текстом Kino-trends («прогон уже идёт»).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Path, Request

from autoshorts.api.deps import AdminDep, OperatorDep, ViewerDep, audit_actor
from autoshorts.api.routers.trends import call, require_bridge
from autoshorts.api.schemas.trends_ops import (
    SETTING_KEYS,
    SETTING_SPECS,
    SettingSpec,
    TrendsPipelineTick,
    TrendsRunCancelling,
    TrendsRunLog,
    TrendsRunLogQueryParams,
    TrendsRuns,
    TrendsRunsQueryParams,
    TrendsRunStarted,
    TrendsSettingItem,
    TrendsSettings,
    TrendsSettingsPatch,
    TrendsVideosRefresh,
    TrendsVideosRefreshRequest,
)
from autoshorts.core.errors import BadRequest, NotFound, TrendsUnreachable, ValidationFailed

log = logging.getLogger(__name__)

router = APIRouter(tags=["trends"])

RunId = Path(ge=1, description="id прогона сбора в Kino-trends")


# ---------------------------------------------------------------- журнал аудита


def _audit(
    request: Request,
    principal: Any,
    action: str,
    *,
    entity_type: str,
    entity_id: str | int | None,
    summary: str,
    diff: dict[str, Any] | None = None,
) -> None:
    from autoshorts.db.pool import connection
    from autoshorts.services import audit

    try:
        with connection() as conn:
            audit.record(
                conn,
                audit_actor(request, principal),
                action,
                entity_type,
                entity_id,
                summary=summary,
                diff=diff,
                safe_keys=frozenset(SETTING_KEYS),
            )
    except Exception:  # noqa: BLE001 - журнал не должен ломать уже выполненное действие
        log.exception("trends audit %s failed", action)


# ---------------------------------------------------------------- прогоны сбора


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) < 19:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def _next_run_at(items: list[Any], hours: int | None) -> str | None:
    """Планировщик Kino-trends стартует новый сбор, когда с начала последнего завершённого прошло
    `schedule_hours` часов (`api._scheduler`). Повторяем расчёт, чтобы экран показал «ждём в …»."""
    if not hours or hours <= 0:
        return None
    for item in items:
        if not isinstance(item, dict) or item.get("status") == "running":
            continue
        started = _parse_iso(item.get("started_at"))
        if started is None:
            return None
        return (started + timedelta(hours=hours)).isoformat(timespec="seconds")
    return datetime.now(UTC).isoformat(timespec="seconds")


def _schedule_hours(bridge: Any) -> int | None:
    """`schedule_hours` из настроек Kino-trends; любая ошибка — None (список прогонов важнее)."""
    try:
        raw = call(bridge, "GET", "/api/settings")
    except (TrendsUnreachable, BadRequest, NotFound):
        return None
    settings = raw.get("settings") if isinstance(raw, dict) else None
    value = settings.get("schedule_hours") if isinstance(settings, dict) else None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


@router.get(
    "/trends/runs",
    name="runs",
    response_model=TrendsRuns,
    summary="Прогоны сбора трендов Kino-trends: история, текущий прогон с фазой и логом, фоновые работы",
)
def trends_runs(principal: ViewerDep, query: TrendsRunsQueryParams):
    bridge = require_bridge()
    data = call(bridge, "GET", "/api/runs", params={"limit": query.limit})
    out = dict(data) if isinstance(data, dict) else {"items": [], "current": {}, "jobs": {}}
    out.setdefault("items", [])
    out.setdefault("current", {})
    out.setdefault("jobs", {})
    hours = _schedule_hours(bridge)
    out["schedule_hours"] = hours
    out["next_run_at"] = _next_run_at(out["items"], hours)
    return out


@router.get(
    "/trends/runs/{run_id}/log",
    name="run_log",
    response_model=TrendsRunLog,
    summary="Лог прогона сбора (последние строки, по порядку)",
)
def trends_run_log(principal: ViewerDep, query: TrendsRunLogQueryParams, run_id: int = RunId):
    data = call(
        require_bridge(),
        "GET",
        f"/api/runs/{run_id}/log",
        params={"limit": query.limit},
        not_found_entity="trends_run",
    )
    return data if isinstance(data, dict) else {"items": []}


@router.post(
    "/trends/runs",
    name="run_start",
    response_model=TrendsRunStarted,
    summary="Запустить сбор трендов сейчас (400, если прогон уже идёт)",
)
def trends_run_start(principal: OperatorDep, request: Request):
    data = call(require_bridge(), "POST", "/api/runs")
    run_id = data.get("run_id") if isinstance(data, dict) else None
    _audit(
        request,
        principal,
        "trends.run_start",
        entity_type="trends_run",
        entity_id=run_id,
        summary=f"сбор трендов запущен вручную (прогон #{run_id})",
    )
    return data


@router.post(
    "/trends/runs/cancel",
    name="run_cancel",
    response_model=TrendsRunCancelling,
    summary="Остановить текущий сбор трендов",
)
def trends_run_cancel(principal: OperatorDep, request: Request):
    data = call(require_bridge(), "POST", "/api/runs/cancel")
    cancelling = bool(data.get("cancelling")) if isinstance(data, dict) else False
    _audit(
        request,
        principal,
        "trends.run_cancel",
        entity_type="trends_run",
        entity_id=None,
        summary="остановка сбора трендов" + ("" if cancelling else " (прогон не шёл)"),
    )
    return data


# ---------------------------------------------------------------- фоновые работы и конвейер


@router.post(
    "/trends/videos-refresh",
    name="videos_refresh",
    response_model=TrendsVideosRefresh,
    summary="Собрать чарты популярных видео и прогнать разбор прямо сейчас (в фоне Kino-trends)",
)
def trends_videos_refresh(
    principal: OperatorDep, request: Request, body: TrendsVideosRefreshRequest | None = None
):
    payload = {"charts_only": True} if body is not None and body.charts_only else {}
    data = call(require_bridge(), "POST", "/api/videos/refresh", json=payload)
    _audit(
        request,
        principal,
        "trends.videos_refresh",
        entity_type="trends_job",
        entity_id="popular",
        summary="обновление чартов и разбора" + (" (только чарты)" if payload else ""),
    )
    return data


@router.post(
    "/trends/pipeline/tick",
    name="pipeline_tick",
    response_model=TrendsPipelineTick,
    summary="Прогнать конвейер Kino-trends → auto-shorts на один шаг сейчас",
)
def trends_pipeline_tick(principal: OperatorDep, request: Request):
    data = call(require_bridge(), "POST", "/api/pipeline/tick")
    out = data if isinstance(data, dict) else {}
    parts = [f"обработано {out.get('processed', 0)}", f"изменено {out.get('changed', 0)}"]
    if out.get("skipped"):
        parts.append(f"пропущен: {out['skipped']}")
    _audit(
        request,
        principal,
        "trends.pipeline_tick",
        entity_type="trends_pipeline",
        entity_id=None,
        summary="шаг конвейера вручную: " + ", ".join(parts),
    )
    return out


# ---------------------------------------------------------------- тумблеры автоматики


def _parse_value(spec: SettingSpec, raw: Any) -> bool | int | str | None:
    """Строка Kino-trends → типизированное значение по описанию ключа."""
    if raw is None:
        return None
    text = str(raw).strip()
    if spec.kind == "bool":
        return text not in {"", "0", "false", "no", "off"}
    if spec.kind == "int":
        try:
            return int(text)
        except ValueError:
            return None
    return text


def _settings_items(raw: Any) -> list[TrendsSettingItem]:
    settings = raw.get("settings") if isinstance(raw, dict) else None
    defaults = raw.get("defaults") if isinstance(raw, dict) else None
    settings = settings if isinstance(settings, dict) else {}
    defaults = defaults if isinstance(defaults, dict) else {}
    items: list[TrendsSettingItem] = []
    for spec in SETTING_SPECS:
        value = _parse_value(spec, settings.get(spec.key))
        default = _parse_value(spec, defaults.get(spec.key))
        items.append(
            TrendsSettingItem(
                key=spec.key,
                kind=spec.kind,
                label=spec.label,
                description=spec.description,
                group=spec.group,
                value=value if value is not None else default,
                default=default,
                choices=list(spec.choices) or None,
                min=spec.min,
                max=spec.max,
                unit=spec.unit,
            )
        )
    return items


def _encode_patch(patch: dict[str, bool | int | str]) -> dict[str, str]:
    """Проверить тело по белому списку и перевести значения в строки Kino-trends. Ошибки — 422 с полями."""
    specs = {spec.key: spec for spec in SETTING_SPECS}
    errors: list[dict[str, Any]] = []
    encoded: dict[str, str] = {}
    for key, value in patch.items():
        spec = specs.get(key)
        if spec is None:
            errors.append(
                {"loc": [key], "msg": "Ключа нет в списке тумблеров автоматики", "type": "unknown_key"}
            )
            continue
        if spec.kind == "bool":
            if isinstance(value, bool):
                encoded[key] = "1" if value else "0"
            elif isinstance(value, int) and value in (0, 1):
                encoded[key] = str(value)
            elif isinstance(value, str) and value.strip().lower() in {"0", "1", "true", "false"}:
                encoded[key] = "1" if value.strip().lower() in {"1", "true"} else "0"
            else:
                errors.append({"loc": [key], "msg": "Нужно true или false", "type": "bool_expected"})
        elif spec.kind == "int":
            number: int | None = None
            if isinstance(value, bool):
                number = None
            elif isinstance(value, int):
                number = value
            elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
                number = int(value.strip())
            if number is None:
                errors.append({"loc": [key], "msg": "Нужно целое число", "type": "int_expected"})
            elif spec.min is not None and number < spec.min:
                errors.append({"loc": [key], "msg": f"Не меньше {spec.min}", "type": "too_small"})
            elif spec.max is not None and number > spec.max:
                errors.append({"loc": [key], "msg": f"Не больше {spec.max}", "type": "too_large"})
            else:
                encoded[key] = str(number)
        else:
            text = str(value).strip() if not isinstance(value, bool) else ""
            if text not in spec.choices:
                errors.append(
                    {"loc": [key], "msg": "Одно из: " + ", ".join(spec.choices), "type": "choice_expected"}
                )
            else:
                encoded[key] = text
    if errors:
        raise ValidationFailed("Недопустимые настройки автоматики", details={"errors": errors})
    if not encoded:
        raise ValidationFailed(
            "Пустое тело: укажите хотя бы один ключ",
            details={"errors": [{"loc": [], "msg": "пусто", "type": "empty"}]},
        )
    return encoded


@router.get(
    "/trends/settings",
    name="settings",
    response_model=TrendsSettings,
    summary="Тумблеры автоматики Kino-trends (белый список) с русскими подписями и типами",
)
def trends_settings(principal: ViewerDep):
    return TrendsSettings(items=_settings_items(call(require_bridge(), "GET", "/api/settings")))


@router.put(
    "/trends/settings",
    name="settings_update",
    response_model=TrendsSettings,
    summary="Переключить автоматику Kino-trends: `{key: value}` только из белого списка (иначе 422)",
)
def trends_settings_update(body: TrendsSettingsPatch, principal: AdminDep, request: Request):
    encoded = _encode_patch(body.root)
    bridge = require_bridge()
    before = {item.key: item.value for item in _settings_items(call(bridge, "GET", "/api/settings"))}
    call(bridge, "PUT", "/api/settings", json=encoded)
    items = _settings_items(call(bridge, "GET", "/api/settings"))
    after = {item.key: item.value for item in items}
    changed = {key: {"from": before.get(key), "to": after.get(key)} for key in encoded}
    _audit(
        request,
        principal,
        "trends.settings",
        entity_type="trends_settings",
        entity_id=None,
        summary="автоматика Kino-trends: " + ", ".join(f"{k}={after.get(k)}" for k in encoded),
        diff=changed,
    )
    return TrendsSettings(items=items)
