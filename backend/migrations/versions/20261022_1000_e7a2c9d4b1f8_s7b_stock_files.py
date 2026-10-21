"""S7b: версии ВН и инвентаризации без файла, вид «СЕРТ» (сертификат материала), партии кладовщика,
ВН с партией сертифицированного материала, остаток материала только своего юрлица, код архива.

Версия без файла: гард БД (вид без флага file_required) и CHECK «всё или ничего» по полям файла.
Откат не удаляет версии: если версии без файла уже есть, откат остановится на SET NOT NULL."""

from collections.abc import Sequence

from alembic import op

revision: str = "e7a2c9d4b1f8"
down_revision: str | Sequence[str] | None = "d9a4c7e2f6b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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

KINDS_BEFORE = KINDS_NEW
KINDS_AFTER = (*KINDS_NEW, "material_certificate")

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

LEGAL_BEFORE = LEGAL
LEGAL_AFTER = (*LEGAL, "material_certificate")

MATERIAL_FACTOR_BEFORE = """
CREATE OR REPLACE FUNCTION app.material_factor(p_material uuid, p_unit text) RETURNS numeric
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT CASE WHEN p_unit = m.base_unit THEN 1::numeric ELSE (
        SELECT u.factor FROM public.material_unit u
        WHERE u.material_id = m.id AND u.unit = p_unit AND u.deleted_at IS NULL) END
    FROM public.material m WHERE m.id = p_material AND m.deleted_at IS NULL
$$
"""

MATERIAL_FACTOR_AFTER = """
CREATE OR REPLACE FUNCTION app.material_factor(p_material uuid, p_unit text) RETURNS numeric
LANGUAGE sql STABLE SET search_path = pg_catalog AS $$
    SELECT CASE WHEN p_unit = m.base_unit THEN 1::numeric ELSE (
        SELECT u.factor FROM public.material_unit u
        WHERE u.material_id = m.id AND u.unit = p_unit AND u.deleted_at IS NULL) END
    FROM public.material m WHERE m.id = p_material AND m.deleted_at IS NULL
$$
"""

