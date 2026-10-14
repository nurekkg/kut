"""equipment: права на чек заправки - запись только через определитель (решения PO 2026-10-13, пункт 8)

Revision ID: e7a2c9f4d8b1
Revises: d1f6b3e9a5c2
Create Date: 2026-10-13 17:00:00.000000

- kut_app не меняет чек заправки напрямую (колонки receipt_key, receipt_content_type отозваны):
  запись идёт через app.equipment_fuel_attach_receipt, которая проверяет право, статус и префикс ключа.
- kut_auth (владелец определителей) получает UPDATE на колонки чека, загрузившего и служебные поля.
- kut_app видит receipt_uploaded_by (ссылку на свой чек показывает сервис).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e7a2c9f4d8b1"
down_revision: str | Sequence[str] | None = "d1f6b3e9a5c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RECEIPT_COLUMNS = ("receipt_key", "receipt_content_type")
DEFINER_COLUMNS = (*RECEIPT_COLUMNS, "receipt_uploaded_by", "updated_at", "updated_by")


def upgrade() -> None:
    op.execute(f"REVOKE UPDATE ({', '.join(RECEIPT_COLUMNS)}) ON equipment_fuel FROM kut_app")
    op.execute("GRANT SELECT (receipt_uploaded_by) ON equipment_fuel TO kut_app")
    op.execute(f"GRANT UPDATE ({', '.join(DEFINER_COLUMNS)}) ON equipment_fuel TO kut_auth")


def downgrade() -> None:
    op.execute(f"REVOKE UPDATE ({', '.join(DEFINER_COLUMNS)}) ON equipment_fuel FROM kut_auth")
    op.execute("REVOKE SELECT (receipt_uploaded_by) ON equipment_fuel FROM kut_app")
    op.execute(f"GRANT UPDATE ({', '.join(RECEIPT_COLUMNS)}) ON equipment_fuel TO kut_app")
