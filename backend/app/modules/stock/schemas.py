"""Схемы склада (ADR-016 часть 5, S7a). Количества - в базовой единице материала."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class WarehouseIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    project_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)


class WarehouseOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    kind: str


class BalanceOut(BaseModel):
    warehouse_id: uuid.UUID
    material_id: uuid.UUID
    base_unit: str
    qty: Decimal


class MovementOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    kind: str
    qty: Decimal
    unit: str
    quantity_in_unit: Decimal
    factor: Decimal
    material_id: uuid.UUID
    batch_id: uuid.UUID | None
    document_id: uuid.UUID | None
    version_id: uuid.UUID | None
    source_line_id: uuid.UUID | None
    project_node_id: uuid.UUID | None
    responsible_user_id: uuid.UUID | None
    reversal_of_id: uuid.UUID | None


class PostOut(BaseModel):
    movements: int


class ReverseOut(BaseModel):
    movement_id: uuid.UUID
