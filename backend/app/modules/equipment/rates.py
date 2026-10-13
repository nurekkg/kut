import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rules import db_rules
from app.modules.equipment.models import (
    Contract,
    EquipmentOperator,
    EquipmentOwnRate,
    EquipmentRate,
)
from app.modules.equipment.schemas import (
    ContractCreate,
    ContractOut,
    ContractPatch,
    CounterpartyRef,
    OperatorCreate,
    OperatorOut,
    OwnRateCreate,
    OwnRateOut,
    RateCreate,
    RateOut,
    RatePatch,
)
from app.modules.equipment.service import not_found, transition_forbidden
from app.modules.org.models import Counterparty

CONTRACT_NEXT = {
    "draft": frozenset({"active", "terminated"}),
    "active": frozenset({"closed", "terminated"}),
}


def own_rate_out(row: EquipmentOwnRate) -> OwnRateOut:
    return OwnRateOut(
        id=row.id,
        legal_entity_id=row.legal_entity_id,
        equipment_type_id=row.equipment_type_id,
        unit=row.unit,
        amount=row.amount,
        valid_from=row.valid_from,
        proposed_by=row.proposed_by,
        approved_by=row.approved_by,
    )


def contract_out(row: Contract) -> ContractOut:
    return ContractOut(
        id=row.id,
        legal_entity_id=row.legal_entity_id,
        counterparty_id=row.counterparty_id,
        kind=row.kind,
        number=row.number,
        date_from=row.date_from,
        date_to=row.date_to,
        status=row.status,
    )


def rate_out(row: EquipmentRate) -> RateOut:
    return RateOut(
        id=row.id,
        contract_id=row.contract_id,
        equipment_unit_id=row.equipment_unit_id,
        equipment_type_id=row.equipment_type_id,
        unit=row.unit,
        with_operator=row.with_operator,
        amount=row.amount,
        min_quantity=row.min_quantity,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
    )


def operator_out(row: EquipmentOperator) -> OperatorOut:
    return OperatorOut(
        id=row.id,
        legal_entity_id=row.legal_entity_id,
        full_name=row.full_name,
        employment=row.employment,
        user_id=row.user_id,
        counterparty_id=row.counterparty_id,
    )


# --- нормативы своей техники (D7) --------------------------------------------------------------


async def list_own_rates(
    session: AsyncSession, *, legal_entity_id: uuid.UUID | None, type_id: uuid.UUID | None
) -> list[OwnRateOut]:
    statement = select(EquipmentOwnRate).where(EquipmentOwnRate.deleted_at.is_(None))
    if legal_entity_id is not None:
        statement = statement.where(EquipmentOwnRate.legal_entity_id == legal_entity_id)
    if type_id is not None:
        statement = statement.where(EquipmentOwnRate.equipment_type_id == type_id)
    rows = await session.scalars(
        statement.order_by(EquipmentOwnRate.valid_from.desc(), EquipmentOwnRate.id)
    )
    return [own_rate_out(row) for row in rows]


async def propose_own_rate(
    session: AsyncSession, data: OwnRateCreate, proposer: uuid.UUID
) -> OwnRateOut:
    row = EquipmentOwnRate(**data.model_dump(), proposed_by=proposer)
    async with db_rules(session):
        session.add(row)
        await session.flush()
    return own_rate_out(row)


async def approve_own_rate(
    session: AsyncSession, rate_id: uuid.UUID, approver: uuid.UUID
) -> OwnRateOut:
    """Утверждает финдиректор (approve:rate). Предложивший не утверждает свой норматив (CHECK)."""
    row = await session.scalar(
        select(EquipmentOwnRate).where(
            EquipmentOwnRate.id == rate_id, EquipmentOwnRate.deleted_at.is_(None)
        )
    )
    if row is None:
        raise not_found()
    if row.proposed_by == approver:
        raise HTTPException(status.HTTP_409_CONFLICT, "equipment.approver_is_proposer")
    if row.approved_by is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "equipment.rate_already_approved")
    row.approved_by = approver
    async with db_rules(session):
        await session.commit()
    return own_rate_out(row)


# --- договоры аренды: шапка (K = A) -----------------------------------------------------------


async def list_contracts(
    session: AsyncSession, *, legal_entity_id: uuid.UUID | None, status_filter: str | None
) -> list[ContractOut]:
    statement = select(Contract).where(Contract.deleted_at.is_(None))
    if legal_entity_id is not None:
        statement = statement.where(Contract.legal_entity_id == legal_entity_id)
    if status_filter is not None:
        statement = statement.where(Contract.status == status_filter)
    rows = await session.scalars(statement.order_by(Contract.number, Contract.id))
    return [contract_out(row) for row in rows]


