import re
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import Select, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rules import db_rules
from app.modules.equipment.models import (
    EquipmentAssignment,
    EquipmentDocument,
    EquipmentMaintenance,
    EquipmentType,
    EquipmentUnit,
)
from app.modules.equipment.schemas import (
    DocumentCreate,
    DocumentOut,
    MaintenanceCreate,
    MaintenanceOut,
    MaintenancePatch,
    TypeCreate,
    TypeOut,
    UnitCard,
    UnitCreate,
    UnitOut,
    UnitPatch,
)

BISHKEK = ZoneInfo("Asia/Bishkek")
SOON_DAYS = 30
BLOCKING_KINDS = ("technical_inspection", "insurance")
DOCUMENT_KINDS = ("passport", "technical_inspection", "insurance", "permit", "other")
MAINTENANCE_NEXT = {
    "planned": frozenset({"in_progress", "done", "cancelled"}),
    "in_progress": frozenset({"done", "cancelled"}),
}
OPEN_REPAIR = ("planned", "in_progress")


def bishkek_today() -> date:
    return datetime.now(BISHKEK).date()


def normalize_key(registry_number: str) -> str:
    """Ключ единицы: номер без пробелов, дефисов и знаков, заглавными (ADR-014 часть 1)."""
    return re.sub(r"[\s\-_./]+", "", registry_number).upper()


def like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "not_found")


def transition_forbidden() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, "equipment.transition_forbidden")


def type_out(row: EquipmentType) -> TypeOut:
    return TypeOut(id=row.id, code=row.code, name=row.name, default_unit=row.default_unit)


def document_out(row: EquipmentDocument) -> DocumentOut:
    return DocumentOut(
        id=row.id,
        equipment_unit_id=row.equipment_unit_id,
        kind=row.kind,
        number=row.number,
        valid_until=row.valid_until,
        version=row.version,
    )


def maintenance_out(row: EquipmentMaintenance) -> MaintenanceOut:
    return MaintenanceOut(
        id=row.id,
        equipment_unit_id=row.equipment_unit_id,
        kind=row.kind,
        title=row.title,
        planned_from=row.planned_from,
        planned_to=row.planned_to,
        status=row.status,
        project_id=row.project_id,
        done_at=row.done_at,
    )


def _units_with_type() -> Select[tuple[EquipmentUnit, str]]:
    return select(EquipmentUnit, EquipmentType.code).join(
        EquipmentType, EquipmentType.id == EquipmentUnit.equipment_type_id
    )


async def _type_code(session: AsyncSession, type_id: uuid.UUID) -> str:
    code = await session.scalar(select(EquipmentType.code).where(EquipmentType.id == type_id))
    if code is None:
        raise not_found()
    return code


async def unit_out(session: AsyncSession, row: EquipmentUnit) -> UnitOut:
    return UnitOut(
        id=row.id,
        legal_entity_id=row.legal_entity_id,
        equipment_type_id=row.equipment_type_id,
        type_code=await _type_code(session, row.equipment_type_id),
        ownership=row.ownership,
        registry_number=row.registry_number,
        contract_id=row.contract_id,
        model=row.model,
        year=row.year,
    )


async def _unit(session: AsyncSession, unit_id: uuid.UUID) -> EquipmentUnit:
    row = await session.scalar(
        select(EquipmentUnit).where(EquipmentUnit.id == unit_id, EquipmentUnit.deleted_at.is_(None))
    )
    if row is None:
        raise not_found()
    return row


async def _maintenance(session: AsyncSession, maintenance_id: uuid.UUID) -> EquipmentMaintenance:
    row = await session.scalar(
        select(EquipmentMaintenance).where(
            EquipmentMaintenance.id == maintenance_id, EquipmentMaintenance.deleted_at.is_(None)
        )
    )
    if row is None:
        raise not_found()
    return row


# --- виды техники -----------------------------------------------------------------------------


async def list_types(session: AsyncSession) -> list[TypeOut]:
    rows = await session.scalars(
        select(EquipmentType).where(EquipmentType.deleted_at.is_(None)).order_by(EquipmentType.code)
    )
    return [type_out(row) for row in rows]


async def create_type(session: AsyncSession, data: TypeCreate) -> TypeOut:
    row = EquipmentType(**data.model_dump())
    async with db_rules(session):
        session.add(row)
        await session.flush()
    return type_out(row)


# --- реестр ----------------------------------------------------------------------------------