POST_WAYBILL_BEFORE = """
CREATE OR REPLACE FUNCTION app.post_waybill(p_version uuid) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    d public.document;
    ln public.document_line;
    m public.material;
    f numeric;
    base numeric;
    bal numeric;
    r public.document_version;
    n integer := 0;
BEGIN
    IF NOT app.has_permission('materials', 'write', 'stock') THEN
        RAISE EXCEPTION 'stock.forbidden';
    END IF;
    SELECT * INTO v FROM public.document_version WHERE id = p_version FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    IF v.status <> 'draft' THEN
        RAISE EXCEPTION 'stock.version_not_draft';
    END IF;
    SELECT * INTO d FROM public.document WHERE id = v.document_id;
    IF d.kind <> 'waybill_internal' THEN
        RAISE EXCEPTION 'stock.kind_invalid';
    END IF;
    IF NOT app.document_writable(d.id) THEN
        RAISE EXCEPTION 'document.forbidden';
    END IF;
    IF d.archived_at IS NOT NULL THEN
        RAISE EXCEPTION 'document.archived';
    END IF;
    IF EXISTS (SELECT 1 FROM public.document_line
               WHERE version_id = p_version AND deleted_at IS NULL
                 AND (material_id IS NULL OR warehouse_id IS NULL
                      OR project_node_id IS NULL OR responsible_user_id IS NULL)) THEN
        RAISE EXCEPTION 'stock.issue_incomplete';
    END IF;
    IF EXISTS (SELECT 1 FROM public.document_line dl
               JOIN public.warehouse w ON w.id = dl.warehouse_id
               WHERE dl.version_id = p_version AND dl.deleted_at IS NULL
                 AND w.project_id IS DISTINCT FROM v.project_id) THEN
        RAISE EXCEPTION 'stock.warehouse_project';
    END IF;
    IF EXISTS (SELECT 1 FROM public.document_line dl
               JOIN public.project_node n ON n.id = dl.project_node_id
               WHERE dl.version_id = p_version AND dl.deleted_at IS NULL
                 AND n.project_id IS DISTINCT FROM v.project_id) THEN
        RAISE EXCEPTION 'stock.not_found';
    END IF;
    FOR r IN SELECT * FROM public.document_version
             WHERE document_id = d.id AND status IN ('approved', 'signed') AND id <> p_version LOOP
        UPDATE public.document_version SET status = 'archived', updated_at = now() WHERE id = r.id;
        INSERT INTO public.document_status_event
            (document_version_id, project_id, legal_entity_id, from_status, to_status, comment, actor_id)
        VALUES (r.id, r.project_id, r.legal_entity_id, r.status, 'archived', NULL, app.user_id());
    END LOOP;
    FOR ln IN SELECT * FROM public.document_line
              WHERE version_id = p_version AND deleted_at IS NULL ORDER BY position_no LOOP
        SELECT * INTO m FROM public.material WHERE id = ln.material_id;
        IF m.legal_entity_id <> v.legal_entity_id THEN
            RAISE EXCEPTION 'stock.not_found';
        END IF;
        IF ln.material_batch_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM public.material_batch mb
            WHERE mb.id = ln.material_batch_id AND mb.material_id = m.id) THEN
            RAISE EXCEPTION 'stock.not_found';
        END IF;
        f := app.material_factor(m.id, ln.unit);
        IF f IS NULL THEN
            RAISE EXCEPTION 'material.unit_unknown';
        END IF;
        base := ln.quantity * f;
        PERFORM app.stock_lock(ln.warehouse_id, m.id);
        bal := app.stock_balance(ln.warehouse_id, m.id, ln.material_batch_id);
        IF bal < base OR app.stock_balance(ln.warehouse_id, m.id) < base THEN
            RAISE EXCEPTION 'stock.insufficient';
        END IF;
        INSERT INTO public.stock_movement (legal_entity_id, project_id, warehouse_id, material_id,
            batch_id, kind, qty, unit, quantity_in_unit, factor, document_id, version_id,
            source_line_id, project_node_id, work_id, responsible_user_id, created_by)
        VALUES (v.legal_entity_id, v.project_id, ln.warehouse_id, m.id, ln.material_batch_id,
            'issue', -base, m.base_unit, ln.quantity, f, d.id, v.id, ln.id, ln.project_node_id,
            ln.work_id, ln.responsible_user_id, app.user_id());
        n := n + 1;
    END LOOP;
    UPDATE public.document_version SET status = 'approved', updated_at = now() WHERE id = p_version;
    INSERT INTO public.document_status_event
        (document_version_id, project_id, legal_entity_id, from_status, to_status, comment, actor_id)
    VALUES (p_version, v.project_id, v.legal_entity_id, 'draft', 'approved', NULL, app.user_id());
    UPDATE public.document SET current_version_id = p_version, updated_at = now()
    WHERE id = d.id;
    RETURN n;
END $$
"""