async def _contract(session: AsyncSession, contract_id: uuid.UUID) -> Contract:
    row = await session.scalar(
        select(Contract).where(Contract.id == contract_id, Contract.deleted_at.is_(None))
    )
    if row is None:
        raise not_found()
    return row


async def create_contract(session: AsyncSession, data: ContractCreate) -> ContractOut:
    row = Contract(**data.model_dump(), status="draft")
    async with db_rules(session):
        session.add(row)
        await session.flush()
    return contract_out(row)


async def patch_contract(
    session: AsyncSession, contract_id: uuid.UUID, patch: ContractPatch
) -> ContractOut:
    row = await _contract(session, contract_id)
    if patch.status is not None and patch.status != row.status:
        if patch.status not in CONTRACT_NEXT.get(row.status, frozenset()):
            raise transition_forbidden()
        row.status = patch.status
    if "date_to" in patch.model_fields_set:
        row.date_to = patch.date_to
    async with db_rules(session):
        await session.commit()
    return contract_out(row)


# --- тарифы договора (деньги) ------------------------------------------------------------------


async def list_rates(session: AsyncSession, contract_id: uuid.UUID) -> list[RateOut]:
    await _contract(session, contract_id)
    rows = await session.scalars(
        select(EquipmentRate)
        .where(EquipmentRate.contract_id == contract_id, EquipmentRate.deleted_at.is_(None))
        .order_by(EquipmentRate.unit, EquipmentRate.with_operator, EquipmentRate.valid_from.desc())
    )
    return [rate_out(row) for row in rows]


async def add_rate(session: AsyncSession, contract_id: uuid.UUID, data: RateCreate) -> RateOut:
    await _contract(session, contract_id)
    row = EquipmentRate(contract_id=contract_id, **data.model_dump())
    async with db_rules(session):
        session.add(row)
        await session.flush()
    return rate_out(row)


async def _rate(session: AsyncSession, contract_id: uuid.UUID, rate_id: uuid.UUID) -> EquipmentRate:
    row = await session.scalar(
        select(EquipmentRate).where(
            EquipmentRate.id == rate_id,
            EquipmentRate.contract_id == contract_id,
            EquipmentRate.deleted_at.is_(None),
        )
    )
    if row is None:
        raise not_found()
    return row


async def patch_rate(
    session: AsyncSession, contract_id: uuid.UUID, rate_id: uuid.UUID, data: RatePatch
) -> RateOut:
    """Окончание тарифа: выбор по дате работы его больше не берёт. Правка - при любых проводках."""
    row = await _rate(session, contract_id, rate_id)
    row.valid_to = data.valid_to
    async with db_rules(session):
        await session.flush()
        await session.commit()
    await session.refresh(row)
    return rate_out(row)


async def delete_rate(session: AsyncSession, contract_id: uuid.UUID, rate_id: uuid.UUID) -> None:
    """Мягкое удаление тарифа. С проводками (rate.has_postings, 409) - отказ БД."""
    row = await _rate(session, contract_id, rate_id)
    row.deleted_at = datetime.now(UTC)
    async with db_rules(session):
        await session.flush()
        await session.commit()


# --- машинисты -----------------------------------------------------------------------------------


async def list_operators(
    session: AsyncSession, *, legal_entity_id: uuid.UUID | None
) -> list[OperatorOut]:
    statement = select(EquipmentOperator).where(EquipmentOperator.deleted_at.is_(None))
    if legal_entity_id is not None:
        statement = statement.where(EquipmentOperator.legal_entity_id == legal_entity_id)
    rows = await session.scalars(statement.order_by(EquipmentOperator.full_name))
    return [operator_out(row) for row in rows]


async def create_operator(session: AsyncSession, data: OperatorCreate) -> OperatorOut:
    row = EquipmentOperator(**data.model_dump())
    async with db_rules(session):
        session.add(row)
        await session.flush()
    return operator_out(row)


async def list_counterparties(session: AsyncSession) -> list[CounterpartyRef]:
    """Контрагенты для формы договора: имя и идентификатор, без реквизитов."""
    rows = await session.execute(
        select(Counterparty.id, Counterparty.name)
        .where(Counterparty.deleted_at.is_(None))
        .order_by(Counterparty.name)
    )
    return [CounterpartyRef(id=row.id, name=row.name) for row in rows.all()]
