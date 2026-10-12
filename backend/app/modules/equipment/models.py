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
    contract_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contract.id"))

    __table_args__ = (
        CheckConstraint(f"ownership IN ({_in(OWNERSHIPS)})", name="ownership_valid"),
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
    __tablename__ = "equipment_rate"

    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contract.id"))
    unit: Mapped[str]
    with_operator: Mapped[bool] = mapped_column(Boolean)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    min_quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    valid_from: Mapped[date]

    __table_args__ = (
        CheckConstraint(f"unit IN ({_in(UNITS)})", name="unit_valid"),
        CheckConstraint("amount >= 0", name="amount_not_negative"),
        CheckConstraint(
            "min_quantity IS NULL OR min_quantity >= 0", name="min_quantity_not_negative"
        ),
        CheckConstraint("unit <> 'month' OR min_quantity IS NULL", name="month_has_no_minimum"),
        Index(
            "uq_equipment_rate_version",
            "contract_id",
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
