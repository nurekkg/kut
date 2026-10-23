"""S7c: инвентаризация (ADR-016 часть 5). Акт пересчёта (inventory_act, без файла).

Предзаполнение даёт строки без факта (quantity NULL): факт вводит кладовщик, отправка на маршрут с пустым фактом
отказывает (inventory.not_counted). Отправка фиксирует учётный остаток строки (snapshot_qty, функция склада).
Утверждение (последний шаг маршрута прораб -> техдиректор) создаёт по одной корректировке stock_movement
kind=adjustment на расхождение (факт минус снимок) со ссылкой на строку; повтор события без второй корректировки
(уникальный индекс + ON CONFLICT). Корректировка не уводит остаток в минус. Факт 0 (всё списано) и пустой факт
допустимы только в инвентаризации; в остальных видах количество строго положительное (правило в гарде строк,
потому что CHECK не видит вид документа). Гард строк пропускает обновление одного только снимка: его ставит
функция склада при отправке, а приложение колонку снимка не пишет (грант UPDATE по колонкам).
Снимок отказывает, если версия блокирована (inventory.version_locked) или по складу и материалу
уже есть акт на согласовании (inventory.in_progress): иначе корректировки считаются дважды.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7e1a4d9b2f6"
down_revision: str | Sequence[str] | None = "b8e2d5a1c9f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SNAPSHOT = """
CREATE FUNCTION app.inventory_snapshot(p_version uuid) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    ln public.document_line;
    n integer;
    ddoc uuid;
    dproject uuid;
    dlegal uuid;
BEGIN
    SELECT d.id, d.project_id, d.legal_entity_id INTO ddoc, dproject, dlegal
    FROM public.document_version v
    JOIN public.document d ON d.id = v.document_id
    WHERE v.id = p_version AND d.kind = 'inventory_act';
    IF NOT FOUND THEN
        RETURN 0;
    END IF;
    -- Утверждённая или согласуемая версия блокирует новую: архив сторнирует её корректировки.
    PERFORM pg_advisory_xact_lock(hashtextextended(ddoc::text, 1));
    IF EXISTS (SELECT 1 FROM public.document_version
               WHERE document_id = ddoc AND id <> p_version
                 AND status IN ('on_approval', 'approved', 'signed')) THEN
        RAISE EXCEPTION 'inventory.version_locked';
    END IF;
    IF EXISTS (SELECT 1 FROM public.document_line
               WHERE version_id = p_version AND deleted_at IS NULL AND quantity IS NULL) THEN
        RAISE EXCEPTION 'inventory.not_counted';
    END IF;
    IF EXISTS (SELECT 1 FROM public.document_line
               WHERE version_id = p_version AND deleted_at IS NULL
                 AND (warehouse_id IS NULL OR material_id IS NULL)) THEN
        RAISE EXCEPTION 'inventory.line_incomplete';
    END IF;
    IF EXISTS (SELECT 1 FROM public.document_line
               WHERE version_id = p_version AND deleted_at IS NULL
               GROUP BY warehouse_id, material_id, material_batch_id HAVING count(*) > 1) THEN
        RAISE EXCEPTION 'inventory.duplicate_line';
    END IF;
    IF EXISTS (SELECT 1 FROM public.document_line l
               JOIN public.material m ON m.id = l.material_id
               WHERE l.version_id = p_version AND l.deleted_at IS NULL
                 AND m.requires_certificate AND l.material_batch_id IS NULL) THEN
        RAISE EXCEPTION 'inventory.batch_required';
    END IF;
    IF EXISTS (SELECT 1 FROM public.document_line l
               JOIN public.material m ON m.id = l.material_id
               WHERE l.version_id = p_version AND l.deleted_at IS NULL
                 AND NOT m.requires_certificate AND l.material_batch_id IS NOT NULL) THEN
        RAISE EXCEPTION 'stock.batch_not_applicable';
    END IF;
    -- Склад - объекта акта, партия - того же материала и юрлица, единица - базовая: иначе снимок читает
    -- чужой остаток, а корректировка пишет в чужой склад или в неверной шкале.
    IF EXISTS (SELECT 1 FROM public.document_line l
               JOIN public.warehouse w ON w.id = l.warehouse_id
               WHERE l.version_id = p_version AND l.deleted_at IS NULL
                 AND (w.project_id IS DISTINCT FROM dproject OR w.legal_entity_id IS DISTINCT FROM dlegal)) THEN
        RAISE EXCEPTION 'stock.warehouse_project';
    END IF;
    IF EXISTS (SELECT 1 FROM public.document_line l
               JOIN public.material_batch b ON b.id = l.material_batch_id
               WHERE l.version_id = p_version AND l.deleted_at IS NULL
                 AND (b.material_id IS DISTINCT FROM l.material_id
                      OR b.legal_entity_id IS DISTINCT FROM dlegal)) THEN
        RAISE EXCEPTION 'inventory.batch_mismatch';
    END IF;
    IF EXISTS (SELECT 1 FROM public.document_line l
               JOIN public.material m ON m.id = l.material_id
               WHERE l.version_id = p_version AND l.deleted_at IS NULL AND l.unit <> m.base_unit) THEN
        RAISE EXCEPTION 'inventory.unit_mismatch';
    END IF;
    FOR ln IN SELECT * FROM public.document_line
              WHERE version_id = p_version AND deleted_at IS NULL
              ORDER BY warehouse_id, material_id LOOP
        PERFORM app.stock_lock(ln.warehouse_id, ln.material_id);
    END LOOP;
    -- Второй акт того же склада и материала ждёт первого: снимок не видит его корректировок.
    IF EXISTS (SELECT 1 FROM public.document_line mine
               JOIN public.document_line other ON other.warehouse_id = mine.warehouse_id
                                              AND other.material_id = mine.material_id
               JOIN public.document_version ov ON ov.id = other.version_id
               JOIN public.document od ON od.id = ov.document_id AND od.kind = 'inventory_act'
               WHERE mine.version_id = p_version AND mine.deleted_at IS NULL
                 AND other.deleted_at IS NULL AND ov.status = 'on_approval'
                 AND ov.id <> p_version) THEN
        RAISE EXCEPTION 'inventory.in_progress';
    END IF;
    UPDATE public.document_line l
       SET snapshot_qty = app.stock_balance(l.warehouse_id, l.material_id, l.material_batch_id)
     WHERE l.version_id = p_version AND l.deleted_at IS NULL;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END $$
