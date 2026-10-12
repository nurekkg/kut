"""Путевые листы (SE-2a, ADR-014 часть 1 §3): ввод, разнесение по строкам и переходы.

Права по роли, автора, переходы, суточный лимит часов и проводку затрат проверяет БД (триггеры
app.equipment_shift_guard, app.equipment_shift_line_guard, app.equipment_shift_post).
Сервис не решает,
кому что можно: он переводит запрос в изменения строк, повтор текущего состояния отвечает без записи
(no-op, как у факта ADR-010), а ошибки БД превращаются в коды ответа в app/core/rules.py.
"""

import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.core.rules import db_rules
from app.modules.equipment.models import EquipmentShift, EquipmentShiftLine, EquipmentUnit
from app.modules.equipment.shift_schemas import (
    LineIn,
    LineOut,
    ShiftBatchCreate,
    ShiftBatchOut,
    ShiftCreate,
    ShiftOut,
    ShiftPatch,
)


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "not_found")


@asynccontextmanager
async def _writable(session: AsyncSession) -> AsyncIterator[None]:
    """Строка видна, но политика UPDATE её не пускает (роль, не автор, делегат вне периода): ORM
    получает 0 строк. Это отказ права, а не сбой, поэтому 403, а не 500."""
    try:
        async with db_rules(session):
            yield
    except StaleDataError as error:
        await session.rollback()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden") from error


async def _unit(session: AsyncSession, unit_id: uuid.UUID) -> EquipmentUnit:
    row = await session.scalar(
        select(EquipmentUnit).where(EquipmentUnit.id == unit_id, EquipmentUnit.deleted_at.is_(None))
    )
    if row is None:
        raise _not_found()
    return row


async def _shift(session: AsyncSession, shift_id: uuid.UUID) -> EquipmentShift:
    row = await session.scalar(
        select(EquipmentShift).where(
            EquipmentShift.id == shift_id, EquipmentShift.deleted_at.is_(None)
        )
    )
    if row is None:
        raise _not_found()
    return row


async def _lines(
    session: AsyncSession, shift_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[EquipmentShiftLine]]:
    grouped: dict[uuid.UUID, list[EquipmentShiftLine]] = {shift_id: [] for shift_id in shift_ids}
    if not shift_ids:
        return grouped
    rows = await session.scalars(
        select(EquipmentShiftLine)
        .where(EquipmentShiftLine.shift_id.in_(shift_ids), EquipmentShiftLine.deleted_at.is_(None))
        .order_by(EquipmentShiftLine.created_at, EquipmentShiftLine.id)
    )
    for line in rows:
        grouped[line.shift_id].append(line)
    return grouped


def _line_rows(
    shift_id: uuid.UUID, project_id: uuid.UUID, lines: Sequence[LineIn]
) -> list[EquipmentShiftLine]:
    return [
        EquipmentShiftLine(
            shift_id=shift_id,
            project_id=project_id,
            node_id=line.node_id,
            schedule_item_id=line.schedule_item_id,
            hours=line.hours,
            trips=line.trips,
            tonnes=line.tonnes,
        )
        for line in lines
    ]


def shift_out(row: EquipmentShift, lines: Sequence[EquipmentShiftLine]) -> ShiftOut:
    return ShiftOut(
        id=row.id,
        project_id=row.project_id,
        equipment_unit_id=row.equipment_unit_id,
        assignment_id=row.assignment_id,
        work_date=row.work_date,
        operator_id=row.operator_id,
        hours=row.hours,
        odometer_from=row.odometer_from,
        odometer_to=row.odometer_to,
        trips=row.trips,
        tonnes=row.tonnes,
        downtime_hours=row.downtime_hours,
        downtime_reason=row.downtime_reason,
        source=row.source,
        status=row.status,
        reject_reason=row.reject_reason,
        confirmed_by=row.confirmed_by,
        confirmed_at=row.confirmed_at,
        created_by=row.created_by,
        posting_version=row.posting_version,
        lines=[
            LineOut(
                id=line.id,
                node_id=line.node_id,
                schedule_item_id=line.schedule_item_id,
                hours=line.hours,
                trips=line.trips,
                tonnes=line.tonnes,
            )
            for line in lines
        ],
        unplanned=row.assignment_id is None,
    )


