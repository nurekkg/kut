"""Склад и движение материалов (ADR-016 часть 5, S7a). Склады - только объектные (Q1).
Движение - append-only: пишут только функции склада (kut_auth); сторно - отдельной строкой."""

import uuid
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import BusinessBase


class Warehouse(BusinessBase):
    """Объектный склад. Тип central заложен в модели, но CHECK в БД его не допускает."""

    __tablename__ = "warehouse"

    project_id: Mapped[uuid.UUID] = mapped_column()
    legal_entity_id: Mapped[uuid.UUID] = mapped_column()
    kind: Mapped[str] = mapped_column(String(16), default="object")
    name: Mapped[str] = mapped_column(String(200))

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_warehouse_project_id_project",
        ),
        UniqueConstraint("id", "project_id", name="uq_warehouse_id_project_id"),
        Index(
            "uq_warehouse_project_id_name",
            "project_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("kind = 'object'", name="kind_object"),
        CheckConstraint("btrim(name) <> ''", name="name_not_blank"),
    )


class StockMovement(BusinessBase):
    """Движение материала в базовой единице. qty со знаком: приход +, расход -."""

    __tablename__ = "stock_movement"

    legal_entity_id: Mapped[uuid.UUID] = mapped_column()
    project_id: Mapped[uuid.UUID] = mapped_column()
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouse.id"))
    material_id: Mapped[uuid.UUID] = mapped_column()
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("material_batch.id"), default=None
    )
    kind: Mapped[str] = mapped_column(String(16))
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    unit: Mapped[str] = mapped_column(String(20))
    quantity_in_unit: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    factor: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document.id"), default=None)
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_version.id"), default=None
    )
    source_line_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_line.id"), default=None
    )
    project_node_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project_node.id"), default=None
    )
    work_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("work.id"), default=None)
    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id"), default=None
    )
    reversal_of_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stock_movement.id"), default=None
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_stock_movement_project_id_project",
        ),
        ForeignKeyConstraint(
            ["material_id", "legal_entity_id"],
            ["material.id", "material.legal_entity_id"],
            name="fk_stock_movement_material_id_material",
        ),
        Index("ix_stock_movement_warehouse_id_material_id", "warehouse_id", "material_id"),
        Index(
            "uq_stock_movement_receipt_source_line",
            "source_line_id",
            unique=True,
            postgresql_where=text("kind = 'receipt' AND source_line_id IS NOT NULL"),
        ),
        Index(
            "uq_stock_movement_adjustment_source",
            "source_line_id",
            unique=True,
            postgresql_where=text("kind = 'adjustment' AND source_line_id IS NOT NULL"),
        ),
        Index(
            "uq_stock_movement_reversal_of_id",
            "reversal_of_id",
            unique=True,
            postgresql_where=text("reversal_of_id IS NOT NULL"),
        ),
        CheckConstraint(
            "kind IN ('receipt', 'issue', 'reversal', 'adjustment')",
            name="kind_valid",
        ),
        CheckConstraint("qty <> 0", name="qty_not_zero"),
        CheckConstraint("quantity_in_unit > 0", name="quantity_positive"),
        CheckConstraint("factor > 0", name="factor_positive"),
        CheckConstraint(
            "(kind = 'receipt' AND qty > 0) OR (kind = 'issue' AND qty < 0)"
            " OR kind IN ('reversal', 'adjustment')",
            name="sign_matches_kind",
        ),
        CheckConstraint(
            "kind <> 'issue' OR (project_node_id IS NOT NULL AND responsible_user_id IS NOT NULL)",
            name="issue_node_and_responsible",
        ),
        CheckConstraint(
            "kind <> 'reversal' OR reversal_of_id IS NOT NULL",
            name="reversal_has_origin",
        ),
    )
