"""S7a: функции склада (приход из М-7, проведение ВН, сторно), сертификат, маршруты по группам видов

Revision ID: d9a4c7e2f6b1
Revises: b6f1e3a9d2c7
Create Date: 2026-10-21 11:00:00

ADR-016 (часть 5). Приход - триггер на переход версии М-7 в approved или signed (повтор не даёт
второго движения: уникальность по source_line_id). ВН проводит кладовщик сразу, без маршрута.
Остаток не уходит в минус: блокировка по (склад, материал) и проверка под ней. Сертификат
проверяется при отправке М-7 на подтверждение (route_send) и на приходе. Маршрут выбирается по
группе вида: склад (М-7), закупка (ЗМ), инвентаризация (ИНВ), прочие - по финансовому признаку.
Кладовщик вносит М-7 и ВН по праву materials:write:stock; остальные виды ему закрыты.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d9a4c7e2f6b1"
down_revision: str | Sequence[str] | None = "b6f1e3a9d2c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STOCK_KINDS = "'m7_act', 'waybill_internal'"

MATERIAL_FACTOR = """
CREATE FUNCTION app.material_factor(p_material uuid, p_unit text) RETURNS numeric
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT CASE WHEN p_unit = m.base_unit THEN 1::numeric ELSE (
        SELECT u.factor FROM public.material_unit u
        WHERE u.material_id = m.id AND u.unit = p_unit AND u.deleted_at IS NULL) END
    FROM public.material m WHERE m.id = p_material AND m.deleted_at IS NULL
$$
"""

STOCK_BALANCE = """
CREATE FUNCTION app.stock_balance(p_warehouse uuid, p_material uuid, p_batch uuid DEFAULT NULL)
RETURNS numeric LANGUAGE sql STABLE AS $$
    SELECT COALESCE(sum(s.qty), 0) FROM public.stock_movement s
    WHERE s.warehouse_id = p_warehouse AND s.material_id = p_material
      AND (p_batch IS NULL OR s.batch_id = p_batch)
$$
"""

STOCK_LOCK = """
CREATE FUNCTION app.stock_lock(p_warehouse uuid, p_material uuid) RETURNS void
LANGUAGE sql AS $$
    SELECT pg_advisory_xact_lock(hashtextextended(p_warehouse::text || ':' || p_material::text, 0))
$$
"""

ROUTE_TEMPLATE_CODE = """
CREATE FUNCTION app.route_template_code(p_kind text, p_financial boolean) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE p_kind
        WHEN 'm7_act' THEN 'warehouse'
        WHEN 'material_request' THEN 'procurement'
        WHEN 'inventory_act' THEN 'inventory'
        ELSE CASE WHEN p_financial THEN 'money' ELSE 'other' END
    END
$$
"""

RECEIPT_FROM_M7 = """
CREATE FUNCTION app.receipt_from_m7(p_version uuid) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    d public.document;
    ln public.document_line;
    m public.material;
    b public.material_batch;
    w public.warehouse;
    f numeric;
    n integer := 0;