async def _add_shift(session: AsyncSession, body: ShiftCreate) -> EquipmentShift:
    unit = await _unit(session, body.equipment_unit_id)
    row = EquipmentShift(
        legal_entity_id=unit.legal_entity_id,
        project_id=body.project_id,
        equipment_unit_id=body.equipment_unit_id,
        assignment_id=body.assignment_id,
        work_date=body.work_date,
        operator_id=body.operator_id,
        hours=body.hours,
        odometer_from=body.odometer_from,
        odometer_to=body.odometer_to,
        trips=body.trips,
        tonnes=body.tonnes,
        downtime_hours=body.downtime_hours,
        downtime_reason=body.downtime_reason,
        status="draft",
    )
    session.add(row)
    await session.flush()
    session.add_all(_line_rows(row.id, row.project_id, body.lines))
    await session.flush()
    return row


async def create_shift(session: AsyncSession, body: ShiftCreate) -> ShiftOut:
    async with db_rules(session):
        row = await _add_shift(session, body)
        await session.commit()
    await session.refresh(row)
    lines = await _lines(session, [row.id])
    return shift_out(row, lines[row.id])


async def create_batch(session: AsyncSession, body: ShiftBatchCreate) -> ShiftBatchOut:
    """Листы на несколько объектов одной транзакцией: при нарушении правила не создан ни один."""
    async with db_rules(session):
        rows = [await _add_shift(session, item) for item in body.items]
        await session.commit()
    for row in rows:
        await session.refresh(row)
    lines = await _lines(session, [row.id for row in rows])
    return ShiftBatchOut(items=[shift_out(row, lines[row.id]) for row in rows])


async def get_shift(session: AsyncSession, shift_id: uuid.UUID) -> ShiftOut:
    row = await _shift(session, shift_id)
    lines = await _lines(session, [row.id])
    return shift_out(row, lines[row.id])


async def list_shifts(
    session: AsyncSession,
    *,
    project_id: uuid.UUID | None,
    unit_id: uuid.UUID | None,
    status_filter: str | None,
    date_from: date | None,
    date_to: date | None,
    limit: int,
    offset: int,
) -> list[ShiftOut]:
    query = select(EquipmentShift).where(EquipmentShift.deleted_at.is_(None))
    if project_id is not None:
        query = query.where(EquipmentShift.project_id == project_id)
    if unit_id is not None:
        query = query.where(EquipmentShift.equipment_unit_id == unit_id)
    if status_filter is not None:
        query = query.where(EquipmentShift.status == status_filter)
    if date_from is not None:
        query = query.where(EquipmentShift.work_date >= date_from)
    if date_to is not None:
        query = query.where(EquipmentShift.work_date <= date_to)
    rows = list(
        await session.scalars(
            query.order_by(EquipmentShift.work_date.desc(), EquipmentShift.id)
            .limit(limit)
            .offset(offset)
        )
    )
    lines = await _lines(session, [row.id for row in rows])
    return [shift_out(row, lines[row.id]) for row in rows]


async def update_shift(session: AsyncSession, shift_id: uuid.UUID, patch: ShiftPatch) -> ShiftOut:
    """Правка черновика (автор) или подтверждённого листа (техдиректор, со сторно проводки в БД).

    Строки разнесения, если переданы, заменяют прежние: старые получают deleted_at,
    новые вставляются;
    каждое изменение строки в БД пересчитывает проводку подтверждённого листа.
    """
    row = await _shift(session, shift_id)
    for field, value in patch.model_dump(exclude_unset=True, exclude={"lines"}).items():
        setattr(row, field, value)
    async with _writable(session):
        await session.flush()
        if patch.lines is not None:
            await session.execute(
                update(EquipmentShiftLine)
                .where(
                    EquipmentShiftLine.shift_id == shift_id,
                    EquipmentShiftLine.deleted_at.is_(None),
                )
                .values(deleted_at=func.now())
                .execution_options(synchronize_session=False)
            )
            session.add_all(_line_rows(shift_id, row.project_id, patch.lines))
        await session.commit()
    await session.refresh(row)
    lines = await _lines(session, [row.id])
    return shift_out(row, lines[row.id])


async def transition(
    session: AsyncSession, shift_id: uuid.UUID, target: str, reason: str | None = None
) -> ShiftOut:
    """Переход листа: submit, confirm, reject, reopen. Повтор текущего состояния - без записи."""
    row = await _shift(session, shift_id)
    if row.status == target:
        lines = await _lines(session, [row.id])
        return shift_out(row, lines[row.id])
    async with _writable(session):
        row.status = target
        if target == "rejected":
            row.reject_reason = reason
        await session.flush()
        await session.commit()
    await session.refresh(row)
    lines = await _lines(session, [row.id])
    return shift_out(row, lines[row.id])