POST_WAYBILL_AFTER = """
CREATE OR REPLACE FUNCTION app.post_waybill(p_version uuid) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    d public.document;
    ln public.document_line;
    m public.material;
    f numeric;
    base numeric;
    bal numeric;
    r public.document_version;
    n integer := 0;
BEGIN
    IF NOT app.has_permission('materials', 'write', 'stock') THEN
        RAISE EXCEPTION 'stock.forbidden';
    END IF;
    SELECT * INTO v FROM public.document_version WHERE id = p_version FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    IF v.status <> 'draft' THEN
        RAISE EXCEPTION 'stock.version_not_draft';
    END IF;
    SELECT * INTO d FROM public.document WHERE id = v.document_id;
    IF d.kind <> 'waybill_internal' THEN
        RAISE EXCEPTION 'stock.kind_invalid';
    END IF;
    IF NOT app.document_writable(d.id) THEN
        RAISE EXCEPTION 'document.forbidden';
    END IF;
    IF d.archived_at IS NOT NULL THEN
        RAISE EXCEPTION 'document.archived';
    END IF;
    IF EXISTS (SELECT 1 FROM public.document_line
               WHERE version_id = p_version AND deleted_at IS NULL
                 AND (material_id IS NULL OR warehouse_id IS NULL
                      OR project_node_id IS NULL OR responsible_user_id IS NULL)) THEN
        RAISE EXCEPTION 'stock.issue_incomplete';
    END IF;
    IF EXISTS (SELECT 1 FROM public.document_line dl
               JOIN public.warehouse w ON w.id = dl.warehouse_id
               WHERE dl.version_id = p_version AND dl.deleted_at IS NULL
                 AND w.project_id IS DISTINCT FROM v.project_id) THEN
        RAISE EXCEPTION 'stock.warehouse_project';
    END IF;
    IF EXISTS (SELECT 1 FROM public.document_line dl
               JOIN public.project_node n ON n.id = dl.project_node_id
               WHERE dl.version_id = p_version AND dl.deleted_at IS NULL
                 AND n.project_id IS DISTINCT FROM v.project_id) THEN
        RAISE EXCEPTION 'stock.not_found';
    END IF;
    FOR r IN SELECT * FROM public.document_version
             WHERE document_id = d.id AND status IN ('approved', 'signed') AND id <> p_version LOOP
        UPDATE public.document_version SET status = 'archived', updated_at = now() WHERE id = r.id;
        INSERT INTO public.document_status_event
            (document_version_id, project_id, legal_entity_id, from_status, to_status, comment, actor_id)
        VALUES (r.id, r.project_id, r.legal_entity_id, r.status, 'archived', NULL, app.user_id());
    END LOOP;
    FOR ln IN SELECT * FROM public.document_line
              WHERE version_id = p_version AND deleted_at IS NULL ORDER BY position_no LOOP
        SELECT * INTO m FROM public.material WHERE id = ln.material_id;
        IF m.legal_entity_id <> v.legal_entity_id THEN
            RAISE EXCEPTION 'stock.not_found';
        END IF;
        IF ln.material_batch_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM public.material_batch mb
            WHERE mb.id = ln.material_batch_id AND mb.material_id = m.id) THEN
            RAISE EXCEPTION 'stock.not_found';
        END IF;
        IF m.requires_certificate AND ln.material_batch_id IS NULL THEN
            RAISE EXCEPTION 'stock.batch_required';
        END IF;
        IF NOT m.requires_certificate AND ln.material_batch_id IS NOT NULL THEN
            RAISE EXCEPTION 'stock.batch_not_applicable';
        END IF;
        f := app.material_factor(m.id, ln.unit);
        IF f IS NULL THEN
            RAISE EXCEPTION 'material.unit_unknown';
        END IF;
        base := ln.quantity * f;
        PERFORM app.stock_lock(ln.warehouse_id, m.id);
        bal := app.stock_balance(ln.warehouse_id, m.id, ln.material_batch_id);
        IF bal < base OR app.stock_balance(ln.warehouse_id, m.id) < base THEN
            RAISE EXCEPTION 'stock.insufficient';
        END IF;
        INSERT INTO public.stock_movement (legal_entity_id, project_id, warehouse_id, material_id,
            batch_id, kind, qty, unit, quantity_in_unit, factor, document_id, version_id,
            source_line_id, project_node_id, work_id, responsible_user_id, created_by)
        VALUES (v.legal_entity_id, v.project_id, ln.warehouse_id, m.id, ln.material_batch_id,
            'issue', -base, m.base_unit, ln.quantity, f, d.id, v.id, ln.id, ln.project_node_id,
            ln.work_id, ln.responsible_user_id, app.user_id());
        n := n + 1;
    END LOOP;
    UPDATE public.document_version SET status = 'approved', updated_at = now() WHERE id = p_version;
    INSERT INTO public.document_status_event
        (document_version_id, project_id, legal_entity_id, from_status, to_status, comment, actor_id)
    VALUES (p_version, v.project_id, v.legal_entity_id, 'draft', 'approved', NULL, app.user_id());
    UPDATE public.document SET current_version_id = p_version, updated_at = now()
    WHERE id = d.id;
    RETURN n;
END $$
"""

