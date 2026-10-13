"""ГСМ, делегаты подтверждения, журнал затрат и количества по технике (SE-2a/SE-2b, PO 2026-10-13).

Проводки создают и сторнируют определители БД (app.equipment_fuel_post, app.equipment_fuel_reverse,
app.equipment_shift_post); сервис читает результат. Сумма заправки приложению не выдана (колонка
amount без SELECT у kut_app): деньги по технике видит только журнал затрат с гейтом read:rates.
Ответы о заправке и листах сумм не содержат ни для кого. Строки журнала отдаёт RLS с гейтом
read:rates; итоги журнала - app.equipment_cost_totals (только read:rates). Количества для вкладки
«Техника» - usage_totals. Чек заправки - ключ в S3, как фото факта: подписанная загрузка, запись
после HEAD; право на чек проверяет БД до подписи (app.equipment_fuel_receipt_check) и при записи
(app.equipment_fuel_attach_receipt).
"""

import re
import uuid
from datetime import UTC, date, datetime

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
    DelegateCandidateOut,
    DelegateCreate,
    DelegateOut,
    FuelCreate,
    FuelOut,
    QuantityUnitOut,
    ReceiptConfirm,
    ReceiptPresign,
    ReceiptUrlOut,
    ShiftFuelCreate,
    UsageOut,
)
from app.modules.equipment.shifts import writable
from app.modules.photos.models import EXTENSIONS, MAX_PHOTO_BYTES
from app.modules.photos.schemas import PresignedUpload

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_RECEIPT_KEY = re.compile(
    rf"project/{_UUID}/shift/{_UUID}/fuel/{_UUID}/{_UUID}\.(?:jpg|png|heic|webp)"
)
_USAGE = text(
    "SELECT project_id, equipment_unit_id, day, SUM(shifts) AS shifts, SUM(hours) AS hours,"
    " SUM(trips) AS trips, SUM(tonnes) AS tonnes, SUM(downtime_hours) AS downtime_hours,"
    " SUM(liters) AS liters FROM ("
    " SELECT s.project_id, s.equipment_unit_id, s.work_date AS day, 1 AS shifts, s.hours,"
    " COALESCE(s.trips, 0) AS trips, COALESCE(s.tonnes, 0) AS tonnes, s.downtime_hours,"
    " 0 AS liters FROM public.equipment_shift s"
    " WHERE s.deleted_at IS NULL AND s.status = 'confirmed' AND s.work_date BETWEEN :f AND :t"
    " AND (CAST(:p AS uuid) IS NULL OR s.project_id = CAST(:p AS uuid))"
    " UNION ALL"
    " SELECT fu.project_id, fu.equipment_unit_id, fu.fuel_date, 0, 0, 0, 0, 0,"
    " COALESCE(fu.liters, 0)"
    " FROM public.equipment_fuel fu"
    " WHERE fu.deleted_at IS NULL AND fu.reversed_at IS NULL AND fu.project_id IS NOT NULL"
    " AND fu.fuel_date BETWEEN :f AND :t"
    " AND (CAST(:p AS uuid) IS NULL OR fu.project_id = CAST(:p AS uuid))"
    ") x GROUP BY project_id, equipment_unit_id, day ORDER BY day, equipment_unit_id"
)


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "not_found")


async def quantity_unit(
    session: AsyncSession, *, unit_id: uuid.UUID, operator_id: uuid.UUID, work_date: date
) -> QuantityUnitOut:
    """Единица тарификации для ввода (без ставки): видимость и тариф - в БД."""
    async with db_rules(session):
        tariff = await session.scalar(
            text("SELECT app.equipment_quantity_unit(:u, :o, :d)"),
            {"u": unit_id, "o": operator_id, "d": work_date},
        )
    return QuantityUnitOut(tariff_unit=tariff)


async def delegate_candidates(
    session: AsyncSession, *, project_id: uuid.UUID
) -> list[DelegateCandidateOut]:
    """Кандидаты в делегаты объекта: право approve:shift и объект проверяет БД (definer-функция)."""
    async with db_rules(session):
        rows = await session.execute(
            text(
                "SELECT o_user_id, o_full_name, o_role_code"
                " FROM app.equipment_delegate_candidates(:p)"
            ),
            {"p": project_id},
        )
        found = [
            DelegateCandidateOut(
                user_id=row.o_user_id, full_name=row.o_full_name, role_code=row.o_role_code
            )
            for row in rows
        ]
    return found


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
        select(EquipmentFuel)
        .where(EquipmentFuel.id == fuel_id, EquipmentFuel.deleted_at.is_(None))
        .execution_options(populate_existing=True)
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


def _fuel_out(row: EquipmentFuel) -> FuelOut:
    return FuelOut(
        id=row.id,
        equipment_unit_id=row.equipment_unit_id,
        project_id=row.project_id,
        shift_id=row.shift_id,
        fuel_date=row.fuel_date,
        liters=row.liters,
        has_receipt=row.receipt_key is not None,
        reversed_at=row.reversed_at,
        reverse_reason=row.reverse_reason,
    )


