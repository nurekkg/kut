import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import BusinessBase

UNITS = ("hour", "motor_hour", "shift", "trip", "tonne", "month")
DOC_KINDS = ("passport", "technical_inspection", "insurance", "permit", "other")
OWNERSHIPS = ("own", "rented")
EMPLOYMENTS = ("staff", "lessor")
CONTRACT_KINDS = ("rental_equipment",)
CONTRACT_STATUSES = ("draft", "active", "closed", "terminated")
REQUEST_STATUSES = ("draft", "submitted", "assigned", "rejected", "cancelled", "closed")
ASSIGNMENT_STATUSES = ("planned", "finished", "cancelled")
MAINTENANCE_KINDS = ("to", "repair")
MAINTENANCE_STATUSES = ("planned", "in_progress", "done", "cancelled")
IMPORT_STATUSES = ("checked", "committed", "rejected")
NIL_UUID = "'00000000-0000-0000-0000-000000000000'::uuid"


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _dates_in_range(column_from: str, column_to: str) -> str:
    return f"{column_from} >= DATE '2000-01-01' AND {column_to} <= DATE '2100-12-31'"


class EquipmentType(BusinessBase):
    __tablename__ = "equipment_type"

    code: Mapped[str]
    name: Mapped[str]
    default_unit: Mapped[str]

    __table_args__ = (
        CheckConstraint("btrim(code) <> '' AND btrim(name) <> ''", name="code_and_name_not_blank"),
        CheckConstraint(f"default_unit IN ({_in(UNITS)})", name="default_unit_valid"),
        Index(
            "uq_equipment_type_code",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class Contract(BusinessBase):
    """Общая шапка договора (K = A). Сейчас один вид - аренда техники; условия в своих таблицах."""

    __tablename__ = "contract"

    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"))
    counterparty_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("counterparty.id"))
    kind: Mapped[str]
    number: Mapped[str]
    date_from: Mapped[date]
    date_to: Mapped[date | None]
    status: Mapped[str] = mapped_column(server_default="draft")

    __table_args__ = (
        CheckConstraint(f"kind IN ({_in(CONTRACT_KINDS)})", name="kind_valid"),
        CheckConstraint(f"status IN ({_in(CONTRACT_STATUSES)})", name="status_valid"),
        CheckConstraint("btrim(number) <> ''", name="number_not_blank"),
        CheckConstraint("date_to IS NULL OR date_to >= date_from", name="dates_ordered"),
        Index(
            "uq_contract_legal_entity_id_kind_number",
            "legal_entity_id",
            "kind",
            "number",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_contract_counterparty_id", "counterparty_id"),
    )


class EquipmentUnit(BusinessBase):
    __tablename__ = "equipment_unit"

    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"))
    equipment_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("equipment_type.id"))
    ownership: Mapped[str]
    registry_key: Mapped[str]
    registry_number: Mapped[str]
    model: Mapped[str | None]
    year: Mapped[int | None]
    contract_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contract.id"))

    __table_args__ = (
        CheckConstraint(f"ownership IN ({_in(OWNERSHIPS)})", name="ownership_valid"),
        CheckConstraint("year IS NULL OR year BETWEEN 1950 AND 2100", name="year_range"),
        CheckConstraint(
            "btrim(registry_key) <> '' AND btrim(registry_number) <> ''",
            name="registry_not_blank",
        ),
        CheckConstraint(
            "(ownership = 'own' AND contract_id IS NULL)"
            " OR (ownership = 'rented' AND contract_id IS NOT NULL)",
            name="contract_matches_ownership",
        ),
        Index(
            "uq_equipment_unit_legal_entity_id_registry_key",
            "legal_entity_id",
            "registry_key",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_equipment_unit_equipment_type_id", "equipment_type_id"),
        Index("ix_equipment_unit_contract_id", "contract_id"),
    )


class EquipmentOwnRate(BusinessBase):
    __tablename__ = "equipment_own_rate"

    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"))
    equipment_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("equipment_type.id"))
    unit: Mapped[str]
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    valid_from: Mapped[date]
    proposed_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"))

    __table_args__ = (
        CheckConstraint(f"unit IN ({_in(UNITS)})", name="unit_valid"),
        CheckConstraint("amount >= 0", name="amount_not_negative"),
        CheckConstraint(
            "approved_by IS NULL OR approved_by <> proposed_by",
            name="approver_is_not_proposer",
        ),
        Index(
            "uq_equipment_own_rate_version",
            "legal_entity_id",
            "equipment_type_id",
            "unit",
            "valid_from",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class EquipmentRate(BusinessBase):
    """Тариф договора аренды. Уровень: единица, тип или договор (по умолчанию, оба поля пусты)."""

    __tablename__ = "equipment_rate"

    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contract.id"))
    equipment_unit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("equipment_unit.id"))
    equipment_type_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("equipment_type.id"))
    unit: Mapped[str]
    with_operator: Mapped[bool] = mapped_column(Boolean)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    min_quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    valid_from: Mapped[date]
    valid_to: Mapped[date | None]

    __table_args__ = (
        CheckConstraint(f"unit IN ({_in(UNITS)})", name="unit_valid"),
        CheckConstraint("amount >= 0", name="amount_not_negative"),
        CheckConstraint(
            "min_quantity IS NULL OR min_quantity >= 0", name="min_quantity_not_negative"
        ),
        CheckConstraint("unit <> 'month' OR min_quantity IS NULL", name="month_has_no_minimum"),
        CheckConstraint("equipment_unit_id IS NULL OR equipment_type_id IS NULL", name="one_level"),
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from", name="valid_to_not_before_from"
        ),
        Index(
            "uq_equipment_rate_level_version",
            "contract_id",
            text(f"COALESCE(equipment_unit_id, {NIL_UUID})"),
            text(f"COALESCE(equipment_type_id, {NIL_UUID})"),
            "unit",
            "with_operator",
            "valid_from",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class EquipmentOperator(BusinessBase):
    __tablename__ = "equipment_operator"

    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"))
    full_name: Mapped[str]
    employment: Mapped[str]
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"))
    counterparty_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("counterparty.id"))

    __table_args__ = (
        CheckConstraint(f"employment IN ({_in(EMPLOYMENTS)})", name="employment_valid"),
        CheckConstraint("btrim(full_name) <> ''", name="full_name_not_blank"),
        CheckConstraint(
            "(employment = 'staff' AND user_id IS NOT NULL AND counterparty_id IS NULL)"
            " OR (employment = 'lessor' AND user_id IS NULL AND counterparty_id IS NOT NULL)",
            name="employment_matches_account",
        ),
        Index(
            "uq_equipment_operator_user_id",
            "user_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND user_id IS NOT NULL"),
        ),
    )


