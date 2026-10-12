"""equipment: правила SE-2b - тариф по уровням, ГСМ в путевом листе, защита без GUC, объект в определителях

Revision ID: a3c6e8f1d2b9
Revises: 9d4f1b7a2c6e
Create Date: 2026-10-13 13:00:00.000000

Что меняет ревизия (решения PO и находки ревью по SE-2a, wiki/arch/se2a-shifts-costs.md):
- тариф: выбор по уровню единица -> тип -> договор; внутри уровня одна единица тарификации среди
  подходящих на дату листа (иначе shift.rate_ambiguous при отправке); два тарифа с одной датой начала
  и разными единицами одного уровня и флага - отказ при вводе (rate.level_conflict);
- ГСМ: заправка может стоять в путевом листе (shift_id); машинист вносит её в своём черновике;
  в журнал строка попадает при проводке листа; сторно - переподтверждением листа;
- защита posting_version и lines_revision, и перехода ремонта в done: меняют только определители
  (current_user = kut_auth); старый GUC ничего не открывает;
- определители с объектом проверяют project_ids (сторно ГСМ, проводка, закрытие ремонта, делегат);
- делегирование не раньше сегодня (app.local_today()); лист с объектом удаляет проект с кодом project_has_shifts.

Тела, которые переписаны, в откате возвращаются из ревизии b6d2f8a4c1e9 (файл читается по пути).
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "a3c6e8f1d2b9"
down_revision: str | Sequence[str] | None = "9d4f1b7a2c6e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Содержание листа: изменение требует новой проводки. Счётчик строк сюда не входит - он меняется
# только определителями и не даёт автору право на правку подтверждённого листа.
CONTENT = (
    "NEW.equipment_unit_id, NEW.project_id, NEW.work_date, NEW.operator_id, NEW.hours,"
    " NEW.odometer_from, NEW.odometer_to, NEW.trips, NEW.tonnes, NEW.downtime_hours,"
    " NEW.downtime_reason, NEW.assignment_id, NEW.source"
)
CONTENT_OLD = CONTENT.replace("NEW.", "OLD.")
POSTING_NEW = f"{CONTENT}, NEW.lines_revision"
POSTING_OLD = f"{CONTENT_OLD}, OLD.lines_revision"

FUNCTIONS = (
    # Тариф листа: норматив своей техники на дату или тариф договора аренды. Аренда выбирает уровень по
    # порядку единица -> тип -> договор; первый уровень, где есть подходящие строки, решает.
    """
CREATE OR REPLACE FUNCTION app.equipment_shift_tariff(p_shift public.equipment_shift)
RETURNS TABLE(o_unit text, o_rate numeric, o_category text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_unit public.equipment_unit;
    v_operator public.equipment_operator;
    v_from date;
    v_count integer;
    v_level text;
BEGIN
    SELECT * INTO v_unit FROM public.equipment_unit WHERE id = p_shift.equipment_unit_id;
    SELECT * INTO v_operator FROM public.equipment_operator WHERE id = p_shift.operator_id;
    IF v_unit.ownership = 'own' THEN
        SELECT MAX(r.valid_from) INTO v_from FROM public.equipment_own_rate r
        WHERE r.legal_entity_id = v_unit.legal_entity_id
          AND r.equipment_type_id = v_unit.equipment_type_id
          AND r.approved_by IS NOT NULL AND r.deleted_at IS NULL
          AND r.valid_from <= p_shift.work_date;
        IF v_from IS NULL THEN
            RAISE EXCEPTION 'shift.rate_missing' USING ERRCODE = 'check_violation';
        END IF;
        SELECT COUNT(*) INTO v_count FROM public.equipment_own_rate r
        WHERE r.legal_entity_id = v_unit.legal_entity_id
          AND r.equipment_type_id = v_unit.equipment_type_id
          AND r.approved_by IS NOT NULL AND r.deleted_at IS NULL AND r.valid_from = v_from;
        IF v_count > 1 THEN
            RAISE EXCEPTION 'shift.rate_ambiguous' USING ERRCODE = 'check_violation';
        END IF;
        SELECT r.unit, r.amount INTO o_unit, o_rate FROM public.equipment_own_rate r
        WHERE r.legal_entity_id = v_unit.legal_entity_id
          AND r.equipment_type_id = v_unit.equipment_type_id
          AND r.approved_by IS NOT NULL AND r.deleted_at IS NULL AND r.valid_from = v_from;
        o_category := 'equipment_own';
        RETURN NEXT;
        RETURN;
    END IF;
    IF v_unit.contract_id IS NULL THEN
        RAISE EXCEPTION 'shift.rate_missing' USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.contract c
        WHERE c.id = v_unit.contract_id AND c.status = 'active' AND c.deleted_at IS NULL
          AND c.date_from <= p_shift.work_date
          AND (c.date_to IS NULL OR c.date_to >= p_shift.work_date)) THEN
        RAISE EXCEPTION 'equipment.contract_inactive' USING ERRCODE = 'check_violation';
    END IF;
    FOREACH v_level IN ARRAY ARRAY['unit', 'type', 'contract'] LOOP
        SELECT COUNT(DISTINCT r.unit) INTO v_count FROM public.equipment_rate r
        WHERE r.contract_id = v_unit.contract_id
          AND r.with_operator = (v_operator.employment = 'lessor')
          AND r.deleted_at IS NULL AND r.valid_from <= p_shift.work_date
          AND CASE v_level
                WHEN 'unit' THEN r.equipment_unit_id = v_unit.id
                WHEN 'type' THEN r.equipment_unit_id IS NULL
                                 AND r.equipment_type_id = v_unit.equipment_type_id
                ELSE r.equipment_unit_id IS NULL AND r.equipment_type_id IS NULL
              END;
        IF v_count = 0 THEN
            CONTINUE;
        END IF;
        IF v_count > 1 THEN
            RAISE EXCEPTION 'shift.rate_ambiguous' USING ERRCODE = 'check_violation';
        END IF;
        SELECT r.unit, r.amount INTO o_unit, o_rate FROM public.equipment_rate r
        WHERE r.contract_id = v_unit.contract_id
          AND r.with_operator = (v_operator.employment = 'lessor')
          AND r.deleted_at IS NULL AND r.valid_from <= p_shift.work_date
          AND CASE v_level
                WHEN 'unit' THEN r.equipment_unit_id = v_unit.id
                WHEN 'type' THEN r.equipment_unit_id IS NULL
                                 AND r.equipment_type_id = v_unit.equipment_type_id
                ELSE r.equipment_unit_id IS NULL AND r.equipment_type_id IS NULL
              END
        ORDER BY r.valid_from DESC LIMIT 1;
        o_category := 'equipment_rental';
        RETURN NEXT;
        RETURN;
    END LOOP;
    RAISE EXCEPTION 'shift.rate_missing' USING ERRCODE = 'check_violation';
