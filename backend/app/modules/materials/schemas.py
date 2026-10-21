"""Схемы справочника материалов (ADR-016 часть 5, S7a). Цена - только при праве на цены закупок."""

import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class UnitIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    unit: str = Field(min_length=1, max_length=20)
    factor: Decimal = Field(gt=0, max_digits=18, decimal_places=6)


class UnitOut(BaseModel):
    id: uuid.UUID
    unit: str
    factor: Decimal


class MaterialIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    legal_entity_id: uuid.UUID
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=300)
    base_unit: str = Field(min_length=1, max_length=20)
    requires_certificate: bool = False


class MaterialPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=300)
    requires_certificate: bool | None = None
    active: bool | None = None


class MaterialOut(BaseModel):
    id: uuid.UUID
    legal_entity_id: uuid.UUID
    code: str
    name: str
    base_unit: str
    requires_certificate: bool
    active: bool
    units: list[UnitOut] = []
    # Цена плана - только с procurement:read:prices (RLS); без права - null.
    plan_price: Decimal | None = None


class BatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    batch_no: str = Field(min_length=1, max_length=60)
    certificate_document_id: uuid.UUID | None = None
    customs_document_id: uuid.UUID | None = None
    country_of_origin: str | None = Field(default=None, max_length=80)


class BatchOut(BaseModel):
    id: uuid.UUID
    material_id: uuid.UUID
    batch_no: str
    certificate_document_id: uuid.UUID | None
    customs_document_id: uuid.UUID | None
    country_of_origin: str | None


class PriceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_price: Decimal = Field(ge=0, max_digits=14, decimal_places=2)


class ImportIssue(BaseModel):
    row: int
    message: str


class ImportReport(BaseModel):
    created: int
    issues: list[ImportIssue]
