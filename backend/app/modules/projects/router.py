import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.deps import get_db, require
from app.modules.auth.principal import Principal, principal_store
from app.modules.idempotency.service import IdempotencyKeyHeader, idempotent_create
from app.modules.identity.models import User
from app.modules.org.models import Counterparty, LegalEntity
from app.modules.projects import service
from app.modules.projects.schemas import (
    FINANCE_FIELDS,
    NodeCreate,
    NodeDetail,
    NodeMove,
    NodeOut,
    NodePatch,
    ProjectCreate,
    ProjectOut,
    ProjectPatch,
)
from app.modules.rbac.enums import Action, Module
from app.modules.rbac.models import Role

router = APIRouter(tags=["projects"])

Db = Annotated[AsyncSession, Depends(get_db)]


def require_project_admin(
    project_param: str | None = None,
) -> Callable[..., Awaitable[Principal]]:
    """Карточка объекта (создание, правка, удаление): право записи и роль scope=group.

    Матрица §7.3 даёт прорабу «R/W (свой)»: он ведёт дерево и ответственных своего объекта, но не
    бюджет, стадию и заказчика - их меняют владелец и технический директор.
    """
    base = require(Module.OBJECTS, Action.WRITE, project_param=project_param)

    async def dependency(principal: Annotated[Principal, Depends(base)]) -> Principal:
        if principal.role_scope != "group":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
        return principal

    return dependency


Reader = Annotated[Principal, Depends(require(Module.OBJECTS, Action.READ))]
ProjectReader = Annotated[
    Principal, Depends(require(Module.OBJECTS, Action.READ, project_param="project_id"))
]
ProjectWriter = Annotated[
    Principal, Depends(require(Module.OBJECTS, Action.WRITE, project_param="project_id"))
]
Admin = Annotated[Principal, Depends(require_project_admin())]
ProjectAdmin = Annotated[Principal, Depends(require_project_admin("project_id"))]


def finance_visible(principal: Principal) -> bool:
    """Бюджет, заказчик, адрес и гарантия объекта - только внутренним ролям с правом finance:read."""
    return principal.is_internal and principal.has(Module.FINANCE, Action.READ)


def _require_finance_for(fields: set[str], principal: Principal) -> None:
    """Писать коммерческие поля карточки нельзя, не имея права их читать."""
    if fields & set(FINANCE_FIELDS) and not finance_visible(principal):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")


async def _create(
    request: Request,
    session: AsyncSession,
    principal: Principal,
    key: uuid.UUID,
    payload: BaseModel,
    handler: Callable[[], Awaitable[BaseModel]],
) -> Response:
    return await idempotent_create(
        session,
        request=request,
        user_id=principal.user_id,
        key=key,
        payload=payload,
        handler=handler,
    )


# --- объекты ----------------------------------------------------------------------------------


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(session: Db, principal: Reader) -> list[ProjectOut]:
    """Объекты, видимые пользователю: фильтр делает RLS, а не код запроса."""
    return await service.list_projects(
        session, internal=principal.is_internal, finance=finance_visible(principal)
    )


@router.post("/projects", status_code=status.HTTP_201_CREATED, response_model=ProjectOut)
async def create_project(
    body: ProjectCreate, request: Request, key: IdempotencyKeyHeader, principal: Admin, session: Db
) -> Response:
    given = {field for field in FINANCE_FIELDS if getattr(body, field) is not None}
    _require_finance_for(given, principal)
    reply = await _create(
        request,
        session,
        principal,
        key,
        body,
        lambda: service.create_project(
            session, body, principal.legal_entity_ids, finance=finance_visible(principal)
        ),
    )
    # Новый объект сразу виден всем ролям scope=group, а не через минуту кэша прав.
    await principal_store.bump_scope()
    return reply


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(project_id: uuid.UUID, session: Db, principal: ProjectReader) -> ProjectOut:
    return await service.get_project(
        session, project_id, internal=principal.is_internal, finance=finance_visible(principal)
    )


@router.patch("/projects/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID, body: ProjectPatch, session: Db, principal: ProjectAdmin
) -> ProjectOut:
    _require_finance_for(set(body.model_fields_set), principal)
    return await service.update_project(
        session, project_id, body, finance=finance_visible(principal)
    )


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: uuid.UUID, session: Db, principal: ProjectAdmin) -> Response:
    await service.delete_project(session, project_id, principal.user_id)
    await principal_store.bump_scope()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- дерево объекта ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/tree", response_model=list[NodeOut])
