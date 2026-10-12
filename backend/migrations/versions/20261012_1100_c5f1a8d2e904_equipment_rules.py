"""equipment: правила БД SE-1 (ADR-014 часть 1 §1 и §3, часть 3 §9)

Revision ID: c5f1a8d2e904
Revises: b3e9d7a1c5f2
Create Date: 2026-10-12 11:00:00.000000

Что держит БД (ошибка приходит кодом в тексте RAISE, app/core/rules.py переводит его в ответ API):
- суточный лимит часов единицы (решение M = C): сумма плановых часов по всем объектам за любой день
  не больше 24. Функция берёт блокировку строки единицы (SELECT ... FOR UPDATE), иначе две
  параллельные вставки обойдут лимит; ошибка не содержит чисел чужих объектов;
- ремонт в работе (или запланированный) на эти даты блокирует назначение;
- истёкший техосмотр или страховка (последняя версия, valid_until раньше конца назначения)
  блокирует назначение; прочие документы только предупреждают - это в сервисе, не в БД;
- арендованная единица и тариф - только к активному договору аренды того же юрлица;
- норматив утверждается однократно: уже утверждённый approved_by не меняется.

Функции - SECURITY DEFINER от kut_auth: видят строки всех объектов, иначе лимит считал бы неверно.
Правила, которые выражаются CHECK и уникальными индексами, - в ревизии b3e9d7a1c5f2.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c5f1a8d2e904"
down_revision: str | Sequence[str] | None = "b3e9d7a1c5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ASSIGNMENT_GUARD = """
CREATE FUNCTION app.equipment_assignment_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_peak numeric;
    v_expired boolean;
BEGIN
    IF NEW.deleted_at IS NOT NULL OR NEW.status <> 'planned' THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.status = 'planned' AND OLD.deleted_at IS NULL
       AND OLD.equipment_unit_id = NEW.equipment_unit_id AND OLD.date_from = NEW.date_from
       AND OLD.date_to = NEW.date_to AND OLD.planned_hours_per_day = NEW.planned_hours_per_day
    THEN
        RETURN NEW;
    END IF;

    PERFORM 1 FROM public.equipment_unit WHERE id = NEW.equipment_unit_id FOR UPDATE;

    SELECT EXISTS (
        SELECT 1 FROM (
            SELECT DISTINCT ON (d.kind) d.kind, d.valid_until
            FROM public.equipment_document d
            WHERE d.equipment_unit_id = NEW.equipment_unit_id
              AND d.kind IN ('technical_inspection', 'insurance') AND d.deleted_at IS NULL
            ORDER BY d.kind, d.version DESC
        ) latest
        WHERE latest.valid_until < NEW.date_to)
    INTO v_expired;
    IF v_expired THEN
        RAISE EXCEPTION 'equipment.document_expired';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.equipment_maintenance m
        WHERE m.equipment_unit_id = NEW.equipment_unit_id AND m.kind = 'repair'
          AND m.status IN ('planned', 'in_progress') AND m.deleted_at IS NULL
          AND m.planned_from <= NEW.date_to
          AND (m.planned_to IS NULL OR m.planned_to >= NEW.date_from)
    ) THEN
        RAISE EXCEPTION 'equipment.maintenance_overlap';
    END IF;

    SELECT COALESCE(MAX(load.hours), 0) INTO v_peak
    FROM (
        SELECT SUM(a.planned_hours_per_day) AS hours
        FROM generate_series(NEW.date_from::timestamp, NEW.date_to::timestamp,
                             interval '1 day') AS dd(d)
        JOIN public.equipment_assignment a
          ON a.equipment_unit_id = NEW.equipment_unit_id AND a.status = 'planned'
         AND a.deleted_at IS NULL AND a.id <> NEW.id
         AND a.date_from <= dd.d::date AND a.date_to >= dd.d::date
        GROUP BY dd.d
    ) load;
    IF v_peak + NEW.planned_hours_per_day > 24 THEN
        RAISE EXCEPTION 'equipment.day_hours_exceeded';
    END IF;
    RETURN NEW;
END $$
"""

UNIT_GUARD = """
CREATE FUNCTION app.equipment_unit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_contract public.contract;
BEGIN
    IF NEW.contract_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.contract_id IS NOT DISTINCT FROM OLD.contract_id THEN
        RETURN NEW;
    END IF;
    SELECT * INTO v_contract FROM public.contract
    WHERE id = NEW.contract_id AND deleted_at IS NULL;
    IF NOT FOUND OR v_contract.status <> 'active' THEN
        RAISE EXCEPTION 'equipment.contract_not_active';
    END IF;
    IF v_contract.kind <> 'rental_equipment' THEN
        RAISE EXCEPTION 'equipment.contract_kind_invalid';
    END IF;
    IF v_contract.legal_entity_id <> NEW.legal_entity_id THEN
        RAISE EXCEPTION 'equipment.contract_legal_entity_mismatch';
    END IF;
    RETURN NEW;
