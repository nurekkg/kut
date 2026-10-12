"""equipment: тариф по единице или типу, ГСМ внутри путевого листа, чек - схема (решения PO, SE-2b вход)

Revision ID: 9d4f1b7a2c6e
Revises: b6d2f8a4c1e9
Create Date: 2026-10-13 12:00:00.000000

Что меняет схема (ADR-014 часть 2 §3, решения PO по OPEN SE-2a):
- equipment_rate: тариф привязан к единице или к типу; без привязки - на уровне договора. Уникальность
  версии расширена уровнем: на одном уровне и флаге «с машинистом» - одна единица тарификации.
- equipment_fuel: заправка может принадлежать путевому листу (shift_id, тот же объект), чек - ключ S3.
- Неизменяемость (триггер immutable_columns) пересобрана с новыми колонками; прежние ревизии не тронуты.

Права колонок и политики - в следующей ревизии; определители - в предыдущей и следующей.
Откат: тарифы уровня единицы и типа мягко удаляются (след в audit_log), заправки в листе теряют ссылку
на лист вместе с колонкой shift_id; суммы в журнале затрат остаются.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "9d4f1b7a2c6e"
down_revision: str | Sequence[str] | None = "b6d2f8a4c1e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NIL_UUID = "'00000000-0000-0000-0000-000000000000'::uuid"

RATE_FROZEN_BEFORE = (
    "id",
    "contract_id",
    "unit",
    "with_operator",
    "amount",
    "min_quantity",
    "valid_from",
)
RATE_FROZEN = (*RATE_FROZEN_BEFORE, "equipment_unit_id", "equipment_type_id")
FUEL_FROZEN_BEFORE = (
    "id",
    "legal_entity_id",
    "project_id",
    "equipment_unit_id",
    "fuel_date",
    "liters",
    "amount",
    "source",
    "created_by",
)
FUEL_FROZEN = (*FUEL_FROZEN_BEFORE, "shift_id")


def _freeze(table: str, columns: Sequence[str]) -> None:
    op.execute(f"DROP TRIGGER IF EXISTS immutable_columns ON {table}")
    op.execute(
        f"CREATE TRIGGER immutable_columns BEFORE UPDATE OF {', '.join(columns)} ON {table}"
        f" FOR EACH ROW EXECUTE FUNCTION app.forbid_column_change("
        f"{', '.join(repr(column) for column in columns)})"
    )


def upgrade() -> None:
    op.execute("ALTER TABLE equipment_rate ADD COLUMN equipment_unit_id uuid NULL")
    op.execute("ALTER TABLE equipment_rate ADD COLUMN equipment_type_id uuid NULL")
    op.execute(
        "ALTER TABLE equipment_rate ADD CONSTRAINT"
        " fk_equipment_rate_equipment_unit_id_equipment_unit"
        " FOREIGN KEY (equipment_unit_id) REFERENCES equipment_unit (id)"
    )
    op.execute(
        "ALTER TABLE equipment_rate ADD CONSTRAINT"
        " fk_equipment_rate_equipment_type_id_equipment_type"
        " FOREIGN KEY (equipment_type_id) REFERENCES equipment_type (id)"
    )
    op.execute(
        "ALTER TABLE equipment_rate ADD CONSTRAINT ck_equipment_rate_one_level"
        " CHECK (equipment_unit_id IS NULL OR equipment_type_id IS NULL)"
    )
    op.execute("DROP INDEX uq_equipment_rate_version")
    op.execute(
        "CREATE UNIQUE INDEX uq_equipment_rate_level_version ON equipment_rate"
        f" (contract_id, COALESCE(equipment_unit_id, {NIL_UUID}),"
        f" COALESCE(equipment_type_id, {NIL_UUID}), unit, with_operator, valid_from)"
        " WHERE deleted_at IS NULL"
    )
    _freeze("equipment_rate", RATE_FROZEN)

    op.execute("ALTER TABLE equipment_fuel ADD COLUMN shift_id uuid NULL")
    op.execute("ALTER TABLE equipment_fuel ADD COLUMN receipt_key varchar NULL")
    op.execute("ALTER TABLE equipment_fuel ADD COLUMN receipt_content_type varchar NULL")
    op.execute(
        "ALTER TABLE equipment_fuel ADD CONSTRAINT"
        " fk_equipment_fuel_project_id_shift_id_equipment_shift"
        " FOREIGN KEY (project_id, shift_id) REFERENCES equipment_shift (project_id, id)"
    )
    op.execute(
        "ALTER TABLE equipment_fuel ADD CONSTRAINT ck_equipment_fuel_shift_has_project"
        " CHECK (shift_id IS NULL OR project_id IS NOT NULL)"
    )
    op.execute(
        "ALTER TABLE equipment_fuel ADD CONSTRAINT ck_equipment_fuel_shift_not_reversed"
        " CHECK (shift_id IS NULL OR (reversed_at IS NULL AND reverse_reason IS NULL))"
    )
    op.execute(
        "ALTER TABLE equipment_fuel ADD CONSTRAINT ck_equipment_fuel_receipt_pair"
        " CHECK ((receipt_key IS NULL) = (receipt_content_type IS NULL))"
    )
    op.execute(
        "ALTER TABLE equipment_fuel ADD CONSTRAINT ck_equipment_fuel_receipt_key_not_blank"
        " CHECK (receipt_key IS NULL OR btrim(receipt_key) <> '')"
    )
    op.execute("CREATE INDEX ix_equipment_fuel_shift_id ON equipment_fuel (shift_id)")
    _freeze("equipment_fuel", FUEL_FROZEN)


def _retire_level_rates() -> None:
    """Тарифы уровня единицы или типа мягко удаляются: прежний индекс знает только договор, и живая
    строка уровня дала бы дубль. Строки и прежние значения остаются в таблице и в audit_log; ссылка на
    единицу или тип теряется вместе с колонкой. Права UPDATE(deleted_at) выдаются на время отката."""
    op.execute("GRANT UPDATE (deleted_at) ON equipment_rate TO kut_auth")
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(
        "UPDATE equipment_rate SET deleted_at = now()"
        " WHERE deleted_at IS NULL AND (equipment_unit_id IS NOT NULL OR equipment_type_id IS NOT NULL)"
    )
    op.execute("RESET ROLE")
    op.execute("REVOKE UPDATE (deleted_at) ON equipment_rate FROM kut_auth")


def downgrade() -> None:
    _freeze("equipment_fuel", FUEL_FROZEN_BEFORE)
    op.execute("DROP INDEX IF EXISTS ix_equipment_fuel_shift_id")
    for name in (
        "ck_equipment_fuel_receipt_key_not_blank",
        "ck_equipment_fuel_receipt_pair",
        "ck_equipment_fuel_shift_not_reversed",
        "ck_equipment_fuel_shift_has_project",
        "fk_equipment_fuel_project_id_shift_id_equipment_shift",
    ):
        op.execute(f"ALTER TABLE equipment_fuel DROP CONSTRAINT {name}")
    op.execute(
        "ALTER TABLE equipment_fuel DROP COLUMN receipt_content_type,"
        " DROP COLUMN receipt_key, DROP COLUMN shift_id"
    )

    _freeze("equipment_rate", RATE_FROZEN_BEFORE)
    _retire_level_rates()
    op.execute("DROP INDEX uq_equipment_rate_level_version")
    op.execute(
        "CREATE UNIQUE INDEX uq_equipment_rate_version ON equipment_rate"
        " (contract_id, unit, with_operator, valid_from) WHERE deleted_at IS NULL"
    )
    op.execute("ALTER TABLE equipment_rate DROP CONSTRAINT ck_equipment_rate_one_level")
    op.execute(
        "ALTER TABLE equipment_rate DROP CONSTRAINT fk_equipment_rate_equipment_type_id_equipment_type"
    )
    op.execute(
        "ALTER TABLE equipment_rate DROP CONSTRAINT fk_equipment_rate_equipment_unit_id_equipment_unit"
    )
    op.execute(
        "ALTER TABLE equipment_rate DROP COLUMN equipment_type_id, DROP COLUMN equipment_unit_id"
    )