"""

ADJUST = """
CREATE FUNCTION app.inventory_adjust(p_version uuid) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    d public.document;
    ln public.document_line;
    m public.material;
    delta numeric;
    bal numeric;
    done integer;
    n integer := 0;
BEGIN
    SELECT * INTO v FROM public.document_version WHERE id = p_version;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    SELECT * INTO d FROM public.document WHERE id = v.document_id;
    IF d.kind <> 'inventory_act' THEN
        RETURN 0;
    END IF;
    FOR ln IN SELECT * FROM public.document_line
              WHERE version_id = p_version AND deleted_at IS NULL ORDER BY position_no LOOP
        IF ln.snapshot_qty IS NULL THEN
            RAISE EXCEPTION 'inventory.no_snapshot';
        END IF;
        delta := ln.quantity - ln.snapshot_qty;
        CONTINUE WHEN delta = 0;
        SELECT * INTO m FROM public.material WHERE id = ln.material_id;
        PERFORM app.stock_lock(ln.warehouse_id, ln.material_id);
        bal := app.stock_balance(ln.warehouse_id, ln.material_id, ln.material_batch_id);
        IF bal + delta < 0 THEN
            RAISE EXCEPTION 'inventory.negative';
        END IF;
        INSERT INTO public.stock_movement (legal_entity_id, project_id, warehouse_id, material_id,
            batch_id, kind, qty, unit, quantity_in_unit, factor, document_id, version_id,
            source_line_id, created_by)
        VALUES (d.legal_entity_id, d.project_id, ln.warehouse_id, ln.material_id, ln.material_batch_id,
            'adjustment', delta, m.base_unit, abs(delta), 1, d.id, v.id, ln.id, app.user_id())
        ON CONFLICT (source_line_id) WHERE kind = 'adjustment' AND source_line_id IS NOT NULL
        DO NOTHING;
        GET DIAGNOSTICS done = ROW_COUNT;
        n := n + done;
    END LOOP;
    RETURN n;
