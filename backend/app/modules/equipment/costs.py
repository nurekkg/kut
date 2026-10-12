"""ГСМ, делегаты подтверждения и журнал затрат (SE-2a, ADR-014 часть 2 §3-5, решение Q11 B).

Проводки создают и сторнируют определители БД (app.equipment_fuel_post, app.equipment_fuel_reverse,
app.equipment_shift_post); сервис читает результат. Итоги для прораба отдаёт
# функция app.equipment_cost_totals:
строк журнала и тарифов в них нет. Строки журнала отдаёт только RLS с гейтом read:rates.
"""

import uuid
from datetime import UTC, date, datetime

from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rules import db_rules
from app.modules.equipment.models import CostEntry, EquipmentDelegate, EquipmentFuel, EquipmentUnit
from app.modules.equipment.shift_schemas import (
    CostEntryOut,
    CostTotalOut,
    DelegateCreate,
    DelegateOut,
    FuelCreate,
    FuelOut,
)


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "not_found")


def fuel_out(row: EquipmentFuel, *, can_rates: bool) -> FuelOut:
    """Сумма заправки - деньги: без права read:rates поле пустое (как в журнале затрат)."""
    return FuelOut(
        id=row.id,
        equipment_unit_id=row.equipment_unit_id,
        project_id=row.project_id,
        fuel_date=row.fuel_date,
        liters=row.liters,
        amount=row.amount if can_rates else None,
        reversed_at=row.reversed_at,
        reverse_reason=row.reverse_reason,
    )


def delegate_out(row: EquipmentDelegate) -> DelegateOut:
    return DelegateOut(
        id=row.id,
        project_id=row.project_id,
        delegate_user_id=row.delegate_user_id,
        date_from=row.date_from,
        date_to=row.date_to,
        reason=row.reason,
        revoked_at=row.revoked_at,
    )


async def _fuel(session: AsyncSession, fuel_id: uuid.UUID) -> EquipmentFuel:
    row = await session.scalar(
        select(EquipmentFuel).where(EquipmentFuel.id == fuel_id, EquipmentFuel.deleted_at.is_(None))
    )
    if row is None:
        raise _not_found()
    return row


async def create_fuel(session: AsyncSession, body: FuelCreate, *, can_rates: bool) -> FuelOut:
    unit = await session.scalar(
        select(EquipmentUnit).where(
            EquipmentUnit.id == body.equipment_unit_id, EquipmentUnit.deleted_at.is_(None)
        )
    )
    if unit is None:
        raise _not_found()
    async with db_rules(session):
        row = EquipmentFuel(
            legal_entity_id=unit.legal_entity_id,
            project_id=body.project_id,
            equipment_unit_id=body.equipment_unit_id,
            fuel_date=body.fuel_date,
            liters=body.liters,
            amount=body.amount,
        )
        session.add(row)
        await session.flush()
        await session.commit()
    await session.refresh(row)
    return fuel_out(row, can_rates=can_rates)


async def list_fuel(
    session: AsyncSession,
    *,
    project_id: uuid.UUID | None,
    unit_id: uuid.UUID | None,
    date_from: date | None,
    date_to: date | None,
    can_rates: bool,
    limit: int,
    offset: int,
) -> list[FuelOut]:
    query = select(EquipmentFuel).where(EquipmentFuel.deleted_at.is_(None))
    if project_id is not None:
        query = query.where(EquipmentFuel.project_id == project_id)
    if unit_id is not None:
        query = query.where(EquipmentFuel.equipment_unit_id == unit_id)
    if date_from is not None:
        query = query.where(EquipmentFuel.fuel_date >= date_from)
    if date_to is not None:
        query = query.where(EquipmentFuel.fuel_date <= date_to)
    rows = await session.scalars(
        query.order_by(EquipmentFuel.fuel_date.desc(), EquipmentFuel.id).limit(limit).offset(offset)
    )
    return [fuel_out(row, can_rates=can_rates) for row in rows]


