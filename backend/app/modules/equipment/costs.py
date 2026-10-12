"""ГСМ, делегаты подтверждения и журнал затрат (SE-2a/SE-2b, ADR-014 часть 2 §3-5, решения PO).

Проводки создают и сторнируют определители БД (app.equipment_fuel_post, app.equipment_fuel_reverse,
app.equipment_shift_post); сервис читает результат. Сумма заправки приложению в таблице не выдана
(ревизия f2b7d4c9e1a6): её показывает журнал затрат текущей версии, и только при read:rates. Строки
журнала отдаёт только RLS с гейтом read:rates; итоги для прораба - app.equipment_cost_totals.
Чек заправки - ключ в S3, как фото факта: подписанная загрузка, подтверждение после HEAD.
"""

import re
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.config import get_settings
from app.core.rules import db_rules
from app.modules.equipment.models import (
    CostEntry,
    EquipmentDelegate,
    EquipmentFuel,
    EquipmentShift,
    EquipmentUnit,
)
from app.modules.equipment.shift_schemas import (
    CostEntryOut,
    CostTotalOut,
    DelegateCreate,
    DelegateOut,
    FuelCreate,
    FuelOut,
    ReceiptConfirm,
    ReceiptPresign,
    ReceiptUrlOut,
    ShiftFuelCreate,
)
from app.modules.equipment.shifts import writable
from app.modules.photos.models import EXTENSIONS, MAX_PHOTO_BYTES
from app.modules.photos.schemas import PresignedUpload

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_RECEIPT_KEY = re.compile(
    rf"project/{_UUID}/shift/{_UUID}/fuel/{_UUID}/{_UUID}\.(?:jpg|png|heic|webp)"
)


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "not_found")


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


async def _shift(session: AsyncSession, shift_id: uuid.UUID) -> EquipmentShift:
    row = await session.scalar(
        select(EquipmentShift).where(
            EquipmentShift.id == shift_id, EquipmentShift.deleted_at.is_(None)
        )
    )
    if row is None:
        raise _not_found()
    return row


async def _fuel(session: AsyncSession, fuel_id: uuid.UUID) -> EquipmentFuel:
    row = await session.scalar(
        select(EquipmentFuel).where(EquipmentFuel.id == fuel_id, EquipmentFuel.deleted_at.is_(None))
    )
    if row is None:
        raise _not_found()
    return row


async def _shift_fuel(
    session: AsyncSession, shift_id: uuid.UUID, fuel_id: uuid.UUID
) -> EquipmentFuel:
    row = await session.scalar(
        select(EquipmentFuel).where(
            EquipmentFuel.id == fuel_id,
            EquipmentFuel.shift_id == shift_id,
            EquipmentFuel.deleted_at.is_(None),
        )
    )
    if row is None:
        raise _not_found()
    return row


async def _fuel_amounts(
    session: AsyncSession, fuel_ids: Sequence[uuid.UUID], *, can_rates: bool
) -> dict[uuid.UUID, Decimal]:
    """Суммы заправок из журнала: текущая версия проводки листа (без листа - версия 1), без сторно.
    Без права read:rates журнал строк не отдаёт, суммы нет."""
    if not can_rates or not fuel_ids:
        return {}
    rows = await session.execute(
        text(
            "SELECT c.source_id, SUM(c.amount) AS amount"
            " FROM public.cost_entry c"
            " JOIN public.equipment_fuel f ON f.id = c.source_id"
            " LEFT JOIN public.equipment_shift s ON s.id = f.shift_id"
            " WHERE c.source_type = 'equipment_fuel' AND c.source_id = ANY(CAST(:ids AS uuid[]))"
            " AND c.reversal_of_id IS NULL AND c.posting_version = COALESCE(s.posting_version, 1)"
            " GROUP BY c.source_id"
        ),
        {"ids": list(fuel_ids)},
    )
    return {row.source_id: row.amount for row in rows}


def _fuel_out(row: EquipmentFuel, amount: Decimal | None) -> FuelOut:
    """Строка заправки без колонки amount: сумму приложение не читает, её даёт журнал (см. выше)."""
    return FuelOut(
        id=row.id,
        equipment_unit_id=row.equipment_unit_id,
        project_id=row.project_id,
        shift_id=row.shift_id,
        fuel_date=row.fuel_date,
        liters=row.liters,
        amount=amount,
        has_receipt=row.receipt_key is not None,
        reversed_at=row.reversed_at,
        reverse_reason=row.reverse_reason,
    )


async def _fuel_outs(
    session: AsyncSession, rows: Sequence[EquipmentFuel], *, can_rates: bool
) -> list[FuelOut]:
    amounts = await _fuel_amounts(session, [row.id for row in rows], can_rates=can_rates)
    return [_fuel_out(row, amounts.get(row.id)) for row in rows]


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
        fuel_id = row.id
        await session.commit()
    return (await _fuel_outs(session, [await _fuel(session, fuel_id)], can_rates=can_rates))[0]


