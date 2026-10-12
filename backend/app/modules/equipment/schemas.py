import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

UnitName = Literal["hour", "motor_hour", "shift", "trip", "tonne", "month"]
DocKind = Literal["passport", "technical_inspection", "insurance", "permit", "other"]
Ownership = Literal["own", "rented"]
Employment = Literal["staff", "lessor"]
MaintenanceKind = Literal["to", "repair"]
MaintenanceTarget = Literal["in_progress", "done", "cancelled"]
ContractTarget = Literal["active", "closed", "terminated"]
Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
MinQuantity = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]
HoursPerDay = Annotated[Decimal, Field(gt=0, le=24, max_digits=4, decimal_places=2)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- справочники -------------------------------------------------------------------------------


class TypeCreate(_Strict):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)
    default_unit: UnitName


class TypeOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    default_unit: str


# --- реестр ------------------------------------------------------------------------------------


class UnitCreate(_Strict):
    legal_entity_id: uuid.UUID
    equipment_type_id: uuid.UUID
    ownership: Ownership
    registry_number: str = Field(min_length=1, max_length=100)
    contract_id: uuid.UUID | None = None
    model: str | None = Field(default=None, max_length=200)
    year: int | None = Field(default=None, ge=1950, le=2100)


class UnitPatch(_Strict):
    """Меняются присланные поля. Договор можно снять (null), тип и номер - нет."""

    equipment_type_id: uuid.UUID | None = None
    registry_number: str | None = Field(default=None, min_length=1, max_length=100)
    contract_id: uuid.UUID | None = None
    model: str | None = Field(default=None, max_length=200)
    year: int | None = Field(default=None, ge=1950, le=2100)

    @model_validator(mode="after")
    def _no_nulls(self) -> Self:
        for field in ("equipment_type_id", "registry_number"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class UnitOut(BaseModel):
    id: uuid.UUID
    legal_entity_id: uuid.UUID
    equipment_type_id: uuid.UUID
    type_code: str
    ownership: str
    registry_number: str
    contract_id: uuid.UUID | None
    model: str | None
    year: int | None


class DocumentOut(BaseModel):
    id: uuid.UUID
    equipment_unit_id: uuid.UUID
    kind: str
    number: str
    valid_until: date
    version: int


class MaintenanceOut(BaseModel):
    id: uuid.UUID
    equipment_unit_id: uuid.UUID
    kind: str
    title: str
    planned_from: date
    planned_to: date | None
    status: str
    project_id: uuid.UUID | None
    done_at: datetime | None


class UnitListOut(UnitOut):
    """Строка реестра: статус на сегодня (ADR-014 часть 1, раздел 3)."""

    status: str


class UnitCard(UnitOut):
    """Карточка: статус на сегодня (вычисляется по видимым строкам), документы, ТО и ремонты."""

    status: str
    documents: list[DocumentOut]
    maintenance: list[MaintenanceOut]


class DocumentCreate(_Strict):
    kind: DocKind
    number: str = Field(min_length=1, max_length=100)
    valid_until: date


class MaintenanceCreate(_Strict):
    equipment_unit_id: uuid.UUID
    kind: MaintenanceKind
    title: str = Field(min_length=1, max_length=300)
    planned_from: date
    planned_to: date | None = None


class MaintenancePatch(_Strict):
    """Закрытие (done) принимает сумму: она уходит в журнал затрат, в ответе её нет."""

    status: MaintenanceTarget
    project_id: uuid.UUID | None = None
    amount: Money | None = None


# --- нормативы, договоры, тарифы, машинисты ----------------------------------------------------


class OwnRateCreate(_Strict):
    legal_entity_id: uuid.UUID
    equipment_type_id: uuid.UUID
    unit: UnitName
    amount: Money
    valid_from: date


class OwnRateOut(BaseModel):
    id: uuid.UUID
    legal_entity_id: uuid.UUID
    equipment_type_id: uuid.UUID
    unit: str
    amount: Decimal
    valid_from: date
    proposed_by: uuid.UUID
    approved_by: uuid.UUID | None


class ContractCreate(_Strict):
    legal_entity_id: uuid.UUID
    counterparty_id: uuid.UUID
    kind: Literal["rental_equipment"]
    number: str = Field(min_length=1, max_length=100)
    date_from: date
    date_to: date | None = None


class ContractPatch(_Strict):
    status: ContractTarget | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> Self:
        if self.status is None and "date_to" not in self.model_fields_set:
            raise ValueError("nothing to change")
        return self


class ContractOut(BaseModel):
    id: uuid.UUID
    legal_entity_id: uuid.UUID
    counterparty_id: uuid.UUID
    kind: str
    number: str
    date_from: date
    date_to: date | None
    status: str


class RateCreate(_Strict):
    unit: UnitName
    with_operator: bool
    amount: Money
    min_quantity: MinQuantity | None = None
    valid_from: date


class RateOut(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    unit: str
    with_operator: bool
    amount: Decimal
    min_quantity: Decimal | None
    valid_from: date


class CounterpartyRef(BaseModel):
    """Контрагент для формы договора: только имя и идентификатор (без реквизитов)."""

    id: uuid.UUID
    name: str


class OperatorCreate(_Strict):
    legal_entity_id: uuid.UUID
    full_name: str = Field(min_length=1, max_length=200)
    employment: Employment
    user_id: uuid.UUID | None = None
    counterparty_id: uuid.UUID | None = None


class OperatorOut(BaseModel):
    id: uuid.UUID
    legal_entity_id: uuid.UUID
    full_name: str
    employment: str
    user_id: uuid.UUID | None
    counterparty_id: uuid.UUID | None


# --- заявки и назначения -----------------------------------------------------------------------


class RequestCreate(_Strict):
    project_id: uuid.UUID
    node_id: uuid.UUID | None = None
    schedule_item_id: uuid.UUID | None = None
    equipment_type_id: uuid.UUID
    date_from: date
    date_to: date
    planned_hours_per_day: HoursPerDay


class RequestPatch(_Strict):
    """Правка черновика и отклонённой заявки. Узел и строка ГПР можно снять (null)."""

    node_id: uuid.UUID | None = None
    schedule_item_id: uuid.UUID | None = None
    equipment_type_id: uuid.UUID | None = None
    date_from: date | None = None
    date_to: date | None = None
    planned_hours_per_day: HoursPerDay | None = None

    @model_validator(mode="after")
    def _no_nulls(self) -> Self:
        for field in ("equipment_type_id", "date_from", "date_to", "planned_hours_per_day"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class ReasonIn(_Strict):
    reason: str = Field(min_length=1, max_length=500)


class RequestOut(BaseModel):
    project_name: str | None = None
    id: uuid.UUID
    project_id: uuid.UUID
    legal_entity_id: uuid.UUID
    node_id: uuid.UUID | None
    schedule_item_id: uuid.UUID | None
    equipment_type_id: uuid.UUID
    date_from: date
    date_to: date
    planned_hours_per_day: Decimal
    status: str
    reject_reason: str | None


class AssignmentCreate(_Strict):
    """Назначение по заявке (project и тип берутся из неё) или без заявки - с объектом."""

    request_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    equipment_unit_id: uuid.UUID
    node_id: uuid.UUID | None = None
    schedule_item_id: uuid.UUID | None = None
    date_from: date
    date_to: date
    planned_hours_per_day: HoursPerDay
    operator_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _object_known(self) -> Self:
        if self.request_id is None and self.project_id is None:
            raise ValueError("request_id or project_id is required")
        return self


class AssignmentPatch(_Strict):
    date_from: date | None = None
    date_to: date | None = None
    planned_hours_per_day: HoursPerDay | None = None
    operator_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _no_nulls(self) -> Self:
        for field in ("date_from", "date_to", "planned_hours_per_day"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class AssignmentOut(BaseModel):
    project_name: str | None = None
    id: uuid.UUID
    project_id: uuid.UUID
    legal_entity_id: uuid.UUID
    equipment_unit_id: uuid.UUID
    request_id: uuid.UUID | None
    node_id: uuid.UUID | None
    schedule_item_id: uuid.UUID | None
    date_from: date
    date_to: date
    planned_hours_per_day: Decimal
    operator_id: uuid.UUID | None
    status: str
    cancel_reason: str | None


class AssignmentWarning(BaseModel):
    """Не блокирует назначение (ADR-014 D6): прочий документ истекает внутри периода."""

    code: str
    kind: str
    valid_until: date


class AssignmentCreated(AssignmentOut):
    warnings: list[AssignmentWarning]


class AssignmentCheck(BaseModel):
    """Проверка назначения без записи: правила те же, что при создании; ответ - предупреждения."""

    warnings: list[AssignmentWarning]


# --- календарь ---------------------------------------------------------------------------------


class CalendarAssignment(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    date_from: date
    date_to: date
    planned_hours_per_day: Decimal
    status: str


class CalendarDay(BaseModel):
    date: date
    hours: Decimal


class CalendarMaintenance(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    planned_from: date
    planned_to: date | None
    status: str


class CalendarWarning(BaseModel):
    """Документ, который истёк или истекает внутри окна календаря."""

    kind: str
    valid_until: date


class CalendarUnit(BaseModel):
    id: uuid.UUID
    registry_number: str
    type_code: str
    ownership: str
    assignments: list[CalendarAssignment]
    days: list[CalendarDay]
    maintenance: list[CalendarMaintenance]
    warnings: list[CalendarWarning]


class CalendarOut(BaseModel):
    date_from: date
    date_to: date
    units: list[CalendarUnit]


# --- импорт реестра ----------------------------------------------------------------------------


class ImportIssue(BaseModel):
    row: int
    column: str
    code: str
    message: str


class ImportReport(BaseModel):
    batch_id: uuid.UUID | None
    status: Literal["checked", "committed", "rejected"]
    rows_total: int
    created: int
    updated: int
    unchanged: int
    errors: list[ImportIssue]


class BatchOut(BaseModel):
    id: uuid.UUID
    kind: str
    file_sha256: str
    status: str
    created_count: int
    updated_count: int
    unchanged_count: int
    error_count: int
    created_at: datetime


class ImportPayload(BaseModel):
    """Тело для Idempotency-Key: хеш файла и вид импорта."""

    file_sha256: str = Field(min_length=64, max_length=64)
