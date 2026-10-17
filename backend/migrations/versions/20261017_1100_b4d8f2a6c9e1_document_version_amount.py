"""документы: сумма принадлежит версии; правка только в draft; журнал по виду (PO 2026-10-17, R1/R3)

Revision ID: b4d8f2a6c9e1
Revises: a7c3e9d1f5b2
Create Date: 2026-10-17 11:00:00.000000

Сумма хранится у версии (document_version_amount, одна строка на версию), а не у документа: иначе её увидит
прораб через шапку. Правка - только пока версия в статусе draft (гард БД): UPDATE после выхода из draft и любой
DELETE - ошибка. Новая сумма = новая версия. Чтение: право на вид (денежные equipment_* - equipment:read:rates,
остальные финансовые - documents:read:financial) и видимость версии; портал - видимость версии (утверждённые).
Журнал аудита: ограничивающая политика по виду строки.

Старая document_amount: данные копируются на последнюю версию каждого документа. Таблица остаётся на месте
без записи и без чтения (политика «никому»): её данные - для отката, удаление - решение PO. Старые функции и гард
записи убираются.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from migrations.helpers import business_table, create_audit_triggers, force_rls, grant

revision: str = "b4d8f2a6c9e1"
down_revision: str | Sequence[str] | None = "a7c3e9d1f5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PORTAL_VERSION_AMOUNT = (
    "id",
    "created_at",
    "updated_at",
    "deleted_at",
    "version_id",
    "document_id",
    "amount",
)

FUNCTIONS_NEW: tuple[str, ...] = (
    """
