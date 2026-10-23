"""Материалы (ADR-016 часть 5, S7a): справочник юрлица, единицы с коэффициентом, партии с
документами сертификата и ГТД, цена плана под правом procurement:read:prices."""

import uuid
from decimal import Decimal

from sqlalchemy import (
    Boolean,
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


class Material(BusinessBase):
    """Справочник материалов юрлица. Ведёт снабженец; код уникален среди живых записей."""

    __tablename__ = "material"

    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"))
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(300))
    base_unit: Mapped[str] = mapped_column(String(20))
    requires_certificate: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))

    __table_args__ = (
        UniqueConstraint("id", "legal_entity_id", name="uq_material_id_legal_entity_id"),
        Index(
            "uq_material_legal_entity_id_code",
            "legal_entity_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("btrim(code) <> ''", name="code_not_blank"),
        CheckConstraint("btrim(name) <> ''", name="name_not_blank"),
        CheckConstraint("btrim(base_unit) <> ''", name="base_unit_not_blank"),
    )


class MaterialUnit(BusinessBase):
    """Единица материала с коэффициентом к базовой: 1 мешок = 25 кг -> factor 25."""

    __tablename__ = "material_unit"

    material_id: Mapped[uuid.UUID] = mapped_column()
    legal_entity_id: Mapped[uuid.UUID] = mapped_column()
    unit: Mapped[str] = mapped_column(String(20))
    factor: Mapped[Decimal] = mapped_column(Numeric(18, 6))

    __table_args__ = (
        ForeignKeyConstraint(
            ["material_id", "legal_entity_id"],
            ["material.id", "material.legal_entity_id"],
            name="fk_material_unit_material_id_material",
        ),
        Index(
            "uq_material_unit_material_id_unit",
            "material_id",
            "unit",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("factor > 0", name="factor_positive"),
        CheckConstraint("btrim(unit) <> ''", name="unit_not_blank"),
    )


class MaterialBatch(BusinessBase):
    """Партия: прослеживается до материала. Сертификат и ГТД - ссылки на документы."""

    __tablename__ = "material_batch"

    material_id: Mapped[uuid.UUID] = mapped_column()
    legal_entity_id: Mapped[uuid.UUID] = mapped_column()
    batch_no: Mapped[str] = mapped_column(String(60))
    certificate_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document.id"), default=None
    )
    customs_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document.id"), default=None
    )
    country_of_origin: Mapped[str | None] = mapped_column(String(80), default=None)

    __table_args__ = (
        ForeignKeyConstraint(
            ["material_id", "legal_entity_id"],
            ["material.id", "material.legal_entity_id"],
            name="fk_material_batch_material_id_material",
        ),
        UniqueConstraint("id", "material_id", name="uq_material_batch_id_material_id"),
        Index(
            "uq_material_batch_material_id_batch_no",
            "material_id",
            "batch_no",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("btrim(batch_no) <> ''", name="batch_no_not_blank"),
    )


class MaterialPrice(BusinessBase):
    """Цена плана за базовую единицу. Строки видны только с procurement:read:prices (RLS)."""

    __tablename__ = "material_price"

    material_id: Mapped[uuid.UUID] = mapped_column()
    legal_entity_id: Mapped[uuid.UUID] = mapped_column()
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))

    __table_args__ = (
        ForeignKeyConstraint(
            ["material_id", "legal_entity_id"],
            ["material.id", "material.legal_entity_id"],
            name="fk_material_price_material_id_material",
        ),
        Index(
            "uq_material_price_material_id",
            "material_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("unit_price >= 0", name="unit_price_not_negative"),
    )