async def create_shift_fuel(
    session: AsyncSession, shift_id: uuid.UUID, body: ShiftFuelCreate, *, can_rates: bool
) -> FuelOut:
    """Заправка в листе: объект, дату и единицу берёт лист; право и статус листа проверяет БД."""
    shift = await _shift(session, shift_id)
    async with db_rules(session):
        row = EquipmentFuel(
            legal_entity_id=shift.legal_entity_id,
            project_id=shift.project_id,
            equipment_unit_id=shift.equipment_unit_id,
            fuel_date=shift.work_date,
            shift_id=shift.id,
            liters=body.liters,
            amount=body.amount,
        )
        session.add(row)
        await session.flush()
        fuel_id = row.id
        await session.commit()
    return (await _fuel_outs(session, [await _fuel(session, fuel_id)], can_rates=can_rates))[0]


async def list_shift_fuel(
    session: AsyncSession, shift_id: uuid.UUID, *, can_rates: bool
) -> list[FuelOut]:
    await _shift(session, shift_id)
    rows = await session.scalars(
        select(EquipmentFuel)
        .where(EquipmentFuel.shift_id == shift_id, EquipmentFuel.deleted_at.is_(None))
        .order_by(EquipmentFuel.created_at, EquipmentFuel.id)
    )
    return await _fuel_outs(session, list(rows), can_rates=can_rates)


async def remove_shift_fuel(session: AsyncSession, shift_id: uuid.UUID, fuel_id: uuid.UUID) -> None:
    """Удаление заправки из листа: черновик - автор или машинист, подтверждённый - техдиректор.
    Подтверждённый лист проводится заново (сторно старой версии - в БД)."""
    row = await _shift_fuel(session, shift_id, fuel_id)
    async with writable(session):
        row.deleted_at = datetime.now(UTC)
        await session.flush()
        await session.commit()


async def receipt_presign(
    session: AsyncSession, shift_id: uuid.UUID, fuel_id: uuid.UUID, body: ReceiptPresign
) -> PresignedUpload:
    row = await _shift_fuel(session, shift_id, fuel_id)
    ttl = get_settings().photo_upload_ttl_seconds
    key = (
        f"project/{row.project_id}/shift/{shift_id}/fuel/{fuel_id}/"
        f"{uuid.uuid4()}.{EXTENSIONS[body.content_type]}"
    )
    return PresignedUpload(
        key=key,
        upload_url=storage.presign_put(key, body.content_type, body.size_bytes, ttl),
        headers={"Content-Type": body.content_type, **storage.WRITE_ONCE_HEADERS},
        expires_in=ttl,
    )


async def receipt_confirm(
    session: AsyncSession,
    shift_id: uuid.UUID,
    fuel_id: uuid.UUID,
    body: ReceiptConfirm,
    *,
    can_rates: bool,
) -> FuelOut:
    """Чек записывается после того, как сервер увидел файл в S3: тип и размер - из хранилища.
    Чек уже есть с тем же ключом - повтор без записи; с другим ключом - отказ (чек не подменяют)."""
    row = await _shift_fuel(session, shift_id, fuel_id)
    prefix = f"project/{row.project_id}/shift/{shift_id}/fuel/{fuel_id}/"
    if not (body.key.startswith(prefix) and _RECEIPT_KEY.fullmatch(body.key)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "receipt_key_invalid")
    if row.receipt_key is not None:
        if row.receipt_key != body.key:
            raise HTTPException(status.HTTP_409_CONFLICT, "receipt_exists")
    else:
        try:
            stored = await storage.head_object(body.key)
        except (ClientError, BotoCoreError) as error:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "storage_unavailable"
            ) from error
        if stored is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "receipt_not_uploaded")
        if stored.content_type not in EXTENSIONS or not 0 < stored.size <= MAX_PHOTO_BYTES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "receipt_invalid")
        async with writable(session):
            row.receipt_key = body.key
            row.receipt_content_type = stored.content_type
            await session.flush()
            await session.commit()
    return (await _fuel_outs(session, [await _fuel(session, fuel_id)], can_rates=can_rates))[0]


async def receipt_url(
    session: AsyncSession, shift_id: uuid.UUID, fuel_id: uuid.UUID
) -> ReceiptUrlOut:
    row = await _shift_fuel(session, shift_id, fuel_id)
    if row.receipt_key is None or row.receipt_content_type is None:
        raise _not_found()
    ttl = get_settings().photo_download_ttl_seconds
    url = storage.presign_get(row.receipt_key, row.receipt_content_type, ttl)
    return ReceiptUrlOut(url=url, expires_in=ttl)


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
    return await _fuel_outs(session, list(rows), can_rates=can_rates)


async def reverse_fuel(
    session: AsyncSession, fuel_id: uuid.UUID, reason: str, *, can_rates: bool
) -> FuelOut:
    """Сторно заправки без листа: строка остаётся, в журнале - сторно. Повтор - без новых строк."""
    await _fuel(session, fuel_id)
    async with db_rules(session):
        await session.execute(
            text("SELECT app.equipment_fuel_reverse(:f, :r)"), {"f": fuel_id, "r": reason}
        )
        await session.commit()
    return (await _fuel_outs(session, [await _fuel(session, fuel_id)], can_rates=can_rates))[0]


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