VERSION_STOCK_AFTER_BEFORE = """
CREATE OR REPLACE FUNCTION app.version_stock_after() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    s public.stock_movement;
BEGIN
    IF NEW.status IN ('approved', 'signed') AND OLD.status IS DISTINCT FROM NEW.status THEN
        PERFORM app.receipt_from_m7(NEW.id);
    ELSIF NEW.status = 'archived' AND OLD.status IN ('approved', 'signed') THEN
        FOR s IN SELECT * FROM public.stock_movement m
                 WHERE m.version_id = NEW.id AND m.kind IN ('receipt', 'issue')
                   AND NOT EXISTS (SELECT 1 FROM public.stock_movement x
                                   WHERE x.reversal_of_id = m.id)
                 ORDER BY m.created_at, m.id LOOP
            PERFORM app.stock_lock(s.warehouse_id, s.material_id);
            IF s.qty > 0 AND app.stock_balance(s.warehouse_id, s.material_id) < s.qty THEN
                RAISE EXCEPTION 'stock.insufficient';
            END IF;
            INSERT INTO public.stock_movement (legal_entity_id, project_id, warehouse_id, material_id,
                batch_id, kind, qty, unit, quantity_in_unit, factor, document_id, version_id,
                source_line_id, project_node_id, work_id, responsible_user_id, reversal_of_id, created_by)
            VALUES (s.legal_entity_id, s.project_id, s.warehouse_id, s.material_id, s.batch_id,
                'reversal', -s.qty, s.unit, s.quantity_in_unit, s.factor, s.document_id, s.version_id,
                NULL, s.project_node_id, s.work_id, s.responsible_user_id, s.id, app.user_id());
        END LOOP;
    END IF;
    RETURN NULL;
END $$
"""

VERSION_STOCK_AFTER_AFTER = """
CREATE OR REPLACE FUNCTION app.version_stock_after() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    s public.stock_movement;
BEGIN
    IF NEW.status IN ('approved', 'signed') AND OLD.status IS DISTINCT FROM NEW.status THEN
        PERFORM app.receipt_from_m7(NEW.id);
    ELSIF NEW.status = 'archived' AND OLD.status IN ('approved', 'signed') THEN
        FOR s IN SELECT * FROM public.stock_movement m
                 WHERE m.version_id = NEW.id AND m.kind IN ('receipt', 'issue')
                   AND NOT EXISTS (SELECT 1 FROM public.stock_movement x
                                   WHERE x.reversal_of_id = m.id)
                 ORDER BY m.created_at, m.id LOOP
            PERFORM app.stock_lock(s.warehouse_id, s.material_id);
            IF s.qty > 0 AND app.stock_balance(s.warehouse_id, s.material_id) < s.qty THEN
                RAISE EXCEPTION 'stock.receipt_consumed';
            END IF;
            INSERT INTO public.stock_movement (legal_entity_id, project_id, warehouse_id, material_id,
                batch_id, kind, qty, unit, quantity_in_unit, factor, document_id, version_id,
                source_line_id, project_node_id, work_id, responsible_user_id, reversal_of_id, created_by)
            VALUES (s.legal_entity_id, s.project_id, s.warehouse_id, s.material_id, s.batch_id,
                'reversal', -s.qty, s.unit, s.quantity_in_unit, s.factor, s.document_id, s.version_id,
                NULL, s.project_node_id, s.work_id, s.responsible_user_id, s.id, app.user_id());
        END LOOP;
    END IF;
    RETURN NULL;
END $$
"""