BEGIN
    SELECT * INTO v FROM public.document_version WHERE id = p_version;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    SELECT * INTO d FROM public.document WHERE id = v.document_id;
    IF d.kind <> 'm7_act' THEN
        RETURN 0;
    END IF;
    IF EXISTS (SELECT 1 FROM public.document_line
               WHERE version_id = p_version AND deleted_at IS NULL AND material_id IS NULL) THEN
        RAISE EXCEPTION 'stock.material_required';
    END IF;
    FOR ln IN SELECT * FROM public.document_line
              WHERE version_id = p_version AND deleted_at IS NULL AND material_id IS NOT NULL
              ORDER BY position_no LOOP
        SELECT * INTO m FROM public.material WHERE id = ln.material_id;
        IF m.legal_entity_id <> v.legal_entity_id THEN
            RAISE EXCEPTION 'stock.not_found';
        END IF;
        IF ln.material_batch_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM public.material_batch mb
            WHERE mb.id = ln.material_batch_id AND mb.material_id = m.id) THEN
            RAISE EXCEPTION 'stock.not_found';
        END IF;
        IF ln.warehouse_id IS NULL THEN
            RAISE EXCEPTION 'stock.warehouse_required';
        END IF;
        SELECT * INTO w FROM public.warehouse WHERE id = ln.warehouse_id;
        IF w.project_id IS DISTINCT FROM v.project_id THEN
            RAISE EXCEPTION 'stock.warehouse_project';
        END IF;
        f := app.material_factor(m.id, ln.unit);
        IF f IS NULL THEN
            RAISE EXCEPTION 'material.unit_unknown';
        END IF;
        IF m.requires_certificate THEN
            IF ln.material_batch_id IS NULL THEN
                RAISE EXCEPTION 'material.certificate_required';
            END IF;
            SELECT * INTO b FROM public.material_batch WHERE id = ln.material_batch_id;
            IF b.certificate_document_id IS NULL THEN
                RAISE EXCEPTION 'material.certificate_required';
            END IF;
        END IF;
        PERFORM app.stock_lock(w.id, m.id);
        INSERT INTO public.stock_movement (legal_entity_id, project_id, warehouse_id, material_id,
            batch_id, kind, qty, unit, quantity_in_unit, factor, document_id, version_id,
            source_line_id, created_by)
        VALUES (v.legal_entity_id, v.project_id, w.id, m.id, ln.material_batch_id, 'receipt',
            ln.quantity * f, m.base_unit, ln.quantity, f, d.id, v.id, ln.id, app.user_id())
        ON CONFLICT (source_line_id) WHERE kind = 'receipt' AND source_line_id IS NOT NULL
        DO NOTHING;
        IF FOUND THEN
            n := n + 1;
        END IF;
    END LOOP;
    RETURN n;
END $$
"""

VERSION_STOCK_AFTER = """
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

POST_WAYBILL = """
CREATE FUNCTION app.post_waybill(p_version uuid) RETURNS integer
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

STOCK_REVERSE = """
CREATE FUNCTION app.stock_reverse(p_movement uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    s public.stock_movement;
    new_id uuid;
BEGIN
    IF NOT app.has_permission('materials', 'write', 'stock') THEN
        RAISE EXCEPTION 'stock.forbidden';
    END IF;
    SELECT * INTO s FROM public.stock_movement WHERE id = p_movement;
    IF NOT FOUND OR NOT (s.project_id = ANY (app.project_ids())) THEN
        RAISE EXCEPTION 'stock.not_found';
    END IF;
    IF s.kind = 'reversal' THEN
        RAISE EXCEPTION 'stock.reversal_of_reversal';
    END IF;
    PERFORM app.stock_lock(s.warehouse_id, s.material_id);
    IF s.qty > 0 AND app.stock_balance(s.warehouse_id, s.material_id) < s.qty THEN
        RAISE EXCEPTION 'stock.insufficient';
    END IF;
    INSERT INTO public.stock_movement (legal_entity_id, project_id, warehouse_id, material_id,
        batch_id, kind, qty, unit, quantity_in_unit, factor, document_id, version_id,
        source_line_id, project_node_id, work_id, responsible_user_id, reversal_of_id, created_by)
    VALUES (s.legal_entity_id, s.project_id, s.warehouse_id, s.material_id, s.batch_id,
        'reversal', -s.qty, s.unit, s.quantity_in_unit, s.factor, s.document_id, s.version_id,
        NULL, s.project_node_id, s.work_id, s.responsible_user_id, s.id, app.user_id())
    RETURNING id INTO new_id;
    RETURN new_id;
END $$
"""

ROUTE_SEND = """
CREATE OR REPLACE FUNCTION app.route_send(p_version uuid) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    d public.document;
    tmpl uuid;
    attempt integer;
BEGIN
    IF app.user_id() IS NULL THEN
        RAISE EXCEPTION 'approval.forbidden';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(p_version::text, 0));
    SELECT * INTO v FROM public.document_version WHERE id = p_version;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    IF v.status <> 'draft' THEN
        RAISE EXCEPTION 'approval.version_not_draft';
    END IF;
    SELECT * INTO d FROM public.document WHERE id = v.document_id;
    IF d.archived_at IS NOT NULL THEN
        RAISE EXCEPTION 'document.archived';
    END IF;
    IF NOT app.document_writable(d.id) THEN
        RAISE EXCEPTION 'document.forbidden';
    END IF;
    IF d.kind = 'waybill_internal' THEN
        RAISE EXCEPTION 'stock.kind_invalid';
    END IF;
    IF d.kind = 'm7_act' AND EXISTS (
        SELECT 1 FROM public.document_line ln
        JOIN public.material m ON m.id = ln.material_id
        WHERE ln.version_id = p_version AND ln.deleted_at IS NULL AND m.requires_certificate
          AND NOT EXISTS (SELECT 1 FROM public.material_batch b
                          WHERE b.id = ln.material_batch_id
                            AND b.certificate_document_id IS NOT NULL)) THEN
        RAISE EXCEPTION 'material.certificate_required';
    END IF;
    SELECT t.id INTO tmpl FROM public.approval_route_template t
    WHERE t.code = app.route_template_code(d.kind, d.financial) AND t.deleted_at IS NULL;
    attempt := coalesce((SELECT max(s.attempt) FROM public.version_route_step s
                         WHERE s.version_id = p_version), 0) + 1;
    INSERT INTO public.version_route_step (document_id, version_id, legal_entity_id, project_id,
        attempt, position, role_code, status)
    SELECT d.id, v.id, v.legal_entity_id, v.project_id, attempt, st.position, st.role_code, 'pending'
    FROM public.approval_route_step_template st
    WHERE st.template_id = tmpl AND st.deleted_at IS NULL
    ORDER BY st.position;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'approval.template_empty';
    END IF;
    PERFORM app.version_move(p_version, 'on_approval', NULL, app.user_id());
    PERFORM app.notify_current_step(p_version);
    RETURN attempt;
