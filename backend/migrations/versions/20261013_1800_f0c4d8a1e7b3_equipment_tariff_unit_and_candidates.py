"""equipment: единица тарификации без ставки для экрана машиниста и кандидаты в делегаты

Revision ID: f0c4d8a1e7b3
Revises: e7a2c9f4d8b1
Create Date: 2026-10-13 18:00:00.000000

- app.equipment_tariff(p_unit, p_operator, p_day): тело тарифа листа из ревизии d1f6b3e9a5c2 (после её
  замен) с параметрами вместо строки листа. app.equipment_shift_tariff(p_shift) - обёртка над ним:
  поведение листа не меняется. Вызывают только определители (EXECUTE у PUBLIC нет).
- app.equipment_quantity_unit(p_unit, p_operator, p_day) text - только единица тарификации, без ставки:
  экран машиниста говорит, что вводить (часы, рейсы, тонны). Видимость единицы и оператора проверяет
  функция; EXECUTE у kut_app.
- app.equipment_delegate_candidates(p_project) - кандидаты в делегаты: право approve:shift и объект у
  вызывающего; активные пользователи с доступом к объекту и правом подтверждающего ведения листов
  (app.equipment_is_delegate_eligible). EXECUTE у kut_app.
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "f0c4d8a1e7b3"
down_revision: str | Sequence[str] | None = "e7a2c9f4d8b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RULES_FILE = "20261013_1600_d1f6b3e9a5c2_equipment_se2a_fix_po_rules.py"
TARIFF_SIGNATURE = "FUNCTION app.equipment_shift_tariff(p_shift public.equipment_shift)"

TARIFF_WRAPPER = """
CREATE FUNCTION app.equipment_shift_tariff(p_shift public.equipment_shift)
RETURNS TABLE(o_unit text, o_rate numeric, o_category text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT t.o_unit, t.o_rate, t.o_category
    FROM app.equipment_tariff(p_shift.equipment_unit_id, p_shift.operator_id, p_shift.work_date) t
$$
"""

QUANTITY_UNIT = """
CREATE FUNCTION app.equipment_quantity_unit(p_unit uuid, p_operator uuid, p_day date) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_user uuid := app.user_id();
    v_unit public.equipment_unit;
    v_tariff text;
BEGIN
    SELECT * INTO v_unit FROM public.equipment_unit WHERE id = p_unit AND deleted_at IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'shift.unit_invalid' USING ERRCODE = 'check_violation';
    END IF;
    IF NOT app.equipment_unit_visible(p_unit) THEN
        RAISE EXCEPTION 'shift.unit_invalid' USING ERRCODE = 'check_violation';
    END IF;
    IF v_user IS NOT NULL AND NOT (v_unit.legal_entity_id = ANY ((SELECT app.legal_entity_ids())::uuid[])) THEN
        RAISE EXCEPTION 'shift.unit_invalid' USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.equipment_operator o
        WHERE o.id = p_operator AND o.deleted_at IS NULL
          AND o.legal_entity_id = v_unit.legal_entity_id) THEN
        RAISE EXCEPTION 'shift.operator_invalid' USING ERRCODE = 'check_violation';
    END IF;
    SELECT t.o_unit INTO v_tariff FROM app.equipment_tariff(p_unit, p_operator, p_day) t;
    RETURN v_tariff;
END $$
"""

DELEGATE_CANDIDATES = """
CREATE FUNCTION app.equipment_delegate_candidates(p_project uuid)
RETURNS TABLE(o_user_id uuid, o_full_name text, o_role_code text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NOT (app.has_permission('equipment', 'approve', 'shift')
            AND p_project = ANY ((SELECT app.project_ids())::uuid[])) THEN
        RAISE EXCEPTION 'delegate.forbidden' USING ERRCODE = 'check_violation';
    END IF;
    RETURN QUERY
    SELECT usr.id, usr.full_name::text, r.code::text
    FROM public."user" usr
    JOIN public.role r ON r.id = usr.role_id
    JOIN public.user_project_access upa
      ON upa.user_id = usr.id AND upa.deleted_at IS NULL AND upa.project_id = p_project
    WHERE usr.deleted_at IS NULL AND usr.is_active
      AND app.equipment_is_delegate_eligible(usr.id)
    ORDER BY usr.full_name;
END $$
"""


def _module(file_name: str):  # type: ignore[no-untyped-def]
    path = Path(__file__).with_name(file_name)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tariff_body() -> str:
    """Тело тарифа после замен ревизии d1f6b3e9a5c2, с параметрами вместо строки листа."""
    sql = _module(RULES_FILE)._patched("equipment_shift_tariff")
    if sql.count(TARIFF_SIGNATURE) != 1:
        raise RuntimeError("сигнатура тарифа не найдена")
    sql = sql.replace(
        TARIFF_SIGNATURE,
        "FUNCTION app.equipment_tariff(p_unit uuid, p_operator uuid, p_day date)",
        1,
    )
    for old, new in (
        ("p_shift.equipment_unit_id", "p_unit"),
        ("p_shift.operator_id", "p_operator"),
        ("p_shift.work_date", "p_day"),
    ):
        sql = sql.replace(old, new)
    if "p_shift" in sql:
        raise RuntimeError("в теле тарифа остались ссылки на строку листа")
    return sql.replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1)


def upgrade() -> None:
    # Функции создаёт и их права меняет владелец kut_auth (как в предыдущих ревизиях правил).
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute("DROP FUNCTION IF EXISTS app.equipment_shift_tariff(public.equipment_shift)")
    op.execute(_tariff_body())
    op.execute(TARIFF_WRAPPER)
    op.execute(QUANTITY_UNIT)
    op.execute(DELEGATE_CANDIDATES)
    op.execute("GRANT EXECUTE ON FUNCTION app.equipment_quantity_unit(uuid, uuid, date) TO kut_app")
    op.execute("GRANT EXECUTE ON FUNCTION app.equipment_delegate_candidates(uuid) TO kut_app")
    op.execute("RESET ROLE")


def downgrade() -> None:
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute("DROP FUNCTION IF EXISTS app.equipment_delegate_candidates(uuid)")
    op.execute("DROP FUNCTION IF EXISTS app.equipment_quantity_unit(uuid, uuid, date)")
    op.execute("DROP FUNCTION IF EXISTS app.equipment_shift_tariff(public.equipment_shift)")
    op.execute(_module(RULES_FILE)._patched("equipment_shift_tariff"))
    op.execute("DROP FUNCTION IF EXISTS app.equipment_tariff(uuid, uuid, date)")
    op.execute("RESET ROLE")