DOCUMENT_KIND_WRITABLE_BEFORE = """
CREATE OR REPLACE FUNCTION app.document_kind_writable(p_kind text, p_project uuid, p_legal_entity uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE(app.is_internal() AND CASE
        WHEN app.document_kind_equipment(p_kind) THEN
            p_project IS NULL AND p_legal_entity = ANY(app.legal_entity_ids())
            AND (app.has_permission('equipment', 'write', 'documents')
                 OR (p_kind = 'equipment_technical_inspection'
                     AND app.has_permission('equipment', 'write', 'maintenance')))
        ELSE (app.has_permission('documents', 'write') AND (
                p_project = ANY(app.project_ids())
                OR (p_project IS NULL AND p_legal_entity = ANY(app.legal_entity_ids()))))
            OR (p_kind IN ('m7_act', 'waybill_internal')
                AND app.has_permission('materials', 'write', 'stock')
                AND (p_project = ANY(app.project_ids())
                     OR (p_project IS NULL AND p_legal_entity = ANY(app.legal_entity_ids()))))
    END, false)
$$
"""

DOCUMENT_KIND_WRITABLE_AFTER = """
CREATE OR REPLACE FUNCTION app.document_kind_writable(p_kind text, p_project uuid, p_legal_entity uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE(app.is_internal() AND CASE
        WHEN app.document_kind_equipment(p_kind) THEN
            p_project IS NULL AND p_legal_entity = ANY(app.legal_entity_ids())
            AND (app.has_permission('equipment', 'write', 'documents')
                 OR (p_kind = 'equipment_technical_inspection'
                     AND app.has_permission('equipment', 'write', 'maintenance')))
        ELSE (app.has_permission('documents', 'write') AND (
                p_project = ANY(app.project_ids())
                OR (p_project IS NULL AND p_legal_entity = ANY(app.legal_entity_ids()))))
            OR (p_kind IN ('m7_act', 'waybill_internal')
                AND app.has_permission('materials', 'write', 'stock')
                AND (p_project = ANY(app.project_ids())
                     OR (p_project IS NULL AND p_legal_entity = ANY(app.legal_entity_ids()))))
            OR (p_kind = 'material_certificate' AND app.has_permission('materials', 'write', 'batch')
                AND p_project IS NULL AND p_legal_entity = ANY(app.legal_entity_ids()))
    END, false)
$$
"""

VERSION_FILE_GUARD = """
CREATE OR REPLACE FUNCTION app.version_file_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    k text;
BEGIN
    IF NEW.s3_key IS NULL THEN
        SELECT d.kind INTO k FROM public.document d WHERE d.id = NEW.document_id;
        IF k IS NULL OR k NOT IN ('waybill_internal', 'inventory_act') THEN
            RAISE EXCEPTION 'document.file_required';
        END IF;
    END IF;
    RETURN NEW;
END $$
"""

BATCH_POLICY_AFTER = (
    "CREATE POLICY material_batch_write_app ON material_batch FOR ALL TO kut_app USING ("
    " (SELECT app.is_internal()) AND ((SELECT app.has_permission('materials', 'write', 'catalog'))"
    " OR (SELECT app.has_permission('materials', 'write', 'batch')))"
    " AND legal_entity_id = ANY ((SELECT app.legal_entity_ids())::uuid[]))"
    " WITH CHECK ("
    " (SELECT app.is_internal()) AND ((SELECT app.has_permission('materials', 'write', 'catalog'))"
    " OR (SELECT app.has_permission('materials', 'write', 'batch')))"
    " AND legal_entity_id = ANY ((SELECT app.legal_entity_ids())::uuid[]))"
)
BATCH_POLICY_BEFORE = (
    "CREATE POLICY material_batch_write_app ON material_batch FOR ALL TO kut_app USING ("
    " (SELECT app.is_internal()) AND (SELECT app.has_permission('materials', 'write', 'catalog'))"
    " AND legal_entity_id = ANY ((SELECT app.legal_entity_ids())::uuid[]))"
    " WITH CHECK ("
    " (SELECT app.is_internal()) AND (SELECT app.has_permission('materials', 'write', 'catalog'))"
    " AND legal_entity_id = ANY ((SELECT app.legal_entity_ids())::uuid[]))"
)


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _scope_check(legal: Sequence[str]) -> str:
    return (
        f"CASE WHEN kind IN ({_in(OBJECT_NEW)}) THEN project_id IS NOT NULL"
        f" WHEN kind IN ({_in(legal)}) THEN project_id IS NULL ELSE true END"
    )