END $$
"""

DOCUMENT_WRITABLE = """
CREATE OR REPLACE FUNCTION app.document_writable(p_document uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE((
        SELECT app.document_kind_writable(d.kind, d.project_id, d.legal_entity_id)
        FROM public.document d WHERE d.id = p_document), false)
$$
"""

LINE_WRITABLE = """
CREATE OR REPLACE FUNCTION app.line_writable(p_document uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE(app.document_writable(p_document) AND (
        app.has_permission('documents', 'write', 'lines')
        OR (app.has_permission('materials', 'write', 'stock') AND EXISTS (
            SELECT 1 FROM public.document d
            WHERE d.id = p_document AND d.kind IN ('m7_act', 'waybill_internal')))), false)
$$
"""

DOCUMENT_KIND_WRITABLE = """
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


ROUTE_SEND_BEFORE_S7A = """
CREATE OR REPLACE FUNCTION app.route_send(p_version uuid) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    d public.document;
    tmpl uuid;
    attempt integer;
BEGIN
    IF app.user_id() IS NULL THEN
        RAISE EXCEPTION 'approval.forbidden';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(p_version::text, 0));
    SELECT * INTO v FROM public.document_version WHERE id = p_version;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    IF v.status <> 'draft' THEN
        RAISE EXCEPTION 'approval.version_not_draft';
    END IF;
    SELECT * INTO d FROM public.document WHERE id = v.document_id;
    IF d.archived_at IS NOT NULL THEN
        RAISE EXCEPTION 'document.archived';
    END IF;
    IF NOT app.document_writable(d.id) THEN
        RAISE EXCEPTION 'document.forbidden';
    END IF;
    SELECT t.id INTO tmpl FROM public.approval_route_template t
    WHERE t.code = CASE WHEN d.financial THEN 'money' ELSE 'other' END AND t.deleted_at IS NULL;
    attempt := coalesce((SELECT max(s.attempt) FROM public.version_route_step s
                         WHERE s.version_id = p_version), 0) + 1;
    INSERT INTO public.version_route_step (document_id, version_id, legal_entity_id, project_id,
        attempt, position, role_code, status)
    SELECT d.id, v.id, v.legal_entity_id, v.project_id, attempt, st.position, st.role_code, 'pending'
    FROM public.approval_route_step_template st
    WHERE st.template_id = tmpl AND st.deleted_at IS NULL
    ORDER BY st.position;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'approval.template_empty';
    END IF;
    PERFORM app.version_move(p_version, 'on_approval', NULL, app.user_id());
    PERFORM app.notify_current_step(p_version);
    RETURN attempt;
END $$
"""

DOCUMENT_KIND_WRITABLE_BEFORE_S7A = """
CREATE OR REPLACE FUNCTION app.document_kind_writable(p_kind text, p_project uuid, p_legal_entity uuid)
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
"""


def upgrade() -> None:
    op.execute("SET LOCAL ROLE kut_auth")
    for definition in (
        MATERIAL_FACTOR,
        STOCK_BALANCE,
        STOCK_LOCK,
        ROUTE_TEMPLATE_CODE,
        RECEIPT_FROM_M7,
        VERSION_STOCK_AFTER,
        POST_WAYBILL,
        STOCK_REVERSE,
    ):
        op.execute(definition)
    op.execute(ROUTE_SEND)
    op.execute(DOCUMENT_WRITABLE)
    op.execute(LINE_WRITABLE)
    op.execute(DOCUMENT_KIND_WRITABLE)
    op.execute("GRANT EXECUTE ON FUNCTION app.version_stock_after() TO kut_owner")
    op.execute("RESET ROLE")

    op.execute(
        "CREATE TRIGGER document_version_stock_after AFTER UPDATE OF status ON document_version"
        " FOR EACH ROW EXECUTE FUNCTION app.version_stock_after()"
    )
    op.execute(
        "CREATE FUNCTION app.stock_movement_append_only() RETURNS trigger LANGUAGE plpgsql AS $$"
        " BEGIN RAISE EXCEPTION 'stock.append_only'; END $$"
    )
    op.execute(
        "CREATE TRIGGER stock_movement_append_only BEFORE UPDATE OR DELETE ON stock_movement"
        " FOR EACH ROW EXECUTE FUNCTION app.stock_movement_append_only()"
    )

    # Исполнение: балансы - обычному запросу (RLS), остальное - только приложению по праву.
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute("REVOKE ALL ON FUNCTION app.receipt_from_m7(uuid) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION app.post_waybill(uuid) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION app.stock_reverse(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION app.post_waybill(uuid) TO kut_app")
    op.execute("GRANT EXECUTE ON FUNCTION app.stock_reverse(uuid) TO kut_app")
    op.execute("GRANT EXECUTE ON FUNCTION app.stock_balance(uuid, uuid, uuid) TO kut_app")
    op.execute("GRANT EXECUTE ON FUNCTION app.material_factor(uuid, text) TO kut_app")
    op.execute("GRANT EXECUTE ON FUNCTION app.route_template_code(text, boolean) TO kut_app")
    op.execute("RESET ROLE")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS stock_movement_append_only ON stock_movement")
    op.execute("DROP FUNCTION IF EXISTS app.stock_movement_append_only()")
    op.execute("DROP TRIGGER IF EXISTS document_version_stock_after ON document_version")
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute("DROP FUNCTION IF EXISTS app.version_stock_after()")
    op.execute("DROP FUNCTION IF EXISTS app.stock_reverse(uuid)")
    op.execute("DROP FUNCTION IF EXISTS app.post_waybill(uuid)")
    op.execute("DROP FUNCTION IF EXISTS app.receipt_from_m7(uuid)")
    op.execute("DROP FUNCTION IF EXISTS app.stock_lock(uuid, uuid)")
    op.execute("DROP FUNCTION IF EXISTS app.stock_balance(uuid, uuid, uuid)")
    op.execute("DROP FUNCTION IF EXISTS app.material_factor(uuid, text)")
    op.execute("DROP FUNCTION IF EXISTS app.route_template_code(text, boolean)")
    op.execute("RESET ROLE")
    # Прежние определения ролей и маршрута (до S7a) - в предыдущих ревизиях; здесь откатываем
    # только права на документы кладовщика и маршрут по финансовому признаку.
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(ROUTE_SEND_BEFORE_S7A)
    op.execute(DOCUMENT_KIND_WRITABLE_BEFORE_S7A)
    op.execute(
        "CREATE OR REPLACE FUNCTION app.document_writable(p_document uuid) RETURNS boolean"
        " LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$"
        " SELECT COALESCE((SELECT app.document_kind_writable(d.kind, d.project_id, d.legal_entity_id)"
        " FROM public.document d WHERE d.id = p_document), false) $$"
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION app.line_writable(p_document uuid) RETURNS boolean"
        " LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$"
        " SELECT COALESCE(app.document_writable(p_document)"
        " AND app.has_permission('documents', 'write', 'lines'), false) $$"
    )
    op.execute("RESET ROLE")