class EquipmentRequest(BusinessBase):
    __tablename__ = "equipment_request"

    project_id: Mapped[uuid.UUID]
    legal_entity_id: Mapped[uuid.UUID]
    node_id: Mapped[uuid.UUID | None]
    schedule_item_id: Mapped[uuid.UUID | None]
    equipment_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("equipment_type.id"))
    date_from: Mapped[date]
    date_to: Mapped[date]
    planned_hours_per_day: Mapped[Decimal] = mapped_column(Numeric(4, 2))
    status: Mapped[str] = mapped_column(server_default="draft")
    reject_reason: Mapped[str | None]

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"], ["project.id", "project.legal_entity_id"]
        ),
        ForeignKeyConstraint(
            ["project_id", "node_id"], ["project_node.project_id", "project_node.id"]
        ),
        ForeignKeyConstraint(
            ["project_id", "schedule_item_id"], ["schedule_item.project_id", "schedule_item.id"]
        ),
        UniqueConstraint("project_id", "id", name="uq_equipment_request_project_id_id"),
        CheckConstraint(f"status IN ({_in(REQUEST_STATUSES)})", name="status_valid"),
        CheckConstraint(
            "planned_hours_per_day > 0 AND planned_hours_per_day <= 24", name="hours_in_day"
        ),
        CheckConstraint("date_to >= date_from", name="dates_ordered"),
        CheckConstraint(_dates_in_range("date_from", "date_to"), name="dates_in_range"),
        CheckConstraint(
            "status <> 'rejected' OR btrim(reject_reason) <> ''", name="reject_reason_required"
        ),
        Index("ix_equipment_request_project_id", "project_id"),
        Index("ix_equipment_request_equipment_type_id", "equipment_type_id"),
    )