END $$
"""

# Версия функции до S7c (откат). Текст совпадает с ревизией f3b8d1e6a4c2.
VERSION_STOCK_AFTER_PREV = """
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

VERSION_STOCK_AFTER_NEW = """
CREATE OR REPLACE FUNCTION app.version_stock_after() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    s public.stock_movement;
    origin uuid;
BEGIN
    IF NEW.status = 'on_approval' AND OLD.status IS DISTINCT FROM NEW.status THEN
        PERFORM app.inventory_snapshot(NEW.id);
    ELSIF NEW.status IN ('approved', 'signed') AND OLD.status IS DISTINCT FROM NEW.status THEN
        PERFORM app.receipt_from_m7(NEW.id);
        PERFORM app.inventory_adjust(NEW.id);
    ELSIF NEW.status = 'archived' AND OLD.status IN ('approved', 'signed') THEN
        -- Подписанная копия наследует движения утверждённой версии: архив родителя при подписи их не снимает.
        -- Архив подписанной копии снимает движения её родителя.
        IF OLD.status = 'approved' AND EXISTS (SELECT 1 FROM public.document_version c
                                               WHERE c.parent_version_id = NEW.id AND c.status = 'signed') THEN
            RETURN NULL;
        END IF;
        origin := CASE WHEN OLD.status = 'signed' THEN NEW.parent_version_id ELSE NEW.id END;
        FOR s IN SELECT * FROM public.stock_movement m
                 WHERE m.version_id = origin AND m.kind IN ('receipt', 'issue', 'adjustment')
                   AND NOT EXISTS (SELECT 1 FROM public.stock_movement x
                                   WHERE x.reversal_of_id = m.id)
                 ORDER BY m.created_at, m.id LOOP
            PERFORM app.stock_lock(s.warehouse_id, s.material_id);
            IF s.qty > 0 AND (app.stock_balance(s.warehouse_id, s.material_id) < s.qty OR app.stock_balance(s.warehouse_id, s.material_id, s.batch_id) < s.qty) THEN
                IF s.kind = 'adjustment' THEN
                    RAISE EXCEPTION 'stock.adjustment_consumed';
                END IF;
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

# Гард строк до S7c (откат): текст совпадает с ревизией 20261019_1000 (LINE_GUARD_NEW, активный до S7c).
GUARD_PREV = """
CREATE OR REPLACE FUNCTION app.document_line_guard() RETURNS trigger
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
    IF d.archived_at IS NOT NULL THEN
        RAISE EXCEPTION 'document.archived';
    END IF;
    IF NOT app.document_kind_lines(d.kind) THEN
        RAISE EXCEPTION 'line.kind_not_allowed';
    END IF;
    IF NEW.project_node_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.project_node n WHERE n.id = NEW.project_node_id AND n.project_id = d.project_id
    ) THEN
        RAISE EXCEPTION 'line.node_invalid';
    END IF;
    IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL
       AND EXISTS (SELECT 1 FROM public.document_line_price p
                   WHERE p.line_id = NEW.id AND p.deleted_at IS NULL)
       AND NOT app.document_money_right(NEW.document_id) THEN
        RAISE EXCEPTION 'line.priced_delete_forbidden';
    END IF;
    RETURN NEW;