async def list_units(
    session: AsyncSession,
    *,
    type_id: uuid.UUID | None,
    ownership: str | None,
    query: str | None,
    limit: int,
    offset: int,
) -> list[UnitOut]:
    statement = (
        _units_with_type()
        .where(EquipmentUnit.deleted_at.is_(None))
        .order_by(EquipmentType.code, EquipmentUnit.registry_number, EquipmentUnit.id)
        .limit(limit)
        .offset(offset)
    )
    if type_id is not None:
        statement = statement.where(EquipmentUnit.equipment_type_id == type_id)
    if ownership is not None:
        statement = statement.where(EquipmentUnit.ownership == ownership)
    if query:
        statement = statement.where(
            or_(
                EquipmentUnit.registry_key.ilike(like_pattern(normalize_key(query)), escape="\\"),
                EquipmentUnit.registry_number.ilike(like_pattern(query), escape="\\"),
            )
        )
    rows = (await session.execute(statement)).all()
    return [
        UnitOut(
            id=unit.id,
            legal_entity_id=unit.legal_entity_id,
            equipment_type_id=unit.equipment_type_id,
            type_code=code,
            ownership=unit.ownership,
            registry_number=unit.registry_number,
            contract_id=unit.contract_id,
            model=unit.model,
            year=unit.year,
        )
        for unit, code in rows
    ]


async def create_unit(session: AsyncSession, data: UnitCreate) -> UnitOut:
    row = EquipmentUnit(
        legal_entity_id=data.legal_entity_id,
        equipment_type_id=data.equipment_type_id,
        ownership=data.ownership,
        registry_key=normalize_key(data.registry_number),
        registry_number=data.registry_number,
        contract_id=data.contract_id,
        model=data.model,
        year=data.year,
    )
    async with db_rules(session):
        session.add(row)
        await session.flush()
    return await unit_out(session, row)


async def update_unit(session: AsyncSession, unit_id: uuid.UUID, patch: UnitPatch) -> UnitOut:
    row = await _unit(session, unit_id)
    for field in patch.model_fields_set:
        value = getattr(patch, field)
        if field == "registry_number":
            row.registry_key = normalize_key(value)
        setattr(row, field, value)
    async with db_rules(session):
        await session.commit()
    return await unit_out(session, row)


async def unit_status(session: AsyncSession, unit_id: uuid.UUID, today: date) -> str:
    """Статус на сегодня (ADR-014 часть 1, раздел 3). Считается по видимым строкам: прораб видит
    часы своих объектов, поэтому чужая загрузка в его ответе не появляется."""
    repair = await session.scalar(
        select(
            exists().where(
                EquipmentMaintenance.equipment_unit_id == unit_id,
                EquipmentMaintenance.kind == "repair",
                EquipmentMaintenance.status.in_(OPEN_REPAIR),
                EquipmentMaintenance.planned_from <= today,
                or_(
                    EquipmentMaintenance.planned_to.is_(None),
                    EquipmentMaintenance.planned_to >= today,
                ),
                EquipmentMaintenance.deleted_at.is_(None),
            )
        )
    )
    if repair:
        return "in_repair"
    documents = (await session.execute(latest_documents_statement(unit_id))).all()
    if any(kind in BLOCKING_KINDS and until < today for kind, until, _version in documents):
        return "doc_expired"
    if any(until <= today + timedelta(days=SOON_DAYS) for _kind, until, _version in documents):
        return "doc_warning"
    total = await session.scalar(
        select(func.coalesce(func.sum(EquipmentAssignment.planned_hours_per_day), 0)).where(
            EquipmentAssignment.equipment_unit_id == unit_id,
            EquipmentAssignment.status == "planned",
            EquipmentAssignment.deleted_at.is_(None),
            EquipmentAssignment.date_from <= today,
            EquipmentAssignment.date_to >= today,
        )
    )
    hours = total or 0
    if hours >= 24:
        return "day_full"
    if hours > 0:
        return "assigned"
    return "available"


async def unit_card(session: AsyncSession, unit_id: uuid.UUID) -> UnitCard:
    row = await _unit(session, unit_id)
    base = await unit_out(session, row)
    documents = await session.scalars(
        select(EquipmentDocument)
        .where(EquipmentDocument.equipment_unit_id == unit_id)
        .order_by(EquipmentDocument.kind, EquipmentDocument.version.desc())
        .limit(200)
    )
    maintenance = await session.scalars(
        select(EquipmentMaintenance)
        .where(EquipmentMaintenance.equipment_unit_id == unit_id)
        .order_by(EquipmentMaintenance.planned_from.desc())
        .limit(50)
    )
    return UnitCard(
        **base.model_dump(),
        status=await unit_status(session, unit_id, bishkek_today()),
        documents=[document_out(item) for item in documents],
        maintenance=[maintenance_out(item) for item in maintenance],
    )


