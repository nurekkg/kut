from enum import StrEnum

from sqlalchemy import CheckConstraint, Index, String, text
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
    """Код юрлица (`code`) нужен номеру документа: <вид>-<код>-<год>-<n>.

    ADR-015 часть 1 раздел 3.4.
    """

    __tablename__ = "legal_entity"

    name: Mapped[str]
    tax_id: Mapped[str | None] = mapped_column(default=None)
    code: Mapped[str | None] = mapped_column(String(8), default=None)

    __table_args__ = (
        CheckConstraint("code IS NULL OR code ~ '^[A-Za-z0-9А-Яа-яЁё]{1,8}$'", name="code_valid"),
        Index(
            "uq_legal_entity_code",
            "code",
            unique=True,
            postgresql_where=text("code IS NOT NULL AND deleted_at IS NULL"),
        ),
    )


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
