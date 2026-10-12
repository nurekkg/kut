import uuid
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rules import db_rules
from app.modules.equipment.models import (
    EquipmentAssignment,
    EquipmentRequest,
    EquipmentType,
    EquipmentUnit,
)
from app.modules.equipment.schemas import (
    AssignmentCreate,
    AssignmentCreated,
    AssignmentOut,
    AssignmentPatch,
    AssignmentWarning,
    CalendarAssignment,
    CalendarDay,
    CalendarOut,
    CalendarUnit,
    RequestCreate,
    RequestOut,
    RequestPatch,
)
from app.modules.equipment.service import (
    latest_documents_statement,
    not_found,
    transition_forbidden,
)
from app.modules.objects.models import Project

MAX_CALENDAR_DAYS = 62
WARNING_KINDS = ("passport", "permit", "other")


def request_out(row: EquipmentRequest) -> RequestOut:
    return RequestOut(
        id=row.id,
        project_id=row.project_id,
        legal_entity_id=row.legal_entity_id,
        node_id=row.node_id,
        schedule_item_id=row.schedule_item_id,
        equipment_type_id=row.equipment_type_id,
        date_from=row.date_from,
        date_to=row.date_to,
        planned_hours_per_day=row.planned_hours_per_day,
        status=row.status,
        reject_reason=row.reject_reason,
    )


def assignment_out(row: EquipmentAssignment) -> AssignmentOut:
    return AssignmentOut(
        id=row.id,
        project_id=row.project_id,
        legal_entity_id=row.legal_entity_id,
        equipment_unit_id=row.equipment_unit_id,
        request_id=row.request_id,
        node_id=row.node_id,
        schedule_item_id=row.schedule_item_id,
        date_from=row.date_from,
        date_to=row.date_to,
        planned_hours_per_day=row.planned_hours_per_day,
        operator_id=row.operator_id,
        status=row.status,
        cancel_reason=row.cancel_reason,
    )


def _forbidden() -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")


async def _project_legal_entity(session: AsyncSession, project_id: uuid.UUID) -> uuid.UUID:
    """Объект, которого не видно (RLS), - 404: его существование не раскрываем."""
    legal_entity = await session.scalar(
        select(Project.legal_entity_id).where(
            Project.id == project_id, Project.deleted_at.is_(None)
        )
    )
    if legal_entity is None:
        raise not_found()
    return legal_entity


async def _request(session: AsyncSession, request_id: uuid.UUID) -> EquipmentRequest:
    row = await session.scalar(
        select(EquipmentRequest).where(
            EquipmentRequest.id == request_id, EquipmentRequest.deleted_at.is_(None)
        )
    )
    if row is None:
        raise not_found()
    return row


async def _assignment(session: AsyncSession, assignment_id: uuid.UUID) -> EquipmentAssignment:
    row = await session.scalar(
        select(EquipmentAssignment).where(
            EquipmentAssignment.id == assignment_id, EquipmentAssignment.deleted_at.is_(None)
        )
    )
    if row is None:
        raise not_found()
    return row


# --- заявки ------------------------------------------------------------------------------------


async def list_requests(
    session: AsyncSession,
    *,
    project_id: uuid.UUID | None,
    status_filter: str | None,
    limit: int,
    offset: int,
) -> list[RequestOut]:
    statement = (
        select(EquipmentRequest)
        .where(EquipmentRequest.deleted_at.is_(None))
        .order_by(EquipmentRequest.date_from.desc(), EquipmentRequest.id)
        .limit(limit)
        .offset(offset)
    )
    if project_id is not None:
        statement = statement.where(EquipmentRequest.project_id == project_id)
    if status_filter is not None:
        statement = statement.where(EquipmentRequest.status == status_filter)
    rows = await session.scalars(statement)
    return [request_out(row) for row in rows]


async def create_request(session: AsyncSession, data: RequestCreate) -> RequestOut:
    legal_entity = await _project_legal_entity(session, data.project_id)
    row = EquipmentRequest(**data.model_dump(), legal_entity_id=legal_entity, status="draft")
    async with db_rules(session):
        session.add(row)
        await session.flush()
    return request_out(row)


async def patch_request(
    session: AsyncSession, request_id: uuid.UUID, patch: RequestPatch, actor: uuid.UUID
) -> RequestOut:
    row = await _request(session, request_id)
    if row.status != "draft":
        raise transition_forbidden()
    await _author_or_approver(session, row, actor, approver=False)
    for field in patch.model_fields_set:
        setattr(row, field, getattr(patch, field))
    async with db_rules(session):
        await session.commit()
    return request_out(row)


async def _author_or_approver(
    session: AsyncSession, row: EquipmentRequest, actor: uuid.UUID, approver: bool
) -> None:
    if row.created_by != actor and not approver:
        raise _forbidden()


async def submit_request(
    session: AsyncSession, request_id: uuid.UUID, actor: uuid.UUID
) -> RequestOut:
    row = await _request(session, request_id)
    if row.status == "submitted":
        return request_out(row)  # повтор - no-op
    if row.status != "draft":
        raise transition_forbidden()
    await _author_or_approver(session, row, actor, approver=False)
    row.status = "submitted"
    async with db_rules(session):
        await session.commit()
    return request_out(row)