CREATE FUNCTION app.version_money_right(p_version uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE((
        SELECT CASE WHEN app.document_kind_money(d.kind)
                    THEN app.has_permission('equipment', 'read', 'rates')
                    ELSE app.has_permission('documents', 'read', 'financial') END
        FROM public.document_version v JOIN public.document d ON d.id = v.document_id
        WHERE v.id = p_version), false)
$$
""",
    """
CREATE FUNCTION app.version_amount_writable(p_version uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE((
        SELECT app.version_money_right(v.id) AND app.document_writable(v.document_id)
        FROM public.document_version v WHERE v.id = p_version), false)
$$
""",
    """
CREATE FUNCTION app.audit_amount_visible(p_data jsonb) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE((
        SELECT CASE WHEN app.document_kind_money(d.kind)
                    THEN app.has_permission('equipment', 'read', 'rates')
                    ELSE app.has_permission('documents', 'read', 'financial') END
        FROM public.document d WHERE d.id = (p_data->>'document_id')::uuid), false)
$$
""",
    """
CREATE FUNCTION app.version_amount_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    d public.document;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'amount.delete_forbidden';
    END IF;
    IF NEW.deleted_at IS NOT NULL THEN
        RAISE EXCEPTION 'amount.delete_forbidden';
    END IF;
    IF TG_OP = 'UPDATE' AND (NEW.version_id IS DISTINCT FROM OLD.version_id
                             OR NEW.document_id IS DISTINCT FROM OLD.document_id) THEN
        RAISE EXCEPTION 'amount.version_not_draft';
    END IF;
    SELECT * INTO v FROM public.document_version WHERE id = NEW.version_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    IF v.status IS DISTINCT FROM 'draft' THEN
        RAISE EXCEPTION 'amount.version_not_draft';
    END IF;
    SELECT * INTO d FROM public.document WHERE id = v.document_id;
    IF NOT d.financial THEN
        RAISE EXCEPTION 'document.amount_not_financial';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.document_id IS DISTINCT FROM d.id THEN
            RAISE EXCEPTION 'document.not_found';
        END IF;
        NEW.project_id := d.project_id;
        NEW.legal_entity_id := d.legal_entity_id;
    END IF;
    RETURN NEW;
END $$
""",
)

# Прежние определения записи и гарда суммы документа (точные тела из ревизии fa1e8c2d5b93) - для отката.
OLD_AMOUNT_WRITABLE = """
CREATE FUNCTION app.document_amount_writable(p_document uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT app.document_writable(p_document) AND app.has_permission('documents', 'read', 'financial')
$$
"""
OLD_AMOUNT_GUARD = """
CREATE FUNCTION app.document_amount_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    d public.document;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT * INTO d FROM public.document WHERE id = NEW.document_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'document.not_found';
        END IF;
        IF NOT d.financial THEN
            RAISE EXCEPTION 'document.amount_not_financial';
        END IF;
        NEW.project_id := d.project_id;
        NEW.legal_entity_id := d.legal_entity_id;
    END IF;
    RETURN NEW;
END $$
"""


def upgrade() -> None:
    business_table(
        "document_version_amount",
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["document_version.id"],
            name=op.f("fk_document_version_amount_version_id_document_version"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["document.id"],
            name=op.f("fk_document_version_amount_document_id_document"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_document_version_amount_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entity.id"],
            name=op.f("fk_document_version_amount_legal_entity_id_legal_entity"),
        ),
        sa.UniqueConstraint("version_id", name=op.f("uq_document_version_amount_version_id")),
        sa.CheckConstraint(
            "amount >= 0", name=op.f("ck_document_version_amount_amount_not_negative")
        ),
    )
    op.create_index(
        op.f("ix_document_version_amount_document_id"), "document_version_amount", ["document_id"]
    )
    op.create_index(
        op.f("ix_document_version_amount_legal_entity_id"),
        "document_version_amount",
        ["legal_entity_id"],
    )
    force_rls("document_version_amount")

    # Функции и выдачи EXECUTE - от владельца функций (kut_auth): иначе REVOKE падает на прав владельца.
    op.execute("SET LOCAL ROLE kut_auth")
    for sql in FUNCTIONS_NEW:
        op.execute(sql)
    for signature in (
        "version_money_right(uuid)",
        "version_amount_writable(uuid)",
        "audit_amount_visible(jsonb)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION app.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION app.{signature} TO kut_app, kut_auth")
    op.execute("REVOKE ALL ON FUNCTION app.version_amount_guard() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION app.version_amount_guard() TO kut_owner")
    op.execute("RESET ROLE")

    op.execute(
        "CREATE TRIGGER document_version_amount_guard"
        " BEFORE INSERT OR UPDATE OR DELETE ON document_version_amount"
        " FOR EACH ROW EXECUTE FUNCTION app.version_amount_guard()"
    )

    op.execute(
        "CREATE POLICY document_version_amount_select_app ON document_version_amount"
        " FOR SELECT TO kut_app"
        " USING (app.version_money_right(version_id) AND EXISTS ("
        " SELECT 1 FROM public.document_version v WHERE v.id = version_id))"
    )
    op.execute(
        "CREATE POLICY document_version_amount_select_portal ON document_version_amount"
        " FOR SELECT TO kut_portal"
        " USING (EXISTS (SELECT 1 FROM public.document_version v WHERE v.id = version_id))"
    )
    op.execute(
        "CREATE POLICY document_version_amount_insert_app ON document_version_amount"
        " FOR INSERT TO kut_app WITH CHECK (app.version_amount_writable(version_id))"
    )
    op.execute(
        "CREATE POLICY document_version_amount_update_app ON document_version_amount"
        " FOR UPDATE TO kut_app USING (app.version_amount_writable(version_id))"
        " WITH CHECK (app.version_amount_writable(version_id))"
    )

    grant(
        "document_version_amount",
        app="SELECT, INSERT, UPDATE (amount, updated_at, updated_by)",
        worker="SELECT, INSERT",
        portal_columns=PORTAL_VERSION_AMOUNT,
    )
    create_audit_triggers(
        "document_version_amount",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        frozen_columns=("document_id", "version_id"),
    )

    op.execute(
        "CREATE POLICY audit_log_version_amount ON audit_log AS RESTRICTIVE FOR SELECT TO kut_app"
        " USING (table_name IS DISTINCT FROM 'document_version_amount'"
        " OR app.audit_amount_visible(COALESCE(new, old)))"
    )

    # Перенос dev-данных: сумма документа - на его последнюю версию. Гард на время переноса отключён (версия
    # может быть уже утверждена: тогда сумма неизменна по правилу R1). Проверка - до включения гарда.
    op.execute("ALTER TABLE document_version_amount DISABLE TRIGGER document_version_amount_guard")
    # kut_auth - BYPASSRLS и читает document_amount и document_version (выдано в 2633d20e6f23).
    op.execute("GRANT SELECT, INSERT ON document_version_amount TO kut_auth")
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(
        "DO $$ BEGIN IF (SELECT count(*) FROM document_amount a WHERE a.deleted_at IS NULL"
        " AND NOT EXISTS (SELECT 1 FROM document_version v WHERE v.document_id = a.document_id)) > 0"
        " THEN RAISE EXCEPTION 'amount: есть суммы документов без версий'; END IF; END $$"
    )
    op.execute(
        "INSERT INTO document_version_amount"
        " (id, version_id, document_id, project_id, legal_entity_id, amount, created_by, updated_by)"
        " SELECT gen_random_uuid(), v.id, a.document_id, a.project_id, a.legal_entity_id, a.amount,"
        " a.created_by, a.updated_by"
        " FROM document_amount a JOIN LATERAL ("
        " SELECT id FROM document_version WHERE document_id = a.document_id"
        " ORDER BY version_no DESC LIMIT 1) v ON true"
        " WHERE a.deleted_at IS NULL"
    )
    op.execute("RESET ROLE")
    op.execute("ALTER TABLE document_version_amount ENABLE TRIGGER document_version_amount_guard")

    # Старый путь записи: политики «никому», гард и функции записи убираются, таблица остаётся для отката.
    for name in (
        "document_amount_select_app",
        "document_amount_select_portal",
        "document_amount_insert_app",
        "document_amount_update_app",
    ):
        op.execute(f"DROP POLICY {name} ON document_amount")
    op.execute(
        "CREATE POLICY document_amount_none_app ON document_amount FOR ALL TO kut_app"
        " USING (false) WITH CHECK (false)"
    )
    op.execute(
        "CREATE POLICY document_amount_none_portal ON document_amount FOR ALL TO kut_portal"
        " USING (false) WITH CHECK (false)"
    )
    op.execute("DROP TRIGGER document_amount_guard ON document_amount")
    op.execute("DROP FUNCTION app.document_amount_guard()")
    op.execute("DROP FUNCTION app.document_amount_writable(uuid)")
    op.execute("REVOKE INSERT, UPDATE ON document_amount FROM kut_app")


def downgrade() -> None:
    # Порядок: сначала журнал и таблица (на ней висят триггер и политики), потом функции, потом старый путь.
    op.execute("DROP POLICY IF EXISTS audit_log_version_amount ON audit_log")
    op.execute("DROP TABLE document_version_amount")
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute("DROP FUNCTION IF EXISTS app.version_amount_guard()")
    op.execute("DROP FUNCTION IF EXISTS app.audit_amount_visible(jsonb)")
    op.execute("DROP FUNCTION IF EXISTS app.version_amount_writable(uuid)")
    op.execute("DROP FUNCTION IF EXISTS app.version_money_right(uuid)")
    op.execute(OLD_AMOUNT_WRITABLE)
    op.execute(OLD_AMOUNT_GUARD)
    op.execute("GRANT EXECUTE ON FUNCTION app.document_amount_writable(uuid) TO kut_app, kut_auth")
    op.execute("GRANT EXECUTE ON FUNCTION app.document_amount_guard() TO kut_owner")
    op.execute("RESET ROLE")
    op.execute("DROP POLICY IF EXISTS document_amount_none_app ON document_amount")
    op.execute("DROP POLICY IF EXISTS document_amount_none_portal ON document_amount")
    op.execute(
        "CREATE TRIGGER document_amount_guard BEFORE INSERT OR UPDATE ON document_amount"
        " FOR EACH ROW EXECUTE FUNCTION app.document_amount_guard()"
    )
    op.execute(
        "CREATE POLICY document_amount_select_app ON document_amount FOR SELECT TO kut_app"
        " USING (app.has_permission('documents', 'read', 'financial') AND EXISTS ("
        "SELECT 1 FROM public.document d WHERE d.id = document_id))"
    )
    op.execute(
        "CREATE POLICY document_amount_select_portal ON document_amount FOR SELECT TO kut_portal"
        " USING (EXISTS (SELECT 1 FROM public.document d WHERE d.id = document_id))"
    )
    op.execute(
        "CREATE POLICY document_amount_insert_app ON document_amount FOR INSERT TO kut_app"
        " WITH CHECK (app.document_amount_writable(document_id))"
    )
    op.execute(
        "CREATE POLICY document_amount_update_app ON document_amount FOR UPDATE TO kut_app"
        " USING (app.document_amount_writable(document_id))"
        " WITH CHECK (app.document_amount_writable(document_id))"
    )
    op.execute("GRANT INSERT ON document_amount TO kut_app")
    op.execute("GRANT UPDATE (amount, updated_at, updated_by) ON document_amount TO kut_app")