END $$
"""

GUARD_NEW = """
CREATE OR REPLACE FUNCTION app.document_line_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    d public.document;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'line.delete_forbidden';
    END IF;
    IF TG_OP = 'INSERT' AND NEW.snapshot_qty IS NOT NULL THEN
        RAISE EXCEPTION 'line.snapshot_forbidden';
    END IF;
    -- Снимок остатка ставит функция склада при отправке на маршрут: меняется только snapshot_qty.
    IF TG_OP = 'UPDATE' AND (to_jsonb(NEW) - 'snapshot_qty') = (to_jsonb(OLD) - 'snapshot_qty') THEN
        RETURN NEW;
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
    IF d.archived_at IS NOT NULL THEN
        RAISE EXCEPTION 'document.archived';
    END IF;
    IF NOT app.document_kind_lines(d.kind) THEN
        RAISE EXCEPTION 'line.kind_not_allowed';
    END IF;
    -- Пустой факт и ноль допустимы только в инвентаризации (в CHECK вид документа не виден).
    IF (NEW.quantity IS NULL OR NEW.quantity <= 0) AND d.kind <> 'inventory_act' THEN
        RAISE EXCEPTION 'line.quantity_invalid';
    END IF;
    IF NEW.project_node_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.project_node n WHERE n.id = NEW.project_node_id AND n.project_id = d.project_id
    ) THEN
        RAISE EXCEPTION 'line.node_invalid';
    END IF;
    IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL
       AND EXISTS (SELECT 1 FROM public.document_line_price p
                   WHERE p.line_id = NEW.id AND p.deleted_at IS NULL)
       AND NOT app.document_money_right(NEW.document_id) THEN
        RAISE EXCEPTION 'line.priced_delete_forbidden';
    END IF;
    RETURN NEW;