async def reverse_fuel(
    session: AsyncSession, fuel_id: uuid.UUID, reason: str, *, can_rates: bool
) -> FuelOut:
    """Сторно заправки: строка остаётся, в журнале появляется сторно. Повтор - без новых строк."""
    await _fuel(session, fuel_id)
    async with db_rules(session):
        await session.execute(
            text("SELECT app.equipment_fuel_reverse(:f, :r)"), {"f": fuel_id, "r": reason}
        )
        await session.commit()
    row = await _fuel(session, fuel_id)
    return fuel_out(row, can_rates=can_rates)


async def create_delegate(session: AsyncSession, body: DelegateCreate) -> DelegateOut:
    async with db_rules(session):
        row = EquipmentDelegate(
            project_id=body.project_id,
            delegate_user_id=body.delegate_user_id,
            date_from=body.date_from,
            date_to=body.date_to,
            reason=body.reason,
        )
        session.add(row)
        await session.flush()
        await session.commit()
    await session.refresh(row)
    return delegate_out(row)


async def list_delegates(
    session: AsyncSession, *, project_id: uuid.UUID | None, limit: int, offset: int
) -> list[DelegateOut]:
    query = select(EquipmentDelegate).where(EquipmentDelegate.deleted_at.is_(None))
    if project_id is not None:
        query = query.where(EquipmentDelegate.project_id == project_id)
    rows = await session.scalars(
        query.order_by(EquipmentDelegate.date_from.desc(), EquipmentDelegate.id)
        .limit(limit)
        .offset(offset)
    )
    return [delegate_out(row) for row in rows]


async def revoke_delegate(
    session: AsyncSession, delegate_id: uuid.UUID, *, user_id: uuid.UUID
) -> DelegateOut:
    row = await session.scalar(
        select(EquipmentDelegate).where(
            EquipmentDelegate.id == delegate_id, EquipmentDelegate.deleted_at.is_(None)
        )
    )
    if row is None:
        raise _not_found()
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        row.revoked_by = user_id
    async with db_rules(session):
        await session.flush()
        await session.commit()
    await session.refresh(row)
    return delegate_out(row)


async def cost_totals(
    session: AsyncSession,
    *,
    date_from: date,
    date_to: date,
    project_id: uuid.UUID | None,
) -> list[CostTotalOut]:
    """Итоги по объекту и категории: прорабу - только итоги (Q11 B); строк и тарифов здесь нет."""
    rows = await session.execute(
        text(
            "SELECT project_id, category, amount, entries"
            " FROM app.equipment_cost_totals(:f, :t, :p)"
        ),
        {"f": date_from, "t": date_to, "p": project_id},
    )
    return [
        CostTotalOut(
            project_id=row.project_id,
            category=row.category,
            amount=row.amount,
            entries=row.entries,
        )
        for row in rows
    ]


async def cost_entries(
    session: AsyncSession,
    *,
    date_from: date,
    date_to: date,
    project_id: uuid.UUID | None,
    category: str | None,
    limit: int,
    offset: int,
) -> list[CostEntryOut]:
    """Строки журнала затрат: только для права read:rates (гейт в маршруте и в RLS)."""
    query = select(CostEntry).where(CostEntry.period >= date_from, CostEntry.period <= date_to)
    if project_id is not None:
        query = query.where(CostEntry.project_id == project_id)
    if category is not None:
        query = query.where(CostEntry.category == category)
    rows = await session.scalars(
        query.order_by(CostEntry.period, CostEntry.created_at, CostEntry.id)
        .limit(limit)
        .offset(offset)
    )
    return [
        CostEntryOut(
            id=row.id,
            legal_entity_id=row.legal_entity_id,
            project_id=row.project_id,
            node_id=row.node_id,
            schedule_item_id=row.schedule_item_id,
            equipment_unit_id=row.equipment_unit_id,
            period=row.period,
            category=row.category,
            source_type=row.source_type,
            source_id=row.source_id,
            posting_version=row.posting_version,
            line_no=row.line_no,
            quantity=row.quantity,
            tariff_unit=row.tariff_unit,
            rate=row.rate,
            amount=row.amount,
            reversal_of_id=row.reversal_of_id,
        )
        for row in rows
    ]
