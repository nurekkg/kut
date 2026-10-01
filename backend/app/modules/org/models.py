from enum import StrEnum

from sqlalchemy import CheckConstraint, Index, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import BusinessBase


class CounterpartyType(StrEnum):
    CUSTOMER = "customer"
    SUBCONTRACTOR = "subcontractor"
    SUPPLIER = "supplier"
    INVESTOR = "investor"


_TYPES = ", ".join(f"'{kind.value}'" for kind in CounterpartyType)


class LegalEntity(BusinessBase):
    __tablename__ = "legal_entity"

    name: Mapped[str]
    tax_id: Mapped[str | None] = mapped_column(default=None)


class Counterparty(BusinessBase):
    __tablename__ = "counterparty"

    name: Mapped[str]
    tax_id: Mapped[str | None] = mapped_column(default=None)
    types: Mapped[list[str]] = mapped_column(ARRAY(String))

    __table_args__ = (
        CheckConstraint(
            f"cardinality(types) >= 1 AND types <@ ARRAY[{_TYPES}]::varchar[]", name="types_valid"
        ),
        Index("ix_counterparty_types", "types", postgresql_using="gin"),
    )