class EquipmentAssignment(BusinessBase):
    __tablename__ = "equipment_assignment"

    project_id: Mapped[uuid.UUID]
    legal_entity_id: Mapped[uuid.UUID]
    equipment_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("equipment_unit.id"))
    request_id: Mapped[uuid.UUID | None]
    node_id: Mapped[uuid.UUID | None]
    schedule_item_id: Mapped[uuid.UUID | None]
    date_from: Mapped[date]
    date_to: Mapped[date]
    planned_hours_per_day: Mapped[Decimal] = mapped_column(Numeric(4, 2))
    operator_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("equipment_operator.id"))
    status: Mapped[str] = mapped_column(server_default="planned")
    cancel_reason: Mapped[str | None]

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"], ["project.id", "project.legal_entity_id"]
        ),
        ForeignKeyConstraint(
            ["project_id", "node_id"], ["project_node.project_id", "project_node.id"]
        ),
        ForeignKeyConstraint(
            ["project_id", "schedule_item_id"], ["schedule_item.project_id", "schedule_item.id"]
        ),
        ForeignKeyConstraint(
            ["project_id", "request_id"], ["equipment_request.project_id", "equipment_request.id"]
        ),
        CheckConstraint(f"status IN ({_in(ASSIGNMENT_STATUSES)})", name="status_valid"),
        CheckConstraint(
            "planned_hours_per_day > 0 AND planned_hours_per_day <= 24", name="hours_in_day"
        ),
        CheckConstraint("date_to >= date_from", name="dates_ordered"),
        CheckConstraint(_dates_in_range("date_from", "date_to"), name="dates_in_range"),
        CheckConstraint(
            "status <> 'cancelled' OR btrim(cancel_reason) <> ''", name="cancel_reason_required"
        ),
        Index(
            "ix_equipment_assignment_equipment_unit_id_date_from", "equipment_unit_id", "date_from"
        ),
        Index("ix_equipment_assignment_project_id", "project_id"),
        Index("ix_equipment_assignment_operator_id", "operator_id"),
    )


class EquipmentMaintenance(BusinessBase):
    __tablename__ = "equipment_maintenance"

    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"))
    equipment_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("equipment_unit.id"))
    kind: Mapped[str]
    title: Mapped[str]
    planned_from: Mapped[date]
    planned_to: Mapped[date | None]
    status: Mapped[str] = mapped_column(server_default="planned")
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("project.id"))
    done_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint(f"kind IN ({_in(MAINTENANCE_KINDS)})", name="kind_valid"),
        CheckConstraint(f"status IN ({_in(MAINTENANCE_STATUSES)})", name="status_valid"),
        CheckConstraint("btrim(title) <> ''", name="title_not_blank"),
        CheckConstraint("planned_to IS NULL OR planned_to >= planned_from", name="dates_ordered"),
        CheckConstraint("status <> 'done' OR done_at IS NOT NULL", name="done_has_time"),
        Index("ix_equipment_maintenance_equipment_unit_id", "equipment_unit_id"),
    )


class EquipmentDocument(BusinessBase):
    """Версии документа техники не удаляются и не меняются: только вставка (ТЗ §2.2)."""

    __tablename__ = "equipment_document"

    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"))
    equipment_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("equipment_unit.id"))
    kind: Mapped[str]
    number: Mapped[str]
    valid_until: Mapped[date]
    version: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint(
            "equipment_unit_id",
            "kind",
            "version",
            name="uq_equipment_document_equipment_unit_id_kind_version",
        ),
        CheckConstraint(f"kind IN ({_in(DOC_KINDS)})", name="kind_valid"),
        CheckConstraint("btrim(number) <> ''", name="number_not_blank"),
        CheckConstraint("version >= 1", name="version_positive"),
    )


