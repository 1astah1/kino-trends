"""Кусты изоляции (M2-A)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Request

from autoshorts.api.deps import OperatorDep, ViewerDep, audit_actor
from autoshorts.api.schemas.clusters import Cluster, ClusterCreate, ClusterList, ClusterPatch
from autoshorts.api.schemas.common import SLUG_PATTERN
from autoshorts.services import clusters as clusters_service

router = APIRouter(tags=["clusters"])

ClusterIdPath = Annotated[str, Path(pattern=SLUG_PATTERN)]


@router.get("/clusters", name="list", response_model=ClusterList, summary="Кусты")
def list_clusters(principal: ViewerDep):
    return clusters_service.list_clusters()


@router.post("/clusters", name="create", status_code=201, response_model=Cluster, summary="Создать куст")
def create_cluster(body: ClusterCreate, principal: OperatorDep, request: Request):
    return clusters_service.create_cluster(
        cluster_id=body.id, title=body.title, notes=body.notes, actor=audit_actor(request, principal)
    )


@router.get("/clusters/{cluster_id}", name="get", response_model=Cluster, summary="Карточка куста")
def get_cluster(cluster_id: ClusterIdPath, principal: ViewerDep):
    return clusters_service.get_cluster(cluster_id)


@router.patch("/clusters/{cluster_id}", name="update", response_model=Cluster, summary="Правка куста")
def update_cluster(cluster_id: ClusterIdPath, body: ClusterPatch, principal: OperatorDep, request: Request):
    changes = {name: getattr(body, name) for name in body.model_fields_set}
    return clusters_service.update_cluster(cluster_id, changes, audit_actor(request, principal))
