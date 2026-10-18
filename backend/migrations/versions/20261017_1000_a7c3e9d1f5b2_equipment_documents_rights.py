"""документы техники: права и видимость по equipment:read/write:documents и денежные виды (PO 2026-10-17, R2/R3)

Revision ID: a7c3e9d1f5b2
Revises: e8b3d1f5a7c2
Create Date: 2026-10-17 10:00:00.000000

Виды equipment_* подчиняются правам техники, а не только documents:read: видимость и запись - по
equipment:read:documents и equipment:write:documents (технический осмотр - ещё по equipment:write:maintenance).
Денежные виды (страховка, прочее) - financial = true; их файл и сумму видит только equipment:read:rates.
Признак financial замораживается триггером immutable_columns: он отключается владельцем на время одного
UPDATE, который идёт под kut_auth (BYPASSRLS), затем проверяется результат и триггер включается снова.
Откат возвращает прежние определения функций и признак.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a7c3e9d1f5b2"
down_revision: str | Sequence[str] | None = "e8b3d1f5a7c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EQUIPMENT_KINDS = (
    "'equipment_insurance', 'equipment_other', 'equipment_passport',"
    " 'equipment_permit', 'equipment_technical_inspection'"
)
MONEY_KINDS = "'equipment_insurance', 'equipment_other'"
OLD_FINANCIAL = (
    "'addendum', 'contract_customer', 'contract_rental', 'contract_subcontract',"
    " 'contract_supply', 'estimate_local', 'estimate_summary', 'invoice_esf', 'ks2', 'ks3', 'ks6',"
    " 'waybill_m15', 'waybill_torg12'"
)
NEW_FINANCIAL = f"{OLD_FINANCIAL}, {MONEY_KINDS}"
CUSTOMER_KINDS_SQL = (
    "'commissioning_act', 'contract_customer', 'hidden_works_act', 'ird', 'ks2', 'ks3', 'ks6'"
)
SUBCONTRACTOR_KINDS_SQL = "'contract_subcontract', 'hidden_works_act', 'ird', 'ks2', 'ks3', 'ks6'"
SUPPLIER_KINDS_SQL = (
    "'contract_supply', 'invoice_esf', 'material_request', 'waybill_m15', 'waybill_torg12'"
)

# Видимость строки документа для внутренних. Виды техники - по её праву, а не по documents:read:
# политика читает признаки текущей строки (поиск по id не годится: строка, которую вставляет тот же
# оператор, для стабильной функции ещё не видна, и RETURNING после INSERT падает).
DOCUMENT_SELECT_OLD = (
    "(SELECT app.is_internal()) AND (SELECT app.has_permission('documents', 'read'))"
    " AND (project_id = ANY ((SELECT app.project_ids())::uuid[])"
    " OR (project_id IS NULL AND legal_entity_id = ANY ((SELECT app.legal_entity_ids())::uuid[])))"
)
DOCUMENT_SELECT_NEW = (
    "CASE WHEN app.document_kind_equipment(kind) THEN"
    " (SELECT app.is_internal()) AND project_id IS NULL"
    " AND legal_entity_id = ANY ((SELECT app.legal_entity_ids())::uuid[])"
    " AND ((SELECT app.has_permission('equipment', 'read', 'documents'))"
    " OR (app.document_kind_money(kind)"
    " AND (SELECT app.has_permission('equipment', 'read', 'rates'))))"
    f" ELSE {DOCUMENT_SELECT_OLD} END"
)

KIND_HELPERS: tuple[str, ...] = (
    f"""