class EquipmentImportBatch(BusinessBase):
    """Журнал импорта: только вставка. Каждая попытка (проверка или загрузка) - новая строка."""

    __tablename__ = "equipment_import_batch"

    kind: Mapped[str]
    file_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str]
    created_count: Mapped[int] = mapped_column(Integer)
    updated_count: Mapped[int] = mapped_column(Integer)
    unchanged_count: Mapped[int] = mapped_column(Integer)
    error_count: Mapped[int] = mapped_column(Integer)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))

    __table_args__ = (
        CheckConstraint("kind = 'units'", name="kind_valid"),
        CheckConstraint(f"status IN ({_in(IMPORT_STATUSES)})", name="status_valid"),
        CheckConstraint("char_length(file_sha256) = 64", name="sha256_length"),
        CheckConstraint(
            "created_count >= 0 AND updated_count >= 0 AND unchanged_count >= 0"
            " AND error_count >= 0",
            name="counts_not_negative",
        ),
    )


SHIFT_STATUSES = ("draft", "submitted", "confirmed", "rejected")
SHIFT_SOURCES = ("manual",)
CATEGORIES = (
    "equipment_own",
    "equipment_rental",
    "equipment_fixed",
    "equipment_min_payment",
    "equipment_fuel",
    "equipment_repair",
)
POSTING_SOURCES = ("equipment_shift", "equipment_fuel", "equipment_maintenance")


def _date_in_range(column: str) -> str:
    return f"{column} >= DATE '2000-01-01' AND {column} <= DATE '2100-12-31'"


class EquipmentShift(BusinessBase):
    """Путевой лист (M = C): единица × день × объект × машинист. Проводка - в журнале затрат."""

    __tablename__ = "equipment_shift"

    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"))
    project_id: Mapped[uuid.UUID]
    equipment_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("equipment_unit.id"))
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("equipment_assignment.id"))
    work_date: Mapped[date]
    operator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("equipment_operator.id"))
    hours: Mapped[Decimal] = mapped_column(Numeric(4, 2))
    odometer_from: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    odometer_to: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    trips: Mapped[int | None] = mapped_column(Integer)
    tonnes: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    downtime_hours: Mapped[Decimal] = mapped_column(Numeric(4, 2), server_default=text("0"))
    downtime_reason: Mapped[str | None]
    source: Mapped[str] = mapped_column(server_default=text("'manual'"))
    status: Mapped[str] = mapped_column(server_default=text("'draft'"))
    reject_reason: Mapped[str | None]
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"))
    confirmed_at: Mapped[datetime | None]
    posting_version: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    lines_revision: Mapped[int] = mapped_column(Integer, server_default=text("0"))

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"], ["project.id", "project.legal_entity_id"]
        ),
        UniqueConstraint("project_id", "id", name="uq_equipment_shift_project_id_id"),
        CheckConstraint(f"status IN ({_in(SHIFT_STATUSES)})", name="status_valid"),
        CheckConstraint(f"source IN ({_in(SHIFT_SOURCES)})", name="source_valid"),
        CheckConstraint("hours > 0 AND hours <= 24", name="hours_in_day"),
        CheckConstraint("downtime_hours >= 0 AND downtime_hours <= hours", name="downtime_valid"),
        CheckConstraint(
            "downtime_hours = 0 OR btrim(coalesce(downtime_reason, '')) <> ''",
            name="downtime_reason_required",
        ),
        CheckConstraint(
            "(odometer_from IS NULL OR odometer_from >= 0)"
            " AND (odometer_to IS NULL OR (odometer_from IS NOT NULL"
            " AND odometer_to >= odometer_from))",
            name="odometer_valid",
        ),
        CheckConstraint("trips IS NULL OR trips >= 0", name="trips_not_negative"),
        CheckConstraint("tonnes IS NULL OR tonnes >= 0", name="tonnes_not_negative"),
        CheckConstraint(_date_in_range("work_date"), name="dates_in_range"),
        CheckConstraint(
            "status <> 'rejected' OR btrim(coalesce(reject_reason, '')) <> ''",
            name="reject_reason_required",
        ),
        CheckConstraint(
            "(status = 'confirmed') = (confirmed_at IS NOT NULL)", name="confirmed_fields"
        ),
        CheckConstraint(
            "confirmed_by IS NULL OR created_by IS NULL OR confirmed_by <> created_by",
            name="confirmer_is_not_author",
        ),
        CheckConstraint(
            "posting_version >= 0 AND lines_revision >= 0", name="counters_not_negative"
        ),
        Index(
            "uq_equipment_shift_key",
            "equipment_unit_id",
            "work_date",
            "project_id",
            "operator_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_equipment_shift_project_id", "project_id"),
        Index("ix_equipment_shift_equipment_unit_id_work_date", "equipment_unit_id", "work_date"),
        Index("ix_equipment_shift_operator_id", "operator_id"),
        Index("ix_equipment_shift_assignment_id", "assignment_id"),
    )


