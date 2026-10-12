"""equipment: исправления SE-1a (решения PO 2026-10-12)

Revision ID: d7b2e9c1a4f8
Revises: c5f1a8d2e904
Create Date: 2026-10-12 12:00:00.000000

- Назначение арендованной единицы только при активном договоре, который покрывает даты назначения
  (`equipment.contract_inactive`, 409): проверка в app.equipment_assignment_guard.
- Номер и контрагент договора неизменяемы после активации (триггер на UPDATE, пока статус не draft).
  Изменения - через новую версию или допсоглашение.
- Реестр: марка и модель (`model`) и год выпуска (`year`, 1950-2100).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7b2e9c1a4f8"
down_revision: str | Sequence[str] | None = "c5f1a8d2e904"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ASSIGNMENT_GUARD = """
CREATE OR REPLACE FUNCTION app.equipment_assignment_guard() RETURNS trigger
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

NEW_ASSIGNMENT_GUARD = """
CREATE OR REPLACE FUNCTION app.equipment_assignment_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_peak numeric;
    v_expired boolean;
    v_contract public.contract;
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

    SELECT c.* INTO v_contract
    FROM public.equipment_unit u JOIN public.contract c ON c.id = u.contract_id
    WHERE u.id = NEW.equipment_unit_id;
    IF FOUND AND (v_contract.status <> 'active' OR v_contract.deleted_at IS NOT NULL
                  OR v_contract.date_from > NEW.date_from
                  OR (v_contract.date_to IS NOT NULL AND v_contract.date_to < NEW.date_to)) THEN
        RAISE EXCEPTION 'equipment.contract_inactive';
    END IF;

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


def upgrade() -> None:
    op.add_column("equipment_unit", sa.Column("model", sa.String(), nullable=True))
    op.add_column("equipment_unit", sa.Column("year", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_equipment_unit_year_range"),
        "equipment_unit",
        "year IS NULL OR year BETWEEN 1950 AND 2100",
    )
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(NEW_ASSIGNMENT_GUARD)
    op.execute("RESET ROLE")
    op.execute(
        "CREATE TRIGGER contract_frozen_after_activation BEFORE UPDATE OF counterparty_id, number"
        " ON contract FOR EACH ROW WHEN (OLD.status <> 'draft')"
        " EXECUTE FUNCTION app.forbid_column_change('counterparty_id', 'number')"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS contract_frozen_after_activation ON contract")
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(OLD_ASSIGNMENT_GUARD)
    op.execute("RESET ROLE")
    op.drop_constraint(op.f("ck_equipment_unit_year_range"), "equipment_unit", type_="check")
    op.drop_column("equipment_unit", "year")
    op.drop_column("equipment_unit", "model")
