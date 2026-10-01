"""project: lifecycle stage, plan dates, budget, customer, address, warranty stub

Revision ID: a9f3c2d8e471
Revises: d7c4e91a3b52
Create Date: 2026-10-02 12:00:00.000000

Миграции до d7c4e91a3b52 включительно заморожены (S2): здесь только новая ревизия. Права и политики
project не меняются: kut_app и kut_worker имеют табличные права (новые колонки входят), а у
kut_portal остаётся прежний явный список колонок, поэтому бюджет, заказчик и гарантия внешним
ролям закрыты, пока их не откроют намеренно.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9f3c2d8e471"
down_revision: str | Sequence[str] | None = "d7c4e91a3b52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Девять стадий ТЗ §4.1; девятая («гарантия») вне MVP, модель заложена.
STAGES = (
    "land_purchase",
    "concept",
    "design",
    "permits",
    "site_preparation",
    "construction",
    "commissioning",
    "handover",
    "warranty",
)

# Заказчик объекта - контрагент с типом customer. Правило в БД, а не только в API: иначе его
# обходит любой другой писатель (сид, воркер, будущие модули).
CUSTOMER_GUARD = """
CREATE FUNCTION app.project_customer_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.counterparty c
        WHERE c.id = NEW.customer_id AND c.deleted_at IS NULL AND 'customer' = ANY (c.types)
    ) THEN
        RAISE EXCEPTION 'project.customer_invalid' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$
"""


def upgrade() -> None:
    op.add_column(
        "project",
        sa.Column("lifecycle_stage", sa.String(), server_default="land_purchase", nullable=False),
    )
    op.add_column("project", sa.Column("planned_start", sa.Date(), nullable=True))
    op.add_column("project", sa.Column("planned_end", sa.Date(), nullable=True))
    op.add_column("project", sa.Column("planned_budget", sa.Numeric(18, 2), nullable=True))
    op.add_column(
        "project", sa.Column("budget_currency", sa.String(), server_default="KGS", nullable=False)
    )
    op.add_column("project", sa.Column("customer_id", sa.Uuid(), nullable=True))
    op.add_column("project", sa.Column("address", sa.String(), nullable=True))
    op.add_column("project", sa.Column("latitude", sa.Numeric(9, 6), nullable=True))
    op.add_column("project", sa.Column("longitude", sa.Numeric(9, 6), nullable=True))
    op.add_column("project", sa.Column("warranty_until", sa.Date(), nullable=True))

    stages = ", ".join(f"'{stage}'" for stage in STAGES)
    op.create_check_constraint(
        op.f("ck_project_lifecycle_stage_valid"), "project", f"lifecycle_stage IN ({stages})"
    )
    op.create_check_constraint(
        op.f("ck_project_planned_dates_ordered"),
        "project",
        "planned_end IS NULL OR planned_start IS NULL OR planned_end >= planned_start",
    )
    op.create_check_constraint(
        op.f("ck_project_budget_not_negative"),
        "project",
        "planned_budget IS NULL OR planned_budget >= 0",
    )
    op.create_check_constraint(
        op.f("ck_project_budget_currency_kgs"), "project", "budget_currency = 'KGS'"
    )
    op.create_check_constraint(
        op.f("ck_project_coordinates_valid"),
        "project",
        "(latitude IS NULL) = (longitude IS NULL)"
        " AND (latitude IS NULL OR (latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180))",
    )
    op.create_foreign_key(
        op.f("fk_project_customer_id_counterparty"),
        "project",
        "counterparty",
        ["customer_id"],
        ["id"],
    )
    op.create_index("ix_project_customer_id", "project", ["customer_id"], unique=False)

    op.execute(CUSTOMER_GUARD)
    op.execute(
        "CREATE TRIGGER project_customer_guard BEFORE INSERT OR UPDATE OF customer_id ON project"
        " FOR EACH ROW WHEN (NEW.customer_id IS NOT NULL)"
        " EXECUTE FUNCTION app.project_customer_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER project_customer_guard ON project")
    op.execute("DROP FUNCTION app.project_customer_guard()")
    op.drop_index("ix_project_customer_id", table_name="project")
    op.drop_constraint(op.f("fk_project_customer_id_counterparty"), "project", type_="foreignkey")
    for name in (
        "ck_project_coordinates_valid",
        "ck_project_budget_currency_kgs",
        "ck_project_budget_not_negative",
        "ck_project_planned_dates_ordered",
        "ck_project_lifecycle_stage_valid",
    ):
        op.drop_constraint(op.f(name), "project", type_="check")
    for column in (
        "warranty_until",
        "longitude",
        "latitude",
        "address",
        "customer_id",
        "budget_currency",
        "planned_budget",
        "planned_end",
        "planned_start",
        "lifecycle_stage",
    ):
        op.drop_column("project", column)
