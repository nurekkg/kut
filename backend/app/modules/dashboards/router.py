import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.deps import get_db, internal_only, require
from app.modules.auth.principal import Principal
from app.modules.dashboards import service
from app.modules.dashboards.schemas import GroupDashboard, ObjectDashboard, PortalDashboard
from app.modules.rbac.enums import Action, Module, RoleScope

router = APIRouter(prefix="/dashboards", tags=["dashboards"])

Db = Annotated[AsyncSession, Depends(get_db)]


def _group_only(
    base: Callable[..., Awaitable[Principal]],
) -> Callable[..., Awaitable[Principal]]:
    """Группа (владелец, финансовый и технический директора) - scope=group. Прораб видит свои
    объекты, но сводку по группе ему не отдаём, даже если право на объекты есть."""

    async def dependency(principal: Annotated[Principal, Depends(base)]) -> Principal:
        if principal.role_scope != RoleScope.GROUP:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
        return principal

    return dependency


ObjectReader = Annotated[
    Principal,
    Depends(internal_only(require(Module.GPR, Action.READ, project_param="project_id"))),
]
PortalReader = Annotated[
    Principal, Depends(require(Module.GPR, Action.READ, project_param="project_id"))
]
GroupReader = Annotated[Principal, Depends(_group_only(require(Module.OBJECTS, Action.READ)))]


@router.get("/object/{project_id}", response_model=ObjectDashboard)
async def object_dashboard(
    project_id: uuid.UUID, session: Db, principal: ObjectReader
) -> ObjectDashboard:
    """Дашборд объекта: % факт и план на сегодня, отставание, ряд по неделям, узлы верхнего
    уровня, проблемные работы и зоны, фото. Бюджет - при finance:read, факт и фото - при
    work_actual:read."""
    return await service.object_dashboard(session, principal, project_id)


@router.get("/group", response_model=GroupDashboard)
async def group_dashboard(session: Db, _principal: GroupReader) -> GroupDashboard:
    """Сводка по видимым объектам группы: стадия, статус, % факт и план, отставание, число проблем,
    заявки на подтверждение, бюджет (при finance:read)."""
    return await service.group_dashboard(session)


@router.get("/portal/{project_id}", response_model=PortalDashboard)
async def portal_dashboard(
    project_id: uuid.UUID, session: Db, _principal: PortalReader
) -> PortalDashboard:
    """Вид заказчика и субподрядчика: % и ряд по неделям своего объекта. Деньги, веса, фото и имена
    сотрудников в ответе не передаются."""
    return await service.portal_dashboard(session, project_id)