END $$
"""

RATE_GUARD = """
CREATE FUNCTION app.equipment_rate_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_contract public.contract;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.contract_id IS NOT DISTINCT FROM OLD.contract_id THEN
        RETURN NEW;
    END IF;
    SELECT * INTO v_contract FROM public.contract
    WHERE id = NEW.contract_id AND deleted_at IS NULL;
    IF NOT FOUND OR v_contract.status <> 'active' THEN
        RAISE EXCEPTION 'equipment.contract_not_active';
    END IF;
    IF v_contract.kind <> 'rental_equipment' THEN
        RAISE EXCEPTION 'equipment.contract_kind_invalid';
    END IF;
    RETURN NEW;
END $$
"""

OWN_RATE_GUARD = """
CREATE FUNCTION app.equipment_own_rate_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF OLD.approved_by IS NOT NULL AND NEW.approved_by IS DISTINCT FROM OLD.approved_by THEN
        RAISE EXCEPTION 'equipment.rate_already_approved';
    END IF;
    RETURN NEW;
END $$
"""

NODE_PREVIOUS = """
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
$$
"""

ITEM_PREVIOUS = """
CREATE OR REPLACE FUNCTION app.schedule_item_has_dependents(p_item uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.schedule_dependency dep
        WHERE (dep.predecessor_id = p_item OR dep.successor_id = p_item)
          AND dep.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.work_actual fact
        WHERE fact.schedule_item_id = p_item AND fact.deleted_at IS NULL)
$$
"""

PROJECT_PREVIOUS = """
CREATE OR REPLACE FUNCTION app.project_has_dependents(p_project uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.project_node node
        WHERE node.project_id = p_project AND node.deleted_at IS NULL)
$$
"""

# Точки расширения S2/S3a (app.*_has_dependents): техника ссылается на узел, строку ГПР и объект,
# поэтому живые заявки и назначения тоже мешают удалению. Тела S2/S3a сохранены выше для понижения.
NODE_DEPENDENTS = """
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
"""

ITEM_DEPENDENTS = """
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
"""

PROJECT_DEPENDENTS = """
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
"""

TRIGGERS = (
    (
        "equipment_assignment_guard",
        "equipment_assignment",
        "BEFORE INSERT OR UPDATE",
        "equipment_assignment_guard",
    ),
    (
        "equipment_unit_guard",
        "equipment_unit",
        "BEFORE INSERT OR UPDATE OF contract_id",
        "equipment_unit_guard",
    ),
    (
        "equipment_rate_guard",
        "equipment_rate",
        "BEFORE INSERT OR UPDATE OF contract_id",
        "equipment_rate_guard",
    ),
    (
        "equipment_own_rate_guard",
        "equipment_own_rate",
        "BEFORE UPDATE OF approved_by",
        "equipment_own_rate_guard",
    ),
)


def upgrade() -> None:
    # Функции создаёт владелец kut_auth; CREATE TRIGGER выполняет kut_owner, которому нужен
    # EXECUTE (триггер от имени kut_app права на вызов не проверяет).
    op.execute("SET LOCAL ROLE kut_auth")
    for sql in (ASSIGNMENT_GUARD, UNIT_GUARD, RATE_GUARD, OWN_RATE_GUARD):
        op.execute(sql)
    for _trigger, _table, _event, function in TRIGGERS:
        op.execute(f"REVOKE ALL ON FUNCTION app.{function}() FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION app.{function}() TO kut_owner")
    for sql in (NODE_DEPENDENTS, ITEM_DEPENDENTS, PROJECT_DEPENDENTS):
        op.execute(sql)
    op.execute("RESET ROLE")
    for trigger, table, event, function in TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {trigger} {event} ON {table} FOR EACH ROW"
            f" EXECUTE FUNCTION app.{function}()"
        )


def downgrade() -> None:
    for trigger, table, _event, function in TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS app.{function}()")
    op.execute("SET LOCAL ROLE kut_auth")
    for sql in (NODE_PREVIOUS, ITEM_PREVIOUS, PROJECT_PREVIOUS):
        op.execute(sql)
    op.execute("RESET ROLE")
