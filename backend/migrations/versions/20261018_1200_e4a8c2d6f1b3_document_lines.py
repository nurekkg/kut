"""документы: позиции версии и цены под правом на деньги (ADR-015 часть 4, S6a-1)

Revision ID: e4a8c2d6f1b3
Revises: d7e3a1c9b5f2
Create Date: 2026-10-18 12:00:00.000000

Позиция (document_line) принадлежит версии: наименование, ед., количество; видна всем, кто видит версию (внутренним -
и в черновике; порталу - утверждённые и подписанные версии). Цена (document_line_price) - отдельная таблица под
правом на деньги вида (как document_version_amount). Сумма позиции и итог - в представлении document_line_amount
(security_invoker: RLS цен действует через него). Записи - только в черновик, с правом documents:write:lines (цены -
ещё и с правом на деньги вида); удаление строк запрещено, мягкое удаление - в черновике. Гарды - триггеры БД, вторая
линия после политик. Аудит обеих таблиц; строки аудита цен видны только с правом на деньги.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from migrations.helpers import (
    business_table,
    create_audit_triggers,
    drop_audit_triggers,
    force_rls,
    grant,
)

revision: str = "e4a8c2d6f1b3"
down_revision: str | Sequence[str] | None = "d7e3a1c9b5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LINE_KINDS = (
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
)
LINE_KIND_LIST = ", ".join(f"'{kind}'" for kind in LINE_KINDS)
DOCUMENT_AUDIT_TABLES = (
    "document",
    "document_version",
    "document_link",
    "document_status_event",
    "document_version_amount",
    "document_line",
    "document_line_price",
)
LINE_FROZEN = (
    "version_id",
    "document_id",
    "legal_entity_id",
    "project_id",
    "position_no",
    "name",
    "unit",
    "quantity",
    "work_id",
    "project_node_id",
)
PRICE_FROZEN = ("line_id", "version_id", "document_id", "legal_entity_id", "project_id")
PORTAL_LINE = (
    "id",
    "created_at",
    "updated_at",
    "deleted_at",
    "document_id",
    "version_id",
    "position_no",
    "name",
    "unit",
    "quantity",
    "work_id",
    "project_node_id",
)
PORTAL_PRICE = (
    "id",
    "created_at",
    "updated_at",
    "deleted_at",
    "line_id",
    "document_id",
    "version_id",
    "unit_price",
)
FUNCTIONS_NEW: tuple[str, ...] = (
    f"""
