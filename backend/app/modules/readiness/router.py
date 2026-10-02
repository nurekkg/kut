import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.deps import get_db, require
from app.modules.auth.principal import Principal
from app.modules.rbac.enums import Action, Module
from app.modules.readiness import service
from app.modules.readiness.schemas import NodeReadinessDetail, ProjectReadiness

router = APIRouter(prefix="/projects/{project_id}", tags=["readiness"])

Db = Annotated[AsyncSession, Depends(get_db)]
Reader = Annotated[
    Principal, Depends(require(Module.GPR, Action.READ, project_param="project_id"))
]
AsOf = Annotated[date | None, Query(description="дата расчёта (по умолчанию сегодня, пояс Бишкека)")]


@router.get("/readiness", response_model=ProjectReadiness)
async def project_readiness(
    project_id: uuid.UUID, session: Db, _principal: Reader, as_of: AsOf = None
) -> ProjectReadiness:
    """% готовности объекта и узлов с ГПР: работа = min(факт / план, 1), узел - среднее по весам."""
    return await service.project_readiness(session, project_id, as_of)


@router.get("/nodes/{node_id}/readiness", response_model=NodeReadinessDetail)
async def node_readiness(
    project_id: uuid.UUID, node_id: uuid.UUID, session: Db, _principal: Reader, as_of: AsOf = None
) -> NodeReadinessDetail:
    """Готовность узла: свои работы, итог поддерева, дети, работы без плана (в % не входят)."""
    return await service.node_readiness(session, project_id, node_id, as_of)
