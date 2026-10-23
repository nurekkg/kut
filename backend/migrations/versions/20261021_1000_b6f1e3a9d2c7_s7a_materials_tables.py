"""S7a: материалы, склад, партии, цены закупок, движение (append-only), поля позиций, вид ИНВ

Revision ID: b6f1e3a9d2c7
Revises: c4e8b1d6f2a9
Create Date: 2026-10-21 10:00:00

ADR-016 (часть 5): справочник юрлица с единицами и флагом сертификата, склады только объектные,
партии с документами сертификата и ГТД, цены плана под procurement:read:prices, движение
stock_movement без UPDATE и DELETE (сторно - отдельной строкой). Позиции версии получают
материал, партию, склад и ответственного. Вид inventory_act заводится сейчас, логика - S7c.
Логика (функции, триггеры, маршруты, права на документы) - в следующей ревизии.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from migrations.helpers import (
    business_table,
    create_audit_triggers,
    force_rls,
    grant,
    narrow_check,
)

revision: str = "b6f1e3a9d2c7"
down_revision: str | Sequence[str] | None = "c4e8b1d6f2a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Виды документов после S7a: прежние списки (миграция 20261014_1100) плюс inventory_act.
KINDS_NEW = (
    "addendum",
    "commissioning_act",
    "contract_customer",
    "contract_rental",
    "contract_subcontract",
    "contract_supply",
    "equipment_insurance",
    "equipment_other",
    "equipment_passport",
    "equipment_permit",
    "equipment_technical_inspection",
    "estimate_local",
    "estimate_summary",
    "hidden_works_act",
    "invoice_esf",
    "inventory_act",
    "ird",
    "ks2",
    "ks3",
    "ks6",
    "m7_act",
    "material_request",
    "waybill_internal",
    "waybill_m15",
    "waybill_torg12",
)
OBJECT_NEW = (
    "commissioning_act",
    "contract_customer",
    "contract_subcontract",
    "estimate_local",
    "estimate_summary",
    "hidden_works_act",
    "inventory_act",
    "ird",
    "ks2",
    "ks3",
    "ks6",
    "material_request",
)
LEGAL = (
    "addendum",
    "contract_rental",
    "contract_supply",
    "equipment_insurance",
    "equipment_other",
    "equipment_passport",
    "equipment_permit",
    "equipment_technical_inspection",
    "invoice_esf",
)
LINE_KINDS_NEW = (
    "ks2",
    "ks3",
    "ks6",
    "estimate_local",
    "estimate_summary",
    "waybill_torg12",
    "waybill_m15",
    "m7_act",
    "waybill_internal",
    "material_request",
    "inventory_act",
)
ROUTE_CODES = "'money', 'other', 'warehouse', 'procurement', 'inventory'"


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _scope_check() -> str:
    return (
        f"CASE WHEN kind IN ({_in(OBJECT_NEW)}) THEN project_id IS NOT NULL"
        f" WHEN kind IN ({_in(LEGAL)}) THEN project_id IS NULL ELSE true END"
    )


def upgrade() -> None:
    business_table(
        "material",
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("base_unit", sa.String(20), nullable=False),
        sa.Column("requires_certificate", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"], ["legal_entity.id"], name="fk_material_legal_entity_id"
        ),
        sa.UniqueConstraint("id", "legal_entity_id", name="uq_material_id_legal_entity_id"),
        sa.CheckConstraint("btrim(code) <> ''", name=op.f("ck_material_code_not_blank")),
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_material_name_not_blank")),
        sa.CheckConstraint("btrim(base_unit) <> ''", name=op.f("ck_material_base_unit_not_blank")),
    )
    op.create_index(
        "uq_material_legal_entity_id_code",
        "material",
        ["legal_entity_id", "code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    business_table(
        "material_unit",
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("factor", sa.Numeric(18, 6), nullable=False),
        sa.ForeignKeyConstraint(
            ["material_id", "legal_entity_id"],
            ["material.id", "material.legal_entity_id"],
            name="fk_material_unit_material_id_material",
        ),
        sa.CheckConstraint("factor > 0", name=op.f("ck_material_unit_factor_positive")),
        sa.CheckConstraint("btrim(unit) <> ''", name=op.f("ck_material_unit_unit_not_blank")),
    )
    op.create_index(
        "uq_material_unit_material_id_unit",
        "material_unit",
        ["material_id", "unit"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    business_table(
        "warehouse",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_warehouse_project_id_project",
        ),
        sa.UniqueConstraint("id", "project_id", name="uq_warehouse_id_project_id"),
        sa.CheckConstraint("kind = 'object'", name=op.f("ck_warehouse_kind_object")),
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_warehouse_name_not_blank")),
    )
    op.create_index(
        "uq_warehouse_project_id_name",
        "warehouse",
        ["project_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    business_table(
        "material_batch",
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("batch_no", sa.String(60), nullable=False),
        sa.Column("certificate_document_id", sa.Uuid(), nullable=True),
        sa.Column("customs_document_id", sa.Uuid(), nullable=True),
        sa.Column("country_of_origin", sa.String(80), nullable=True),
        sa.ForeignKeyConstraint(
            ["material_id", "legal_entity_id"],
            ["material.id", "material.legal_entity_id"],
            name="fk_material_batch_material_id_material",
        ),
        sa.ForeignKeyConstraint(
            ["certificate_document_id"],
            ["document.id"],
            name="fk_material_batch_certificate_document_id_document",
        ),
        sa.ForeignKeyConstraint(
            ["customs_document_id"],
            ["document.id"],
            name="fk_material_batch_customs_document_id_document",
        ),
        sa.UniqueConstraint("id", "material_id", name="uq_material_batch_id_material_id"),
        sa.CheckConstraint(
            "btrim(batch_no) <> ''", name=op.f("ck_material_batch_batch_no_not_blank")
        ),
    )
    op.create_index(
        "uq_material_batch_material_id_batch_no",
        "material_batch",
        ["material_id", "batch_no"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    business_table(
        "material_price",
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("unit_price", sa.Numeric(14, 2), nullable=False),
        sa.ForeignKeyConstraint(
            ["material_id", "legal_entity_id"],
            ["material.id", "material.legal_entity_id"],
            name="fk_material_price_material_id_material",
        ),
        sa.CheckConstraint(
            "unit_price >= 0", name=op.f("ck_material_price_unit_price_not_negative")
        ),
    )
    op.create_index(
        "uq_material_price_material_id",
        "material_price",
        ["material_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    business_table(
        "stock_movement",
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("qty", sa.Numeric(18, 4), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("quantity_in_unit", sa.Numeric(18, 4), nullable=False),
        sa.Column("factor", sa.Numeric(18, 6), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("version_id", sa.Uuid(), nullable=True),
        sa.Column("source_line_id", sa.Uuid(), nullable=True),
        sa.Column("project_node_id", sa.Uuid(), nullable=True),
        sa.Column("work_id", sa.Uuid(), nullable=True),
        sa.Column("responsible_user_id", sa.Uuid(), nullable=True),
        sa.Column("reversal_of_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_stock_movement_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["material_id", "legal_entity_id"],
            ["material.id", "material.legal_entity_id"],
            name="fk_stock_movement_material_id_material",
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"], ["warehouse.id"], name="fk_stock_movement_warehouse_id_warehouse"
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["material_batch.id"], name="fk_stock_movement_batch_id_material_batch"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["document.id"], name="fk_stock_movement_document_id_document"
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["document_version.id"],
            name="fk_stock_movement_version_id_document_version",
        ),
        sa.ForeignKeyConstraint(
            ["source_line_id"],
            ["document_line.id"],
            name="fk_stock_movement_source_line_id_document_line",
        ),
        sa.ForeignKeyConstraint(
            ["project_node_id"],
            ["project_node.id"],
            name="fk_stock_movement_project_node_id_project_node",
        ),
        sa.ForeignKeyConstraint(["work_id"], ["work.id"], name="fk_stock_movement_work_id_work"),
        sa.ForeignKeyConstraint(
            ["responsible_user_id"], ["user.id"], name="fk_stock_movement_responsible_user_id_user"
        ),
        sa.ForeignKeyConstraint(
            ["reversal_of_id"],
            ["stock_movement.id"],
            name="fk_stock_movement_reversal_of_id_stock_movement",
        ),
        sa.CheckConstraint(
            "kind IN ('receipt', 'issue', 'reversal', 'adjustment')",
            name=op.f("ck_stock_movement_kind_valid"),
        ),
        sa.CheckConstraint("qty <> 0", name=op.f("ck_stock_movement_qty_not_zero")),
        sa.CheckConstraint(
            "quantity_in_unit > 0", name=op.f("ck_stock_movement_quantity_positive")
        ),
        sa.CheckConstraint("factor > 0", name=op.f("ck_stock_movement_factor_positive")),
        sa.CheckConstraint(
            "(kind = 'receipt' AND qty > 0) OR (kind = 'issue' AND qty < 0)"
            " OR kind IN ('reversal', 'adjustment')",
            name=op.f("ck_stock_movement_sign_matches_kind"),
        ),
        sa.CheckConstraint(
            "kind <> 'issue' OR (project_node_id IS NOT NULL AND responsible_user_id IS NOT NULL)",
            name=op.f("ck_stock_movement_issue_node_and_responsible"),
        ),
        sa.CheckConstraint(
            "kind <> 'reversal' OR reversal_of_id IS NOT NULL",
            name=op.f("ck_stock_movement_reversal_has_origin"),
        ),
    )
    op.create_index(
        "ix_stock_movement_warehouse_id_material_id",
        "stock_movement",
        ["warehouse_id", "material_id"],
    )
    op.create_index(
        "uq_stock_movement_receipt_source_line",
        "stock_movement",
        ["source_line_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'receipt' AND source_line_id IS NOT NULL"),
    )
    op.create_index(
        "uq_stock_movement_reversal_of_id",
        "stock_movement",
        ["reversal_of_id"],
        unique=True,
        postgresql_where=sa.text("reversal_of_id IS NOT NULL"),
    )

    # Позиции версии: материал, партия, склад, ответственный (ВН) и заготовка заявки (S8b).
    op.add_column("document_line", sa.Column("material_id", sa.Uuid(), nullable=True))
    op.add_column("document_line", sa.Column("material_batch_id", sa.Uuid(), nullable=True))
    op.add_column("document_line", sa.Column("warehouse_id", sa.Uuid(), nullable=True))
    op.add_column("document_line", sa.Column("responsible_user_id", sa.Uuid(), nullable=True))
    op.add_column("document_line", sa.Column("purchase_request_line_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_document_line_material_id_material",
        "document_line",
        "material",
        ["material_id", "legal_entity_id"],
        ["id", "legal_entity_id"],
    )
    op.create_foreign_key(
        "fk_document_line_material_batch_id_material_batch",
        "document_line",
        "material_batch",
        ["material_batch_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_document_line_warehouse_id_warehouse",
        "document_line",
        "warehouse",
        ["warehouse_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_document_line_responsible_user_id_user",
        "document_line",
        "user",
        ["responsible_user_id"],
        ["id"],
    )

    # Вид ИНВ (акт инвентаризации): CHECK видов, области и списка видов с позициями.
    op.drop_constraint(op.f("ck_document_kind_valid"), "document", type_="check")
    op.create_check_constraint(
        op.f("ck_document_kind_valid"), "document", f"kind IN ({_in(KINDS_NEW)})"
    )
    op.drop_constraint(op.f("ck_document_scope_valid"), "document", type_="check")
    op.create_check_constraint(op.f("ck_document_scope_valid"), "document", _scope_check())
    op.drop_constraint(
        op.f("ck_document_number_counter_kind_valid"), "document_number_counter", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_document_number_counter_kind_valid"),
        "document_number_counter",
        f"kind IN ({_in(KINDS_NEW)})",
    )

    # Шаблоны маршрутов по группам видов (ADR-016 часть 5): склад, закупка, инвентаризация.
    op.drop_constraint(
        op.f("ck_approval_route_template_code"), "approval_route_template", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_approval_route_template_code"),
        "approval_route_template",
        f"code IN ({ROUTE_CODES})",
    )
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(
        "INSERT INTO approval_route_template (code, name) VALUES"
        " ('warehouse', 'Склад: приёмка М-7 - прораб'),"
        " ('procurement', 'Закупка: выбор поставщика - техдиректор, финдиректор'),"
        " ('inventory', 'Инвентаризация: прораб, техдиректор')"
    )
    op.execute(
        "INSERT INTO approval_route_step_template (template_id, position, role_code)"
        " SELECT t.id, s.position, s.role_code FROM approval_route_template t"
        " JOIN (VALUES ('warehouse', 1, 'foreman'),"
        " ('procurement', 1, 'technical_director'), ('procurement', 2, 'finance_director'),"
        " ('inventory', 1, 'foreman'), ('inventory', 2, 'technical_director')) AS s(code, position, role_code)"
        " ON s.code = t.code"
    )
    op.execute("RESET ROLE")

    # Таблицы без логики: RLS по объекту или юрлицу, права, журнал.
    for table in ("material", "material_unit", "material_batch", "material_price"):
        force_rls(table)
    force_rls("warehouse")
    force_rls("stock_movement")
    op.execute(
        "CREATE POLICY material_select_app ON material FOR SELECT TO kut_app USING ("
        " (SELECT app.is_internal()) AND (SELECT app.has_permission('materials', 'read'))"
        " AND legal_entity_id = ANY ((SELECT app.legal_entity_ids())::uuid[]))"
    )
    op.execute(
        "CREATE POLICY material_write_app ON material FOR ALL TO kut_app USING ("
        " (SELECT app.is_internal()) AND (SELECT app.has_permission('materials', 'write', 'catalog'))"
        " AND legal_entity_id = ANY ((SELECT app.legal_entity_ids())::uuid[]))"
        " WITH CHECK ("
        " (SELECT app.is_internal()) AND (SELECT app.has_permission('materials', 'write', 'catalog'))"
        " AND legal_entity_id = ANY ((SELECT app.legal_entity_ids())::uuid[]))"
    )
    for table in ("material_unit", "material_batch"):
        op.execute(
            f"CREATE POLICY {table}_select_app ON {table} FOR SELECT TO kut_app USING ("
            " (SELECT app.is_internal()) AND (SELECT app.has_permission('materials', 'read'))"
            " AND legal_entity_id = ANY ((SELECT app.legal_entity_ids())::uuid[]))"
        )
        op.execute(
            f"CREATE POLICY {table}_write_app ON {table} FOR ALL TO kut_app USING ("
            " (SELECT app.is_internal()) AND (SELECT app.has_permission('materials', 'write', 'catalog'))"
            " AND legal_entity_id = ANY ((SELECT app.legal_entity_ids())::uuid[]))"
            " WITH CHECK ("
            " (SELECT app.is_internal()) AND (SELECT app.has_permission('materials', 'write', 'catalog'))"
            " AND legal_entity_id = ANY ((SELECT app.legal_entity_ids())::uuid[]))"
        )
    # Цена плана: читает и пишет только право на цены закупок (Q3), строк без права нет.
    op.execute(
        "CREATE POLICY material_price_select_app ON material_price FOR SELECT TO kut_app USING ("
        " (SELECT app.is_internal()) AND (SELECT app.has_permission('procurement', 'read', 'prices'))"
        " AND legal_entity_id = ANY ((SELECT app.legal_entity_ids())::uuid[]))"
    )
    op.execute(
        "CREATE POLICY material_price_write_app ON material_price FOR ALL TO kut_app USING ("
        " (SELECT app.is_internal()) AND (SELECT app.has_permission('procurement', 'read', 'prices'))"
        " AND (SELECT app.has_permission('materials', 'write', 'catalog'))"
        " AND legal_entity_id = ANY ((SELECT app.legal_entity_ids())::uuid[]))"
        " WITH CHECK ("
        " (SELECT app.is_internal()) AND (SELECT app.has_permission('procurement', 'read', 'prices'))"
        " AND (SELECT app.has_permission('materials', 'write', 'catalog'))"
        " AND legal_entity_id = ANY ((SELECT app.legal_entity_ids())::uuid[]))"
    )
    op.execute(
        "CREATE POLICY warehouse_select_app ON warehouse FOR SELECT TO kut_app USING ("
        " (SELECT app.is_internal()) AND (SELECT app.has_permission('materials', 'read'))"
        " AND project_id = ANY ((SELECT app.project_ids())::uuid[]))"
    )
    op.execute(
        "CREATE POLICY warehouse_write_app ON warehouse FOR ALL TO kut_app USING ("
        " (SELECT app.is_internal()) AND (SELECT app.has_permission('materials', 'write', 'catalog'))"
        " AND project_id = ANY ((SELECT app.project_ids())::uuid[]))"
        " WITH CHECK ("
        " (SELECT app.is_internal()) AND (SELECT app.has_permission('materials', 'write', 'catalog'))"
        " AND project_id = ANY ((SELECT app.project_ids())::uuid[]))"
    )
    op.execute(
        "CREATE POLICY stock_movement_select_app ON stock_movement FOR SELECT TO kut_app USING ("
        " (SELECT app.is_internal()) AND (SELECT app.has_permission('materials', 'read'))"
        " AND project_id = ANY ((SELECT app.project_ids())::uuid[]))"
    )
    # Движение пишут только функции склада (kut_auth): у kut_app нет INSERT, UPDATE и DELETE.
    op.execute("GRANT SELECT ON stock_movement TO kut_app")
    op.execute("GRANT SELECT, INSERT ON stock_movement TO kut_auth")
    op.execute(
        "GRANT SELECT ON material, material_unit, material_batch, material_price, warehouse TO kut_auth"
    )
    grant("material", app="SELECT, INSERT, UPDATE")
    grant("material_unit", app="SELECT, INSERT, UPDATE")
    grant("material_batch", app="SELECT, INSERT, UPDATE")
    grant("material_price", app="SELECT, INSERT, UPDATE")
    grant("warehouse", app="SELECT, INSERT, UPDATE")
    op.execute(
        "GRANT UPDATE (material_id, material_batch_id, warehouse_id, responsible_user_id)"
        " ON document_line TO kut_app"
    )
    create_audit_triggers("material", legal_entity_column="legal_entity_id")
    create_audit_triggers("material_unit", legal_entity_column="legal_entity_id")
    create_audit_triggers("material_batch", legal_entity_column="legal_entity_id")
    create_audit_triggers("material_price", legal_entity_column="legal_entity_id")
    create_audit_triggers(
        "warehouse", project_column="project_id", legal_entity_column="legal_entity_id"
    )
    create_audit_triggers(
        "stock_movement",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        frozen_columns=(
            "kind",
            "qty",
            "unit",
            "quantity_in_unit",
            "factor",
            "material_id",
            "warehouse_id",
            "batch_id",
            "document_id",
            "version_id",
            "source_line_id",
            "project_node_id",
            "work_id",
            "responsible_user_id",
            "reversal_of_id",
        ),
    )


def downgrade() -> None:
    op.execute(
        "REVOKE UPDATE (material_id, material_batch_id, warehouse_id, responsible_user_id)"
        " ON document_line FROM kut_app"
    )
    drop_names = ("stock_movement",)
    for table in drop_names:
        op.drop_table(table)
    op.drop_column("document_line", "purchase_request_line_id")
    op.drop_column("document_line", "responsible_user_id")
    op.drop_column("document_line", "warehouse_id")
    op.drop_column("document_line", "material_batch_id")
    op.drop_column("document_line", "material_id")
    op.execute("GRANT DELETE ON approval_route_step_template, approval_route_template TO kut_auth")
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(
        "DELETE FROM approval_route_step_template WHERE template_id IN (SELECT id FROM approval_route_template WHERE code IN ('warehouse', 'procurement', 'inventory'))"
    )
    op.execute(
        "DELETE FROM approval_route_template WHERE code IN ('warehouse', 'procurement', 'inventory')"
    )
    op.execute("RESET ROLE")
    op.execute(
        "REVOKE DELETE ON approval_route_step_template, approval_route_template FROM kut_auth"
    )
    op.drop_constraint(
        op.f("ck_approval_route_template_code"), "approval_route_template", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_approval_route_template_code"),
        "approval_route_template",
        "code IN ('money', 'other')",
    )
    narrow_check(
        "document_number_counter",
        "ck_document_number_counter_kind_valid",
        "kind IN (" + _in(tuple(k for k in KINDS_NEW if k != "inventory_act")) + ")",
    )
    narrow_check(
        "document",
        "ck_document_kind_valid",
        "kind IN (" + _in(tuple(k for k in KINDS_NEW if k != "inventory_act")) + ")",
    )
    narrow_check(
        "document",
        "ck_document_scope_valid",
        "CASE WHEN kind IN ("
        + _in(tuple(k for k in OBJECT_NEW if k != "inventory_act"))
        + ") THEN project_id IS NOT NULL WHEN kind IN ("
        + _in(LEGAL)
        + ") THEN project_id IS NULL ELSE true END",
    )
    for table in ("material_price", "material_batch", "material_unit", "warehouse", "material"):
        op.drop_table(table)
