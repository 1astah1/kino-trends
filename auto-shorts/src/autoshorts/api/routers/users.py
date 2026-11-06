"""Пользователи (только admin, только из веб-сессии). Владелец — M1-B."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from autoshorts.api.deps import AdminSessionDep, audit_actor
from autoshorts.api.schemas.users import ResetPasswordRequest, User, UserCreate, UserList, UserUpdate
from autoshorts.services import users as users_service

router = APIRouter(tags=["users"])


@router.get("/users", name="list", response_model=UserList, summary="Список пользователей")
def list_users(principal: AdminSessionDep):
    return {"items": users_service.list_users()}


@router.post("/users", name="create", status_code=201, response_model=User, summary="Создать пользователя")
def create_user(body: UserCreate, principal: AdminSessionDep, request: Request):
    return users_service.create_user(
        login=body.login,
        display_name=body.display_name,
        role=body.role,
        password=body.password,
        tg_user_id=body.tg_user_id,
        actor=audit_actor(request, principal),
    )


@router.patch("/users/{user_id}", name="update", response_model=User, summary="Правка пользователя")
def update_user(user_id: int, body: UserUpdate, principal: AdminSessionDep, request: Request):
    changes = {name: getattr(body, name) for name in body.model_fields_set}
    return users_service.update_user(user_id, changes, audit_actor(request, principal))


@router.post(
    "/users/{user_id}/reset-password",
    name="reset_password",
    status_code=204,
    response_class=Response,
    summary="Сброс пароля",
)
def reset_password(user_id: int, body: ResetPasswordRequest, principal: AdminSessionDep, request: Request):
    users_service.reset_password(user_id, body.new_password, audit_actor(request, principal))
    return Response(status_code=204)
