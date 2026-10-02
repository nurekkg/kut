import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Select, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.rules import db_rules
from app.modules.identity.models import User, UserProjectAccess
from app.modules.objects.models import Project, ProjectNode
from app.modules.rbac.models import Role
from app.modules.schedule.models import ScheduleDependency, ScheduleItem
from app.modules.schedule.schemas import (
    DependencyCreate,
    DependencyOut,
    ScheduleItemCreate,
    ScheduleItemOut,
    ScheduleItemPatch,
)
from app.modules.works.models import Work

MAX_LIST = 10_000


def not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "not_found")


# Внешний пул (kut_portal) читает только выданные колонки: ответственного для него в выборке нет.
_ITEM_PUBLIC = (
    ScheduleItem.id,
    ScheduleItem.project_id,
    ScheduleItem.node_id,
    ScheduleItem.work_id,
    Work.code.label("work_code"),
    Work.name.label("work_name"),
    Work.unit,
    ScheduleItem.plan_start,
    ScheduleItem.plan_end,
    ScheduleItem.plan_volume,
    ScheduleItem.weight,
)


def _item_select(*, internal: bool) -> Select[Any]:
    statement = select(*_ITEM_PUBLIC).join(Work, Work.id == ScheduleItem.work_id)
    if not internal:
        return statement
    responsible = aliased(User)
    return statement.add_columns(
        ScheduleItem.responsible_user_id, responsible.full_name.label("responsible_name")
    ).outerjoin(responsible, responsible.id == ScheduleItem.responsible_user_id)


async def list_items(
    session: AsyncSession,
    project_id: uuid.UUID,
    *,
    internal: bool,
    node_id: uuid.UUID | None,
    subtree: bool,
    work_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> list[ScheduleItemOut]:
    statement = _item_select(internal=internal).where(ScheduleItem.project_id == project_id)
    if node_id is not None:
        if subtree:
            statement = statement.join(ProjectNode, ProjectNode.id == ScheduleItem.node_id).where(
                ProjectNode.path.contains([node_id])
            )
        else:
            statement = statement.where(ScheduleItem.node_id == node_id)
    if work_id is not None:
        statement = statement.where(ScheduleItem.work_id == work_id)
    rows = await session.execute(
        statement.order_by(ScheduleItem.plan_start, Work.code, ScheduleItem.id)
        .limit(min(limit, MAX_LIST))
        .offset(offset)
    )
    return [ScheduleItemOut.model_validate(row, from_attributes=True) for row in rows]


async def get_item(
    session: AsyncSession, project_id: uuid.UUID, item_id: uuid.UUID, *, internal: bool
) -> ScheduleItemOut:
    row = (
        await session.execute(
            _item_select(internal=internal).where(
                ScheduleItem.id == item_id, ScheduleItem.project_id == project_id
            )
        )
    ).one_or_none()
    if row is None:
        raise not_found()
    return ScheduleItemOut.model_validate(row, from_attributes=True)


async def _check_responsible(
    session: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID | None
) -> None:
    """Ответственный - активный внутренний пользователь с доступом к этому объекту."""
    if user_id is None:
        return
    has_access = exists().where(
        UserProjectAccess.user_id == User.id,
        UserProjectAccess.project_id == project_id,
        UserProjectAccess.deleted_at.is_(None),
    )
    found = await session.scalar(
        select(User.id)
        .join(Role, Role.id == User.role_id)
        .where(
            User.id == user_id,
            User.is_active.is_(True),
            Role.scope != "counterparty",
            or_(Role.scope == "group", has_access),
        )
    )
    if found is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "responsible_invalid")


async def create_item(
    session: AsyncSession, project_id: uuid.UUID, data: ScheduleItemCreate
) -> ScheduleItemOut:
    """Без COMMIT: вызывается внутри идемпотентной транзакции."""
    legal_entity_id = await session.scalar(
        select(Project.legal_entity_id).where(Project.id == project_id)
    )
    if legal_entity_id is None:
        raise not_found()
    await _check_responsible(session, project_id, data.responsible_user_id)
    item = ScheduleItem(project_id=project_id, legal_entity_id=legal_entity_id, **data.model_dump())
    async with db_rules(session):
        session.add(item)
        await session.flush()
    return await get_item(session, project_id, item.id, internal=True)