async def create_fuel(session: AsyncSession, body: FuelCreate) -> FuelOut:
    """Заправка без листа. Накладная без объекта - начальник отдела и техдиректор (БД)."""
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
    return _fuel_out(await _fuel(session, fuel_id))


async def create_shift_fuel(
    session: AsyncSession, shift_id: uuid.UUID, body: ShiftFuelCreate
) -> FuelOut:
    """Заправка в листе: объект, дату и единицу берёт лист; право и статус проверяет БД."""
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
    return _fuel_out(await _fuel(session, fuel_id))


async def list_shift_fuel(session: AsyncSession, shift_id: uuid.UUID) -> list[FuelOut]:
    await _shift(session, shift_id)
    rows = await session.scalars(
        select(EquipmentFuel)
        .where(EquipmentFuel.shift_id == shift_id, EquipmentFuel.deleted_at.is_(None))
        .order_by(EquipmentFuel.created_at, EquipmentFuel.id)
    )
    return [_fuel_out(row) for row in rows]


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
    """Подпись загрузки - только тому, кто вправе сменить чек: право и статус проверяет БД."""
    row = await _shift_fuel(session, shift_id, fuel_id)
    async with db_rules(session):
        await session.execute(text("SELECT app.equipment_fuel_receipt_check(:f)"), {"f": fuel_id})
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
    session: AsyncSession, shift_id: uuid.UUID, fuel_id: uuid.UUID, body: ReceiptConfirm
) -> FuelOut:
    """Чек записывается после того, как сервер увидел файл в S3: тип и размер - из хранилища.
    Повтор того же ключа - без записи. Другой ключ заменяет чек, пока лист в черновике или отклонён
    (после отправки и подтверждения - нет, БД). Прежний файл в S3 остаётся: документы не
    удаляются."""
    row = await _shift_fuel(session, shift_id, fuel_id)
    prefix = f"project/{row.project_id}/shift/{shift_id}/fuel/{fuel_id}/"
    if not (body.key.startswith(prefix) and _RECEIPT_KEY.fullmatch(body.key)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "receipt_key_invalid")
    if row.receipt_key != body.key:
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
        async with db_rules(session):
            await session.execute(
                text("SELECT app.equipment_fuel_attach_receipt(:f, :k, :t)"),
                {"f": fuel_id, "k": body.key, "t": stored.content_type},
            )
            await session.commit()
    return _fuel_out(await _fuel(session, fuel_id))


async def receipt_url(
    session: AsyncSession,
    shift_id: uuid.UUID,
    fuel_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    can_rates: bool,
) -> ReceiptUrlOut:
    """Ссылка на чек: загрузивший видит свой; остальным - только с read:rates (чек - это сумма)."""
    row = await _shift_fuel(session, shift_id, fuel_id)
    if row.receipt_key is None or row.receipt_content_type is None:
        raise _not_found()
    if not (can_rates or row.receipt_uploaded_by == user_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
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
    return [_fuel_out(row) for row in rows]


async def reverse_fuel(session: AsyncSession, fuel_id: uuid.UUID, reason: str) -> FuelOut:
    """Сторно заправки без листа: строка остаётся, в журнале - сторно. Повтор - без новых строк."""
    await _fuel(session, fuel_id)
    async with db_rules(session):
        await session.execute(
            text("SELECT app.equipment_fuel_reverse(:f, :r)"), {"f": fuel_id, "r": reason}
        )
        await session.commit()
    return _fuel_out(await _fuel(session, fuel_id))


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


async def usage_totals(
    session: AsyncSession,
    *,
    date_from: date,
    date_to: date,
    project_id: uuid.UUID | None,
) -> list[UsageOut]:
    """Количества по технике, объекту и дню: часы, рейсы, тонны, простой (подтверждённые листы)
    и литры (заправки без сторно). Денег нет ни в одной колонке; строки видны по RLS листов."""
    rows = await session.execute(_USAGE, {"f": date_from, "t": date_to, "p": project_id})
    return [
        UsageOut(
            project_id=row.project_id,
            equipment_unit_id=row.equipment_unit_id,
            day=row.day,
            shifts=row.shifts,
            hours=row.hours,
            trips=row.trips,
            tonnes=row.tonnes,
            downtime_hours=row.downtime_hours,
            liters=row.liters,
        )
        for row in rows
    ]


async def cost_totals(
    session: AsyncSession,
    *,
    date_from: date,
    date_to: date,
    project_id: uuid.UUID | None,
) -> list[CostTotalOut]:
    """Итоги по объекту и категории - только с read:rates (БД); без него список пуст (Q11 C)."""
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
    unit_id: uuid.UUID | None,
    category: str | None,
    limit: int,
    offset: int,
) -> list[CostEntryOut]:
    """Строки журнала затрат: только для права read:rates (гейт в маршруте и в RLS)."""
    query = select(CostEntry).where(CostEntry.period >= date_from, CostEntry.period <= date_to)
    if project_id is not None:
        query = query.where(CostEntry.project_id == project_id)
    if unit_id is not None:
        query = query.where(CostEntry.equipment_unit_id == unit_id)
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