class EquipmentShiftLine(BusinessBase):
    """Разнесение листа по узлам и работам. Сумма строк равна итогу листа (триггер на COMMIT)."""

    __tablename__ = "equipment_shift_line"

    shift_id: Mapped[uuid.UUID]
    project_id: Mapped[uuid.UUID]
    node_id: Mapped[uuid.UUID | None]
    schedule_item_id: Mapped[uuid.UUID | None]
    hours: Mapped[Decimal] = mapped_column(Numeric(4, 2))
    trips: Mapped[int | None] = mapped_column(Integer)
    tonnes: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "shift_id"], ["equipment_shift.project_id", "equipment_shift.id"]
        ),
        ForeignKeyConstraint(
            ["project_id", "node_id"], ["project_node.project_id", "project_node.id"]
        ),
        ForeignKeyConstraint(
            ["project_id", "schedule_item_id"], ["schedule_item.project_id", "schedule_item.id"]
        ),
        CheckConstraint("hours > 0 AND hours <= 24", name="hours_in_day"),
        CheckConstraint("trips IS NULL OR trips >= 0", name="trips_not_negative"),
        CheckConstraint("tonnes IS NULL OR tonnes >= 0", name="tonnes_not_negative"),
        Index("ix_equipment_shift_line_shift_id", "shift_id"),
    )


class EquipmentFuel(BusinessBase):
    """Заправка (ГСМ) строкой. Строка не меняется: сторно - отдельной строкой журнала.

    Сумма (amount) - деферред: приложение колонку не читает, её даёт журнал затрат (гейт read:rates,
    ревизия f2b7d4c9e1a6). Заправка в листе (shift_id) попадает в журнал при проводке листа.
    """

    __tablename__ = "equipment_fuel"

    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"))
    project_id: Mapped[uuid.UUID | None]
    equipment_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("equipment_unit.id"))
    fuel_date: Mapped[date]
    liters: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), deferred=True)
    source: Mapped[str] = mapped_column(server_default=text("'manual'"))
    reversed_at: Mapped[datetime | None]
    reversed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"))
    reverse_reason: Mapped[str | None]
    shift_id: Mapped[uuid.UUID | None]
    receipt_key: Mapped[str | None]
    receipt_content_type: Mapped[str | None]
    receipt_uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"))

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"], ["project.id", "project.legal_entity_id"]
        ),
        ForeignKeyConstraint(
            ["project_id", "shift_id"],
            ["equipment_shift.project_id", "equipment_shift.id"],
            name="fk_equipment_fuel_project_id_shift_id_equipment_shift",
        ),
        CheckConstraint("shift_id IS NULL OR project_id IS NOT NULL", name="shift_has_project"),
        CheckConstraint(
            "shift_id IS NULL OR (reversed_at IS NULL AND reverse_reason IS NULL)",
            name="shift_not_reversed",
        ),
        CheckConstraint(
            "(receipt_key IS NULL) = (receipt_content_type IS NULL)", name="receipt_pair"
        ),
        CheckConstraint(
            "receipt_key IS NULL OR btrim(receipt_key) <> ''", name="receipt_key_not_blank"
        ),
        CheckConstraint("amount >= 0", name="amount_not_negative"),
        CheckConstraint("liters IS NULL OR liters >= 0", name="liters_not_negative"),
        CheckConstraint(f"source IN ({_in(SHIFT_SOURCES)})", name="source_valid"),
        CheckConstraint(_date_in_range("fuel_date"), name="dates_in_range"),
        CheckConstraint(
            "(reversed_at IS NULL) = (reversed_by IS NULL)"
            " AND (reversed_at IS NULL) = (reverse_reason IS NULL)",
            name="reversal_complete",
        ),
        CheckConstraint(
            "reverse_reason IS NULL OR btrim(reverse_reason) <> ''",
            name="reverse_reason_not_blank",
        ),
        Index("ix_equipment_fuel_equipment_unit_id_fuel_date", "equipment_unit_id", "fuel_date"),
        Index("ix_equipment_fuel_project_id", "project_id"),
        Index("ix_equipment_fuel_shift_id", "shift_id"),
    )


