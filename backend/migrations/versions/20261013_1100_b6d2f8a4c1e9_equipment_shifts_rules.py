"""equipment: правила путевых листов, проводки затрат и журнал - SE-2a (ADR-014 часть 1 §1 и §3, часть 2 §3)

Revision ID: b6d2f8a4c1e9
Revises: e4f1a9c3b7d2
Create Date: 2026-10-13 11:00:00.000000

Что держит БД (ошибка приходит кодом в тексте RAISE; app/core/rules.py переводит её в ответ API):
- переходы листа: draft -> submitted -> confirmed | rejected -> draft; правка подтверждённого - только
  техдиректор (write:after_approval); повтор того же состояния без изменений - без проводок;
- автор ввода не подтверждает свой лист; подтверждает прораб своего объекта, техдиректор (резерв)
  или делегат на период (право проверяет триггер, RLS отсекает чужие объекты);
- суточный лимит часов единицы по всем объектам и машинистам (24 ч), под блокировкой строки единицы;
- строки листа: сумма часов, рейсов и тонн равна итогу листа (отложенный триггер, на COMMIT);
- проводка затрат в той же транзакции, что подтверждение (решение D13 A): функция от kut_auth читает
  утверждённый норматив или тариф договора, сама проверяет право подтверждения, пишет строки журнала;
  правка подтверждённого - сторно старой версии и новые строки, всё в audit_log;
- журнал затрат неизменяем: UPDATE и DELETE отклоняются триггером, записи - только определители;
- ремонт закрывается только функцией с суммой: прямой переход в done отклоняется триггером.

Определители - SECURITY DEFINER от kut_auth (BYPASSRLS, владелец функций app.*), search_path пуст.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b6d2f8a4c1e9"
down_revision: str | Sequence[str] | None = "e4f1a9c3b7d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Имена колонок, которые образуют содержание листа: их изменение требует проводки заново.
CONTENT = (
    "equipment_unit_id, project_id, work_date, operator_id, hours, odometer_from, odometer_to,"
    " trips, tonnes, downtime_hours, downtime_reason, assignment_id, source, lines_revision"
)
CONTENT_ROW_NEW = (
    "NEW.equipment_unit_id, NEW.project_id, NEW.work_date, NEW.operator_id, NEW.hours,"
    " NEW.odometer_from, NEW.odometer_to, NEW.trips, NEW.tonnes, NEW.downtime_hours,"
    " NEW.downtime_reason, NEW.assignment_id, NEW.source, NEW.lines_revision"
)
CONTENT_ROW_OLD = CONTENT_ROW_NEW.replace("NEW.", "OLD.")

FUNCTIONS = (
    # Лимит часов и тарифы: вспомогательные определители (их вызывают триггеры от kut_auth).
    """
CREATE FUNCTION app.equipment_day_hours_other(p_unit uuid, p_day date, p_exclude uuid) RETURNS numeric
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE(SUM(s.hours), 0) FROM public.equipment_shift s
    WHERE s.equipment_unit_id = p_unit AND s.work_date = p_day AND s.deleted_at IS NULL
      AND s.status <> 'rejected' AND (p_exclude IS NULL OR s.id <> p_exclude)
$$
""",
    """
CREATE FUNCTION app.equipment_shift_total_quantity(p_shift public.equipment_shift, p_unit text)
RETURNS numeric LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF p_unit = 'hour' THEN
        RETURN p_shift.hours;
    ELSIF p_unit = 'motor_hour' THEN
        IF p_shift.odometer_from IS NULL OR p_shift.odometer_to IS NULL THEN
            RAISE EXCEPTION 'shift.quantity_missing' USING ERRCODE = 'check_violation';
        END IF;
        RETURN p_shift.odometer_to - p_shift.odometer_from;
    ELSIF p_unit = 'trip' THEN
        IF p_shift.trips IS NULL THEN
            RAISE EXCEPTION 'shift.quantity_missing' USING ERRCODE = 'check_violation';
        END IF;
        RETURN p_shift.trips;
    ELSIF p_unit = 'tonne' THEN
        IF p_shift.tonnes IS NULL THEN
            RAISE EXCEPTION 'shift.quantity_missing' USING ERRCODE = 'check_violation';
        END IF;
        RETURN p_shift.tonnes;
    ELSIF p_unit = 'shift' THEN
        RETURN 1;
    END IF;
    RAISE EXCEPTION 'shift.rate_unit_invalid' USING ERRCODE = 'check_violation';