# --- документы техники -------------------------------------------------------------------------


async def list_documents(session: AsyncSession, unit_id: uuid.UUID) -> list[DocumentOut]:
    await _unit(session, unit_id)
    rows = await session.scalars(
        select(EquipmentDocument)
        .where(EquipmentDocument.equipment_unit_id == unit_id)
        .order_by(EquipmentDocument.kind, EquipmentDocument.version.desc())
    )
    return [document_out(row) for row in rows]


async def add_document(
    session: AsyncSession, unit_id: uuid.UUID, data: DocumentCreate
) -> DocumentOut:
    """Новая версия строкой: старые не меняются и не удаляются (ТЗ §2.2)."""
    unit = await _unit(session, unit_id)
    last_version = await session.scalar(
        select(func.coalesce(func.max(EquipmentDocument.version), 0)).where(
            EquipmentDocument.equipment_unit_id == unit_id,
            EquipmentDocument.kind == data.kind,
        )
    )
    row = EquipmentDocument(
        legal_entity_id=unit.legal_entity_id,
        equipment_unit_id=unit.id,
        kind=data.kind,
        number=data.number,
        valid_until=data.valid_until,
        version=(last_version or 0) + 1,
    )
    async with db_rules(session):
        session.add(row)
        await session.flush()
    return document_out(row)


# --- ТО и ремонты ------------------------------------------------------------------------------


async def list_maintenance(
    session: AsyncSession,
    *,
    unit_id: uuid.UUID | None,
    status_filter: str | None,
    limit: int,
    offset: int,
) -> list[MaintenanceOut]:
    statement = (
        select(EquipmentMaintenance)
        .where(EquipmentMaintenance.deleted_at.is_(None))
        .order_by(EquipmentMaintenance.planned_from.desc(), EquipmentMaintenance.id)
        .limit(limit)
        .offset(offset)
    )
    if unit_id is not None:
        statement = statement.where(EquipmentMaintenance.equipment_unit_id == unit_id)
    if status_filter is not None:
        statement = statement.where(EquipmentMaintenance.status == status_filter)
    rows = await session.scalars(statement)
    return [maintenance_out(row) for row in rows]


async def create_maintenance(session: AsyncSession, data: MaintenanceCreate) -> MaintenanceOut:
    unit = await _unit(session, data.equipment_unit_id)
    row = EquipmentMaintenance(
        legal_entity_id=unit.legal_entity_id,
        equipment_unit_id=unit.id,
        kind=data.kind,
        title=data.title,
        planned_from=data.planned_from,
        planned_to=data.planned_to,
        status="planned",
    )
    async with db_rules(session):
        session.add(row)
        await session.flush()
    return maintenance_out(row)


async def transition_maintenance(
    session: AsyncSession, maintenance_id: uuid.UUID, patch: MaintenancePatch
) -> MaintenanceOut:
    """Переходы ТО и ремонта (часть 1, раздел 3). Повтор текущего состояния - no-op."""
    row = await _maintenance(session, maintenance_id)
    if patch.status == row.status:
        return maintenance_out(row)
    if patch.status not in MAINTENANCE_NEXT.get(row.status, frozenset()):
        raise transition_forbidden()
    if row.status == "planned" and patch.status == "done" and row.kind == "repair":
        raise transition_forbidden()  # ремонт проходит через «в работе»; ТО можно закрыть сразу
    row.status = patch.status
    if patch.status == "done":
        row.done_at = datetime.now(UTC)
        if patch.project_id is not None:
            row.project_id = patch.project_id
    async with db_rules(session):
        await session.commit()
    return maintenance_out(row)


def latest_documents_statement(unit_id: uuid.UUID) -> Select[tuple[str, date, int]]:
    return (
        select(EquipmentDocument.kind, EquipmentDocument.valid_until, EquipmentDocument.version)
        .where(
            EquipmentDocument.equipment_unit_id == unit_id, EquipmentDocument.deleted_at.is_(None)
        )
        .distinct(EquipmentDocument.kind)
        .order_by(EquipmentDocument.kind, EquipmentDocument.version.desc())
    )