async def update_item(
    session: AsyncSession, project_id: uuid.UUID, item_id: uuid.UUID, patch: ScheduleItemPatch
) -> ScheduleItemOut:
    item = (
        await session.scalars(
            select(ScheduleItem).where(
                ScheduleItem.id == item_id, ScheduleItem.project_id == project_id
            )
        )
    ).one_or_none()
    if item is None:
        raise not_found()
    if "responsible_user_id" in patch.model_fields_set:
        await _check_responsible(session, project_id, patch.responsible_user_id)
    for field in patch.model_fields_set:
        setattr(item, field, getattr(patch, field))
    async with db_rules(session):
        await session.commit()
    return await get_item(session, project_id, item_id, internal=True)


async def delete_item(
    session: AsyncSession, project_id: uuid.UUID, item_id: uuid.UUID, actor: uuid.UUID
) -> None:
    """Строка уходит вместе со своими зависимостями; строку с фактом БД не отпускает."""
    async with db_rules(session):
        await session.execute(
            update(ScheduleDependency)
            .where(
                ScheduleDependency.project_id == project_id,
                or_(
                    ScheduleDependency.predecessor_id == item_id,
                    ScheduleDependency.successor_id == item_id,
                ),
                ScheduleDependency.deleted_at.is_(None),
            )
            .values(deleted_at=func.now(), updated_by=actor)
            .execution_options(synchronize_session=False)
        )
        deleted = await session.scalar(
            update(ScheduleItem)
            .where(
                ScheduleItem.id == item_id,
                ScheduleItem.project_id == project_id,
                ScheduleItem.deleted_at.is_(None),
            )
            .values(deleted_at=func.now(), updated_by=actor)
            .returning(ScheduleItem.id)
            .execution_options(synchronize_session=False)
        )
        if deleted is None:
            raise not_found()
        await session.commit()


# --- зависимости ------------------------------------------------------------------------------


async def list_dependencies(session: AsyncSession, project_id: uuid.UUID) -> list[DependencyOut]:
    rows = await session.execute(
        select(
            ScheduleDependency.id,
            ScheduleDependency.project_id,
            ScheduleDependency.predecessor_id,
            ScheduleDependency.successor_id,
        )
        .where(ScheduleDependency.project_id == project_id)
        .order_by(ScheduleDependency.created_at, ScheduleDependency.id)
    )
    return [DependencyOut.model_validate(row, from_attributes=True) for row in rows]


async def create_dependency(
    session: AsyncSession, project_id: uuid.UUID, data: DependencyCreate
) -> DependencyOut:
    """Без COMMIT: вызывается внутри идемпотентной транзакции. Цикл и чужой объект отсекает БД."""
    legal_entity_id = await session.scalar(
        select(Project.legal_entity_id).where(Project.id == project_id)
    )
    if legal_entity_id is None:
        raise not_found()
    dependency = ScheduleDependency(
        project_id=project_id, legal_entity_id=legal_entity_id, **data.model_dump()
    )
    async with db_rules(session):
        session.add(dependency)
        await session.flush()
    return DependencyOut(
        id=dependency.id,
        project_id=project_id,
        predecessor_id=data.predecessor_id,
        successor_id=data.successor_id,
    )


async def delete_dependency(
    session: AsyncSession, project_id: uuid.UUID, dependency_id: uuid.UUID, actor: uuid.UUID
) -> None:
    async with db_rules(session):
        deleted = await session.scalar(
            update(ScheduleDependency)
            .where(
                ScheduleDependency.id == dependency_id,
                ScheduleDependency.project_id == project_id,
                ScheduleDependency.deleted_at.is_(None),
            )
            .values(deleted_at=func.now(), updated_by=actor)
            .returning(ScheduleDependency.id)
            .execution_options(synchronize_session=False)
        )
        if deleted is None:
            raise not_found()
        await session.commit()
