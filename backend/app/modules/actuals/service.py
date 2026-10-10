import uuid
from datetime import date
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Select, case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.rules import db_rules
from app.modules.actuals.models import ActualStatus, WorkActual
from app.modules.actuals.schemas import ActualCreate, ActualOut, ActualPatch
from app.modules.identity.models import User
from app.modules.objects.models import Project, ProjectNode
from app.modules.photos.models import WorkPhoto
from app.modules.schedule.models import ScheduleItem
from app.modules.works.models import Work

MAX_LIST = 5000


def not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "not_found")


# Внешний пул (kut_portal) читает только выданные колонки, а вид работ - только из ГПР своих
# объектов: поэтому вид работ присоединяется необязательно, а людей и геометок в выборке нет.
_ACTUAL_PUBLIC = (
    WorkActual.id,
    WorkActual.project_id,
    WorkActual.node_id,
    WorkActual.work_id,
    Work.code.label("work_code"),
    Work.name.label("work_name"),
    Work.unit,
    WorkActual.schedule_item_id,
    WorkActual.volume,
    WorkActual.work_date,
    WorkActual.status,
    WorkActual.approved_at,
    WorkActual.created_at,
)


def _actual_select(*, internal: bool) -> Select[Any]:
    statement = select(*_ACTUAL_PUBLIC).outerjoin(Work, Work.id == WorkActual.work_id)
    if not internal:
        return statement
    author, approver, plan = aliased(User), aliased(User), aliased(ScheduleItem)
    same_item = aliased(WorkActual)
    live_photos = (
        select(func.count(WorkPhoto.id))
        .where(WorkPhoto.work_actual_id == WorkActual.id, WorkPhoto.deleted_at.is_(None))
        .correlate(WorkActual)
        .scalar_subquery()
    )
    # Подтверждённый по строке ГПР объём: индекс по schedule_item_id, на странице до 5000 строк.
    confirmed = (
        select(func.coalesce(func.sum(same_item.volume), 0))
        .where(
            same_item.schedule_item_id == WorkActual.schedule_item_id,
            same_item.status == ActualStatus.APPROVED.value,
            same_item.deleted_at.is_(None),
        )
        .correlate(WorkActual)
        .scalar_subquery()
    )
    return (
        statement.add_columns(
            WorkActual.author_id,
            author.full_name.label("author_name"),
            WorkActual.approved_by,
            approver.full_name.label("approved_by_name"),
            WorkActual.reject_reason,
            WorkActual.latitude,
            WorkActual.longitude,
            WorkActual.accuracy_m,
            live_photos.label("photo_count"),
            plan.plan_volume.label("item_plan_volume"),
            case((WorkActual.schedule_item_id.is_(None), None), else_=confirmed).label(
                "item_confirmed_volume"
            ),
        )
        .outerjoin(author, author.id == WorkActual.author_id)
        .outerjoin(approver, approver.id == WorkActual.approved_by)
        .outerjoin(plan, plan.id == WorkActual.schedule_item_id)
    )


async def list_actuals(
    session: AsyncSession,
    project_id: uuid.UUID,
    *,
    internal: bool,
    node_id: uuid.UUID | None,
    subtree: bool,
    work_id: uuid.UUID | None,
    statuses: list[str] | None,
    author_id: uuid.UUID | None,
    date_from: date | None,
    date_to: date | None,
    limit: int,
    offset: int,
) -> list[ActualOut]:
    statement = _actual_select(internal=internal).where(WorkActual.project_id == project_id)
    if node_id is not None:
        if subtree:
            statement = statement.join(ProjectNode, ProjectNode.id == WorkActual.node_id).where(
                ProjectNode.path.contains([node_id])
            )
        else:
            statement = statement.where(WorkActual.node_id == node_id)
    if work_id is not None:
        statement = statement.where(WorkActual.work_id == work_id)
    if statuses:
        statement = statement.where(WorkActual.status.in_(statuses))
    if author_id is not None and internal:
        statement = statement.where(WorkActual.author_id == author_id)
    if date_from is not None:
        statement = statement.where(WorkActual.work_date >= date_from)
    if date_to is not None:
        statement = statement.where(WorkActual.work_date <= date_to)
    rows = await session.execute(
        statement.order_by(WorkActual.work_date.desc(), WorkActual.created_at.desc(), WorkActual.id)
        .limit(min(limit, MAX_LIST))
        .offset(offset)
    )
    return [ActualOut.model_validate(row, from_attributes=True) for row in rows]