async def reopen_request(
    session: AsyncSession, request_id: uuid.UUID, actor: uuid.UUID
) -> RequestOut:
    row = await _request(session, request_id)
    if row.status == "draft":
        return request_out(row)
    if row.status != "rejected":
        raise transition_forbidden()
    await _author_or_approver(session, row, actor, approver=False)
    row.status = "draft"
    row.reject_reason = None
    async with db_rules(session):
        await session.commit()
    return request_out(row)


async def reject_request(session: AsyncSession, request_id: uuid.UUID, reason: str) -> RequestOut:
    row = await _request(session, request_id)
    if row.status == "rejected":
        return request_out(row)
    if row.status != "submitted":
        raise transition_forbidden()
    row.status = "rejected"
    row.reject_reason = reason
    async with db_rules(session):
        await session.commit()
    return request_out(row)


async def cancel_request(
    session: AsyncSession, request_id: uuid.UUID, actor: uuid.UUID, *, approver: bool
) -> RequestOut:
    """Черновик и поданную отменяет автор или утверждающий. Назначенную - только утверждающий;
    её плановые назначения отменяются с причиной (CHECK требует причину)."""
    row = await _request(session, request_id)
    if row.status == "cancelled":
        return request_out(row)
    if row.status not in ("draft", "submitted", "assigned"):
        raise transition_forbidden()
    if row.status == "assigned" and not approver:
        raise _forbidden()
    await _author_or_approver(session, row, actor, approver=approver)
    if row.status == "assigned":
        planned = await session.scalars(
            select(EquipmentAssignment).where(
                EquipmentAssignment.request_id == row.id,
                EquipmentAssignment.status == "planned",
                EquipmentAssignment.deleted_at.is_(None),
            )
        )
        for assignment in planned:
            assignment.status = "cancelled"
            assignment.cancel_reason = "Заявка отменена"
    row.status = "cancelled"
    async with db_rules(session):
        await session.commit()
    return request_out(row)


# --- назначения --------------------------------------------------------------------------------


async def _document_warnings(
    session: AsyncSession, unit_id: uuid.UUID, date_to: date
) -> list[AssignmentWarning]:
    """Прочие документы не блокируют назначение (D6), но их срок внутри периода - предупреждение."""
    rows = (await session.execute(latest_documents_statement(unit_id))).all()
    return [
        AssignmentWarning(code="equipment.document_expiring", kind=kind, valid_until=until)
        for kind, until, _version in rows
        if kind in WARNING_KINDS and until < date_to
    ]


async def create_assignment(session: AsyncSession, data: AssignmentCreate) -> AssignmentCreated:
    unit = await session.scalar(
        select(EquipmentUnit).where(
            EquipmentUnit.id == data.equipment_unit_id, EquipmentUnit.deleted_at.is_(None)
        )
    )
    if unit is None:
        raise not_found()
    request = None
    project_id = data.project_id
    if data.request_id is not None:
        request = await _request(session, data.request_id)
        if request.status not in ("submitted", "assigned"):
            raise HTTPException(status.HTTP_409_CONFLICT, "equipment.request_not_assignable")
        if data.project_id is not None and data.project_id != request.project_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "equipment.request_invalid")
        if unit.equipment_type_id != request.equipment_type_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "equipment.type_mismatch")
        project_id = request.project_id
    if project_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "equipment.project_required")
    legal_entity = await _project_legal_entity(session, project_id)
    row = EquipmentAssignment(
        **data.model_dump(exclude={"request_id", "project_id"}),
        project_id=project_id,
        legal_entity_id=legal_entity,
        request_id=data.request_id,
        status="planned",
    )
    async with db_rules(session):
        session.add(row)
        await session.flush()
    if request is not None and request.status == "submitted":
        request.status = "assigned"
    warnings = await _document_warnings(session, unit.id, data.date_to)
    async with db_rules(session):
        await session.commit()
    return AssignmentCreated(**assignment_out(row).model_dump(), warnings=warnings)


async def list_assignments(
    session: AsyncSession,
    *,
    unit_id: uuid.UUID | None,
    project_id: uuid.UUID | None,
    date_from: date | None,
    date_to: date | None,
    limit: int,
    offset: int,
) -> list[AssignmentOut]:
    statement = (
        select(EquipmentAssignment)
        .where(EquipmentAssignment.deleted_at.is_(None))
        .order_by(EquipmentAssignment.date_from, EquipmentAssignment.id)
        .limit(limit)
        .offset(offset)
    )
    if unit_id is not None:
        statement = statement.where(EquipmentAssignment.equipment_unit_id == unit_id)
    if project_id is not None:
        statement = statement.where(EquipmentAssignment.project_id == project_id)
    if date_from is not None:
        statement = statement.where(EquipmentAssignment.date_to >= date_from)
    if date_to is not None:
        statement = statement.where(EquipmentAssignment.date_from <= date_to)
    rows = await session.scalars(statement)
    return [assignment_out(row) for row in rows]