CREATE FUNCTION app.document_kind_equipment(p_kind text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$ SELECT p_kind IN ({EQUIPMENT_KINDS}) $$
""",
    f"""
CREATE FUNCTION app.document_kind_money(p_kind text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$ SELECT p_kind IN ({MONEY_KINDS}) $$
""",
)

FUNCTIONS_NEW: tuple[str, ...] = (
    """
CREATE FUNCTION app.document_kind_writable(p_kind text, p_project uuid, p_legal_entity uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE(app.is_internal() AND CASE
        WHEN app.document_kind_equipment(p_kind) THEN
            p_project IS NULL AND p_legal_entity = ANY(app.legal_entity_ids())
            AND (app.has_permission('equipment', 'write', 'documents')
                 OR (p_kind = 'equipment_technical_inspection'
                     AND app.has_permission('equipment', 'write', 'maintenance')))
        ELSE app.has_permission('documents', 'write') AND (
            p_project = ANY(app.project_ids())
            OR (p_project IS NULL AND p_legal_entity = ANY(app.legal_entity_ids())))
    END, false)
$$
""",
    """
CREATE OR REPLACE FUNCTION app.document_writable(p_document uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE((
        SELECT app.document_kind_writable(d.kind, d.project_id, d.legal_entity_id)
        FROM public.document d WHERE d.id = p_document), false)
$$
""",
    f"""
CREATE OR REPLACE FUNCTION app.document_allowed(p_document uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    d public.document;
BEGIN
    SELECT * INTO d FROM public.document WHERE id = p_document;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    IF app.is_internal() THEN
        IF app.document_kind_equipment(d.kind) THEN
            -- Документы техники: право техники и юрлицо; денежные виды видны и по тарифам (R3).
            RETURN COALESCE((app.has_permission('equipment', 'read', 'documents')
                    OR (app.document_kind_money(d.kind)
                        AND app.has_permission('equipment', 'read', 'rates')))
                AND d.project_id IS NULL AND d.legal_entity_id = ANY(app.legal_entity_ids()), false);
        END IF;
        RETURN COALESCE(app.has_permission('documents', 'read') AND (
            d.project_id = ANY(app.project_ids())
            OR (d.project_id IS NULL AND d.legal_entity_id = ANY(app.legal_entity_ids()))), false);
    END IF;
    IF NOT app.has_permission('documents', 'read') OR d.archived_at IS NOT NULL
       OR d.counterparty_id IS NULL OR d.counterparty_id IS DISTINCT FROM app.counterparty_id()
       OR (d.project_id IS NOT NULL AND NOT d.project_id = ANY(app.project_ids())) THEN
        RETURN false;
    END IF;
    IF NOT (
        (app.user_role_code() = 'customer' AND d.kind IN ({CUSTOMER_KINDS_SQL}))
        OR (app.user_role_code() = 'subcontractor' AND d.kind IN ({SUBCONTRACTOR_KINDS_SQL}))
        OR (app.user_role_code() = 'supplier' AND d.kind IN ({SUPPLIER_KINDS_SQL}))) THEN
        RETURN false;
    END IF;
    RETURN EXISTS (
        SELECT 1 FROM public.document_version v
        WHERE v.id = d.current_version_id AND v.status IN ('approved', 'signed'));
END $$
""",
    """
CREATE OR REPLACE FUNCTION app.document_version_storage_key(p_version uuid) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    d public.document;
BEGIN
    SELECT * INTO v FROM public.document_version WHERE id = p_version;
    IF NOT FOUND OR NOT COALESCE(app.document_allowed(v.document_id), false) THEN
        RETURN NULL;
    END IF;
    IF NOT app.is_internal() AND v.status NOT IN ('approved', 'signed') THEN
        RETURN NULL;
    END IF;
    SELECT * INTO d FROM public.document WHERE id = v.document_id;
    IF app.is_internal() THEN
        IF app.document_kind_money(d.kind) THEN
            IF NOT app.has_permission('equipment', 'read', 'rates') THEN
                RETURN NULL;
            END IF;
        ELSIF d.financial AND NOT app.has_permission('documents', 'read', 'financial') THEN
            RETURN NULL;
        END IF;
    END IF;
    RETURN v.s3_key;
END $$
""",
    """
CREATE OR REPLACE FUNCTION app.next_document_number(
    p_legal_entity uuid, p_kind text, p_year integer) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_no integer;
BEGIN
    IF app.user_id() IS NOT NULL
       AND NOT COALESCE(app.document_kind_writable(p_kind, NULL, p_legal_entity), false) THEN
        RAISE EXCEPTION 'document.forbidden';
    END IF;
    INSERT INTO public.document_number_counter AS c (legal_entity_id, kind, year, last_no)
    VALUES (p_legal_entity, p_kind, p_year, 1)
    ON CONFLICT (legal_entity_id, kind, year) DO UPDATE SET last_no = c.last_no + 1
    RETURNING c.last_no INTO v_no;
    RETURN v_no;
END $$
""",
)

# Прежние определения (точные тела из ревизии fa1e8c2d5b93) - для отката.
OLD_DOCUMENT_WRITABLE = """
CREATE OR REPLACE FUNCTION app.document_writable(p_document uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE((
        SELECT app.document_scope_writable(d.project_id, d.legal_entity_id)
        FROM public.document d WHERE d.id = p_document), false)
$$
"""
HARDENED_DOCUMENT_ALLOWED = f"""
CREATE OR REPLACE FUNCTION app.document_allowed(p_document uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    d public.document;
BEGIN
    SELECT * INTO d FROM public.document WHERE id = p_document;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    IF app.is_internal() THEN
        RETURN COALESCE(app.has_permission('documents', 'read') AND (
            d.project_id = ANY(app.project_ids())
            OR (d.project_id IS NULL AND d.legal_entity_id = ANY(app.legal_entity_ids()))), false);
    END IF;
    IF NOT app.has_permission('documents', 'read') OR d.archived_at IS NOT NULL
       OR d.counterparty_id IS NULL OR d.counterparty_id IS DISTINCT FROM app.counterparty_id()
       OR (d.project_id IS NOT NULL AND NOT d.project_id = ANY(app.project_ids())) THEN
        RETURN false;
    END IF;
    IF NOT (
        (app.user_role_code() = 'customer' AND d.kind IN ({CUSTOMER_KINDS_SQL}))
        OR (app.user_role_code() = 'subcontractor' AND d.kind IN ({SUBCONTRACTOR_KINDS_SQL}))
        OR (app.user_role_code() = 'supplier' AND d.kind IN ({SUPPLIER_KINDS_SQL}))) THEN
        RETURN false;
    END IF;
    RETURN EXISTS (
        SELECT 1 FROM public.document_version v
        WHERE v.id = d.current_version_id AND v.status IN ('approved', 'signed'));
END $$
"""

HARDENED_STORAGE_KEY = """
CREATE OR REPLACE FUNCTION app.document_version_storage_key(p_version uuid) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    d public.document;
BEGIN
    SELECT * INTO v FROM public.document_version WHERE id = p_version;
    IF NOT FOUND OR NOT COALESCE(app.document_allowed(v.document_id), false) THEN
        RETURN NULL;
    END IF;
    IF NOT app.is_internal() AND v.status NOT IN ('approved', 'signed') THEN
        RETURN NULL;
    END IF;
    SELECT * INTO d FROM public.document WHERE id = v.document_id;
    IF app.is_internal() AND d.financial AND NOT app.has_permission('documents', 'read', 'financial') THEN
        RETURN NULL;
    END IF;
    RETURN v.s3_key;
END $$
"""

OLD_NEXT_NUMBER = """
CREATE OR REPLACE FUNCTION app.next_document_number(p_legal_entity uuid, p_kind text, p_year integer)
RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_no integer;
BEGIN
    IF app.user_id() IS NOT NULL AND (
        NOT app.is_internal() OR NOT app.has_permission('documents', 'write')
        OR NOT p_legal_entity = ANY(app.legal_entity_ids())) THEN
        RAISE EXCEPTION 'document.forbidden';
    END IF;
    INSERT INTO public.document_number_counter AS c (legal_entity_id, kind, year, last_no)
    VALUES (p_legal_entity, p_kind, p_year, 1)
    ON CONFLICT (legal_entity_id, kind, year) DO UPDATE SET last_no = c.last_no + 1
    RETURNING c.last_no INTO v_no;
    RETURN v_no;
END $$
"""

HELPER_SIGNATURES = (
    "document_kind_equipment(text)",
    "document_kind_money(text)",
    "document_kind_writable(text, uuid, uuid)",
)


def _set_financial(value: str, kinds: str, check_kinds: str) -> None:
    """Признак financial: владелец отключает триггер immutable_columns, UPDATE и проверка идут под
    kut_auth (BYPASSRLS, UPDATE на document). Затем триггер включается, CHECK пересоздаётся."""
    op.execute("ALTER TABLE document DISABLE TRIGGER immutable_columns")
    op.execute("ALTER TABLE document DROP CONSTRAINT ck_document_financial_matches_kind")
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(f"UPDATE document SET financial = {value} WHERE kind IN ({kinds})")
    op.execute(
        "DO $$ BEGIN IF (SELECT count(*) FROM document WHERE kind IN ("
        f"{kinds}) AND financial IS DISTINCT FROM {value}) > 0 THEN"
        " RAISE EXCEPTION 'financial: денежные виды не помечены'; END IF; END $$"
    )
    op.execute("RESET ROLE")
    op.execute("ALTER TABLE document ENABLE TRIGGER immutable_columns")
    op.execute(
        "ALTER TABLE document ADD CONSTRAINT ck_document_financial_matches_kind"
        f" CHECK (financial = (kind IN ({check_kinds})))"
    )


def upgrade() -> None:
    op.execute("SET LOCAL ROLE kut_auth")
    for sql in KIND_HELPERS:
        op.execute(sql)
    for sql in FUNCTIONS_NEW:
        op.execute(sql)
    for signature in HELPER_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION app.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION app.{signature} TO kut_app, kut_worker, kut_auth")
    op.execute("RESET ROLE")

    op.execute("DROP POLICY document_insert_app ON document")
    op.execute(
        "CREATE POLICY document_insert_app ON document FOR INSERT TO kut_app"
        " WITH CHECK (app.document_kind_writable(kind, project_id, legal_entity_id))"
    )
    op.execute("DROP POLICY document_select_app ON document")
    op.execute(
        "CREATE POLICY document_select_app ON document FOR SELECT TO kut_app"
        f" USING ({DOCUMENT_SELECT_NEW})"
    )
    op.execute("DROP POLICY document_update_app ON document")
    op.execute(
        "CREATE POLICY document_update_app ON document FOR UPDATE TO kut_app"
        " USING (app.document_kind_writable(kind, project_id, legal_entity_id))"
        " WITH CHECK (app.document_kind_writable(kind, project_id, legal_entity_id))"
    )

    _set_financial("true", MONEY_KINDS, NEW_FINANCIAL)


def downgrade() -> None:
    _set_financial("false", MONEY_KINDS, OLD_FINANCIAL)
    op.execute("DROP POLICY document_insert_app ON document")
    op.execute(
        "CREATE POLICY document_insert_app ON document FOR INSERT TO kut_app"
        " WITH CHECK (app.document_scope_writable(project_id, legal_entity_id))"
    )
    op.execute("DROP POLICY document_select_app ON document")
    op.execute(
        "CREATE POLICY document_select_app ON document FOR SELECT TO kut_app"
        f" USING ({DOCUMENT_SELECT_OLD})"
    )
    op.execute("DROP POLICY document_update_app ON document")
    op.execute(
        "CREATE POLICY document_update_app ON document FOR UPDATE TO kut_app"
        " USING (app.document_scope_writable(project_id, legal_entity_id))"
        " WITH CHECK (app.document_scope_writable(project_id, legal_entity_id))"
    )
    # Функции принадлежат kut_auth: восстановление и удаление - от его имени.
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(OLD_DOCUMENT_WRITABLE)
    op.execute(HARDENED_DOCUMENT_ALLOWED)
    op.execute(HARDENED_STORAGE_KEY)
    op.execute(OLD_NEXT_NUMBER)
    op.execute("DROP FUNCTION IF EXISTS app.document_kind_writable(text, uuid, uuid)")
    op.execute("DROP FUNCTION IF EXISTS app.document_kind_money(text)")
    op.execute("DROP FUNCTION IF EXISTS app.document_kind_equipment(text)")
    op.execute("RESET ROLE")
