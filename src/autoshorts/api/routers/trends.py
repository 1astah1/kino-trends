"""Трендоскаут — мост к Kino-trends (тег `trends`; контракт — `schemas.trends`).

Своего сбора трендов здесь нет: экран `/trends` консоли показывает данные отдельного сервиса
Kino-trends (`kino_trends_url`), который парсит популярные видео СНГ и фильмы-тренды, проверяет
авторские права и ставит «чистые» ролики в конвейер (он же шлёт источники в auto-shorts).

Исходящий HTTP — `httpx.Client` с таймаутом `kino_trends_timeout_sec`, без прокси и ретраев
(сервис локальный). Для тестов — `transport_override` (как в `collectors.http`): `httpx.MockTransport`
подставляется во все клиенты модуля.

Ошибки моста в едином формате §5.1: 409 `trends_not_configured`, 502 `trends_unreachable`;
400/404 от Kino-trends (`{"detail": "…"}`) пробрасываются как `bad_request` / `trends_item_not_found`
(`trends_video_not_found` для ролика, `trends_frames_not_found` / `trends_thumb_not_found` для картинок).
Мутации (enqueue / retry / cancel, решения по ролику, разбор заново, заказ mp4) пишутся в журнал аудита:
состояние живёт в Kino-trends, но кто нажал кнопку в консоли — должно быть видно здесь.

Разбор ролика (`/trends/videos/{video_id}` и действия) проксирует `/api/videos/{id}` Kino-trends; картинки
(обложка, плитка кадров, постер кино) идут через наш API, потому что у консоли CSP `img-src 'self'`.
Кино и сериалы (`/trends/films*`) — список `/api/films` с признаком «готово к нарезке», карточка = бриф
`/api/brief/{id}` + статистика `/api/films/{id}` одним ответом, права и исходники — прокси один в один.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Path, Request
from fastapi.responses import Response

from autoshorts.api.deps import OperatorDep, ViewerDep, audit_actor
from autoshorts.api.schemas.trends import (
    PIPELINE_ACTIVE_STATES,
    PIPELINE_FAILED_STATES,
    PIPELINE_REVIEW_STATES,
    TrendsEnqueueRequest,
    TrendsFilmDetail,
    TrendsFilmDetailQueryParams,
    TrendsFilmFindSourcesQueryParams,
    TrendsFilmFindSourcesStarted,
    TrendsFilmList,
    TrendsFilmRights,
    TrendsFilmRightsManualRequest,
    TrendsFilmSources,
    TrendsFilmsQueryParams,
    TrendsPipeline,
    TrendsPipelineCounts,
    TrendsPipelineItem,
    TrendsPipelineQueryParams,
    TrendsStatus,
    TrendsToday,
    TrendsTodayCounts,
    TrendsVideoDetail,
    TrendsVideoLevelRequest,
    TrendsVideoList,
    TrendsVideoPickRequest,
    TrendsVideosQueryParams,
)
from autoshorts.core.errors import BadRequest, Conflict, NotFound, TrendsNotConfigured, TrendsUnreachable

log = logging.getLogger(__name__)

router = APIRouter(tags=["trends"])

USER_AGENT = "autoshorts-trends-bridge/1.0"
MAX_ERROR_TEXT = 300

transport_override: httpx.BaseTransport | None = None
"""Транспорт для всех клиентов модуля (тесты без сети)."""

ItemId = Path(ge=1, description="id задания в очереди Kino-trends")
VideoId = Path(pattern=r"^[A-Za-z0-9_-]{6,20}$", description="id ролика YouTube")
TitleId = Path(pattern=r"^[A-Za-z0-9_-]{1,40}$", description="id произведения (Wikidata Q… или слаг)")
THUMB_PATH = "/api/v1/trends/videos/{video_id}/thumb"
FRAMES_PATH = "/api/v1/trends/videos/{video_id}/frames"
POSTER_PATH = "/api/v1/trends/films/{title_id}/poster"
ANALYZE_TIMEOUT_SEC = 90.0
"""Разбор ролика заново, перепроверка прав и поиск исходников в Kino-trends ходят в нейросеть и по чужим
страницам — до минуты, поэтому свой таймаут вместо `kino_trends_timeout_sec`."""
YOUTUBE_ID_RE = re.compile(r"(?:v=|/shorts/|youtu\.be/|/embed/)([A-Za-z0-9_-]{6,20})")


def rewrite_thumbs(data: Any) -> Any:
    """Картинки роликов — через наш API, а не с i.ytimg.com / Kino-trends: у консоли CSP `img-src 'self'`,
    а чужие домены у части зрителей не открываются. Рекурсивно подменяет `thumb` (обложка) и `frames_url`
    (плитка кадров, у Kino-trends это относительный путь) в словарях, где есть `video_id`."""
    if isinstance(data, list):
        return [rewrite_thumbs(x) for x in data]
    if isinstance(data, dict):
        out = {k: rewrite_thumbs(v) for k, v in data.items()}
        vid = out.get("video_id")
        if isinstance(vid, str) and vid and "/" not in vid:
            if out.get("thumb"):
                out["thumb"] = THUMB_PATH.format(video_id=vid)
            if out.get("frames_url"):
                out["frames_url"] = FRAMES_PATH.format(video_id=vid)
        return out
    return data


def finish_video_detail(data: Any) -> Any:
    """Деталь ролика наружу: картинки через наш API, `frames_url` — по признаку `frames_available`."""
    out = rewrite_thumbs(data)
    if isinstance(out, dict):
        vid = out.get("video_id")
        has_frames = bool(out.get("frames_available")) and isinstance(vid, str) and vid and "/" not in vid
        out["frames_url"] = FRAMES_PATH.format(video_id=vid) if has_frames else None
    return out


def _poster_url(title_id: Any) -> str | None:
    return (
        POSTER_PATH.format(title_id=title_id)
        if isinstance(title_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", title_id)
        else None
    )


def finish_film_rows(data: Any) -> Any:
    """Список кино наружу: обложки лучших нарезок через наш API, у каждой строки — `poster_url`."""
    out = rewrite_thumbs(data)
    if isinstance(out, dict) and isinstance(out.get("items"), list):
        for row in out["items"]:
            if isinstance(row, dict):
                row["poster_url"] = _poster_url(row.get("title_id"))
    return out


def youtube_id(url: Any) -> str | None:
    """id ролика из ссылки YouTube (`watch?v=`, `/shorts/`, `youtu.be/`); для Rutube и прочих — None."""
    if not isinstance(url, str):
        return None
    match = YOUTUBE_ID_RE.search(url)
    return match.group(1) if match else None


def merge_film_detail(title_id: str, brief: Any, stats: Any) -> dict[str, Any]:
    """Карточка кино = бриф (почему, план, права, исходники, нарезчик) + статистика (каналы, дни, сезоны,
    чужие нарезки). У референсов брифа нет video_id — достаём из ссылки, чтобы показать миниатюру."""
    brief = brief if isinstance(brief, dict) else {}
    stats = stats if isinstance(stats, dict) else {}
    out: dict[str, Any] = {**brief}
    out.setdefault("title", stats.get("title") or {"title_id": title_id})
    if not out.get("stats"):
        out["stats"] = stats.get("stats")
    for key in ("channels", "hashtags", "durations", "daily", "videos"):
        out[key] = stats.get(key) or []
    if not out.get("seasons"):
        out["seasons"] = stats.get("seasons") or []
    refs = []
    for ref in out.get("references") or []:
        if isinstance(ref, dict):
            vid = ref.get("video_id") or youtube_id(ref.get("url"))
            ref = {**ref, "video_id": vid, "thumb": "x" if vid else None}  # thumb перепишет rewrite_thumbs
        refs.append(ref)
    out["references"] = refs
    title = out.get("title") if isinstance(out.get("title"), dict) else {}
    out["poster_url"] = _poster_url(title.get("title_id") or title_id)
    imdb = title.get("imdb_id")
    out["imdb_url"] = f"https://www.imdb.com/title/{imdb}/" if isinstance(imdb, str) and imdb else None
    if not out.get("kinopoisk_url") and title.get("kp_id"):
        out["kinopoisk_url"] = f"https://www.kinopoisk.ru/film/{title['kp_id']}/"
    return rewrite_thumbs(out)


# ---------------------------------------------------------------- настройки моста


class Bridge:
    """Адрес и таймаут Kino-trends из рантайм-настроек."""

    def __init__(self, url: str | None, timeout_sec: int, public_url: str | None) -> None:
        self.url = (url or "").strip().rstrip("/") or None
        self.timeout_sec = timeout_sec
        self.public_url = (public_url or "").strip() or None

    @property
    def url_set(self) -> bool:
        return self.url is not None

    @property
    def configured(self) -> bool:
        if not self.url:
            return False
        parts = urlsplit(self.url)
        return parts.scheme in {"http", "https"} and bool(parts.netloc)


def bridge_settings() -> Bridge:
    from autoshorts.services.settings import get_runtime_settings

    rs = get_runtime_settings()
    return Bridge(rs.kino_trends_url, rs.kino_trends_timeout_sec, rs.kino_trends_public_url)


def require_bridge() -> Bridge:
    bridge = bridge_settings()
    if not bridge.configured:
        raise TrendsNotConfigured(details={"url_set": bridge.url_set})
    return bridge


def make_client(bridge: Bridge, *, timeout: float | None = None) -> httpx.Client:
    kwargs: dict[str, Any] = {
        "base_url": bridge.url or "",
        "timeout": httpx.Timeout(float(timeout if timeout is not None else bridge.timeout_sec)),
        "headers": {"User-Agent": USER_AGENT, "Accept": "application/json"},
    }
    if transport_override is not None:
        kwargs["transport"] = transport_override
    return httpx.Client(**kwargs)


# ---------------------------------------------------------------- вызов Kino-trends


def _short(text: str) -> str:
    return " ".join(str(text).split())[:MAX_ERROR_TEXT]


def _detail(response: httpx.Response) -> str:
    """Текст ошибки Kino-trends: `{"detail": "…"}` или первые символы тела."""
    try:
        body = response.json()
    except ValueError:
        return _short(response.text)
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("error") or body
        if isinstance(detail, dict):
            detail = detail.get("message") or detail
        return _short(str(detail))
    return _short(str(body))


def call(
    bridge: Bridge,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    not_found_entity: str = "trends_item",
    timeout: float | None = None,
) -> Any:
    """Один запрос к Kino-trends → распарсенный JSON. Сетевые сбои и 5xx → 502, 400 → 400, 404 → 404.
    `timeout` — вместо настройки `kino_trends_timeout_sec` для заведомо долгих вызовов (разбор ролика)."""
    clean_params = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        with make_client(bridge, timeout=timeout) as client:
            response = client.request(method, path, params=clean_params, json=json)
    except httpx.HTTPError as exc:
        reason = _short(str(exc) or type(exc).__name__)
        log.warning("kino-trends %s %s failed: %s", method, path, reason)
        raise TrendsUnreachable(
            f"Kino-trends не отвечает ({type(exc).__name__})", details={"url": bridge.url, "reason": reason}
        ) from exc
    if response.status_code >= 500:
        raise TrendsUnreachable(
            f"Kino-trends ответил {response.status_code}",
            details={"url": bridge.url, "status": response.status_code, "reason": _detail(response)},
        )
    if response.status_code == 404:
        raise NotFound(not_found_entity, _detail(response) or "Kino-trends: не найдено")
    if response.status_code >= 400:
        raise BadRequest(
            _detail(response) or "Kino-trends отклонил запрос", details={"status": response.status_code}
        )
    try:
        return response.json()
    except ValueError as exc:
        raise TrendsUnreachable(
            "Kino-trends ответил не JSON", details={"url": bridge.url, "reason": _short(response.text)}
        ) from exc


# ---------------------------------------------------------------- статус


def _today_counts(today: Any) -> TrendsTodayCounts:
    counts = today.get("counts") if isinstance(today, dict) else None
    counts = counts if isinstance(counts, dict) else {}

    def n(key: str) -> int:
        value = counts.get(key)
        return int(value) if isinstance(value, int | float) else 0

    return TrendsTodayCounts(
        films=n("film_total"),
        films_done=n("film_done"),
        videos=n("videos"),
        clean=n("clean"),
        check=n("check"),
        picks=n("picks"),
    )


def _pipeline_counts(status: Any) -> TrendsPipelineCounts:
    status = status if isinstance(status, dict) else {}
    raw = status.get("counts")
    counts = (
        {k: int(v) for k, v in raw.items() if isinstance(v, int | float)} if isinstance(raw, dict) else {}
    )

    def total(states: frozenset[str]) -> int:
        return sum(v for k, v in counts.items() if k in states)

    health = status.get("health") if isinstance(status.get("health"), dict) else {}
    return TrendsPipelineCounts(
        active=total(PIPELINE_ACTIVE_STATES),
        review=total(PIPELINE_REVIEW_STATES),
        failed=total(PIPELINE_FAILED_STATES),
        published=counts.get("published", 0),
        linked=bool(status.get("configured")) and health.get("status") == "ok",
        enabled=bool(status.get("enabled")),
    )


@router.get(
    "/trends/status",
    name="status",
    response_model=TrendsStatus,
    summary="Состояние моста к Kino-trends: настроен, доступен, счётчики дня и очереди",
)
def trends_status(principal: ViewerDep):
    bridge = bridge_settings()
    checked_at = datetime.now(UTC).isoformat(timespec="seconds")
    base = {
        "configured": bridge.configured,
        "url_set": bridge.url_set,
        "public_url": bridge.public_url,
        "checked_at": checked_at,
    }
    if not bridge.configured:
        error = (
            "Адрес Kino-trends не похож на http(s)-URL" if bridge.url_set else "Адрес Kino-trends не задан"
        )
        return TrendsStatus(**base, reachable=False, error=error, today=None, pipeline=None)
    try:
        today = call(bridge, "GET", "/api/today")
        pipeline = call(bridge, "GET", "/api/pipeline/status")
    except (TrendsUnreachable, BadRequest, NotFound) as exc:
        return TrendsStatus(**base, reachable=False, error=exc.message, today=None, pipeline=None)
    return TrendsStatus(
        **base,
        reachable=True,
        error=None,
        today=_today_counts(today),
        pipeline=_pipeline_counts(pipeline),
    )


# ---------------------------------------------------------------- чтение


@router.get(
    "/trends/today",
    name="today",
    response_model=TrendsToday,
    summary="Подборка дня Kino-trends: кино по календарю, чистые ролики, кандидаты, счётчики",
)
def trends_today(principal: ViewerDep):
    return rewrite_thumbs(call(require_bridge(), "GET", "/api/today"))


@router.get(
    "/trends/videos",
    name="videos",
    response_model=TrendsVideoList,
    summary="Популярные видео СНГ с уровнем чистоты (фильтры level, fit, short, sort, q)",
)
def trends_videos(principal: ViewerDep, query: TrendsVideosQueryParams):
    params: dict[str, Any] = {
        "level": query.level,
        "fit": query.fit,
        "short": query.short,
        "pick": 1 if query.pick else None,
        "sort": query.sort,
        "q": query.q or None,
        "lang": query.lang or None,
        "limit": query.limit,
        "offset": query.offset,
    }
    return rewrite_thumbs(call(require_bridge(), "GET", "/api/videos", params=params))


def _proxy_image(bridge: Bridge, path: str, *, entity: str, missing: str, cache_control: str) -> Response:
    """Картинка из Kino-trends как есть (обложка из его кэша, плитка кадров) с нашим Cache-Control."""
    try:
        with make_client(bridge) as client:
            response = client.get(path)
    except httpx.HTTPError as exc:
        raise TrendsUnreachable(
            f"Kino-trends не отвечает ({type(exc).__name__})", details={"url": bridge.url}
        ) from exc
    if response.status_code == 404:
        raise NotFound(entity, missing)
    if response.status_code >= 400 or not response.headers.get("content-type", "").startswith("image"):
        raise TrendsUnreachable(
            f"Kino-trends ответил {response.status_code} на картинку {path}", details={"url": bridge.url}
        )
    return Response(
        content=response.content,
        media_type=response.headers.get("content-type", "image/jpeg"),
        headers={"Cache-Control": cache_control},
    )


@router.get(
    "/trends/videos/{video_id}/thumb",
    name="video_thumb",
    summary="Обложка ролика через Kino-trends (его кэш /thumb), чтобы консоль не ходила на чужие домены",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}},
)
def trends_video_thumb(principal: ViewerDep, video_id: str = VideoId) -> Response:
    return _proxy_image(
        require_bridge(),
        f"/thumb/{video_id}.jpg",
        entity="trends_thumb",
        missing="Обложки нет",
        cache_control="private, max-age=86400",
    )


# ---------------------------------------------------------------- разбор ролика


def _video_detail(bridge: Bridge, video_id: str) -> Any:
    return finish_video_detail(
        call(bridge, "GET", f"/api/videos/{video_id}", not_found_entity="trends_video")
    )


@router.get(
    "/trends/videos/{video_id}",
    name="video",
    response_model=TrendsVideoDetail,
    summary="Разбор ролика: карточка YouTube, оценка чистоты, нейросеть по тексту и кадрам, замеры, файл",
)
def trends_video(principal: ViewerDep, video_id: str = VideoId):
    return _video_detail(require_bridge(), video_id)


@router.get(
    "/trends/videos/{video_id}/frames",
    name="video_frames",
    summary="Плитка из 6 кадров ролика (кэш Kino-trends); пересчитывается разбором, поэтому кэш короткий",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}},
)
def trends_video_frames(principal: ViewerDep, video_id: str = VideoId) -> Response:
    return _proxy_image(
        require_bridge(),
        f"/api/videos/{video_id}/frames.jpg",
        entity="trends_frames",
        missing="Кадры ещё не вынимались",
        cache_control="private, max-age=300",
    )


@router.get(
    "/trends/pipeline",
    name="pipeline",
    response_model=TrendsPipeline,
    summary="Очередь конвейера Kino-trends → auto-shorts со счётчиками и состоянием связи",
)
def trends_pipeline(principal: ViewerDep, query: TrendsPipelineQueryParams):
    return call(
        require_bridge(), "GET", "/api/pipeline", params={"status": query.status, "limit": query.limit}
    )


# ---------------------------------------------------------------- действия (operator)


def _audit(request: Request, principal: Any, action: str, item: Any, *, summary: str) -> None:
    from autoshorts.db.pool import connection
    from autoshorts.services import audit

    item_id = item.get("id") if isinstance(item, dict) else None
    diff = (
        {k: item.get(k) for k in ("kind", "ref_id", "label", "status") if k in item}
        if isinstance(item, dict)
        else None
    )
    try:
        with connection() as conn:
            audit.record(
                conn,
                audit_actor(request, principal),
                action,
                "trends_item",
                item_id,
                summary=summary,
                diff=diff,
            )
    except Exception:  # noqa: BLE001 - журнал не должен ломать уже выполненное действие
        log.exception("trends audit %s failed", action)


@router.post(
    "/trends/pipeline/enqueue",
    name="enqueue",
    response_model=TrendsPipelineItem,
    summary="Поставить кино (title_id) или ролик (video_id) в конвейер Kino-trends",
)
def trends_enqueue(body: TrendsEnqueueRequest, principal: OperatorDep, request: Request):
    payload: dict[str, Any] = {"kind": body.kind, "ref_id": body.ref_id}
    if body.note:
        payload["note"] = body.note
    item = call(require_bridge(), "POST", "/api/pipeline/enqueue", json=payload)
    _audit(request, principal, "trends.enqueue", item, summary=f"в конвейер: {body.kind} {body.ref_id}")
    return item


@router.post(
    "/trends/pipeline/{item_id}/retry",
    name="retry",
    response_model=TrendsPipelineItem,
    summary="Повторить задание конвейера (failed, qc_failed, cancelled)",
)
def trends_retry(principal: OperatorDep, request: Request, item_id: int = ItemId):
    item = call(require_bridge(), "POST", f"/api/pipeline/{item_id}/retry")
    _audit(request, principal, "trends.retry", item, summary=f"повтор задания {item_id}")
    return item


@router.post(
    "/trends/pipeline/{item_id}/cancel",
    name="cancel",
    response_model=TrendsPipelineItem,
    summary="Отменить задание конвейера",
)
def trends_cancel(principal: OperatorDep, request: Request, item_id: int = ItemId):
    item = call(require_bridge(), "POST", f"/api/pipeline/{item_id}/cancel")
    _audit(request, principal, "trends.cancel", item, summary=f"отмена задания {item_id}")
    return item


# ---------------------------------------------------------------- решения по ролику (operator)


def _audit_video(
    request: Request, principal: Any, action: str, video_id: str, detail: Any, *, summary: str
) -> None:
    from autoshorts.db.pool import connection
    from autoshorts.services import audit

    diff = None
    if isinstance(detail, dict):
        file_info = detail.get("file") if isinstance(detail.get("file"), dict) else {}
        diff = {
            "level": detail.get("level"),
            "manual_level": detail.get("manual_level"),
            "score": detail.get("score"),
            "pick": detail.get("pick"),
            "file_status": file_info.get("status"),
        }
    try:
        with connection() as conn:
            audit.record(
                conn,
                audit_actor(request, principal),
                action,
                "trends_video",
                video_id,
                summary=summary,
                diff=diff,
            )
    except Exception:  # noqa: BLE001 - журнал не должен ломать уже выполненное действие
        log.exception("trends audit %s failed", action)


@router.post(
    "/trends/videos/{video_id}/pick",
    name="video_pick",
    response_model=TrendsVideoDetail,
    summary="Взять ролик в подборку под перезалив или убрать из неё (решение оператора, pick_auto=0)",
)
def trends_video_pick(
    body: TrendsVideoPickRequest, principal: OperatorDep, request: Request, video_id: str = VideoId
):
    payload: dict[str, Any] = {"pick": body.pick}
    if body.note:
        payload["note"] = body.note
    detail = finish_video_detail(
        call(
            require_bridge(),
            "POST",
            f"/api/videos/{video_id}/pick",
            json=payload,
            not_found_entity="trends_video",
        )
    )
    _audit_video(
        request,
        principal,
        "trends.video_pick",
        video_id,
        detail,
        summary=f"ролик {video_id}: {'в подборку' if body.pick else 'убран из подборки'}",
    )
    return detail


@router.post(
    "/trends/videos/{video_id}/level",
    name="video_level",
    response_model=TrendsVideoDetail,
    summary="Уровень чистоты руками (clean | check | no) или null — вернуть автоматическую оценку",
)
def trends_video_level(
    body: TrendsVideoLevelRequest, principal: OperatorDep, request: Request, video_id: str = VideoId
):
    detail = finish_video_detail(
        call(
            require_bridge(),
            "POST",
            f"/api/videos/{video_id}/level",
            json={"level": body.level},
            not_found_entity="trends_video",
        )
    )
    _audit_video(
        request,
        principal,
        "trends.video_level",
        video_id,
        detail,
        summary=f"ролик {video_id}: уровень {body.level or 'авто'}",
    )
    return detail


@router.post(
    "/trends/videos/{video_id}/analyze",
    name="video_analyze",
    response_model=TrendsVideoDetail,
    summary="Разобрать ролик заново: текст нейросетью, 6 кадров с хранилища, пересчёт оценки (до минуты)",
)
def trends_video_analyze(principal: OperatorDep, request: Request, video_id: str = VideoId):
    detail = finish_video_detail(
        call(
            require_bridge(),
            "POST",
            f"/api/videos/{video_id}/analyze",
            json={},
            not_found_entity="trends_video",
            timeout=ANALYZE_TIMEOUT_SEC,
        )
    )
    notes = detail.get("notes") if isinstance(detail, dict) else None
    tail = f": {'; '.join(map(str, notes))}" if isinstance(notes, list) and notes else ""
    _audit_video(
        request,
        principal,
        "trends.video_analyze",
        video_id,
        detail,
        summary=f"ролик {video_id}: разобран заново{tail}",
    )
    return detail


@router.post(
    "/trends/videos/{video_id}/file",
    name="video_file",
    response_model=TrendsVideoDetail,
    summary="Заказать mp4 ролика на хранилище Kino-trends (прямая ссылка появится в file.file_url)",
)
def trends_video_file(principal: OperatorDep, request: Request, video_id: str = VideoId):
    bridge = require_bridge()
    # Kino-trends отвечает `{ok, status}`; наружу — обновлённая деталь, чтобы экран не собирал её сам
    call(bridge, "POST", f"/api/videos/{video_id}/file", not_found_entity="trends_video")
    detail = _video_detail(bridge, video_id)
    _audit_video(
        request, principal, "trends.video_file", video_id, detail, summary=f"ролик {video_id}: заказан mp4"
    )
    return detail


# ---------------------------------------------------------------- кино и сериалы


@router.get(
    "/trends/films",
    name="films",
    response_model=TrendsFilmList,
    summary="Фильмы и сериалы, которые режут в шортсы: нарезки, права, исходник, признак «готово к нарезке»",
)
def trends_films(principal: ViewerDep, query: TrendsFilmsQueryParams):
    params: dict[str, Any] = {
        "period": query.period,
        "lang": query.lang,
        "sort": query.sort,
        "kind": query.kind,
        "genre": query.genre or None,
        "q": query.q or None,
        "ready": 1 if query.ready else None,
        "limit": query.limit,
        "offset": query.offset,
    }
    return finish_film_rows(call(require_bridge(), "GET", "/api/films", params=params))


@router.post(
    "/trends/films/find-sources",
    name="films_find_sources",
    response_model=TrendsFilmFindSourcesStarted,
    summary="Фоновый поиск исходников для топа произведений без них; ход — в /trends/runs (jobs.sources_top)",
)
def trends_films_find_sources(
    principal: OperatorDep, request: Request, query: TrendsFilmFindSourcesQueryParams
):
    try:
        data = call(
            require_bridge(), "POST", "/api/films/sources/find-top", params={"limit": query.limit}, json={}
        )
    except BadRequest as exc:
        if exc.details.get("status") == 409:  # поиск уже идёт — это не ошибка запроса, а состояние
            raise Conflict(exc.message) from exc
        raise
    _audit_film(
        request,
        principal,
        "trends.films_find_sources",
        "top",
        data,
        summary=f"поиск исходников для топа: до {query.limit} произведений",
    )
    return {
        "started": bool(data.get("started", True)) if isinstance(data, dict) else True,
        "limit": query.limit,
    }


@router.get(
    "/trends/films/{title_id}",
    name="film",
    response_model=TrendsFilmDetail,
    summary="Карточка произведения: бриф (почему, план, права, исходники, нарезчику) и статистика нарезок",
)
def trends_film(principal: ViewerDep, query: TrendsFilmDetailQueryParams, title_id: str = TitleId):
    bridge = require_bridge()
    params = {"period": query.period, "lang": query.lang}
    brief = call(bridge, "GET", f"/api/brief/{title_id}", params=params, not_found_entity="trends_film")
    try:
        stats = call(bridge, "GET", f"/api/films/{title_id}", params=params, not_found_entity="trends_film")
    except (NotFound, BadRequest):  # бриф есть, а статистики за период нет — карточка всё равно нужна
        stats = None
    return merge_film_detail(title_id, brief, stats)


@router.get(
    "/trends/films/{title_id}/poster",
    name="film_poster",
    summary="Постер произведения через Kino-trends (его кэш), чтобы консоль не ходила на чужие домены",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}},
)
def trends_film_poster(principal: ViewerDep, title_id: str = TitleId) -> Response:
    return _proxy_image(
        require_bridge(),
        f"/api/films/{title_id}/poster.jpg",
        entity="trends_poster",
        missing="Постера нет",
        cache_control="private, max-age=86400",
    )


@router.get(
    "/trends/films/{title_id}/rights",
    name="film_rights",
    response_model=TrendsFilmRights,
    summary="Оценка авторских прав произведения: уровень, риск, причины, правообладатель, улики",
)
def trends_film_rights(principal: ViewerDep, title_id: str = TitleId):
    return call(require_bridge(), "GET", f"/api/films/{title_id}/rights", not_found_entity="trends_film")


def _audit_film(
    request: Request, principal: Any, action: str, title_id: str, data: Any, *, summary: str
) -> None:
    from autoshorts.db.pool import connection
    from autoshorts.services import audit

    diff = None
    if isinstance(data, dict):
        diff = {k: data.get(k) for k in ("level", "risk", "auto_level", "passes") if k in data}
        signals = data.get("signals") if isinstance(data.get("signals"), dict) else {}
        if "manual_level" in signals:
            diff["manual_level"] = signals.get("manual_level")
        if isinstance(data.get("items"), list):
            diff["sources"] = len(data["items"])
    try:
        with connection() as conn:
            audit.record(
                conn,
                audit_actor(request, principal),
                action,
                "trends_film",
                title_id,
                summary=summary,
                diff=diff or None,
            )
    except Exception:  # noqa: BLE001 - журнал не должен ломать уже выполненное действие
        log.exception("trends audit %s failed", action)


@router.post(
    "/trends/films/{title_id}/rights/refresh",
    name="film_rights_refresh",
    response_model=TrendsFilmRights,
    summary="Перепроверить права: спросить модель о правообладателе, перечитать чужие нарезки, исходники",
)
def trends_film_rights_refresh(principal: OperatorDep, request: Request, title_id: str = TitleId):
    data = call(
        require_bridge(),
        "POST",
        f"/api/films/{title_id}/rights/refresh",
        json={},
        not_found_entity="trends_film",
        timeout=ANALYZE_TIMEOUT_SEC,
    )
    _audit_film(
        request,
        principal,
        "trends.film_rights_refresh",
        title_id,
        data,
        summary=f"кино {title_id}: права перепроверены",
    )
    return data


@router.post(
    "/trends/films/{title_id}/rights/manual",
    name="film_rights_manual",
    response_model=TrendsFilmRights,
    summary="Решение оператора по правам (ok | caution | block) или null — вернуть автоматическую оценку",
)
def trends_film_rights_manual(
    body: TrendsFilmRightsManualRequest, principal: OperatorDep, request: Request, title_id: str = TitleId
):
    payload: dict[str, Any] = {"level": body.level}
    if body.note:
        payload["note"] = body.note
    data = call(
        require_bridge(),
        "POST",
        f"/api/films/{title_id}/rights/manual",
        json=payload,
        not_found_entity="trends_film",
    )
    _audit_film(
        request,
        principal,
        "trends.film_rights_manual",
        title_id,
        data,
        summary=f"кино {title_id}: права {body.level or 'авто'}" + (f" ({body.note})" if body.note else ""),
    )
    return data


@router.get(
    "/trends/films/{title_id}/sources",
    name="film_sources",
    response_model=TrendsFilmSources,
    summary="Исходники произведения (полный фильм, серии) на YouTube и Rutube с проверкой моделью",
)
def trends_film_sources(principal: ViewerDep, title_id: str = TitleId):
    return call(require_bridge(), "GET", f"/api/films/{title_id}/sources", not_found_entity="trends_film")


@router.post(
    "/trends/films/{title_id}/sources",
    name="film_sources_find",
    response_model=TrendsFilmSources,
    summary="Найти исходники заново на YouTube и Rutube (поиск и проверка моделью, до минуты)",
)
def trends_film_sources_find(principal: OperatorDep, request: Request, title_id: str = TitleId):
    data = call(
        require_bridge(),
        "POST",
        f"/api/films/{title_id}/sources",
        json={},
        not_found_entity="trends_film",
        timeout=ANALYZE_TIMEOUT_SEC,
    )
    _audit_film(
        request,
        principal,
        "trends.film_sources_find",
        title_id,
        data,
        summary=f"кино {title_id}: поиск исходников",
    )
    return data
