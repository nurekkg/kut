"""Справочник материалов (ADR-016 часть 5, S7a). Права и цены - в политиках БД (RLS); сервис
приводит ввод к виду строк и переводит нарушения правил БД в коды API."""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rules import db_rules
from app.modules.documents.models import Document, DocumentVersion
from app.modules.equipment import imports as spreadsheet
from app.modules.materials.models import Material, MaterialBatch, MaterialPrice, MaterialUnit
from app.modules.materials.schemas import (
    BatchIn,
    BatchOut,
    ImportIssue,
    ImportReport,
    MaterialIn,
    MaterialOut,
    MaterialPatch,
    PriceIn,
    UnitIn,
    UnitOut,
)

HEADERS = {
    "артикул": "code",
    "наименование": "name",
    "базовая единица": "base_unit",
    "нужен сертификат": "requires_certificate",
}
YES = {"да", "1", "true", "yes"}
NO = {"", "нет", "0", "false", "no"}


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "not_found")


async def _material(session: AsyncSession, material_id: uuid.UUID) -> Material:
    row = await session.scalar(
        select(Material).where(Material.id == material_id, Material.deleted_at.is_(None))
    )
    if row is None:
        raise _not_found()
    return row


async def _out(session: AsyncSession, row: Material) -> MaterialOut:
    units = (
        await session.scalars(
            select(MaterialUnit)
            .where(MaterialUnit.material_id == row.id, MaterialUnit.deleted_at.is_(None))
            .order_by(MaterialUnit.unit)
        )
    ).all()
    # Строка цены без права не возвращается политикой БД: null означает «не видно».
    price = await session.scalar(
        select(MaterialPrice.unit_price).where(
            MaterialPrice.material_id == row.id, MaterialPrice.deleted_at.is_(None)
        )
    )
    return MaterialOut(
        id=row.id,
        legal_entity_id=row.legal_entity_id,
        code=row.code,
        name=row.name,
        base_unit=row.base_unit,
        requires_certificate=row.requires_certificate,
        active=row.active,
        units=[UnitOut(id=unit.id, unit=unit.unit, factor=unit.factor) for unit in units],
        plan_price=price,
    )


async def list_materials(
    session: AsyncSession, legal_entity_id: uuid.UUID | None, active_only: bool
) -> list[MaterialOut]:
    query = select(Material).where(Material.deleted_at.is_(None)).order_by(Material.code)
    if legal_entity_id is not None:
        query = query.where(Material.legal_entity_id == legal_entity_id)
    if active_only:
        query = query.where(Material.active.is_(True))
    rows = (await session.scalars(query)).all()
    return [await _out(session, row) for row in rows]


async def get_material(session: AsyncSession, material_id: uuid.UUID) -> MaterialOut:
    return await _out(session, await _material(session, material_id))


async def create_material(session: AsyncSession, body: MaterialIn) -> MaterialOut:
    async with db_rules(session):
        row = Material(
            legal_entity_id=body.legal_entity_id,
            code=body.code,
            name=body.name,
            base_unit=body.base_unit,
            requires_certificate=body.requires_certificate,
        )
        session.add(row)
        await session.flush()
    await session.commit()
    return await _out(session, row)


async def update_material(
    session: AsyncSession, material_id: uuid.UUID, body: MaterialPatch
) -> MaterialOut:
    row = await _material(session, material_id)
    if (
        body.requires_certificate is not None
        and body.requires_certificate != row.requires_certificate
    ):
        has_stock = await session.scalar(
            text("SELECT app.material_has_movements(:m)"), {"m": row.id}
        )
        if has_stock:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "material.certificate_flag_locked"
            )
    async with db_rules(session):
        for field in ("name", "requires_certificate", "active"):
            value = getattr(body, field)
            if value is not None:
                setattr(row, field, value)
        await session.flush()
    await session.commit()
    return await _out(session, row)


async def add_unit(session: AsyncSession, material_id: uuid.UUID, body: UnitIn) -> MaterialOut:
    row = await _material(session, material_id)
    if body.unit == row.base_unit:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "material.unit_is_base")
    async with db_rules(session):
        session.add(
            MaterialUnit(
                material_id=row.id,
                legal_entity_id=row.legal_entity_id,
                unit=body.unit,
                factor=body.factor,
            )
        )
        await session.flush()
    await session.commit()
    return await _out(session, row)


