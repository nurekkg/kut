"""Маршруты путевых листов, ГСМ, делегатов и журнала затрат (SE-2a, ADR-014 часть 3 §9 SE-2).

Права по роли - зависимости маршрута (как в SE-1). Автор, переходы, лимит и проводка - в БД:
отказ приходит кодом, app/core/rules.py переводит его в ответ. Подтверждение открыто любому
с доступом к объекту: право подтверждения проверяет БД (роль, делегат на период, не автор).
"""

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.modules.auth.deps import require
from app.modules.auth.principal import Principal
from app.modules.equipment import costs, shifts
from app.modules.equipment.router import Db, Limit, Offset, RatesReader, Reader
from app.modules.equipment.shift_schemas import (
    CostEntryOut,
    CostTotalOut,
    DelegateCreate,
    DelegateOut,
    FuelCreate,
    FuelOut,
    ReasonIn,
    ReceiptConfirm,
    ReceiptPresign,
    ReceiptUrlOut,
    ShiftBatchCreate,
    ShiftBatchOut,
    ShiftCreate,
    ShiftFuelCreate,
    ShiftOut,
    ShiftPatch,
)
from app.modules.idempotency.service import IdempotencyKeyHeader, idempotent_create
from app.modules.photos.schemas import PresignedUpload
from app.modules.rbac.enums import Action, Module

router = APIRouter(prefix="/equipment", tags=["equipment"])

ShiftWriter = Annotated[
    Principal, Depends(require(Module.EQUIPMENT, Action.WRITE, qualifier="shift"))
]
ShiftApprover = Annotated[
    Principal, Depends(require(Module.EQUIPMENT, Action.APPROVE, qualifier="shift"))
]
ShiftStatusFilter = Annotated[
    Literal["draft", "submitted", "confirmed", "rejected"] | None, Query(alias="status")
]


def _rates(principal: Principal) -> bool:
    return principal.has(Module.EQUIPMENT, Action.READ, "rates")


# --- путевые листы ------------------------------------------------------------------------------


@router.post("/shifts", status_code=status.HTTP_201_CREATED, response_model=ShiftOut)
async def create_shift(
    body: ShiftCreate,
    request: Request,
    key: IdempotencyKeyHeader,
    principal: ShiftWriter,
    session: Db,
) -> Response:
    return await idempotent_create(
        session,
        request=request,
        user_id=principal.user_id,
        key=key,
        payload=body,
        handler=lambda: shifts.create_shift(session, body),
    )


@router.post("/shifts/batch", status_code=status.HTTP_201_CREATED, response_model=ShiftBatchOut)
async def create_shift_batch(
    body: ShiftBatchCreate,
    request: Request,
    key: IdempotencyKeyHeader,
    principal: ShiftWriter,
    session: Db,
) -> Response:
    """Ввод на несколько объектов одним экраном: все листы одной транзакцией, один ключ."""
    return await idempotent_create(
        session,
        request=request,
        user_id=principal.user_id,
        key=key,
        payload=body,
        handler=lambda: shifts.create_batch(session, body),
    )


