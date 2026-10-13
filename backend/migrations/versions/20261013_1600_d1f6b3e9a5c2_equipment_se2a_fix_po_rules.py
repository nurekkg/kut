"""equipment: правила решений PO 2026-10-13 - окно делегата, подтверждающий не оператор, накладной ГСМ,
итоги только с read:rates, срок тарифа, чек, ремонт в done только через определитель

Revision ID: d1f6b3e9a5c2
Revises: c4e8a2d7b9f1
Create Date: 2026-10-13 16:00:00.000000

Что меняет ревизия (решения PO 1-8 от 2026-10-13, wiki/arch/se2a-shifts-costs.md):
- окно делегата: право подтверждения и проводки - по дате подтверждения (app.local_today()), не по дате листа;
  политика UPDATE листа - та же дата;
- подтверждающий не оператор листа, если у оператора есть учётка (shift.approver_is_operator);
- накладная заправка без объекта - только equipment_head и technical_director (политика INSERT и триггер);
- итоги журнала - только с read:rates (approve:shift больше не даёт сумм);
- тариф с valid_to: выбор по дате работы учитывает окончание; тариф без проводок удаляется мягко,
  с проводками - rate.has_postings;
- чек заправки: проверка права и статуса до подписи presign (app.equipment_fuel_receipt_check), запись чека
  через определитель app.equipment_fuel_attach_receipt (право по листу, статус черновика или отклонения,
  префикс ключа по заправке, тип из S3);
- ремонт, заведённый сразу в done, отказан (триггер вставки; закрытие - только определитель).

Заменяемые функции берутся из ревизии a3c6e8f1d2b9 (итог SE-2b) и меняются заменами: каждая замена
должна совпасть ровно указанное число раз, иначе миграция остановится. В откате тела возвращаются из
a3c6e8f1d2b9 и b6d2f8a4c1e9 (файлы читаются по пути).
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "d1f6b3e9a5c2"
down_revision: str | Sequence[str] | None = "c4e8a2d7b9f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROJECTS = "((SELECT app.project_ids())::uuid[])"
LEGAL = "((SELECT app.legal_entity_ids())::uuid[])"
IS_MACHINIST = "(SELECT app.is_machinist())"
IS_BILL_ROLE = "(SELECT app.equipment_is_fuel_bill_role())"
CAN_READ = "(SELECT app.has_permission('equipment', 'read'))"
CAN_WRITE_SHIFT = "(SELECT app.has_permission('equipment', 'write', 'shift'))"
CAN_APPROVE_SHIFT = "(SELECT app.has_permission('equipment', 'approve', 'shift'))"
CAN_AFTER_APPROVAL = "(SELECT app.has_permission('equipment', 'write', 'after_approval'))"
OWN_OPERATOR_OF_SHIFT = (
    "EXISTS (SELECT 1 FROM public.equipment_operator o"
    " WHERE o.id = equipment_shift.operator_id AND o.user_id = app.user_id())"
)
SHIFT_SCOPE = (
    f"equipment_shift.project_id = ANY {PROJECTS} AND {CAN_READ}"
    f" AND (NOT {IS_MACHINIST} OR {OWN_OPERATOR_OF_SHIFT})"
)
FUEL_SCOPE = (
    f"equipment_fuel.legal_entity_id = ANY {LEGAL}"
    f" AND (equipment_fuel.project_id IS NULL OR equipment_fuel.project_id = ANY {PROJECTS})"
)

# Политики в прежнем виде (ревизии e4f1a9c3b7d2, f2b7d4c9e1a6) - для отката.
OLD_SHIFT_UPDATE = (
    f"{SHIFT_SCOPE} AND ((equipment_shift.created_by = app.user_id() AND {CAN_WRITE_SHIFT})"
    f" OR {CAN_APPROVE_SHIFT} OR {CAN_AFTER_APPROVAL}"
    f" OR app.equipment_delegated(equipment_shift.project_id, equipment_shift.work_date))"
)
NEW_SHIFT_UPDATE = OLD_SHIFT_UPDATE.replace("equipment_shift.work_date))", "app.local_today()))")
OLD_FUEL_INSERT = (
    f"(equipment_fuel.shift_id IS NULL AND {FUEL_SCOPE} AND {CAN_WRITE_SHIFT}"
    f" AND NOT {IS_MACHINIST})"
    f" OR (equipment_fuel.shift_id IS NOT NULL AND {FUEL_SCOPE}"
    f" AND ({CAN_WRITE_SHIFT} OR {CAN_AFTER_APPROVAL}))"
)
NEW_FUEL_INSERT = (
    f"(equipment_fuel.shift_id IS NULL AND equipment_fuel.project_id IS NULL"
    f" AND {FUEL_SCOPE} AND {IS_BILL_ROLE})"
    f" OR (equipment_fuel.shift_id IS NULL AND equipment_fuel.project_id IS NOT NULL"
    f" AND {FUEL_SCOPE} AND {CAN_WRITE_SHIFT} AND NOT {IS_MACHINIST})"
    f" OR (equipment_fuel.shift_id IS NOT NULL AND {FUEL_SCOPE}"
    f" AND ({CAN_WRITE_SHIFT} OR {CAN_AFTER_APPROVAL}))"
)

# Замены в телах функций из a3c6e8f1d2b9: (что найти, на что заменить, сколько раз должно совпасть).
PATCHES: dict[str, tuple[tuple[str, str, int], ...]] = {
    # Тариф: окончание тарифа (valid_to) выводит его из выбора на более поздние даты.
    "equipment_shift_tariff": (
        (
            "AND r.deleted_at IS NULL AND r.valid_from <= p_shift.work_date\n          AND CASE v_level",
            "AND r.deleted_at IS NULL AND r.valid_from <= p_shift.work_date\n"
            "          AND (r.valid_to IS NULL OR r.valid_to >= p_shift.work_date)\n"
            "          AND CASE v_level",
            2,
        ),
    ),
    # Проводка: окно делегата по дате проводки (подтверждения), не по дате листа.
    "equipment_shift_post": (
        (
            "        OR app.equipment_delegated(v_shift.project_id, v_shift.work_date)\n",
            "        OR app.equipment_delegated(v_shift.project_id, app.local_today())\n",
            1,
        ),
    ),
    # Подтверждение: окно делегата по дате подтверждения; подтверждающий не оператор листа.
    "equipment_shift_guard": (
        (
            "            IF NOT (v_can_approve OR app.equipment_delegated(NEW.project_id, NEW.work_date)) THEN\n"
            "                RAISE EXCEPTION 'shift.forbidden' USING ERRCODE = 'check_violation';\n"
            "            END IF;\n",
            "            IF NOT (v_can_approve OR app.equipment_delegated(NEW.project_id, app.local_today())) THEN\n"
            "                RAISE EXCEPTION 'shift.forbidden' USING ERRCODE = 'check_violation';\n"
            "            END IF;\n"
            "            IF EXISTS (\n"
            "                SELECT 1 FROM public.equipment_operator o\n"
            "                WHERE o.id = NEW.operator_id AND o.user_id = v_user) THEN\n"
            "                RAISE EXCEPTION 'shift.approver_is_operator' USING ERRCODE = 'check_violation';\n"
            "            END IF;\n",
            1,
        ),
    ),
    # Заправка без листа: накладная без объекта - только equipment_head и technical_director.
    "equipment_fuel_guard": (
        (
            "    IF NEW.shift_id IS NULL THEN\n"
            "        IF v_user IS NOT NULL AND (\n"
            "            NOT app.has_permission('equipment', 'write', 'shift') OR app.is_machinist()\n"
            "        ) THEN\n"
            "            RAISE EXCEPTION 'fuel.forbidden' USING ERRCODE = 'check_violation';\n"
            "        END IF;\n",
            "    IF NEW.shift_id IS NULL THEN\n"
            "        IF v_user IS NOT NULL AND NEW.project_id IS NULL"
            " AND NOT app.equipment_is_fuel_bill_role() THEN\n"
            "            RAISE EXCEPTION 'fuel.forbidden' USING ERRCODE = 'check_violation';\n"
            "        END IF;\n"
            "        IF v_user IS NOT NULL AND NEW.project_id IS NOT NULL AND (\n"
            "            NOT app.has_permission('equipment', 'write', 'shift') OR app.is_machinist()\n"
            "        ) THEN\n"
            "            RAISE EXCEPTION 'fuel.forbidden' USING ERRCODE = 'check_violation';\n"
            "        END IF;\n",
            1,
        ),
    ),
}

# Функции новой ревизии. Определители чека и ремонта вызывает приложение (kut_app), поэтому их EXECUTE
# остаётся у PUBLIC; проверки внутри - по контексту запроса и объекту.
NEW_FUNCTIONS = (
    """
