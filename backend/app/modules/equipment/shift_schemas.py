"""Схемы путевых листов, ГСМ, делегатов и журнала затрат (SE-2a, ADR-014 часть 3 §9, SE-2).

Шапка листа денег не содержит: суммы видны только в журнале и в ГСМ с правом read:rates.
Тела запросов запрещают лишние поля (extra=forbid), как в SE-1.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.equipment.schemas import HoursPerDay, Money, ReasonIn
from app.modules.photos.models import MAX_PHOTO_BYTES

Odometer = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]
Tonnes = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=3)]
Downtime = Annotated[Decimal, Field(ge=0, le=24, max_digits=4, decimal_places=2)]
Liters = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]
ShiftStatus = str


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- путевые листы ------------------------------------------------------------------------------


class LineIn(StrictModel):
    """Строка разнесения листа: узел и/или строка ГПР, часы, рейсы, тонны."""

    node_id: uuid.UUID | None = None
    schedule_item_id: uuid.UUID | None = None
    hours: HoursPerDay
    trips: int | None = Field(default=None, ge=0)
    tonnes: Tonnes | None = None


class ShiftCreate(StrictModel):
    equipment_unit_id: uuid.UUID
    project_id: uuid.UUID
    work_date: date
    operator_id: uuid.UUID
    assignment_id: uuid.UUID | None = None
    hours: HoursPerDay
    odometer_from: Odometer | None = None
    odometer_to: Odometer | None = None
    trips: int | None = Field(default=None, ge=0)
    tonnes: Tonnes | None = None
    downtime_hours: Downtime = Decimal("0")
    downtime_reason: str | None = Field(default=None, max_length=500)
    lines: list[LineIn] = Field(default_factory=list, max_length=50)


class ShiftPatch(StrictModel):
    """Правка черновика автором или подтверждённого техдиректором. lines заменяет разнесение."""

    operator_id: uuid.UUID | None = None
    hours: HoursPerDay | None = None
    odometer_from: Odometer | None = None
    odometer_to: Odometer | None = None
    trips: int | None = Field(default=None, ge=0)
    tonnes: Tonnes | None = None
    downtime_hours: Downtime | None = None
    downtime_reason: str | None = Field(default=None, max_length=500)
    assignment_id: uuid.UUID | None = None
    lines: list[LineIn] | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def _something_to_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("nothing to change")
        for field in ("operator_id", "hours", "downtime_hours"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class ShiftBatchCreate(StrictModel):
    """Ввод на несколько объектов одним экраном: несколько листов одним запросом (один ключ)."""

    items: list[ShiftCreate] = Field(min_length=1, max_length=20)


class LineOut(BaseModel):
    id: uuid.UUID
    node_id: uuid.UUID | None
    schedule_item_id: uuid.UUID | None
    hours: Decimal
    trips: int | None
    tonnes: Decimal | None


class ShiftOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    equipment_unit_id: uuid.UUID
    assignment_id: uuid.UUID | None
    work_date: date
    operator_id: uuid.UUID
    hours: Decimal
    odometer_from: Decimal | None
    odometer_to: Decimal | None
    trips: int | None
    tonnes: Decimal | None
    downtime_hours: Decimal
    downtime_reason: str | None
    source: str
    status: ShiftStatus
    reject_reason: str | None
    confirmed_by: uuid.UUID | None
    confirmed_at: datetime | None
    created_by: uuid.UUID | None
    posting_version: int
    lines: list[LineOut]
    unplanned: bool


class ShiftBatchOut(BaseModel):
    items: list[ShiftOut]


# --- ГСМ ----------------------------------------------------------------------------------------


class FuelCreate(StrictModel):
    """Заправка строкой. Без объекта - накладные (видны только с гейтом денег)."""

    equipment_unit_id: uuid.UUID
    project_id: uuid.UUID | None = None
    fuel_date: date
    liters: Liters | None = None
    amount: Money


class ShiftFuelCreate(StrictModel):
    """Заправка в путевом листе: объект, дата и единица - от листа. Сумму вводит машинист."""

    liters: Liters | None = None
    amount: Money


class ReceiptPresign(StrictModel):
    """Чек заправки: тип и размер - в подписи загрузки, как у фото факта."""

    content_type: Literal["image/jpeg", "image/png", "image/heic", "image/webp"]
    size_bytes: int = Field(gt=0, le=MAX_PHOTO_BYTES)


class ReceiptConfirm(StrictModel):
    key: str = Field(min_length=1, max_length=300)


class ReceiptUrlOut(BaseModel):
    url: str
    expires_in: int


class FuelOut(BaseModel):
    id: uuid.UUID
    equipment_unit_id: uuid.UUID
    project_id: uuid.UUID | None
    shift_id: uuid.UUID | None
    fuel_date: date
    liters: Decimal | None
    amount: Decimal | None
    has_receipt: bool
    reversed_at: datetime | None
    reverse_reason: str | None


# --- делегаты -----------------------------------------------------------------------------------


class DelegateCreate(StrictModel):
    project_id: uuid.UUID
    delegate_user_id: uuid.UUID
    date_from: date
    date_to: date
    reason: str = Field(min_length=1, max_length=300)


class DelegateOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    delegate_user_id: uuid.UUID
    date_from: date
    date_to: date
    reason: str
    revoked_at: datetime | None


# --- журнал затрат ------------------------------------------------------------------------------


class CostTotalOut(BaseModel):
    """Итог по объекту и категории (без строк журнала и тарифов)."""

    project_id: uuid.UUID | None
    category: str
    amount: Decimal
    entries: int


class CostEntryOut(BaseModel):
    id: uuid.UUID
    legal_entity_id: uuid.UUID
    project_id: uuid.UUID | None
    node_id: uuid.UUID | None
    schedule_item_id: uuid.UUID | None
    equipment_unit_id: uuid.UUID | None
    period: date
    category: str
    source_type: str
    source_id: uuid.UUID
    posting_version: int
    line_no: int
    quantity: Decimal | None
    tariff_unit: str | None
    rate: Decimal | None
    amount: Decimal
    reversal_of_id: uuid.UUID | None


__all__ = [
    "CostEntryOut",
    "CostTotalOut",
    "DelegateCreate",
    "DelegateOut",
    "FuelCreate",
    "FuelOut",
    "LineIn",
    "LineOut",
    "ReasonIn",
    "ReceiptConfirm",
    "ReceiptPresign",
    "ReceiptUrlOut",
    "ShiftBatchCreate",
    "ShiftBatchOut",
    "ShiftCreate",
    "ShiftFuelCreate",
    "ShiftOut",
    "ShiftPatch",
]
