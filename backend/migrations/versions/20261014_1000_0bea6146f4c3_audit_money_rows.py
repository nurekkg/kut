"""аудит: строки денежных таблиц видны только с денежным правом (F1, ADR-015 часть 3, раздел 13)

Revision ID: 0bea6146f4c3
Revises: b5e9d2f7c4a1
Create Date: 2026-10-14 10:00:00.000000

Журнал audit_log пишет old/new строки целиком. Суммы cost_entry и equipment_fuel.amount в нём
видны любому внутреннему пользователю с доступом к объекту и правом на таблицу журнала: политика
audit_log_equipment_module пускает всех с equipment:read, а политики денег закрывали только
equipment_rate, equipment_own_rate и contract. API журнал не читает, но SELECT у kut_app есть.

Исправление: RESTRICTIVE-политика на строки журнала этих таблиц - нужен equipment:read:rates
(то же право, что на сами проводки и ГСМ). Остальные политики журнала не меняются.
Откат убирает политику.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0bea6146f4c3"
down_revision: str | Sequence[str] | None = "b5e9d2f7c4a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CAN_READ_RATES = "(SELECT app.has_permission('equipment', 'read', 'rates'))"
MONEY_TABLES = ("cost_entry", "equipment_fuel")


def upgrade() -> None:
    not_money = " AND ".join(f"table_name IS DISTINCT FROM '{table}'" for table in MONEY_TABLES)
    op.execute(
        "CREATE POLICY audit_log_money_rows ON audit_log AS RESTRICTIVE FOR SELECT TO kut_app"
        f" USING (({not_money}) OR {CAN_READ_RATES})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS audit_log_money_rows ON audit_log")
