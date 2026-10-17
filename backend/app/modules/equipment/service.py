import re
import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rules import db_rules
from app.modules.documents.models import Document, DocumentLink
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
    UnitListOut,
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


def document_out(row: EquipmentDocument, document_no: str | None = None) -> DocumentOut:
    return DocumentOut(
        id=row.id,
        equipment_unit_id=row.equipment_unit_id,
        kind=row.kind,
        number=row.number,
        valid_until=row.valid_until,
        version=row.version,
        document_id=row.document_id,
        document_no=document_no,
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
    status_filter: str | None,
    project_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> list[UnitListOut]:
    today = bishkek_today()
    statement = (
        _units_with_type()
        .where(EquipmentUnit.deleted_at.is_(None))
        .order_by(EquipmentType.code, EquipmentUnit.registry_number, EquipmentUnit.id)
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
    if project_id is not None:
        statement = statement.where(EquipmentUnit.id.in_(assigned_today(project_id, today)))
    if status_filter is None:
        statement = statement.limit(limit).offset(offset)
    rows = (await session.execute(statement)).all()
    statuses = await unit_statuses(session, [unit.id for unit, _code in rows], today)
    listed = [unit_list_out(unit, code, statuses[unit.id]) for unit, code in rows]
    if status_filter is None:
        return listed
    matching = [item for item in listed if item.status == status_filter]
    return matching[offset : offset + limit]


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


async def unit_statuses(
    session: AsyncSession, unit_ids: Sequence[uuid.UUID], today: date
) -> dict[uuid.UUID, str]:
    """Статус на сегодня (ADR-014 часть 1, раздел 3) пачкой: три запроса на всю выборку.

    Видимость - RLS: прораб видит часы своих объектов, поэтому чужая загрузка в ответе не
    появляется. Порядок признаков тот же, что у одной единицы: ремонт, документ, часы.
    """
    if not unit_ids:
        return {}
    repairs = set(
        await session.scalars(
            select(EquipmentMaintenance.equipment_unit_id).where(
                EquipmentMaintenance.equipment_unit_id.in_(unit_ids),
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
    documents: dict[uuid.UUID, list[tuple[str, date]]] = defaultdict(list)
    for unit_id, kind, until in (await session.execute(latest_documents_bulk(unit_ids))).all():
        documents[unit_id].append((kind, until))
    hours_rows = await session.execute(
        select(
            EquipmentAssignment.equipment_unit_id,
            func.sum(EquipmentAssignment.planned_hours_per_day),
        )
        .where(
            EquipmentAssignment.equipment_unit_id.in_(unit_ids),
            EquipmentAssignment.status == "planned",
            EquipmentAssignment.deleted_at.is_(None),
            EquipmentAssignment.date_from <= today,
            EquipmentAssignment.date_to >= today,
        )
        .group_by(EquipmentAssignment.equipment_unit_id)
    )
    hours = {unit_id: total for unit_id, total in hours_rows.all()}
    return {unit_id: _status_of(unit_id, today, repairs, documents, hours) for unit_id in unit_ids}


def _status_of(
    unit_id: uuid.UUID,
    today: date,
    repairs: set[uuid.UUID],
    documents: dict[uuid.UUID, list[tuple[str, date]]],
    hours: dict[uuid.UUID, Decimal],
) -> str:
    if unit_id in repairs:
        return "in_repair"
    found = documents.get(unit_id, [])
    if any(kind in BLOCKING_KINDS and until < today for kind, until in found):
        return "doc_expired"
    if any(until <= today + timedelta(days=SOON_DAYS) for _kind, until in found):
        return "doc_warning"
    total = hours.get(unit_id, Decimal(0))
    if total >= 24:
        return "day_full"
    if total > 0:
        return "assigned"
    return "available"


async def unit_status(session: AsyncSession, unit_id: uuid.UUID, today: date) -> str:
    return (await unit_statuses(session, [unit_id], today))[unit_id]


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
        documents=await document_outs(session, list(documents)),
        maintenance=[maintenance_out(item) for item in maintenance],
    )


# --- документы техники -------------------------------------------------------------------------


DOCUMENT_KIND_OF = {
    "passport": "equipment_passport",
    "technical_inspection": "equipment_technical_inspection",
    "insurance": "equipment_insurance",
    "permit": "equipment_permit",
    "other": "equipment_other",
}


async def document_outs(
    session: AsyncSession, rows: Sequence[EquipmentDocument]
) -> list[DocumentOut]:
    """Строки срока с регистрационным номером связанного документа: номер отдаёт RLS документов."""
    ids = [row.document_id for row in rows if row.document_id is not None]
    numbers: dict[uuid.UUID, str] = {}
    if ids:
        numbers = {
            found.id: found.registration_no
            for found in await session.execute(
                select(Document.id, Document.registration_no).where(Document.id.in_(ids))
            )
        }
    return [
        document_out(row, numbers.get(row.document_id) if row.document_id else None) for row in rows
    ]


async def _linked_document_no(
    session: AsyncSession, unit_id: uuid.UUID, kind: str, document_id: uuid.UUID
) -> str:
    """Документ строки срока (Q8): виден вызывающему, того же вида и связан с этой единицей."""
    found = (
        await session.execute(
            select(Document.kind, Document.registration_no).where(Document.id == document_id)
        )
    ).one_or_none()
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    if found.kind != DOCUMENT_KIND_OF[kind]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "equipment.document_kind_mismatch"
        )
    linked = await session.scalar(
        select(DocumentLink.id).where(
            DocumentLink.document_id == document_id,
            DocumentLink.equipment_unit_id == unit_id,
        )
    )
    if linked is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "equipment.document_unit_mismatch"
        )
    number: str = found.registration_no
    return number


async def list_documents(session: AsyncSession, unit_id: uuid.UUID) -> list[DocumentOut]:
    await _unit(session, unit_id)
    rows = await session.scalars(
        select(EquipmentDocument)
        .where(EquipmentDocument.equipment_unit_id == unit_id)
        .order_by(EquipmentDocument.kind, EquipmentDocument.version.desc())
    )
    return await document_outs(session, list(rows))


async def add_document(
    session: AsyncSession, unit_id: uuid.UUID, data: DocumentCreate
) -> DocumentOut:
    """Новая версия строкой: старые не меняются и не удаляются (ТЗ §2.2)."""
    unit = await _unit(session, unit_id)
    # Блокировка на время транзакции: параллельные добавления этой единицы ждут друг друга, поэтому
    # номер версии идёт без пропусков и не сталкивается с уникальным индексом (F2, ADR-015 часть 3
    # раздел 13). Блокировка строки FOR UPDATE здесь не годится: политика записи техники (RLS)
    # скрывает строку от механика, и лок не берётся; advisory-лок от прав не зависит.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"equipment_document:{unit_id}"},
    )
    last_version = await session.scalar(
        select(func.coalesce(func.max(EquipmentDocument.version), 0)).where(
            EquipmentDocument.equipment_unit_id == unit_id,
            EquipmentDocument.kind == data.kind,
        )
    )
    linked_no = None
    if data.document_id is not None:
        linked_no = await _linked_document_no(session, unit_id, data.kind, data.document_id)
    row = EquipmentDocument(
        legal_entity_id=unit.legal_entity_id,
        equipment_unit_id=unit.id,
        kind=data.kind,
        number=data.number,
        valid_until=data.valid_until,
        version=(last_version or 0) + 1,
        document_id=data.document_id,
    )
    async with db_rules(session):
        session.add(row)
        await session.flush()
    return document_out(row, linked_no)


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
    if patch.status == "done":
        # Закрытие через определитель: сумма - в журнал затрат той же транзакции (ADR, часть 1 §3).
        async with db_rules(session):
            await session.execute(
                text("SELECT app.equipment_maintenance_close(:m, :p, :a)"),
                {"m": maintenance_id, "p": patch.project_id, "a": patch.amount},
            )
            await session.commit()
        await session.refresh(row)
        return maintenance_out(row)
    if patch.status not in MAINTENANCE_NEXT.get(row.status, frozenset()):
        raise transition_forbidden()
    row.status = patch.status
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


def latest_documents_bulk(unit_ids: Sequence[uuid.UUID]) -> Select[tuple[uuid.UUID, str, date]]:
    """Последняя версия каждого вида документа по всей выборке единиц, одним запросом."""
    return (
        select(
            EquipmentDocument.equipment_unit_id,
            EquipmentDocument.kind,
            EquipmentDocument.valid_until,
        )
        .where(
            EquipmentDocument.equipment_unit_id.in_(unit_ids),
            EquipmentDocument.deleted_at.is_(None),
        )
        .distinct(EquipmentDocument.equipment_unit_id, EquipmentDocument.kind)
        .order_by(
            EquipmentDocument.equipment_unit_id,
            EquipmentDocument.kind,
            EquipmentDocument.version.desc(),
        )
    )


def assigned_today(project_id: uuid.UUID, today: date) -> Select[tuple[uuid.UUID]]:
    """Единицы, которые объект занимает сегодня: назначения в статусе planned."""
    return select(EquipmentAssignment.equipment_unit_id).where(
        EquipmentAssignment.project_id == project_id,
        EquipmentAssignment.status == "planned",
        EquipmentAssignment.deleted_at.is_(None),
        EquipmentAssignment.date_from <= today,
        EquipmentAssignment.date_to >= today,
    )


def unit_list_out(unit: EquipmentUnit, type_code: str, status_value: str) -> UnitListOut:
    return UnitListOut(
        id=unit.id,
        legal_entity_id=unit.legal_entity_id,
        equipment_type_id=unit.equipment_type_id,
        type_code=type_code,
        ownership=unit.ownership,
        registry_number=unit.registry_number,
        contract_id=unit.contract_id,
        model=unit.model,
        year=unit.year,
        status=status_value,
    )
