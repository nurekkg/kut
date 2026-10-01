import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.deps import get_db, require
from app.modules.auth.principal import Principal
from app.modules.idempotency.service import IdempotencyKeyHeader, idempotent_create
from app.modules.rbac.enums import Action, Module
from app.modules.works import service
from app.modules.works.schemas import WorkCreate, WorkOut, WorkPatch

router = APIRouter(prefix="/works", tags=["works"])

Db = Annotated[AsyncSession, Depends(get_db)]
Reader = Depends(require(Module.WORKS, Action.READ))
Writer = Annotated[Principal, Depends(require(Module.WORKS, Action.WRITE))]


@router.get("", response_model=list[WorkOut], dependencies=[Reader])
async def list_works(
    session: Db,
    q: Annotated[str | None, Query(max_length=100)] = None,
    category: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[WorkOut]:
    return await service.list_works(session, query=q, category=category, limit=limit, offset=offset)


@router.get("/categories", response_model=list[str], dependencies=[Reader])
async def list_categories(session: Db) -> list[str]:
    """Категории, уже встречающиеся в справочнике: подсказка для формы."""
    return await service.list_categories(session)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=WorkOut)
async def create_work(
    body: WorkCreate, request: Request, key: IdempotencyKeyHeader, principal: Writer, session: Db
) -> Response:
    return await idempotent_create(
        session,
        request=request,
        user_id=principal.user_id,
        key=key,
        payload=body,
        handler=lambda: service.create_work(session, body),
    )


@router.get("/{work_id}", response_model=WorkOut, dependencies=[Reader])
async def get_work(work_id: uuid.UUID, session: Db) -> WorkOut:
    return await service.get_work(session, work_id)


@router.patch("/{work_id}", response_model=WorkOut)
async def update_work(
    work_id: uuid.UUID, body: WorkPatch, session: Db, _principal: Writer
) -> WorkOut:
    return await service.update_work(session, work_id, body)


@router.delete("/{work_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_work(work_id: uuid.UUID, session: Db, principal: Writer) -> Response:
    await service.delete_work(session, work_id, principal.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