END $$
""",
    # Проводка листа: сторно текущей версии (строки листа и ГСМ), новая версия строк и ГСМ. Объект - по
    # правам; без GUC: posting_version меняет только этот определитель (current_user = kut_auth).
    """
CREATE OR REPLACE FUNCTION app.equipment_shift_post(p_shift uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_user uuid := app.user_id();
    v_shift public.equipment_shift;
    v_unit public.equipment_unit;
    v_tariff record;
    v_version integer;
    v_total numeric;
    v_qty numeric;
    v_amount numeric;
    v_lines integer;
    v_index integer := 0;
    v_alloc numeric := 0;
    v_line public.equipment_shift_line;
BEGIN
    SELECT * INTO v_shift FROM public.equipment_shift WHERE id = p_shift;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'shift.not_found' USING ERRCODE = 'check_violation';
    END IF;
    IF v_user IS NOT NULL AND NOT (
        app.has_permission('equipment', 'approve', 'shift')
        OR app.has_permission('equipment', 'write', 'after_approval')
        OR app.equipment_delegated(v_shift.project_id, v_shift.work_date)
    ) THEN
        RAISE EXCEPTION 'shift.forbidden' USING ERRCODE = 'check_violation';
    END IF;
    IF v_user IS NOT NULL AND NOT (v_shift.project_id = ANY ((SELECT app.project_ids())::uuid[])) THEN
        RAISE EXCEPTION 'shift.forbidden' USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO v_unit FROM public.equipment_unit WHERE id = v_shift.equipment_unit_id;

    IF v_shift.posting_version > 0 THEN
        INSERT INTO public.cost_entry (created_by, legal_entity_id, project_id, node_id,
            schedule_item_id, equipment_unit_id, period, category, source_type, source_id,
            posting_version, line_no, quantity, tariff_unit, rate, amount, reversal_of_id)
        SELECT v_user, c.legal_entity_id, c.project_id, c.node_id, c.schedule_item_id,
            c.equipment_unit_id, c.period, c.category, c.source_type, c.source_id,
            c.posting_version, c.line_no, -c.quantity, c.tariff_unit, c.rate, -c.amount, c.id
        FROM public.cost_entry c
        WHERE c.posting_version = v_shift.posting_version AND c.reversal_of_id IS NULL
          AND ((c.source_type = 'equipment_shift' AND c.source_id = p_shift)
               OR (c.source_type = 'equipment_fuel' AND c.source_id IN (
                   SELECT f.id FROM public.equipment_fuel f WHERE f.shift_id = p_shift)))
          AND NOT EXISTS (SELECT 1 FROM public.cost_entry r WHERE r.reversal_of_id = c.id);
    END IF;

    SELECT * INTO v_tariff FROM app.equipment_shift_tariff(v_shift);
    v_version := v_shift.posting_version + 1;
    UPDATE public.equipment_shift SET posting_version = v_version WHERE id = p_shift;

    SELECT COUNT(*) INTO v_lines FROM public.equipment_shift_line
    WHERE shift_id = p_shift AND deleted_at IS NULL;
    IF v_lines = 0 THEN
        v_qty := app.equipment_shift_total_quantity(v_shift, v_tariff.o_unit);
        v_amount := ROUND(v_qty * v_tariff.o_rate, 2);
        INSERT INTO public.cost_entry (created_by, legal_entity_id, project_id, equipment_unit_id,
            period, category, source_type, source_id, posting_version, line_no, quantity,
            tariff_unit, rate, amount)
        VALUES (v_user, v_unit.legal_entity_id, v_shift.project_id, v_unit.id, v_shift.work_date,
            v_tariff.o_category, 'equipment_shift', p_shift, v_version, 1, v_qty,
            v_tariff.o_unit, v_tariff.o_rate, v_amount);
    ELSE
        v_total := app.equipment_shift_total_quantity(v_shift, v_tariff.o_unit);
        FOR v_line IN
            SELECT * FROM public.equipment_shift_line
            WHERE shift_id = p_shift AND deleted_at IS NULL ORDER BY created_at, id
        LOOP
            v_index := v_index + 1;
            IF v_tariff.o_unit = 'hour' THEN
                v_qty := v_line.hours;
            ELSIF v_tariff.o_unit = 'trip' THEN
                IF v_line.trips IS NULL THEN
                    RAISE EXCEPTION 'shift.quantity_missing' USING ERRCODE = 'check_violation';
                END IF;
                v_qty := v_line.trips;
            ELSIF v_tariff.o_unit = 'tonne' THEN
                IF v_line.tonnes IS NULL THEN
                    RAISE EXCEPTION 'shift.quantity_missing' USING ERRCODE = 'check_violation';
                END IF;
                v_qty := v_line.tonnes;
            ELSIF v_index < v_lines THEN
                v_qty := ROUND(v_total * v_line.hours / v_shift.hours, 3);
                v_alloc := v_alloc + v_qty;
            ELSE
                v_qty := v_total - v_alloc;
            END IF;
            v_amount := ROUND(v_qty * v_tariff.o_rate, 2);
            INSERT INTO public.cost_entry (created_by, legal_entity_id, project_id, node_id,
                schedule_item_id, equipment_unit_id, period, category, source_type, source_id,
                posting_version, line_no, quantity, tariff_unit, rate, amount)
            VALUES (v_user, v_unit.legal_entity_id, v_shift.project_id, v_line.node_id,
                v_line.schedule_item_id, v_unit.id, v_shift.work_date, v_tariff.o_category,
                'equipment_shift', p_shift, v_version, v_index, v_qty, v_tariff.o_unit,
                v_tariff.o_rate, v_amount);
        END LOOP;
    END IF;

    INSERT INTO public.cost_entry (created_by, legal_entity_id, project_id, equipment_unit_id,
        period, category, source_type, source_id, posting_version, line_no, quantity, amount)
    SELECT v_user, f.legal_entity_id, f.project_id, f.equipment_unit_id, v_shift.work_date,
        'equipment_fuel', 'equipment_fuel', f.id, v_version, 1, f.liters, f.amount
    FROM public.equipment_fuel f
    WHERE f.shift_id = p_shift AND f.deleted_at IS NULL;
END $$
""",
    # Строка журнала после подтверждения: проводка при переходе в confirmed и при правке содержимого
    # подтверждённого листа, включая строки и ГСМ (счётчик строк входит в содержание проводки).
    f"""
CREATE OR REPLACE FUNCTION app.equipment_shift_after_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.status = 'confirmed' AND (
        OLD.status IS DISTINCT FROM 'confirmed'
        OR ({POSTING_NEW}) IS DISTINCT FROM ({POSTING_OLD})
    ) THEN
        PERFORM app.equipment_shift_post(NEW.id);
    END IF;
    RETURN NULL;
END $$
""",
    # Защитный триггер листа: права по роли, объект, автор, переходы и суточный лимит; без GUC.
    f"""
CREATE OR REPLACE FUNCTION app.equipment_shift_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_user uuid := app.user_id();
    v_unit public.equipment_unit;
    v_operator public.equipment_operator;
    v_assignment public.equipment_assignment;
    v_tariff record;
    v_can_write boolean := false;
    v_can_approve boolean := false;
    v_can_after boolean := false;
    v_status_changed boolean;
    v_content_changed boolean;
    v_other numeric;
BEGIN
    IF v_user IS NOT NULL THEN
        v_can_write := app.has_permission('equipment', 'write', 'shift');
        v_can_approve := app.has_permission('equipment', 'approve', 'shift');
        v_can_after := app.has_permission('equipment', 'write', 'after_approval');
        IF NOT (NEW.project_id = ANY ((SELECT app.project_ids())::uuid[])) THEN
            RAISE EXCEPTION 'shift.forbidden' USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    SELECT * INTO v_unit FROM public.equipment_unit
    WHERE id = NEW.equipment_unit_id AND deleted_at IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'shift.unit_invalid' USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO v_operator FROM public.equipment_operator
    WHERE id = NEW.operator_id AND deleted_at IS NULL;
    IF NOT FOUND OR v_operator.legal_entity_id <> v_unit.legal_entity_id THEN
        RAISE EXCEPTION 'shift.operator_invalid' USING ERRCODE = 'check_violation';
    END IF;
    NEW.legal_entity_id := v_unit.legal_entity_id;
    IF v_user IS NOT NULL AND app.is_machinist()
       AND v_operator.user_id IS DISTINCT FROM v_user THEN
        RAISE EXCEPTION 'shift.forbidden' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.assignment_id IS NOT NULL THEN
        SELECT * INTO v_assignment FROM public.equipment_assignment
        WHERE id = NEW.assignment_id AND deleted_at IS NULL;
        IF NOT FOUND OR v_assignment.equipment_unit_id <> NEW.equipment_unit_id
           OR v_assignment.project_id <> NEW.project_id THEN
            RAISE EXCEPTION 'shift.assignment_invalid' USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF v_user IS NOT NULL THEN
            IF NOT v_can_write THEN
                RAISE EXCEPTION 'shift.forbidden' USING ERRCODE = 'check_violation';
            END IF;
            NEW.created_by := v_user;
        END IF;
        IF NEW.status <> 'draft' THEN
            RAISE EXCEPTION 'shift.transition_forbidden' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.work_date > app.local_today() THEN
            RAISE EXCEPTION 'shift.date_in_future' USING ERRCODE = 'check_violation';
        END IF;
        NEW.confirmed_by := NULL;
        NEW.confirmed_at := NULL;
        NEW.reject_reason := NULL;
        NEW.posting_version := 0;
        NEW.lines_revision := 0;
        PERFORM 1 FROM public.equipment_unit WHERE id = NEW.equipment_unit_id FOR UPDATE;
        v_other := app.equipment_day_hours_other(NEW.equipment_unit_id, NEW.work_date, NULL);
        IF v_other + NEW.hours > 24 THEN
            RAISE EXCEPTION 'shift.day_hours_exceeded' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.deleted_at IS NOT NULL OR NEW.deleted_at IS NOT NULL THEN
        RAISE EXCEPTION 'shift.locked' USING ERRCODE = 'check_violation';
    END IF;
    v_status_changed := NEW.status IS DISTINCT FROM OLD.status;
    v_content_changed := ({CONTENT}) IS DISTINCT FROM ({CONTENT_OLD});
    IF NOT v_status_changed THEN
        NEW.confirmed_by := OLD.confirmed_by;
        NEW.confirmed_at := OLD.confirmed_at;
        NEW.reject_reason := OLD.reject_reason;
    END IF;
    IF NOT v_status_changed AND NOT v_content_changed THEN
        RETURN NEW;
    END IF;

    IF NOT v_status_changed THEN
        IF OLD.status = 'draft' THEN
            IF v_user IS NOT NULL AND (NOT v_can_write OR v_user IS DISTINCT FROM OLD.created_by) THEN
                RAISE EXCEPTION 'shift.forbidden' USING ERRCODE = 'check_violation';
            END IF;
        ELSIF OLD.status = 'confirmed' THEN
            IF v_user IS NOT NULL AND NOT v_can_after THEN
                RAISE EXCEPTION 'shift.forbidden' USING ERRCODE = 'check_violation';
            END IF;
        ELSE
            RAISE EXCEPTION 'shift.locked' USING ERRCODE = 'check_violation';
        END IF;
        PERFORM 1 FROM public.equipment_unit WHERE id = NEW.equipment_unit_id FOR UPDATE;
        v_other := app.equipment_day_hours_other(NEW.equipment_unit_id, NEW.work_date, NEW.id);
        IF v_other + NEW.hours > 24 THEN
            RAISE EXCEPTION 'shift.day_hours_exceeded' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status = 'draft' AND NEW.status = 'submitted' THEN
        IF v_user IS NOT NULL AND (NOT v_can_write OR v_user IS DISTINCT FROM OLD.created_by) THEN
            RAISE EXCEPTION 'shift.forbidden' USING ERRCODE = 'check_violation';
        END IF;
        IF v_content_changed THEN
            RAISE EXCEPTION 'shift.transition_forbidden' USING ERRCODE = 'check_violation';
        END IF;
        SELECT * INTO v_tariff FROM app.equipment_shift_tariff(NEW);
        PERFORM app.equipment_shift_total_quantity(NEW, v_tariff.o_unit);
        IF EXISTS (
            SELECT 1 FROM public.equipment_shift_line l
            WHERE l.shift_id = NEW.id AND l.deleted_at IS NULL
              AND ((v_tariff.o_unit = 'trip' AND l.trips IS NULL)
                   OR (v_tariff.o_unit = 'tonne' AND l.tonnes IS NULL))
        ) THEN
            RAISE EXCEPTION 'shift.quantity_missing' USING ERRCODE = 'check_violation';
        END IF;
        PERFORM 1 FROM public.equipment_unit WHERE id = NEW.equipment_unit_id FOR UPDATE;
        v_other := app.equipment_day_hours_other(NEW.equipment_unit_id, NEW.work_date, NEW.id);
        IF v_other + NEW.hours > 24 THEN
            RAISE EXCEPTION 'shift.day_hours_exceeded' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    ELSIF OLD.status = 'submitted' AND NEW.status IN ('confirmed', 'rejected') THEN
        IF v_user IS NOT NULL THEN
            IF v_user = OLD.created_by THEN
                RAISE EXCEPTION 'shift.transition_forbidden' USING ERRCODE = 'check_violation';
            END IF;
            IF NOT (v_can_approve OR app.equipment_delegated(NEW.project_id, NEW.work_date)) THEN
                RAISE EXCEPTION 'shift.forbidden' USING ERRCODE = 'check_violation';
            END IF;
        END IF;
        IF v_content_changed THEN
            RAISE EXCEPTION 'shift.transition_forbidden' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.status = 'confirmed' THEN
            NEW.confirmed_by := coalesce(v_user, NEW.confirmed_by);
            NEW.confirmed_at := now();
            NEW.reject_reason := NULL;
        ELSE
            NEW.confirmed_by := NULL;
            NEW.confirmed_at := NULL;
        END IF;
        RETURN NEW;
    ELSIF OLD.status = 'rejected' AND NEW.status = 'draft' THEN
        IF v_user IS NOT NULL AND (NOT v_can_write OR v_user IS DISTINCT FROM OLD.created_by) THEN
            RAISE EXCEPTION 'shift.forbidden' USING ERRCODE = 'check_violation';
        END IF;
        IF v_content_changed THEN
            RAISE EXCEPTION 'shift.transition_forbidden' USING ERRCODE = 'check_violation';
        END IF;
        PERFORM 1 FROM public.equipment_unit WHERE id = NEW.equipment_unit_id FOR UPDATE;
        v_other := app.equipment_day_hours_other(NEW.equipment_unit_id, NEW.work_date, NEW.id);
        IF v_other + NEW.hours > 24 THEN
            RAISE EXCEPTION 'shift.day_hours_exceeded' USING ERRCODE = 'check_violation';
        END IF;
        NEW.reject_reason := NULL;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'shift.transition_forbidden' USING ERRCODE = 'check_violation';
END $$
""",
    # Право менять ГСМ в листе: черновик и отклонённый - машинист этого листа или автор с write:shift;
    # подтверждённый - техдиректор (after_approval); отправленный - нет. Вызывают триггеры ГСМ.
    """
CREATE FUNCTION app.equipment_fuel_sheet_check(p_shift public.equipment_shift) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_user uuid := app.user_id();
    v_own_sheet boolean;
BEGIN
    IF v_user IS NULL THEN
        RETURN;
    END IF;
    IF NOT (p_shift.project_id = ANY ((SELECT app.project_ids())::uuid[])) THEN
        RAISE EXCEPTION 'fuel.forbidden' USING ERRCODE = 'check_violation';
    END IF;
    v_own_sheet := EXISTS (
        SELECT 1 FROM public.equipment_operator o
        WHERE o.id = p_shift.operator_id AND o.user_id = v_user);
    IF p_shift.status IN ('draft', 'rejected') THEN
        IF NOT app.has_permission('equipment', 'write', 'shift')
           OR NOT (v_own_sheet OR v_user IS NOT DISTINCT FROM p_shift.created_by) THEN
            RAISE EXCEPTION 'fuel.forbidden' USING ERRCODE = 'check_violation';
        END IF;
    ELSIF p_shift.status = 'confirmed' THEN
        IF NOT app.has_permission('equipment', 'write', 'after_approval') THEN
            RAISE EXCEPTION 'fuel.forbidden' USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        RAISE EXCEPTION 'shift.locked' USING ERRCODE = 'check_violation';
    END IF;
END $$
""",
    # Заправка: без листа - прораб или мастер (не машинист), объект по правам; в листе - по листу.
    """
CREATE OR REPLACE FUNCTION app.equipment_fuel_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_user uuid := app.user_id();
    v_unit public.equipment_unit;
    v_shift public.equipment_shift;
BEGIN
    IF NEW.shift_id IS NULL THEN
        IF v_user IS NOT NULL AND (
            NOT app.has_permission('equipment', 'write', 'shift') OR app.is_machinist()
        ) THEN
            RAISE EXCEPTION 'fuel.forbidden' USING ERRCODE = 'check_violation';
        END IF;
        SELECT * INTO v_unit FROM public.equipment_unit
        WHERE id = NEW.equipment_unit_id AND deleted_at IS NULL;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'fuel.unit_invalid' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.fuel_date > app.local_today() THEN
            RAISE EXCEPTION 'fuel.date_in_future' USING ERRCODE = 'check_violation';
        END IF;
        IF v_user IS NOT NULL AND NEW.project_id IS NOT NULL
           AND NOT (NEW.project_id = ANY ((SELECT app.project_ids())::uuid[])) THEN
            RAISE EXCEPTION 'fuel.forbidden' USING ERRCODE = 'check_violation';
        END IF;
        NEW.legal_entity_id := v_unit.legal_entity_id;
    ELSE
        SELECT * INTO v_shift FROM public.equipment_shift
        WHERE id = NEW.shift_id AND deleted_at IS NULL;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'fuel.shift_invalid' USING ERRCODE = 'check_violation';
        END IF;
        PERFORM app.equipment_fuel_sheet_check(v_shift);
        NEW.project_id := v_shift.project_id;
        NEW.legal_entity_id := v_shift.legal_entity_id;
        NEW.equipment_unit_id := v_shift.equipment_unit_id;
        NEW.fuel_date := v_shift.work_date;
    END IF;
    IF v_user IS NOT NULL THEN
        NEW.created_by := v_user;
    END IF;
    NEW.reversed_at := NULL;
    NEW.reversed_by := NULL;
    NEW.reverse_reason := NULL;
    RETURN NEW;
END $$
""",
    # Проводка заправки без листа - строка журнала; заправка в листе попадает в журнал при проводке листа.
    """
CREATE OR REPLACE FUNCTION app.equipment_fuel_post() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.shift_id IS NULL THEN
        INSERT INTO public.cost_entry (created_by, legal_entity_id, project_id, equipment_unit_id,
            period, category, source_type, source_id, posting_version, line_no, quantity, amount)
        VALUES (NEW.created_by, NEW.legal_entity_id, NEW.project_id, NEW.equipment_unit_id,
            NEW.fuel_date, 'equipment_fuel', 'equipment_fuel', NEW.id, 1, 1, NEW.liters, NEW.amount);
    END IF;
    RETURN NULL;
END $$
""",
    # Изменение заправки в листе: мягкое удаление и чек - по праву на лист; у заправки без листа
    # меняется только сторно (его пишет определитель), удаления нет.
    """
CREATE FUNCTION app.equipment_fuel_update_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_shift public.equipment_shift;
BEGIN
    IF OLD.shift_id IS NULL THEN
        IF NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN
            RAISE EXCEPTION 'fuel.forbidden' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.deleted_at IS NOT NULL THEN
        RAISE EXCEPTION 'fuel.forbidden' USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO v_shift FROM public.equipment_shift WHERE id = OLD.shift_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'fuel.shift_invalid' USING ERRCODE = 'check_violation';
    END IF;
    PERFORM app.equipment_fuel_sheet_check(v_shift);
    RETURN NEW;
END $$
""",
    # Добавление и удаление заправки в листе меняют содержание листа: счётчик строк, и подтверждённый
    # лист проводится заново (правка техдиректора). Чек и прочие поля счётчик не трогают.
    """
CREATE FUNCTION app.equipment_fuel_shift_bump() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.shift_id IS NULL THEN
        RETURN NULL;
    END IF;
    IF TG_OP = 'INSERT' THEN
        UPDATE public.equipment_shift SET lines_revision = lines_revision + 1 WHERE id = NEW.shift_id;
        RETURN NULL;
    END IF;
    IF NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN
        UPDATE public.equipment_shift SET lines_revision = lines_revision + 1 WHERE id = NEW.shift_id;
    END IF;
    RETURN NULL;
END $$
""",
    # Сторно заправки без листа: строка не меняется, пишется сторно. Заправка в листе сторнируется
    # переподтверждением листа, поэтому здесь отказ. Объект - по project_ids (прораб чужого объекта
    # не сторнирует даже при праве после согласования).
    """
CREATE OR REPLACE FUNCTION app.equipment_fuel_reverse(p_fuel uuid, p_reason text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_user uuid := app.user_id();
    v_fuel public.equipment_fuel;
    v_cost public.cost_entry;
BEGIN
    IF v_user IS NULL OR NOT app.has_permission('equipment', 'write', 'after_approval') THEN
        RAISE EXCEPTION 'fuel.forbidden' USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO v_fuel FROM public.equipment_fuel WHERE id = p_fuel AND deleted_at IS NULL
      AND legal_entity_id = ANY ((SELECT app.legal_entity_ids())::uuid[])
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'fuel.not_found' USING ERRCODE = 'check_violation';
    END IF;
    IF v_fuel.project_id IS NOT NULL
       AND NOT (v_fuel.project_id = ANY ((SELECT app.project_ids())::uuid[])) THEN
        RAISE EXCEPTION 'fuel.forbidden' USING ERRCODE = 'check_violation';
    END IF;
    IF v_fuel.shift_id IS NOT NULL THEN
        RAISE EXCEPTION 'fuel.shift_bound' USING ERRCODE = 'check_violation';
    END IF;
    IF v_fuel.reversed_at IS NOT NULL THEN
        RETURN;
    END IF;
    IF btrim(coalesce(p_reason, '')) = '' THEN
        RAISE EXCEPTION 'fuel.reason_required' USING ERRCODE = 'check_violation';
    END IF;
    UPDATE public.equipment_fuel
    SET reversed_at = now(), reversed_by = v_user, reverse_reason = p_reason
    WHERE id = p_fuel;
    SELECT * INTO v_cost FROM public.cost_entry
    WHERE source_type = 'equipment_fuel' AND source_id = p_fuel AND reversal_of_id IS NULL;
    INSERT INTO public.cost_entry (created_by, legal_entity_id, project_id, equipment_unit_id,
        period, category, source_type, source_id, posting_version, line_no, quantity, amount,
        reversal_of_id)
    VALUES (v_user, v_cost.legal_entity_id, v_cost.project_id, v_cost.equipment_unit_id,
        v_cost.period, v_cost.category, 'equipment_fuel', p_fuel, v_cost.posting_version,
        v_cost.line_no, -v_cost.quantity, -v_cost.amount, v_cost.id);
END $$
""",
    # Закрытие ТО и ремонта через функцию: объект (если указан) - по project_ids; без GUC.
    """
CREATE OR REPLACE FUNCTION app.equipment_maintenance_close(
    p_maintenance uuid, p_project uuid, p_amount numeric)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_user uuid := app.user_id();
    v_m public.equipment_maintenance;
BEGIN
    IF v_user IS NOT NULL AND NOT app.has_permission('equipment', 'write', 'maintenance') THEN
        RAISE EXCEPTION 'maintenance.forbidden' USING ERRCODE = 'check_violation';
    END IF;
    IF v_user IS NOT NULL AND p_project IS NOT NULL
       AND NOT (p_project = ANY ((SELECT app.project_ids())::uuid[])) THEN
        RAISE EXCEPTION 'maintenance.forbidden' USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO v_m FROM public.equipment_maintenance
    WHERE id = p_maintenance AND deleted_at IS NULL
      AND (v_user IS NULL OR legal_entity_id = ANY ((SELECT app.legal_entity_ids())::uuid[]))
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'maintenance.not_found' USING ERRCODE = 'check_violation';
    END IF;
    IF v_m.status = 'done' THEN
        RETURN;
    END IF;
    IF v_m.status NOT IN ('planned', 'in_progress')
       OR (v_m.kind = 'repair' AND v_m.status <> 'in_progress') THEN
        RAISE EXCEPTION 'maintenance.transition_forbidden' USING ERRCODE = 'check_violation';
    END IF;
    IF v_m.kind = 'repair' AND p_amount IS NULL THEN
        RAISE EXCEPTION 'maintenance.amount_required' USING ERRCODE = 'check_violation';
    END IF;
    IF p_amount IS NOT NULL AND p_amount < 0 THEN
        RAISE EXCEPTION 'maintenance.amount_invalid' USING ERRCODE = 'check_violation';
    END IF;
    IF p_project IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.project p
        WHERE p.id = p_project AND p.legal_entity_id = v_m.legal_entity_id
          AND p.deleted_at IS NULL) THEN
        RAISE EXCEPTION 'maintenance.project_invalid' USING ERRCODE = 'check_violation';
    END IF;
    UPDATE public.equipment_maintenance
    SET status = 'done', done_at = now(), project_id = p_project
    WHERE id = p_maintenance;
    IF p_amount IS NOT NULL AND p_amount > 0 THEN
        INSERT INTO public.cost_entry (created_by, legal_entity_id, project_id, equipment_unit_id,
            period, category, source_type, source_id, posting_version, line_no, amount)
        VALUES (v_user, v_m.legal_entity_id, p_project, v_m.equipment_unit_id,
            app.local_today(), 'equipment_repair', 'equipment_maintenance', p_maintenance, 1, 1,
            p_amount);
    END IF;
END $$
""",
    # Переход ТО и ремонта в done - только через определитель выше (current_user = kut_auth); без GUC.
    """
CREATE FUNCTION app.equipment_maintenance_status_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.status = 'done' AND OLD.status IS DISTINCT FROM 'done'
       AND current_user <> 'kut_auth' THEN
        RAISE EXCEPTION 'maintenance.close_forbidden' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$
""",
    # Делегат подтверждения: объект по project_ids, eligibility (не машинист, write:shift), дата - не раньше
    # сегодня (без задним числом; закрытие месяца - SE-3).
    """
CREATE OR REPLACE FUNCTION app.equipment_delegate_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF app.user_id() IS NOT NULL
       AND NOT (NEW.project_id = ANY ((SELECT app.project_ids())::uuid[])) THEN
        RAISE EXCEPTION 'delegate.forbidden' USING ERRCODE = 'check_violation';
    END IF;
    IF NOT app.equipment_is_delegate_eligible(NEW.delegate_user_id) THEN
        RAISE EXCEPTION 'delegate.not_eligible' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.date_from < app.local_today() THEN
        RAISE EXCEPTION 'delegate.in_past' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$
""",
    # Тарифы: строка относится к договору и своему уровню; внутри уровня, флага и даты начала - одна единица.
    """
CREATE FUNCTION app.equipment_rate_level_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.equipment_unit_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.equipment_unit u
        WHERE u.id = NEW.equipment_unit_id AND u.contract_id = NEW.contract_id
          AND u.deleted_at IS NULL) THEN
        RAISE EXCEPTION 'rate.target_invalid' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.equipment_type_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.equipment_type t
        WHERE t.id = NEW.equipment_type_id AND t.deleted_at IS NULL) THEN
        RAISE EXCEPTION 'rate.target_invalid' USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.equipment_rate r
        WHERE r.contract_id = NEW.contract_id AND r.with_operator = NEW.with_operator
          AND r.deleted_at IS NULL AND r.valid_from = NEW.valid_from AND r.unit <> NEW.unit
          AND r.equipment_unit_id IS NOT DISTINCT FROM NEW.equipment_unit_id
          AND r.equipment_type_id IS NOT DISTINCT FROM NEW.equipment_type_id) THEN
        RAISE EXCEPTION 'rate.level_conflict' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$
