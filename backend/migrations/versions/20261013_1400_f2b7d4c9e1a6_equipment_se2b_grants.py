"""equipment: права колонок ГСМ и версий листа, политики заправки в листе - SE-2b (ADR-014 часть 2 §3)

Revision ID: f2b7d4c9e1a6
Revises: a3c6e8f1d2b9
Create Date: 2026-10-13 14:00:00.000000

- equipment_fuel.amount: приложение сумму не читает (колонка SELECT у kut_app не выдана); сумму видит
  только журнал затрат с гейтом read:rates. Записать сумму приложение может (INSERT).
- equipment_fuel: приложение меняет только мягкое удаление и чек (колонки UPDATE); заправку без листа
  не удаляют - сторно пишет определитель.
- equipment_shift: версии листа (posting_version, lines_revision) меняет только определитель; у kut_app и
  kut_worker колонок UPDATE на них нет.
- политики заправки: в листе - машинист этого листа, автор с write:shift и техдиректор после согласования
  (точную проверку делает триггер equipment_fuel_guard через equipment_fuel_sheet_check).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f2b7d4c9e1a6"
down_revision: str | Sequence[str] | None = "a3c6e8f1d2b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROJECTS = "((SELECT app.project_ids())::uuid[])"
LEGAL = "((SELECT app.legal_entity_ids())::uuid[])"
IS_MACHINIST = "(SELECT app.is_machinist())"
CAN_READ = "(SELECT app.has_permission('equipment', 'read'))"
CAN_WRITE_SHIFT = "(SELECT app.has_permission('equipment', 'write', 'shift'))"
CAN_AFTER_APPROVAL = "(SELECT app.has_permission('equipment', 'write', 'after_approval'))"
FUEL_SCOPE = (
    f"equipment_fuel.legal_entity_id = ANY {LEGAL}"
    f" AND (equipment_fuel.project_id IS NULL OR equipment_fuel.project_id = ANY {PROJECTS})"
)
OWN_SHEET_FUEL = (
    "equipment_fuel.shift_id IS NOT NULL AND EXISTS (SELECT 1 FROM public.equipment_shift s"
    " JOIN public.equipment_operator o ON o.id = s.operator_id"
    " WHERE s.id = equipment_fuel.shift_id AND o.user_id = app.user_id())"
)
# Прежние выражения SE-2a (ревизия e4f1a9c3b7d2) - для отката.
OLD_SELECT = f"equipment_fuel.created_by = app.user_id() OR ({FUEL_SCOPE} AND {CAN_READ} AND NOT {IS_MACHINIST})"
OLD_INSERT = f"{FUEL_SCOPE} AND {CAN_WRITE_SHIFT} AND NOT {IS_MACHINIST}"
OLD_UPDATE = f"{FUEL_SCOPE} AND {CAN_AFTER_APPROVAL}"

FUEL_RECEIPT_COLUMNS = (
    "deleted_at",
    "receipt_key",
    "receipt_content_type",
    "updated_at",
    "updated_by",
)
SHIFT_FROZEN_COLUMNS = ("id", "created_at", "posting_version", "lines_revision")


def _column_privilege(
    action: str, privilege: str, table: str, logins: str, excluded: Sequence[str] = ()
) -> None:
    """GRANT/REVOKE привилегии на все колонки таблицы, кроме перечисленных (список берётся из каталога)."""
    preposition = "TO" if action == "GRANT" else "FROM"
    names = ", ".join(f"'{column}'" for column in excluded) or "''"
    op.execute(
        "DO $$ DECLARE cols text; BEGIN"
        " SELECT string_agg(quote_ident(column_name), ', ' ORDER BY ordinal_position) INTO cols"
        " FROM information_schema.columns WHERE table_schema = 'public'"
        f" AND table_name = '{table}' AND column_name NOT IN ({names});"
        f" EXECUTE format('{action} {privilege} (%s) ON {table} {preposition} {logins}', cols);"
        " END $$"
    )


def _policies(select: str, insert: str, update: str) -> None:
    op.execute("DROP POLICY IF EXISTS equipment_fuel_select_app ON equipment_fuel")
    op.execute("DROP POLICY IF EXISTS equipment_fuel_insert_app ON equipment_fuel")
    op.execute("DROP POLICY IF EXISTS equipment_fuel_update_app ON equipment_fuel")
    op.execute(
        f"CREATE POLICY equipment_fuel_select_app ON equipment_fuel FOR SELECT TO kut_app"
        f" USING ({select})"
    )
    op.execute(
        f"CREATE POLICY equipment_fuel_insert_app ON equipment_fuel FOR INSERT TO kut_app"
        f" WITH CHECK ({insert})"
    )
    op.execute(
        f"CREATE POLICY equipment_fuel_update_app ON equipment_fuel FOR UPDATE TO kut_app"
        f" USING ({update}) WITH CHECK ({update})"
    )


def upgrade() -> None:
    # Сумма ГСМ: приложение её не читает (гейт - журнал затрат, read:rates). Колонки SELECT - кроме amount.
    op.execute("REVOKE SELECT ON equipment_fuel FROM kut_app")
    _column_privilege("GRANT", "SELECT", "equipment_fuel", "kut_app", excluded=("amount",))
    op.execute(f"GRANT UPDATE ({', '.join(FUEL_RECEIPT_COLUMNS)}) ON equipment_fuel TO kut_app")

    # Версии листа меняет только определитель.
    op.execute("REVOKE UPDATE ON equipment_shift FROM kut_app, kut_worker")
    _column_privilege(
        "GRANT", "UPDATE", "equipment_shift", "kut_app, kut_worker", excluded=SHIFT_FROZEN_COLUMNS
    )

    scope_insert = (
        f"(equipment_fuel.shift_id IS NULL AND {FUEL_SCOPE} AND {CAN_WRITE_SHIFT}"
        f" AND NOT {IS_MACHINIST})"
        f" OR (equipment_fuel.shift_id IS NOT NULL AND {FUEL_SCOPE}"
        f" AND ({CAN_WRITE_SHIFT} OR {CAN_AFTER_APPROVAL}))"
    )
    _policies(
        select=(
            f"equipment_fuel.created_by = app.user_id() OR ({FUEL_SCOPE} AND {CAN_READ}"
            f" AND NOT {IS_MACHINIST}) OR ({OWN_SHEET_FUEL})"
        ),
        insert=scope_insert,
        update=f"{FUEL_SCOPE} AND ({CAN_WRITE_SHIFT} OR {CAN_AFTER_APPROVAL})",
    )


def downgrade() -> None:
    _policies(select=OLD_SELECT, insert=OLD_INSERT, update=OLD_UPDATE)
    _column_privilege("REVOKE", "UPDATE", "equipment_shift", "kut_app, kut_worker")
    op.execute("GRANT UPDATE ON equipment_shift TO kut_app, kut_worker")
    op.execute(f"REVOKE UPDATE ({', '.join(FUEL_RECEIPT_COLUMNS)}) ON equipment_fuel FROM kut_app")
    _column_privilege("REVOKE", "SELECT", "equipment_fuel", "kut_app")
    op.execute("GRANT SELECT ON equipment_fuel TO kut_app")