async def add_batch(session: AsyncSession, material_id: uuid.UUID, body: BatchIn) -> BatchOut:
    row = await _material(session, material_id)
    if row.requires_certificate and body.certificate_document_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "material.certificate_required")
    if body.certificate_document_id is not None:
        certificate = (
            await session.execute(
                select(Document.kind, Document.legal_entity_id).where(
                    Document.id == body.certificate_document_id
                )
            )
        ).one_or_none()
        has_file = await session.scalar(
            select(DocumentVersion.id)
            .where(
                DocumentVersion.document_id == body.certificate_document_id,
                DocumentVersion.s3_key.is_not(None),
            )
            .limit(1)
        )
        if (
            certificate is None
            or certificate.kind != "material_certificate"
            or certificate.legal_entity_id != row.legal_entity_id
            or has_file is None
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "material.certificate_invalid"
            )
        reused = await session.scalar(
            select(MaterialBatch.id)
            .where(
                MaterialBatch.certificate_document_id == body.certificate_document_id,
                MaterialBatch.material_id != row.id,
                MaterialBatch.deleted_at.is_(None),
            )
            .limit(1)
        )
        if reused is not None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "material.certificate_invalid"
            )
    existing = await session.scalar(
        select(MaterialBatch).where(
            MaterialBatch.material_id == row.id,
            MaterialBatch.batch_no == body.batch_no,
            MaterialBatch.deleted_at.is_(None),
        )
    )
    if existing is not None and existing.certificate_document_id == body.certificate_document_id:
        # Повтор после потери ответа: та же партия с тем же сертификатом - не вторая запись.
        return BatchOut(
            id=existing.id,
            material_id=row.id,
            batch_no=existing.batch_no,
            certificate_document_id=existing.certificate_document_id,
            customs_document_id=existing.customs_document_id,
            country_of_origin=existing.country_of_origin,
        )
    async with db_rules(session):
        batch = MaterialBatch(
            material_id=row.id,
            legal_entity_id=row.legal_entity_id,
            batch_no=body.batch_no,
            certificate_document_id=body.certificate_document_id,
            customs_document_id=body.customs_document_id,
            country_of_origin=body.country_of_origin,
        )
        session.add(batch)
        await session.flush()
    await session.commit()
    return BatchOut(
        id=batch.id,
        material_id=row.id,
        batch_no=batch.batch_no,
        certificate_document_id=batch.certificate_document_id,
        customs_document_id=batch.customs_document_id,
        country_of_origin=batch.country_of_origin,
    )


async def set_price(session: AsyncSession, material_id: uuid.UUID, body: PriceIn) -> MaterialOut:
    row = await _material(session, material_id)
    async with db_rules(session):
        existing = await session.scalar(
            select(MaterialPrice).where(
                MaterialPrice.material_id == row.id, MaterialPrice.deleted_at.is_(None)
            )
        )
        if existing is None:
            session.add(
                MaterialPrice(
                    material_id=row.id,
                    legal_entity_id=row.legal_entity_id,
                    unit_price=body.unit_price,
                )
            )
        else:
            existing.unit_price = body.unit_price
        await session.flush()
    await session.commit()
    return await _out(session, row)


def _flag(value: str) -> bool | None:
    cleaned = value.strip().lower()
    if cleaned in YES:
        return True
    if cleaned in NO:
        return False
    return None


def _cell(cells: list[str], columns: dict[str, int], name: str) -> str:
    """Значение колонки строки импорта; пустое, если колонки нет или строка короче."""
    index = columns.get(name)
    return cells[index].strip() if index is not None and index < len(cells) else ""


async def import_materials(
    session: AsyncSession,
    legal_entity_id: uuid.UUID,
    filename: str,
    data: bytes,
    dry_run: bool = False,
) -> ImportReport:
    """Импорт XLSX или CSV: всё или ничего. Любая ошибка строки - файл не записывается."""
    rows = spreadsheet.parse(filename, data)
    if not rows:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "material.import_empty")
    header = [cell.strip().lower() for cell in rows[0]]
    columns = {HEADERS[name]: index for index, name in enumerate(header) if name in HEADERS}
    if {"code", "name", "base_unit"} - columns.keys():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "material.import_header")

    issues: list[ImportIssue] = []
    parsed: list[dict[str, str | bool]] = []
    seen: set[str] = set()
    existing = set(
        (
            await session.scalars(
                select(Material.code).where(
                    Material.legal_entity_id == legal_entity_id, Material.deleted_at.is_(None)
                )
            )
        ).all()
    )
    for number, cells in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in cells):
            continue

        code, name, unit = (
            _cell(cells, columns, "code"),
            _cell(cells, columns, "name"),
            _cell(cells, columns, "base_unit"),
        )
        if not code or not name or not unit:
            issues.append(
                ImportIssue(row=number, message="Заполните артикул, наименование и единицу")
            )
            continue
        if code in seen or code in existing:
            issues.append(ImportIssue(row=number, message=f"Артикул {code} уже есть в справочнике"))
            continue
        flag = _flag(_cell(cells, columns, "requires_certificate"))
        if flag is None:
            issues.append(ImportIssue(row=number, message="Сертификат: укажите да или нет"))
            continue
        seen.add(code)
        parsed.append({"code": code, "name": name, "base_unit": unit, "requires_certificate": flag})
    if dry_run:
        return ImportReport(created=0 if issues else len(parsed), issues=issues)
    if issues:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "material.import_invalid", "issues": [i.model_dump() for i in issues]},
        )
    async with db_rules(session):
        for item in parsed:
            session.add(Material(legal_entity_id=legal_entity_id, **item))
        await session.flush()
    await session.commit()
    return ImportReport(created=len(parsed), issues=[])