""",
    # Лист, заправка, делегат: удаление проекта с ними - отдельный код, не project_has_nodes.
    """
CREATE FUNCTION app.project_has_shifts(p_project uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.equipment_shift sh
        WHERE sh.project_id = p_project AND sh.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.equipment_fuel fuel
        WHERE fuel.project_id = p_project AND fuel.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.equipment_delegate dlg
        WHERE dlg.project_id = p_project AND dlg.deleted_at IS NULL AND dlg.revoked_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.cost_entry cost
        WHERE cost.project_id = p_project)
$$
""",
    """
CREATE FUNCTION app.project_shift_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL
       AND app.project_has_shifts(OLD.id) THEN
        RAISE EXCEPTION 'project.has_shifts' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$
""",
    # Зависимости проекта возвращаются к телу SE-1 (ревизия c5f1): листы проверяет project_has_shifts.
    """
CREATE OR REPLACE FUNCTION app.project_has_dependents(p_project uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.project_node node
        WHERE node.project_id = p_project AND node.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.equipment_request req
        WHERE req.project_id = p_project AND req.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.equipment_assignment asg
        WHERE asg.project_id = p_project AND asg.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.equipment_maintenance mnt
        WHERE mnt.project_id = p_project AND mnt.deleted_at IS NULL)