async def patch_assignment(
    session: AsyncSession, assignment_id: uuid.UUID, patch: AssignmentPatch
) -> AssignmentOut:
    row = await _assignment(session, assignment_id)
    if row.status != "planned":
        raise transition_forbidden()
    for field in patch.model_fields_set:
        setattr(row, field, getattr(patch, field))
    async with db_rules(session):
        await session.commit()
    return assignment_out(row)


async def _close_request_if_done(session: AsyncSession, request_id: uuid.UUID | None) -> None:
    if request_id is None:
        return
    request = await _request(session, request_id)
    if request.status != "assigned":
        return
    statuses = set(
        await session.scalars(
            select(EquipmentAssignment.status).where(
                EquipmentAssignment.request_id == request_id,
                EquipmentAssignment.deleted_at.is_(None),
            )
        )
    )
    if "planned" not in statuses and "finished" in statuses:
        request.status = "closed"


async def finish_assignment(session: AsyncSession, assignment_id: uuid.UUID) -> AssignmentOut:
    row = await _assignment(session, assignment_id)
    if row.status == "finished":
        return assignment_out(row)
    if row.status != "planned":
        raise transition_forbidden()
    row.status = "finished"
    await session.flush()
    await _close_request_if_done(session, row.request_id)
    async with db_rules(session):
        await session.commit()
    return assignment_out(row)


async def cancel_assignment(
    session: AsyncSession, assignment_id: uuid.UUID, reason: str
) -> AssignmentOut:
    row = await _assignment(session, assignment_id)
    if row.status == "cancelled":
        return assignment_out(row)
    if row.status != "planned":
        raise transition_forbidden()
    row.status = "cancelled"
    row.cancel_reason = reason
    async with db_rules(session):
        await session.commit()
    return assignment_out(row)


# --- календарь занятости ----------------------------------------------------------------------


async def calendar(
    session: AsyncSession,
    *,
    date_from: date,
    date_to: date,
    legal_entity_id: uuid.UUID | None,
    type_id: uuid.UUID | None,
    limit: int,
) -> CalendarOut:
    """Единицы × дни: назначения и суммы часов по дням. Видимость - RLS: прораб получает часы своих
    объектов, машинист - своих назначений."""
    if date_to < date_from or (date_to - date_from).days + 1 > MAX_CALENDAR_DAYS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "equipment.calendar_window_invalid"
        )
    statement = (
        select(EquipmentUnit, EquipmentType.code)
        .join(EquipmentType, EquipmentType.id == EquipmentUnit.equipment_type_id)
        .where(EquipmentUnit.deleted_at.is_(None))
        .order_by(EquipmentType.code, EquipmentUnit.registry_number, EquipmentUnit.id)
        .limit(limit)
    )
    if legal_entity_id is not None:
        statement = statement.where(EquipmentUnit.legal_entity_id == legal_entity_id)
    if type_id is not None:
        statement = statement.where(EquipmentUnit.equipment_type_id == type_id)
    units = (await session.execute(statement)).all()
    if not units:
        return CalendarOut(date_from=date_from, date_to=date_to, units=[])
    unit_ids = [unit.id for unit, _code in units]
    rows = await session.execute(
        select(EquipmentAssignment, Project.name)
        .join(Project, Project.id == EquipmentAssignment.project_id)
        .where(
            EquipmentAssignment.equipment_unit_id.in_(unit_ids),
            EquipmentAssignment.status == "planned",
            EquipmentAssignment.deleted_at.is_(None),
            EquipmentAssignment.date_from <= date_to,
            EquipmentAssignment.date_to >= date_from,
        )
        .order_by(EquipmentAssignment.date_from, EquipmentAssignment.id)
    )
    items: dict[uuid.UUID, list[CalendarAssignment]] = defaultdict(list)
    hours: dict[uuid.UUID, dict[date, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for assignment, project_name in rows.all():
        items[assignment.equipment_unit_id].append(
            CalendarAssignment(
                id=assignment.id,
                project_id=assignment.project_id,
                project_name=project_name,
                date_from=assignment.date_from,
                date_to=assignment.date_to,
                planned_hours_per_day=assignment.planned_hours_per_day,
                status=assignment.status,
            )
        )
        day = max(assignment.date_from, date_from)
        last = min(assignment.date_to, date_to)
        while day <= last:
            hours[assignment.equipment_unit_id][day] += assignment.planned_hours_per_day
            day += timedelta(days=1)
    return CalendarOut(
        date_from=date_from,
        date_to=date_to,
        units=[
            CalendarUnit(
                id=unit.id,
                registry_number=unit.registry_number,
                type_code=code,
                ownership=unit.ownership,
                assignments=items.get(unit.id, []),
                days=[
                    CalendarDay(date=day, hours=total)
                    for day, total in sorted(hours.get(unit.id, {}).items())
                ],
            )
            for unit, code in units
        ],
    )