async def get_tree(project_id: uuid.UUID, session: Db, principal: ProjectReader) -> Response:
    """Всё дерево одним запросом: плоский список с parent_id и path (id предков от корня).

    Тело - готовый JSON из БД (форма NodeOut): на тысячах узлов так быстрее в разы.
    """
    document = await service.tree_json(session, project_id, internal=principal.is_internal)
    return Response(content=document, media_type="application/json")


@router.post(
    "/projects/{project_id}/nodes", status_code=status.HTTP_201_CREATED, response_model=NodeDetail
)
async def create_node(
    project_id: uuid.UUID,
    body: NodeCreate,
    request: Request,
    key: IdempotencyKeyHeader,
    principal: ProjectWriter,
    session: Db,
) -> Response:
    return await _create(
        request,
        session,
        principal,
        key,
        body,
        lambda: service.create_node(session, project_id, body),
    )


@router.get("/projects/{project_id}/nodes/{node_id}", response_model=NodeDetail)
async def get_node(
    project_id: uuid.UUID, node_id: uuid.UUID, session: Db, principal: ProjectReader
) -> NodeDetail:
    return await service.get_node(session, project_id, node_id, internal=principal.is_internal)


@router.patch("/projects/{project_id}/nodes/{node_id}", response_model=NodeDetail)
async def update_node(
    project_id: uuid.UUID,
    node_id: uuid.UUID,
    body: NodePatch,
    session: Db,
    _principal: ProjectWriter,
) -> NodeDetail:
    return await service.update_node(session, project_id, node_id, body)


@router.delete("/projects/{project_id}/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    project_id: uuid.UUID, node_id: uuid.UUID, session: Db, principal: ProjectWriter
) -> Response:
    await service.delete_node(session, project_id, node_id, principal.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/projects/{project_id}/nodes/{node_id}/move", response_model=NodeDetail)
async def move_node(
    project_id: uuid.UUID,
    node_id: uuid.UUID,
    body: NodeMove,
    session: Db,
    _principal: ProjectWriter,
) -> NodeDetail:
    """Перенос поддерева под другого родителя и/или смена позиции среди братьев."""
    return await service.move_node(session, project_id, node_id, body.parent_id, body.index)


# --- справочники для форм ----------------------------------------------------------------------


class RefOut(BaseModel):
    id: uuid.UUID
    name: str


# Эти справочники нужны только форме карточки объекта, а карточку правят роли scope=group с
# правом записи: остальным (прораб, мастер, внешние) список юрлиц и заказчиков группы не отдаётся.
@router.get("/legal-entities", response_model=list[RefOut])
async def list_legal_entities(session: Db, _principal: Admin) -> list[RefOut]:
    """Юрлица, видимые пользователю (форма создания объекта): границу ставит RLS."""
    rows = await session.execute(
        select(LegalEntity.id, LegalEntity.name).order_by(LegalEntity.name, LegalEntity.id)
    )
    return [RefOut.model_validate(row, from_attributes=True) for row in rows]


@router.get("/customers", response_model=list[RefOut])
async def list_customers(session: Db, _principal: Admin) -> list[RefOut]:
    """Контрагенты с типом «заказчик» (поле заказчика в карточке объекта)."""
    rows = await session.execute(
        select(Counterparty.id, Counterparty.name)
        .where(Counterparty.types.contains(["customer"]))
        .order_by(Counterparty.name, Counterparty.id)
    )
    return [RefOut.model_validate(row, from_attributes=True) for row in rows]


# --- пользователи (справочник для форм) --------------------------------------------------------


class UserOut(BaseModel):
    id: uuid.UUID
    full_name: str
    role: str


@router.get(
    "/users",
    response_model=list[UserOut],
    dependencies=[Depends(require(Module.OBJECTS, Action.READ))],
)
async def list_users(session: Db) -> list[UserOut]:
    """Видимость задаёт политика user_select_app (S1b): коллеги по объектам или все."""
    rows = await session.execute(
        select(User.id, User.full_name, Role.code.label("role"))
        .join(Role, Role.id == User.role_id)
        .order_by(User.full_name, User.id)
    )
    return [UserOut.model_validate(row, from_attributes=True) for row in rows]