END $$
""",
    # Тариф листа: норматив своей техники на дату (утверждённый) или тариф договора аренды с машинистом
    # или без (по оператору). Неоднозначный тариф - отказ, а не выбор наугад.
    """
CREATE FUNCTION app.equipment_shift_tariff(p_shift public.equipment_shift)
RETURNS TABLE(o_unit text, o_rate numeric, o_category text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_unit public.equipment_unit;
    v_operator public.equipment_operator;
    v_from date;
    v_count integer;
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
    SELECT COUNT(DISTINCT r.unit) INTO v_count FROM public.equipment_rate r
    WHERE r.contract_id = v_unit.contract_id
      AND r.with_operator = (v_operator.employment = 'lessor')
      AND r.deleted_at IS NULL AND r.valid_from <= p_shift.work_date;
    IF v_count = 0 THEN
        RAISE EXCEPTION 'shift.rate_missing' USING ERRCODE = 'check_violation';
    END IF;
    IF v_count > 1 THEN
        RAISE EXCEPTION 'shift.rate_ambiguous' USING ERRCODE = 'check_violation';
    END IF;
    SELECT r.unit, r.amount INTO o_unit, o_rate FROM public.equipment_rate r
    WHERE r.contract_id = v_unit.contract_id
      AND r.with_operator = (v_operator.employment = 'lessor')
      AND r.deleted_at IS NULL AND r.valid_from <= p_shift.work_date
    ORDER BY r.valid_from DESC LIMIT 1;
    o_category := 'equipment_rental';
    RETURN NEXT;
END $$
""",
    # Строки листа: сумма часов, рейсов и тонн совпадает с итогом листа (отложенная проверка на COMMIT).
    """
CREATE FUNCTION app.equipment_shift_lines_check() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_id uuid;
    v_shift public.equipment_shift;
    v_count integer;
    v_hours numeric;
    v_trips numeric;
    v_tonnes numeric;
BEGIN
    IF TG_TABLE_NAME = 'equipment_shift' THEN
        v_id := NEW.id;
    ELSE
        v_id := COALESCE(NEW.shift_id, OLD.shift_id);
    END IF;
    SELECT * INTO v_shift FROM public.equipment_shift WHERE id = v_id;
    IF NOT FOUND OR v_shift.deleted_at IS NOT NULL THEN
        RETURN NULL;
    END IF;
    SELECT COUNT(*), COALESCE(SUM(l.hours), 0), COALESCE(SUM(l.trips), 0),
           COALESCE(SUM(l.tonnes), 0)
      INTO v_count, v_hours, v_trips, v_tonnes
    FROM public.equipment_shift_line l
    WHERE l.shift_id = v_id AND l.deleted_at IS NULL;
    IF v_count > 0 AND (v_hours <> v_shift.hours
                        OR v_trips <> COALESCE(v_shift.trips, 0)
                        OR v_tonnes <> COALESCE(v_shift.tonnes, 0)) THEN
        RAISE EXCEPTION 'shift.lines_mismatch' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END $$
""",
    # Проводка листа: сторно текущей версии, новая версия строк журнала; строки по узлам либо одна строка
    # на объект. Округление - по строке (ROUND до тиына). Разнесение моточасов и смен по строкам - по часам.
    """
CREATE FUNCTION app.equipment_shift_post(p_shift uuid) RETURNS void
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
    SELECT * INTO v_unit FROM public.equipment_unit WHERE id = v_shift.equipment_unit_id;

    IF v_shift.posting_version > 0 THEN
        INSERT INTO public.cost_entry (created_by, legal_entity_id, project_id, node_id,
            schedule_item_id, equipment_unit_id, period, category, source_type, source_id,
            posting_version, line_no, quantity, tariff_unit, rate, amount, reversal_of_id)
        SELECT v_user, c.legal_entity_id, c.project_id, c.node_id, c.schedule_item_id,
            c.equipment_unit_id, c.period, c.category, c.source_type, c.source_id,
            c.posting_version, c.line_no, -c.quantity, c.tariff_unit, c.rate, -c.amount, c.id
        FROM public.cost_entry c
        WHERE c.source_type = 'equipment_shift' AND c.source_id = p_shift
          AND c.posting_version = v_shift.posting_version AND c.reversal_of_id IS NULL
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
END $$
""",
    # Строка журнала после подтверждения: AFTER-триггер листа. Проводка - при переходе в confirmed и при
    # правке содержимого подтверждённого листа (сторно старой версии, новые строки).
    f"""
CREATE FUNCTION app.equipment_shift_after_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.status = 'confirmed' AND (
        OLD.status IS DISTINCT FROM 'confirmed'
        OR ({CONTENT_ROW_NEW}) IS DISTINCT FROM ({CONTENT_ROW_OLD})
    ) THEN
        PERFORM set_config('kut.posting', 'on', true);
        PERFORM app.equipment_shift_post(NEW.id);
        PERFORM set_config('kut.posting', 'off', true);
    END IF;
    RETURN NULL;
END $$
""",
    # Защитный триггер листа: права по роли (app.has_permission), автор, переходы и суточный лимит.
    f"""
CREATE FUNCTION app.equipment_shift_guard() RETURNS trigger
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
    v_posting boolean;
    v_status_changed boolean;
    v_content_changed boolean;
    v_other numeric;
BEGIN
    IF v_user IS NOT NULL THEN
        v_can_write := app.has_permission('equipment', 'write', 'shift');
        v_can_approve := app.has_permission('equipment', 'approve', 'shift');
        v_can_after := app.has_permission('equipment', 'write', 'after_approval');
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
    v_posting := coalesce(current_setting('kut.posting', true), '') = 'on';
    v_status_changed := NEW.status IS DISTINCT FROM OLD.status;
    v_content_changed := ({CONTENT_ROW_NEW}) IS DISTINCT FROM ({CONTENT_ROW_OLD});
    IF NOT v_posting THEN
        NEW.posting_version := OLD.posting_version;
    END IF;
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
    # Строка листа: родительский лист должен быть открыт автору (draft, rejected) или техдиректору
    # (confirmed); каждое изменение строки увеличивает lines_revision - проводка пересчитывается.
    """
CREATE FUNCTION app.equipment_shift_line_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_user uuid := app.user_id();
    v_shift public.equipment_shift;
BEGIN
    SELECT * INTO v_shift FROM public.equipment_shift WHERE id = NEW.shift_id;
    IF NOT FOUND OR v_shift.project_id <> NEW.project_id THEN
        RAISE EXCEPTION 'shift.line_invalid' USING ERRCODE = 'check_violation';
    END IF;
    IF v_shift.status IN ('draft', 'rejected') THEN
        IF v_user IS NOT NULL AND (
            NOT app.has_permission('equipment', 'write', 'shift')
            OR v_user IS DISTINCT FROM v_shift.created_by) THEN
            RAISE EXCEPTION 'shift.forbidden' USING ERRCODE = 'check_violation';
        END IF;
    ELSIF v_shift.status = 'confirmed' THEN
        IF v_user IS NOT NULL AND NOT app.has_permission('equipment', 'write', 'after_approval') THEN
            RAISE EXCEPTION 'shift.forbidden' USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        RAISE EXCEPTION 'shift.locked' USING ERRCODE = 'check_violation';
    END IF;
    UPDATE public.equipment_shift SET lines_revision = lines_revision + 1 WHERE id = NEW.shift_id;
    RETURN NULL;
END $$
""",
    # Заправка: право write:shift, дата не в будущем, юрлицо - от единицы; проводка - строка журнала.
    """
CREATE FUNCTION app.equipment_fuel_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_user uuid := app.user_id();
    v_unit public.equipment_unit;
BEGIN
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
    NEW.legal_entity_id := v_unit.legal_entity_id;
    IF v_user IS NOT NULL THEN
        NEW.created_by := v_user;
    END IF;
    NEW.reversed_at := NULL;
    NEW.reversed_by := NULL;
    NEW.reverse_reason := NULL;
    RETURN NEW;
END $$
""",
    """
CREATE FUNCTION app.equipment_fuel_post() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    INSERT INTO public.cost_entry (created_by, legal_entity_id, project_id, equipment_unit_id,
        period, category, source_type, source_id, posting_version, line_no, quantity, amount)
    VALUES (NEW.created_by, NEW.legal_entity_id, NEW.project_id, NEW.equipment_unit_id,
        NEW.fuel_date, 'equipment_fuel', 'equipment_fuel', NEW.id, 1, 1, NEW.liters, NEW.amount);
    RETURN NULL;
END $$
""",
    # Сторно заправки: строка не меняется, пишется сторно в журнал; повтор - без новых строк.
    """
CREATE FUNCTION app.equipment_fuel_reverse(p_fuel uuid, p_reason text) RETURNS void
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
      AND (v_user IS NULL OR legal_entity_id = ANY ((SELECT app.legal_entity_ids())::uuid[]))
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'fuel.not_found' USING ERRCODE = 'check_violation';
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
    # Ремонт и ТО: закрытие только через функцию. Ремонт - из «в работе» и с суммой (сумма уходит в журнал).
    """
CREATE FUNCTION app.equipment_maintenance_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.status = 'done' AND OLD.status IS DISTINCT FROM 'done'
       AND coalesce(current_setting('kut.maintenance_close', true), '') <> 'on' THEN
        RAISE EXCEPTION 'maintenance.close_forbidden' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$
""",
    """
CREATE FUNCTION app.equipment_maintenance_close(p_maintenance uuid, p_project uuid, p_amount numeric)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_user uuid := app.user_id();
    v_m public.equipment_maintenance;
BEGIN
    IF v_user IS NOT NULL AND NOT app.has_permission('equipment', 'write', 'maintenance') THEN
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
    PERFORM set_config('kut.maintenance_close', 'on', true);
    UPDATE public.equipment_maintenance
    SET status = 'done', done_at = now(), project_id = p_project
    WHERE id = p_maintenance;
    PERFORM set_config('kut.maintenance_close', 'off', true);
    IF p_amount IS NOT NULL AND p_amount > 0 THEN
        INSERT INTO public.cost_entry (created_by, legal_entity_id, project_id, equipment_unit_id,
            period, category, source_type, source_id, posting_version, line_no, amount)
        VALUES (v_user, v_m.legal_entity_id, p_project, v_m.equipment_unit_id,
            app.local_today(), 'equipment_repair', 'equipment_maintenance', p_maintenance, 1, 1,
            p_amount);
    END IF;
END $$
""",
    # Журнал затрат неизменяем: ни приложение, ни владелец, ни воркер не меняют и не удаляют строки.
    """
CREATE FUNCTION app.cost_entry_forbid_change() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'cost_entry.immutable' USING ERRCODE = 'check_violation';
END $$
""",
    # Итоги по объекту и категории для прораба (право подтверждения листа) и для гейта денег; строк
    # журнала и тарифов не возвращает. Накладные (без объекта) - только с гейтом денег.
    """
CREATE FUNCTION app.equipment_cost_totals(p_from date, p_to date, p_project uuid DEFAULT NULL)
RETURNS TABLE(project_id uuid, category text, amount numeric, entries bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_user uuid := app.user_id();
    v_rates boolean := true;
    v_totals boolean := true;
BEGIN
    IF v_user IS NOT NULL THEN
        v_rates := app.has_permission('equipment', 'read', 'rates');
        v_totals := v_rates OR app.has_permission('equipment', 'approve', 'shift');
    END IF;
    IF NOT v_totals THEN
        RETURN;
    END IF;
    RETURN QUERY
    SELECT c.project_id, c.category::text, SUM(c.amount), COUNT(*)
    FROM public.cost_entry c
    WHERE c.period BETWEEN p_from AND p_to
      AND (p_project IS NULL OR c.project_id = p_project)
      AND (v_user IS NULL OR c.legal_entity_id = ANY ((SELECT app.legal_entity_ids())::uuid[]))
      AND ((c.project_id IS NOT NULL AND (v_user IS NULL
            OR c.project_id = ANY ((SELECT app.project_ids())::uuid[])))
           OR (c.project_id IS NULL AND v_rates))
    GROUP BY c.project_id, c.category::text;
END $$
""",
    # Делегат подтверждения - тот, кто ведёт листы на объекте (write:shift), но не машинист.
    """
CREATE FUNCTION app.equipment_delegate_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NOT app.equipment_is_delegate_eligible(NEW.delegate_user_id) THEN
        RAISE EXCEPTION 'delegate.not_eligible' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$
""",
    # Точки расширения SE-1 (зависимости мешают мягкому удалению): листы, строки, ГСМ, делегаты и журнал
    # затрат - живая история, объект, узел и строка ГПР с ней не удаляются.
    """
CREATE OR REPLACE FUNCTION app.project_node_has_dependents(p_node uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.project_node child
        WHERE child.parent_id = p_node AND child.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.schedule_item item
        WHERE item.node_id = p_node AND item.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.work_actual fact
        WHERE fact.node_id = p_node AND fact.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.equipment_request req
        WHERE req.node_id = p_node AND req.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.equipment_assignment asg
        WHERE asg.node_id = p_node AND asg.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.equipment_shift_line ln
        WHERE ln.node_id = p_node AND ln.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.cost_entry cost
        WHERE cost.node_id = p_node)
$$
""",
    """
CREATE OR REPLACE FUNCTION app.schedule_item_has_dependents(p_item uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.schedule_dependency dep
        WHERE (dep.predecessor_id = p_item OR dep.successor_id = p_item)
          AND dep.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.work_actual fact
        WHERE fact.schedule_item_id = p_item AND fact.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.equipment_request req
        WHERE req.schedule_item_id = p_item AND req.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.equipment_assignment asg
        WHERE asg.schedule_item_id = p_item AND asg.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.equipment_shift_line ln
        WHERE ln.schedule_item_id = p_item AND ln.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.cost_entry cost
        WHERE cost.schedule_item_id = p_item)
$$
""",
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
        OR EXISTS (
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
)

# Тела до SE-2a (ревизия c5f1, SE-1): откат возвращает их без листов и журнала.
PREVIOUS_DEPENDENTS = (
    """
CREATE OR REPLACE FUNCTION app.project_node_has_dependents(p_node uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.project_node child
        WHERE child.parent_id = p_node AND child.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.schedule_item item
        WHERE item.node_id = p_node AND item.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.work_actual fact
        WHERE fact.node_id = p_node AND fact.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.equipment_request req
        WHERE req.node_id = p_node AND req.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.equipment_assignment asg
        WHERE asg.node_id = p_node AND asg.deleted_at IS NULL)
$$
""",
    """
CREATE OR REPLACE FUNCTION app.schedule_item_has_dependents(p_item uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.schedule_dependency dep
        WHERE (dep.predecessor_id = p_item OR dep.successor_id = p_item)
          AND dep.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.work_actual fact
        WHERE fact.schedule_item_id = p_item AND fact.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.equipment_request req
        WHERE req.schedule_item_id = p_item AND req.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.equipment_assignment asg
        WHERE asg.schedule_item_id = p_item AND asg.deleted_at IS NULL)
$$
""",
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
)

# Триггеры: (имя, таблица, событие, функция, отложенный?)
TRIGGERS = (
    (
        "equipment_shift_guard",
        "equipment_shift",
        "BEFORE INSERT OR UPDATE",
        "equipment_shift_guard",
        False,
    ),
    (
        "equipment_shift_after_change",
        "equipment_shift",
        "AFTER UPDATE OF status, equipment_unit_id, project_id, work_date, operator_id, hours,"
        " odometer_from, odometer_to, trips, tonnes, downtime_hours, downtime_reason, assignment_id,"
        " lines_revision",
        "equipment_shift_after_change",
        False,
    ),
    (
        "equipment_shift_lines_check_shift",
        "equipment_shift",
        "AFTER UPDATE OF hours, trips, tonnes, lines_revision",
        "equipment_shift_lines_check",
        True,
    ),
    (
        "equipment_shift_line_guard",
        "equipment_shift_line",
        "AFTER INSERT OR UPDATE",
        "equipment_shift_line_guard",
        False,
    ),
    (
        "equipment_shift_lines_check",
        "equipment_shift_line",
        "AFTER INSERT OR UPDATE OR DELETE",
        "equipment_shift_lines_check",
        True,
    ),
    ("equipment_fuel_guard", "equipment_fuel", "BEFORE INSERT", "equipment_fuel_guard", False),
    ("equipment_fuel_post", "equipment_fuel", "AFTER INSERT", "equipment_fuel_post", False),
    (
        "equipment_delegate_guard",
        "equipment_delegate",
        "BEFORE INSERT",
        "equipment_delegate_guard",
        False,
    ),
    (
        "equipment_maintenance_guard",
        "equipment_maintenance",
        "BEFORE UPDATE",
        "equipment_maintenance_guard",
        False,
    ),
    (
        "cost_entry_forbid_change",
        "cost_entry",
        "BEFORE UPDATE OR DELETE",
        "cost_entry_forbid_change",
        False,
    ),
)

# Функции, которые вызывает приложение (kut_app, kut_worker); остальные - только для определителей.
CALLABLE = {
    "equipment_fuel_reverse(uuid, text)",
    "equipment_maintenance_close(uuid, uuid, numeric)",
    "equipment_cost_totals(date, date, uuid)",
}
TRIGGER_FUNCTIONS = (
    "equipment_shift_guard()",
    "equipment_shift_after_change()",
    "equipment_shift_lines_check()",
    "equipment_shift_line_guard()",
    "equipment_fuel_guard()",
    "equipment_fuel_post()",
    "equipment_maintenance_guard()",
    "equipment_delegate_guard()",
    "cost_entry_forbid_change()",
)


def upgrade() -> None:
    # Определители (kut_auth) берут текущую дату листа: app.local_today выдан только клиентским ролям.
    op.execute("GRANT EXECUTE ON FUNCTION app.local_today() TO kut_auth")
    # Функции создаёт и их права меняет владелец kut_auth (REVOKE от kut_owner отказывает).
    op.execute("SET LOCAL ROLE kut_auth")
    for sql in FUNCTIONS:
        op.execute(sql)
    for signature in TRIGGER_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION app.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION app.{signature} TO kut_owner")
    for signature in CALLABLE:
        op.execute(f"REVOKE ALL ON FUNCTION app.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION app.{signature} TO kut_app, kut_worker")
    for signature in (
        "equipment_day_hours_other(uuid, date, uuid)",
        "equipment_shift_total_quantity(public.equipment_shift, text)",
        "equipment_shift_tariff(public.equipment_shift)",
        "equipment_shift_post(uuid)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION app.{signature} FROM PUBLIC")
    op.execute("RESET ROLE")

    for name, table, event, function, deferred in TRIGGERS:
        if deferred:
            op.execute(
                f"CREATE CONSTRAINT TRIGGER {name} {event} ON {table}"
                f" DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app.{function}()"
            )
        else:
            op.execute(
                f"CREATE TRIGGER {name} {event} ON {table} FOR EACH ROW"
                f" EXECUTE FUNCTION app.{function}()"
            )

    # Определители обновляют строки ТО при закрытии и читают их; ремонт закрывает функция.
    op.execute("GRANT UPDATE (status, done_at, project_id) ON equipment_maintenance TO kut_auth")
    op.execute("GRANT SELECT ON equipment_maintenance TO kut_auth")


def downgrade() -> None:
    for name, table, _event, _function, _deferred in reversed(TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    op.execute("SET LOCAL ROLE kut_auth")
    for sql in PREVIOUS_DEPENDENTS:
        op.execute(sql)
    for signature in (
        "equipment_cost_totals(date, date, uuid)",
        "equipment_maintenance_close(uuid, uuid, numeric)",
        "equipment_fuel_reverse(uuid, text)",
        "equipment_day_hours_other(uuid, date, uuid)",
        "equipment_shift_total_quantity(public.equipment_shift, text)",
        "equipment_shift_tariff(public.equipment_shift)",
        "equipment_shift_post(uuid)",
        "equipment_shift_line_guard()",
        "equipment_shift_lines_check()",
        "equipment_shift_after_change()",
        "equipment_shift_guard()",
        "equipment_fuel_post()",
        "equipment_fuel_guard()",
        "equipment_maintenance_guard()",
        "equipment_delegate_guard()",
        "cost_entry_forbid_change()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS app.{signature}")
    op.execute("RESET ROLE")
    op.execute("REVOKE UPDATE (status, done_at, project_id) ON equipment_maintenance FROM kut_auth")
    op.execute("REVOKE EXECUTE ON FUNCTION app.local_today() FROM kut_auth")