$$
""",
    # Версии листа меняет только определитель: UPDATE из других ролей отказывает (current_user <> kut_auth).
    """
CREATE FUNCTION app.equipment_shift_version_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
BEGIN
    IF current_user <> 'kut_auth' AND (
        NEW.posting_version IS DISTINCT FROM OLD.posting_version
        OR NEW.lines_revision IS DISTINCT FROM OLD.lines_revision) THEN
        RAISE EXCEPTION 'shift.posting_forbidden' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$
""",
)

# Триггеры новой ревизии: (имя, таблица, событие, функция). Остальные триггеры - из b6d2 без изменений.
TRIGGERS = (
    (
        "equipment_shift_version_guard",
        "equipment_shift",
        "BEFORE UPDATE OF posting_version, lines_revision",
        "equipment_shift_version_guard",
    ),
    (
        "equipment_fuel_update_guard",
        "equipment_fuel",
        "BEFORE UPDATE",
        "equipment_fuel_update_guard",
    ),
    (
        "equipment_fuel_shift_bump",
        "equipment_fuel",
        "AFTER INSERT OR UPDATE",
        "equipment_fuel_shift_bump",
    ),
    (
        "equipment_maintenance_status_guard",
        "equipment_maintenance",
        "BEFORE UPDATE OF status",
        "equipment_maintenance_status_guard",
    ),
    ("equipment_rate_level_guard", "equipment_rate", "BEFORE INSERT", "equipment_rate_level_guard"),
    ("project_shift_guard", "project", "BEFORE UPDATE OF deleted_at", "project_shift_guard"),
)

# Функции новой ревизии, которые вызывает триггер: только владелец и триггер (PUBLIC - нет).
TRIGGER_FUNCTIONS = (
    "equipment_shift_version_guard()",
    "equipment_fuel_update_guard()",
    "equipment_fuel_shift_bump()",
    "equipment_maintenance_status_guard()",
    "equipment_rate_level_guard()",
    "project_shift_guard()",
)
HELPERS = (
    "equipment_fuel_sheet_check(public.equipment_shift)",
    "project_has_shifts(uuid)",
)

# Функции, которые переписаны этой ревизией: в откате возвращаются тела из b6d2.
REPLACED = (
    "equipment_shift_tariff",
    "equipment_shift_post",
    "equipment_shift_after_change",
    "equipment_shift_guard",
    "equipment_fuel_guard",
    "equipment_fuel_post",
    "equipment_fuel_reverse",
    "equipment_maintenance_close",
    "equipment_delegate_guard",
    "project_has_dependents",
)


def _b6d2():  # type: ignore[no-untyped-def]
    path = Path(__file__).with_name("20261013_1100_b6d2f8a4c1e9_equipment_shifts_rules.py")
    spec = importlib.util.spec_from_file_location("b6d2_rules_for_downgrade", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _previous(name: str) -> str:
    marker = f"FUNCTION app.{name}("
    module = _b6d2()
    for sql in (*module.FUNCTIONS, *module.PREVIOUS_DEPENDENTS):
        if marker in sql:
            return sql.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    raise LookupError(name)


def upgrade() -> None:
    # Триггер ремонта из b6d2 и его функция уходят: вместо них - определитель переходов ниже.
    op.execute("DROP TRIGGER IF EXISTS equipment_maintenance_guard ON equipment_maintenance")
    # Функции создаёт и их права меняет владелец kut_auth (REVOKE от kut_owner отказывает).
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute("DROP FUNCTION IF EXISTS app.equipment_maintenance_guard()")
    for sql in FUNCTIONS:
        op.execute(sql)
    for signature in (*TRIGGER_FUNCTIONS, *HELPERS):
        op.execute(f"REVOKE ALL ON FUNCTION app.{signature} FROM PUBLIC")
    for signature in TRIGGER_FUNCTIONS:
        op.execute(f"GRANT EXECUTE ON FUNCTION app.{signature} TO kut_owner")
    op.execute("RESET ROLE")

    for name, table, event, function in TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {name} {event} ON {table} FOR EACH ROW"
            f" EXECUTE FUNCTION app.{function}()"
        )


def downgrade() -> None:
    for name, table, _event, _function in reversed(TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    op.execute("SET LOCAL ROLE kut_auth")
    for name in REPLACED:
        op.execute(_previous(name))
    op.execute(_previous("equipment_maintenance_guard"))
    for signature in (*TRIGGER_FUNCTIONS, *HELPERS):
        op.execute(f"DROP FUNCTION IF EXISTS app.{signature}")
    op.execute("REVOKE ALL ON FUNCTION app.equipment_maintenance_guard() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION app.equipment_maintenance_guard() TO kut_owner")
    op.execute("RESET ROLE")
    op.execute(
        "CREATE TRIGGER equipment_maintenance_guard BEFORE UPDATE ON equipment_maintenance"
        " FOR EACH ROW EXECUTE FUNCTION app.equipment_maintenance_guard()"
    )
