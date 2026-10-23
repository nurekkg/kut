"""S7b (исправления ревью кода): откат и сторно по партии, приход с партией только сертифицированного
материала. Ревизия поверх e7a2c9d4b1f8: функции переопределяются, таблиц не меняем."""

from collections.abc import Sequence

from alembic import op

revision: str = "f3b8d1e6a4c2"
down_revision: str | Sequence[str] | None = "e7a2c9d4b1f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
            IF s.qty > 0 AND (app.stock_balance(s.warehouse_id, s.material_id) < s.qty OR app.stock_balance(s.warehouse_id, s.material_id, s.batch_id) < s.qty) THEN
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

RECEIPT_FROM_M7_BEFORE = """
CREATE OR REPLACE FUNCTION app.receipt_from_m7(p_version uuid) RETURNS integer
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

RECEIPT_FROM_M7_AFTER = """
CREATE OR REPLACE FUNCTION app.receipt_from_m7(p_version uuid) RETURNS integer
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
        IF NOT m.requires_certificate AND ln.material_batch_id IS NOT NULL THEN
            RAISE EXCEPTION 'stock.batch_not_applicable';
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

STOCK_REVERSE_BEFORE = """
CREATE OR REPLACE FUNCTION app.stock_reverse(p_movement uuid) RETURNS uuid
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

STOCK_REVERSE_AFTER = """
CREATE OR REPLACE FUNCTION app.stock_reverse(p_movement uuid) RETURNS uuid
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
    IF s.qty > 0 AND (app.stock_balance(s.warehouse_id, s.material_id) < s.qty OR app.stock_balance(s.warehouse_id, s.material_id, s.batch_id) < s.qty) THEN
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


def upgrade() -> None:
    op.execute("SET LOCAL ROLE kut_auth")
    for body in (VERSION_STOCK_AFTER_AFTER, RECEIPT_FROM_M7_AFTER, STOCK_REVERSE_AFTER):
        op.execute(body)
    op.execute("RESET ROLE")


def downgrade() -> None:
    op.execute("SET LOCAL ROLE kut_auth")
    for body in (VERSION_STOCK_AFTER_BEFORE, RECEIPT_FROM_M7_BEFORE, STOCK_REVERSE_BEFORE):
        op.execute(body)
    op.execute("RESET ROLE")
