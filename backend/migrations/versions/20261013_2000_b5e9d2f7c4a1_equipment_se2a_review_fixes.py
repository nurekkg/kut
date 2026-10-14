"""equipment: исправления по ревью безопасности SE-2a-fix и экранов

Revision ID: b5e9d2f7c4a1
Revises: a9d2e6c1f4b8
Create Date: 2026-10-13 21:00:00.000000

Находки ревью (2026-10-13) и исправления:
- app.equipment_rate_has_postings: проводки считаются от даты начала тарифа без верхней границы.
  Верхняя граница valid_to позволяла сжать тариф (PATCH valid_to) и удалить его вместе с проводками.
- app.equipment_is_delegate_eligible и app.equipment_is_fuel_bill_role: неактивный пользователь не
  подтверждает листы и не вводит накладные ГСМ.
- app.equipment_quantity_unit: машинист получает единицу тарифа только со своим оператором.
- app.equipment_unit_labels: номер регистрации - только для единиц с листом на объекте вызывающего
  (решение PO: номер виден на видимых листах, включая мастера-делегата). Без пользователя
  (системный вызов) ограничения нет, как и раньше.
- app.equipment_fuel_attach_receipt: пустой тип чека отказан (NOT IN при NULL не отказывал).
- Политика SELECT equipment_fuel: ветка «свои заправки» требует видимости юрлица и объекта, поэтому
  после отзыва доступа к объекту создатель их не видит.

Изменения - точечные замены в определениях прежних ревизий: каждая проверяет единственное вхождение
фрагмента. Откат возвращает прежние определения и прежнюю политику.
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "b5e9d2f7c4a1"
down_revision: str | Sequence[str] | None = "a9d2e6c1f4b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _module(file_name: str):  # type: ignore[no-untyped-def]
    path = Path(__file__).with_name(file_name)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GRANTS = _module("20261013_1400_f2b7d4c9e1a6_equipment_se2b_grants.py")
RULES = _module("20261013_1600_d1f6b3e9a5c2_equipment_se2a_fix_po_rules.py")
SCHEMA = _module("20261013_1000_e4f1a9c3b7d2_equipment_shifts_schema.py")
TARIFF = _module("20261013_1800_f0c4d8a1e7b3_equipment_tariff_unit_and_candidates.py")
LABELS = _module("20261013_1900_a9d2e6c1f4b8_equipment_unit_labels.py")


def _definition(sources: Sequence[str], name: str) -> str:
    found = [text for text in sources if f"CREATE FUNCTION app.{name}(" in text]
    if len(found) != 1:
        raise RuntimeError(f"определение app.{name} не найдено однозначно")
    return found[0]


# (прежнее определение, фрагмент, замена)
CHANGES: tuple[tuple[str, str, str], ...] = (
    (
        _definition(RULES.NEW_FUNCTIONS, "equipment_rate_has_postings"),
        "          AND c.period >= r.valid_from\n"
        "          AND (r.valid_to IS NULL OR c.period <= r.valid_to)\n",
        "          AND c.period >= r.valid_from\n",
    ),
    (
        _definition(RULES.NEW_FUNCTIONS, "equipment_is_fuel_bill_role"),
        "WHERE usr.id = app.user_id() AND usr.deleted_at IS NULL",
        "WHERE usr.id = app.user_id() AND usr.deleted_at IS NULL AND usr.is_active",
    ),
    (
        _definition([SCHEMA.ELIGIBLE_FUNCTION], "equipment_is_delegate_eligible"),
        "WHERE u.id = p_user AND u.deleted_at IS NULL AND r.code <> 'machinist'",
        "WHERE u.id = p_user AND u.deleted_at IS NULL AND u.is_active AND r.code <> 'machinist'",
    ),
    (
        _definition([TARIFF.QUANTITY_UNIT], "equipment_quantity_unit"),
        "          AND o.legal_entity_id = v_unit.legal_entity_id) THEN",
        "          AND o.legal_entity_id = v_unit.legal_entity_id\n"
        "          AND (v_user IS NULL OR NOT app.is_machinist() OR o.user_id = v_user)) THEN",
    ),
    (
        _definition([LABELS.UNIT_LABELS], "equipment_unit_labels"),
        "WHERE u.id = ANY(p_units) AND u.deleted_at IS NULL",
        "WHERE u.id = ANY(p_units) AND u.deleted_at IS NULL\n"
        "      AND (app.user_id() IS NULL OR EXISTS (\n"
        "          SELECT 1 FROM public.equipment_shift s\n"
        "          WHERE s.equipment_unit_id = u.id AND s.deleted_at IS NULL\n"
        "            AND s.project_id = ANY ((SELECT app.project_ids())::uuid[])))",
    ),
    (
        _definition(RULES.NEW_FUNCTIONS, "equipment_fuel_attach_receipt"),
        "IF p_content_type NOT IN (",
        "IF p_content_type IS NULL OR p_content_type NOT IN (",
    ),
)

FUEL_SELECT_OLD = (
    f"equipment_fuel.created_by = app.user_id() OR ({GRANTS.FUEL_SCOPE} AND {GRANTS.CAN_READ}"
    f" AND NOT {GRANTS.IS_MACHINIST}) OR ({GRANTS.OWN_SHEET_FUEL})"
)
FUEL_SELECT_NEW = (
    f"(equipment_fuel.created_by = app.user_id() AND {GRANTS.FUEL_SCOPE})"
    f" OR ({GRANTS.FUEL_SCOPE} AND {GRANTS.CAN_READ} AND NOT {GRANTS.IS_MACHINIST})"
    f" OR ({GRANTS.OWN_SHEET_FUEL})"
)


def _patched(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise RuntimeError(f"фрагмент не найден однозначно: {old[:60]!r}")
    return source.replace(old, new).replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def _restore(source: str) -> str:
    return source.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def _fuel_select(expression: str) -> None:
    op.execute("DROP POLICY IF EXISTS equipment_fuel_select_app ON equipment_fuel")
    op.execute(
        "CREATE POLICY equipment_fuel_select_app ON equipment_fuel FOR SELECT TO kut_app"
        f" USING ({expression})"
    )


def upgrade() -> None:
    op.execute("SET LOCAL ROLE kut_auth")
    for source, old, new in CHANGES:
        op.execute(_patched(source, old, new))
    op.execute("RESET ROLE")
    _fuel_select(FUEL_SELECT_NEW)


def downgrade() -> None:
    _fuel_select(FUEL_SELECT_OLD)
    op.execute("SET LOCAL ROLE kut_auth")
    for source, _, _ in CHANGES:
        op.execute(_restore(source))
    op.execute("RESET ROLE")