END $$
"""

# Право на запись позиций до S7c (откат): текст совпадает с ревизией 20261021_1100 (S7a, LINE_WRITABLE).
LINE_WRITABLE_PREV = """
CREATE OR REPLACE FUNCTION app.line_writable(p_document uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE(app.document_writable(p_document) AND (
        app.has_permission('documents', 'write', 'lines')
        OR (app.has_permission('materials', 'write', 'stock') AND EXISTS (
            SELECT 1 FROM public.document d
            WHERE d.id = p_document AND d.kind IN ('m7_act', 'waybill_internal')))), false)
$$
"""

LINE_WRITABLE_NEW = """
CREATE OR REPLACE FUNCTION app.line_writable(p_document uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE(app.document_writable(p_document) AND (
        app.has_permission('documents', 'write', 'lines')
        OR (app.has_permission('materials', 'write', 'stock') AND EXISTS (
            SELECT 1 FROM public.document d
            WHERE d.id = p_document AND d.kind IN ('m7_act', 'waybill_internal', 'inventory_act')))), false)
$$
"""

# Права на документы склада до S7c (откат): текст совпадает с ревизией 20261022_1000 (e7a2c9d4b1f8).
KIND_WRITABLE_PREV = """
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

KIND_WRITABLE_NEW = """
CREATE OR REPLACE FUNCTION app.document_kind_writable(p_kind text, p_project uuid, p_legal_entity uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE(app.is_internal() AND CASE
        WHEN app.document_kind_equipment(p_kind) THEN
            p_project IS NULL AND p_legal_entity = ANY(app.legal_entity_ids())
            AND (app.has_permission('equipment', 'write', 'documents')
                 OR (p_kind = 'equipment_technical_inspection'
                     AND app.has_permission('equipment', 'write', 'maintenance')))
        ELSE (app.has_permission('documents', 'write') AND p_kind <> 'inventory_act' AND (
                p_project = ANY(app.project_ids())
                OR (p_project IS NULL AND p_legal_entity = ANY(app.legal_entity_ids()))))
            OR (p_kind IN ('m7_act', 'waybill_internal', 'inventory_act')
                AND app.has_permission('materials', 'write', 'stock')
                AND (p_project = ANY(app.project_ids())
                     OR (p_project IS NULL AND p_legal_entity = ANY(app.legal_entity_ids()))))
            OR (p_kind = 'material_certificate' AND app.has_permission('materials', 'write', 'batch')
                AND p_project IS NULL AND p_legal_entity = ANY(app.legal_entity_ids()))
    END, false)
$$
"""


# Виды с позициями до S7c (откат): текст совпадает с ревизией 20261018_1200 (document_kind_lines).
LINE_KINDS_PREV = """
CREATE OR REPLACE FUNCTION app.document_kind_lines(p_kind text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$ SELECT p_kind IN ('ks2', 'ks3', 'ks6', 'estimate_local', 'estimate_summary', 'waybill_torg12', 'waybill_m15', 'm7_act', 'waybill_internal', 'material_request') $$
"""

LINE_KINDS_NEW = """
CREATE OR REPLACE FUNCTION app.document_kind_lines(p_kind text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$ SELECT p_kind IN ('ks2', 'ks3', 'ks6', 'estimate_local', 'estimate_summary', 'waybill_torg12', 'waybill_m15', 'm7_act', 'waybill_internal', 'material_request', 'inventory_act') $$
"""


def upgrade() -> None:
    op.add_column("document_line", sa.Column("snapshot_qty", sa.Numeric(18, 4), nullable=True))
    op.alter_column("document_line", "quantity", existing_type=sa.Numeric(18, 4), nullable=True)
    # Откат может оставить nonnegative (есть строки с нулём): снимаем оба имени, подъём не зависит от данных.
    for name in ("ck_document_line_quantity_positive", "ck_document_line_quantity_nonnegative"):
        op.execute(f"ALTER TABLE document_line DROP CONSTRAINT IF EXISTS {name}")
    op.create_check_constraint(
        op.f("ck_document_line_quantity_nonnegative"), "document_line", "quantity >= 0"
    )
    op.create_index(
        op.f("uq_stock_movement_adjustment_source"),
        "stock_movement",
        ["source_line_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'adjustment' AND source_line_id IS NOT NULL"),
    )
    op.execute("GRANT UPDATE (snapshot_qty) ON document_line TO kut_auth")
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(GUARD_NEW)
    op.execute(SNAPSHOT)
    op.execute(ADJUST)
    op.execute("REVOKE ALL ON FUNCTION app.inventory_snapshot(uuid) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION app.inventory_adjust(uuid) FROM PUBLIC")
    op.execute(VERSION_STOCK_AFTER_NEW)
    op.execute(KIND_WRITABLE_NEW)
    op.execute(LINE_WRITABLE_NEW)
    op.execute(LINE_KINDS_NEW)
    op.execute("RESET ROLE")


def downgrade() -> None:
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(VERSION_STOCK_AFTER_PREV)
    op.execute(KIND_WRITABLE_PREV)
    op.execute(LINE_WRITABLE_PREV)
    op.execute(LINE_KINDS_PREV)
    op.execute("DROP FUNCTION IF EXISTS app.inventory_adjust(uuid)")
    op.execute("DROP FUNCTION IF EXISTS app.inventory_snapshot(uuid)")
    op.execute(GUARD_PREV)
    op.execute("RESET ROLE")
    op.execute("REVOKE UPDATE (snapshot_qty) ON document_line FROM kut_auth")
    op.drop_index(op.f("uq_stock_movement_adjustment_source"), table_name="stock_movement")
    # Строки с нулём и пустым фактом инвентаризации откат не удаляет: CHECK и NOT NULL остаются шире (NOTICE).
    op.execute(
        "DO $$ BEGIN ALTER TABLE document_line DROP CONSTRAINT ck_document_line_quantity_nonnegative;"
        " ALTER TABLE document_line ADD CONSTRAINT ck_document_line_quantity_positive CHECK (quantity > 0);"
        " EXCEPTION WHEN check_violation THEN RAISE NOTICE"
        " 'откат: CHECK количества оставлен неотрицательным (есть строки с нулём инвентаризации)'; END $$"
    )
    op.execute(
        "DO $$ BEGIN ALTER TABLE document_line ALTER COLUMN quantity SET NOT NULL;"
        " EXCEPTION WHEN not_null_violation THEN RAISE NOTICE"
        " 'откат: количество оставлено необязательным (есть строки инвентаризации без факта)'; END $$"
    )
    op.drop_column("document_line", "snapshot_qty")
