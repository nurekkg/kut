import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.deps import get_db, require
from app.modules.identity.models import User
from app.modules.objects.models import Project
from app.modules.rbac.enums import Action, Module
from app.modules.rbac.models import Role

router = APIRouter(tags=["projects"])

Db = Annotated[AsyncSession, Depends(get_db)]

# Колонки перечислены явно: внешний пул (kut_portal) читает только выданные ему столбцы,
# а created_by / updated_by и другие служебные поля наружу не отдаются.
_PROJECT_COLUMNS = (Project.id, Project.name, Project.status, Project.legal_entity_id)


class ProjectOut(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    legal_entity_id: uuid.UUID


class UserOut(BaseModel):
    id: uuid.UUID
    full_name: str
    role: str


@router.get(
    "/projects",
    response_model=list[ProjectOut],
    dependencies=[Depends(require(Module.OBJECTS, Action.READ))],
)
async def list_projects(session: Db) -> list[ProjectOut]:
    """Объекты, видимые пользователю: фильтр делает RLS, а не код запроса."""
    rows = await session.execute(select(*_PROJECT_COLUMNS).order_by(Project.name, Project.id))
    return [ProjectOut.model_validate(row, from_attributes=True) for row in rows]


@router.get(
    "/projects/{project_id}",
    response_model=ProjectOut,
    dependencies=[Depends(require(Module.OBJECTS, Action.READ, project_param="project_id"))],
)
async def get_project(project_id: uuid.UUID, session: Db) -> ProjectOut:
    row = (
        await session.execute(select(*_PROJECT_COLUMNS).where(Project.id == project_id))
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return ProjectOut.model_validate(row, from_attributes=True)


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
