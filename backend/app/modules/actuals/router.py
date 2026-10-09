import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.actuals import service
from app.modules.actuals.models import ActualStatus
from app.modules.actuals.schemas import ActualCreate, ActualOut, ActualPatch, ActualReject
from app.modules.auth.deps import get_db, require
from app.modules.auth.principal import Principal
from app.modules.idempotency.service import IdempotencyKeyHeader, idempotent_create
from app.modules.rbac.enums import Action, Module

router = APIRouter(prefix="/projects/{project_id}/work-actuals", tags=["work-actuals"])

Db = Annotated[AsyncSession, Depends(get_db)]
Reader = Annotated[
    Principal, Depends(require(Module.WORK_ACTUAL, Action.READ, project_param="project_id"))
]
Writer = Annotated[
    Principal, Depends(require(Module.WORK_ACTUAL, Action.WRITE, project_param="project_id"))
]
Approver = Annotated[
    Principal, Depends(require(Module.WORK_ACTUAL, Action.APPROVE, project_param="project_id"))
]


@router.get("", response_model=list[ActualOut])
async def list_actuals(
    project_id: uuid.UUID,
    session: Db,
    principal: Reader,
    node_id: uuid.UUID | None = None,
    subtree: Annotated[bool, Query(description="узел и все его потомки")] = False,
    work_id: uuid.UUID | None = None,
    status_in: Annotated[list[ActualStatus] | None, Query(alias="status")] = None,
    author_id: uuid.UUID | None = None,
    mine: Annotated[bool, Query(description="только мои факты")] = False,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ActualOut]:
    """Внешним ролям отдаётся только подтверждённый факт (граница в БД: политика kut_portal)."""
    return await service.list_actuals(
        session,
        project_id,
        internal=principal.is_internal,
        node_id=node_id,
        subtree=subtree,
        work_id=work_id,
        statuses=[item.value for item in status_in] if status_in else None,
        author_id=principal.user_id if mine else author_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ActualOut)
async def create_actual(
    project_id: uuid.UUID,
    body: ActualCreate,
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
        handler=lambda: service.create_actual(session, project_id, body, principal.user_id),
    )


@router.get("/{actual_id}", response_model=ActualOut)
async def get_actual(
    project_id: uuid.UUID, actual_id: uuid.UUID, session: Db, principal: Reader
) -> ActualOut:
    return await service.get_actual(session, project_id, actual_id, internal=principal.is_internal)


@router.patch("/{actual_id}", response_model=ActualOut)
async def update_actual(
    project_id: uuid.UUID,
    actual_id: uuid.UUID,
    body: ActualPatch,
    session: Db,
    _principal: Writer,
) -> ActualOut:
    """Правка содержания: автор - пока факт черновик или отклонён; согласованный - техдиректор."""
    return await service.update_actual(session, project_id, actual_id, body)


@router.delete("/{actual_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_actual(
    project_id: uuid.UUID, actual_id: uuid.UUID, session: Db, principal: Writer
) -> Response:
    await service.delete_actual(session, project_id, actual_id, principal.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{actual_id}/submit", response_model=ActualOut)
async def submit_actual(
    project_id: uuid.UUID, actual_id: uuid.UUID, session: Db, _principal: Writer
) -> ActualOut:
    return await service.submit_actual(session, project_id, actual_id)


@router.post("/{actual_id}/approve", response_model=ActualOut)
async def approve_actual(
    project_id: uuid.UUID, actual_id: uuid.UUID, session: Db, _principal: Approver
) -> ActualOut:
    return await service.approve_actual(session, project_id, actual_id)


@router.post("/{actual_id}/reject", response_model=ActualOut)
async def reject_actual(
    project_id: uuid.UUID,
    actual_id: uuid.UUID,
    body: ActualReject,
    session: Db,
    _principal: Approver,
) -> ActualOut:
    return await service.reject_actual(session, project_id, actual_id, body.reason)
