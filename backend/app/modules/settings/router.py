import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.deps import get_db, require
from app.modules.auth.errors import Conflict, InvalidInput, NotFound, PermissionDenied
from app.modules.auth.principal import Principal, principal_store
from app.modules.idempotency.service import IdempotencyKeyHeader, fingerprint, run_idempotent
from app.modules.rbac.enums import Action, Module
from app.modules.settings import service
from app.modules.settings.schemas import (
    AccessChange,
    ActiveChange,
    CounterpartyIn,
    CounterpartyOut,
    EventPage,
    InviteOut,
    LegalEntityIn,
    LegalEntityOut,
    ProjectOut,
    RoleChange,
    RoleOut,
    UserAdminOut,
    UserCreate,
)
from app.modules.settings.service import SettingsError, UserFilter

# Весь раздел только для роли с settings:admin (сейчас владелец): нет права - 403.
router = APIRouter(
    prefix="/settings",
    tags=["settings"],
    dependencies=[Depends(require(Module.SETTINGS, Action.ADMIN))],
)

Db = Annotated[AsyncSession, Depends(get_db)]
Admin = Annotated[Principal, Depends(require(Module.SETTINGS, Action.ADMIN))]


@contextmanager
def translated() -> Iterator[None]:
    """Ошибки сервисного слоя и функций БД превращаются в ответы с понятным кодом."""
    try:
        yield
    except SettingsError as error:
        raise HTTPException(error.status, error.code) from error
    except Conflict as error:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{error.field}_taken") from error
    except InvalidInput as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, error.code) from error
    except NotFound as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from error
    except PermissionDenied as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden") from error


async def _create(
    request: Request,
    session: AsyncSession,
    admin: Principal,
    key: uuid.UUID,
    payload: Any,
    handler: Any,
) -> Response:
    """Создание под Idempotency-Key: запись ключа, изменения и ответ - одна транзакция."""

    async def run() -> tuple[int, Any]:
        with translated():
            created = await handler()
        return status.HTTP_201_CREATED, created.model_dump(mode="json")

    result = await run_idempotent(
        session,
        user_id=admin.user_id,
        key=key,
        request_hash=fingerprint(request.method, request.url.path, payload.model_dump(mode="json")),
        handler=run,
    )
    return result.to_response()


# --- юрлица -----------------------------------------------------------------------------------


@router.get("/legal-entities", response_model=list[LegalEntityOut])
async def list_legal_entities(session: Db) -> list[LegalEntityOut]:
    return await service.list_legal_entities(session)


@router.post("/legal-entities", status_code=status.HTTP_201_CREATED, response_model=LegalEntityOut)
async def create_legal_entity(
    body: LegalEntityIn, request: Request, key: IdempotencyKeyHeader, admin: Admin, session: Db
) -> Response:
    reply = await _create(
        request, session, admin, key, body, lambda: service.create_legal_entity(session, body)
    )
    # Новое юрлицо входит в контекст владельца при следующей выдаче прав, а не через минуту кэша.
    await principal_store.forget(admin.user_id)
    return reply


@router.patch("/legal-entities/{entity_id}", response_model=LegalEntityOut)
async def update_legal_entity(
    entity_id: uuid.UUID, body: LegalEntityIn, session: Db
) -> LegalEntityOut:
    with translated():
        return await service.update_legal_entity(session, entity_id, body)