async def get_actual(
    session: AsyncSession, project_id: uuid.UUID, actual_id: uuid.UUID, *, internal: bool
) -> ActualOut:
    row = (
        await session.execute(
            _actual_select(internal=internal).where(
                WorkActual.id == actual_id, WorkActual.project_id == project_id
            )
        )
    ).one_or_none()
    if row is None:
        raise not_found()
    return ActualOut.model_validate(row, from_attributes=True)


async def _load(session: AsyncSession, project_id: uuid.UUID, actual_id: uuid.UUID) -> WorkActual:
    fact = (
        await session.scalars(
            select(WorkActual).where(
                WorkActual.id == actual_id, WorkActual.project_id == project_id
            )
        )
    ).one_or_none()
    if fact is None:
        raise not_found()
    return fact


async def create_actual(
    session: AsyncSession, project_id: uuid.UUID, data: ActualCreate, author_id: uuid.UUID
) -> ActualOut:
    """Без COMMIT: вызывается внутри идемпотентной транзакции.

    Статус, автора, подтвердившего и время вычисляет триггер БД: прораб и техдиректор получают
    согласованный факт сразу, остальные - черновик или отправленный (submit).
    """
    legal_entity_id = await session.scalar(
        select(Project.legal_entity_id).where(Project.id == project_id)
    )
    if legal_entity_id is None:
        raise not_found()
    fact = WorkActual(
        project_id=project_id,
        legal_entity_id=legal_entity_id,
        author_id=author_id,
        status=ActualStatus.SUBMITTED.value if data.submit else ActualStatus.DRAFT.value,
        **data.model_dump(exclude={"submit"}),
    )
    async with db_rules(session):
        session.add(fact)
        await session.flush()
    return await get_actual(session, project_id, fact.id, internal=True)


async def update_actual(
    session: AsyncSession, project_id: uuid.UUID, actual_id: uuid.UUID, patch: ActualPatch
) -> ActualOut:
    fact = await _load(session, project_id, actual_id)
    for field in patch.model_fields_set:
        setattr(fact, field, getattr(patch, field))
    async with db_rules(session):
        await session.commit()
    return await get_actual(session, project_id, actual_id, internal=True)


async def _transition(
    session: AsyncSession,
    project_id: uuid.UUID,
    actual_id: uuid.UUID,
    new_status: ActualStatus,
    reason: str | None = None,
) -> ActualOut:
    fact = await _load(session, project_id, actual_id)
    fact.status = new_status.value
    if reason is not None:
        fact.reject_reason = reason
    async with db_rules(session):
        await session.commit()
    return await get_actual(session, project_id, actual_id, internal=True)


async def submit_actual(
    session: AsyncSession, project_id: uuid.UUID, actual_id: uuid.UUID
) -> ActualOut:
    return await _transition(session, project_id, actual_id, ActualStatus.SUBMITTED)


async def approve_actual(
    session: AsyncSession, project_id: uuid.UUID, actual_id: uuid.UUID
) -> ActualOut:
    return await _transition(session, project_id, actual_id, ActualStatus.APPROVED)


async def reject_actual(
    session: AsyncSession, project_id: uuid.UUID, actual_id: uuid.UUID, reason: str
) -> ActualOut:
    return await _transition(session, project_id, actual_id, ActualStatus.REJECTED, reason)


async def delete_actual(
    session: AsyncSession, project_id: uuid.UUID, actual_id: uuid.UUID, actor: uuid.UUID
) -> None:
    """Факт уходит вместе со своими фото; кто и что может удалить, решает БД."""
    async with db_rules(session):
        await session.execute(
            update(WorkPhoto)
            .where(
                WorkPhoto.work_actual_id == actual_id,
                WorkPhoto.project_id == project_id,
                WorkPhoto.deleted_at.is_(None),
            )
            .values(deleted_at=func.now(), updated_by=actor)
            .execution_options(synchronize_session=False)
        )
        deleted = await session.scalar(
            update(WorkActual)
            .where(
                WorkActual.id == actual_id,
                WorkActual.project_id == project_id,
                WorkActual.deleted_at.is_(None),
            )
            .values(deleted_at=func.now(), updated_by=actor)
            .returning(WorkActual.id)
            .execution_options(synchronize_session=False)
        )
        if deleted is None:
            raise not_found()
        await session.commit()