@router.get("/shifts", response_model=list[ShiftOut])
async def list_shifts(
    _principal: Reader,
    session: Db,
    project_id: uuid.UUID | None = None,
    unit_id: uuid.UUID | None = None,
    status_filter: ShiftStatusFilter = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> list[ShiftOut]:
    return await shifts.list_shifts(
        session,
        project_id=project_id,
        unit_id=unit_id,
        status_filter=status_filter,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


@router.get("/shifts/{shift_id}", response_model=ShiftOut)
async def get_shift(shift_id: uuid.UUID, _principal: Reader, session: Db) -> ShiftOut:
    return await shifts.get_shift(session, shift_id)


@router.patch("/shifts/{shift_id}", response_model=ShiftOut)
async def update_shift(
    shift_id: uuid.UUID, body: ShiftPatch, _principal: Reader, session: Db
) -> ShiftOut:
    return await shifts.update_shift(session, shift_id, body)


@router.post("/shifts/{shift_id}/submit", response_model=ShiftOut)
async def submit_shift(shift_id: uuid.UUID, _principal: ShiftWriter, session: Db) -> ShiftOut:
    return await shifts.transition(session, shift_id, "submitted")


@router.post("/shifts/{shift_id}/confirm", response_model=ShiftOut)
async def confirm_shift(shift_id: uuid.UUID, _principal: Reader, session: Db) -> ShiftOut:
    return await shifts.transition(session, shift_id, "confirmed")


@router.post("/shifts/{shift_id}/reject", response_model=ShiftOut)
async def reject_shift(
    shift_id: uuid.UUID, body: ReasonIn, _principal: Reader, session: Db
) -> ShiftOut:
    return await shifts.transition(session, shift_id, "rejected", body.reason)


@router.post("/shifts/{shift_id}/reopen", response_model=ShiftOut)
async def reopen_shift(shift_id: uuid.UUID, _principal: ShiftWriter, session: Db) -> ShiftOut:
    return await shifts.transition(session, shift_id, "draft")


# --- ГСМ ----------------------------------------------------------------------------------------


@router.post("/fuel", status_code=status.HTTP_201_CREATED, response_model=FuelOut)
async def create_fuel(
    body: FuelCreate,
    request: Request,
    key: IdempotencyKeyHeader,
    principal: ShiftWriter,
    session: Db,
) -> Response:
    return await idempotent_create(
        session,
        request=request,
        user_id=principal.user_id,
        key=key,
        payload=body,
        handler=lambda: costs.create_fuel(session, body, can_rates=_rates(principal)),
    )


@router.get("/fuel", response_model=list[FuelOut])
async def list_fuel(
    principal: Reader,
    session: Db,
    project_id: uuid.UUID | None = None,
    unit_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> list[FuelOut]:
    return await costs.list_fuel(
        session,
        project_id=project_id,
        unit_id=unit_id,
        date_from=date_from,
        date_to=date_to,
        can_rates=_rates(principal),
        limit=limit,
        offset=offset,
    )


@router.post("/fuel/{fuel_id}/reverse", response_model=FuelOut)
async def reverse_fuel(
    fuel_id: uuid.UUID, body: ReasonIn, principal: Reader, session: Db
) -> FuelOut:
    return await costs.reverse_fuel(session, fuel_id, body.reason, can_rates=_rates(principal))


# Заправка в путевом листе: право проверяет БД (машинист - в своём черновике, техдиректор - после
# согласования), маршрут открыт на чтение как у самого листа.


@router.post("/shifts/{shift_id}/fuel", status_code=status.HTTP_201_CREATED, response_model=FuelOut)
async def create_shift_fuel(
    shift_id: uuid.UUID,
    body: ShiftFuelCreate,
    request: Request,
    key: IdempotencyKeyHeader,
    principal: Reader,
    session: Db,
) -> Response:
    return await idempotent_create(
        session,
        request=request,
        user_id=principal.user_id,
        key=key,
        payload=body,
        handler=lambda: costs.create_shift_fuel(
            session, shift_id, body, can_rates=_rates(principal)
        ),
    )


@router.get("/shifts/{shift_id}/fuel", response_model=list[FuelOut])
async def list_shift_fuel(shift_id: uuid.UUID, principal: Reader, session: Db) -> list[FuelOut]:
    return await costs.list_shift_fuel(session, shift_id, can_rates=_rates(principal))


@router.post("/shifts/{shift_id}/fuel/{fuel_id}/remove", status_code=status.HTTP_204_NO_CONTENT)
async def remove_shift_fuel(
    shift_id: uuid.UUID, fuel_id: uuid.UUID, _principal: Reader, session: Db
) -> Response:
    await costs.remove_shift_fuel(session, shift_id, fuel_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/shifts/{shift_id}/fuel/{fuel_id}/receipt", response_model=PresignedUpload)
async def presign_receipt(
    shift_id: uuid.UUID, fuel_id: uuid.UUID, body: ReceiptPresign, _principal: Reader, session: Db
) -> PresignedUpload:
    return await costs.receipt_presign(session, shift_id, fuel_id, body)


@router.post("/shifts/{shift_id}/fuel/{fuel_id}/receipt/confirm", response_model=FuelOut)
async def confirm_receipt(
    shift_id: uuid.UUID,
    fuel_id: uuid.UUID,
    body: ReceiptConfirm,
    principal: Reader,
    session: Db,
) -> FuelOut:
    return await costs.receipt_confirm(
        session, shift_id, fuel_id, body, can_rates=_rates(principal)
    )


# Чек показывает сумму заправки: ссылка на него - с правом read:rates, как у журнала затрат.
@router.get("/shifts/{shift_id}/fuel/{fuel_id}/receipt", response_model=ReceiptUrlOut)
async def receipt_url(
    shift_id: uuid.UUID, fuel_id: uuid.UUID, _principal: RatesReader, session: Db
) -> ReceiptUrlOut:
    return await costs.receipt_url(session, shift_id, fuel_id)


# --- делегаты подтверждения ---------------------------------------------------------------------


@router.post("/delegates", status_code=status.HTTP_201_CREATED, response_model=DelegateOut)
async def create_delegate(
    body: DelegateCreate,
    request: Request,
    key: IdempotencyKeyHeader,
    principal: ShiftApprover,
    session: Db,
) -> Response:
    return await idempotent_create(
        session,
        request=request,
        user_id=principal.user_id,
        key=key,
        payload=body,
        handler=lambda: costs.create_delegate(session, body),
    )


@router.get("/delegates", response_model=list[DelegateOut])
async def list_delegates(
    _principal: Reader,
    session: Db,
    project_id: uuid.UUID | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> list[DelegateOut]:
    return await costs.list_delegates(session, project_id=project_id, limit=limit, offset=offset)


@router.post("/delegates/{delegate_id}/revoke", response_model=DelegateOut)
async def revoke_delegate(
    delegate_id: uuid.UUID, principal: ShiftApprover, session: Db
) -> DelegateOut:
    return await costs.revoke_delegate(session, delegate_id, user_id=principal.user_id)


# --- журнал затрат ------------------------------------------------------------------------------


@router.get("/costs/totals", response_model=list[CostTotalOut])
async def cost_totals(
    _principal: Reader,
    session: Db,
    date_from: date,
    date_to: date,
    project_id: uuid.UUID | None = None,
) -> list[CostTotalOut]:
    return await costs.cost_totals(
        session, date_from=date_from, date_to=date_to, project_id=project_id
    )


@router.get("/costs/entries", response_model=list[CostEntryOut])
async def cost_entries(
    _principal: RatesReader,
    session: Db,
    date_from: date,
    date_to: date,
    project_id: uuid.UUID | None = None,
    category: str | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> list[CostEntryOut]:
    return await costs.cost_entries(
        session,
        date_from=date_from,
        date_to=date_to,
        project_id=project_id,
        category=category,
        limit=limit,
        offset=offset,
    )
