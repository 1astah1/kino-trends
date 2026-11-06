"""Публикации: разбор ссылки, регистрация, реестр, метрики (§5.2, §5.4 Publication). Владелец — M3-A.

Контракт — `api/schemas/publications.py`. Заглушки контракта M5M3-0 отвечают 501; M3-A заменяет тела,
не меняя пути, имена и схемы. Разбор ссылок — `domain/url_parse.py` (25+ форм YouTube и VK).
Регистрация — одна транзакция (публикация, алиас-ссылка, пакет и ролик → published, мастер в
`masters/`), `Idempotency-Key` как у `POST /jobs`. Удаление — только admin.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from autoshorts.api.deps import AdminDep, OperatorDep, ViewerDep, audit_actor
from autoshorts.api.schemas.common import (
    SLUG_PATTERN,
    DateFromQuery,
    DateToQuery,
    LimitQuery,
    OffsetQuery,
    SortQuery,
    TaskAccepted,
)
from autoshorts.api.schemas.publications import (
    MetricsGranularity,
    ParsedUrl,
    ParseUrlRequest,
    Publication,
    PublicationCreate,
    PublicationCreated,
    PublicationMetrics,
    PublicationPage,
    PublicationPatch,
    PublicationRetention,
    PublicationTrafficSeries,
)
from autoshorts.api.schemas.tracking import TrafficGranularity
from autoshorts.domain.enums import JobKind, Platform, PublicationStatus, VerifyStatus

router = APIRouter(tags=["publications"])


def _connection() -> Any:
    from autoshorts.db.pool import connection

    return connection()


def _svc() -> Any:
    from autoshorts.services import publications

    return publications


def _caller(request: Request, principal: Any) -> Any:
    from autoshorts.services.jobs import Caller

    return Caller.from_principal(principal, audit_actor(request, principal))


def _out(conn: Any, row: dict[str, Any]) -> dict[str, Any]:
    return Publication.model_validate(_svc().publication_view(conn, row)).model_dump(mode="json")


SlugQuery = Annotated[str | None, Query(pattern=SLUG_PATTERN)]
IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", max_length=128, description="Повтор с тем же ключом вернёт тот же ответ"),
]


@router.post(
    "/publications/parse-url",
    name="parse_url",
    response_model=ParsedUrl,
    summary="Разобрать ссылку YouTube/VK (422 unsupported_url)",
)
def parse_url(body: ParseUrlRequest, principal: OperatorDep):
    return _svc().parse_url(body.url)


@router.post(
    "/publications",
    name="create",
    status_code=201,
    response_model=PublicationCreated,
    summary="Зарегистрировать публикацию по ссылке (409, 422 platform_mismatch)",
)
def create_publication(
    body: PublicationCreate, principal: OperatorDep, request: Request, idempotency_key: IdempotencyKey = None
):
    from autoshorts.api.schemas.tracking import TrackingLink
    from autoshorts.core.errors import Conflict
    from autoshorts.repos import jobs as jobs_repo
    from autoshorts.services import tracking as tracking_service

    scope = "publications"
    with _connection() as conn:
        if idempotency_key:
            stored = jobs_repo.idempotency_claim(conn, int(principal.user_id), f"{scope}:{idempotency_key}")
            if stored is not None:
                if not stored["response"]:
                    raise Conflict("Запрос с этим ключом ещё выполняется", code="idempotency_in_progress")
                return JSONResponse(stored["response"], status_code=201)
        pub, links = _svc().register(
            conn, body.package_id, body.url, body.published_at, _caller(request, principal)
        )
        out = PublicationCreated.model_validate(
            {
                "publication": _svc().publication_view(conn, _svc().require(conn, int(pub["id"]))),
                "tracking_links": [
                    TrackingLink.model_validate(tracking_service.link_view(conn, link)) for link in links
                ],
            }
        ).model_dump(mode="json")
        if idempotency_key:
            jobs_repo.idempotency_store(conn, int(principal.user_id), f"{scope}:{idempotency_key}", out)
    return JSONResponse(out, status_code=201)


@router.get("/publications", name="list", response_model=PublicationPage, summary="Реестр публикаций")
def list_publications(
    principal: ViewerDep,
    platform: Platform | None = None,
    channel_id: SlugQuery = None,
    niche_id: SlugQuery = None,
    product_id: SlugQuery = None,
    campaign_id: int | None = None,
    banner_variant_id: int | None = None,
    kind: JobKind | None = None,
    status: list[PublicationStatus] | None = Query(default=None),
    verify_status: list[VerifyStatus] | None = Query(default=None),
    date_from: DateFromQuery = None,
    date_to: DateToQuery = None,
    sort: SortQuery = "-published_at",
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
):
    with _connection() as conn:
        return _svc().list_publications(
            conn,
            platform=platform.value if platform else None,
            channel_id=channel_id,
            niche_id=niche_id,
            product_id=product_id,
            campaign_id=campaign_id,
            banner_variant_id=banner_variant_id,
            kind=kind.value if kind else None,
            statuses=[x.value for x in status] if status else None,
            verify_statuses=[x.value for x in verify_status] if verify_status else None,
            date_from=date_from,
            date_to=date_to,
            sort=sort or "-published_at",
            limit=limit,
            offset=offset,
        )


@router.post(
    "/publications/analytics/collect",
    name="collect_analytics",
    response_model=dict[str, Any],
    summary="Досмотры и удержание из YouTube Analytics по подключённым каналам (канал не чаще раза в 3 ч)",
)
def collect_analytics(principal: OperatorDep, force: bool = False):
    from autoshorts.services import yt_analytics as analytics

    return analytics.collect(force=force)


@router.get(
    "/publications/{publication_id}", name="get", response_model=Publication, summary="Карточка публикации"
)
def get_publication(publication_id: int, principal: ViewerDep):
    with _connection() as conn:
        return _out(conn, _svc().require(conn, publication_id))


@router.patch(
    "/publications/{publication_id}",
    name="update",
    response_model=Publication,
    summary="Статус и отметки (удалена площадкой, клейм, …)",
)
def update_publication(publication_id: int, body: PublicationPatch, principal: OperatorDep, request: Request):
    with _connection() as conn:
        _svc().patch(
            conn, publication_id, body.model_dump(exclude_unset=True), audit_actor(request, principal)
        )
        return _out(conn, _svc().require(conn, publication_id))


@router.delete(
    "/publications/{publication_id}",
    name="delete",
    status_code=204,
    response_class=Response,
    summary="Мягко удалить ошибочную регистрацию (admin; ролик → approved, пакет → ready)",
)
def delete_publication(publication_id: int, principal: AdminDep, request: Request):
    with _connection() as conn:
        _svc().delete(conn, publication_id, _caller(request, principal))
    return Response(status_code=204)


@router.post(
    "/publications/{publication_id}/remove-remote",
    name="remove_remote",
    response_model=Publication,
    summary="Снять ролик с YouTube (удалить на площадке; у подключения канала нужно право youtube.force-ssl)",
)
def remove_remote_publication(publication_id: int, principal: OperatorDep, request: Request):
    with _connection() as conn:
        view = _svc().remove_remote(conn, publication_id, _caller(request, principal))
        return Publication.model_validate(view).model_dump(mode="json")


@router.post(
    "/publications/{publication_id}/reverify",
    name="reverify",
    status_code=202,
    response_model=TaskAccepted,
    summary="Повторить проверку принадлежности каналу",
)
def reverify_publication(publication_id: int, principal: OperatorDep, request: Request):
    with _connection() as conn:
        _svc().require(conn, publication_id)
        return {"task_id": _svc().enqueue_verify(conn, publication_id)}


@router.post(
    "/publications/{publication_id}/refresh-metrics",
    name="refresh_metrics",
    status_code=202,
    response_model=TaskAccepted,
    summary="Внеочередной опрос метрик",
)
def refresh_metrics(publication_id: int, principal: OperatorDep, request: Request):
    with _connection() as conn:
        return {"task_id": _svc().refresh_metrics(conn, publication_id)}


@router.get(
    "/publications/{publication_id}/metrics",
    name="metrics",
    response_model=PublicationMetrics,
    summary="Ряд метрик",
)
def publication_metrics(
    publication_id: int, principal: ViewerDep, granularity: MetricsGranularity = MetricsGranularity.RAW
):
    with _connection() as conn:
        return _svc().metrics(conn, publication_id, granularity.value)


@router.get(
    "/publications/{publication_id}/retention",
    name="retention",
    response_model=PublicationRetention,
    summary="Кривая удержания (только при OAuth YouTube Analytics)",
)
def publication_retention(publication_id: int, principal: ViewerDep):
    with _connection() as conn:
        return _svc().retention(conn, publication_id)


@router.get(
    "/publications/{publication_id}/traffic",
    name="traffic",
    response_model=PublicationTrafficSeries,
    summary="Клики и конверсии по часам/дням",
)
def publication_traffic(
    publication_id: int, principal: ViewerDep, granularity: TrafficGranularity = TrafficGranularity.DAY
):
    with _connection() as conn:
        return _svc().traffic(conn, publication_id, granularity.value)