CREATE FUNCTION app.document_kind_lines(p_kind text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$ SELECT p_kind IN ({LINE_KIND_LIST}) $$
""",
    """
CREATE FUNCTION app.document_money_right(p_document uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE((
        SELECT CASE WHEN app.document_kind_money(d.kind)
                    THEN app.has_permission('equipment', 'read', 'rates')
                    ELSE app.has_permission('documents', 'read', 'financial') END
        FROM public.document d WHERE d.id = p_document), false)
$$
""",
    """
CREATE FUNCTION app.line_writable(p_document uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE(app.document_writable(p_document)
        AND app.has_permission('documents', 'write', 'lines'), false)
$$
""",
    """
CREATE FUNCTION app.line_price_writable(p_document uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE(app.document_money_right(p_document) AND app.line_writable(p_document), false)
$$
""",
    """
CREATE FUNCTION app.document_line_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    d public.document;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'line.delete_forbidden';
    END IF;
    IF app.user_id() IS NOT NULL AND NOT app.line_writable(NEW.document_id) THEN
        RAISE EXCEPTION 'document.forbidden';
    END IF;
    SELECT * INTO v FROM public.document_version WHERE id = NEW.version_id FOR SHARE;
    IF NOT FOUND OR v.document_id IS DISTINCT FROM NEW.document_id THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    IF v.status IS DISTINCT FROM 'draft' THEN
        RAISE EXCEPTION 'line.version_not_draft';
    END IF;
    SELECT * INTO d FROM public.document WHERE id = v.document_id;
    IF NOT app.document_kind_lines(d.kind) THEN
        RAISE EXCEPTION 'line.kind_not_allowed';
    END IF;
    RETURN NEW;
END $$
""",
    """
CREATE FUNCTION app.document_line_price_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    l public.document_line;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'line.delete_forbidden';
    END IF;
    IF app.user_id() IS NOT NULL AND NOT app.line_price_writable(NEW.document_id) THEN
        RAISE EXCEPTION 'document.forbidden';
    END IF;
    SELECT * INTO l FROM public.document_line WHERE id = NEW.line_id;
    IF NOT FOUND OR l.deleted_at IS NOT NULL THEN
        RAISE EXCEPTION 'line.deleted';
    END IF;
    IF l.version_id IS DISTINCT FROM NEW.version_id OR l.document_id IS DISTINCT FROM NEW.document_id THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    SELECT * INTO v FROM public.document_version WHERE id = NEW.version_id FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    IF v.status IS DISTINCT FROM 'draft' THEN
        RAISE EXCEPTION 'line.version_not_draft';
    END IF;
    RETURN NEW;
END $$
""",
    """
CREATE FUNCTION app.audit_line_price_visible(p_row uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE(app.document_money_right((
        SELECT x.document_id FROM public.document_line_price x WHERE x.id = p_row)), false)
$$
""",
    """
CREATE FUNCTION app.version_priced_lines(p_version uuid) RETURNS integer
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT count(*)::integer FROM public.document_line_price p
    JOIN public.document_line l ON l.id = p.line_id
    WHERE p.version_id = p_version AND l.deleted_at IS NULL AND app.document_allowed(p.document_id)
$$
""",
)
FUNCTION_SIGNATURES: tuple[str, ...] = (
    "document_kind_lines(text)",
    "document_money_right(uuid)",
    "line_writable(uuid)",
    "line_price_writable(uuid)",
    "audit_line_price_visible(uuid)",
    "version_priced_lines(uuid)",
)
DOCUMENT_AUDIT_POLICY = (
    "CREATE POLICY audit_log_document_access ON audit_log AS RESTRICTIVE FOR SELECT TO kut_app"
    " USING (COALESCE(table_name, '') NOT IN ({tables})"
    " OR app.audit_document_visible(table_name, row_id))"
)


def _document_audit_policy(tables: Sequence[str]) -> str:
    return DOCUMENT_AUDIT_POLICY.format(tables=", ".join(f"'{name}'" for name in tables))


def upgrade() -> None:
    business_table(
        "document_line",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("position_no", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("work_id", sa.Uuid(), nullable=True),
        sa.Column("project_node_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"], ["document.id"], name=op.f("fk_document_line_document_id_document")
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["document_version.id"],
            name=op.f("fk_document_line_version_id_document_version"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_document_line_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entity.id"],
            name=op.f("fk_document_line_legal_entity_id_legal_entity"),
        ),
        sa.ForeignKeyConstraint(
            ["work_id"], ["work.id"], name=op.f("fk_document_line_work_id_work")
        ),
        sa.ForeignKeyConstraint(
            ["project_node_id"],
            ["project_node.id"],
            name=op.f("fk_document_line_project_node_id_project_node"),
        ),
        sa.CheckConstraint("position_no >= 1", name=op.f("ck_document_line_position_positive")),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_document_line_quantity_positive")),
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_document_line_name_not_blank")),
        sa.CheckConstraint("btrim(unit) <> ''", name=op.f("ck_document_line_unit_not_blank")),
    )
    op.create_index(
        op.f("uq_document_line_version_id_position_no"),
        "document_line",
        ["version_id", "position_no"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(op.f("ix_document_line_document_id"), "document_line", ["document_id"])
    op.create_index(op.f("ix_document_line_version_id"), "document_line", ["version_id"])
    op.create_index(op.f("ix_document_line_legal_entity_id"), "document_line", ["legal_entity_id"])
    force_rls("document_line")

    business_table(
        "document_line_price",
        sa.Column("line_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("unit_price", sa.Numeric(14, 2), nullable=False),
        sa.ForeignKeyConstraint(
            ["line_id"],
            ["document_line.id"],
            name=op.f("fk_document_line_price_line_id_document_line"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["document.id"],
            name=op.f("fk_document_line_price_document_id_document"),
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["document_version.id"],
            name=op.f("fk_document_line_price_version_id_document_version"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_document_line_price_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entity.id"],
            name=op.f("fk_document_line_price_legal_entity_id_legal_entity"),
        ),
        sa.UniqueConstraint("line_id", name=op.f("uq_document_line_price_line_id")),
        sa.CheckConstraint(
            "unit_price >= 0", name=op.f("ck_document_line_price_unit_price_not_negative")
        ),
    )
    op.create_index(
        op.f("ix_document_line_price_document_id"), "document_line_price", ["document_id"]
    )
    op.create_index(
        op.f("ix_document_line_price_version_id"), "document_line_price", ["version_id"]
    )
    op.create_index(
        op.f("ix_document_line_price_legal_entity_id"),
        "document_line_price",
        ["legal_entity_id"],
    )
    force_rls("document_line_price")

    op.execute("SET LOCAL ROLE kut_auth")
    for sql in FUNCTIONS_NEW:
        op.execute(sql)
    for signature in FUNCTION_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION app.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION app.{signature} TO kut_app, kut_auth")
    op.execute("REVOKE ALL ON FUNCTION app.document_line_guard() FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION app.document_line_price_guard() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION app.document_line_guard() TO kut_owner")
    op.execute("GRANT EXECUTE ON FUNCTION app.document_line_price_guard() TO kut_owner")
    op.execute("RESET ROLE")

    op.execute(
        "CREATE TRIGGER document_line_guard BEFORE INSERT OR UPDATE OR DELETE ON document_line"
        " FOR EACH ROW EXECUTE FUNCTION app.document_line_guard()"
    )
    op.execute(
        "CREATE TRIGGER document_line_price_guard BEFORE INSERT OR UPDATE OR DELETE ON document_line_price"
        " FOR EACH ROW EXECUTE FUNCTION app.document_line_price_guard()"
    )

    op.execute(
        "CREATE POLICY document_line_select_app ON document_line FOR SELECT TO kut_app"
        " USING (EXISTS (SELECT 1 FROM public.document_version v WHERE v.id = version_id))"
    )
    op.execute(
        "CREATE POLICY document_line_select_portal ON document_line FOR SELECT TO kut_portal"
        " USING (EXISTS (SELECT 1 FROM public.document_version v WHERE v.id = version_id))"
    )
    op.execute(
        "CREATE POLICY document_line_insert_app ON document_line FOR INSERT TO kut_app"
        " WITH CHECK (app.line_writable(document_id))"
    )
    op.execute(
        "CREATE POLICY document_line_update_app ON document_line FOR UPDATE TO kut_app"
        " USING (app.line_writable(document_id)) WITH CHECK (app.line_writable(document_id))"
    )
    op.execute(
        "CREATE POLICY document_line_price_select_app ON document_line_price FOR SELECT TO kut_app"
        " USING (app.document_money_right(document_id) AND EXISTS ("
        "SELECT 1 FROM public.document_version v WHERE v.id = version_id))"
    )
    op.execute(
        "CREATE POLICY document_line_price_select_portal ON document_line_price FOR SELECT TO kut_portal"
        " USING (EXISTS (SELECT 1 FROM public.document_version v WHERE v.id = version_id))"
    )
    op.execute(
        "CREATE POLICY document_line_price_insert_app ON document_line_price FOR INSERT TO kut_app"
        " WITH CHECK (app.line_price_writable(document_id))"
    )
    op.execute(
        "CREATE POLICY document_line_price_update_app ON document_line_price FOR UPDATE TO kut_app"
        " USING (app.line_price_writable(document_id))"
        " WITH CHECK (app.line_price_writable(document_id))"
    )

    grant(
        "document_line",
        app="SELECT, INSERT, UPDATE (deleted_at, updated_at, updated_by)",
        worker="SELECT, INSERT",
        portal_columns=PORTAL_LINE,
    )
    grant(
        "document_line_price",
        app="SELECT, INSERT, UPDATE (unit_price, updated_at, updated_by)",
        worker="SELECT, INSERT",
        portal_columns=PORTAL_PRICE,
    )
    op.execute("GRANT SELECT ON document_line, document_line_price TO kut_auth")
    create_audit_triggers(
        "document_line",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        frozen_columns=LINE_FROZEN,
    )
    create_audit_triggers(
        "document_line_price",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        frozen_columns=PRICE_FROZEN,
    )

    # Строки аудита цен видны только с правом на деньги вида (как строки сумм версии).
    op.execute(
        "CREATE POLICY audit_log_document_line_price ON audit_log AS RESTRICTIVE FOR SELECT TO kut_app"
        " USING (table_name IS DISTINCT FROM 'document_line_price'"
        " OR app.audit_line_price_visible(row_id))"
    )
    # Строки аудита позиций - по праву на документ (C1): таблицы добавляются в список политики.
    op.execute("DROP POLICY IF EXISTS audit_log_document_access ON audit_log")
    op.execute(_document_audit_policy(DOCUMENT_AUDIT_TABLES))

    op.execute(
        "CREATE VIEW document_line_amount WITH (security_invoker = true) AS"
        " SELECT l.id AS line_id, l.version_id, l.document_id, l.position_no, l.quantity,"
        " p.unit_price, round(l.quantity * p.unit_price, 2) AS amount"
        " FROM document_line l LEFT JOIN document_line_price p ON p.line_id = l.id"
        " WHERE l.deleted_at IS NULL"
    )
    op.execute("GRANT SELECT ON document_line_amount TO kut_app, kut_portal")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS document_line_amount")
    op.execute("DROP POLICY IF EXISTS audit_log_document_line_price ON audit_log")
    op.execute("DROP POLICY IF EXISTS audit_log_document_access ON audit_log")
    op.execute(_document_audit_policy(DOCUMENT_AUDIT_TABLES[:5]))
    drop_audit_triggers("document_line_price")
    drop_audit_triggers("document_line")
    op.execute("DROP TABLE document_line_price")
    op.execute("DROP TABLE document_line")
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute("DROP FUNCTION IF EXISTS app.document_line_price_guard()")
    op.execute("DROP FUNCTION IF EXISTS app.document_line_guard()")
    for signature in reversed(FUNCTION_SIGNATURES):
        op.execute(f"DROP FUNCTION IF EXISTS app.{signature}")
    op.execute("RESET ROLE")