class EquipmentDelegate(BusinessBase):
    """Делегат подтверждения листов объекта на период (Q5 A)."""

    __tablename__ = "equipment_delegate"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id"))
    delegate_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"))
    date_from: Mapped[date]
    date_to: Mapped[date]
    reason: Mapped[str]
    revoked_at: Mapped[datetime | None]
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"))

    __table_args__ = (
        CheckConstraint("date_to >= date_from", name="dates_ordered"),
        CheckConstraint(
            _date_in_range("date_from") + " AND " + _date_in_range("date_to"),
            name="dates_in_range",
        ),
        CheckConstraint("btrim(reason) <> ''", name="reason_not_blank"),
        CheckConstraint("(revoked_at IS NULL) = (revoked_by IS NULL)", name="revocation_complete"),
        Index("ix_equipment_delegate_project_id", "project_id"),
        Index("ix_equipment_delegate_delegate_user_id", "delegate_user_id"),
    )


class CostEntry(BusinessBase):
    """Журнал затрат (ADR-014 часть 2 §3): запись - определителями; UPDATE и DELETE запрещены."""

    __tablename__ = "cost_entry"

    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"))
    project_id: Mapped[uuid.UUID | None]
    node_id: Mapped[uuid.UUID | None]
    schedule_item_id: Mapped[uuid.UUID | None]
    equipment_unit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("equipment_unit.id"))
    period: Mapped[date]
    category: Mapped[str]
    source_type: Mapped[str]
    source_id: Mapped[uuid.UUID]
    posting_version: Mapped[int] = mapped_column(Integer)
    line_no: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    tariff_unit: Mapped[str | None]
    rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    reversal_of_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cost_entry.id"))

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"], ["project.id", "project.legal_entity_id"]
        ),
        ForeignKeyConstraint(
            ["project_id", "node_id"], ["project_node.project_id", "project_node.id"]
        ),
        ForeignKeyConstraint(
            ["project_id", "schedule_item_id"], ["schedule_item.project_id", "schedule_item.id"]
        ),
        CheckConstraint(f"category IN ({_in(CATEGORIES)})", name="category_valid"),
        CheckConstraint(f"source_type IN ({_in(POSTING_SOURCES)})", name="source_type_valid"),
        CheckConstraint(
            f"tariff_unit IS NULL OR tariff_unit IN ({_in(UNITS)})", name="tariff_unit_valid"
        ),
        CheckConstraint("posting_version >= 1", name="posting_version_positive"),
        CheckConstraint("line_no >= 1", name="line_no_positive"),
        CheckConstraint(
            "(reversal_of_id IS NULL AND amount >= 0)"
            " OR (reversal_of_id IS NOT NULL AND amount <= 0)",
            name="amount_sign",
        ),
        CheckConstraint(
            "quantity IS NULL OR (reversal_of_id IS NULL AND quantity >= 0)"
            " OR (reversal_of_id IS NOT NULL AND quantity <= 0)",
            name="quantity_sign",
        ),
        CheckConstraint("rate IS NULL OR rate >= 0", name="rate_not_negative"),
        Index(
            "uq_cost_entry_source_line",
            "source_type",
            "source_id",
            "posting_version",
            "line_no",
            unique=True,
            postgresql_where=text("reversal_of_id IS NULL"),
        ),
        Index(
            "uq_cost_entry_reversal_of_id",
            "reversal_of_id",
            unique=True,
            postgresql_where=text("reversal_of_id IS NOT NULL"),
        ),
        Index("ix_cost_entry_project_id_period", "project_id", "period"),
        Index("ix_cost_entry_legal_entity_id", "legal_entity_id"),
        Index("ix_cost_entry_source_id", "source_type", "source_id"),
    )