@router.delete("/legal-entities/{entity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_legal_entity(entity_id: uuid.UUID, admin: Admin, session: Db) -> Response:
    with translated():
        await service.delete_legal_entity(session, entity_id, admin.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- контрагенты --------------------------------------------------------------------------------


@router.get("/counterparties", response_model=list[CounterpartyOut])
async def list_counterparties(
    session: Db,
    type: Annotated[str | None, Query(max_length=20)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CounterpartyOut]:
    return await service.list_counterparties(
        session, kind=type, query=q, limit=limit, offset=offset
    )


@router.post("/counterparties", status_code=status.HTTP_201_CREATED, response_model=CounterpartyOut)
async def create_counterparty(
    body: CounterpartyIn, request: Request, key: IdempotencyKeyHeader, admin: Admin, session: Db
) -> Response:
    return await _create(
        request, session, admin, key, body, lambda: service.create_counterparty(session, body)
    )


@router.patch("/counterparties/{entity_id}", response_model=CounterpartyOut)
async def update_counterparty(
    entity_id: uuid.UUID, body: CounterpartyIn, session: Db
) -> CounterpartyOut:
    with translated():
        return await service.update_counterparty(session, entity_id, body)


@router.delete("/counterparties/{entity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_counterparty(entity_id: uuid.UUID, admin: Admin, session: Db) -> Response:
    with translated():
        await service.delete_counterparty(session, entity_id, admin.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- справочники для форм ----------------------------------------------------------------------


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(session: Db) -> list[RoleOut]:
    return await service.list_roles(session)


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(session: Db) -> list[ProjectOut]:
    return await service.list_projects(session)


# --- пользователи -------------------------------------------------------------------------------


@router.get("/users", response_model=list[UserAdminOut])
async def list_users(
    session: Db,
    role: Annotated[str | None, Query(max_length=40)] = None,
    project_id: uuid.UUID | None = None,
    active: bool | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[UserAdminOut]:
    filters = UserFilter(role=role, project_id=project_id, active=active, query=q)
    return await service.list_users(session, filters, limit=limit, offset=offset)


@router.post("/users", status_code=status.HTTP_201_CREATED, response_model=UserAdminOut)
async def create_user(
    body: UserCreate, request: Request, key: IdempotencyKeyHeader, admin: Admin, session: Db
) -> Response:
    return await _create(
        request, session, admin, key, body, lambda: service.create_user(session, body)
    )


@router.get("/users/{user_id}", response_model=UserAdminOut)
async def get_user(user_id: uuid.UUID, session: Db) -> UserAdminOut:
    with translated():
        return await service.get_user(session, user_id)


@router.put("/users/{user_id}/role", response_model=UserAdminOut)
async def change_role(user_id: uuid.UUID, body: RoleChange, session: Db) -> UserAdminOut:
    with translated():
        return await service.change_role(session, user_id, body.role_id)


@router.put("/users/{user_id}/active", response_model=UserAdminOut)
async def set_active(user_id: uuid.UUID, body: ActiveChange, session: Db) -> UserAdminOut:
    with translated():
        return await service.set_active(session, user_id, body.active)


@router.put("/users/{user_id}/access", response_model=UserAdminOut)
async def set_access(
    user_id: uuid.UUID, body: AccessChange, admin: Admin, session: Db
) -> UserAdminOut:
    with translated():
        return await service.set_access(session, user_id, body, admin.user_id)


@router.post("/users/{user_id}/invite", response_model=InviteOut)
async def issue_invite(user_id: uuid.UUID, session: Db, response: Response) -> InviteOut:
    response.headers["Cache-Control"] = "no-store"  # в ответе одноразовая ссылка
    with translated():
        return await service.issue_invite(session, user_id)


@router.post("/users/{user_id}/reset-mfa", response_model=UserAdminOut)
async def reset_mfa(user_id: uuid.UUID, session: Db) -> UserAdminOut:
    with translated():
        return await service.reset_mfa(session, user_id)


# --- журнал событий входа ------------------------------------------------------------------------


@router.get("/events", response_model=EventPage)
async def list_events(
    session: Db,
    action: Annotated[str | None, Query(max_length=60)] = None,
    actor_id: uuid.UUID | None = None,
    ip: Annotated[str | None, Query(max_length=45)] = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> EventPage:
    return await service.list_events(
        session,
        limit=limit,
        before=before,
        action=action,
        actor=actor_id,
        ip=ip,
        date_from=date_from,
        date_to=date_to,
    )
