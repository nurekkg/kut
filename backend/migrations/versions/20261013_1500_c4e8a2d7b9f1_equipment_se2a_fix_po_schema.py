"""equipment: решения PO 2026-10-13 - срок тарифа (valid_to) и загрузивший чек заправки

Revision ID: c4e8a2d7b9f1
Revises: f2b7d4c9e1a6
Create Date: 2026-10-13 15:00:00.000000

- equipment_rate.valid_to: дата окончания тарифа, пусто - без окончания. Тариф с окончанием не выбирается
  на более поздние даты; удалить тариф можно только без проводок (определитель в ревизии правил).
  CHECK: окончание не раньше начала.
- equipment_fuel.receipt_uploaded_by: кто загрузил текущий чек. Ссылку на чек видят загрузивший и гейт
  read:rates. Чеки, загруженные до этой ревизии, загрузившего не имеют: ссылку видит только read:rates.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8a2d7b9f1"
down_revision: str | Sequence[str] | None = "f2b7d4c9e1a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("equipment_rate", sa.Column("valid_to", sa.Date(), nullable=True))
    op.create_check_constraint(
        op.f("ck_equipment_rate_valid_to_not_before_from"),
        "equipment_rate",
        "valid_to IS NULL OR valid_to >= valid_from",
    )
    op.add_column("equipment_fuel", sa.Column("receipt_uploaded_by", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_equipment_fuel_receipt_uploaded_by_user"),
        "equipment_fuel",
        "user",
        ["receipt_uploaded_by"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_equipment_fuel_receipt_uploaded_by_user"), "equipment_fuel", type_="foreignkey"
    )
    op.drop_column("equipment_fuel", "receipt_uploaded_by")
    op.drop_constraint(
        op.f("ck_equipment_rate_valid_to_not_before_from"), "equipment_rate", type_="check"
    )
    op.drop_column("equipment_rate", "valid_to")