CREATE FUNCTION app.equipment_is_fuel_bill_role() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public."user" usr
        JOIN public.role r ON r.id = usr.role_id
        WHERE usr.id = app.user_id() AND usr.deleted_at IS NULL
          AND r.code IN ('equipment_head', 'technical_director'))
$$
""",
    # Право на чек до подписи presign и до записи: заправка листа, объект, право записи листа, статус
    # черновика или отклонения (после отправки и подтверждения чек не меняют), право на сам лист.
    """
CREATE FUNCTION app.equipment_fuel_receipt_check(p_fuel uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_user uuid := app.user_id();
    v_fuel public.equipment_fuel;
    v_shift public.equipment_shift;
BEGIN
    IF v_user IS NULL THEN
        RETURN;
    END IF;
    SELECT * INTO v_fuel FROM public.equipment_fuel
    WHERE id = p_fuel AND deleted_at IS NULL AND shift_id IS NOT NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'fuel.not_found' USING ERRCODE = 'check_violation';
    END IF;
    IF NOT (v_fuel.project_id = ANY ((SELECT app.project_ids())::uuid[])) THEN
        RAISE EXCEPTION 'fuel.forbidden' USING ERRCODE = 'check_violation';
    END IF;
    IF NOT (app.has_permission('equipment', 'write', 'shift')
            OR app.has_permission('equipment', 'write', 'after_approval')) THEN
        RAISE EXCEPTION 'fuel.forbidden' USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO v_shift FROM public.equipment_shift WHERE id = v_fuel.shift_id;
    IF v_shift.status NOT IN ('draft', 'rejected') THEN
        RAISE EXCEPTION 'receipt.locked' USING ERRCODE = 'check_violation';
    END IF;
    PERFORM app.equipment_fuel_sheet_check(v_shift);
END $$
""",
    # Запись чека: проверка - как у подписи; ключ - внутри префикса заправки; тип - из допустимых.
    """
CREATE FUNCTION app.equipment_fuel_attach_receipt(p_fuel uuid, p_key text, p_content_type text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_user uuid := app.user_id();
    v_fuel public.equipment_fuel;
    v_prefix text;
BEGIN
    PERFORM app.equipment_fuel_receipt_check(p_fuel);
    IF p_content_type NOT IN ('image/jpeg', 'image/png', 'image/heic', 'image/webp') THEN
        RAISE EXCEPTION 'receipt.invalid' USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO v_fuel FROM public.equipment_fuel WHERE id = p_fuel;
    v_prefix := 'project/' || v_fuel.project_id || '/shift/' || v_fuel.shift_id
        || '/fuel/' || v_fuel.id || '/';
    IF left(p_key, length(v_prefix)) IS DISTINCT FROM v_prefix THEN
        RAISE EXCEPTION 'receipt.invalid' USING ERRCODE = 'check_violation';
    END IF;
    UPDATE public.equipment_fuel
    SET receipt_key = p_key, receipt_content_type = p_content_type,
        receipt_uploaded_by = v_user, updated_at = now(), updated_by = v_user
    WHERE id = p_fuel;
END $$
""",
    # Тариф с проводками: есть журнальные строки аренды по его технике и периоду действия (для
    # конкретной единицы, типа или всего договора). Проверка консервативна: сторно строк тоже учитываются.
    """
CREATE FUNCTION app.equipment_rate_has_postings(p_rate uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.equipment_rate r
        JOIN public.equipment_unit u ON u.contract_id = r.contract_id
        JOIN public.cost_entry c ON c.equipment_unit_id = u.id
        WHERE r.id = p_rate
          AND c.category = 'equipment_rental'
          AND c.period >= r.valid_from
          AND (r.valid_to IS NULL OR c.period <= r.valid_to)
          AND (r.equipment_unit_id IS NULL OR u.id = r.equipment_unit_id)
          AND (r.equipment_type_id IS NULL OR u.equipment_type_id = r.equipment_type_id))
$$
""",
    """
CREATE FUNCTION app.equipment_rate_delete_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.deleted_at IS NOT NULL AND OLD.deleted_at IS NULL
       AND app.equipment_rate_has_postings(NEW.id) THEN
        RAISE EXCEPTION 'rate.has_postings' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$
""",
    # Ремонт заводят в работе (in_progress) или плане; статус done - только через определитель закрытия.
    """
CREATE FUNCTION app.equipment_maintenance_insert_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.status = 'done' AND current_user <> 'kut_auth' THEN
        RAISE EXCEPTION 'maintenance.close_forbidden' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$
""",
)

# Итоги журнала: только с read:rates. Прорабу (approve:shift) сумм и денежных категорий нет.
COST_TOTALS = """
CREATE OR REPLACE FUNCTION app.equipment_cost_totals(p_from date, p_to date, p_project uuid DEFAULT NULL)
RETURNS TABLE(project_id uuid, category text, amount numeric, entries bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_user uuid := app.user_id();
    v_rates boolean := true;
BEGIN
    IF v_user IS NOT NULL THEN
        v_rates := app.has_permission('equipment', 'read', 'rates');
    END IF;
    IF NOT v_rates THEN
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
           OR c.project_id IS NULL)
    GROUP BY c.project_id, c.category::text;
END $$
"""

# Функции, которые вызывает приложение (политика INSERT или сервис): EXECUTE у kut_app явный - у PUBLIC нет.
APP_CALLABLE = (
    "equipment_is_fuel_bill_role()",
    "equipment_fuel_receipt_check(uuid)",
    "equipment_fuel_attach_receipt(uuid, text, text)",
)

# Триггеры новой ревизии: (имя, таблица, событие, функция).
TRIGGERS = (
    (
        "equipment_rate_delete_guard",
        "equipment_rate",
        "BEFORE UPDATE OF deleted_at",
        "equipment_rate_delete_guard",
    ),
    (
        "equipment_maintenance_insert_guard",
        "equipment_maintenance",
        "BEFORE INSERT",
        "equipment_maintenance_insert_guard",
    ),
)
# Функции, которые вызывают только триггеры или политики: у PUBLIC EXECUTE нет.
TRIGGER_ONLY = ("equipment_rate_delete_guard()", "equipment_maintenance_insert_guard()")
HELPERS_ONLY = ("equipment_rate_has_postings(uuid)",)

ROLLBACK_NEW = (
    "equipment_maintenance_insert_guard()",
    "equipment_rate_delete_guard()",
    "equipment_rate_has_postings(uuid)",
    "equipment_fuel_attach_receipt(uuid, text, text)",
    "equipment_fuel_receipt_check(uuid)",
    "equipment_is_fuel_bill_role()",
)


def _module(file_name: str):  # type: ignore[no-untyped-def]
    path = Path(__file__).with_name(file_name)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _previous(name: str) -> str:
    """Тело функции до этой ревизии: a3c6e8f1d2b9 (итог SE-2b), для остальных - b6d2f8a4c1e9."""
    marker = f"FUNCTION app.{name}("
    for file_name, attribute in (
        ("20261013_1300_a3c6e8f1d2b9_equipment_se2b_rules.py", "FUNCTIONS"),
        ("20261013_1100_b6d2f8a4c1e9_equipment_shifts_rules.py", "FUNCTIONS"),
        ("20261013_1100_b6d2f8a4c1e9_equipment_shifts_rules.py", "PREVIOUS_DEPENDENTS"),
    ):
        for sql in getattr(_module(file_name), attribute, ()):
            if marker in sql:
                return sql.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    raise LookupError(name)


def _patched(name: str) -> str:
    sql = _previous(name)
    for old, new, count in PATCHES[name]:
        found = sql.count(old)
        if found != count:
            raise RuntimeError(f"{name}: найдено {found} вместо {count}: {old[:70]!r}")
        sql = sql.replace(old, new)
    return sql


def _policy(table: str, command: str, expression: str) -> None:
    name = f"{table}_{command.lower()}_app"
    op.execute(f"DROP POLICY IF EXISTS {name} ON {table}")
    if command == "INSERT":
        op.execute(
            f"CREATE POLICY {name} ON {table} FOR INSERT TO kut_app WITH CHECK ({expression})"
        )
    else:
        op.execute(
            f"CREATE POLICY {name} ON {table} FOR UPDATE TO kut_app"
            f" USING ({expression}) WITH CHECK ({expression})"
        )


def upgrade() -> None:
    # Функции создаёт и их права меняет владелец kut_auth (как в предыдущих ревизиях правил).
    op.execute("SET LOCAL ROLE kut_auth")
    for name in PATCHES:
        op.execute(_patched(name))
    op.execute(COST_TOTALS)
    for sql in NEW_FUNCTIONS:
        op.execute(sql)
    for signature in (*TRIGGER_ONLY, *HELPERS_ONLY):
        op.execute(f"REVOKE ALL ON FUNCTION app.{signature} FROM PUBLIC")
    for signature in TRIGGER_ONLY:
        op.execute(f"GRANT EXECUTE ON FUNCTION app.{signature} TO kut_owner")
    for signature in APP_CALLABLE:
        op.execute(f"GRANT EXECUTE ON FUNCTION app.{signature} TO kut_app")
    op.execute("RESET ROLE")

    _policy("equipment_shift", "UPDATE", NEW_SHIFT_UPDATE)
    _policy("equipment_fuel", "INSERT", NEW_FUEL_INSERT)

    for name, table, event, function in TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {name} {event} ON {table} FOR EACH ROW"
            f" EXECUTE FUNCTION app.{function}()"
        )


def downgrade() -> None:
    for name, table, _event, _function in reversed(TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    _policy("equipment_fuel", "INSERT", OLD_FUEL_INSERT)
    _policy("equipment_shift", "UPDATE", OLD_SHIFT_UPDATE)
    op.execute("SET LOCAL ROLE kut_auth")
    for name in PATCHES:
        op.execute(_previous(name))
    op.execute(_previous("equipment_cost_totals"))
    for signature in ROLLBACK_NEW:
        op.execute(f"DROP FUNCTION IF EXISTS app.{signature}")
    op.execute("RESET ROLE")