def _set_functions(bodies: Sequence[str]) -> None:
    op.execute("SET LOCAL ROLE kut_auth")
    for body in bodies:
        op.execute(body)
    op.execute("RESET ROLE")


FILE_COLUMNS = ("s3_key", "content_type", "size_bytes", "checksum_sha256")


def upgrade() -> None:
    for column in FILE_COLUMNS:
        op.alter_column("document_version", column, nullable=True)
    op.create_check_constraint(
        op.f("ck_document_version_file_all_or_none"),
        "document_version",
        "(s3_key IS NULL AND content_type IS NULL AND size_bytes IS NULL AND checksum_sha256 IS NULL)"
        " OR (s3_key IS NOT NULL AND content_type IS NOT NULL AND size_bytes IS NOT NULL"
        " AND checksum_sha256 IS NOT NULL)",
    )
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(VERSION_FILE_GUARD)
    op.execute("GRANT EXECUTE ON FUNCTION app.version_file_guard() TO kut_owner")
    op.execute("RESET ROLE")
    op.execute(
        "CREATE TRIGGER document_version_file_guard BEFORE INSERT ON document_version"
        " FOR EACH ROW EXECUTE FUNCTION app.version_file_guard()"
    )
    op.drop_constraint(op.f("ck_document_kind_valid"), "document", type_="check")
    op.create_check_constraint(
        op.f("ck_document_kind_valid"), "document", f"kind IN ({_in(KINDS_AFTER)})"
    )
    op.drop_constraint(
        op.f("ck_document_number_counter_kind_valid"), "document_number_counter", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_document_number_counter_kind_valid"),
        "document_number_counter",
        f"kind IN ({_in(KINDS_AFTER)})",
    )
    op.drop_constraint(op.f("ck_document_scope_valid"), "document", type_="check")
    op.create_check_constraint(
        op.f("ck_document_scope_valid"), "document", _scope_check(LEGAL_AFTER)
    )
    op.execute("DROP POLICY material_batch_write_app ON material_batch")
    op.execute(BATCH_POLICY_AFTER)
    _set_functions(
        (
            MATERIAL_FACTOR_AFTER,
            POST_WAYBILL_AFTER,
            VERSION_STOCK_AFTER_AFTER,
            DOCUMENT_KIND_WRITABLE_AFTER,
        )
    )


def downgrade() -> None:
    _set_functions(
        (
            MATERIAL_FACTOR_BEFORE,
            POST_WAYBILL_BEFORE,
            VERSION_STOCK_AFTER_BEFORE,
            DOCUMENT_KIND_WRITABLE_BEFORE,
        )
    )
    op.execute("DROP POLICY material_batch_write_app ON material_batch")
    op.execute(BATCH_POLICY_BEFORE)
    op.drop_constraint(op.f("ck_document_scope_valid"), "document", type_="check")
    op.create_check_constraint(
        op.f("ck_document_scope_valid"), "document", _scope_check(LEGAL_BEFORE)
    )
    op.drop_constraint(
        op.f("ck_document_number_counter_kind_valid"), "document_number_counter", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_document_number_counter_kind_valid"),
        "document_number_counter",
        f"kind IN ({_in(KINDS_BEFORE)})",
    )
    op.drop_constraint(op.f("ck_document_kind_valid"), "document", type_="check")
    op.create_check_constraint(
        op.f("ck_document_kind_valid"), "document", f"kind IN ({_in(KINDS_BEFORE)})"
    )
    op.execute("DROP TRIGGER IF EXISTS document_version_file_guard ON document_version")
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute("DROP FUNCTION IF EXISTS app.version_file_guard()")
    op.execute("RESET ROLE")
    op.drop_constraint(
        op.f("ck_document_version_file_all_or_none"), "document_version", type_="check"
    )
    for column in FILE_COLUMNS:
        op.alter_column("document_version", column, nullable=False)
