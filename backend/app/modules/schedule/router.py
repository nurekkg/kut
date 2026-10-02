import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.deps import get_db, require
from app.modules.auth.principal import Principal
from app.modules.idempotency.service import IdempotencyKeyHeader, idempotent_create
from app.modules.rbac.enums import Action, Module
from app.modules.schedule import service
from app.modules.schedule.schemas import (
    DependencyCreate,
    DependencyOut,
    ScheduleItemCreate,
    ScheduleItemOut,
    ScheduleItemPatch,
)

router = APIRouter(prefix="/projects/{project_id}/schedule", tags=["schedule"])

Db = Annotated[AsyncSession, Depends(get_db)]
Reader = Annotated[
    Principal, Depends(require(Module.GPR, Action.READ, project_param="project_id"))
]
Writer = Annotated[
    Principal, Depends(require(Module.GPR, Action.WRITE, project_param="project_id"))
]


# Зависимости объявлены раньше «/{item_id}»: иначе слово dependencies попало бы в параметр пути.
@router.get("/dependencies", response_model=list[DependencyOut])
async def list_dependencies(project_id: uuid.UUID, session: Db, _principal: Reader) -> list[DependencyOut]:
    return await service.list_dependencies(session, project_id)


@router.post(
    "/dependencies", status_code=status.HTTP_201_CREATED, response_model=DependencyOut
)
async def create_dependency(
    project_id: uuid.UUID,
    body: DependencyCreate,
    request: Request,
    key: IdempotencyKeyHeader,
    principal: Writer,
    session: Db,
) -> Response:
    return await idempotent_create(
        session,
        request=request,
        user_id=principal.user_id,
        key=key,
        payload=body,
        handler=lambda: service.create_dependency(session, project_id, body),
    )


@router.delete("/dependencies/{dependency_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dependency(
    project_id: uuid.UUID, dependency_id: uuid.UUID, session: Db, principal: Writer
) -> Response:
    await service.delete_dependency(session, project_id, dependency_id, principal.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("", response_model=list[ScheduleItemOut])
async def list_items(
    project_id: uuid.UUID,
    session: Db,
    principal: Reader,
    node_id: uuid.UUID | None = None,
    subtree: Annotated[bool, Query(description="узел и все его потомки")] = False,
    work_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ScheduleItemOut]:
    return await service.list_items(
        session,
        project_id,
        internal=principal.is_internal,
        node_id=node_id,
        subtree=subtree,
        work_id=work_id,
        limit=limit,
        offset=offset,
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ScheduleItemOut)
async def create_item(
    project_id: uuid.UUID,
    body: ScheduleItemCreate,
    request: Request,
    key: IdempotencyKeyHeader,
    principal: Writer,
    session: Db,
) -> Response:
    return await idempotent_create(
        session,
        request=request,
        user_id=principal.user_id,
        key=key,
        payload=body,
        handler=lambda: service.create_item(session, project_id, body),
    )


@router.get("/{item_id}", response_model=ScheduleItemOut)
async def get_item(
    project_id: uuid.UUID, item_id: uuid.UUID, session: Db, principal: Reader
) -> ScheduleItemOut:
    return await service.get_item(session, project_id, item_id, internal=principal.is_internal)


@router.patch("/{item_id}", response_model=ScheduleItemOut)
async def update_item(
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    body: ScheduleItemPatch,
    session: Db,
    _principal: Writer,
) -> ScheduleItemOut:
    return await service.update_item(session, project_id, item_id, body)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    project_id: uuid.UUID, item_id: uuid.UUID, session: Db, principal: Writer
) -> Response:
    await service.delete_item(session, project_id, item_id, principal.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
